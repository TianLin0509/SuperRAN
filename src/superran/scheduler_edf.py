"""包长感知（EDF）调度优先级内核。

EDF = *earliest drain first*：优先调度“最快能传完”的用户。度量是

    m_u = TBS_u / Buffer_u × (1 / priority_u)

``Buffer_u / TBS_u`` 是“还需几个调度机会才能排空缓冲区”，取倒数即为优先级：
缓冲区小 + 信道好（TBS 大）的用户先走，一次传完就把资源释放出去；缓冲区大
+ 信道差的用户排后面——它这次传了也清不空，不如先让小包过去。适合信令、
IoT、IM 这类小包占比高的场景。

算法蓝本是一套外部生产级 5G MAC 调度器的 EDF 实现。与比例公平
（SuperRAN 的 ``pf`` / ``qos_pf``）的分工：

================  =========================  =========================
维度              qos_pf（EPF）              edf
================  =========================  =========================
分母              历史平均速率 ``r_avg``     当前缓冲区 ``queue_bytes``
状态量            需要 IIR 滤波维护          无状态，直接读队列
优化目标          长期公平 + 吞吐            短期排空效率 + 时延
公平性            好                         差（大包用户可能被饿死）
================  =========================  =========================

**单位**：SuperRAN 主循环里 TBS 与缓冲区都是 **bytes**；蓝本文档写的是 bits。
比值无量纲，两种口径给出完全相同的排序，本模块统一按 bytes 记。

**本模块只算度量**，不碰 RBG 分配、不碰 HARQ 状态机、不持有任何状态。按需
分配 RBG（“只给够传完 buffer 的那几个 RBG”）在 SuperRAN 里是所有调度算法
共享的既有行为，见 :func:`superran.experience._build_su_plan` 里的
``required_rbg_for_indices`` + ``n = min(remaining_need, remaining)``。
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

__all__ = [
    "SRB_PRIORITY_BOOST",
    "RETX_PRIORITY_BOOST",
    "edf_priority",
    "edf_metric",
    "mixed_metric",
]

#: 信令无线承载（SRB）的绝对优先加值。蓝本 §1.4：在算出的 EDF 值上直接加一个
#: 大常数，保证信令无论 Buffer/TBS 如何都排在所有数据承载之前。
#:
#: **SuperRAN 当前不建模逻辑信道**：experience 模式下每个 UE 只有一个 DRB 队列，
#: 没有 SRB。因此主循环里这个加值只在用户显式声明
#: ``resource_type="signalling"`` 的业务类时才会触发；不声明就永远不触发，
#: 默认行为与不带 SRB 的实现逐位相同。本常数不会凭空造出信令话务。
SRB_PRIORITY_BOOST = 5000.0

#: 重传的绝对优先加值（蓝本 §2.4 方式 A）。
#:
#: **SuperRAN 主循环不用它**：``experience`` 已经把 HARQ pending 用户整体前置
#: 并按 ``first_tti`` 排序（结构性绝对优先，且保证同一 TB 不会被 PF 重排无限
#: 拖延软缓冲）。再叠一个常数只会打乱 ``first_tti`` 顺序，不会更强。这里保留
#: 常数与 :func:`edf_priority` 的 ``is_retx`` 分支，是为了让内核对蓝本完整可测，
#: 也供不带 HARQ 前置机制的调用方使用。
RETX_PRIORITY_BOOST = 10000.0


def _positive_finite(name: str, value: float, *, minimum: float = 0.0) -> float:
    out = float(value)
    if not np.isfinite(out) or out < minimum:
        raise ValueError(f"{name} 必须是不小于 {minimum:g} 的有限数")
    return out


def edf_priority(
    tbs: float,
    buffer_size: float,
    lch_priority: float = 1.0,
    *,
    is_srb: bool = False,
    is_retx: bool = False,
    srb_priority_boost: float = SRB_PRIORITY_BOOST,
    retx_priority_boost: float = RETX_PRIORITY_BOOST,
) -> float:
    """单个用户 / 单条逻辑信道的 EDF 优先级（标量参考实现）。

    :param tbs: 本次调度预估能传的传输块大小。SuperRAN 口径是 bytes。
    :param buffer_size: 该逻辑信道缓冲区待发数据量，与 ``tbs`` 同单位。
    :param lch_priority: 逻辑信道优先级，**1 = 最高**，值越大优先级越低
        （沿用 5QI 方向，与 :class:`superran.system.TrafficClassConfig` 的
        ``priority`` 一致）。
    :param is_srb: 是否为信令无线承载。
    :param is_retx: 是否为重传。SuperRAN 主循环恒传 ``False``，理由见
        :data:`RETX_PRIORITY_BOOST`。
    :returns: EDF 优先级，越大越优先。

    边界（蓝本 §6）：

    * ``buffer_size == 0`` → 0.0，无数据不发；
    * ``tbs == 0``（MCS 或 rank 为 0）→ 0.0，传不动就不发；
    * ``buffer_size < tbs`` → 比值 > 1，一个调度机会就能清空，**理应**很高；
    * ``buffer_size >> tbs`` → 比值趋近 0，**理应**很低，代价是大包可能饿死，
      靠混合模式（:func:`mixed_metric`）或 GBR/PDB 时延因子兜底。
    """
    _positive_finite("tbs", tbs)
    _positive_finite("buffer_size", buffer_size)
    prio = _positive_finite("lch_priority", lch_priority, minimum=1.0)
    _positive_finite("srb_priority_boost", srb_priority_boost)
    _positive_finite("retx_priority_boost", retx_priority_boost)

    if float(buffer_size) <= 0.0 or float(tbs) <= 0.0:
        # 无数据 / 传不动。此时**不加** SRB 与重传的绝对优先常数：那两个常数
        # 表达的是“同样有数据要发时谁先走”，给一个空队列加 5000 会凭空造出
        # 一个抢在所有真实数据前面的幽灵 grant。
        return 0.0

    priority = (float(tbs) / float(buffer_size)) * (1.0 / prio)
    if is_srb:
        priority += float(srb_priority_boost)
    if is_retx:
        priority += float(retx_priority_boost)
    return float(priority)


def _srb_boost_vector(
    size: int,
    srb_mask: Sequence[bool] | np.ndarray | None,
    srb_priority_boost: float,
) -> np.ndarray:
    if srb_mask is None:
        return np.zeros(size, dtype=float)
    mask = np.asarray(srb_mask, dtype=bool)
    if mask.shape != (size,):
        raise ValueError(f"srb_mask 长度必须是 {size}")
    return np.where(mask, float(srb_priority_boost), 0.0)


def edf_metric(
    potential: np.ndarray,
    queue_bytes: np.ndarray,
    priority_factor: np.ndarray,
    *,
    srb_mask: Sequence[bool] | np.ndarray | None = None,
    srb_priority_boost: float = SRB_PRIORITY_BOOST,
) -> np.ndarray:
    """候选集上的向量化 EDF 度量，语义与 :func:`edf_priority` 逐元素一致。

    :param potential: 每个候选用户的预估 TBS（bytes）。SuperRAN 主循环传的是
        **全带宽** TBS，与蓝本 §2.3“假设全带宽可用，用于优先级排序”一致；
        实际给几个 RBG 由后面的按需分配决定。
    :param queue_bytes: 每个候选用户的缓冲区待发字节数。
    :param priority_factor: ``1 / lch_priority``。SuperRAN 由
        ``qos_priority_weighting`` 开关决定：``none`` 恒为 1.0，
        ``inverse_priority`` 取 ``1 / max(TrafficClass.priority, 1)``。
    :param srb_mask: 哪些候选属于信令承载。``None`` 表示一个都不是（默认）。
    """
    tbs = np.asarray(potential, dtype=float)
    queue = np.asarray(queue_bytes, dtype=float)
    weight = np.asarray(priority_factor, dtype=float)
    if not (tbs.shape == queue.shape == weight.shape) or tbs.ndim != 1:
        raise ValueError("potential / queue_bytes / priority_factor 必须是等长一维数组")
    _positive_finite("srb_priority_boost", srb_priority_boost)

    servable = (queue > 0.0) & (tbs > 0.0)
    # 分母只在 servable 位置有意义；其余位置先钳到 1.0 避免 0 除产生 warning，
    # 再由 where 整体丢弃，结果与逐元素 early-return 完全一致。
    core = np.where(servable, tbs / np.maximum(queue, 1.0), 0.0) * weight
    boost = _srb_boost_vector(tbs.size, srb_mask, srb_priority_boost)
    return core + np.where(servable, boost, 0.0)


def mixed_metric(
    epf_core: np.ndarray,
    edf_core: np.ndarray,
    priority_factor: np.ndarray,
    *,
    weight: float,
    epf_scale: float = 1.0,
    srb_mask: Sequence[bool] | np.ndarray | None = None,
    srb_priority_boost: float = SRB_PRIORITY_BOOST,
) -> np.ndarray:
    """EPF + EDF 的加权混合，照抄蓝本加权混合模式（``PriorityCalcMethod == 2``）的原式::

        m = ((1 − w) · thp_filter · EPF + w · EDF) × (1 / lch_priority)

    :param epf_core: 不含 ``priority_factor`` 的 EPF 分量，即 SuperRAN 的
        ``potential^beta / r_avg^alpha × delay_factor^gamma``。
    :param edf_core: 不含 ``priority_factor`` 的 EDF 分量，即 ``TBS / Buffer``。
    :param weight: ``w ∈ [0, 1]``，EDF 的权重。0 = 纯 EPF，1 = 纯 EDF。
    :param epf_scale: 蓝本的 ``thp_filter`` 标定系数，默认 1.0。

    .. warning::
       **两个分量不同量纲，w 单独一个数不足以定义混合比例。** EPF 分量是
       ``bytes^beta / bytes^alpha``，典型落在 [1, 1e3]；EDF 分量是无量纲比值，
       典型落在 [1e-2, 1e3]。``epf_scale`` 就是蓝本用来配平量级的旋钮，
       它没标定时中间的 w 会被量级差吞掉——w=0.5 可能实际等价于 w=0.99。
       因此 experience 结果里会报出这两个分量在本次运行的**实测中位数**
       （``scheduler_mixed_component_scale``），用来判断 w 是否真的生效。
       w=0 与 w=1 两端不受影响，严格退化成纯 EPF / 纯 EDF。
    """
    epf = np.asarray(epf_core, dtype=float)
    edf = np.asarray(edf_core, dtype=float)
    factor = np.asarray(priority_factor, dtype=float)
    if not (epf.shape == edf.shape == factor.shape) or epf.ndim != 1:
        raise ValueError("epf_core / edf_core / priority_factor 必须是等长一维数组")
    w = _positive_finite("weight", weight)
    if w > 1.0:
        raise ValueError("weight 必须落在 [0, 1]")
    scale = _positive_finite("epf_scale", epf_scale)
    _positive_finite("srb_priority_boost", srb_priority_boost)

    combined = ((1.0 - w) * scale * epf + w * edf) * factor
    boost = _srb_boost_vector(epf.size, srb_mask, srb_priority_boost)
    # SRB 加值跟着 EDF 的语义走：只有本来就有数据可发的候选才享受绝对优先。
    return combined + np.where(edf > 0.0, boost, 0.0)

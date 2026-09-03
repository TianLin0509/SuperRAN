"""CSI 反馈时延与老化。

**基站永远不知道"现在"的信道。** 它知道的是上一次 SRS 探到的那个信道，
再加上估计、算预编码、下发调度的时间。TDD 下行靠互易性从上行 SRS 取 CSI，
所以这条时延链是：

    SRS 发送 → 信道估计 → 预编码计算 → PDSCH 发送
    └───────────── 这段时间信道一直在变 ─────────────┘

平台在此之前默认基站拿到的是**零时延完美 CSI**——预编码和评估用同一个矩阵，
于是 SVD 预编码永远精确匹配、ZF 零陷永远打得准。这在现网是不成立的，
而且它系统性地**高估 MU 增益**：MU 的全部收益都建立在零陷打得准上。

## 时延从哪来

两部分，量级差很远：

* **SRS 周期** ``T_SRS``——现网典型 5 / 10 / 20 / 40 ms。
* **SRS 跳频**——这才是大头。SRS 为了省上行开销与提高导频功率密度，
  一次只探一小段带宽，靠跳频扫完全带。

## 跳频的 17 是标准里逐字有的

38.211 Table 6.4.1.4.3-1 的 **C_SRS = 63 行**：

    m_SRS = (272, 16, 8, 4)      N = (1, 17, 2, 2)

取 ``B_SRS = 1``、``b_hop = 0``：每次 SRS 占 **16 RB（正好 1 个 RBG）**，
要 **17 跳**才扫完 272 RB。这和本项目的 17 RBG × 16 RB 载波配置 1:1 对上。

后果很直接：``T_SRS = 10 ms`` 时全带扫一遍要 **170 ms**，
某个 RBG 的 CSI 陈旧时长在 0 ~ 160 ms 之间轮转，**平均 80 ms**。
2.6 GHz、30 km/h 的相干时间只有约 6 ms——CSI 早就过期了。

当前只支持上述一个预置口径，因此 SuperRAN 直接固化并版本化
C_SRS=63 / B_SRS=1 / b_hop=0 的 17 跳顺序：
RBG 0 → 8 → 16 → 7 → … → 1 → 9；奇数 ``N_b=17`` 的步长 8 来自标准的
``floor(N_b/2)`` 镜像跳频公式，并非顺序扫描。它不依赖外部库的
helper；未来增加其他带宽时再实现完整通用资源映射。

## 老化怎么进 SINR

**不是给 SINR 打个折扣，是让预编码真的算错。**

    W = SVD(H_stale)            ← 基站用陈旧信道算预编码
    SINR = MMSE(H_true, W)      ← 实际传输吃的是当前信道

零时延时 ``H_stale == H_true``，``H_true·W`` 恰好对角化，
逐流 SINR 退化成 ``σ_k²·P/rank/σ_n²``——**和原来的
``su_rank_adaptation`` 逐位相同**。这条恒等式是本模块的核心自检
（``test_csi_aging`` 第 1 节），它保证老化模型不是叠加上去的第二套物理。

有老化时 ``H_true·W`` 不再对角，流间泄漏进入 MMSE 的分母，
表现为 BF 增益下降 + 流间干扰——这正是现网的物理。

参考：Sionna 的 CSI 反馈链路同样把预编码信道与评估信道分成两个输入
（``sionna.phy.mimo`` 的 precoding 与 detection 是解耦的），
本模块的 ``h_prec`` / ``h_eval`` 沿用同一分工。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

from . import beamforming as bf
from . import mumimo as mu

__all__ = [
    "SRS_PERIOD_CHOICES",
    "SRS_RESOURCE_PERIOD_CHOICES",
    "CSI_REPORT_PERIOD_CHOICES",
    "CsiConfig",
    "hop_order",
    "rbg_csi_staleness_ms",
    "rbg_age_ms",
    "rbg_lag_snapshots",
    "rbg_lag_snapshots_by_antenna_group",
    "stale_channel",
    "stale_channel_by_antenna_group",
    "svd_precoder",
    "mmse_stream_sinr",
    "AgedRankChoice",
    "rank_adaptation_aged",
    "jakes_correlation",
    "aging_summary",
]

_EPS = 1e-12

#: 现网典型 SRS 周期（ms）。**只允许这四个值**——它们对应 38.331 里
#: ``periodicityAndOffset`` 在 30 kHz SCS 下的 sl10 / sl20 / sl40 / sl80 时隙。
SRS_PERIOD_CHOICES: tuple[float, ...] = (5.0, 10.0, 20.0, 40.0)
# The user-confirmed 2T4R allocation profile pairs consecutive 5-ms air
# opportunities (slot7->17) into one logical four-port SRS period.  Therefore
# its shortest allocatable period is 10 ms.  A 5-ms SRS remains available only
# as an explicit no-allocation/full-band diagnostic upper bound.
SRS_RESOURCE_PERIOD_CHOICES: tuple[float, ...] = (10.0, 20.0, 40.0)

# 38.331 的 CSI-ReportPeriodicityAndOffset 按 slot 配置，并不存在“PMI 固定 5 ms”
# 这一条标准结论。这里给 30 kHz SCS 下常用的工程扫描点；默认 20 ms = 40 slots。
CSI_REPORT_PERIOD_CHOICES: tuple[float, ...] = (5.0, 10.0, 20.0, 40.0, 80.0)

#: 38.211 Table 6.4.1.4.3-1 里 m_SRS,0 = 272（全带）且 m_SRS,1 = 16（1 个 RBG）的那一行
SRS_C_SRS_FULL_BAND = 63
SRS_B_SRS_ONE_RBG = 1
DEFAULT_HOP_FACTOR = 17

#: 宽带 CQI 滤波的两个可选作用域。现场口径是在量化后的 CQI 档上滤波。
CQI_FILTER_DOMAINS: tuple[str, ...] = ("cqi_index", "sinr_db")
@dataclass
class CsiConfig:
    """CSI 时延链的配置。

    ``enabled=False`` 时整条链退化成零时延完美 CSI，也就是本模块出现之前的行为。
    保留这个开关是为了能做 A/B 对比——**老化的代价必须能被量出来**，
    而不是悄悄地混进所有结果里。
    """

    enabled: bool = True
    #: SRS 周期（ms），取值见 :data:`SRS_PERIOD_CHOICES`
    srs_period_ms: float = 10.0
    #: 跳频开关。关掉时每次 SRS 探全带，陈旧时长只剩周期内相位 + 处理时延
    hopping: bool = True
    #: 跳频倍数。默认 17 = C_SRS 63 / B_SRS 1，每跳 1 个 RBG
    hop_factor: int = DEFAULT_HOP_FACTOR
    #: 信道估计 + 预编码计算 + 调度下发的固定时延（ms）
    processing_delay_ms: float = 2.0
    #: 宽带 CQI/PMI 报告周期；与 5 ms 信道快照生成间隔是两个独立量
    csi_report_period_ms: float = 20.0
    #: 为每个 UE 分配独立的周期/符号/comb/循环移位资源，并把周期 offset
    #: 接入 CSI 老化。关闭仅用于复现旧的“所有 UE offset=0”上界。
    srs_resource_allocation: bool = True
    #: 从 srs_period_ms 开始，在 10/20/40 ms 中选择能容纳全局 UE 的最短周期。
    #: 关闭只用于显式固定周期的容量/老化消融。
    srs_period_adaptive: bool = True
    #: 重放有限信道 trace 时，是否把上一轮 trace 当作预启动阶段的因果历史
    periodic_trace_history: bool = False
    #: 宽带 CQI 的一阶 IIR 滤波系数：``s <- s + lambda*(x - s)``。
    #: 1.0 表示不做滤波（每次上报直接生效），越小记忆越长。
    #: 现场口径是 ``CQI = CQI + lambda*(最新测量 - CQI)``。**0.25 已由负责人
    #: 确认为当前工程默认，但尚未经现场测量/设备数据标定**；它不是协议标准值，
    #: 也不得表述成现场等价。
    cqi_filter_lambda: float = 0.25
    #: 滤波作用域。``cqi_index`` 在量化后的连续 CQI 档上滤波（现场口径），
    #: ``sinr_db`` 在量化前的 PMI-SINR 上滤波（便于做口径消融）。
    cqi_filter_domain: str = "cqi_index"

    def __post_init__(self) -> None:
        for name in (
            "enabled", "hopping", "srs_resource_allocation",
            "srs_period_adaptive", "periodic_trace_history",
        ):
            if not isinstance(getattr(self, name), (bool, np.bool_)):
                raise ValueError(f"{name} 必须是布尔值")
        if self.srs_period_ms not in SRS_PERIOD_CHOICES:
            raise ValueError(
                f"srs_period_ms 只支持 {SRS_PERIOD_CHOICES}，收到 {self.srs_period_ms}"
            )
        if (self.srs_resource_allocation
                and self.srs_period_ms not in SRS_RESOURCE_PERIOD_CHOICES):
            raise ValueError(
                "基础 2T4R SRS 资源分配只支持 10/20/40 ms 全局周期；"
                "5 ms 只允许在 srs_resource_allocation=False 时作为显式诊断上界"
            )
        if (isinstance(self.hop_factor, (bool, np.bool_))
                or not isinstance(self.hop_factor, (int, np.integer))
                or int(self.hop_factor) < 1):
            raise ValueError("hop_factor 必须是至少为 1 的整数")
        if (not np.isfinite(self.processing_delay_ms)
                or self.processing_delay_ms < 0):
            raise ValueError("processing_delay_ms 必须是有限非负数")
        if (not np.isfinite(self.csi_report_period_ms)
                or self.csi_report_period_ms <= 0):
            raise ValueError("csi_report_period_ms 必须是有限正数")
        if (isinstance(self.cqi_filter_lambda, (bool, np.bool_))
                or not np.isfinite(self.cqi_filter_lambda)
                or not 0.0 < float(self.cqi_filter_lambda) <= 1.0):
            raise ValueError("cqi_filter_lambda 必须是 (0,1] 内的有限数")
        if self.cqi_filter_domain not in CQI_FILTER_DOMAINS:
            raise ValueError(
                f"cqi_filter_domain 只支持 {CQI_FILTER_DOMAINS}，"
                f"收到 {self.cqi_filter_domain!r}")

    @property
    def full_sweep_ms(self) -> float:
        """扫完全带宽需要多久。不跳频时就是一个 SRS 周期。"""
        return self.srs_period_ms * (self.hop_factor if self.hopping else 1)

    @property
    def srs_transmissions_per_full_sweep(self) -> int:
        """Physical SRS transmissions needed for all logical UE ports."""
        hops = self.hop_factor if self.hopping else 1
        return int(hops * (2 if self.srs_resource_allocation else 1))

    @property
    def mean_csi_staleness_ms(self) -> float:
        """全带宽平均 CSI 陈旧时长。

        跳频时某个 RBG 的陈旧时长在 ``0 ~ (H-1)·T`` 之间均匀轮转，
        均值 ``(H-1)·T/2``；
        再加上周期内相位的均值 ``T/2`` 与固定处理时延。
        """
        hops = (self.hop_factor - 1) if self.hopping else 0
        return hops * self.srs_period_ms / 2.0 + self.srs_period_ms / 2.0 + \
            self.processing_delay_ms

    @property
    def mean_age_ms(self) -> float:
        """兼容旧代码；新结果统一使用 ``mean_csi_staleness_ms``。"""
        return self.mean_csi_staleness_ms

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "srs_period_ms": self.srs_period_ms,
            "hopping": self.hopping,
            "hop_factor": self.hop_factor if self.hopping else 1,
            "srs_transmissions_per_full_sweep": (
                self.srs_transmissions_per_full_sweep),
            "antenna_group_measurement_skew_ms": (
                5.0 if self.srs_resource_allocation else 0.0),
            "processing_delay_ms": self.processing_delay_ms,
            "csi_report_period_ms": self.csi_report_period_ms,
            "srs_resource_allocation": bool(self.srs_resource_allocation),
            "srs_period_adaptive": bool(self.srs_period_adaptive),
            "csi_report_period_basis": (
                "engineering_default_20ms; 38.331 configures periodicity in slots, "
                "not a universal 5ms PMI period"),
            "csi_report_feedback_latency_ms": 0.0,
            "csi_report_feedback_latency_scope": (
                "not modelled beyond processing_delay_ms; report becomes available "
                "at its report snapshot"),
            "cqi_filter": (
                "causal first-order IIR over report instants: "
                "s <- s + lambda*(x - s), initialised by the first report"),
            "cqi_filter_lambda": float(self.cqi_filter_lambda),
            "cqi_filter_domain": str(self.cqi_filter_domain),
            "cqi_filter_lambda_basis": (
                "lead-confirmed engineering default, not yet calibrated against "
                "field measurements or device data; "
                "lambda=1 disables filtering"),
            "periodic_trace_history": self.periodic_trace_history,
            "full_sweep_ms": round(self.full_sweep_ms, 2),
            "mean_csi_staleness_ms": round(self.mean_csi_staleness_ms, 2),
            "standard": (
                f"38.211 Table 6.4.1.4.3-1 C_SRS={SRS_C_SRS_FULL_BAND} "
                f"B_SRS={SRS_B_SRS_ONE_RBG}：m_SRS=(272,16,8,4) N=(1,17,2,2)，"
                f"每跳 16 RB、17 跳扫完全带"
            ) if self.hopping else "不跳频，每次 SRS 探全带",
            "note": (
                "预编码用陈旧 CSI、评估用当前信道。enabled=False 时退化成零时延"
                "完美 CSI（本模块出现之前的行为），可用于 A/B 对比。"
            ),
        }


def validate_hopping_grid(
    cfg: CsiConfig,
    rbg_prb_sizes: tuple[int, ...] | list[int],
) -> None:
    """拦住尚未建立资源映射的非预置栅格 SRS 跳频老化。

    当前生产模型把 38.211 的 ``C_SRS=63/B_SRS=1`` 映射为 17 次、每次
    16 PRB。对 51/106/273 RB 直接套同一 ``hop_factor=17`` 并不代表标准
    SRS 资源：有些 PRB 永远未测，有些 RBG 又被错误复用。过去还会在查表失败
    时退回恒等扫描，结果看似正常却没有物理含义，因此这里选择硬失败。
    """
    if not cfg.enabled or not cfg.hopping:
        return
    raw_sizes = tuple(rbg_prb_sizes)
    if any(
        isinstance(value, (bool, np.bool_))
        or not isinstance(value, (int, np.integer))
        or int(value) < 1
        for value in raw_sizes
    ):
        raise ValueError("SRS 跳频的 rbg_prb_sizes 必须全部是正整数")
    sizes = tuple(int(value) for value in raw_sizes)
    if sizes != (16,) * 17 or int(cfg.hop_factor) != 17:
        raise ValueError(
            "SRS 跳频老化当前只验证了预置 272 PRB = 17×16 配置"
            "（C_SRS=63, B_SRS=1, b_hop=0）。本载波 RBG 大小为 "
            f"{list(sizes)}，禁止静默套用 17-hop/恒等扫描。"
            "请关闭 srs_hopping（显式的全带 SRS 工程上界），或补充该载波的 "
            "C_SRS/B_SRS/b_hop/n_RRC 资源配置后再仿真。"
        )


# ---------------------------------------------------------------------------
# 跳频序列
# ---------------------------------------------------------------------------
@lru_cache(maxsize=64)
def _standard_hop_order(num_rbg: int, rb_per_rbg: int,
                        hop_factor: int) -> np.ndarray:
    """返回 SuperRAN 已验证的 100 MHz / 17-hop 序列。

    当前不提供通用跳频树：参数不是 17×16 就硬失败，不从外部
    helper 动态取结果，也不退回恒等扫描。
    """
    if (num_rbg, rb_per_rbg, hop_factor) != (17, 16, 17):
        raise ValueError(
            "SRS hopping 当前只支持 100 MHz、272 RB、17 RBG × 16 RB "
            "的 17-hop profile"
        )
    from . import hardware as hw  # noqa: PLC0415

    order = np.asarray(hw.COMPANY_SRS_17_HOP_ORDER_RBG, dtype=int)
    order.flags.writeable = False
    return order


def hop_order(num_rbg: int, *, rb_per_rbg: int = 16,
              hop_factor: int = DEFAULT_HOP_FACTOR) -> tuple[np.ndarray, str]:
    """第 j 次 SRS 机会探的是哪个 RBG。返回 ``(order, source)``。

    结果是 SuperRAN 自己固化的 C_SRS=63 / B_SRS=1 / b_hop=0
    预置 profile；只接受 17×16 参数。返回的 order 是共享只读数组。
    """
    for name, value in (("num_rbg", num_rbg), ("rb_per_rbg", rb_per_rbg),
                        ("hop_factor", hop_factor)):
        if (isinstance(value, (bool, np.bool_))
                or not isinstance(value, (int, np.integer)) or int(value) < 1):
            raise ValueError(f"{name} 必须是至少为 1 的整数")
    order = _standard_hop_order(int(num_rbg), int(rb_per_rbg), int(hop_factor))
    from . import hardware as hw  # noqa: PLC0415

    return order, f"superran:{hw.SUPERRAN_SRS_HOPPING_PROFILE_ID}"


def rbg_csi_staleness_ms(cfg: CsiConfig, num_rbg: int, t_ms: float, *,
                         rb_per_rbg: int = 16,
                         opportunity_offset_ms: float = 0.0,
                         frequency_resource_id: int = 0) -> np.ndarray:
    """时刻 ``t_ms`` 上，每个 RBG 的 CSI 陈旧时长（ms）。返回 ``[num_rbg]``。

    这里不是“SRS 年龄”：SRS 的配置量是周期。返回值指“距最近一次覆盖该 RBG
    的 SRS 测量已经过去多久 + 处理时延”，也就是预编码所用 CSI 的陈旧时长。
    跳频时各 RBG 的陈旧时长**随时间轮转**——同一个 RBG 在不同 TTI 上不同，
    所以长时间平均下来所有 RBG 是等价的，不会有"某几个 RBG 永远最差"。
    """
    if (isinstance(num_rbg, (bool, np.bool_))
            or not isinstance(num_rbg, (int, np.integer)) or int(num_rbg) < 1):
        raise ValueError("num_rbg 必须是至少为 1 的整数")
    if not np.isfinite(t_ms):
        raise ValueError("t_ms 必须是有限数")
    t = float(t_ms)
    per = cfg.srs_period_ms
    offset = float(opportunity_offset_ms)
    if (not np.isfinite(offset) or offset < 0.0 or offset >= per):
        raise ValueError(
            "opportunity_offset_ms 必须是 [0, srs_period_ms) 内的有限数"
        )
    if not cfg.enabled:
        return np.zeros(num_rbg)
    # 处理时延不是“给最新一次 SRS 的年龄机械加一个常数”。时刻 t 真正可用的
    # 是 measurement_time <= t-processing_delay 的最近一次机会；尤其在周期边界
    # t=nT 上，本次 SRS 仍在处理，绝不能立即拿来预编码。旧写法先按 t 选机会、
    # 再把 processing_delay 加到年龄上，会把这个尚不可用的机会错当成可用，并且
    # 在跳频场景选错 RBG phase。
    usable_measurement_time = t - float(cfg.processing_delay_ms)
    n = int(np.floor((usable_measurement_time - offset) / per + 1e-9))
    measurement_time = offset + n * per
    within = t - measurement_time               # 距真正可用的那次机会过了多久
    if not cfg.hopping:
        return np.full(num_rbg, within)

    order, _ = hop_order(num_rbg, rb_per_rbg=rb_per_rbg, hop_factor=cfg.hop_factor)
    h = len(order)
    if (isinstance(frequency_resource_id, (bool, np.bool_))
            or not isinstance(frequency_resource_id, (int, np.integer))
            or not 0 <= int(frequency_resource_id) < h):
        raise ValueError(f"frequency_resource_id 必须是 0..{h - 1} 的整数")
    phase = int(frequency_resource_id)
    # 同一个 RBG 在一个周期里可能被探多次，取最近的那次
    hops = np.full(num_rbg, h - 1, dtype=int)
    for occurrence_mod in range(h):
        k = order[(occurrence_mod + phase) % h]
        if 0 <= int(k) < num_rbg:
            hops[int(k)] = min(
                int(hops[int(k)]), (n - occurrence_mod) % h)
    return hops * per + within


def rbg_age_ms(cfg: CsiConfig, num_rbg: int, t_ms: float, *,
               rb_per_rbg: int = 16,
               opportunity_offset_ms: float = 0.0,
               frequency_resource_id: int = 0) -> np.ndarray:
    """兼容旧 API；新代码请用 :func:`rbg_csi_staleness_ms`。"""
    return rbg_csi_staleness_ms(
        cfg, num_rbg, t_ms, rb_per_rbg=rb_per_rbg,
        opportunity_offset_ms=opportunity_offset_ms,
        frequency_resource_id=frequency_resource_id)


def rbg_lag_snapshots(cfg: CsiConfig, num_rbg: int, *, snapshot_ms: float,
                      snapshot_index: int, rb_per_rbg: int = 16,
                      opportunity_offset_ms: float = 0.0,
                      frequency_resource_id: int = 0) -> np.ndarray:
    """把每个 RBG 的 CSI 陈旧时长折成**整数个信道快照**。

    信道快照之间隔 ``snapshot_ms``（由 :func:`system.snapshot_interval_ms` 算出，
    默认 5 ms），所以陈旧时长只能量化到这个粒度。

    **量化误差要报出来，不能假装没有。** 默认配置下主导项是跳频
    （T_SRS=10 ms 时跨度 0~160 ms），2 ms 的处理时延落在量化噪声里；
    但如果有人把 SRS 周期设成 5 ms 又关掉跳频，陈旧时长就全在一个快照以内，
    这时老化模型基本失效——:func:`aging_summary` 会警告。
    """
    if not np.isfinite(snapshot_ms) or float(snapshot_ms) <= 0:
        raise ValueError("snapshot_ms 必须是有限正数")
    if (isinstance(snapshot_index, (bool, np.bool_))
            or not isinstance(snapshot_index, (int, np.integer))
            or int(snapshot_index) < 0):
        raise ValueError("snapshot_index 必须是非负整数")
    if not cfg.enabled:
        return np.zeros(num_rbg, dtype=int)
    staleness = rbg_csi_staleness_ms(
        cfg, num_rbg, snapshot_index * float(snapshot_ms),
        rb_per_rbg=rb_per_rbg,
        opportunity_offset_ms=opportunity_offset_ms,
        frequency_resource_id=frequency_resource_id)
    # 必须向上取整。四舍五入会把 2 ms / 5 ms 量化成 0，等于在测量已经发生
    # 之前使用当前信道；例如 7 ms 还会被缩成 5 ms。离散快照无法表示精确时长
    # 时，只能取“不新于真实测量”的最近快照，才能守住因果性。
    ratio = np.maximum(staleness, 0.0) / max(float(snapshot_ms), _EPS)
    return np.maximum(0, np.ceil(ratio - 1e-12)).astype(int)


def rbg_lag_snapshots_by_antenna_group(
    cfg: CsiConfig,
    num_rbg: int,
    *,
    snapshot_ms: float,
    snapshot_index: int,
    opportunity_offsets_ms: tuple[float, float],
    frequency_resource_id: int = 0,
    rb_per_rbg: int = 16,
) -> np.ndarray:
    """Return ``[2,RBG]`` lags for the two 2T legs of a 2T4R UE.

    The first row belongs to logical antenna ports 0/1 and the second to 2/3.
    Both legs share one hop counter but occur at consecutive available SRS
    opportunities.  Calling the same hopping model with the two offsets gives
    the desired behaviour: after leg 0 but before leg 1, the first pair has
    already advanced to the new RBG while the second pair still carries its
    previous-cycle estimate.
    """
    if len(opportunity_offsets_ms) != 2:
        raise ValueError("2T4R SRS requires exactly two opportunity offsets")
    offsets = tuple(float(value) for value in opportunity_offsets_ms)
    if not offsets[1] > offsets[0]:
        raise ValueError("second 2T SRS opportunity must follow the first")
    return np.stack([
        rbg_lag_snapshots(
            cfg, num_rbg, snapshot_ms=snapshot_ms,
            snapshot_index=snapshot_index, rb_per_rbg=rb_per_rbg,
            opportunity_offset_ms=offset,
            frequency_resource_id=frequency_resource_id,
        )
        for offset in offsets
    ], axis=0)


def stale_channel(snaps: list[np.ndarray], snapshot_index: int,
                  lags: np.ndarray, *, periodic_history: bool = False) -> np.ndarray:
    """按逐 RBG 的滞后拼出基站"以为"的信道。

    ``snaps[s]`` 形状 ``[RBG, BS, UE]``；返回同形状，第 ``k`` 行取自
    ``snaps[snapshot_index - lags[k]]``。

    冷启动默认在索引越界时钳到最早快照。只有 ``periodic_history=True`` 时才
    回到有限 trace 的上一轮；它表示仿真已经预启动、当前 trace 是周期重放，
    并非把本轮未来偷给本轮过去。调用方必须把这个假设写进结果。
    """
    cur = np.asarray(snaps[snapshot_index])
    out = np.array(cur, copy=True)
    for k in range(cur.shape[0]):
        raw = int(snapshot_index) - int(lags[k])
        s = raw % len(snaps) if periodic_history else max(0, raw)
        out[k] = np.asarray(snaps[s])[k]
    return out


def stale_channel_by_antenna_group(
    snaps: list[np.ndarray],
    snapshot_index: int,
    lags_by_group: np.ndarray,
    *,
    antenna_port_groups: tuple[tuple[int, ...], tuple[int, ...]] = (
        (0, 1), (2, 3)),
    periodic_history: bool = False,
) -> np.ndarray:
    """Assemble a 2T4R channel from two independently aged 64x2 slices.

    ``snaps[s]`` has shape ``[RBG,BS,UE-port]`` and ``lags_by_group`` has
    shape ``[2,RBG]``.  Each antenna-port group is copied from the latest
    causally available snapshot for its own SRS opportunity.  This preserves
    the five-millisecond switching skew instead of pretending all four UE
    columns were measured simultaneously.
    """
    if not snaps:
        raise ValueError("snaps must not be empty")
    cur = np.asarray(snaps[snapshot_index])
    lags = np.asarray(lags_by_group)
    if lags.shape != (len(antenna_port_groups), cur.shape[0]):
        raise ValueError(
            "lags_by_group must have shape "
            f"({len(antenna_port_groups)},{cur.shape[0]}), got {lags.shape}"
        )
    flattened = [int(port) for group in antenna_port_groups for port in group]
    if sorted(flattened) != list(range(cur.shape[-1])):
        raise ValueError(
            "antenna_port_groups must cover every UE port exactly once; "
            f"groups={antenna_port_groups}, ue_ports={cur.shape[-1]}"
        )
    out = np.array(cur, copy=True)
    for group_index, ports in enumerate(antenna_port_groups):
        port_idx = list(ports)
        for rbg in range(cur.shape[0]):
            raw = int(snapshot_index) - int(lags[group_index, rbg])
            source = raw % len(snaps) if periodic_history else max(0, raw)
            src = np.asarray(snaps[source])
            out[rbg][:, port_idx] = src[rbg][:, port_idx]
    return out


# ---------------------------------------------------------------------------
# 预编码失配下的 SINR
# ---------------------------------------------------------------------------
def svd_precoder(h_prec: np.ndarray) -> np.ndarray:
    """从（陈旧的）信道算 SVD 预编码。``[RBG, BS, UE]`` → ``[RBG, BS, K]``。

    列按奇异值降序、单位范数——**方向而已，功率另给**
    （和 :func:`mumimo.mu_precoder` 同一约定）。
    """
    hb = np.asarray(h_prec)
    hm = np.conj(np.transpose(hb, (0, 2, 1)))          # [F, N_rx, N_tx]
    _, _, vh = np.linalg.svd(hm, full_matrices=False)  # vh: [F, K, N_tx]
    return np.conj(np.transpose(vh, (0, 2, 1)))        # [F, N_tx, K]


def mmse_stream_sinr(h_eval: np.ndarray, w: np.ndarray, *,
                     power_per_stream: float, noise_power: float) -> np.ndarray:
    """预编码 ``w`` 打在信道 ``h_eval`` 上，MMSE 接收机的逐流 SINR。

    ``h_eval`` ``[RBG, BS, UE]``，``w`` ``[RBG, BS, rank]``，返回 ``[RBG, rank]``（线性）。

    模型：``y = H W s + n``，``E[ss^H] = p·I``、``E[nn^H] = σ²·I``，
    MMSE 均衡后第 k 流的后处理 SINR 是标准结果::

        SINR_k = 1 / [ (I + (p/σ²)·(HW)^H(HW))^{-1} ]_kk  −  1

    **零时延时它必须退化成 ``σ_k²·p/σ²``。** 因为那时 ``HW = UΣ_r``、
    ``(HW)^H(HW) = Σ_r²`` 是对角阵，逆的对角元就是 ``1/(1+p·σ_k²/σ²)``。
    这条恒等式保证老化模型不是叠加上去的第二套物理，而是同一套物理的推广。
    """
    hb = np.asarray(h_eval)
    hm = np.conj(np.transpose(hb, (0, 2, 1)))          # [F, N_rx, N_tx]
    g = hm @ np.asarray(w)                             # [F, N_rx, r]
    gram = np.conj(np.transpose(g, (0, 2, 1))) @ g     # [F, r, r]
    r = gram.shape[-1]
    a = np.eye(r) + (float(power_per_stream) / max(float(noise_power), _EPS)) * gram
    diag = np.real(np.einsum("fkk->fk", np.linalg.inv(a)))
    return np.maximum(1.0 / np.maximum(diag, _EPS) - 1.0, 0.0)


@dataclass
class AgedRankChoice:
    """老化下的 rank 自适应结果。**基站以为的**与**真实的**必须分开。"""

    rank: int                            # 基站选的 rank（按它自己的陈旧 CSI）
    sinr_db: float                       # 该 rank 下的真实接收 SINR
    mcs: int                             # 真实 SINR 对应的 MCS
    se: float                            # 真实谱效
    se_gnb: float                        # 基站以为的谱效
    candidates: list[dict[str, Any]]     # 逐 rank 的真实量
    gnb_candidates: list[dict[str, Any]]  # 逐 rank 的基站估计量
    power_constraint: str = "ebf"
    power_diagnostics: dict[str, Any] | None = None


def rank_adaptation_aged(h_prec: np.ndarray, h_eval: np.ndarray, *,
                         noise_power: float, max_rank: int = mu.SU_MAX_RANK,
                         table: int = 3, target_bler: float = 0.1,
                         total_power: float = 1.0,
                         rb_per_rbg: int = 1,
                         rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
                         w_override: np.ndarray | None = None,
                         power_constraint: bf.PowerConstraint | str = "ebf") -> AgedRankChoice:
    """预编码用 ``h_prec``、评估用 ``h_eval`` 的 rank 自适应。

    两者都是 ``[RBG, BS, UE]``（已经降过粒度）。除了预编码信道来源不同，
    判据与 :func:`mumimo.su_rank_adaptation` 完全一致：遍历 rank 1..max_rank，
    单码字口径压成用户级 SINR，取 ``rank × MCS谱效`` 最高的那个。

    ``rb_per_rbg`` 直通 :func:`mumimo.user_sinr_db`：输入已降到 RBG 粒度时传 1
    （每行就是一个 RBG），停在 RB 粒度时传 16（由它按 16 分组）。
    **传错会改变单码字的聚合口径**，SINR 会差几个 dB。

    **rank 由基站按自己的 CSI 选，不是按真实信道选。** 这一条很容易写错，
    而写错的方向恰好是"老化看起来没那么糟"：如果拿真实 SINR 去挑 rank，
    等于让基站预知信道变成了什么样，它会自动避开老化最狠的那个 rank，
    损失被凭空抹掉一大半。高速下真实的现象正是"基站点了 rank 4、
    实际只撑得住 rank 1"——这是老化损失的重要一环，必须留在结果里。

    零时延时 ``h_prec is h_eval``，两套量逐位相同，退化成原来的行为。

    ``w_override`` 给定时用它当发射权（形状 ``[F, N_tx, K]``，列单位范数），
    不再从 ``h_prec`` 做 SVD——用来把**实际发送权换成 Type I 码本**，
    看码本权在老化下是不是比 SVD 更耐受（自由度少，能算错的地方也少）。
    注意它仍必须是从**陈旧** CSI 算出来的，否则又变成基站预知信道了。
    """
    hp = np.asarray(h_prec)
    he = np.asarray(h_eval)
    if hp.shape != he.shape:
        raise ValueError(f"预编码与评估信道形状必须一致，收到 {hp.shape} vs {he.shape}")
    r_max = max(1, min(int(max_rank), hp.shape[1], hp.shape[2]))
    if w_override is not None:
        w_full = np.asarray(w_override)
        if w_full.shape[:2] != hp.shape[:2]:
            raise ValueError(f"w_override 形状 {w_full.shape} 与信道 {hp.shape} 对不上")
        r_max = max(1, min(r_max, w_full.shape[2]))
    else:
        w_full = svd_precoder(hp)                      # [F, N_tx, K]

    cands: list[dict[str, Any]] = []
    gnb: list[dict[str, Any]] = []
    best_r, best_se_gnb = 1, -1.0
    diag_by_rank: list[dict[str, Any]] = []
    for r in range(1, r_max + 1):
        p_per = float(total_power) / r
        _q, w, pdiag = bf.equal_power_weights(
            w_full[:, :, :r], mode=power_constraint, total_power=total_power)
        diag_by_rank.append(pdiag.as_dict())
        # 真实：陈旧预编码打在当前信道上
        true_stream_sinr = mmse_stream_sinr(
            he, w, power_per_stream=p_per, noise_power=noise_power)
        true_rbg_sinr = mu.rbg_sinr_db(
            true_stream_sinr, rb_per_rbg=rb_per_rbg,
            rbg_boundaries=rbg_boundaries)
        s_true = float(np.mean(true_rbg_sinr))
        se_t, mcs_t = mu.se_from_sinr(s_true, r, table=table, target_bler=target_bler)
        cands.append({"rank": r, "sinr_db": round(s_true, 2), "mcs": mcs_t.index,
                      "se": round(se_t, 4),
                      "sinr_rbg_db": [float(x) for x in true_rbg_sinr]})
        # 基站以为的：陈旧预编码打在陈旧信道上（它只有这个）
        gnb_stream_sinr = mmse_stream_sinr(
            hp, w, power_per_stream=p_per, noise_power=noise_power)
        gnb_rbg_sinr = mu.rbg_sinr_db(
            gnb_stream_sinr, rb_per_rbg=rb_per_rbg,
            rbg_boundaries=rbg_boundaries)
        s_gnb = float(np.mean(gnb_rbg_sinr))
        se_g, mcs_g = mu.se_from_sinr(s_gnb, r, table=table, target_bler=target_bler)
        gnb.append({"rank": r, "sinr_db": round(s_gnb, 2), "mcs": mcs_g.index,
                    "se": round(se_g, 4),
                    "sinr_rbg_db": [float(x) for x in gnb_rbg_sinr]})
        if se_g > best_se_gnb:
            best_r, best_se_gnb = r, se_g

    return AgedRankChoice(
        rank=best_r,
        sinr_db=float(cands[best_r - 1]["sinr_db"]),
        mcs=int(cands[best_r - 1]["mcs"]),
        se=float(cands[best_r - 1]["se"]),
        se_gnb=float(best_se_gnb),
        candidates=cands, gnb_candidates=gnb,
        power_constraint=str(power_constraint).lower(),
        power_diagnostics=diag_by_rank[best_r - 1],
    )


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def jakes_correlation(delay_ms: float, speed_kmh: float,
                      carrier_hz: float = 2.6e9) -> float:
    """Jakes 模型下滞后 ``delay_ms`` 的信道时间相关系数 ``|J₀(2π·f_d·τ)|``。

    ``f_d = v·f_c/c``。用来判断"这个时延到底算不算长"——
    相干时间的常用定义是相关系数掉到 0.5 的那个 τ。
    """
    from scipy.special import j0  # noqa: PLC0415

    f_d = float(speed_kmh) / 3.6 * float(carrier_hz) / 299_792_458.0
    return float(abs(j0(2.0 * np.pi * f_d * float(delay_ms) / 1000.0)))


def coherence_time_ms(speed_kmh: float, carrier_hz: float = 2.6e9) -> float:
    """相干时间（ms）：相关系数首次掉到 0.5 的滞后。速度为 0 时返回 inf。"""
    if speed_kmh <= 0:
        return float("inf")
    lo, hi = 0.0, 1.0
    while jakes_correlation(hi, speed_kmh, carrier_hz) > 0.5 and hi < 1e5:
        hi *= 2.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if jakes_correlation(mid, speed_kmh, carrier_hz) > 0.5:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def aging_summary(cfg: CsiConfig, *, num_rbg: int = 17, snapshot_ms: float = 5.0,
                  speed_kmh: float = 3.0, carrier_hz: float = 2.6e9,
                  rb_per_rbg: int = 16) -> dict[str, Any]:
    """把这套配置下的老化画像算出来，供说明书与结果摘要引用。"""
    if cfg.hopping:
        order, source = hop_order(num_rbg, rb_per_rbg=rb_per_rbg,
                                  hop_factor=cfg.hop_factor)
    else:
        # 不跳频时没有"扫描顺序"这个概念，不拿 17-hop 校验去撞窄带诊断。
        order, source = list(range(int(num_rbg))), "not_applicable_non_hopping"

    stale_ms = rbg_csi_staleness_ms(
        cfg, num_rbg, 0.0, rb_per_rbg=rb_per_rbg) if cfg.enabled \
        else np.zeros(num_rbg)
    lags = rbg_lag_snapshots(cfg, num_rbg, snapshot_ms=snapshot_ms,
                             snapshot_index=0, rb_per_rbg=rb_per_rbg)
    t_c = coherence_time_ms(speed_kmh, carrier_hz)
    # 配置公式只在完整 17-hop 稳态上等于真实均值；窄带诊断、不同 phase 或
    # 不跳频时应报告本次实际算出的向量，避免 3-RBG 示例仍写成 82 ms。
    mean_staleness = float(np.mean(stale_ms)) if cfg.enabled and len(stale_ms) else 0.0
    warn: list[str] = []
    if cfg.enabled and max(lags) == 0:
        warn.append(
            f"所有 RBG 的滞后都量化成 0 个快照（快照间隔 {snapshot_ms:g} ms，"
            f"平均 CSI 陈旧时长 {mean_staleness:.1f} ms）——这套配置下老化模型几乎不起作用，"
            f"结果与零时延完美 CSI 基本相同。")
    if (cfg.enabled and mean_staleness > 0 and t_c > 0
            and mean_staleness / t_c > 5):
        warn.append(
            f"平均 CSI 陈旧时长 {mean_staleness:.0f} ms 是相干时间 {t_c:.0f} ms 的 "
            f"{mean_staleness / t_c:.0f} 倍——预编码基本是在对一个无关的信道做匹配，"
            f"MU 增益会接近甚至低于 SU。")
    return {
        "config": cfg.as_dict(),
        "hop_order": [int(x) for x in order],
        "hop_order_source": source,
        "antenna_switching_profile": (
            "2T4R_ports01_then23_next_srs_opportunity"
            if cfg.srs_resource_allocation else None),
        "antenna_group_measurement_skew_ms": (
            5.0 if cfg.srs_resource_allocation else 0.0),
        "srs_transmissions_per_full_4port_sweep": (
            2 * int(cfg.hop_factor) if cfg.hopping
            and cfg.srs_resource_allocation else int(cfg.hop_factor)),
        "rbg_csi_staleness_ms": [round(float(x), 2) for x in stale_ms],
        "rbg_lag_snapshots": [int(x) for x in lags],
        "snapshot_ms": snapshot_ms,
        "mean_csi_staleness_ms": round(mean_staleness, 2),
        "max_csi_staleness_ms": round(
            float(max(stale_ms)) if len(stale_ms) else 0.0, 2),
        "speed_kmh": speed_kmh,
        "doppler_hz": round(speed_kmh / 3.6 * carrier_hz / 299_792_458.0, 2),
        "coherence_time_ms": round(t_c, 2) if np.isfinite(t_c) else None,
        "jakes_rho_at_mean_csi_staleness": round(
            jakes_correlation(mean_staleness, speed_kmh, carrier_hz), 4),
        "warnings": warn,
    }

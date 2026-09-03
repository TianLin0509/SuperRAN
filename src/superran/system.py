"""系统级仿真：连续 TTI、话务模型、PF 调度、体验速率。

**这一层回答的问题和链路级不一样。** 链路级问"这个信道能跑多快"，
系统级问"**这个小区里的用户实际体验到多快**"——后者要把话务的到达与结束、
调度器在多用户间的取舍与缓冲区排空算进去。两个模式都把一次用户 grant
视为一个单码字 TB，并只允许一次 IR/CC 重传：空口 MCS/RBG 数/rank/TBS 冻结，
BLER 从预置 NewTx 曲线推导。当前不展开 RV、LLR、并行 HARQ process 或标准时序。

本文件保留历史 ``legacy_v1`` 口径以复现旧结果；它的 ``tail/head_tail`` 是
项目早期的近似实现，不能再冒充 28.552。标准化的 DRB busy-period、首传起点、
末段排除与 Rel-19 小 burst 折算在 :mod:`superran.experience` 的
``experience_v2`` 路径实现，两种模式的结果会显式带版本号。

架构上分两相，这是能跑十万 TTI 的关键：

    第一相（贵）：逐 UE、逐信道快照，把 rank 1..4 的 SINR / MCS / 谱效
                  全部算好存成表。SVD 只在这里做。
    第二相（便宜）：TTI 主循环只查表 + 算 PF 度量 + 更新缓冲区，
                  没有任何矩阵运算。

实测 20000 TTI × 12 UE 在第二相里是秒级；如果把 SVD 放进主循环，
同样规模要几十分钟。
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import numpy as np

from . import beamforming as bf
from . import carrier as carrier_grid
from . import csi_aging as ca
from . import mumimo as mu
from . import power_control as pc
from . import rng as rg
from . import srs_resource as srsr

_EPS = 1e-12
_REPLICATION_PROCESS_STATE: dict[str, Any] = {}


def _finite_real(value: Any) -> bool:
    """True only for finite JSON-like real numbers; booleans are not numbers."""
    return (
        not isinstance(value, (bool, np.bool_))
        and isinstance(value, (int, float, np.integer, np.floating))
        and np.isfinite(float(value))
    )

#: S 时隙折合成多少个下行 TTI。大部分符号是下行，但有 GP 与上行符号。
#: **主循环与 dl_ratio 必须用同一个数**，否则实际调度的下行比报告的多。
S_SLOT_DL_FRACTION = 0.7

EvaluationMode = Literal["capacity", "experience"]
TrafficModel = Literal["full_buffer", "ftp3", "cbr", "bimodal", "mixed", "cdf"]
SchedAlgorithm = Literal["pf", "qos_pf", "rr", "max_ci", "edf", "qos_pf_edf"]
PfAccounting = Literal["auto", "legacy_best_se", "scheduled_tbs",
                       "acked_goodput", "legacy_fullband"]
PriorityWeighting = Literal["none", "inverse_priority"]
ThroughputTrim = Literal["none", "tail", "head_tail"]
SmallBurstPolicy = Literal["fractional_slot", "exclude"]
HarqCombining = Literal["ir", "cc"]
TtiTraceMode = Literal["off", "sampled", "full"]
FrequencySelectionMode = Literal["auto", "on", "off"]


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrafficClassConfig:
    """experience 模式的一类 DRB 业务。

    ``ue_share`` 决定多少 UE 使用该类业务；包长与到达过程是外生业务量，
    **不能用“希望占几个 RBG”反推**——实际需要的 RBG 还取决于 MCS/rank。
    ``priority`` 越小优先级越高，沿用 5QI 的方向；``resource_type`` 只在
    ``qos_pf`` 下决定是否启用 HOL/PDB 时延因子。
    """

    name: str
    ue_share: float
    file_bytes: int
    arrival_rate_hz: float
    priority: int = 50
    pdb_ms: float = 100.0
    resource_type: str = "non_GBR"
    cbr_mbps: float = 0.0
    is_small: bool = False
    packet_size_cdf: str | None = None
    interarrival_cdf: str | None = None
    packet_size_scale: float = 1.0
    interarrival_scale: float = 1.0
    interarrival_cdf_unit: Literal["ms", "s"] | None = None
    ue_ids: tuple[int, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrafficClassConfig:
        """从 MCP 的 JSON profile 构造配置；未知键硬失败，避免拼写错误静默失效。"""
        if not isinstance(value, dict):
            raise ValueError("traffic_profiles 的每一项必须是对象")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"Traffic profile 含未知字段：{unknown}")
        kwargs = dict(value)
        if "ue_ids" in kwargs:
            raw_ids = kwargs["ue_ids"]
            if not isinstance(raw_ids, (list, tuple)):
                raise ValueError("Traffic profile.ue_ids 必须是整数数组")
            kwargs["ue_ids"] = tuple(raw_ids)
        try:
            return cls(**kwargs)
        except TypeError as exc:
            raise ValueError(f"Traffic profile 缺少必填字段或类型错误：{exc}") from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "ue_share": self.ue_share,
            "file_bytes": self.file_bytes,
            "arrival_rate_hz": self.arrival_rate_hz,
            "priority": self.priority, "pdb_ms": self.pdb_ms,
            "resource_type": self.resource_type,
            "cbr_mbps": self.cbr_mbps, "is_small": self.is_small,
            "packet_size_cdf": self.packet_size_cdf,
            "interarrival_cdf": self.interarrival_cdf,
            "packet_size_scale": self.packet_size_scale,
            "interarrival_scale": self.interarrival_scale,
            "interarrival_cdf_unit": self.interarrival_cdf_unit,
            "ue_ids": list(self.ue_ids),
        }


@dataclass
class TrafficConfig:
    """话务模型。

    ``ftp3`` 是 3GPP 的 FTP Model 3（见 TR 36.814 Annex A.2.1.3.1 与
    TR 38.802 §A.2.1.3）：每个用户按泊松过程到达固定大小的文件，
    到达率控制负载。**它是评价体验速率的标准话务模型**——
    full buffer 下"体验速率"没有意义，因为缓冲区永远不空、没有 burst 边界。
    """

    model: TrafficModel = "ftp3"
    file_bytes: int = 500_000            # FTP3 常用 0.5 MB
    arrival_rate_hz: float = 2.0         # 每用户每秒到达几个文件
    cbr_mbps: float = 5.0                # CBR 模式的恒定速率
    # --- bimodal：现网话务按**占用 RBG 数**的分布，两头高中间低 ---
    # 用户 2026-08-02 给的现网口径：
    #   1 个 RBG（小包）约 30%、17 个 RBG（满带宽）约 30%、
    #   2~16 个 RBG 相对均匀分布，折合平均 PRB 利用率约 30%
    #   （另有约 30% 的 TTI 根本没有调度，0 个 RBG）
    # **这是"一次传输占多少频域资源"的分布，不是文件大小的分布。**
    # 我第一版理解成了文件大小，两者完全不同：前者决定单次调度的 TBS，
    # 后者决定一个 burst 要发多少个 TTI。
    p_small_rbg: float = 0.30            # 只占 1 个 RBG
    p_full_rbg: float = 0.30             # 占满全部 RBG
    p_idle_tti: float = 0.30             # 根本没有调度的 TTI 占比
    # --- experience_v2：外生定义的 mixed 业务（默认大小 UE 各半）---
    small_ue_share: float = 0.5
    small_file_bytes: int = 1_500
    small_arrival_rate_hz: float = 20.0
    small_priority: int = 20
    small_pdb_ms: float = 20.0
    large_priority: int = 80
    large_pdb_ms: float = 300.0
    # --- 经验 CDF：全局标量与所有 profile 的局部标量相乘 ---
    packet_size_cdf: str | None = None
    interarrival_cdf: str | None = None
    packet_size_scale: float = 1.0
    interarrival_scale: float = 1.0
    interarrival_cdf_unit: Literal["ms", "s"] = "ms"
    classes: tuple[TrafficClassConfig, ...] = ()

    def __post_init__(self) -> None:
        if self.model not in ("full_buffer", "ftp3", "cbr", "bimodal", "mixed", "cdf"):
            raise ValueError(f"未知话务模型 {self.model!r}")
        for name, value in (("file_bytes", self.file_bytes),
                            ("small_file_bytes", self.small_file_bytes)):
            if (isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, np.integer)) or int(value) < 1):
                raise ValueError(f"{name} 必须是至少为 1 的整数")
        for name, value in (
            ("arrival_rate_hz", self.arrival_rate_hz),
            ("small_arrival_rate_hz", self.small_arrival_rate_hz),
            ("cbr_mbps", self.cbr_mbps),
            ("small_pdb_ms", self.small_pdb_ms),
            ("large_pdb_ms", self.large_pdb_ms),
        ):
            if not _finite_real(value) or float(value) < 0:
                raise ValueError(f"{name} 必须是有限非负数")
        for name, value in (("small_priority", self.small_priority),
                            ("large_priority", self.large_priority)):
            if (isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, np.integer)) or int(value) < 1):
                raise ValueError(f"{name} 必须是至少为 1 的整数")
        if (not _finite_real(self.small_ue_share)
                or not 0.0 <= float(self.small_ue_share) <= 1.0):
            raise ValueError("small_ue_share 必须是 [0,1] 内的有限数")
        for name, value in (("packet_size_scale", self.packet_size_scale),
                            ("interarrival_scale", self.interarrival_scale)):
            if not _finite_real(value) or float(value) <= 0:
                raise ValueError(f"{name} 必须是有限正数")
        if self.interarrival_cdf_unit not in ("ms", "s"):
            raise ValueError("interarrival_cdf_unit 只支持 ms / s")
        for name, value in (("packet_size_cdf", self.packet_size_cdf),
                            ("interarrival_cdf", self.interarrival_cdf)):
            if value is not None and (
                    not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} 必须是非空路径字符串或 None")
        if self.model == "cdf" and not self.classes \
                and (self.packet_size_cdf is None or self.interarrival_cdf is None):
            raise ValueError("cdf 话务需要 packet_size_cdf 与 interarrival_cdf 两份文件")
        for name, value in (("p_small_rbg", self.p_small_rbg),
                            ("p_full_rbg", self.p_full_rbg),
                            ("p_idle_tti", self.p_idle_tti)):
            if not _finite_real(value) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} 必须是 [0,1] 内的有限数")
        if float(self.p_small_rbg) + float(self.p_full_rbg) > 1.0 + _EPS:
            raise ValueError("p_small_rbg + p_full_rbg 不能超过 1")
        class_names: set[str] = set()
        explicit_ue_ids: set[int] = set()
        for c in self.classes:
            if not isinstance(c.name, str) or not c.name.strip():
                raise ValueError("TrafficClass.name 不能为空")
            if c.name in class_names:
                raise ValueError(f"TrafficClass.name 不可重复：{c.name!r}")
            class_names.add(c.name)
            if not _finite_real(c.ue_share) or float(c.ue_share) < 0:
                raise ValueError(f"TrafficClass {c.name!r} 的 ue_share 必须有限非负")
            if (isinstance(c.file_bytes, (bool, np.bool_))
                    or not isinstance(c.file_bytes, (int, np.integer))
                    or int(c.file_bytes) < 1):
                raise ValueError(f"TrafficClass {c.name!r} 的 file_bytes 必须至少为 1")
            if (isinstance(c.priority, (bool, np.bool_))
                    or not isinstance(c.priority, (int, np.integer))
                    or int(c.priority) < 1):
                raise ValueError(f"TrafficClass {c.name!r} 的 priority 必须至少为 1")
            if (not isinstance(c.resource_type, str)
                    or not c.resource_type.strip()):
                raise ValueError(f"TrafficClass {c.name!r} 的 resource_type 不能为空")
            if not isinstance(c.is_small, (bool, np.bool_)):
                raise ValueError(f"TrafficClass {c.name!r} 的 is_small 必须是布尔值")
            for field_name, value in (
                    ("packet_size_scale", c.packet_size_scale),
                    ("interarrival_scale", c.interarrival_scale)):
                if not _finite_real(value) or float(value) <= 0:
                    raise ValueError(
                        f"TrafficClass {c.name!r} 的 {field_name} 必须是有限正数")
            if c.interarrival_cdf_unit not in (None, "ms", "s"):
                raise ValueError(
                    f"TrafficClass {c.name!r} 的 interarrival_cdf_unit 只支持 ms / s")
            for field_name, value in (("packet_size_cdf", c.packet_size_cdf),
                                      ("interarrival_cdf", c.interarrival_cdf)):
                if value is not None and (
                        not isinstance(value, str) or not value.strip()):
                    raise ValueError(
                        f"TrafficClass {c.name!r} 的 {field_name} "
                        "必须是非空路径字符串或 None")
            local_ids: set[int] = set()
            for ue_id in c.ue_ids:
                if (isinstance(ue_id, (bool, np.bool_))
                        or not isinstance(ue_id, (int, np.integer)) or int(ue_id) < 0):
                    raise ValueError(f"TrafficClass {c.name!r} 的 ue_ids 必须是非负整数")
                if int(ue_id) in local_ids or int(ue_id) in explicit_ue_ids:
                    raise ValueError(f"UE {int(ue_id)} 被重复分配到多个 traffic profile")
                local_ids.add(int(ue_id))
                explicit_ue_ids.add(int(ue_id))
            for field_name, value in (("arrival_rate_hz", c.arrival_rate_hz),
                                      ("cbr_mbps", c.cbr_mbps),
                                      ("pdb_ms", c.pdb_ms)):
                if not _finite_real(value) or float(value) < 0:
                    raise ValueError(
                        f"TrafficClass {c.name!r} 的 {field_name} 必须有限非负")
        if (self.classes
                and sum(float(c.ue_share) for c in self.classes) <= 0
                and not any(c.ue_ids for c in self.classes)):
            raise ValueError("TrafficClass.ue_share 之和必须大于 0")
        if self.model == "cdf" and self.classes:
            for c in self.classes:
                if c.packet_size_cdf is None and self.packet_size_cdf is None:
                    raise ValueError(
                        f"CDF profile {c.name!r} 缺少 packet_size_cdf")
                if c.interarrival_cdf is None and self.interarrival_cdf is None:
                    raise ValueError(
                        f"CDF profile {c.name!r} 缺少 interarrival_cdf")

    def as_dict(self) -> dict[str, Any]:
        d = {
            "model": self.model,
            "packet_size_scale": self.packet_size_scale,
            "interarrival_scale": self.interarrival_scale,
            "load_multiplier_vs_profile": round(
                self.packet_size_scale / self.interarrival_scale, 6),
        }
        if self.model == "ftp3":
            d |= {"file_bytes": self.file_bytes, "arrival_rate_hz": self.arrival_rate_hz,
                  "offered_load_mbps_per_ue":
                      round(self.file_bytes * self.packet_size_scale * 8
                            * self.arrival_rate_hz / self.interarrival_scale / 1e6, 3)}
        elif self.model == "cbr":
            d |= {"cbr_mbps": self.cbr_mbps}
        elif self.model == "bimodal":
            d |= {"p_small_rbg": self.p_small_rbg, "p_full_rbg": self.p_full_rbg,
                  "p_idle_tti": self.p_idle_tti,
                  "expected_prb_utilization": round(self.expected_prb_util(), 4),
                  "note": ("**按占用 RBG 数分布，不是按文件大小。** 现网两头高中间低："
                           "1 个 RBG 约 30%、满带宽约 30%、中间相对均匀，"
                           "另有约 30% 的 TTI 根本没有调度。"
                           "**小包测不到体验速率**——一个 TTI 就发完，"
                           "3GPP 掐尾口径下没有可测量的时间。")}
        elif self.model in ("mixed", "cdf"):
            d |= {
                "classes": [c.as_dict() for c in self.resolved_classes()],
                "packet_size_cdf": self.packet_size_cdf,
                "interarrival_cdf": self.interarrival_cdf,
                "interarrival_cdf_unit": self.interarrival_cdf_unit,
                "note": ("按 UE 分配外生业务类；包长、到达率、PDB 与优先级先定义，"
                         "CDF 与缩放标量决定到达过程，实际 RBG 占用仍由该用户当时的 "
                         "MCS/rank 与 TBS 反查决定。"),
            }
        return d

    def resolved_classes(self) -> tuple[TrafficClassConfig, ...]:
        """把简化输入解析成 experience 模式使用的业务类。"""
        if self.classes:
            return tuple(self.classes)
        if self.model == "mixed":
            share = min(max(float(self.small_ue_share), 0.0), 1.0)
            return (
                TrafficClassConfig(
                    name="small", ue_share=share,
                    file_bytes=max(1, int(self.small_file_bytes)),
                    arrival_rate_hz=max(0.0, float(self.small_arrival_rate_hz)),
                    priority=int(self.small_priority), pdb_ms=float(self.small_pdb_ms),
                    resource_type="delay_critical_GBR", is_small=True),
                TrafficClassConfig(
                    name="large", ue_share=1.0 - share,
                    file_bytes=max(1, int(self.file_bytes)),
                    arrival_rate_hz=max(0.0, float(self.arrival_rate_hz)),
                    priority=int(self.large_priority), pdb_ms=float(self.large_pdb_ms),
                    resource_type="non_GBR", is_small=False),
            )
        if self.model == "cdf":
            return (TrafficClassConfig(
                name="default", ue_share=1.0,
                file_bytes=max(1, int(self.file_bytes)),
                arrival_rate_hz=max(0.0, float(self.arrival_rate_hz)),
                priority=int(self.large_priority), pdb_ms=float(self.large_pdb_ms),
                packet_size_cdf=str(self.packet_size_cdf),
                interarrival_cdf=str(self.interarrival_cdf),
                interarrival_cdf_unit=self.interarrival_cdf_unit),)
        if self.model == "cbr":
            return (TrafficClassConfig(
                name="cbr", ue_share=1.0, file_bytes=1, arrival_rate_hz=0.0,
                priority=50, pdb_ms=100.0, resource_type="GBR",
                cbr_mbps=float(self.cbr_mbps)),)
        if self.model == "full_buffer":
            return (TrafficClassConfig(
                name="full_buffer", ue_share=1.0, file_bytes=1 << 50,
                arrival_rate_hz=0.0, priority=50, pdb_ms=0.0),)
        # ftp3：单一大流；bimodal 只属于 legacy 路径，experience 会显式拒绝。
        return (TrafficClassConfig(
            name="large", ue_share=1.0, file_bytes=max(1, int(self.file_bytes)),
            arrival_rate_hz=max(0.0, float(self.arrival_rate_hz)),
            priority=int(self.large_priority), pdb_ms=float(self.large_pdb_ms)),)

    def expected_prb_util(self, num_rbg: int = 17) -> float:
        """这套分布折合出来的平均 PRB 利用率。**这是设计意图，不是仿真结果。**

        ``p_idle_tti`` **不驱动任何仿真行为**——它只出现在这个解析式里。
        真实的空闲 TTI 来自"没有用户有数据"，由到达率与信道共同决定，
        由主循环如实测出来（``cell.occupancy``）。

        **强行按概率随机拒绝调度是假物理**：真实调度器不会在有数据时掷骰子
        放弃这个 TTI。所以这个旋钮保留为对标锚点而不是输入，
        实测与它偏离超过 10 个百分点时 :func:`simulate` 会在 ``notes`` 里告警——
        那说明到达率没调到位，而不是仿真错了。
        """
        p_mid = max(0.0, 1.0 - self.p_small_rbg - self.p_full_rbg)
        mid_mean = (2 + num_rbg - 1) / 2.0 / num_rbg     # 2~16 均匀的均值
        busy = (self.p_small_rbg * (1.0 / num_rbg)
                + self.p_full_rbg * 1.0 + p_mid * mid_mean)
        return float(busy * (1.0 - self.p_idle_tti))


@dataclass
class SchedulerConfig:
    """调度器。

    ``pf`` 比例公平：度量 ``R_inst / R_avg``，``R_avg`` 按指数窗更新::

        R_avg(t+1) = (1 - 1/Tc)·R_avg(t) + (1/Tc)·R_served(t)

    ``Tc`` 就是 ``pf_window_tti``。它决定公平的时间尺度：太小接近 max-C/I
    （只喂近点用户），太大接近轮询（不利用信道起伏）。
    """

    algorithm: SchedAlgorithm = "pf"
    pf_window_tti: int = 100
    # ``auto``：capacity/legacy 用 best_se，experience_v2 用实际 scheduled TBS。
    # acked_goodput 是研究型口径，NACK 时给 0 会反向抬高坏链路用户优先级，
    # 所以不作为默认。
    pf_accounting: PfAccounting = "auto"
    # auto: 链路表有逐 RBG SINR 就启用；on: 缺字段硬失败；off: 宽带/顺序基线。
    # 与 RB 功控正交，关闭功控不再让频率选择性凭空消失。
    frequency_selective: FrequencySelectionMode = "auto"
    # P0 资源账本先建模物理 RBG、每 RBG 层数和逻辑 layer-PRB；PDCCH/CCE
    # 按用户确认暂不建模。None 表示 num_PRB × max_layers 的自然上界。
    max_layers_per_rbg: int = 4
    max_logical_prb_per_tti: int | None = None
    # qos_pf = w(priority) * R_inst^beta / R_avg^alpha * delay_factor^gamma。
    # 默认 alpha=beta=1、gamma=0、w=1，严格退化成经典 PF；现场 EPF 定义
    # 未确认前，不把业务权重或时延权重偷偷打开。
    qos_avg_rate_exponent: float = 1.0       # alpha
    qos_instant_rate_exponent: float = 1.0   # beta
    qos_delay_exponent: float = 0.0          # gamma
    qos_priority_weighting: PriorityWeighting = "none"
    # --- EDF（包长感知）---
    # edf = TBS / Buffer × w(priority)；qos_pf_edf 是它与 qos_pf 的 蓝本原式
    # 加权混合 ((1−w)·scale·EPF + w·EDF) × w(priority)。两个分量不同量纲，
    # edf_mixed_epf_scale 就是 蓝本的 thp_filter 配平系数；未标定时中间的
    # 权重会被量级差吞掉，因此结果里必须报出两个分量的实测量级。
    edf_mixed_weight: float = 0.5        # w：0 = 纯 qos_pf，1 = 纯 edf
    edf_mixed_epf_scale: float = 1.0     # 蓝本 thp_filter
    # SRB 绝对优先加值。SuperRAN 不建模逻辑信道，只有显式声明
    # resource_type="signalling" 的业务类才会触发；不声明就永远不触发。
    srb_priority_boost: float = 5000.0
    # 时延兜底：队首等待达到该门限的用户无条件排到最前，组内按等待降序。
    # EDF 的分母是积压，越饿分母越大、优先级越低（与 PF 的 r_avg 越饿越小相反），
    # 靠算法自身不会恢复，必须外挂上界。None = 关闭，行为与不带兜底逐位相同。
    edf_starvation_hol_ms: float | None = None
    # --- OLLA（外环链路自适应）---
    # 发送端先由 CQI 门限 + BF Gain 反折 MCS，再叠加连续 MCS 域
    # OLLA，floor 后钳位。下面 ``*_db`` 是已发布 API 的历史字段名，
    # 只为参数兼容保留；值的物理单位已明确是连续 MCS index，不是 dB。
    # 步长按目标 BLER 不对称：ACK 加 up、NACK 减 down，
    # 稳态时 BLER → up/(up+down)。默认 down=None，进入仿真时从链路表
    # target_bler 反解；用户显式给值则完整保留，供消融/特殊研究。
    # 步长放大能加快收敛但会在稳态附近抖得更厉害——要快收敛就临时调大，
    # 出正式结论用基线值。
    olla_enabled: bool = True
    # **步长比决定稳态 BLER，与步长绝对值无关。** 推导见 :func:`olla_step_down_for`。
    # 用户 2026-08-02 给的现网粗估是 +0.01/−0.1，但那对应稳态 9.09% 而不是 10%；
    # 2026-08-03 他自己也指出 NACK 应该是 −0.09 左右。按目标 10% 精确解就是 −0.09。
    olla_step_up_db: float = 0.01        # 现网基线（用户 2026-08-02）
    olla_step_down_db: float | None = None
    olla_min_db: float = -20.0
    olla_max_db: float = 3.0
    # **加速收敛用的等比放大系数**（用户 2026-08-03 批准，条件是必须告知）。
    # 两个步长同乘一个数，稳态 BLER = up/(up+down) **完全不变**，
    # 变的只有收敛速度和稳态附近的抖动幅度。
    # 现网基线 +0.01/−0.1 在整数 MCS 档上常常压不动一档，8 秒仿真里
    # BLER 还停在 0.16~0.22；放大 10 倍能在同样时长内收敛，
    # 代价是稳态抖动更大。**出正式结论时用 1.0。**
    olla_speedup: float = 1.0
    # 可选的预启动专用加速。只在 KPI 窗口之前生效；进入测量窗口后恢复
    # ``olla_speedup``，避免为了 1 s 内收敛而把正式统计期的 OLLA 抖动也放大。
    # 默认 1.0 保持历史行为；正式短时体验实验若启用，必须随结果显式上报。
    olla_warmup_speedup: float = 1.0
    mu_enabled: bool = True              # 是否允许 MU 配对（SU/MU 自适应）
    max_mu_users: int = 2
    mu_rank_per_user: int = 2
    mu_corr_threshold: float = 0.7
    mu_precoder: str = "zf"
    # RZF 的每个复信道系数 CSI 误差方差。它必须来自估计器协方差或离线标定，
    # 不能在运行时偷看 h_true 逐快照反推；0.0 精确保持历史 ZF/RZF 噪声加载口径。
    mu_csi_error_variance: float = 0.0
    # MU 与 SU 分开维护 OLLA；步长可先复用同一基线，但状态绝不能共用。
    mu_olla_step_up_db: float = 0.01
    mu_olla_step_down_db: float | None = None

    def __post_init__(self) -> None:
        if self.algorithm not in (
                "pf", "qos_pf", "rr", "max_ci", "edf", "qos_pf_edf"):
            raise ValueError(f"未知调度器 {self.algorithm!r}")
        if (isinstance(self.pf_window_tti, (bool, np.bool_))
                or not isinstance(self.pf_window_tti, (int, np.integer))
                or int(self.pf_window_tti) < 1):
            raise ValueError("pf_window_tti 必须是至少为 1 的整数")
        if self.pf_accounting not in (
            "auto", "legacy_best_se", "scheduled_tbs", "acked_goodput",
            "legacy_fullband",
        ):
            raise ValueError(f"未知 PF 记账口径 {self.pf_accounting!r}")
        if self.frequency_selective not in ("auto", "on", "off"):
            raise ValueError("frequency_selective 只支持 auto / on / off")
        if (isinstance(self.max_layers_per_rbg, (bool, np.bool_))
                or not isinstance(self.max_layers_per_rbg, (int, np.integer))
                or int(self.max_layers_per_rbg) < 1):
            raise ValueError("max_layers_per_rbg 必须是至少为 1 的整数")
        if self.max_logical_prb_per_tti is not None and (
            isinstance(self.max_logical_prb_per_tti, (bool, np.bool_))
            or not isinstance(self.max_logical_prb_per_tti, (int, np.integer))
            or int(self.max_logical_prb_per_tti) < 1
        ):
            raise ValueError("max_logical_prb_per_tti 必须为 null 或正整数")
        if self.qos_priority_weighting not in ("none", "inverse_priority"):
            raise ValueError("qos_priority_weighting 只支持 none / inverse_priority")
        if not np.isfinite(self.edf_mixed_weight) or not (
                0.0 <= float(self.edf_mixed_weight) <= 1.0):
            raise ValueError("edf_mixed_weight 必须落在 [0, 1]")
        for name, value in (("edf_mixed_epf_scale", self.edf_mixed_epf_scale),
                            ("srb_priority_boost", self.srb_priority_boost)):
            if not np.isfinite(value) or float(value) < 0:
                raise ValueError(f"{name} 必须是有限非负数")
        if self.edf_starvation_hol_ms is not None and (
                not np.isfinite(self.edf_starvation_hol_ms)
                or float(self.edf_starvation_hol_ms) <= 0):
            raise ValueError("edf_starvation_hol_ms 必须为 null 或正数")
        for name, value in (
            ("qos_avg_rate_exponent", self.qos_avg_rate_exponent),
            ("qos_instant_rate_exponent", self.qos_instant_rate_exponent),
            ("qos_delay_exponent", self.qos_delay_exponent),
        ):
            if not np.isfinite(value) or float(value) < 0:
                raise ValueError(f"{name} 必须是有限非负数")
        for name, value in (
            ("olla_step_up_db", self.olla_step_up_db),
            ("mu_olla_step_up_db", self.mu_olla_step_up_db),
            ("olla_speedup", self.olla_speedup),
            ("olla_warmup_speedup", self.olla_warmup_speedup),
        ):
            if not np.isfinite(value) or float(value) <= 0:
                raise ValueError(f"{name} 必须是有限正数")
        for name, value in (
            ("olla_step_down_db", self.olla_step_down_db),
            ("mu_olla_step_down_db", self.mu_olla_step_down_db),
        ):
            if value is not None and (
                    not np.isfinite(value) or float(value) <= 0):
                raise ValueError(f"{name} 必须为 null（自动）或有限正数")
        if (not np.isfinite(self.olla_min_db) or not np.isfinite(self.olla_max_db)
                or float(self.olla_min_db) >= float(self.olla_max_db)):
            raise ValueError("olla_min_db / olla_max_db 必须有限且 min < max")
        if (isinstance(self.max_mu_users, (bool, np.bool_))
                or not isinstance(self.max_mu_users, (int, np.integer))
                or int(self.max_mu_users) < 1):
            raise ValueError("max_mu_users 必须是至少为 1 的整数")
        if (isinstance(self.mu_rank_per_user, (bool, np.bool_))
                or not isinstance(self.mu_rank_per_user, (int, np.integer))
                or int(self.mu_rank_per_user) < 1):
            raise ValueError("mu_rank_per_user 必须是至少为 1 的整数")
        if (not np.isfinite(self.mu_corr_threshold)
                or not 0.0 <= float(self.mu_corr_threshold) <= 1.0):
            raise ValueError("mu_corr_threshold 必须是 [0,1] 内的有限数")
        if self.mu_precoder not in ("zf", "rzf"):
            raise ValueError("mu_precoder 只支持 zf / rzf")
        if (not np.isfinite(self.mu_csi_error_variance)
                or float(self.mu_csi_error_variance) < 0):
            raise ValueError("mu_csi_error_variance 必须是有限非负数")

    @property
    def step_up(self) -> float:
        return self.olla_step_up_db * max(float(self.olla_speedup), _EPS)

    @property
    def step_down(self) -> float:
        if self.olla_step_down_db is None:
            raise RuntimeError("OLLA down 步长尚未按 target_bler 解析")
        return self.olla_step_down_db * max(float(self.olla_speedup), _EPS)

    @property
    def mu_step_up(self) -> float:
        return self.mu_olla_step_up_db * max(float(self.olla_speedup), _EPS)

    @property
    def mu_step_down(self) -> float:
        if self.mu_olla_step_down_db is None:
            raise RuntimeError("MU OLLA down 步长尚未按 target_bler 解析")
        return self.mu_olla_step_down_db * max(float(self.olla_speedup), _EPS)

    def resolved_for_target(self, target_bler: float) -> SchedulerConfig:
        """用目标 BLER 补齐自动步长，不覆盖用户显式值。"""
        target = float(target_bler)
        su_down = (
            olla_step_down_for(target, float(self.olla_step_up_db))
            if self.olla_step_down_db is None else float(self.olla_step_down_db)
        )
        mu_down = (
            olla_step_down_for(target, float(self.mu_olla_step_up_db))
            if self.mu_olla_step_down_db is None else float(self.mu_olla_step_down_db)
        )
        out = replace(
            self,
            olla_step_down_db=su_down,
            mu_olla_step_down_db=mu_down,
        )
        # 标来源：本实例的 down 步长是"留空按目标自动反解"还是用户显式值——
        # 显式 override 与自动反解在结果里必须可区分，不能只报"已解析"。
        out._olla_down_auto = (  # noqa: SLF001
            self.olla_step_down_db is None, self.mu_olla_step_down_db is None)
        return out

    def as_dict(self) -> dict[str, Any]:
        resolved = (
            self.olla_step_down_db is not None
            and self.mu_olla_step_down_db is not None
        )
        auto = getattr(self, "_olla_down_auto", None)

        def _down_src(down: float | None, was_auto: bool | None) -> str:
            # None（未解析）与反解产物都按目标自动推导；只有用户显式给值
            # 且不是反解结果时才算 override。
            if down is None or was_auto is True:
                return "auto_from_target_bler"
            return "explicit_user_override"

        d: dict[str, Any] = {
            "algorithm": self.algorithm, "pf_window_tti": self.pf_window_tti,
            "pf_accounting": self.pf_accounting,
            "frequency_selective": self.frequency_selective,
            "max_layers_per_rbg": int(self.max_layers_per_rbg),
            "max_logical_prb_per_tti": self.max_logical_prb_per_tti,
            "pdcch_cce_model": "not_modelled_by_explicit_scope",
            "qos_avg_rate_exponent": self.qos_avg_rate_exponent,
            "qos_instant_rate_exponent": self.qos_instant_rate_exponent,
            "qos_delay_exponent": self.qos_delay_exponent,
            "qos_priority_weighting": self.qos_priority_weighting,
            # 这四个改了数字就会变，必须跟着结果一起走：少了它们，
            # kpi_compare 会把 w=0（纯 qos_pf）和 w=1（纯 edf）两臂报成"配置无差异"。
            "edf_mixed_weight": float(self.edf_mixed_weight),
            "edf_mixed_epf_scale": float(self.edf_mixed_epf_scale),
            "srb_priority_boost": float(self.srb_priority_boost),
            "edf_starvation_hol_ms": (
                None if self.edf_starvation_hol_ms is None
                else float(self.edf_starvation_hol_ms)),
            "mu_enabled": self.mu_enabled, "max_mu_users": self.max_mu_users,
            "mu_rank_per_user": self.mu_rank_per_user,
            "mu_corr_threshold": self.mu_corr_threshold,
            "mu_precoder": self.mu_precoder,
            "mu_csi_error_variance": self.mu_csi_error_variance,
            "olla_enabled": self.olla_enabled,
            "olla_domain": "continuous_mcs_index",
            "olla_parameter_name_compatibility": (
                "legacy *_db input names are retained; values are MCS-index offsets"
            ),
            "olla_baseline_steps_mcs": [self.olla_step_up_db,
                                          self.olla_step_down_db],
            "olla_baseline_steps_db": [self.olla_step_up_db, self.olla_step_down_db],
            "olla_down_source": _down_src(
                self.olla_step_down_db, None if auto is None else auto[0]),
            "olla_speedup": self.olla_speedup,
            "olla_warmup_speedup": self.olla_warmup_speedup,
            "olla_effective_steps_db": (
                [round(self.step_up, 6), round(self.step_down, 6)]
                if resolved else [round(self.step_up, 6), None]
            ),
            "olla_effective_steps_mcs": (
                [round(self.step_up, 6), round(self.step_down, 6)]
                if resolved else [round(self.step_up, 6), None]
            ),
            "mu_olla_baseline_steps_mcs": [self.mu_olla_step_up_db,
                                             self.mu_olla_step_down_db],
            "mu_olla_baseline_steps_db": [self.mu_olla_step_up_db,
                                           self.mu_olla_step_down_db],
            "mu_olla_down_source": _down_src(
                self.mu_olla_step_down_db, None if auto is None else auto[1]),
            "mu_olla_effective_steps_db": (
                [round(self.mu_step_up, 6), round(self.mu_step_down, 6)]
                if resolved else [round(self.mu_step_up, 6), None]
            ),
            "mu_olla_effective_steps_mcs": (
                [round(self.mu_step_up, 6), round(self.mu_step_down, 6)]
                if resolved else [round(self.mu_step_up, 6), None]
            ),
            "olla_target_bler": (
                round(self.olla_step_up_db / (self.olla_step_up_db
                                               + self.olla_step_down_db), 3)
                if self.olla_step_down_db is not None else None
            ),
            "mu_olla_target_bler": (
                round(self.mu_olla_step_up_db / (self.mu_olla_step_up_db
                                                  + self.mu_olla_step_down_db), 3)
                if self.mu_olla_step_down_db is not None else None
            )}
        if self.olla_speedup != 1.0 and resolved:
            d["olla_speedup_warning"] = (
                f"**OLLA 步长已等比放大 {self.olla_speedup:g} 倍**"
                f"（{self.olla_step_up_db:g}/{self.olla_step_down_db:g} → "
                f"{self.step_up:g}/{self.step_down:g}）。稳态 BLER 不变"
                f"（仍是 {self.olla_step_up_db / (self.olla_step_up_db + self.olla_step_down_db):.1%}），"
                f"但稳态附近抖动更大。这是为了在短仿真里收敛，"
                f"**出正式结论请把 olla_speedup 设回 1.0**。")
        return d


@dataclass
class KpiConfig:
    """KPI 统计口径。**换口径数字会明显变，所以它必须跟着结果一起走。**"""

    trim: ThroughputTrim = "tail"
    min_burst_tti: int = 2               # 短于这个的 burst 不计入体验速率
    # 体验仿真默认预启动 1 s，让 OLLA、PF 均值与 SRS 全带扫描先收敛。
    # warmup_tti 是兼容/测试用显式覆盖；None 时由 warmup_s 和 numerology 换算。
    warmup_tti: int | None = None
    warmup_s: float = 1.0
    small_burst_policy: SmallBurstPolicy = "fractional_slot"
    # KPI 对比工作台默认保留一个可控大小的代表性 TTI 轨迹：一半均匀覆盖测量窗，
    # 一半留给 MU/NACK/HARQ/多 UE 等关键事件。full 只在用户明确要求时开启，
    # 否则 5 个算法 x 8 次重复 x 10000 TTI 会把结果合同膨胀到不可交付。
    tti_trace_mode: TtiTraceMode = "sampled"
    tti_trace_max_points: int = 256

    def __post_init__(self) -> None:
        if self.trim not in ("none", "tail", "head_tail"):
            raise ValueError("trim 只支持 none / tail / head_tail")
        if (isinstance(self.min_burst_tti, (bool, np.bool_))
                or not isinstance(self.min_burst_tti, (int, np.integer))
                or int(self.min_burst_tti) < 1):
            raise ValueError("min_burst_tti 必须是至少为 1 的整数")
        if self.warmup_tti is not None and (
            isinstance(self.warmup_tti, (bool, np.bool_))
            or not isinstance(self.warmup_tti, (int, np.integer))
            or int(self.warmup_tti) < 0
        ):
            raise ValueError("warmup_tti 必须是非负整数或 None")
        if not np.isfinite(self.warmup_s) or float(self.warmup_s) < 0:
            raise ValueError("warmup_s 必须是有限非负数")
        if self.small_burst_policy not in ("fractional_slot", "exclude"):
            raise ValueError("small_burst_policy 只支持 fractional_slot / exclude")
        if self.tti_trace_mode not in ("off", "sampled", "full"):
            raise ValueError("tti_trace_mode 只支持 off / sampled / full")
        if (
            isinstance(self.tti_trace_max_points, (bool, np.bool_))
            or not isinstance(self.tti_trace_max_points, (int, np.integer))
            or int(self.tti_trace_max_points) < 1
        ):
            raise ValueError("tti_trace_max_points 必须是至少为 1 的整数")

    def resolve_warmup_tti(self, tti_ms: float) -> int:
        if not np.isfinite(tti_ms) or float(tti_ms) <= 0:
            raise ValueError("tti_ms 必须是有限正数")
        if self.warmup_tti is not None:
            return int(self.warmup_tti)
        return max(0, int(round(float(self.warmup_s) * 1000.0 / float(tti_ms))))

    def as_dict(self) -> dict[str, Any]:
        return {"trim": self.trim, "min_burst_tti": self.min_burst_tti,
                "warmup_s": self.warmup_s,
                "warmup_tti_override": self.warmup_tti,
                "small_burst_policy": self.small_burst_policy,
                "tti_trace_mode": self.tti_trace_mode,
                "tti_trace_max_points": int(self.tti_trace_max_points),
                "experience_standard": "3GPP TS 28.552 Rel-19",
                "trim_note": {
                    "none": "legacy：从到达算到清空，含最后一个 TTI",
                    "tail": ("legacy：从到达算、排除清空 TTI；它包含首传前排队，"
                             "不是 TS 28.552 的 T2 起点"),
                    "head_tail": ("legacy 掐头去尾，最接近 28.552："
                                  "从首次调度开始并排除清空 TTI；"
                                  "experience_v2 改用 DRB busy-period 事件记录器"),
                }[self.trim]}


@dataclass
class NeighborLoadConfig:
    """邻区负载。**不能假设所有小区都是 full buffer。**

    ChannelHub 的几何 SINR 是按**所有邻区都在发**算出来的，等于 100% PRB
    利用率。真实网络 5G 典型平均 PRB 利用率是 10% / 30% / 50%——
    邻区没在发的那些 PRB 上，本小区用户根本不受干扰。
    按 full buffer 算会把干扰放大到不真实的程度。

    折算方式：干扰功率按利用率 ``η`` 线性缩放，噪声不变::

        SINR' = S / (η·I + N)，其中 I = S/SIR、N = S/SNR

    ``prb_utilization = 1.0`` 时退化成原来的 full buffer 行为。

    **当前入口仍只支持全网配同一个负载值**（用户 2026-08-03 定的口径）。
    这不再是数据缺失：当前 ChannelHub 会额外保存逐 slot/逐小区的下行干扰
    分母项，RB 功控已经逐小区使用它。这里保留标量只是尚未开放逐小区负载表
    的产品接口；不要再解释成“几何 SIR 只有聚合量”。

    ``jitter`` 让**实际生效值**在配置值的 ±5% 内随机波动。这不是装饰：
    现网负载本来就是逐 TTI 抖的，一个恒定值会让所有快照的干扰完全一样，
    结果看起来比真实情况干净。波动是乘性的，``0.3 → [0.285, 0.315]``。
    """

    prb_utilization: float = 0.3          # 5G 典型：0.1 / 0.3 / 0.5
    jitter: float = 0.05                  # 实际值在 ±5% 内波动（用户 2026-08-03）
    seed: int = 0

    def __post_init__(self) -> None:
        if (not np.isfinite(self.prb_utilization)
                or not 0.0 <= float(self.prb_utilization) <= 1.0):
            raise ValueError("prb_utilization 必须是 [0,1] 内的有限数")
        if (not np.isfinite(self.jitter)
                or not 0.0 <= float(self.jitter) <= 1.0):
            raise ValueError("neighbor load jitter 必须是 [0,1] 内的有限数")
        if (isinstance(self.seed, (bool, np.bool_))
                or not isinstance(self.seed, (int, np.integer)) or int(self.seed) < 0):
            raise ValueError("neighbor load seed 必须是非负整数")

    def realized(self, n: int, rng: np.random.Generator | None = None) -> np.ndarray:
        """抽 ``n`` 个实际生效的利用率。``jitter=0`` 时就是 n 份配置值。"""
        if (isinstance(n, (bool, np.bool_))
                or not isinstance(n, (int, np.integer)) or int(n) < 0):
            raise ValueError("n 必须是非负整数")
        r = rng if rng is not None else np.random.default_rng(self.seed)
        base = float(self.prb_utilization)
        if self.jitter <= 0:
            return np.full(n, base)
        lo, hi = base * (1.0 - self.jitter), base * (1.0 + self.jitter)
        return np.clip(r.uniform(lo, hi, size=n), 0.0, 1.0)

    def as_dict(self) -> dict[str, Any]:
        return {"prb_utilization": self.prb_utilization,
                "jitter": self.jitter,
                "realized_range": [round(self.prb_utilization * (1 - self.jitter), 4),
                                   round(self.prb_utilization * (1 + self.jitter), 4)],
                "scope": "network_wide_single_value",
                "note": ("邻区按这个 PRB 利用率折算干扰；1.0 等于假设所有邻区"
                         "full buffer（ChannelHub 几何 SINR 的原始假设）。"
                         f"实际生效值逐快照在 ±{self.jitter * 100:.0f}% 内波动。"
                         "当前只支持全网统一值——几何 SIR 是聚合量，"
                         "拿不到逐邻区的贡献，没法映射逐小区负载。")}


def apply_neighbor_load(sinr_db: float, sir_db: float, utilization: float) -> float:
    """把几何 SINR 按邻区负载折算。返回新的 SINR（dB）。

    推导：令 S=1，则 I = 1/SIR_lin、N = 1/SINR_lin − I。
    邻区只在 ``η`` 比例的 PRB 上发，干扰变成 ``η·I``，噪声不变::

        SINR' = 1 / (η·I + N)

    ``sir_db`` 拿不到（单小区哨兵 49.9）时原样返回——没有干扰可折算。
    """
    if (not np.isfinite(utilization)
            or not 0.0 <= float(utilization) <= 1.0):
        raise ValueError("utilization 必须是 [0,1] 内的有限数")
    u = float(utilization)
    if u >= 1.0 or not np.isfinite(sir_db) or sir_db >= 49.0:
        return float(sinr_db)
    s_lin = 10.0 ** (float(sinr_db) / 10.0)
    i_lin = 10.0 ** (-float(sir_db) / 10.0)
    n_lin = 1.0 / s_lin - i_lin
    if n_lin <= 0:                        # 口径对不上时不硬算
        return float(sinr_db)
    return float(10.0 * np.log10(1.0 / (u * i_lin + n_lin)))


@dataclass
class SystemConfig:
    evaluation_mode: EvaluationMode = "capacity"
    duration_s: float = 5.0
    scs_khz: int = 30                    # 30 kHz → slot 0.5 ms
    num_rbg: int = 17
    rb_per_rbg: int = 16
    # Type-0 首尾 RBG 可能不足名义 P。None 保持历史等长行为；显式 tuple
    # 则是每组真实 PRB 数，TBS、功控和利用率全部以它为准。
    rbg_prb_sizes: tuple[int, ...] | None = None
    tdd_pattern: str = "DDDSU"           # 只统计 D 时隙
    # 每个 TB 最多一次重传。IR：半谱效等效 MCS（默认）；CC：SINR +10log10(2)。
    harq_combining: HarqCombining = "ir"
    # **信道快照之间隔多久，由 ChannelHub 决定，不能拍脑袋。**
    # internal_sim.py:3252 把 UE 每个"时隙"推进
    #     speed × max(srs_periodicity, csirs_periodicity) × slot_duration_s
    # 默认 10 × 0.5 ms = **5 ms**——它们是连续的 SRS/CSI-RS 机会，不是连续 TTI。
    #
    # 我原来拍了 10.0，差 2 倍；更要命的是在量 CSI 老化时把「滞后 1 个快照」
    # 读成了 0.5 ms（一个 TTI），实际是 5 ms，**整整差 10 倍**。
    # 验证方法：Jakes 的 ρ(τ)=|J0(2π·fd·τ)| 首零点在 τ=2.405/(2π·fd)，
    # 3 km/h 时是 53 ms；实测极小值落在第 10 个快照 → 每快照 5.3 ms，对上。
    snapshot_update_ms: float = 5.0
    # 发射权功率约束：系统/TDD 默认 NEBF（每天线 P/M 且用满总功率）；
    # EBF/PEBF 仍可显式选择，定义见 beamforming.py。
    power_constraint: str = "nebf"
    # 与上面的空间约束正交：逐 RB 连续功率倍率，默认关闭/均匀分配。
    rb_power_control: pc.RbPowerControlConfig = field(
        default_factory=pc.RbPowerControlConfig)
    seed: int = 0

    def __post_init__(self) -> None:
        if self.evaluation_mode not in ("capacity", "experience"):
            raise ValueError("evaluation_mode 只支持 capacity / experience")
        if not np.isfinite(self.duration_s) or float(self.duration_s) <= 0:
            raise ValueError("duration_s 必须是有限正数")
        if (isinstance(self.scs_khz, (bool, np.bool_))
                or not isinstance(self.scs_khz, (int, np.integer))
                or int(self.scs_khz) <= 0):
            raise ValueError("scs_khz 必须是正整数")
        for name, value in (("num_rbg", self.num_rbg),
                            ("rb_per_rbg", self.rb_per_rbg)):
            if (isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, np.integer)) or int(value) < 1):
                raise ValueError(f"{name} 必须是至少为 1 的整数")
        if self.rbg_prb_sizes is None:
            self.rbg_prb_sizes = tuple(
                int(self.rb_per_rbg) for _ in range(int(self.num_rbg))
            )
        else:
            try:
                raw_sizes = tuple(self.rbg_prb_sizes)
            except TypeError as exc:
                raise ValueError("rbg_prb_sizes 必须是正整数数组") from exc
            if any(
                isinstance(value, (bool, np.bool_))
                or not isinstance(value, (int, np.integer))
                for value in raw_sizes
            ):
                raise ValueError("rbg_prb_sizes 每项都必须是正整数，不能是布尔值或小数")
            sizes = tuple(int(value) for value in raw_sizes)
            if len(sizes) != int(self.num_rbg) or any(value < 1 for value in sizes):
                raise ValueError(
                    "rbg_prb_sizes 长度必须等于 num_rbg，且每项至少为 1"
                )
            self.rbg_prb_sizes = sizes
        pattern = str(self.tdd_pattern).upper()
        if not pattern or any(slot not in "DSU" for slot in pattern):
            raise ValueError("tdd_pattern 只允许 D/S/U 且不能为空")
        if not any(slot in "DS" for slot in pattern):
            raise ValueError("下行系统仿真的 tdd_pattern 至少需要一个 D 或 S 时隙")
        if str(self.harq_combining).lower() not in ("ir", "cc"):
            raise ValueError("harq_combining 只支持 ir / cc")
        if (not np.isfinite(self.snapshot_update_ms)
                or float(self.snapshot_update_ms) <= 0):
            raise ValueError("snapshot_update_ms 必须是有限正数")
        if str(self.power_constraint).lower() not in ("ebf", "pebf", "nebf"):
            raise ValueError("power_constraint 只支持 ebf / pebf / nebf")
        if not isinstance(self.rb_power_control, pc.RbPowerControlConfig):
            raise ValueError("rb_power_control 必须是 RbPowerControlConfig")
        if (self.rb_power_control.enabled
                and int(self.rb_power_control.num_rb)
                != self.num_rb):
            raise ValueError(
                "RB 功控 profile 长度与系统带宽不一致："
                f"{self.rb_power_control.num_rb} vs "
                f"{self.num_rb}")
        if (isinstance(self.seed, (bool, np.bool_))
                or not isinstance(self.seed, (int, np.integer)) or int(self.seed) < 0):
            raise ValueError("seed 必须是非负整数")

    @property
    def tti_ms(self) -> float:
        return 1.0 / (self.scs_khz / 15.0)          # 15→1ms, 30→0.5ms, 60→0.25ms

    @property
    def num_tti(self) -> int:
        return int(round(self.duration_s * 1000.0 / self.tti_ms))

    @property
    def num_rb(self) -> int:
        return int(sum(self.rbg_prb_sizes or ()))

    @property
    def rbg_boundaries(self) -> tuple[tuple[int, int], ...]:
        cursor = 0
        out: list[tuple[int, int]] = []
        for width in self.rbg_prb_sizes or ():
            out.append((cursor, cursor + int(width)))
            cursor += int(width)
        return tuple(out)

    @property
    def dl_ratio(self) -> float:
        """TDD 图案里下行时隙占比。S 时隙按 0.7 个下行折算（大部分符号是 D）。"""
        p = self.tdd_pattern.upper() or "D"
        return (p.count("D") + S_SLOT_DL_FRACTION * p.count("S")) / len(p)

    def as_dict(self) -> dict[str, Any]:
        return {"evaluation_mode": self.evaluation_mode,
                "model_version": ("experience_v2" if self.evaluation_mode == "experience"
                                  else "legacy_v1"),
                "duration_s": self.duration_s, "scs_khz": self.scs_khz,
                "tti_ms": self.tti_ms, "num_tti": self.num_tti,
                "num_rbg": self.num_rbg, "rb_per_rbg": self.rb_per_rbg,
                "rbg_prb_sizes": list(self.rbg_prb_sizes or ()),
                "rbg_boundaries": [list(pair) for pair in self.rbg_boundaries],
                "num_rb": self.num_rb,
                "tdd_pattern": self.tdd_pattern,
                "harq_combining": str(self.harq_combining).lower(),
                "max_retransmissions": 1,
                "tb_error_unit": "one user grant in one TTI = one single-codeword TB",
                "dl_slot_ratio": round(self.dl_ratio, 4),
                "snapshot_update_ms": self.snapshot_update_ms,
                "power_constraint": self.power_constraint,
                "rb_power_control": self.rb_power_control.as_dict(),
                "seed": self.seed}


# ---------------------------------------------------------------------------
# 第一相：把信道压成查表
# ---------------------------------------------------------------------------
@dataclass
class MuPairLink:
    """两个 UE 在各快照下的 MU rank-2 链路差分；TTI 主循环只查表。"""

    users: tuple[int, int]
    rank_per_user: int
    true_sinr_db: np.ndarray             # [snapshot,2]，用于 BLER
    predicted_sinr_db: np.ndarray        # [snapshot,2]，基站 CSI 视角
    corr_loss_tx_db: np.ndarray          # [snapshot,2]，MCS 公式中的 CorrLoss
    corr_loss_true_db: np.ndarray        # [snapshot,2]，物理对账
    power_loss_db: float
    correlation: np.ndarray              # [snapshot] 宽带归一化相关系数
    leakage_ratio: np.ndarray            # [snapshot] 真实残余 MU 干扰
    predicted_leakage_ratio: np.ndarray  # [snapshot]
    power_constraint: str
    precoder: str
    true_sinr_rbg_db: np.ndarray | None = None       # [snapshot,2,RBG]
    predicted_sinr_rbg_db: np.ndarray | None = None  # [snapshot,2,RBG]
    corr_loss_tx_rbg_db: np.ndarray | None = None    # [snapshot,2,RBG]
    corr_loss_true_rbg_db: np.ndarray | None = None  # [snapshot,2,RBG]
    receiver: str = "per_user_lmmse"
    csi_error_variance: float = 0.0
    power_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    rzf_regularization: list[dict[str, Any]] = field(default_factory=list)

    def side(self, ue: int) -> int:
        if int(ue) == self.users[0]:
            return 0
        if int(ue) == self.users[1]:
            return 1
        raise KeyError(f"UE {ue} 不在 MU pair {self.users}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "users": list(self.users), "rank_per_user": self.rank_per_user,
            "power_loss_db": round(float(self.power_loss_db), 6),
            "power_loss_scope": (
                "equal stream-power split: two rank-2 MU users use P/4 per stream "
                "versus rank-2 SU P/2; normalization residual is in CorrLoss"),
            "correlation_mean": float(np.mean(self.correlation)),
            "correlation_max": float(np.max(self.correlation)),
            "corr_loss_tx_db_mean": [float(x) for x in np.mean(
                self.corr_loss_tx_db, axis=0)],
            "corr_loss_true_db_mean": [float(x) for x in np.mean(
                self.corr_loss_true_db, axis=0)],
            "leakage_ratio_mean": float(np.mean(self.leakage_ratio)),
            "power_constraint": self.power_constraint,
            "precoder": self.precoder,
            "receiver": self.receiver,
            "frequency_sinr_resolution": (
                "RBG" if self.true_sinr_rbg_db is not None else "wideband"),
            "csi_error_variance": float(self.csi_error_variance),
            "rzf_regularization": list(self.rzf_regularization),
        }


@dataclass
class UeLinkTable:
    """一个 UE 在各个信道快照下、各个 rank 的链路能力。TTI 主循环只读它。"""

    ue: int
    sinr_db: np.ndarray                  # [snapshot, rank] 用户级 SINR
    mcs: np.ndarray                      # [snapshot, rank]
    se: np.ndarray                       # [snapshot, rank] = rank × MCS 谱效
    best_rank: np.ndarray                # [snapshot] rank 自适应选中的秩（1-indexed）
    best_se: np.ndarray                  # [snapshot]
    geo_sinr_db: float
    outage: np.ndarray | None = None     # [snapshot] 该快照下根本调度不动
    iot_db: float = float("nan")         # 干扰抬升：(I+N)/N，>20 dB 算高干扰
    iot_sample_valid: float = 1.0        # 这个 UE 有多少比例的**快照**算得出 IoT
    sir_db: float = float("nan")
    # 这是历史字段名，不是物理 ``SINR_NEBF/PEBF/EBF``。它保存
    # ``Gamma(MCS(CQI))+BF Gain`` 这个 gNB 侧 AMC 预测坐标；真实接收 SINR 在
    # ``sinr_db/sinr_rbg_db``，并且只有后者可以查 BLER。
    sinr_tx_db: np.ndarray | None = None  # [snapshot, rank] legacy alias: AMC prediction
    mcs_tx: np.ndarray | None = None      # [snapshot, rank] 发送端据此定的 MCS
    # 逐 RBG 真值供实际 grant 的 BLER；功控打开时内部保留 272 RB 后再按
    # 16 RB 线性聚合，不能拿全带 SINR 代替一个 1-RBG 小包。
    sinr_rbg_db: np.ndarray | None = None     # [snapshot,rank,RBG]
    sinr_tx_rbg_db: np.ndarray | None = None  # legacy alias: per-RBG AMC prediction
    # --- 发送侧 SINR 的拆解，供审计与说明书引用 ---
    bf_gain_db: np.ndarray | None = None   # [snapshot, rank] SVD − PMI（基站自算）
    pmi_sinr_db: np.ndarray | None = None  # [snapshot, rank] Type I 权下的用户级 SINR
    # 历史字段保存 0..14 的映射表行；标准上报 codepoint 另存 0..15，
    # 其中 codepoint 0 为 out-of-range、表行 0 对应 codepoint 1。
    cqi_index: np.ndarray | None = None    # [rank] legacy internal row
    cqi_index_per_snapshot: np.ndarray | None = None  # [snapshot,rank] legacy row
    reported_cqi_codepoint: np.ndarray | None = None  # [rank] 4-bit CQI 0..15
    reported_cqi_codepoint_per_snapshot: np.ndarray | None = None  # [snapshot,rank]
    csi_lag_snapshots: np.ndarray | None = None  # [snapshot] 平均 CSI 滞后（快照数）
    # 2T4R 两个天线组分别在相邻 SRS 机会测量；保留逐 RBG lag 才能审计
    # 64x4 是否真的由两个不同时间的 64x2 片段拼成。
    csi_lag_snapshots_by_antenna_group_rbg: np.ndarray | None = None  # [S,2,RBG]
    # **基站以为的谱效**：rank 自适应与 PF 调度都只能看它，不能看真实值。
    # 拿真实谱效去调度等于让基站预知信道，老化损失会被凭空抹掉一大半。
    # 零时延时它与 ``se`` / ``best_se`` 逐位相同。
    se_gnb: np.ndarray | None = None       # [snapshot, rank]
    best_se_gnb: np.ndarray | None = None  # [snapshot]
    mcs_table: int = 3                     # experience_v2 当前只接受预置表 3
    # 建表时用的目标 BLER。**主循环必须读它，不能自己写死 0.1**——
    # 否则 build_link_tables(target_bler=...) 选出来的 rank 和主循环选出来的 MCS
    # 是按两个不同判据来的，而这种不一致在结果里完全看不出来。
    target_bler: float = 0.1
    power_constraint: str = "nebf"
    frequency_rows_per_rbg: int = 1
    frequency_rbg_boundaries: tuple[tuple[int, int], ...] | None = None
    serving_cell_index: int | None = None
    rb_power_control_fingerprint: str | None = None
    rb_power_coupling_diagnostics: dict[str, Any] | None = None
    power_diagnostics: list[dict[str, Any]] | None = None  # [snapshot] 选中 rank
    csi_report_source_snapshot: np.ndarray | None = None   # [snapshot]
    csi_report_period_ms: float | None = None
    srs_resource_assignment: srsr.SrsResourceAssignment | None = None
    precoding_csi_source: str = "evaluation_channel"
    # MU 建表只保存 RBG 粒度；避免 TTI 主循环反复做 SVD/矩阵求逆。
    h_true_rbg: np.ndarray | None = field(default=None, repr=False)  # [S,F,BS,UE]
    h_prec_rbg: np.ndarray | None = field(default=None, repr=False)  # [S,F,BS,UE]
    noise_power_by_snapshot: np.ndarray | None = field(default=None, repr=False)
    mu_links: dict[int, MuPairLink] = field(default_factory=dict, repr=False)

    @property
    def amc_predicted_sinr_db(self) -> np.ndarray | None:
        """Preferred name for the legacy ``sinr_tx_db`` decision coordinate."""
        return self.sinr_tx_db

    @property
    def amc_predicted_sinr_rbg_db(self) -> np.ndarray | None:
        """Preferred name for the legacy ``sinr_tx_rbg_db`` decision coordinate."""
        return self.sinr_tx_rbg_db


def la_sel(sinr_db: float, table: int, target_bler: float) -> int:
    """选 MCS 的薄封装，建表时用。"""
    from . import linkadapt as la  # noqa: PLC0415

    return int(la.select_mcs(float(sinr_db), table=table,
                             target_bler=target_bler).index)


def olla_step_down_for(target_bler: float, step_up: float = 0.01) -> float:
    """给定目标 IBLER 与 ACK 步长，反解 NACK 步长。

    OLLA 是个随机逼近：ACK 加 ``s_up``、NACK 减 ``s_down``。偏置在期望漂移
    为零时稳态::

        (1 − p)·s_up = p·s_down   ⟹   p = s_up / (s_up + s_down)
                                  ⟹   s_down = s_up · (1 − p) / p

    **稳态 BLER 只取决于两个步长的比**，与绝对值无关——绝对值只影响收敛速度
    和稳态附近的抖动（这正是 ``olla_speedup`` 能等比放大的原因）。

    目标 10%、``s_up = 0.01`` 时 ``s_down = 0.09``。
    **现网常说的 +0.01/−0.1 其实对应 9.09% 而不是 10%**，差得不多但不是一回事。

    注意这是**连续偏置**下的理想稳态。实际 MCS 是整数档，偏置要累积到跨过
    一整档才会真正改变发送，所以实测 BLER 会围绕理论值抖，且与信道的
    档位间隔有关——:func:`simulate` 会把实测值报出来，别只信理论值。
    """
    p = float(target_bler)
    if not 0.0 < p < 1.0:
        raise ValueError(f"target_bler 必须在 (0,1)，收到 {target_bler}")
    return float(step_up) * (1.0 - p) / p


def _type1_precoder(
    h_rbg: np.ndarray,
    rank: int,
    *,
    n_h: int | None = None,
    n_v: int | None = None,
    port_order: str | None = None,
    vertical_index_order: str | None = None,
) -> np.ndarray:
    """Type-I-style 单面板**宽带列码本近似**，强制到指定 rank。

    接受 ``[F,BS,UE]`` 或 ``[T,F,BS,UE]``，返回 ``[F,BS,rank]``。

    宽带意味着全带共用一个权（``compute_precoder`` 在 ``E[H H^H]`` 上搜列码本
    再广播回各 RBG），这正对应用户口径里的**全带 CQI**。当前实现复用了
    38.214 Type-I 单面板的过采样 DFT/双极化列，但多层 PMI 用增量贪心列选择，
    **不是完整枚举 38.214 的多层矩阵码本**；不做子带 CQI、不做频选调度。
    """
    from . import linklevel as ll  # noqa: PLC0415

    hh = np.asarray(h_rbg)
    if hh.ndim == 3:
        hh = hh[None]
    if hh.ndim != 4:
        raise ValueError(f"h_rbg 应为 [F,BS,UE] 或 [T,F,BS,UE]，收到 {hh.shape}")
    return ll.compute_precoder(
        hh,
        method="type1",
        max_rank=int(rank),
        rank_threshold=0.0,
        n_h=n_h,
        n_v=n_v,
        port_order=port_order,
        vertical_index_order=vertical_index_order,
    ).w


def _reported_cqi_of(sinr_db: float, target_bler: float) -> int:
    """用户级 PMI-SINR → 上报 4-bit CQI codepoint 0..15。"""
    from . import linkadapt as la  # noqa: PLC0415

    return int(la.select_reported_cqi(
        float(sinr_db), target_bler=float(target_bler), mcs_table=3))


def _cqi_threshold_sinr(cqi_index: int, target_bler: float) -> float:
    """内部 CQI → 离散映射 MCS → 目标 BLER 的 NewTx SINR 门限。"""
    from . import bler_curves as bc  # noqa: PLC0415
    from . import linkadapt as la  # noqa: PLC0415

    m = la.internal_cqi_to_mcs(int(cqi_index), mcs_table=3)
    return float(bc.get_curve(int(m["mcs"]), "newtx").required_sinr_db(float(target_bler)))


def _nan_safe(fn, values, *args) -> float:
    """全是 NaN 时返回 NaN 而不是让 numpy 抛 RuntimeWarning。"""
    v = [x for x in values if np.isfinite(x)]
    return float(fn(v, *args)) if v else float("nan")


def interference_free_sinr(sinr_db: float, sir_db: float) -> float:
    """从含干扰的几何 SINR 反推**无干扰**的 SNR（同口径）。

    令 S=1：``I = 1/SIR``、``I+N = 1/SINR``，所以 ``N = 1/SINR − 1/SIR``，
    无干扰时 ``SNR = 1/N``。

    **这是发送端一开始看到的世界。** 发送端不知道瞬时干扰，按无干扰
    （或 CQI 反馈的粗略统计）选 MCS；接收端实打实吃着干扰，SINR 更低，
    于是误码，OLLA 把偏置压下来。干扰越大，OLLA 收敛到的偏置越负——
    这就是"干扰越大、接收 SINR 越低、吞吐越低"的第一性路径。

    当前 first-party ``snr_dB`` 与该反解值同为单 RB、预数字波束口径；这里
    仍从 SINR/SIR 反解，是为了兼容 SNR 定标不受控的历史/外部数据。
    """
    if not (np.isfinite(sinr_db) and np.isfinite(sir_db)) or sir_db >= 49.0:
        return float(sinr_db)
    s_lin = 10.0 ** (float(sinr_db) / 10.0)
    i_lin = 10.0 ** (-float(sir_db) / 10.0)
    n_lin = 1.0 / s_lin - i_lin
    if n_lin <= 0:
        return float(sinr_db)
    return float(-10.0 * np.log10(n_lin))


def _iot(sinr_db: float, sir_db: float) -> float:
    """IoT = SIR/(SIR−SINR)（线性域）。**只能用同口径的两个量**。

    体现的是干扰主导还是噪声主导：IoT 接近 0 dB 说明几乎没有干扰、
    完全是噪声受限；密集城区经常到 20 dB 以上，那时干扰是绝对主导，
    再加发射功率也没用（信号和干扰同步上涨）。
    """
    from . import interference as itf  # noqa: PLC0415

    if not (np.isfinite(sinr_db) and np.isfinite(sir_db)):
        return float("nan")
    # **SIR < SINR 物理上不可能**（SINR = S/(I+N) ≤ S/I = SIR）。
    # 出现它只有一个原因：两个量不同口径。实测 num_slots_per_sample=4 时
    # sinr_dB 是各 slot 的 dB 均值、sir_dB 只取最后一个 slot，
    # 20 个样本里 12 个的 sir−sinr 是负的（最小 −9.5 dB），IoT 直接算成 inf。
    # 单时隙下同一场景 IoT 中位 32.2 dB —— 正对应现网密集城区 >20 dB。
    # 宁可返回 nan 也不给一个偏低到误导人的数。
    if sir_db < sinr_db - 1e-6:
        return float("nan")
    return float(np.asarray(itf.iot_db(sinr_db, sir_db)).item())


def snapshot_interval_ms(cfg: dict[str, Any]) -> float:
    """由配置算出信道快照之间隔多久（ms）。

    新数据集把 ``sample_interval_s`` 作为独立真相源。它不能由0.5-ms NR slot、
    两条2T SRS腿的5-ms间隔或10-ms四端口SRS周期反推；混用这些时钟会让
    CSI老化、多普勒和移动距离整体错一个数量级。

    旧数据没有显式字段时才保留历史回退：
    ``max(srs_periodicity, csirs_periodicity) × slot_duration``。
    """
    explicit = cfg.get("sample_interval_s")
    if explicit is not None:
        if isinstance(explicit, (bool, np.bool_)):
            raise ValueError("sample_interval_s 必须是有限正秒数")
        try:
            interval_s = float(explicit)
        except (TypeError, ValueError) as exc:
            raise ValueError("sample_interval_s 必须是有限正秒数") from exc
        if not np.isfinite(interval_s) or interval_s <= 0.0:
            raise ValueError("sample_interval_s 必须是有限正秒数")
        return 1000.0 * interval_s
    scs_khz = carrier_grid.scs_khz_from_config(cfg)
    slot_ms = 15.0 / float(scs_khz)
    periods: list[int] = []
    for name in ("srs_periodicity", "csirs_periodicity"):
        raw = cfg.get(name, 10)
        if (isinstance(raw, (bool, np.bool_))
                or not isinstance(raw, (int, np.integer)) or int(raw) < 1):
            raise ValueError(f"{name} 必须是至少为 1 的 slot 整数")
        periods.append(int(raw))
    per = max(periods)
    return slot_ms * per


def group_samples_by_ue(n_samples: int, num_ues: int) -> list[list[int]]:
    """把数据集里的样本按 UE 分组。

    **样本数不等于用户数。** ChannelHub 一次生成 ``num_samples`` 个样本，
    分布在 ``num_ues`` 个 UE 位置上（轮转分配，每 UE
    ``num_samples/num_ues`` 个）。把每个样本当成一个独立用户，
    小区就被塞进了 4 倍的人——实测 40 样本 / 10 UE 的配置下，
    每用户谱效从应有的 0.32 掉到 0.08，**看起来像边缘用户被饿死**，
    其实是分母大了 4 倍。

    同一个 UE 的多个样本是**时间相关的**（多普勒就是从相邻样本的位移算的），
    所以它们正好当这个 UE 的信道快照序列用。
    """
    n_ue = max(1, min(int(num_ues), int(n_samples)))
    return [list(range(u, int(n_samples), n_ue)) for u in range(n_ue)]


def build_link_tables(
    h_users: list[np.ndarray],
    geo_sinr_db: list[float],
    *,
    h_for_precoding_users: list[np.ndarray] | None = None,
    geo_sir_db: list[float] | None = None,
    neighbor_load: float = 1.0,
    max_rank: int = mu.SU_MAX_RANK,
    table: int = 3,
    target_bler: float = 0.1,
    num_snapshots: int = 1,
    num_ues: int | None = None,
    rb_per_rbg: int = 16,
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
    csi: ca.CsiConfig | None = None,
    srs_cell_ids: list[int] | tuple[int, ...] | int | None = None,
    srs_pci_mod3: list[int] | tuple[int, ...] | int | None = None,
    snapshot_ms: float = 5.0,
    load_jitter_rng: np.random.Generator | None = None,
    neighbor_load_jitter: float = 0.05,
    precoder: str = "svd",
    power_constraint: str = "nebf",
    mu_enabled: bool = False,
    mu_rank_per_user: int = mu.MU_MAX_RANK,
    mu_precoder: str = "zf",
    mu_csi_error_variance: float = 0.0,
    rb_power_control: pc.RbPowerControlConfig | None = None,
    power_geometry: pc.DownlinkPowerGeometry | None = None,
    bs_panel: list[int] | tuple[int, int, int] | None = None,
    port_order: str | None = None,
    vertical_index_order: str | None = None,
) -> list[UeLinkTable]:
    """第一相：逐 UE 把 rank 1..max_rank 的 SINR / MCS / 谱效全部算好。

    **SVD 只在这里做。** 主循环里再也不碰矩阵——这是十万 TTI 能跑完的原因。

    ``h_users[i]`` 形状 ``[T, RB, BS, UE]``；``T > 1`` 时把每个时隙当一个
    独立快照（ChannelHub 的多时隙是时间相关的，正好用来表达信道起伏）。

    ``h_for_precoding_users`` 是基站实际可获得的估计信道（通常是数据集的
    ``h_est``）。给定后，SRS 滞后、PMI/CQI、BF 与 MU 预编码只看它，``h_users``
    只用于真实接收评估；不给时沿用 ``h_users``，保持旧调用的理想估计基线。

    ``num_ues`` 给定时，按 :func:`group_samples_by_ue` 把样本合并成这么多个
    用户，同一 UE 的多个样本当作它的快照序列。**不给的话每个样本算一个用户**
    ——那通常不是你想要的，见该函数的说明。

    ``csi`` 给定且 ``enabled`` 时走 CSI 老化：预编码用滞后若干个快照的信道，
    评估用当前快照。逐 RBG 的滞后由 SRS 周期与跳频决定，见 :mod:`csi_aging`。
    不给的话是零时延完美 CSI——**那是个上界，不是现网**。

    ``csi.srs_resource_allocation``开启时，每个2T4R UE从本地固定载波资源池
    分到相邻两个SRS机会：ports0/1与2/3分别使用一个2-port leg。BBL排除，
    4个CS切成两块，17个frequency id进入候选；只能使用本PCI模3颜色。
    全局周期从配置下限开始自动选择10/20/40 ms中最短可容纳值。两个leg的
    offset与频域相位分别进入端口组CSI老化，再拼成64×4。``srs_cell_ids`` /
    ``srs_pci_mod3``是结果UE粒度。RE级接收由 :mod:`srs_waveform` 提供；本函数
    没有邻区UE到受害gNB的UL cross-link，因此不会在这里伪造导频波形污染或
    静默替换数据集已有的 ``h_est``。

    ``load_jitter_rng`` 只用来抽邻区负载的逐快照抖动，抖动幅度由
    ``neighbor_load_jitter`` 给出；它**应当来自
    ``rng.RngBook(...).generator("neighbor_load")``**，不要在调用处写
    ``default_rng(seed + 常数)``（NumPy 并行随机数文档把它标成
    "UNSAFE! Do not do this!"）。

    **除了它，本函数完全确定性**——SVD、码本搜索、MCS 查表都不含随机。
    这正是 :func:`simulate_replications` 能"建一次表、重跑 n 次主循环"的前提；
    ``tests/test_rng.py`` 第 8 节逐位断言了这条。

    ``precoder`` 决定**实际发射权**：``svd``（每个 RBG 的单快照 SVD 特征波束）
    或 ``type1``（Type-I-style 宽带列码本近似）。注意 Type I 参照权在两种模式下都要算——
    它是 CQI 与 BF Gain 的参照系；``precoder="type1"`` 只是把它同时当成发射权，
    于是 BF Gain 恒为 0（发射权就是参照权）。

    ``rb_power_control`` 开启时必须同时给 ``power_geometry``。此时保留逐 RB
    信道分辨率，并按 ``q_serving*S / (N + sum(q_k*I_k))`` 同时更新期望信号与
    每个邻区的干扰；聚合 SIR 不足以完成这一步，因此缺数据会硬失败。
    """
    if precoder not in ("svd", "type1"):
        raise ValueError(f"precoder 只支持 'svd' / 'type1'，收到 {precoder!r}")
    if str(power_constraint).lower() not in ("ebf", "pebf", "nebf"):
        raise ValueError("power_constraint 只支持 ebf / pebf / nebf")
    power_cfg = rb_power_control or pc.RbPowerControlConfig()
    if not isinstance(power_cfg, pc.RbPowerControlConfig):
        raise ValueError("rb_power_control 必须是 RbPowerControlConfig")
    power_enabled = bool(power_cfg.enabled)
    if power_enabled and power_geometry is None:
        raise ValueError(
            "已开启 RB 功控，但没有逐小区 S/I/N 功率分解；不能用聚合 SINR 近似")
    h_eval_users = [np.asarray(x) for x in h_users]
    if not h_eval_users:
        raise ValueError("h_users 至少需要一个信道样本")
    first_rb = int(h_eval_users[0].shape[-3])
    for i, value in enumerate(h_eval_users):
        if value.ndim not in (3, 4) or int(value.shape[-3]) != first_rb:
            raise ValueError(
                f"样本 {i} 的信道应为同 RB 数的 [RB,BS,UE]/[T,RB,BS,UE]，"
                f"收到 {value.shape}"
            )
    resolved_boundaries = (
        carrier_grid.validate_boundaries(first_rb, rbg_boundaries)
        if rbg_boundaries is not None
        else carrier_grid.uniform_boundaries(first_rb, rb_per_rbg)
    )
    if csi is not None:
        ca.validate_hopping_grid(
            csi, [stop - start for start, stop in resolved_boundaries]
        )
    explicit_precoding_csi = h_for_precoding_users is not None
    if h_for_precoding_users is None:
        h_precoding_users = h_eval_users
    else:
        if len(h_for_precoding_users) != len(h_eval_users):
            raise ValueError(
                "h_for_precoding_users 与 h_users 的样本数必须一致")
        h_precoding_users = [np.asarray(x) for x in h_for_precoding_users]
        for i, (he, hp) in enumerate(zip(
                h_eval_users, h_precoding_users, strict=True)):
            if he.shape != hp.shape:
                raise ValueError(
                    f"样本 {i} 的评估/预编码信道形状不一致：{he.shape} vs {hp.shape}")
    h_users = h_eval_users
    sir_in = list(geo_sir_db) if geo_sir_db is not None else [float("nan")] * len(h_users)
    # 逐快照的几何量。**合并成一个均值会把动态范围压掉一半**——
    # 实测 40 个样本的 SINR 跨度 20.7 dB，按 UE 取均值后只剩 11.9 dB，
    # "5% 边缘用户"于是变成了一个中等信道的用户，边缘 MCS 报 8.2 而不是 <5。
    # 现在每个快照保留自己的 SINR/SIR，只有对外报的标量才取均值。
    per_snap_sinr: list[list[float]] = [[float(x)] for x in geo_sinr_db]
    per_snap_sir: list[list[float]] = [[float(x)] for x in sir_in]
    # Each row is (S, N, I_by_cell, serving_cell).  It stays aligned with the
    # flattened channel snapshots through the sample->UE grouping below.
    per_snap_power: list[list[tuple[float, float, np.ndarray, int]]] | None = None
    power_profiles: np.ndarray | None = None
    if power_enabled:
        assert power_geometry is not None
        if power_geometry.num_samples != len(h_users):
            raise ValueError(
                "逐小区功率分解与信道样本数不一致："
                f"{power_geometry.num_samples} vs {len(h_users)}")
        if int(power_cfg.num_rb) != first_rb:
            raise ValueError(
                f"RB 功控 profile 是 {power_cfg.num_rb} RB，但信道是 {first_rb} RB")
        power_profiles = power_cfg.resolve_profiles(power_geometry.num_cells)
        per_snap_power = []
        per_snap_sinr = []
        per_snap_sir = []
        for sample, h_sample in enumerate(h_users):
            arr_sample = np.asarray(h_sample)
            n_slot = int(arr_sample.shape[0]) if arr_sample.ndim == 4 else 1
            intf_rows = power_geometry.slots_for_sample(sample, n_slot)
            signal = float(power_geometry.signal_power_mw[sample])
            noise = float(power_geometry.thermal_noise_power_mw[sample])
            serving = int(power_geometry.serving_cell_index[sample])
            rows = [(signal, noise, np.asarray(intf_rows[t], dtype=float), serving)
                    for t in range(n_slot)]
            per_snap_power.append(rows)
            per_snap_sinr.append([
                float(10.0 * np.log10(
                    signal / max(noise + float(np.sum(row[2])), _EPS)))
                for row in rows])
            per_snap_sir.append([
                (float(10.0 * np.log10(
                    signal / max(float(np.sum(row[2])), _EPS)))
                 if float(np.sum(row[2])) > 0 else 49.9)
                for row in rows])
    if num_ues is not None and num_ues < len(h_users):
        groups = group_samples_by_ue(len(h_users), num_ues)
        merged_h, merged_p, merged_g, merged_s = [], [], [], []
        merged_power: list[list[tuple[float, float, np.ndarray, int]]] = []
        for g in groups:
            per_sample = [np.asarray(h_users[i]) for i in g]
            per_prec = [np.asarray(h_precoding_users[i]) for i in g]
            merged_h.append(np.concatenate(
                [x.reshape(-1, *x.shape[-3:]) for x in per_sample], axis=0))
            merged_p.append(np.concatenate(
                [x.reshape(-1, *x.shape[-3:]) for x in per_prec], axis=0))
            if power_enabled:
                assert per_snap_power is not None
                merged_g.append([value for sample in g
                                 for value in per_snap_sinr[sample]])
                merged_s.append([value for sample in g
                                 for value in per_snap_sir[sample]])
                merged_power.append([value for sample in g
                                     for value in per_snap_power[sample]])
            else:
                # 每个样本贡献 T 个快照，它的几何量在这 T 个快照上重复
                merged_g.append([float(geo_sinr_db[i])
                                 for i, x in zip(g, per_sample, strict=True)
                                 for _ in range(x.shape[0] if x.ndim == 4 else 1)])
                merged_s.append([float(sir_in[i])
                                 for i, x in zip(g, per_sample, strict=True)
                                 for _ in range(x.shape[0] if x.ndim == 4 else 1)])
        h_users = merged_h
        h_precoding_users = merged_p
        per_snap_sinr, per_snap_sir = merged_g, merged_s
        if power_enabled:
            per_snap_power = merged_power
        geo_sinr_db = [_nan_safe(np.mean, v) for v in merged_g]
        sir_in = [_nan_safe(np.mean, v) for v in merged_s]

    # **邻区不是 full buffer。** 按 PRB 利用率折算干扰后再建表——
    # 折算必须发生在算 SINR/MCS/rank 之前，事后乘系数是补不回来的。
    load_cfg = NeighborLoadConfig(
        prb_utilization=float(neighbor_load),
        jitter=float(neighbor_load_jitter))
    loads = [
        (load_cfg.realized(len(gs), rng=load_jitter_rng)
         if neighbor_load < 1.0 and load_jitter_rng is not None
         else np.full(len(gs), float(neighbor_load)))
        for gs in per_snap_sinr
    ]
    power_scales: list[list[np.ndarray]] | None = None
    power_iot: list[list[float]] | None = None
    power_reported_geo: list[list[float]] | None = None
    power_coupling_diag: list[dict[str, Any]] | None = None
    serving_cell_by_ue: list[int | None] | None = None
    if power_enabled:
        assert per_snap_power is not None and power_profiles is not None
        power_scales, power_iot, power_reported_geo = [], [], []
        power_coupling_diag, serving_cell_by_ue = [], []
        anchor_sinr_all: list[list[float]] = []
        loaded_sir_all: list[list[float]] = []
        for ue, (rows, us) in enumerate(zip(per_snap_power, loads, strict=True)):
            scales_u: list[np.ndarray] = []
            iot_u: list[float] = []
            geo_u: list[float] = []
            intf_delta: list[float] = []
            anchor_u: list[float] = []
            sir_u: list[float] = []
            serving_set = {int(row[3]) for row in rows}
            if len(serving_set) != 1:
                raise ValueError(
                    f"UE {ue} 的时间快照跨越多个 serving cell {sorted(serving_set)}；"
                    "当前系统仿真没有实现切换，RB 功控拒绝混表")
            serving = int(next(iter(serving_set)))
            serving_cell_by_ue.append(serving)
            for row, util in zip(rows, us, strict=True):
                signal, noise, intf, serving_row = row
                coupled = pc.couple_rb_power(
                    signal_power_mw=signal, thermal_noise_power_mw=noise,
                    interference_power_per_cell_mw=intf,
                    serving_cell_index=serving_row, profiles=power_profiles,
                    neighbor_utilization=float(util))
                scales_u.append(coupled.channel_power_scale)
                iot_u.append(float(np.mean(coupled.iot_db)))
                geo_lin = 10.0 ** (coupled.geometric_sinr_db / 10.0)
                geo_rbg_db = np.asarray([
                    10.0 * np.log10(max(float(np.mean(
                        geo_lin[start:stop])), _EPS))
                    for start, stop in resolved_boundaries])
                geo_u.append(float(np.mean(geo_rbg_db)))
                base_i = max(coupled.baseline_denominator_mw - noise, _EPS)
                anchor_u.append(float(10.0 * np.log10(
                    signal / max(coupled.baseline_denominator_mw, _EPS))))
                sir_u.append(float(10.0 * np.log10(signal / base_i))
                             if base_i > _EPS else 49.9)
                intf_delta.append(float(10.0 * np.log10(
                    max(float(np.mean(coupled.controlled_interference_mw)), _EPS)
                    / base_i)))
            power_scales.append(scales_u)
            power_iot.append(iot_u)
            power_reported_geo.append(geo_u)
            anchor_sinr_all.append(anchor_u)
            loaded_sir_all.append(sir_u)
            flat_scale = np.concatenate(scales_u)
            power_coupling_diag.append({
                "ue": int(ue),
                "serving_cell_index": serving,
                "channel_power_scale_min": float(np.min(flat_scale)),
                "channel_power_scale_max": float(np.max(flat_scale)),
                "mean_interference_delta_db": float(np.mean(intf_delta)),
                "mean_final_geometry_sinr_db": float(np.mean(geo_u)),
                "formula": "q_serving*S / (N + eta*sum(q_interferer*I_cell))",
            })
        per_snap_sinr = anchor_sinr_all
        per_snap_sir = loaded_sir_all
        geo_sinr_db = [_nan_safe(np.mean, x) for x in power_reported_geo]
        sir_in = [_nan_safe(np.mean, x) for x in loaded_sir_all]
    elif neighbor_load < 1.0:
        # **SINR 和 SIR 必须一起折算。** 干扰降到 η 倍，SIR 就升高 1/η；
        # 只改 SINR 会让后面的 IoT = SIR/(SIR−SINR) 用两个不同口径的量算，
        # 直接报 inf。这和把不同物理域的标量硬拼在一起是同一类错。
        #
        # 负载**逐快照抖动**（NeighborLoadConfig.jitter，默认 ±5%）时，
        # 每个快照拿自己那份 η——所以下面是逐元素而不是一个全局标量。
        def _one(g: float, sr: float, u: float) -> tuple[float, float]:
            # 只有同口径且 SIR>SINR 才能从两者反解出非负噪声。早期代码在
            # 口径错配时保留 SINR、却仍单独抬高 SIR，制造了新的不自洽量。
            if np.isfinite(g) and np.isfinite(sr) and sr < 49.0:
                n_lin = 10.0 ** (-g / 10.0) - 10.0 ** (-sr / 10.0)
                if n_lin <= 0:
                    return g, sr
            u_db = 10.0 * np.log10(max(u, 1e-12))
            new_sir = (sr - u_db) if np.isfinite(sr) and sr < 49.0 else sr
            return apply_neighbor_load(g, sr, u), new_sir

        _pairs = [[_one(g, sr, float(u)) for g, sr, u in zip(gs, ss, us, strict=True)]
                  for gs, ss, us in zip(per_snap_sinr, per_snap_sir, loads, strict=True)]
        per_snap_sinr = [[p[0] for p in row] for row in _pairs]
        per_snap_sir = [[p[1] for p in row] for row in _pairs]
        geo_sinr_db = [_nan_safe(np.mean, v) for v in per_snap_sinr]
        sir_in = [_nan_safe(np.mean, v) for v in per_snap_sir]

    n_link_ue = len(h_users)
    resolved_serving_cell_values: list[int] | None = None
    if srs_cell_ids is not None:
        if isinstance(srs_cell_ids, (int, np.integer)) and not isinstance(
                srs_cell_ids, (bool, np.bool_)):
            raw_cell_values = [int(srs_cell_ids)] * n_link_ue
        else:
            try:
                raw_cell_values = list(srs_cell_ids)
            except TypeError as exc:
                raise ValueError(
                    "srs_cell_ids 必须是整数或逐 UE 整数数组") from exc
        if len(raw_cell_values) != n_link_ue or any(
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, np.integer))
            for value in raw_cell_values
        ):
            raise ValueError(f"srs_cell_ids 必须包含 {n_link_ue} 个逐 UE 整数")
        resolved_serving_cell_values = [int(value) for value in raw_cell_values]

    srs_assignments: tuple[srsr.SrsResourceAssignment, ...] | None = None
    if csi is not None and csi.enabled and bool(csi.srs_resource_allocation):
        def _expand_srs_values(
            name: str,
            raw: list[int] | tuple[int, ...] | int | None,
            default: list[int],
        ) -> list[int]:
            if raw is None:
                values = list(default)
            elif isinstance(raw, (int, np.integer)) and not isinstance(
                    raw, (bool, np.bool_)):
                values = [int(raw)] * n_link_ue
            else:
                try:
                    values = list(raw)  # type: ignore[arg-type]
                except TypeError as exc:
                    raise ValueError(f"{name} 必须是整数或逐 UE 整数数组") from exc
            if len(values) != n_link_ue or any(
                isinstance(value, (bool, np.bool_))
                or not isinstance(value, (int, np.integer))
                for value in values
            ):
                raise ValueError(f"{name} 必须包含 {n_link_ue} 个逐 UE 整数")
            return [int(value) for value in values]

        default_cells = (
            [int(value) if value is not None else 0 for value in serving_cell_by_ue]
            if serving_cell_by_ue is not None else
            (list(resolved_serving_cell_values)
             if resolved_serving_cell_values is not None else [0] * n_link_ue)
        )
        cell_values = _expand_srs_values(
            "srs_cell_ids", resolved_serving_cell_values, default_cells)
        pci_values = _expand_srs_values(
            "srs_pci_mod3", srs_pci_mod3,
            [int(value) % 3 for value in cell_values])
        if any(value not in (0, 1, 2) for value in pci_values):
            raise ValueError("srs_pci_mod3 每项必须是 0 / 1 / 2")
        port_values = [int(np.asarray(value).shape[-1]) for value in h_users]
        srs_assignments = srsr.allocate_basic_srs_resources(
            list(range(n_link_ue)), period_ms=float(csi.srs_period_ms),
            n_ports_by_ue=port_values, cell_ids=cell_values,
            pci_mod3_by_ue=pci_values, hopping=bool(csi.hopping),
            adaptive_period=bool(csi.srs_period_adaptive))

    effective_csi = csi
    if srs_assignments:
        selected_periods = {float(row.period_ms) for row in srs_assignments}
        if len(selected_periods) != 1:
            raise RuntimeError(
                "SRS resource allocator violated the one-global-period contract: "
                f"{sorted(selected_periods)}"
            )
        assert csi is not None
        effective_csi = replace(
            csi, srs_period_ms=float(next(iter(selected_periods))))

    out: list[UeLinkTable] = []
    aging = csi is not None and csi.enabled
    for i, (h, h_precoding) in enumerate(zip(
            h_users, h_precoding_users, strict=True)):
        hh = np.asarray(h)
        hh_prec = np.asarray(h_precoding)
        snaps = [hh[t:t + 1] for t in range(hh.shape[0])] if hh.ndim == 4 else [hh]
        prec_snaps = ([hh_prec[t:t + 1] for t in range(hh_prec.shape[0])]
                      if hh_prec.ndim == 4 else [hh_prec])
        if len(prec_snaps) != len(snaps):
            raise ValueError(f"UE {i} 的评估/预编码快照数不一致")
        if len(snaps) < num_snapshots:
            # 时隙不够就循环复用，**不伪造起伏**。注意复用当前不在结果里
            # 标注：server 路径不传 num_snapshots（默认 1），本分支仅测试
            # 可达；若未来开放，需要先补 snapshots_reused 披露字段。
            snaps = [snaps[t % len(snaps)] for t in range(num_snapshots)]
            prec_snaps = [prec_snaps[t % len(prec_snaps)] for t in range(num_snapshots)]
        n_s = len(snaps)

        # --- 压成二维再降粒度，老化与 BF Gain 都在这个粒度上算 ---
        _2d = [np.asarray(x).mean(axis=0) if np.asarray(x).ndim == 4 else np.asarray(x)
               for x in snaps]
        _2d_prec = [
            np.asarray(x).mean(axis=0) if np.asarray(x).ndim == 4 else np.asarray(x)
            for x in prec_snaps]
        if power_enabled:
            assert power_scales is not None
            scaled_eval: list[np.ndarray] = []
            scaled_prec: list[np.ndarray] = []
            for s, (he_row, hp_row) in enumerate(zip(_2d, _2d_prec, strict=True)):
                scale = np.asarray(
                    power_scales[i][s % len(power_scales[i])], dtype=float)
                if he_row.shape[0] != scale.size or hp_row.shape[0] != scale.size:
                    raise ValueError(
                        f"UE {i} snapshot {s} 的 RB 数与功控 profile 不一致："
                        f"{he_row.shape[0]}/{hp_row.shape[0]} vs {scale.size}")
                amp = np.sqrt(np.maximum(scale, 0.0))[:, None, None]
                scaled_eval.append(he_row * amp)
                scaled_prec.append(hp_row * amp)
            # Power can vary inside one 16-RB RBG.  Keep all RBs through the
            # MMSE/SVD path and aggregate only the resulting SINR, otherwise a
            # one-RB boost can disappear when the centre RB is sampled.
            snaps_u = scaled_eval
            prec_snaps_u = scaled_prec
            grp = max(1, int(rb_per_rbg))
            raw_frequency_rows = True
        elif rb_per_rbg > 1:
            snaps_u = [mu.rbg_reduce(
                x, rb_per_rbg, rbg_boundaries=resolved_boundaries) for x in _2d]
            prec_snaps_u = [mu.rbg_reduce(
                x, rb_per_rbg, rbg_boundaries=resolved_boundaries) for x in _2d_prec]
            grp = 1
            raw_frequency_rows = False
        else:
            snaps_u = _2d                                           # 每行 = 1 RB
            prec_snaps_u = _2d_prec
            grp = 1
            raw_frequency_rows = True
        n_rows = snaps_u[0].shape[0]
        n_rbg_eff = len(resolved_boundaries)
        aggregation_boundaries = (
            resolved_boundaries if raw_frequency_rows else None
        )

        # Type-I-style 是宽带权（同一快照内所有 RBG 共用一组列），但绝不能在
        # **整个仿真时域**上只搜一次。那会把未来快照放进当前 PMI，形成 oracle。
        # 下面在每个快照的可用 h_prec 上搜索；若要模拟更慢的 PMI 周期，应显式
        # 持有上一次报告，而不是偷看未来后做全时域平均。

        sinr = np.zeros((n_s, max_rank))
        sinr_rbg = np.zeros((n_s, max_rank, n_rbg_eff))
        mcs = np.zeros((n_s, max_rank), dtype=int)
        se = np.zeros((n_s, max_rank))
        sinr_tx = np.zeros((n_s, max_rank))
        mcs_tx = np.zeros((n_s, max_rank), dtype=int)
        bf_gain = np.zeros((n_s, max_rank))
        bf_gain_rbg = np.zeros((n_s, max_rank, n_rbg_eff))
        pmi_sinr = np.zeros((n_s, max_rank))
        pmi_sinr_rbg = np.zeros((n_s, max_rank, n_rbg_eff))
        sinr_tx_rbg = np.zeros((n_s, max_rank, n_rbg_eff))
        lag_used = np.zeros(n_s)
        lag_by_group_rbg = (
            np.zeros((n_s, 2, n_rbg_eff), dtype=int)
            if srs_assignments is not None else None)
        se_gnb = np.zeros((n_s, max_rank))       # 基站以为的谱效，rank 与调度都看它
        rank_gnb = np.ones(n_s, dtype=int)
        report_source = np.zeros(n_s, dtype=int)
        noise_by_snapshot = np.zeros(n_s, dtype=float)
        selected_power_diag: list[dict[str, Any]] = [{} for _ in range(n_s)]
        _gs = per_snap_sinr[i] if i < len(per_snap_sinr) else [geo_sinr_db[i]]
        _ss = per_snap_sir[i] if i < len(per_snap_sir) else [sir_in[i]]

        # 先把每个时刻基站可用的信道建完，PMI 报告才能在后续快照里持有上一份。
        h_prec_seq: list[np.ndarray] = []
        for s in range(n_s):
            if aging:
                assert effective_csi is not None
                if srs_assignments is not None:
                    assignment = srs_assignments[i]
                    group_lag_rbg = ca.rbg_lag_snapshots_by_antenna_group(
                        effective_csi, n_rbg_eff, snapshot_ms=snapshot_ms,
                        snapshot_index=s, rb_per_rbg=rb_per_rbg,
                        opportunity_offsets_ms=tuple(
                            float(leg.offset_ms) for leg in assignment.legs),
                        frequency_resource_id=int(
                            assignment.frequency_resource_id))
                    assert lag_by_group_rbg is not None
                    lag_by_group_rbg[s] = group_lag_rbg
                    group_lags = (
                        np.stack([
                            carrier_grid.expand_rbg_values(
                                group_lag_rbg[g], resolved_boundaries,
                                num_rows=n_rows)
                            for g in range(group_lag_rbg.shape[0])
                        ])
                        if raw_frequency_rows else group_lag_rbg)
                    h_prec_seq.append(ca.stale_channel_by_antenna_group(
                        prec_snaps_u, s, group_lags,
                        antenna_port_groups=assignment.antenna_port_groups,
                        periodic_history=bool(
                            effective_csi.periodic_trace_history)))
                    lag_used[s] = float(np.mean(group_lag_rbg))
                else:
                    lag_rbg = ca.rbg_lag_snapshots(
                        effective_csi, n_rbg_eff, snapshot_ms=snapshot_ms,
                        snapshot_index=s, rb_per_rbg=rb_per_rbg,
                        opportunity_offset_ms=0.0)
                    lags = (
                        carrier_grid.expand_rbg_values(
                            lag_rbg, resolved_boundaries, num_rows=n_rows)
                        if raw_frequency_rows else np.asarray(lag_rbg, dtype=int)
                    )
                    h_prec_seq.append(ca.stale_channel(
                        prec_snaps_u, s, lags,
                        periodic_history=bool(
                            effective_csi.periodic_trace_history)))
                    lag_used[s] = float(np.mean(lag_rbg))
            else:
                h_prec_seq.append(prec_snaps_u[s])

        report_period_ms = (float(csi.csi_report_period_ms)
                            if csi is not None else float(snapshot_ms))
        report_every = max(1, int(np.ceil(
            report_period_ms / max(float(snapshot_ms), _EPS) - 1e-12)))
        # 宽带 PMI 在一个 CSI 报告周期内**不变**：同一个 report_s 传进去的是同一个
        # 数组，搜出来的权逐位相同。默认 20 ms 报告周期 / 5 ms 快照下，
        # 4 次里有 3 次是重复搜索（码本列选择是建表里最贵的几步之一）。
        # 按 report_s 记忆化，不改任何数值，只是不再算第二遍。
        pmi_by_report: dict[int, np.ndarray] = {}

        for s, hs in enumerate(snaps):
            _g = _gs[s % len(_gs)]
            # 逐快照用它自己的几何 SINR，不用 UE 的均值——保住动态范围
            npow = mu.noise_from_geometric_sinr(hs, _g)
            noise_by_snapshot[s] = npow

            # SRS 周期/跳频决定 h_prec 的陈旧程度；CSI report 周期决定宽带 PMI/CQI
            # 何时更新。二者不是同一个周期，更不是 5 ms 快照间隔的别名。
            h_prec = h_prec_seq[s]
            report_s = (s // report_every) * report_every
            report_source[s] = report_s

            # 两个权都只看当前时刻可获得的同一份（可能陈旧）CSI。这样比较的是
            # 权值自由度/码本量化，不混入“一个看未来、另一个不看”的信息优势。
            _panel = [int(v) for v in bs_panel] if bs_panel is not None else []
            _nh = _panel[0] if len(_panel) == 3 else None
            _nv = _panel[1] if len(_panel) == 3 else None
            w_pmi_s = pmi_by_report.get(report_s)
            if w_pmi_s is None:
                w_pmi_s = _type1_precoder(
                    h_prec_seq[report_s],
                    max_rank,
                    n_h=_nh,
                    n_v=_nv,
                    port_order=port_order,
                    vertical_index_order=vertical_index_order,
                )
                pmi_by_report[report_s] = w_pmi_s

            # 预编码用 h_prec、评估用当前快照。零时延时两者相同，
            # 结果与 mumimo.su_rank_adaptation **逐位相同**（test_csi_aging 第 1 节）。
            # Type I 参照权是**在陈旧信道的协方差上**搜的（宽带 PMI 本就是慢量），
            # 所以它同样吃老化——只是自由度少，能算错的地方也少。
            _wov = w_pmi_s if precoder == "type1" else None
            rc = ca.rank_adaptation_aged(h_prec, snaps_u[s], noise_power=npow,
                                         max_rank=max_rank, table=table,
                                         target_bler=target_bler, rb_per_rbg=grp,
                                         rbg_boundaries=aggregation_boundaries,
                                         w_override=_wov,
                                         power_constraint=power_constraint)
            for c in rc.candidates:
                r = c["rank"] - 1
                sinr[s, r], mcs[s, r], se[s, r] = c["sinr_db"], c["mcs"], c["se"]
                sinr_rbg[s, r] = np.asarray(c["sinr_rbg_db"], dtype=float)
            for c in rc.gnb_candidates:
                se_gnb[s, c["rank"] - 1] = c["se"]
            rank_gnb[s] = rc.rank
            selected_power_diag[s] = dict(rc.power_diagnostics or {})

            # --- BF Gain = SVD − PMI，**两者都在基站自己的（陈旧）CSI 上算** ---
            # 基站是从 SRS 拿的信道，它能自己算出 BF Gain，但算的是滞后那一刻的。
            # 老化时这会让它**高估**增益（以为预编码是匹配的），于是 MCS 点高了，
            # 误码上来，再由 OLLA 拉回去——这正是现网的机制。
            # BF Gain 是**实际发射权**相对 PMI 参照权的增益。
            # precoder="type1" 时两者是同一个权，所以它恒为 0——这不是特例处理，
            # 是定义的直接后果：码本发送没有额外的 BF 增益可加。
            w_tx_prec = w_pmi_s if precoder == "type1" else ca.svd_precoder(h_prec)
            rank_cap = min(max_rank, w_tx_prec.shape[2], w_pmi_s.shape[2])
            for r in range(1, rank_cap + 1):
                p_per = 1.0 / r
                _qtx, w_tx_model, _ = bf.equal_power_weights(
                    w_tx_prec[:, :, :r], mode=power_constraint, total_power=1.0)
                _qpmi, w_pmi_model, _ = bf.equal_power_weights(
                    w_pmi_s[:, :, :r], mode=power_constraint, total_power=1.0)
                s_tx = ca.mmse_stream_sinr(h_prec, w_tx_model,
                                           power_per_stream=p_per, noise_power=npow)
                s_pmi = ca.mmse_stream_sinr(h_prec, w_pmi_model,
                                            power_per_stream=p_per, noise_power=npow)
                g_tx = mu.user_sinr_db(
                    s_tx, rb_per_rbg=grp,
                    rbg_boundaries=aggregation_boundaries)
                g_pmi = mu.user_sinr_db(
                    s_pmi, rb_per_rbg=grp,
                    rbg_boundaries=aggregation_boundaries)
                bf_gain[s, r - 1] = g_tx - g_pmi
                bf_gain_rbg[s, r - 1] = (
                    mu.rbg_sinr_db(
                        s_tx, rb_per_rbg=grp,
                        rbg_boundaries=aggregation_boundaries)
                    - mu.rbg_sinr_db(
                        s_pmi, rb_per_rbg=grp,
                        rbg_boundaries=aggregation_boundaries))
                # CQI 是终端在**真实信道**上用 PMI 权测的，所以这里用当前快照
                pmi_stream = ca.mmse_stream_sinr(
                    snaps_u[s], w_pmi_model,
                    power_per_stream=p_per, noise_power=npow)
                pmi_sinr_rbg[s, r - 1] = mu.rbg_sinr_db(
                    pmi_stream, rb_per_rbg=grp,
                    rbg_boundaries=aggregation_boundaries)
                pmi_sinr[s, r - 1] = float(np.mean(pmi_sinr_rbg[s, r - 1]))

        # --- 发送侧 SINR = CQI 门限 + BF Gain（用户 2026-08-03 定的口径）---
        # 现场流程（CLAUDE.md 已固化）：
        #   内部 CQI → 离散表映射初始 MCS → 该 MCS 的目标 BLER SINR 门限
        #   → + BF Gain → 按 SINR 重映射 MCS → + OLLA → floor
        # 这里只走到"+BF Gain"为止，OLLA 留在 TTI 主循环里逐 TTI 更新。
        #
        # **CQI 是长期滤波的宽带量**。滤波只能使用 0..s 的观测；过去把整个
        # 仿真的 PMI SINR 先求均值再回填每个快照，当前 TTI 会偷看到未来。
        # 这里用 expanding mean 作为没有额外时间常数配置时的透明因果基线；
        # 若现场给出 IIR 系数/反馈周期，应替换这一行但仍必须保持因果。
        # **BF Gain 是瞬时的**，基站每次调度都能从自己的 CSI 算出来。
        # 早先版本把发送侧写成"接收 SINR 的长期均值"，那是个事后诸葛亮的量——
        # 它已经包含了 SVD 的增益，等于假设基站预先知道自己波束打得准不准。
        cqi_by_snapshot = np.zeros((n_s, max_rank), dtype=int)
        reported_cqi_by_snapshot = np.zeros((n_s, max_rank), dtype=int)
        for _r in range(max_rank):
            report_observations: list[float] = []
            held_cqi = 0
            held_reported_cqi = 0
            for _s in range(n_s):
                if _s == int(report_source[_s]):
                    report_observations.append(float(pmi_sinr[_s, _r]))
                    filtered_pmi = _nan_safe(np.mean, report_observations)
                    held_reported_cqi = _reported_cqi_of(filtered_pmi, target_bler)
                    # 物理 out-of-range codepoint 0 没有可映射 MCS。Phase-A 表仍用
                    # 最低行作为防御占位，真实可用性由 outage/BLER 路径硬判。
                    held_cqi = max(held_reported_cqi - 1, 0)
                else:
                    filtered_pmi = _nan_safe(np.mean, report_observations)
                cqi_by_snapshot[_s, _r] = held_cqi
                reported_cqi_by_snapshot[_s, _r] = held_reported_cqi
                thr = _cqi_threshold_sinr(
                    int(cqi_by_snapshot[_s, _r]), target_bler)
                # 表行 0 对应最低可用 MCS；上报 codepoint 0 时这里只保留防御占位，
                # 真实调度可用性仍由接收侧 outage/BLER 判定。
                if not np.isfinite(thr):
                    thr = filtered_pmi if np.isfinite(filtered_pmi) else -20.0
                gain = bf_gain[_s, _r] if np.isfinite(bf_gain[_s, _r]) else 0.0
                sinr_tx[_s, _r] = thr + gain
                gain_rbg = np.where(
                    np.isfinite(bf_gain_rbg[_s, _r]),
                    bf_gain_rbg[_s, _r], 0.0)
                sinr_tx_rbg[_s, _r] = thr + gain_rbg
                mcs_tx[_s, _r] = la_sel(sinr_tx[_s, _r], table, target_bler)
        cqi_idx = cqi_by_snapshot[-1].copy()
        reported_cqi = reported_cqi_by_snapshot[-1].copy()
        # **rank 由基站按自己的 CSI 挑**（零时延时 se_gnb 与 se 逐位相同）
        best = rank_gnb - 1
        # **覆盖判定。** 用户级 SINR 连 MCS 0 的 10% BLER 门限都够不到时，
        # 这个快照下他根本调度不动——发了也是白发。必须显式标出来：
        # PF 的度量是 R_inst/R_avg，一个永远发不成功的用户 R_avg 会趋近 0，
        # 度量发散，调度器于是死盯着他，把整个小区拖垮。这是 PF 的经典病理。
        # 上报 CQI0 是协议语义的 out-of-range，必须比“当前真实 SINR
        # 恰好能否解 MCS0”更早拦截。否则 PMI 测得 CQI0、SVD 增益偶然
        # 较大时仍会被调度，与已选定的 4-bit CQI 合同矛盾。
        outage = np.array([
            bool(reported_cqi_by_snapshot[t, best[t]] == 0)
            or _bler_lookup(int(mcs[t, best[t]]), float(sinr[t, best[t]])) > 0.5
            for t in range(n_s)
        ])
        out.append(UeLinkTable(
            ue=i, sinr_db=sinr, mcs=mcs, se=se,
            best_rank=best + 1, best_se=se[np.arange(n_s), best],
            geo_sinr_db=float(geo_sinr_db[i]), outage=outage,
            # **IoT 逐快照算再取中位，不能拿平均后的 SINR/SIR 去算。**
            # 两个量各自平均后相减，差值可以塌到 0，IoT 直接报 inf——
            # 实测逐样本算出来是 5~41 dB，从来不是 inf。
            iot_db=_nan_safe(
                np.nanmedian,
                (_iots := (power_iot[i] if power_enabled and power_iot is not None
                           else [_iot(g, r) for g, r in
                                 zip(_gs, _ss, strict=False)]))),
            # **逐样本的有效率，不是逐用户。** 一个用户 8 个快照里 4 个算不出 IoT，
            # nanmedian 照样给出有限值 → 这个用户被算成"有效" → 小区级有效率报 100%，
            # 而实际一半样本被丢了。实测 ds_9625340c：逐用户 100%、逐样本只有 46%。
            # 粒度错了的后果不是"少报一个警告"，是**报错了另一个警告**：
            # 系统会去怪站间距和邻区负载，而真因是这个量本身在多时隙下就不成立。
            iot_sample_valid=float(np.mean([np.isfinite(x) for x in _iots]))
            if _iots else 0.0,
            sir_db=float(sir_in[i]), sinr_tx_db=sinr_tx, mcs_tx=mcs_tx,
            sinr_rbg_db=sinr_rbg, sinr_tx_rbg_db=sinr_tx_rbg,
            bf_gain_db=bf_gain, pmi_sinr_db=pmi_sinr, cqi_index=cqi_idx,
            cqi_index_per_snapshot=cqi_by_snapshot,
            reported_cqi_codepoint=reported_cqi,
            reported_cqi_codepoint_per_snapshot=reported_cqi_by_snapshot,
            csi_lag_snapshots=lag_used, se_gnb=se_gnb,
            csi_lag_snapshots_by_antenna_group_rbg=lag_by_group_rbg,
            best_se_gnb=se_gnb[np.arange(n_s), best], mcs_table=int(table),
            target_bler=float(target_bler),
            power_constraint=str(power_constraint).lower(),
            frequency_rows_per_rbg=int(grp),
            frequency_rbg_boundaries=aggregation_boundaries,
            serving_cell_index=(
                serving_cell_by_ue[i] if serving_cell_by_ue is not None else
                (resolved_serving_cell_values[i]
                 if resolved_serving_cell_values is not None else None)),
            rb_power_control_fingerprint=pc.config_fingerprint(power_cfg),
            rb_power_coupling_diagnostics=(power_coupling_diag[i]
                                           if power_coupling_diag is not None else None),
            power_diagnostics=selected_power_diag,
            csi_report_source_snapshot=report_source,
            csi_report_period_ms=report_period_ms,
            srs_resource_assignment=(
                srs_assignments[i] if srs_assignments is not None else None),
            precoding_csi_source=("explicit_estimate" if explicit_precoding_csi
                                  else "evaluation_channel"),
            h_true_rbg=np.asarray(snaps_u), h_prec_rbg=np.asarray(h_prec_seq),
            noise_power_by_snapshot=noise_by_snapshot,
        ))
    if mu_enabled:
        build_mu_pair_tables(
            out, rank_per_user=int(mu_rank_per_user),
            precoder=str(mu_precoder), power_constraint=str(power_constraint),
            csi_error_variance=float(mu_csi_error_variance))
    return out


def build_mu_pair_tables(
    tables: list[UeLinkTable], *, rank_per_user: int = mu.MU_MAX_RANK,
    precoder: str = "zf", power_constraint: str = "nebf",
    csi_error_variance: float = 0.0,
) -> dict[str, Any]:
    """预计算所有两用户 MU 链路及 ``CorrLoss + powerLoss`` 分解。

    第一版只做两用户、每用户 rank2。它不在 TTI 循环做矩阵运算；PF 顺序和
    队列状态仍逐 TTI 决定“哪一对”被拿来查表。
    """
    if len(tables) < 2:
        raise ValueError("MU 建表至少需要 2 个 UE")
    rank = int(rank_per_user)
    if rank != mu.MU_MAX_RANK:
        raise ValueError(f"当前 MU 体验基线固定每用户 rank{mu.MU_MAX_RANK}")
    if precoder not in ("zf", "rzf"):
        raise ValueError("MU 体验基线的 precoder 只支持 zf / rzf")
    if not np.isfinite(csi_error_variance) or float(csi_error_variance) < 0:
        raise ValueError("csi_error_variance 必须是有限非负数")
    n_snap = int(tables[0].sinr_db.shape[0])
    rows_per_rbg = int(tables[0].frequency_rows_per_rbg)
    rbg_boundaries = tables[0].frequency_rbg_boundaries
    n_rbg = int(tables[0].sinr_rbg_db.shape[2]) \
        if tables[0].sinr_rbg_db is not None else 1
    for t in tables:
        if (t.h_true_rbg is None or t.h_prec_rbg is None
                or t.noise_power_by_snapshot is None):
            raise ValueError("MU 建表缺少 RBG 粒度 true/precoding channel 或逐快照噪声")
        if t.sinr_db.shape[1] < rank:
            raise ValueError(f"UE {t.ue} 不支持 MU rank{rank}")
        if t.sinr_db.shape[0] != n_snap:
            raise ValueError("MU 各 UE 的 snapshot 数必须一致")
        if int(t.frequency_rows_per_rbg) != rows_per_rbg:
            raise ValueError("MU 各 UE 的频域粒度必须一致")
        if t.frequency_rbg_boundaries != rbg_boundaries:
            raise ValueError("MU 各 UE 的 RBG 边界必须一致")
        if t.sinr_rbg_db is None or int(t.sinr_rbg_db.shape[2]) != n_rbg:
            raise ValueError("MU 建表缺少一致的逐 RBG SU SINR")

    # 基站视角的 SU rank2 物理 SINR 只与 UE/快照有关，先缓存，避免每个 pair 重算。
    su_pred = np.zeros((len(tables), n_snap), dtype=float)
    su_pred_rbg = np.zeros((len(tables), n_snap, n_rbg), dtype=float)
    for u, table in enumerate(tables):
        assert table.h_prec_rbg is not None and table.noise_power_by_snapshot is not None
        for s in range(n_snap):
            rc = ca.rank_adaptation_aged(
                table.h_prec_rbg[s], table.h_prec_rbg[s],
                noise_power=float(table.noise_power_by_snapshot[s]),
                max_rank=rank, rb_per_rbg=rows_per_rbg,
                rbg_boundaries=rbg_boundaries,
                power_constraint=power_constraint)
            su_pred[u, s] = float(rc.candidates[rank - 1]["sinr_db"])
            su_pred_rbg[u, s] = np.asarray(
                rc.candidates[rank - 1]["sinr_rbg_db"], dtype=float)

    power_loss = -10.0 * np.log10(2.0)  # 2 个 rank2 UE：每流 P/4 vs SU 的 P/2
    pair_count = 0
    for i in range(len(tables)):
        for j in range(i + 1, len(tables)):
            if (tables[i].serving_cell_index is not None
                    and tables[j].serving_cell_index is not None
                    and tables[i].serving_cell_index != tables[j].serving_cell_index):
                # One MU precoder cannot span independent serving cells.
                continue
            true_sinr = np.zeros((n_snap, 2), dtype=float)
            pred_sinr = np.zeros((n_snap, 2), dtype=float)
            true_sinr_rbg = np.zeros((n_snap, 2, n_rbg), dtype=float)
            pred_sinr_rbg = np.zeros((n_snap, 2, n_rbg), dtype=float)
            corr = np.zeros(n_snap, dtype=float)
            leakage = np.zeros(n_snap, dtype=float)
            pred_leakage = np.zeros(n_snap, dtype=float)
            pdiag: list[dict[str, Any]] = []
            regdiag: list[dict[str, Any]] = []
            ti, tj = tables[i], tables[j]
            assert ti.h_true_rbg is not None and tj.h_true_rbg is not None
            assert ti.h_prec_rbg is not None and tj.h_prec_rbg is not None
            assert ti.noise_power_by_snapshot is not None
            assert tj.noise_power_by_snapshot is not None
            for s in range(n_snap):
                hp = mu.effective_user_channels(
                    [ti.h_prec_rbg[s][None], tj.h_prec_rbg[s][None]],
                    streams_per_user=rank)
                noise = np.array([ti.noise_power_by_snapshot[s],
                                  tj.noise_power_by_snapshot[s]], dtype=float)
                rt = mu.mu_link_performance_lmmse(
                    [ti.h_true_rbg[s], tj.h_true_rbg[s]],
                    [ti.h_prec_rbg[s], tj.h_prec_rbg[s]],
                    noise_power=noise, streams_per_user=rank, precoder=precoder,
                    power_constraint=power_constraint, rb_per_rbg=rows_per_rbg,
                    rbg_boundaries=rbg_boundaries,
                    csi_error_variance=float(csi_error_variance))
                rp = mu.mu_link_performance_lmmse(
                    [ti.h_prec_rbg[s], tj.h_prec_rbg[s]],
                    [ti.h_prec_rbg[s], tj.h_prec_rbg[s]],
                    noise_power=noise, streams_per_user=rank, precoder=precoder,
                    power_constraint=power_constraint, rb_per_rbg=rows_per_rbg,
                    rbg_boundaries=rbg_boundaries,
                    csi_error_variance=float(csi_error_variance))
                true_sinr[s] = rt.sinr_per_user_db
                pred_sinr[s] = rp.sinr_per_user_db
                assert rt.sinr_per_user_rbg_db is not None
                assert rp.sinr_per_user_rbg_db is not None
                true_sinr_rbg[s] = rt.sinr_per_user_rbg_db
                pred_sinr_rbg[s] = rp.sinr_per_user_rbg_db
                leakage[s] = rt.leakage_ratio
                pred_leakage[s] = rp.leakage_ratio
                pdiag.append(dict(rt.power_diagnostics or {}))
                regdiag.append(dict(rt.rzf_regularization or {}))
                g = mu._wideband_user_vectors(hp)
                denom = max(float(np.linalg.norm(g[0]) * np.linalg.norm(g[1])), _EPS)
                corr[s] = abs(complex(g[0].conj() @ g[1])) / denom

            su_true = np.column_stack((ti.sinr_db[:, rank - 1],
                                       tj.sinr_db[:, rank - 1]))
            assert ti.sinr_rbg_db is not None and tj.sinr_rbg_db is not None
            su_true_rbg = np.stack((ti.sinr_rbg_db[:, rank - 1],
                                    tj.sinr_rbg_db[:, rank - 1]), axis=1)
            su_pred_pair = np.column_stack((su_pred[i], su_pred[j]))
            su_pred_pair_rbg = np.stack((su_pred_rbg[i], su_pred_rbg[j]), axis=1)
            link = MuPairLink(
                users=(i, j), rank_per_user=rank,
                true_sinr_db=true_sinr, predicted_sinr_db=pred_sinr,
                corr_loss_tx_db=pred_sinr - su_pred_pair - power_loss,
                corr_loss_true_db=true_sinr - su_true - power_loss,
                power_loss_db=float(power_loss), correlation=corr,
                leakage_ratio=leakage, predicted_leakage_ratio=pred_leakage,
                power_constraint=str(power_constraint).lower(), precoder=precoder,
                true_sinr_rbg_db=true_sinr_rbg,
                predicted_sinr_rbg_db=pred_sinr_rbg,
                corr_loss_tx_rbg_db=(
                    pred_sinr_rbg - su_pred_pair_rbg - power_loss),
                corr_loss_true_rbg_db=(
                    true_sinr_rbg - su_true_rbg - power_loss),
                csi_error_variance=float(csi_error_variance),
                power_diagnostics=pdiag, rzf_regularization=regdiag)
            ti.mu_links[j] = link
            tj.mu_links[i] = link
            pair_count += 1
    return {
        "pairs": pair_count, "snapshots": n_snap,
        "rank_per_user": rank, "power_loss_db": float(power_loss),
        "precoder": precoder, "power_constraint": str(power_constraint).lower(),
        "csi_error_variance": float(csi_error_variance),
    }


def measure_mu_gain(
    h_users: list[np.ndarray],
    geo_sinr_db: list[float],
    *,
    h_for_precoding_users: list[np.ndarray] | None = None,
    geo_sir_db: list[float] | None = None,
    neighbor_load: float = 1.0,
    neighbor_load_jitter: float = 0.05,
    load_jitter_rng: np.random.Generator | None = None,
    num_ues: int | None = None,
    max_mu_users: int = 4,
    max_snapshots: int = 4,
    csi: ca.CsiConfig | None = None,
    snapshot_ms: float = 5.0,
    rb_per_rbg: int = 16,
    rbg_boundaries: tuple[tuple[int, int], ...] | None = None,
    power_constraint: str = "nebf",
    mu_precoder: str = "zf",
    mu_csi_error_variance: float = 0.0,
) -> dict[str, Any]:
    """实测 MU 相对 SU 的小区谱效比，供 TTI 主循环使用。

    **主循环里不可能逐 TTI 真做配对**——那要在每个 TTI 上做 SVD 与矩阵求逆，
    十万 TTI 直接跑不完。折中是：在建表阶段用
    :func:`mumimo.su_mu_adaptation` 在若干个快照上真配一遍，
    把 MU/SU 的小区谱效比测出来，主循环按这个比例折算。

    **这是当前最大的简化，必须说清楚。** 它假设 MU 增益在时间上是稳定的，
    而真实的配对增益随用户瞬时信道起伏。返回值里带 ``per_snapshot``，
    比值的离散程度就是这个假设的可信度——波动大就说明不该用一个标量。

    ``csi`` 开启老化时，配对的预编码走**陈旧信道**、评估走当前信道。
    **MU 受老化的打击远重于 SU**：ZF 的全部价值就是把配对用户之间的干扰
    零陷掉，而零陷是按基站以为的信道打的——信道一变，零陷就落空，
    残余干扰直接进分母。SU 只是波束没对准，损失温和得多。

    若没有任何快照能完成 MU 配对，本函数返回 ``measured=False`` 和逐快照
    ``errors``，**不会把 1.0 当成实测增益**。调用方在 ``mu_enabled=True`` 时
    必须把它视为硬错误；保留 ``ratio=1.0`` 只为让诊断对象保持固定字段结构。

    信道输入统一为 ``[T,RB,BS,UE]``；单快照 ``[RB,BS,UE]`` 会显式补 ``T=1``。
    不能把 3D 输入的 RB 维当成时间维逐 RB 配对，否则 MU/SU 比值会依赖一个
    与物理时间无关的数组解释。
    """
    def _time_major(value: np.ndarray, *, label: str, index: int) -> np.ndarray:
        channel = np.asarray(value)
        if channel.ndim == 3:
            channel = channel[None]
        if channel.ndim != 4 or any(int(n) < 1 for n in channel.shape):
            raise ValueError(
                f"{label}[{index}] 应为非空 [RB,BS,UE] 或 [T,RB,BS,UE]，"
                f"收到 {channel.shape}")
        return channel

    for name, value in (("max_mu_users", max_mu_users),
                        ("max_snapshots", max_snapshots),
                        ("rb_per_rbg", rb_per_rbg)):
        if (isinstance(value, (bool, np.bool_))
                or not isinstance(value, (int, np.integer)) or int(value) < 1):
            raise ValueError(f"{name} 必须是至少为 1 的整数")
    if not np.isfinite(snapshot_ms) or float(snapshot_ms) <= 0:
        raise ValueError("snapshot_ms 必须是有限正数")
    if mu_precoder not in ("zf", "rzf"):
        raise ValueError("mu_precoder 只支持 zf / rzf")
    if (not np.isfinite(mu_csi_error_variance)
            or float(mu_csi_error_variance) < 0):
        raise ValueError("mu_csi_error_variance 必须是有限非负数")

    h_eval_users = [
        _time_major(x, label="h_users", index=i) for i, x in enumerate(h_users)]
    if not h_eval_users:
        raise ValueError("h_users 至少需要一个信道样本")
    first_rb = int(h_eval_users[0].shape[1])
    for i, value in enumerate(h_eval_users[1:], start=1):
        if int(value.shape[1]) != first_rb:
            raise ValueError(
                f"MU 各 UE 必须使用同一 RB 栅格；UE0={first_rb} RB，"
                f"UE{i}={int(value.shape[1])} RB"
            )
    resolved_boundaries = (
        carrier_grid.validate_boundaries(first_rb, rbg_boundaries)
        if rbg_boundaries is not None
        else carrier_grid.uniform_boundaries(first_rb, rb_per_rbg)
    )
    if csi is not None:
        ca.validate_hopping_grid(
            csi, [stop - start for start, stop in resolved_boundaries]
        )
    if len(geo_sinr_db) != len(h_eval_users):
        raise ValueError("geo_sinr_db 与 h_users 的样本数必须一致")
    sir_in = (list(geo_sir_db) if geo_sir_db is not None
              else [float("nan")] * len(h_eval_users))
    if len(sir_in) != len(h_eval_users):
        raise ValueError("geo_sir_db 与 h_users 的样本数必须一致")
    load_cfg = NeighborLoadConfig(
        prb_utilization=float(neighbor_load),
        jitter=float(neighbor_load_jitter))
    if h_for_precoding_users is None:
        h_precoding_users = h_eval_users
        csi_source = "evaluation_channel"
    else:
        if len(h_for_precoding_users) != len(h_eval_users):
            raise ValueError("h_for_precoding_users 与 h_users 的样本数必须一致")
        h_precoding_users = [
            _time_major(x, label="h_for_precoding_users", index=i)
            for i, x in enumerate(h_for_precoding_users)]
        for i, (he, hp) in enumerate(zip(
                h_eval_users, h_precoding_users, strict=True)):
            if he.shape != hp.shape:
                raise ValueError(
                    f"样本 {i} 的评估/预编码信道形状不一致：{he.shape} vs {hp.shape}")
        csi_source = "explicit_estimate"
    h_users = h_eval_users
    per_snap_sinr: list[list[float]] = [[float(x)] for x in geo_sinr_db]
    per_snap_sir: list[list[float]] = [[float(x)] for x in sir_in]
    if num_ues is not None and num_ues < len(h_users):
        groups = group_samples_by_ue(len(h_users), num_ues)
        merged_h, merged_p, merged_g, merged_s = [], [], [], []
        for group in groups:
            eval_samples = [np.asarray(h_users[i]) for i in group]
            prec_samples = [np.asarray(h_precoding_users[i]) for i in group]
            merged_h.append(np.concatenate(
                [x.reshape(-1, *x.shape[-3:]) for x in eval_samples], axis=0))
            merged_p.append(np.concatenate(
                [x.reshape(-1, *x.shape[-3:]) for x in prec_samples], axis=0))
            merged_g.append([
                float(geo_sinr_db[i])
                for i, x in zip(group, eval_samples, strict=True)
                for _ in range(x.shape[0] if x.ndim == 4 else 1)])
            merged_s.append([
                float(sir_in[i])
                for i, x in zip(group, eval_samples, strict=True)
                for _ in range(x.shape[0] if x.ndim == 4 else 1)])
        h_users, h_precoding_users = merged_h, merged_p
        per_snap_sinr, per_snap_sir = merged_g, merged_s

    # 与 build_link_tables 使用同一个负载模型和同一随机流起点。过去这里只用
    # full-buffer 几何 SINR 算 MU/SU 比值，再把它乘到已经按邻区负载折算的 SU
    # 链路表上；两个工作点不一致，尤其会扭曲 RZF/功率约束下的比值。
    if float(neighbor_load) < 1.0:
        loads = [
            (load_cfg.realized(len(gs), rng=load_jitter_rng)
             if load_jitter_rng is not None
             else np.full(len(gs), float(neighbor_load)))
            for gs in per_snap_sinr
        ]
        adjusted: list[list[float]] = []
        for gs, ss, us in zip(
                per_snap_sinr, per_snap_sir, loads, strict=True):
            adjusted.append([
                apply_neighbor_load(g, sr, float(u))
                for g, sr, u in zip(gs, ss, us, strict=True)])
        per_snap_sinr = adjusted

    aging = csi is not None and csi.enabled
    ratios: list[float] = []
    su_values: list[float] = []
    mu_values: list[float] = []
    modes: list[str] = []
    errors: list[dict[str, Any]] = []
    if len(h_users) < 2 or max_mu_users < 2:
        return {
            "ratio": 1.0,
            "measured": False,
            "errors": [{
                "snapshot": None,
                "type": "ValueError",
                "message": "MU 至少需要 2 个候选用户且 max_mu_users>=2",
            }],
            "note": "MU 增益未测得；这个 1.0 只是诊断占位值，禁止用于仿真。",
        }
    n = min(max_snapshots, max(1, min(np.asarray(h).shape[0] for h in h_users)))
    # **两条路径必须同粒度，否则比的不是老化。** 早先老化侧降到 RBG、
    # 完美侧留在 RB，su_mu_adaptation 内部又按 16 分组，等于两边口径不同，
    # 算出来的"老化损失"里混着粒度差。现在一律留在 RB 粒度，
    # 只把逐 RBG 的滞后展开到逐 RB（一跳本来就覆盖 16 个连续 RB）。
    seq = [[np.asarray(h)[t] for t in range(np.asarray(h).shape[0])] for h in h_users]
    seq_prec = [
        [np.asarray(h)[t] for t in range(np.asarray(h).shape[0])]
        for h in h_precoding_users]
    n_rb = seq[0][0].shape[0] if seq and seq[0] else 1
    n_rbg = len(resolved_boundaries)
    for t in range(n):
        snaps = [np.asarray(h)[t:t + 1] for h in h_users]
        npow = np.asarray([
            mu.noise_from_geometric_sinr(
                snaps[u], per_snap_sinr[u][t % len(per_snap_sinr[u])])
            for u in range(len(snaps))], dtype=float)
        prec = ([np.asarray(seq_prec[u][t])[None] for u in range(len(seq_prec))]
                if h_for_precoding_users is not None else None)
        if aging:
            assert csi is not None
            lag_rbg = ca.rbg_lag_snapshots(csi, n_rbg, snapshot_ms=snapshot_ms,
                                           snapshot_index=t, rb_per_rbg=rb_per_rbg)
            lags = carrier_grid.expand_rbg_values(
                lag_rbg, resolved_boundaries, num_rows=n_rb
            )
            # su_mu_adaptation 吃 [T,RB,BS,UE] 或 [RB,BS,UE]，补回一个时隙维
            prec = [ca.stale_channel(
                s, t, lags,
                periodic_history=bool(csi.periodic_trace_history))[None]
                for s in seq_prec]
        try:
            dec = mu.su_mu_adaptation(snaps, noise_power=npow,
                                      h_users_for_precoding=prec,
                                      max_mu_users=max_mu_users,
                                      precoder=mu_precoder,
                                      csi_error_variance=float(mu_csi_error_variance),
                                      power_constraint=power_constraint,
                                      rbg_boundaries=resolved_boundaries)
        except (ValueError, np.linalg.LinAlgError) as exc:
            errors.append({
                "snapshot": int(t),
                "type": type(exc).__name__,
                "message": str(exc),
            })
            continue
        if dec.su_se > 0:
            ratios.append(dec.mu_se / dec.su_se)
            su_values.append(float(dec.su_se))
            mu_values.append(float(dec.mu_se))
            modes.append(dec.mode)
    if not ratios:
        return {
            "ratio": 1.0,
            "measured": False,
            "errors": errors,
            "note": "MU 增益没有任何有效快照；这个 1.0 只是诊断占位值，禁止用于仿真。",
        }
    r = float(np.median(ratios))
    spread = float(np.std(ratios) / max(abs(r), _EPS))
    return {
        "ratio": r, "measured": True, "per_snapshot": [round(x, 3) for x in ratios],
        "su_se_median": float(np.median(su_values)),
        "mu_se_median": float(np.median(mu_values)),
        "su_se_per_snapshot": [round(x, 4) for x in su_values],
        "mu_se_per_snapshot": [round(x, 4) for x in mu_values],
        "mode_share_mu": modes.count("MU") / len(modes),
        "relative_spread": round(spread, 3),
        "csi_aging": bool(aging),
        "precoding_csi_source": csi_source,
        "mu_precoder": mu_precoder,
        "mu_csi_error_variance": float(mu_csi_error_variance),
        "neighbor_load": load_cfg.as_dict(),
        "effective_geo_sinr_db": [
            [float(row[t % len(row)]) for t in range(n)]
            for row in per_snap_sinr],
        "errors": errors,
        "note": (f"**这是一个标量近似**：在 {len(ratios)} 个快照上真配了一遍取中位数，"
                 f"主循环按它折算，没有逐 TTI 重新配对。"
                 + (f"另有 {len(errors)} 个快照失败，错误明细已返回。" if errors else "")
                 + f"比值离散度 {spread * 100:.0f}%——超过 30% 就说明 MU 增益"
                 f"随时间起伏很大，用一个标量会失真。"
                 + ("配对预编码用的是**陈旧 CSI**（已开老化）。" if aging else
                    "配对预编码用的是**零时延完美 CSI**，这是上界不是现网。")),
    }


# ---------------------------------------------------------------------------
# 话务
# ---------------------------------------------------------------------------
@dataclass
class _Burst:
    start_tti: int
    bytes_total: int
    bytes_left: int
    first_tti: int = -1
    last_tti: int = -1
    n_tti: int = 0
    bytes_first: int = 0
    bytes_last: int = 0
    prev_tti: int = -1                   # 倒数第二次被服务的 TTI，掐尾时用
    is_small: bool = False               # bimodal 的小包（只占 1 个 RBG）


class _Traffic:
    """按话务模型往每个 UE 的缓冲区里投 burst。"""

    def __init__(self, cfg: TrafficConfig, n_ue: int, tti_ms: float,
                 rng: np.random.Generator, small_bytes: int = 1500,
                 num_rbg: int = 17) -> None:
        self.cfg, self.n_ue, self.tti_ms, self.rng = cfg, n_ue, tti_ms, rng
        self.active: list[_Burst | None] = [None] * n_ue
        self.queue: list[list[_Burst]] = [[] for _ in range(n_ue)]
        self.done: list[list[_Burst]] = [[] for _ in range(n_ue)]
        self._p_arrive = cfg.arrival_rate_hz * tti_ms / 1000.0
        if self._p_arrive > 1.0:
            raise ValueError(
                f"伯努利到达模型要求 arrival_rate_hz × TTI ≤ 1（当前 "
                f"{cfg.arrival_rate_hz} Hz × {tti_ms} ms = {self._p_arrive:.2f}）："
                "超过后每 TTI 恒到达一个文件，offered load 被静默钳位。"
                "请降低到达率，或改用 experience_v2 的泊松到达")
        self.offered_bytes = 0
        # 小包只占 1 个 RBG：按 1/num_rbg 的 RE 数、中等 MCS 估个字节数。
        # 它小到一个 TTI 就发完，所以体验速率完全由调度时延决定。
        self._per_rbg_bytes = max(50, int(small_bytes or 1500))
        self.num_rbg = int(num_rbg)
        self.rbg_hist: list[int] = []
        self._cbr_exact_per_tti = float(
            cfg.cbr_mbps * 1e6 * tti_ms / 1000.0 / 8)
        self._cbr_carry = np.zeros(n_ue, dtype=float)

    def step(self, tti: int) -> None:
        if self.cfg.model == "full_buffer":
            for u in range(self.n_ue):
                if self.active[u] is None:
                    self.active[u] = _Burst(tti, 1 << 62, 1 << 62)
            return
        if self.cfg.model == "cbr":
            for u in range(self.n_ue):
                # 每 TTI 直接 int 会永久丢掉小数部分：1 Mbps@0.5 ms 是 62.5 B，
                # 旧实现每格只投 62 B，长跑固定少 0.8%；更低速率甚至永远为 0。
                self._cbr_carry[u] += max(0.0, self._cbr_exact_per_tti)
                n_bytes = int(np.floor(self._cbr_carry[u]))
                self._cbr_carry[u] -= n_bytes
                if n_bytes <= 0:
                    continue
                b = self.active[u]
                if b is None:
                    self.active[u] = _Burst(tti, n_bytes, n_bytes)
                else:
                    b.bytes_left += n_bytes
                    b.bytes_total += n_bytes
                self.offered_bytes += n_bytes
            return
        # ftp3 / bimodal：泊松到达（每 TTI 用伯努利近似，p 很小时等价）
        for u in range(self.n_ue):
            if self.rng.random() < self._p_arrive:
                if self.cfg.model == "bimodal":
                    n_rbg, small = self.draw_rbg(self.num_rbg)
                    # 一次调度占 n_rbg 个 RBG，burst 大小按它一个 TTI 的承载算
                    n_bytes = max(200, int(self._per_rbg_bytes * n_rbg))
                else:
                    small, n_bytes = False, self.cfg.file_bytes
                b = _Burst(tti, n_bytes, n_bytes, is_small=small)
                self.offered_bytes += n_bytes
                if self.active[u] is None:
                    self.active[u] = b
                else:
                    self.queue[u].append(b)

    def draw_rbg(self, num_rbg: int | None = None) -> tuple[int, bool]:
        """抽一次传输占几个 RBG。两头高中间低。返回 ``(RBG 数, 是不是小包)``。

        **num_rbg 必须跟着配置走。** 早先签名给了默认值 17、调用处又不传，
        于是 ``num_rbg=8`` 的配置照样抽出 1~17 个 RBG——
        实测平均 9.03 个 RBG 却只有 8 个可用，"满带宽占比"也从 0.30 变成 0.586。
        """
        num_rbg = int(num_rbg if num_rbg is not None else self.num_rbg)
        x = self.rng.random()
        if x < self.cfg.p_small_rbg:
            n = 1
        elif x < self.cfg.p_small_rbg + self.cfg.p_full_rbg:
            n = num_rbg
        else:
            n = int(self.rng.integers(2, max(3, num_rbg)))   # 2~16 均匀
        self.rbg_hist.append(n)
        return n, n == 1

    def has_data(self, u: int) -> bool:
        return self.active[u] is not None

    def bytes_left(self, u: int) -> int:
        """当前 burst 还剩多少没发。SU/MU 判决要用它——一个 TTI 能传完就不配对。"""
        b = self.active[u]
        return int(b.bytes_left) if b is not None else 0

    def serve(self, u: int, tti: int, n_bytes: int) -> int:
        """给这个 UE 发 ``n_bytes``，返回实际发出去的字节数。"""
        b = self.active[u]
        if b is None or n_bytes <= 0:
            return 0
        sent = min(n_bytes, b.bytes_left)
        b.bytes_left -= sent
        if b.first_tti < 0:
            b.first_tti, b.bytes_first = tti, sent
        b.prev_tti = b.last_tti
        b.last_tti, b.bytes_last = tti, sent
        b.n_tti += 1
        if b.bytes_left <= 0:
            self.done[u].append(b)
            self.active[u] = self.queue[u].pop(0) if self.queue[u] else None
        return sent


# ---------------------------------------------------------------------------
# KPI
# ---------------------------------------------------------------------------
def _burst_throughput_mbps(b: _Burst, tti_ms: float, cfg: KpiConfig) -> float | None:
    """单个 burst 的体验速率（Mbps）。不合格返回 ``None``。

    3GPP TS 28.552 §5.1.1.3 的口径：**排除清空缓冲区的最后一个 slice**，
    因为那个 TTI 通常只用了一部分就把数据发完了，把它算进去等于用
    "半个 TTI 的时间"去除"半个 TTI 的数据"，得到一个虚高的瞬时速率。
    单 slice 的 burst 因此完全无法测量，只能整个丢掉。

    **分母是一段时间，不是被调度的 TTI 数。** 这两个差得很远——
    用户排队等调度的那些 TTI 也在消耗体验。早先按被调度 TTI 数算，
    12 个用户各报出 583 Mbps、小区合计 8.2 Gbps，对一个 100 MHz 小区
    物理上不可能（峰值约 1.2 Gbps）——每个用户被算成"轮到我就独享整个小区"。

    起点按 ``trim`` 分两种（用户 2026-08-02 明确）：

    * ``none`` / ``tail``：从**数据到达**算起，等调度的时间计入分母
    * ``head_tail``：从**首次被调度的 TTI** 算起，
      **话务到达但还没被调度的等待时间不计入**
    """
    if b.n_tti < max(2, cfg.min_burst_tti) or b.last_tti < 0:
        return None
    vol = b.bytes_total
    # 掐头 = 起点从"到达"挪到"首次被调度"
    t0 = b.first_tti if cfg.trim == "head_tail" else b.start_tti
    n = b.last_tti - t0 + 1
    if cfg.trim in ("tail", "head_tail"):
        # 掐尾：排除清空缓冲区的最后一个 slice，时间与数据同时扣
        vol -= b.bytes_last
        n -= (b.last_tti - b.prev_tti) if b.prev_tti >= 0 else 1
    if n <= 0 or vol <= 0:
        return None
    return vol * 8.0 / (n * tti_ms / 1000.0) / 1e6


@dataclass
class UeKpi:
    ue: int
    geo_sinr_db: float
    iot_db: float
    experienced_mbps: float
    served_mbps: float                   # 端到端平均（含空闲，用于对照）
    bursts: int
    avg_mcs: float
    avg_rank: float
    bler_first_tx: float
    retx_bler: float
    residual_bler: float
    sched_tti: int
    retx_tti: int

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


@dataclass
class SystemResult:
    """一次系统级仿真的全部结果，小区级与用户级都在。"""

    config: dict[str, Any]
    cell: dict[str, Any]
    users: list[dict[str, Any]]
    elapsed_s: float
    notes: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = {"config": self.config, "cell": self.cell, "users": self.users,
               "elapsed_s": round(self.elapsed_s, 3), "notes": self.notes}
        if self.diagnostics:
            out["diagnostics"] = self.diagnostics
        return out

    def text(self) -> str:
        c = self.cell
        if self.config.get("system", {}).get("model_version") == "experience_v2":
            def _v(key: str, digits: int = 2) -> str:
                value = c.get(key)
                return "n/a" if value is None else f"{float(value):.{digits}f}"

            return (
                f"DRB 体验速率 {_v('drb_throughput_rel19_mbps')} Mbps"
                f"（含头 {_v('drb_throughput_head_inclusive_mbps')}、"
                f"大 burst {_v('large_burst_drb_throughput_mbps')}、"
                f"小 burst 折算 {_v('small_burst_fractional_mbps')}）\n"
                f"首包时延 P95 {_v('first_packet_delay_ms_p95')} ms，"
                f"small 到达对象等待 P95 {_v('small_queue_wait_ms_p95')} ms，"
                f"完成时延 P95 {_v('small_completion_delay_ms_p95')} ms，"
                f"PDB miss {_v('small_pdb_miss_ratio', 4)}\n"
                f"平均调度 MCS {_v('avg_mcs', 1)}，平均 rank {_v('avg_rank')}，"
                f"NewTx 尝试 BLER {_v('newtx_attempt_bler', 3)}；"
                f"本小区 PRB 利用率 {_v('serving_cell_prb_utilization', 3)}，"
                f"MU 配对占已用 PRB {_v('mu_paired_prb_share_of_used', 3)}，"
                f"同 TTI 多 UE 占比 {_v('multi_ue_tti_share', 3)}"
            )
        return (
            f"小区体验速率 {c['cell_experienced_mbps']:.2f} Mbps"
            f"（用户中位 {c['ue_experienced_median_mbps']:.2f}、"
            f"5% 边缘 {c['ue_experienced_p5_mbps']:.2f}）\n"
            f"平均调度 MCS {c['avg_mcs']:.1f}（5% 边缘 {c['edge_mcs_p5']:.1f}），"
            f"平均 rank {c['avg_rank']:.2f}，首传 BLER {c['bler_first_tx']:.3f}\n"
            f"IoT 中位 {c['iot_db_median']:.1f} dB"
            f"（{c['high_iot_ue_share']:.0%} 的用户 ≥20 dB 属高干扰），"
            f"MU 配对占 RBG {c['mu_rbg_share']:.1%}\n"
            f"调度 {c['scheduled_tti']} 个 TTI / 共 {c['dl_tti']} 个下行 TTI"
            f"（占用率 {c['occupancy']:.1%}），MU 占比 {c['mu_share']:.1%}"
        )


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _LegacyHarqTb:
    """一个等待唯一一次重传的 TB；发送身份在首次 NACK 时冻结。"""

    mcs: int
    rank: int
    tb_bytes: int
    payload_bytes: int
    slot: str
    first_tti: int
    was_mu: bool


def simulate(
    tables: list[UeLinkTable],
    *,
    sys_cfg: SystemConfig | None = None,
    traffic: TrafficConfig | None = None,
    sched: SchedulerConfig | None = None,
    kpi: KpiConfig | None = None,
    mu_se_ratio: float = 1.0,
    rng: rg.RngBook | None = None,
    progress: Any = None,
) -> SystemResult:
    """跑 TTI 主循环。**这里没有任何矩阵运算**，全是查表加算术。

    ``mu_se_ratio`` 是 MU 相对 SU 的小区谱效比（由 :func:`mumimo.su_mu_adaptation`
    在第一相测出来）。>1 时调度器会在有足够用户排队时切到 MU。

    ``rng`` 是 :class:`rng.RngBook`，**按用途分流**：话务到达、HARQ 误码抽样、
    调度器决胜各拿一条互相独立的流。不给的话从 ``sys_cfg.seed`` 构造
    ``RngBook(master_seed=sys_cfg.seed, replication=0)``，老调用方不用改。

    **分流前是一个 rng 同时喂话务和 HARQ**，改一下 ``arrival_rate_hz`` 会让
    HARQ 的伯努利序列整个错位——"话务模型的影响"里于是混着"HARQ 换了一批随机数"。
    这类污染在结果里完全看不出来。分流后改话务只动话务流；HARQ 与调度决胜还按
    ``[TTI, UE]`` 固定索引，A/B 调度顺序分叉也不会把后续事件的随机数错位。
    """
    sys_cfg = sys_cfg or SystemConfig()
    traffic = traffic or TrafficConfig()
    sched = sched or SchedulerConfig()
    kpi = kpi or KpiConfig()
    t0 = time.perf_counter()

    from . import linkadapt as la  # noqa: PLC0415

    book = rng if rng is not None else rg.RngBook(master_seed=int(sys_cfg.seed))
    if not tables:
        raise ValueError("至少需要一个 UE 链路表")
    if sys_cfg.evaluation_mode not in ("capacity", "experience"):
        raise ValueError("evaluation_mode 只支持 capacity / experience")
    cfg_power = str(sys_cfg.power_constraint).lower()
    if cfg_power not in ("ebf", "pebf", "nebf"):
        raise ValueError("power_constraint 只支持 ebf / pebf / nebf")
    for i, table in enumerate(tables):
        table_power = str(getattr(table, "power_constraint", "ebf")).lower()
        if table_power != cfg_power:
            raise ValueError(
                f"UE {i} 链路表功率约束 {table_power} 与系统配置 {cfg_power} 不一致")
    _srs_rows = [
        table.srs_resource_assignment for table in tables
        if table.srs_resource_assignment is not None
    ]
    if _srs_rows and len(_srs_rows) != len(tables):
        raise ValueError("SRS 资源分配不能只覆盖部分 UE；请重新构建整套链路表")
    _srs_summary = srsr.summarize_assignments(_srs_rows)
    # **主循环选 MCS 用的表与目标 BLER，必须就是建表时那一套。** 各 UE 之间也得
    # 一致——一个小区里不可能一半用表 1、一半用表 3。早先这里写死 table=3 /
    # target_bler=0.1，经 MCP 走默认值恰好对得上，直接调 Python API 时就会出现
    # "rank 按 A 判据选、MCS 按 B 判据选"，而这种错配没有任何症状。
    _table_id = int(getattr(tables[0], "mcs_table", 3))
    _target_bler = float(getattr(tables[0], "target_bler", 0.1))
    _mismatch = [
        i for i, table in enumerate(tables)
        if int(getattr(table, "mcs_table", 3)) != _table_id
        or float(getattr(table, "target_bler", 0.1)) != _target_bler
    ]
    if _mismatch:
        raise ValueError(
            f"链路表的 MCS 表 / 目标 BLER 在 UE 之间不一致（错配 UE={_mismatch}）；"
            f"UE0 是 table={_table_id}、target_bler={_target_bler:g}")
    if _table_id not in la.MCS_TABLES:
        raise ValueError(f"未知 MCS 表 {_table_id}")
    # 用户默认只设 target_bler；SU/MU down 步长都由它与各自 up
    # 步长反解。显式 down 值是有意的研究 override，不会被覆盖。
    sched = sched.resolved_for_target(_target_bler)
    _mcs_table = la.MCS_TABLES[_table_id]
    serving_cells = {
        int(table.serving_cell_index) for table in tables
        if table.serving_cell_index is not None
    }
    if sys_cfg.rb_power_control.enabled:
        expected_power_fingerprint = pc.config_fingerprint(sys_cfg.rb_power_control)
        mismatched = [
            i for i, table in enumerate(tables)
            if table.rb_power_control_fingerprint != expected_power_fingerprint
        ]
        if mismatched:
            raise ValueError(
                "RB 功控链路表与系统配置不是同一份 profile；"
                f"错配 UE={mismatched}。请按当前 override 重新 build_link_tables")
    if serving_cells and (
            len(serving_cells) != 1
            or any(table.serving_cell_index is None for table in tables)):
        raise ValueError(
            "当前 SystemResult 是单小区调度结果，不能把不同 serving cell 的 UE "
            f"放进同一资源池（实得 {sorted(serving_cells)}）。请生成/筛选同一服务"
            "小区的 UE；逐小区联合调度属于下一阶段网络级仿真")
    if sys_cfg.rb_power_control.enabled and len(serving_cells) != 1:
        raise ValueError(
            "RB 功控要求链路表带唯一 serving_cell_index；"
            "请按当前数据集 metadata 重新 build_link_tables")
    if sys_cfg.evaluation_mode == "experience":
        # 独立路径把两种 profile 的资源分配与 KPI 语义彻底隔开。
        from . import experience as ex  # noqa: PLC0415

        run = ex.simulate_experience(
            tables, sys_cfg=sys_cfg, traffic_cfg=traffic, sched=sched, kpi=kpi,
            book=book, s_slot_fraction=S_SLOT_DL_FRACTION, progress=progress)
        return SystemResult(
            config={"system": sys_cfg.as_dict(), "traffic": traffic.as_dict(),
                    "scheduler": sched.as_dict(), "kpi": kpi.as_dict(),
                    "srs_resource_allocation": _srs_summary,
                    "harq_model": {
                        "max_retransmissions": 1,
                        "combining": str(sys_cfg.harq_combining).lower(),
                        "bler_source": "preset NewTx curves only",
                        "identity": "same MCS/RBG-count/rank/TBS as initial TB",
                    },
                    "mu_se_ratio": 1.0, "rng": book.as_dict(),
                    "physical_approximations": {
                        "sinr": (
                            "RBG grant-specific single-codeword SINR/MCS; current TB "
                            "compression is arithmetic mean in dB across granted RBGs, "
                            "not calibrated EESM/MIESM; frequency selection is independent "
                            "of RB power control"
                            if run.cell.get("frequency_selection", {}).get("enabled") else
                            "explicit wideband/sequential-RBG baseline"),
                        "harq": (
                            "one retransmission; same MCS/RBG-count/rank/TBS; "
                            f"{str(sys_cfg.harq_combining).upper()} combining derived "
                            "from preset NewTx curves; failed retransmission payload "
                            "later becomes a new TB"),
                        "allocator": (
                            "one PF sort; data-limited SU/MU plans; full MU partner scoring; "
                            "transactional RBG/layer/logical-PRB ledger; unified finalizer; "
                            "compare useful bytes; unused tail RBG stays idle; PDCCH not modelled"),
                        "mu_link_adaptation": (
                            "CQI+BF+SU-OLLA+CorrLoss+powerLoss+MU-OLLA; "
                            "separate user-level SU/MU OLLA arrays"),
                        "mu_receiver": (
                            "per-user LMMSE over each UE receive array; own rank streams "
                            "jointly detected, other-user streams form interference covariance"),
                        "csi_reporting": (
                            "periodic wideband PMI/CQI hold; zero extra feedback latency; "
                            "CQI uses causal expanding mean at report instants"),
                        "power_constraint": sys_cfg.power_constraint,
                        "crn_event_mapping": "harq and scheduler tie-break indexed by [TTI,UE]",
                        "tbs_resources": ("38.214 TBS quantization with preset MCS table 3; "
                                          "12 data symbols/RB and S-slot 0.7 scaling"),
                        "type1": ("single-panel Type-I-style beam-column subset; "
                                  "greedy multi-layer approximation"),
                    }},
            cell=run.cell, users=run.users, elapsed_s=run.elapsed_s,
            notes=run.notes, diagnostics=run.diagnostics)
    if sched.pf_accounting not in ("auto", "legacy_best_se"):
        raise ValueError("capacity/legacy_v1 只支持 pf_accounting=auto 或 legacy_best_se；"
                         "scheduled_tbs 请使用 evaluation_mode='experience'")
    if sched.algorithm in ("qos_pf", "edf", "qos_pf_edf"):
        raise ValueError(
            f"{sched.algorithm} 只属于 evaluation_mode='experience'")
    if traffic.model == "mixed":
        raise ValueError("mixed 话务只属于 evaluation_mode='experience'")
    n_ue = len(tables)
    rng_traffic = book.generator("traffic")
    harq_draw = book.generator("harq").random((int(sys_cfg.num_tti), n_ue))
    scheduler_draw = book.generator("scheduler").random((int(sys_cfg.num_tti), n_ue))
    # 小包只占 1 个 RBG：按该 RBG 的 RE 数 × 中等 MCS 谱效估承载
    # 1 个 RBG 一个 TTI 的承载：RB×12 子载波×12 数据符号×中等谱效
    _small_b = max(200, int(sys_cfg.rb_per_rbg * 12 * 12 * 3.0 / 8))
    tr = _Traffic(traffic, n_ue, sys_cfg.tti_ms, rng_traffic, small_bytes=_small_b,
                  num_rbg=sys_cfg.num_rbg)

    n_rb = sys_cfg.num_rb
    # 每 TTI 可用 RE：RB × 12 子载波 × 12 个数据符号（扣 DM-RS 与控制开销）
    re_per_tti = n_rb * 12 * 12
    # **S 时隙不是满下行。** 主循环把 D 和 S 一视同仁地当整个下行 TTI 调度，
    # 而 SystemConfig.dl_ratio 报告时又把 S 折成 0.7——同一个量两套口径，
    # 于是"实际调度的下行"比"报告的下行"多。现在按同一个系数折 RE。
    _re_of = {"D": re_per_tti, "S": int(re_per_tti * S_SLOT_DL_FRACTION)}
    snap_every = max(1, int(round(sys_cfg.snapshot_update_ms / sys_cfg.tti_ms)))
    n_snap = tables[0].sinr_db.shape[0]

    r_avg = np.full(n_ue, 1e-6)
    served = np.zeros(n_ue)
    sched_cnt = np.zeros(n_ue, dtype=int)
    retx_cnt = np.zeros(n_ue, dtype=int)
    retx_nack = np.zeros(n_ue, dtype=int)
    mcs_sum = np.zeros(n_ue)
    rank_sum = np.zeros(n_ue)
    nack_first = np.zeros(n_ue)
    tx_first = np.zeros(n_ue)
    nack_final = np.zeros(n_ue)
    harq_pending: dict[int, _LegacyHarqTb] = {}

    dl_tti = 0
    busy_tti = 0
    mu_tti = 0
    outage_tti = 0
    su_fits_skip = 0
    mu_rbg = 0
    olla_db = np.zeros(n_ue)              # 历史变量名；实际单位为连续 MCS index
    pattern = sys_cfg.tdd_pattern.upper() or "D"

    # **调度器只能用基站自己估的谱效。** 用真实谱效等于让它预知信道，
    # 它会自动绕开 CSI 老化最严重的用户，老化的代价就凭空消失了。
    # 零时延时 best_se_gnb 与 best_se 逐位相同，行为不变。
    # 在循环外解引用一次——这是每 TTI 每用户都要碰的量。
    _sched_se = [t.best_se_gnb if t.best_se_gnb is not None else t.best_se
                 for t in tables]

    for tti in range(sys_cfg.num_tti):
        # 业务到达属于连续时间轴，不能因为这一格是 U 时隙就消失。旧实现先
        # continue 再 step，DDDSU 下会系统性漏掉 20% 到达量，负载、排队和体验
        # 全被低估。先维护队列，再决定本 TTI 能否做下行调度。
        tr.step(tti)
        _slot = pattern[tti % len(pattern)]
        if _slot not in ("D", "S"):
            continue                                   # 上行时隙不调度下行
        re_per_tti = _re_of[_slot]                     # S 时隙的 RE 少三成
        dl_tti += 1
        snap = (tti // snap_every) % n_snap

        cand = [u for u in range(n_ue) if tr.has_data(u)
                and (u not in harq_pending or harq_pending[u].slot == _slot)
                and not (tables[u].outage is not None and tables[u].outage[snap])]
        blocked = sum(1 for u in range(n_ue) if tr.has_data(u)
                      and tables[u].outage is not None and tables[u].outage[snap])
        outage_tti += blocked
        if not cand:
            r_avg *= (1.0 - 1.0 / sched.pf_window_tti)
            continue

        # --- 调度判决 ---
        # 用的是 _sched_se：**基站自己估的**谱效（见主循环外的说明）
        inst_se = np.array([_sched_se[u][snap] for u in cand])
        if sched.algorithm == "pf":
            metric = inst_se / np.maximum(r_avg[cand], 1e-9)
        elif sched.algorithm == "max_ci":
            metric = inst_se
        else:                                          # rr
            metric = np.array([-((tti + u) % n_ue) for u in cand], dtype=float)
        # **决胜（tie-break）要随机，不能按 UE 编号。** 度量打平时 argsort 稳定排序
        # 恒把编号小的排前面，于是同信道、同队列的两个用户里编号小的**系统性**多拿
        # 调度机会——PF 的公平性判据看不出来（它只看 R_avg，而 R_avg 确实被拉平了），
        # 但逐用户 KPI 会带一个与编号相关的偏置。
        # 只在**真有平局**时才抽签：没有平局时 lexsort 与 argsort 结果相同，
        # 但抽签会白白消耗 scheduler 流，也让"没有平局的配置"变得不可复现比对。
        _m = metric.tolist()
        if len(_m) > 1 and len(set(_m)) < len(_m):
            order = np.lexsort((scheduler_draw[tti, cand], -metric))
        else:
            order = np.argsort(-metric)

        # 同 slot 类型的待重传 TB 优先，且只重传一次。相同 slot 保证原 MCS/rank/
        # 全带 RBG 对应的 TBS 不因 D/S 可用 RE 不同而改变。
        _retx_ready = [
            u for u in cand
            if u in harq_pending and harq_pending[u].slot == _slot
        ]

        # **SU 能一个 TTI 传完就不触发 MU**（用户 2026-08-02 的现场准则）——
        # 数据都发完了，配对没有意义，还白白引入用户间干扰。
        _top = cand[order[0]]
        _top_rank = int(tables[_top].best_rank[snap])
        _top_mcs = _mcs_table[int(tables[_top].mcs[snap, _top_rank - 1])]
        _su_bytes = int(la.transport_block_size(
            re_per_tti, _top_mcs.rate, _top_mcs.q_m, layers=_top_rank) // 8)
        _fits_in_su = tr.bytes_left(_top) <= _su_bytes
        if _retx_ready:
            use_mu = False
            picked = [min(_retx_ready, key=lambda u: harq_pending[u].first_tti)]
        else:
            use_mu = (sched.mu_enabled and mu_se_ratio > 1.0
                      and len(cand) >= 2 and not _fits_in_su)
            if _fits_in_su and len(cand) >= 2:
                su_fits_skip += 1
            picked = ([cand[i] for i in order[:sched.max_mu_users]]
                      if use_mu else [cand[order[0]]])
        if use_mu:
            mu_tti += 1
            mu_rbg += sys_cfg.num_rbg          # MU 时整band 都是配对的
        busy_tti += 1

        # --- 发送 ---
        # **MU 是空间复用，不是频率复用。** 配对的每个用户都拿**全带宽**，
        # 靠不同的空间波束区分。早先按 1/K 分 RE，MU 的聚合吞吐就和 SU 一模一样——
        # 等于把空间复用做成了时频复用，MU 增益整个消失。
        n_pair = len(picked)
        actual_inst_se = np.zeros(n_ue, dtype=float)
        for u in picked:
            pend = harq_pending.get(u)
            if pend is not None:
                r, m = int(pend.rank), int(pend.mcs)
            else:
                r = int(tables[u].best_rank[snap])
                if use_mu:
                    r = min(r, mu.MU_MAX_RANK)  # MU 每用户硬顶 rank2（工程约束）
            # 发送端先按 CQI 门限 + BF Gain 的 SINR 反折基准 MCS，
            # 再在 MCS 域叠加 OLLA；接收端仍按真实 SINR 判误码。
            if pend is None:
                if tables[u].sinr_tx_db is not None and sched.olla_enabled:
                    _tx = float(tables[u].sinr_tx_db[snap, r - 1])
                    _base_mcs = int(la.select_mcs(
                        _tx, table=_table_id, target_bler=_target_bler).index)
                    m = int(la.apply_olla_mcs(
                        _base_mcs, float(olla_db[u]),
                        mcs_table=_table_id)["final_mcs"])
                else:
                    m = int(tables[u].mcs[snap, r - 1])
            mcs_obj = _mcs_table[m]
            if pend is not None:
                tb_bytes = int(pend.tb_bytes)
                payload_bytes = int(pend.payload_bytes)
            else:
                tbs_bits = la.transport_block_size(
                    re_per_tti, mcs_obj.rate, mcs_obj.q_m, layers=r)
                if use_mu:
                    # 配对后每人只分到 1/K 的功率、还要吃残余干扰。
                    # mu_se_ratio 是建表阶段用真实 SU/MU 自适应测出来的**聚合**比值，
                    # 所以这里除以配对数，使 K 个用户加起来 = ratio x 单用户 SU。
                    tbs_bits *= mu_se_ratio / n_pair
                tb_bytes = max(1, int(tbs_bits // 8))
                payload_bytes = min(int(tr.bytes_left(u)), tb_bytes)
            actual_inst_se[u] = mcs_obj.se * r * (
                mu_se_ratio / n_pair if use_mu else 1.0)

            # HARQ：首传按该 MCS 的 BLER 判 ACK/NACK，失败进重传
            if pend is not None:
                sinr = float(tables[u].sinr_db[snap, r - 1])
                retx = la.harq_retransmission_bler(
                    m, sinr, combining=sys_cfg.harq_combining, table=_table_id)
                bler = float(retx["bler"])
                retx_cnt[u] += 1
                if harq_draw[tti, u] > bler:
                    served[u] += tr.serve(u, tti, payload_bytes)
                else:
                    retx_nack[u] += 1
                    nack_final[u] += 1
                # 无论 ACK/NACK 都结束本次 HARQ；失败字节仍在队列，后续作为新 TB。
                harq_pending.pop(u, None)
                sched_cnt[u] += 1
                mcs_sum[u] += m
                rank_sum[u] += r
                continue

            sinr = float(tables[u].sinr_db[snap, r - 1])
            bler = _bler_lookup(m, sinr)
            tx_first[u] += 1
            sched_cnt[u] += 1
            mcs_sum[u] += m
            rank_sum[u] += r
            if harq_draw[tti, u] > bler:
                sent = tr.serve(u, tti, tb_bytes)
                served[u] += sent
                if sched.olla_enabled:      # ACK：小步上调
                    olla_db[u] = min(olla_db[u] + sched.step_up,
                                     sched.olla_max_db)
            else:
                nack_first[u] += 1
                harq_pending[u] = _LegacyHarqTb(
                    mcs=m, rank=r, tb_bytes=tb_bytes,
                    payload_bytes=payload_bytes, slot=_slot,
                    first_tti=tti, was_mu=bool(use_mu))
                if sched.olla_enabled:      # NACK：大步下调
                    olla_db[u] = max(olla_db[u] - sched.step_down,
                                     sched.olla_min_db)

        # --- PF 平均速率更新 ---
        # PF 使用实际发射 MCS/rank（重传也保持原 TB 参数），不拿当前 best_se 冒充。
        inst = actual_inst_se
        a = 1.0 / sched.pf_window_tti
        r_avg = (1.0 - a) * r_avg + a * inst
        if progress and tti % 5000 == 0:
            progress(tti, sys_cfg.num_tti)

    # --- KPI 汇总 ---
    offered_bytes = int(tr.offered_bytes)
    served_bytes = int(np.sum(served, dtype=np.float64))
    users: list[UeKpi] = []
    small_thp: list[float] = []
    large_thp: list[float] = []
    for u in range(n_ue):
        _warmup_tti = kpi.resolve_warmup_tti(sys_cfg.tti_ms)
        _done = [b for b in tr.done[u] if b.start_tti >= _warmup_tti]
        thps = [x for x in (_burst_throughput_mbps(b, sys_cfg.tti_ms, kpi)
                            for b in _done) if x is not None]
        # **小包和大包要分开报。** 小包的体验速率被调度时延主导、大包才反映
        # 信道能力，混在一起平均会得到一个谁都不像的数。
        _sm = [x for b in _done if b.is_small
               for x in [_burst_throughput_mbps(b, sys_cfg.tti_ms, kpi)] if x is not None]
        _lg = [x for b in _done if not b.is_small
               for x in [_burst_throughput_mbps(b, sys_cfg.tti_ms, kpi)] if x is not None]
        small_thp.append(float(np.mean(_sm)) if _sm else float("nan"))
        large_thp.append(float(np.mean(_lg)) if _lg else float("nan"))
        users.append(UeKpi(
            ue=u, geo_sinr_db=tables[u].geo_sinr_db, iot_db=tables[u].iot_db,
            experienced_mbps=float(np.mean(thps)) if thps else 0.0,
            served_mbps=served[u] * 8 / max(sys_cfg.duration_s, _EPS) / 1e6,
            bursts=len(thps),
            avg_mcs=float(mcs_sum[u] / max(sched_cnt[u], 1)),
            avg_rank=float(rank_sum[u] / max(sched_cnt[u], 1)),
            bler_first_tx=float(nack_first[u] / max(tx_first[u], 1)),
            retx_bler=float(retx_nack[u] / max(retx_cnt[u], 1)),
            residual_bler=float(
                nack_final[u]
                / max(tx_first[u] - int(u in harq_pending), 1)),
            sched_tti=int(sched_cnt[u]), retx_tti=int(retx_cnt[u]),
        ))

    exp = np.array([x.experienced_mbps for x in users if x.bursts > 0])
    cell = {
        # **小区体验速率是各用户体验速率的平均，不是求和。** 用户是时分复用的，
        # 求和会得到"每个用户都独享整个小区"的假数——实测过一次 8.2 Gbps
        # 落在 100 MHz 小区上，物理峰值只有约 1.2 Gbps。
        "cell_experienced_mbps": float(np.mean(exp)) if exp.size else 0.0,
        "ue_experienced_mean_mbps": float(np.mean(exp)) if exp.size else 0.0,
        "ue_experienced_median_mbps": float(np.median(exp)) if exp.size else 0.0,
        "ue_experienced_p5_mbps": float(np.percentile(exp, 5)) if exp.size else 0.0,
        # Keep accounting quantities at full precision.  ``offered_mbps`` used
        # to be rounded to 3 decimals while this value was not; an exactly
        # balanced trace could then appear to serve 0.000333 Mbps more than it
        # offered.  Presentation may round, the result contract must not.
        "cell_served_mbps": served_bytes * 8 / max(sys_cfg.duration_s, _EPS) / 1e6,
        "avg_mcs": float(np.sum(mcs_sum) / max(np.sum(sched_cnt), 1)),
        "avg_rank": float(np.sum(rank_sum) / max(np.sum(sched_cnt), 1)),
        "bler_first_tx": float(np.sum(nack_first) / max(np.sum(tx_first), 1)),
        "retx_bler": float(np.sum(retx_nack) / max(np.sum(retx_cnt), 1)),
        "retx_attempts": int(np.sum(retx_cnt)),
        "retx_nacks": int(np.sum(retx_nack)),
        "residual_bler": float(
            np.sum(nack_final) / max(np.sum(tx_first) - len(harq_pending), 1)),
        "pending_harq_tb_at_end": int(len(harq_pending)),
        "residual_bler_definition": (
            "failed unique retransmissions / initial TBs whose HARQ outcome is "
            "observed; end-of-run pending TBs are right-censored"),
        "dl_tti": dl_tti, "scheduled_tti": busy_tti,
        "occupancy": busy_tti / max(dl_tti, 1),
        "mu_share": mu_tti / max(busy_tti, 1),
        "measured_bursts": int(np.sum([x.bursts for x in users])),
        # bimodal 下小包与大包分开报：前者由调度时延主导，后者反映信道能力
        "rbg_size_hist": (
            {"p_1rbg": round(float(np.mean(np.array(tr.rbg_hist) == 1)), 3),
             "p_full": round(float(np.mean(np.array(tr.rbg_hist) >= sys_cfg.num_rbg)), 3),
             "mean_rbg": round(float(np.mean(tr.rbg_hist)), 2),
             "n": len(tr.rbg_hist)} if tr.rbg_hist else None),
        "small_pkt_experienced_mbps": (float(np.nanmean(small_thp))
                                       if np.any(np.isfinite(small_thp)) else None),
        "large_pkt_experienced_mbps": (float(np.nanmean(large_thp))
                                       if np.any(np.isfinite(large_thp)) else None),
        "outage_ue": int(sum(1 for t in tables
                             if t.outage is not None and t.outage.all())),
        "outage_skips": int(outage_tti),
        # **OLLA 收敛到多少，就说明发送端把干扰低估了多少。**
        # 它应当与 IoT 同向：干扰越大、偏置越负。
        "olla_db_mean": float(np.mean(olla_db)),
        "olla_db_p5": float(np.percentile(olla_db, 5)),
        "olla_db_p95": float(np.percentile(olla_db, 95)),
        "olla_mcs_mean": float(np.mean(olla_db)),
        "olla_mcs_p5": float(np.percentile(olla_db, 5)),
        "olla_mcs_p95": float(np.percentile(olla_db, 95)),
        "olla_domain": "continuous_mcs_index",
        "olla_target_bler": round(sched.olla_step_up_db
                                  / (sched.olla_step_up_db + sched.olla_step_down_db), 4),
        # **MU 配对比例**：MU 配对的 RBG 数占已调度 RBG 总数。
        # 现场经验值：30%~50% PRB 利用率下大约 5%~20%。
        "mu_rbg_share": mu_rbg / max(busy_tti * sys_cfg.num_rbg, 1),
        "su_fits_skips": int(su_fits_skip),
        # **IoT = (I+N)/N**：干扰主导还是噪声主导。密集城区常 >20 dB。
        "iot_db_median": _nan_safe(np.nanmedian, [t.iot_db for t in tables]),
        "iot_db_p5": _nan_safe(np.nanpercentile, [t.iot_db for t in tables], 5),
        "iot_db_p95": _nan_safe(np.nanpercentile, [t.iot_db for t in tables], 95),
        "iot_sample_valid_share": float(np.mean(
            [t.iot_sample_valid for t in tables])) if tables else 0.0,
        "iot_valid_ue_share": float(np.mean(
            [bool(np.isfinite(t.iot_db)) for t in tables])),
        "high_iot_ue_share": float(np.mean([
            (t.iot_db >= 20.0) if np.isfinite(t.iot_db) else False for t in tables])),
        # **边缘用户 MCS**：现场经验通常 < 5。它比平均 MCS 更能暴露覆盖问题。
        "edge_mcs_p5": _nan_safe(np.nanpercentile,
                                 [x.avg_mcs for x in users if x.sched_tti > 0], 5),
        # 守恒对账：到达了多少、发完了多少、还压着多少。
        # 不报这三个的话，"实际吞吐 105 Mbps vs 话务负载 144 Mbps"
        # 这种缺口只能靠猜——它可能是队列积压（正常），也可能是漏数据（bug）。
        "offered_mbps": offered_bytes * 8 / max(sys_cfg.duration_s, _EPS) / 1e6,
        "offered_bytes": offered_bytes,
        "served_bytes": served_bytes,
        "completed_bursts": int(sum(len(x) for x in tr.done)),
        "backlog_bursts": int(sum(1 for x in tr.active if x is not None)
                              + sum(len(q) for q in tr.queue)),
        "backlog_bytes": int(sum((x.bytes_left for x in tr.active if x is not None), 0)
                             + sum(b.bytes_left for q in tr.queue for b in q)),
    }
    _acct = served_bytes + cell["backlog_bytes"]
    cell["accounting_error_pct"] = round(
        abs(_acct - offered_bytes) / max(offered_bytes, 1) * 100, 3)
    notes: list[str] = []
    if n_snap < 4:
        notes.append(f"**信道快照只有 {n_snap} 个**，时间起伏被严重低估，"
                     "PF 的多用户分集增益拿不到——生成时把 num_slots_per_sample 调大。")
    if cell["measured_bursts"] < 20:
        notes.append(f"只有 {cell['measured_bursts']} 个 burst 进入体验速率统计，"
                     "样本太少。**加长 duration_s 或提高到达率**。")
    if cell["backlog_bytes"] > 0.15 * max(offered_bytes, 1):
        notes.append(
            f"**队列积压 {cell['backlog_bytes']*8/1e6:.1f} Mb**"
            f"（占到达量 {cell['backlog_bytes']/max(offered_bytes,1):.0%}）——"
            "系统在这个负载下没有收敛，体验速率被排队时间拖低。"
            "降低 arrival_rate_hz 或加长 duration_s 再看。")
    if cell["accounting_error_pct"] > 1.0:
        notes.append(f"**字节对不上账（差 {cell['accounting_error_pct']}%）**——"
                     "发出去的 + 还压着的 应该等于到达的。这是 bug 不是现象。")
    if np.isfinite(cell["edge_mcs_p5"]) and cell["edge_mcs_p5"] > 8:
        notes.append(
            f"**5% 边缘用户的 MCS 是 {cell['edge_mcs_p5']:.1f}，偏高**"
            "（现场经验通常 <5）。多半是撒点没覆盖到真正的边缘，"
            "或者邻区负载设得太低、干扰被低估了。")
    # **p_idle_tti 是对标锚点，不是仿真输入。** 它只进解析式 expected_prb_util，
    # 不生成任何空闲 TTI——真实的空闲来自"没人有数据"。两者差太多说明
    # 到达率没调到位，得说出来，否则用户会以为设了 30% 就真是 30%。
    if traffic.model == "bimodal":
        _want_idle = float(traffic.p_idle_tti)
        _got_idle = 1.0 - float(cell["occupancy"])
        if abs(_got_idle - _want_idle) > 0.10:
            notes.append(
                f"**空闲 TTI 实测 {_got_idle:.0%}，而 p_idle_tti 设的是 "
                f"{_want_idle:.0%}。** p_idle_tti **不驱动仿真**——它只是个对标锚点，"
                f"真实的空闲 TTI 由到达率与信道决定。要对齐现网就调 "
                f"arrival_rate_hz，改 p_idle_tti 只会改报告里的 expected_prb_util，"
                f"不会改任何实际行为。")
    _tgt = cell["olla_target_bler"]
    if sched.olla_enabled and cell["bler_first_tx"] > _tgt * 1.6:
        notes.append(
            f"**首传 BLER {cell['bler_first_tx']:.3f} 明显高于 OLLA 的稳态目标 "
            f"{_tgt:.3f}，说明外环还没收敛完。** 现网基线步长 +0.01/−0.1 很慢，"
            f"每次 NACK 只压 0.1 dB，而 MCS 是整数档、小步长常常压不动一档。"
            "要看稳态结论就加长 duration_s；要快收敛就临时把步长调大"
            "（比例不变则稳态 BLER 不变）。")
    # **判据必须是逐样本有效率。** 逐用户的那个恒等于 1（nanmedian 会把
    # 半数 nan 的用户也算成有效），于是这条正确的告警从不触发，
    # 反而触发下面那条"检查站间距"——把用户支使去查一个根本没问题的配置。
    _iot_ok = cell.get("iot_sample_valid_share", 1.0)
    if _iot_ok < 0.9:
        notes.append(
            f"**IoT 不可信：只有 {_iot_ok:.0%} 的样本算得出来**"
            f"（逐用户口径会报 {cell['iot_valid_ue_share']:.0%}，那个数会骗人）。"
            "根因是生成时 num_slots_per_sample > 1——那时 sinr_dB 是各 slot 的"
            "dB 均值、sir_dB 只取最后一个 slot，两者不同口径，"
            "会出现 SIR < SINR 这种物理上不可能的值。"
            "**别去查站间距和邻区负载，配置没问题，是这个量本身在多时隙下不成立。**"
            "要看 IoT 就用 num_slots_per_sample=1 单独生成一批"
            "——但那批做不了系统级仿真（PF 拿不到时间分集、CSI 老化恒为 0），"
            "**这两个需求当前无法在同一个数据集上同时满足**。")
    elif np.isfinite(cell["iot_db_median"]) and cell["iot_db_median"] < 3:
        notes.append(
            f"**IoT 中位只有 {cell['iot_db_median']:.1f} dB**，几乎是噪声受限。"
            "密集城区实际常在 20 dB 以上——检查是不是站间距太大、"
            "或者邻区负载 prb_utilization 设得过低。")
    if traffic.model == "bimodal":
        _u = traffic.expected_prb_util(sys_cfg.num_rbg)
        if abs(_u - 0.30) > 0.05:
            notes.append(
                f"**这套 RBG 尺寸分布折合出来的 PRB 利用率是 {_u:.1%}，"
                f"现网口径约 30%**。差在中间段——2~{sys_cfg.num_rbg - 1} 个 RBG "
                f"均匀分布的均值是 {(2 + sys_cfg.num_rbg - 1) / 2 / sys_cfg.num_rbg:.2f}，"
                "偏高，把中间段改成偏小的分布才能对齐。"
                "**别指望调 p_idle_tti**——它不驱动仿真，只改这个报告数字，"
                "真实的空闲 TTI 由 arrival_rate_hz 决定。"
                "**这个参数我没有替你调，因为它直接决定负载**。")
    if traffic.model == "bimodal" and cell["small_pkt_experienced_mbps"] is None:
        notes.append(
            "**legacy_v1 的小包体验速率测不出来**：历史 trim 实现会排除"
            "清空缓冲区的末 slice，单 slice burst 因而没有时间分母。"
            "这只是 legacy 复现口径的盲区；要按 TS 28.552 Rel-19 的小 burst"
            "fractional-slot 口径与 FIFO 等待/PDB，请改用 evaluation_mode='experience'。")
    if cell["outage_ue"]:
        notes.append(
            f"**{cell['outage_ue']} 个用户全程处于覆盖外**（用户级 SINR 够不到 MCS 0 的门限），"
            "已从调度中剔除。他们不进 BLER 与体验速率统计——"
            "但这本身就是个结论：这些点位需要补站或降配。")
    if cell["occupancy"] > 0.98:
        notes.append("**下行时隙几乎占满**，系统已过载——此时体验速率反映的是"
                     "容量上限而不是用户体验，降低到达率再测。")
    return SystemResult(
        config={"system": sys_cfg.as_dict(), "traffic": traffic.as_dict(),
                "scheduler": sched.as_dict(), "kpi": kpi.as_dict(),
                "srs_resource_allocation": _srs_summary,
                "harq_model": {
                    "max_retransmissions": 1,
                    "combining": str(sys_cfg.harq_combining).lower(),
                    "bler_source": "preset NewTx curves only",
                    "identity": "same MCS/RBG-count/rank/TBS as initial TB",
                    "timing": "retransmit on the same D/S slot type",
                },
                "mu_se_ratio": round(float(mu_se_ratio), 4),
                "rng": {**book.as_dict(),
                        "event_mapping": "harq and scheduler tie-break indexed by [TTI,UE]"}},
        cell=cell, users=[x.as_dict() for x in users],
        elapsed_s=time.perf_counter() - t0, notes=notes,
    )


# ---------------------------------------------------------------------------
# 多次重复：所有 KPI 带置信区间
# ---------------------------------------------------------------------------


def _init_replication_process(
    tables: list[UeLinkTable],
    sys_cfg: SystemConfig | None,
    traffic: TrafficConfig | None,
    sched: SchedulerConfig | None,
    kpi: KpiConfig | None,
    mu_se_ratio: float,
) -> None:
    """Install read-only simulation state once in each spawned worker."""
    _REPLICATION_PROCESS_STATE.clear()
    _REPLICATION_PROCESS_STATE.update({
        "tables": tables,
        "sys_cfg": sys_cfg,
        "traffic": traffic,
        "sched": sched,
        "kpi": kpi,
        "mu_se_ratio": float(mu_se_ratio),
    })


def _run_replication_process(index: int, book: rg.RngBook) -> tuple[int, SystemResult]:
    """ProcessPool target; stable index restores replication order exactly."""
    state = _REPLICATION_PROCESS_STATE
    return index, simulate(
        state["tables"], sys_cfg=state["sys_cfg"], traffic=state["traffic"],
        sched=state["sched"], kpi=state["kpi"],
        mu_se_ratio=float(state["mu_se_ratio"]), rng=book)


def _resolve_replication_workers(
    requested: int | str,
    *,
    num_replications: int,
    num_tti: int,
    num_ues: int,
) -> tuple[int, dict[str, Any]]:
    """Choose process count from measured TTI work, never from sample count alone."""
    if isinstance(requested, str):
        text = requested.strip().lower()
        if text == "auto":
            mode = "auto"
        elif text.isdigit() and int(text) >= 1:
            requested = int(text)
            mode = "explicit"
        else:
            raise ValueError("replication_workers 只支持正整数或 'auto'")
    elif (isinstance(requested, (bool, np.bool_))
          or not isinstance(requested, (int, np.integer))
          or int(requested) < 1):
        raise ValueError("replication_workers 只支持正整数或 'auto'")
    else:
        mode = "explicit"

    cpu = max(int(os.cpu_count() or 1), 1)
    cap = max(1, min(8, cpu, int(num_replications)))
    work_units = int(num_replications) * int(num_tti) * max(int(num_ues), 1)
    # Frozen on this workstation by scripts/run_performance_audit.py:
    # 8 reps × 6 UE × 10k TTI gained 1.61x with four processes, while
    # threads were 0.72x.  Stay serial for smaller jobs to avoid Windows spawn
    # and table-pickle overhead; explicit user settings remain authoritative.
    if mode == "auto":
        workers = min(4, cap) if (num_replications >= 4 and work_units >= 300_000) else 1
    else:
        if int(requested) > cap:
            raise ValueError(
                f"replication_workers={int(requested)} 超过当前安全上限 {cap}"
                "（受 CPU、重复次数与每进程链路表内存共同限制）")
        workers = int(requested)
    return workers, {
        "requested": requested,
        "policy": mode,
        "workers": workers,
        "worker_cap": cap,
        "work_units_rep_tti_ue": work_units,
        "auto_parallel_threshold": 300_000,
        "backend": "process" if workers > 1 else "serial",
        "thread_backend_disabled": (
            "measured slower for Python event loop; see performance_audit.json"),
    }


@dataclass
class ReplicationResult:
    """n 次重复的汇总。**每个 KPI 都是 mean / std / ci95 / n_rep，不是一个裸数。**"""

    runs: list[SystemResult]
    books: list[rg.RngBook]
    cell: dict[str, dict[str, Any]]
    users: list[dict[str, Any]]
    config: dict[str, Any]
    elapsed_s: float
    build_elapsed_s: float = 0.0
    parallel: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def n_rep(self) -> int:
        return len(self.runs)

    def stat(self, key: str) -> rg.KpiStat:
        """某个小区级 KPI 在各次重复上的分布，拿去做 A/B 用。"""
        return rg.summarize([r.cell[key] for r in self.runs if key in r.cell], key)

    def as_dict(self) -> dict[str, Any]:
        cell_samples: dict[str, list[float]] = {}
        for key in sorted(self.cell):
            values = [run.cell.get(key) for run in self.runs]
            if values and all(_finite_real(value) for value in values):
                cell_samples[key] = [float(value) for value in values]
        out = {"config": self.config, "cell": self.cell, "users": self.users,
               "n_rep": self.n_rep,
               "replications": [b.as_dict()["replication"] for b in self.books],
               "elapsed_s": round(self.elapsed_s, 3),
               "build_tables_s": round(self.build_elapsed_s, 3),
               "parallel": self.parallel,
               "notes": self.notes,
               "comparison_evidence": {
                   "schema": "superran_system_comparison_evidence_v1",
                   "rng_books": [book.as_dict() for book in self.books],
                   "cell_samples_by_replication": cell_samples,
                   "pairing_key": "(master_seed, replication)",
                   "scope": (
                       "paired KPI decisions use per-replication values; "
                       "single-TTI trace is diagnostic and never replaces Gate 3"
                   ),
               }}
        if self.runs:
            definitions = self.runs[0].diagnostics.get("kpi_definitions")
            if definitions:
                out["kpi_definitions"] = definitions
            trace = self.runs[0].diagnostics.get("tti_trace")
            if trace:
                out["tti_trace"] = trace
            for key in ("traffic_profiles", "traffic_samples"):
                value = self.runs[0].diagnostics.get(key)
                if value is not None:
                    out[key] = value
            # summarize_runs 只保留数值型 KPI，结构化诊断会被整条丢掉。
            # 调度器身份与混合分量量级是判读结果的前提（notes 明确指着它们），
            # 所以按 kpi_definitions/tti_trace 同款方式搬过来。
            for key in ("scheduler_priority_metric",
                        "scheduler_mixed_component_scale",
                        "scheduler_starvation_lifts"):
                value = self.runs[0].cell.get(key)
                if value is not None:
                    out.setdefault("scheduler", {})[key] = value
        return out

    def text(self) -> str:
        def _s(k: str) -> str:
            d = self.cell.get(k) or {}
            m, lo, hi = d.get("mean"), *(d.get("ci95") or [None, None])
            if m is None:
                return "n/a"
            return f"{m:.2f}" if lo is None else f"{m:.2f} [{lo:.2f}, {hi:.2f}]"
        head = (f"（{self.n_rep} 次重复，方括号内是 95% 置信区间）"
                if self.n_rep > 1 else
                "（**只跑了 1 次，下面所有数字都没有置信区间**，"
                "不能用来做比较）")
        if self.config.get("system", {}).get("model_version") == "experience_v2":
            return (
                f"{head}\n"
                f"小区体验速率 {_s('cell_experienced_mbps')} Mbps"
                f"（含头 {_s('cell_head_inclusive_experienced_mbps')}，"
                f"5% 边缘 {_s('ue_experienced_p5_mbps')}）\n"
                f"首包时延 P95 {_s('first_packet_delay_ms_p95')} ms，"
                f"本小区 PRB 利用率 {_s('serving_cell_prb_utilization')}，"
                f"MU 配对占已用 PRB {_s('mu_paired_prb_share_of_used')}\n"
                f"平均调度 MCS {_s('avg_mcs')}，平均 rank {_s('avg_rank')}，"
                f"首传 BLER {_s('bler_first_tx')}"
            )
        return (
            f"{head}\n"
            f"小区体验速率 {_s('cell_experienced_mbps')} Mbps"
            f"（5% 边缘 {_s('ue_experienced_p5_mbps')}）\n"
            f"平均调度 MCS {_s('avg_mcs')}，平均 rank {_s('avg_rank')}，"
            f"首传 BLER {_s('bler_first_tx')}"
        )


def simulate_replications(
    tables: list[UeLinkTable],
    *,
    num_replications: int = 8,
    master_seed: int = 0,
    replication_start: int = 0,
    sys_cfg: SystemConfig | None = None,
    traffic: TrafficConfig | None = None,
    sched: SchedulerConfig | None = None,
    kpi: KpiConfig | None = None,
    mu_se_ratio: float = 1.0,
    build_elapsed_s: float = 0.0,
    replication_workers: int | str = 1,
    progress: Any = None,
) -> ReplicationResult:
    """跑 n 次独立重复，所有 KPI 报 ``mean / std / ci95 / n_rep``。

    **关键优化：只重跑 TTI 主循环，不重建链路表。** :func:`build_link_tables`
    与随机种子完全无关（它只做 SVD、码本搜索、MCS 查表），所以建一次表就够了。
    多跑 n 次的代价因此是 ``(n−1)·T_loop / (T_build + T_loop)``——
    实测 ds_6e9715bc 上建表 5.14 s、单次主循环 0.99 s（交错 3 轮取中位，
    建表轮间波动 11.3%、主循环 3.2%），n=8 是 13.0 s vs 单次 6.1 s，**多 113%**。
    建表越贵这个比例越低：按 10.5 s / 1.1 s 算是 +66%。

    重复实验换的是 ``replication``（对应 ns-3 的 ``RngRun``）而不是 ``master_seed``
    （对应 ``RngSeed``），理由见 :mod:`rng` 的模块文档。
    ``replication_start`` 可在同一主种子下选择一段不重叠的 RngRun；默认仍从 0
    开始。它用于把负载校准的 probe 与正式反馈样本隔离开。

    ``replication_workers`` 默认 1 保持库/Notebook/脚本入口安全；MCP 前门默认
    ``"auto"``，只在测得足以覆盖 Windows spawn 与链路表序列化成本时启用进程。
    每个进程初始化时只接收一次只读链路表，任务只传 RngBook；结果按原 replication
    index 还原，因此 1/4 workers 的 KPI 与随机身份逐位一致。线程后端不提供：当前
    TTI 状态机以 Python 事件处理为主，实测 4 线程比串行更慢。

    **这个置信区间覆盖什么、不覆盖什么，必须说清楚。** 各次重复共用同一批信道
    与同一张链路表，所以区间反映的是**话务到达、HARQ 误码、调度决胜**这三条流。
    邻区负载抖动在建表阶段就定死了，**它不进区间**。

    这个取舍是量过的，不是拍的（``measurements/rng_replication.json``）：
    64 次 replication（表固定）与 32 次 master seed 扫描（每次重建表、
    负载抖动重抽）的变异系数对照——

    ===========================  ==================  ==================
    KPI                          replication (n=64)  master seed (n=32)
    ===========================  ==================  ==================
    ``cell_experienced_mbps``    9.40% [8.0, 11.4]   5.93% [4.8, 7.9]
    ``ue_experienced_p5_mbps``   18.62% [15.9, 22.6] 18.93% [15.2, 25.2]
    ``avg_mcs``                  8.14% [6.9, 9.9]    10.46% [8.4, 13.9]
    ``avg_rank``                 2.86% [2.4, 3.5]    2.51% [2.0, 3.3]
    ``bler_first_tx``            8.84% [7.5, 10.7]   10.59% [8.5, 14.1]
    ===========================  ==================  ==================

    方括号是**变异系数自身**的 95% 区间（χ²）。五个 KPI 里四个两列的区间重叠，
    也就是说**冻结链路表并没有可分辨地把离散度报小**——系统级的主导方差就是
    话务与 HARQ，正好是区间覆盖的那几条流。

    顺带一个必须记住的量级：n=8 时变异系数自身的 95% 区间是 ``0.66×~2.04×``。
    ``measurements/seed_variance.json`` 里那个 11.4% 是 8 个种子测的，
    真值可能在 7.5%~23% 之间——**那张表上的 CoV 只精确到大约 2 倍**，
    不要拿它去做精细比较。

    信道实现本身的不确定度是**另一个、更大的方差分量**，要覆盖它得用不同 seed
    重新生成数据集，本函数不做也做不到。
    """
    if (isinstance(num_replications, (bool, np.bool_))
            or not isinstance(num_replications, (int, np.integer))
            or int(num_replications) < 1):
        raise ValueError(f"重复次数至少 1 次且必须为整数，收到 {num_replications}")
    n = int(num_replications)
    if (isinstance(master_seed, (bool, np.bool_))
            or not isinstance(master_seed, (int, np.integer)) or int(master_seed) < 0):
        raise ValueError(f"master_seed 必须是非负整数，收到 {master_seed}")
    if n < 1:
        raise ValueError(f"重复次数至少 1 次，收到 {n}")
    t0 = time.perf_counter()
    books = rg.replications(master_seed, n, start=replication_start)
    worker_cfg = sys_cfg or SystemConfig()
    workers, parallel = _resolve_replication_workers(
        replication_workers, num_replications=n, num_tti=worker_cfg.num_tti,
        num_ues=len(tables))
    runs: list[SystemResult]
    if workers <= 1:
        runs = []
        for i, bk in enumerate(books):
            runs.append(simulate(
                tables, sys_cfg=sys_cfg, traffic=traffic, sched=sched,
                kpi=kpi, mu_se_ratio=mu_se_ratio, rng=bk))
            if progress:
                progress(i + 1, n)
    else:
        ordered: list[SystemResult | None] = [None] * n
        try:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_replication_process,
                initargs=(tables, sys_cfg, traffic, sched, kpi, float(mu_se_ratio)),
            ) as pool:
                futures = {
                    pool.submit(_run_replication_process, i, bk): i
                    for i, bk in enumerate(books)
                }
                complete = 0
                for future in as_completed(futures):
                    index, run = future.result()
                    ordered[index] = run
                    complete += 1
                    if progress:
                        progress(complete, n)
            if any(run is None for run in ordered):
                raise RuntimeError("并行重复实验缺少结果")
            runs = [run for run in ordered if run is not None]
        except Exception as exc:
            if str(replication_workers).strip().lower() != "auto":
                raise RuntimeError(
                    f"显式 replication_workers={workers} 执行失败，未静默降级") from exc
            parallel["fallback_reason"] = f"{type(exc).__name__}: {exc}"
            parallel["workers"] = 1
            parallel["backend"] = "serial_fallback"
            runs = []
            for i, bk in enumerate(books):
                runs.append(simulate(
                    tables, sys_cfg=sys_cfg, traffic=traffic, sched=sched,
                    kpi=kpi, mu_se_ratio=mu_se_ratio, rng=bk))
                if progress:
                    progress(i + 1, n)

    cell = rg.summarize_runs([r.cell for r in runs])
    # TTI 占用分布不是一个可直接求均值的 scalar，不能被 summarize_runs 静默
    # 丢掉。每个 0..N 桶分别跨 replication 汇总，供 KPI 页直接画柱状图；每次
    # replication 内先归一到 TTI share，避免运行时长变化时按绝对 count 加权。
    occupancy_distributions = [
        r.cell.get("tti_occupied_rbg_distribution") for r in runs]
    if all(isinstance(d, dict) for d in occupancy_distributions):
        first_distribution = occupancy_distributions[0]
        assert isinstance(first_distribution, dict)
        first_bins = first_distribution.get("bins", [])
        aggregated_bins: list[dict[str, Any]] = []
        for idx, first_bin in enumerate(first_bins):
            shares = [
                float(d["bins"][idx]["tti_share"])
                for d in occupancy_distributions
                if isinstance(d, dict) and len(d.get("bins", [])) == len(first_bins)
            ]
            counts = [
                float(d["bins"][idx]["tti_count"])
                for d in occupancy_distributions
                if isinstance(d, dict) and len(d.get("bins", [])) == len(first_bins)
            ]
            if len(shares) != n or len(counts) != n:
                raise RuntimeError("各 replication 的 TTI RBG occupancy 桶数不一致")
            aggregated_bins.append({
                "occupied_rbg": int(first_bin["occupied_rbg"]),
                "tti_share": rg.summarize(shares, f"occupied_rbg_{idx}_tti_share").as_dict(),
                "tti_count": rg.summarize(counts, f"occupied_rbg_{idx}_tti_count").as_dict(),
            })
        cell["tti_occupied_rbg_distribution"] = {
            "scope": first_distribution.get("scope"),
            "x": "occupied_rbg_count",
            "y": "tti_share",
            "num_rbg": int(first_distribution.get("num_rbg", len(first_bins) - 1)),
            "n_rep": n,
            "bins": aggregated_bins,
        }
    # 用户级字段会继续增加。这里按数值类型自动汇总，避免每加一个 KPI 就漏改
    # 一份硬编码白名单；类别字段与布尔资格字段仍按各自语义单独处理。
    users: list[dict[str, Any]] = []
    for u in range(len(runs[0].users)):
        row: dict[str, Any] = {"ue": u}
        for k in ("geo_sinr_db", "iot_db"):
            row[k] = runs[0].users[u].get(k)
        classes = [str(r.users[u].get("traffic_class")) for r in runs]
        class_counts = {name: classes.count(name) for name in sorted(set(classes))}
        row["traffic_class"] = (
            classes[0] if len(class_counts) == 1
            else "varies_across_replications")
        row["traffic_class_counts"] = class_counts
        numeric_keys = sorted({
            key
            for run in runs
            for key, value in run.users[u].items()
            if key not in {"ue", "geo_sinr_db", "iot_db"}
            and isinstance(value, (int, float, np.integer, np.floating))
            and not isinstance(value, (bool, np.bool_))
        })
        for k in numeric_keys:
            vals = [
                float(x)
                if isinstance((x := run.users[u].get(k)),
                              (int, float, np.integer, np.floating))
                and not isinstance(x, (bool, np.bool_)) and np.isfinite(x)
                else float("nan")
                for run in runs
            ]
            if any(np.isfinite(vals)):
                row[k] = rg.summarize(vals, k).as_dict()
        users.append(row)

    # notes 去重但保序。**按原文去重是不够的**：像"首传 BLER 0.287 高于目标"
    # 这种 note 把逐次重复的数值嵌在文本里，8 次重复就是 8 条只差几个数字的
    # 告警，把真正不同的那几条淹掉。所以去重键是**抹掉数字后的模板**，
    # 保留第一条原文并标注命中了几次——数字不同这件事本身不是新信息。
    import re  # noqa: PLC0415

    _seen: dict[str, int] = {}
    _order: list[tuple[str, str]] = []
    for r in runs:
        for s in r.notes:
            k = re.sub(r"[0-9]+(?:\.[0-9]+)?", "#", s)
            if k not in _seen:
                _seen[k] = 0
                _order.append((k, s))
            _seen[k] += 1
    notes = [(txt if _seen[k] <= 1 else
              f"{txt}（{_seen[k]}/{n} 次重复都触发；上面的数值取自第 1 次）")
             for k, txt in _order]
    warn = rg.min_replications_note(n)
    if warn:
        notes.insert(0, warn)
    # 相对区间最宽的那个 KPI 值得单独点名——它决定了这组数字能说到多细。
    # **只在头条 KPI 里挑**：backlog_bytes 这类均值贴近 0 的量相对半宽动辄
    # 几百个百分点（实测 140%），点名它只会把注意力引到一个没人要下结论的字段上。
    _HEADLINE = ("cell_experienced_mbps", "cell_head_inclusive_experienced_mbps",
                 "ue_experienced_p5_mbps", "first_packet_delay_ms_p95",
                 "serving_cell_prb_utilization", "mu_paired_prb_share_of_used",
                 "ue_experienced_median_mbps", "cell_served_mbps",
                 "avg_mcs", "avg_rank", "bler_first_tx")
    _worst = max(
        ((k, v) for k, v in cell.items()
         if k in _HEADLINE and v.get("rel_half_width") is not None and v.get("mean")),
        key=lambda kv: kv[1]["rel_half_width"], default=None)
    if _worst and _worst[1]["rel_half_width"] > 0.05:
        notes.append(
            f"**头条 KPI 里 {_worst[0]} 的 95% 置信区间最宽，半宽是均值的 "
            f"{_worst[1]['rel_half_width']:.1%}**（n_rep={n}）——"
            f"比这更小的差异，这次实验分辨不出来。要下更细的结论就加 num_replications，"
            f"区间按 1/√n 收窄（注意还带 t 修正，收得比 1/√n 更快一些）。")

    cfg = dict(runs[0].config)
    cfg["rng"] = {
        **runs[0].config["rng"],
        "replication": (
            f"{int(replication_start)}..{int(replication_start) + n - 1}"),
        "num_replications": n,
    }
    cfg["execution"] = {"replication_parallelism": parallel}
    if parallel.get("fallback_reason"):
        notes.insert(
            0, "**重复实验并行自动降级为串行**：" + str(parallel["fallback_reason"]))
    return ReplicationResult(
        runs=runs, books=books, cell=cell, users=users, config=cfg,
        elapsed_s=time.perf_counter() - t0, build_elapsed_s=float(build_elapsed_s),
        parallel=parallel,
        notes=notes,
    )


TrafficCalibrationAxis = Literal["interarrival", "packet_size", "balanced"]


@dataclass
class TrafficCalibrationResult:
    """目标 PRB 利用率的话务校准结果；最终 KPI 仍来自一次正式仿真。"""

    result: ReplicationResult
    initial_traffic: TrafficConfig
    calibrated_traffic: TrafficConfig
    target_prb_utilization: float
    tolerance: float
    axis: TrafficCalibrationAxis
    status: str
    history: list[dict[str, Any]]
    formal_history: list[dict[str, Any]]
    probe_replication_ids: list[int]
    formal_replication_ids: list[int]

    def as_dict(self) -> dict[str, Any]:
        stat = self.result.cell.get("serving_cell_prb_utilization", {})
        achieved = stat.get("mean") if isinstance(stat, dict) else None
        return {
            "status": self.status,
            "target_prb_utilization": self.target_prb_utilization,
            "tolerance_absolute": self.tolerance,
            "achieved_prb_utilization": achieved,
            "achieved_ci95": stat.get("ci95") if isinstance(stat, dict) else None,
            "absolute_error": (
                abs(float(achieved) - self.target_prb_utilization)
                if isinstance(achieved, (int, float)) else None),
            "axis": self.axis,
            "initial_traffic": self.initial_traffic.as_dict(),
            "calibrated_traffic": self.calibrated_traffic.as_dict(),
            "history": self.history,
            "formal_history": self.formal_history,
            "num_probe_iterations": len(self.history),
            "num_formal_runs": len(self.formal_history),
            "probe_replication_ids": list(self.probe_replication_ids),
            "formal_replication_ids": list(self.formal_replication_ids),
            "common_random_numbers": (
                "probe factors reuse one fixed RngRun set; formal feedback "
                "reuses a second fixed, probe-disjoint RngRun set under the "
                "same master_seed"),
            "scope": (
                "design-input calibration, not an algorithm A/B claim; "
                "serving-cell PRB utilization remains a measured result"),
        }


def calibrate_traffic_to_prb(
    tables: list[UeLinkTable],
    *,
    target_prb_utilization: float,
    axis: TrafficCalibrationAxis = "interarrival",
    tolerance: float = 0.02,
    max_iterations: int = 6,
    probe_replications: int = 2,
    formal_refinements: int = 2,
    num_replications: int = 8,
    master_seed: int = 0,
    sys_cfg: SystemConfig,
    traffic: TrafficConfig,
    sched: SchedulerConfig | None = None,
    kpi: KpiConfig | None = None,
    mu_se_ratio: float = 1.0,
    build_elapsed_s: float = 0.0,
    replication_workers: int | str = 1,
) -> TrafficCalibrationResult:
    """用话务双标量把实测 PRB 利用率校准到目标附近。

    默认只调包间隔，保留包长 CDF 的业务含义；``packet_size`` 只调包长，
    ``balanced`` 在 log 域把负载倍率均分给两轴。探测轮之间固定随机样本 ID；
    正式反馈使用同一 master seed 下与 probe 不重叠的 RngRun 区间，并据正式均值
    判定是否达标。
    """
    target = float(target_prb_utilization)
    tol = float(tolerance)
    if not np.isfinite(target) or not 0.0 < target < 1.0:
        raise ValueError("target_prb_utilization 必须是 (0,1) 内的比例")
    if axis not in ("interarrival", "packet_size", "balanced"):
        raise ValueError("load_calibration_axis 只支持 interarrival/packet_size/balanced")
    if not np.isfinite(tol) or not 0.0 < tol < 1.0:
        raise ValueError("load_calibration_tolerance 必须是 (0,1) 内的绝对误差")
    for name, value in (("max_iterations", max_iterations),
                        ("probe_replications", probe_replications),
                        ("num_replications", num_replications)):
        if (isinstance(value, (bool, np.bool_))
                or not isinstance(value, (int, np.integer)) or int(value) < 1):
            raise ValueError(f"{name} 必须是正整数")
    if (isinstance(formal_refinements, (bool, np.bool_))
            or not isinstance(formal_refinements, (int, np.integer))
            or int(formal_refinements) < 0):
        raise ValueError("formal_refinements 必须是非负整数")
    if str(sys_cfg.evaluation_mode) != "experience":
        raise ValueError("目标 PRB 话务校准只支持 evaluation_mode='experience'")
    if str(traffic.model) in ("full_buffer", "cbr", "bimodal"):
        raise ValueError(
            f"话务模型 {traffic.model!r} 不支持双标量 PRB 校准；"
            "请用 ftp3/mixed/cdf")

    def _scaled(factor: float) -> TrafficConfig:
        factor = float(np.clip(factor, 1e-4, 1e4))
        if axis == "packet_size":
            return replace(
                traffic,
                packet_size_scale=float(traffic.packet_size_scale) * factor)
        if axis == "balanced":
            root = float(np.sqrt(factor))
            return replace(
                traffic,
                packet_size_scale=float(traffic.packet_size_scale) * root,
                interarrival_scale=float(traffic.interarrival_scale) / root)
        return replace(
            traffic,
            interarrival_scale=float(traffic.interarrival_scale) / factor)

    history: list[dict[str, Any]] = []
    probe_results: list[tuple[float, TrafficConfig, ReplicationResult, float]] = []
    probe_replication_ids = list(range(int(probe_replications)))
    formal_replication_ids = list(range(
        int(probe_replications),
        int(probe_replications) + int(num_replications),
    ))
    factor = 1.0
    for iteration in range(int(max_iterations)):
        cfg = _scaled(factor)
        probe = simulate_replications(
            tables, num_replications=int(probe_replications),
            master_seed=int(master_seed), replication_start=0,
            sys_cfg=sys_cfg, traffic=cfg,
            sched=sched, kpi=kpi, mu_se_ratio=mu_se_ratio,
            replication_workers=replication_workers)
        stat = probe.cell.get("serving_cell_prb_utilization", {})
        util = float(stat.get("mean", 0.0))
        probe_results.append((factor, cfg, probe, util))
        history.append({
            "iteration": iteration + 1,
            "offered_load_factor_vs_input": factor,
            "packet_size_scale": float(cfg.packet_size_scale),
            "interarrival_scale": float(cfg.interarrival_scale),
            "measured_prb_utilization": util,
            "ci95": stat.get("ci95"),
            "absolute_error": abs(util - target),
            "n_rep": int(probe_replications),
        })
        if abs(util - target) <= tol:
            break

        below = [(f, u) for f, _, _, u in probe_results if u < target]
        above = [(f, u) for f, _, _, u in probe_results if u >= target]
        next_factor: float
        if below and above:
            lower = max(below, key=lambda item: item[0])[0]
            upper = min(above, key=lambda item: item[0])[0]
            if lower < upper:
                next_factor = float(np.sqrt(lower * upper))
            else:
                next_factor = factor * float(np.clip(
                    target / max(util, 1e-6), 0.5, 2.0))
        else:
            next_factor = factor * float(np.clip(
                target / max(util, 1e-6), 0.25, 4.0))
        next_factor = float(np.clip(next_factor, 1e-4, 1e4))
        if any(np.isclose(next_factor, f, rtol=1e-9, atol=1e-12)
               for f, _, _, _ in probe_results):
            break
        factor = next_factor

    best_factor, _, _, _ = min(
        probe_results, key=lambda item: abs(item[3] - target))
    formal_runs: list[tuple[float, TrafficConfig, ReplicationResult, float]] = []
    formal_history: list[dict[str, Any]] = []
    formal_factor = float(best_factor)
    for refinement in range(int(formal_refinements) + 1):
        formal_cfg = _scaled(formal_factor)
        formal_result = simulate_replications(
            tables, num_replications=int(num_replications),
            master_seed=int(master_seed),
            replication_start=int(probe_replications),
            sys_cfg=sys_cfg, traffic=formal_cfg,
            sched=sched, kpi=kpi, mu_se_ratio=mu_se_ratio,
            build_elapsed_s=float(build_elapsed_s),
            replication_workers=replication_workers)
        formal_stat = formal_result.cell.get(
            "serving_cell_prb_utilization", {})
        formal_util = float(formal_stat.get("mean", 0.0))
        formal_runs.append(
            (formal_factor, formal_cfg, formal_result, formal_util))
        formal_history.append({
            "formal_run": refinement + 1,
            "offered_load_factor_vs_input": formal_factor,
            "packet_size_scale": float(formal_cfg.packet_size_scale),
            "interarrival_scale": float(formal_cfg.interarrival_scale),
            "measured_prb_utilization": formal_util,
            "ci95": formal_stat.get("ci95"),
            "absolute_error": abs(formal_util - target),
            "n_rep": int(num_replications),
        })
        if abs(formal_util - target) <= tol:
            break
        next_factor = formal_factor * float(np.clip(
            target / max(formal_util, 1e-6), 0.25, 4.0))
        next_factor = float(np.clip(next_factor, 1e-4, 1e4))
        if any(np.isclose(next_factor, row[0], rtol=1e-9, atol=1e-12)
               for row in formal_runs):
            break
        formal_factor = next_factor

    _, best_cfg, final, final_util = min(
        formal_runs, key=lambda item: abs(item[3] - target))
    status = ("target_met" if abs(final_util - target) <= tol
              else "formal_result_outside_tolerance")
    return TrafficCalibrationResult(
        result=final, initial_traffic=traffic, calibrated_traffic=best_cfg,
        target_prb_utilization=target, tolerance=tol, axis=axis,
        status=status, history=history, formal_history=formal_history,
        probe_replication_ids=probe_replication_ids,
        formal_replication_ids=formal_replication_ids)


_BLER_CACHE: dict[tuple[str, int, int, str], float] = {}
_BLER_CACHE_STEP_DB = 0.05


def _bler_lookup(mcs: int, sinr_db: float, tx_mode: str = "newtx") -> float:
    """查表 BLER，按源曲线 0.05 dB 网格量化后缓存。

    预置曲线的原始横轴步长就是 0.05 dB。旧实现按 0.5 dB 缓存，在瀑布区会把
    工作点最多平移 0.25 dB，足以显著改变 ACK/NACK；缓存不能以牺牲源数据一个
    数量级的分辨率为代价。

    **key 里带曲线数据的 SHA-256。** 当前只有一套预置曲线，所以不带也是对的；
    但 ``MCS_TABLE_SOURCES`` 已经为第二套 profile 留了位置，届时一个进程级
    全局字典会静默返回上一套的值——和"CDL 表被异常吞掉后继续用错表"是同一类事故。
    """
    # **nan 要在这里兜住。** int(round(nan*2)) 直接 ValueError，
    # 而 nan SINR 是能真到这儿的（被拒样本、全零信道、几何 SINR 缺失）。
    # 一个用户的一个快照能把整条系统级仿真挂掉，报的错还看不出是谁。
    if sinr_db != sinr_db:                      # nan
        return 1.0                              # 发不出去
    from . import bler_curves as bc  # noqa: PLC0415

    clipped = min(max(float(sinr_db), -60.0), 60.0)
    key = (bc.data.DATA_SHA256, int(mcs),
           int(round(clipped / _BLER_CACHE_STEP_DB)), tx_mode)
    v = _BLER_CACHE.get(key)
    if v is None:
        v = float(np.atleast_1d(
            bc.get_curve(int(mcs), tx_mode).evaluate(
                key[2] * _BLER_CACHE_STEP_DB))[0])
        _BLER_CACHE[key] = float(min(max(v, 0.0), 1.0))
    return _BLER_CACHE[key]

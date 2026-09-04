"""下行 AMC 的两条跨 TTI 策略：Rank 选择与 HARQ 反馈时序。

这两件事都不是"某一个 TTI 内怎么算 SINR"，而是"状态在 TTI 之间怎么演进"，
所以它们既不属于链路表（那是逐快照的物理量），也不属于 TTI 主循环。
放在同一个模块里，:mod:`system` 的建表相与 :mod:`experience` 的主循环共用
同一份实现，不会各写一套然后悄悄漂开。

## 一、Rank 不能每个信道快照改一次

早先两条主循环都直接读链路表的 ``best_rank[snap]``——那是**逐快照的瞬时
谱效最优 rank**，默认 5 ms 就可能换一次。现网不这么做：rank 变一次，
预编码、TBS、OLLA 收敛点全跟着变，高频抖动会让链路自适应根本收敛不了
（用户 2026-09-02 的原话："rank 不是 5 ms 改一次，这会导致链路收敛不稳定"）。

因此本模块提供三种模式：

``fixed``（默认，``fixed_rank=2``）
    仿真基线。rank 全程固定，没有任何切换，也就没有乒乓。

``adaptive``
    按 ``period_tti``（默认 1000 个 TTI）做一次决策，且要跨过一个**谱效比
    门限**才允许切换——门限本身就是防乒乓的迟滞。抬升之后还要进入一个
    回退观察窗，实测不好就退回去。

``link_table``
    历史行为：逐快照跟随链路表的 ``best_rank``。只作反向对照用，
    它正是上面那段"链路收敛不稳定"描述的那个模式。

## 二、ACK/NACK 要等上行时隙

TB 在下行时隙发出，UE 只能在上行时隙把 ACK/NACK 报回来。所以 OLLA 更新与
该 TB 的重传资格都不可能在同一个 TTI 生效。本模块把"从发送到反馈生效"的
偏移按 TDD 图案算成一张表，两条主循环共用。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

__all__ = [
    "RANK_MODES",
    "CqiReportConfig",
    "CqiReporter",
    "attach_runtime_cqi",
    "FirstTxFeedback",
    "RankConfig",
    "RankController",
    "feedback_effective_offsets",
]

_EPS = 1e-12


# ---------------------------------------------------------------------------
# 一、HARQ 反馈时序
# ---------------------------------------------------------------------------
def feedback_effective_offsets(pattern: str) -> tuple[int, ...]:
    """每个 slot 相位：从下行发送到 ACK/NACK **生效**之间的 TTI 偏移。

    合同（用户 2026-09-02 确认）：TB 在 ``D``/``S`` 时隙发出，UE 在其后的
    **第一个 ``U`` 时隙**上报 ACK/NACK；OLLA 更新与该 TB 的重传资格从这个
    ``U`` 之后的**第一个 ``D``/``S`` 时隙**起生效。

    **重传还有第二个、独立的约束：时隙类型要一致。** D 与 S 的可用 RE 不同，
    同一份 MCS/RBG/rank 在两种时隙上算出来的 TBS 也不同，所以冻结的 TB 只能
    回到同类型时隙上重发。两个约束取交集：在 ``S`` 上发出的 TB，本函数给的
    偏移只约束 OLLA，实际重传要等到**下一个 ``S``**（``DDDSU`` 下是一整个
    周期之后）。别把这张表读成"重传一定在这么多个 TTI 之后"。

    默认图案 ``DDDSU`` 在 30 kHz 下逐相位得到 ``(5, 4, 3, 2, 6)`` 个 TTI，
    前四项即 D/D/D/S 发送对应的 2.5 / 2.0 / 1.5 / 1.0 ms；第五项是 ``U``
    相位，下行不会在那里发送，它只是为了让返回值与图案等长。两个 ``DDDSU``
    周期正好是 8 个下行时隙配 2 个上行时隙，与现场的 8:2 配比一致。

    **图案里没有 ``U`` 时隙时退化成偏移 1。** 纯下行的合成图案（测试常用的
    ``"D"`` / ``"DS"``）没有上行可承载反馈，这时保持旧的零时延行为是唯一
    自洽的选择；调用方必须把这件事写进 ``notes``，不能让它看起来像已经
    建模了反馈时延。

    这里**不建模** k1/k2 的具体取值或 PUCCH 资源：偏移完全由图案决定，
    等价于"第一个可用上行机会就能把反馈带回来"。也就是说，当前取的是
    **最小 K1**，与 38.213 §5.3 Table 9.2-7 的查表值有差距——真实 K1 只会
    更大，所以本模型给出的反馈比现场更快、OLLA 收敛也更快。
    HARQ **进程数**不由本函数决定，见 ``SystemConfig.harq_max_processes``。
    """
    text = str(pattern).upper()
    if not text or any(slot not in "DSU" for slot in text):
        raise ValueError(f"tdd_pattern 只允许 D/S/U 且不能为空，收到 {pattern!r}")
    if not any(slot in "DS" for slot in text):
        raise ValueError("下行反馈时序至少需要一个 D 或 S 时隙")
    n = len(text)
    if "U" not in text:
        return tuple(1 for _ in range(n))
    out: list[int] = []
    for start in range(n):
        step = 1
        while text[(start + step) % n] != "U":
            step += 1
        step += 1
        while text[(start + step) % n] not in ("D", "S"):
            step += 1
        out.append(int(step))
    return tuple(out)


@dataclass(frozen=True)
class FirstTxFeedback:
    """A causally hidden first-transmission result waiting for HARQ feedback.

    The decoder outcome is sampled when the TB is sent, but the gNB must not use
    that outcome until ``effective_tti``.  Both system loops keep one of these
    records for **ACK and NACK**, so the HARQ process that carried the TB stays
    occupied until its result becomes visible.  The ``capacity`` loop gives each UE
    ``harq_max_processes`` such slots (default 8), so a UE with a free process can
    send a new TB while an earlier one is still in flight; the ``experience`` loop
    still has exactly one slot per UE.

    ``olla_delta_mcs`` is frozen at transmission time because the warm-up speed
    multiplier and SU/MU mode belong to that transmission.  Applying the event
    later changes OLLA and the rank monitor together, at the same causal boundary.
    """

    ue: int
    ack: bool
    mcs: int
    rank: int
    realized_se: float
    tx_tti: int
    effective_tti: int
    use_mu_olla: bool
    olla_delta_mcs: float = 0.0

    def __post_init__(self) -> None:
        if int(self.ue) < 0:
            raise ValueError("feedback ue 必须是非负整数")
        if int(self.mcs) < 0 or int(self.rank) < 1:
            raise ValueError("feedback MCS/rank 非法")
        if int(self.tx_tti) < 0 or int(self.effective_tti) <= int(self.tx_tti):
            raise ValueError("feedback effective_tti 必须晚于 tx_tti")
        if not math.isfinite(float(self.realized_se)):
            raise ValueError("feedback realized_se 必须有限")
        if not math.isfinite(float(self.olla_delta_mcs)):
            raise ValueError("feedback OLLA 增量必须有限")

    def due(self, tti: int) -> bool:
        """Whether this result is causally visible to the gNB at ``tti``."""
        return int(tti) >= int(self.effective_tti)

    def apply(
        self,
        *,
        rank_controller: RankController,
        su_olla: np.ndarray,
        mu_olla: np.ndarray,
        olla_min: float,
        olla_max: float,
    ) -> None:
        """Expose the ACK/NACK to rank and OLLA at the feedback boundary."""
        if int(self.ue) >= int(len(su_olla)) or int(self.ue) >= int(len(mu_olla)):
            raise IndexError(f"feedback UE {self.ue} 超出 OLLA 状态范围")
        rank_controller.record_feedback(
            int(self.ue), ack=bool(self.ack), mcs=int(self.mcs),
            realized_se=float(self.realized_se), tx_tti=int(self.tx_tti),
            tx_rank=int(self.rank), feedback_tti=int(self.effective_tti))
        state = mu_olla if self.use_mu_olla else su_olla
        state[int(self.ue)] = float(min(max(
            float(state[int(self.ue)]) + float(self.olla_delta_mcs),
            float(olla_min)), float(olla_max)))


# ---------------------------------------------------------------------------
# 二、Rank 策略
# ---------------------------------------------------------------------------
RANK_MODES: tuple[str, ...] = ("fixed", "adaptive", "link_table")
SE_SAMPLE_SCOPES: tuple[str, ...] = ("snapshot", "tti")
RANK_SWITCH_RULES: tuple[str, ...] = ("spec_asymmetric", "unified_ratio")


@dataclass
class RankConfig:
    """Rank 选择策略。

    ``mode='fixed'`` 是仿真基线，``fixed_rank`` 默认 2。除非明确要研究 rank
    自适应本身，都应该保持固定——否则 rank 会成为一个没人控制的自由度，
    把别的对比全部污染。

    ``mode='adaptive'`` 的常数**全部来自用户 2026-09-02 给的现场实现规格**
    （生产级 5G MAC 仿真蓝本），不再是工程猜测。逐项见各字段注释。

    ``mode='link_table'`` 是**历史行为**：直接跟随链路表的逐快照
    ``best_rank``，也就是每个信道快照都可能换 rank。它保留下来只有一个用途
    ——做"rank 稳定到底买到了什么"的反向对照。不要拿它出正式结论。
    """

    mode: str = "fixed"
    #: ``fixed`` 模式下的固定 rank；也是 ``adaptive`` 的初始 rank。
    fixed_rank: int = 2

    # --- 判决节拍 ---------------------------------------------------------
    #: 判决周期 ``rank_judge_period``。1000 个 TTI 在 30 kHz 下是 500 ms。
    period_tti: int = 1000
    #: 触发一次判决所需的最少谱效滤波样本数（现场 ``min_filter_samples``）。
    min_filter_samples: int = 3

    # --- 迟滞门限 ---------------------------------------------------------
    #: 升 rank 迟滞：``滤波谱效[最优] > 滤波谱效[当前] × gain_factor_raise``
    #: 才允许升。现场默认 1.1，即最优 rank 的谱效要高出 10%。
    gain_factor_raise: float = 1.1
    #: 降 rank 迟滞。**它的含义取决于 ``switch_rule``**，见该字段。
    gain_factor_reduce: float = 1.1
    #: 切换判据的写法。**两份现场来源对降 rank 这一侧写得不一样**，而且两种写法
    #: 会让 ``gain_factor_reduce`` 的含义**正好相反**，所以必须显式选、并随结果报。
    #:
    #: ``unified_ratio``（**默认**，用户 2026-09-03 的裁决）
    #:     两个方向共用一条式子 ``se[best] > G · se[cur]``，G 按方向取
    #:     ``G↑`` / ``G↓``。默认两个都是 1.1，也就是**按滤波谱效最大化选 rank，
    #:     但任何方向的切换都要求最优 rank 超过当前 rank 10% 才允许**——
    #:     这是用户给的判据原话。常数在两个方向上含义相同，不会读反。
    #:
    #: ``spec_asymmetric``（实现规格文档的写法，保留作对照）
    #:     升：``se[best] > G↑ · se[cur]`` 才升。
    #:     降：``se[cur] > G↓ · se[best]`` 时**保持不变**，否则降。
    #:     因为 best 由 argmax 选出、``se[best] >= se[cur]`` 恒成立，
    #:     所以 ``G↓ >= 1`` 时"保持不变"永远为假 → **降 rank 立即生效**。
    #:     ``G↓ = 0.9`` 时降的门槛是 ``se[best] >= se[cur]/0.9``，即 **11.1%**
    #:     余量——和默认那条同一个意图，只是数值差 1.1 个百分点。
    #:
    #: 也就是说同一个 ``G↓ = 1.1``：``unified_ratio`` 下降要 10% 余量，
    #: ``spec_asymmetric`` 下降是"立即"。**含义相反，必须连 switch_rule 一起读**
    #: ——这也是排查现场 rank 自适应行为异常时首先要钉死的一条。
    #: 两条路径的实际判据都写进 diagnostics。
    switch_rule: str = "unified_ratio"
    #: 逐 rank 估计谱效的一阶 IIR 系数 ``se_filter_beta``：
    #: ``filt <- beta*obs + (1-beta)*filt``。现场默认 0.1（强平滑）。
    se_filter_beta: float = 0.1
    #: 谱效样本的采集粒度。现场是**每 TTI** 累积一次；SuperRAN 的 AMC 坐标
    #: （CQI 门限 + BF Gain）是**逐信道快照**的分段常数，逐 TTI 采样等于把同一个
    #: 数重复上百次，会让 beta=0.1 的平滑在快照之间完全失效。默认因此按
    #: ``snapshot`` 采样——一次真正的新观测算一个样本。要复现现场节拍就设成
    #: ``tti``。这是**口径差异，不是等价实现**，必须随结果一起报。
    se_sample_scope: str = "snapshot"

    # --- 谱效估计的两个修正 -----------------------------------------------
    #: 最小 MCS 闸门：该 rank 预估 MCS 低于它就把谱效置 0（这一层基本传不动，
    #: 不配当有效层）。现场默认 9。
    min_mcs_threshold: int = 9
    #: 各 rank 的资源消耗系数（高 rank 的 DMRS 开销更大），谱效乘上它。
    #: 现场默认 ``[1.0, 0.97, 0.95, 0.93]``，索引即 rank-1。
    resource_cost_ratio: tuple[float, ...] = (1.0, 0.97, 0.95, 0.93)

    # --- 升 rank 之后的快速回退监测 ---------------------------------------
    fallback_enabled: bool = True
    #: 硬门限：监测期内新增 NACK 超过它**立即回退**，不等窗口结束。
    quick_fallback_nack_thld: int = 90
    #: 软门限：窗口结束时初传 BLER ≥ 它就回退。
    quick_fallback_ibler_thld: float = 0.3
    #: 软门限：窗口结束时 ``新 rank 实测谱效 / 原 rank 实测谱效`` 低于它就回退。
    #: 1.0 的含义是"没有变得更好就退回去"。
    quick_fallback_se_ratio_thld: float = 1.0
    #: 窗口结束时调度次数少于它，样本不足，直接退出监测（不判成功也不判失败）。
    quick_fallback_min_sched: int = 15
    #: 监测窗长上限，实际取 ``min(该值, 当前判决周期 - 10)``。
    quick_fallback_window_tti: int = 400
    #: 每回退一次，判决周期变成 ``period × 2^n``，n 最多到这里（默认 4 → ×16）。
    #: 升 rank 成功或正常降 rank 时 n 清零。**这是现场的防乒乓机制**：
    #: 估计谱效说该升、实测误码说该降，两个判据每周期互相推翻一次，
    #: 指数退避让重试越来越稀。
    max_backoff_times: int = 4

    # --- 主动向上探测（现场 RankProbeSwitch，默认关）---------------------
    probe_enabled: bool = False
    #: 逐 rank 的探测 MCS 门限：当前 rank 的平均 MCS 高于 ``[rank-1]`` 才探。
    #: 现场硬编码 ``{1:9, 2:22, 3:20, 4:18}``。
    probe_mcs_threshold_by_rank: tuple[int, ...] = (9, 22, 20, 18)

    def __post_init__(self) -> None:
        if self.mode not in RANK_MODES:
            raise ValueError(f"rank mode 只支持 {RANK_MODES}，收到 {self.mode!r}")
        if self.switch_rule not in RANK_SWITCH_RULES:
            raise ValueError(
                f"switch_rule 只支持 {RANK_SWITCH_RULES}，"
                f"收到 {self.switch_rule!r}")
        if self.se_sample_scope not in SE_SAMPLE_SCOPES:
            raise ValueError(
                f"se_sample_scope 只支持 {SE_SAMPLE_SCOPES}，"
                f"收到 {self.se_sample_scope!r}")
        for name in ("fixed_rank", "period_tti", "min_filter_samples",
                     "min_mcs_threshold", "quick_fallback_nack_thld",
                     "quick_fallback_min_sched", "quick_fallback_window_tti",
                     "max_backoff_times"):
            value = getattr(self, name)
            if (isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, np.integer))):
                raise ValueError(f"{name} 必须是整数")
        if int(self.fixed_rank) < 1:
            raise ValueError("fixed_rank 必须至少为 1")
        if int(self.period_tti) < 1:
            raise ValueError("period_tti 必须至少为 1")
        if int(self.min_filter_samples) < 1:
            raise ValueError("min_filter_samples 必须至少为 1")
        if int(self.min_mcs_threshold) < 0:
            raise ValueError("min_mcs_threshold 必须非负")
        if int(self.quick_fallback_nack_thld) < 1:
            raise ValueError("quick_fallback_nack_thld 必须至少为 1")
        if int(self.quick_fallback_min_sched) < 1:
            raise ValueError("quick_fallback_min_sched 必须至少为 1")
        if int(self.quick_fallback_window_tti) < 1:
            raise ValueError("quick_fallback_window_tti 必须至少为 1")
        if int(self.max_backoff_times) < 0:
            raise ValueError("max_backoff_times 必须非负")
        for name in ("gain_factor_raise", "gain_factor_reduce"):
            value = getattr(self, name)
            if not np.isfinite(value) or float(value) <= 0:
                raise ValueError(f"{name} 必须是有限正数")
        if float(self.gain_factor_raise) < 1.0:
            raise ValueError(
                "gain_factor_raise < 1 会让升 rank 比不升还容易，"
                "那不是迟滞而是反向偏置；现场值为 1.1")
        if (not np.isfinite(self.se_filter_beta)
                or not 0.0 < float(self.se_filter_beta) <= 1.0):
            raise ValueError("se_filter_beta 必须在 (0,1]")
        if not np.isfinite(self.quick_fallback_ibler_thld) or not (
                0.0 < float(self.quick_fallback_ibler_thld) < 1.0):
            raise ValueError("quick_fallback_ibler_thld 必须在 (0,1)")
        if (not np.isfinite(self.quick_fallback_se_ratio_thld)
                or float(self.quick_fallback_se_ratio_thld) <= 0):
            raise ValueError("quick_fallback_se_ratio_thld 必须是有限正数")
        ratios = tuple(float(x) for x in self.resource_cost_ratio)
        if not ratios or any(
                (not np.isfinite(x)) or x <= 0 for x in ratios):
            raise ValueError("resource_cost_ratio 必须是非空的有限正数序列")
        object.__setattr__(self, "resource_cost_ratio", ratios)
        probes = tuple(int(x) for x in self.probe_mcs_threshold_by_rank)
        if not probes or any(x < 0 for x in probes):
            raise ValueError("probe_mcs_threshold_by_rank 必须是非空非负整数序列")
        object.__setattr__(self, "probe_mcs_threshold_by_rank", probes)
        for name in ("probe_enabled", "fallback_enabled"):
            if not isinstance(getattr(self, name), (bool, np.bool_)):
                raise ValueError(f"{name} 必须是布尔值")

    def as_dict(self) -> dict[str, Any]:
        sources = {
            "fixed": "fixed product baseline",
            "adaptive": "periodic filtered-spectral-efficiency adaptation",
            "link_table": "per-snapshot best_rank from the link table (legacy)",
        }
        out: dict[str, Any] = {
            "mode": self.mode,
            "fixed_rank": int(self.fixed_rank),
            "rank_source": sources[self.mode],
        }
        if self.mode == "fixed":
            out["adaptation_note"] = (
                "rank 全程固定，链路表里的逐快照 best_rank 不参与发送决策")
            return out
        if self.mode == "link_table":
            out["adaptation_note"] = (
                "历史行为：rank 每个信道快照都可能变，只作反向对照，"
                "不要用它出正式结论")
            return out
        out.update({
            "period_tti": int(self.period_tti),
            "min_filter_samples": int(self.min_filter_samples),
            "gain_factor_raise": float(self.gain_factor_raise),
            "gain_factor_reduce": float(self.gain_factor_reduce),
            "switch_rule": str(self.switch_rule),
            "raise_criterion": "se[best] > gain_factor_raise * se[current]",
            "reduce_criterion": (
                "hold while se[current] > gain_factor_reduce * se[best]"
                if self.switch_rule == "spec_asymmetric"
                else "switch down only if se[best] > gain_factor_reduce"
                     " * se[current]"),
            "reduce_hysteresis_active": bool(
                float(self.gain_factor_reduce) < 1.0
                if self.switch_rule == "spec_asymmetric"
                else float(self.gain_factor_reduce) > 1.0),
            # 两种写法下"降 rank 需要多大余量"不是同一个换算，直接算出来。
            "reduce_margin_pct": (
                round((1.0 / float(self.gain_factor_reduce) - 1.0) * 100.0, 2)
                if self.switch_rule == "spec_asymmetric"
                else round((float(self.gain_factor_reduce) - 1.0) * 100.0, 2)),
            "raise_margin_pct": round(
                (float(self.gain_factor_raise) - 1.0) * 100.0, 2),
            "reduce_hysteresis_note": (
                "最优 rank 由 argmax 选出，se[best] >= se[current] 恒成立。"
                "spec_asymmetric 下 gain_factor_reduce >= 1 → 降 rank 立即生效"
                "（<1 才有降迟滞）；unified_ratio 下正好相反，>1 → 降 rank 也要"
                "那么多余量、UE 可能卡在高 rank 下不来。同一个常数在两种写法下"
                "含义相反，必须连 switch_rule 一起读"),
            "se_filter_beta": float(self.se_filter_beta),
            "se_sample_scope": str(self.se_sample_scope),
            "se_sample_scope_note": (
                "snapshot：一次新的 AMC 坐标算一个样本（默认，因为坐标在快照内"
                "是常数）；tti：复现现场的逐 TTI 累积节拍。两者不等价"),
            "min_mcs_threshold": int(self.min_mcs_threshold),
            "resource_cost_ratio": [float(x) for x in self.resource_cost_ratio],
            "fallback_enabled": bool(self.fallback_enabled),
            "quick_fallback_nack_thld": int(self.quick_fallback_nack_thld),
            "quick_fallback_ibler_thld": float(self.quick_fallback_ibler_thld),
            "quick_fallback_se_ratio_thld": float(
                self.quick_fallback_se_ratio_thld),
            "quick_fallback_min_sched": int(self.quick_fallback_min_sched),
            "quick_fallback_window_tti": int(self.quick_fallback_window_tti),
            "max_backoff_times": int(self.max_backoff_times),
            "probe_enabled": bool(self.probe_enabled),
            "probe_mcs_threshold_by_rank": [
                int(x) for x in self.probe_mcs_threshold_by_rank],
            "anti_ping_pong": (
                "升 rank 的 10% 谱效迟滞、周期判决节拍、升 rank 后的快速回退"
                "监测，以及回退后判决周期的指数退避"),
            "constants_status": (
                "全部取自用户 2026-09-02 提供的现场实现规格（生产级 5G MAC "
                "仿真蓝本）；se_sample_scope 是 SuperRAN 侧显式的口径选择"),
        })
        return out


class RankController:
    """逐 UE 的 rank 状态机。**完全确定性**，不消耗任何随机流。

    确定性是硬要求：``simulate_replications`` 的公共随机数把第 k 次重复绑到
    同一批话务与 ACK/NACK 抽签，rank 状态机若引入自己的随机源，两臂就再也
    对不齐了。

    ``fixed`` 模式下除了一次上限钳位之外什么都不做，开销为零。

    时序（与现场规格第 9 节一致）::

        每 TTI:  累积谱效滤波样本 → 快速回退监测（可立即回退）
        每 period TTI 且样本数够: 迟滞门限判决 → 升则进入监测、降则直接生效
        回退发生: 恢复 rank 与 OLLA 偏置，判决周期 ×2（最多 ×2^max_backoff）
    """

    def __init__(self, cfg: RankConfig, n_ue: int, *, tti_ms: float,
                 max_rank_available: int, snapshot_ms: float = 5.0) -> None:
        if int(n_ue) < 1:
            raise ValueError("n_ue 必须至少为 1")
        if not np.isfinite(tti_ms) or float(tti_ms) <= 0:
            raise ValueError("tti_ms 必须是有限正数")
        if int(max_rank_available) < 1:
            raise ValueError("max_rank_available 必须至少为 1")
        self.cfg = cfg
        self.n_ue = int(n_ue)
        self.tti_ms = float(tti_ms)
        if not np.isfinite(snapshot_ms) or float(snapshot_ms) <= 0:
            raise ValueError("snapshot_ms 必须是有限正数")
        self.snapshot_ms = float(snapshot_ms)
        self.max_rank = int(max_rank_available)
        start = min(int(cfg.fixed_rank), self.max_rank)
        self._rank = np.full(self.n_ue, start, dtype=int)
        # 逐 rank 的滤波估计谱效；NaN 表示还没有观测。
        self._se_filt = np.full((self.n_ue, self.max_rank), np.nan, dtype=float)
        self._filter_count = np.zeros(self.n_ue, dtype=int)
        self._last_snap = np.full(self.n_ue, -1, dtype=int)
        self._last_judge_tti = np.zeros(self.n_ue, dtype=int)
        self._backoff = np.zeros(self.n_ue, dtype=int)
        # 实际（ACK 加权）谱效的滤波值，用于快速回退的谱效比判据。
        self._realized_se = np.full(self.n_ue, np.nan, dtype=float)
        # 快速回退监测状态（现场 raise_flag / rsvd_* / fallback_*）
        self._monitor_until = np.full(self.n_ue, -1, dtype=int)
        # Only feedback for TBs actually transmitted after this instant belongs
        # to the raised-rank monitor.  A delayed result from the previous rank
        # must not make the new rank fall back.
        self._monitor_started_tti = np.full(self.n_ue, -1, dtype=int)
        self._rsvd_rank = np.zeros(self.n_ue, dtype=int)
        self._rsvd_olla = np.full(self.n_ue, np.nan, dtype=float)
        self._rsvd_se = np.full(self.n_ue, np.nan, dtype=float)
        self._mon_tx = np.zeros(self.n_ue, dtype=int)
        self._mon_nack = np.zeros(self.n_ue, dtype=int)
        self._mon_se_sum = np.zeros(self.n_ue, dtype=float)
        # 短时统计（探测用）
        self._short_tx = np.zeros(self.n_ue, dtype=int)
        self._short_mcs_sum = np.zeros(self.n_ue, dtype=float)
        self.events: list[dict[str, Any]] = []
        self._counts = {
            "switch_up": 0, "switch_down": 0, "probe_up": 0,
            "fallback_hard_nack": 0, "fallback_ibler": 0, "fallback_se_ratio": 0,
            "monitor_exit_low_sample": 0, "raise_confirmed": 0,
            "period_decisions": 0, "blocked_by_raise_hysteresis": 0,
            "blocked_by_reduce_hysteresis": 0, "blocked_by_filter_samples": 0,
            "blocked_by_monitor": 0,
        }

    # -- 查询 ----------------------------------------------------------------
    @property
    def adaptive(self) -> bool:
        """是否需要维护自适应状态（滤波、周期决策、回退监测）。"""
        return self.cfg.mode == "adaptive"

    def rank_of(self, ue: int) -> int:
        return int(self._rank[int(ue)])

    def rank_for(self, ue: int, link_table_rank: int) -> int:
        """本 TTI 实际该用的 rank。

        ``link_table`` 模式直接跟随链路表的逐快照 ``best_rank``（历史行为，
        只作反向对照）；其余模式用状态机自己的值。两种情况都钳到可用上限。
        """
        if self.cfg.mode == "link_table":
            return int(min(max(int(link_table_rank), 1), self.max_rank))
        return int(self._rank[int(ue)])

    def ranks(self) -> np.ndarray:
        return self._rank.copy()

    def judge_period_tti(self, ue: int) -> int:
        """该 UE 当前生效的判决周期（含指数退避）。"""
        return int(self.cfg.period_tti) * (2 ** int(self._backoff[int(ue)]))

    # -- 观测 ----------------------------------------------------------------
    def observe_link(self, ue: int, snap: int, se_by_rank: Any,
                     mcs_by_rank: Any = None) -> None:
        """更新某个 UE 的逐 rank 滤波谱效。

        ``se_by_rank[r-1]`` 是"如果这个 TTI 用 rank r 发，按当前 AMC 坐标
        （CQI 门限 + BF Gain）叠加 OLLA 后会选到的 MCS，其 rank×谱效是多少"。

        进滤波之前先做现场规格的两个修正：

        * **最小 MCS 闸门**：``mcs_by_rank[r-1] < min_mcs_threshold`` 时该
          rank 的谱效置 0——那一层基本传不动，不配当有效层。不给
          ``mcs_by_rank`` 就跳过这一步（手工构造的调用方）。
        * **资源消耗加权**：乘 ``resource_cost_ratio[r-1]``，体现高 rank 的
          DMRS 开销。序列比 ``max_rank`` 短时，尾部按最后一个值延用。

        采样粒度由 ``se_sample_scope`` 决定，见该字段的说明。
        """
        if not self.adaptive:
            return
        index = int(ue)
        if self.cfg.se_sample_scope == "snapshot":
            if int(snap) == int(self._last_snap[index]):
                return
        self._last_snap[index] = int(snap)
        beta = float(self.cfg.se_filter_beta)
        values = np.asarray(se_by_rank, dtype=float)
        limit = min(self.max_rank, int(values.size))
        if limit < 1:
            return
        mcs_values = (None if mcs_by_rank is None
                      else np.asarray(mcs_by_rank, dtype=float))
        ratios = self.cfg.resource_cost_ratio
        updated = False
        for rank_index in range(limit):
            observation = float(values[rank_index])
            if not math.isfinite(observation):
                continue
            if (mcs_values is not None and rank_index < int(mcs_values.size)
                    and float(mcs_values[rank_index])
                    < float(self.cfg.min_mcs_threshold)):
                observation = 0.0
            observation *= float(
                ratios[rank_index] if rank_index < len(ratios) else ratios[-1])
            current = self._se_filt[index, rank_index]
            self._se_filt[index, rank_index] = (
                observation if not math.isfinite(current)
                else current + beta * (observation - current))
            updated = True
        if updated:
            self._filter_count[index] += 1

    def record_feedback(self, ue: int, *, ack: bool, mcs: int,
                        realized_se: float, tx_tti: int | None = None,
                        tx_rank: int | None = None,
                        feedback_tti: int | None = None) -> None:
        """在 ACK/NACK **到达基站时**登记一次首传结果。

        ``realized_se`` 是这次首传实际兑现的谱效：ACK 时是 ``rank × MCS 谱效``，
        NACK 时是 0。快速回退的谱效比判据用的就是它的滤波值——只看 BLER 看不
        出"误码没超标但谱效反而降了"这一条。

        ``tx_tti`` / ``tx_rank`` identify the transmission that produced the
        delayed feedback.  A result sent before the current raise monitor, or
        under another rank, still updates long-term diagnostics but cannot vote
        in that new-rank fallback window.  ``feedback_tti`` is retained in the
        call contract so tests can prove the main loops do not expose outcomes
        at send time.
        """
        if not self.adaptive:
            return
        index = int(ue)
        beta = float(self.cfg.se_filter_beta)
        value = float(realized_se)
        current = self._realized_se[index]
        self._realized_se[index] = (
            value if not math.isfinite(current)
            else current + beta * (value - current))
        self._short_tx[index] += 1
        self._short_mcs_sum[index] += float(mcs)
        monitor_start = int(self._monitor_started_tti[index])
        belongs_to_monitor = (
            self._monitor_until[index] >= 0
            and (tx_tti is None or int(tx_tti) >= monitor_start)
            and (tx_rank is None or int(tx_rank) == int(self._rank[index]))
        )
        if belongs_to_monitor:
            self._mon_tx[index] += 1
            self._mon_nack[index] += int(not bool(ack))
            self._mon_se_sum[index] += value

    def record_first_tx(self, ue: int, *, ack: bool, mcs: int,
                        realized_se: float) -> None:
        """Compatibility wrapper for callers that already waited for feedback.

        New system loops must call :meth:`record_feedback` only when the event
        becomes causally visible.  This wrapper keeps small policy-only tests and
        downstream callers source-compatible; it must not be called at send time.
        """
        self.record_feedback(
            ue, ack=ack, mcs=mcs, realized_se=realized_se)

    # -- 周期决策 ------------------------------------------------------------
    def step(self, tti: int, *,
             olla_by_ue: Any = None) -> list[tuple[int, float]]:
        """每个 TTI 开始时调用。

        先跑快速回退监测（可能立即回退），再看是否到判决周期。

        ``olla_by_ue`` 给定时，升 rank 会把该 UE 当前的 OLLA 偏置存成回退点；
        回退时把它连同 rank 一起恢复——现场的 ``rsvd_olla`` 就是这个意思，
        因为新 rank 上的 OLLA 是在错误的工作点上收敛出来的，退回旧 rank 时
        必须一起退回，否则旧 rank 会带着一个别人的偏置继续跑。

        返回 ``[(ue, 恢复后的 OLLA 偏置), ...]``，调用方按它写回自己的 OLLA
        状态。没有回退发生时返回空列表。
        """
        if not self.adaptive:
            return []
        now = int(tti)
        restores: list[tuple[int, float]] = []
        if self.cfg.fallback_enabled:
            restores = self._check_quick_fallback(now)
        for ue in range(self.n_ue):
            if int(self._monitor_until[ue]) >= 0:
                continue
            if now - int(self._last_judge_tti[ue]) < self.judge_period_tti(ue):
                continue
            self._last_judge_tti[ue] = now
            self._counts["period_decisions"] += 1
            if int(self._filter_count[ue]) < int(self.cfg.min_filter_samples):
                self._counts["blocked_by_filter_samples"] += 1
                self._reset_short_term(ue)
                continue
            self._judge(now, ue, olla_by_ue)
            self._reset_short_term(ue)
        return restores

    def _check_quick_fallback(self, tti: int) -> list[tuple[int, float]]:
        """升 rank 之后的监测窗，每 TTI 跑一次。"""
        restores: list[tuple[int, float]] = []
        for ue in range(self.n_ue):
            if int(self._monitor_until[ue]) < 0:
                continue
            new_nack = int(self._mon_nack[ue])
            # 硬门限：NACK 增量超限，立即回退，不等窗口结束。
            if new_nack > int(self.cfg.quick_fallback_nack_thld):
                restores.append(self._do_fallback(tti, ue, "hard_nack"))
                continue
            if tti < int(self._monitor_until[ue]):
                continue
            sched = int(self._mon_tx[ue])
            if sched < int(self.cfg.quick_fallback_min_sched):
                # 调度次数不足，样本不够，既不判成功也不判失败。
                self._counts["monitor_exit_low_sample"] += 1
                self._backoff[ue] = 0
                self._clear_monitor(ue)
                continue
            ibler = float(new_nack) / max(sched, 1)
            realized = float(self._mon_se_sum[ue]) / max(sched, 1)
            previous = float(self._rsvd_se[ue])
            se_ratio = (realized / previous
                        if math.isfinite(previous) and abs(previous) > _EPS
                        else math.inf)
            if ibler >= float(self.cfg.quick_fallback_ibler_thld):
                restores.append(self._do_fallback(tti, ue, "ibler"))
            elif se_ratio < float(self.cfg.quick_fallback_se_ratio_thld):
                restores.append(self._do_fallback(tti, ue, "se_ratio"))
            else:
                self._counts["raise_confirmed"] += 1
                self._backoff[ue] = 0
                self._clear_monitor(ue)
        return restores

    def _do_fallback(self, tti: int, ue: int, reason: str) -> tuple[int, float]:
        old = int(self._rank[ue])
        new_rank = int(self._rsvd_rank[ue])
        self._log(tti, ue, old, new_rank, f"quick_fallback_{reason}")
        self._rank[ue] = new_rank
        self._counts[f"fallback_{reason}"] += 1
        self._backoff[ue] = min(int(self._backoff[ue]) + 1,
                                int(self.cfg.max_backoff_times))
        # 回退也算一次判决，重新起算周期（此时周期已经翻倍）。
        self._last_judge_tti[ue] = tti
        restored = float(self._rsvd_olla[ue])
        self._clear_monitor(ue)
        return (int(ue), restored if math.isfinite(restored) else 0.0)

    def _judge(self, tti: int, ue: int, olla_by_ue: Any) -> None:
        current = int(self._rank[ue])
        filtered = self._se_filt[ue]
        if not np.any(np.isfinite(filtered)):
            return
        best_index = int(np.nanargmax(filtered))
        best_rank = best_index + 1
        best_se = float(filtered[best_index])
        current_se = float(filtered[current - 1])
        if best_rank == current:
            if self.cfg.probe_enabled:
                self._maybe_probe(tti, ue, olla_by_ue)
            return
        if not math.isfinite(current_se):
            self._apply_change(tti, ue, best_rank, "current_rank_unobserved",
                               best_se, olla_by_ue)
            return
        if best_rank > current:
            # 升 rank：两种写法一致——最优谱效要比当前高出 G↑ 倍。
            if best_se > current_se * float(self.cfg.gain_factor_raise):
                self._apply_change(tti, ue, best_rank, "filtered_se_raise",
                                   best_se, olla_by_ue)
            else:
                self._counts["blocked_by_raise_hysteresis"] += 1
                if self.cfg.probe_enabled:
                    self._maybe_probe(tti, ue, olla_by_ue)
            return
        # 降 rank。
        if current_se <= _EPS:
            # **当前 rank 已经被最小 MCS 闸门判成"发不出去"（谱效 0）。**
            # 守着它没有任何上行空间，迟滞在这里没有意义——而且此时
            # ``se[best] > 1.1 · 0`` 在 best 也是 0 时同样为假，不加这条escape
            # 的话 UE 会卡在一个已知不可用的 rank 上。降到候选里最稳的那一档。
            self._apply_change(tti, ue, best_rank, "current_rank_gated_out",
                               best_se, olla_by_ue)
            return
        # **两份来源的写法不同，且让 G↓ 的含义正好相反**，见
        # RankConfig.switch_rule。
        if self.cfg.switch_rule == "spec_asymmetric":
            hold = current_se > best_se * float(self.cfg.gain_factor_reduce)
        else:
            hold = not (best_se > current_se
                        * float(self.cfg.gain_factor_reduce))
        if hold:
            self._counts["blocked_by_reduce_hysteresis"] += 1
            return
        self._apply_change(tti, ue, best_rank, "filtered_se_reduce",
                           best_se, olla_by_ue)

    def _maybe_probe(self, tti: int, ue: int, olla_by_ue: Any) -> None:
        """主动向上探一档：当前 rank 的平均 MCS 够高就试试 rank+1。"""
        tx = int(self._short_tx[ue])
        if tx < int(self.cfg.quick_fallback_min_sched):
            return
        current = int(self._rank[ue])
        target = min(current + 1, self.max_rank)
        if target == current:
            return
        thresholds = self.cfg.probe_mcs_threshold_by_rank
        threshold = float(thresholds[min(current - 1, len(thresholds) - 1)])
        mean_mcs = float(self._short_mcs_sum[ue]) / max(tx, 1)
        if mean_mcs >= threshold:
            self._apply_change(tti, ue, target, "probe_mcs_threshold",
                               float(self._se_filt[ue, target - 1]), olla_by_ue)
            self._counts["probe_up"] += 1

    def _apply_change(self, tti: int, ue: int, new_rank: int, reason: str,
                      target_se: float, olla_by_ue: Any) -> None:
        old = int(self._rank[ue])
        self._log(tti, ue, old, int(new_rank), reason, target_se=target_se)
        self._rank[ue] = int(new_rank)
        if new_rank > old:
            self._counts["switch_up"] += 1
            if self.cfg.fallback_enabled:
                window = min(int(self.cfg.quick_fallback_window_tti),
                             max(1, self.judge_period_tti(ue) - 10))
                self._monitor_until[ue] = tti + window
                self._monitor_started_tti[ue] = int(tti)
                self._rsvd_rank[ue] = old
                self._rsvd_se[ue] = float(self._realized_se[ue])
                self._rsvd_olla[ue] = (
                    float(np.asarray(olla_by_ue, dtype=float)[ue])
                    if olla_by_ue is not None else float("nan"))
                self._mon_tx[ue] = 0
                self._mon_nack[ue] = 0
                self._mon_se_sum[ue] = 0.0
        else:
            self._counts["switch_down"] += 1
            self._backoff[ue] = 0

    def _clear_monitor(self, ue: int) -> None:
        self._monitor_until[ue] = -1
        self._monitor_started_tti[ue] = -1
        self._mon_tx[ue] = 0
        self._mon_nack[ue] = 0
        self._mon_se_sum[ue] = 0.0

    def _reset_short_term(self, ue: int) -> None:
        self._short_tx[ue] = 0
        self._short_mcs_sum[ue] = 0.0

    def _log(self, tti: int, ue: int, old: int, new: int, reason: str,
             *, target_se: float | None = None) -> None:
        if len(self.events) >= 512:
            return
        row: dict[str, Any] = {
            "tti": int(tti), "ue": int(ue),
            "from_rank": int(old), "to_rank": int(new), "reason": reason,
        }
        if target_se is not None and math.isfinite(target_se):
            row["target_filtered_se"] = round(float(target_se), 4)
        self.events.append(row)

    # -- 结果 ----------------------------------------------------------------
    def diagnostics(self) -> dict[str, Any]:
        out: dict[str, Any] = dict(self.cfg.as_dict())
        out["max_rank_available"] = int(self.max_rank)
        out["final_rank_by_ue"] = [int(x) for x in self._rank]
        if self.adaptive:
            # **谱效滤波的记忆长度必须换算成时间报出来。** beta 是"每样本"的
            # 系数，而样本的时间间隔由 se_sample_scope 决定：按 TTI 采样时
            # 1/beta 个样本就是 1/beta 个 TTI，按快照采样时是 1/beta 个快照
            # ——同一个 beta 在两种粒度下差一个 snapshot/TTI 的倍数。只报 beta
            # 会让读者以为两者可比。
            _per_sample_ms = (self.tti_ms if self.cfg.se_sample_scope == "tti"
                              else self.snapshot_ms)
            out["se_filter_memory_ms"] = round(
                _per_sample_ms / float(self.cfg.se_filter_beta), 3)
            out["se_filter_memory_note"] = (
                f"1/beta = {1.0 / float(self.cfg.se_filter_beta):.1f} 个样本，"
                f"每样本 {_per_sample_ms:g} ms（scope="
                f"{self.cfg.se_sample_scope}）")
            out["se_filter_samples_by_ue"] = [
                int(x) for x in self._filter_count]
            out["backoff_times_by_ue"] = [int(x) for x in self._backoff]
            out["effective_judge_period_by_ue"] = [
                self.judge_period_tti(u) for u in range(self.n_ue)]
            out.update({f"count_{k}": int(v) for k, v in self._counts.items()})
            out["count_fallback"] = int(
                self._counts["fallback_hard_nack"]
                + self._counts["fallback_ibler"]
                + self._counts["fallback_se_ratio"])
            out["events"] = list(self.events)
            out["events_truncated"] = bool(len(self.events) >= 512)
        return out


# ---------------------------------------------------------------------------
# 三、CQI 上报：运行时按 SRS 周期事件触发
# ---------------------------------------------------------------------------
#: CQI 上报周期跟 **CSI 报告周期**（用户 2026-09-04 定），不跟上行 SRS 周期。
#: SRS 是上行探测、服务于互易性预编码；CQI 来自 CSI-RS + CSI 报告，周期由
#: ``CsiConfig.csi_report_period_ms`` 配（默认 20 ms）。``None`` 表示"从链路表
#: 带出来的 csi_report_period_ms 换算成 TTI"，30 kHz 下 20 ms = 40 TTI。
CQI_PERIOD_TTI_DEFAULT = None
#: 处理时延：UE 测到、上报、基站能用上之间的空档，单位 TTI。
CSI_DELAY_TTI_DEFAULT = 3


@lru_cache(maxsize=64)
def _cqi_quantiser(target_bler: float, mcs_table: int) -> tuple[Any, Any]:
    """(上报量化门限数组, 每个内部 CQI 行对应的发送门限数组)。

    两个都是 ``(target_bler, mcs_table)`` 的纯函数，取值集合很小。运行时上报
    每个周期、每个 UE、每个 rank 都要用，所以整表算一次缓存起来：量化用
    ``searchsorted``、反查门限用数组下标，都不再走 Python 循环。
    """
    from . import bler_curves as bc  # noqa: PLC0415
    from . import linkadapt as la  # noqa: PLC0415

    # 与 la.select_reported_cqi 逐值等价：codepoint = 有多少个门限 <= 观测值。
    edges = np.asarray(
        la._internal_cqi_thresholds(float(target_bler), int(mcs_table)),
        dtype=float)
    rows = np.array([
        float(bc.get_curve(
            int(la.internal_cqi_to_mcs(r, mcs_table=int(mcs_table))["mcs"]),
            "newtx").required_sinr_db(float(target_bler)))
        for r in range(len(la.INTERNAL_CQI_TO_MCS))], dtype=float)
    return edges, rows


@dataclass(frozen=True)
class CqiReportConfig:
    """UE 侧 CQI 上报的时间行为。

    这一层管的是 **MCS 决策输入的新鲜度**，不是预编码权的老化——后者是
    :mod:`csi_aging` 的事（``h_prec`` 相对 ``h_eval`` 有多陈旧）。两者是两个
    独立维度，**不要合并**：预编码用 ``h_prec``、MCS 用本类给出的
    ``sinr_tx_db``、误块抽签用 ``h_eval`` 上的真实 SINR，三条互不覆盖。

    ``enabled=False`` 退回建表阶段一次性算好的 ``sinr_tx_db``——也就是本类
    出现之前的行为，逐位一致，用于 A/B 对照。
    """

    enabled: bool = True
    #: 两次 CQI 上报之间隔多少个 TTI。``None`` = 跟链路表带出来的
    #: ``csi_report_period_ms``（默认 20 ms，30 kHz 下 = 40 TTI）——**这是默认**。
    #: 显式给整数只用于消融：1 = 每个 TTI 都有新 CQI（理想上界）。
    cqi_period_tti: int | None = CQI_PERIOD_TTI_DEFAULT
    #: 测量到基站能用上之间的处理时延（TTI）。UE 测的是
    #: ``tti - 上报周期 - csi_delay_tti`` 时刻的信道，不是当前信道。
    csi_delay_tti: int = CSI_DELAY_TTI_DEFAULT
    #: UE 端实现损失（dB），在 UE 测出 PMI-SINR 之后、量化成 CQI 之前扣掉。
    #: 这是 **UE 本振噪声 + 解调实现损失的等效惩罚**，不是信道的一部分；
    #: 1.5 dB 是工程默认，**未经现场设备数据标定**。
    ue_implementation_loss_db: float = 1.5

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, (bool, np.bool_)):
            raise ValueError("enabled 必须是布尔值")
        if self.cqi_period_tti is not None:
            if (isinstance(self.cqi_period_tti, (bool, np.bool_))
                    or not isinstance(self.cqi_period_tti, (int, np.integer))):
                raise ValueError("cqi_period_tti 必须是整数或 None")
            if int(self.cqi_period_tti) < 1:
                raise ValueError("cqi_period_tti 必须至少为 1")
        if (isinstance(self.csi_delay_tti, (bool, np.bool_))
                or not isinstance(self.csi_delay_tti, (int, np.integer))):
            raise ValueError("csi_delay_tti 必须是整数")
        if int(self.csi_delay_tti) < 0:
            raise ValueError("csi_delay_tti 必须非负")
        if (isinstance(self.ue_implementation_loss_db, (bool, np.bool_))
                or not math.isfinite(float(self.ue_implementation_loss_db))
                or float(self.ue_implementation_loss_db) < 0.0):
            raise ValueError("ue_implementation_loss_db 必须是有限非负数")

    def resolve_period_tti(self, csi_report_period_ms: float | None,
                           tti_ms: float) -> int:
        """把上报周期解析成 TTI 数。

        ``cqi_period_tti`` 显式给了就用它；否则按链路表带出来的
        ``csi_report_period_ms`` 换算——**CQI 跟 CSI 报告周期，不跟上行 SRS
        周期**（用户 2026-09-04 定）。两者都拿不到时退回 1（每 TTI 都上报），
        并且这种情况只可能出现在没有 CSI 配置的合成夹具上。
        """
        if self.cqi_period_tti is not None:
            return int(self.cqi_period_tti)
        if (csi_report_period_ms is None or not math.isfinite(
                float(csi_report_period_ms)) or float(csi_report_period_ms) <= 0):
            return 1
        return max(1, int(round(float(csi_report_period_ms) / float(tti_ms))))

    def as_dict(self, resolved_period_tti: int | None = None) -> dict[str, Any]:
        period = (int(resolved_period_tti) if resolved_period_tti is not None
                  else self.cqi_period_tti)
        return {"enabled": bool(self.enabled),
                "cqi_period_tti": (None if self.cqi_period_tti is None
                                   else int(self.cqi_period_tti)),
                "cqi_period_tti_resolved": (None if period is None
                                            else int(period)),
                "cqi_period_source": ("explicit_override"
                                      if self.cqi_period_tti is not None
                                      else "csi_report_period_ms"),
                "csi_delay_tti": int(self.csi_delay_tti),
                "measurement_lag_tti": (
                    None if period is None
                    else int(period) + int(self.csi_delay_tti)),
                "ue_implementation_loss_db": float(
                    self.ue_implementation_loss_db),
                "contract": (
                    "UE reports CQI once per CSI report period (from "
                    "CsiConfig.csi_report_period_ms unless cqi_period_tti "
                    "overrides it) from the channel seen period+csi_delay_tti "
                    "TTIs earlier, minus a UE implementation penalty; the IIR "
                    "filter runs online at each report; the gNB adds its "
                    "instantaneous BF gain at read time"
                    if self.enabled
                    else "CQI chain precomputed per snapshot in build_link_tables")}


def attach_runtime_cqi(tables: list[Any], config: CqiReportConfig | None, *,
                       snap_every: int,
                       tti_ms: float) -> tuple[list[Any], CqiReporter | None]:
    """为一次仿真准备运行时 CQI：返回（可写的链路表副本, reporter）。

    ``config`` 为 None / ``enabled=False`` / 链路表缺 ``pmi_sinr_db`` 时原样返回
    调用方的表和 ``None``，主循环行为与本机制出现之前逐位一致。

    启用时只复制 ``sinr_tx_db`` 与 ``sinr_tx_rbg_db`` 两个数组（都很小），
    其余字段共享。**不能原地改调用方的表**：同一份表会被多次 replication 复用，
    改了就会让第 2 次开始的重复实验带着第 1 次的 CQI 状态跑。
    """
    from dataclasses import replace as _replace  # noqa: PLC0415

    if config is None or not bool(config.enabled):
        return list(tables), None
    if any(getattr(t, "pmi_sinr_db", None) is None for t in tables):
        return list(tables), None
    private: list[Any] = []
    for table in tables:
        rows = getattr(table, "sinr_tx_db", None)
        rbg = getattr(table, "sinr_tx_rbg_db", None)
        private.append(_replace(
            table,
            sinr_tx_db=None if rows is None else np.array(rows, dtype=float),
            sinr_tx_rbg_db=None if rbg is None else np.array(rbg, dtype=float)))
    reporter = CqiReporter(
        private, config, snap_every=snap_every,
        cqi_filter_lambda=float(getattr(private[0], "cqi_filter_lambda", 1.0)),
        cqi_filter_domain=str(
            getattr(private[0], "cqi_filter_domain", "cqi_index")),
        period_tti=config.resolve_period_tti(
            getattr(private[0], "csi_report_period_ms", None), float(tti_ms)))
    reporter.cold_start()
    return private, reporter


class CqiReporter:
    """运行时的逐 UE CQI 状态机。

    调度器读到的是"**最近一次 UE 上报并滤波后**的值"，而不是当前时刻的真值。
    上报之间 CQI 保持不变，新鲜度由 ``srs_period_tti`` 决定。

    与建表阶段那份离线实现相比，物理链完全一样（PMI-SINR → 4-bit CQI 量化 →
    一阶 IIR → 反查目标 BLER 的 SINR 门限 → 加 BF Gain），差别有三处：

    1. **时间栅格**。离线版落在信道快照上（默认 5 ms），最细只能到一个快照；
       本类落在 TTI 上（30 kHz 下 0.5 ms），``srs_period_tti=4`` 就是 2 ms。
    2. **测量时刻**。离线版用报告所在快照本身；本类显式退
       ``srs_period_tti + srs_delay_tti`` 个 TTI，体现 SRS 往返。
    3. **UE 实现损失**。离线版没有这一项。

    **BF Gain 不进状态**。它是瞬时量：基站每次调度都能从自己当前的 CSI 算出来。
    所以本类只持有 CQI 反查出的 **SINR 门限**，读的时候再按**当前快照**
    加 BF Gain。把 BF Gain 一起冻进上报时刻，等于假设基站也不知道自己的波束
    这一刻打得准不准。
    """

    def __init__(self, tables: list[Any], config: CqiReportConfig, *,
                 snap_every: int, cqi_filter_lambda: float,
                 cqi_filter_domain: str, period_tti: int) -> None:
        from . import linkadapt as la  # noqa: PLC0415

        self.config = config
        self.period_tti = max(1, int(period_tti))
        self._tables = list(tables)
        self._snap_every = max(1, int(snap_every))
        self._lam = float(cqi_filter_lambda)
        self._domain = str(cqi_filter_domain)
        self._max_codepoint = len(la.INTERNAL_CQI_TO_MCS)
        n_ue = len(self._tables)
        self._n_snap = int(np.asarray(self._tables[0].sinr_db).shape[0])
        self._max_rank = int(np.asarray(self._tables[0].sinr_db).shape[1])
        # [ue][rank] 的连续滤波状态与最近一次有限观测
        self._filter: list[list[float | None]] = [
            [None] * self._max_rank for _ in range(n_ue)]
        self._last_obs_db: list[list[float]] = [
            [float("nan")] * self._max_rank for _ in range(n_ue)]
        # 持有的 CQI 门限（**不含 BF Gain**）
        self._threshold_db = np.full((n_ue, self._max_rank), float("nan"))
        self._reported_cqi = np.zeros((n_ue, self._max_rank), dtype=int)
        # 预取：主循环每次上报/回写都要碰它们，不要每次 getattr + asarray
        self._pmi: list[np.ndarray] = []
        for _ue, _table in enumerate(self._tables):
            _rows = getattr(_table, "pmi_sinr_db", None)
            if _rows is None:
                raise ValueError(
                    f"UE {_ue} 的链路表没有 pmi_sinr_db，运行时 CQI 上报无从测起；"
                    "请用当前版本的 build_link_tables 重新预计算，或把 "
                    "cqi_report 设为 CqiReportConfig(enabled=False)")
            self._pmi.append(np.asarray(_rows, dtype=float))
        self._bf_gain: list[np.ndarray | None] = [
            (None if getattr(t, "bf_gain_db", None) is None
             else np.nan_to_num(np.asarray(t.bf_gain_db, dtype=float),
                                nan=0.0, posinf=0.0, neginf=0.0))
            for t in self._tables]
        self._bf_gain_rbg: list[np.ndarray | None] = [
            (None if getattr(t, "bf_gain_rbg_db", None) is None
             else np.nan_to_num(np.asarray(t.bf_gain_rbg_db, dtype=float),
                                nan=0.0, posinf=0.0, neginf=0.0))
            for t in self._tables]
        self._next_tti = np.zeros(n_ue, dtype=np.int64)
        self._last_update_tti = np.full(n_ue, -1, dtype=np.int64)
        self._update_count = np.zeros(n_ue, dtype=np.int64)

    # -- 内部：一次上报 ----------------------------------------------------
    @property
    def measurement_lag_tti(self) -> int:
        """UE 测的信道比当前时刻早多少个 TTI：一个上报周期 + 处理时延。"""
        return int(self.period_tti) + int(self.config.csi_delay_tti)

    def _measure_snapshot(self, tti: int) -> int:
        meas_tti = max(0, int(tti) - self.measurement_lag_tti)
        return int((meas_tti // self._snap_every) % self._n_snap)

    def _quantise(self, value: float, edges: Any) -> int:
        """SINR → 上报 4-bit CQI codepoint，与 ``la.select_reported_cqi`` 等价。"""
        if not np.isfinite(value):
            return int(len(edges)) if value > 0 else 0
        return int(np.searchsorted(edges, float(value), side="right"))

    def _report(self, ue: int, tti: int) -> None:
        table = self._tables[ue]
        snap = self._measure_snapshot(tti)
        target = float(getattr(table, "target_bler", 0.1))
        mcs_table = int(getattr(table, "mcs_table", 3))
        edges, row_thr = _cqi_quantiser(target, mcs_table)
        loss = float(self.config.ue_implementation_loss_db)
        obs = self._pmi[ue][snap] - loss                 # [rank]
        for r in range(self._max_rank):
            obs_db = float(obs[r])
            if np.isfinite(obs_db):
                self._last_obs_db[ue][r] = obs_db
                raw = (obs_db if self._domain == "sinr_db"
                       else float(self._quantise(obs_db, edges)))
                state = self._filter[ue][r]
                self._filter[ue][r] = (
                    raw if state is None else state + self._lam * (raw - state))
            state = self._filter[ue][r]
            if state is None:
                reported = 0
            elif self._domain == "sinr_db":
                reported = self._quantise(state, edges)
            else:
                reported = int(min(max(int(np.floor(state + 1e-9)), 0),
                                   self._max_codepoint))
            self._reported_cqi[ue, r] = reported
            # codepoint 0 是协议语义的 out-of-range，没有可映射 MCS；
            # 沿用建表阶段的防御占位：退到表行 0，可用性由 outage/BLER 硬判。
            thr = float(row_thr[max(reported - 1, 0)])
            if not np.isfinite(thr):
                thr = (self._last_obs_db[ue][r]
                       if np.isfinite(self._last_obs_db[ue][r]) else -20.0)
            self._threshold_db[ue, r] = thr
        self._last_update_tti[ue] = int(tti)
        self._update_count[ue] += 1

    # -- 对外 --------------------------------------------------------------
    def cold_start(self) -> None:
        """主循环开跑前给每个 UE 上报一次。

        不做这一步，第一个 TTI 的 ``sinr_tx_db`` 是 NaN，MCS 无从判起。
        冷启动用的仍然是"退了测量时延之后"的快照，所以它不是偷看当前信道。
        """
        for ue in range(len(self._tables)):
            self._report(ue, 0)
            self._next_tti[ue] = int(self.period_tti)

    def step(self, tti: int) -> bool:
        """把本 TTI 到期的 UE 全部上报一遍；返回是否真的有人上报。

        返回值给调用方省掉一次无谓的回写：十万 TTI 的主循环里，
        ``srs_period_tti=4`` 时四次里有三次什么都没变。
        """
        period = int(self.period_tti)
        due = np.nonzero(self._next_tti <= int(tti))[0]
        if due.size == 0:
            return False
        for ue in due:
            self._report(int(ue), int(tti))
            # 直接加周期而不是"从当前 TTI 重新起算"，避免上行时隙把节拍拖偏。
            nxt = int(self._next_tti[ue]) + period
            while nxt <= int(tti):
                nxt += period
            self._next_tti[ue] = nxt
        return True

    def apply_to_tables(self, snap: int) -> None:
        """把当前持有的 CQI 写回链路表的 ``sinr_tx_db`` / ``sinr_tx_rbg_db``。

        **为什么用回写而不是让每个读点改成调函数**：两条主循环加起来有二十
        多处读这两个数组（频选打分、MU 准入、rank 观测、grant 复算……）。
        逐点改动等于二十多个可能漏掉的地方，而漏掉的那个会**静默**继续用
        建表阶段那份值。回写只有一个写点，读侧一行不用动。

        写的是**本次 simulate 私有的副本**（见 :func:`attach`），不碰调用方
        传进来的链路表——那份表会被多次 replication 复用。
        """
        for ue, table in enumerate(self._tables):
            thr = self._threshold_db[ue]
            rows = getattr(table, "sinr_tx_db", None)
            if rows is not None:
                limit = min(self._max_rank, int(rows.shape[1]))
                gain = self._bf_gain_rows(ue, snap)
                rows[int(snap), :limit] = thr[:limit] + gain[:limit]
            rbg = getattr(table, "sinr_tx_rbg_db", None)
            if rbg is not None:
                values = self.sinr_tx_rbg_db_rows(ue, snap)
                if values is not None:
                    limit = min(self._max_rank, int(rbg.shape[1]))
                    rbg[int(snap), :limit] = values[:limit]

    def sinr_tx_rbg_db_rows(self, ue: int, snap: int) -> np.ndarray | None:
        """``[rank, RBG]`` 的逐 RBG 决策坐标；没有逐 RBG BF Gain 时返回 None。"""
        rows = self._bf_gain_rbg[int(ue)]
        if rows is None:
            return None
        return self._threshold_db[int(ue)][:, None] + rows[int(snap)]

    def sinr_tx_db(self, ue: int, snap: int, rank: int) -> float:
        """该 UE 在当前快照下、rank 档的 AMC 决策 SINR（门限 + 瞬时 BF Gain）。"""
        thr = float(self._threshold_db[int(ue), int(rank) - 1])
        gain = self._bf_gain(int(ue), int(snap), int(rank) - 1)
        return thr + (gain if np.isfinite(gain) else 0.0)

    def sinr_tx_rbg_db(self, ue: int, snap: int, rank: int) -> np.ndarray | None:
        """逐 RBG 版本；链路表没有逐 RBG BF Gain 时返回 ``None``。"""
        rows = self.sinr_tx_rbg_db_rows(int(ue), int(snap))
        return None if rows is None else rows[int(rank) - 1]

    def _bf_gain_rows(self, ue: int, snap: int) -> np.ndarray:
        """``[rank]`` 的瞬时 BF Gain；缺数据或非有限值都按 0 dB 处理。"""
        rows = self._bf_gain[int(ue)]
        return np.zeros(self._max_rank) if rows is None else rows[int(snap)]

    def _bf_gain(self, ue: int, snap: int, rank_idx: int) -> float:
        return float(self._bf_gain_rows(ue, snap)[rank_idx])

    def age_tti(self, ue: int, tti: int) -> int:
        """当前 TTI 与该 UE 最近一次上报之间隔了多少个 TTI。"""
        last = int(self._last_update_tti[int(ue)])
        return int(tti) - last if last >= 0 else -1

    def reported_cqi(self, ue: int, rank: int) -> int:
        return int(self._reported_cqi[int(ue), int(rank) - 1])

    def diagnostics(self, tti: int) -> dict[str, Any]:
        ages = [self.age_tti(u, tti) for u in range(len(self._tables))]
        return {
            **self.config.as_dict(self.period_tti),
            "cqi_update_count": [int(x) for x in self._update_count],
            "cqi_age_tti": [int(x) for x in ages],
            "cqi_age_tti_max": int(max(ages)) if ages else 0,
            "reported_cqi_codepoint": [
                [int(v) for v in row] for row in self._reported_cqi],
        }

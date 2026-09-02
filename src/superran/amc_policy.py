"""下行 AMC 的两条跨 TTI 策略：Rank 选择与 HARQ 反馈时序。

这两件事都不是"某一个 TTI 内怎么算 SINR"，而是"状态在 TTI 之间怎么演进"，
所以它们既不属于链路表（那是逐快照的物理量），也不属于某一条评估路径。
放在同一个模块里，``capacity``/``legacy_v1`` 与 ``experience``/``experience_v2``
共用同一份实现，不会各写一套然后悄悄漂开。

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
from typing import Any

import numpy as np

__all__ = [
    "RANK_MODES",
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

    这里**不建模** k1/k2 的具体取值、PUCCH 资源或 HARQ 进程数：偏移完全由
    图案决定，等价于"第一个可用上行机会就能把反馈带回来"。
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


# ---------------------------------------------------------------------------
# 二、Rank 策略
# ---------------------------------------------------------------------------
RANK_MODES: tuple[str, ...] = ("fixed", "adaptive", "link_table")


@dataclass
class RankConfig:
    """Rank 选择策略。

    ``mode='fixed'`` 是仿真基线，``fixed_rank`` 默认 2。除非明确要研究 rank
    自适应本身，都应该保持固定——否则 rank 会成为一个没人控制的自由度，
    把别的对比全部污染。

    ``mode='adaptive'`` 的常数目前是**工程默认，尚未按现场标定**：
    ``switch_se_ratio`` / ``probe_*`` / ``fallback_*`` 都需要用户确认。
    ``probe_enabled`` 因此默认关闭——未标定的探测机制不应该在默认路径上跑。

    ``mode='link_table'`` 是**历史行为**：直接跟随链路表的逐快照
    ``best_rank``，也就是每个信道快照都可能换 rank。它保留下来只有一个用途
    ——做"rank 稳定到底买到了什么"的反向对照。不要拿它出正式结论。
    """

    mode: str = "fixed"
    #: ``fixed`` 模式下的固定 rank；也是 ``adaptive`` 的初始 rank。
    fixed_rank: int = 2
    #: 自适应决策周期。1000 个 TTI 在 30 kHz 下是 500 ms。
    period_tti: int = 1000
    #: 最优/当前 的滤波谱效比要超过它才允许切换；迟滞就在这里。
    switch_se_ratio: float = 1.05
    #: 逐 rank 估计谱效的一阶 IIR 系数，与 CQI 滤波同一形式。
    se_filter_lambda: float = 0.25
    #: Rank 探测。ρ 的现场定义尚未确认，这里用**短时首传 ACK 率**当代理，
    #: 因此默认关闭：没标定的机制不进默认路径。
    probe_enabled: bool = False
    probe_ack_ratio_threshold: float = 0.95
    probe_mcs_threshold: int = 22
    #: 抬升后的回退观察窗。窗内首传误码过高、或实际谱效反而下降就退回。
    fallback_enabled: bool = True
    fallback_window_ms: float = 200.0
    fallback_bler_threshold: float = 0.2
    #: 观察窗内至少要有这么多次首传才允许判决，否则样本太少不作数。
    fallback_min_first_tx: int = 20
    #: 回退之后把"刚退下来的那一档及以上"封住几个决策周期。语义是
    #: **从回退那一刻起、连续 N 个决策周期内都不许再升到该档**：N=1 只挡住
    #: 同一次 step 里紧接着的那次周期决策，N=2 再多挡一个完整周期。
    #: **没有这一条就必然乒乓**：估计谱效说该升、实测误码说该降，
    #: 两个判据每个周期互相推翻一次。0 表示不封，只用于消融。
    fallback_bar_periods: int = 2

    def __post_init__(self) -> None:
        if self.mode not in RANK_MODES:
            raise ValueError(f"rank mode 只支持 {RANK_MODES}，收到 {self.mode!r}")
        for name in ("fixed_rank", "period_tti", "probe_mcs_threshold",
                     "fallback_min_first_tx", "fallback_bar_periods"):
            value = getattr(self, name)
            if (isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, np.integer))):
                raise ValueError(f"{name} 必须是整数")
        if int(self.fixed_rank) < 1:
            raise ValueError("fixed_rank 必须至少为 1")
        if int(self.period_tti) < 1:
            raise ValueError("period_tti 必须至少为 1")
        if int(self.probe_mcs_threshold) < 0:
            raise ValueError("probe_mcs_threshold 必须非负")
        if int(self.fallback_min_first_tx) < 1:
            raise ValueError("fallback_min_first_tx 必须至少为 1")
        if int(self.fallback_bar_periods) < 0:
            raise ValueError("fallback_bar_periods 必须非负")
        if not np.isfinite(self.switch_se_ratio) or float(self.switch_se_ratio) < 1.0:
            raise ValueError("switch_se_ratio 必须是 >= 1 的有限数（1.0 表示无迟滞）")
        if (not np.isfinite(self.se_filter_lambda)
                or not 0.0 < float(self.se_filter_lambda) <= 1.0):
            raise ValueError("se_filter_lambda 必须在 (0,1]")
        if not np.isfinite(self.probe_ack_ratio_threshold) or not (
                0.0 <= float(self.probe_ack_ratio_threshold) <= 1.0):
            raise ValueError("probe_ack_ratio_threshold 必须在 [0,1]")
        if (not np.isfinite(self.fallback_window_ms)
                or float(self.fallback_window_ms) <= 0):
            raise ValueError("fallback_window_ms 必须是有限正数")
        if not np.isfinite(self.fallback_bler_threshold) or not (
                0.0 < float(self.fallback_bler_threshold) < 1.0):
            raise ValueError("fallback_bler_threshold 必须在 (0,1)")
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
            "switch_se_ratio": float(self.switch_se_ratio),
            "se_filter_lambda": float(self.se_filter_lambda),
            "probe_enabled": bool(self.probe_enabled),
            "probe_ack_ratio_threshold": float(self.probe_ack_ratio_threshold),
            "probe_mcs_threshold": int(self.probe_mcs_threshold),
            "probe_rho_definition": (
                "short-term first-transmission ACK ratio used as an explicit "
                "proxy; the field rho definition is not confirmed"),
            "fallback_enabled": bool(self.fallback_enabled),
            "fallback_window_ms": float(self.fallback_window_ms),
            "fallback_bler_threshold": float(self.fallback_bler_threshold),
            "fallback_min_first_tx": int(self.fallback_min_first_tx),
            "fallback_bar_periods": int(self.fallback_bar_periods),
            "anti_ping_pong": (
                "hysteresis on the filtered-SE ratio, a periodic decision "
                "cadence, and a post-fallback bar on the reverted rank"),
            "constants_status": (
                "engineering defaults pending field calibration; "
                "switch_se_ratio / probe_* / fallback_* are not standard values"),
        })
        return out


class RankController:
    """逐 UE 的 rank 状态机。**完全确定性**，不消耗任何随机流。

    确定性是硬要求：``simulate_replications`` 的公共随机数把第 k 次重复绑到
    同一批话务与 ACK/NACK 抽签，rank 状态机若引入自己的随机源，两臂就再也
    对不齐了。

    ``fixed`` 模式下除了一次上限钳位之外什么都不做，开销为零。
    """

    def __init__(self, cfg: RankConfig, n_ue: int, *, tti_ms: float,
                 max_rank_available: int) -> None:
        if int(n_ue) < 1:
            raise ValueError("n_ue 必须至少为 1")
        if not np.isfinite(tti_ms) or float(tti_ms) <= 0:
            raise ValueError("tti_ms 必须是有限正数")
        if int(max_rank_available) < 1:
            raise ValueError("max_rank_available 必须至少为 1")
        self.cfg = cfg
        self.n_ue = int(n_ue)
        self.tti_ms = float(tti_ms)
        self.max_rank = int(max_rank_available)
        start = min(int(cfg.fixed_rank), self.max_rank)
        self._rank = np.full(self.n_ue, start, dtype=int)
        self._fallback_window_tti = max(
            1, int(round(float(cfg.fallback_window_ms) / self.tti_ms)))
        # 逐 rank 的滤波估计谱效；NaN 表示还没有观测。
        self._se_filt = np.full((self.n_ue, self.max_rank), np.nan, dtype=float)
        self._last_snap = np.full(self.n_ue, -1, dtype=int)
        # 实际（ACK 加权）谱效的滤波值，用于回退判决第二条。
        self._realized_se = np.full(self.n_ue, np.nan, dtype=float)
        # 回退观察窗状态
        self._probation_until = np.full(self.n_ue, -1, dtype=int)
        self._probation_prev_rank = np.zeros(self.n_ue, dtype=int)
        self._probation_prev_se = np.full(self.n_ue, np.nan, dtype=float)
        self._probation_tx = np.zeros(self.n_ue, dtype=int)
        self._probation_nack = np.zeros(self.n_ue, dtype=int)
        self._probation_se_sum = np.zeros(self.n_ue, dtype=float)
        # 回退之后封住的最低 rank（含它自己）与解封时刻。
        self._barred_from = np.zeros(self.n_ue, dtype=int)
        self._bar_until = np.full(self.n_ue, -1, dtype=int)
        # 短时 ACK 率（探测用代理）
        self._short_tx = np.zeros(self.n_ue, dtype=int)
        self._short_ack = np.zeros(self.n_ue, dtype=int)
        self._short_mcs_sum = np.zeros(self.n_ue, dtype=float)
        self.events: list[dict[str, Any]] = []
        self._counts = {
            "switch_up": 0, "switch_down": 0, "probe_up": 0, "fallback": 0,
            "period_decisions": 0, "blocked_by_hysteresis": 0,
            "blocked_by_fallback_bar": 0,
        }

    # -- 查询 ----------------------------------------------------------------
    @property
    def adaptive(self) -> bool:
        """是否需要维护自适应状态（滤波、周期决策、回退窗）。"""
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

    # -- 观测 ----------------------------------------------------------------
    def observe_link(self, ue: int, snap: int, se_by_rank: Any) -> None:
        """更新某个 UE 的逐 rank 滤波谱效。

        ``se_by_rank[r-1]`` 是"如果这个 TTI 用 rank r 发，按当前 AMC 坐标
        （CQI 门限 + BF Gain）叠加 OLLA 后会选到的 MCS，其 rank×谱效是多少"。
        调用方每个 UE 每个**信道快照**调一次就够——AMC 坐标本来就只在快照
        边界变，逐 TTI 重算只是把同一个数再算十遍。
        """
        if not self.adaptive:
            return
        index = int(ue)
        if int(snap) == int(self._last_snap[index]):
            return
        self._last_snap[index] = int(snap)
        lam = float(self.cfg.se_filter_lambda)
        values = np.asarray(se_by_rank, dtype=float)
        limit = min(self.max_rank, int(values.size))
        for rank_index in range(limit):
            observation = float(values[rank_index])
            if not math.isfinite(observation):
                continue
            current = self._se_filt[index, rank_index]
            self._se_filt[index, rank_index] = (
                observation if not math.isfinite(current)
                else current + lam * (observation - current))

    def record_first_tx(self, ue: int, *, ack: bool, mcs: int,
                        realized_se: float) -> None:
        """登记一次**首传**结果。重传不进任何 rank 判据。

        ``realized_se`` 是这次首传实际兑现的谱效：ACK 时是 ``rank × MCS 谱效``，
        NACK 时是 0。回退判决的第二条"初传误码不高但实际频谱效率降低"用的
        就是它的滤波值——只看 BLER 看不出这一条。
        """
        if not self.adaptive:
            return
        index = int(ue)
        lam = float(self.cfg.se_filter_lambda)
        value = float(realized_se)
        current = self._realized_se[index]
        self._realized_se[index] = (
            value if not math.isfinite(current)
            else current + lam * (value - current))
        self._short_tx[index] += 1
        self._short_ack[index] += int(bool(ack))
        self._short_mcs_sum[index] += float(mcs)
        if self._probation_until[index] >= 0:
            self._probation_tx[index] += 1
            self._probation_nack[index] += int(not bool(ack))
            self._probation_se_sum[index] += value

    # -- 周期决策 ------------------------------------------------------------
    def step(self, tti: int) -> None:
        """在每个 TTI 开始时调用：先结回退观察窗，再看是否到自适应周期。"""
        if not self.adaptive:
            return
        now = int(tti)
        if self.cfg.fallback_enabled:
            self._resolve_probation(now)
        if now > 0 and now % int(self.cfg.period_tti) == 0:
            self._counts["period_decisions"] += 1
            self._periodic_decision(now)

    def _resolve_probation(self, tti: int) -> None:
        for ue in range(self.n_ue):
            deadline = int(self._probation_until[ue])
            if deadline < 0 or tti < deadline:
                continue
            tx = int(self._probation_tx[ue])
            reason = ""
            if tx >= int(self.cfg.fallback_min_first_tx):
                bler = float(self._probation_nack[ue]) / max(tx, 1)
                realized = float(self._probation_se_sum[ue]) / max(tx, 1)
                previous = float(self._probation_prev_se[ue])
                if bler > float(self.cfg.fallback_bler_threshold):
                    reason = "first_tx_bler_above_threshold"
                elif math.isfinite(previous) and realized < previous:
                    reason = "realized_se_regressed"
            if reason:
                new_rank = int(self._probation_prev_rank[ue])
                reverted_from = int(self._rank[ue])
                self._log(tti, ue, reverted_from, new_rank, reason)
                self._rank[ue] = new_rank
                self._counts["fallback"] += 1
                bar_periods = int(self.cfg.fallback_bar_periods)
                if bar_periods > 0:
                    # 刚被实测否掉的那一档先别再试，否则下一次周期决策
                    # 会立刻按估计谱效把它顶回去。
                    self._barred_from[ue] = reverted_from
                    self._bar_until[ue] = (
                        tti + bar_periods * int(self.cfg.period_tti))
            self._clear_probation(ue)

    def _periodic_decision(self, tti: int) -> None:
        for ue in range(self.n_ue):
            if int(self._probation_until[ue]) >= 0:
                # 观察窗还没结，这一轮不动它。
                self._reset_short_term(ue)
                continue
            current = int(self._rank[ue])
            filtered = self._se_filt[ue]
            if not np.any(np.isfinite(filtered)):
                self._reset_short_term(ue)
                continue
            barred = self._barred_rank(tti, ue)
            if barred:
                # 先看"没有封锁的话会选谁"——只有当封锁真的挡掉了赢家时
                # 才计数，否则这个诊断会把无关的周期也算进去。
                if int(np.nanargmax(filtered)) + 1 >= barred:
                    self._counts["blocked_by_fallback_bar"] += 1
                filtered = filtered.copy()
                filtered[barred - 1:] = np.nan
                if not np.any(np.isfinite(filtered)):
                    self._reset_short_term(ue)
                    continue
            best_index = int(np.nanargmax(filtered))
            best_rank = best_index + 1
            best_se = float(filtered[best_index])
            current_se = float(filtered[current - 1])
            switched = False
            if best_rank != current and math.isfinite(current_se):
                ratio = best_se / max(abs(current_se), _EPS)
                if ratio > float(self.cfg.switch_se_ratio):
                    self._apply_change(
                        tti, ue, best_rank, "filtered_se_ratio", best_se)
                    switched = True
                else:
                    self._counts["blocked_by_hysteresis"] += 1
            elif best_rank != current and not math.isfinite(current_se):
                self._apply_change(
                    tti, ue, best_rank, "current_rank_unobserved", best_se)
                switched = True
            if not switched and self.cfg.probe_enabled:
                self._maybe_probe(tti, ue)
            self._reset_short_term(ue)

    def _barred_rank(self, tti: int, ue: int) -> int:
        """回退封锁：返回被封住的最低 rank（0 表示没有封锁）。"""
        if int(self._bar_until[ue]) < 0:
            return 0
        if tti >= int(self._bar_until[ue]):
            self._bar_until[ue] = -1
            self._barred_from[ue] = 0
            return 0
        return int(self._barred_from[ue])

    def _maybe_probe(self, tti: int, ue: int) -> None:
        tx = int(self._short_tx[ue])
        if tx < int(self.cfg.fallback_min_first_tx):
            return
        barred = self._barred_rank(tti, ue)
        if barred and int(self._rank[ue]) + 1 >= barred:
            self._counts["blocked_by_fallback_bar"] += 1
            return
        ack_ratio = float(self._short_ack[ue]) / max(tx, 1)
        mean_mcs = float(self._short_mcs_sum[ue]) / max(tx, 1)
        if (ack_ratio > float(self.cfg.probe_ack_ratio_threshold)
                and mean_mcs > float(self.cfg.probe_mcs_threshold)):
            target = min(int(self._rank[ue]) + 1, self.max_rank)
            if target != int(self._rank[ue]):
                self._apply_change(
                    tti, ue, target, "probe_ack_ratio_and_mcs",
                    float(self._se_filt[ue, target - 1]))
                self._counts["probe_up"] += 1

    def _apply_change(self, tti: int, ue: int, new_rank: int, reason: str,
                      target_se: float) -> None:
        old = int(self._rank[ue])
        self._log(tti, ue, old, int(new_rank), reason, target_se=target_se)
        self._rank[ue] = int(new_rank)
        if new_rank > old:
            self._counts["switch_up"] += 1
            if self.cfg.fallback_enabled:
                self._probation_until[ue] = tti + self._fallback_window_tti
                self._probation_prev_rank[ue] = old
                self._probation_prev_se[ue] = float(self._realized_se[ue])
                self._probation_tx[ue] = 0
                self._probation_nack[ue] = 0
                self._probation_se_sum[ue] = 0.0
        else:
            self._counts["switch_down"] += 1

    def _clear_probation(self, ue: int) -> None:
        self._probation_until[ue] = -1
        self._probation_tx[ue] = 0
        self._probation_nack[ue] = 0
        self._probation_se_sum[ue] = 0.0

    def _reset_short_term(self, ue: int) -> None:
        self._short_tx[ue] = 0
        self._short_ack[ue] = 0
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
            out["fallback_window_tti"] = int(self._fallback_window_tti)
            out["barred_rank_by_ue"] = [int(x) for x in self._barred_from]
            out.update({f"count_{k}": int(v) for k, v in self._counts.items()})
            out["events"] = list(self.events)
            out["events_truncated"] = bool(len(self.events) >= 512)
        return out

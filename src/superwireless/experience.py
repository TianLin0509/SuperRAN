"""体验评估模式：DRB busy-period、按需 RBG 分配与 Rel-19 KPI。

这个模块只做系统级第二相（TTI 主循环），不碰信道矩阵。它和
``system.simulate`` 的 legacy 路径并存：legacy 用来复现历史结果，本文实现的
``experience_v2`` 用实际分配的 TBS 给 PF 记账，并允许一个 TTI 服务多个 UE。

物理边界明确写在结果里：当前使用宽带 MCS / SINR，不做频选调度；没有 HARQ
软合并，NACK 的 payload 留在队列，下一次按 new transmission 重试。
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import linkadapt as la
from . import rng as rg

_EPS = 1e-12
_SLOT_INDEX = {"D": 0, "S": 1}


@dataclass(frozen=True)
class TbsLookup:
    """``[D/S, MCS, rank, n_rbg]`` 的 TBS 字节表。

    38.214 的 TBS 量化使 TBS 只近似线性，不能用 ``bytes / bytes_per_rbg``
    反推 RBG 数。``required_rbg`` 用 ``searchsorted`` 找到第一个够用的 TBS；
    建表时同时验证每条序列严格递增，否则反查假设当场失败。

    当前项目固定 272 RB = 17×16，首尾 RBG 与中间 RBG 等长。若未来允许任意
    BWP 起点，必须把 RBG bitmap/各组实际 RB 数纳入索引，不能只按 ``n_rbg``。
    """

    values: np.ndarray                 # int64 [2, 28, 4, num_rbg]，单位 byte
    num_rbg: int
    rb_per_rbg: int
    s_slot_fraction: float

    @classmethod
    def build(cls, num_rbg: int, rb_per_rbg: int,
              s_slot_fraction: float = 0.7) -> TbsLookup:
        n_rbg = int(num_rbg)
        rb = int(rb_per_rbg)
        if n_rbg < 1 or rb < 1:
            raise ValueError("num_rbg 与 rb_per_rbg 必须为正整数")
        table = np.zeros((2, 28, 4, n_rbg), dtype=np.int64)
        re_one = rb * 12 * 12
        for slot, frac in (("D", 1.0), ("S", float(s_slot_fraction))):
            si = _SLOT_INDEX[slot]
            for mcs in range(28):
                obj = la.MCS_TABLES[3][mcs]
                for rank in range(1, 5):
                    for n in range(1, n_rbg + 1):
                        n_re = int(n * re_one * frac)
                        table[si, mcs, rank - 1, n - 1] = (
                            la.transport_block_size(
                                n_re, obj.rate, obj.q_m, layers=rank) // 8)
        diff = np.diff(table, axis=-1)
        if diff.size and np.any(diff <= 0):
            bad = np.argwhere(diff <= 0)[0]
            slot = "D" if int(bad[0]) == 0 else "S"
            raise ValueError(
                "TBS 表不再严格递增，searchsorted 反查不成立："
                f"slot={slot}, mcs={int(bad[1])}, rank={int(bad[2]) + 1}, "
                f"n_rbg={int(bad[3]) + 1}->{int(bad[3]) + 2}")
        return cls(table, n_rbg, rb, float(s_slot_fraction))

    def row(self, slot: str, mcs: int, rank: int) -> np.ndarray:
        try:
            si = _SLOT_INDEX[str(slot).upper()]
        except KeyError as exc:
            raise ValueError(f"只支持 D/S 下行时隙，收到 {slot!r}") from exc
        if not 0 <= int(mcs) <= 27:
            raise ValueError(f"MCS 必须在 0..27，收到 {mcs}")
        if not 1 <= int(rank) <= 4:
            raise ValueError(f"rank 必须在 1..4，收到 {rank}")
        return self.values[si, int(mcs), int(rank) - 1]

    def tbs_bytes(self, slot: str, mcs: int, rank: int, n_rbg: int) -> int:
        n = int(n_rbg)
        if not 1 <= n <= self.num_rbg:
            raise ValueError(f"n_rbg 必须在 1..{self.num_rbg}，收到 {n}")
        return int(self.row(slot, mcs, rank)[n - 1])

    def required_rbg(self, slot: str, mcs: int, rank: int,
                     payload_bytes: int) -> tuple[int, bool]:
        """返回 ``(最小 RBG 数, 本 TTI 能否全部装下)``。"""
        need = max(1, int(payload_bytes))
        row = self.row(slot, mcs, rank)
        idx = int(np.searchsorted(row, need, side="left"))
        if idx >= self.num_rbg:
            return self.num_rbg, bool(need <= int(row[-1]))
        return idx + 1, True

    def as_dict(self) -> dict[str, Any]:
        return {
            "shape": list(self.values.shape),
            "entries": int(self.values.size),
            "unit": "byte",
            "axes": ["slot_class(D/S)", "mcs(0..27)", "rank(1..4)",
                     f"n_rbg(1..{self.num_rbg})"],
            "strictly_increasing": True,
            "inverse": "numpy.searchsorted(side='left')",
            "rb_per_rbg": self.rb_per_rbg,
            "s_slot_fraction": self.s_slot_fraction,
            "mcs_profile": "company_20b_256qam_table_3",
            "n_re_model": ("12 data symbols/RB in D; S scales N_RE by "
                           f"{self.s_slot_fraction:g}; no exact DMRS/PTRS/CORESET pattern"),
            "standard_boundary": ("TBS quantization follows 38.214 5.1.3.2; "
                                  "MCS profile and N_RE inputs are engineering profiles"),
        }


@dataclass
class AckEvent:
    tti: int
    payload_bytes: int
    scheduled_bytes: int
    padding_bytes: int


@dataclass
class BusyPeriod:
    """一个 DRB buffer 从空到非空、再回到空的完整 busy period。"""

    start_tti: int
    traffic_class: str
    pdb_ms: float
    bytes_arrived: int = 0
    bytes_acked: int = 0
    first_tx_tti: int = -1
    last_ack_tti: int = -1
    tx_attempts: int = 0
    ack_events: list[AckEvent] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        return self.bytes_acked >= self.bytes_arrived > 0


@dataclass
class ArrivalItem:
    """DRB 队列中的一个外生到达对象，按 FIFO 记录调度与完成时刻。

    FTP/mixed 下一个对象对应一个文件；CBR 下对应本 TTI 到达的字节块。它不是
    凭空声称的 IP packet：结果字段统一叫 ``arrival_*``，只有 1500 B small 类
    可以作为小包代理。busy-period 吞吐仍由 :class:`BusyPeriod` 独立记录。
    """

    arrival_tti: int
    total_bytes: int
    remaining_bytes: int
    first_tx_tti: int = -1
    completion_tti: int = -1


@dataclass
class DrbQueue:
    ue: int
    traffic_class: Any
    queued_bytes: int = 0
    active: BusyPeriod | None = None
    done: list[BusyPeriod] = field(default_factory=list)
    items: deque[ArrivalItem] = field(default_factory=deque)
    done_items: list[ArrivalItem] = field(default_factory=list)

    def arrive(self, tti: int, n_bytes: int) -> None:
        n = int(n_bytes)
        if n <= 0:
            return
        if self.active is None:
            self.active = BusyPeriod(
                start_tti=int(tti), traffic_class=str(self.traffic_class.name),
                pdb_ms=float(self.traffic_class.pdb_ms))
        self.active.bytes_arrived += n
        self.queued_bytes += n
        self.items.append(ArrivalItem(int(tti), n, n))

    def transmit(self, tti: int, scheduled_bytes: int, payload_bytes: int,
                 *, ack: bool) -> int:
        """记录一次空口发送。只有 ACK 才从队列扣 payload。"""
        b = self.active
        if b is None or payload_bytes <= 0:
            return 0
        if b.first_tx_tti < 0:
            b.first_tx_tti = int(tti)
        b.tx_attempts += 1
        # 首传等待属于到达对象，而不是整个 busy period。一次 TB 可以拼接多个
        # FIFO 对象；即便 NACK，它们也已经发生过首传，不能等 ACK 后才记起点。
        mark_left = min(int(payload_bytes), self.queued_bytes)
        for item in self.items:
            if mark_left <= 0:
                break
            covered = min(int(item.remaining_bytes), mark_left)
            if covered > 0 and item.first_tx_tti < 0:
                item.first_tx_tti = int(tti)
            mark_left -= covered
        if not ack:
            return 0
        payload = min(int(payload_bytes), self.queued_bytes)
        padding = max(0, int(scheduled_bytes) - payload)
        self.queued_bytes -= payload
        b.bytes_acked += payload
        b.last_ack_tti = int(tti)
        b.ack_events.append(AckEvent(int(tti), payload, int(scheduled_bytes), padding))
        consume_left = payload
        while consume_left > 0 and self.items:
            item = self.items[0]
            take = min(int(item.remaining_bytes), consume_left)
            item.remaining_bytes -= take
            consume_left -= take
            if item.remaining_bytes == 0:
                item.completion_tti = int(tti)
                self.done_items.append(item)
                self.items.popleft()
        if self.queued_bytes == 0:
            # 当前 TTI 的到达已在调度前进入队列，因此清空就真的结束 busy period。
            self.done.append(b)
            self.active = None
        return payload


def arrival_item_metrics(item: ArrivalItem, tti_ms: float,
                         pdb_ms: float) -> tuple[float | None, float | None, bool | None]:
    """返回 ``(首调度等待, 完成时延, PDB miss)``，单位 ms。

    PDB 判断只对调用方赋予该到达对象的业务口径成立；FTP 文件不是标准定义的
    单个 PDCP SDU，因此结果会按 traffic class 分开，不能把 large 文件 PDB 与
    1500 B small 小包代理混成一个结论。
    """
    if item.first_tx_tti < 0 or item.completion_tti < 0:
        return None, None, None
    wait = max(0, item.first_tx_tti - item.arrival_tti) * float(tti_ms)
    completion = (item.completion_tti - item.arrival_tti + 1) * float(tti_ms)
    miss = completion > float(pdb_ms) if float(pdb_ms) > 0 else None
    return wait, completion, miss


@dataclass(frozen=True)
class BurstMetrics:
    throughput_mbps: float | None
    throughput_kind: str | None
    queue_wait_ms: float | None
    completion_delay_ms: float | None
    pdb_miss: bool | None


def burst_metrics(burst: BusyPeriod, tti_ms: float,
                  small_burst_policy: str = "fractional_slot") -> BurstMetrics:
    """按 28.552 Rel-19 与时延口径计算一个已完成 busy period。

    * 大 burst：从首传开始，体积与时间都排除清空 buffer 的最后一个 ACK piece。
    * 单次首传即成功的小 burst：可选 fractional-slot，时间按
      ``slot × payload/TBVol`` 折算（TBVol−PaddingVol 就是 payload）。
    * 排队等待与完成时延从 arrival 开始，和 3GPP throughput 分开上报。
    """
    if not burst.completed or burst.first_tx_tti < 0 or burst.last_ack_tti < 0:
        return BurstMetrics(None, None, None, None, None)
    wait = max(0, burst.first_tx_tti - burst.start_tti) * float(tti_ms)
    completion = (burst.last_ack_tti - burst.start_tti + 1) * float(tti_ms)
    pdb_miss = completion > float(burst.pdb_ms) if burst.pdb_ms > 0 else None
    events = burst.ack_events
    if len(events) >= 2:
        vol = int(sum(e.payload_bytes for e in events[:-1]))
        duration_tti = events[-2].tti - burst.first_tx_tti + 1
        if vol > 0 and duration_tti > 0:
            thp = vol * 8.0 / (duration_tti * float(tti_ms) / 1000.0) / 1e6
            return BurstMetrics(thp, "rel19_large_burst", wait, completion, pdb_miss)
    if (small_burst_policy == "fractional_slot" and len(events) == 1
            and burst.tx_attempts == 1):
        e = events[0]
        if e.payload_bytes > 0 and e.scheduled_bytes > 0:
            effective_ms = float(tti_ms) * e.payload_bytes / e.scheduled_bytes
            thp = e.payload_bytes * 8.0 / (effective_ms / 1000.0) / 1e6
            return BurstMetrics(thp, "rel19_fractional_slot", wait, completion, pdb_miss)
    return BurstMetrics(None, None, wait, completion, pdb_miss)


@dataclass(frozen=True)
class Allocation:
    tti: int
    ue: int
    traffic_class: str
    slot: str
    rbg_indices: tuple[int, ...]
    n_rbg: int
    mcs: int
    rank: int
    scheduled_bytes: int
    payload_bytes: int
    acked_bytes: int
    padding_bytes: int
    pf_credit_bytes: int
    sinr_db: float
    bler: float
    ack: bool

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["rbg_indices"] = list(self.rbg_indices)
        d["sinr_db"] = round(self.sinr_db, 4)
        d["bler"] = round(self.bler, 6)
        return d


@dataclass
class ExperienceRun:
    cell: dict[str, Any]
    users: list[dict[str, Any]]
    notes: list[str]
    diagnostics: dict[str, Any]
    elapsed_s: float


class ExperienceTraffic:
    """每个 UE 一个 DRB；mixed 模式按 UE 分配业务类，避免混淆 burst 边界。"""

    def __init__(self, cfg: Any, n_ue: int, tti_ms: float,
                 rng: np.random.Generator) -> None:
        self.cfg, self.n_ue, self.tti_ms, self.rng = cfg, int(n_ue), float(tti_ms), rng
        classes = list(cfg.resolved_classes())
        if not classes:
            raise ValueError("experience 模式至少需要一个 TrafficClass")
        for c in classes:
            if int(c.file_bytes) < 1:
                raise ValueError(f"TrafficClass {c.name!r} 的 file_bytes 必须至少为 1")
            if float(c.arrival_rate_hz) < 0 or float(c.cbr_mbps) < 0:
                raise ValueError(f"TrafficClass {c.name!r} 的到达率/CBR 不能为负")
            if float(c.pdb_ms) < 0:
                raise ValueError(f"TrafficClass {c.name!r} 的 pdb_ms 不能为负")
        shares = np.asarray([max(0.0, float(c.ue_share)) for c in classes], dtype=float)
        if float(shares.sum()) <= 0:
            raise ValueError("TrafficClass.ue_share 之和必须大于 0")
        shares /= shares.sum()
        raw = shares * self.n_ue
        counts = np.floor(raw).astype(int)
        for i in np.argsort(-(raw - counts))[:self.n_ue - int(counts.sum())]:
            counts[int(i)] += 1
        labels = [c for c, n in zip(classes, counts, strict=True) for _ in range(int(n))]
        # 随机排列消除“业务类恰好绑定 UE 编号/远近点”的系统偏差；CRN 两臂会复用它。
        perm = self.rng.permutation(self.n_ue)
        assigned: list[Any] = [None] * self.n_ue
        for pos, ue in enumerate(perm):
            assigned[int(ue)] = labels[pos]
        self.queues = [DrbQueue(u, assigned[u]) for u in range(self.n_ue)]
        self.offered_bytes = 0
        self.arrival_events = 0
        self.unbounded = str(cfg.model) == "full_buffer"
        self._cbr_carry = np.zeros(self.n_ue, dtype=float)

    def step(self, tti: int) -> None:
        for u, q in enumerate(self.queues):
            c = q.traffic_class
            if self.unbounded:
                if q.active is None:
                    q.arrive(tti, 1 << 50)
                continue
            if str(self.cfg.model) == "cbr":
                exact = float(c.cbr_mbps) * 1e6 * self.tti_ms / 1000.0 / 8
                self._cbr_carry[u] += max(0.0, exact)
                n = int(np.floor(self._cbr_carry[u]))
                self._cbr_carry[u] -= n
                if n > 0:
                    q.arrive(tti, n)
                    self.offered_bytes += n
                    self.arrival_events += 1
                continue
            lam = max(0.0, float(c.arrival_rate_hz) * self.tti_ms / 1000.0)
            n_arrivals = int(self.rng.poisson(lam))
            if n_arrivals:
                n_each = int(c.file_bytes)
                # 同一 TTI 的多个文件仍是多个外生到达对象；DRB busy period 会
                # 自然合并，但等待/PDB 不能把它们压成一个大文件。
                for _ in range(n_arrivals):
                    q.arrive(tti, n_each)
                self.offered_bytes += n_arrivals * n_each
                self.arrival_events += n_arrivals

    def has_data(self, ue: int) -> bool:
        return self.queues[int(ue)].queued_bytes > 0

    def bytes_left(self, ue: int) -> int:
        return int(self.queues[int(ue)].queued_bytes)

    def hol_delay_ms(self, ue: int, tti: int) -> float:
        b = self.queues[int(ue)].active
        return max(0, int(tti) - b.start_tti) * self.tti_ms if b is not None else 0.0

    def transmit(self, ue: int, tti: int, scheduled_bytes: int,
                 payload_bytes: int, *, ack: bool) -> int:
        return self.queues[int(ue)].transmit(
            tti, scheduled_bytes, payload_bytes, ack=ack)

    @property
    def backlog_bytes(self) -> int:
        return int(sum(q.queued_bytes for q in self.queues))


_BLER_CACHE: dict[tuple[int, int], float] = {}
_BLER_CACHE_STEP_DB = 0.05


def _bler_lookup(mcs: int, sinr_db: float) -> float:
    if not np.isfinite(sinr_db):
        return 1.0
    clipped = float(np.clip(sinr_db, -60.0, 60.0))
    key = (int(mcs), int(round(clipped / _BLER_CACHE_STEP_DB)))
    if key not in _BLER_CACHE:
        from . import bler_curves as bc  # noqa: PLC0415

        value = float(np.atleast_1d(
            bc.get_curve(key[0], "newtx").evaluate(
                key[1] * _BLER_CACHE_STEP_DB))[0])
        _BLER_CACHE[key] = float(np.clip(value, 0.0, 1.0))
    return _BLER_CACHE[key]


def _finite(values: Iterable[float]) -> list[float]:
    return [float(x) for x in values if np.isfinite(x)]


def _pct(values: Iterable[float], q: float) -> float | None:
    v = _finite(values)
    return float(np.percentile(v, q)) if v else None


def _mean(values: Iterable[float]) -> float | None:
    v = _finite(values)
    return float(np.mean(v)) if v else None


def _ordered_candidates(metric: np.ndarray, cand: Sequence[int], tti: int,
                        algorithm: str, n_ue: int,
                        tie_keys: np.ndarray) -> np.ndarray:
    if algorithm == "rr":
        return np.argsort(np.asarray([((u - tti) % n_ue) for u in cand], dtype=float))
    vals = metric.tolist()
    if len(vals) > 1 and len(set(vals)) < len(vals):
        return np.lexsort((np.asarray(tie_keys, dtype=float), -metric))
    return np.argsort(-metric)


def simulate_experience(
    tables: Sequence[Any], *, sys_cfg: Any, traffic_cfg: Any, sched: Any,
    kpi: Any, book: rg.RngBook, s_slot_fraction: float = 0.7,
    progress: Any = None,
) -> ExperienceRun:
    """运行 ``experience_v2``。返回值由 :mod:`system` 包成 ``SystemResult``。"""
    t0 = time.perf_counter()
    if not tables:
        raise ValueError("至少需要一个 UE 链路表")
    if int(sys_cfg.num_tti) < 1 or float(sys_cfg.tti_ms) <= 0:
        raise ValueError("duration_s/scs_khz 必须产生至少一个正时长 TTI")
    if int(sys_cfg.num_rbg) < 1 or int(sys_cfg.rb_per_rbg) < 1:
        raise ValueError("num_rbg 与 rb_per_rbg 必须为正整数")
    pattern = str(sys_cfg.tdd_pattern).upper()
    if not pattern or any(x not in "DSU" for x in pattern):
        raise ValueError("tdd_pattern 只允许 D/S/U 且不能为空")
    if int(sched.pf_window_tti) < 1:
        raise ValueError("pf_window_tti 必须至少为 1")
    if bool(sched.mu_enabled):
        raise ValueError("experience_v2 首版只支持 SU；请把 mu_enabled=False")
    if str(traffic_cfg.model) == "bimodal":
        raise ValueError("experience_v2 不接受按目标 RBG 数反推包长的 bimodal；请用 mixed")
    if str(traffic_cfg.model) not in ("mixed", "ftp3", "full_buffer", "cbr"):
        raise ValueError(f"experience_v2 不支持话务 {traffic_cfg.model!r}")
    if str(sched.algorithm) not in ("pf", "qos_pf", "rr", "max_ci"):
        raise ValueError(f"experience_v2 不支持调度器 {sched.algorithm!r}")
    if str(getattr(sched, "qos_priority_weighting", "none")) not in (
            "none", "inverse_priority"):
        raise ValueError("qos_priority_weighting 只支持 none / inverse_priority")
    for name in ("qos_avg_rate_exponent", "qos_instant_rate_exponent",
                 "qos_delay_exponent"):
        value = float(getattr(sched, name, 0.0))
        if not np.isfinite(value) or value < 0:
            raise ValueError(f"{name} 必须是有限非负数")
    small_policy = str(getattr(kpi, "small_burst_policy", "fractional_slot"))
    if small_policy not in ("fractional_slot", "exclude"):
        raise ValueError("small_burst_policy 只支持 fractional_slot / exclude")
    accounting = str(getattr(sched, "pf_accounting", "auto"))
    if accounting == "auto":
        accounting = "scheduled_tbs"
    if accounting not in ("scheduled_tbs", "acked_goodput", "legacy_fullband"):
        raise ValueError("experience_v2 的 pf_accounting 只支持 scheduled_tbs / "
                         "acked_goodput / legacy_fullband")

    n_ue = len(tables)
    n_snap = int(tables[0].sinr_db.shape[0])
    if n_snap < 1:
        raise ValueError("链路表至少需要一个 snapshot")
    for i, table in enumerate(tables):
        if int(getattr(table, "mcs_table", 3)) != 3:
            raise ValueError("experience_v2 的 TBS/BLER 反查只支持 MCS table 3")
        if table.sinr_db.shape[0] != n_snap:
            raise ValueError(f"UE {i} 的 snapshot 数与 UE0 不一致")
        if table.sinr_db.ndim != 2 or table.sinr_db.shape[1] < 1:
            raise ValueError(f"UE {i} 的 sinr_db 必须是 [snapshot,rank]")
    lookup = TbsLookup.build(sys_cfg.num_rbg, sys_cfg.rb_per_rbg, s_slot_fraction)
    tr = ExperienceTraffic(traffic_cfg, n_ue, sys_cfg.tti_ms,
                           book.generator("traffic"))
    # **CRN 必须绑定到同一个事件，而不是“第几个被调度者”。** 若顺序消费一条
    # generator，A/B 的调度一旦分叉，后续同一个随机数会落到不同 UE/TTI，
    # 名义上种子相同、实际上事件错位。固定生成 [TTI,UE] 网格后，方案只会决定
    # 某个格子是否被使用，不会移动别的格子；tie-break 同理。
    harq_draw = book.generator("harq").random((int(sys_cfg.num_tti), n_ue))
    scheduler_draw = book.generator("scheduler").random((int(sys_cfg.num_tti), n_ue))
    snap_every = max(1, int(round(sys_cfg.snapshot_update_ms / sys_cfg.tti_ms)))
    r_avg = np.full(n_ue, 1e-6, dtype=float)
    olla_db = np.zeros(n_ue, dtype=float)
    served = np.zeros(n_ue, dtype=float)
    scheduled_tbs = np.zeros(n_ue, dtype=float)
    attempted_payload = np.zeros(n_ue, dtype=float)
    padding = np.zeros(n_ue, dtype=float)
    sched_cnt = np.zeros(n_ue, dtype=int)
    mcs_sum = np.zeros(n_ue, dtype=float)
    rank_sum = np.zeros(n_ue, dtype=float)
    tx_count = np.zeros(n_ue, dtype=int)
    nack_count = np.zeros(n_ue, dtype=int)

    dl_tti = busy_tti = multi_ue_tti = outage_skips = 0
    allocated_rbg = scheduled_ues_sum = 0
    available_rbg_equiv = allocated_rbg_equiv = 0.0
    rbg_hist: list[int] = []
    allocation_sample: list[Allocation] = []
    allocation_limit = 256
    max_rbg_in_tti = overlap_violations = 0
    class_alloc_rbg: dict[str, int] = {}
    class_acked: dict[str, int] = {}

    for tti in range(int(sys_cfg.num_tti)):
        # 业务在 UL/保护时隙照样到达；旧实现把 step 放在 continue 后面，会漏掉这些到达。
        tr.step(tti)
        slot = pattern[tti % len(pattern)]
        if slot not in ("D", "S"):
            continue
        dl_tti += 1
        slot_fraction = 1.0 if slot == "D" else float(s_slot_fraction)
        available_rbg_equiv += int(sys_cfg.num_rbg) * slot_fraction
        snap = (tti // snap_every) % n_snap
        cand = [u for u in range(n_ue) if tr.has_data(u)
                and not (tables[u].outage is not None and tables[u].outage[snap])]
        outage_skips += sum(1 for u in range(n_ue) if tr.has_data(u)
                            and tables[u].outage is not None and tables[u].outage[snap])
        a = 1.0 / max(int(sched.pf_window_tti), 1)
        if not cand:
            r_avg *= 1.0 - a
            continue

        rank_of: dict[int, int] = {}
        mcs_of: dict[int, int] = {}
        potential = np.zeros(len(cand), dtype=float)
        delay_factor = np.ones(len(cand), dtype=float)
        priority_factor = np.ones(len(cand), dtype=float)
        for i, u in enumerate(cand):
            rank = int(tables[u].best_rank[snap])
            if tables[u].sinr_tx_db is not None and sched.olla_enabled:
                tx_sinr = float(tables[u].sinr_tx_db[snap, rank - 1]) + olla_db[u]
                mcs = int(la.select_mcs(tx_sinr, table=3, target_bler=0.1).index)
            else:
                mcs = int(tables[u].mcs[snap, rank - 1])
            rank_of[u], mcs_of[u] = rank, mcs
            potential[i] = lookup.tbs_bytes(slot, mcs, rank, sys_cfg.num_rbg)
            c = tr.queues[u].traffic_class
            if str(getattr(sched, "qos_priority_weighting", "none")) == \
                    "inverse_priority":
                priority_factor[i] = 1.0 / max(float(c.priority), 1.0)
            if str(c.resource_type).upper() in ("GBR", "DELAY_CRITICAL_GBR") \
                    and float(c.pdb_ms) > 0:
                hol = tr.hol_delay_ms(u, tti)
                remain = float(c.pdb_ms) - hol
                delay_factor[i] = (1000.0 if remain <= 0 else
                                   min(1000.0, float(c.pdb_ms) / max(remain, _EPS)))

        if sched.algorithm == "pf":
            metric = potential / np.maximum(r_avg[cand], 1e-9)
        elif sched.algorithm == "qos_pf":
            alpha = float(getattr(sched, "qos_avg_rate_exponent", 1.0))
            beta = float(getattr(sched, "qos_instant_rate_exponent", 1.0))
            gamma = float(getattr(sched, "qos_delay_exponent", 0.0))
            metric = (priority_factor
                      * np.power(np.maximum(potential, 1.0), beta)
                      / np.power(np.maximum(r_avg[cand], 1e-9), alpha)
                      * np.power(delay_factor, gamma))
        elif sched.algorithm == "max_ci":
            metric = potential
        else:
            metric = np.zeros(len(cand), dtype=float)
        order = _ordered_candidates(metric, cand, tti, str(sched.algorithm),
                                    n_ue, scheduler_draw[tti, cand])

        remaining = int(sys_cfg.num_rbg)
        cursor = tti % int(sys_cfg.num_rbg)
        used_indices: set[int] = set()
        rbg_offset = 0
        inst = np.zeros(n_ue, dtype=float)
        allocations_this_tti = 0
        for oi in order:
            if remaining <= 0:
                break
            u = int(cand[int(oi)])
            rank, mcs = rank_of[u], mcs_of[u]
            n_need, _fits = lookup.required_rbg(
                slot, mcs, rank, tr.bytes_left(u))
            n_alloc = min(n_need, remaining)
            tb_bytes = lookup.tbs_bytes(slot, mcs, rank, n_alloc)
            payload = min(tr.bytes_left(u), tb_bytes)
            if payload <= 0:
                continue
            indices = tuple((cursor + rbg_offset + j) % int(sys_cfg.num_rbg)
                            for j in range(n_alloc))
            if used_indices.intersection(indices):
                overlap_violations += 1
            used_indices.update(indices)
            sinr = float(tables[u].sinr_db[snap, rank - 1])
            bler = _bler_lookup(mcs, sinr)
            ack = bool(harq_draw[tti, u] > bler)
            acked = tr.transmit(u, tti, tb_bytes, payload, ack=ack)
            pad = max(0, tb_bytes - payload)
            if accounting == "scheduled_tbs":
                credit = tb_bytes
            elif accounting == "acked_goodput":
                credit = acked
            else:
                credit = lookup.tbs_bytes(slot, mcs, rank, sys_cfg.num_rbg)
            inst[u] += credit
            tx_count[u] += 1
            nack_count[u] += int(not ack)
            sched_cnt[u] += 1
            mcs_sum[u] += mcs
            rank_sum[u] += rank
            served[u] += acked
            scheduled_tbs[u] += tb_bytes
            attempted_payload[u] += payload
            padding[u] += pad
            if sched.olla_enabled:
                if ack:
                    olla_db[u] = min(olla_db[u] + sched.step_up, sched.olla_max_db)
                else:
                    olla_db[u] = max(olla_db[u] - sched.step_down, sched.olla_min_db)
            cls = str(tr.queues[u].traffic_class.name)
            class_alloc_rbg[cls] = class_alloc_rbg.get(cls, 0) + n_alloc
            class_acked[cls] = class_acked.get(cls, 0) + acked
            alloc = Allocation(
                tti=tti, ue=u, traffic_class=cls, slot=slot,
                rbg_indices=indices, n_rbg=n_alloc, mcs=mcs, rank=rank,
                scheduled_bytes=tb_bytes, payload_bytes=payload,
                acked_bytes=acked, padding_bytes=pad, pf_credit_bytes=int(credit),
                sinr_db=sinr, bler=bler, ack=ack)
            if len(allocation_sample) < allocation_limit:
                allocation_sample.append(alloc)
            rbg_hist.append(n_alloc)
            allocated_rbg += n_alloc
            allocated_rbg_equiv += n_alloc * slot_fraction
            rbg_offset += n_alloc
            remaining -= n_alloc
            allocations_this_tti += 1
        if allocations_this_tti:
            busy_tti += 1
            scheduled_ues_sum += allocations_this_tti
            multi_ue_tti += int(allocations_this_tti > 1)
            max_rbg_in_tti = max(max_rbg_in_tti, len(used_indices))
        r_avg = (1.0 - a) * r_avg + a * inst
        if progress and tti % 5000 == 0:
            progress(tti, int(sys_cfg.num_tti))

    users: list[dict[str, Any]] = []
    all_wait: list[float] = []                 # arrival object, FIFO
    all_completion: list[float] = []           # arrival object, FIFO
    all_busy_wait: list[float] = []            # DRB busy period 首调度
    all_busy_completion: list[float] = []      # DRB busy period 排空
    all_thp: list[float] = []
    small_thp: list[float] = []
    large_thp: list[float] = []
    large_user_thp: list[float] = []
    pdb_flags: list[bool] = []
    small_wait: list[float] = []
    small_completion: list[float] = []
    small_pdb_flags: list[bool] = []
    large_wait: list[float] = []
    large_completion: list[float] = []
    large_pdb_flags: list[bool] = []
    class_arrival_kpis: dict[str, dict[str, Any]] = {}
    measured_bursts = completed_bursts = 0
    completed_arrival_objects = 0
    warmup = int(kpi.warmup_tti)
    for u, q in enumerate(tr.queues):
        done = [b for b in q.done if b.start_tti >= warmup]
        metrics = [burst_metrics(b, sys_cfg.tti_ms, small_policy) for b in done]
        thp = [m.throughput_mbps for m in metrics if m.throughput_mbps is not None]
        busy_waits = [m.queue_wait_ms for m in metrics if m.queue_wait_ms is not None]
        busy_completes = [m.completion_delay_ms for m in metrics
                          if m.completion_delay_ms is not None]
        done_items = [item for item in q.done_items if item.arrival_tti >= warmup]
        item_metrics = [arrival_item_metrics(item, sys_cfg.tti_ms,
                                             float(q.traffic_class.pdb_ms))
                        for item in done_items]
        waits = [x[0] for x in item_metrics if x[0] is not None]
        completes = [x[1] for x in item_metrics if x[1] is not None]
        pflags = [x[2] for x in item_metrics if x[2] is not None]
        svals = [m.throughput_mbps for m in metrics
                 if m.throughput_kind == "rel19_fractional_slot"
                 and m.throughput_mbps is not None]
        lvals = [m.throughput_mbps for m in metrics
                 if m.throughput_kind == "rel19_large_burst"
                 and m.throughput_mbps is not None]
        all_wait.extend(float(x) for x in waits)
        all_completion.extend(float(x) for x in completes)
        all_busy_wait.extend(float(x) for x in busy_waits)
        all_busy_completion.extend(float(x) for x in busy_completes)
        all_thp.extend(float(x) for x in thp)
        small_thp.extend(float(x) for x in svals)
        large_thp.extend(float(x) for x in lvals)
        pdb_flags.extend(bool(x) for x in pflags)
        completed_bursts += len(done)
        completed_arrival_objects += len(done_items)
        measured_bursts += len(thp)
        ue_thp = float(np.mean(thp)) if thp else 0.0
        is_small_class = bool(q.traffic_class.is_small)
        if not is_small_class and thp:
            large_user_thp.append(ue_thp)
        target_wait = small_wait if is_small_class else large_wait
        target_completion = small_completion if is_small_class else large_completion
        target_pdb = small_pdb_flags if is_small_class else large_pdb_flags
        target_wait.extend(float(x) for x in waits)
        target_completion.extend(float(x) for x in completes)
        target_pdb.extend(bool(x) for x in pflags)
        cls_name = str(q.traffic_class.name)
        cls_row = class_arrival_kpis.setdefault(
            cls_name, {"wait": [], "completion": [], "pdb": [], "completed": 0,
                       "is_small": is_small_class})
        cls_row["wait"].extend(float(x) for x in waits)
        cls_row["completion"].extend(float(x) for x in completes)
        cls_row["pdb"].extend(bool(x) for x in pflags)
        cls_row["completed"] += len(done_items)
        users.append({
            "ue": u,
            "traffic_class": str(q.traffic_class.name),
            "geo_sinr_db": round(float(tables[u].geo_sinr_db), 4),
            "iot_db": round(float(tables[u].iot_db), 4),
            "experienced_mbps": ue_thp,
            "large_burst_experienced_mbps": _mean(lvals),
            "small_burst_fractional_mbps": _mean(svals),
            "served_mbps": float(served[u] * 8 / max(sys_cfg.duration_s, _EPS) / 1e6),
            "bursts": len(thp),
            "completed_bursts": len(done),
            "completed_arrival_objects": len(done_items),
            "arrival_queue_wait_p95_ms": _pct(waits, 95),
            "arrival_completion_delay_p95_ms": _pct(completes, 95),
            "arrival_pdb_miss_ratio": float(np.mean(pflags)) if pflags else None,
            "busy_period_first_schedule_wait_p95_ms": _pct(busy_waits, 95),
            "busy_period_completion_delay_p95_ms": _pct(busy_completes, 95),
            # 兼容字段从 experience_v2 起明确指 FIFO arrival object，不再拿
            # busy period 的首个 arrival 代替期间所有小包。
            "queue_wait_p95_ms": _pct(waits, 95),
            "completion_delay_p95_ms": _pct(completes, 95),
            "pdb_miss_ratio": float(np.mean(pflags)) if pflags else None,
            "avg_mcs": float(mcs_sum[u] / max(sched_cnt[u], 1)),
            "avg_rank": float(rank_sum[u] / max(sched_cnt[u], 1)),
            "bler_first_tx": float(nack_count[u] / max(tx_count[u], 1)),
            "newtx_attempt_bler": float(nack_count[u] / max(tx_count[u], 1)),
            "residual_bler": None,
            "sched_tti": int(sched_cnt[u]),
            "retx_tti": 0,
            "queued_bytes": int(q.queued_bytes),
        })

    user_exp = [float(u["experienced_mbps"]) for u in users if int(u["bursts"]) > 0]
    offered = int(tr.offered_bytes)
    backlog = int(tr.backlog_bytes)
    acked_total = int(np.sum(served))
    acct_error = (0.0 if tr.unbounded else
                  abs(acked_total + backlog - offered) / max(offered, 1) * 100.0)
    sched_total = float(np.sum(scheduled_tbs))
    attempted_total = float(np.sum(attempted_payload))
    padding_total = float(np.sum(padding))
    tx_total = int(np.sum(tx_count))
    nack_total = int(np.sum(nack_count))
    class_arrival_summary = {
        name: {
            "is_small": bool(row["is_small"]),
            "completed_arrival_objects": int(row["completed"]),
            "queue_wait_ms_p50": _pct(row["wait"], 50),
            "queue_wait_ms_p95": _pct(row["wait"], 95),
            "queue_wait_ms_p99": _pct(row["wait"], 99),
            "completion_delay_ms_p50": _pct(row["completion"], 50),
            "completion_delay_ms_p95": _pct(row["completion"], 95),
            "completion_delay_ms_p99": _pct(row["completion"], 99),
            "pdb_miss_ratio": (float(np.mean(row["pdb"])) if row["pdb"] else None),
        }
        for name, row in class_arrival_kpis.items()
    }
    cell = {
        "cell_experienced_mbps": _mean(user_exp) or 0.0,
        "ue_experienced_mean_mbps": _mean(user_exp) or 0.0,
        "ue_experienced_median_mbps": _pct(user_exp, 50) or 0.0,
        "ue_experienced_p5_mbps": _pct(user_exp, 5) or 0.0,
        "drb_throughput_rel19_mbps": _mean(all_thp),
        "large_burst_drb_throughput_mbps": _mean(large_thp),
        "large_flow_drb_throughput_p5_mbps": _pct(large_user_thp, 5),
        "small_burst_fractional_mbps": _mean(small_thp),
        "arrival_queue_wait_ms_p50": _pct(all_wait, 50),
        "arrival_queue_wait_ms_p95": _pct(all_wait, 95),
        "arrival_queue_wait_ms_p99": _pct(all_wait, 99),
        "arrival_completion_delay_ms_p50": _pct(all_completion, 50),
        "arrival_completion_delay_ms_p95": _pct(all_completion, 95),
        "arrival_completion_delay_ms_p99": _pct(all_completion, 99),
        "arrival_pdb_miss_ratio": float(np.mean(pdb_flags)) if pdb_flags else None,
        "small_queue_wait_ms_p50": _pct(small_wait, 50),
        "small_queue_wait_ms_p95": _pct(small_wait, 95),
        "small_queue_wait_ms_p99": _pct(small_wait, 99),
        "small_completion_delay_ms_p95": _pct(small_completion, 95),
        "small_pdb_miss_ratio": (float(np.mean(small_pdb_flags))
                                  if small_pdb_flags else None),
        "large_queue_wait_ms_p95": _pct(large_wait, 95),
        "large_completion_delay_ms_p95": _pct(large_completion, 95),
        "large_pdb_miss_ratio": (float(np.mean(large_pdb_flags))
                                  if large_pdb_flags else None),
        "busy_period_first_schedule_wait_ms_p95": _pct(all_busy_wait, 95),
        "busy_period_completion_delay_ms_p95": _pct(all_busy_completion, 95),
        "class_arrival_kpis": class_arrival_summary,
        # 兼容字段在 experience_v2 中等价于 arrival-object 口径。
        "queue_wait_ms_p50": _pct(all_wait, 50),
        "queue_wait_ms_p95": _pct(all_wait, 95),
        "queue_wait_ms_p99": _pct(all_wait, 99),
        "completion_delay_ms_p50": _pct(all_completion, 50),
        "completion_delay_ms_p95": _pct(all_completion, 95),
        "completion_delay_ms_p99": _pct(all_completion, 99),
        "pdb_miss_ratio": float(np.mean(pdb_flags)) if pdb_flags else None,
        "cell_served_mbps": float(acked_total * 8 / max(sys_cfg.duration_s, _EPS) / 1e6),
        "avg_mcs": float(np.sum(mcs_sum) / max(np.sum(sched_cnt), 1)),
        "avg_rank": float(np.sum(rank_sum) / max(np.sum(sched_cnt), 1)),
        "bler_first_tx": float(nack_total / max(tx_total, 1)),
        "newtx_attempt_bler": float(nack_total / max(tx_total, 1)),
        "residual_bler": None,
        "residual_bler_definition": "not_applicable_without_harq_or_drop_limit",
        "dl_tti": int(dl_tti),
        "scheduled_tti": int(busy_tti),
        "occupancy": float(busy_tti / max(dl_tti, 1)),
        "resource_utilization": float(allocated_rbg_equiv / max(available_rbg_equiv, 1.0)),
        "rbg_slot_occupancy": float(allocated_rbg / max(dl_tti * sys_cfg.num_rbg, 1)),
        "scheduled_ues_per_busy_tti": float(scheduled_ues_sum / max(busy_tti, 1)),
        "multi_ue_tti_share": float(multi_ue_tti / max(busy_tti, 1)),
        "payload_fill_ratio": float(attempted_total / max(sched_total, 1.0)),
        "ack_payload_efficiency": float(acked_total / max(sched_total, 1.0)),
        "padding_ratio": float(padding_total / max(sched_total, 1.0)),
        "actual_rbg_size_hist": ({
            "p_1rbg": float(np.mean(np.asarray(rbg_hist) == 1)),
            "p_full": float(np.mean(np.asarray(rbg_hist) == sys_cfg.num_rbg)),
            "mean_rbg": float(np.mean(rbg_hist)), "n": len(rbg_hist),
        } if rbg_hist else None),
        # 兼容旧消费者；experience_v2 里它明确就是 actual allocation。
        "rbg_size_hist": ({
            "p_1rbg": float(np.mean(np.asarray(rbg_hist) == 1)),
            "p_full": float(np.mean(np.asarray(rbg_hist) == sys_cfg.num_rbg)),
            "mean_rbg": float(np.mean(rbg_hist)), "n": len(rbg_hist),
        } if rbg_hist else None),
        "small_pkt_experienced_mbps": _mean(small_thp),
        "large_pkt_experienced_mbps": _mean(large_thp),
        "measured_bursts": int(measured_bursts),
        "completed_bursts": int(completed_bursts),
        "completed_arrival_objects": int(completed_arrival_objects),
        "offered_mbps": (None if tr.unbounded else
                         float(offered * 8 / max(sys_cfg.duration_s, _EPS) / 1e6)),
        "backlog_bytes": backlog,
        "backlog_bursts": int(sum(q.active is not None for q in tr.queues)),
        "accounting_error_pct": round(float(acct_error), 6),
        "outage_ue": int(sum(1 for t in tables
                              if t.outage is not None and bool(t.outage.all()))),
        "outage_skips": int(outage_skips),
        "olla_db_mean": float(np.mean(olla_db)),
        "olla_db_p5": float(np.percentile(olla_db, 5)),
        "olla_db_p95": float(np.percentile(olla_db, 95)),
        "mu_share": 0.0,
        "mu_rbg_share": 0.0,
        "pf_accounting": accounting,
        "harq_model": "none_retry_as_new_tx",
        "class_allocated_rbg": class_alloc_rbg,
        "class_acked_bytes": class_acked,
    }

    notes: list[str] = [
        "experience_v2 使用**宽带 SINR/MCS**做误码与调度，不包含逐 RBG 频选增益。",
        "TBS 量化算法走 38.214 §5.1.3.2，但 MCS 是公司 20B profile；D 时隙按"
        "每 RB 12 个数据符号、S 时隙按 0.7 倍 N_RE，未展开 DMRS/PTRS/CORESET。",
        "HARQ 软合并未建模；NACK payload 留在 DRB 队列，下一次按 NewTx 重试。",
        f"PF 平均量口径是 **{accounting}**；ACKed bytes 另作为 KPI 统计。",
        "分配器每个 DL TTI 只排序一次：按 PF/QoS-PF 优先级依次给最小够用 RBG；"
        "剩余 RBG 没有候选需求时留空，不回填给第一名。",
        "DRB throughput 按 buffer busy period；queue/completion/PDB 按 FIFO "
        "arrival object（mixed/FTP 为文件，CBR 为每 TTI 字节块）。1500 B small "
        "类可作小包代理，large FTP 文件的 PDB 不能冒充逐 PDCP SDU 时延。",
    ]
    if str(sched.algorithm) == "qos_pf":
        notes.append(
            "qos_pf 使用 w(priority) × R_inst^beta / R_avg^alpha × "
            "[PDB/(PDB−HOL)]^gamma（时延因子上限 1000）；默认 "
            "alpha=beta=1、gamma=0、w=1，严格退化成经典 PF。它是显式工程 "
            "profile，未冒充现场未确认的 EPF。")
    if n_snap < 4:
        notes.append(f"**信道快照只有 {n_snap} 个**，时间起伏被严重低估，PF 多用户分集不足。")
    if measured_bursts < 20 and not tr.unbounded:
        notes.append(f"只有 {measured_bursts} 个 busy period 进入体验 KPI，样本太少；"
                     "加长 duration_s 或调高到达率。")
    if not tr.unbounded and backlog > 0.15 * max(offered, 1):
        notes.append(f"**队列积压 {backlog * 8 / 1e6:.1f} Mb**"
                     f"（占到达量 {backlog / max(offered, 1):.0%}），系统未收敛。")
    if acct_error > 1.0:
        notes.append(f"**字节对不上账（差 {acct_error:.3f}%）**："
                     "arrived 必须等于 acked + queued + in_flight + dropped。")
    if cell["occupancy"] > 0.98:
        notes.append("**下行时隙几乎占满**，当前结果更接近容量上限而非稳态体验。")
    diagnostics = {
        "tbs_lookup": lookup.as_dict(),
        "allocation_sample": [a.as_dict() for a in allocation_sample],
        "allocation_sample_limit": allocation_limit,
        "max_rbg_in_any_tti": int(max_rbg_in_tti),
        "rbg_overlap_violations": int(overlap_violations),
        "allocated_rbg_total": int(allocated_rbg),
        "available_rbg_total": int(dl_tti * sys_cfg.num_rbg),
        "allocated_rbg_equivalent": float(allocated_rbg_equiv),
        "available_rbg_equivalent": float(available_rbg_equiv),
        "arrival_events": int(tr.arrival_events),
        "byte_conservation": {
            "arrived": None if tr.unbounded else offered,
            "acked": acked_total,
            "queued": backlog,
            "in_flight": 0,
            "dropped": 0,
            "error_pct": round(float(acct_error), 6),
        },
        "crn_event_mapping": "traffic sequential fixed UE/TTI loop; harq and tie-break indexed [TTI,UE]",
    }
    return ExperienceRun(cell=cell, users=users, notes=notes,
                         diagnostics=diagnostics,
                         elapsed_s=time.perf_counter() - t0)

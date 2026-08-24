"""体验评估模式：DRB busy-period、按需 RBG 分配与 Rel-19 KPI。

这个模块只做系统级第二相（TTI 主循环），不碰信道矩阵。它和
``system.simulate`` 的 legacy 路径并存：legacy 用来复现历史结果，本文实现的
``experience_v2`` 用实际分配的 TBS 给 PF 记账，并允许一个 TTI 服务多个 UE。

物理边界明确写在结果里：RB 功控关闭时使用宽带 MCS/SINR；开启时按实际 grant
bitmap 聚合逐 RBG SINR 并重选 MCS。当前聚合仍是 dB 算术平均而非标定过的
EESM/MIESM。每个单码字 TB 最多一次 IR/CC 重传：空口 MCS、RBG 数、rank 与
TBS 保持不变，BLER 只由预置 NewTx 曲线推导；失败 payload 留队并成为后续新 TB。
"""
from __future__ import annotations

import time
import zlib
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import linkadapt as la
from . import rng as rg
from . import traffic as traffic_cdf

_EPS = 1e-12
_SLOT_INDEX = {"D": 0, "S": 1}


@dataclass(frozen=True)
class TbsLookup:
    """``[D/S, MCS, rank, n_rbg]`` 的 TBS 字节表。

    38.214 的 TBS 量化使 TBS 只近似线性，不能用 ``bytes / bytes_per_rbg``
    反推 RBG 数。``required_rbg`` 用 ``searchsorted`` 找到第一个够用的 TBS；
    建表时验证每条序列单调不减。TBS 量化允许相邻前缀出现平台，平台并不破坏
    ``searchsorted(side="left")`` 的“第一个够用”语义。

    ``values`` 保留从 RBG0 开始的前缀表，兼容等长载波与旧 API；实际 grant
    会用 ``rbg_indices`` 把各组真实 PRB 数相加后查 TBS。这样 Configuration 2
    下 51 RB 的 ``[8,8,8,8,8,8,3]`` 尾组既不会丢，也不会被错算成 8 PRB。
    """

    values: np.ndarray                 # int64 [2, 28, 4, num_rbg]，单位 byte
    num_rbg: int
    rb_per_rbg: int
    s_slot_fraction: float
    rbg_prb_sizes: tuple[int, ...]
    mcs_table: int = 3
    target_bler: float = 0.1

    @classmethod
    def build(cls, num_rbg: int, rb_per_rbg: int,
              s_slot_fraction: float = 0.7, *,
              rbg_prb_sizes: Sequence[int] | None = None,
              mcs_table: int = 3,
              target_bler: float = 0.1) -> TbsLookup:
        for name, value in (("num_rbg", num_rbg), ("rb_per_rbg", rb_per_rbg)):
            if (isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, np.integer)) or int(value) < 1):
                raise ValueError(f"{name} 必须是至少为 1 的整数")
        if (not np.isfinite(s_slot_fraction)
                or not 0.0 < float(s_slot_fraction) <= 1.0):
            raise ValueError("s_slot_fraction 必须是 (0,1] 内的有限数")
        n_rbg = int(num_rbg)
        rb = int(rb_per_rbg)
        if rbg_prb_sizes is None:
            sizes = tuple(rb for _ in range(n_rbg))
        else:
            try:
                raw_sizes = tuple(rbg_prb_sizes)
            except TypeError as exc:
                raise ValueError("rbg_prb_sizes 必须是正整数数组") from exc
            if any(
                isinstance(value, (bool, np.bool_))
                or not isinstance(value, (int, np.integer))
                for value in raw_sizes
            ):
                raise ValueError(
                    "rbg_prb_sizes 每项都必须是正整数，不能是布尔值或小数"
                )
            sizes = tuple(int(value) for value in raw_sizes)
        if len(sizes) != n_rbg or any(value < 1 for value in sizes):
            raise ValueError("rbg_prb_sizes 长度必须等于 num_rbg，且每项为正整数")
        if (isinstance(mcs_table, (bool, np.bool_))
                or not isinstance(mcs_table, (int, np.integer))):
            raise ValueError("mcs_table 必须是整数")
        if int(mcs_table) != 3:
            raise ValueError("experience_v2 的 TBS/BLER 反查只支持 MCS table 3")
        if not np.isfinite(target_bler) or not 0.0 < float(target_bler) < 1.0:
            raise ValueError("target_bler 必须是 (0,1) 内的有限数")
        table = np.zeros((2, 28, 4, n_rbg), dtype=np.int64)
        prefix_prb = np.cumsum(np.asarray(sizes, dtype=np.int64))
        for slot, frac in (("D", 1.0), ("S", float(s_slot_fraction))):
            si = _SLOT_INDEX[slot]
            for mcs in range(28):
                obj = la.MCS_TABLES[int(mcs_table)][mcs]
                for rank in range(1, 5):
                    for n in range(1, n_rbg + 1):
                        n_re = int(int(prefix_prb[n - 1]) * 12 * 12 * frac)
                        table[si, mcs, rank - 1, n - 1] = (
                            la.transport_block_size(
                                n_re, obj.rate, obj.q_m, layers=rank) // 8)
        diff = np.diff(table, axis=-1)
        if diff.size and np.any(diff < 0):
            bad = np.argwhere(diff < 0)[0]
            slot = "D" if int(bad[0]) == 0 else "S"
            raise ValueError(
                "TBS 表出现下降，searchsorted 反查不成立："
                f"slot={slot}, mcs={int(bad[1])}, rank={int(bad[2]) + 1}, "
                f"n_rbg={int(bad[3]) + 1}->{int(bad[3]) + 2}")
        return cls(
            table, n_rbg, rb, float(s_slot_fraction), sizes,
            int(mcs_table), float(target_bler)
        )

    def _tbs_for_prbs(self, slot: str, mcs: int, rank: int, num_prb: int) -> int:
        # row() 同时完成 slot/MCS/rank 的输入校验。
        self.row(slot, mcs, rank)
        if (
            isinstance(num_prb, (bool, np.bool_))
            or not isinstance(num_prb, (int, np.integer))
            or int(num_prb) < 1
        ):
            raise ValueError("num_prb 必须至少为 1")
        frac = 1.0 if str(slot).upper() == "D" else self.s_slot_fraction
        obj = la.MCS_TABLES[self.mcs_table][int(mcs)]
        n_re = int(int(num_prb) * 12 * 12 * frac)
        return int(la.transport_block_size(
            n_re, obj.rate, obj.q_m, layers=int(rank)) // 8)

    def row(self, slot: str, mcs: int, rank: int) -> np.ndarray:
        try:
            si = _SLOT_INDEX[str(slot).upper()]
        except KeyError as exc:
            raise ValueError(f"只支持 D/S 下行时隙，收到 {slot!r}") from exc
        if (isinstance(mcs, (bool, np.bool_))
                or not isinstance(mcs, (int, np.integer)) or not 0 <= int(mcs) <= 27):
            raise ValueError(f"MCS 必须在 0..27，收到 {mcs}")
        if (isinstance(rank, (bool, np.bool_))
                or not isinstance(rank, (int, np.integer)) or not 1 <= int(rank) <= 4):
            raise ValueError(f"rank 必须在 1..4，收到 {rank}")
        return self.values[si, int(mcs), int(rank) - 1]

    def tbs_bytes(self, slot: str, mcs: int, rank: int, n_rbg: int) -> int:
        if (isinstance(n_rbg, (bool, np.bool_))
                or not isinstance(n_rbg, (int, np.integer))
                or not 1 <= int(n_rbg) <= self.num_rbg):
            raise ValueError(f"n_rbg 必须在 1..{self.num_rbg}，收到 {n_rbg}")
        n = int(n_rbg)
        return int(self.row(slot, mcs, rank)[n - 1])

    def tbs_bytes_for_indices(
        self, slot: str, mcs: int, rank: int, indices: Sequence[int]
    ) -> int:
        if not indices:
            raise ValueError("RBG grant 不能为空")
        if any(
            isinstance(index, (bool, np.bool_))
            or not isinstance(index, (int, np.integer))
            for index in indices
        ):
            raise ValueError("RBG index 必须是整数")
        normalized = tuple(int(index) for index in indices)
        if len(set(normalized)) != len(normalized):
            raise ValueError("同一个 RBG 不能在一次 grant 中重复")
        try:
            num_prb = sum(self.rbg_prb_sizes[index] for index in normalized)
        except IndexError as exc:
            raise ValueError(f"RBG index 超出 0..{self.num_rbg - 1}") from exc
        if any(index < 0 for index in normalized):
            raise ValueError("RBG index 不能为负")
        return self._tbs_for_prbs(slot, mcs, rank, num_prb)

    def required_rbg(self, slot: str, mcs: int, rank: int,
                     payload_bytes: int) -> tuple[int, bool]:
        """返回 ``(最小 RBG 数, 本 TTI 能否全部装下)``。"""
        if (isinstance(payload_bytes, (bool, np.bool_))
                or not isinstance(payload_bytes, (int, np.integer))
                or int(payload_bytes) < 1):
            raise ValueError("payload_bytes 必须是至少为 1 的整数")
        need = int(payload_bytes)
        row = self.row(slot, mcs, rank)
        idx = int(np.searchsorted(row, need, side="left"))
        if idx >= self.num_rbg:
            return self.num_rbg, bool(need <= int(row[-1]))
        return idx + 1, True

    def required_rbg_for_indices(
        self, slot: str, mcs: int, rank: int, payload_bytes: int,
        ordered_indices: Sequence[int],
    ) -> tuple[int, bool]:
        """在给定连续分配顺序上找最少组数；每个前缀按真实 PRB 数算 TBS。"""
        if (isinstance(payload_bytes, (bool, np.bool_))
                or not isinstance(payload_bytes, (int, np.integer))
                or int(payload_bytes) < 1):
            raise ValueError("payload_bytes 必须是至少为 1 的整数")
        if any(
            isinstance(index, (bool, np.bool_))
            or not isinstance(index, (int, np.integer))
            for index in ordered_indices
        ):
            raise ValueError("RBG index 必须是整数")
        order = tuple(int(index) for index in ordered_indices)
        if not order:
            raise ValueError("ordered_indices 不能为空")
        row = np.asarray([
            self.tbs_bytes_for_indices(slot, mcs, rank, order[:n])
            for n in range(1, len(order) + 1)
        ], dtype=np.int64)
        # TBS 对 PRB 数单调不减，前缀累加必然不减——单调性由构造保证，
        # 不再留一个永不触发的"下降检查"冒充防线。
        need = int(payload_bytes)
        idx = int(np.searchsorted(row, need, side="left"))
        if idx >= len(order):
            return len(order), bool(need <= int(row[-1]))
        return idx + 1, True

    def as_dict(self) -> dict[str, Any]:
        return {
            "shape": list(self.values.shape),
            "entries": int(self.values.size),
            "unit": "byte",
            "axes": ["slot_class(D/S)", "mcs(0..27)", "rank(1..4)",
                     f"n_rbg(1..{self.num_rbg})"],
            "strictly_increasing": bool(
                self.values.shape[-1] <= 1
                or np.all(np.diff(self.values, axis=-1) > 0)
            ),
            "non_decreasing": bool(
                self.values.shape[-1] <= 1
                or np.all(np.diff(self.values, axis=-1) >= 0)
            ),
            "inverse": "numpy.searchsorted(side='left')",
            "rb_per_rbg": self.rb_per_rbg,
            "rbg_prb_sizes": list(self.rbg_prb_sizes),
            "s_slot_fraction": self.s_slot_fraction,
            "mcs_table": self.mcs_table,
            "target_bler": self.target_bler,
            "mcs_profile": (
                "preset_20b_256qam_table_3" if self.mcs_table == 3
                else f"mcs_table_{self.mcs_table}"
            ),
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
    # 首调度等待在 first_tx 发生时就已经完整可观测，不需要等整个 arrival
    # object 完成。旧实现要求 completion_tti 也存在，会把“已经开始发送、但在
    # 仿真结束时尚未传完”的长等待对象删掉，过载算法反而更容易留下漂亮样本。
    wait = (max(0, item.first_tx_tti - item.arrival_tti) * float(tti_ms)
            if item.first_tx_tti >= 0 else None)
    if item.completion_tti < 0:
        return wait, None, None
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
    head_inclusive_throughput_mbps: float | None = None


def burst_metrics(burst: BusyPeriod, tti_ms: float,
                  small_burst_policy: str = "fractional_slot") -> BurstMetrics:
    """按 28.552 Rel-19 与时延口径计算一个已完成 busy period。

    * 大 burst：从首传开始，体积与时间都排除清空 buffer 的最后一个 ACK piece。
    * 单次首传即成功的小 burst：可选 fractional-slot，时间按
      ``slot × payload/TBVol`` 折算（TBVol−PaddingVol 就是 payload）。
    * 排队等待与完成时延从 arrival 开始，和 3GPP throughput 分开上报。
    * 含头速率与上述吞吐使用完全相同的 payload numerator 和去尾规则，唯一差异是
      denominator 还包含从 busy-period 到达到首次调度的等待时间。
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
            duration_ms = duration_tti * float(tti_ms)
            thp = vol * 8.0 / (duration_ms / 1000.0) / 1e6
            head_thp = vol * 8.0 / ((wait + duration_ms) / 1000.0) / 1e6
            return BurstMetrics(
                thp, "rel19_large_burst", wait, completion, pdb_miss, head_thp)
    if (small_burst_policy == "fractional_slot" and len(events) == 1
            and burst.tx_attempts == 1):
        e = events[0]
        if e.payload_bytes > 0 and e.scheduled_bytes > 0:
            effective_ms = float(tti_ms) * e.payload_bytes / e.scheduled_bytes
            thp = e.payload_bytes * 8.0 / (effective_ms / 1000.0) / 1e6
            head_thp = e.payload_bytes * 8.0 / (
                (wait + effective_ms) / 1000.0) / 1e6
            return BurstMetrics(
                thp, "rel19_fractional_slot", wait, completion, pdb_miss, head_thp)
    return BurstMetrics(None, None, wait, completion, pdb_miss)


@dataclass(frozen=True)
class Allocation:
    tti: int
    snapshot: int
    ue: int
    traffic_class: str
    slot: str
    rbg_indices: tuple[int, ...]
    n_rbg: int
    n_prb: int
    mcs: int
    rank: int
    scheduled_bytes: int
    payload_bytes: int
    acked_bytes: int
    padding_bytes: int
    pf_credit_bytes: int
    queue_bytes_before: int
    required_rbg: int
    fits_in_fullband: bool
    potential_fullband_bytes: int
    pf_average_before_bytes: float
    scheduler_metric: float
    base_tx_sinr_db: float
    mcs_input_sinr_db: float
    sinr_prediction_error_db: float
    olla_before_db: float
    mcs_without_olla: int
    sinr_db: float
    bler: float
    ack: bool
    harq_random_draw: float = 0.0
    transmission_mode: str = "SU"
    mu_group_id: int | None = None
    partner_ue: int | None = None
    corr_loss_db: float = 0.0
    power_loss_db: float = 0.0
    su_olla_before_db: float = 0.0
    mu_olla_before_db: float = 0.0
    su_olla_after_db: float = 0.0
    mu_olla_after_db: float = 0.0
    pair_correlation: float | None = None
    plan_su_useful_bytes: int = 0
    plan_mu_useful_bytes: int = 0
    plan_selected_reason: str = ""
    harq_tx_mode: str = "newtx"
    harq_combining: str | None = None
    bler_lookup_mcs: int | None = None
    bler_lookup_sinr_db: float | None = None
    original_tb_tti: int | None = None
    original_transmission_mode: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["rbg_indices"] = list(self.rbg_indices)
        d["sinr_db"] = round(self.sinr_db, 4)
        d["base_tx_sinr_db"] = round(self.base_tx_sinr_db, 4)
        d["mcs_input_sinr_db"] = round(self.mcs_input_sinr_db, 4)
        d["sinr_prediction_error_db"] = round(self.sinr_prediction_error_db, 4)
        d["olla_before_db"] = round(self.olla_before_db, 4)
        d["olla_before_mcs"] = round(self.olla_before_db, 4)
        d["su_olla_before_mcs"] = round(self.su_olla_before_db, 4)
        d["mu_olla_before_mcs"] = round(self.mu_olla_before_db, 4)
        d["su_olla_after_mcs"] = round(self.su_olla_after_db, 4)
        d["mu_olla_after_mcs"] = round(self.mu_olla_after_db, 4)
        d["olla_domain"] = "continuous_mcs_index"
        d["pf_average_before_bytes"] = round(self.pf_average_before_bytes, 6)
        d["scheduler_metric"] = round(self.scheduler_metric, 6)
        d["bler"] = round(self.bler, 6)
        d["harq_random_draw"] = round(self.harq_random_draw, 6)
        if self.bler_lookup_sinr_db is not None:
            d["bler_lookup_sinr_db"] = round(self.bler_lookup_sinr_db, 4)
        return d


def _trace_sampling_plan(
    *, mode: str, max_points: int, warmup: int, num_tti: int, pattern: str
) -> tuple[set[int], int]:
    """Return deterministic uniform TTI anchors and reserved event capacity.

    ``sampled`` deliberately reserves roughly half the budget for events.  Uniform
    anchors make different algorithms align on the same x coordinates; event rows
    explain divergences such as MU selection, NACK or HARQ without pretending the
    event sample is an unbiased time-series sample.
    """
    resolved = str(mode).lower()
    if resolved == "off":
        return set(), 0
    eligible = [
        tti
        for tti in range(int(warmup), int(num_tti))
        if pattern[tti % len(pattern)] in ("D", "S")
    ]
    if not eligible:
        return set(), 0
    if resolved == "full":
        return set(eligible), 0
    uniform_count = min(len(eligible), max(1, int(max_points) // 2))
    positions = np.linspace(0, len(eligible) - 1, uniform_count, dtype=int)
    uniform = {eligible[int(position)] for position in positions}
    return uniform, max(0, int(max_points) - len(uniform))


def _tti_trace_row(
    *,
    tti: int,
    tti_ms: float,
    slot: str,
    snapshot: int,
    sample_reasons: Sequence[str],
    candidates: Sequence[int],
    blocked_ues: int,
    allocations: Sequence[Allocation],
    backlog_bytes_after: int,
    pf_average_after: np.ndarray,
) -> dict[str, Any]:
    """Collapse one scheduling opportunity while retaining every grant detail."""
    grants = [allocation.as_dict() for allocation in allocations]
    used = sorted({index for allocation in allocations for index in allocation.rbg_indices})
    scheduled_ues = sorted({int(allocation.ue) for allocation in allocations})
    modes = sorted({str(allocation.transmission_mode) for allocation in allocations})
    return {
        "tti": int(tti),
        "time_ms": round(float(tti) * float(tti_ms), 6),
        "slot": str(slot),
        "snapshot": int(snapshot),
        "sample_reasons": list(dict.fromkeys(str(reason) for reason in sample_reasons)),
        "candidate_ues": [int(ue) for ue in candidates],
        "blocked_outage_ues": int(blocked_ues),
        "scheduled_ues": scheduled_ues,
        "scheduled_user_count": len(scheduled_ues),
        "transmission_modes": modes,
        "has_mu": "MU" in modes,
        "occupied_rbg": len(used),
        "used_rbg_indices": used,
        "newtx_count": sum(allocation.harq_tx_mode == "newtx" for allocation in allocations),
        "retx_count": sum(allocation.harq_tx_mode == "retx" for allocation in allocations),
        "nack_count": sum(not allocation.ack for allocation in allocations),
        "scheduled_bytes": sum(int(allocation.scheduled_bytes) for allocation in allocations),
        "payload_bytes": sum(int(allocation.payload_bytes) for allocation in allocations),
        "acked_bytes": sum(int(allocation.acked_bytes) for allocation in allocations),
        "padding_bytes": sum(int(allocation.padding_bytes) for allocation in allocations),
        "backlog_bytes_after": int(backlog_bytes_after),
        "pf_average_after_bytes": {
            str(ue): round(float(pf_average_after[ue]), 6) for ue in scheduled_ues
        },
        "grants": grants,
    }


@dataclass(frozen=True)
class _PlannedGrant:
    """无副作用的 TTI 方案项；只有选中方案后才消费 HARQ 随机数/改队列。"""

    mode: str
    users: tuple[int, ...]
    rbg_indices: tuple[int, ...]
    n_rbg: int
    ranks: tuple[int, ...]
    mcs: tuple[int, ...]
    base_tx_sinr_db: tuple[float, ...]
    mcs_without_olla: tuple[int, ...]
    true_sinr_db: tuple[float, ...]
    corr_loss_db: tuple[float, ...]
    power_loss_db: float
    required_rbg: tuple[int, ...]
    fits_in_fullband: tuple[bool, ...]
    tbs_bytes: tuple[int, ...]
    useful_bytes: tuple[int, ...]
    potential_fullband_bytes: tuple[int, ...]
    pair_correlation: float | None = None


@dataclass(frozen=True)
class _HarqTb:
    """等待唯一一次重传的单码字 TB；空口发送身份在首传 NACK 时冻结。"""

    mcs: int
    rank: int
    n_rbg: int
    tb_bytes: int
    payload_bytes: int
    slot: str
    first_tti: int
    first_mode: str


@dataclass(frozen=True)
class _TtiPlan:
    name: str
    grants: tuple[_PlannedGrant, ...]
    useful_bytes: int
    used_rbg: int
    has_mu: bool
    clears_all_queues: bool


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
        # traffic 顶层流内部再按用途稳定分流。只从父流固定读取一次 entropy；
        # 子流键取名字的 crc32，新增一种抽样不会平移已有子流。这样调包间隔不会
        # 因事件数改变而把后续包长随机数整体错位。
        entropy = [int(x) for x in rng.integers(
            0, 2 ** 32, size=8, dtype=np.uint32)]

        def _child(name: str) -> np.random.Generator:
            key = zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF
            return np.random.default_rng(np.random.SeedSequence(
                entropy, spawn_key=(key,)))

        self._assignment_rng = _child("profile_assignment")
        self._arrival_rng = _child("arrival_count")
        self._packet_rng = _child("packet_size")
        self._interval_rng = _child("interarrival")
        self._phase_rng = _child("initial_phase")
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
        assigned: list[Any] = [None] * self.n_ue
        for c in classes:
            for ue in getattr(c, "ue_ids", ()):
                uid = int(ue)
                if uid >= self.n_ue:
                    raise ValueError(
                        f"TrafficClass {c.name!r} 指定 UE {uid}，但本小区只有 "
                        f"0..{self.n_ue - 1}")
                if assigned[uid] is not None:
                    raise ValueError(f"UE {uid} 被重复分配 traffic profile")
                assigned[uid] = c
        remaining = [u for u, c in enumerate(assigned) if c is None]
        if remaining:
            if float(shares.sum()) <= 0:
                raise ValueError("还有未分配 UE，但 TrafficClass.ue_share 之和为 0")
            shares /= shares.sum()
            raw = shares * len(remaining)
            counts = np.floor(raw).astype(int)
            for i in np.argsort(-(raw - counts))[:len(remaining) - int(counts.sum())]:
                counts[int(i)] += 1
            labels = [
                c for c, n in zip(classes, counts, strict=True) for _ in range(int(n))]
            # 随机排列消除“业务类恰好绑定 UE 编号/远近点”的系统偏差；CRN 两臂会复用它。
            perm = self._assignment_rng.permutation(np.asarray(remaining, dtype=int))
            for pos, ue in enumerate(perm):
                assigned[int(ue)] = labels[pos]
        if any(c is None for c in assigned):
            raise RuntimeError("Traffic profile 分配后仍有 UE 未绑定")
        self.queues = [DrbQueue(u, assigned[u]) for u in range(self.n_ue)]
        self.offered_bytes = 0
        self.arrival_events = 0
        self.unbounded = str(cfg.model) == "full_buffer"
        self._cbr_carry = np.zeros(self.n_ue, dtype=float)
        self._packet_size_sample: list[int] = []
        self._interarrival_sample_ms: list[float] = []
        self._sample_limit = 4096
        global_size_scale = float(getattr(cfg, "packet_size_scale", 1.0))
        global_interval_scale = float(getattr(cfg, "interarrival_scale", 1.0))
        self._profile_samplers: dict[str, dict[str, Any]] = {}
        for c in classes:
            packet_path = (getattr(c, "packet_size_cdf", None)
                           or getattr(cfg, "packet_size_cdf", None))
            interval_path = (getattr(c, "interarrival_cdf", None)
                             or getattr(cfg, "interarrival_cdf", None))
            packet_cdf = (traffic_cdf.load_empirical_cdf(
                packet_path, kind="packet_size", value_unit="byte")
                if packet_path else None)
            interval_unit = str(
                getattr(c, "interarrival_cdf_unit", None)
                or getattr(cfg, "interarrival_cdf_unit", "ms"))
            interval_cdf = (traffic_cdf.load_empirical_cdf(
                interval_path, kind="interarrival", value_unit=interval_unit)
                if interval_path else None)
            size_scale = global_size_scale * float(
                getattr(c, "packet_size_scale", 1.0))
            interval_scale = global_interval_scale * float(
                getattr(c, "interarrival_scale", 1.0))
            self._profile_samplers[str(c.name)] = {
                "packet_cdf": packet_cdf,
                "interval_cdf": interval_cdf,
                "size_scale": size_scale,
                "interval_scale": interval_scale,
                "interval_unit_ms": 1000.0 if interval_unit == "s" else 1.0,
            }
        self._next_arrival_ms = np.full(self.n_ue, np.inf, dtype=float)
        for u, q in enumerate(self.queues):
            sampler = self._profile_samplers[str(q.traffic_class.name)]
            if sampler["interval_cdf"] is not None:
                interval = self._sample_interval_ms(u)
                self._next_arrival_ms[u] = float(self._phase_rng.random()) * interval
        self.profile_summaries = self._profile_summaries(classes)

    def _profile_summaries(self, classes: list[Any]) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for c in classes:
            sampler = self._profile_samplers[str(c.name)]
            packet_cdf = sampler["packet_cdf"]
            interval_cdf = sampler["interval_cdf"]
            mean_bytes = (float(packet_cdf.mean) if packet_cdf is not None
                          else float(c.file_bytes)) * float(sampler["size_scale"])
            if interval_cdf is not None:
                mean_interval_ms = (float(interval_cdf.mean)
                                    * float(sampler["interval_unit_ms"])
                                    * float(sampler["interval_scale"]))
            else:
                rate = float(c.arrival_rate_hz) / float(sampler["interval_scale"])
                mean_interval_ms = 1000.0 / rate if rate > 0 else float("inf")
            offered = (mean_bytes * 8.0 / (mean_interval_ms / 1000.0) / 1e6
                       if np.isfinite(mean_interval_ms) and mean_interval_ms > 0 else 0.0)
            summaries.append({
                "name": str(c.name),
                "assigned_ue_ids": [
                    u for u, q in enumerate(self.queues)
                    if str(q.traffic_class.name) == str(c.name)],
                "explicit_ue_ids": [int(x) for x in getattr(c, "ue_ids", ())],
                "packet_size_scale_effective": float(sampler["size_scale"]),
                "interarrival_scale_effective": float(sampler["interval_scale"]),
                "estimated_mean_packet_bytes": mean_bytes,
                "estimated_mean_interarrival_ms": mean_interval_ms,
                "estimated_offered_mbps_per_ue": offered,
                "packet_size_cdf": (
                    packet_cdf.as_dict() if packet_cdf is not None else None),
                "interarrival_cdf": (
                    interval_cdf.as_dict() if interval_cdf is not None else None),
            })
        return summaries

    def _packet_bytes(self, ue: int) -> int:
        q = self.queues[int(ue)]
        sampler = self._profile_samplers[str(q.traffic_class.name)]
        cdf = sampler["packet_cdf"]
        raw = float(cdf.sample(self._packet_rng).item()) if cdf is not None \
            else float(q.traffic_class.file_bytes)
        n_bytes = max(1, int(np.floor(raw * float(sampler["size_scale"]) + 0.5)))
        if len(self._packet_size_sample) < self._sample_limit:
            self._packet_size_sample.append(n_bytes)
        return n_bytes

    def _sample_interval_ms(self, ue: int) -> float:
        q = self.queues[int(ue)]
        sampler = self._profile_samplers[str(q.traffic_class.name)]
        cdf = sampler["interval_cdf"]
        if cdf is None:
            raise RuntimeError("只有 CDF renewal profile 才能抽包间隔")
        interval = (float(cdf.sample(self._interval_rng).item())
                    * float(sampler["interval_unit_ms"])
                    * float(sampler["interval_scale"]))
        if not np.isfinite(interval) or interval <= 0:
            raise ValueError(f"UE {ue} 抽到非法包间隔 {interval} ms")
        if len(self._interarrival_sample_ms) < self._sample_limit:
            self._interarrival_sample_ms.append(interval)
        return interval

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
            sampler = self._profile_samplers[str(c.name)]
            if sampler["interval_cdf"] is not None:
                now_ms = int(tti) * self.tti_ms
                n_arrivals = 0
                while self._next_arrival_ms[u] <= now_ms + 1e-12:
                    n_each = self._packet_bytes(u)
                    q.arrive(tti, n_each)
                    self.offered_bytes += n_each
                    self.arrival_events += 1
                    n_arrivals += 1
                    if n_arrivals > 100_000:
                        raise RuntimeError(
                            f"UE {u} 单 TTI 到达超过 100000 个；检查 interarrival_scale")
                    self._next_arrival_ms[u] += self._sample_interval_ms(u)
                continue
            lam = max(
                0.0, float(c.arrival_rate_hz)
                / float(sampler["interval_scale"]) * self.tti_ms / 1000.0)
            n_arrivals = int(self._arrival_rng.poisson(lam))
            if n_arrivals:
                # 同一 TTI 的多个文件仍是多个外生到达对象；DRB busy period 会
                # 自然合并，但等待/PDB 不能把它们压成一个大文件。
                for _ in range(n_arrivals):
                    n_each = self._packet_bytes(u)
                    q.arrive(tti, n_each)
                    self.offered_bytes += n_each
                self.arrival_events += n_arrivals

    @property
    def traffic_samples(self) -> dict[str, Any]:
        return {
            "scope": "prefix sample capped at 4096 generated arrival events",
            "substream_scheme": (
                "fixed parent entropy + crc32 named spawn_key for profile_assignment/"
                "arrival_count/packet_size/interarrival/initial_phase"),
            "packet_size_bytes": list(self._packet_size_sample),
            "interarrival_ms": list(self._interarrival_sample_ms),
        }

    def has_data(self, ue: int) -> bool:
        return self.queues[int(ue)].queued_bytes > 0

    def bytes_left(self, ue: int) -> int:
        return int(self.queues[int(ue)].queued_bytes)

    def hol_delay_ms(self, ue: int, tti: int) -> float:
        q = self.queues[int(ue)]
        # HOL 是**队首到达对象**的等待：busy period 起点那个包可能早已传完，
        # 拿 busy 起点算会把长命 busy period 里的新包也钉成满档时延加速。
        if q.items:
            return max(0, int(tti) - int(q.items[0].arrival_tti)) * self.tti_ms
        b = q.active
        return max(0, int(tti) - b.start_tti) * self.tti_ms if b is not None else 0.0

    def transmit(self, ue: int, tti: int, scheduled_bytes: int,
                 payload_bytes: int, *, ack: bool) -> int:
        return self.queues[int(ue)].transmit(
            tti, scheduled_bytes, payload_bytes, ack=ack)

    @property
    def backlog_bytes(self) -> int:
        return int(sum(q.queued_bytes for q in self.queues))


_BLER_CACHE: dict[tuple[str, int, int], float] = {}
_BLER_CACHE_STEP_DB = 0.05

def _select_mcs(sinr_db: float, lookup: TbsLookup) -> int:
    """按链路表自己的 MCS table/目标 BLER 选档。

    :func:`linkadapt.select_mcs` 已缓存门限，因此这里既保留原热点优化，也不再
    把 experience 主循环偷偷锁死在 10% BLER。
    """
    return int(la.select_mcs(
        float(sinr_db), table=int(lookup.mcs_table),
        target_bler=float(lookup.target_bler)
    ).index)


def _bler_lookup(mcs: int, sinr_db: float) -> float:
    value = float(sinr_db)
    # NaN / -Inf 表示链路不可用；+Inf 则应落到预置曲线的高 SINR 尾部。
    # 旧写法把所有非有限值都返回 1.0，导致理想无噪声反例反而 100% NACK，
    # 并且与 legacy 系统路径的边界语义不一致。
    if np.isnan(value) or value == float("-inf"):
        return 1.0
    from . import bler_curves as bc  # noqa: PLC0415

    clipped = float(np.clip(value, -60.0, 60.0))
    # key 带曲线数据指纹：换一套 BLER profile 时旧值必须失效，
    # 否则进程级全局缓存会静默把上一套的数字接着用下去。
    key = (bc.data.DATA_SHA256, int(mcs),
           int(round(clipped / _BLER_CACHE_STEP_DB)))
    if key not in _BLER_CACHE:
        value = float(np.atleast_1d(
            bc.get_curve(key[1], "newtx").evaluate(
                key[2] * _BLER_CACHE_STEP_DB))[0])
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


def _immediate_service_ratio(values: Iterable[float]) -> float | None:
    """Fraction of arrivals with observed first-TX that start in their arrival TTI."""
    v = _finite(values)
    return float(np.mean(np.asarray(v) <= _EPS)) if v else None


def _ordered_candidates(metric: np.ndarray, cand: Sequence[int], tti: int,
                        algorithm: str, n_ue: int,
                        tie_keys: np.ndarray) -> np.ndarray:
    if algorithm == "rr":
        return np.argsort(np.asarray([((u - tti) % n_ue) for u in cand], dtype=float))
    vals = metric.tolist()
    if len(vals) > 1 and len(set(vals)) < len(vals):
        return np.lexsort((np.asarray(tie_keys, dtype=float), -metric))
    return np.argsort(-metric)


def _grant_indices(cursor: int, offset: int, n_rbg: int,
                   total_rbg: int) -> tuple[int, ...]:
    return tuple((int(cursor) + int(offset) + j) % int(total_rbg)
                 for j in range(int(n_rbg)))


def _subset_db(values: np.ndarray, indices: Sequence[int]) -> float:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or not indices:
        raise ValueError("逐 RBG SINR 必须是一维且 grant 不能为空")
    return float(np.mean(arr[np.asarray(indices, dtype=int)]))


def _resource_totals_close(left: float, right: float) -> bool:
    """Machine-precision-aware equality for long-run fractional PRB ledgers.

    S slots contribute fractional RBG equivalents, so two mathematically equal
    ledgers can accumulate in a different order.  A fixed absolute tolerance
    eventually false-fails as the simulation grows; the relative term remains
    twelve orders below one PRB while covering normal float64 summation error.
    """
    return bool(np.isclose(
        float(left), float(right), rtol=1e-12, atol=1e-9))


def _frequency_su_values(
    *, table: Any, snap: int, rank: int, indices: tuple[int, ...],
    olla_db: float, olla_enabled: bool, lookup: TbsLookup, slot: str,
) -> dict[str, Any]:
    if table.sinr_rbg_db is None:
        raise ValueError("RB 功控需要逐 RBG true SINR")
    base_rows = (table.sinr_tx_rbg_db if table.sinr_tx_rbg_db is not None
                 else table.sinr_rbg_db)
    base = _subset_db(base_rows[snap, rank - 1], indices)
    true = _subset_db(table.sinr_rbg_db[snap, rank - 1], indices)
    no_olla_mcs = _select_mcs(base, lookup)
    mcs = (
        int(la.apply_olla_mcs(
            no_olla_mcs, float(olla_db), mcs_table=int(lookup.mcs_table)
        )["final_mcs"])
        if olla_enabled else no_olla_mcs
    )
    tbs = int(lookup.tbs_bytes_for_indices(slot, mcs, rank, indices))
    return {"base": base, "true": true, "mcs": mcs,
            "mcs_without_olla": no_olla_mcs, "tbs": tbs}


def _frequency_su_need(
    *, table: Any, snap: int, rank: int, queue_bytes: int,
    cursor: int, offset: int, num_rbg: int, olla_db: float,
    olla_enabled: bool, lookup: TbsLookup, slot: str,
) -> tuple[int, bool]:
    for n in range(1, int(num_rbg) + 1):
        idx = _grant_indices(cursor, offset, n, num_rbg)
        values = _frequency_su_values(
            table=table, snap=snap, rank=rank, indices=idx,
            olla_db=olla_db, olla_enabled=olla_enabled,
            lookup=lookup, slot=slot)
        if int(values["tbs"]) >= int(queue_bytes):
            return n, True
    return int(num_rbg), False


def _frequency_mu_values(
    *, pair_link: Any, users: tuple[int, int], tables: Sequence[Any], snap: int,
    rank: int, indices: tuple[int, ...], su_olla_db: np.ndarray,
    mu_olla_db: np.ndarray, olla_enabled: bool, lookup: TbsLookup, slot: str,
) -> list[dict[str, Any]]:
    if (pair_link.true_sinr_rbg_db is None
            or pair_link.corr_loss_tx_rbg_db is None):
        raise ValueError("RB 功控 + MU 需要逐 RBG pair SINR/CorrLoss")
    out: list[dict[str, Any]] = []
    for user in users:
        side = int(pair_link.side(user))
        table = tables[user]
        if table.sinr_rbg_db is None:
            raise ValueError("RB 功控 + MU 需要逐 RBG SU SINR")
        base_rows = (table.sinr_tx_rbg_db if table.sinr_tx_rbg_db is not None
                     else table.sinr_rbg_db)
        base = _subset_db(base_rows[snap, rank - 1], indices)
        corr = _subset_db(pair_link.corr_loss_tx_rbg_db[snap, side], indices)
        true = _subset_db(pair_link.true_sinr_rbg_db[snap, side], indices)
        no_olla_sinr = base + corr + float(pair_link.power_loss_db)
        no_olla_mcs = _select_mcs(no_olla_sinr, lookup)
        mcs = (
            int(la.apply_olla_mcs(
                no_olla_mcs,
                float(su_olla_db[user]) + float(mu_olla_db[user]),
                mcs_table=int(lookup.mcs_table),
            )["final_mcs"])
            if olla_enabled else no_olla_mcs
        )
        out.append({
            "base": base, "corr": corr, "true": true, "mcs": mcs,
            "mcs_without_olla": no_olla_mcs,
            "tbs": int(lookup.tbs_bytes_for_indices(slot, mcs, rank, indices)),
        })
    return out


def _build_su_plan(
    ordered_users: Sequence[int], *, queue_bytes: dict[int, int],
    lookup: TbsLookup, slot: str, num_rbg: int,
    rank_of: dict[int, int], mcs_of: dict[int, int],
    base_tx_sinr_of: dict[int, float], mcs_without_olla_of: dict[int, int],
    true_sinr_of: dict[int, float], potential_of: dict[int, int],
    blocked_data: bool, cursor: int, tables: Sequence[Any], snap: int,
    su_olla_db: np.ndarray, olla_enabled: bool, frequency_aware: bool,
    harq_pending: dict[int, _HarqTb] | None = None,
) -> _TtiPlan:
    pending_map = harq_pending or {}
    remaining = int(num_rbg)
    grants: list[_PlannedGrant] = []
    for u0 in ordered_users:
        if remaining <= 0:
            break
        u = int(u0)
        q = int(queue_bytes[u])
        pending = pending_map.get(u)
        rank = int(pending.rank) if pending is not None else int(rank_of[u])
        mcs = int(pending.mcs) if pending is not None else int(mcs_of[u])
        offset = int(num_rbg) - remaining
        if pending is not None:
            if q < int(pending.payload_bytes):
                raise RuntimeError(
                    f"UE {u} HARQ 队列只剩 {q} B，小于冻结 payload "
                    f"{pending.payload_bytes} B")
            need, fits = int(pending.n_rbg), True
            if need > remaining:
                continue
            n = need
            indices = _grant_indices(cursor, offset, n, num_rbg)
            if frequency_aware:
                if tables[u].sinr_rbg_db is None:
                    raise ValueError("RB 功控重传需要逐 RBG true SINR")
                base_rows = (tables[u].sinr_tx_rbg_db
                             if tables[u].sinr_tx_rbg_db is not None
                             else tables[u].sinr_rbg_db)
                base_tx = _subset_db(base_rows[snap, rank - 1], indices)
                true_sinr = _subset_db(
                    tables[u].sinr_rbg_db[snap, rank - 1], indices)
            else:
                base_rows = (tables[u].sinr_tx_db
                             if tables[u].sinr_tx_db is not None
                             else tables[u].sinr_db)
                base_tx = float(base_rows[snap, rank - 1])
                true_sinr = float(tables[u].sinr_db[snap, rank - 1])
            no_olla_mcs = _select_mcs(base_tx, lookup)
            current_tbs = int(
                lookup.tbs_bytes_for_indices(slot, mcs, rank, indices))
            if current_tbs != int(pending.tb_bytes):
                raise RuntimeError(
                    "HARQ 重传的同 MCS/RBG/rank 未复现原 TBS："
                    f"UE {u}, original={pending.tb_bytes} B, current={current_tbs} B")
            tbs = int(pending.tb_bytes)
        elif frequency_aware:
            need, fits = _frequency_su_need(
                table=tables[u], snap=snap, rank=rank, queue_bytes=q,
                cursor=cursor, offset=offset, num_rbg=num_rbg,
                olla_db=float(su_olla_db[u]), olla_enabled=olla_enabled,
                lookup=lookup, slot=slot)
            n = min(int(need), remaining)
            indices = _grant_indices(cursor, offset, n, num_rbg)
            values = _frequency_su_values(
                table=tables[u], snap=snap, rank=rank, indices=indices,
                olla_db=float(su_olla_db[u]), olla_enabled=olla_enabled,
                lookup=lookup, slot=slot)
            mcs = int(values["mcs"])
            tbs = int(values["tbs"])
            base_tx = float(values["base"])
            no_olla_mcs = int(values["mcs_without_olla"])
            true_sinr = float(values["true"])
        else:
            full_order = _grant_indices(cursor, offset, num_rbg, num_rbg)
            need, fits = lookup.required_rbg_for_indices(
                slot, mcs, rank, q, full_order)
            n = min(int(need), remaining)
            indices = _grant_indices(cursor, offset, n, num_rbg)
            tbs = lookup.tbs_bytes_for_indices(slot, mcs, rank, indices)
            base_tx = float(base_tx_sinr_of[u])
            no_olla_mcs = int(mcs_without_olla_of[u])
            true_sinr = float(true_sinr_of[u])
        useful = (int(pending.payload_bytes) if pending is not None
                  else min(q, tbs))
        if useful <= 0:
            continue
        grants.append(_PlannedGrant(
            mode="SU", users=(u,), rbg_indices=indices,
            n_rbg=n, ranks=(rank,), mcs=(mcs,),
            base_tx_sinr_db=(base_tx,),
            mcs_without_olla=(no_olla_mcs,),
            true_sinr_db=(true_sinr,), corr_loss_db=(0.0,),
            power_loss_db=0.0, required_rbg=(int(need),),
            fits_in_fullband=(bool(fits),), tbs_bytes=(int(tbs),),
            useful_bytes=(int(useful),),
            potential_fullband_bytes=(int(potential_of[u]),)))
        remaining -= n
    total_q = int(sum(queue_bytes.values()))
    useful_total = int(sum(sum(g.useful_bytes) for g in grants))
    return _TtiPlan(
        name="SU", grants=tuple(grants), useful_bytes=useful_total,
        used_rbg=int(num_rbg) - remaining, has_mu=False,
        clears_all_queues=(not blocked_data and useful_total == total_q))


def _build_mu_plan(
    ordered_users: Sequence[int], *, queue_bytes: dict[int, int],
    lookup: TbsLookup, slot: str, num_rbg: int,
    rank_of: dict[int, int], mcs_of: dict[int, int],
    base_tx_sinr_of: dict[int, float], mcs_without_olla_of: dict[int, int],
    true_sinr_of: dict[int, float], potential_of: dict[int, int],
    tables: Sequence[Any], snap: int, sched: Any,
    su_olla_db: np.ndarray, mu_olla_db: np.ndarray, blocked_data: bool,
    cursor: int = 0, frequency_aware: bool = False,
) -> _TtiPlan:
    """PF-anchor + SUS 的两用户 rank2 方案；可与 SU grant 混排。"""
    remaining = int(num_rbg)
    ordered = [int(x) for x in ordered_users]
    pending = set(ordered)
    grants: list[_PlannedGrant] = []
    has_mu = False
    corr_thr = float(getattr(sched, "mu_corr_threshold", 0.7))
    mu_rank = int(getattr(sched, "mu_rank_per_user", 2))
    for anchor in ordered:
        if remaining <= 0:
            break
        if anchor not in pending:
            continue
        pending.remove(anchor)
        partner: int | None = None
        pair_link: Any = None
        if int(getattr(sched, "max_mu_users", 2)) >= 2 and mu_rank == 2:
            for v in ordered:
                if v not in pending:
                    continue
                link = getattr(tables[anchor], "mu_links", {}).get(v)
                if link is None:
                    continue
                if float(link.correlation[snap]) <= corr_thr:
                    # OLLA 已经把某个 MU 用户压到 MCS0 曲线仍超过 50% BLER 时，
                    # 继续把 ``select_mcs`` 的下界 0 当成“可调度”会形成永久 NACK
                    # 黑洞。这个判决只用 gNB 可见的 CQI/BF/CorrLoss 与用户级
                    # OLLA，不偷看 true SINR；若当前 pair 不可用，继续尝试下一个
                    # SUS 候选，全部不可用才回退 SU。
                    pair_feasible = True
                    for member in (anchor, v):
                        member_side = int(link.side(member))
                        if tables[member].sinr_tx_db is not None:
                            member_base = float(
                                tables[member].sinr_tx_db[snap, mu_rank - 1])
                        else:
                            member_base = float(
                                tables[member].sinr_db[snap, mu_rank - 1])
                        member_tx_sinr = (
                            member_base
                            + float(link.corr_loss_tx_db[snap, member_side])
                            + float(link.power_loss_db))
                        member_mcs = _select_mcs(member_tx_sinr, lookup)
                        if bool(getattr(sched, "olla_enabled", True)):
                            member_mcs = int(la.apply_olla_mcs(
                                member_mcs,
                                float(su_olla_db[member]) + float(mu_olla_db[member]),
                                mcs_table=int(lookup.mcs_table),
                            )["final_mcs"])
                        if (not np.isfinite(member_tx_sinr)
                                or _bler_lookup(member_mcs, member_tx_sinr) > 0.5):
                            pair_feasible = False
                            break
                    if pair_feasible:
                        partner, pair_link = v, link
                        break

        if partner is None:
            q = int(queue_bytes[anchor])
            rank, mcs = int(rank_of[anchor]), int(mcs_of[anchor])
            offset = int(num_rbg) - remaining
            if frequency_aware:
                need, fits = _frequency_su_need(
                    table=tables[anchor], snap=snap, rank=rank, queue_bytes=q,
                    cursor=cursor, offset=offset, num_rbg=num_rbg,
                    olla_db=float(su_olla_db[anchor]),
                    olla_enabled=bool(getattr(sched, "olla_enabled", True)),
                    lookup=lookup, slot=slot)
                n = min(int(need), remaining)
                indices = _grant_indices(cursor, offset, n, num_rbg)
                values = _frequency_su_values(
                    table=tables[anchor], snap=snap, rank=rank, indices=indices,
                    olla_db=float(su_olla_db[anchor]),
                    olla_enabled=bool(getattr(sched, "olla_enabled", True)),
                    lookup=lookup, slot=slot)
                mcs = int(values["mcs"])
                tbs = int(values["tbs"])
                base_tx = float(values["base"])
                no_olla_mcs = int(values["mcs_without_olla"])
                true_sinr = float(values["true"])
            else:
                full_order = _grant_indices(cursor, offset, num_rbg, num_rbg)
                need, fits = lookup.required_rbg_for_indices(
                    slot, mcs, rank, q, full_order)
                n = min(int(need), remaining)
                indices = _grant_indices(cursor, offset, n, num_rbg)
                tbs = lookup.tbs_bytes_for_indices(slot, mcs, rank, indices)
                base_tx = float(base_tx_sinr_of[anchor])
                no_olla_mcs = int(mcs_without_olla_of[anchor])
                true_sinr = float(true_sinr_of[anchor])
            useful = min(q, tbs)
            if useful > 0:
                grants.append(_PlannedGrant(
                    mode="SU", users=(anchor,), rbg_indices=indices,
                    n_rbg=n, ranks=(rank,), mcs=(mcs,),
                    base_tx_sinr_db=(base_tx,),
                    mcs_without_olla=(no_olla_mcs,),
                    true_sinr_db=(true_sinr,), corr_loss_db=(0.0,),
                    power_loss_db=0.0, required_rbg=(int(need),),
                    fits_in_fullband=(bool(fits),), tbs_bytes=(int(tbs),),
                    useful_bytes=(int(useful),),
                    potential_fullband_bytes=(int(potential_of[anchor]),)))
                remaining -= n
            continue

        pending.remove(partner)
        users = (anchor, partner)
        offset = int(num_rbg) - remaining
        full_order = _grant_indices(cursor, offset, num_rbg, num_rbg)
        ranks: list[int] = []
        mcs_list: list[int] = []
        base_list: list[float] = []
        no_olla_list: list[int] = []
        true_list: list[float] = []
        corr_loss: list[float] = []
        needs: list[int] = []
        fits_list: list[bool] = []
        potentials: list[int] = []
        for u in users:
            side = int(pair_link.side(u))
            if tables[u].sinr_tx_db is not None:
                su_base = float(tables[u].sinr_tx_db[snap, mu_rank - 1])
            else:
                su_base = float(tables[u].sinr_db[snap, mu_rank - 1])
            cl = float(pair_link.corr_loss_tx_db[snap, side])
            pl = float(pair_link.power_loss_db)
            no_olla_sinr = su_base + cl + pl
            no_olla_mcs = _select_mcs(no_olla_sinr, lookup)
            if bool(getattr(sched, "olla_enabled", True)):
                # 用户指定口径：SU OLLA 与 MU OLLA 都是用户级，但状态分开维护。
                mcs_u = int(la.apply_olla_mcs(
                    no_olla_mcs,
                    float(su_olla_db[u]) + float(mu_olla_db[u]),
                    mcs_table=int(lookup.mcs_table),
                )["final_mcs"])
            else:
                mcs_u = no_olla_mcs
            q = int(queue_bytes[u])
            need, fits = lookup.required_rbg_for_indices(
                slot, mcs_u, mu_rank, q, full_order)
            ranks.append(mu_rank)
            mcs_list.append(mcs_u)
            base_list.append(su_base)
            no_olla_list.append(no_olla_mcs)
            true_list.append(float(pair_link.true_sinr_db[snap, side]))
            corr_loss.append(cl)
            needs.append(int(need))
            fits_list.append(bool(fits))
            potentials.append(lookup.tbs_bytes_for_indices(
                slot, mcs_u, mu_rank, full_order))
        if frequency_aware:
            needs = []
            fits_list = []
            for side, u in enumerate(users):
                found = int(num_rbg)
                fit = False
                for candidate_n in range(1, int(num_rbg) + 1):
                    candidate_idx = _grant_indices(
                        cursor, offset, candidate_n, num_rbg)
                    candidate_values = _frequency_mu_values(
                        pair_link=pair_link, users=users, tables=tables,
                        snap=snap, rank=mu_rank, indices=candidate_idx,
                        su_olla_db=su_olla_db, mu_olla_db=mu_olla_db,
                        olla_enabled=bool(getattr(sched, "olla_enabled", True)),
                        lookup=lookup, slot=slot)
                    if int(candidate_values[side]["tbs"]) >= int(queue_bytes[u]):
                        found, fit = candidate_n, True
                        break
                needs.append(found)
                fits_list.append(fit)
        # 同一 RBG bitmap 上同时发两个 TB。分到“先让至少一个队列排空”的最小值，
        # 之后再轮到下一 PF anchor；不会把 MU 误算成两份物理 RBG。
        n = min(min(needs), remaining)
        indices = _grant_indices(cursor, offset, n, num_rbg)
        if frequency_aware:
            actual = _frequency_mu_values(
                pair_link=pair_link, users=users, tables=tables,
                snap=snap, rank=mu_rank, indices=indices,
                su_olla_db=su_olla_db, mu_olla_db=mu_olla_db,
                olla_enabled=bool(getattr(sched, "olla_enabled", True)),
                lookup=lookup, slot=slot)
            mcs_list = [int(x["mcs"]) for x in actual]
            base_list = [float(x["base"]) for x in actual]
            no_olla_list = [int(x["mcs_without_olla"]) for x in actual]
            true_list = [float(x["true"]) for x in actual]
            corr_loss = [float(x["corr"]) for x in actual]
            tbs_list = [int(x["tbs"]) for x in actual]
            full_idx = _grant_indices(cursor, offset, num_rbg, num_rbg)
            full_values = _frequency_mu_values(
                pair_link=pair_link, users=users, tables=tables,
                snap=snap, rank=mu_rank, indices=full_idx,
                su_olla_db=su_olla_db, mu_olla_db=mu_olla_db,
                olla_enabled=bool(getattr(sched, "olla_enabled", True)),
                lookup=lookup, slot=slot)
            potentials = [int(x["tbs"]) for x in full_values]
        else:
            tbs_list = [lookup.tbs_bytes_for_indices(
                slot, mcs_list[k], mu_rank, indices) for k in range(2)]
        useful_list = [min(int(queue_bytes[u]), int(tbs_list[k]))
                       for k, u in enumerate(users)]
        if sum(useful_list) > 0:
            grants.append(_PlannedGrant(
                mode="MU", users=users, rbg_indices=indices,
                n_rbg=int(n), ranks=tuple(ranks),
                mcs=tuple(mcs_list), base_tx_sinr_db=tuple(base_list),
                mcs_without_olla=tuple(no_olla_list), true_sinr_db=tuple(true_list),
                corr_loss_db=tuple(corr_loss), power_loss_db=float(pair_link.power_loss_db),
                required_rbg=tuple(needs), fits_in_fullband=tuple(fits_list),
                tbs_bytes=tuple(int(x) for x in tbs_list),
                useful_bytes=tuple(int(x) for x in useful_list),
                potential_fullband_bytes=tuple(int(x) for x in potentials),
                pair_correlation=float(pair_link.correlation[snap])))
            remaining -= n
            has_mu = True

    total_q = int(sum(queue_bytes.values()))
    useful_total = int(sum(sum(g.useful_bytes) for g in grants))
    return _TtiPlan(
        name="MU", grants=tuple(grants), useful_bytes=useful_total,
        used_rbg=int(num_rbg) - remaining, has_mu=has_mu,
        clears_all_queues=(not blocked_data and useful_total == total_q))


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
    harq_combining = str(getattr(sys_cfg, "harq_combining", "ir")).lower()
    if harq_combining not in ("ir", "cc"):
        raise ValueError("harq_combining 只支持 ir / cc")
    for name in ("olla_speedup", "olla_warmup_speedup"):
        value = float(getattr(sched, name, 1.0))
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"{name} 必须是有限正数")

    if (not np.isfinite(float(sched.olla_min_db))
            or not np.isfinite(float(sched.olla_max_db))
            or float(sched.olla_min_db) >= float(sched.olla_max_db)):
        raise ValueError("olla_min_db / olla_max_db 必须有限且 min < max")
    if bool(sched.mu_enabled):
        if int(getattr(sched, "max_mu_users", 2)) != 2:
            raise ValueError("experience_v2 当前 MU 基线固定两用户配对（max_mu_users=2）")
        if int(getattr(sched, "mu_rank_per_user", 2)) != 2:
            raise ValueError("experience_v2 当前 MU 基线固定每用户 rank2")
        if str(getattr(sched, "mu_precoder", "zf")) not in ("zf", "rzf"):
            raise ValueError("experience_v2 的 MU precoder 只支持 zf / rzf")
    if str(traffic_cfg.model) == "bimodal":
        raise ValueError("experience_v2 不接受按目标 RBG 数反推包长的 bimodal；请用 mixed")
    if str(traffic_cfg.model) not in (
            "mixed", "cdf", "ftp3", "full_buffer", "cbr"):
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

    warmup = int(kpi.resolve_warmup_tti(sys_cfg.tti_ms))
    trace_mode = str(getattr(kpi, "tti_trace_mode", "sampled")).lower()
    trace_max_points = int(getattr(kpi, "tti_trace_max_points", 256))
    if trace_mode not in ("off", "sampled", "full"):
        raise ValueError("tti_trace_mode 只支持 off / sampled / full")
    if trace_max_points < 1:
        raise ValueError("tti_trace_max_points 必须至少为 1")
    if warmup >= int(sys_cfg.num_tti):
        raise ValueError(
            f"预启动 {warmup} TTI 不得覆盖全部仿真时长 {int(sys_cfg.num_tti)} TTI")
    measurement_duration_s = (
        (int(sys_cfg.num_tti) - warmup) * float(sys_cfg.tti_ms) / 1000.0)

    n_ue = len(tables)
    n_snap = int(tables[0].sinr_db.shape[0])
    mcs_table = int(getattr(tables[0], "mcs_table", 3))
    link_target_bler = float(getattr(tables[0], "target_bler", 0.1))
    if not np.isfinite(link_target_bler) or not 0.0 < link_target_bler < 1.0:
        raise ValueError("链路表 target_bler 必须在 (0,1)")
    # OLLA 步长在这里兑现"留空 = 按链路表 target_bler 自动反解"的合同。
    # system.simulate 与 server 也会在更上游解析，但本函数作为公开入口
    # 必须自给自足——否则默认 SchedulerConfig() 直调会在校验处 float(None)。
    _resolve = getattr(sched, "resolved_for_target", None)
    if callable(_resolve):
        sched = _resolve(link_target_bler)
    for name in ("olla_step_up_db", "olla_step_down_db",
                 "mu_olla_step_up_db", "mu_olla_step_down_db"):
        _raw = getattr(sched, name, None)
        if _raw is None:
            raise ValueError(
                f"{name} 为 None 且调度配置不提供 resolved_for_target，"
                "无法按链路表 target_bler 自动反解；请显式给步长")
        _value = float(_raw)
        if not np.isfinite(_value) or _value <= 0:
            raise ValueError(f"{name} 必须是有限正数")
    if n_snap < 1:
        raise ValueError("链路表至少需要一个 snapshot")
    for i, table in enumerate(tables):
        if int(getattr(table, "mcs_table", 3)) != mcs_table:
            raise ValueError(f"UE {i} 的 MCS table 与 UE0 不一致")
        if not np.isclose(
            float(getattr(table, "target_bler", 0.1)), link_target_bler,
            rtol=0.0, atol=1e-12,
        ):
            raise ValueError(f"UE {i} 的 target_bler 与 UE0 不一致")
        if mcs_table != 3:
            raise ValueError("experience_v2 的 TBS/BLER 反查只支持 MCS table 3")
        if table.sinr_db.shape[0] != n_snap:
            raise ValueError(f"UE {i} 的 snapshot 数与 UE0 不一致")
        if table.sinr_db.ndim != 2 or table.sinr_db.shape[1] < 1:
            raise ValueError(f"UE {i} 的 sinr_db 必须是 [snapshot,rank]")
        table_power = str(getattr(table, "power_constraint", "ebf")).lower()
        cfg_power = str(getattr(sys_cfg, "power_constraint", "ebf")).lower()
        if table_power != cfg_power:
            raise ValueError(
                f"UE {i} 链路表功率约束 {table_power} 与系统配置 {cfg_power} 不一致")
        if bool(sched.mu_enabled):
            if table.sinr_db.shape[1] < 2:
                raise ValueError(f"UE {i} 不支持 MU rank2")
            if len(getattr(table, "mu_links", {})) < n_ue - 1:
                raise ValueError(
                    "已启用 MU，但链路表没有完整 pair 数据；"
                    "请用 build_link_tables(..., mu_enabled=True) 预计算")
    # MCS 选择目标与 OLLA 稳态目标是两个显式口径。MCP 默认会把它们对齐；
    # Python API 仍允许研究者故意给 SU/MU 不同目标，结果中的
    # target_bler_by_mode 会完整披露，不能在这里把这种消融误判为非法输入。
    raw_rbg_sizes = getattr(sys_cfg, "rbg_prb_sizes", None)
    rbg_sizes = (
        tuple(int(sys_cfg.rb_per_rbg) for _ in range(int(sys_cfg.num_rbg)))
        if raw_rbg_sizes is None
        else tuple(int(value) for value in raw_rbg_sizes)
    )
    lookup = TbsLookup.build(
        sys_cfg.num_rbg, sys_cfg.rb_per_rbg, s_slot_fraction,
        rbg_prb_sizes=rbg_sizes, mcs_table=mcs_table,
        target_bler=link_target_bler)
    frequency_aware = bool(getattr(
        getattr(sys_cfg, "rb_power_control", None), "enabled", False))
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
    mu_olla_db = np.zeros(n_ue, dtype=float)
    served = np.zeros(n_ue, dtype=float)
    scheduled_tbs = np.zeros(n_ue, dtype=float)
    attempted_payload = np.zeros(n_ue, dtype=float)
    padding = np.zeros(n_ue, dtype=float)
    sched_cnt = np.zeros(n_ue, dtype=int)
    mcs_sum = np.zeros(n_ue, dtype=float)
    rank_sum = np.zeros(n_ue, dtype=float)
    tx_count = np.zeros(n_ue, dtype=int)
    nack_count = np.zeros(n_ue, dtype=int)
    retx_count = np.zeros(n_ue, dtype=int)
    retx_nack_count = np.zeros(n_ue, dtype=int)
    served_measured = np.zeros(n_ue, dtype=float)
    scheduled_tbs_measured = np.zeros(n_ue, dtype=float)
    attempted_payload_measured = np.zeros(n_ue, dtype=float)
    padding_measured = np.zeros(n_ue, dtype=float)
    sched_cnt_measured = np.zeros(n_ue, dtype=int)
    mcs_sum_measured = np.zeros(n_ue, dtype=float)
    rank_sum_measured = np.zeros(n_ue, dtype=float)
    tx_count_measured = np.zeros(n_ue, dtype=int)
    nack_count_measured = np.zeros(n_ue, dtype=int)
    retx_count_measured = np.zeros(n_ue, dtype=int)
    retx_nack_count_measured = np.zeros(n_ue, dtype=int)
    harq_pending: dict[int, _HarqTb] = {}
    # 用户级资源量同时保留两种口径：grant exposure 便于回答“这个 UE 有多少
    # 资源处于 MU 配对”，attribution 把共享 MU RBG 等分给配对 UE，跨 UE 求和
    # 必须精确回到小区物理占用。两者不能混为一个分母。
    user_grant_rbg_equiv = np.zeros(n_ue, dtype=float)
    user_attributed_rbg_equiv = np.zeros(n_ue, dtype=float)
    user_mu_grant_rbg_equiv = np.zeros(n_ue, dtype=float)
    user_grant_prb_equiv = np.zeros(n_ue, dtype=float)
    user_attributed_prb_equiv = np.zeros(n_ue, dtype=float)
    user_mu_grant_prb_equiv = np.zeros(n_ue, dtype=float)
    user_mu_tx_measured = np.zeros(n_ue, dtype=int)

    # OLLA 是否在预启动期收敛，不能只看全程一个 BLER。下面同时保留
    # warmup / 测量前半 / 测量后半，且把 SU、MU 分开；expected_bler 是给定
    # 真实 SINR 与所选 MCS 后的曲线期望，observed_bler 才包含 HARQ 抽样噪声。
    phases = ("warmup", "measurement_first_half", "measurement_second_half")
    modes = ("SU", "MU")
    adaptation_stats: dict[str, dict[str, dict[str, float | int]]] = {
        phase: {
            mode: {
                "tx": 0, "nack": 0, "expected_bler_sum": 0.0,
                "prediction_error_db_sum": 0.0,
                "prediction_error_db_sq_sum": 0.0,
            }
            for mode in modes
        }
        for phase in phases
    }
    mode_tx_by_ue = {mode: np.zeros(n_ue, dtype=int) for mode in modes}
    mode_nack_by_ue = {mode: np.zeros(n_ue, dtype=int) for mode in modes}
    mode_expected_bler_by_ue = {
        mode: np.zeros(n_ue, dtype=float) for mode in modes}
    olla_at_measurement_start: dict[str, list[float]] | None = None
    measurement_mid_tti = warmup + (int(sys_cfg.num_tti) - warmup) // 2

    dl_tti = busy_tti = multi_ue_tti = outage_skips = 0
    dl_tti_full = 0
    mu_tti = mu_rbg = mu_user_tx = 0
    mu_rbg_equiv = 0.0
    mu_prb_equiv = 0.0
    su_decisions = mu_decisions = su_forced_clear = harq_retx_forced_su = 0
    su_plan_useful = mu_plan_useful = 0
    allocated_rbg = scheduled_ues_sum = 0
    allocated_rbg_full = 0
    available_rbg_equiv = allocated_rbg_equiv = 0.0
    available_prb_equiv = allocated_prb_equiv = 0.0
    total_prb = int(sum(lookup.rbg_prb_sizes))
    # ``rbg_hist`` 是每个非零 grant 的大小，不能回答“一个 TTI 总共占了几个 RBG”。
    # 后者必须每个测量窗 DL 调度机会只记一次，而且把 idle TTI 记进 0 桶。
    rbg_hist: list[int] = []
    tti_occupied_rbg_counts = np.zeros(int(sys_cfg.num_rbg) + 1, dtype=np.int64)
    allocation_sample: list[Allocation] = []
    allocation_limit = 256
    allocation_recent: deque[Allocation] = deque(maxlen=allocation_limit)
    trace_uniform_ttis, trace_event_limit = _trace_sampling_plan(
        mode=trace_mode,
        max_points=trace_max_points,
        warmup=warmup,
        num_tti=int(sys_cfg.num_tti),
        pattern=pattern,
    )
    tti_trace_rows: dict[int, dict[str, Any]] = {}
    trace_event_count = 0
    max_rbg_in_tti = overlap_violations = 0
    class_alloc_rbg: dict[str, int] = {}
    class_physical_rbg_share: dict[str, float] = {}
    class_acked: dict[str, int] = {}
    offered_before_measurement = 0
    backlog_at_measurement_start = 0

    for tti in range(int(sys_cfg.num_tti)):
        in_measurement = tti >= warmup
        if tti == warmup:
            offered_before_measurement = int(tr.offered_bytes)
            backlog_at_measurement_start = int(tr.backlog_bytes)
            olla_at_measurement_start = {
                "su_db": [float(x) for x in olla_db],
                "mu_db": [float(x) for x in mu_olla_db],
                "su_mcs": [float(x) for x in olla_db],
                "mu_mcs": [float(x) for x in mu_olla_db],
                "domain": "continuous_mcs_index",
                "pf_average_bytes": [float(x) for x in r_avg],
            }
        # 业务在 UL/保护时隙照样到达；旧实现把 step 放在 continue 后面，会漏掉这些到达。
        tr.step(tti)
        slot = pattern[tti % len(pattern)]
        if slot not in ("D", "S"):
            continue
        dl_tti_full += 1
        dl_tti += int(in_measurement)
        slot_fraction = 1.0 if slot == "D" else float(s_slot_fraction)
        if in_measurement:
            available_rbg_equiv += int(sys_cfg.num_rbg) * slot_fraction
            available_prb_equiv += total_prb * slot_fraction
        snap = (tti // snap_every) % n_snap
        cand = [u for u in range(n_ue) if tr.has_data(u)
                and (u not in harq_pending or harq_pending[u].slot == slot)
                and not (tables[u].outage is not None and tables[u].outage[snap])]
        blocked_this_tti = sum(
            1
            for u in range(n_ue)
            if tr.has_data(u)
            and tables[u].outage is not None
            and tables[u].outage[snap]
        )
        if in_measurement:
            outage_skips += blocked_this_tti
        a = 1.0 / max(int(sched.pf_window_tti), 1)
        if not cand:
            if in_measurement:
                tti_occupied_rbg_counts[0] += 1
            r_avg *= 1.0 - a
            trace_reasons: list[str] = []
            if tti in trace_uniform_ttis:
                trace_reasons.append("full" if trace_mode == "full" else "uniform")
            if (
                in_measurement
                and blocked_this_tti
                and (tti in trace_uniform_ttis or trace_event_count < trace_event_limit)
            ):
                trace_reasons.append("outage")
                if tti not in trace_uniform_ttis:
                    trace_event_count += 1
            if in_measurement and trace_reasons:
                tti_trace_rows[tti] = _tti_trace_row(
                    tti=tti,
                    tti_ms=float(sys_cfg.tti_ms),
                    slot=slot,
                    snapshot=snap,
                    sample_reasons=trace_reasons,
                    candidates=(),
                    blocked_ues=blocked_this_tti,
                    allocations=(),
                    backlog_bytes_after=int(tr.backlog_bytes),
                    pf_average_after=r_avg,
                )
            continue

        rank_of: dict[int, int] = {}
        mcs_of: dict[int, int] = {}
        base_tx_sinr_of: dict[int, float] = {}
        mcs_without_olla_of: dict[int, int] = {}
        potential = np.zeros(len(cand), dtype=float)
        delay_factor = np.ones(len(cand), dtype=float)
        priority_factor = np.ones(len(cand), dtype=float)
        for i, u in enumerate(cand):
            pending = harq_pending.get(u)
            rank = (int(pending.rank) if pending is not None
                    else int(tables[u].best_rank[snap]))
            if pending is not None:
                base_rows = (tables[u].sinr_tx_db
                             if tables[u].sinr_tx_db is not None
                             else tables[u].sinr_db)
                base_tx_sinr = float(base_rows[snap, rank - 1])
                mcs = int(pending.mcs)
                mcs_without_olla = _select_mcs(base_tx_sinr, lookup)
            elif tables[u].sinr_tx_db is not None and sched.olla_enabled:
                # 硬合同：先用 CQI 门限 + BF Gain 的 SINR 反折无 OLLA MCS，
                # 再叠加连续 MCS 域 OLLA，floor 后钳到当前 profile。
                base_tx_sinr = float(tables[u].sinr_tx_db[snap, rank - 1])
                mcs_without_olla = _select_mcs(base_tx_sinr, lookup)
                mcs = int(la.apply_olla_mcs(
                    mcs_without_olla, float(olla_db[u]),
                    mcs_table=int(lookup.mcs_table),
                )["final_mcs"])
            else:
                base_tx_sinr = float(tables[u].sinr_db[snap, rank - 1])
                mcs = int(tables[u].mcs[snap, rank - 1])
                mcs_without_olla = mcs
            rank_of[u], mcs_of[u] = rank, mcs
            base_tx_sinr_of[u] = base_tx_sinr
            mcs_without_olla_of[u] = mcs_without_olla
            potential[i] = (int(pending.tb_bytes) if pending is not None else
                            lookup.tbs_bytes_for_indices(
                                slot, mcs, rank,
                                tuple(range(int(sys_cfg.num_rbg)))))
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
        cand_pos = {int(u): i for i, u in enumerate(cand)}

        metric_order = [int(cand[int(oi)]) for oi in order]
        # HARQ 是同一 TB 的第二次且最后一次机会：同 D/S 类型可发时优先于
        # 新 TB，并按首传时刻排序。这样不会因 PF 重排而无限拖延软缓冲。
        pending_ready = sorted(
            (u for u in metric_order if u in harq_pending),
            key=lambda u: harq_pending[u].first_tti)
        ordered_users = pending_ready + [
            u for u in metric_order if u not in harq_pending]
        queue_bytes = {int(u): tr.bytes_left(int(u)) for u in cand}
        true_sinr_of = {
            int(u): float(tables[int(u)].sinr_db[
                snap, int(rank_of[int(u)]) - 1]) for u in cand}
        potential_of = {int(u): int(potential[cand_pos[int(u)]]) for u in cand}
        blocked_data = any(
            tr.has_data(u) and tables[u].outage is not None
            and bool(tables[u].outage[snap]) for u in range(n_ue))
        blocked_data = blocked_data or any(
            tr.has_data(u) and u in harq_pending
            and harq_pending[u].slot != slot for u in range(n_ue))
        cursor = tti % int(sys_cfg.num_rbg)
        su_plan = _build_su_plan(
            ordered_users, queue_bytes=queue_bytes, lookup=lookup, slot=slot,
            num_rbg=int(sys_cfg.num_rbg), rank_of=rank_of, mcs_of=mcs_of,
            base_tx_sinr_of=base_tx_sinr_of,
            mcs_without_olla_of=mcs_without_olla_of,
            true_sinr_of=true_sinr_of, potential_of=potential_of,
            blocked_data=blocked_data, cursor=cursor, tables=tables, snap=snap,
            su_olla_db=olla_db, olla_enabled=bool(sched.olla_enabled),
            frequency_aware=frequency_aware, harq_pending=harq_pending)
        if bool(sched.mu_enabled) and not pending_ready:
            mu_plan = _build_mu_plan(
                ordered_users, queue_bytes=queue_bytes, lookup=lookup, slot=slot,
                num_rbg=int(sys_cfg.num_rbg), rank_of=rank_of, mcs_of=mcs_of,
                base_tx_sinr_of=base_tx_sinr_of,
                mcs_without_olla_of=mcs_without_olla_of,
                true_sinr_of=true_sinr_of, potential_of=potential_of,
                tables=tables, snap=snap, sched=sched,
                su_olla_db=olla_db, mu_olla_db=mu_olla_db,
                blocked_data=blocked_data, cursor=cursor,
                frequency_aware=frequency_aware)
        else:
            mu_plan = _TtiPlan("MU", tuple(), 0, 0, False, False)

        if in_measurement and not pending_ready:
            su_plan_useful += su_plan.useful_bytes
            mu_plan_useful += mu_plan.useful_bytes
        if pending_ready:
            selected_plan = su_plan
            selected_reason = "HARQ_retx_priority"
            harq_retx_forced_su += int(in_measurement)
        elif su_plan.clears_all_queues:
            selected_plan = su_plan
            selected_reason = "SU_clears_all_queues"
            su_forced_clear += int(in_measurement)
        elif (bool(sched.mu_enabled) and mu_plan.has_mu
              and mu_plan.useful_bytes >= su_plan.useful_bytes):
            selected_plan = mu_plan
            selected_reason = "MU_useful_bytes_ge_SU"
            mu_decisions += int(in_measurement)
        else:
            selected_plan = su_plan
            selected_reason = ("SU_useful_bytes_gt_MU" if mu_plan.has_mu
                               else "SU_no_eligible_MU_pair")
            su_decisions += int(in_measurement)

        used_indices: set[int] = set()
        inst = np.zeros(n_ue, dtype=float)
        users_this_tti = 0
        tti_allocations: list[Allocation] = []
        for group_idx, grant in enumerate(selected_plan.grants):
            n_alloc = int(grant.n_rbg)
            indices = tuple(int(x) for x in grant.rbg_indices)
            grant_prb = int(sum(lookup.rbg_prb_sizes[index] for index in indices))
            grant_prb_equiv = grant_prb * slot_fraction
            if used_indices.intersection(indices):
                overlap_violations += 1
            used_indices.update(indices)
            for side, u in enumerate(grant.users):
                rank, mcs = int(grant.ranks[side]), int(grant.mcs[side])
                tb_bytes = int(grant.tbs_bytes[side])
                queue_before = tr.bytes_left(u)
                pending_tb = harq_pending.get(u)
                is_retx = pending_tb is not None
                if is_retx:
                    assert pending_tb is not None
                    identity = (mcs, n_alloc, rank, tb_bytes)
                    expected_identity = (
                        int(pending_tb.mcs), int(pending_tb.n_rbg),
                        int(pending_tb.rank), int(pending_tb.tb_bytes))
                    if identity != expected_identity:
                        raise RuntimeError(
                            "HARQ 重传身份被改写："
                            f"actual={identity}, expected={expected_identity}")
                    payload = int(pending_tb.payload_bytes)
                else:
                    payload = min(queue_before, tb_bytes)
                if payload <= 0:
                    continue
                sinr = float(grant.true_sinr_db[side])
                if is_retx:
                    retx_eval = la.harq_retransmission_bler(
                        mcs, sinr, combining=harq_combining,
                        table=mcs_table)
                    bler = float(retx_eval["bler"])
                    lookup_mcs = int(retx_eval["lookup_mcs"])
                    lookup_sinr = float(retx_eval["lookup_sinr_db"])
                else:
                    bler = _bler_lookup(mcs, sinr)
                    lookup_mcs = mcs
                    lookup_sinr = sinr
                ack = bool(harq_draw[tti, u] > bler)
                su_olla_before = float(olla_db[u])
                mu_olla_before = float(mu_olla_db[u])
                mcs_input_sinr = float(grant.base_tx_sinr_db[side])
                if grant.mode == "MU" and not is_retx:
                    mcs_input_sinr += (float(grant.corr_loss_db[side])
                                       + float(grant.power_loss_db))
                prediction_error = sinr - mcs_input_sinr
                mode = str(grant.mode)
                if not is_retx:
                    phase = ("warmup" if tti < warmup else
                             "measurement_first_half"
                             if tti < measurement_mid_tti else
                             "measurement_second_half")
                    phase_mode = adaptation_stats[phase][mode]
                    phase_mode["tx"] = int(phase_mode["tx"]) + 1
                    phase_mode["nack"] = int(phase_mode["nack"]) + int(not ack)
                    phase_mode["expected_bler_sum"] = (
                        float(phase_mode["expected_bler_sum"]) + bler)
                    phase_mode["prediction_error_db_sum"] = (
                        float(phase_mode["prediction_error_db_sum"])
                        + prediction_error)
                    phase_mode["prediction_error_db_sq_sum"] = (
                        float(phase_mode["prediction_error_db_sq_sum"])
                        + prediction_error ** 2)
                    mode_tx_by_ue[mode][u] += 1
                    mode_nack_by_ue[mode][u] += int(not ack)
                    mode_expected_bler_by_ue[mode][u] += bler
                acked = tr.transmit(u, tti, tb_bytes, payload, ack=ack)
                if is_retx:
                    retx_count[u] += 1
                    retx_nack_count[u] += int(not ack)
                    if pending_tb is not None and pending_tb.first_tti >= warmup:
                        retx_count_measured[u] += 1
                        retx_nack_count_measured[u] += int(not ack)
                    # 只允许一次重传；失败 payload 留在 DRB 队列，之后成为新 TB。
                    harq_pending.pop(u, None)
                else:
                    tx_count[u] += 1
                    nack_count[u] += int(not ack)
                    if not ack:
                        harq_pending[u] = _HarqTb(
                            mcs=mcs, rank=rank, n_rbg=n_alloc,
                            tb_bytes=tb_bytes, payload_bytes=payload,
                            slot=slot, first_tti=tti,
                            first_mode=str(grant.mode))
                pad = max(0, tb_bytes - payload)
                if accounting == "scheduled_tbs":
                    credit = tb_bytes
                elif accounting == "acked_goodput":
                    credit = acked
                else:
                    credit = lookup.tbs_bytes_for_indices(
                        slot, mcs, rank, tuple(range(int(sys_cfg.num_rbg))))
                inst[u] += credit
                sched_cnt[u] += 1
                mcs_sum[u] += mcs
                rank_sum[u] += rank
                served[u] += acked
                scheduled_tbs[u] += tb_bytes
                attempted_payload[u] += payload
                padding[u] += pad
                if in_measurement:
                    served_measured[u] += acked
                    scheduled_tbs_measured[u] += tb_bytes
                    attempted_payload_measured[u] += payload
                    padding_measured[u] += pad
                    if not is_retx:
                        tx_count_measured[u] += 1
                        nack_count_measured[u] += int(not ack)
                    sched_cnt_measured[u] += 1
                    mcs_sum_measured[u] += mcs
                    rank_sum_measured[u] += rank
                    grant_equiv = n_alloc * slot_fraction
                    user_grant_rbg_equiv[u] += grant_equiv
                    user_attributed_rbg_equiv[u] += (
                        grant_equiv / max(len(grant.users), 1))
                    user_grant_prb_equiv[u] += grant_prb_equiv
                    user_attributed_prb_equiv[u] += (
                        grant_prb_equiv / max(len(grant.users), 1))
                    if grant.mode == "MU":
                        user_mu_grant_rbg_equiv[u] += grant_equiv
                        user_mu_grant_prb_equiv[u] += grant_prb_equiv
                        user_mu_tx_measured[u] += 1
                if sched.olla_enabled and not is_retx:
                    speed = (float(getattr(sched, "olla_warmup_speedup", 1.0))
                             if not in_measurement
                             else float(getattr(sched, "olla_speedup", 1.0)))
                    if grant.mode == "MU":
                        if ack:
                            mu_olla_db[u] = min(
                                mu_olla_db[u]
                                + float(sched.mu_olla_step_up_db) * speed,
                                sched.olla_max_db)
                        else:
                            mu_olla_db[u] = max(
                                mu_olla_db[u]
                                - float(sched.mu_olla_step_down_db) * speed,
                                sched.olla_min_db)
                    elif ack:
                        olla_db[u] = min(
                            olla_db[u] + float(sched.olla_step_up_db) * speed,
                            sched.olla_max_db)
                    else:
                        olla_db[u] = max(
                            olla_db[u] - float(sched.olla_step_down_db) * speed,
                            sched.olla_min_db)
                cls = str(tr.queues[u].traffic_class.name)
                if in_measurement:
                    class_alloc_rbg[cls] = class_alloc_rbg.get(cls, 0) + n_alloc
                    class_physical_rbg_share[cls] = (
                        class_physical_rbg_share.get(cls, 0.0)
                        + n_alloc / max(len(grant.users), 1))
                    class_acked[cls] = class_acked.get(cls, 0) + acked
                pos = cand_pos[u]
                alloc = Allocation(
                    tti=tti, snapshot=snap, ue=u, traffic_class=cls, slot=slot,
                    rbg_indices=indices, n_rbg=n_alloc, n_prb=grant_prb,
                    mcs=mcs, rank=rank,
                    scheduled_bytes=tb_bytes, payload_bytes=payload,
                    acked_bytes=acked, padding_bytes=pad, pf_credit_bytes=int(credit),
                    queue_bytes_before=int(queue_before),
                    required_rbg=int(grant.required_rbg[side]),
                    fits_in_fullband=bool(grant.fits_in_fullband[side]),
                    potential_fullband_bytes=int(grant.potential_fullband_bytes[side]),
                    pf_average_before_bytes=float(r_avg[u]),
                    scheduler_metric=float(metric[pos]),
                    base_tx_sinr_db=float(grant.base_tx_sinr_db[side]),
                    mcs_input_sinr_db=mcs_input_sinr,
                    sinr_prediction_error_db=prediction_error,
                    olla_before_db=su_olla_before,
                    mcs_without_olla=int(grant.mcs_without_olla[side]),
                    sinr_db=sinr, bler=bler, ack=ack,
                    harq_random_draw=float(harq_draw[tti, u]),
                    transmission_mode=grant.mode,
                    mu_group_id=(tti * 100 + group_idx if grant.mode == "MU" else None),
                    partner_ue=(grant.users[1 - side] if grant.mode == "MU" else None),
                    corr_loss_db=float(grant.corr_loss_db[side]),
                    power_loss_db=float(grant.power_loss_db),
                    su_olla_before_db=su_olla_before,
                    mu_olla_before_db=mu_olla_before,
                    su_olla_after_db=float(olla_db[u]),
                    mu_olla_after_db=float(mu_olla_db[u]),
                    pair_correlation=grant.pair_correlation,
                    plan_su_useful_bytes=su_plan.useful_bytes,
                    plan_mu_useful_bytes=mu_plan.useful_bytes,
                    plan_selected_reason=selected_reason,
                    harq_tx_mode=("retx" if is_retx else "newtx"),
                    harq_combining=(harq_combining
                                    if is_retx else None),
                    bler_lookup_mcs=lookup_mcs,
                    bler_lookup_sinr_db=lookup_sinr,
                    original_tb_tti=(int(pending_tb.first_tti)
                                     if pending_tb is not None else tti),
                    original_transmission_mode=(
                        str(pending_tb.first_mode)
                        if pending_tb is not None else str(grant.mode)))
                tti_allocations.append(alloc)
                if in_measurement:
                    if len(allocation_sample) < allocation_limit:
                        allocation_sample.append(alloc)
                    allocation_recent.append(alloc)
                    mu_user_tx += int(grant.mode == "MU")
                users_this_tti += 1
            allocated_rbg_full += n_alloc
            if in_measurement:
                rbg_hist.append(n_alloc)
                allocated_rbg += n_alloc
                allocated_rbg_equiv += n_alloc * slot_fraction
                allocated_prb_equiv += grant_prb_equiv
                if grant.mode == "MU":
                    mu_rbg += n_alloc
                    mu_rbg_equiv += n_alloc * slot_fraction
                    mu_prb_equiv += grant_prb_equiv
        if users_this_tti and in_measurement:
            busy_tti += 1
            scheduled_ues_sum += users_this_tti
            multi_ue_tti += int(users_this_tti > 1)
            mu_tti += int(any(g.mode == "MU" for g in selected_plan.grants))
            max_rbg_in_tti = max(max_rbg_in_tti, len(used_indices))
        if in_measurement:
            tti_occupied_rbg_counts[len(used_indices)] += 1
        r_avg = (1.0 - a) * r_avg + a * inst
        if in_measurement and trace_mode != "off":
            event_reasons: list[str] = []
            if any(allocation.transmission_mode == "MU" for allocation in tti_allocations):
                event_reasons.append("mu")
            if any(not allocation.ack for allocation in tti_allocations):
                event_reasons.append("nack")
            if any(allocation.harq_tx_mode == "retx" for allocation in tti_allocations):
                event_reasons.append("retx")
            if len({allocation.ue for allocation in tti_allocations}) > 1:
                event_reasons.append("multi_ue")
            if blocked_this_tti:
                event_reasons.append("outage")
            if not tti_allocations:
                event_reasons.append("no_grant")

            reasons: list[str] = []
            if tti in trace_uniform_ttis:
                reasons.append("full" if trace_mode == "full" else "uniform")
            capture_event = bool(event_reasons) and (
                tti in trace_uniform_ttis or trace_event_count < trace_event_limit
            )
            if capture_event:
                reasons.extend(event_reasons)
                if tti not in trace_uniform_ttis:
                    trace_event_count += 1
            if reasons:
                tti_trace_rows[tti] = _tti_trace_row(
                    tti=tti,
                    tti_ms=float(sys_cfg.tti_ms),
                    slot=slot,
                    snapshot=snap,
                    sample_reasons=reasons,
                    candidates=cand,
                    blocked_ues=blocked_this_tti,
                    allocations=tti_allocations,
                    backlog_bytes_after=int(tr.backlog_bytes),
                    pf_average_after=r_avg,
                )
        if progress and tti % 5000 == 0:
            progress(tti, int(sys_cfg.num_tti))

    pending_measured = np.asarray([
        int(u in harq_pending and harq_pending[u].first_tti >= warmup)
        for u in range(n_ue)
    ], dtype=int)
    users: list[dict[str, Any]] = []
    all_wait: list[float] = []                 # arrival object, FIFO
    all_completion: list[float] = []           # arrival object, FIFO
    all_busy_wait: list[float] = []            # DRB busy period 首调度
    all_busy_completion: list[float] = []      # DRB busy period 排空
    all_thp: list[float] = []
    all_head_thp: list[float] = []
    small_thp: list[float] = []
    small_head_thp: list[float] = []
    large_thp: list[float] = []
    large_head_thp: list[float] = []
    large_user_thp: list[float] = []
    large_user_head_thp: list[float] = []
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
    queue_wait_observed_objects = 0
    queue_wait_right_censored_objects = 0
    pdb_decidable_objects = 0
    pdb_right_censored_objects = 0
    deadline_missed_incomplete_objects = 0
    for u, q in enumerate(tr.queues):
        done = [b for b in q.done if b.start_tti >= warmup]
        metrics = [burst_metrics(b, sys_cfg.tti_ms, small_policy) for b in done]
        thp = [m.throughput_mbps for m in metrics if m.throughput_mbps is not None]
        head_thp = [m.head_inclusive_throughput_mbps for m in metrics
                    if m.head_inclusive_throughput_mbps is not None]
        busy_waits = [m.queue_wait_ms for m in metrics if m.queue_wait_ms is not None]
        busy_completes = [m.completion_delay_ms for m in metrics
                          if m.completion_delay_ms is not None]
        done_items = [item for item in q.done_items if item.arrival_tti >= warmup]
        incomplete_items = [item for item in q.items if item.arrival_tti >= warmup]
        done_item_metrics = [arrival_item_metrics(
            item, sys_cfg.tti_ms, float(q.traffic_class.pdb_ms))
            for item in done_items]
        incomplete_item_metrics = [arrival_item_metrics(
            item, sys_cfg.tti_ms, float(q.traffic_class.pdb_ms))
            for item in incomplete_items]
        waits = [x[0] for x in [*done_item_metrics, *incomplete_item_metrics]
                 if x[0] is not None]
        completes = [x[1] for x in done_item_metrics if x[1] is not None]
        completed_pflags = [x[2] for x in done_item_metrics if x[2] is not None]
        wait_right_censored = [
            item for item in incomplete_items if item.first_tx_tti < 0]
        pdb_ms = float(q.traffic_class.pdb_ms)
        # 未完成对象不能一律从 PDB 分母消失。仿真结束时已经到达/越过 deadline
        # 的对象是“确定 miss”；尚未到 deadline 的才是右删失，不能武断判成成功。
        overdue_incomplete = [
            item for item in incomplete_items
            if pdb_ms > 0
            and (int(sys_cfg.num_tti) - int(item.arrival_tti))
            * float(sys_cfg.tti_ms) >= pdb_ms
        ]
        right_censored = [
            item for item in incomplete_items
            if pdb_ms > 0 and item not in overdue_incomplete
        ]
        pflags = [*completed_pflags, *([True] * len(overdue_incomplete))]
        svals = [m.throughput_mbps for m in metrics
                 if m.throughput_kind == "rel19_fractional_slot"
                 and m.throughput_mbps is not None]
        shead = [m.head_inclusive_throughput_mbps for m in metrics
                 if m.throughput_kind == "rel19_fractional_slot"
                 and m.head_inclusive_throughput_mbps is not None]
        lvals = [m.throughput_mbps for m in metrics
                 if m.throughput_kind == "rel19_large_burst"
                 and m.throughput_mbps is not None]
        lhead = [m.head_inclusive_throughput_mbps for m in metrics
                 if m.throughput_kind == "rel19_large_burst"
                 and m.head_inclusive_throughput_mbps is not None]
        all_wait.extend(float(x) for x in waits)
        all_completion.extend(float(x) for x in completes)
        all_busy_wait.extend(float(x) for x in busy_waits)
        all_busy_completion.extend(float(x) for x in busy_completes)
        all_thp.extend(float(x) for x in thp)
        all_head_thp.extend(float(x) for x in head_thp)
        small_thp.extend(float(x) for x in svals)
        small_head_thp.extend(float(x) for x in shead)
        large_thp.extend(float(x) for x in lvals)
        large_head_thp.extend(float(x) for x in lhead)
        pdb_flags.extend(bool(x) for x in pflags)
        completed_bursts += len(done)
        completed_arrival_objects += len(done_items)
        queue_wait_observed_objects += len(waits)
        queue_wait_right_censored_objects += len(wait_right_censored)
        pdb_decidable_objects += len(pflags)
        pdb_right_censored_objects += len(right_censored)
        deadline_missed_incomplete_objects += len(overdue_incomplete)
        measured_bursts += len(thp)
        ue_thp = float(np.mean(thp)) if thp else 0.0
        ue_head_thp = float(np.mean(head_thp)) if head_thp else 0.0
        is_small_class = bool(q.traffic_class.is_small)
        # 无界话务（full buffer）没有外生到达对象，busy period 永不结束，
        # 体验速率对它无定义——只有真实到达/未完成对象才让 UE 有资格进体验 KPI。
        experience_eligible = bool(done_items or incomplete_items)
        if not is_small_class and experience_eligible:
            large_user_thp.append(ue_thp)
            large_user_head_thp.append(ue_head_thp)
        target_wait = small_wait if is_small_class else large_wait
        target_completion = small_completion if is_small_class else large_completion
        target_pdb = small_pdb_flags if is_small_class else large_pdb_flags
        target_wait.extend(float(x) for x in waits)
        target_completion.extend(float(x) for x in completes)
        target_pdb.extend(bool(x) for x in pflags)
        cls_name = str(q.traffic_class.name)
        cls_row = class_arrival_kpis.setdefault(
            cls_name, {"wait": [], "completion": [], "pdb": [], "completed": 0,
                       "wait_observed": 0, "wait_right_censored": 0,
                       "pdb_decidable": 0, "right_censored": 0,
                       "deadline_missed_incomplete": 0,
                       "is_small": is_small_class})
        cls_row["wait"].extend(float(x) for x in waits)
        cls_row["completion"].extend(float(x) for x in completes)
        cls_row["pdb"].extend(bool(x) for x in pflags)
        cls_row["completed"] += len(done_items)
        cls_row["wait_observed"] += len(waits)
        cls_row["wait_right_censored"] += len(wait_right_censored)
        cls_row["pdb_decidable"] += len(pflags)
        cls_row["right_censored"] += len(right_censored)
        cls_row["deadline_missed_incomplete"] += len(overdue_incomplete)
        users.append({
            "ue": u,
            "traffic_class": str(q.traffic_class.name),
            "geo_sinr_db": round(float(tables[u].geo_sinr_db), 4),
            "iot_db": round(float(tables[u].iot_db), 4),
            "experienced_mbps": ue_thp if experience_eligible else None,
            "head_inclusive_experienced_mbps": (
                ue_head_thp if experience_eligible else None),
            "experience_kpi_eligible": experience_eligible,
            "experience_kpi_measured": bool(thp),
            "large_burst_experienced_mbps": _mean(lvals),
            "large_burst_head_inclusive_mbps": _mean(lhead),
            "small_burst_fractional_mbps": _mean(svals),
            "small_burst_head_inclusive_mbps": _mean(shead),
            "served_mbps": float(
                served_measured[u] * 8 / max(measurement_duration_s, _EPS) / 1e6),
            "bursts": len(thp),
            "completed_bursts": len(done),
            "completed_arrival_objects": len(done_items),
            "queue_wait_observed_arrival_objects": len(waits),
            "queue_wait_right_censored_arrival_objects": len(wait_right_censored),
            "queue_wait_observed_share": float(
                len(waits) / max(len(waits) + len(wait_right_censored), 1)),
            "pdb_decidable_arrival_objects": len(pflags),
            "pdb_right_censored_arrival_objects": len(right_censored),
            "deadline_missed_incomplete_arrival_objects": len(overdue_incomplete),
            "arrival_queue_wait_p95_ms": _pct(waits, 95),
            "first_packet_delay_ms_p50": _pct(waits, 50),
            "first_packet_delay_ms_mean": _mean(waits),
            "first_packet_delay_ms_p95": _pct(waits, 95),
            "first_packet_delay_ms_p99": _pct(waits, 99),
            "first_packet_delay_observed_share": float(
                len(waits) / max(len(waits) + len(wait_right_censored), 1)),
            "arrival_completion_delay_p95_ms": _pct(completes, 95),
            "arrival_pdb_miss_ratio": float(np.mean(pflags)) if pflags else None,
            "busy_period_first_schedule_wait_p95_ms": _pct(busy_waits, 95),
            "busy_period_completion_delay_p95_ms": _pct(busy_completes, 95),
            # 兼容字段从 experience_v2 起明确指 FIFO arrival object，不再拿
            # busy period 的首个 arrival 代替期间所有小包。
            "queue_wait_p95_ms": _pct(waits, 95),
            "completion_delay_p95_ms": _pct(completes, 95),
            "pdb_miss_ratio": float(np.mean(pflags)) if pflags else None,
            "avg_mcs": float(mcs_sum_measured[u] / max(sched_cnt_measured[u], 1)),
            "avg_rank": float(rank_sum_measured[u] / max(sched_cnt_measured[u], 1)),
            "bler_first_tx": float(
                nack_count_measured[u] / max(tx_count_measured[u], 1)),
            "newtx_attempt_bler": float(
                nack_count_measured[u] / max(tx_count_measured[u], 1)),
            "retx_bler": float(
                retx_nack_count_measured[u]
                / max(retx_count_measured[u], 1)),
            "residual_bler": float(
                retx_nack_count_measured[u]
                / max(tx_count_measured[u] - pending_measured[u], 1)),
            "pending_harq_tb_at_end": int(pending_measured[u]),
            "sched_tti": int(sched_cnt_measured[u]),
            "grant_prb_equivalent": float(
                user_grant_prb_equiv[u]),
            "allocated_prb_equivalent_attributed": float(
                user_attributed_prb_equiv[u]),
            "cell_used_prb_attribution_share": float(
                user_attributed_prb_equiv[u]
                / max(allocated_prb_equiv, _EPS)),
            "mu_paired_prb_equivalent": float(
                user_mu_grant_prb_equiv[u]),
            "mu_paired_prb_share_of_user_used": float(
                user_mu_grant_prb_equiv[u]
                / max(user_grant_prb_equiv[u], _EPS)),
            "mu_tx_share": float(
                user_mu_tx_measured[u] / max(sched_cnt_measured[u], 1)),
            "retx_tti": int(retx_count_measured[u]),
            "queued_bytes": int(q.queued_bytes),
        })

    # 只平均“有完成 burst 的 UE”会把完全饿死的 UE 从分布中删掉，算法越差反而
    # 越容易留下漂亮样本。只要观测窗内有外生到达，该 UE 就进入 zero-inclusive
    # 分布；没有到达的空闲 UE 不计，避免把话务随机性误当调度失败。
    user_exp = [float(u["experienced_mbps"]) for u in users
                if bool(u["experience_kpi_eligible"])]
    user_head_exp = [float(u["head_inclusive_experienced_mbps"]) for u in users
                     if bool(u["experience_kpi_eligible"])]
    user_exp_completed_only = [float(u["experienced_mbps"]) for u in users
                               if bool(u["experience_kpi_measured"])]
    offered = int(tr.offered_bytes)
    offered_measured = max(0, offered - int(offered_before_measurement))
    backlog = int(tr.backlog_bytes)
    acked_total = int(np.sum(served))
    acked_total_measured = int(np.sum(served_measured))
    acct_error = (0.0 if tr.unbounded else
                  abs(acked_total + backlog - offered) / max(offered, 1) * 100.0)
    measurement_balance_error_bytes = (None if tr.unbounded else int(
        backlog_at_measurement_start + offered_measured
        - acked_total_measured - backlog))
    measurement_acct_error = (None if tr.unbounded else
        abs(float(measurement_balance_error_bytes))
        / max(backlog_at_measurement_start + offered_measured, 1) * 100.0)
    sched_total = float(np.sum(scheduled_tbs_measured))
    attempted_total = float(np.sum(attempted_payload_measured))
    padding_total = float(np.sum(padding_measured))
    tx_total = int(np.sum(tx_count_measured))
    nack_total = int(np.sum(nack_count_measured))
    retx_total = int(np.sum(retx_count_measured))
    retx_nack_total = int(np.sum(retx_nack_count_measured))
    pending_harq_total = int(np.sum(pending_measured))
    observed_harq_total = max(tx_total - pending_harq_total, 0)

    def _finalize_adaptation_stats() -> dict[str, Any]:
        result: dict[str, Any] = {}
        for phase, by_mode in adaptation_stats.items():
            result[phase] = {}
            for mode, row in by_mode.items():
                n = int(row["tx"])
                mean_error = float(row["prediction_error_db_sum"]) / max(n, 1)
                mean_sq = float(row["prediction_error_db_sq_sum"]) / max(n, 1)
                result[phase][mode] = {
                    "tx": n,
                    "nack": int(row["nack"]),
                    "observed_bler": (float(row["nack"]) / n if n else None),
                    "expected_bler": (
                        float(row["expected_bler_sum"]) / n if n else None),
                    "sinr_prediction_error_db_mean": mean_error if n else None,
                    "sinr_prediction_error_db_rmse": (
                        float(np.sqrt(max(mean_sq, 0.0))) if n else None),
                }
        return result

    adaptation_summary = _finalize_adaptation_stats()
    measured_mode = {
        mode: {
            "tx": int(adaptation_summary["measurement_first_half"][mode]["tx"]
                      + adaptation_summary["measurement_second_half"][mode]["tx"]),
            "nack": int(adaptation_summary["measurement_first_half"][mode]["nack"]
                        + adaptation_summary["measurement_second_half"][mode]["nack"]),
        }
        for mode in modes
    }
    for _mode, row in measured_mode.items():
        row["bler"] = float(row["nack"] / max(row["tx"], 1))

    target_by_mode = {
        "SU": float(sched.olla_step_up_db) / (
            float(sched.olla_step_up_db) + float(sched.olla_step_down_db)),
        "MU": float(sched.mu_olla_step_up_db) / (
            float(sched.mu_olla_step_up_db) + float(sched.mu_olla_step_down_db)),
    }
    convergence_tol = 0.05
    convergence_min_tx = 100
    convergence: dict[str, Any] = {
        "target_bler": target_by_mode["SU"],
        "target_bler_by_mode": target_by_mode,
        "absolute_tolerance": convergence_tol,
        "min_tx_per_half": convergence_min_tx,
        "modes": {},
    }
    active_modes_ok: list[bool] = []
    all_mode_tx = max(sum(int(row["tx"]) for row in measured_mode.values()), 1)
    for mode in modes:
        olla_target = target_by_mode[mode]
        first = adaptation_summary["measurement_first_half"][mode]
        second = adaptation_summary["measurement_second_half"][mode]
        n_first, n_second = int(first["tx"]), int(second["tx"])
        share = (n_first + n_second) / all_mode_tx
        p_first, p_second = first["expected_bler"], second["expected_bler"]
        if not bool(sched.olla_enabled):
            status, passed = "disabled", True
        elif n_first + n_second == 0:
            status, passed = "inactive", True
        elif share < 0.01:
            status, passed = "negligible_under_1pct_of_user_grants", True
        elif n_first < convergence_min_tx or n_second < convergence_min_tx:
            status, passed = "insufficient_samples", False
        else:
            assert p_first is not None and p_second is not None
            passed = (abs(float(p_second) - olla_target) <= convergence_tol
                      and abs(float(p_second) - float(p_first)) <= convergence_tol)
            status = "converged" if passed else "not_converged"
        convergence["modes"][mode] = {
            "status": status,
            "passed": bool(passed),
            "target_bler": float(olla_target),
            "measurement_user_grant_share": float(share),
            "first_half_expected_bler": p_first,
            "second_half_expected_bler": p_second,
            "first_half_tx": n_first,
            "second_half_tx": n_second,
        }
        active_modes_ok.append(bool(passed))
    convergence["all_active_modes_converged"] = bool(all(active_modes_ok))
    class_arrival_summary = {
        name: {
            "is_small": bool(row["is_small"]),
            "completed_arrival_objects": int(row["completed"]),
            "queue_wait_observed_arrival_objects": int(row["wait_observed"]),
            "queue_wait_right_censored_arrival_objects": int(
                row["wait_right_censored"]),
            "queue_wait_observed_share": float(
                row["wait_observed"]
                / max(row["wait_observed"] + row["wait_right_censored"], 1)),
            "pdb_decidable_arrival_objects": int(row["pdb_decidable"]),
            "pdb_right_censored_arrival_objects": int(row["right_censored"]),
            "deadline_missed_incomplete_arrival_objects": int(
                row["deadline_missed_incomplete"]),
            "queue_wait_ms_p50": _pct(row["wait"], 50),
            "queue_wait_ms_mean": _mean(row["wait"]),
            "queue_wait_ms_p95": _pct(row["wait"], 95),
            "queue_wait_ms_p99": _pct(row["wait"], 99),
            "first_packet_delay_ms_p50": _pct(row["wait"], 50),
            "first_packet_delay_ms_mean": _mean(row["wait"]),
            "first_packet_delay_ms_p95": _pct(row["wait"], 95),
            "first_packet_delay_ms_p99": _pct(row["wait"], 99),
            "first_packet_delay_observed_share": float(
                row["wait_observed"]
                / max(row["wait_observed"] + row["wait_right_censored"], 1)),
            "immediate_service_ratio": _immediate_service_ratio(row["wait"]),
            "completion_delay_ms_p50": _pct(row["completion"], 50),
            "completion_delay_ms_p95": _pct(row["completion"], 95),
            "completion_delay_ms_p99": _pct(row["completion"], 99),
            "pdb_miss_ratio": (float(np.mean(row["pdb"])) if row["pdb"] else None),
        }
        for name, row in class_arrival_kpis.items()
    }
    tti_hist_total = int(np.sum(tti_occupied_rbg_counts))
    if tti_hist_total != int(dl_tti):
        raise RuntimeError(
            "TTI RBG occupancy 对账失败："
            f"hist={tti_hist_total}, measurement_dl_tti={int(dl_tti)}")
    attributed_prb_total = float(np.sum(user_attributed_prb_equiv))
    if not _resource_totals_close(attributed_prb_total, allocated_prb_equiv):
        raise RuntimeError(
            "用户 PRB attribution 对账失败："
            f"users={attributed_prb_total}, cell={allocated_prb_equiv}")
    tti_occupied_rbg_distribution = {
        "scope": "measurement_window_dl_scheduling_opportunities_including_idle",
        "x": "occupied_rbg_count",
        "y": "tti_share",
        "num_rbg": int(sys_cfg.num_rbg),
        "n_tti": int(dl_tti),
        "bins": [
            {
                "occupied_rbg": int(n_rbg),
                "tti_count": int(count),
                "tti_share": float(count / max(int(dl_tti), 1)),
            }
            for n_rbg, count in enumerate(tti_occupied_rbg_counts)
        ],
    }
    serving_cell_prb_utilization = float(
        allocated_prb_equiv / max(available_prb_equiv, _EPS))
    mu_paired_prb_share_of_used = float(
        mu_prb_equiv / max(allocated_prb_equiv, _EPS))
    mu_paired_prb_utilization = float(
        mu_prb_equiv / max(available_prb_equiv, _EPS))
    cell = {
        "cell_experienced_mbps": _mean(user_exp) or 0.0,
        "cell_head_inclusive_experienced_mbps": _mean(user_head_exp) or 0.0,
        "cell_experienced_completed_only_mbps": (
            _mean(user_exp_completed_only) or 0.0),
        "ue_experienced_mean_mbps": _mean(user_exp) or 0.0,
        "ue_experienced_median_mbps": _pct(user_exp, 50) or 0.0,
        "ue_experienced_p5_mbps": _pct(user_exp, 5) or 0.0,
        "ue_experience_eligible": int(len(user_exp)),
        "ue_experience_measured": int(len(user_exp_completed_only)),
        "ue_experience_measured_share": float(
            len(user_exp_completed_only) / max(len(user_exp), 1)),
        "drb_throughput_rel19_mbps": _mean(all_thp),
        "drb_throughput_head_inclusive_mbps": _mean(all_head_thp),
        "large_burst_drb_throughput_mbps": _mean(large_thp),
        "large_burst_head_inclusive_mbps": _mean(large_head_thp),
        "large_flow_drb_throughput_p5_mbps": _pct(large_user_thp, 5),
        "large_flow_head_inclusive_p5_mbps": _pct(large_user_head_thp, 5),
        "small_burst_fractional_mbps": _mean(small_thp),
        "small_burst_head_inclusive_mbps": _mean(small_head_thp),
        "arrival_queue_wait_ms_p50": _pct(all_wait, 50),
        "arrival_queue_wait_ms_mean": _mean(all_wait),
        "arrival_queue_wait_ms_p95": _pct(all_wait, 95),
        "arrival_queue_wait_ms_p99": _pct(all_wait, 99),
        "arrival_immediate_service_ratio": _immediate_service_ratio(all_wait),
        # 用户口径“首包时延”：每个外生 arrival object 从生成到第一次调度。
        # 未调度对象不伪造数值，单列为右删失并用 observed_share 暴露覆盖率。
        "first_packet_delay_ms_p50": _pct(all_wait, 50),
        "first_packet_delay_ms_mean": _mean(all_wait),
        "first_packet_delay_ms_p95": _pct(all_wait, 95),
        "first_packet_delay_ms_p99": _pct(all_wait, 99),
        "first_packet_delay_observed_share": float(
            queue_wait_observed_objects
            / max(queue_wait_observed_objects + queue_wait_right_censored_objects, 1)),
        "arrival_completion_delay_ms_p50": _pct(all_completion, 50),
        "arrival_completion_delay_ms_p95": _pct(all_completion, 95),
        "arrival_completion_delay_ms_p99": _pct(all_completion, 99),
        "arrival_pdb_miss_ratio": float(np.mean(pdb_flags)) if pdb_flags else None,
        "small_queue_wait_ms_p50": _pct(small_wait, 50),
        "small_queue_wait_ms_mean": _mean(small_wait),
        "small_queue_wait_ms_p95": _pct(small_wait, 95),
        "small_queue_wait_ms_p99": _pct(small_wait, 99),
        "small_immediate_service_ratio": _immediate_service_ratio(small_wait),
        "small_completion_delay_ms_p95": _pct(small_completion, 95),
        "small_pdb_miss_ratio": (float(np.mean(small_pdb_flags))
                                  if small_pdb_flags else None),
        "large_queue_wait_ms_p95": _pct(large_wait, 95),
        "large_queue_wait_ms_mean": _mean(large_wait),
        "large_immediate_service_ratio": _immediate_service_ratio(large_wait),
        "large_completion_delay_ms_p95": _pct(large_completion, 95),
        "large_pdb_miss_ratio": (float(np.mean(large_pdb_flags))
                                  if large_pdb_flags else None),
        "busy_period_first_schedule_wait_ms_p95": _pct(all_busy_wait, 95),
        "busy_period_completion_delay_ms_p95": _pct(all_busy_completion, 95),
        "class_arrival_kpis": class_arrival_summary,
        # 兼容字段在 experience_v2 中等价于 arrival-object 口径。
        "queue_wait_ms_p50": _pct(all_wait, 50),
        "queue_wait_ms_mean": _mean(all_wait),
        "queue_wait_ms_p95": _pct(all_wait, 95),
        "queue_wait_ms_p99": _pct(all_wait, 99),
        "immediate_service_ratio": _immediate_service_ratio(all_wait),
        "completion_delay_ms_p50": _pct(all_completion, 50),
        "completion_delay_ms_p95": _pct(all_completion, 95),
        "completion_delay_ms_p99": _pct(all_completion, 99),
        "pdb_miss_ratio": float(np.mean(pdb_flags)) if pdb_flags else None,
        "cell_served_mbps": float(
            acked_total_measured * 8 / max(measurement_duration_s, _EPS) / 1e6),
        "avg_mcs": float(
            np.sum(mcs_sum_measured) / max(np.sum(sched_cnt_measured), 1)),
        "avg_rank": float(
            np.sum(rank_sum_measured) / max(np.sum(sched_cnt_measured), 1)),
        "bler_first_tx": float(nack_total / max(tx_total, 1)),
        "su_bler_first_tx": measured_mode["SU"]["bler"],
        "mu_bler_first_tx": measured_mode["MU"]["bler"],
        "su_tx_count": measured_mode["SU"]["tx"],
        "mu_tx_count": measured_mode["MU"]["tx"],
        "newtx_attempt_bler": float(nack_total / max(tx_total, 1)),
        "retx_bler": float(retx_nack_total / max(retx_total, 1)),
        "retx_attempts": retx_total,
        "retx_nacks": retx_nack_total,
        "residual_bler": float(
            retx_nack_total / max(observed_harq_total, 1)),
        "pending_harq_tb_at_end": pending_harq_total,
        "residual_bler_definition": (
            "failed unique retransmissions / initial TBs whose HARQ outcome is "
            "observed in the measurement cohort; end-of-run pending TBs are "
            "right-censored"),
        "dl_tti": int(dl_tti),
        "scheduled_tti": int(busy_tti),
        "occupancy": float(busy_tti / max(dl_tti, 1)),
        "serving_cell_prb_utilization": serving_cell_prb_utilization,
        # 兼容旧消费者；它从 experience_v2 起就是本小区 PRB-equivalent 利用率，
        # 不是输入侧的 neighbor_prb_util。
        "resource_utilization": serving_cell_prb_utilization,
        # 与 serving_cell_prb_utilization 同一 equiv 口径（S 时隙按 0.7 折算）；
        # 整数槽计数会让 DDDSU 全忙时报 100% 而利用率报 94%，自相打架。
        "rbg_slot_occupancy": float(
            allocated_rbg_equiv / max(available_rbg_equiv, _EPS)),
        "tti_occupied_rbg_distribution": tti_occupied_rbg_distribution,
        "scheduled_ues_per_busy_tti": float(scheduled_ues_sum / max(busy_tti, 1)),
        "multi_ue_tti_share": float(multi_ue_tti / max(busy_tti, 1)),
        "payload_fill_ratio": float(attempted_total / max(sched_total, 1.0)),
        "ack_payload_efficiency": float(
            acked_total_measured / max(sched_total, 1.0)),
        "padding_ratio": float(padding_total / max(sched_total, 1.0)),
        "actual_rbg_size_hist": ({
            "p_1rbg": float(np.mean(np.asarray(rbg_hist) == 1)),
            "p_full": float(np.mean(np.asarray(rbg_hist) == sys_cfg.num_rbg)),
            "mean_rbg": float(np.mean(rbg_hist)), "n": len(rbg_hist),
            "scope": "nonzero_grant_size_not_tti_total",
        } if rbg_hist else None),
        # 兼容旧消费者；experience_v2 里它明确就是 actual allocation。
        "rbg_size_hist": ({
            "p_1rbg": float(np.mean(np.asarray(rbg_hist) == 1)),
            "p_full": float(np.mean(np.asarray(rbg_hist) == sys_cfg.num_rbg)),
            "mean_rbg": float(np.mean(rbg_hist)), "n": len(rbg_hist),
            "scope": "nonzero_grant_size_not_tti_total",
        } if rbg_hist else None),
        "small_pkt_experienced_mbps": _mean(small_thp),
        "large_pkt_experienced_mbps": _mean(large_thp),
        "measured_bursts": int(measured_bursts),
        "completed_bursts": int(completed_bursts),
        "completed_arrival_objects": int(completed_arrival_objects),
        "queue_wait_observed_arrival_objects": int(queue_wait_observed_objects),
        "queue_wait_right_censored_arrival_objects": int(
            queue_wait_right_censored_objects),
        "queue_wait_observed_share": float(
            queue_wait_observed_objects
            / max(queue_wait_observed_objects + queue_wait_right_censored_objects, 1)),
        "pdb_decidable_arrival_objects": int(pdb_decidable_objects),
        "pdb_right_censored_arrival_objects": int(pdb_right_censored_objects),
        "deadline_missed_incomplete_arrival_objects": int(
            deadline_missed_incomplete_objects),
        "offered_mbps": (None if tr.unbounded else float(
            offered_measured * 8 / max(measurement_duration_s, _EPS) / 1e6)),
        "offered_bytes_measurement": (None if tr.unbounded else offered_measured),
        "backlog_bytes_at_measurement_start": (
            None if tr.unbounded else backlog_at_measurement_start),
        "measurement_accounting_error_pct": (
            None if measurement_acct_error is None
            else round(float(measurement_acct_error), 6)),
        "measurement_start_s": warmup * float(sys_cfg.tti_ms) / 1000.0,
        "measurement_duration_s": measurement_duration_s,
        "warmup_tti": warmup,
        "backlog_bytes": backlog,
        "backlog_bursts": int(sum(q.active is not None for q in tr.queues)),
        "accounting_error_pct": round(float(acct_error), 6),
        "outage_ue": int(sum(1 for t in tables
                              if t.outage is not None and bool(t.outage.all()))),
        "outage_skips": int(outage_skips),
        "olla_db_mean": float(np.mean(olla_db)),
        "olla_db_p5": float(np.percentile(olla_db, 5)),
        "olla_db_p95": float(np.percentile(olla_db, 95)),
        "olla_mcs_mean": float(np.mean(olla_db)),
        "olla_mcs_p5": float(np.percentile(olla_db, 5)),
        "olla_mcs_p95": float(np.percentile(olla_db, 95)),
        "olla_domain": "continuous_mcs_index",
        "mu_share": float(mu_tti / max(busy_tti, 1)),
        "mu_rbg_share": float(mu_rbg / max(allocated_rbg, 1)),
        "mu_paired_prb_share_of_used": mu_paired_prb_share_of_used,
        "mu_paired_prb_utilization": mu_paired_prb_utilization,
        "mu_paired_prb_equivalent": float(mu_prb_equiv),
        "allocated_prb_equivalent": float(allocated_prb_equiv),
        "available_prb_equivalent": float(available_prb_equiv),
        "mu_user_tx_share": float(
            mu_user_tx / max(int(np.sum(sched_cnt_measured)), 1)),
        "su_mu_plan": {
            "su_selected": int(su_decisions), "mu_selected": int(mu_decisions),
            "su_forced_all_queues_clear": int(su_forced_clear),
            "harq_retx_forced_su": int(harq_retx_forced_su),
            "su_planned_useful_bytes": int(su_plan_useful),
            "mu_planned_useful_bytes": int(mu_plan_useful),
            "comparison_unit": "useful_payload_bytes_capped_by_queue",
            "tie_break": "MU when eligible and MU >= SU",
        },
        "su_olla_db_mean": float(np.mean(olla_db)),
        "mu_olla_db_mean": float(np.mean(mu_olla_db)),
        "su_olla_mcs_mean": float(np.mean(olla_db)),
        "mu_olla_mcs_mean": float(np.mean(mu_olla_db)),
        "olla_convergence": convergence,
        "olla_warmup_speedup": float(
            getattr(sched, "olla_warmup_speedup", 1.0)),
        "olla_measurement_speedup": float(getattr(sched, "olla_speedup", 1.0)),
        "pf_accounting": accounting,
        "harq_model": {
            "max_retransmissions": 1,
            "combining": harq_combining,
            "bler_source": "preset NewTx curves only",
            "identity": "same MCS/RBG-count/rank/TBS as initial TB",
            "timing": "retransmit on the same D/S slot type",
            "post_failure": "payload remains queued and later becomes a new TB",
        },
        "class_allocated_rbg": class_alloc_rbg,
        "class_allocated_rbg_scope": (
            "per-user grant; a shared MU RBG is counted for both UEs and is not additive "
            "to physical cell RBG"),
        "class_physical_rbg_share": class_physical_rbg_share,
        "class_acked_bytes": class_acked,
    }

    if tr.unbounded:
        # full buffer 没有 busy-period 边界，体验速率无定义；报 None 而不是
        # 0.0——zero-inclusive 口径是为"有外生到达但被饿死"的 UE 设计的。
        for _k in ("cell_experienced_mbps",
                   "cell_head_inclusive_experienced_mbps",
                   "cell_experienced_completed_only_mbps",
                   "ue_experienced_mean_mbps", "ue_experienced_median_mbps",
                   "ue_experienced_p5_mbps"):
            cell[_k] = None

    notes: list[str] = [
        (
            "experience_v2 已开启 RB 功控：按实际 grant bitmap 聚合逐 RBG "
            "SINR、重选 MCS 并判误码；当前有效 SINR 是 RBG dB 算术平均，尚未用"
            "标定过的 EESM/MIESM。"
            if frequency_aware else
            "experience_v2 在 RB 功控关闭时使用**宽带 SINR/MCS**做误码与调度，"
            "不包含逐 RBG 频选增益。"
        ),
        "TBS 量化算法走 38.214 §5.1.3.2，但 MCS 使用预置 20B profile；D 时隙按"
        "每 RB 12 个数据符号、S 时隙按 0.7 倍 N_RE，未展开 DMRS/PTRS/CORESET。",
        ("HARQ 每个单码字 TB 最多一次重传，重传保持初传 MCS、RBG 数、rank 与 TBS；"
         f"当前合并={harq_combining.upper()}。CC 用同一 NewTx 曲线并把码字 "
         "SINR 抬升 10log10(2)=3.0103 dB；IR 用原 MCS 一半谱效映射等效低档 MCS，"
         "在不变 SINR 上查该 NewTx 曲线。等效 MCS 只用于 BLER 查表，不改写空口 MCS。"
         "重传失败后结束本次 HARQ，payload 留在 DRB 队列并在后续作为新 TB。"),
        f"PF 平均量口径是 **{accounting}**；ACKed bytes 另作为 KPI 统计。",
        "分配器每个 DL TTI 只排序一次：按 PF/QoS-PF 优先级依次给最小够用 RBG；"
        "剩余 RBG 没有候选需求时留空，不回填给第一名。",
        "DRB throughput 按 buffer busy period；queue/completion/PDB 按 FIFO "
        "arrival object（mixed/FTP 为文件，CBR 为每 TTI 字节块）。1500 B small "
        "类可作小包代理，large FTP 文件的 PDB 不能冒充逐 PDCP SDU 时延。",
        "含头速率与掐头去尾 DRB throughput 使用相同 payload numerator 和去尾规则；"
        "它只把 arrival 到首次调度的首包时延加回 denominator。",
        "完成时延分位数只含已完成对象；PDB miss 分母还纳入仿真结束时已超过"
        "deadline 的未完成对象，未到 deadline 的对象单列为右删失。用户体验速率"
        "采用有到达 UE 的 zero-inclusive 分布，避免饿死 UE 从统计中消失。",
        "queue-wait 在 arrival 首次调度时即成为完整观测，不要求该对象最终完成；"
        "仿真结束仍从未调度的对象单列为 queue-wait 右删失，并报告观测覆盖率。",
        f"前 {warmup * float(sys_cfg.tti_ms) / 1000.0:g} s 为预启动；PF/OLLA/SRS "
        f"状态继续演进，但所有体验、吞吐、BLER 与资源 KPI 只统计之后的 "
        f"{measurement_duration_s:g} s。",
    ]
    if bool(sched.mu_enabled):
        notes.append(
            "MU 在 PF 固定排序后构造两用户 rank2 方案；MCS 输入严格拆为 "
            "CQI+BF+SU-OLLA+CorrLoss+powerLoss+MU-OLLA。SU 与 MU 各自维护用户级 "
            "OLLA；SU/MU 方案按队列封顶后的 useful payload bytes 比较，SU 能清空"
            "全部队列时强制 SU，剩余 RBG 留空。")
    if any(row.get("packet_size_cdf") or row.get("interarrival_cdf")
           for row in tr.profile_summaries):
        notes.append(
            "经验 CDF 话务按 value,cdf 逆变换采样：包大小 CDF 决定每个外生"
            "arrival 的字节数，包间隔 CDF 驱动逐 UE renewal process；存在包间隔 "
            "CDF 时 arrival_rate_hz 不再参与到达时刻。全局与 profile 的两个"
            "缩放标量相乘，负载一阶近似正比于 packet_size_scale / "
            "interarrival_scale。")
    if bool(sched.olla_enabled) and float(
            getattr(sched, "olla_warmup_speedup", 1.0)) != float(
            getattr(sched, "olla_speedup", 1.0)):
        notes.append(
            f"预启动期 OLLA 步长显式放大 "
            f"{float(getattr(sched, 'olla_warmup_speedup', 1.0)):g} 倍；进入 KPI "
            f"窗口后恢复为 {float(getattr(sched, 'olla_speedup', 1.0)):g} 倍。"
            "步长比不变，因此目标 BLER 不变；此设置只缩短收敛时间。")
    if not bool(convergence["all_active_modes_converged"]):
        notes.append(
            "**OLLA 收敛门未通过**：至少一个占比不低于 1% 的传输模式在测量"
            "前后半的期望 BLER 尚未稳定到目标 ±5 个百分点；该次体验 KPI 只能"
            "用于诊断，不能进入正式比较。请延长预启动或显式使用预启动专用加速。")
    if str(sched.algorithm) == "qos_pf":
        notes.append(
            "qos_pf 使用 w(priority) × R_inst^beta / R_avg^alpha × "
            "[PDB/(PDB−HOL)]^gamma（时延因子上限 1000）；默认 "
            "alpha=beta=1、gamma=0、w=1，严格退化成经典 PF。它是显式工程 "
            "profile，未冒充现场未确认的 EPF。")
    if n_snap < 4:
        notes.append(f"**信道快照只有 {n_snap} 个**，时间起伏被严重低估，PF 多用户分集不足。")
    if tr.unbounded:
        notes.append("**话务无界（full buffer）**：busy period 永不结束，"
                     "体验速率无定义，体验类 KPI 报 None（不是 0）；"
                     "容量口径请看 cell_served_mbps。")
    if measured_bursts < 20 and not tr.unbounded:
        notes.append(f"只有 {measured_bursts} 个 busy period 进入体验 KPI，样本太少；"
                     "加长 duration_s 或调高到达率。")
    if not tr.unbounded and backlog > 0.15 * max(offered, 1):
        notes.append(f"**队列积压 {backlog * 8 / 1e6:.1f} Mb**"
                     f"（占到达量 {backlog / max(offered, 1):.0%}），系统未收敛。")
    if acct_error > 1.0:
        notes.append(f"**字节对不上账（差 {acct_error:.3f}%）**："
                     "arrived 必须等于 acked + queued + in_flight + dropped。")
    if measurement_acct_error is not None and measurement_acct_error > 1.0:
        notes.append(
            f"**测量窗口字节对不上账（差 {measurement_acct_error:.3f}%）**："
            "start_backlog + arrived_in_window 必须等于 acked_in_window + end_backlog。")
    if cell["serving_cell_prb_utilization"] > 0.98:
        notes.append("**本小区 PRB 利用率超过 98%**，当前结果更接近容量上限而非稳态体验。")
    diagnostics = {
        "tbs_lookup": lookup.as_dict(),
        "tti_trace": {
            "schema": "superran_tti_trace_v1",
            "mode": trace_mode,
            "source_replication": int(book.replication),
            "tti_ms": float(sys_cfg.tti_ms),
            "warmup_tti": int(warmup),
            "max_points": int(trace_max_points),
            "uniform_anchor_count": len(trace_uniform_ttis),
            "event_row_count": sum(
                "uniform" not in row["sample_reasons"]
                and "full" not in row["sample_reasons"]
                for row in tti_trace_rows.values()
            ),
            "sampling_contract": (
                "all measurement-window DL TTIs"
                if trace_mode == "full"
                else "deterministic uniform anchors plus bounded MU/NACK/retx/"
                     "multi-UE/outage events"
                if trace_mode == "sampled"
                else "disabled"
            ),
            "rows": [tti_trace_rows[key] for key in sorted(tti_trace_rows)],
        },
        "allocation_sample": [a.as_dict() for a in allocation_sample],
        "allocation_recent_sample": [a.as_dict() for a in allocation_recent],
        "allocation_sample_limit": allocation_limit,
        "max_rbg_in_any_tti": int(max_rbg_in_tti),
        "rbg_overlap_violations": int(overlap_violations),
        "allocated_rbg_total": int(allocated_rbg),
        "allocated_rbg_total_full_run": int(allocated_rbg_full),
        "available_rbg_total": int(dl_tti * sys_cfg.num_rbg),
        "dl_tti_full_run": int(dl_tti_full),
        "allocated_rbg_equivalent": float(allocated_rbg_equiv),
        "available_rbg_equivalent": float(available_rbg_equiv),
        "tti_occupied_rbg_distribution": tti_occupied_rbg_distribution,
        "kpi_definitions": {
            "first_packet_delay": {
                "unit": "ms",
                "scope": "measurement-window FIFO arrival objects",
                "formula": "(first_scheduled_tti - arrival_tti) * tti_ms",
                "censoring": "never-scheduled objects are right-censored and excluded from percentiles",
            },
            "head_inclusive_throughput": {
                "unit": "Mbps",
                "scope": "completed DRB busy periods eligible for the trimmed throughput KPI",
                "formula": "same payload and tail exclusion as Rel-19-style throughput; denominator += first_packet_delay",
            },
            "serving_cell_prb_utilization": {
                "unit": "ratio",
                "scope": "measurement-window DL scheduling opportunities",
                "formula": "allocated PRB-equivalent / available PRB-equivalent; D=1, S=0.7",
                "full_buffer_expected": 1.0,
                "not_neighbor_prb_util": True,
            },
            "tti_occupied_rbg_distribution": {
                "unit": "TTI share",
                "scope": "one sample per measurement-window DL scheduling opportunity, including idle",
                "bins": f"0..{int(sys_cfg.num_rbg)} occupied RBG",
            },
            "mu_paired_prb": {
                "unit": "ratio and PRB-equivalent",
                "share_of_used": "MU-paired PRB-equivalent / allocated PRB-equivalent",
                "utilization": "MU-paired PRB-equivalent / available PRB-equivalent",
            },
            "user_prb_attribution": {
                "unit": "PRB-equivalent",
                "grant_exposure": (
                    "a shared MU PRB is counted for every paired UE; not additive"),
                "attributed": (
                    "a shared MU PRB is divided equally across paired UEs; additive to cell"),
                "invariant": "sum(user attributed PRB) == cell allocated PRB",
            },
        },
        "traffic_profiles": tr.profile_summaries,
        "traffic_samples": tr.traffic_samples,
        "arrival_events": int(tr.arrival_events),
        "measurement_window": {
            "warmup_tti": warmup,
            "warmup_s": warmup * float(sys_cfg.tti_ms) / 1000.0,
            "duration_s": measurement_duration_s,
            "offered_bytes": None if tr.unbounded else offered_measured,
            "acked_bytes": acked_total_measured,
            "start_backlog_bytes": (
                None if tr.unbounded else backlog_at_measurement_start),
            "end_backlog_bytes": None if tr.unbounded else backlog,
            "balance_error_bytes": measurement_balance_error_bytes,
            "accounting_error_pct": (
                None if measurement_acct_error is None
                else round(float(measurement_acct_error), 6)),
        },
        "olla_state_final": {
            "su_db": [float(x) for x in olla_db],
            "mu_db": [float(x) for x in mu_olla_db],
            "su_mcs": [float(x) for x in olla_db],
            "mu_mcs": [float(x) for x in mu_olla_db],
            "domain": "continuous_mcs_index",
            "scope": "separate user-level arrays; not pair-specific",
        },
        "olla_state_at_measurement_start": olla_at_measurement_start,
        "link_adaptation_by_phase": adaptation_summary,
        "link_adaptation_by_ue": {
            mode: [
                {
                    "ue": u,
                    "tx": int(mode_tx_by_ue[mode][u]),
                    "nack": int(mode_nack_by_ue[mode][u]),
                    "observed_bler": float(
                        mode_nack_by_ue[mode][u] / max(mode_tx_by_ue[mode][u], 1)),
                    "expected_bler": float(
                        mode_expected_bler_by_ue[mode][u]
                        / max(mode_tx_by_ue[mode][u], 1)),
                }
                for u in range(n_ue)
            ]
            for mode in modes
        },
        "byte_conservation": {
            "arrived": None if tr.unbounded else offered,
            "acked": acked_total,
            "queued": backlog,
            "in_flight": 0,
            "dropped": 0,
            "error_pct": round(float(acct_error), 6),
        },
        "queue_wait_observation": {
            "observed_first_tx": int(queue_wait_observed_objects),
            "right_censored_never_scheduled": int(queue_wait_right_censored_objects),
            "scope": "measurement-window arrivals; completion not required for wait",
        },
        "crn_event_mapping": (
            "traffic uses stable named substreams for profile/arrival-count/packet-size/"
            "interarrival/phase; harq and tie-break are indexed [TTI,UE]"),
    }
    return ExperienceRun(cell=cell, users=users, notes=notes,
                         diagnostics=diagnostics,
                         elapsed_s=time.perf_counter() - t0)

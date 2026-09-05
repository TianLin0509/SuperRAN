"""系统级 TTI 主循环：DRB busy-period、按需 RBG 分配与 Rel-19 KPI。

这个模块只做系统级第二相（TTI 主循环），不碰信道矩阵，是**唯一**的评估路径。
``experience_v2`` 用实际分配的 TBS 给 PF 记账，并允许一个 TTI 服务多个 UE。

**"容量仿真"不是另一条分支**，而是 ``TrafficConfig(model="full_buffer")``
这个话务配置点：缓冲区永不空 ⇒ :func:`_build_su_plan` 的按需 RBG 反查恒等于
全带宽、RBG 全部用满（频选或 MU 打开时一个 TTI 会服务多个用户，实测默认 1.09、开 MU 1.74）。
调度、AMC、HARQ、解调 SINR 聚合全部照
本模块的定义走，**没有为它开的任何特例分支**。代价是 busy period 永不结束，
**标准与工程两套 KPI 因此分家**：TS 28.552 的样本只在 "DRB DL buffer emptied"
事件上形成（TS 128 552 V19.5.0 p54），满缓冲下一个都不会形成，所以
``drb_throughput_rel19_mbps`` / ``cell_experienced_mbps`` 报 ``None``——**这是
定义使然，不是缺陷**。满缓冲要看的是工程口径：ITU-R M.2412 / TR 38.913 的
``ue_served_p5_mbps``（每 UE 已服务净荷 ÷ 观测窗长）与 ``active_window_goodput_mbps``
（在飞 busy period 的窗内段 goodput）。这两条路径算法不同，满缓冲下应当收敛，
实测 61.868 vs 61.968 Mbps，差 0.16%——**这正是拿来自查的交叉核对**。

物理边界明确写在结果里：逐 RBG 频选与 RB 功控是两个独立开关；只要链路表
带逐 RBG SINR，实际 grant 就按 bitmap 聚合并重选 MCS。当前聚合仍是 dB
算术平均而非标定过的
EESM/MIESM。每个单码字 TB 最多一次 IR/CC 重传：空口 MCS、RBG 数、rank 与
TBS 保持不变，BLER 只由预置 NewTx 曲线推导；失败 payload 留队并成为后续新 TB。
"""
from __future__ import annotations

import time
import zlib
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from . import amc_policy as ap
from . import linkadapt as la
from . import rng as rg
from . import scheduler_edf as sedf
from . import scheduler_finalize as sfinal
from . import scheduler_frequency as sfreq
from . import scheduler_mu as smu
from . import scheduler_resource as sres
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


def active_window_goodput(burst: BusyPeriod, tti_ms: float,
                          warmup_tti: int) -> BurstMetrics:
    """还在飞的 busy period 在测量窗内那一段的**工程口径 goodput**。

    **这不是 TS 28.552 的吞吐样本，名字里绝不能出现 rel19。** 标准的样本只在
    "DRB DL buffer emptied" 事件上形成（TS 128 552 V19.5.0 p54），并排除清空
    buffer 的最后一个 piece。buffer 没排空就没有该事件，也就没有标准样本。

    那为什么还要报它：**过载与满缓冲下标准样本可能一个都没有**，此时用户仍然
    需要知道"正在传的时候有多快"。所以另起一个字段、另起一个名字，
    与标准字段并列上报，任何时候都不混进 ``drb_throughput_rel19_mbps``。

    **不掐尾，是因为它本来就是 goodput（有用字节 ÷ 经过时间），不是标准吞吐。**
    我曾断言"在飞的末 ACK 必是满 slot 所以无尾可掐"——**那个断言是错的**，
    审核给了反例：首传 100B 装进 1000B TB 后 NACK，等待期间新增 1 B，重传 ACK
    时队列仍非空、该 ACK 却带 900 B padding。padding 对 goodput 无影响
    （分子本来就只数有用字节），但它足以否定"满 slot"这个说法，
    因此也否定了"可以按标准口径处理在飞段"的想法。

    含头速率只在**该 busy period 本身起始于测量窗内**时才给：起始于窗外的 burst，
    它的排队等待发生在窗外，加进窗内分母是两个口径混用。
    """
    if burst.first_tx_tti < 0:
        return BurstMetrics(None, None, None, None, None)
    events = [e for e in burst.ack_events if e.tti >= int(warmup_tti)]
    if not events:
        return BurstMetrics(None, None, None, None, None)
    vol = int(sum(e.payload_bytes for e in events))
    start_tti = max(int(burst.first_tx_tti), int(warmup_tti))
    duration_tti = int(events[-1].tti) - start_tti + 1
    if vol <= 0 or duration_tti <= 0:
        return BurstMetrics(None, None, None, None, None)
    duration_ms = duration_tti * float(tti_ms)
    thp = vol * 8.0 / (duration_ms / 1000.0) / 1e6
    head_thp = None
    if int(burst.start_tti) >= int(warmup_tti):
        wait = max(0, burst.first_tx_tti - burst.start_tti) * float(tti_ms)
        head_thp = vol * 8.0 / ((wait + duration_ms) / 1000.0) / 1e6
    # 完成时延与 PDB 需要对象真的传完，在飞的 burst 给不出，保持 None。
    # kind 刻意不含 rel19：大/小 burst 分视图是标准口径的，这个样本不进那里。
    return BurstMetrics(thp, "engineering_active_window", None, None, None, head_thp)


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
    required_rbg_from_remaining_pool: int
    fits_in_remaining_pool: bool
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
    #: 本次 ACK/NACK 将要施加的 OLLA 增量（MCS 档）与它**生效**的 TTI。
    #: 反馈要等上行时隙，所以 ``*_after_db`` 在同一个 TTI 里通常还没变。
    olla_delta_mcs: float = 0.0
    olla_effective_tti: int | None = None
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
    reservation_id: str | None = None
    logical_prb: int = 0
    layers_per_rbg: int = 0
    finalizer_version: str = "grant-finalizer-v1"
    frequency_selection_score_gain: float = 0.0
    frequency_incremental_useful_bytes: int = 0
    frequency_evaluated_subsets: int = 0
    frequency_selected_source: str = "wideband_or_sequential"
    mu_candidate_score: float | None = None
    mu_candidate_count: int = 0
    mu_rejected_candidate_reasons: tuple[str, ...] = ()

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
        d["olla_delta_mcs"] = round(self.olla_delta_mcs, 6)
        d["olla_domain"] = "continuous_mcs_index"
        d["pf_average_before_bytes"] = round(self.pf_average_before_bytes, 6)
        d["scheduler_metric"] = round(self.scheduler_metric, 6)
        d["bler"] = round(self.bler, 6)
        d["harq_random_draw"] = round(self.harq_random_draw, 6)
        if self.bler_lookup_sinr_db is not None:
            d["bler_lookup_sinr_db"] = round(self.bler_lookup_sinr_db, 4)
        d["mu_rejected_candidate_reasons"] = list(
            self.mu_rejected_candidate_reasons)
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
    resource_ledger: dict[str, Any] | None = None,
    mu_candidate_decisions: Sequence[smu.MuCandidateDecision] = (),
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
        "resource_ledger": resource_ledger,
        "mu_candidate_decisions": [
            decision.as_dict() for decision in mu_candidate_decisions],
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
    required_rbg_from_remaining_pool: tuple[int, ...] = ()
    fits_in_remaining_pool: tuple[bool, ...] = ()
    pair_correlation: float | None = None
    candidate_score: float | None = None
    candidate_count: int = 0
    rejected_candidate_reasons: tuple[str, ...] = ()
    frequency_selection_score_gain: float = 0.0
    frequency_incremental_useful_bytes: int = 0
    frequency_evaluated_subsets: int = 0
    frequency_selected_source: str = "wideband_or_sequential"
    reservation_id: str | None = None


@dataclass(frozen=True)
class _HarqTb:
    """单进程 HARQ 状态；ACK/NACK 在反馈到达前都保持 in-flight。"""

    mcs: int
    rank: int
    n_rbg: int
    tb_bytes: int
    payload_bytes: int
    slot: str
    first_tti: int
    first_mode: str
    feedback: ap.FirstTxFeedback
    state: str = "await_feedback"
    final_feedback_tti: int | None = None

    @property
    def ready_tti(self) -> int:
        return int(self.feedback.effective_tti)

    @property
    def first_ack(self) -> bool:
        return bool(self.feedback.ack)


@dataclass(frozen=True)
class _TtiPlan:
    name: str
    grants: tuple[_PlannedGrant, ...]
    useful_bytes: int
    used_rbg: int
    has_mu: bool
    clears_all_queues: bool
    mu_candidate_decisions: tuple[smu.MuCandidateDecision, ...] = ()
    resource_admission: sres.ResourceAdmission | None = None


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


def _rank_se_estimates(
    table: Any, snap: int, olla_offset: float, *, olla_enabled: bool,
    lookup: TbsLookup, max_rank: int,
) -> tuple[list[float], list[int]]:
    """逐 rank 的估计谱效与 MCS，喂给 :class:`amc_policy.RankController`。

    对每个 rank 假设 ``r``：拿该 rank 的 AMC 预测坐标（CQI 门限 + BF Gain，
    两者都已经按 ``P/r`` 的每流功率算过，所以功率分摊已经在里面了），叠加
    当前用户级 OLLA 偏置，反折出**真的会发下去**的 MCS，谱效记为
    ``r × MCS 谱效``。

    **资源消耗加权与最小 MCS 闸门不在这里做**，它们由 ``RankController``
    统一施加——系统级只有一条评估路径，这个控制器只有一份实现。因此这里
    把 MCS 一并返回。

    这与现场"用一份上报 RI 的 CQI 再按 ``10log10(RI/r)`` 外推"不是同一个近似：
    本实现对每个 rank 假设各有一份该 rank 下测得的 CQI 与 BF Gain，比外推更
    贴近物理，代价是它要求链路表逐 rank 都算过。两者的差异必须写进文档，
    不能当成同一个算法。
    """
    rows = table.sinr_tx_db
    if rows is None:
        return [], []
    limit = min(int(max_rank), int(rows.shape[1]))
    profile = la.MCS_TABLES[int(lookup.mcs_table)]
    se_out: list[float] = []
    mcs_out: list[int] = []
    for rank in range(1, limit + 1):
        base = float(rows[snap, rank - 1])
        mcs = _select_mcs(base, lookup)
        if olla_enabled:
            mcs = int(la.apply_olla_mcs(
                mcs, float(olla_offset),
                mcs_table=int(lookup.mcs_table))["final_mcs"])
        se_out.append(float(rank) * float(profile[mcs].se))
        mcs_out.append(int(mcs))
    return se_out, mcs_out


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


def _scheduler_metric_identity(
    sched: Any, *, srb_observed: bool = False,
) -> dict[str, Any]:
    """把本次运行实际使用的优先级公式写进结果，避免只报一个算法名。"""
    algorithm = str(sched.algorithm)
    formulas = {
        "pf": "TBS_fullband / R_avg",
        "qos_pf": "w(priority) × TBS^beta / R_avg^alpha × delay^gamma",
        "rr": "(u − tti) mod N",
        "max_ci": "TBS_fullband",
        "edf": "w(priority) × TBS_fullband / Buffer",
        "qos_pf_edf": ("((1−w)×scale×[TBS^beta/R_avg^alpha×delay^gamma] "
                       "+ w×[TBS/Buffer]) × w(priority)"),
    }
    out: dict[str, Any] = {
        "algorithm": algorithm,
        "formula": formulas.get(algorithm, "unknown"),
        "units": "TBS 与 Buffer 均为 bytes；比值无量纲",
    }
    if algorithm in ("edf", "qos_pf_edf"):
        out["srb_priority_boost"] = float(
            getattr(sched, "srb_priority_boost", 5000.0))
        out["srb_modelled"] = bool(srb_observed)
        out["srb_note"] = (
            "本次运行**出现了** resource_type='signalling' 的业务类，SRB 加值已"
            "生效。注意它是加性而非真正绝对：数据承载只要 TBS/Buffer 超过加值"
            "就能压过去（当前包长下实测 EDF 度量上界约 143，够不到 5000）"
            if srb_observed else
            "SuperRAN 不建模逻辑信道；只有显式声明 resource_type='signalling' "
            "的业务类才会拿到 SRB 加值，本次运行没有这样的类，加值恒不触发")
        out["retx_priority"] = (
            "结构性绝对优先：HARQ pending 用户整体前置并按 first_tti 排序，"
            "不使用蓝本的 +10000 常数")
        starve = getattr(sched, "edf_starvation_hol_ms", None)
        out["starvation_guard_hol_ms"] = (
            None if starve is None else float(starve))
        out["starvation_guard_note"] = (
            "关闭：EDF 的分母是积压，越饿分母越大、优先级越低，靠算法自身不会"
            "恢复，饱和下必然饿死一部分大包用户"
            if starve is None else
            f"队首等待达到 {float(starve):g} ms 的用户无条件排到最前，组内按等待降序")
        out["rbg_allocation"] = (
            "按需分配是所有算法共享的既有行为：required_rbg_for_indices "
            "算出传完 buffer 所需 RBG 数，只取这么多")
    if algorithm == "qos_pf_edf":
        out["mixed_weight"] = float(getattr(sched, "edf_mixed_weight", 0.5))
        out["mixed_epf_scale"] = float(
            getattr(sched, "edf_mixed_epf_scale", 1.0))
    return out


def _mixed_component_scale(
    epf_medians: Sequence[float], edf_medians: Sequence[float], *,
    weight: float, epf_scale: float,
) -> dict[str, Any]:
    """混合模式两个分量的实测量级，用来判断权重 w 是否被量纲差吞掉。

    EPF 分量是 ``bytes^beta / bytes^alpha``，EDF 分量是无量纲比值；``epf_scale``
    （蓝本的 ``thp_filter``）没标定时，名义上的 w=0.5 可能实际等价于
    w=0.99。这里报出逐 TTI 中位数的中位数与加权后的实际占比，让这件事可被
    证伪，而不是让用户以为调了 w 就调了混合比例。
    """
    if not epf_medians or not edf_medians:
        return {"samples": 0, "note": "测量窗内没有候选用户，无法取证"}
    epf_med = float(np.median(np.asarray(epf_medians, dtype=float)))
    edf_med = float(np.median(np.asarray(edf_medians, dtype=float)))
    w = float(weight)
    epf_term = (1.0 - w) * float(epf_scale) * epf_med
    edf_term = w * edf_med
    total = epf_term + edf_term
    share = None if total <= 0 else float(edf_term / total)
    out: dict[str, Any] = {
        "samples": int(min(len(epf_medians), len(edf_medians))),
        "scope": "median over measurement TTIs of the per-TTI candidate median",
        "caveats": [
            "量的是数值量级占比，不是排序影响力；排序只取决于两个分量各自的"
            "离散度，占比 0.002 仍可能改变一部分 UE 的先后",
            "轻载下候选集常只有 1-2 个 UE，逐 TTI 中位数会退化",
            "这是**逐工作点**的读数，不是可迁移常数：同一个 epf_scale 在轻载"
            "与饱和下可以给出完全相反的结论",
            "统计量是内生的——改了 epf_scale 排序就变，两个分量的中位数会跟着"
            "变，所以一次性按比值反解不收敛，需要迭代",
        ],
        "epf_core_median": epf_med,
        "edf_core_median": edf_med,
        "nominal_edf_weight": w,
        "epf_scale": float(epf_scale),
        "effective_edf_share": share,
    }
    if share is not None and 0.0 < w < 1.0 and (share < 0.05 or share > 0.95):
        weak = "EDF" if share < 0.05 else "EPF"
        out["warning"] = (
            f"名义 EDF 权重 {w:g}，但两个分量在本工作点的量级差把实际占比压到 "
            f"{share:.3g}，{weak} 分量对度量数值的贡献已接近可忽略。"
            "**这不等于它对排序毫无影响**——排序看的是离散度，弱分量仍可能改变"
            "一部分 UE 的先后（实测占比 0.002 时公平度仍走了纯 EPF→纯 EDF 全程"
            "的约 2.4%）。若希望 w 成为真正可解释的旋钮，请针对**本工作点**迭代"
            "调 edf_mixed_epf_scale：排序一变分量中位数也会变，按当前比值一步"
            "反解不收敛。")
    return out


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


def _granted_true_sinr_db(table: Any, snap: int, rank: int,
                          indices: Sequence[int], fallback_db: float) -> float:
    """真实解码 SINR 只取**实际授予的那几个 RBG**。

    这些逐 RBG 值是同一份 gNB 发射权打到 ``h_true`` 后按经典 MMSE 算出来的
    后处理 SINR（见 :func:`csi_aging.rank_adaptation_aged`），不是从全带值折
    算的。宽带路径早先直接用全带均值：一个只占 1 个 RBG 的小包，误块概率却
    按 17 个 RBG 的平均信道判——频选衰落越深，这个错越大，而且**两个方向都
    可能错**（授到好 RBG 时高估误块，授到坏 RBG 时低估）。

    ``fallback_db`` 只服务没有逐 RBG 真值的手工链路表；正常建表一定有。
    """
    rbg = getattr(table, "sinr_rbg_db", None)
    if (rbg is not None and len(indices)
            and max(int(x) for x in indices) < int(np.shape(rbg)[-1])):
        return _subset_db(rbg[snap, rank - 1], indices)
    return float(fallback_db)


def _granted_pair_true_sinr_db(link: Any, snap: int, side: int,
                               indices: Sequence[int],
                               fallback_db: float) -> float:
    """MU 配对用户的真实解码 SINR，同样只取实际授予的 RBG。

    ``true_sinr_rbg_db`` 来自 ``mu_link_performance_lmmse``：同一套 ZF/RZF 权
    打到两个用户的 ``h_true`` 上，对方的流进入干扰协方差，再逐用户 LMMSE
    检测。它是真算出来的，不是在 SU SINR 上折一个配对余量。
    """
    rbg = getattr(link, "true_sinr_rbg_db", None)
    if (rbg is not None and len(indices)
            and max(int(x) for x in indices) < int(np.shape(rbg)[-1])):
        return _subset_db(rbg[snap, side], indices)
    return float(fallback_db)


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


def _frequency_pool_audit(
    indices: Sequence[int], score: Sequence[float], *, cursor: int,
    evaluate: Callable[[tuple[int, ...]], Any],
    tbs_of: Callable[[Any], int], queue_bytes: int,
) -> tuple[int, bool, int]:
    """Audit minimum sufficient RBGs and maximum TBS on one resource pool.

    Frequency-aware MCS can change when another RBG joins the bitmap, so TBS is
    not assumed monotonic in prefix length.  Examine the same sequential and
    quality prefixes as the selector, deduplicate identical bitmaps, and keep
    the smallest fitting count plus the maximum achievable TBS.
    """
    pool = tuple(int(value) for value in indices)
    if not pool:
        raise ValueError("frequency audit pool cannot be empty")
    orders = (
        sfreq.rotated_order(pool, cursor=cursor, total_rbg=len(score)),
        sfreq.quality_order(pool, score, cursor=cursor),
    )
    seen: set[frozenset[int]] = set()
    rows: list[tuple[int, int]] = []
    for order in orders:
        for count in range(1, len(order) + 1):
            bitmap = tuple(order[:count])
            key = frozenset(bitmap)
            if key in seen:
                continue
            seen.add(key)
            rows.append((count, int(tbs_of(evaluate(bitmap)))))
    potential = max(tbs for _count, tbs in rows)
    fitting = [count for count, tbs in rows if tbs >= int(queue_bytes)]
    return (
        min(fitting) if fitting else len(pool),
        bool(fitting),
        int(potential),
    )


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
    available: set[int] = set(range(int(num_rbg)))
    remaining = len(available)
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
        frequency_score_gain = 0.0
        frequency_incremental = 0
        frequency_evaluated = 0
        frequency_source = "wideband_or_sequential"
        full_need = remaining_need = int(num_rbg)
        full_fits = remaining_fits = False
        full_potential = int(potential_of[u])
        if pending is not None:
            if q < int(pending.payload_bytes):
                raise RuntimeError(
                    f"UE {u} HARQ 队列只剩 {q} B，小于冻结 payload "
                    f"{pending.payload_bytes} B")
            full_need = remaining_need = int(pending.n_rbg)
            full_fits = remaining_fits = True
            if remaining_need > remaining:
                continue
            n = remaining_need
            if frequency_aware:
                if tables[u].sinr_rbg_db is None:
                    raise ValueError("频选重传需要逐 RBG true SINR")
                base_rows = (tables[u].sinr_tx_rbg_db
                             if tables[u].sinr_tx_rbg_db is not None
                             else tables[u].sinr_rbg_db)
                score = np.asarray(base_rows[snap, rank - 1], dtype=float)
                ordered = sfreq.quality_order(
                    tuple(available), score, cursor=cursor)
                indices = tuple(ordered[:n])
                frequency_score_gain = float(
                    np.mean(score[np.asarray(indices, dtype=int)])
                    - np.mean(score[np.asarray(tuple(available), dtype=int)]))
                frequency_source = "quality_fixed_count_retx"
                base_tx = _subset_db(base_rows[snap, rank - 1], indices)
                true_sinr = _subset_db(
                    tables[u].sinr_rbg_db[snap, rank - 1], indices)
            else:
                indices = tuple(sfreq.rotated_order(
                    tuple(available), cursor=cursor, total_rbg=num_rbg)[:n])
                base_rows = (tables[u].sinr_tx_db
                             if tables[u].sinr_tx_db is not None
                             else tables[u].sinr_db)
                base_tx = float(base_rows[snap, rank - 1])
                true_sinr = _granted_true_sinr_db(
                    tables[u], snap, rank, indices,
                    float(tables[u].sinr_db[snap, rank - 1]))
            no_olla_mcs = _select_mcs(base_tx, lookup)
            current_tbs = int(
                lookup.tbs_bytes_for_indices(slot, mcs, rank, indices))
            if current_tbs != int(pending.tb_bytes):
                raise RuntimeError(
                    "HARQ 重传的同 MCS/RBG/rank 未复现原 TBS："
                    f"UE {u}, original={pending.tb_bytes} B, current={current_tbs} B")
            tbs = int(pending.tb_bytes)
        elif frequency_aware:
            table = tables[u]
            base_rows = (table.sinr_tx_rbg_db if table.sinr_tx_rbg_db is not None
                         else table.sinr_rbg_db)
            if base_rows is None:
                raise ValueError("频选调度需要逐 RBG predicted SINR")
            score = np.asarray(base_rows[snap, rank - 1], dtype=float)
            trial_cache: dict[tuple[int, ...], dict[str, Any]] = {}

            def _evaluate_su(
                indices_value: tuple[int, ...],
                *,
                table_value: Any = table,
                rank_value: int = rank,
                user_value: int = u,
                queue_value: int = q,
                cache: dict[tuple[int, ...], dict[str, Any]] = trial_cache,
            ) -> dict[str, Any]:
                key = tuple(sorted(int(value) for value in indices_value))
                cached = cache.get(key)
                if cached is not None:
                    return cached
                trial = _frequency_su_values(
                    table=table_value, snap=snap, rank=rank_value,
                    indices=key,
                    olla_db=float(su_olla_db[user_value]),
                    olla_enabled=olla_enabled,
                    lookup=lookup, slot=slot)
                trial["useful_bytes"] = (
                    min(queue_value, int(trial["tbs"])),)
                cache[key] = trial
                return trial

            selection = sfreq.select_frequency_subset(
                tuple(available), score, cursor=cursor,
                evaluate=_evaluate_su,
                sufficient=lambda trial, queue_value=q: (
                    int(trial["tbs"]) >= queue_value))
            indices = tuple(selection.selected_indices)
            n = len(indices)
            values = selection.selected_grant
            remaining_need, remaining_fits, _remaining_potential = (
                _frequency_pool_audit(
                    tuple(available), score, cursor=cursor,
                    evaluate=_evaluate_su,
                    tbs_of=lambda trial: int(trial["tbs"]),
                    queue_bytes=q))
            full_need, full_fits, full_potential = _frequency_pool_audit(
                tuple(range(int(num_rbg))), score, cursor=cursor,
                evaluate=_evaluate_su,
                tbs_of=lambda trial: int(trial["tbs"]),
                queue_bytes=q)
            frequency_score_gain = float(selection.selection_score_gain)
            frequency_incremental = int(selection.incremental_useful_bytes)
            frequency_evaluated = int(selection.evaluated_subset_count)
            frequency_source = str(selection.selected_source)
            mcs = int(values["mcs"])
            tbs = int(values["tbs"])
            base_tx = float(values["base"])
            no_olla_mcs = int(values["mcs_without_olla"])
            true_sinr = float(values["true"])
        else:
            full_order = _grant_indices(cursor, offset, num_rbg, num_rbg)
            full_need, full_fits = lookup.required_rbg_for_indices(
                slot, mcs, rank, q, full_order)
            remaining_order = sfreq.rotated_order(
                tuple(available), cursor=cursor, total_rbg=num_rbg)
            remaining_need, remaining_fits = lookup.required_rbg_for_indices(
                slot, mcs, rank, q, remaining_order)
            n = min(int(remaining_need), remaining)
            indices = tuple(remaining_order[:n])
            tbs = lookup.tbs_bytes_for_indices(slot, mcs, rank, indices)
            base_tx = float(base_tx_sinr_of[u])
            no_olla_mcs = int(mcs_without_olla_of[u])
            true_sinr = _granted_true_sinr_db(
                tables[u], snap, rank, indices, float(true_sinr_of[u]))
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
            power_loss_db=0.0, required_rbg=(int(full_need),),
            fits_in_fullband=(bool(full_fits),), tbs_bytes=(int(tbs),),
            useful_bytes=(int(useful),),
            potential_fullband_bytes=(int(full_potential),),
            required_rbg_from_remaining_pool=(int(remaining_need),),
            fits_in_remaining_pool=(bool(remaining_fits),),
            frequency_selection_score_gain=frequency_score_gain,
            frequency_incremental_useful_bytes=frequency_incremental,
            frequency_evaluated_subsets=frequency_evaluated,
            frequency_selected_source=frequency_source))
        available.difference_update(indices)
        remaining = len(available)
    total_q = int(sum(queue_bytes.values()))
    useful_total = int(sum(sum(g.useful_bytes) for g in grants))
    return _TtiPlan(
        name="SU", grants=tuple(grants), useful_bytes=useful_total,
        used_rbg=int(num_rbg) - remaining, has_mu=False,
        # ``total_q``只含本 TTI 的 serviceable candidates。outage UE 或错
        # slot HARQ 已在 cand 前门排除，不能再用系统别处的 blocked backlog
        # 否决“SU 清空全部可服务队列则强制 SU”的产品合同。
        clears_all_queues=(useful_total == total_q))


def _build_mu_plan(
    ordered_users: Sequence[int], *, queue_bytes: dict[int, int],
    lookup: TbsLookup, slot: str, num_rbg: int,
    rank_of: dict[int, int], mcs_of: dict[int, int],
    base_tx_sinr_of: dict[int, float], mcs_without_olla_of: dict[int, int],
    true_sinr_of: dict[int, float], potential_of: dict[int, int],
    tables: Sequence[Any], snap: int, sched: Any,
    su_olla_db: np.ndarray, mu_olla_db: np.ndarray, blocked_data: bool,
    cursor: int = 0, frequency_aware: bool = False,
    pair_evaluation_cache: dict[tuple[Any, ...], smu.MuCandidateEvaluation] | None = None,
) -> _TtiPlan:
    """PF anchor fixed; enumerate and score every feasible two-user rank2 pair."""
    ordered = [int(value) for value in ordered_users]
    pending = set(ordered)
    available: set[int] = set(range(int(num_rbg)))
    grants: list[_PlannedGrant] = []
    decisions: list[smu.MuCandidateDecision] = []
    corr_thr = float(getattr(sched, "mu_corr_threshold", 0.7))
    mu_rank = int(getattr(sched, "mu_rank_per_user", 2))
    olla_enabled = bool(getattr(sched, "olla_enabled", True))
    min_pairing_mcs = int(getattr(sched, "min_pairing_mcs", 4))
    orthogonalization_mode = str(
        getattr(sched, "orthogonalization_mode", "select"))
    if orthogonalization_mode not in ("none", "select", "schmidt"):
        raise ValueError("orthogonalization_mode 只支持 none / select / schmidt")
    if orthogonalization_mode == "schmidt":
        raise NotImplementedError("TODO: Schmidt 正交化未实现")

    def _single_user_grant(user: int) -> _PlannedGrant | None:
        q = int(queue_bytes[user])
        rank = int(rank_of[user])
        mcs = int(mcs_of[user])
        offset = int(num_rbg) - len(available)
        score_gain = 0.0
        incremental = 0
        evaluated = 0
        source = "wideband_or_sequential"
        full_need = remaining_need = int(num_rbg)
        full_fits = remaining_fits = False
        full_potential = int(potential_of[user])
        if frequency_aware:
            table = tables[user]
            base_rows = (table.sinr_tx_rbg_db if table.sinr_tx_rbg_db is not None
                         else table.sinr_rbg_db)
            if base_rows is None:
                raise ValueError("频选调度需要逐 RBG predicted SINR")
            score = np.asarray(base_rows[snap, rank - 1], dtype=float)
            trial_cache: dict[tuple[int, ...], dict[str, Any]] = {}

            def _evaluate(indices_value: tuple[int, ...]) -> dict[str, Any]:
                key = tuple(sorted(int(value) for value in indices_value))
                cached = trial_cache.get(key)
                if cached is not None:
                    return cached
                value = _frequency_su_values(
                    table=table, snap=snap, rank=rank, indices=key,
                    olla_db=float(su_olla_db[user]),
                    olla_enabled=olla_enabled, lookup=lookup, slot=slot)
                value["useful_bytes"] = (min(q, int(value["tbs"])),)
                trial_cache[key] = value
                return value

            selection = sfreq.select_frequency_subset(
                tuple(available), score, cursor=cursor, evaluate=_evaluate,
                sufficient=lambda value: int(value["tbs"]) >= q)
            indices = tuple(selection.selected_indices)
            value = selection.selected_grant
            n = len(indices)
            remaining_need, remaining_fits, _remaining_potential = (
                _frequency_pool_audit(
                    tuple(available), score, cursor=cursor,
                    evaluate=_evaluate,
                    tbs_of=lambda trial: int(trial["tbs"]),
                    queue_bytes=q))
            full_need, full_fits, full_potential = _frequency_pool_audit(
                tuple(range(int(num_rbg))), score, cursor=cursor,
                evaluate=_evaluate,
                tbs_of=lambda trial: int(trial["tbs"]),
                queue_bytes=q)
            mcs = int(value["mcs"])
            tbs = int(value["tbs"])
            base_tx = float(value["base"])
            no_olla = int(value["mcs_without_olla"])
            true_sinr = float(value["true"])
            score_gain = float(selection.selection_score_gain)
            incremental = int(selection.incremental_useful_bytes)
            evaluated = int(selection.evaluated_subset_count)
            source = str(selection.selected_source)
        else:
            full_order = _grant_indices(cursor, offset, num_rbg, num_rbg)
            full_need, full_fits = lookup.required_rbg_for_indices(
                slot, mcs, rank, q, full_order)
            remaining_order = sfreq.rotated_order(
                tuple(available), cursor=cursor, total_rbg=num_rbg)
            remaining_need, remaining_fits = lookup.required_rbg_for_indices(
                slot, mcs, rank, q, remaining_order)
            n = min(int(remaining_need), len(available))
            indices = tuple(remaining_order[:n])
            tbs = int(lookup.tbs_bytes_for_indices(slot, mcs, rank, indices))
            base_tx = float(base_tx_sinr_of[user])
            no_olla = int(mcs_without_olla_of[user])
            true_sinr = _granted_true_sinr_db(
                tables[user], snap, rank, indices, float(true_sinr_of[user]))
        useful = min(q, int(tbs))
        if useful <= 0:
            return None
        return _PlannedGrant(
            mode="SU", users=(user,), rbg_indices=indices, n_rbg=len(indices),
            ranks=(rank,), mcs=(mcs,), base_tx_sinr_db=(base_tx,),
            mcs_without_olla=(no_olla,), true_sinr_db=(true_sinr,),
            corr_loss_db=(0.0,), power_loss_db=0.0,
            required_rbg=(int(full_need),),
            fits_in_fullband=(bool(full_fits),),
            tbs_bytes=(int(tbs),), useful_bytes=(int(useful),),
            potential_fullband_bytes=(int(full_potential),),
            required_rbg_from_remaining_pool=(int(remaining_need),),
            fits_in_remaining_pool=(bool(remaining_fits),),
            frequency_selection_score_gain=score_gain,
            frequency_incremental_useful_bytes=incremental,
            frequency_evaluated_subsets=evaluated,
            frequency_selected_source=source)

    def _reject(
        anchor: int, partner: int, pf_order: int, reason: str,
        *, correlation: float | None = None,
        predicted_bler_max: float | None = None,
    ) -> smu.MuCandidateEvaluation:
        return smu.MuCandidateEvaluation(
            anchor_ue=anchor, partner_ue=partner, pf_order=pf_order,
            feasible=False, rejection_reason=reason, correlation=correlation,
            predicted_bler_max=predicted_bler_max, useful_bytes=0,
            used_rbg=0, useful_bytes_per_rbg=0.0, final_mcs=(), grant=None)

    def _evaluate_pair(
        anchor: int, partner: int, pf_order: int,
    ) -> smu.MuCandidateEvaluation:
        cache_key: tuple[Any, ...] | None = None
        if pair_evaluation_cache is not None:
            cache_key = (
                int(anchor), int(partner), int(snap), str(slot),
                tuple(sorted(available)), int(cursor) % int(num_rbg),
                int(queue_bytes[anchor]), int(queue_bytes[partner]),
                float(su_olla_db[anchor]), float(su_olla_db[partner]),
                float(mu_olla_db[anchor]), float(mu_olla_db[partner]),
                bool(olla_enabled), bool(frequency_aware),
            )
            cached_pair = pair_evaluation_cache.get(cache_key)
            if cached_pair is not None:
                return replace(cached_pair, pf_order=int(pf_order))
        link = getattr(tables[anchor], "mu_links", {}).get(partner)
        if link is None:
            return _reject(anchor, partner, pf_order, "missing_pair_link")
        below = [
            user for user in (anchor, partner)
            if int(mcs_of[user]) < min_pairing_mcs
        ]
        if below:
            return _reject(
                anchor, partner, pf_order,
                "mcs_below_min_pairing")
        correlation = float(link.correlation[snap])
        if not np.isfinite(correlation):
            return _reject(anchor, partner, pf_order, "nonfinite_correlation")
        if (orthogonalization_mode == "select"
                and correlation > corr_thr):
            return _reject(
                anchor, partner, pf_order, "correlation_threshold",
                correlation=correlation)
        if 2 * mu_rank > int(getattr(sched, "max_layers_per_rbg", 4)):
            return _reject(
                anchor, partner, pf_order, "layer_limit",
                correlation=correlation)

        users = (anchor, partner)
        offset = int(num_rbg) - len(available)
        score_gain = 0.0
        incremental = 0
        evaluated = 0
        source = "wideband_or_sequential"
        if frequency_aware:
            if (link.true_sinr_rbg_db is None
                    or link.corr_loss_tx_rbg_db is None):
                return _reject(
                    anchor, partner, pf_order, "missing_rbg_pair_sinr",
                    correlation=correlation)
            score = np.zeros(int(num_rbg), dtype=float)
            for user in users:
                side = int(link.side(user))
                table = tables[user]
                base_rows = (table.sinr_tx_rbg_db
                             if table.sinr_tx_rbg_db is not None
                             else table.sinr_rbg_db)
                if base_rows is None:
                    return _reject(
                        anchor, partner, pf_order, "missing_rbg_su_sinr",
                        correlation=correlation)
                score += (
                    np.asarray(base_rows[snap, mu_rank - 1], dtype=float)
                    + np.asarray(link.corr_loss_tx_rbg_db[snap, side], dtype=float)
                    + float(link.power_loss_db))

            trial_cache: dict[tuple[int, ...], dict[str, Any]] = {}

            def _evaluate(indices_value: tuple[int, ...]) -> dict[str, Any]:
                # TBS/SINR only depends on the bitmap, not its presentation order.
                # The selector and the per-user "required RBG" audit examine many
                # identical prefixes; memoizing here halves the MU hot path without
                # changing one bit of the decision.
                key = tuple(sorted(int(value) for value in indices_value))
                cached = trial_cache.get(key)
                if cached is not None:
                    return cached
                values = _frequency_mu_values(
                    pair_link=link, users=users, tables=tables, snap=snap,
                    rank=mu_rank, indices=key,
                    su_olla_db=su_olla_db, mu_olla_db=mu_olla_db,
                    olla_enabled=olla_enabled, lookup=lookup, slot=slot)
                result = {
                    "values": values,
                    "useful_bytes": tuple(
                        min(int(queue_bytes[user]), int(values[side]["tbs"]))
                        for side, user in enumerate(users)),
                }
                trial_cache[key] = result
                return result

            selection = sfreq.select_frequency_subset(
                tuple(available), score, cursor=cursor, evaluate=_evaluate,
                sufficient=lambda value: all(
                    int(value["values"][side]["tbs"]) >= int(queue_bytes[user])
                    for side, user in enumerate(users)))
            indices = tuple(selection.selected_indices)
            actual = selection.selected_grant["values"]
            remaining_needs: list[int] = []
            remaining_fits: list[bool] = []
            needs = []
            fits_list = []
            potentials = []
            for side, user in enumerate(users):
                remaining_need, remaining_fit, _remaining_potential = (
                    _frequency_pool_audit(
                        tuple(available), score, cursor=cursor,
                        evaluate=_evaluate,
                        tbs_of=lambda trial, side_value=side: int(
                            trial["values"][side_value]["tbs"]),
                        queue_bytes=int(queue_bytes[user])))
                full_need, full_fit, full_potential = _frequency_pool_audit(
                    tuple(range(int(num_rbg))), score, cursor=cursor,
                    evaluate=_evaluate,
                    tbs_of=lambda trial, side_value=side: int(
                        trial["values"][side_value]["tbs"]),
                    queue_bytes=int(queue_bytes[user]))
                remaining_needs.append(int(remaining_need))
                remaining_fits.append(bool(remaining_fit))
                needs.append(int(full_need))
                fits_list.append(bool(full_fit))
                potentials.append(int(full_potential))
            score_gain = float(selection.selection_score_gain)
            incremental = int(selection.incremental_useful_bytes)
            evaluated = int(selection.evaluated_subset_count)
            source = str(selection.selected_source)
        else:
            full_order = _grant_indices(cursor, offset, num_rbg, num_rbg)
            actual = []
            needs = []
            fits_list = []
            remaining_needs = []
            remaining_fits = []
            potentials = []
            remaining_order = sfreq.rotated_order(
                tuple(available), cursor=cursor, total_rbg=num_rbg)
            for user in users:
                side = int(link.side(user))
                base_rows = (tables[user].sinr_tx_db
                             if tables[user].sinr_tx_db is not None
                             else tables[user].sinr_db)
                base = float(base_rows[snap, mu_rank - 1])
                corr = float(link.corr_loss_tx_db[snap, side])
                mcs_input = base + corr + float(link.power_loss_db)
                no_olla = _select_mcs(mcs_input, lookup)
                mcs = (int(la.apply_olla_mcs(
                    no_olla, float(su_olla_db[user]) + float(mu_olla_db[user]),
                    mcs_table=int(lookup.mcs_table))["final_mcs"])
                    if olla_enabled else no_olla)
                need, fits = lookup.required_rbg_for_indices(
                    slot, mcs, mu_rank, int(queue_bytes[user]), full_order)
                remaining_need, remaining_fit = lookup.required_rbg_for_indices(
                    slot, mcs, mu_rank, int(queue_bytes[user]), remaining_order)
                actual.append({
                    "base": base, "corr": corr,
                    "true": float(link.true_sinr_db[snap, side]),
                    "mcs": mcs, "mcs_without_olla": no_olla})
                needs.append(int(need))
                fits_list.append(bool(fits))
                remaining_needs.append(int(remaining_need))
                remaining_fits.append(bool(remaining_fit))
                potentials.append(int(lookup.tbs_bytes_for_indices(
                    slot, mcs, mu_rank, full_order)))
            # One MU grant owns a shared RBG bitmap.  The pair must therefore
            # keep allocating until both queues fit (or resources run out).
            # Stopping when the first/small queue fits leaves the larger user
            # unfinished, removes both from pending, and can strand the
            # remaining RBGs—an especially damaging small+large packet bug.
            n = min(max(remaining_needs), len(available))
            indices = tuple(remaining_order[:n])
            # 位图定下来之后才知道解码 SINR 该在哪几个 RBG 上取。
            for value, user in zip(actual, users, strict=True):
                value["tbs"] = int(lookup.tbs_bytes_for_indices(
                    slot, int(value["mcs"]), mu_rank, indices))
                value["true"] = _granted_pair_true_sinr_db(
                    link, snap, int(link.side(user)), indices,
                    float(value["true"]))

        mcs_list = tuple(int(value["mcs"]) for value in actual)
        if any(mcs < min_pairing_mcs for mcs in mcs_list):
            return _reject(
                anchor, partner, pf_order, "pair_mcs_below_min_pairing",
                correlation=correlation)
        predicted_blers = tuple(
            _bler_lookup(
                int(value["mcs"]),
                float(value["base"]) + float(value["corr"])
                + float(link.power_loss_db))
            for value in actual)
        max_predicted_bler = max(predicted_blers)
        if (not all(np.isfinite(
                float(value["base"]) + float(value["corr"])
                + float(link.power_loss_db)) for value in actual)
                or max_predicted_bler > 0.5):
            return _reject(
                anchor, partner, pf_order, "predicted_bler_gt_0.5",
                correlation=correlation,
                predicted_bler_max=float(max_predicted_bler))
        useful = tuple(
            min(int(queue_bytes[user]), int(actual[side]["tbs"]))
            for side, user in enumerate(users))
        grant = _PlannedGrant(
            mode="MU", users=users, rbg_indices=indices, n_rbg=len(indices),
            ranks=(mu_rank, mu_rank), mcs=mcs_list,
            base_tx_sinr_db=tuple(float(value["base"]) for value in actual),
            mcs_without_olla=tuple(
                int(value["mcs_without_olla"]) for value in actual),
            true_sinr_db=tuple(float(value["true"]) for value in actual),
            corr_loss_db=tuple(float(value["corr"]) for value in actual),
            power_loss_db=float(link.power_loss_db),
            required_rbg=tuple(int(value) for value in needs),
            fits_in_fullband=tuple(bool(value) for value in fits_list),
            tbs_bytes=tuple(int(value["tbs"]) for value in actual),
            useful_bytes=useful,
            potential_fullband_bytes=tuple(int(value) for value in potentials),
            required_rbg_from_remaining_pool=tuple(
                int(value) for value in remaining_needs),
            fits_in_remaining_pool=tuple(
                bool(value) for value in remaining_fits),
            pair_correlation=correlation,
            frequency_selection_score_gain=score_gain,
            frequency_incremental_useful_bytes=incremental,
            frequency_evaluated_subsets=evaluated,
            frequency_selected_source=source)
        useful_total = int(sum(useful))
        density = float(useful_total / max(len(indices), 1))
        result = smu.MuCandidateEvaluation(
            anchor_ue=anchor, partner_ue=partner, pf_order=pf_order,
            feasible=True, rejection_reason=None, correlation=correlation,
            predicted_bler_max=float(max_predicted_bler),
            useful_bytes=useful_total, used_rbg=len(indices),
            useful_bytes_per_rbg=density, final_mcs=mcs_list, grant=grant)
        if pair_evaluation_cache is not None and cache_key is not None:
            pair_evaluation_cache[cache_key] = result
        return result

    for anchor in ordered:
        if not available:
            break
        if anchor not in pending:
            continue
        pending.remove(anchor)
        evaluations: list[smu.MuCandidateEvaluation] = []
        if int(getattr(sched, "max_mu_users", 2)) >= 2 and mu_rank == 2:
            for pf_order, partner in enumerate(ordered):
                if partner in pending:
                    evaluations.append(_evaluate_pair(anchor, partner, pf_order))
        decision = smu.choose_mu_candidate(anchor, evaluations)
        decisions.append(decision)
        if decision.selected_partner_ue is not None:
            partner = int(decision.selected_partner_ue)
            pending.remove(partner)
            selected = replace(
                decision.selected_grant,
                candidate_score=float(decision.selected_score),
                candidate_count=len(decision.evaluations),
                rejected_candidate_reasons=decision.rejection_reasons)
            grants.append(selected)
            available.difference_update(selected.rbg_indices)
        else:
            fallback = _single_user_grant(anchor)
            if fallback is not None:
                grants.append(fallback)
                available.difference_update(fallback.rbg_indices)

    total_q = int(sum(queue_bytes.values()))
    useful_total = int(sum(sum(grant.useful_bytes) for grant in grants))
    return _TtiPlan(
        name="MU", grants=tuple(grants), useful_bytes=useful_total,
        used_rbg=int(num_rbg) - len(available),
        has_mu=any(grant.mode == "MU" for grant in grants),
        # 与 SU 使用同一 serviceable-queue 分母；blocked_data 只作诊断，
        # 不改变当前候选计划是否已经清空所有可服务队列。
        clears_all_queues=(useful_total == total_q),
        mu_candidate_decisions=tuple(decisions))


def _admit_plan_resources(
    plan: _TtiPlan,
    *,
    budget: sres.ResourceBudget,
    tti: int,
) -> _TtiPlan:
    """Apply the same transactional resource contract before SU/MU comparison."""
    ledger = sres.ResourceLedger(budget, tti=tti)
    admission = ledger.admit_grants(plan.grants)
    accepted: list[_PlannedGrant] = []
    for grant_index, reservation_id in zip(
            admission.accepted_grant_indices,
            admission.reservation_ids, strict=True):
        accepted.append(replace(
            plan.grants[int(grant_index)], reservation_id=reservation_id))
    useful = int(sum(sum(grant.useful_bytes) for grant in accepted))
    used = len({
        index for grant in accepted for index in grant.rbg_indices
    })
    return replace(
        plan,
        grants=tuple(accepted),
        useful_bytes=useful,
        used_rbg=used,
        has_mu=any(grant.mode == "MU" for grant in accepted),
        clears_all_queues=(plan.clears_all_queues and not admission.rejections),
        resource_admission=admission,
    )


def _finalize_selected_plan(
    plan: _TtiPlan,
    *,
    queue_bytes: dict[int, int],
    lookup: TbsLookup,
    slot: str,
    su_olla_db: np.ndarray,
    mu_olla_db: np.ndarray,
    olla_enabled: bool,
    harq_pending: dict[int, _HarqTb],
    tables: Sequence[Any],
) -> tuple[sfinal.FinalGrant, ...]:
    """Recompute every executable physical value and hard-compare the estimate."""
    out: list[sfinal.FinalGrant] = []
    for grant in plan.grants:
        olla_values: list[float] = []
        frozen_mcs: list[int | None] = []
        frozen_tbs: list[int | None] = []
        frozen_payload: list[int | None] = []
        explicit_newtx_mcs: list[int | None] = []
        for user in grant.users:
            pending = harq_pending.get(int(user))
            olla_values.append(
                float(su_olla_db[int(user)])
                + (float(mu_olla_db[int(user)]) if grant.mode == "MU" else 0.0)
            )
            frozen_mcs.append(None if pending is None else int(pending.mcs))
            frozen_tbs.append(None if pending is None else int(pending.tb_bytes))
            frozen_payload.append(None if pending is None else int(pending.payload_bytes))
            explicit_newtx_mcs.append(
                int(grant.mcs[len(explicit_newtx_mcs)])
                if pending is None
                and getattr(tables[int(user)], "sinr_tx_db", None) is None
                and getattr(tables[int(user)], "sinr_tx_rbg_db", None) is None
                else None)
        candidate = sfinal.CandidateGrant(
            mode=grant.mode,
            users=tuple(int(value) for value in grant.users),
            rbg_indices=tuple(int(value) for value in grant.rbg_indices),
            ranks=tuple(int(value) for value in grant.ranks),
            base_predicted_sinr_db=tuple(
                float(value) for value in grant.base_tx_sinr_db),
            receive_sinr_db=tuple(float(value) for value in grant.true_sinr_db),
            corr_loss_db=tuple(float(value) for value in grant.corr_loss_db),
            power_loss_db=float(grant.power_loss_db),
            olla_mcs=tuple(olla_values),
            queue_bytes=tuple(int(queue_bytes[int(user)]) for user in grant.users),
            required_rbg=tuple(int(value) for value in grant.required_rbg),
            fits_in_fullband=tuple(bool(value) for value in grant.fits_in_fullband),
            potential_fullband_bytes=tuple(
                int(value) for value in grant.potential_fullband_bytes),
            required_rbg_from_remaining_pool=tuple(
                int(value) for value in grant.required_rbg_from_remaining_pool),
            fits_in_remaining_pool=tuple(
                bool(value) for value in grant.fits_in_remaining_pool),
            pair_correlation=grant.pair_correlation,
            frozen_mcs=tuple(frozen_mcs),
            frozen_tbs_bytes=tuple(frozen_tbs),
            frozen_payload_bytes=tuple(frozen_payload),
            explicit_newtx_mcs=tuple(explicit_newtx_mcs),
            candidate_score=grant.candidate_score,
            candidate_count=int(grant.candidate_count),
            rejected_candidate_reasons=tuple(grant.rejected_candidate_reasons),
            frequency_selection_score_gain=float(
                grant.frequency_selection_score_gain),
            frequency_incremental_useful_bytes=int(
                grant.frequency_incremental_useful_bytes),
            frequency_evaluated_subsets=int(grant.frequency_evaluated_subsets),
            frequency_selected_source=str(grant.frequency_selected_source),
        )
        finalized = sfinal.finalize_candidate_grant(
            candidate, lookup=lookup, slot=slot, olla_enabled=olla_enabled)
        if (
            finalized.mcs != tuple(int(value) for value in grant.mcs)
            or finalized.tbs_bytes != tuple(int(value) for value in grant.tbs_bytes)
            or finalized.useful_bytes != tuple(int(value) for value in grant.useful_bytes)
        ):
            raise RuntimeError(
                "GrantFinalizer 与候选计划不一致："
                f"plan(mcs={grant.mcs},tbs={grant.tbs_bytes},useful={grant.useful_bytes}) "
                f"!= final(mcs={finalized.mcs},tbs={finalized.tbs_bytes},"
                f"useful={finalized.useful_bytes})"
            )
        if grant.reservation_id is None:
            raise RuntimeError("FinalGrant 缺少资源账本 reservation_id")
        out.append(finalized.with_reservation(grant.reservation_id))
    return tuple(out)


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
    if str(traffic_cfg.model) not in (
            "mixed", "cdf", "ftp3", "full_buffer", "cbr"):
        raise ValueError(f"不支持的话务模型 {traffic_cfg.model!r}")
    if str(sched.algorithm) not in (
            "pf", "qos_pf", "rr", "max_ci", "edf", "qos_pf_edf"):
        raise ValueError(f"experience_v2 不支持调度器 {sched.algorithm!r}")
    if (str(sched.algorithm) in ("edf", "qos_pf_edf")
            and str(traffic_cfg.model) == "full_buffer"):
        # EDF 的分母是真实待发缓冲区。full_buffer 把队列钉在 2**50 B，比值退化成
        # max_ci 的常数缩放；更糟的是队列会随已服务字节缓慢减小，被服务得多的
        # 用户分母更小、优先级反而更高，形成纯建模伪影的正反馈饥饿。硬失败，
        # 不做静默降级。
        raise ValueError(
            f"{sched.algorithm} 需要有限队列，不接受 full_buffer 话务；"
            "容量口径（话务开到最大）请用 pf / max_ci，长期公平口径请用 qos_pf")
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
        raise ValueError("pf_accounting 只支持 scheduled_tbs / acked_goodput / "
                         "legacy_fullband")

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
    mu_pair_graph: dict[str, Any] | None = None
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
        if bool(sched.mu_enabled) and table.sinr_db.shape[1] < 2:
            raise ValueError(f"UE {i} 不支持 MU rank2")
    if bool(sched.mu_enabled):
        try:
            mu_pair_graph = smu.validate_pair_graph(tables)
        except ValueError as exc:
            raise ValueError(
                "已启用 MU，但链路表没有完整、双向且维度一致的 pair graph；"
                f"请用 build_link_tables(..., mu_enabled=True) 重新预计算：{exc}") from exc
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
    frequency_mode = str(getattr(sched, "frequency_selective", "auto"))
    su_frequency_ready = all(
        getattr(table, "sinr_rbg_db", None) is not None
        and np.asarray(table.sinr_rbg_db).ndim == 3
        and int(np.asarray(table.sinr_rbg_db).shape[-1]) == int(sys_cfg.num_rbg)
        and (getattr(table, "sinr_tx_rbg_db", None) is not None
             or getattr(table, "sinr_rbg_db", None) is not None)
        and (getattr(table, "sinr_tx_rbg_db", None) is None
             or int(np.asarray(table.sinr_tx_rbg_db).shape[-1])
             == int(sys_cfg.num_rbg))
        for table in tables)
    mu_frequency_ready = (not bool(sched.mu_enabled)) or all(
        all(
            getattr(link, "true_sinr_rbg_db", None) is not None
            and getattr(link, "corr_loss_tx_rbg_db", None) is not None
            and int(np.asarray(link.true_sinr_rbg_db).shape[-1])
            == int(sys_cfg.num_rbg)
            and int(np.asarray(link.corr_loss_tx_rbg_db).shape[-1])
            == int(sys_cfg.num_rbg)
            for link in getattr(table, "mu_links", {}).values()
        )
        for table in tables)
    frequency_ready = bool(su_frequency_ready and mu_frequency_ready)
    if frequency_mode == "on" and not frequency_ready:
        raise ValueError(
            "frequency_selective='on' 需要完整逐 RBG SU/MU SINR；"
            "请重新 build_link_tables，不能静默退回宽带"
        )
    frequency_aware = bool(
        frequency_mode == "on" or (frequency_mode == "auto" and frequency_ready))
    resource_budget = sres.ResourceBudget(
        num_rbg=int(sys_cfg.num_rbg),
        rbg_prb_sizes=tuple(int(value) for value in rbg_sizes),
        max_layers_per_rbg=int(getattr(sched, "max_layers_per_rbg", 4)),
        max_logical_prb=(
            None if getattr(sched, "max_logical_prb_per_tti", None) is None
            else int(sched.max_logical_prb_per_tti)),
    )
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
    # --- HARQ 反馈时序 -------------------------------------------------
    # ACK/NACK 只能搭上行时隙回来，所以 OLLA 更新与重传资格都不在发送
    # 那个 TTI 生效。偏移逐 slot 相位算一次，主循环只做查表。
    feedback_delay_on = bool(getattr(sys_cfg, "harq_feedback_delay", True))
    pattern_len = len(pattern)
    feedback_offsets = (
        ap.feedback_effective_offsets(pattern) if feedback_delay_on
        else tuple(1 for _ in range(pattern_len)))
    feedback_modelled = feedback_delay_on and "U" in pattern
    # 首传 ACK 与 NACK 都进入 ``harq_pending``。抽样结果在 ready_tti 前只
    # 存在于 in-flight 事件里，不能被 OLLA/rank 看见，也不能让该 UE 发新 TB。
    feedback_wait_skips = 0
    # --- Rank 策略 -----------------------------------------------------
    rank_cfg = getattr(sched, "rank", None)
    if not isinstance(rank_cfg, ap.RankConfig):
        rank_cfg = ap.RankConfig()
    rank_ctl = ap.RankController(
        rank_cfg, n_ue, tti_ms=float(sys_cfg.tti_ms),
        snapshot_ms=float(sys_cfg.snapshot_update_ms),
        max_rank_available=min(
            int(tables[0].sinr_db.shape[1]),
            int(getattr(sched, "max_layers_per_rbg", 4))))
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
    mcs_first_sum_measured = np.zeros(n_ue, dtype=float)
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
    pf_gain_rejects = 0
    pf_gain_ratios: list[float] = []
    su_plan_useful = mu_plan_useful = 0
    allocated_rbg = scheduled_ues_sum = 0
    allocated_rbg_full = 0
    available_rbg_equiv = allocated_rbg_equiv = 0.0
    available_prb_equiv = allocated_prb_equiv = 0.0
    allocated_logical_prb_equiv = 0.0
    max_layers_used = 0
    resource_rejection_reasons: dict[str, int] = {}
    resource_evaluated_rejection_reasons: dict[str, int] = {}
    finalizer_grant_count = 0
    frequency_grant_count = 0
    frequency_quality_selected_count = 0
    frequency_score_gains: list[float] = []
    frequency_incremental_useful = 0
    frequency_evaluated_subsets = 0
    mu_candidate_count = 0
    mu_candidate_feasible_count = 0
    mu_candidate_selected_count = 0
    mu_candidate_rejection_reasons: dict[str, int] = {}
    mu_candidate_selected_scores: list[float] = []
    mu_pair_evaluation_cache: dict[
        tuple[Any, ...], smu.MuCandidateEvaluation] = {}
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
    # EDF / 混合模式的常量与量级取证累加器（其它算法下恒不使用）。
    srb_boost = float(getattr(sched, "srb_priority_boost", 5000.0))
    mixed_weight = float(getattr(sched, "edf_mixed_weight", 0.5))
    mixed_epf_scale = float(getattr(sched, "edf_mixed_epf_scale", 1.0))
    _starve = getattr(sched, "edf_starvation_hol_ms", None)
    starvation_hol_ms = None if _starve is None else float(_starve)
    starvation_lifts = 0
    srb_observed = False
    mixed_epf_medians: list[float] = []
    mixed_edf_medians: list[float] = []

    def _plan_pf_metric(plan: _TtiPlan) -> float:
        """当前 PF 状态下计划的线性化比例公平度量。"""
        return float(sum(
            float(useful) / max(float(r_avg[user]), _EPS)
            for grant in plan.grants
            for user, useful in zip(
                grant.users, grant.useful_bytes, strict=True)
        ))

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
        # 到期的 ACK/NACK 先同时交给 OLLA 与 RankController，再做本 TTI
        # 的决策。ACK 删除进程；NACK 转为唯一一次重传就绪状态。
        for _u_fb, _pending_fb in list(harq_pending.items()):
            if _pending_fb.state == "await_final_feedback":
                if _pending_fb.final_feedback_tti is None:
                    raise RuntimeError("终次 HARQ 反馈状态缺少生效 TTI")
                if tti >= int(_pending_fb.final_feedback_tti):
                    # 终次反馈只释放进程；不再进入首传 OLLA/rank 学习，
                    # 也不产生第三次传输。
                    harq_pending.pop(_u_fb, None)
                continue
            if (_pending_fb.state != "await_feedback"
                    or not _pending_fb.feedback.due(tti)):
                continue
            _pending_fb.feedback.apply(
                rank_controller=rank_ctl, su_olla=olla_db, mu_olla=mu_olla_db,
                olla_min=float(sched.olla_min_db),
                olla_max=float(sched.olla_max_db))
            if _pending_fb.first_ack:
                harq_pending.pop(_u_fb, None)
            else:
                harq_pending[_u_fb] = replace(
                    _pending_fb, state="retx_ready")
        # 快速回退会把 rank 与 OLLA 一起退回：新 rank 上的 OLLA 是在错误
        # 工作点上收敛出来的，只退 rank 会让旧 rank 带着别人的偏置继续跑。
        for _u_rk, _olla_rk in rank_ctl.step(tti, olla_by_ue=olla_db):
            olla_db[_u_rk] = float(min(max(
                _olla_rk, sched.olla_min_db), sched.olla_max_db))
        slot = pattern[tti % pattern_len]
        if slot not in ("D", "S"):
            continue
        dl_tti_full += 1
        dl_tti += int(in_measurement)
        slot_fraction = 1.0 if slot == "D" else float(s_slot_fraction)
        if in_measurement:
            available_rbg_equiv += int(sys_cfg.num_rbg) * slot_fraction
            available_prb_equiv += total_prb * slot_fraction
        snap = (tti // snap_every) % n_snap
        # 待重传的 TB 要等两件事：同类型时隙，以及 ACK/NACK 真的回来了。
        # 单 HARQ 进程模型下，这期间该 UE 也发不了新 TB。
        cand = [u for u in range(n_ue) if tr.has_data(u)
                and (u not in harq_pending
                     or (harq_pending[u].state == "retx_ready"
                         and harq_pending[u].slot == slot))
                and not (tables[u].outage is not None and tables[u].outage[snap])]
        if in_measurement:
            feedback_wait_skips += sum(
                1 for u in range(n_ue)
                if tr.has_data(u) and u in harq_pending
                and harq_pending[u].state in (
                    "await_feedback", "await_final_feedback"))
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
        # EDF 的分母：当前待发缓冲区。与 potential 同为 bytes，比值无量纲。
        buffer_bytes = np.zeros(len(cand), dtype=float)
        # SuperRAN 不建模逻辑信道，只有显式声明 resource_type="signalling" 的
        # 业务类才算 SRB；默认全 False，SRB 加值永不触发。
        srb_flag = np.zeros(len(cand), dtype=bool)
        # 时延兜底用的队首等待；关闭时保持全零且不参与任何运算。
        hol_ms = np.zeros(len(cand), dtype=float)
        for i, u in enumerate(cand):
            pending = harq_pending.get(u)
            if rank_ctl.adaptive and tables[u].sinr_tx_db is not None:
                _se_est, _mcs_est = _rank_se_estimates(
                    tables[u], snap, float(olla_db[u]),
                    olla_enabled=bool(sched.olla_enabled), lookup=lookup,
                    max_rank=rank_ctl.max_rank)
                rank_ctl.observe_link(u, snap, _se_est, _mcs_est)
            # **rank 不再逐快照跟着 best_rank 跳。** 默认固定 rank2；自适应
            # 模式由 RankController 按周期决策。重传沿用冻结的 rank。
            rank = (int(pending.rank) if pending is not None
                    else rank_ctl.rank_for(u, int(tables[u].best_rank[snap])))
            if pending is not None:
                base_rows = (tables[u].sinr_tx_db
                             if tables[u].sinr_tx_db is not None
                             else tables[u].sinr_db)
                base_tx_sinr = float(base_rows[snap, rank - 1])
                mcs = int(pending.mcs)
                mcs_without_olla = _select_mcs(base_tx_sinr, lookup)
            elif tables[u].sinr_tx_db is not None:
                # 硬合同：先用 CQI 门限 + BF Gain 的 SINR 反折无 OLLA MCS，
                # 再叠加连续 MCS 域 OLLA，floor 后钳到当前 profile。
                # **关掉 OLLA 只去掉最后这一步叠加，决策坐标不变。**
                # 早先 ``olla_enabled=False`` 会掉进下面的 else 分支，改用
                # 真实接收 SINR 反折出的 MCS——那是上帝视角：首传 BLER 被
                # 构造在目标值上，CSI 老化与 BF 失配的代价整个消失，
                # 于是"开/关 OLLA"的消融同时换掉了链路自适应的信息面。
                base_tx_sinr = float(tables[u].sinr_tx_db[snap, rank - 1])
                mcs_without_olla = _select_mcs(base_tx_sinr, lookup)
                mcs = (
                    int(la.apply_olla_mcs(
                        mcs_without_olla, float(olla_db[u]),
                        mcs_table=int(lookup.mcs_table),
                    )["final_mcs"])
                    if sched.olla_enabled else mcs_without_olla
                )
            else:
                # 链路表根本没有 AMC 预测坐标（手工构造的表）。这不是
                # "关掉 OLLA"，是"没有 CQI/BF 可用"，只能退回表自带的 MCS。
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
            buffer_bytes[i] = float(tr.bytes_left(u))
            if starvation_hol_ms is not None:
                hol_ms[i] = float(tr.hol_delay_ms(u, tti))
            srb_flag[i] = str(c.resource_type).strip().upper() == "SIGNALLING"
            if srb_flag[i]:
                srb_observed = True
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
        elif sched.algorithm == "edf":
            # m = TBS / Buffer × w(priority)。分子沿用全带宽 potential，与蓝本
            # “假设全带宽可用，只用于排序”一致；实际给几个 RBG 由后面的按需
            # 分配决定。HARQ pending 用户的 potential 是冻结的子带 TBS，但他们
            # 已被 pending_ready 整体前置。pending 内部按 first_tti 稳定排序，
            # first_tti 打平时才回落到 metric 顺序——那时组内所有人的分子都是
            # 冻结 tb_bytes，仍是同口径比较，所以口径不一致不会传导到排序。
            metric = sedf.edf_metric(
                potential, buffer_bytes, priority_factor,
                srb_mask=srb_flag, srb_priority_boost=srb_boost)
        elif sched.algorithm == "qos_pf_edf":
            alpha = float(getattr(sched, "qos_avg_rate_exponent", 1.0))
            beta = float(getattr(sched, "qos_instant_rate_exponent", 1.0))
            gamma = float(getattr(sched, "qos_delay_exponent", 0.0))
            # 与 qos_pf 分支逐项相同，只是把 priority_factor 提到混合之后乘，
            # 这样 w=0 时严格退化成 qos_pf（epf_scale 默认 1.0）。
            epf_core = (np.power(np.maximum(potential, 1.0), beta)
                        / np.power(np.maximum(r_avg[cand], 1e-9), alpha)
                        * np.power(delay_factor, gamma))
            edf_core = sedf.edf_metric(
                potential, buffer_bytes, np.ones(len(cand), dtype=float))
            metric = sedf.mixed_metric(
                epf_core, edf_core, priority_factor,
                weight=mixed_weight, epf_scale=mixed_epf_scale,
                srb_mask=srb_flag, srb_priority_boost=srb_boost)
            # 量级取证：两个分量不同量纲，epf_scale 未标定时 w 会被量级差吞掉。
            # 逐 TTI 记中位数，结果里报中位数的中位数，让 w 是否生效可被证伪。
            if in_measurement:
                mixed_epf_medians.append(float(np.median(epf_core)))
                mixed_edf_medians.append(float(np.median(edf_core)))
        elif sched.algorithm == "max_ci":
            metric = potential
        else:
            metric = np.zeros(len(cand), dtype=float)
        if (starvation_hol_ms is not None
                and str(sched.algorithm) in ("edf", "qos_pf_edf")):
            lifted = sedf.apply_starvation_guard(
                metric, hol_ms, threshold_ms=starvation_hol_ms)
            if in_measurement:
                starvation_lifts += int(np.count_nonzero(lifted != metric))
            metric = lifted
        order = _ordered_candidates(metric, cand, tti, str(sched.algorithm),
                                    n_ue, scheduler_draw[tti, cand])
        cand_pos = {int(u): i for i, u in enumerate(cand)}

        metric_order = [int(cand[int(oi)]) for oi in order]
        # HARQ 是同一 TB 的第二次且最后一次机会：同 D/S 类型可发时优先于
        # 新 TB，并按首传时刻排序。这样不会因 PF 重排而无限拖延软缓冲。
        pending_ready = sorted(
            (u for u in metric_order if u in harq_pending
             and harq_pending[u].state == "retx_ready"),
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
            and (harq_pending[u].state == "await_feedback"
                 or harq_pending[u].slot != slot)
            for u in range(n_ue))
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
        su_plan = _admit_plan_resources(
            su_plan, budget=resource_budget, tti=tti)
        if bool(sched.mu_enabled) and not pending_ready:
            if len(mu_pair_evaluation_cache) > 20_000:
                # Finite-queue states can be unique every TTI.  Keep the cache a
                # bounded hot-path aid, never an unbounded second result store.
                mu_pair_evaluation_cache.clear()
            mu_plan = _build_mu_plan(
                ordered_users, queue_bytes=queue_bytes, lookup=lookup, slot=slot,
                num_rbg=int(sys_cfg.num_rbg), rank_of=rank_of, mcs_of=mcs_of,
                base_tx_sinr_of=base_tx_sinr_of,
                mcs_without_olla_of=mcs_without_olla_of,
                true_sinr_of=true_sinr_of, potential_of=potential_of,
                tables=tables, snap=snap, sched=sched,
                su_olla_db=olla_db, mu_olla_db=mu_olla_db,
                blocked_data=blocked_data, cursor=cursor,
                frequency_aware=frequency_aware,
                pair_evaluation_cache=mu_pair_evaluation_cache)
        else:
            mu_plan = _TtiPlan("MU", tuple(), 0, 0, False, False)
        mu_plan = _admit_plan_resources(
            mu_plan, budget=resource_budget, tti=tti)

        if in_measurement and not pending_ready:
            su_plan_useful += su_plan.useful_bytes
            mu_plan_useful += mu_plan.useful_bytes
        _su_pf = _plan_pf_metric(su_plan)
        _mu_pf = _plan_pf_metric(mu_plan)
        _pf_ratio = (_mu_pf / max(_su_pf, _EPS)
                     if mu_plan.has_mu else 0.0)
        _pf_threshold = float(getattr(sched, "pf_gain_threshold", 0.0))
        _pf_gate_pass = (
            _pf_threshold <= 0.0
            or _pf_ratio >= _pf_threshold + 1e-9
        )
        if in_measurement and mu_plan.has_mu:
            pf_gain_ratios.append(float(_pf_ratio))
        if pending_ready:
            selected_plan = su_plan
            selected_reason = "HARQ_retx_priority"
            harq_retx_forced_su += int(in_measurement)
        elif su_plan.clears_all_queues:
            selected_plan = su_plan
            selected_reason = "SU_clears_all_queues"
            su_forced_clear += int(in_measurement)
        elif (bool(sched.mu_enabled) and mu_plan.has_mu
              and mu_plan.useful_bytes >= su_plan.useful_bytes
              and _pf_gate_pass):
            selected_plan = mu_plan
            selected_reason = "MU_useful_bytes_ge_SU"
            mu_decisions += int(in_measurement)
        else:
            selected_plan = su_plan
            if mu_plan.has_mu and not _pf_gate_pass:
                selected_reason = "SU_pf_gain_below_threshold"
                pf_gain_rejects += int(in_measurement)
            else:
                selected_reason = ("SU_useful_bytes_gt_MU" if mu_plan.has_mu
                                   else "SU_no_eligible_MU_pair")
            su_decisions += int(in_measurement)

        final_grants = _finalize_selected_plan(
            selected_plan,
            queue_bytes=queue_bytes,
            lookup=lookup,
            slot=slot,
            su_olla_db=olla_db,
            mu_olla_db=mu_olla_db,
            olla_enabled=bool(sched.olla_enabled),
            harq_pending=harq_pending,
            tables=tables,
        )
        if in_measurement:
            for evaluated_plan in (su_plan, mu_plan):
                admission = evaluated_plan.resource_admission
                if admission is not None:
                    for rejected in admission.rejections:
                        resource_evaluated_rejection_reasons[rejected.reason] = (
                            resource_evaluated_rejection_reasons.get(
                                rejected.reason, 0) + 1)
            selected_admission = selected_plan.resource_admission
            if selected_admission is None:
                raise RuntimeError("选中的计划没有经过 ResourceLedger")
            for rejected in selected_admission.rejections:
                resource_rejection_reasons[rejected.reason] = (
                    resource_rejection_reasons.get(rejected.reason, 0) + 1)
            selected_snapshot = selected_admission.snapshot
            allocated_logical_prb_equiv += (
                float(selected_snapshot.used_logical_prb) * slot_fraction)
            max_layers_used = max(
                max_layers_used, int(selected_snapshot.max_layers_used))
            finalizer_grant_count += len(final_grants)
            for grant in final_grants:
                if frequency_aware:
                    frequency_grant_count += 1
                    frequency_quality_selected_count += int(
                        str(grant.frequency_selected_source).startswith("quality"))
                    frequency_score_gains.append(float(
                        grant.frequency_selection_score_gain))
                    frequency_incremental_useful += int(
                        grant.frequency_incremental_useful_bytes)
                    frequency_evaluated_subsets += int(
                        grant.frequency_evaluated_subsets)
            for decision in mu_plan.mu_candidate_decisions:
                mu_candidate_count += len(decision.evaluations)
                mu_candidate_feasible_count += decision.feasible_count
                if decision.selected_partner_ue is not None:
                    mu_candidate_selected_count += 1
                if decision.selected_score is not None:
                    mu_candidate_selected_scores.append(
                        float(decision.selected_score))
                for reason in decision.rejection_reasons:
                    mu_candidate_rejection_reasons[reason] = (
                        mu_candidate_rejection_reasons.get(reason, 0) + 1)

        used_indices: set[int] = set()
        inst = np.zeros(n_ue, dtype=float)
        users_this_tti = 0
        tti_allocations: list[Allocation] = []
        for group_idx, grant in enumerate(final_grants):
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
                    if pending_tb.state != "retx_ready":
                        raise RuntimeError(
                            "尚未收到反馈的 HARQ TB 不得进入发送路径")
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
                mcs_input_sinr = float(grant.mcs_input_sinr_db[side])
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
                    # 只允许一次重传。终次 ACK/NACK 在发送时抽样，但 gNB
                    # 要等反馈回来才释放单进程；失败 payload 之后成为新 TB。
                    harq_pending[u] = replace(
                        pending_tb, state="await_final_feedback",
                        final_feedback_tti=(
                            tti + int(feedback_offsets[tti % pattern_len])))
                else:
                    tx_count[u] += 1
                    nack_count[u] += int(not ack)
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
                        # avg_mcs 的分母含重传（重传重放的是冻结的旧 MCS），
                        # 想看"链路自适应现在选到哪一档"要用这个首传口径。
                        mcs_first_sum_measured[u] += mcs
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
                olla_delta = 0.0
                olla_effective_tti = tti
                if sched.olla_enabled and not is_retx:
                    speed = (float(getattr(sched, "olla_warmup_speedup", 1.0))
                             if not in_measurement
                             else float(getattr(sched, "olla_speedup", 1.0)))
                    if grant.mode == "MU":
                        step = (float(sched.mu_olla_step_up_db) if ack
                                else -float(sched.mu_olla_step_down_db))
                    else:
                        step = (float(sched.olla_step_up_db) if ack
                                else -float(sched.olla_step_down_db))
                    # **步长在发送时刻定，生效在反馈回来之后。** 放大系数按
                    # 发送时刻所处的窗口取：那次传输确实发生在预热期。
                    olla_delta = step * speed
                    olla_effective_tti = (
                        tti + int(feedback_offsets[tti % pattern_len]))
                if not is_retx:
                    # 单 HARQ 进程：首传 ACK/NACK 都占住 UE，直到反馈到达。
                    # outcome 在发送时抽样，但只能由下一轮顶部的 due-event
                    # 路径交给 OLLA 与 RankController。
                    harq_pending[u] = _HarqTb(
                        mcs=mcs, rank=rank, n_rbg=n_alloc,
                        tb_bytes=tb_bytes, payload_bytes=payload,
                        slot=slot, first_tti=tti,
                        first_mode=str(grant.mode),
                        feedback=ap.FirstTxFeedback(
                            ue=int(u), ack=bool(ack), mcs=int(mcs),
                            rank=int(rank),
                            realized_se=(
                                float(rank) * float(
                                    la.MCS_TABLES[int(lookup.mcs_table)][mcs].se)
                                if ack else 0.0),
                            tx_tti=int(tti),
                            effective_tti=int(
                                tti + int(feedback_offsets[tti % pattern_len])),
                            use_mu_olla=(str(grant.mode) == "MU"),
                            olla_delta_mcs=float(olla_delta)))
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
                    required_rbg_from_remaining_pool=int(
                        grant.required_rbg_from_remaining_pool[side]),
                    fits_in_remaining_pool=bool(
                        grant.fits_in_remaining_pool[side]),
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
                    olla_delta_mcs=float(olla_delta),
                    olla_effective_tti=(
                        int(olla_effective_tti) if olla_delta else None),
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
                        if pending_tb is not None else str(grant.mode)),
                    reservation_id=grant.reservation_id,
                    logical_prb=int(rank * grant_prb),
                    layers_per_rbg=int(sum(grant.ranks)),
                    finalizer_version=str(grant.finalizer_version),
                    frequency_selection_score_gain=float(
                        grant.frequency_selection_score_gain),
                    frequency_incremental_useful_bytes=int(
                        grant.frequency_incremental_useful_bytes),
                    frequency_evaluated_subsets=int(
                        grant.frequency_evaluated_subsets),
                    frequency_selected_source=str(
                        grant.frequency_selected_source),
                    mu_candidate_score=grant.candidate_score,
                    mu_candidate_count=int(grant.candidate_count),
                    mu_rejected_candidate_reasons=tuple(
                        grant.rejected_candidate_reasons))
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
                    resource_ledger=(
                        selected_plan.resource_admission.as_dict()
                        if selected_plan.resource_admission is not None else None),
                    mu_candidate_decisions=mu_plan.mu_candidate_decisions,
                )
        if progress and tti % 5000 == 0:
            progress(tti, int(sys_cfg.num_tti))

    pending_measured = np.asarray([
        int(u in harq_pending and harq_pending[u].first_tti >= warmup)
        for u in range(n_ue)
    ], dtype=int)
    unresolved_terminal_measured = np.asarray([
        int(
            u in harq_pending
            and harq_pending[u].first_tti >= warmup
            and not (
                harq_pending[u].state == "await_final_feedback"
                or (harq_pending[u].state == "await_feedback"
                    and harq_pending[u].first_ack)
            )
        )
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
    completed_burst_count = inflight_burst_count = 0
    active_window_goodputs: list[float] = []
    completed_arrival_objects = 0
    queue_wait_observed_objects = 0
    queue_wait_right_censored_objects = 0
    pdb_decidable_objects = 0
    pdb_right_censored_objects = 0
    deadline_missed_incomplete_objects = 0
    for u, q in enumerate(tr.queues):
        done = [b for b in q.done if b.start_tti >= warmup]
        metrics = [burst_metrics(b, sys_cfg.tti_ms, small_policy) for b in done]
        # **标准样本只来自已排空的 busy period。** TS 128 552 V19.5.0 p54：
        # 样本在 "DRB DL buffer emptied" 事件上形成。在飞的 busy period 不产生
        # 标准样本，绝不能混进 drb_throughput_rel19_mbps —— 混了就等于让工程量
        # 顶着标准的名字，跨实现对标和历史趋势都失去定义一致性。
        thp = [m.throughput_mbps for m in metrics if m.throughput_mbps is not None]
        completed_burst_count += len(thp)
        head_thp = [m.head_inclusive_throughput_mbps for m in metrics
                    if m.head_inclusive_throughput_mbps is not None]
        # 工程口径的在飞窗内 goodput 另存一路，另起名字上报。
        active_metric = (
            active_window_goodput(q.active, sys_cfg.tti_ms, warmup)
            if q.active is not None else None)
        ue_active_goodput = None
        if active_metric is not None and active_metric.throughput_mbps is not None:
            ue_active_goodput = float(active_metric.throughput_mbps)
            active_window_goodputs.append(ue_active_goodput)
            inflight_burst_count += 1
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
        # 大/小 burst 分视图是**标准口径**的，工程 goodput 不进这里。
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
        # **busy-period 口径**对它无定义——只有真实到达/未完成对象才让 UE 有资格
        # 进这套 KPI。ITU 口径的 ue_served_* 不受此限，任何话务下都算。
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
            "srs_resource_assignment": (
                tables[u].srs_resource_assignment.as_dict()
                if getattr(tables[u], "srs_resource_assignment", None) is not None
                else None),
            "traffic_class": str(q.traffic_class.name),
            "geo_sinr_db": round(float(tables[u].geo_sinr_db), 4),
            "iot_db": round(float(tables[u].iot_db), 4),
            "experienced_mbps": ue_thp if experience_eligible else None,
            "head_inclusive_experienced_mbps": (
                ue_head_thp if experience_eligible else None),
            "experience_kpi_eligible": experience_eligible,
            # **"measured" 保持原义 = 有已完成 burst 的样本**，它是删失诊断：
            # 和 experience_kpi_eligible 之差就是"有话务但一个 burst 都没传完"。
            # 在飞样本进 experienced_mbps，但不许把这个诊断也填满。
            "experience_kpi_measured": bool(thp),
            # 工程口径：在飞 busy period 在窗内那一段的 goodput。**不是标准样本。**
            "active_window_goodput_mbps": ue_active_goodput,
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
            "avg_mcs_first_tx": float(
                mcs_first_sum_measured[u] / max(tx_count_measured[u], 1)),
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
                / max(tx_count_measured[u]
                      - unresolved_terminal_measured[u], 1)),
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
    # 标准样本本来就只来自已完成 busy period，所以 completed-only 与
    # experienced_mbps 现在同源；保留这个键是为了旧消费者不断。
    user_exp_completed_only = [float(u["experienced_mbps"]) for u in users
                               if bool(u["experience_kpi_measured"])]
    offered = int(tr.offered_bytes)
    offered_measured = max(0, offered - int(offered_before_measurement))
    backlog = int(tr.backlog_bytes)
    acked_total = int(np.sum(served))
    acked_total_measured = int(np.sum(served_measured))
    # **满缓冲下这个检查不成立，必须报 None 而不是 0.0。**
    # 它比的是「到达 = 已发 + 积压」，而 full buffer 的 offered 是无界的种子字节，
    # 差值没有意义。旧容量分支在这里算出 3.7e21（把 1<<62 的种子算进了 offered），
    # 那是**用垃圾数冒充测量**；合并初版改成硬编码 0.0，那是**用漂亮数冒充测量**，
    # 同一种错。旁边三个兄弟字段（offered_bytes_measurement /
    # backlog_bytes_at_measurement_start / measurement_accounting_error_pct）
    # 满缓冲下都报 None，这个也必须一致。
    acct_error = (None if tr.unbounded else
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
    observed_harq_total = max(
        tx_total - int(np.sum(unresolved_terminal_measured)), 0)

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
    # **和建表相共用同一份 _nan_safe，不各写一套。** 两份实现曾经短暂并存，
    # 契约还不一样（一个先滤非有限值再聚合，一个交给 nan* 函数自己处理），
    # 那正是同名不同义的漂移。函数内 import 是为了不破坏服务端的 lazy 策略。
    from .system import _nan_safe  # noqa: PLC0415

    serving_cell_prb_utilization = float(
        allocated_prb_equiv / max(available_prb_equiv, _EPS))
    mu_paired_prb_share_of_used = float(
        mu_prb_equiv / max(allocated_prb_equiv, _EPS))
    mu_paired_prb_utilization = float(
        mu_prb_equiv / max(available_prb_equiv, _EPS))
    # **用户吞吐的分布：ITU-R M.2412 / TR 38.913 的"用户体验速率"口径。**
    # 每 UE 在测量窗内 ACK 到的净荷字节 / 窗长，取跨 UE 的分布；5% 分位就是
    # cell-edge user throughput。它和 busy-period 口径是**两个不同的定义**，
    # 不是同一个数的两种精度：
    #   * busy-period（下面的 cell_experienced_mbps / drb_throughput_rel19_mbps）
    #     量的是"一个 burst 从首传到发完有多快"，需要 buffer 排空来划边界；
    #   * 这里量的是"一个用户在一段时间里平均拿到多少"，只需要一个观测窗。
    # 满缓冲评估用的正是后者——前者在 full buffer 下没有边界可用，后者照常成立。
    # **无条件计算，不按话务模型分支**：任何话务下它都有意义。
    user_served = [float(row["served_mbps"]) for row in users]
    cell = {
        "ue_served_mean_mbps": _mean(user_served),
        "ue_served_median_mbps": _pct(user_served, 50),
        "ue_served_p5_mbps": _pct(user_served, 5),
        "ue_served_throughput_definition": (
            "per-UE ACKed payload bytes over the measurement window divided by "
            "the window duration; the 5th percentile is the ITU-R M.2412 / "
            "TR 38.913 cell-edge user throughput. Defined for every traffic "
            "model including full buffer, where the busy-period KPIs are not."),
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
        # --- 工程口径，与上面的标准字段并列、绝不混入 ---------------------
        # 过载与满缓冲下标准样本可能一个都没有（buffer 永不排空 ⇒ 无 emptied
        # 事件）。此时仍需要知道"正在传的时候有多快"，所以另起字段另起名字。
        "active_window_goodput_mbps": _mean(active_window_goodputs),
        "active_window_goodput_samples": int(inflight_burst_count),
        "active_window_goodput_definition": (
            "ENGINEERING metric, NOT a TS 28.552 sample: ACKed payload of the "
            "still-in-flight busy period within the measurement window divided by "
            "the elapsed time from its first transmission (or window start) to its "
            "last in-window ACK. No tail trimming because this is goodput, not a "
            "standard throughput sample; the last in-window ACK may itself carry "
            "padding. Reported alongside drb_throughput_rel19_mbps, never merged "
            "into it."),
        # **标准样本的统计有效性必须可见。** 过载时已完成 busy period 会变得很少
        # 甚至为 0，此时 drb_throughput_rel19_mbps 是少数样本的均值或 None。
        "drb_throughput_completed_bursts": int(completed_burst_count),
        "drb_throughput_inflight_bursts": int(inflight_burst_count),
        "drb_throughput_inflight_share": float(
            inflight_burst_count
            / max(completed_burst_count + inflight_burst_count, 1)),
        "drb_throughput_sample_scope": (
            "TS 128 552 V19.5.0 p54: samples form on the DRB DL buffer-emptied "
            "event only, with the buffer-clearing final piece excluded. "
            "Still-in-flight busy periods produce NO standard sample; they are "
            "reported separately as active_window_goodput_mbps. "
            "drb_throughput_inflight_share high means the standard KPI rests on "
            "few samples."),
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
        "avg_mcs_first_tx": float(
            np.sum(mcs_first_sum_measured) / max(np.sum(tx_count_measured), 1)),
        "avg_mcs_definition": (
            "avg_mcs averages the air-interface MCS over every measured grant, "
            "retransmissions included (a retransmission replays the frozen "
            "first-transmission MCS); avg_mcs_first_tx keeps only new "
            "transmissions and is the link-adaptation view"),
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
            "failed unique retransmissions / initial TBs with a sampled terminal "
            "decoder outcome in the measurement cohort; only first-NACK TBs "
            "still awaiting/completing their single retransmission are right-censored"),
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
        "accounting_error_pct": (
            None if acct_error is None else round(float(acct_error), 6)),
        "outage_ue": int(sum(1 for t in tables
                              if t.outage is not None and bool(t.outage.all()))),
        "outage_skips": int(outage_skips),
        "harq_feedback_wait_skips": int(feedback_wait_skips),
        "olla_db_mean": float(np.mean(olla_db)),
        "olla_db_p5": float(np.percentile(olla_db, 5)),
        "olla_db_p95": float(np.percentile(olla_db, 95)),
        "olla_mcs_mean": float(np.mean(olla_db)),
        "olla_mcs_p5": float(np.percentile(olla_db, 5)),
        "olla_mcs_p95": float(np.percentile(olla_db, 95)),
        "olla_domain": "continuous_mcs_index",
        "olla_target_bler": round(
            float(sched.olla_step_up_db)
            / (float(sched.olla_step_up_db) + float(sched.olla_step_down_db)), 4),
        # **IoT = (I+N)/N**：干扰主导还是噪声主导。密集城区常 >20 dB。
        # 它是链路表的纯函数，和话务模型无关，所以任何配置下都该报。
        "iot_db_median": _nan_safe(np.nanmedian, [t.iot_db for t in tables]),
        "iot_db_p5": _nan_safe(np.nanpercentile, [t.iot_db for t in tables], 5),
        "iot_db_p95": _nan_safe(np.nanpercentile, [t.iot_db for t in tables], 95),
        # **有效率必须按样本算，不是按用户。** 逐用户的 nanmedian 会把
        # "8 个快照里 4 个算不出来"的用户也算成有效，于是小区级恒报 100%，
        # 正确的多时隙告警从不触发。两个口径都报出来，差异才可见。
        "iot_sample_valid_share": (
            float(np.mean([t.iot_sample_valid for t in tables])) if tables else 0.0),
        "iot_valid_ue_share": float(np.mean(
            [bool(np.isfinite(t.iot_db)) for t in tables])) if tables else 0.0,
        "high_iot_ue_share": float(np.mean([
            (t.iot_db >= 20.0) if np.isfinite(t.iot_db) else False
            for t in tables])) if tables else 0.0,
        # **边缘用户 MCS**：现场经验通常 < 5，比平均 MCS 更能暴露覆盖问题。
        "edge_mcs_p5": _nan_safe(
            np.nanpercentile,
            [float(row["avg_mcs"]) for row in users
             if int(row.get("sched_tti", 0) or 0) > 0], 5),
        "mu_share": float(mu_tti / max(busy_tti, 1)),
        "mu_rbg_share": float(mu_rbg / max(allocated_rbg, 1)),
        "mu_paired_prb_share_of_used": mu_paired_prb_share_of_used,
        "mu_paired_prb_utilization": mu_paired_prb_utilization,
        "mu_paired_prb_equivalent": float(mu_prb_equiv),
        "allocated_prb_equivalent": float(allocated_prb_equiv),
        "available_prb_equivalent": float(available_prb_equiv),
        "resource_ledger": {
            "budget": resource_budget.as_dict(),
            "allocated_logical_prb_equivalent": float(
                allocated_logical_prb_equiv),
            "available_logical_prb_equivalent": float(
                available_prb_equiv * int(resource_budget.max_layers_per_rbg)),
            "logical_prb_utilization": float(
                allocated_logical_prb_equiv
                / max(available_prb_equiv
                      * int(resource_budget.max_layers_per_rbg), _EPS)),
            "max_layers_used": int(max_layers_used),
            "selected_plan_rejections": resource_rejection_reasons,
            "evaluated_plan_rejections": resource_evaluated_rejection_reasons,
            "physical_overlap_violations": int(overlap_violations),
            "pdcch_cce": "not_modelled_by_explicit_scope",
        },
        "grant_finalizer": {
            "version": "grant-finalizer-v1",
            "finalized_grants": int(finalizer_grant_count),
            "plan_final_mismatch_count": 0,
            "one_codeword_per_user_tb": True,
        },
        "frequency_selection": {
            "requested_mode": frequency_mode,
            "enabled": bool(frequency_aware),
            "rbg_fields_ready": bool(frequency_ready),
            "independent_of_rb_power_control": True,
            "policy": "best-quality-prefix-with-sequential-safety-net-v1",
            "grant_count": int(frequency_grant_count),
            "quality_selected_count": int(frequency_quality_selected_count),
            "mean_selection_score_gain": (
                float(np.mean(frequency_score_gains))
                if frequency_score_gains else None),
            "selection_score_definition": (
                "selected-minus-available mean of predicted per-RBG score; "
                "SU score is user dB SINR, MU score is the sum of both users' "
                "effective predicted dB coordinates"),
            "incremental_predicted_useful_bytes_vs_sequential": int(
                frequency_incremental_useful),
            "evaluated_subset_count": int(frequency_evaluated_subsets),
            "effective_sinr": "arithmetic mean in dB over granted RBGs and rank streams",
            "allocation_audit_fields": {
                "required_rbg/fits_in_fullband/potential_fullband_bytes": (
                    "re-evaluated against the complete carrier RBG pool"),
                "required_rbg_from_remaining_pool/fits_in_remaining_pool": (
                    "re-evaluated against the RBGs still available to this PF user"),
            },
        },
        "mu_candidate_scoring": {
            "candidate_count": int(mu_candidate_count),
            "feasible_count": int(mu_candidate_feasible_count),
            "selected_count": int(mu_candidate_selected_count),
            "selected_score_mean_useful_bytes_per_rbg": (
                float(np.mean(mu_candidate_selected_scores))
                if mu_candidate_selected_scores else None),
            "rejection_reasons": mu_candidate_rejection_reasons,
            "objective": (
                "PF anchor fixed; maximize queue-limited useful bytes per RBG; "
                "tie by useful bytes, lower correlation, earlier PF partner"),
        },
        "mu_pair_graph": (mu_pair_graph if mu_pair_graph is not None else {
            "status": "not_required", "reason": "mu_disabled"}),
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
            "min_pairing_mcs": int(getattr(sched, "min_pairing_mcs", 4)),
            "pf_gain_threshold": float(
                getattr(sched, "pf_gain_threshold", 0.0)),
            "pf_gain_rejects": int(pf_gain_rejects),
            "pf_gain_ratio_mean": (
                float(np.mean(pf_gain_ratios)) if pf_gain_ratios else None),
            "pf_gain_metric": "sum(useful_bytes / PF_R_avg_bytes)",
            "orthogonalization_mode": str(
                getattr(sched, "orthogonalization_mode", "select")),
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
        "scheduler_priority_metric": _scheduler_metric_identity(
            sched, srb_observed=srb_observed),
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

    if (starvation_hol_ms is not None
            and str(sched.algorithm) in ("edf", "qos_pf_edf")):
        cell["scheduler_starvation_lifts"] = {
            "threshold_hol_ms": float(starvation_hol_ms),
            "lifted_candidate_ttis": int(starvation_lifts),
            "scope": "measurement window; counts candidate-TTI pairs, not UEs",
        }

    if str(sched.algorithm) == "qos_pf_edf":
        cell["scheduler_mixed_component_scale"] = _mixed_component_scale(
            mixed_epf_medians, mixed_edf_medians,
            weight=mixed_weight, epf_scale=mixed_epf_scale)

    if tr.unbounded:
        # **TS 28.552 的样本在 buffer emptied 事件上形成**，而 full buffer 下
        # buffer 永不排空 ⇒ 一个标准样本都没有。这不是实现缺陷，是标准的定义。
        # 报 None 而不是 0.0：把"标准未定义"写成 0 会被当成"测到了 0 Mbps"。
        #
        # **需要一个数的用户看这两个工程字段**（任何话务下都有值）：
        #   * ue_served_p5/median/mean_mbps —— ITU-R M.2412 / TR 38.913 口径，
        #     每 UE 已服务净荷 ÷ 观测窗长，5% 分位即 cell-edge user throughput；
        #   * active_window_goodput_mbps —— 在飞 busy period 窗内段的 goodput。
        for _k in ("cell_experienced_mbps",
                   "cell_head_inclusive_experienced_mbps",
                   "cell_experienced_completed_only_mbps",
                   "ue_experienced_mean_mbps", "ue_experienced_median_mbps",
                   "ue_experienced_p5_mbps",
                   "drb_throughput_rel19_mbps",
                   "drb_throughput_head_inclusive_mbps"):
            cell[_k] = None

    notes: list[str] = [
        (
            "experience_v2 已开启逐 RBG 频选：它与 RB 功控解耦，按实际 grant "
            "bitmap 聚合 predicted/receive SINR、重选 MCS 并判误码；当前有效 "
            "SINR 是 RBG dB 算术平均，尚未用标定过的 EESM/MIESM。"
            if frequency_aware else
            "experience_v2 当前显式使用**宽带/顺序 RBG 基线**；这是 "
            "frequency_selective='off' 或逐 RBG 字段不可用的结果，不再由 RB 功控"
            "开关暗中决定。"
        ),
        "TBS 量化算法走 38.214 §5.1.3.2，但 MCS 使用预置 20B profile；D 时隙按"
        "每 RB 12 个数据符号、S 时隙按 0.7 倍 N_RE，未展开 DMRS/PTRS/CORESET。",
        ("HARQ 每个单码字 TB 最多一次重传，重传保持初传 MCS、RBG 数、rank 与 TBS；"
         f"当前合并={harq_combining.upper()}。CC 用同一 NewTx 曲线并把码字 "
         "SINR 抬升 10log10(2)=3.0103 dB；IR 用原 MCS 一半谱效映射等效低档 MCS，"
         "在不变 SINR 上查该 NewTx 曲线。等效 MCS 只用于 BLER 查表，不改写空口 MCS。"
         "重传失败后结束本次 HARQ，payload 留在 DRB 队列并在后续作为新 TB。"),
        f"PF 平均量口径是 **{accounting}**；ACKed bytes 另作为 KPI 统计。",
        (f"Rank 策略={rank_cfg.mode}"
         + (f"（固定 rank{min(int(rank_cfg.fixed_rank), rank_ctl.max_rank)}）"
            if rank_cfg.mode == "fixed"
            else f"（每 {int(rank_cfg.period_tti)} TTI 决策一次，升 rank 谱效比"
                 f"门限 {float(rank_cfg.gain_factor_raise):g}）")
         + "。链路表里的逐快照 best_rank 是瞬时谱效最优值，**不再**直接作为"
           "发送 rank——那会让 rank 每个信道快照就换一次。"),
        ("ACK/NACK 搭发送之后第一个 U 时隙回传，OLLA 更新与重传资格从该 U 之后"
         f"第一个 D/S 时隙起生效；{pattern} 下逐相位偏移 "
         f"{list(feedback_offsets)} 个 TTI。**重传还要额外等到同类型时隙**"
         "（S 上发的 TB 要等下一个 S），两个约束取交集。等待期间该 UE 因单 "
         "HARQ 进程模型不参与调度。k1/k2、PUCCH 资源与并行 HARQ 进程都未建模。"
         if feedback_modelled else
         "**HARQ 反馈按零时延处理**：TDD 图案里没有 U 时隙，或反向对照显式"
         "关掉了时延模型。ACK/NACK 在发送同一个 TTI 就生效，这是上界不是现网。"),
        "分配器每个 DL TTI 只排序一次：按 PF/QoS-PF 优先级依次给最小够用 RBG；"
        "剩余 RBG 没有候选需求时留空，不回填给第一名。",
        "每个 SU/MU 候选计划先经过 ResourceLedger：物理 RBG 只扣一次、逐 RBG "
        "总层数不超过预算、逻辑 layer-PRB 单独记账；按当前范围 PDCCH/CCE 不建模。"
        "选中计划再统一经过 GrantFinalizer 重算 MCS/TBS/useful bytes，和计划估值"
        "不一致会硬失败。",
        "MU 先固定 PF anchor，再枚举全部伙伴；过滤缺链路、相关性、层数和预测 "
        "BLER 后，按 queue-limited useful bytes/RBG 评分，不再取第一个可行伙伴。",
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
    if str(sched.algorithm) in ("edf", "qos_pf_edf"):
        notes.append(
            "**edf 是包长感知调度**：优先级 = TBS_fullband / Buffer × w(priority)，"
            "即“还需几个调度机会才能排空缓冲区”的倒数。缓冲区小 + 信道好的"
            "用户先走。**它牺牲长期公平性换小包时延**——大包用户在重载下可能"
            "长期排在后面，请对照 ue_experienced 的分位数和 Jain 公平度判读，"
            "不要只看小区吞吐。**被饿死的不是「大包用户」而是「大缓冲 + 边缘"
            "信道」**——分子是信道相关的 TBS，EDF 对坏信道和大积压是乘性双重"
            "惩罚，且没有 PF 的 1/R_avg 补偿项。"
            + ("SRB 加值本次已生效（存在 signalling 业务类）。" if srb_observed else
               "SRB 绝对优先未生效：SuperRAN 不建模逻辑信道。"))
    if (starvation_hol_ms is not None
            and str(sched.algorithm) in ("edf", "qos_pf_edf")):
        lifts = cell.get("scheduler_starvation_lifts", {})
        notes.append(
            f"**时延兜底已开启**：队首等待达到 {float(starvation_hol_ms):g} ms 的"
            f"用户无条件排到最前，组内按等待降序；测量窗内抬升 "
            f"{lifts.get('lifted_candidate_ttis')} 个候选-TTI。它给的是等待上界，"
            "代价是吞吐——被抬升的用户往往正是信道差、传同样字节要占更多 RBG 的"
            "那些。要判读代价请对比关闭兜底的同种子运行。")
    if str(sched.algorithm) == "qos_pf_edf":
        scale_report = cell.get("scheduler_mixed_component_scale", {})
        notes.append(
            "**qos_pf_edf 照抄蓝本加权混合模式的原式** "
            "((1−w)·thp_filter·EPF + w·EDF)×w(priority)。两个分量不同量纲，"
            "w 单独一个数不定义混合比例——请看 "
            "cell.scheduler_mixed_component_scale 里两个分量的实测中位数与 "
            f"effective_edf_share（本次 {scale_report.get('effective_edf_share')}）"
            "，必要时用 edf_mixed_epf_scale 配平。w=0 严格退化成 qos_pf，"
            "w=1 严格退化成 edf。**退化只在没有 signalling 业务类时逐位成立**："
            "SRB 绝对优先按设计与 w 无关，声明了 signalling 类时 w=0 仍会带着"
            "那个加值，因而与纯 qos_pf 有可测差异。")
        if "warning" in scale_report:
            notes.append("**混合权重已退化**：" + str(scale_report["warning"]))
    if str(sched.algorithm) == "qos_pf":
        notes.append(
            "qos_pf 使用 w(priority) × R_inst^beta / R_avg^alpha × "
            "[PDB/(PDB−HOL)]^gamma（时延因子上限 1000）；默认 "
            "alpha=beta=1、gamma=0、w=1，严格退化成经典 PF。它是显式工程 "
            "profile，未冒充现场未确认的 EPF。")
    if n_snap < 4:
        notes.append(f"**信道快照只有 {n_snap} 个**，时间起伏被严重低估，PF 多用户分集不足。")
    if tr.unbounded:
        notes.append(
            "**话务无界（full buffer）**：buffer 永不排空，burst 没有边界，"
            "因此 **28.552 的 busy-period 吞吐**（cell_experienced_mbps / "
            "drb_throughput_rel19_mbps / 完成时延 / PDB）无定义，报 None（不是 0）。"
            "**用户体验速率仍然有定义**，只是走另一个口径：ue_served_p5_mbps "
            "是 ITU-R M.2412 / TR 38.913 的 cell-edge user throughput（每 UE "
            "已服务净荷 / 观测窗长的 5% 分位），ue_served_mean/median_mbps 是同一"
            "分布的均值与中位；另一个工程口径是 active_window_goodput_mbps"
            "（在飞 busy period 落在测量窗内那段的 goodput）——它与 ue_served "
            "算法完全不同，满缓冲下应当收敛，可拿来互相自查；"
            "小区总吞吐看 cell_served_mbps。"
            "**标准与工程不是同一个数的两种精度，不可互相顶替。**")
    if measured_bursts < 20 and not tr.unbounded:
        notes.append(f"只有 {measured_bursts} 个 busy period 进入体验 KPI，样本太少；"
                     "加长 duration_s 或调高到达率。")
    if not tr.unbounded and backlog > 0.15 * max(offered, 1):
        notes.append(f"**队列积压 {backlog * 8 / 1e6:.1f} Mb**"
                     f"（占到达量 {backlog / max(offered, 1):.0%}），系统未收敛。")
    if acct_error is not None and acct_error > 1.0:
        notes.append(f"**字节对不上账（差 {acct_error:.3f}%）**："
                     "arrived 必须等于 acked + queued + in_flight + dropped。")
    if measurement_acct_error is not None and measurement_acct_error > 1.0:
        notes.append(
            f"**测量窗口字节对不上账（差 {measurement_acct_error:.3f}%）**："
            "start_backlog + arrived_in_window 必须等于 acked_in_window + end_backlog。")
    if cell["serving_cell_prb_utilization"] > 0.98:
        notes.append("**本小区 PRB 利用率超过 98%**，当前结果更接近容量上限而非稳态体验。")
    # **判据必须是逐样本有效率。** 逐用户的那个恒等于 1（nanmedian 会把半数 nan
    # 的用户也算成有效），于是这条正确的告警从不触发，反而触发下面那条"检查
    # 站间距"——把用户支使去查一个根本没问题的配置。
    _iot_ok = float(cell.get("iot_sample_valid_share", 1.0))
    if _iot_ok < 0.9:
        notes.append(
            f"**IoT 不可信：只有 {_iot_ok:.0%} 的样本算得出来**"
            f"（逐用户口径会报 {cell['iot_valid_ue_share']:.0%}，那个数会骗人）。"
            "根因是生成时 num_slots_per_sample > 1——那时 sinr_dB 是各 slot 的 dB "
            "均值、sir_dB 只取最后一个 slot，两者不同口径，会出现 SIR < SINR 这种"
            "物理上不可能的值。**别去查站间距和邻区负载，配置没问题，是这个量本身"
            "在多时隙下不成立。** 要看 IoT 就用 num_slots_per_sample=1 单独生成一批"
            "——但那批做不了系统级仿真（PF 拿不到时间分集、CSI 老化恒为 0）。")
    elif (np.isfinite(cell["iot_db_median"]) and cell["iot_db_median"] < 3):
        notes.append(
            f"**IoT 中位只有 {cell['iot_db_median']:.1f} dB**，几乎是噪声受限。"
            "密集城区实际常在 20 dB 以上——检查是不是站间距太大、"
            "或者邻区负载 prb_utilization 设得过低。")
    if np.isfinite(cell["edge_mcs_p5"]) and cell["edge_mcs_p5"] > 8:
        notes.append(
            f"**5% 边缘用户的 MCS 是 {cell['edge_mcs_p5']:.1f}，偏高**"
            "（现场经验通常 <5）。多半是撒点没覆盖到真正的边缘，"
            "或者邻区负载设得太低、干扰被低估了。")
    if cell["outage_ue"]:
        notes.append(
            f"**{cell['outage_ue']} 个用户全程处于覆盖外**"
            "（用户级 SINR 够不到 MCS 0 的门限），已从调度中剔除。"
            "他们没有发射，所以**不进 BLER 统计**；"
            "但他们**以 0 进 ue_served_* 分布**——只要有一个覆盖外用户，"
            "ue_served_p5_mbps 就从「最差被服务用户的速率」变成「覆盖底噪」，"
            "方向偏低，而这个键是按 ITU-R M.2412 的 cell-edge user throughput 交付的。"
            "按标准密度撒点的多小区场景最容易出覆盖洞，引用 p5 前先看这个计数。"
            "——覆盖外本身也是结论：这些点位需要补站或降配。")
    diagnostics = {
        "rank_policy": rank_ctl.diagnostics(),
        "harq_feedback": {
            "delay_modelled": bool(feedback_modelled),
            "requested": bool(feedback_delay_on),
            "tdd_pattern": pattern,
            "effective_offsets_tti": [int(x) for x in feedback_offsets],
            "offset_ms": [
                round(float(x) * float(sys_cfg.tti_ms), 4)
                for x in feedback_offsets],
            "contract": (
                "ACK/NACK rides the first U slot after the transmission; the "
                "ACK and NACK both hold the single process in flight; OLLA, "
                "rank feedback and retransmission eligibility start at the "
                "first D/S slot after that U slot; terminal retransmission "
                "feedback only releases the process and causes no more learning/TX"
                if feedback_modelled
                else "zero-delay feedback (pattern has no U slot, or the "
                     "delay model is switched off for a reverse control)"),
            "not_modelled": (
                "k1/k2 values, PUCCH resources and parallel HARQ processes are "
                "not modelled; one HARQ process per UE"),
            "wait_skips": int(feedback_wait_skips),
        },
        "tbs_lookup": lookup.as_dict(),
        "srs_resource_assignments": [
            table.srs_resource_assignment.as_dict()
            for table in tables
            if getattr(table, "srs_resource_assignment", None) is not None
        ],
        "resource_ledger": cell["resource_ledger"],
        "grant_finalizer": cell["grant_finalizer"],
        "frequency_selection": cell["frequency_selection"],
        "mu_candidate_scoring": cell["mu_candidate_scoring"],
        "mu_pair_graph": cell["mu_pair_graph"],
        "tti_trace": {
            "schema": "superran_tti_trace_v1",
            "additive_contract_version": 2,
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
            "logical_prb_utilization": {
                "unit": "ratio",
                "scope": "measurement-window spatial/baseband resource",
                "formula": (
                    "sum_grant(sum_user(rank) * physical_PRB) / "
                    "(available_PRB * max_layers_per_rbg)"),
                "pdcch_cce": "not modelled",
            },
            "frequency_selection": {
                "unit": "RBG subset",
                "decision_information": "gNB-predicted per-RBG SINR only",
                "safety_net": (
                    "quality prefixes and sequential prefixes are both evaluated; "
                    "selected predicted useful bytes cannot be lower than sequential"),
                "effective_sinr": "arithmetic dB mean over selected RBGs",
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
            # arrived 无界时这条守恒式本身不成立，报 None 而不是 0.0。
            "error_pct": (
                None if acct_error is None else round(float(acct_error), 6)),
            "not_applicable_reason": (
                "full buffer: offered bytes are an unbounded seed, so "
                "arrived = acked + queued has no meaning" if tr.unbounded else None),
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

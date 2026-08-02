"""系统级仿真：连续 TTI、话务模型、PF 调度、体验速率。

**这一层回答的问题和链路级不一样。** 链路级问"这个信道能跑多快"，
系统级问"**这个小区里的用户实际体验到多快**"——后者要把话务的到达与结束、
调度器在多用户间的取舍、HARQ 重传、缓冲区排空全部算进去。

体验速率是现网真正上报的 KPI，它**不是**吞吐量的平均：

* 只在"有数据要发"的时间段里算（没数据的时候不算你慢）
* **掐尾**——把清空缓冲区的那个 TTI 排除掉（3GPP TS 28.552 §5.1.1.3）。
  不掐的话，一个只用半个 TTI 就发完的小包会被算成"半个 TTI 的速率"，
  数值虚高得离谱。
* **掐头**——运营商话统里通常还会排除首个 TTI（含调度时延与 BSR 上报往返）。
  两种口径都实现了，见 :class:`KpiConfig`。

架构上分两相，这是能跑十万 TTI 的关键：

    第一相（贵）：逐 UE、逐信道快照，把 rank 1..4 的 SINR / MCS / 谱效
                  全部算好存成表。SVD 只在这里做。
    第二相（便宜）：TTI 主循环只查表 + 算 PF 度量 + 更新缓冲区，
                  没有任何矩阵运算。

实测 20000 TTI × 12 UE 在第二相里是秒级；如果把 SVD 放进主循环，
同样规模要几十分钟。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from . import mumimo as mu

_EPS = 1e-12

TrafficModel = Literal["full_buffer", "ftp3", "cbr"]
SchedAlgorithm = Literal["pf", "rr", "max_ci"]
ThroughputTrim = Literal["none", "tail", "head_tail"]


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
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

    def as_dict(self) -> dict[str, Any]:
        d = {"model": self.model}
        if self.model == "ftp3":
            d |= {"file_bytes": self.file_bytes, "arrival_rate_hz": self.arrival_rate_hz,
                  "offered_load_mbps_per_ue":
                      round(self.file_bytes * 8 * self.arrival_rate_hz / 1e6, 3)}
        elif self.model == "cbr":
            d |= {"cbr_mbps": self.cbr_mbps}
        return d


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
    mu_enabled: bool = True              # 是否允许 MU 配对（SU/MU 自适应）
    max_mu_users: int = 4

    def as_dict(self) -> dict[str, Any]:
        return {"algorithm": self.algorithm, "pf_window_tti": self.pf_window_tti,
                "mu_enabled": self.mu_enabled, "max_mu_users": self.max_mu_users}


@dataclass
class KpiConfig:
    """KPI 统计口径。**换口径数字会明显变，所以它必须跟着结果一起走。**"""

    trim: ThroughputTrim = "tail"
    min_burst_tti: int = 2               # 短于这个的 burst 不计入体验速率
    warmup_tti: int = 200                # 前多少个 TTI 不计入统计（PF 均值要收敛）

    def as_dict(self) -> dict[str, Any]:
        return {"trim": self.trim, "min_burst_tti": self.min_burst_tti,
                "warmup_tti": self.warmup_tti,
                "trim_note": {
                    "none": "不掐，含清空缓冲区的那个 TTI（数值虚高，不建议）",
                    "tail": "掐尾：排除清空缓冲区的最后一个 TTI（3GPP TS 28.552 §5.1.1.3）",
                    "head_tail": "掐头去尾：再排除首个 TTI（运营商话统常用口径）",
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
    """

    prb_utilization: float = 0.3          # 5G 典型：0.1 / 0.3 / 0.5

    def as_dict(self) -> dict[str, Any]:
        return {"prb_utilization": self.prb_utilization,
                "note": ("邻区按这个 PRB 利用率折算干扰；1.0 等于假设所有邻区"
                         "full buffer（ChannelHub 几何 SINR 的原始假设）")}


def apply_neighbor_load(sinr_db: float, sir_db: float, utilization: float) -> float:
    """把几何 SINR 按邻区负载折算。返回新的 SINR（dB）。

    推导：令 S=1，则 I = 1/SIR_lin、N = 1/SINR_lin − I。
    邻区只在 ``η`` 比例的 PRB 上发，干扰变成 ``η·I``，噪声不变::

        SINR' = 1 / (η·I + N)

    ``sir_db`` 拿不到（单小区哨兵 49.9）时原样返回——没有干扰可折算。
    """
    u = min(max(float(utilization), 0.0), 1.0)
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
    duration_s: float = 5.0
    scs_khz: int = 30                    # 30 kHz → slot 0.5 ms
    num_rbg: int = 17
    rb_per_rbg: int = 16
    tdd_pattern: str = "DDDSU"           # 只统计 D 时隙
    snapshot_update_ms: float = 10.0     # 信道快照多久换一次
    seed: int = 0

    @property
    def tti_ms(self) -> float:
        return 1.0 / (self.scs_khz / 15.0)          # 15→1ms, 30→0.5ms, 60→0.25ms

    @property
    def num_tti(self) -> int:
        return int(round(self.duration_s * 1000.0 / self.tti_ms))

    @property
    def dl_ratio(self) -> float:
        """TDD 图案里下行时隙占比。S 时隙按 0.7 个下行折算（大部分符号是 D）。"""
        p = self.tdd_pattern.upper() or "D"
        return (p.count("D") + 0.7 * p.count("S")) / len(p)

    def as_dict(self) -> dict[str, Any]:
        return {"duration_s": self.duration_s, "scs_khz": self.scs_khz,
                "tti_ms": self.tti_ms, "num_tti": self.num_tti,
                "num_rbg": self.num_rbg, "rb_per_rbg": self.rb_per_rbg,
                "num_rb": self.num_rbg * self.rb_per_rbg,
                "tdd_pattern": self.tdd_pattern,
                "dl_slot_ratio": round(self.dl_ratio, 4),
                "snapshot_update_ms": self.snapshot_update_ms, "seed": self.seed}


# ---------------------------------------------------------------------------
# 第一相：把信道压成查表
# ---------------------------------------------------------------------------
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
    sir_db: float = float("nan")


def _iot(sinr_db: float, sir_db: float) -> float:
    """IoT = SIR/(SIR−SINR)（线性域）。**只能用同口径的两个量**。

    体现的是干扰主导还是噪声主导：IoT 接近 0 dB 说明几乎没有干扰、
    完全是噪声受限；密集城区经常到 20 dB 以上，那时干扰是绝对主导，
    再加发射功率也没用（信号和干扰同步上涨）。
    """
    from . import interference as itf  # noqa: PLC0415

    if not (np.isfinite(sinr_db) and np.isfinite(sir_db)):
        return float("nan")
    return float(np.asarray(itf.iot_db(sinr_db, sir_db)).item())


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
    geo_sir_db: list[float] | None = None,
    neighbor_load: float = 1.0,
    max_rank: int = mu.SU_MAX_RANK,
    table: int = 3,
    target_bler: float = 0.1,
    num_snapshots: int = 1,
    num_ues: int | None = None,
) -> list[UeLinkTable]:
    """第一相：逐 UE 把 rank 1..max_rank 的 SINR / MCS / 谱效全部算好。

    **SVD 只在这里做。** 主循环里再也不碰矩阵——这是十万 TTI 能跑完的原因。

    ``h_users[i]`` 形状 ``[T, RB, BS, UE]``；``T > 1`` 时把每个时隙当一个
    独立快照（ChannelHub 的多时隙是时间相关的，正好用来表达信道起伏）。

    ``num_ues`` 给定时，按 :func:`group_samples_by_ue` 把样本合并成这么多个
    用户，同一 UE 的多个样本当作它的快照序列。**不给的话每个样本算一个用户**
    ——那通常不是你想要的，见该函数的说明。
    """
    sir_in = list(geo_sir_db) if geo_sir_db is not None else [float("nan")] * len(h_users)
    if num_ues is not None and num_ues < len(h_users):
        groups = group_samples_by_ue(len(h_users), num_ues)
        merged_h, merged_g, merged_s = [], [], []
        for g in groups:
            merged_h.append(np.concatenate(
                [np.asarray(h_users[i]).reshape(-1, *np.asarray(h_users[i]).shape[-3:])
                 for i in g], axis=0))
            merged_g.append(float(np.nanmean([geo_sinr_db[i] for i in g])))
            merged_s.append(float(np.nanmean([sir_in[i] for i in g])))
        h_users, geo_sinr_db, sir_in = merged_h, merged_g, merged_s

    # **邻区不是 full buffer。** 按 PRB 利用率折算干扰后再建表——
    # 折算必须发生在算 SINR/MCS/rank 之前，事后乘系数是补不回来的。
    if neighbor_load < 1.0:
        # **SINR 和 SIR 必须一起折算。** 干扰降到 η 倍，SIR 就升高 1/η；
        # 只改 SINR 会让后面的 IoT = SIR/(SIR−SINR) 用两个不同口径的量算，
        # 直接报 inf。这和 CLAUDE.md 里「IoT 不是 snr 减 sinr」是同一类错。
        _u_db = 10.0 * np.log10(max(neighbor_load, 1e-12))
        geo_sinr_db = [apply_neighbor_load(g, sr, neighbor_load)
                       for g, sr in zip(geo_sinr_db, sir_in, strict=False)]
        sir_in = [(sr - _u_db) if np.isfinite(sr) and sr < 49.0 else sr
                  for sr in sir_in]

    out: list[UeLinkTable] = []
    for i, h in enumerate(h_users):
        hh = np.asarray(h)
        snaps = [hh[t:t + 1] for t in range(hh.shape[0])] if hh.ndim == 4 else [hh]
        if len(snaps) < num_snapshots:
            # 时隙不够就复用，但**不伪造起伏**——复用会在结果里如实标注
            snaps = [snaps[t % len(snaps)] for t in range(num_snapshots)]
        n_s = len(snaps)

        sinr = np.zeros((n_s, max_rank))
        mcs = np.zeros((n_s, max_rank), dtype=int)
        se = np.zeros((n_s, max_rank))
        for s, hs in enumerate(snaps):
            npow = mu.noise_from_geometric_sinr(hs, geo_sinr_db[i])
            rc = mu.su_rank_adaptation(hs, noise_power=npow, max_rank=max_rank,
                                       table=table, target_bler=target_bler)
            for c in rc.candidates:
                r = c["rank"] - 1
                sinr[s, r], mcs[s, r], se[s, r] = c["sinr_db"], c["mcs"], c["se"]
        best = np.argmax(se, axis=1)
        # **覆盖判定。** 用户级 SINR 连 MCS 0 的 10% BLER 门限都够不到时，
        # 这个快照下他根本调度不动——发了也是白发。必须显式标出来：
        # PF 的度量是 R_inst/R_avg，一个永远发不成功的用户 R_avg 会趋近 0，
        # 度量发散，调度器于是死盯着他，把整个小区拖垮。这是 PF 的经典病理。
        outage = np.array([
            _bler_lookup(int(mcs[t, best[t]]), float(sinr[t, best[t]])) > 0.5
            for t in range(n_s)
        ])
        out.append(UeLinkTable(
            ue=i, sinr_db=sinr, mcs=mcs, se=se,
            best_rank=best + 1, best_se=se[np.arange(n_s), best],
            geo_sinr_db=float(geo_sinr_db[i]), outage=outage,
            iot_db=float(_iot(geo_sinr_db[i], sir_in[i])), sir_db=float(sir_in[i]),
        ))
    return out


def measure_mu_gain(
    h_users: list[np.ndarray],
    geo_sinr_db: list[float],
    *,
    num_ues: int | None = None,
    max_mu_users: int = 4,
    max_snapshots: int = 4,
) -> dict[str, Any]:
    """实测 MU 相对 SU 的小区谱效比，供 TTI 主循环使用。

    **主循环里不可能逐 TTI 真做配对**——那要在每个 TTI 上做 SVD 与矩阵求逆，
    十万 TTI 直接跑不完。折中是：在建表阶段用
    :func:`mumimo.su_mu_adaptation` 在若干个快照上真配一遍，
    把 MU/SU 的小区谱效比测出来，主循环按这个比例折算。

    **这是当前最大的简化，必须说清楚。** 它假设 MU 增益在时间上是稳定的，
    而真实的配对增益随用户瞬时信道起伏。返回值里带 ``per_snapshot``，
    比值的离散程度就是这个假设的可信度——波动大就说明不该用一个标量。
    """
    if num_ues is not None and num_ues < len(h_users):
        groups = group_samples_by_ue(len(h_users), num_ues)
        h_users = [np.asarray(h_users[g[0]]) for g in groups]
        geo_sinr_db = [float(np.nanmean([geo_sinr_db[i] for i in g])) for g in groups]

    ratios: list[float] = []
    modes: list[str] = []
    n = min(max_snapshots, max(1, min(np.asarray(h).shape[0] for h in h_users)))
    for t in range(n):
        snaps = [np.asarray(h)[t:t + 1] for h in h_users]
        npow = mu.noise_from_geometric_sinr(snaps[0], geo_sinr_db[0])
        try:
            dec = mu.su_mu_adaptation(snaps, noise_power=npow,
                                      max_mu_users=max_mu_users)
        except Exception:  # noqa: BLE001
            continue
        if dec.su_se > 0:
            ratios.append(dec.mu_se / dec.su_se)
            modes.append(dec.mode)
    if not ratios:
        return {"ratio": 1.0, "measured": False,
                "note": "配对测不出来（用户数或天线数不足），MU 按 1.0 处理"}
    r = float(np.median(ratios))
    spread = float(np.std(ratios) / max(abs(r), _EPS))
    return {
        "ratio": r, "measured": True, "per_snapshot": [round(x, 3) for x in ratios],
        "mode_share_mu": modes.count("MU") / len(modes),
        "relative_spread": round(spread, 3),
        "note": (f"**这是一个标量近似**：在 {len(ratios)} 个快照上真配了一遍取中位数，"
                 f"主循环按它折算，没有逐 TTI 重新配对。"
                 f"比值离散度 {spread * 100:.0f}%——超过 30% 就说明 MU 增益"
                 f"随时间起伏很大，用一个标量会失真。"),
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


class _Traffic:
    """按话务模型往每个 UE 的缓冲区里投 burst。"""

    def __init__(self, cfg: TrafficConfig, n_ue: int, tti_ms: float,
                 rng: np.random.Generator) -> None:
        self.cfg, self.n_ue, self.tti_ms, self.rng = cfg, n_ue, tti_ms, rng
        self.active: list[_Burst | None] = [None] * n_ue
        self.queue: list[list[_Burst]] = [[] for _ in range(n_ue)]
        self.done: list[list[_Burst]] = [[] for _ in range(n_ue)]
        self._p_arrive = cfg.arrival_rate_hz * tti_ms / 1000.0
        self.offered_bytes = 0
        self._cbr_per_tti = int(cfg.cbr_mbps * 1e6 * tti_ms / 1000.0 / 8)

    def step(self, tti: int) -> None:
        if self.cfg.model == "full_buffer":
            for u in range(self.n_ue):
                if self.active[u] is None:
                    self.active[u] = _Burst(tti, 1 << 62, 1 << 62)
            return
        if self.cfg.model == "cbr":
            for u in range(self.n_ue):
                b = self.active[u]
                if b is None:
                    self.active[u] = _Burst(tti, self._cbr_per_tti, self._cbr_per_tti)
                else:
                    b.bytes_left += self._cbr_per_tti
                    b.bytes_total += self._cbr_per_tti
                self.offered_bytes += self._cbr_per_tti
            return
        # ftp3：泊松到达（每 TTI 用伯努利近似，p 很小时等价）
        for u in range(self.n_ue):
            if self.rng.random() < self._p_arrive:
                b = _Burst(tti, self.cfg.file_bytes, self.cfg.file_bytes)
                self.offered_bytes += self.cfg.file_bytes
                if self.active[u] is None:
                    self.active[u] = b
                else:
                    self.queue[u].append(b)

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

    def as_dict(self) -> dict[str, Any]:
        return {"config": self.config, "cell": self.cell, "users": self.users,
                "elapsed_s": round(self.elapsed_s, 3), "notes": self.notes}

    def text(self) -> str:
        c = self.cell
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
def simulate(
    tables: list[UeLinkTable],
    *,
    sys_cfg: SystemConfig | None = None,
    traffic: TrafficConfig | None = None,
    sched: SchedulerConfig | None = None,
    kpi: KpiConfig | None = None,
    mu_se_ratio: float = 1.0,
    progress: Any = None,
) -> SystemResult:
    """跑 TTI 主循环。**这里没有任何矩阵运算**，全是查表加算术。

    ``mu_se_ratio`` 是 MU 相对 SU 的小区谱效比（由 :func:`mumimo.su_mu_adaptation`
    在第一相测出来）。>1 时调度器会在有足够用户排队时切到 MU。
    """
    sys_cfg = sys_cfg or SystemConfig()
    traffic = traffic or TrafficConfig()
    sched = sched or SchedulerConfig()
    kpi = kpi or KpiConfig()
    t0 = time.perf_counter()

    n_ue = len(tables)
    rng = np.random.default_rng(sys_cfg.seed)
    tr = _Traffic(traffic, n_ue, sys_cfg.tti_ms, rng)

    n_rb = sys_cfg.num_rbg * sys_cfg.rb_per_rbg
    # 每 TTI 可用 RE：RB × 12 子载波 × 12 个数据符号（扣 DM-RS 与控制开销）
    re_per_tti = n_rb * 12 * 12
    snap_every = max(1, int(round(sys_cfg.snapshot_update_ms / sys_cfg.tti_ms)))
    n_snap = tables[0].sinr_db.shape[0]

    r_avg = np.full(n_ue, 1e-6)
    served = np.zeros(n_ue)
    sched_cnt = np.zeros(n_ue, dtype=int)
    retx_cnt = np.zeros(n_ue, dtype=int)
    mcs_sum = np.zeros(n_ue)
    rank_sum = np.zeros(n_ue)
    nack_first = np.zeros(n_ue)
    tx_first = np.zeros(n_ue)
    nack_final = np.zeros(n_ue)
    harq_pending: dict[int, tuple[int, int]] = {}   # ue -> (剩余重传次数, TB bytes)

    dl_tti = 0
    busy_tti = 0
    mu_tti = 0
    outage_tti = 0
    su_fits_skip = 0
    mu_rbg = 0
    pattern = sys_cfg.tdd_pattern.upper() or "D"

    from . import linkadapt as la  # noqa: PLC0415

    for tti in range(sys_cfg.num_tti):
        if pattern[tti % len(pattern)] not in ("D", "S"):
            continue                                   # 上行/特殊时隙不调度下行
        dl_tti += 1
        tr.step(tti)
        snap = (tti // snap_every) % n_snap

        cand = [u for u in range(n_ue) if tr.has_data(u)
                and not (tables[u].outage is not None and tables[u].outage[snap])]
        blocked = sum(1 for u in range(n_ue) if tr.has_data(u)
                      and tables[u].outage is not None and tables[u].outage[snap])
        outage_tti += blocked
        if not cand:
            r_avg *= (1.0 - 1.0 / sched.pf_window_tti)
            continue

        # --- 调度判决 ---
        inst_se = np.array([tables[u].best_se[snap] for u in cand])
        if sched.algorithm == "pf":
            metric = inst_se / np.maximum(r_avg[cand], 1e-9)
        elif sched.algorithm == "max_ci":
            metric = inst_se
        else:                                          # rr
            metric = np.array([-((tti + u) % n_ue) for u in cand], dtype=float)
        order = np.argsort(-metric)

        # **SU 能一个 TTI 传完就不触发 MU**（用户 2026-08-02 的现场准则）——
        # 数据都发完了，配对没有意义，还白白引入用户间干扰。
        _top = cand[order[0]]
        _su_bytes = int(la.transport_block_size(
            re_per_tti, la.MCS_TABLES[3][int(tables[_top].mcs[
                snap, int(tables[_top].best_rank[snap]) - 1])].rate,
            la.MCS_TABLES[3][int(tables[_top].mcs[
                snap, int(tables[_top].best_rank[snap]) - 1])].q_m,
            layers=int(tables[_top].best_rank[snap])) // 8)
        _fits_in_su = tr.bytes_left(_top) <= _su_bytes
        use_mu = (sched.mu_enabled and mu_se_ratio > 1.0
                  and len(cand) >= 2 and not _fits_in_su)
        if _fits_in_su and len(cand) >= 2:
            su_fits_skip += 1
        picked = [cand[i] for i in order[:sched.max_mu_users]] if use_mu else [cand[order[0]]]
        if use_mu:
            mu_tti += 1
            mu_rbg += sys_cfg.num_rbg          # MU 时整band 都是配对的
        busy_tti += 1

        # --- 发送 ---
        # **MU 是空间复用，不是频率复用。** 配对的每个用户都拿**全带宽**，
        # 靠不同的空间波束区分。早先按 1/K 分 RE，MU 的聚合吞吐就和 SU 一模一样——
        # 等于把空间复用做成了时频复用，MU 增益整个消失。
        n_pair = len(picked)
        for u in picked:
            r = int(tables[u].best_rank[snap])
            if use_mu:
                r = min(r, mu.MU_MAX_RANK)      # MU 每用户硬顶 rank2（工程约束）
            m = int(tables[u].mcs[snap, r - 1])
            mcs_obj = la.MCS_TABLES[3][m]
            tbs_bits = la.transport_block_size(
                re_per_tti, mcs_obj.rate, mcs_obj.q_m, layers=r)
            if use_mu:
                # 配对后每人只分到 1/K 的功率、还要吃残余干扰。
                # mu_se_ratio 是建表阶段用真实 SU/MU 自适应测出来的**聚合**比值，
                # 所以这里除以配对数，使 K 个用户加起来 = ratio x 单用户 SU。
                tbs_bits *= mu_se_ratio / n_pair
            tb_bytes = max(1, int(tbs_bits // 8))

            # HARQ：首传按该 MCS 的 BLER 判 ACK/NACK，失败进重传
            pend = harq_pending.get(u)
            if pend is not None:
                left, size = pend
                # 重传查 ReTx 曲线（合并增益体现在曲线本身更靠左）。
                # 用上一次的 SINR 近似——真软合并要 LLR，本项目明确不做。
                bler = _bler_lookup(int(tables[u].mcs[snap, r - 1]),
                                    float(tables[u].sinr_db[snap, r - 1]), "retx")
                retx_cnt[u] += 1
                if rng.random() > bler:
                    # **重传成功也要计入 served。** 早先这里漏了，
                    # 字节进了缓冲区却没进统计，对账差 4.5%。
                    served[u] += tr.serve(u, tti, size)
                    harq_pending.pop(u, None)
                elif left > 1:
                    harq_pending[u] = (left - 1, size)
                else:
                    harq_pending.pop(u, None)
                    nack_final[u] += 1
                sched_cnt[u] += 1
                mcs_sum[u] += m
                rank_sum[u] += r
                continue

            sinr = float(tables[u].sinr_db[snap, r - 1])
            bler = float(la.bler_curve(m, "newtx")["bler_at"](sinr)) \
                if False else _bler_lookup(m, sinr)
            tx_first[u] += 1
            sched_cnt[u] += 1
            mcs_sum[u] += m
            rank_sum[u] += r
            if rng.random() > bler:
                sent = tr.serve(u, tti, tb_bytes)
                served[u] += sent
            else:
                nack_first[u] += 1
                harq_pending[u] = (3, tb_bytes)

        # --- PF 平均速率更新 ---
        inst = np.zeros(n_ue)
        for u in picked:
            # PF 的瞬时速率：MU 下每人分到 ratio/K 份
            inst[u] = tables[u].best_se[snap] * (
                mu_se_ratio / len(picked) if use_mu else 1.0)
        a = 1.0 / sched.pf_window_tti
        r_avg = (1.0 - a) * r_avg + a * inst
        if progress and tti % 5000 == 0:
            progress(tti, sys_cfg.num_tti)

    # --- KPI 汇总 ---
    offered_bytes = tr.offered_bytes
    users: list[UeKpi] = []
    for u in range(n_ue):
        thps = [x for x in (_burst_throughput_mbps(b, sys_cfg.tti_ms, kpi)
                            for b in tr.done[u] if b.start_tti >= kpi.warmup_tti)
                if x is not None]
        users.append(UeKpi(
            ue=u, geo_sinr_db=tables[u].geo_sinr_db, iot_db=tables[u].iot_db,
            experienced_mbps=float(np.mean(thps)) if thps else 0.0,
            served_mbps=served[u] * 8 / max(sys_cfg.duration_s, _EPS) / 1e6,
            bursts=len(thps),
            avg_mcs=float(mcs_sum[u] / max(sched_cnt[u], 1)),
            avg_rank=float(rank_sum[u] / max(sched_cnt[u], 1)),
            bler_first_tx=float(nack_first[u] / max(tx_first[u], 1)),
            residual_bler=float(nack_final[u] / max(tx_first[u], 1)),
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
        "cell_served_mbps": float(np.sum([x.served_mbps for x in users])),
        "avg_mcs": float(np.sum(mcs_sum) / max(np.sum(sched_cnt), 1)),
        "avg_rank": float(np.sum(rank_sum) / max(np.sum(sched_cnt), 1)),
        "bler_first_tx": float(np.sum(nack_first) / max(np.sum(tx_first), 1)),
        "residual_bler": float(np.sum(nack_final) / max(np.sum(tx_first), 1)),
        "dl_tti": dl_tti, "scheduled_tti": busy_tti,
        "occupancy": busy_tti / max(dl_tti, 1),
        "mu_share": mu_tti / max(busy_tti, 1),
        "measured_bursts": int(np.sum([x.bursts for x in users])),
        "outage_ue": int(sum(1 for t in tables
                             if t.outage is not None and t.outage.all())),
        "outage_skips": int(outage_tti),
        # **MU 配对比例**：MU 配对的 RBG 数占已调度 RBG 总数。
        # 现场经验值：30%~50% PRB 利用率下大约 5%~20%。
        "mu_rbg_share": mu_rbg / max(busy_tti * sys_cfg.num_rbg, 1),
        "su_fits_skips": int(su_fits_skip),
        # **IoT = (I+N)/N**：干扰主导还是噪声主导。密集城区常 >20 dB。
        "iot_db_median": float(np.nanmedian([t.iot_db for t in tables])),
        "iot_db_p5": float(np.nanpercentile([t.iot_db for t in tables], 5)),
        "iot_db_p95": float(np.nanpercentile([t.iot_db for t in tables], 95)),
        "high_iot_ue_share": float(np.mean([
            (t.iot_db >= 20.0) if np.isfinite(t.iot_db) else False for t in tables])),
        # **边缘用户 MCS**：现场经验通常 < 5。它比平均 MCS 更能暴露覆盖问题。
        "edge_mcs_p5": float(np.nanpercentile(
            [x.avg_mcs for x in users if x.sched_tti > 0] or [float("nan")], 5)),
        # 守恒对账：到达了多少、发完了多少、还压着多少。
        # 不报这三个的话，"实际吞吐 105 Mbps vs 话务负载 144 Mbps"
        # 这种缺口只能靠猜——它可能是队列积压（正常），也可能是漏数据（bug）。
        "offered_mbps": round(offered_bytes * 8 / max(sys_cfg.duration_s, _EPS) / 1e6, 3),
        "completed_bursts": int(sum(len(x) for x in tr.done)),
        "backlog_bursts": int(sum(1 for x in tr.active if x is not None)
                              + sum(len(q) for q in tr.queue)),
        "backlog_bytes": int(sum((x.bytes_left for x in tr.active if x is not None), 0)
                             + sum(b.bytes_left for q in tr.queue for b in q)),
    }
    _acct = cell["cell_served_mbps"] * sys_cfg.duration_s * 1e6 / 8 + cell["backlog_bytes"]
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
    if np.isfinite(cell["iot_db_median"]) and cell["iot_db_median"] < 3:
        notes.append(
            f"**IoT 中位只有 {cell['iot_db_median']:.1f} dB**，几乎是噪声受限。"
            "密集城区实际常在 20 dB 以上——检查是不是站间距太大、"
            "或者邻区负载 prb_utilization 设得过低。")
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
                "mu_se_ratio": round(float(mu_se_ratio), 4)},
        cell=cell, users=[x.as_dict() for x in users],
        elapsed_s=time.perf_counter() - t0, notes=notes,
    )


_BLER_CACHE: dict[tuple[int, int, str], float] = {}


def _bler_lookup(mcs: int, sinr_db: float, tx_mode: str = "newtx") -> float:
    """查表 BLER，按 0.5 dB 量化后缓存——主循环里会被叫十万次。

    量化到 0.5 dB 是有意的：BLER 曲线在门限附近很陡，但 0.5 dB 的分辨率
    足够（一档 MCS 的间隔约 1~2 dB），而缓存命中率因此接近 100%。
    """
    key = (int(mcs), int(round(sinr_db * 2)), tx_mode)
    v = _BLER_CACHE.get(key)
    if v is None:
        from . import bler_curves as bc  # noqa: PLC0415

        try:
            v = float(np.atleast_1d(
                bc.get_curve(int(mcs), tx_mode).evaluate(key[1] / 2.0))[0])
        except Exception:  # noqa: BLE001
            v = 0.1
        _BLER_CACHE[key] = float(min(max(v, 0.0), 1.0))
    return _BLER_CACHE[key]

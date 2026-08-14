"""链路性能：预编码 → 后处理 SINR → 谱效。

这是蒙特卡洛仿真最常用的评价链路。设计上刻意把三段拆开，
每段都能单独替换成你自己的算法，再接回来算最终指标：

    预编码 W  →  有效信道 H_eff = W^H · H  →  后处理 SINR  →  谱效

**为什么不直接给一个"谱效"数字。** 谱效取决于三个独立选择：用什么预编码、
用什么接收机、算不算干扰。同一批信道，协方差特征预编码和受限列码本能差好几个
bit/s/Hz。把这三段摊开，对比才有意义——这也正是"你的方法要跟什么比"的落点。

预编码与有效信道复用 ChannelHub 的 ``phy_sim.precoding``（与它的干扰投影逻辑
保持一致）；SINR 与谱效按 MIMO 标准公式实现，口径在各函数文档里写明。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from . import beamforming as bf

_EPS = 1e-30

PrecoderMethod = Literal["svd", "svd_wideband", "dft", "type1", "mrt", "identity"]
ReceiverType = Literal["mmse", "mrc", "zf", "irc"]
InterferenceModel = Literal["isotropic", "precoded", "victim_aligned"]
RuuSource = Literal["true", "sample"]
RankSelection = Literal["max_se", "threshold"]


def _tx_covariance(h: np.ndarray) -> np.ndarray:
    """Return the wideband transmit covariance ``E[H H^H]``.

    Time/frequency samples are *power* observations.  Averaging the complex
    channel first is not equivalent: two otherwise identical snapshots with a
    pi phase rotation would cancel to zero.  Keeping this helper here makes the
    wideband MRT/DFT/Type-I paths use the same physically meaningful statistic.
    """
    hh = np.asarray(h)
    if hh.ndim != 4:
        raise ValueError(f"h 应为 [T, RB, BS_ant, UE_ant]，收到 {hh.shape}")
    cols = np.transpose(hh, (2, 0, 1, 3)).reshape(hh.shape[2], -1)
    return cols @ cols.conj().T / max(cols.shape[1], 1)


# ---------------------------------------------------------------------------
# 预编码
# ---------------------------------------------------------------------------


@dataclass
class Precoder:
    """预编码结果。"""

    w: np.ndarray  # [RB, BS_ant, rank] complex64
    rank: int
    method: str
    singular_values: np.ndarray | None = None  # [RB, min(BS,UE)]
    indices: list[int] | None = None  # 码本方案才有

    def as_dict(self) -> dict[str, Any]:
        return {
            "shape": list(self.w.shape),
            "rank": self.rank,
            "method": self.method,
            "indices": self.indices,
        }


def _covariance_eigen_precoder(
    h: np.ndarray, *, wideband: bool, max_rank: int, rank_threshold: float,
    method: str, forced_rank: int | None = None,
) -> Precoder:
    """Build a phase-invariant static eigen-precoder from ``E[H H^H]``.

    ``Precoder.w`` has no time axis, so for ``T>1`` the physically meaningful
    static weight is based on temporal power covariance.  For ``T=1`` this is
    exactly the left-singular-vector precoder (up to arbitrary column phase).
    """
    hh = np.asarray(h)
    t, rb, bs, ue = hh.shape
    min_dim = min(bs, ue)
    rank_cap = max(1, min(int(max_rank), min_dim))
    # Per-RB R_f = E_t[H_tf H_tf^H].  Do not average complex H over time.
    cov_rb = np.einsum("tfbu,tfcu->fbc", hh, hh.conj()) / max(t, 1)
    cov_eval = np.mean(cov_rb, axis=0, keepdims=True) if wideband else cov_rb
    eigval, eigvec = np.linalg.eigh(cov_eval)
    eigval = np.maximum(eigval.real[:, ::-1], 0.0)
    eigvec = eigvec[:, :, ::-1]
    singular = np.sqrt(eigval[:, :min_dim])
    rank_each = np.ones(singular.shape[0], dtype=int)
    for f, sv in enumerate(singular):
        if sv.size and sv[0] > _EPS:
            rank_each[f] = int(np.clip(
                np.sum(sv > sv[0] * float(rank_threshold)), 1, rank_cap))
    rank = (int(np.clip(forced_rank, 1, rank_cap)) if forced_rank is not None else
            int(rank_each[0] if wideband else
                np.clip(np.median(rank_each), 1, rank_cap)))
    if wideband:
        w0 = eigvec[0, :, :rank]
        w = np.broadcast_to(w0[None], (rb, bs, rank)).copy()
        # Diagnostics remain [RB,min_dim], matching the historical contract.
        s_out = np.broadcast_to(singular[0][None], (rb, min_dim)).copy()
    else:
        w = eigvec[:, :, :rank].copy()
        s_out = singular
    norms = np.linalg.norm(w, axis=1, keepdims=True)
    w = np.where(norms > _EPS, w / norms, w).astype(np.complex64)
    return Precoder(w=w, rank=rank, method=method,
                    singular_values=s_out.astype(np.float32))


def compute_precoder(
    h: np.ndarray,
    *,
    method: PrecoderMethod = "svd",
    max_rank: int = 4,
    rank_threshold: float = 0.1,
    n_h: int | None = None,
    n_v: int | None = None,
    port_order: str | None = None,
    vertical_index_order: str | None = None,
    forced_rank: int | None = None,
) -> Precoder:
    """计算预编码矩阵。

    参数
    ----
    h : ``[T, RB, BS_ant, UE_ant]`` 复数信道。传理想信道得到的是上界，
        传估计信道得到的才是实际系统能做到的——两者的差就是估计误差的代价。
    method :
        * ``svd``          每 RB 用 ``E_t[H H^H]`` 的主特征向量。T=1 时严格等价
          于瞬时 SVD；T>1 时是跨该时间窗的最优静态协方差波束，不冒充逐时隙上界。
        * ``svd_wideband`` 全带/全时域共用一个协方差特征 W。反馈开销更小，
          但有宽带损失，更贴近静态宽带权。
        * ``dft``          DFT 波束码本，rank-1。对应真实网络里的波束赋形。
        * ``type1``        Type-I-style 单面板列码本近似，带量化损失。
        * ``mrt``          最大比传输，rank-1，对准最强方向。
        * ``identity``     不预编码，直接用天线端口。对照用。

    返回的 W 各列已单位化。
    """
    h = np.asarray(h)
    if h.ndim != 4:
        raise ValueError(f"h 应为 [T, RB, BS_ant, UE_ant]，收到 {h.shape}")

    if method in ("svd", "svd_wideband"):
        return _covariance_eigen_precoder(
            h, wideband=(method == "svd_wideband"), max_rank=max_rank,
            rank_threshold=rank_threshold, method=method, forced_rank=forced_rank)

    rb = h.shape[1]
    bs = h.shape[2]
    ue = h.shape[3]
    r_tx = _tx_covariance(h)

    if method == "mrt":
        eigval, eigvec = np.linalg.eigh(r_tx)
        w1 = eigvec[:, int(np.argmax(eigval)) : int(np.argmax(eigval)) + 1]
        w1 = w1 / max(np.linalg.norm(w1), _EPS)
        w = np.broadcast_to(w1[None], (rb, bs, 1)).astype(np.complex64).copy()
        return Precoder(w=w, rank=1, method=method)

    if method == "identity":
        rank = min(forced_rank if forced_rank is not None else max_rank,
                   max_rank, bs, ue)
        eye = np.eye(bs, rank, dtype=np.complex64)
        w = np.broadcast_to(eye[None], (rb, bs, rank)).copy()
        return Precoder(w=w, rank=rank, method=method)

    if method == "dft":
        from .measure import dft_beam_matrix

        beams = dft_beam_matrix(bs)
        metric = np.real(np.sum(beams.conj() * (r_tx @ beams), axis=0))
        best = int(np.argmax(metric))
        w1 = beams[:, best : best + 1]
        w = np.broadcast_to(w1[None], (rb, bs, 1)).astype(np.complex64).copy()
        return Precoder(w=w, rank=1, method=method, indices=[best])

    if method == "type1":
        from .measure import pmi_type_i

        # 秩自适应。38.214 的 Type I 反馈里 RI（秩指示）和 PMI 是一起报的，
        # 真实系统绝不会在低秩信道上硬塞满层——总功率固定时多开的那几层
        # 每层分到的功率更少、SINR 更低，谱效反而掉。
        # 这里用与 SVD 同一套判据（奇异值相对最大值的门限），两者才可比；
        # 缺了这一步，Type I 会在低秩信道上输给 rank-1 的 DFT 波束，
        # 看起来像"码本不如单波束"，其实是没做秩自适应。
        ev = np.linalg.eigvalsh(r_tx).real[::-1]
        eff_rank = (int(forced_rank) if forced_rank is not None else
                    int(max((ev >= (rank_threshold ** 2)
                             * max(ev[0], _EPS)).sum(), 1)))
        eff_rank = min(eff_rank, ue)
        r = pmi_type_i(
            h,
            n_h=n_h,
            n_v=n_v,
            max_rank=min(max_rank, eff_rank),
            port_order=port_order,
            vertical_index_order=vertical_index_order,
        )
        w = np.broadcast_to(r.precoder[None], (rb, *r.precoder.shape)).astype(np.complex64).copy()
        return Precoder(w=w, rank=r.rank, method=method, indices=list(r.indices))

    raise ValueError(f"未知的预编码方式 {method!r}")


def effective_channel(h: np.ndarray, w: np.ndarray) -> np.ndarray:
    """有效信道 ``H_eff[t,rb] = W[rb]^H · H[t,rb]``，形状 ``[T, RB, rank, UE_ant]``。"""
    return np.einsum("fbr,tfbu->tfru", np.conj(w), np.asarray(h)).astype(np.complex64)


# ---------------------------------------------------------------------------
# 后处理 SINR
# ---------------------------------------------------------------------------


@dataclass
class LinkPerformance:
    """一次链路评估的完整结果。"""

    sinr_per_layer_db: np.ndarray  # [rank] 各层后处理 SINR
    spectral_efficiency: float  # bit/s/Hz，各层求和后按频率平均
    se_per_layer: np.ndarray  # [rank]
    rank: int
    method: str
    receiver: str
    precoder_indices: list[int] | None
    capacity_bound: float  # 同信道的容量上界，用于看离天花板多远
    sinr_per_rb_db: np.ndarray  # [RB, rank] 逐 RB 逐层
    noise_power: float
    interference_power: float
    # IRC 能零陷几个干扰，取决于 R_uu 的有效秩——它必须跟着结果一起走，
    # 否则"IRC 涨了 2.4 bit/s/Hz"这个数没法判断可不可信。
    interference_rank: float | None = None
    interference_model: str | None = None
    r_uu_source: str | None = None
    operating_point: dict[str, Any] | None = None
    rank_selection: str = "threshold"
    rank_candidates: list[dict[str, float | int]] = field(default_factory=list)
    power_constraint: str = "ebf"
    power_diagnostics: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sinr_per_layer_db": [round(float(x), 2) for x in self.sinr_per_layer_db],
            "spectral_efficiency": round(float(self.spectral_efficiency), 4),
            "se_per_layer": [round(float(x), 4) for x in self.se_per_layer],
            "rank": self.rank,
            "method": self.method,
            "receiver": self.receiver,
            "precoder_indices": self.precoder_indices,
            "capacity_bound": round(float(self.capacity_bound), 4),
            "efficiency_vs_bound": round(
                float(self.spectral_efficiency / max(self.capacity_bound, _EPS)), 3
            ),
            "noise_power": float(self.noise_power),
            "interference_power": float(self.interference_power),
            "interference_rank": (None if self.interference_rank is None
                                  else round(float(self.interference_rank), 2)),
            "interference_model": self.interference_model,
            "r_uu_source": self.r_uu_source,
            "operating_point": self.operating_point,
            "rank_selection": self.rank_selection,
            "rank_candidates": self.rank_candidates,
            "power_constraint": self.power_constraint,
            "power_diagnostics": self.power_diagnostics,
        }


def _noise_from_snr(h: np.ndarray, snr_db: float) -> float:
    """由信道平均增益和**合成的预波束 SNR**反推噪声功率。

    约定：SNR = E[|h|^2]·P_tx / N0，取 P_tx = 1。这样同一批信道在不同
    信噪比下的对比是干净的——只有噪声在变。
    """
    sig = prebeam_reference_power(h)
    return sig / max(10.0 ** (snr_db / 10.0), _EPS)


def prebeam_reference_power(h: np.ndarray, *, total_power: float = 1.0) -> float:
    """Per-coefficient pre-digital-beam signal reference ``E[|h|²]·P``.

    First-party ChannelHub ``snr_dB/sir_dB/sinr_dB`` scalars are defined at
    this reference.  Fixed subarray and element-pattern gains are already in
    the conducted link budget; digital multi-port precoding gain remains in
    ``H`` and is therefore added exactly once by the link-level calculation.
    """
    if not np.isfinite(total_power) or float(total_power) < 0:
        raise ValueError(f"total_power 必须是有限非负数，收到 {total_power}")
    return float(np.mean(np.abs(np.asarray(h)) ** 2)) * float(total_power)


def rank1_reference_power(h: np.ndarray, *, total_power: float = 1.0) -> float:
    """返回 rank-1 最强特征波束的平均接收信号功率 ``E[σ₁²]·P``。

    这是一个后波束诊断量，不是 ChannelHub 标量的默认锚点。频域最多抽 32 个
    RB；默认工作点使用 :func:`prebeam_reference_power`，让数字预编码增益由 H
    贡献一次。
    """
    hb = np.asarray(h)
    if hb.ndim == 3:
        hb = hb[None]
    if hb.ndim != 4:
        raise ValueError(f"h 应为 [T,RB,BS,UE] 或 [RB,BS,UE]，收到 {hb.shape}")
    if not np.isfinite(total_power) or float(total_power) < 0:
        raise ValueError(f"total_power 必须是有限非负数，收到 {total_power}")
    n_rb = hb.shape[1]
    step = max(1, n_rb // 32)
    s1 = float(np.mean([
        np.linalg.svd(hb[t, f], compute_uv=False)[0] ** 2
        for t in range(hb.shape[0]) for f in range(0, n_rb, step)
    ]))
    return s1 * float(total_power)


@dataclass(frozen=True)
class GeometricImpairment:
    """把 ChannelHub 几何 SINR 映射成链路级噪声/干扰的可审计结果。"""

    noise_power: float
    interference_cov: np.ndarray | None
    signal_reference_power: float
    total_impairment_power: float
    interference_power: float
    sinr_db: float
    sir_db: float | None
    model: str
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "dataset_geometric_sinr",
            "anchor": "prebeam_mean_coefficient_power",
            "sinr_db": round(float(self.sinr_db), 4),
            "sir_db": None if self.sir_db is None else round(float(self.sir_db), 4),
            "signal_reference_power": float(self.signal_reference_power),
            "total_impairment_power": float(self.total_impairment_power),
            "noise_power": float(self.noise_power),
            "interference_power": float(self.interference_power),
            "interference_covariance_used": self.interference_cov is not None,
            "model": self.model,
            "note": self.note,
        }


def post_equalizer_sinr(
    h_eff: np.ndarray,
    noise_power: float,
    *,
    receiver: ReceiverType = "mmse",
    interference_cov: np.ndarray | None = None,
) -> np.ndarray:
    """接收机均衡后的逐层 SINR（线性值），形状 ``[RB, rank]``。

    口径
    ----
    设某个 RB 上有效信道 ``G = H_eff^H``（形状 ``[UE_ant, rank]``），
    每层等分发射功率 ``P/rank``（P=1），噪声加干扰协方差 ``R_n``：

    * **MMSE**：``SINR_k = 1 / [ (I + (P/rank)·G^H R_n^{-1} G)^{-1} ]_kk - 1``
      这是线性 MMSE 接收机的标准结果，也是最常用的口径。
    * **ZF**：``SINR_k = (P/rank) / [ (G^H R_n^{-1} G)^{-1} ]_kk``
      迫零，完全消除层间干扰但放大噪声。
    * **MRC**：逐层最大比合并，**不消除层间干扰**，多层时会明显偏低——
      单层传输时才等价于最优。
    * **IRC**：与 MMSE 同一个公式，区别**全在 ``R_n`` 怎么给**——见下。

    ``interference_cov`` 给定时（``[UE_ant, UE_ant]`` 或 ``[RB, UE_ant, UE_ant]``），
    干扰会计入 ``R_n``，得到的是真正的 SINR 而非 SNR。

    MMSE 与 IRC 的分工
    ------------------
    **公式相同，喂进去的 ``R_n`` 不同，这才是这两个词的全部区别。**

    * ``receiver="mmse"``：把干扰当**白噪声**——只取干扰总功率摊到各接收天线，
      ``R_n = (N0 + I_tot/N_rx)·I``。这是业界默认的对照基线。
    * ``receiver="irc"``：用干扰的**完整空间协方差**（有色、通常低秩），
      接收机据此在干扰来向上打零陷。

    所以 IRC 相对 MMSE 的增益完全来自 ``R_uu`` 的**非白性**。
    如果传进来的 ``interference_cov`` 本身就接近单位阵，两者必然重合——
    这不是实现错了，是干扰真的白。
    """
    h_eff = np.asarray(h_eff)
    if h_eff.ndim == 3:
        h_tf = h_eff[None]
    elif h_eff.ndim == 4:
        h_tf = h_eff
    else:
        raise ValueError(f"h_eff 应为 [RB,rank,UE] 或 [T,RB,rank,UE]，收到 {h_eff.shape}")
    n_t, rb, rank, ue = h_tf.shape
    p_per_layer = 1.0 / max(rank, 1)

    if not np.isfinite(noise_power) or float(noise_power) < 0:
        raise ValueError(f"noise_power 必须是有限非负数，收到 {noise_power}")
    out_tf = np.zeros((n_t, rb, rank), dtype=np.float64)
    for t in range(n_t):
        for f in range(rb):
            g = h_tf[t, f].conj().T  # [UE, rank]
            r_n = np.eye(ue, dtype=np.complex128) * max(float(noise_power), _EPS)
            if interference_cov is not None:
                ic = np.asarray(interference_cov)
                r_uu = ic[f] if ic.ndim == 3 else ic
                if receiver == "irc":
                    r_n = r_n + r_uu            # 保留空间结构，才能打零陷
                else:
                    # **非 IRC 的接收机把干扰当白噪声。** 只取总功率摊到各天线，
                    # 丢掉方向信息——这正是 IRC 要赢的那个基线。
                    r_n = r_n + np.eye(ue, dtype=np.complex128) * (
                        float(np.real(np.trace(r_uu))) / max(ue, 1)
                    )

            r_inv = np.linalg.pinv(r_n)
            a = g.conj().T @ r_inv @ g  # [rank, rank]

            if receiver in ("mmse", "irc"):
                m = np.eye(rank, dtype=np.complex128) + p_per_layer * a
                m_inv = np.linalg.pinv(m)
                diag = np.real(np.diag(m_inv))
                out_tf[t, f] = np.maximum(
                    1.0 / np.maximum(diag, _EPS) - 1.0, 0.0)
            elif receiver == "zf":
                a_inv = np.linalg.pinv(a)
                out_tf[t, f] = p_per_layer / np.maximum(
                    np.real(np.diag(a_inv)), _EPS)
            elif receiver == "mrc":
                for k in range(rank):
                    gk = g[:, k]
                    sig = p_per_layer * float(np.real(gk.conj() @ r_inv @ gk)) ** 2
                    # 层间干扰：其余层经同一 MRC 权后的泄漏
                    leak = 0.0
                    for j in range(rank):
                        if j == k:
                            continue
                        leak += p_per_layer * abs(
                            complex(gk.conj() @ r_inv @ g[:, j])) ** 2
                    nz = float(np.real(gk.conj() @ r_inv @ gk))
                    out_tf[t, f, k] = sig / max(leak + nz, _EPS)
            else:
                raise ValueError(f"未知接收机 {receiver!r}")

    if n_t == 1:
        return out_tf[0]
    # API 历史上返回 [RB,rank]。用速率等价 SINR 折叠时间，保证后续
    # log2(1+SINR) 恰好等于逐时隙速率平均；绝不能先平均复信道。
    return np.expm1(np.mean(np.log1p(out_tf), axis=0))


def interference_covariance(
    h_interferers: np.ndarray,
    *,
    model: InterferenceModel = "precoded",
    r_uu_source: RuuSource = "true",
    r_uu_samples: int = 8,
    diagonal_loading: float = 0.01,
    seed: int | None = 0,
) -> np.ndarray:
    """干扰在终端侧的空间协方差 ``R_uu``，形状 ``[RB, UE_ant, UE_ant]``。

    **这个函数决定 IRC 有没有东西可赢。** IRC 的全部增益来自 ``R_uu`` 的非白性，
    所以怎么建它比接收机公式本身重要得多。

    ``model`` —— 干扰小区在发什么
    ------------------------------
    * ``"precoded"``（默认）：邻区波束与受害 UE 的交叉信道**统计独立**。
      数据集没有保存邻区被服务 UE 的信道，因而不能从受害 UE 的 ``H_k`` 反推
      邻区实际波束；这里按 ``seed`` 生成单位范数宽带波束，并在同一快照的所有
      RB 上复用。几何 SIR 决定总干扰功率，本函数只提供空间/频率形状。
    * ``"victim_aligned"``：故障复现/上界诊断。用受害 UE 交叉信道的主左奇异
      向量发射，等于假设邻区故意把波束对准受害 UE；它不是“服务自己用户”。
    * ``"isotropic"``：干扰小区**各向同性**发射，``R_uu = (1/N_bs)·Σ H_k^H H_k``。

    两者差别不是细节。各向同性会把 ``R_uu`` 洗白、把秩抬到满，
    IRC 相对 MMSE 的增益随之**系统性偏小**——看起来像"IRC 没什么用"，
    其实是干扰建模把它能利用的结构抹掉了。

    ``r_uu_source`` —— 接收机拿到的是真值还是估计
    ---------------------------------------------
    * ``"true"``：用真实协方差。**这是上界，不是可实现性能。**
    * ``"sample"``：用 ``r_uu_samples`` 个快照的样本协方差 + 对角加载
      ``diagonal_loading``（相对于迹）。真实接收机只能这么干，
      样本数少于天线数时样本协方差是奇异的，必须加载。

    默认给 ``"true"`` 是因为它可复现、适合做机理研究；
    **报 IRC 增益时必须说清用的哪个**，否则数字不可比。
    """
    hi = np.asarray(h_interferers)
    if hi.ndim != 5:
        raise ValueError(f"h_interferers 需要 [K-1, T, RB, BS, UE]，实得 {hi.shape}")
    n_k, n_t, rb, _bs, ue = hi.shape
    beams: np.ndarray | None = None
    if model == "precoded":
        # 每个干扰源使用独立、可复现的随机流。这样给输入追加第 K 个干扰源时，
        # 前 K-1 个源的波束不会改变，R_uu(K)-R_uu(K-1) 仍严格是 PSD。
        beams = np.empty((n_k, n_t, _bs), dtype=np.complex128)
        for k in range(n_k):
            child_seed = None if seed is None else np.random.SeedSequence([int(seed), k])
            rng_k = np.random.default_rng(child_seed)
            z = (rng_k.standard_normal((n_t, _bs))
                 + 1j * rng_k.standard_normal((n_t, _bs)))
            beams[k] = z / np.maximum(np.linalg.norm(z, axis=1, keepdims=True), _EPS)

    def _cov_from(hk_f: np.ndarray, beam: np.ndarray | None = None) -> np.ndarray:
        """单个干扰小区在单个 RB 上的贡献，``hk_f`` 是 ``[BS, UE]``。"""
        if model == "isotropic":
            return hk_f.conj().T @ hk_f / max(hk_f.shape[0], 1)
        if model == "victim_aligned":
            u, _s, _vh = np.linalg.svd(hk_f, full_matrices=False)
            w = u[:, :1]                   # 故障复现：波束对准受害 UE
        else:
            if beam is None:
                raise RuntimeError("precoded 干扰模型缺少独立邻区波束")
            w = np.asarray(beam).reshape(-1, 1)
        y = (w.conj().T @ hk_f).ravel()    # [UE] 到达本终端的干扰空间签名
        # 功率归一到与各向同性同一口径：迹相同，只是分布不同。
        # 不归一的话 precoded 会同时改变干扰"强度"和"方向"，
        # 两个因素混在一起，IRC 增益就说不清是哪来的。
        iso_tr = float(np.real(np.trace(hk_f.conj().T @ hk_f))) / max(hk_f.shape[0], 1)
        cov = np.outer(y.conj(), y)
        tr = float(np.real(np.trace(cov)))
        return cov * (iso_tr / tr) if tr > _EPS else cov

    cov = np.zeros((rb, ue, ue), dtype=np.complex128)
    if r_uu_source == "true":
        for k in range(n_k):
            for f in range(rb):
                cov[f] += np.mean(
                    np.stack([_cov_from(
                        hi[k, t, f], None if beams is None else beams[k, t])
                        for t in range(n_t)]), axis=0)
        return cov

    # sample：只能使用输入里真实存在的快照。过去在 T 不够时给同一个信道加 5%
    # 人工抖动来“补样本”，会凭空抬高协方差秩，让 IRC 获得不存在的信息。
    # 样本不足导致的奇异性必须由对角加载处理，不能伪造新观测。
    n_s = min(max(int(r_uu_samples), 1), n_t)
    sample_idx = range(n_t - n_s, n_t)
    for k in range(n_k):
        for f in range(rb):
            acc = np.zeros((ue, ue), dtype=np.complex128)
            for s_i in sample_idx:
                acc += _cov_from(
                    hi[k, s_i, f], None if beams is None else beams[k, s_i])
            cov[f] += acc / n_s
    load = float(diagonal_loading)
    if load > 0:
        for f in range(rb):
            cov[f] += np.eye(ue) * (float(np.real(np.trace(cov[f]))) / max(ue, 1) * load)
    return cov


def geometric_impairment(
    h: np.ndarray,
    sinr_db: float,
    *,
    sir_db: float | None = None,
    h_interferers: np.ndarray | None = None,
    total_power: float = 1.0,
    interference_model: InterferenceModel = "precoded",
    r_uu_source: RuuSource = "true",
    r_uu_samples: int = 8,
    diagonal_loading: float = 0.01,
    seed: int | None = 0,
) -> GeometricImpairment:
    """把几何 ``SINR/SIR`` 标定成链路计算可直接使用的损伤功率。

    ``sinr_dB`` 的信号项锚到预波束单系数功率 ``E[|h|²]·P``。若同时有可信的
    ``sir_dB`` 与干扰信道，则按

    ``I = S/10^(SIR/10)``, ``N = S/10^(SINR/10) - I``

    分离噪声和干扰，并只用干扰信道提供协方差的空间/频率形状；协方差的平均
    每接收天线功率会重新缩放到 ``I``。这样 MMSE 的白干扰基线与几何工作点同量纲，
    IRC 才能只靠空间结构取得增益。缺少任一条件或标量不自洽时，不会把未标定的
    干扰信道再叠一次，而是把 ``I+N`` 全部作为各向同性损伤。
    """
    s = prebeam_reference_power(h, total_power=total_power)
    sinr = float(sinr_db)
    if not np.isfinite(sinr):
        raise ValueError(f"sinr_db 必须是有限数，收到 {sinr_db}")
    total = s / max(10.0 ** (sinr / 10.0), _EPS)

    fallback_note = (
        "几何 SINR 是预数字波束口径；缺少可自洽的 SIR/干扰协方差，"
        "故将总损伤 I+N 作为白噪声，不重复叠加 h_interferers。"
    )
    if sir_db is None or h_interferers is None or not np.isfinite(float(sir_db)):
        return GeometricImpairment(
            noise_power=total, interference_cov=None,
            signal_reference_power=s, total_impairment_power=total,
            interference_power=0.0, sinr_db=sinr,
            sir_db=None if sir_db is None else float(sir_db),
            model="prebeam_anchor_total_impairment_isotropic", note=fallback_note,
        )

    sir = float(sir_db)
    interference = s / max(10.0 ** (sir / 10.0), _EPS)
    tol = max(total, _EPS) * 1e-9
    if interference > total + tol:
        return GeometricImpairment(
            noise_power=total, interference_cov=None,
            signal_reference_power=s, total_impairment_power=total,
            interference_power=0.0, sinr_db=sinr, sir_db=sir,
            model="prebeam_anchor_total_impairment_isotropic_inconsistent_sir",
            note=(fallback_note + f" 当前 SIR={sir:.4f} dB 小于 SINR={sinr:.4f} dB，"
                  "会推出负噪声，已显式回退。"),
        )

    raw = interference_covariance(
        h_interferers, model=interference_model, r_uu_source=r_uu_source,
        r_uu_samples=r_uu_samples, diagonal_loading=diagonal_loading, seed=seed,
    )
    ue = raw.shape[-1]
    raw_power = float(
        np.mean(np.real(np.trace(raw, axis1=1, axis2=2))) / max(ue, 1)
    )
    if not np.isfinite(raw_power) or raw_power <= _EPS:
        return GeometricImpairment(
            noise_power=total, interference_cov=None,
            signal_reference_power=s, total_impairment_power=total,
            interference_power=0.0, sinr_db=sinr, sir_db=sir,
            model="prebeam_anchor_total_impairment_isotropic_zero_interferer",
            note=fallback_note + " 干扰协方差功率为零，已显式回退。",
        )

    scaled = raw * (interference / raw_power)
    noise = max(total - interference, 0.0)
    return GeometricImpairment(
        noise_power=noise, interference_cov=scaled,
        signal_reference_power=s, total_impairment_power=total,
        interference_power=interference, sinr_db=sinr, sir_db=sir,
        model="prebeam_anchor_sinr_sir_spatial_split",
        note=(
            "S 取预波束 E[|h|²]·P；SINR 给 I+N，SIR 给 I；"
            "h_interferers 只提供空间/频率形状并按几何 I 重标定。"
        ),
    )


def effective_rank(cov: np.ndarray, threshold: float = 0.01) -> float:
    """``R_uu`` 的有效秩（特征值大于最大值 ``threshold`` 倍的个数，各 RB 平均）。

    **IRC 的可零陷干扰数上限就是它。** ``N_rx`` 根接收天线最多零陷
    ``N_rx - 1`` 个独立干扰方向；有效秩逼近 ``N_rx`` 时 IRC 相对 MMSE
    的增益必然趋近 0——那时干扰在空间上已经接近白的，没有结构可利用。

    实测提醒：ChannelHub 的**单个干扰小区信道是秩 1 的**
    （σ₂/σ₁ ≈ 4e-8，96 个抽样全部如此），而服务小区是满秩。
    这让 IRC 处在最有利的工况——3 个干扰小区、4 根接收天线，
    刚好能全部零陷。**真实干扰不会这么干净，所以这里的 IRC 增益偏乐观。**
    """
    c = np.asarray(cov)
    if c.ndim == 2:
        c = c[None]
    ranks = []
    for f in range(c.shape[0]):
        ev = np.linalg.eigvalsh(c[f]).real
        top = float(ev.max())
        if top <= _EPS:
            continue
        ranks.append(int(np.sum(ev > top * threshold)))
    return float(np.mean(ranks)) if ranks else 0.0


def capacity_upper_bound(
    h: np.ndarray,
    noise_power: float,
    *,
    interference_cov: np.ndarray | None = None,
) -> float:
    """Perfect-CSI MIMO capacity with per-resource water-filling, bit/s/Hz.

    Total transmit power is one on every time/frequency resource, matching the
    SU/MU paths.  A former implementation spread that power across *all* channel
    modes.  That is a baseline, not an upper bound: concentrating power on fewer
    strong modes can beat it.  Here each ``[T,RB]`` realization is water-filled
    over its singular modes and capacities are then averaged non-coherently.
    """
    hh = np.asarray(h)
    if hh.ndim == 3:
        hh = hh[None]
    if hh.ndim != 4:
        raise ValueError(f"h 应为 [T,RB,BS,UE] 或 [RB,BS,UE]，收到 {hh.shape}")
    if not np.isfinite(noise_power) or float(noise_power) < 0:
        raise ValueError(f"noise_power 必须是有限非负数，收到 {noise_power}")
    n0 = max(float(noise_power), _EPS)

    # 无有色干扰时保留批量 SVD 快路径。给定 R_uu 时先以
    # R_n = N0 I + R_uu 白化接收端，再对等效奇异模做注水；这才是同一损伤口径下
    # 的容量上界，不能在性能里算了干扰、在“上界”里又把干扰丢掉。
    normalized_noise = n0
    if interference_cov is None:
        gains = (np.linalg.svd(hh, compute_uv=False) ** 2).reshape(
            -1, min(hh.shape[-2:]))
    else:
        cov = np.asarray(interference_cov, dtype=np.complex128)
        if cov.ndim == 2:
            cov = np.broadcast_to(cov[None], (hh.shape[1], *cov.shape))
        if cov.shape != (hh.shape[1], hh.shape[3], hh.shape[3]):
            raise ValueError(
                "interference_cov 应为 [UE,UE] 或 [RB,UE,UE]，"
                f"收到 {cov.shape}，信道为 {hh.shape}"
            )
        gains_list: list[np.ndarray] = []
        eye = np.eye(hh.shape[3], dtype=np.complex128)
        for t in range(hh.shape[0]):
            for f in range(hh.shape[1]):
                rn = n0 * eye + 0.5 * (cov[f] + cov[f].conj().T)
                ev, vec = np.linalg.eigh(rn)
                scale = max(float(np.max(np.abs(ev))), _EPS)
                if float(np.min(ev)) < -1e-9 * scale:
                    raise ValueError("interference_cov 不是正半定矩阵")
                inv_sqrt = (vec * (1.0 / np.sqrt(np.maximum(ev, _EPS)))[None, :]) @ vec.conj().T
                gains_list.append(np.linalg.svd(hh[t, f] @ inv_sqrt, compute_uv=False) ** 2)
        gains = np.stack(gains_list)
        normalized_noise = 1.0

    # Closed-form active-set water filling. Total transmit power is one.
    inv = np.where(
        gains > _EPS, normalized_noise / np.maximum(gains, _EPS), np.inf)
    order = np.argsort(inv, axis=1)
    inv_s = np.take_along_axis(inv, order, axis=1)
    k = np.arange(1, inv_s.shape[1] + 1, dtype=float)[None, :]
    levels = (1.0 + np.cumsum(inv_s, axis=1)) / k
    n_active = np.sum(levels > inv_s, axis=1).astype(int)
    caps = np.zeros(gains.shape[0], dtype=float)
    valid_rows = np.flatnonzero(n_active > 0)
    if valid_rows.size:
        ka = n_active[valid_rows]
        mu = levels[valid_rows, ka - 1]
        power_s = np.maximum(mu[:, None] - inv_s[valid_rows], 0.0)
        power = np.zeros_like(power_s)
        np.put_along_axis(power, order[valid_rows], power_s, axis=1)
        caps[valid_rows] = np.sum(
            np.log2(1.0 + power * gains[valid_rows] / normalized_noise), axis=1)
    return float(np.mean(caps)) if caps.size else 0.0


def link_performance(
    h: np.ndarray,
    *,
    snr_db: float | None = None,
    noise_power: float | None = None,
    method: PrecoderMethod = "svd",
    receiver: ReceiverType = "mmse",
    max_rank: int = 4,
    rank_threshold: float = 0.1,
    h_for_precoding: np.ndarray | None = None,
    h_interferers: np.ndarray | None = None,
    interference_cov: np.ndarray | None = None,
    n_h: int | None = None,
    n_v: int | None = None,
    port_order: str | None = None,
    vertical_index_order: str | None = None,
    interference_model: InterferenceModel = "precoded",
    r_uu_source: RuuSource = "true",
    r_uu_samples: int = 8,
    diagonal_loading: float = 0.01,
    seed: int | None = 0,
    operating_point: dict[str, Any] | None = None,
    rank_selection: RankSelection = "max_se",
    power_constraint: bf.PowerConstraint | str = "ebf",
) -> LinkPerformance:
    """一站式：预编码 → 有效信道 → 逐层 SINR → 谱效。

    参数
    ----
    h : ``[T, RB, BS_ant, UE_ant]`` 用于**评估**的真实信道。
    h_for_precoding : 用于**计算预编码**的信道，默认与 h 相同。
        传估计信道即可评估"用有误差的 CSI 做预编码"的代价——
        这是 CSI 反馈类课题最核心的对比。
    snr_db / noise_power : 二选一。``snr_db`` 是显式合成的**预波束 SNR**，按
        ``mean(|h|²)`` 反推噪声；数据集几何 SINR 必须先经
        :func:`geometric_impairment` 标定，再传 ``noise_power``。
    h_interferers : ``[K-1, T, RB, BS_ant, UE_ant]``，给定则计入干扰，
        得到真正的 SINR。
    interference_cov : 已经标定功率的 ``[RB,UE,UE]`` 干扰协方差；与
        ``h_interferers`` 互斥。
    receiver : ``mmse`` 把干扰当白噪声（基线），``irc`` 用完整空间协方差打零陷。
        两者公式相同，差别只在 ``R_n``——见 ``post_equalizer_sinr``。
    interference_model / r_uu_source : 见 ``interference_covariance``。
        **报 IRC 增益时这两个必须一起报**，换一个设置数字就不可比。

    谱效口径：``SE = mean_rb Σ_layer log2(1 + SINR[rb, layer])``。
    """
    h = np.asarray(h)
    if h_interferers is not None and interference_cov is not None:
        raise ValueError("h_interferers 与 interference_cov 只能给一个")
    if noise_power is None:
        if snr_db is None:
            raise ValueError("snr_db 与 noise_power 至少给一个")
        noise_power = _noise_from_snr(h, snr_db)

    intf_cov = None if interference_cov is None else np.asarray(interference_cov)
    intf_power = 0.0
    intf_rank: float | None = None
    if h_interferers is not None:
        intf_cov = interference_covariance(
            h_interferers, model=interference_model,
            r_uu_source=r_uu_source, r_uu_samples=r_uu_samples,
            diagonal_loading=diagonal_loading, seed=seed,
        )
    if intf_cov is not None:
        ue = intf_cov.shape[-1]
        if intf_cov.ndim == 2:
            intf_power = float(np.real(np.trace(intf_cov))) / max(ue, 1)
        else:
            intf_power = float(
                np.mean(np.real(np.trace(intf_cov, axis1=1, axis2=2)))
                / max(ue, 1)
            )
        intf_rank = effective_rank(intf_cov)

    h_p = np.asarray(h_for_precoding) if h_for_precoding is not None else h
    if rank_selection not in ("max_se", "threshold"):
        raise ValueError(f"rank_selection 只支持 'max_se'/'threshold'，收到 {rank_selection!r}")
    rank_candidates: list[dict[str, float | int]] = []
    if rank_selection == "threshold" or method in ("mrt", "dft"):
        prec = compute_precoder(
            h_p, method=method, max_rank=max_rank, rank_threshold=rank_threshold,
            n_h=n_h, n_v=n_v, port_order=port_order,
            vertical_index_order=vertical_index_order,
        )
    else:
        # Rank 必须按发送端可获得的 h_p 选择，再固定该权到 h_true 上评估。
        # 旧的“奇异值超过最大值 10% 就开层”不看 N0/I：同一信道在低 SNR 与
        # 高 SNR 会选同一个 rank，弱层分走功率后甚至让总谱效下降。
        r_cap = max(1, min(int(max_rank), h_p.shape[-2], h_p.shape[-1]))
        prec = None
        best_score = -np.inf
        # SVD/identity 的列天然按强到弱排列；Type-I-style 搜索是增量贪心，rank R
        # 的前 r 列也与单独搜 rank r 相同。一次搜满后切前缀，避免把码本搜索
        # 无意义地重复四遍。
        full = compute_precoder(
            h_p, method=method, max_rank=r_cap,
            rank_threshold=rank_threshold, n_h=n_h, n_v=n_v,
            port_order=port_order,
            vertical_index_order=vertical_index_order,
            forced_rank=r_cap,
        )
        for requested_rank in range(1, full.rank + 1):
            cand = Precoder(
                w=full.w[:, :, :requested_rank], rank=requested_rank,
                method=full.method, singular_values=full.singular_values,
                indices=(None if full.indices is None
                         else full.indices[:requested_rank]),
            )
            _q_cand, w_model, _pdiag = bf.equal_power_weights(
                cand.w, mode=power_constraint, total_power=1.0)
            pred_sinr = post_equalizer_sinr(
                effective_channel(h_p, w_model), noise_power,
                receiver=receiver, interference_cov=intf_cov)
            score = float(np.mean(np.sum(
                np.log2(1.0 + np.maximum(pred_sinr, 0.0)), axis=1)))
            rank_candidates.append({
                "rank": int(cand.rank), "predicted_se": round(score, 6)})
            if score > best_score + 1e-12:
                best_score, prec = score, cand
        if prec is None:  # pragma: no cover - r_cap 至少为 1
            raise RuntimeError("rank 候选为空")
    _q, w_model, pdiag = bf.equal_power_weights(
        prec.w, mode=power_constraint, total_power=1.0)
    h_eff = effective_channel(h, w_model)

    sinr_lin = post_equalizer_sinr(
        h_eff, noise_power, receiver=receiver, interference_cov=intf_cov
    )
    se_rb = np.log2(1.0 + np.maximum(sinr_lin, 0.0))  # [RB, rank]
    se_per_layer = se_rb.mean(axis=0)

    # 报告的逐层 SINR 必须能反算出上面的逐层谱效。线性 SINR 先平均会被
    # 频选尖峰抬高；正确口径是速率等效 SINR：exp(E[ln(1+SINR)])-1。
    sinr_rate_equiv = np.expm1(np.mean(np.log1p(np.maximum(sinr_lin, 0.0)), axis=0))

    return LinkPerformance(
        sinr_per_layer_db=10.0 * np.log10(np.maximum(sinr_rate_equiv, _EPS)),
        spectral_efficiency=float(se_per_layer.sum()),
        se_per_layer=se_per_layer,
        rank=prec.rank,
        method=prec.method,
        receiver=receiver,
        precoder_indices=prec.indices,
        capacity_bound=capacity_upper_bound(
            h, noise_power, interference_cov=intf_cov),
        sinr_per_rb_db=10.0 * np.log10(np.maximum(sinr_lin, _EPS)),
        noise_power=float(noise_power),
        interference_power=intf_power,
        interference_rank=intf_rank,
        interference_model=(
            interference_model if h_interferers is not None
            else ("provided_covariance" if interference_cov is not None else None)
        ),
        r_uu_source=(
            r_uu_source if h_interferers is not None
            else ("provided" if interference_cov is not None else None)
        ),
        operating_point=operating_point,
        rank_selection=rank_selection,
        rank_candidates=rank_candidates,
        power_constraint=str(power_constraint).lower(),
        power_diagnostics=pdiag.as_dict(),
    )


# ---------------------------------------------------------------------------
# 蒙特卡洛
# ---------------------------------------------------------------------------


@dataclass
class MonteCarloResult:
    """一批样本上的统计结果，含收敛性判断。"""

    n: int
    se_mean: float
    se_std: float
    se_ci95: tuple[float, float]
    se_percentiles: dict[str, float]
    sinr_mean_db: float
    rank_hist: dict[int, int]
    converged: bool
    relative_ci_width: float
    method: str
    receiver: str
    power_constraint: str = "ebf"
    per_sample_se: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))
    operating_point_mode: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "se_mean": round(self.se_mean, 4),
            "se_std": round(self.se_std, 4),
            "se_ci95": [round(x, 4) for x in self.se_ci95],
            "se_percentiles": {k: round(v, 4) for k, v in self.se_percentiles.items()},
            "sinr_mean_db": round(self.sinr_mean_db, 2),
            "rank_hist": {str(k): v for k, v in self.rank_hist.items()},
            "converged": self.converged,
            "relative_ci_width": round(self.relative_ci_width, 4),
            "method": self.method,
            "receiver": self.receiver,
            "power_constraint": self.power_constraint,
            "operating_point_mode": self.operating_point_mode,
        }


def monte_carlo(
    channels: np.ndarray,
    *,
    snr_db: float | None = None,
    noise_powers: np.ndarray | None = None,
    method: PrecoderMethod = "svd",
    receiver: ReceiverType = "mmse",
    power_constraint: bf.PowerConstraint | str = "ebf",
    max_rank: int = 4,
    channels_for_precoding: np.ndarray | None = None,
    interferers: np.ndarray | None = None,
    interference_covariances: Sequence[np.ndarray | None] | None = None,
    ci_target: float = 0.05,
    n_h: int | None = None,
    n_v: int | None = None,
    port_order: str | None = None,
    vertical_index_order: str | None = None,
    operating_point_mode: str | None = None,
) -> MonteCarloResult:
    """在一批样本上跑蒙特卡洛，返回均值、置信区间与收敛判断。

    ``converged`` 的判据：谱效均值的 95% 置信区间**相对宽度**小于 ``ci_target``
    （默认 5%）。不收敛说明样本量不够，此时两个方案的差异可能只是噪声——
    这是蒙特卡洛仿真最容易犯的错，所以这里默认就算。

    ``noise_powers`` 与 ``interference_covariances`` 可逐样本给（例如由数据集
    几何 SINR/SIR 标定），否则用统一的合成预波束 ``snr_db``。
    """
    ch = np.asarray(channels)
    n = ch.shape[0]
    if interferers is not None and interference_covariances is not None:
        raise ValueError("interferers 与 interference_covariances 只能给一个")
    if noise_powers is not None and len(noise_powers) != n:
        raise ValueError(f"noise_powers 长度应为 {n}，收到 {len(noise_powers)}")
    if interference_covariances is not None and len(interference_covariances) != n:
        raise ValueError(
            f"interference_covariances 长度应为 {n}，"
            f"收到 {len(interference_covariances)}")
    se = np.zeros(n)
    sinr = np.zeros(n)
    ranks: dict[int, int] = {}

    for i in range(n):
        kw: dict[str, Any] = {
            "method": method, "receiver": receiver, "max_rank": max_rank,
            "n_h": n_h, "n_v": n_v, "port_order": port_order,
            "vertical_index_order": vertical_index_order,
            "power_constraint": power_constraint}
        if noise_powers is not None:
            kw["noise_power"] = float(noise_powers[i])
        else:
            kw["snr_db"] = snr_db
        if channels_for_precoding is not None:
            kw["h_for_precoding"] = channels_for_precoding[i]
        if interferers is not None:
            kw["h_interferers"] = interferers[i]
        if interference_covariances is not None and interference_covariances[i] is not None:
            kw["interference_cov"] = interference_covariances[i]
        r = link_performance(ch[i], **kw)
        se[i] = r.spectral_efficiency
        sinr[i] = float(np.mean(r.sinr_per_layer_db))
        ranks[r.rank] = ranks.get(r.rank, 0) + 1

    mean = float(se.mean())
    std = float(se.std(ddof=1)) if n > 1 else 0.0
    if n > 1:
        from scipy.stats import t as student_t  # noqa: PLC0415

        critical = float(student_t.ppf(0.975, n - 1))
        half = critical * std / np.sqrt(n)
    else:
        half = 0.0
    rel = (2 * half / max(abs(mean), _EPS)) if n > 1 else float("inf")

    return MonteCarloResult(
        n=n,
        se_mean=mean,
        se_std=std,
        se_ci95=(mean - half, mean + half),
        se_percentiles={
            "p5": float(np.percentile(se, 5)),
            "p50": float(np.percentile(se, 50)),
            "p95": float(np.percentile(se, 95)),
        },
        sinr_mean_db=float(sinr.mean()),
        rank_hist=dict(sorted(ranks.items())),
        converged=bool(rel < ci_target),
        relative_ci_width=float(rel),
        method=method,
        receiver=receiver,
        power_constraint=str(power_constraint).lower(),
        per_sample_se=se,
        operating_point_mode=(
            operating_point_mode
            or ("per_sample_impairment" if noise_powers is not None
                else "synthetic_prebeam_snr")
        ),
    )


def compare_precoders(
    channels: np.ndarray,
    *,
    methods: tuple[PrecoderMethod, ...] = ("svd", "svd_wideband", "type1", "dft"),
    snr_db: float | None = 20.0,
    noise_powers: np.ndarray | None = None,
    receiver: ReceiverType = "mmse",
    power_constraint: bf.PowerConstraint | str = "ebf",
    max_rank: int = 4,
    channels_for_precoding: np.ndarray | None = None,
    interferers: np.ndarray | None = None,
    interference_covariances: Sequence[np.ndarray | None] | None = None,
    n_h: int | None = None,
    n_v: int | None = None,
    port_order: str | None = None,
    vertical_index_order: str | None = None,
    operating_point_mode: str | None = None,
) -> dict[str, Any]:
    """同一批信道上横向对比多种预编码方案。

    这是"你的方法要跟什么比"最直接的答案：把你的方案和这几个标准方案
    放在同一批信道、同一接收机、同一信噪比下比，差异才归因得清楚。
    """
    out: dict[str, Any] = {}
    for m in methods:
        r = monte_carlo(
            channels, snr_db=snr_db, noise_powers=noise_powers,
            method=m, receiver=receiver, max_rank=max_rank,
            power_constraint=power_constraint,
            channels_for_precoding=channels_for_precoding,
            interferers=interferers,
            interference_covariances=interference_covariances,
            n_h=n_h, n_v=n_v, port_order=port_order,
            vertical_index_order=vertical_index_order,
            operating_point_mode=operating_point_mode,
        )
        out[m] = r.as_dict()
    if "svd" in out:
        base = out["svd"]["se_mean"]
        for m in out:
            out[m]["vs_svd_pct"] = round(100.0 * out[m]["se_mean"] / max(base, _EPS), 1)
    return out

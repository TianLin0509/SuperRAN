"""链路性能：预编码 → 后处理 SINR → 谱效。

这是蒙特卡洛仿真最常用的评价链路。设计上刻意把三段拆开，
每段都能单独替换成你自己的算法，再接回来算最终指标：

    预编码 W  →  有效信道 H_eff = W^H · H  →  后处理 SINR  →  谱效

**为什么不直接给一个"谱效"数字。** 谱效取决于三个独立选择：用什么预编码、
用什么接收机、算不算干扰。同一批信道，SVD 理想预编码和 Type I 码本能差好几个
bit/s/Hz。把这三段摊开，对比才有意义——这也正是"你的方法要跟什么比"的落点。

预编码与有效信道复用 ChannelHub 的 ``phy_sim.precoding``（与它的干扰投影逻辑
保持一致）；SINR 与谱效按 MIMO 标准公式实现，口径在各函数文档里写明。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

_EPS = 1e-30

PrecoderMethod = Literal["svd", "svd_wideband", "dft", "type1", "mrt", "identity"]
ReceiverType = Literal["mmse", "mrc", "zf"]


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


def compute_precoder(
    h: np.ndarray,
    *,
    method: PrecoderMethod = "svd",
    max_rank: int = 4,
    rank_threshold: float = 0.1,
    n_h: int | None = None,
    n_v: int | None = None,
) -> Precoder:
    """计算预编码矩阵。

    参数
    ----
    h : ``[T, RB, BS_ant, UE_ant]`` 复数信道。传理想信道得到的是上界，
        传估计信道得到的才是实际系统能做到的——两者的差就是估计误差的代价。
    method :
        * ``svd``          逐 RB 做 SVD，取左奇异矢量。**理论最优**，是上界基线。
        * ``svd_wideband`` 全带共用一个 W（由宽带协方差特征分解得到）。
          反馈开销小得多，但有宽带损失，更贴近实际硬件。
        * ``dft``          DFT 波束码本，rank-1。对应真实网络里的波束赋形。
        * ``type1``        38.214 Type I 码本搜索，带量化损失。
        * ``mrt``          最大比传输，rank-1，对准最强方向。
        * ``identity``     不预编码，直接用天线端口。对照用。

    返回的 W 各列已单位化。
    """
    h = np.asarray(h)
    if h.ndim != 4:
        raise ValueError(f"h 应为 [T, RB, BS_ant, UE_ant]，收到 {h.shape}")

    if method in ("svd", "svd_wideband"):
        from .channelhub import _ensure_path

        _ensure_path()
        if method == "svd":
            from msg_embedding.phy_sim.precoding import compute_dl_precoding  # noqa: PLC0415

            r = compute_dl_precoding(h, max_rank=max_rank, rank_threshold=rank_threshold)
        else:
            from msg_embedding.phy_sim.precoding import (  # noqa: PLC0415
                compute_dl_precoding_wideband,
            )

            r = compute_dl_precoding_wideband(h, max_rank=max_rank, rank_threshold=rank_threshold)
        return Precoder(w=r.w_dl, rank=r.rank, method=method, singular_values=r.singular_values)

    rb = h.shape[1]
    bs = h.shape[2]
    h_avg = h.mean(axis=(0, 1))  # [BS, UE]

    if method == "mrt":
        u, s, _ = np.linalg.svd(h_avg, full_matrices=False)
        w1 = u[:, :1]
        w1 = w1 / max(np.linalg.norm(w1), _EPS)
        w = np.broadcast_to(w1[None], (rb, bs, 1)).astype(np.complex64).copy()
        return Precoder(w=w, rank=1, method=method)

    if method == "identity":
        rank = min(max_rank, bs)
        eye = np.eye(bs, rank, dtype=np.complex64)
        w = np.broadcast_to(eye[None], (rb, bs, rank)).copy()
        return Precoder(w=w, rank=rank, method=method)

    if method == "dft":
        from .measure import dft_beam_matrix

        beams = dft_beam_matrix(bs)
        metric = np.linalg.norm(beams.conj().T @ h_avg, axis=1)
        best = int(np.argmax(metric))
        w1 = beams[:, best : best + 1]
        w = np.broadcast_to(w1[None], (rb, bs, 1)).astype(np.complex64).copy()
        return Precoder(w=w, rank=1, method=method, indices=[best])

    if method == "type1":
        from .measure import pmi_type_i

        r = pmi_type_i(h, n_h=n_h, n_v=n_v, max_rank=max_rank)
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
    capacity_bound: float  # 同信道的容量上界，用于看离天花板多远
    sinr_per_rb_db: np.ndarray  # [RB, rank] 逐 RB 逐层
    noise_power: float
    interference_power: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "sinr_per_layer_db": [round(float(x), 2) for x in self.sinr_per_layer_db],
            "spectral_efficiency": round(float(self.spectral_efficiency), 4),
            "se_per_layer": [round(float(x), 4) for x in self.se_per_layer],
            "rank": self.rank,
            "method": self.method,
            "receiver": self.receiver,
            "capacity_bound": round(float(self.capacity_bound), 4),
            "efficiency_vs_bound": round(
                float(self.spectral_efficiency / max(self.capacity_bound, _EPS)), 3
            ),
            "noise_power": float(self.noise_power),
            "interference_power": float(self.interference_power),
        }


def _noise_from_snr(h: np.ndarray, snr_db: float) -> float:
    """由信道平均增益和目标信噪比反推噪声功率。

    约定：SNR = E[|h|²]·P_tx / N0，取 P_tx = 1。这样同一批信道在不同
    信噪比下的对比是干净的——只有噪声在变。
    """
    sig = float(np.mean(np.abs(np.asarray(h)) ** 2))
    return sig / max(10.0 ** (snr_db / 10.0), _EPS)


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

    * **MMSE**：``SINR_k = 1 / [ (I + (P/rank)·G^H R_n^{-1} G)^{-1} ]_kk − 1``
      这是线性 MMSE 接收机的标准结果，也是最常用的口径。
    * **ZF**：``SINR_k = (P/rank) / [ (G^H R_n^{-1} G)^{-1} ]_kk``
      迫零，完全消除层间干扰但放大噪声。
    * **MRC**：逐层最大比合并，**不消除层间干扰**，多层时会明显偏低——
      单层传输时才等价于最优。

    ``interference_cov`` 给定时（``[UE_ant, UE_ant]`` 或 ``[RB, UE_ant, UE_ant]``），
    干扰会计入 ``R_n``，得到的是真正的 SINR 而非 SNR。
    """
    h_eff = np.asarray(h_eff)
    if h_eff.ndim == 4:
        h_eff = h_eff.mean(axis=0)  # 时间平均 -> [RB, rank, UE]
    rb, rank, ue = h_eff.shape
    p_per_layer = 1.0 / max(rank, 1)

    out = np.zeros((rb, rank), dtype=np.float64)
    for f in range(rb):
        g = h_eff[f].conj().T  # [UE, rank]
        r_n = np.eye(ue, dtype=np.complex128) * noise_power
        if interference_cov is not None:
            ic = np.asarray(interference_cov)
            r_n = r_n + (ic[f] if ic.ndim == 3 else ic)

        r_inv = np.linalg.pinv(r_n)
        a = g.conj().T @ r_inv @ g  # [rank, rank]

        if receiver == "mmse":
            m = np.eye(rank, dtype=np.complex128) + p_per_layer * a
            m_inv = np.linalg.pinv(m)
            diag = np.real(np.diag(m_inv))
            out[f] = np.maximum(1.0 / np.maximum(diag, _EPS) - 1.0, 0.0)
        elif receiver == "zf":
            a_inv = np.linalg.pinv(a)
            out[f] = p_per_layer / np.maximum(np.real(np.diag(a_inv)), _EPS)
        elif receiver == "mrc":
            for k in range(rank):
                gk = g[:, k]
                sig = p_per_layer * float(np.real(gk.conj() @ r_inv @ gk)) ** 2
                # 层间干扰：其余层经同一 MRC 权后的泄漏
                leak = 0.0
                for j in range(rank):
                    if j == k:
                        continue
                    leak += p_per_layer * abs(complex(gk.conj() @ r_inv @ g[:, j])) ** 2
                nz = float(np.real(gk.conj() @ r_inv @ gk))
                out[f, k] = sig / max(leak + nz, _EPS)
        else:
            raise ValueError(f"未知接收机 {receiver!r}")
    return out


def capacity_upper_bound(h: np.ndarray, noise_power: float) -> float:
    """容量上界 ``mean_rb log2 det(I + H H^H / (rank·N0))``，bit/s/Hz。

    等功率分配、理想接收机、无干扰。任何实际方案都不该超过它——
    这条性质本身就是一个可用的自检。
    """
    h = np.asarray(h)
    h_avg = h.mean(axis=0)  # [RB, BS, UE]
    rb = h_avg.shape[0]
    caps = []
    for f in range(rb):
        hm = h_avg[f]  # [BS, UE]
        g = hm.conj().T  # [UE, BS]
        n_str = min(g.shape)
        m = np.eye(g.shape[0]) + (g @ g.conj().T) / max(n_str * noise_power, _EPS)
        sign, logdet = np.linalg.slogdet(m)
        if sign > 0:
            caps.append(float(logdet / np.log(2)))
    return float(np.mean(caps)) if caps else 0.0


def link_performance(
    h: np.ndarray,
    *,
    snr_db: float | None = None,
    noise_power: float | None = None,
    method: PrecoderMethod = "svd",
    receiver: ReceiverType = "mmse",
    max_rank: int = 4,
    h_for_precoding: np.ndarray | None = None,
    h_interferers: np.ndarray | None = None,
    n_h: int | None = None,
    n_v: int | None = None,
) -> LinkPerformance:
    """一站式：预编码 → 有效信道 → 逐层 SINR → 谱效。

    参数
    ----
    h : ``[T, RB, BS_ant, UE_ant]`` 用于**评估**的真实信道。
    h_for_precoding : 用于**计算预编码**的信道，默认与 h 相同。
        传估计信道即可评估"用有误差的 CSI 做预编码"的代价——
        这是 CSI 反馈类课题最核心的对比。
    snr_db / noise_power : 二选一。给 snr_db 时按信道平均增益反推噪声。
    h_interferers : ``[K-1, T, RB, BS_ant, UE_ant]``，给定则计入干扰，
        得到真正的 SINR。

    谱效口径：``SE = mean_rb Σ_layer log2(1 + SINR[rb, layer])``。
    """
    h = np.asarray(h)
    if noise_power is None:
        if snr_db is None:
            raise ValueError("snr_db 与 noise_power 至少给一个")
        noise_power = _noise_from_snr(h, snr_db)

    h_p = np.asarray(h_for_precoding) if h_for_precoding is not None else h
    prec = compute_precoder(h_p, method=method, max_rank=max_rank, n_h=n_h, n_v=n_v)
    h_eff = effective_channel(h, prec.w)

    intf_cov = None
    intf_power = 0.0
    if h_interferers is not None:
        hi = np.asarray(h_interferers)
        # 干扰在终端侧的空间协方差：对干扰小区、时间、发射天线求平均
        # [K-1, T, RB, BS, UE] -> [RB, UE, UE]
        rb = hi.shape[2]
        ue = hi.shape[4]
        cov = np.zeros((rb, ue, ue), dtype=np.complex128)
        for k in range(hi.shape[0]):
            hk = hi[k].mean(axis=0)  # [RB, BS, UE]
            for f in range(rb):
                g = hk[f]  # [BS, UE]
                cov[f] += g.conj().T @ g / max(g.shape[0], 1)
        intf_cov = cov
        intf_power = float(np.mean(np.real(np.trace(cov, axis1=1, axis2=2))) / max(ue, 1))

    sinr_lin = post_equalizer_sinr(
        h_eff, noise_power, receiver=receiver, interference_cov=intf_cov
    )
    se_rb = np.log2(1.0 + np.maximum(sinr_lin, 0.0))  # [RB, rank]
    se_per_layer = se_rb.mean(axis=0)

    return LinkPerformance(
        sinr_per_layer_db=10.0 * np.log10(np.maximum(sinr_lin.mean(axis=0), _EPS)),
        spectral_efficiency=float(se_per_layer.sum()),
        se_per_layer=se_per_layer,
        rank=prec.rank,
        method=prec.method,
        receiver=receiver,
        capacity_bound=capacity_upper_bound(h, noise_power),
        sinr_per_rb_db=10.0 * np.log10(np.maximum(sinr_lin, _EPS)),
        noise_power=float(noise_power),
        interference_power=intf_power,
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
    per_sample_se: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))

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
        }


def monte_carlo(
    channels: np.ndarray,
    *,
    snr_db: float | None = None,
    noise_powers: np.ndarray | None = None,
    method: PrecoderMethod = "svd",
    receiver: ReceiverType = "mmse",
    max_rank: int = 4,
    channels_for_precoding: np.ndarray | None = None,
    interferers: np.ndarray | None = None,
    ci_target: float = 0.05,
    n_h: int | None = None,
    n_v: int | None = None,
) -> MonteCarloResult:
    """在一批样本上跑蒙特卡洛，返回均值、置信区间与收敛判断。

    ``converged`` 的判据：谱效均值的 95% 置信区间**相对宽度**小于 ``ci_target``
    （默认 5%）。不收敛说明样本量不够，此时两个方案的差异可能只是噪声——
    这是蒙特卡洛仿真最容易犯的错，所以这里默认就算。

    ``noise_powers`` 可逐样本给（例如用各样本自身的 SINR 反推），
    否则用统一的 ``snr_db``。
    """
    ch = np.asarray(channels)
    n = ch.shape[0]
    se = np.zeros(n)
    sinr = np.zeros(n)
    ranks: dict[int, int] = {}

    for i in range(n):
        kw: dict[str, Any] = {"method": method, "receiver": receiver, "max_rank": max_rank,
                              "n_h": n_h, "n_v": n_v}
        if noise_powers is not None:
            kw["noise_power"] = float(noise_powers[i])
        else:
            kw["snr_db"] = snr_db
        if channels_for_precoding is not None:
            kw["h_for_precoding"] = channels_for_precoding[i]
        if interferers is not None:
            kw["h_interferers"] = interferers[i]
        r = link_performance(ch[i], **kw)
        se[i] = r.spectral_efficiency
        sinr[i] = float(np.mean(r.sinr_per_layer_db))
        ranks[r.rank] = ranks.get(r.rank, 0) + 1

    mean = float(se.mean())
    std = float(se.std(ddof=1)) if n > 1 else 0.0
    half = 1.96 * std / max(np.sqrt(n), 1.0)
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
        per_sample_se=se,
    )


def compare_precoders(
    channels: np.ndarray,
    *,
    methods: tuple[PrecoderMethod, ...] = ("svd", "svd_wideband", "type1", "dft"),
    snr_db: float = 20.0,
    receiver: ReceiverType = "mmse",
    max_rank: int = 4,
    channels_for_precoding: np.ndarray | None = None,
    n_h: int | None = None,
    n_v: int | None = None,
) -> dict[str, Any]:
    """同一批信道上横向对比多种预编码方案。

    这是"你的方法要跟什么比"最直接的答案：把你的方案和这几个标准方案
    放在同一批信道、同一接收机、同一信噪比下比，差异才归因得清楚。
    """
    out: dict[str, Any] = {}
    for m in methods:
        r = monte_carlo(
            channels, snr_db=snr_db, method=m, receiver=receiver, max_rank=max_rank,
            channels_for_precoding=channels_for_precoding, n_h=n_h, n_v=n_v,
        )
        out[m] = r.as_dict()
    if "svd" in out:
        base = out["svd"]["se_mean"]
        for m in out:
            out[m]["vs_svd_pct"] = round(100.0 * out[m]["se_mean"] / max(base, _EPS), 1)
    return out

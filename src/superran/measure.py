"""原始测量量计算。

铁律：这里所有输出都是**物理量**——不做峰值归一化、不做区间截断、不做
任何门控缩放。ChannelHub 的 data/bridge.py 会做这些（那是为 MAE token
服务的），本模块刻意绕开它。

单位一律标注在函数文档里，含糊的量不提供。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

_EPS = 1e-30


# ---------------------------------------------------------------------------
# 时延域
# ---------------------------------------------------------------------------


@dataclass
class PDPResult:
    """时延功率谱。与 bridge 的 pdp_crop 不同：不归一化、带真实时延轴。"""

    power: np.ndarray  # [n_taps] 线性功率（未归一化）
    delays_s: np.ndarray  # [n_taps] 对应时延，秒
    power_db: np.ndarray  # [n_taps] dB
    rms_delay_spread_s: float
    mean_delay_s: float
    delay_resolution_s: float
    unambiguous_period_s: float
    window: str
    window_variance_correction_s2: float
    power_conservation_ratio: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "power": self.power,
            "delays_s": self.delays_s,
            "power_db": self.power_db,
            "rms_delay_spread_s": self.rms_delay_spread_s,
            "mean_delay_s": self.mean_delay_s,
            "delay_resolution_s": self.delay_resolution_s,
            "unambiguous_period_s": self.unambiguous_period_s,
            "window": self.window,
            "window_variance_correction_s2": self.window_variance_correction_s2,
            "power_conservation_ratio": self.power_conservation_ratio,
        }


def _circular_delay_moments(
    power: np.ndarray,
    delays_s: np.ndarray,
    period_s: float,
) -> tuple[float, float]:
    """Return branch-stable circular mean and local variance in seconds.

    The IFFT delay axis is periodic.  A fixed ``[-T/2,T/2)`` branch maps a
    perfectly valid 2 us path to a negative delay when ``T=2.78 us``.  The
    weighted circular mean chooses the branch from the data itself; residuals
    are then wrapped to the nearest image around that mean.
    """
    p = np.asarray(power, dtype=np.float64)
    total = float(np.sum(p))
    if total <= _EPS:
        return 0.0, 0.0
    w = p / total
    phase = 2.0 * np.pi * np.asarray(delays_s, dtype=np.float64) / period_s
    resultant = np.sum(w * np.exp(1j * phase))
    if abs(resultant) <= 1e-12:
        reference = float(delays_s[int(np.argmax(w))])
    else:
        reference = float((np.angle(resultant) % (2.0 * np.pi)) * period_s / (2.0 * np.pi))
    delta = (delays_s - reference + period_s / 2.0) % period_s - period_s / 2.0
    mean = (reference + float(np.sum(w * delta))) % period_s
    residual = (delays_s - mean + period_s / 2.0) % period_s - period_s / 2.0
    variance = float(np.sum(w * residual ** 2))
    return mean, max(variance, 0.0)


def power_delay_profile(
    h: np.ndarray,
    *,
    subcarrier_spacing_hz: float = 30_000.0,
    per_antenna: bool = False,
) -> PDPResult:
    """从频域信道算时延功率谱。

    参数
    ----
    h : [T, RB, BS_ant, UE_ant] 复数频域信道
    subcarrier_spacing_hz : 子载波间隔，用于换算时延轴
    per_antenna : True 时保留 BS 天线维度（大规模阵列各天线 PDP 不同）

    返回的 power 是**线性功率、未归一化**，频域先施加能量归一 Hann 窗以抑制
    周期绕回泄漏；时延轴由 RB 带宽推出：
    每 RB 占 12 个子载波，故频域采样间隔 = 12 * SCS，时延分辨率 = 1/(RB * 12 * SCS)。
    RMS 矩使用数据驱动的圆周解绕，并扣除 Hann 仪器核自身的二阶矩；因此单径
    不会再被报告成约 5.9 ns 的假时延扩展。输出同时给出分辨率、无模糊周期、
    窗函数校正量与功率守恒比，便于审计可分辨边界。
    """
    h = np.asarray(h)
    if h.ndim != 4:
        raise ValueError(f"h 应为 [T, RB, BS, UE] 四维，收到 {h.shape}")
    n_rb = h.shape[1]

    # 有限带宽的矩形截断会产生很强的 Dirichlet 旁瓣；尤其是靠近 0 的路径，
    # 旁瓣会周期性绕到 IFFT 末端。若把末端直接当作接近 1/df 的正时延，
    # 一个 13 ns 单径也会被误报成数百 ns 的 RMS delay spread。
    # Hann 窗把该泄漏压下去；按均方值归一后不改变总能量口径。
    if n_rb >= 3:
        freq_window = np.hanning(n_rb).astype(np.float64)
        freq_window /= np.sqrt(np.mean(freq_window ** 2))
    else:
        freq_window = np.ones(n_rb, dtype=np.float64)
    h_windowed = h * freq_window[None, :, None, None]
    h_delay = np.fft.ifft(h_windowed, axis=1) * np.sqrt(n_rb)
    p = np.abs(h_delay) ** 2

    # A window changes total energy for a frequency-selective realization even
    # when its mean-square coefficient is one.  Restore every T/BS/UE snapshot
    # to the original frequency-domain energy, not merely the ensemble mean.
    target_energy = np.sum(np.abs(h) ** 2, axis=1)       # [T,BS,UE]
    windowed_energy = np.sum(p, axis=1)                 # [T,BS,UE]
    scale = np.ones_like(target_energy, dtype=np.float64)
    valid = windowed_energy > _EPS
    scale[valid] = target_energy[valid] / windowed_energy[valid]
    p = p * scale[:, None, :, :]

    if per_antenna:
        power = p.mean(axis=(0, 3))  # [RB, BS]
        flat = power.mean(axis=1)
    else:
        power = p.mean(axis=(0, 2, 3))  # [RB]
        flat = power
    power = power.real.astype(np.float64)
    flat = flat.real.astype(np.float64)

    df = 12.0 * subcarrier_spacing_hz  # 每 RB 的频域跨度
    delays_s = np.arange(n_rb, dtype=np.float64) / (n_rb * df)
    delay_period_s = 1.0 / df
    mean_tau, raw_variance = _circular_delay_moments(flat, delays_s, delay_period_s)

    # Deterministic measurement-kernel variance.  Convolution with the Hann
    # delay kernel adds this variance to an isolated or well-separated physical
    # PDP.  Subtracting it is the finite-bandwidth analogue of de-embedding the
    # instrument response; clamp at zero rather than manufacturing precision.
    kernel = np.abs(np.fft.ifft(freq_window) * np.sqrt(n_rb)) ** 2
    _, kernel_variance = _circular_delay_moments(kernel, delays_s, delay_period_s)
    corrected_variance = max(raw_variance - kernel_variance, 0.0)
    rms_ds = float(np.sqrt(corrected_variance))

    target_total = float(np.mean(target_energy))
    measured_total = float(np.sum(flat))
    power_ratio = (
        measured_total / target_total if target_total > _EPS
        else (1.0 if measured_total <= _EPS else float("inf"))
    )

    return PDPResult(
        power=power,
        delays_s=delays_s,
        power_db=10.0 * np.log10(np.maximum(power, _EPS)),
        rms_delay_spread_s=rms_ds,
        mean_delay_s=mean_tau,
        delay_resolution_s=1.0 / (n_rb * df),
        unambiguous_period_s=delay_period_s,
        window="hann_energy_normalized" if n_rb >= 3 else "rectangular",
        window_variance_correction_s2=kernel_variance,
        power_conservation_ratio=power_ratio,
    )


def coherence_bandwidth_hz(rms_delay_spread_s: float, correlation: float = 0.5) -> float:
    """相干带宽估计。correlation=0.5 时用 1/(5*DS)，0.9 时用 1/(50*DS)。"""
    if rms_delay_spread_s <= _EPS:
        return float("inf")
    factor = 5.0 if correlation <= 0.5 else 50.0
    return 1.0 / (factor * rms_delay_spread_s)


# ---------------------------------------------------------------------------
# 空间域
# ---------------------------------------------------------------------------


def spatial_covariance(h: np.ndarray) -> np.ndarray:
    """基站侧空间协方差 R_hh，[BS_ant, BS_ant] 复数。

    对 T / RB / UE_ant 全部快照求平均。与 bridge 的差别：这里返回**完整矩阵**，
    不做特征分解截断。
    """
    h = np.asarray(h)
    t, rb, bs, ue = h.shape
    # [BS, T*RB*UE]，每列是一个空间快照
    cols = np.transpose(h, (2, 0, 1, 3)).reshape(bs, -1)
    return (cols @ cols.conj().T) / cols.shape[1]


def eigen_spectrum(r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """R_hh 的**全部**特征值与特征向量，按特征值降序。

    返回 (eigenvalues[BS], eigenvectors[BS, BS])。bridge 只给前 4 个，这里给全部。
    """
    vals, vecs = np.linalg.eigh(r)
    order = np.argsort(vals)[::-1]
    return np.maximum(vals[order].real, 0.0), vecs[:, order]


def condition_number(h: np.ndarray) -> float:
    """宽带空间条件数。衡量空间复用难度。

    对接收侧 Gram 矩阵 ``E[H^H H]`` 求特征值；不能先对复信道做时频
    平均，否则相位旋转会把一个功率完全正常的信道抵消成零。
    """
    hh = np.asarray(h)
    gram = np.einsum("tfbu,tfbv->uv", hh.conj(), hh) / max(
        hh.shape[0] * hh.shape[1], 1)
    s = np.sqrt(np.maximum(np.linalg.eigvalsh(gram).real, 0.0))[::-1]
    s = s[s > _EPS]
    if s.size < 2:
        return float("inf")
    return float(s[0] / s[-1])


# ---------------------------------------------------------------------------
# 功率类
# ---------------------------------------------------------------------------


def channel_gain_db(h: np.ndarray, *, per_antenna: bool = True) -> np.ndarray:
    """信道增益 10*log10(mean|h|^2)，dB。

    **不做区间截断**（bridge 会 clip 到 [-160,-60] dBm，边缘场景会失真）。
    per_antenna=True 时返回 [BS_ant]，否则返回标量数组。
    """
    h = np.asarray(h)
    axes = (0, 1, 3) if per_antenna else (0, 1, 2, 3)
    p = (np.abs(h) ** 2).mean(axis=axes)
    return 10.0 * np.log10(np.maximum(np.atleast_1d(p), _EPS))


def absolute_rsrp_dbm(
    h: np.ndarray,
    *,
    tx_power_dbm: float,
    pathloss_db: float,
    per_antenna: bool = True,
) -> np.ndarray:
    """绝对 RSRP，dBm。

    需要发射功率与路损（两者都在 ChannelSample.meta 里）。若 h 已含路损，
    调用方应传 pathloss_db=0.0 避免重复计入。
    """
    return channel_gain_db(h, per_antenna=per_antenna) + tx_power_dbm - pathloss_db


def dft_beam_matrix(n_ant: int, spacing_lambda: float = 0.5) -> np.ndarray:
    """物理导向角均匀分布的 DFT 波束矩阵 [n_ant, n_ant]。

    与 ChannelHub 的 SSB 波束定义一致：角度在 [-pi/2, pi/2) 均匀取。
    """
    n = int(n_ant)
    idx = np.arange(n)
    theta = np.pi * (idx / n - 0.5)
    kd = 2 * np.pi * spacing_lambda * np.sin(theta)  # [n_beams]
    return np.exp(1j * np.outer(idx, kd)) / np.sqrt(n)


def beam_domain_rsrp_db(h: np.ndarray, spacing_lambda: float = 0.5) -> np.ndarray:
    """波束域 RSRP，[n_beams] dB。匹配滤波后各 DFT 波束的接收功率。"""
    hh = np.asarray(h)
    beams = dft_beam_matrix(hh.shape[2], spacing_lambda)
    # [T,RB,beam,UE]，先取功率再平均，避免跨时频的复相位相消。
    h_beam = np.einsum("bk,tfbu->tfku", beams.conj(), hh)
    p = (np.abs(h_beam) ** 2).mean(axis=(0, 1, 3))
    return 10.0 * np.log10(np.maximum(p, _EPS))


# ---------------------------------------------------------------------------
# SRS 侧特征（物理量版本）
# ---------------------------------------------------------------------------


@dataclass
class SRSFeatures:
    """SRS 侧空间特征。与 bridge 的 srs1..srs4 token 的差别：全部特征值、
    未做 RMS 归一化、未乘 SINR 门控。"""

    covariance: np.ndarray  # [BS, BS]
    eigenvalues: np.ndarray  # [BS] 全部，降序
    eigenvectors: np.ndarray  # [BS, BS]
    gain_db: np.ndarray  # [BS] 每天线信道增益
    beam_rsrp_db: np.ndarray  # [n_beams]
    dominant_rank: int  # 特征值 > 0.1*max 的个数

    def as_dict(self) -> dict[str, Any]:
        return {
            "covariance": self.covariance,
            "eigenvalues": self.eigenvalues,
            "eigenvectors": self.eigenvectors,
            "gain_db": self.gain_db,
            "beam_rsrp_db": self.beam_rsrp_db,
            "dominant_rank": self.dominant_rank,
        }


def srs_features(h: np.ndarray, spacing_lambda: float = 0.5) -> SRSFeatures:
    """从上行信道提取 SRS 侧空间特征（全物理量）。"""
    r = spatial_covariance(h)
    vals, vecs = eigen_spectrum(r)
    rank = int(np.sum(vals > 0.1 * max(vals[0], _EPS))) if vals.size else 0
    return SRSFeatures(
        covariance=r,
        eigenvalues=vals,
        eigenvectors=vecs,
        gain_db=channel_gain_db(h, per_antenna=True),
        beam_rsrp_db=beam_domain_rsrp_db(h, spacing_lambda),
        dominant_rank=max(rank, 1),
    )


# ---------------------------------------------------------------------------
# PMI —— Type-I-style 单面板列码本近似
# ---------------------------------------------------------------------------


def type_i_codebook(n1: int, o1: int, n2: int, o2: int, *, dual_pol: bool = True) -> np.ndarray:
    """38.214 Type-I 单面板结构中的过采样 DFT/双极化**列集合**。

    水平/垂直各自的过采样 DFT 矢量做 Kronecker 积，双极化再拼 4 个 QPSK 同相因子。
    数学与 ChannelHub bridge 的实现一致，便于交叉验证。它不是完整的多层 PMI
    矩阵集合；多层路径由 :func:`pmi_type_i` 增量选列，是明确的工程近似。
    """
    def _dft(n: int, o: int) -> np.ndarray:
        k = np.arange(n * o)
        idx = np.arange(n)
        return np.exp(-1j * 2 * np.pi * np.outer(idx, k) / (n * o)) / np.sqrt(n)

    dft_h = _dft(n1, o1)  # [n1, n1*o1]
    dft_v = _dft(n2, o2)  # [n2, n2*o2]

    n_spatial = n1 * n2
    n_beams = n1 * o1 * n2 * o2
    spatial = np.zeros((n_spatial, n_beams), dtype=np.complex128)
    for i1 in range(n1 * o1):
        for i2 in range(n2 * o2):
            vec = np.kron(dft_v[:, i2], dft_h[:, i1])
            nrm = np.linalg.norm(vec)
            spatial[:, i1 * (n2 * o2) + i2] = vec / nrm if nrm > 1e-10 else vec

    if not dual_pol:
        return spatial

    co_phases = np.exp(1j * np.pi * np.arange(4) / 2)
    cb = np.zeros((2 * n_spatial, n_beams * 4), dtype=np.complex128)
    for b in range(n_beams):
        v = spatial[:, b]
        for p, phi in enumerate(co_phases):
            cb[:, b * 4 + p] = np.concatenate([v, phi * v]) / np.sqrt(2)
    return cb


@dataclass
class PMIResult:
    """PMI 搜索结果。与 bridge 的 pmi1..pmi4 token 的差别：给的是**码本索引**
    和预编码矩阵本身，未经门控缩放。"""

    indices: list[int]  # 每层选中的码本列号
    precoder: np.ndarray  # [ports, rank] 预编码矩阵 W
    rank: int
    layer_gain_db: list[float]  # 每层匹配增益
    codebook_size: int
    layout: tuple[int, int]  # (n_h, n_v)

    def as_dict(self) -> dict[str, Any]:
        return {
            "indices": self.indices,
            "precoder": self.precoder,
            "rank": self.rank,
            "layer_gain_db": self.layer_gain_db,
            "codebook_size": self.codebook_size,
            "layout": list(self.layout),
        }


def _infer_layout(n_ports: int, n_h: int | None, n_v: int | None) -> tuple[int, int, bool]:
    """推断 (n_h, n_v, dual_pol)。64 口默认按 8H4V 双极化解读。"""
    if n_h and n_v:
        dual = n_ports == 2 * n_h * n_v
        return n_h, n_v, dual
    known = {64: (8, 4), 32: (8, 2), 16: (4, 2), 8: (2, 2), 4: (2, 1)}
    if n_ports in known:
        h, v = known[n_ports]
        return h, v, True
    return n_ports, 1, False


def pmi_type_i(
    h: np.ndarray,
    *,
    n_h: int | None = None,
    n_v: int | None = None,
    max_rank: int = 4,
    o1: int = 4,
    o2: int = 4,
    port_order: str | None = None,
) -> PMIResult:
    """在 Type-I-style 单面板列集合上做宽带 PMI 近似搜索。

    h : [T, RB, BS_ant, UE_ant]。按宽带发射协方差上的平均接收功率
    逐层贪心选波束，每选一层就投影掉该方向，避免层间重复。
    该贪心列组合不等同于完整枚举 38.214 多层 Type-I 码本，结果会显式标成近似。
    """
    h = np.asarray(h)
    if h.ndim != 4:
        raise ValueError(f"h 应为 [T,RB,BS,UE]，收到 {h.shape}")
    n_ports = h.shape[2]

    nh, nv, dual = _infer_layout(n_ports, n_h, n_v)
    o2_eff = o2 if nv > 1 else 1
    cb = type_i_codebook(nh, o1, nv, o2_eff, dual_pol=dual)

    if cb.shape[0] != n_ports:
        # 阵型与端口数对不上时退回单极化线阵，保证可用
        cb = type_i_codebook(n_ports, o1, 1, 1, dual_pol=False)
        nh, nv = n_ports, 1

    # Type-I columns use protocol p/v/h order. Generated channels retain the
    # EffectiveArray order declared in metadata (64T h/v/p or 256T p/h/v).
    # Re-index codebook rows once so all covariance math below stays in the
    # actual channel-port order and the returned W can be applied directly.
    if dual and port_order is not None:
        from msg_embedding.phy_sim.effective_array import PortIndex

        idx = PortIndex(n_h=nh, n_v=nv, n_p=2, port_order=port_order)
        perm = idx.type1_to_canonical()
        cb_input = np.empty_like(cb)
        cb_input[perm, :] = cb
        cb = cb_input

    # R_tx = E[H H^H]。逐层贪心：每层取平均接收功率最大的码本列，
    # 再把该方向从协方差中投影掉。相比 mean(H)，它对任意公共相位旋转不变。
    cols = np.transpose(h, (2, 0, 1, 3)).reshape(n_ports, -1)
    residual = cols @ cols.conj().T / max(cols.shape[1], 1)
    indices: list[int] = []
    gains: list[float] = []
    rank_cap = min(max_rank, n_ports, h.shape[3])

    for _ in range(rank_cap):
        metric = np.real(np.sum(cb.conj() * (residual @ cb), axis=0))
        if indices:
            metric[indices] = -np.inf
        best = int(np.argmax(metric))
        g = float(metric[best])
        if g <= _EPS:
            break
        indices.append(best)
        gains.append(10.0 * np.log10(max(g, _EPS)))
        w = cb[:, best : best + 1]
        proj = np.eye(n_ports, dtype=np.complex128) - w @ w.conj().T
        residual = proj @ residual @ proj.conj().T

    if not indices:  # 极端退化情形
        indices = [0]
        gains = [-np.inf]

    precoder = cb[:, indices]
    return PMIResult(
        indices=indices,
        precoder=precoder.astype(np.complex64),
        rank=len(indices),
        layer_gain_db=gains,
        codebook_size=cb.shape[1],
        layout=(nh, nv),
    )


# ---------------------------------------------------------------------------
# 容量与链路
# ---------------------------------------------------------------------------


def channel_capacity_bps_hz(h: np.ndarray, snr_db: float) -> float:
    """合成预波束 SNR 下的逐时频最优注水容量，bit/s/Hz。

    这是兼容旧调用的薄包装。显式 ``snr_db`` 表示受控的合成预数字波束 SNR。
    数据集逐样本工作点应使用 ``Dataset.capacity()``；它会按 first-party
    预波束几何 SINR 与 E[|H|²] 做同口径标定。
    """
    from .linklevel import _noise_from_snr, capacity_upper_bound

    return capacity_upper_bound(h, _noise_from_snr(h, float(snr_db)))


# ---------------------------------------------------------------------------
# 多径结构（从 CDL/TDL 剖面重建）
# ---------------------------------------------------------------------------


@dataclass
class PathStructure:
    """每条径/每簇的几何。TDL 模型没有角度，此时角度字段为 None。"""

    model: str
    delays_s: np.ndarray
    powers_linear: np.ndarray
    powers_db: np.ndarray
    aod_rad: np.ndarray | None
    aoa_rad: np.ndarray | None
    zod_rad: np.ndarray | None
    zoa_rad: np.ndarray | None
    k_factor_db: float | None
    is_los: bool
    num_paths: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "delays_s": self.delays_s,
            "powers_linear": self.powers_linear,
            "powers_db": self.powers_db,
            "aod_rad": self.aod_rad,
            "aoa_rad": self.aoa_rad,
            "zod_rad": self.zod_rad,
            "zoa_rad": self.zoa_rad,
            "k_factor_db": self.k_factor_db,
            "is_los": self.is_los,
            "num_paths": self.num_paths,
        }


def path_structure(model: str, tau_rms_s: float) -> PathStructure:
    """按信道模型名与实际时延扩展，重建每条径的时延/功率/角度。

    这是确定性重建：CDL/TDL 剖面是 38.901 的标准查表值，
    时延 = 归一化时延 * tau_rms。CDL 有每簇角度，TDL 没有。
    """
    from .channelhub import cdl_profile  # 延迟导入，避免循环依赖

    prof = cdl_profile(model)
    name = getattr(prof, "name", model)
    has_angles = all(hasattr(prof, a) for a in ("aod_rad", "aoa_rad", "zod_rad", "zoa_rad"))

    powers = np.asarray(prof.powers_normalized(), dtype=np.float64)
    return PathStructure(
        model=name,
        delays_s=np.asarray(prof.delays_seconds(tau_rms_s), dtype=np.float64),
        powers_linear=powers,
        powers_db=10.0 * np.log10(np.maximum(powers, _EPS)),
        aod_rad=np.asarray(prof.aod_rad()) if has_angles else None,
        aoa_rad=np.asarray(prof.aoa_rad()) if has_angles else None,
        zod_rad=np.asarray(prof.zod_rad()) if has_angles else None,
        zoa_rad=np.asarray(prof.zoa_rad()) if has_angles else None,
        k_factor_db=getattr(prof, "k_factor_dB", None),
        is_los=bool(getattr(prof, "is_los", False)),
        num_paths=int(len(powers)),
    )


# ---------------------------------------------------------------------------
# 测量量注册表 —— 取货时按名字点单
# ---------------------------------------------------------------------------

MEASUREMENT_CATALOG: dict[str, str] = {
    "linkperf": "链路性能：预编码 → 逐层 SINR → 谱效，含容量上界与多方案对比",
    "validate": "可信度体检：对标 38.901、物理定律自检、蒙特卡洛收敛判断",
    "channel": "频域信道矩阵 [T, RB, BS_ant, UE_ant]，理想与估计两版",
    "pdp": "时延功率谱：未归一化功率 + 真实时延轴 + RMS 时延扩展",
    "paths": "每条径/簇的时延、功率、角度（CDL 才有角度）",
    "srs": "SRS 侧空间特征：完整协方差、全部特征值、每天线增益、波束域 RSRP",
    "pmi": "Type-I-style 单面板列码本近似：列索引 + 预编码矩阵 + 秩",
    "rsrp": "每天线信道增益与波束域 RSRP（不截断）",
    "sinr": "信噪比 / 信干比 / 信干噪比等链路标量",
    "capacity": "MIMO 容量与条件数",
    "geometry": "路损、阴影、3D 距离、视距判定、多普勒、UE 位置",
    "topology": "站点与扇区几何、小区参数",
}

# 自然语言关键词 → 测量量名。取货时用户说人话，这里做映射。
_ALIASES: dict[str, str] = {
    "信道": "channel", "channel": "channel", "h": "channel", "信道矩阵": "channel",
    "pdp": "pdp", "时延功率谱": "pdp", "功率时延谱": "pdp", "多径": "pdp",
    "时延": "pdp", "delay": "pdp",
    "paths": "paths", "径": "paths", "角度": "paths", "aoa": "paths", "aod": "paths",
    "簇": "paths", "cluster": "paths",
    "srs": "srs", "协方差": "srs", "covariance": "srs", "特征值": "srs",
    "pmi": "pmi", "码本": "pmi", "codebook": "pmi", "预编码": "pmi", "precoder": "pmi",
    # 注意：别用"功率"这种过泛的词——"时延功率谱"会被误命中
    "rsrp": "rsrp", "接收功率": "rsrp", "信号功率": "rsrp", "增益": "rsrp",
    "sinr": "sinr", "snr": "sinr", "信噪比": "sinr", "信干噪比": "sinr", "sir": "sinr",
    "capacity": "capacity", "容量": "capacity", "条件数": "capacity",
    "linkperf": "linkperf", "谱效": "linkperf", "频谱效率": "linkperf",
    "链路性能": "linkperf", "spectral": "linkperf", "预编码增益": "linkperf",
    "蒙特卡洛": "linkperf", "monte": "linkperf",
    "validate": "validate", "验证": "validate", "可信": "validate",
    "体检": "validate", "标定": "validate", "校验": "validate",
    "geometry": "geometry", "几何": "geometry", "路损": "geometry",
    "pathloss": "geometry", "位置": "geometry", "距离": "geometry",
    "topology": "topology", "拓扑": "topology", "站点": "topology",
}


def resolve_measurements(want: str | list[str] | None) -> list[str]:
    """把自然语言或列表解析成测量量名清单。解析不出来时默认只给信道。"""
    if want is None:
        return ["channel"]
    if isinstance(want, list):
        raw = [str(w).strip().lower() for w in want]
    else:
        text = str(want).lower()
        raw = [t for t in text.replace("，", ",").replace("、", ",").replace("+", ",").split(",")]
        raw = [t.strip() for t in raw if t.strip()]

    out: list[str] = []

    def _add(name: str) -> None:
        if name not in out:
            out.append(name)

    for token in raw:
        exact = _ALIASES.get(token)
        if exact:
            _add(exact)
            continue
        # 一句话里可能同时点了好几样（"我还想看 PMI 和 SRS RSRP"），
        # 所以要收集全部命中，不能匹配到第一个就停。
        # 只用长度 >= 2 的别名做子串匹配，避免 "h" 这类单字符误命中。
        for alias, target in _ALIASES.items():
            if len(alias) >= 2 and alias in token:
                _add(target)

    if not out:
        out = ["channel"]
    if "channel" not in out:
        out.insert(0, "channel")  # 信道永远给，它是其他量的原料
    return out

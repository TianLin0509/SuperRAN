"""First-party radio primitives and statistical channel source.

This module is deliberately self-contained.  It implements the narrow physical
waist that SuperRAN used to obtain through ``msg_embedding``: standard carrier
tables, reference sequences, topology, effective arrays, frequency-domain
LMMSE interpolation, and a deterministic 38.901-style statistical source.

The implementation is owned by SuperRAN.  No external source tree is searched,
added to ``sys.path``, or imported at runtime.  Optional ray-tracing engines are
kept outside this module and are discovered as ordinary third-party packages.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, ClassVar

import numpy as np

_C = 299_792_458.0
_EPS = 1e-30


# ---------------------------------------------------------------------------
# 38.104 carrier table and local TDD catalogue
# ---------------------------------------------------------------------------

NR_RB_TABLE: dict[str, dict[int, dict[int, int]]] = {
    "FR1": {
        15: {5: 25, 10: 52, 15: 79, 20: 106, 25: 133, 30: 160, 40: 216, 50: 270},
        30: {
            5: 11, 10: 24, 15: 38, 20: 51, 25: 65, 30: 78, 40: 106,
            50: 133, 60: 162, 70: 189, 80: 217, 90: 245, 100: 273,
        },
        60: {
            10: 11, 15: 18, 20: 24, 25: 31, 30: 38, 40: 51,
            50: 65, 60: 79, 70: 93, 80: 107, 90: 121, 100: 135,
        },
    },
    "FR2": {
        60: {50: 66, 100: 132, 200: 264},
        120: {50: 32, 100: 66, 200: 132, 400: 264},
    },
}


def nr_rb_lookup(
    bandwidth_hz: float,
    scs_hz: float,
    *,
    frequency_range: str = "FR1",
) -> int:
    """Return the standardized NR resource-block count.

    Only literal table entries are accepted.  A synthetic carrier must provide
    ``num_rb`` explicitly rather than receiving an approximate inverse.
    """
    fr = str(frequency_range).upper()
    bw_mhz = int(round(float(bandwidth_hz) / 1e6))
    scs_khz = int(round(float(scs_hz) / 1e3))
    try:
        return NR_RB_TABLE[fr][scs_khz][bw_mhz]
    except KeyError as exc:
        raise ValueError(
            f"unsupported NR carrier: {bw_mhz} MHz @ {scs_khz} kHz in {fr}"
        ) from exc


def nr_valid_scs(*, frequency_range: str = "FR1") -> list[int]:
    fr = str(frequency_range).upper()
    if fr not in NR_RB_TABLE:
        raise ValueError(f"frequency_range must be FR1 or FR2, got {frequency_range!r}")
    return sorted(NR_RB_TABLE[fr])


def nr_valid_bandwidths(scs_khz: int, *, frequency_range: str = "FR1") -> list[int]:
    fr = str(frequency_range).upper()
    try:
        return sorted(NR_RB_TABLE[fr][int(scs_khz)])
    except KeyError as exc:
        raise ValueError(f"unsupported SCS {scs_khz} kHz in {fr}") from exc


@dataclass(frozen=True)
class SpecialSlot:
    dl_symbols: int
    gp_symbols: int
    ul_symbols: int


@dataclass(frozen=True)
class TddPattern:
    name: str
    slots: str
    periodicity_ms: float
    special: SpecialSlot

    @property
    def period_slots(self) -> int:
        return len(self.slots)

    @property
    def num_dl(self) -> int:
        return self.slots.count("D")

    @property
    def num_ul(self) -> int:
        return self.slots.count("U")

    @property
    def num_special(self) -> int:
        return self.slots.count("S")


_TDD_PATTERNS: dict[str, TddPattern] = {
    "DDDSU": TddPattern("DDDSU", "DDDSU", 5.0, SpecialSlot(10, 2, 2)),
    "DDSUU": TddPattern("DDSUU", "DDSUU", 5.0, SpecialSlot(10, 2, 2)),
    "DDDDDDDSUU": TddPattern("DDDDDDDSUU", "DDDDDDDSUU", 10.0, SpecialSlot(6, 4, 4)),
    "DDDSUDDSUU": TddPattern("DDDSUDDSUU", "DDDSUDDSUU", 10.0, SpecialSlot(10, 2, 2)),
    "DSUUD": TddPattern("DSUUD", "DSUUD", 5.0, SpecialSlot(6, 4, 4)),
    "DDDDDDDDD_UL": TddPattern("DDDDDDDDD_UL", "DDDDD", 5.0, SpecialSlot(10, 2, 2)),
    "UUUUUUUUU_DL": TddPattern("UUUUUUUUU_DL", "UUUUU", 5.0, SpecialSlot(10, 2, 2)),
}


def get_tdd_pattern(name: str) -> TddPattern:
    try:
        return _TDD_PATTERNS[str(name)]
    except KeyError as exc:
        raise ValueError(f"unknown TDD pattern {name!r}; available={sorted(_TDD_PATTERNS)}") from exc


def list_tdd_patterns() -> list[str]:
    return list(_TDD_PATTERNS)


# ---------------------------------------------------------------------------
# 38.211 reference sequences
# ---------------------------------------------------------------------------


def zadoff_chu(root: int, length: int) -> np.ndarray:
    """Generate a unit-modulus Zadoff-Chu sequence."""
    n_zc = int(length)
    q = int(root)
    if n_zc < 1:
        raise ValueError("length must be positive")
    if math.gcd(q, n_zc) != 1:
        raise ValueError(f"root={q} must be coprime with length={n_zc}")
    n = np.arange(n_zc, dtype=np.float64)
    if n_zc % 2:
        phase = -np.pi * q * n * (n + 1.0) / n_zc
    else:
        phase = -np.pi * q * n * n / n_zc
    return np.exp(1j * phase).astype(np.complex128)


def _largest_prime_below(n: int) -> int:
    for candidate in range(max(int(n) - 1, 2), 1, -1):
        if all(candidate % d for d in range(2, int(math.sqrt(candidate)) + 1)):
            return candidate
    return 2


def srs_base_sequence(u: int, v: int, length: int) -> np.ndarray:
    """Low-PAPR SRS base sequence for the requested allocation length.

    For long allocations the construction follows the standard ZC extension:
    select the largest prime below ``M_sc`` and periodically extend it.  Short
    allocations use a deterministic constant-amplitude phase sequence.
    """
    m_sc = int(length)
    if m_sc < 1:
        raise ValueError("length must be positive")
    if m_sc >= 36:
        n_zc = _largest_prime_below(m_sc)
        q_bar = n_zc * (int(u) + 1) / 31.0
        q = int(math.floor(q_bar + 0.5))
        if int(v) & 1:
            q = int(math.floor(2.0 * q_bar)) - q
        q = max(q % n_zc, 1)
        while math.gcd(q, n_zc) != 1:
            q = (q + 1) % n_zc or 1
        base = zadoff_chu(q, n_zc)
        return base[np.arange(m_sc) % n_zc].astype(np.complex64)
    n = np.arange(m_sc, dtype=np.float64)
    phase = 2.0 * np.pi * ((int(u) % 30) + 1) * n * (n + 1.0) / (2.0 * m_sc)
    phase += np.pi * (int(v) & 1) * n
    return np.exp(1j * phase).astype(np.complex64)


def pseudo_random(c_init: int, length: int) -> np.ndarray:
    """38.211 Gold sequence with ``N_c=1600``."""
    size = int(length)
    if size < 0:
        raise ValueError("length must be non-negative")
    nc = 1600
    total = nc + size + 31
    x1 = np.zeros(total, dtype=np.uint8)
    x2 = np.zeros(total, dtype=np.uint8)
    x1[0] = 1
    init = int(c_init) & ((1 << 31) - 1)
    x2[:31] = [(init >> i) & 1 for i in range(31)]
    for n in range(total - 31):
        x1[n + 31] = x1[n + 3] ^ x1[n]
        x2[n + 31] = x2[n + 3] ^ x2[n + 2] ^ x2[n + 1] ^ x2[n]
    return (x1[nc:nc + size] ^ x2[nc:nc + size]).astype(np.uint8)


def pss(n_id_2: int) -> np.ndarray:
    x = np.zeros(127, dtype=np.uint8)
    x[:7] = (0, 1, 1, 0, 1, 1, 1)
    for n in range(120):
        x[n + 7] = x[n + 4] ^ x[n]
    shift = 43 * (int(n_id_2) % 3)
    return (1.0 - 2.0 * x[(np.arange(127) + shift) % 127]).astype(np.float32)


def sss(pci: int) -> np.ndarray:
    """38.211 SSS binary m-sequence construction."""
    n_id_1, n_id_2 = divmod(int(pci), 3)
    x0 = np.zeros(127, dtype=np.uint8)
    x1 = np.zeros(127, dtype=np.uint8)
    x0[0] = x1[0] = 1
    for n in range(120):
        x0[n + 7] = x0[n + 4] ^ x0[n]
        x1[n + 7] = x1[n + 1] ^ x1[n]
    m0 = 15 * (n_id_1 // 112) + 5 * n_id_2
    m1 = n_id_1 % 112
    n = np.arange(127)
    return ((1.0 - 2.0 * x0[(n + m0) % 127]) *
            (1.0 - 2.0 * x1[(n + m1) % 127])).astype(np.float32)


def pbch_dmrs(pci: int, ssb_index: int = 0) -> np.ndarray:
    c_init = (1 << 11) * (int(ssb_index) + 1) * (int(pci) // 4 + 1)
    c_init += (1 << 6) * (int(ssb_index) + 1) + int(pci) % 4
    bits = pseudo_random(c_init, 288).reshape(-1, 2)
    return ((1 - 2 * bits[:, 0]) + 1j * (1 - 2 * bits[:, 1])).astype(np.complex64) / np.sqrt(2)


@dataclass(frozen=True)
class SrsBandwidthRow:
    c_srs: int
    m_srs: tuple[int, int, int, int]
    n: tuple[int, int, int, int]


def _srs_rows() -> tuple[SrsBandwidthRow, ...]:
    # TS 38.211 Table 6.4.1.4.3-1, columns C_SRS/m_SRS,b/N_b.
    raw = (
        ((4, 4, 4, 4), (1, 1, 1, 1)), ((8, 4, 4, 4), (1, 2, 1, 1)),
        ((12, 4, 4, 4), (1, 3, 1, 1)), ((16, 4, 4, 4), (1, 4, 1, 1)),
        ((16, 8, 4, 4), (1, 2, 2, 1)), ((20, 4, 4, 4), (1, 5, 1, 1)),
        ((24, 4, 4, 4), (1, 6, 1, 1)), ((24, 12, 4, 4), (1, 2, 3, 1)),
        ((28, 4, 4, 4), (1, 7, 1, 1)), ((32, 16, 8, 4), (1, 2, 2, 2)),
        ((36, 12, 4, 4), (1, 3, 3, 1)), ((40, 20, 4, 4), (1, 2, 5, 1)),
        ((48, 16, 8, 4), (1, 3, 2, 2)), ((48, 24, 12, 4), (1, 2, 2, 3)),
        ((52, 4, 4, 4), (1, 13, 1, 1)), ((56, 28, 4, 4), (1, 2, 7, 1)),
        ((60, 20, 4, 4), (1, 3, 5, 1)), ((64, 32, 16, 4), (1, 2, 2, 4)),
        ((72, 24, 12, 4), (1, 3, 2, 3)), ((72, 36, 12, 4), (1, 2, 3, 3)),
        ((76, 4, 4, 4), (1, 19, 1, 1)), ((80, 40, 20, 4), (1, 2, 2, 5)),
        ((88, 44, 4, 4), (1, 2, 11, 1)), ((96, 32, 16, 4), (1, 3, 2, 4)),
        ((96, 48, 24, 4), (1, 2, 2, 6)), ((104, 52, 4, 4), (1, 2, 13, 1)),
        ((112, 56, 28, 4), (1, 2, 2, 7)), ((120, 60, 20, 4), (1, 2, 3, 5)),
        ((120, 40, 8, 4), (1, 3, 5, 2)), ((120, 24, 12, 4), (1, 5, 2, 3)),
        ((128, 64, 32, 4), (1, 2, 2, 8)), ((128, 64, 16, 4), (1, 2, 4, 4)),
        ((128, 16, 8, 4), (1, 8, 2, 2)), ((132, 44, 4, 4), (1, 3, 11, 1)),
        ((136, 68, 4, 4), (1, 2, 17, 1)), ((144, 72, 36, 4), (1, 2, 2, 9)),
        ((144, 48, 24, 12), (1, 3, 2, 2)), ((144, 48, 16, 4), (1, 3, 3, 4)),
        ((144, 16, 8, 4), (1, 9, 2, 2)), ((152, 76, 4, 4), (1, 2, 19, 1)),
        ((160, 80, 40, 4), (1, 2, 2, 10)), ((160, 80, 20, 4), (1, 2, 4, 5)),
        ((160, 32, 16, 4), (1, 5, 2, 4)), ((168, 84, 28, 4), (1, 2, 3, 7)),
        ((176, 88, 44, 4), (1, 2, 2, 11)), ((184, 92, 4, 4), (1, 2, 23, 1)),
        ((192, 96, 48, 4), (1, 2, 2, 12)), ((192, 96, 24, 4), (1, 2, 4, 6)),
        ((192, 64, 16, 4), (1, 3, 4, 4)), ((192, 24, 8, 4), (1, 8, 3, 2)),
        ((208, 104, 52, 4), (1, 2, 2, 13)), ((216, 108, 36, 4), (1, 2, 3, 9)),
        ((224, 112, 56, 4), (1, 2, 2, 14)), ((240, 120, 60, 4), (1, 2, 2, 15)),
        ((240, 80, 20, 4), (1, 3, 4, 5)), ((240, 48, 16, 8), (1, 5, 3, 2)),
        ((240, 24, 12, 4), (1, 10, 2, 3)), ((256, 128, 64, 4), (1, 2, 2, 16)),
        ((256, 128, 32, 4), (1, 2, 4, 8)), ((256, 16, 8, 4), (1, 16, 2, 2)),
        ((264, 132, 44, 4), (1, 2, 3, 11)), ((272, 136, 68, 4), (1, 2, 2, 17)),
        ((272, 68, 4, 4), (1, 4, 17, 1)), ((272, 16, 8, 4), (1, 17, 2, 2)),
    )
    return tuple(
        SrsBandwidthRow(index, tuple(m_values), tuple(n_values))
        for index, (m_values, n_values) in enumerate(raw)
    )


SRS_BW_TABLE = _srs_rows()


@dataclass(frozen=True)
class SRSResourceConfig:
    C_SRS: int
    B_SRS: int = 0
    K_TC: int = 2
    n_RRC: int = 0
    b_hop: int = 0
    n_SRS_ID: int = 0
    T_SRS: int = 1
    T_offset: int = 0
    N_ap: int = 1

    @property
    def hopping_enabled(self) -> bool:
        return int(self.b_hop) < int(self.B_SRS)


_COMPANY_HOP_ORDER = (0, 8, 16, 7, 15, 6, 14, 5, 13, 4, 12, 3, 11, 2, 10, 1, 9)


def _srs_row(c_srs: int) -> SrsBandwidthRow:
    if not 0 <= int(c_srs) < len(SRS_BW_TABLE):
        raise ValueError("C_SRS must be in 0..63")
    return SRS_BW_TABLE[int(c_srs)]


def srs_hopping_cycle_length(config: SRSResourceConfig) -> int:
    row = _srs_row(config.C_SRS)
    if not config.hopping_enabled:
        return 1
    return max(1, row.m_srs[0] // row.m_srs[int(config.B_SRS)])


def srs_rb_indices(
    config: SRSResourceConfig,
    slot: int,
    symbol: int,
    total_rb: int,
) -> np.ndarray:
    del symbol
    row = _srs_row(config.C_SRS)
    b = int(config.B_SRS)
    if not 0 <= b <= 3:
        raise ValueError("B_SRS must be in 0..3")
    width = int(row.m_srs[b])
    if width > int(total_rb):
        raise ValueError(f"SRS allocation width {width} exceeds carrier {total_rb}")
    cycle = srs_hopping_cycle_length(config)
    if cycle == 17 and width == 16 and int(total_rb) == 272:
        index = _COMPANY_HOP_ORDER[int(slot) % cycle]
    elif cycle > 1:
        stride = cycle // 2 + 1
        while math.gcd(stride, cycle) != 1:
            stride += 1
        index = (int(config.n_RRC) + int(slot) * stride) % cycle
    else:
        index = int(config.n_RRC) % max(int(total_rb) // width, 1)
    start = index * width
    return np.arange(start, start + width, dtype=np.int64)


def srs_sequence(
    *,
    n_SRS_ID: int,
    K_TC: int,
    n_cs: int,
    N_ap: int,
    Msc: int,
    slot: int,
    symbol: int,
    n_ap_index: int = 0,
    group_hopping: bool = False,
    slots_per_frame: int = 20,
) -> np.ndarray:
    del slots_per_frame
    u = (int(n_SRS_ID) + (int(slot) if group_hopping else 0)) % 30
    v = (int(slot) + int(symbol)) & 1
    base = np.exp(
        1j * np.angle(srs_base_sequence(u, v, int(Msc)).astype(np.complex128))
    )
    limits = {2: 8, 4: 12, 8: 6}
    if int(K_TC) not in limits:
        raise ValueError("K_TC must be 2, 4 or 8")
    alpha_index = (int(n_cs) + int(n_ap_index) * limits[int(K_TC)] // max(int(N_ap), 1))
    alpha = 2.0 * np.pi * (alpha_index % limits[int(K_TC)]) / limits[int(K_TC)]
    out = base * np.exp(1j * alpha * np.arange(int(Msc)))
    out /= np.maximum(np.abs(out), _EPS)
    return out.astype(np.complex128)


def _srs_port_sequences(
    num_rb: int,
    n_srs_id: int,
    n_ports: int,
    *,
    slot: int,
    symbol: int,
    K_TC: int = 2,
) -> np.ndarray:
    tones = max(int(num_rb) * 12 // int(K_TC), 1)
    return np.stack(
        [
            srs_sequence(
                n_SRS_ID=int(n_srs_id), K_TC=int(K_TC), n_cs=0,
                N_ap=int(n_ports), Msc=tones, slot=int(slot), symbol=int(symbol),
                n_ap_index=port,
            ) / math.sqrt(max(int(n_ports), 1))
            for port in range(int(n_ports))
        ],
        axis=0,
    )


HOP_EST_MODES = frozenset({"ls_hop_sequential", "ls_hop_concat"})
LMMSE_EST_MODES = frozenset({"ls_mmse", "ls_lmmse"})


def ls_pilot_observation(
    h_true: np.ndarray,
    pilot_rb: np.ndarray,
    sigma: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """LS 观测 ``H_p = H + n``，形状 ``[RB_p, symbol, port, rx]``。

    本仓的信道张量到 RB 为止、没有逐 RE 波形，所以这里是**相干解扩之后**的
    等效观测：把接收信号除以已知导频（Y/X）的结果就是真值加一个复高斯项，
    方差由测量 SNR 给定。序列长度带来的处理增益不在这里重复计入——它已经
    包含在调用方给的 ``snr_dB`` 口径里。这是工程近似，会写进数据集 meta。
    """
    obs = np.moveaxis(np.asarray(h_true)[:, pilot_rb], 1, 0).astype(np.complex128)
    if float(sigma) <= 0.0:
        return obs
    noise = rng.standard_normal(obs.shape) + 1j * rng.standard_normal(obs.shape)
    return obs + (float(sigma) / math.sqrt(2.0)) * noise


def frequency_interpolate(
    values: np.ndarray,
    positions: np.ndarray,
    n_rb: int,
    *,
    est_mode: str,
    tau_rms_s: float,
    delta_f_hz: float,
    snr_linear: float,
) -> np.ndarray:
    """把导频观测铺到全带宽，返回 ``[RB, ...]``。

    * ``ls_linear`` —— 导频之间线性插值，导频以外不做任何平滑。**这是没有
      先验的基线**：导频铺满全带时它就等于原始 LS，噪声一点没压。
    * ``ls_mmse`` / ``ls_lmmse`` —— 维纳插值 ``R_tp (R_pp + R_v)^-1 H_p``，
      ``R`` 由指数功率时延谱和 ``tau_rms`` 给出。即使导频铺满全带它也**压噪**：
      信道在频域相关而噪声白，这就是它比 LS 好的全部原因。
    * 跳频档同样走维纳插值：一次只探部分带宽，非导频 RB 必须靠先验补。
    """
    grid = np.arange(int(n_rb), dtype=np.float64)
    if est_mode in LMMSE_EST_MODES or est_mode in HOP_EST_MODES:
        return lmmse_frequency_interpolate(
            values, positions, grid, float(tau_rms_s), float(delta_f_hz),
            float(snr_linear), dtype="complex64")
    out = np.empty((int(n_rb), *np.asarray(values).shape[1:]), dtype=np.complex64)
    flat_in = np.asarray(values).reshape(np.asarray(values).shape[0], -1)
    flat_out = out.reshape(int(n_rb), -1)
    pos = np.asarray(positions, dtype=np.float64).reshape(-1)
    for col in range(flat_in.shape[1]):
        flat_out[:, col] = (
            np.interp(grid, pos, flat_in[:, col].real)
            + 1j * np.interp(grid, pos, flat_in[:, col].imag)
        ).astype(np.complex64)
    return out


def estimate_channel_with_interference(
    *,
    h_serving_true: np.ndarray,
    h_interferers: np.ndarray | None,
    pilots_serving: np.ndarray,
    interferer_cell_ids: Any,
    direction: str,
    snr_dB: float,
    rng: np.random.Generator,
    est_mode: str,
    valid_symbol_mask: np.ndarray,
    srs_rb_indices: np.ndarray,
    tau_rms_ns: float = 300.0,
    subcarrier_spacing: float = 30_000.0,
    prior_estimate: np.ndarray | None = None,
    pilot_history: Sequence[tuple[np.ndarray, np.ndarray]] = (),
    **kwargs: Any,
) -> SimpleNamespace:
    """First-party SRS/CSI-RS 观测器：LS 导频观测 + 频域插值。

    **这是主生成链唯一的估计入口**（``InternalSimSource.iter_samples`` 直接调它）。
    它消费真实的导频 RB 位置，绝不用 ``h_true`` 顶替缺失的观测。

    返回 ``h_est``（全带宽估计）、``h_pilot``（本次机会的 LS 观测）和
    ``pilot_rb``（本次探到的 RB）。后两个给跳频档用来跨机会拼带宽。

    ``prior_estimate`` 是上一次机会留下的全带估计；``ls_hop_sequential`` 只刷新
    本次探到的那些 RB，其余保留上一次的**估计值**（不是从旧真值快照复制）。
    ``pilot_history`` 是各跳最近一次的 ``(rb, 观测)``；``ls_hop_concat`` 把它们
    与本次观测并成一组非均匀导频，做一次联合维纳插值。

    ``h_interferers`` must be the UL cross-link from each colliding UE to this
    gNB, already weighted by that UE's residual pilot correlation.  It is not
    the dataset's ``h_interferers`` tensor, which is the downlink
    neighbour-gNB -> our-UE channel; substituting that models the wrong link,
    the wrong array and the wrong angles.  The calibrated end-to-end path is
    :func:`superran.srs_waveform.observe_srs_leg`, which synthesises real
    resource elements and despreads them instead of assuming a weight.
    """
    del pilots_serving, interferer_cell_ids, direction, kwargs
    truth = np.asarray(h_serving_true, dtype=np.complex64)
    pilots = np.asarray(srs_rb_indices, dtype=np.int64).reshape(-1)
    if truth.ndim != 4 or pilots.size < 1:
        raise ValueError("expected H[symbol,RB,port,rx] and non-empty pilot RBs")
    if np.any(pilots < 0) or np.any(pilots >= truth.shape[1]):
        raise ValueError("pilot RB outside channel grid")
    symbols = np.flatnonzero(np.asarray(valid_symbol_mask, dtype=bool))
    if symbols.size == 0:
        raise ValueError("valid_symbol_mask selects no observation")
    mode = str(est_mode)
    n_rb = int(truth.shape[1])
    n0 = 10.0 ** (-float(snr_dB) / 10.0)
    snr_linear = 1.0 / max(n0, _EPS)
    tau_s = float(tau_rms_ns) * 1e-9
    delta_f = 12.0 * float(subcarrier_spacing)

    # 只有被 valid_symbol_mask 选中的符号上真的有 SRS/CSI-RS；其余符号取
    # 时间上最近的那次观测，也就是"CSI 在两次机会之间保持不变"。
    observed = np.asarray(
        [int(symbols[np.argmin(np.abs(symbols - t))]) for t in range(truth.shape[0])]
    )
    contaminated = truth.astype(np.complex128)
    if h_interferers is not None:
        interference = np.asarray(h_interferers)
        if interference.size:
            # **碰撞的导频是相加，不是取平均。** 取平均会让 K 个干扰者加起来
            # 只剩一个的功率——污染越多反而越干净，方向完全反了。
            contaminated = contaminated + np.sum(interference, axis=0)
    pilot_obs = ls_pilot_observation(
        contaminated[observed], pilots, math.sqrt(max(n0, 0.0)), rng)

    if mode == "ls_hop_concat" and pilot_history:
        # **本次机会的观测必须赢。** 跳序是 17 个 RBG 的置换，第 18 次机会又会
        # 回到第 1 次探过的那个 RBG；历史里那条陈旧观测和本次观测落在同一批 RB
        # 上。曾经的写法把本次观测拼在最后再用 np.unique(return_index=True) 去重
        # ——它保留的是**首次**出现，于是从第 18 次机会起，刚测到的子带永远被
        # 17 次机会以前的旧值挤掉（实测：真值 9、LS 观测 9，输出仍是旧的 1）。
        # 现在显式地先把与本次 RB 重叠的历史条目剔掉，不依赖去重函数的取舍顺序。
        current = set(int(v) for v in pilots.tolist())
        positions = [pilots]
        values = [pilot_obs]
        for raw_rb, raw_val in pilot_history:
            rb = np.asarray(raw_rb, dtype=np.int64).reshape(-1)
            keep_mask = np.asarray([int(v) not in current for v in rb.tolist()])
            if not bool(np.any(keep_mask)):
                continue
            positions.append(rb[keep_mask])
            values.append(np.asarray(raw_val)[keep_mask])
        merged_pos = np.concatenate(positions)
        merged_val = np.concatenate(values, axis=0)
        if np.unique(merged_pos).size != merged_pos.size:
            raise ValueError(
                "跳频拼接出现重复导频 RB：各跳的 RB 集合必须互不重叠")
        order = np.argsort(merged_pos)
        full = frequency_interpolate(
            merged_val[order], merged_pos[order], n_rb, est_mode=mode,
            tau_rms_s=tau_s, delta_f_hz=delta_f, snr_linear=snr_linear)
    else:
        full = frequency_interpolate(
            pilot_obs, pilots, n_rb, est_mode=mode,
            tau_rms_s=tau_s, delta_f_hz=delta_f, snr_linear=snr_linear)

    estimate = np.moveaxis(full, 0, 1).astype(np.complex64)
    if mode == "ls_hop_sequential" and prior_estimate is not None:
        held = np.array(prior_estimate, dtype=np.complex64, copy=True)
        if held.shape != estimate.shape:
            raise ValueError("prior_estimate 与本次信道张量形状不符")
        held[:, pilots] = estimate[:, pilots]
        estimate = held
    return SimpleNamespace(h_est=estimate, h_pilot=pilot_obs, pilot_rb=pilots)


# ---------------------------------------------------------------------------
# Array, topology and codebook primitives
# ---------------------------------------------------------------------------

# Storage defaults.  Each tensor costs one extra full small-scale channel
# synthesis per interferer per sample, so both generation time and dataset
# size scale with the interferer count.
#
# The downlink interferer channel is ON by default (2026-09-08 maintainer
# ruling): a multi-cell dataset that silently ships without it looks like a
# clean single-cell experiment to every downstream consumer.  Cap the cost
# with ``max_per_ue_intf_cells`` (default 3), or switch it off explicitly.
#
# The UL cross-link stays opt-in: it only feeds SRS pilot contamination and
# nothing consumes it unless that experiment is being run.
_STORE_INTERFERER_CHANNELS_DEFAULT = True
_STORE_SRS_CROSS_LINK_DEFAULT = False
# Strongest-N neighbours kept per UE; matches the documented storage contract.
_MAX_PER_UE_INTF_CELLS_DEFAULT = 3

# How many SRS slots one cell can reserve before the allocator has to move to
# a longer global period.  Measured on the product profile (272 RB / 30 kHz /
# 17-hop / 4 cyclic shifts): 21 cells x 68 slots still fits the 10 ms period,
# the 69th slot forces 20 ms.  Reserving up to this many costs nothing in
# period and does not perturb the resources already handed to lower slots.
_SRS_SLOTS_AT_BASE_PERIOD = 68

PORT_LAYOUT_CONTRACT_VERSION = "pol_h_v-top_to_bottom-v1"


@dataclass(frozen=True)
class PortIndex:
    n_h: int
    n_v: int
    n_p: int = 2
    port_order: str = "pol_h_v"
    vertical_index_order: str = "top_to_bottom"

    def __post_init__(self) -> None:
        if min(self.n_h, self.n_v, self.n_p) < 1:
            raise ValueError("array dimensions must be positive")
        if self.port_order not in {"pol_h_v", "h_v_pol"}:
            raise ValueError("port_order must be pol_h_v or h_v_pol")
        if self.vertical_index_order not in {"top_to_bottom", "bottom_to_top"}:
            raise ValueError("unsupported vertical_index_order")

    @property
    def size(self) -> int:
        return self.n_h * self.n_v * self.n_p

    def flat(self, h: int, v_physical_top: int, p: int) -> int:
        logical_v = (
            int(v_physical_top)
            if self.vertical_index_order == "top_to_bottom"
            else self.n_v - 1 - int(v_physical_top)
        )
        if self.port_order == "pol_h_v":
            return int(p) * self.n_h * self.n_v + int(h) * self.n_v + logical_v
        return (int(h) * self.n_v + logical_v) * self.n_p + int(p)

    def type1_to_canonical(self) -> np.ndarray:
        out = np.empty(self.size, dtype=np.intp)
        for p in range(self.n_p):
            for v in range(self.n_v):
                for h in range(self.n_h):
                    source = p * self.n_v * self.n_h + v * self.n_h + h
                    # Type-I logical v follows the layout declaration; map it
                    # back to a physical top-row coordinate before flattening.
                    physical_v = v if self.vertical_index_order == "top_to_bottom" else self.n_v - 1 - v
                    out[source] = self.flat(h, physical_v, p)
        return out

    def permutation_from(self, other: PortIndex) -> np.ndarray:
        if (self.n_h, self.n_v, self.n_p) != (other.n_h, other.n_v, other.n_p):
            raise ValueError("port layouts must have identical dimensions")
        out = np.empty(self.size, dtype=np.intp)
        for p in range(self.n_p):
            for v in range(self.n_v):
                for h in range(self.n_h):
                    out[self.flat(h, v, p)] = other.flat(h, v, p)
        return out

    def permute_from_layout(self, values: np.ndarray, other: PortIndex, *, axis: int = 0) -> np.ndarray:
        arr = np.asarray(values)
        if arr.shape[int(axis)] != self.size:
            raise ValueError(f"axis {axis} has {arr.shape[int(axis)]} ports, expected {self.size}")
        return np.take(arr, self.permutation_from(other), axis=int(axis))


@dataclass
class EffectiveArray:
    rf_shape: tuple[int, int, int]
    elements_per_rf_port: int = 1
    horizontal_spacing_lambda: float = 0.5
    ae_vertical_spacing_lambda: float = 0.67
    fixed_downtilt_deg: float = 0.0
    port_order: str = "pol_h_v"
    vertical_index_order: str = "top_to_bottom"
    polarization_slant_angles_deg: tuple[float, ...] = (45.0, -45.0)
    element_pattern_source: str = "parametric_temporary"

    @property
    def physical_shape(self) -> tuple[int, int, int]:
        return (self.rf_shape[0], self.rf_shape[1] * self.elements_per_rf_port, self.rf_shape[2])

    @property
    def num_ports(self) -> int:
        return int(np.prod(self.rf_shape))

    def coupling_matrix(self) -> np.ndarray:
        n_h, n_v, n_p = self.rf_shape
        m = int(self.elements_per_rf_port)
        n_phys_v = n_v * m
        F = np.zeros((n_h * n_phys_v * n_p, n_h * n_v * n_p), dtype=np.complex128)
        rf = PortIndex(n_h, n_v, n_p, self.port_order, self.vertical_index_order)
        tilt = math.radians(float(self.fixed_downtilt_deg))
        q = np.arange(m, dtype=np.float64) - (m - 1.0) / 2.0
        weights = np.exp(1j * 2.0 * np.pi * self.ae_vertical_spacing_lambda * q * math.sin(tilt))
        weights /= math.sqrt(m)
        for p in range(n_p):
            for h in range(n_h):
                for v_top in range(n_v):
                    port = rf.flat(h, v_top, p)
                    for local in range(m):
                        v_phys = v_top * m + local
                        elem = p * n_h * n_phys_v + h * n_phys_v + v_phys
                        F[elem, port] = weights[local]
        return F

    def physical_positions_lambda(self) -> np.ndarray:
        n_h, n_phys_v, n_p = self.physical_shape
        rows = []
        z0 = (n_phys_v - 1.0) * self.ae_vertical_spacing_lambda / 2.0
        for _p in range(n_p):
            for h in range(n_h):
                for v in range(n_phys_v):
                    rows.append((0.0, h * self.horizontal_spacing_lambda,
                                 z0 - v * self.ae_vertical_spacing_lambda))
        return np.asarray(rows, dtype=np.float64)

    def effective_positions_lambda(self) -> np.ndarray:
        F = np.abs(self.coupling_matrix()) ** 2
        return F.T @ self.physical_positions_lambda()

    def rf_phase_centers_lambda(self) -> np.ndarray:
        return self.effective_positions_lambda()

    def coupling_hash(self) -> str:
        matrix = np.ascontiguousarray(self.coupling_matrix().view(np.float64))
        return hashlib.sha256(matrix.tobytes()).hexdigest()

    def metadata(self) -> dict[str, Any]:
        return {
            "rf_shape": list(self.rf_shape),
            "physical_shape": list(self.physical_shape),
            "elements_per_rf_port": int(self.elements_per_rf_port),
            "horizontal_spacing_lambda": float(self.horizontal_spacing_lambda),
            "ae_vertical_spacing_lambda": float(self.ae_vertical_spacing_lambda),
            "fixed_downtilt_deg": float(self.fixed_downtilt_deg),
            "port_order": self.port_order,
            "vertical_index_order": self.vertical_index_order,
            "polarization_slant_angles_deg": list(self.polarization_slant_angles_deg),
            "element_pattern_source": self.element_pattern_source,
        }

    def pattern_hash(self) -> str:
        payload = json.dumps(self.metadata(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def effective_tx_steering(
        self, azimuth_rad: float, elevation_rad: float, carrier_freq_hz: float
    ) -> np.ndarray:
        """Return the RF-port steering vector after the fixed feed network.

        Positions are expressed in wavelengths at the declared reference, so
        only the direction cosines enter for the current narrowband response;
        ``carrier_freq_hz`` remains explicit to prevent callers from confusing
        physical metres with normalized coordinates.
        """
        if not np.isfinite(float(carrier_freq_hz)) or float(carrier_freq_hz) <= 0.0:
            raise ValueError("carrier_freq_hz must be finite and positive")
        az = float(azimuth_rad)
        el = float(elevation_rad)
        direction = np.asarray(
            [math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)],
            dtype=np.float64,
        )
        physical = np.exp(2j * np.pi * (self.physical_positions_lambda() @ direction))
        return self.coupling_matrix().conj().T @ physical


def make_effective_array(cfg: dict[str, Any]) -> EffectiveArray:
    panel = tuple(int(x) for x in cfg.get("bs_panel", (1, 1, 1)))
    if len(panel) != 3:
        raise ValueError("bs_panel must contain [N_H,N_V,N_P]")
    ant = dict(cfg.get("bs_antenna") or {})
    sub = dict(ant.get("fixed_vertical_subarray") or {})
    pattern = dict(ant.get("element_pattern") or {})
    return EffectiveArray(
        rf_shape=panel,
        elements_per_rf_port=int(sub.get("elements_per_rf_port", 1)),
        horizontal_spacing_lambda=float(ant.get("horizontal_port_spacing_lambda", 0.5)),
        ae_vertical_spacing_lambda=float(sub.get("ae_vertical_spacing_lambda", 0.67)),
        fixed_downtilt_deg=float(sub.get("fixed_downtilt_deg", 0.0)),
        port_order=str(ant.get("port_order", "pol_h_v")),
        vertical_index_order=str(ant.get("vertical_index_order", "top_to_bottom")),
        polarization_slant_angles_deg=tuple(
            float(value)
            for value in pattern.get("polarization_slant_angles_deg", (45.0, -45.0))
        ),
        element_pattern_source=str(pattern.get("source", "parametric_temporary")),
    )


@dataclass(frozen=True)
class Cell:
    position: np.ndarray
    azimuth_deg: float
    site_id: int
    cell_id: int


def _site_coordinates(rings: int, isd_m: float) -> list[tuple[float, float]]:
    if rings <= 0:
        return [(0.0, 0.0)]
    # Axial hex coordinates, clockwise rings beginning east.  Site identity is
    # stable and matches the latest reference convention used by the audit.
    coords: list[tuple[int, int]] = [(0, 0)]
    directions = ((0, -1), (-1, 0), (-1, 1), (0, 1), (1, 0), (1, -1))
    for radius in range(1, int(rings) + 1):
        q, r = radius, 0
        for dq, dr in directions:
            for _ in range(radius):
                coords.append((q, r))
                q += dq
                r += dr
    out = []
    for q, r in coords:
        out.append((float(isd_m) * (q + 0.5 * r), float(isd_m) * (math.sqrt(3.0) / 2.0) * r))
    return out


def make_hex_grid(
    *, num_rings: int, isd_m: float, sectors: int, tx_height_m: float, scenario: str = "UMa_NLOS"
) -> list[Cell]:
    del scenario
    cells: list[Cell] = []
    for site_id, (x, y) in enumerate(_site_coordinates(int(num_rings), float(isd_m))):
        for sector in range(max(int(sectors), 1)):
            azimuth = 0.0 if sectors <= 1 else (120.0 * sector) % 360.0
            cells.append(Cell(np.asarray([x, y, float(tx_height_m)]), azimuth, site_id, len(cells)))
    return cells


def make_linear_grid(
    *, num_sites: int, isd_m: float, sectors: int, tx_height_m: float,
    scenario: str = "UMa_NLOS", track_offset_m: float = 80.0,
) -> list[Cell]:
    del scenario
    cells: list[Cell] = []
    origin = (max(int(num_sites), 1) - 1) / 2.0
    for site_id in range(max(int(num_sites), 1)):
        x = (site_id - origin) * float(isd_m)
        y = float(track_offset_m) * (-1.0 if site_id % 2 else 1.0)
        for sector in range(max(int(sectors), 1)):
            azimuth = 0.0 if sectors <= 1 else (120.0 * sector) % 360.0
            cells.append(Cell(np.asarray([x, y, float(tx_height_m)]), azimuth, site_id, len(cells)))
    return cells


def generate_dft_codebook(n_h: int, n_v: int, n_p: int = 2) -> np.ndarray:
    """Unit-norm dual-polarization separable DFT beams."""
    n_h, n_v, n_p = int(n_h), int(n_v), int(n_p)
    beams: list[np.ndarray] = []
    for p in range(n_p):
        for kv in range(n_v):
            for kh in range(n_h):
                row = np.zeros(n_h * n_v * n_p, dtype=np.complex128)
                for h in range(n_h):
                    for v in range(n_v):
                        idx = p * n_h * n_v + h * n_v + v
                        row[idx] = np.exp(2j * np.pi * (kh * h / n_h + kv * v / n_v))
                row /= np.linalg.norm(row)
                beams.append(row)
    return np.asarray(beams, dtype=np.complex64)


def select_csirs_beam(codebook: np.ndarray, h: np.ndarray) -> int:
    cb = np.asarray(codebook)
    channel = np.asarray(h)
    if channel.shape[-2] != cb.shape[1]:
        raise ValueError(f"channel BS axis {channel.shape[-2]} != codebook ports {cb.shape[1]}")
    # Power is formed before averaging.  Complex averaging would cancel two
    # equal-power RBs with opposite phases and can change the selected beam.
    projected = np.einsum("...bu,kb->...ku", channel, cb.conj(), optimize=True)
    power = np.mean(np.abs(projected) ** 2, axis=tuple(i for i in range(projected.ndim) if i != projected.ndim - 2))
    return int(np.argmax(power))


def project_interference_channels(
    h_interferers: np.ndarray,
    h_serving_of_interferers: list[np.ndarray],
    *,
    max_rank: int = 4,
    bs_panel: tuple[int, int, int] | None = None,
) -> tuple[np.ndarray, list[int]]:
    del bs_panel
    h_i = np.asarray(h_interferers)
    rank_width = max(1, min(int(max_rank), h_i.shape[-2], h_i.shape[-1]))
    out = np.zeros((*h_i.shape[:-2], rank_width, h_i.shape[-1]), dtype=h_i.dtype)
    ranks: list[int] = []
    for k in range(h_i.shape[0]):
        design = np.asarray(h_serving_of_interferers[k])
        wide = np.mean(design, axis=tuple(range(design.ndim - 2)))
        u, s, _vh = np.linalg.svd(wide, full_matrices=False)
        rank = max(1, min(int(max_rank), int(np.sum(s > max(s[0] * 1e-3, _EPS)))))
        w = u[:, :rank] / math.sqrt(rank)
        projected = np.einsum("...bu,br->...ru", h_i[k], w.conj(), optimize=True)
        out[k, ..., :rank, :] = projected
        ranks.append(rank)
    return out, ranks


# ---------------------------------------------------------------------------
# Channel profiles and frequency-domain LMMSE
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelProfile:
    name: str
    delays_norm: np.ndarray
    powers_dB: np.ndarray
    aod_deg: np.ndarray | None = None
    aoa_deg: np.ndarray | None = None
    zod_deg: np.ndarray | None = None
    zoa_deg: np.ndarray | None = None
    c_asd_deg: float = 0.0
    c_asa_deg: float = 0.0
    c_zsd_deg: float = 0.0
    c_zsa_deg: float = 0.0
    xpr_db: float = 0.0
    is_los: bool = False
    k_factor_dB: float | None = None

    def powers_normalized(self) -> np.ndarray:
        power = 10.0 ** (np.asarray(self.powers_dB, dtype=np.float64) / 10.0)
        return power / max(float(np.sum(power)), _EPS)

    def delays_seconds(self, tau_rms_s: float) -> np.ndarray:
        return np.asarray(self.delays_norm, dtype=np.float64) * float(tau_rms_s)


_TDL_BASE: dict[str, tuple[list[float], list[float], bool]] = {
    "TDL-A": (
        [0.0000, 0.3819, 0.4025, 0.5868, 0.4610, 0.5375, 0.6708, 0.5750,
         0.7618, 1.5375, 1.8978, 2.2242, 2.1718, 2.4942, 2.5119, 3.0582,
         4.0810, 4.4579, 4.5695, 4.7966, 5.0066, 5.3043, 9.6586],
        [-13.4, 0.0, -2.2, -4.0, -6.0, -8.2, -9.9, -10.5, -7.5, -15.9,
         -6.6, -16.7, -12.4, -15.2, -10.8, -11.3, -12.7, -16.2, -18.3,
         -18.9, -16.6, -19.9, -29.7],
        False,
    ),
    "TDL-B": (
        [0.0000, 0.1072, 0.2155, 0.2095, 0.2870, 0.2986, 0.3752, 0.5055,
         0.3681, 0.3697, 0.5700, 0.5283, 1.1021, 1.2756, 1.5474, 1.7842,
         2.0169, 2.8294, 3.0219, 3.6187, 4.1067, 4.2790, 4.7834],
        [0.0, -2.2, -4.0, -3.2, -9.8, -1.2, -3.4, -5.2, -7.6, -3.0,
         -8.9, -9.0, -4.8, -5.7, -7.5, -1.9, -7.6, -12.2, -9.8,
         -11.4, -14.9, -9.2, -11.3],
        False,
    ),
    "TDL-C": (
        [0.0000, 0.2099, 0.2219, 0.2329, 0.2176, 0.6366, 0.6448, 0.6560,
         0.6584, 0.7935, 0.8213, 0.9336, 1.2285, 1.3083, 2.1704, 2.7105,
         4.2589, 4.6003, 5.4902, 5.6077, 6.3065, 6.6374, 7.0427, 8.6523],
        [-4.4, -1.2, -3.5, -5.2, -2.5, 0.0, -2.2, -3.9, -7.4, -7.1,
         -10.7, -11.1, -5.1, -6.8, -8.7, -13.2, -13.9, -13.9, -15.8,
         -17.1, -16.0, -15.7, -21.6, -22.8],
        False,
    ),
    "TDL-D": (
        [0.0000, 0.0350, 0.6120, 1.3630, 1.4050, 1.8040, 2.5960,
         1.7750, 4.0420, 7.9370, 9.4240, 9.7080, 12.5250],
        [-0.2, -13.5, -18.8, -21.0, -22.8, -17.9, -20.1,
         -21.9, -22.9, -27.8, -23.6, -24.8, -30.0],
        True,
    ),
    "TDL-E": (
        [0.0000, 0.0317, 0.2014, 0.4986, 0.5302, 0.7236, 0.8090,
         0.9009, 1.2610, 1.7698, 2.5283, 3.7925, 5.0228, 5.8668],
        [-0.03, -22.03, -15.8, -18.1, -19.8, -22.9, -22.4,
         -18.6, -20.8, -22.6, -22.3, -25.6, -20.2, -29.8],
        True,
    ),
}

TDL_TABLES_SHA256 = "67a90df9bf97d9388c6596a72dbcc90ea347d4468839240175c95fdddc2db338"
SRS_BW_TABLE_SHA256 = "702191bc2ed0fcbf66b7e5f8b707aae6399389143a51ee699ef018eb948bd942"


def standard_table_digests() -> dict[str, str]:
    tdl_payload = {
        name: [values[0], values[1], values[2]]
        for name, values in _TDL_BASE.items()
    }
    srs_payload = [(row.c_srs, row.m_srs, row.n) for row in SRS_BW_TABLE]
    return {
        "tdl": hashlib.sha256(
            json.dumps(tdl_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "srs": hashlib.sha256(
            json.dumps(srs_payload, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def get_channel_profile(name: str) -> ChannelProfile:
    key = str(name).upper().replace("_", "-")
    if key.startswith("CDL-"):
        from .spec38901 import CDL_TABLES

        try:
            row = CDL_TABLES[key]
        except KeyError as exc:
            raise ValueError(f"unknown CDL profile {name!r}") from exc
        per = row["per_cluster"]
        return ChannelProfile(
            key,
            np.asarray(row["delays_norm"], dtype=np.float64),
            np.asarray(row["powers_dB"], dtype=np.float64),
            np.asarray(row["aod_deg"], dtype=np.float64),
            np.asarray(row["aoa_deg"], dtype=np.float64),
            np.asarray(row["zod_deg"], dtype=np.float64),
            np.asarray(row["zoa_deg"], dtype=np.float64),
            float(per["cASD"]), float(per["cASA"]), float(per["cZSD"]),
            float(per["cZSA"]), float(per["XPR"]), key in {"CDL-D", "CDL-E"},
            13.3 if key == "CDL-D" else (22.0 if key == "CDL-E" else None),
        )
    try:
        delays, powers, is_los = _TDL_BASE[key]
    except KeyError as exc:
        raise ValueError(f"unknown channel profile {name!r}") from exc
    return ChannelProfile(
        key,
        np.asarray(delays),
        np.asarray(powers),
        is_los=is_los,
        k_factor_dB=(13.3 if key == "TDL-D" else (22.0 if key == "TDL-E" else None)),
    )


def list_channel_models() -> dict[str, list[str]]:
    return {"cdl": [f"CDL-{x}" for x in "ABCDE"], "tdl": list(_TDL_BASE)}


def exponential_pdp_covariance(
    positions_a: np.ndarray | int,
    positions_b: np.ndarray | float,
    tau_rms_s: float,
    delta_f_hz: float | None = None,
) -> np.ndarray:
    if delta_f_hz is None and np.isscalar(positions_a) and np.isscalar(positions_b):
        # Compatibility form: (n_rb, tau_rms_s, rb_spacing_hz).
        count = int(positions_a)
        delta_f_hz = float(tau_rms_s)
        tau_rms_s = float(positions_b)
        positions_a = np.arange(count, dtype=np.float64)
        positions_b = np.arange(count, dtype=np.float64)
    if delta_f_hz is None:
        raise ValueError("delta_f_hz is required for explicit positions")
    a = np.asarray(positions_a, dtype=np.float64).reshape(-1, 1)
    b = np.asarray(positions_b, dtype=np.float64).reshape(1, -1)
    omega_tau = 2.0 * np.pi * float(delta_f_hz) * float(tau_rms_s) * (a - b)
    return 1.0 / (1.0 + 1j * omega_tau)


def lmmse_frequency_interpolate(
    h_pilot: np.ndarray,
    pilot_positions: np.ndarray,
    target_positions: np.ndarray,
    tau_rms_s: float,
    delta_f_hz: float,
    snr_linear: float,
    *,
    noise_covariance: np.ndarray | None = None,
    dtype: str = "complex64",
) -> np.ndarray:
    """Direct arbitrary pilot-to-target LMMSE interpolation.

    ``R_tp (R_pp + R_v)^-1 h_p`` is evaluated directly.  There is no compact
    pilot-grid interpolation stage, so punctured/non-uniform pilots retain the
    covariance implied by their actual positions.
    """
    hp = np.asarray(h_pilot)
    pp = np.asarray(pilot_positions, dtype=np.float64).reshape(-1)
    tp = np.asarray(target_positions, dtype=np.float64).reshape(-1)
    if hp.shape[0] != pp.size:
        raise ValueError("h_pilot first axis must match pilot_positions")
    if np.unique(pp).size != pp.size:
        raise ValueError("pilot_positions must be unique")
    r_pp = exponential_pdp_covariance(pp, pp, tau_rms_s, delta_f_hz)
    r_tp = exponential_pdp_covariance(tp, pp, tau_rms_s, delta_f_hz)
    if noise_covariance is None:
        rv = np.eye(pp.size, dtype=np.complex128) / max(float(snr_linear), _EPS)
    else:
        rv = np.asarray(noise_covariance, dtype=np.complex128)
        if rv.shape != r_pp.shape:
            raise ValueError(f"noise_covariance must be {r_pp.shape}, got {rv.shape}")
    flat = hp.reshape(pp.size, -1)
    weights = np.linalg.solve(r_pp + rv, flat)
    return (r_tp @ weights).reshape((tp.size, *hp.shape[1:])).astype(dtype)


def polarization_basis(slants_deg: tuple[float, ...] | list[float]) -> np.ndarray:
    """Return real Jones basis rows for the declared slant angles."""
    angles = np.radians(np.asarray(slants_deg, dtype=np.float64).reshape(-1))
    if angles.size < 1 or not np.isfinite(angles).all():
        raise ValueError("polarization slants must be finite and non-empty")
    return np.stack((np.cos(angles), np.sin(angles)), axis=1)


def polarization_jones_matrix(xpr_db: float, rng: np.random.Generator) -> np.ndarray:
    """Generate one 38.901-style per-ray 2x2 polarization coupling matrix.

    Co-polar terms have unit magnitude.  Cross-polar voltage is attenuated by
    ``sqrt(1/XPR)`` and every entry receives an independent random phase.
    """
    xpr_linear = 10.0 ** (float(xpr_db) / 10.0)
    cross = 1.0 / math.sqrt(max(xpr_linear, _EPS))
    phase = np.exp(1j * rng.uniform(-np.pi, np.pi, size=(2, 2)))
    return phase * np.asarray([[1.0, cross], [cross, 1.0]], dtype=np.float64)


def _panel_shape(n_ports: int, configured: Any) -> tuple[int, int, int]:
    try:
        panel = tuple(int(value) for value in configured)
    except (TypeError, ValueError):
        panel = ()
    if len(panel) == 3 and min(panel) > 0 and int(np.prod(panel)) == int(n_ports):
        return panel
    if int(n_ports) % 2 == 0:
        return (int(n_ports) // 2, 1, 2)
    return (int(n_ports), 1, 1)


def _spatial_panel_response(
    n_h: int,
    n_v: int,
    azimuth_rad: float,
    zenith_rad: float,
    *,
    horizontal_spacing: float = 0.5,
    vertical_spacing: float = 0.5,
) -> np.ndarray:
    """Separable unit-norm response for one polarization block.

    The vertical index follows the ``top_to_bottom`` port contract: ``v=0`` is
    the topmost row and the element height *decreases* with ``v``.  The phase
    reference sits at the geometric centre of the column, so the vertical term
    is ``(z0 - v * d_v) * sin(el)`` with ``z0 = (n_v - 1) * d_v / 2`` — the same
    coordinates :meth:`EffectiveArray.physical_positions_lambda` uses.  Both
    array-response implementations therefore describe one and the same array;
    an earlier ``+v * d_v`` here pointed the vertical steering the opposite way.
    """
    elevation = np.pi / 2.0 - float(zenith_rad)
    z0 = (int(n_v) - 1.0) * float(vertical_spacing) / 2.0
    values = []
    for h in range(int(n_h)):
        for v in range(int(n_v)):
            phase = 2.0 * np.pi * (
                float(horizontal_spacing) * h * math.cos(elevation) * math.sin(float(azimuth_rad))
                + (z0 - float(vertical_spacing) * v) * math.sin(elevation)
            )
            values.append(np.exp(1j * phase))
    result = np.asarray(values, dtype=np.complex128)
    return result / math.sqrt(max(result.size, 1))


def fixed_subarray_response(
    zenith_rad: float,
    *,
    elements_per_rf_port: int,
    ae_vertical_spacing_lambda: float,
    fixed_downtilt_deg: float,
) -> complex:
    """Complex pattern of one fixed vertical sub-array, seen from ``zenith_rad``.

    The 1-to-M feed network applies a frozen progressive phase that points the
    sub-array ``fixed_downtilt_deg`` below the horizon.  The scalar returned
    here is that fixed beam evaluated in the arrival/departure direction; the
    remaining RF-port array factor is separable and handled by
    :func:`_spatial_panel_response` with the port phase-centre spacing.

    Returns ``1`` for ``elements_per_rf_port <= 1`` (no feed network).
    """
    m = int(elements_per_rf_port)
    if m <= 1:
        return complex(1.0)
    spacing = float(ae_vertical_spacing_lambda)
    tilt = -math.radians(float(fixed_downtilt_deg))
    offsets = np.arange(m, dtype=np.float64) - (m - 1.0) / 2.0
    feed = np.exp(2j * np.pi * spacing * offsets * math.sin(tilt)) / math.sqrt(m)
    element_response = np.exp(
        2j * np.pi * spacing * offsets * math.sin(np.pi / 2.0 - float(zenith_rad))
    )
    return complex(np.vdot(feed, element_response))


# ---------------------------------------------------------------------------
# First-party statistical channel source
# ---------------------------------------------------------------------------


@dataclass
class ChannelSample:
    h_serving_true: np.ndarray | None = None
    h_serving_est: np.ndarray | None = None
    h_interferers: np.ndarray | None = None
    # UL cross-link: interfering UE -> victim gNB, [intf_ue,time,rb,bs,ue].
    # Never interchangeable with ``h_interferers`` (neighbour gNB -> our UE).
    h_ul_cross: np.ndarray | None = None
    interference_signal: np.ndarray | None = None
    noise_power_dBm: float = -100.0
    snr_dB: float = 0.0
    sir_dB: float | None = None
    sinr_dB: float = 0.0
    ssb_rsrp_dBm: list[float] | None = None
    ssb_rsrq_dB: list[float] | None = None
    ssb_sinr_dB: list[float] | None = None
    ssb_best_beam_idx: list[int] | None = None
    ssb_pcis: list[int] | None = None
    link: str = "DL"
    channel_est_mode: str = "ideal"
    serving_cell_id: int = 0
    ue_position: np.ndarray | None = None
    channel_model: str | None = None
    tdd_pattern: str | None = None
    slot_duration_s: float = 0.5e-3
    link_pairing: str = "single"
    h_ul_true: np.ndarray | None = None
    h_ul_est: np.ndarray | None = None
    h_dl_true: np.ndarray | None = None
    h_dl_est: np.ndarray | None = None
    ul_sir_dB: float | None = None
    dl_sir_dB: float | None = None
    num_interfering_ues: int | None = None
    ul_pre_sinr_dB: float | None = None
    ul_snr_dB: float | None = None
    ul_sinr_dB: float | None = None
    w_dl: np.ndarray | None = None
    dl_rank: int | None = None
    source: str = "internal_sim"
    sample_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    meta: dict[str, Any] = field(default_factory=dict)

    # Retain the structural introspection used by the source handshake without
    # acquiring pydantic as an implementation dependency for every sample.
    model_fields: ClassVar[dict[str, object]] = {
        name: object() for name in ("h_ul_true", "h_ul_est", "h_dl_true", "h_dl_est")
    }


def _db_to_mw(value_dbm: float) -> float:
    return float(10.0 ** (float(value_dbm) / 10.0))


def _ratio_db(num: float, den: float) -> float:
    return float(10.0 * math.log10(max(num, _EPS) / max(den, _EPS)))


def _circular_delta_deg(angle: float, reference: float) -> float:
    return (float(angle) - float(reference) + 180.0) % 360.0 - 180.0


def _seed_from_parts(*parts: int) -> int:
    entropy = [int(part) & 0xFFFFFFFF for part in parts]
    return int(
        np.random.SeedSequence(entropy).generate_state(1, dtype=np.uint32)[0]
    )


class InternalSimSource:
    """Deterministic 38.901-style statistical channel generator.

    Geometry, shadowing, small-scale fading and estimation noise use separate
    named seed derivations.  A global sample index makes worker partitioning
    bit-exact: changing the worker count cannot change UE identity or fading.
    """

    UL_GEOMETRY_SIR_META_KEY = "ul_geometry_sir_dB"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = dict(cfg)
        self.num_ues = max(int(self.cfg.get("num_ues", 1) or 1), 1)
        self.num_samples = max(int(self.cfg.get("num_samples", self.num_ues) or self.num_ues), 1)
        self._seed = int(self.cfg.get("seed", 0) or 0)
        self._ue_seed = int(self.cfg.get("ue_seed", self._seed + 1) or (self._seed + 1))
        self._offset = int(self.cfg.get("sample_index_offset", 0) or 0)
        # 跳频估计档要跨 SRS 机会攒带宽，所以生成器在这两档下**有状态**。
        # 每个 UE 一份：上一次机会留下的全带估计、以及各跳最近一次的导频观测。
        # generate._parallel_exactness_blocker 会因此把这两档强制串行。
        self._hop_estimate: dict[tuple[int, str], np.ndarray] = {}
        self._hop_pilots: dict[tuple[int, str], dict[int, tuple[np.ndarray, np.ndarray]]] = {}
        self._hop_last_seen: dict[tuple[int, str], dict[int, int]] = {}
        self._hop_occasion: dict[tuple[int, str], int] = {}
        self._hop_last_rbg: dict[int, int] = {}

    def _srs_occasion(self, trajectory_time_s: float, slot_duration_s: float,
                      srs_offset_slots: int) -> tuple[int, float, float]:
        """本样本时刻真正可用的那次 SRS 机会，返回 ``(序号, 周期 ms, 时延 ms)``。

        **必须与调度侧共用同一个时序公式**（``csi_aging.srs_occasion_index``）。
        早先这里按"一个样本一次机会"推进跳序，而调度侧按 SRS 周期 + 处理时延推进，
        两个时钟从第一个快照起就对不上——实测 20 个快照里两侧标出来的 RBG
        一个都对不上，估计值的逐 RBG 年龄和调度器的新鲜度门说的是两件事。
        """
        from . import csi_aging as ca  # noqa: PLC0415

        period_ms = max(
            float(self.cfg.get("srs_periodicity", 10) or 10) * float(slot_duration_s) * 1e3,
            1e-9,
        )
        delay_ms = float(self.cfg.get("srs_processing_delay_ms", 2.0) or 0.0)
        offset_ms = (float(srs_offset_slots) * float(slot_duration_s) * 1e3) % period_ms
        index = ca.srs_occasion_index(
            float(trajectory_time_s) * 1e3, period_ms=period_ms,
            processing_delay_ms=delay_ms, offset_ms=offset_ms)
        self._srs_offset_ms = offset_ms
        return index, period_ms, delay_ms

    def _hop_estimate_sequence(
        self, *, ue_id: int, est_mode: str, est_snr: float,
        rng_est: np.random.Generator, n_time: int, n_rb: int,
        trajectory_time_s: float, sample_interval_s: float,
        slot_duration_s: float, srs_offset_slots: int,
        channel_at: Any, tau_rms_ns: float, subcarrier_spacing: float,
        assignment: Any = None, contamination: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, bool, list[int], int,
               np.ndarray, np.ndarray]:
        """跳频档的估计：**在每次 SRS 机会自己的测量时刻上取信道**。

        这是"时钟统一"必须落到观测内容上的那一半。只把跳序标签对齐、观测仍取
        当前样本的信道，等于发给调度器一份贴着旧标签的新 CSI——实测标着 0 ms
        测量的估计与 H(0 ms) 相对差 1.325、与 H(5 ms) 只差 0.196（就是估计噪声），
        跳频档因此**根本没有老化**。

        三件事在同一个循环里解决：
        * 观测在 ``t_m = offset + n*period`` 上取，不是在样本时刻取；
        * 两个样本之间跨过的**每一次**机会都补测（粗采样时不再漏跳）；
        * 一个样本内部逐 slot 按各自时刻找机会，同一次机会内估计逐位不变。

        ``channel_at(t_s)`` 由调用方给出，用同一条轨迹的散射体在任意时刻重算
        小尺度信道（连续轨迹让这件事逐位可复现，实测差 0）。**大尺度几何用的是
        当前样本的**（角度、路损）：``static`` 场景下完全精确；UE 真的在动时，
        测量时刻与样本时刻之间的位移没有反映到角度里，这一条写进 meta。
        """
        from . import csi_aging as ca  # noqa: PLC0415

        period_ms = max(
            float(self.cfg.get("srs_periodicity", 10) or 10)
            * float(slot_duration_s) * 1e3, 1e-9)
        delay_ms = float(self.cfg.get("srs_processing_delay_ms", 2.0) or 0.0)
        offset_ms = (float(srs_offset_slots) * float(slot_duration_s) * 1e3) % period_ms
        hop_cycle = len(_COMPANY_HOP_ORDER)

        slots: dict[str, list[np.ndarray]] = {"dl": [], "ul": []}
        cold_start = False
        hop_index = -1
        pilots = np.arange(int(n_rb), dtype=np.int64)
        occasion = 0
        # **逐 slot 记，不是逐样本记。** 一个样本内部可能跨过多次 SRS 机会
        # （实测 4 个 slot 横跨 3 次机会）；只留最后一次会让下游把新鲜度落在
        # 错误的 slot 上，而且前面几个 slot 的年龄整条都是错的。
        age_by_slot: list[list[int]] = []
        occasion_by_slot: list[int] = []
        for slot in range(int(n_time)):
            t_slot = float(trajectory_time_s) + slot * float(sample_interval_s)
            occasion = ca.srs_occasion_index(
                t_slot * 1e3, period_ms=period_ms,
                processing_delay_ms=delay_ms, offset_ms=offset_ms)
            cursor = self._hop_occasion.get(ue_id)
            if cursor is None:
                cursor = occasion - 1
                cold_start = True
            # 补测所有漏掉的机会；超过一个完整跳频周期就没必要再往回补了
            # （那时全带都会被重新扫过一遍）。
            first = max(cursor + 1, occasion - hop_cycle + 1)
            for index in range(first, occasion + 1):
                measured = channel_at((offset_ms + index * period_ms) / 1e3)
                pilots = self._srs_pilot_rbs(n_rb, index, est_mode, assignment)
                # **被探到的是哪个 RBG，只能从真正探到的那些 RB 反推。**
                # 每个 UE 有自己的 SRS 频域相位，全局固化跳序只是没有资源分配
                # 时的兜底；拿它记账会和实际探测对不上——实测 4 个 UE、8 个样本
                # 全部错位（实际探 RBG8，却把 RBG0 标成新）。
                rbg = self._sounded_rbg(pilots)
                for direction in ("dl", "ul"):
                    key = (ue_id, direction)
                    history = (
                        tuple(self._hop_pilots.get(key, {}).values())
                        if est_mode == "ls_hop_concat" else ()
                    )
                    out = estimate_channel_with_interference(
                        h_serving_true=measured,
                        pilots_serving=None,
                        interferer_cell_ids=None,
                        direction=direction,
                        snr_dB=est_snr,
                        rng=rng_est,
                        est_mode=est_mode,
                        valid_symbol_mask=np.ones(1, dtype=bool),
                        srs_rb_indices=pilots,
                        tau_rms_ns=tau_rms_ns,
                        subcarrier_spacing=subcarrier_spacing,
                        prior_estimate=self._hop_estimate.get(key),
                        pilot_history=history,
                        # 污染是上行 SRS 上的现象，下行 CSI-RS 估计不吃它。
                        h_interferers=(contamination if direction == "ul" else None),
                    )
                    self._hop_estimate[key] = out.h_est
                    self._hop_pilots.setdefault(key, {})[rbg] = (
                        out.pilot_rb, out.h_pilot)
                    self._hop_last_seen.setdefault(key, {})[rbg] = index
                self._hop_occasion[ue_id] = index
                self._hop_last_rbg[ue_id] = rbg
                hop_index = rbg
            for direction in ("dl", "ul"):
                slots[direction].append(
                    np.asarray(self._hop_estimate[(ue_id, direction)])[0])
            slot_seen = self._hop_last_seen.get((ue_id, "dl"), {})
            age_by_slot.append([
                (occasion - slot_seen[k]) if k in slot_seen else -1
                for k in range(hop_cycle)])
            occasion_by_slot.append(int(occasion))
        # 本样本没有新机会时，跳序号仍应是**当前生效那份 CSI 是哪一跳测的**，
        # 不是 -1（-1 只表示"非跳频档"）。补测循环不跑时不能把它留空。
        # 本样本没有新机会时，跳序号仍应是**当前生效那份 CSI 是哪一跳测的**。
        hop_index = int(self._hop_last_rbg.get(ue_id, hop_index))
        seen = self._hop_last_seen.get((ue_id, "dl"), {})
        ages = [(occasion - seen[k]) if k in seen else -1
                for k in range(hop_cycle)]
        return (
            np.stack(slots["dl"]).astype(np.complex64),
            np.stack(slots["ul"]).astype(np.complex64),
            pilots, hop_index, cold_start, ages, occasion,
            np.asarray(age_by_slot, dtype=int),
            np.asarray(occasion_by_slot, dtype=int),
        )

    def _sounded_rbg(self, pilot_rb: np.ndarray) -> int:
        """这批导频 RB 属于哪个 RBG。

        **和测量共用同一个真相。** 记账（谁被标成新、谁的 CSI 多老）如果去查
        全局固化跳序，就会和按 SRS 资源分配算出来的实际探测位置分家：每个 UE
        的频域相位不同，两者从第一个样本起就对不上。
        """
        rb = np.asarray(pilot_rb, dtype=np.int64).reshape(-1)
        if rb.size != 16:
            raise ValueError(
                f"一次 SRS 机会应探 16 个 RB，收到 {rb.size} 个；"
                "逐 RBG 的新鲜度记账依赖这个宽度")
        start = int(rb.min())
        if int(rb.max()) - start != 15 or start % 16 != 0:
            raise ValueError(
                f"导频 RB 必须是对齐的连续 16 个（收到 {start}..{int(rb.max())}）")
        return start // 16

    def _srs_pilot_rbs(self, n_rb: int, occasion: int, est_mode: str,
                       assignment: Any = None) -> np.ndarray:
        """本次 SRS 机会真正探到的 RB。

        非跳频档是**显式的全带 SRS 工程上界**：一次覆盖全带宽，每个 RB 都有导频。
        跳频档按 TS 38.211 表 6.4.1.4.3-1 的 ``C_SRS=63 / B_SRS=1 / b_hop=0``
        取 16 RB 一跳，顺序用本仓固化的 17-hop profile（与 ``csi_aging.hop_order``
        同一条序列）。其它带宽直接拒绝——跳频树不静默推广。
        """
        if est_mode not in HOP_EST_MODES:
            return np.arange(int(n_rb), dtype=np.int64)
        if assignment is not None and int(n_rb) == 272:
            # **探测 RB 的唯一真相是 SRS 资源分配。** 分配器给的是这个 UE 自己的
            # 频域相位，固化跳序只是没有分配时的兜底；两者并存会让"污染落在哪些
            # RB"和"导频取自哪些 RB"指向不同的子带——文本上合得上，物理上是错的。
            from .srs_waveform import assignment_rb_indices  # noqa: PLC0415

            return np.asarray(
                assignment_rb_indices(assignment, int(occasion) % len(_COMPANY_HOP_ORDER)),
                dtype=np.int64)
        if int(n_rb) != 272:
            raise ValueError(
                f"channel_est_mode={est_mode!r} 需要 272 RB 的 17x16 跳频 profile"
                f"（C_SRS=63, B_SRS=1, b_hop=0），本次 num_rb={int(n_rb)}。"
                "跳频树不提供通用推广：请改用非跳频估计档，或把载波设成 272 RB。"
            )
        resource = SRSResourceConfig(
            C_SRS=63, B_SRS=1,
            K_TC=int(self.cfg.get("srs_comb", 2) or 2),
            n_RRC=0, b_hop=0,
        )
        return srs_rb_indices(resource, int(occasion), 0, int(n_rb))

    def _build_sites(self) -> list[Cell]:
        n_sites = max(int(self.cfg.get("num_sites", 1) or 1), 1)
        sectors = max(int(self.cfg.get("sectors_per_site", 1) or 1), 1)
        custom = self.cfg.get("custom_site_positions")
        if custom:
            cells: list[Cell] = []
            for site_id, raw in enumerate(custom):
                if isinstance(raw, dict):
                    pos = np.asarray(
                        [raw.get("x", 0.0), raw.get("y", 0.0), raw.get("z", self.cfg.get("tx_height_m", 25.0))],
                        dtype=np.float64,
                    )
                    base_azimuth = float(raw.get("azimuth_deg", 0.0) or 0.0)
                else:
                    pos = np.asarray(raw, dtype=np.float64)
                    if pos.shape == (2,):
                        pos = np.append(pos, float(self.cfg.get("tx_height_m", 25.0) or 25.0))
                    base_azimuth = 0.0
                if pos.shape != (3,):
                    raise ValueError("custom_site_positions entries must contain x/y/z")
                for sector in range(sectors):
                    azimuth = base_azimuth if sectors <= 1 else (base_azimuth + 120.0 * sector) % 360.0
                    cells.append(Cell(pos.copy(), azimuth, site_id, len(cells)))
            return cells
        kwargs = {
            "isd_m": float(self.cfg.get("isd_m", 500.0) or 500.0),
            "sectors": sectors,
            "tx_height_m": float(self.cfg.get("tx_height_m", 25.0) or 25.0),
            "scenario": str(self.cfg.get("scenario", "UMa_NLOS")),
        }
        if str(self.cfg.get("topology_layout", "hexagonal")) == "linear":
            return make_linear_grid(
                num_sites=n_sites,
                track_offset_m=float(self.cfg.get("track_offset_m", 80.0) or 80.0),
                **kwargs,
            )
        rings = 0 if n_sites <= 1 else (1 if n_sites <= 7 else 2)
        return make_hex_grid(num_rings=rings, **kwargs)

    def _place_ues(self, rng: np.random.Generator, sites: list[Cell], n: int) -> np.ndarray:
        custom = self.cfg.get("custom_ue_positions")
        if custom:
            parsed: list[np.ndarray] = []
            for raw in custom:
                if isinstance(raw, dict):
                    pos = np.asarray(
                        [raw.get("x", 0.0), raw.get("y", 0.0), raw.get("z", self.cfg.get("ue_height_m", 1.5))],
                        dtype=np.float64,
                    )
                else:
                    pos = np.asarray(raw, dtype=np.float64)
                    if pos.shape == (2,):
                        pos = np.append(pos, float(self.cfg.get("ue_height_m", 1.5) or 1.5))
                if pos.shape != (3,):
                    raise ValueError("custom_ue_positions entries must contain x/y/z")
                parsed.append(pos)
            return np.stack([parsed[i % len(parsed)] for i in range(int(n))])
        site_positions: dict[int, np.ndarray] = {}
        for cell in sites:
            site_positions.setdefault(cell.site_id, cell.position)
        anchors = list(site_positions.values()) or [np.asarray([0.0, 0.0, 25.0])]
        isd = float(self.cfg.get("isd_m", 500.0) or 500.0)
        min_d = max(float(self.cfg.get("min_ue_distance_m", 20.0) or 20.0), 10.0)
        max_d = max(float(self.cfg.get("max_ue_distance_m", isd * 0.7) or isd * 0.7), min_d + 1.0)
        height = float(self.cfg.get("ue_height_m", 1.5) or 1.5)
        out = np.zeros((int(n), 3), dtype=np.float64)
        for i in range(int(n)):
            anchor = anchors[i % len(anchors)]
            radius = math.sqrt(rng.uniform(min_d * min_d, max_d * max_d))
            angle = rng.uniform(-np.pi, np.pi)
            out[i] = (anchor[0] + radius * math.cos(angle),
                      anchor[1] + radius * math.sin(angle), height)
        return out

    def describe(self) -> dict[str, Any]:
        sites = self._build_sites()
        rb = int(self.cfg.get("num_rb", 273) or 273)
        bs = int(self.cfg.get("num_bs_tx_ant", 64) or 64)
        ue = int(self.cfg.get("num_ue_rx_ant", 4) or 4)
        return {
            "source": "internal_sim",
            "implementation": "superran-first-party",
            "num_samples": self.num_samples,
            "num_cells": len(sites),
            "shape": [int(self.cfg.get("num_slots_per_sample", 1) or 1), rb, bs, ue],
            "reciprocity_contract": "superran-tdd-transpose-canonical-v2",
        }

    def _pathloss(self, distance_3d_m: float, is_los: bool) -> float:
        scenario = str(self.cfg.get("scenario", "UMa_NLOS"))
        fc = float(self.cfg.get("carrier_freq_hz", 3.5e9) or 3.5e9)
        if scenario.startswith("UMa"):
            from .validate import pathloss_38901_uma_los, pathloss_38901_uma_nlos

            if scenario.endswith("_LOS") or is_los:
                return float(pathloss_38901_uma_los(distance_3d_m, fc,
                                                    h_bs_m=float(self.cfg.get("tx_height_m", 25.0) or 25.0)))
            return float(pathloss_38901_uma_nlos(distance_3d_m, fc, apply_los_floor=False))
        if scenario.startswith("UMi"):
            d = max(float(distance_3d_m), 10.0)
            fc_ghz = fc / 1e9
            los_value = 32.4 + 21.0 * math.log10(d) + 20.0 * math.log10(fc_ghz)
            if scenario.endswith("_LOS") or is_los:
                return float(los_value)
            h_ut = float(self.cfg.get("ue_height_m", 1.5) or 1.5)
            nlos_value = (
                22.4 + 35.3 * math.log10(d) + 21.3 * math.log10(fc_ghz)
                - 0.3 * (h_ut - 1.5)
            )
            return float(max(los_value, nlos_value))
        # 38.901-compatible log-distance fallback for non-UMa presets.
        fc_ghz = fc / 1e9
        exponent = 21.0 if is_los else 31.9
        return float(32.4 + 20.0 * math.log10(fc_ghz) + exponent * math.log10(max(distance_3d_m, 1.0)))

    def _large_scale_state(
        self, cell: Cell, ue_position: np.ndarray, scenario: str
    ) -> tuple[bool, float, float, float]:
        """One link's LOS draw, delay spread, shadow fading and LOS probability.

        The law is quantised on the UE position, so the serving loop and the
        UL cross-link generator draw the *same* random field for the same
        (site, position) pair.  Duplicating it would let the two drift apart
        and make an SRS contamination experiment silently non-comparable.
        """
        delta = np.asarray(ue_position, dtype=np.float64) - cell.position
        d2 = max(float(np.linalg.norm(delta[:2])), 10.0)
        p_los = min(18.0 / d2 + math.exp(-d2 / 63.0) * (1.0 - 18.0 / d2), 1.0)
        forced = scenario.endswith("_LOS")
        qx = int(math.floor(float(ue_position[0]) / 10.0))
        qy = int(math.floor(float(ue_position[1]) / 10.0))
        los_rng = np.random.default_rng(
            _seed_from_parts(self._seed, cell.site_id, qx, qy, 0x10A5)
        )
        los = bool(forced or los_rng.random() < p_los)
        tau_ns = float(self.cfg.get("tau_rms_ns", 100.0 if los else 300.0) or 300.0)
        lsp_rng = np.random.default_rng(
            _seed_from_parts(self._seed, cell.site_id, qx, qy, 0x15F0)
        )
        sf = float(lsp_rng.normal(0.0, 2.0 if los else 3.0))
        return los, tau_ns, sf, float(p_los)

    def _sector_gain_db(
        self, cell: Cell, ue_position: np.ndarray, elements_per_port: int
    ) -> float:
        """Analog element/subarray gain minus the horizontal sector rolloff."""
        delta = np.asarray(ue_position, dtype=np.float64) - cell.position
        bearing = math.degrees(math.atan2(float(delta[1]), float(delta[0])))
        offset = _circular_delta_deg(bearing, cell.azimuth_deg)
        effective_array = (
            str(self.cfg.get("antenna_model_mode", "legacy_64")) == "effective_subarray"
        )
        element_gain = (
            8.0 + 10.0 * math.log10(max(int(elements_per_port), 1))
            if effective_array else 0.0
        )
        return float(element_gain - min(12.0 * (offset / 65.0) ** 2, 30.0))

    def _srs_cross_link_ues(
        self,
        *,
        sites: list[Cell],
        serving: int,
        rx_all: list[float],
        gain_all: list[float],
        pathloss_all: list[float],
        global_index: int,
        scenario: str,
        configured_model: str,
        elements_per_port: int,
        n_time: int,
        n_rb: int,
        n_bs: int,
        n_ue: int,
        doppler_hz: float,
        victim_assignment: Any = None,
        victim_slot: int | None = None,
        srs_occupancy: list[dict[int, int]] | None = None,
    ) -> tuple[np.ndarray | None, dict[str, Any]]:
        """UL cross-link channels: interfering UEs -> *this* serving gNB.

        This is the quantity SRS pilot contamination actually consumes, and it
        is **not** ``h_interferers``.  ``h_interferers`` is the downlink
        neighbour-gNB -> our-UE channel; substituting it here would model the
        wrong link, the wrong array and the wrong angles.

        Who is allowed to contaminate is decided by the repository's own SRS
        resource contract: only cells sharing our PCI-mod-3 colour draw from
        the same SRS resource pool, so only they can occupy the same
        time/comb/cyclic-shift leaf.  Different-colour neighbours still
        interfere on data (that lives in the geometry budget) but cannot
        pollute our SRS pilots.

        Returned tensor is ``[intf_ue, time, RB, gNB_rx_port, UE_port]``,
        normalised so the *desired* UE's UL link has unit small-scale power;
        the amplitude therefore already carries this interferer's UL received
        power relative to the desired UE at the same gNB.
        """
        if not self._srs_cross_link_enabled() or len(sites) < 2:
            return None, {}
        same_colour_only = bool(
            self.cfg.get("srs_cross_link_same_pci_colour_only", True)
        )
        serving_colour = int(sites[serving].cell_id % 1008) % 3
        candidates = [
            k for k in range(len(sites))
            if k != serving
            and (
                not same_colour_only
                or int(sites[k].cell_id % 1008) % 3 == serving_colour
            )
        ]
        # Strongest neighbours first; ties broken by cell index so the choice
        # never depends on Python's sort stability across topologies.
        candidates.sort(key=lambda k: (-float(rx_all[k]), k))
        requested = self.cfg.get("max_srs_cross_link_ues")
        if requested is None:
            requested = self.cfg.get("num_interfering_ues", 0) or 0
        n_cross = max(min(int(requested), len(candidates)), 0)
        if n_cross == 0:
            return None, {
                "srs_cross_link_cells": [],
                "srs_cross_link_skipped": (
                    "no_same_pci_colour_neighbour" if not candidates
                    else "max_srs_cross_link_ues_is_zero"
                ),
            }

        serving_cell = sites[serving]
        # Desired UE's UL received level at this gNB, with the UE transmit
        # power cancelled out: equal-power UEs, no uplink power control.
        desired_rel_db = float(gain_all[serving]) - float(pathloss_all[serving])
        isd = float(self.cfg.get("isd_m", 500.0) or 500.0)
        min_d = max(float(self.cfg.get("min_ue_distance_m", 20.0) or 20.0), 10.0)
        max_d = max(
            float(self.cfg.get("max_ue_distance_m", isd * 0.7) or isd * 0.7),
            min_d + 1.0,
        )
        height = float(self.cfg.get("ue_height_m", 1.5) or 1.5)

        max_attempts = max(int(self.cfg.get("srs_cross_link_drop_attempts", 24) or 24), 1)

        def _relative_level(cell: Cell, pos: np.ndarray) -> tuple[float, bool, float]:
            los, _tau, sf, _p = self._large_scale_state(cell, pos, scenario)
            d3 = max(float(np.linalg.norm(pos - cell.position)), 10.0)
            gain = self._sector_gain_db(cell, pos, elements_per_port)
            return gain - (self._pathloss(d3, los) + sf), los, d3

        rows: list[np.ndarray] = []
        cells_used: list[int] = []
        ues_used: list[int] = []
        collides: list[bool] = []
        occupied_flags: list[bool] = []
        freq_phase: list[int] = []
        sir_db: list[float] = []
        distances: list[float] = []
        los_flags: list[bool] = []
        rejected: list[int] = []
        attempts_used: list[int] = []
        plan = None
        _resources_collide = None
        if victim_assignment is not None and srs_occupancy is not None:
            from .srs_resource import resources_collide as _rc  # noqa: PLC0415

            _resources_collide = _rc
            plan, slots_per_cell = self._srs_network_plan(
                sites, scenario, elements_per_port
            )
        for k in candidates:
            if len(rows) >= n_cross:
                break
            # A concrete neighbour UE, not an anonymous direction: its SRS
            # resource is what decides whether it pollutes us at all.
            # Which reserved interferer slot in that neighbour cell this UE
            # occupies.  Different slots mean different SRS leaves, which is
            # exactly what makes some same-colour neighbours harmless.
            # Only one UE in that neighbour can possibly hit us: the one
            # holding the *same slot index* we hold.  A different slot is a
            # different cyclic shift or frequency phase, i.e. a different
            # resource element set, so it cannot pollute our pilots at all.
            # Whether such a UE exists is read from the neighbour's live
            # occupancy -- an empty slot means nobody is sounding there.
            intf_ue = 0 if victim_slot is None else int(victim_slot)
            drop_rng = np.random.default_rng(
                np.random.SeedSequence(
                    [self._seed, 0x5C10, int(sites[k].cell_id), intf_ue]
                )
            )
            # The interfering UE must really be served by cell k, otherwise the
            # drop puts a "neighbour" UE on top of our own site and invents a
            # contamination level no scheduler would ever produce.
            intf_pos = None
            for attempt in range(max_attempts):
                radius = math.sqrt(drop_rng.uniform(min_d * min_d, max_d * max_d))
                angle = drop_rng.uniform(-np.pi, np.pi)
                trial = np.asarray([
                    float(sites[k].position[0]) + radius * math.cos(angle),
                    float(sites[k].position[1]) + radius * math.sin(angle),
                    height,
                ], dtype=np.float64)
                levels = [_relative_level(cell, trial)[0] for cell in sites]
                if int(np.argmax(levels)) == k:
                    intf_pos = trial
                    attempts_used.append(attempt + 1)
                    break
            if intf_pos is None:
                rejected.append(int(sites[k].cell_id))
                continue

            rel_db, los, d3 = _relative_level(serving_cell, intf_pos)
            delta = intf_pos - serving_cell.position
            horizontal = max(float(np.linalg.norm(delta[:2])), _EPS)
            scale = math.sqrt(max(10.0 ** ((rel_db - desired_rel_db) / 10.0), 0.0))

            aod = math.atan2(float(delta[1]), float(delta[0]))
            aoa = (aod + 2.0 * np.pi) % (2.0 * np.pi) - np.pi
            zod = np.pi / 2.0 - math.atan2(float(delta[2]), horizontal)
            zoa = np.pi / 2.0 - math.atan2(float(-delta[2]), horizontal)
            cross_rng = np.random.default_rng(
                np.random.SeedSequence([self._seed, 0x5C11, global_index, k])
            )
            cross = self._small_scale_channel(
                get_channel_profile(self._effective_model(configured_model, los)),
                cross_rng,
                n_time=n_time,
                n_rb=n_rb,
                n_bs=n_bs,
                n_ue=n_ue,
                doppler_hz=doppler_hz,
                realization_index=global_index * max(len(sites), 1) + k + 0x5C10,
                link_aod_rad=aod,
                link_aoa_rad=aoa,
                link_zod_rad=zod,
                link_zoa_rad=zoa,
                cell=serving_cell,
                ue_position=intf_pos,
                is_los=los,
                role="interferer",
            )
            other = None if plan is None else plan.get(
                (int(sites[k].cell_id), intf_ue)
            )
            occupied = bool(
                srs_occupancy is not None and intf_ue in srs_occupancy[k]
            )
            # Nobody holding that slot means nobody is transmitting on it, so
            # the cross-link carries no signal.  Storing the geometry anyway
            # lets any consumer that forgets to read the occupancy flag hand
            # a silent UE to the receiver as if it were sounding, and invent
            # contamination that never happened.  "No transmitter, no
            # waveform" has to hold in the data itself, not only in a flag.
            if plan is not None and not occupied:
                rows.append(np.zeros_like(cross, dtype=np.complex64))
            else:
                rows.append((cross * scale).astype(np.complex64))
            cells_used.append(int(sites[k].cell_id))
            ues_used.append(intf_ue)
            sir_db.append(
                float(desired_rel_db - rel_db) if (plan is None or occupied)
                else float("-inf")
            )
            distances.append(d3)
            los_flags.append(bool(los))
            if other is None:
                # That cell does not even reserve this slot.
                collides.append(False)
                freq_phase.append(-1)
            else:
                collides.append(
                    occupied
                    and bool(_resources_collide(victim_assignment, other))
                )
                freq_phase.append(int(other.frequency_resource_id))
            occupied_flags.append(occupied)

        if len(rows) < n_cross:
            # A ragged interferer axis would be silently dropped at write
            # time, and the dataset would then look like a clean single-cell
            # SRS experiment.  Fail loudly instead.
            raise RuntimeError(
                f"sample {global_index}: only {len(rows)} of {n_cross} requested "
                f"SRS cross-link UEs could be dropped inside their own cell "
                f"(same-PCI-colour candidates: {len(candidates)}, rejected: "
                f"{rejected}).  Lower max_srs_cross_link_ues, raise "
                f"srs_cross_link_drop_attempts, or widen max_ue_distance_m."
            )
        meta = {
            "srs_cross_link_cells": cells_used,
            "srs_cross_link_cell_ids": np.asarray(cells_used, dtype=np.int64),
            "srs_cross_link_ue_ids": np.asarray(ues_used, dtype=np.int64),
            "srs_cross_link_collides": np.asarray(collides, dtype=np.int64),
            "srs_cross_link_slot_occupied": np.asarray(
                occupied_flags, dtype=np.int64
            ),
            "srs_cross_link_frequency_resource_id": np.asarray(
                freq_phase, dtype=np.int64
            ),
            "srs_cross_link_ul_sir_db_vec": np.asarray(sir_db, dtype=np.float64),
            "srs_cross_link_rejected_cells": rejected,
            "srs_cross_link_drop_attempts": attempts_used,
            "srs_cross_link_pci": [cid % 1008 for cid in cells_used],
            "srs_cross_link_serving_pci_mod3": serving_colour,
            "srs_cross_link_same_pci_colour_only": same_colour_only,
            "srs_cross_link_ul_sir_db": sir_db,
            "srs_cross_link_distance_to_victim_m": distances,
            "srs_cross_link_is_los": los_flags,
            "srs_cross_link_model": (
                "interfering_ue_to_victim_gnb_equal_ue_power_no_ul_power_control_v1"
            ),
            "srs_cross_link_axes": "[intf_ue,time,rb,gnb_rx_port,ue_port]",
        }
        return np.stack(rows), meta

    def _srs_cross_link_enabled(self) -> bool:
        """Whether to synthesise UL cross-link channels.  Explicit false wins."""
        measurements = self.cfg.get("measurements") or {}
        if "srs_cross_link_channels" in measurements:
            return bool(measurements["srs_cross_link_channels"])
        if "store_srs_cross_link_channels" in self.cfg:
            return bool(self.cfg["store_srs_cross_link_channels"])
        return _STORE_SRS_CROSS_LINK_DEFAULT

    def _serving_cell_index(
        self, sites: list[Cell], position: np.ndarray, scenario: str,
        elements_per_port: int,
    ) -> int:
        """Strongest cell for one UE position, same rule as the sample loop."""
        levels = []
        for cell in sites:
            los, _tau, sf, _p = self._large_scale_state(cell, position, scenario)
            d3 = max(float(np.linalg.norm(np.asarray(position) - cell.position)), 10.0)
            gain = self._sector_gain_db(cell, position, elements_per_port)
            levels.append(gain - (self._pathloss(d3, los) + sf))
        return int(np.argmax(levels))

    def _ue_position_at(
        self, base_positions: np.ndarray, ue: int, round_index: int
    ) -> np.ndarray:
        """Where UE ``ue`` is at snapshot ``round_index``.

        Mirrors the sample loop's own motion law, so "who is in this cell now"
        and "which channel did we generate" can never disagree.
        """
        position = np.asarray(base_positions[int(ue)], dtype=np.float64).copy()
        mode = str(self.cfg.get("mobility_mode", "static")).strip().lower()
        speed = max(float(self.cfg.get("ue_speed_kmh", 3.0) or 0.0), 0.0) / 3.6
        if mode != "static" and speed > 0.0:
            heading = math.radians(float(
                self.cfg.get("ue_heading_deg", self.cfg.get("track_heading_deg", 0.0))
                or 0.0
            ))
            travel = speed * float(
                self.cfg.get("sample_interval_s", 5e-3) or 5e-3
            ) * int(round_index)
            position[0] += travel * math.cos(heading)
            position[1] += travel * math.sin(heading)
        return position

    def _srs_slot_state(
        self, sites: list[Cell], scenario: str, elements_per_port: int,
        round_index: int,
    ) -> tuple[dict[int, tuple[int, int]], list[dict[int, int]]]:
        """Which SRS slot each UE holds at snapshot ``round_index``.

        The rule is the one a real gNB follows on handover: **UEs already in
        the cell keep their resource, and the arriving UE is given a free
        one.**  It never displaces a UE that is already sounding, and two UEs
        in the same cell can therefore never end up on the same resource.

        The state is evolved from snapshot 0 forward, so it is a function of
        the snapshot index alone.  That matters for parallel generation: if it
        depended on the order samples happen to arrive, two workers would
        build two different resource plans for the same instant.  Every worker
        replays the same history and gets the same answer.

        Returns ``(assignment, occupancy)`` where ``assignment`` maps UE to
        ``(cell index, slot)`` and ``occupancy`` maps, per cell, slot to UE.
        """
        cache = getattr(self, "_srs_slot_cache", None)
        if cache is None:
            cache = []
            self._srs_slot_cache = cache
        target = int(round_index)
        if target < 0:
            raise ValueError("round_index must be non-negative")
        if target < len(cache):
            return cache[target]

        base = self._place_ues(
            np.random.default_rng(self._ue_seed + 7000), sites, self.num_ues
        )
        static = str(
            self.cfg.get("mobility_mode", "static")
        ).strip().lower() == "static" or max(
            float(self.cfg.get("ue_speed_kmh", 3.0) or 0.0), 0.0
        ) <= 0.0
        pool = self._srs_slots_per_cell(sites, scenario, elements_per_port)

        while len(cache) <= target:
            step = len(cache)
            if static and cache:
                # Nothing moves, so the very first assignment stands forever.
                cache.append(cache[0])
                continue
            serving = [
                self._serving_cell_index(
                    sites, self._ue_position_at(base, u, step), scenario,
                    elements_per_port,
                )
                for u in range(self.num_ues)
            ]
            previous = cache[step - 1][0] if cache else {}
            occupancy: list[dict[int, int]] = [{} for _ in sites]
            assignment: dict[int, tuple[int, int]] = {}
            # 1) Stayers keep what they already have.
            for ue in range(self.num_ues):
                held = previous.get(ue)
                if held is not None and held[0] == serving[ue]:
                    occupancy[serving[ue]][held[1]] = ue
                    assignment[ue] = (serving[ue], held[1])
            # 2) Arrivals take the lowest free slot, in UE order so the result
            #    never depends on iteration order.
            for ue in range(self.num_ues):
                if ue in assignment:
                    continue
                cell_index = serving[ue]
                used = occupancy[cell_index]
                slot = next((k for k in range(pool[cell_index]) if k not in used), None)
                if slot is None:
                    raise RuntimeError(
                        f"snapshot {step}: cell {sites[cell_index].cell_id} already "
                        f"has all {pool[cell_index]} SRS slots occupied, so UE {ue} "
                        "cannot be given a resource of its own.  Raise "
                        "srs_slots_per_cell (each cell may reserve up to "
                        f"{_SRS_SLOTS_AT_BASE_PERIOD} slots without changing the "
                        "global SRS period), or reduce the UE count."
                    )
                used[slot] = ue
                assignment[ue] = (cell_index, slot)
            cache.append((assignment, occupancy))
        return cache[target]

    def _srs_slots_per_cell(
        self, sites: list[Cell], scenario: str, elements_per_port: int,
    ) -> list[int]:
        """Slots each cell reserves.  Must not depend on the sample slice."""
        cached = getattr(self, "_srs_pool_cache", None)
        if cached is not None:
            return cached
        explicit = self.cfg.get("srs_slots_per_cell")
        base_positions = self._place_ues(
            np.random.default_rng(self._ue_seed + 7000), sites, self.num_ues
        )
        home = [
            self._serving_cell_index(
                sites, base_positions[u], scenario, elements_per_port
            )
            for u in range(self.num_ues)
        ]
        counts = [0] * len(sites)
        for cell_index in home:
            counts[cell_index] += 1
        if explicit is not None:
            pool = [max(int(explicit), 1)] * len(sites)
        elif str(
            self.cfg.get("mobility_mode", "static")
        ).strip().lower() == "static" or max(
            float(self.cfg.get("ue_speed_kmh", 3.0) or 0.0), 0.0
        ) <= 0.0:
            # Nothing moves: each cell needs exactly the UEs it serves.
            pool = [max(c, 1) for c in counts]
        else:
            # UEs can pile into one cell, so reserve generously.  Anything up
            # to the base-period ceiling is free: it neither lengthens the SRS
            # period nor changes the resource already given to a lower slot.
            pool = [
                max(min(int(self.num_ues), _SRS_SLOTS_AT_BASE_PERIOD), 1)
            ] * len(sites)
        self._srs_pool_cache = pool
        return pool

    def _srs_network_plan(
        self, sites: list[Cell], scenario: str, elements_per_port: int
    ) -> tuple[dict[tuple[int, int], Any], dict[int, int]]:
        """One network-wide SRS resource plan, allocated once per source.

        Each **cell** owns a pool of UE slots; a UE occupies its slot in
        whichever cell is currently serving it.  That matters the moment UEs
        move: an SRS resource belongs to the serving cell's PCI-mod-3 pool, so
        a handed-over UE must draw from the *target* cell.  Keying the plan by
        UE alone froze it in the UE's first cell and quietly lost collisions.

        Which slot a UE holds at a given instant is decided by
        :meth:`_srs_slot_state`, not here: this method only hands each
        ``(cell, slot)`` pair its resource.  Pool sizing lives in
        :meth:`_srs_slots_per_cell`.

        Contamination is decided by this *allocator*, not by PCI colour: two
        same-colour cells share a pool, but the allocator then hands out
        different frequency phases, cyclic shifts or symbols, and a neighbour
        on a different leaf contributes exactly zero.  Colour only narrows the
        candidate list.

        Returns ``(slot_resources, slots_per_cell)``.
        """
        cached = getattr(self, "_srs_plan_cache", None)
        if cached is not None:
            return cached
        from .srs_resource import allocate_basic_srs_resources  # noqa: PLC0415

        positions = self._place_ues(
            np.random.default_rng(self._ue_seed + 7000), sites, self.num_ues
        )
        slots_by_cell = self._srs_slots_per_cell(sites, scenario, elements_per_port)

        ue_ids: list[int] = []
        cell_ids: list[int] = []
        # Deterministic order: cell by cell, slot by slot.  Sample order and
        # worker count cannot change it.
        #
        # There is deliberately no separate pool for the cross-link
        # interferers: an interfering UE is just a UE served by that
        # neighbour cell, so it draws from the same slot list.  Giving them
        # their own reserved slots put them after every victim slot, so a
        # victim's leaf could never match an interferer's and cross-cell
        # collisions became impossible.
        for index, cell in enumerate(sites):
            for slot in range(slots_by_cell[index]):
                ue_ids.append(int(slot))
                cell_ids.append(int(cell.cell_id))
        plan = allocate_basic_srs_resources(
            ue_ids,
            cell_ids=cell_ids,
            period_ms=float(self.cfg.get("srs_periodicity", 10) or 10),
            hopping=True,
            adaptive_period=True,
        )
        victims: dict[tuple[int, int], Any] = {
            (int(a.cell_id), int(a.ue_id)): a for a in plan
        }
        slots_per_cell = {
            int(cell.cell_id): slots_by_cell[i] for i, cell in enumerate(sites)
        }
        table = (victims, slots_per_cell)
        self._srs_plan_cache = table
        return table

    def _srs_estimate_from_occasions(
        self,
        *,
        truth: np.ndarray,
        sigma: float,
        rng: np.random.Generator,
        mode: str,
        assignment: Any,
        occurrence: int,
        contamination: list[tuple[float, np.ndarray]],
    ) -> np.ndarray:
        """LS channel estimate sampled at the real SRS resource positions.

        One SRS occasion sounds **16 RB**, not the whole carrier.  Which RBG
        that is comes from the assignment's 17-hop phase.

        * ``ls_hop_sequential`` -- only the current occasion is available, so
          only its 16 RB carry an observation and the rest of the band is
          extrapolated from them.  This is what a single 2T occasion really
          gives you.
        * ``ls_hop_concat`` -- the band is stitched from the last full 17-hop
          cycle, so every RBG has been sounded, each at its **own** occasion
          with its own noise realisation and its own contamination state.
        """
        from . import hardware as hw  # noqa: PLC0415
        from .srs_waveform import assignment_rb_indices  # noqa: PLC0415

        n_rb = int(truth.shape[1])
        if n_rb != hw.COMPANY_NUM_RB:
            raise ValueError(
                f"SRS-sampled estimation needs the {hw.COMPANY_NUM_RB}-RB product "
                f"carrier (the 17-hop RBG map is frozen on it); got {n_rb} RB. "
                "Use channel_est_mode='ls_linear'/'ls_mmse' on other carriers."
            )
        cycle = len(hw.COMPANY_SRS_17_HOP_ORDER_RBG)
        if mode == "ls_hop_sequential":
            offsets = [0]
        elif mode == "ls_hop_concat":
            offsets = list(range(cycle))
        else:
            raise ValueError(f"unknown SRS hopping estimation mode {mode!r}")

        estimate = np.zeros_like(truth, dtype=np.complex64)
        observed = np.zeros(n_rb, dtype=bool)
        for offset in offsets:
            occ = (int(occurrence) - offset) % cycle
            rb = assignment_rb_indices(assignment, occ)
            block = truth[:, rb].astype(np.complex128)
            if sigma > 0.0:
                noise = (
                    rng.standard_normal(block.shape)
                    + 1j * rng.standard_normal(block.shape)
                ) / math.sqrt(2.0)
                block = block + sigma * noise
            for weight, cross in contamination:
                # A real collision implies the same frequency-resource phase,
                # so the collider occupies exactly these 16 RB at this
                # occasion.  Nothing leaks onto the rest of the carrier.
                block = block + float(weight) * np.asarray(cross)[:, rb]
            estimate[:, rb] = block.astype(np.complex64)
            observed[rb] = True

        if not observed.all():
            # Sequential mode knows one RBG.  Everything else is extrapolated
            # from it -- explicitly, so nobody reads the untouched band as a
            # measurement.
            pilots = np.flatnonzero(observed)
            grid = np.arange(n_rb)
            filled = np.empty_like(estimate)
            for t in range(estimate.shape[0]):
                for bs in range(estimate.shape[2]):
                    for ue in range(estimate.shape[3]):
                        values = estimate[t, pilots, bs, ue]
                        filled[t, :, bs, ue] = (
                            np.interp(grid, pilots, values.real)
                            + 1j * np.interp(grid, pilots, values.imag)
                        )
            estimate = filled.astype(np.complex64)
        return estimate

    def _srs_pilot_contamination_rho(
        self, h_ul_cross: np.ndarray | None
    ) -> np.ndarray | None:
        """Residual SRS pilot correlation per colliding UE, or ``None``.

        ``rho_k`` is what is left of interferer *k*'s SRS after our gNB
        despreads with the local ZC sequence and applies the delay gate.
        ``1.0`` means a full leaf collision (same symbol, comb and cyclic
        shift): the interferer's channel enters our estimate unattenuated.

        It is **not** calibrated here.  Nothing is applied unless the caller
        asks for it, and the calibrated per-occasion value comes from the
        RE-level receiver in :mod:`superran.srs_waveform`, which despreads a
        real waveform instead of assuming a number.
        """
        if h_ul_cross is None:
            return None
        raw = self.cfg.get("srs_pilot_contamination_rho")
        if raw is None or (isinstance(raw, bool) and not raw):
            return None
        n = int(np.asarray(h_ul_cross).shape[0])
        rho = np.asarray(
            [float(raw)] * n if np.isscalar(raw) or isinstance(raw, (int, float))
            else list(raw),
            dtype=np.float64,
        )
        if rho.shape != (n,):
            raise ValueError(
                "srs_pilot_contamination_rho must be a scalar or one value per "
                f"cross-link UE ({n}); got shape {rho.shape}"
            )
        if not np.all(np.isfinite(rho)) or np.any(rho < 0.0) or np.any(rho > 1.0):
            raise ValueError(
                "srs_pilot_contamination_rho must be finite and within [0, 1]"
            )
        if not np.any(rho > 0.0):
            return None
        return rho

    def _effective_model(self, configured: str, is_los: bool) -> str:
        key = configured.upper().replace("_", "-")
        family = "CDL" if key.startswith("CDL") else "TDL"
        if is_los:
            return key if key in {f"{family}-D", f"{family}-E"} else f"{family}-D"
        return key if key in {f"{family}-A", f"{family}-B", f"{family}-C"} else f"{family}-C"

    def _channel(self, profile: ChannelProfile, rng: np.random.Generator, *,
                 n_time: int, n_rb: int, n_bs: int, n_ue: int, doppler_hz: float,
                 realization_index: int, link_aod_rad: float, link_aoa_rad: float,
                 link_zod_rad: float, link_zoa_rad: float,
                 time_offset_s: float = 0.0) -> np.ndarray:
        powers = 10.0 ** (profile.powers_dB / 10.0)
        powers /= max(float(np.sum(powers)), _EPS)
        tau_rms = float(self.cfg.get("tau_rms_ns", 300.0) or 300.0) * 1e-9
        delays = profile.delays_norm * tau_rms
        scs = float(self.cfg.get("subcarrier_spacing", 30_000.0) or 30_000.0)
        freq = (np.arange(n_rb, dtype=np.float64) - (n_rb - 1.0) / 2.0) * 12.0 * scs
        interval = float(self.cfg.get("sample_interval_s", 5e-3) or 5e-3)
        # **绝对时间轴。** ``time_offset_s`` 是本样本第一个 slot 在这条轨迹上的
        # 时刻；同一个 UE 的第 r 轮覆盖 ``[r*n_time*dt, (r+1)*n_time*dt)``，
        # 相邻两轮首尾相接、不重叠。配合"每条轨迹一套散射体"（rng 按 UE 派生，
        # 见 iter_samples），小尺度衰落就成了时间的连续函数，相邻样本的相关系数
        # 自动等于 Jakes 的 ``J0(2*pi*f_d*dt)``——因为每条射线的多普勒投影角
        # 是均匀分布的，而 ``E_theta[exp(j*2*pi*f_d*cos(theta)*dt)] = J0(...)``。
        #
        # 这一步对 CDL 正确、对射线追踪**错误**，两者不能照抄：CDL 每条径的相位
        # 是随机数、位置移动只改簇的角度，时间演化全靠这里的多普勒项；RT 的径
        # 相位来自真实径长，位置一动几何相位就已经算过一遍，再叠加时间偏移会把
        # 相位算两遍（sionna_rt.synthesize_channel 里有实测数字）。
        times = float(time_offset_s) + np.arange(n_time, dtype=np.float64) * interval
        h = np.zeros((n_time, n_rb, n_bs, n_ue), dtype=np.complex128)
        # Each diffuse table component receives 20 independent sub-rays.
        # Per-ray angle offsets, XPR/Jones phases and Doppler projections are
        # separate.  D/E row zero is the deterministic specular component;
        # its K ratio is already in the table powers and is never mixed twice.
        bs_shape = _panel_shape(n_bs, self.cfg.get("bs_panel"))
        ue_shape = _panel_shape(n_ue, self.cfg.get("ue_panel"))
        bs_layout = PortIndex(*bs_shape, "pol_h_v", "top_to_bottom")
        ue_layout = PortIndex(*ue_shape, "pol_h_v", "top_to_bottom")
        bs_slants = tuple(
            float(value)
            for value in (
                ((self.cfg.get("bs_antenna") or {}).get("element_pattern") or {}).get(
                    "polarization_slant_angles_deg",
                    (45.0, -45.0) if bs_shape[2] == 2 else np.linspace(-45.0, 45.0, bs_shape[2]),
                )
            )
        )
        ue_slants = tuple(
            float(value)
            for value in self.cfg.get(
                "ue_polarization_slant_angles_deg",
                (45.0, -45.0) if ue_shape[2] == 2 else np.linspace(-45.0, 45.0, ue_shape[2]),
            )
        )
        bs_basis = polarization_basis(bs_slants)
        ue_basis = polarization_basis(ue_slants)
        bs_subarray = ((self.cfg.get("bs_antenna") or {}).get("fixed_vertical_subarray") or {})
        bs_v_spacing = float(bs_subarray.get("elements_per_rf_port", 1) or 1) * float(
            bs_subarray.get("ae_vertical_spacing_lambda", 0.5) or 0.5
        )
        for cluster, power in enumerate(powers):
            aoa0 = (
                (float(link_aoa_rad) if profile.is_los and cluster == 0 else None)
                if profile.aoa_deg is None
                else float(link_aoa_rad) + math.radians(float(profile.aoa_deg[cluster]))
            )
            aod0 = (
                (float(link_aod_rad) if profile.is_los and cluster == 0 else None)
                if profile.aod_deg is None
                else float(link_aod_rad) + math.radians(float(profile.aod_deg[cluster]))
            )
            zoa0 = (
                (float(link_zoa_rad) if profile.is_los and cluster == 0 else None)
                if profile.zoa_deg is None
                else float(link_zoa_rad) + math.radians(float(profile.zoa_deg[cluster]) - 90.0)
            )
            zod0 = (
                (float(link_zod_rad) if profile.is_los and cluster == 0 else None)
                if profile.zod_deg is None
                else float(link_zod_rad) + math.radians(float(profile.zod_deg[cluster]) - 90.0)
            )
            ray_count = 1 if profile.is_los and cluster == 0 else 20
            for _ in range(ray_count):
                aoa = (
                    rng.uniform(-np.pi, np.pi)
                    if aoa0 is None
                    else aoa0 + math.radians(profile.c_asa_deg) * rng.normal() / 3.0
                )
                aod = (
                    rng.uniform(-np.pi, np.pi)
                    if aod0 is None
                    else aod0 + math.radians(profile.c_asd_deg) * rng.normal() / 3.0
                )
                zoa = (
                    rng.uniform(0.0, np.pi)
                    if zoa0 is None
                    else zoa0 + math.radians(profile.c_zsa_deg) * rng.normal() / 3.0
                )
                zod = (
                    rng.uniform(0.0, np.pi)
                    if zod0 is None
                    else zod0 + math.radians(profile.c_zsd_deg) * rng.normal() / 3.0
                )
                bs_space = _spatial_panel_response(
                    bs_shape[0], bs_shape[1], aod, zod,
                    horizontal_spacing=float(
                        (self.cfg.get("bs_antenna") or {}).get(
                            "horizontal_port_spacing_lambda", 0.5
                        )
                    ),
                    vertical_spacing=bs_v_spacing,
                )
                feed_count = int(bs_subarray.get("elements_per_rf_port", 1) or 1)
                if feed_count > 1:
                    bs_space = bs_space * fixed_subarray_response(
                        zod,
                        elements_per_rf_port=feed_count,
                        ae_vertical_spacing_lambda=float(
                            bs_subarray.get("ae_vertical_spacing_lambda", 0.67) or 0.67
                        ),
                        fixed_downtilt_deg=float(
                            bs_subarray.get("fixed_downtilt_deg", 0.0) or 0.0
                        ),
                    )
                ue_space = _spatial_panel_response(
                    ue_shape[0], ue_shape[1], aoa, zoa,
                    horizontal_spacing=0.5, vertical_spacing=0.5,
                )
                jones = polarization_jones_matrix(
                    profile.xpr_db if profile.xpr_db > 0.0 else 8.0,
                    rng,
                )
                spatial = np.zeros((n_bs, n_ue), dtype=np.complex128)
                for p_bs in range(bs_shape[2]):
                    for p_ue in range(ue_shape[2]):
                        coupling = ue_basis[p_ue] @ jones @ bs_basis[p_bs]
                        for h_bs in range(bs_shape[0]):
                            for v_bs in range(bs_shape[1]):
                                b = bs_layout.flat(h_bs, v_bs, p_bs)
                                b_space = bs_space[h_bs * bs_shape[1] + v_bs]
                                for h_ue in range(ue_shape[0]):
                                    for v_ue in range(ue_shape[1]):
                                        u = ue_layout.flat(h_ue, v_ue, p_ue)
                                        u_space = ue_space[h_ue * ue_shape[1] + v_ue]
                                        spatial[b, u] = coupling * b_space * np.conj(u_space)
                phase = rng.uniform(-np.pi, np.pi)
                delay_phase = np.exp(-2j * np.pi * freq * delays[cluster])
                projected_fd = float(doppler_hz) * math.cos(rng.uniform(-np.pi, np.pi))
                time_phase = np.exp(1j * (phase + 2.0 * np.pi * projected_fd * times))
                h += math.sqrt(float(power) / ray_count) * time_phase[:, None, None, None] * delay_phase[None, :, None, None] * spatial[None, None]
        # UE-side spatial correlation is produced by the geometry above -- the
        # UE panel response and the per-ray polarization coupling -- and by
        # nothing else.  An earlier build multiplied H by a rank-deficient
        # mixing matrix whose weight cycled with period 7 in the sample index;
        # that is not a 38.901 quantity and it stamped a deterministic period
        # onto the conditioning of every multi-antenna channel.
        # Unit average coefficient power keeps link-level SNR semantics stable.
        h /= math.sqrt(max(float(np.mean(np.abs(h) ** 2)), _EPS))
        return h.astype(np.complex64)

    def _small_scale_channel(
        self,
        profile: ChannelProfile,
        rng: np.random.Generator,
        *,
        n_time: int,
        n_rb: int,
        n_bs: int,
        n_ue: int,
        doppler_hz: float,
        realization_index: int,
        link_aod_rad: float,
        link_aoa_rad: float,
        link_zod_rad: float,
        link_zoa_rad: float,
        cell: Cell,
        ue_position: np.ndarray,
        is_los: bool,
        role: str,
        time_offset_s: float = 0.0,
    ) -> np.ndarray:
        """One BS-UE link's small-scale channel, shape ``[time, rb, bs, ue]``.

        This is the **only** seam where the channel-generation engine is
        chosen.  Everything upstream of it — site layout, UE placement, LOS
        draw, path loss, shadow fading, serving selection, the pre-beam
        S/N/I budget — and everything downstream — estimation noise, SSB,
        TDD pairing, metadata — is shared.  An alternative engine therefore
        changes the channel matrix and nothing else, which is what makes a
        CDL-versus-ray-tracing comparison attributable.

        ``role`` is ``"serving"`` or ``"interferer"``; ``profile`` is the
        statistical CDL/TDL profile and is ignored by engines that derive
        multipath from geometry instead.
        """
        del cell, ue_position, is_los, role
        return self._channel(
            profile,
            rng,
            n_time=n_time,
            n_rb=n_rb,
            n_bs=n_bs,
            n_ue=n_ue,
            doppler_hz=doppler_hz,
            realization_index=realization_index,
            link_aod_rad=link_aod_rad,
            link_aoa_rad=link_aoa_rad,
            link_zod_rad=link_zod_rad,
            link_zoa_rad=link_zoa_rad,
            time_offset_s=time_offset_s,
        )

    def iter_samples(self) -> Iterator[ChannelSample]:
        sites = self._build_sites()
        positions = self._place_ues(np.random.default_rng(self._ue_seed + 7000), sites, self.num_ues)
        n_rb = int(self.cfg.get("num_rb", 273) or 273)
        n_bs = int(self.cfg.get("num_bs_tx_ant", self.cfg.get("num_bs_rx_ant", 64)) or 64)
        n_ue = int(self.cfg.get("num_ue_rx_ant", 4) or 4)
        n_ue_tx = int(self.cfg.get("num_ue_tx_ant", n_ue) or n_ue)
        link = str(self.cfg.get("link", "DL")).upper()
        if link == "BOTH" and n_ue_tx != n_ue:
            raise ValueError("paired TDD generation requires num_ue_tx_ant == num_ue_rx_ant")
        n_time = max(int(self.cfg.get("num_slots_per_sample", 1) or 1), 1)
        sample_interval_s = float(self.cfg.get("sample_interval_s", 5e-3) or 5e-3)
        configured_model = str(self.cfg.get("channel_model", "CDL-C"))
        scenario = str(self.cfg.get("scenario", "UMa_NLOS"))
        scs = float(self.cfg.get("subcarrier_spacing", 30_000.0) or 30_000.0)
        mu = int(round(math.log2(max(scs / 15_000.0, 1.0))))
        slot_duration = 1e-3 / (2 ** mu)
        fc = float(self.cfg.get("carrier_freq_hz", 3.5e9) or 3.5e9)
        speed = max(float(self.cfg.get("ue_speed_kmh", 3.0) or 0.0), 0.0) / 3.6
        # Radio engineering convention used by the frozen product checks.
        # Keep 3e8 here rather than mixing it with geometry's exact SI c.
        doppler = speed * fc / 300_000_000.0
        tx_dbm = float(self.cfg.get("tx_power_dbm", 46.0) or 46.0)
        nf_db = float(self.cfg.get("noise_figure_db", 7.0) or 7.0)
        noise_dbm = -174.0 + 10.0 * math.log10(12.0 * scs) + nf_db
        noise_mw = _db_to_mw(noise_dbm)
        measure_ssb = bool((self.cfg.get("measurements") or {}).get("ssb_rsrp", True))
        # An explicit ``false`` must win.  Folding both keys through ``or``
        # with a shared default makes the off switch unreachable as soon as
        # the default flips to true, and that failure is silent.
        _measurements = self.cfg.get("measurements") or {}
        if "interferer_channels" in _measurements:
            keep_interferer_h = bool(_measurements["interferer_channels"])
        elif "store_interferer_channels" in self.cfg:
            keep_interferer_h = bool(self.cfg["store_interferer_channels"])
        else:
            keep_interferer_h = _STORE_INTERFERER_CHANNELS_DEFAULT
        bs_ant = dict(self.cfg.get("bs_antenna") or {})
        subarray = dict(bs_ant.get("fixed_vertical_subarray") or {})
        elements_per_port = int(subarray.get("elements_per_rf_port", 1) or 1)

        # **静止 + 零多普勒 + 多轮 = 逐位重复的矩阵，必须硬失败。**
        # 小尺度实现现在按轨迹派生，时间演化全靠多普勒。速度为 0 时多普勒为 0、
        # 位置也不动，于是同一个 UE 的每一轮都是同一个矩阵。它跑得通、meta 自洽、
        # 下游还会把它们当成独立快照——正是最难查的那种假数据。
        # TDD 图案与 SRS offset 只依赖配置，提到循环外算一次：估计器要在生成
        # 每个样本之前就知道本条轨迹的 SRS 时序。
        pattern_name = str(self.cfg.get("tdd_pattern", "DDDSU"))
        try:
            slots_pattern = get_tdd_pattern(pattern_name).slots
        except ValueError:
            slots_pattern = "".join(ch for ch in pattern_name if ch in "DSU") or "D"
        paired_dl_rs_slot = next(
            (idx for idx, direction in enumerate(slots_pattern) if direction in "DS"), 0)
        paired_ul_srs_slot = next(
            (idx for idx, direction in enumerate(slots_pattern) if direction in "US"), 0)
        explicit_srs_offset = self.cfg.get("srs_offset")
        srs_offset = (
            int(explicit_srs_offset)
            if explicit_srs_offset is not None
            else paired_ul_srs_slot
        )

        rounds = -(-int(self.num_samples) // max(int(self.num_ues), 1))
        if rounds > 1 and doppler <= 0.0:
            raise ValueError(
                f"ue_speed_kmh={float(self.cfg.get('ue_speed_kmh', 3.0) or 0.0):g}"
                f" 时多普勒为 0，位置也不动，而每个 UE 有 {rounds} 轮样本："
                "小尺度衰落按轨迹连续演化后，这些样本会**逐位相同**，"
                "不是 {n} 个独立信道实现（旧实现靠每样本重掷随机数掩盖了这一点，"
                "代价是相邻样本毫无时间相关性、CSI 老化失去物理意义）。"
                "要么给 ue_speed_kmh 一个正值（哪怕 3 km/h 也会让相邻样本按 "
                "Jakes 去相关），要么把 num_samples 降到 num_ues 以内。"
                "**不会静默产出重复矩阵。**".format(n=int(self.num_samples))
            )

        for local_index in range(self.num_samples):
            global_index = self._offset + local_index
            ue_id = global_index % self.num_ues
            position = positions[ue_id].copy()
            round_index = global_index // self.num_ues
            mobility_mode = str(self.cfg.get("mobility_mode", "static")).strip().lower()
            speed_mps = max(float(self.cfg.get("ue_speed_kmh", 3.0) or 0.0), 0.0) / 3.6
            # **一条轨迹只有一个时钟。** 一个样本横跨 n_time 个 sample_interval_s，
            # 所以第 r 轮的起始时刻是 r*n_time*dt，位移也必须走同样多的时间。
            # 旧实现每轮只推进一个 dt，位置钟比时间钟慢 n_time 倍，相邻两轮的
            # 时间窗口互相重叠（n_time=8 时重叠 7/8），下游还会把它们当独立快照。
            trajectory_time_s = round_index * n_time * sample_interval_s
            if mobility_mode != "static" and speed_mps > 0.0:
                heading = math.radians(
                    float(self.cfg.get("ue_heading_deg", self.cfg.get("track_heading_deg", 0.0)) or 0.0)
                )
                travel = speed_mps * trajectory_time_s
                position[0] += travel * math.cos(heading)
                position[1] += travel * math.sin(heading)
            # **小尺度的随机源按轨迹派生，不按样本派生。** 同一个 UE 的所有样本
            # 共用一套散射体（簇/射线的角度、极化相位、多普勒投影角），随时间
            # 演化的只有多普勒相位；这样相邻样本才是同一条物理信道上的两个时刻，
            # 而不是两次互不相干的瑞利实现。仍然只依赖 (seed, ue_id, 绝对时刻)，
            # 与 global_index 的分片方式无关，所以并行切片依旧逐位可复现。
            rng_small = np.random.default_rng(np.random.SeedSequence([self._seed, 211, ue_id]))
            # 估计噪声相反：每次测量的热噪声本来就是独立的，仍按样本派生。
            rng_est = np.random.default_rng(np.random.SeedSequence([self._seed, 307, global_index]))

            site_state: dict[int, tuple[bool, float, float, float]] = {}
            pathloss_all: list[float] = []
            rx_all: list[float] = []
            gain_all: list[float] = []
            los_all: list[bool] = []
            prob_all: list[float] = []
            ds_all: list[float] = []
            sf_all: list[float] = []
            group_ids: list[int] = []
            distances: list[float] = []
            for cell in sites:
                delta = position - cell.position
                d3 = max(float(np.linalg.norm(delta)), 10.0)
                d2 = max(float(np.linalg.norm(delta[:2])), 10.0)
                if cell.site_id not in site_state:
                    site_state[cell.site_id] = self._large_scale_state(
                        cell, position, scenario
                    )
                los, tau_ns, sf, p_los = site_state[cell.site_id]
                gain = self._sector_gain_db(cell, position, elements_per_port)
                pl = self._pathloss(d3, los) + sf
                # Keep the total-carrier received power independent of the
                # frequency grid.  Per-RB PSD is formed once below; otherwise
                # an algebraically cancelling +/-10log10(N_RB) leaves tiny
                # floating differences in SIR and breaks exact geometry probes.
                # ``tx_power_dbm`` is total conducted carrier power.  Digital
                # precoding gain stays in H; only the analog element/subarray
                # pattern enters this pre-beam received-power budget.
                rx = tx_dbm + gain - pl
                pathloss_all.append(pl)
                rx_all.append(rx)
                gain_all.append(gain)
                los_all.append(los)
                prob_all.append(1.0 if scenario.endswith("_LOS") else p_los)
                ds_all.append(tau_ns)
                sf_all.append(sf)
                group_ids.append(cell.site_id)
                distances.append(d3)

            serving = int(np.argmax(rx_all))
            serving_cell = sites[serving]
            link_delta = position - serving_cell.position
            horizontal_distance = max(float(np.linalg.norm(link_delta[:2])), _EPS)
            link_aod = math.atan2(float(link_delta[1]), float(link_delta[0]))
            link_aoa = (link_aod + 2.0 * np.pi) % (2.0 * np.pi) - np.pi
            tx_elevation = math.atan2(float(link_delta[2]), horizontal_distance)
            rx_elevation = math.atan2(float(-link_delta[2]), horizontal_distance)
            link_zod = np.pi / 2.0 - tx_elevation
            link_zoa = np.pi / 2.0 - rx_elevation
            est_mode_requested = str(self.cfg.get("channel_est_mode", "ls_linear"))
            total_signal_mw = _db_to_mw(rx_all[serving])
            signal_mw = total_signal_mw / max(n_rb, 1)
            per_cell_i = np.asarray([
                0.0 if i == serving else _db_to_mw(value) / max(n_rb, 1)
                for i, value in enumerate(rx_all)
            ], dtype=np.float64)
            interference_mw = float(np.sum(per_cell_i))
            snr_db = _ratio_db(signal_mw, noise_mw)
            total_interference_mw = sum(
                _db_to_mw(value) for i, value in enumerate(rx_all) if i != serving
            )
            sir_db = (
                49.9
                if total_interference_mw <= 0
                else _ratio_db(total_signal_mw, total_interference_mw)
            )
            sinr_db = _ratio_db(signal_mw, noise_mw + interference_mw)
            is_los = bool(los_all[serving])
            effective_model = self._effective_model(configured_model, is_los)
            profile = get_channel_profile(effective_model)
            h_dl = self._small_scale_channel(
                profile, rng_small, n_time=n_time, n_rb=n_rb,
                n_bs=n_bs, n_ue=n_ue, doppler_hz=doppler,
                realization_index=global_index,
                link_aod_rad=link_aod, link_aoa_rad=link_aoa,
                link_zod_rad=link_zod, link_zoa_rad=link_zoa,
                cell=serving_cell, ue_position=position, is_los=is_los,
                role="serving", time_offset_s=trajectory_time_s,
            )

            est_pilot_rb = np.arange(n_rb, dtype=np.int64)
            est_hop_index = -1
            est_cold_start = False
            est_rbg_age = None
            est_rbg_age_by_slot = None
            est_occasion_by_slot = None
            est_srs_occasion = None
            srs_period_ms = float(self.cfg.get("srs_periodicity", 10) or 10) * slot_duration * 1e3
            srs_delay_ms = float(self.cfg.get("srs_processing_delay_ms", 2.0) or 0.0)
            # Real SRS resources for this snapshot: which 16 RB this UE
            # sounded, and which neighbour UEs share that exact leaf.
            srs_occurrence = int(round_index)
            victim_assignment = None
            srs_occupancy = None
            srs_slot = -1
            srs_pilot_rb = np.arange(n_rb, dtype=np.int64)
            # The SRS plan is needed whenever anything downstream has to know
            # which resource this UE sounded: the hopping estimators, the
            # contamination weight, and -- always -- the per-sample identity
            # of the cross-link interferers, so a consumer can bind each
            # stored link back to a real neighbour UE and its SRS resource.
            _cross_on = self._srs_cross_link_enabled()
            needs_srs_plan = (
                _cross_on
                or est_mode_requested in ("ls_hop_concat", "ls_hop_sequential")
                or self.cfg.get("srs_pilot_contamination_rho") is not None
            )
            if needs_srs_plan and len(sites) >= 1:
                victims, _ = self._srs_network_plan(
                    sites, scenario, elements_per_port
                )
                srs_assign, srs_occupancy = self._srs_slot_state(
                    sites, scenario, elements_per_port, round_index
                )
                held = srs_assign.get(int(ue_id))
                if held is None:
                    raise RuntimeError(
                        f"UE {ue_id} 在快照 {round_index} 上没有 SRS 槽位"
                    )
                # The occupancy table and the sample loop must agree on which
                # cell serves this UE; if they ever diverge the resource would
                # belong to a different cell than the channel we generated.
                if int(held[0]) != int(serving):
                    raise RuntimeError(
                        f"SRS 占用表认为 UE {ue_id} 在小区 "
                        f"{sites[held[0]].cell_id}，而本样本的服务小区是 "
                        f"{serving_cell.cell_id}；两者必须一致"
                    )
                srs_slot = int(held[1])
                key = (int(serving_cell.cell_id), srs_slot)
                if key not in victims:
                    raise RuntimeError(
                        f"小区 {serving_cell.cell_id} 没有槽位 {srs_slot} 的 SRS "
                        "资源；请检查 srs_slots_per_cell 与占用表是否一致。"
                    )
                victim_assignment = victims[key]
                if n_rb == 272:
                    from .srs_waveform import (  # noqa: PLC0415
                        assignment_rb_indices as _arb,
                    )
                    srs_pilot_rb = _arb(victim_assignment, srs_occurrence)

            h_ul_cross, cross_meta = self._srs_cross_link_ues(
                sites=sites,
                serving=serving,
                rx_all=rx_all,
                gain_all=gain_all,
                pathloss_all=pathloss_all,
                global_index=global_index,
                scenario=scenario,
                configured_model=configured_model,
                elements_per_port=elements_per_port,
                n_time=n_time,
                n_rb=n_rb,
                n_bs=n_bs,
                n_ue=n_ue,
                doppler_hz=doppler,
                victim_assignment=victim_assignment,
                victim_slot=None if srs_slot < 0 else srs_slot,
                srs_occupancy=srs_occupancy,
            )

            est_mode = est_mode_requested
            if est_mode == "ideal":
                h_dl_est = h_dl.copy()
                h_ul_est = h_dl.copy()
            else:
                measurement_sir = max(10.0 - 10.0 * math.log10(
                    max(int(self.cfg.get("num_interfering_ues", 0) or 0), 1)), -20.0)
                # The estimator works after coherent pilot de-spreading; the
                # scalar pre-beam geometry reference is not itself its NMSE.
                # Keep a small positive observable floor while measurement SIR
                # still controls relative degradation across paired scenarios.
                est_snr = max(
                    min(snr_db, measurement_sir if link == "BOTH" else snr_db),
                    0.1,
                )
                # **一次 SRS 机会才推进一跳，不是一个样本推进一跳。**
                # 时序取自 csi_aging.srs_occasion_index —— 与调度侧同一个公式、
                # 同一个周期、同一个处理时延。
                hopping = est_mode in HOP_EST_MODES
                occasion, srs_period_ms, srs_delay_ms = self._srs_occasion(
                    trajectory_time_s, slot_duration, srs_offset)
                est_srs_occasion = occasion

                # SRS pilot contamination.  A colliding neighbour UE's SRS
                # survives our despreading with residual correlation rho, so
                # its *whole channel to our gNB* lands inside our estimate.
                # That is why contamination is not the same thing as extra
                # noise: the error points at the interferer's spatial
                # direction, which is exactly where reciprocity-based
                # precoding then steers energy.
                #
                # Who actually contaminates is decided by the SRS allocator,
                # not by PCI colour: a same-colour neighbour on a different
                # frequency-resource phase, comb or cyclic shift lands on
                # other resource elements and contributes nothing.
                rho = self._srs_pilot_contamination_rho(h_ul_cross)
                contamination: list[tuple[float, np.ndarray]] = []
                if rho is not None:
                    collides = np.asarray(
                        cross_meta.get(
                            "srs_cross_link_collides", np.zeros(0, dtype=np.int64)
                        )
                    ).reshape(-1)
                    for k, weight in enumerate(rho):
                        if k < collides.size and bool(collides[k]):
                            contamination.append(
                                (float(weight), np.asarray(h_ul_cross)[k])
                            )
                    cross_meta["srs_pilot_contamination_rho"] = [
                        float(v) for v in rho
                    ]
                    cross_meta["srs_pilot_contamination_applied"] = bool(contamination)
                elif h_ul_cross is not None:
                    cross_meta["srs_pilot_contamination_applied"] = False

                # 污染进估计器的形式是"已按残余相关性加权的上行交叉链路"，
                # 由估计器在**它自己那次机会探到的 RB 上**取切片，所以不会
                # 把一次机会的污染抹到 272 RB 上。
                contamination_tensor = (
                    np.stack([float(w) * np.asarray(c) for w, c in contamination])
                    if contamination else None
                )

                if hopping:
                    # 同一条轨迹的散射体，在任意时刻重算小尺度信道。连续轨迹让
                    # 这件事逐位可复现（实测与原样本差 0）。每轮的几何量用默认参数
                    # 显式绑定，闭包不去捕获循环变量。
                    def _channel_at(
                        moment_s: float, *, _ue=ue_id, _prof=profile,
                        _idx=global_index, _aod=link_aod, _aoa=link_aoa,
                        _zod=link_zod, _zoa=link_zoa, _cell=serving_cell,
                        _pos=position, _los=is_los,
                    ) -> np.ndarray:
                        trace_rng = np.random.default_rng(
                            np.random.SeedSequence([self._seed, 211, _ue]))
                        return self._small_scale_channel(
                            _prof, trace_rng, n_time=1, n_rb=n_rb,
                            n_bs=n_bs, n_ue=n_ue, doppler_hz=doppler,
                            realization_index=_idx,
                            link_aod_rad=_aod, link_aoa_rad=_aoa,
                            link_zod_rad=_zod, link_zoa_rad=_zoa,
                            cell=_cell, ue_position=_pos,
                            is_los=_los, role="serving",
                            time_offset_s=moment_s)

                    (h_dl_est, h_ul_est, est_pilot_rb, est_hop_index,
                     est_cold_start, est_rbg_age, est_srs_occasion,
                     est_rbg_age_by_slot, est_occasion_by_slot) = (
                        self._hop_estimate_sequence(
                            ue_id=ue_id, est_mode=est_mode, est_snr=est_snr,
                            rng_est=rng_est, n_time=n_time, n_rb=n_rb,
                            trajectory_time_s=trajectory_time_s,
                            sample_interval_s=sample_interval_s,
                            slot_duration_s=slot_duration,
                            srs_offset_slots=srs_offset,
                            channel_at=_channel_at,
                            tau_rms_ns=ds_all[serving],
                            subcarrier_spacing=scs,
                            assignment=victim_assignment,
                            contamination=contamination_tensor))
                    # **数据集报的"本次探到哪些 RB"必须是估计器真正测的那一组。**
                    # 主干那侧按 round_index 算 srs_occurrence，估计器按共用 SRS
                    # 时钟（含处理时延）算机会序号，两者差一次机会：于是 meta 说
                    # 探了 RBG0、估计其实来自 RBG9（实测 12/12 个样本全错位）。
                    # 跳频档以估计器为准，非跳频档保持主干原样。
                    srs_pilot_rb = np.asarray(est_pilot_rb, dtype=np.int64)
                    srs_occurrence = int(est_srs_occasion)
                else:
                    # 全带 SRS 的**工程上界**："现在就探"，每个样本自己测一次。
                    # 跳频陈旧度由系统侧的老化模型建模（见 server 侧的守卫），
                    # 所以这里不做机会锁定，否则周期内相位会被算两遍。
                    est_pilot_rb = np.arange(n_rb, dtype=np.int64)
                    mask = np.ones(n_time, dtype=bool)
                    # 全带代理不是把一次机会的污染抹开的许可：只有这次真的探到
                    # 的那 16 个 RB 会被污染，其余位置显式置零。
                    masked_contamination = None
                    if contamination_tensor is not None:
                        masked_contamination = np.zeros_like(contamination_tensor)
                        masked_contamination[:, :, srs_pilot_rb] = (
                            contamination_tensor[:, :, srs_pilot_rb])
                    results = []
                    for direction_key in ("dl", "ul"):
                        out = estimate_channel_with_interference(
                            h_serving_true=h_dl,
                            # 污染是上行 SRS 上的现象：下行估计不吃它。
                            h_interferers=(masked_contamination
                                           if direction_key == "ul" else None),
                            pilots_serving=None,
                            interferer_cell_ids=None,
                            direction=direction_key,
                            snr_dB=est_snr,
                            rng=rng_est,
                            est_mode=est_mode,
                            valid_symbol_mask=mask,
                            srs_rb_indices=est_pilot_rb,
                            tau_rms_ns=ds_all[serving],
                            subcarrier_spacing=scs,
                        )
                        results.append(out.h_est)
                    h_dl_est, h_ul_est = results
            h_ul = h_dl.copy()  # canonical v2: physical transpose, same stored BS/UE tensor
            site_models = [
                self._effective_model(configured_model, site_state[cell.site_id][0])
                for cell in sites
            ]

            h_intf = None
            intf_cells: list[int] = []
            intf_rx: list[float] = []
            if keep_interferer_h and len(sites) > 1:
                rows = []
                # The documented contract is "keep the 3 strongest by
                # default".  The old code default was "keep every neighbour",
                # which nobody hit while storage was off; with storage on it
                # would silently cost one full channel synthesis per cell
                # (21x on a 7-site hex layout).
                raw_cap = self.cfg.get(
                    "max_per_ue_intf_cells", _MAX_PER_UE_INTF_CELLS_DEFAULT
                )
                if raw_cap is None:
                    raw_cap = _MAX_PER_UE_INTF_CELLS_DEFAULT
                max_cells = max(min(int(raw_cap), len(sites) - 1), 0)
                # "Keep the strongest N" has to mean strongest, not
                # lowest-numbered.  Iterating cells in index order and
                # stopping at N silently kept cells 0,1,2 -- on a 7-site
                # layout that picked a neighbour up to 22 dB weaker than the
                # real dominant interferer, so the equalizer saw the wrong
                # interference directions.  Ties break on cell index so the
                # order never depends on sort stability.
                ranked = sorted(
                    (k for k in range(len(sites)) if k != serving),
                    key=lambda k: (-float(rx_all[k]), k),
                )
                for k in ranked:
                    if max_cells <= 0:
                        break
                    scale = math.sqrt(max(per_cell_i[k] / max(signal_mw, _EPS), _EPS))
                    cross_delta = position - sites[k].position
                    cross_horizontal = max(float(np.linalg.norm(cross_delta[:2])), _EPS)
                    cross_aod = math.atan2(float(cross_delta[1]), float(cross_delta[0]))
                    cross_aoa = (cross_aod + 2.0 * np.pi) % (2.0 * np.pi) - np.pi
                    cross_zod = np.pi / 2.0 - math.atan2(
                        float(cross_delta[2]), cross_horizontal
                    )
                    cross_zoa = np.pi / 2.0 - math.atan2(
                        float(-cross_delta[2]), cross_horizontal
                    )
                    cross_rng = np.random.default_rng(
                        np.random.SeedSequence([self._seed, 401, ue_id, k])
                    )
                    cross = self._small_scale_channel(
                        get_channel_profile(site_models[k]),
                        cross_rng,
                        n_time=n_time,
                        n_rb=n_rb,
                        n_bs=n_bs,
                        n_ue=n_ue,
                        doppler_hz=doppler,
                        realization_index=global_index * max(len(sites), 1) + k,
                        link_aod_rad=cross_aod,
                        link_aoa_rad=cross_aoa,
                        link_zod_rad=cross_zod,
                        link_zoa_rad=cross_zoa,
                        cell=sites[k],
                        ue_position=position,
                        is_los=bool(los_all[k]),
                        role="interferer",
                        time_offset_s=trajectory_time_s,
                    )
                    rows.append((cross * scale).astype(np.complex64))
                    intf_cells.append(int(sites[k].cell_id))
                    intf_rx.append(float(rx_all[k]))
                    if len(rows) >= max_cells:
                        break
                if rows:
                    h_intf = np.stack(rows)

            ssb_sinr: list[float] | None = None
            if measure_ssb:
                ssb_sinr = []
                for i, rx in enumerate(rx_all):
                    wanted = _db_to_mw(rx)
                    other = sum(_db_to_mw(v) for j, v in enumerate(rx_all) if j != i)
                    ssb_sinr.append(_ratio_db(wanted, noise_mw + other))

            ul_measure_sir = max(10.0 - 10.0 * math.log10(
                max(int(self.cfg.get("num_interfering_ues", 0) or 0), 1)), -20.0)
            ul_sinr = -10.0 * math.log10(
                10.0 ** (-snr_db / 10.0) + 10.0 ** (-ul_measure_sir / 10.0)
            )
            antenna_profile = (
                f"fixed_1to{elements_per_port}_vertical_subarray_{n_bs}T"
                if str(self.cfg.get("antenna_model_mode", "legacy_64")) == "effective_subarray"
                else f"legacy_independent_ports_{n_bs}T"
            )
            if scenario.startswith("UMa"):
                pathloss_model = "3gpp-tr38901-uma"
                pathloss_approximate = False
            elif scenario.startswith("UMi"):
                pathloss_model = "3gpp-tr38901-umi-street-canyon"
                pathloss_approximate = False
            else:
                pathloss_model = "log-distance-engineering-fallback-v1"
                pathloss_approximate = True
            meta = {
                "implementation": "superran-first-party",
                "source_contract_id": "superran-native-source-contract-v2",
                "num_cells": len(sites),
                "site_state_policy": "same_site_shared_cross_site_independent_v1",
                "physical_site_group_ids": group_ids,
                "pathloss_all_db": pathloss_all,
                "rx_power_all_dbm": rx_all,
                "antenna_gain_all_db": gain_all,
                "is_los_all": los_all,
                "los_probability_all": prob_all,
                "sample_tau_rms_all_ns": ds_all,
                "shadow_fading_all_db": sf_all,
                "effective_channel_model_all": site_models,
                "pathloss_dB": pathloss_all[serving],
                "pathloss_model": pathloss_model,
                "pathloss_model_approximate": pathloss_approximate,
                "distance_3d_m": distances[serving],
                "is_los": is_los,
                "los_probability": prob_all[serving],
                "rx_power_serving_dbm": rx_all[serving],
                "doppler_hz": doppler,
                "sample_tau_rms_ns": ds_all[serving],
                "tau_rms_ns": ds_all[serving],
                "noise_power_dbm": noise_dbm,
                "antenna_gain_serving_db": gain_all[serving],
                "sinr_geometry_db": sinr_db,
                "sir_geometry_db": sir_db,
                "rician_k_db": profile.k_factor_dB,
                "num_taps": len(profile.powers_dB),
                "serving_pci": serving_cell.cell_id % 1008,
                "ue_id": ue_id,
                "ue_id_source": "superran_global_sample_index",
                "round_idx": round_index,
                "trajectory_id": ue_id,
                "tx_power_dbm": tx_dbm,
                "ue_tx_power_dbm": float(self.cfg.get("ue_tx_power_dbm", 23.0) or 23.0),
                "noise_figure_db": nf_db,
                "serving_cell_index": serving,
                "dl_signal_power_mw": signal_mw,
                "dl_thermal_noise_power_mw": noise_mw,
                "dl_interference_power_per_slot_per_cell_mw": per_cell_i.reshape(1, -1),
                "dl_power_decomposition_version": "superran-prebeam-per-rb-sni-v1",
                **cross_meta,
                # Which neighbours h_interferers actually holds, strongest
                # first.  Without it "the strongest 3" is unverifiable and a
                # selection bug stays invisible.
                "interferer_cell_ids": np.asarray(intf_cells, dtype=np.int64),
                # 必须与 interferer_cell_ids 同序（都按保留顺序，最强优先）；
                # 按小区编号另取一遍会让两列错位。
                "interferer_rx_power_dbm": np.asarray(intf_rx, dtype=np.float64),
                "srs_occurrence_index": srs_occurrence,
                "srs_victim_rb_start": int(srs_pilot_rb[0]),
                "srs_victim_rb_count": int(srs_pilot_rb.size),
                "srs_victim_frequency_resource_id": (
                    -1 if victim_assignment is None
                    else int(victim_assignment.frequency_resource_id)
                ),
                "srs_victim_ue_id": int(ue_id),
                "srs_victim_slot": srs_slot,
                "srs_slots_reserved_per_cell": (
                    -1 if victim_assignment is None
                    else int(self._srs_slots_per_cell(
                        sites, scenario, elements_per_port)[serving])
                ),
                "srs_cell_occupancy": (
                    -1 if srs_occupancy is None else int(len(srs_occupancy[serving]))
                ),
                # The cell the assignment really came from.  Reporting the
                # serving cell here regardless would hide exactly the
                # handover mis-binding this key exists to expose.
                "srs_victim_cell_id": (
                    -1 if victim_assignment is None
                    else int(victim_assignment.cell_id)
                ),
                "srs_serving_cell_id": int(serving_cell.cell_id),
                "ul_geometry_sir_dB": sir_db,
                "ul_geometry_sir_model": "shared_dl_geometry_sir_symmetric_neighbour_power_v1",
                "effective_channel_model": effective_model,
                "channel_model": configured_model,
                "antenna_profile": antenna_profile,
                "tdd_slot_direction": str(self.cfg.get("tdd_pattern", "DDDSU"))[0],
                "paired_dl_rs_slot": paired_dl_rs_slot,
                "paired_ul_srs_slot": paired_ul_srs_slot,
                "paired_rs_slot_gap": paired_ul_srs_slot - paired_dl_rs_slot,
                "srs_periodicity": int(self.cfg.get("srs_periodicity", 10) or 10),
                "srs_offset": srs_offset,
                "srs_offset_source": (
                    "explicit_config"
                    if explicit_srs_offset is not None
                    else "auto_first_full_ul_slot"
                ),
                "srs_first_ul_opportunity_slot": paired_ul_srs_slot,
                "srs_active_in_slot": link == "BOTH",
                "indexed_slot_rs_schedule_valid": True,
                "rs_opportunity_abstraction_used": False,
                "channel_generation_mode": "internal_sim",
                "time_axis_semantics": "slot_snapshots",
                "channel_est_implementation": "superran-ls-pilot-frequency-estimator-v1",
                "channel_est_observation_model": "coherent_despread_rb_granular_ls",
                "channel_est_pilot_rb_count": int(np.asarray(est_pilot_rb).size),
                "channel_est_full_band_srs": bool(
                    int(np.asarray(est_pilot_rb).size) == n_rb),
                "srs_hop_index": est_hop_index,
                "srs_occasion_index": est_srs_occasion,
                "srs_estimation_period_ms": srs_period_ms,
                "srs_estimation_processing_delay_ms": srs_delay_ms,
                "srs_estimation_timing_source": "csi_aging.srs_occasion_index",
                "srs_measurement_policy": (
                    "occasion_locked_measured_at_srs_time"
                    if est_mode in HOP_EST_MODES else
                    ("perfect_csi" if est_mode == "ideal"
                     else "sound_now_full_band_upper_bound")),
                "srs_measurement_geometry_approximation": (
                    "large_scale_geometry_taken_from_sample_time"
                    if est_mode in HOP_EST_MODES else None),
                "csi_aging_already_in_estimate": bool(est_mode in HOP_EST_MODES),
                "srs_hop_profile": (
                    "superran-c63-b1-bhop0-17x16" if est_hop_index >= 0 else None),
                "channel_est_cold_start": est_cold_start,
                "small_scale_time_model": "continuous_trajectory_jakes_v1",
                "small_scale_seed_scope": "per_trajectory_ue",
                "trajectory_time_s": trajectory_time_s,
                "sample_time_window_s": [
                    trajectory_time_s,
                    trajectory_time_s + n_time * sample_interval_s,
                ],
                "sample_interval_s": sample_interval_s,
                "symbol_grid_approximate": (
                    int(self.cfg.get("num_ofdm_symbols", 14) or 14) < 14
                ),
                "channel_contract": {
                    "reciprocity_contract_version": "superran-tdd-transpose-canonical-v2",
                    "physical_reciprocity": "H_UL = transpose(H_DL)",
                    "canonical_storage": "both links use [time,rb,bs_port,ue_port]",
                    "canonical_ul_equals_dl_at_zero_calibration": True,
                    "rs_opportunity_model": "indexed-slot TDD and periodicity schedule",
                },
            }
            if est_rbg_age_by_slot is not None:
                meta["csi_rbg_age_by_slot"] = np.asarray(est_rbg_age_by_slot)
                meta["srs_occasion_by_slot"] = np.asarray(est_occasion_by_slot)
            if est_rbg_age is not None:
                # **逐 RBG 的 CSI 年龄必须落盘。** 跳频档的陈旧度是烘进 h_est 的，
                # 系统侧没有这一份就只能拿自己的 CsiConfig 重算，而它并不知道
                # 数据是按哪次机会、哪一跳测的——实测会把"只更新了一个子带"
                # 报成"17 个子带全新"。
                meta["csi_rbg_age_occasions"] = list(est_rbg_age)
            paired = link == "BOTH"
            yield ChannelSample(
                h_serving_true=h_dl,
                h_serving_est=h_dl_est,
                h_interferers=h_intf,
                h_ul_cross=h_ul_cross,
                noise_power_dBm=noise_dbm,
                snr_dB=snr_db,
                sir_dB=sir_db,
                sinr_dB=sinr_db,
                ssb_rsrp_dBm=list(rx_all) if measure_ssb else None,
                ssb_rsrq_dB=list(ssb_sinr) if ssb_sinr is not None else None,
                ssb_sinr_dB=ssb_sinr,
                ssb_best_beam_idx=[0] * len(sites) if measure_ssb else None,
                ssb_pcis=[cell.cell_id % 1008 for cell in sites] if measure_ssb else None,
                link=link,
                channel_est_mode=est_mode,
                serving_cell_id=serving,
                ue_position=position,
                channel_model=effective_model,
                tdd_pattern=str(self.cfg.get("tdd_pattern", "DDDSU")),
                slot_duration_s=slot_duration,
                link_pairing="paired" if paired else "single",
                h_ul_true=h_ul if paired else None,
                h_ul_est=h_ul_est if paired else None,
                h_dl_true=h_dl if paired else None,
                h_dl_est=h_dl_est if paired else None,
                ul_sir_dB=ul_measure_sir if paired else None,
                dl_sir_dB=max(sir_db, -49.9) if paired else None,
                num_interfering_ues=int(self.cfg.get("num_interfering_ues", 0) or 0),
                ul_pre_sinr_dB=ul_sinr if paired else None,
                ul_snr_dB=snr_db if paired else None,
                ul_sinr_dB=ul_sinr if paired else None,
                dl_rank=min(n_bs, n_ue),
                meta=meta,
            )


SOURCE_REGISTRY: dict[str, type[InternalSimSource]] = {"internal_sim": InternalSimSource}

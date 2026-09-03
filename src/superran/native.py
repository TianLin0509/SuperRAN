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
from collections.abc import Iterator
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
    **kwargs: Any,
) -> SimpleNamespace:
    """Compact first-party SRS/CSI-RS LS or frequency-LMMSE observer.

    The audit helper intentionally exposes only the estimated channel.  It
    consumes the real pilot RB positions and never substitutes ``h_true`` for
    a missing observation.
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
    n0 = 10.0 ** (-float(snr_dB) / 10.0)
    estimate = np.empty_like(truth)
    grid = np.arange(truth.shape[1])
    for symbol in range(truth.shape[0]):
        observed_symbol = int(symbols[np.argmin(np.abs(symbols - symbol))])
        pilot_values = truth[observed_symbol, pilots].astype(np.complex128)
        if h_interferers is not None:
            interference = np.asarray(h_interferers)
            if interference.size:
                pilot_values = pilot_values + np.mean(interference, axis=0)[observed_symbol, pilots]
        if n0 > _EPS:
            noise = (rng.standard_normal(pilot_values.shape) + 1j * rng.standard_normal(pilot_values.shape))
            pilot_values = pilot_values + math.sqrt(n0 / 2.0) * noise
        if str(est_mode) in {"ls_mmse", "ls_lmmse"}:
            full = lmmse_frequency_interpolate(
                pilot_values,
                pilots,
                grid,
                float(tau_rms_ns) * 1e-9,
                12.0 * float(subcarrier_spacing),
                1.0 / max(n0, _EPS),
                dtype="complex64",
            )
        else:
            full = np.empty((truth.shape[1], *truth.shape[2:]), dtype=np.complex64)
            for port in range(truth.shape[2]):
                for rx in range(truth.shape[3]):
                    values = pilot_values[:, port, rx]
                    full[:, port, rx] = (
                        np.interp(grid, pilots, values.real)
                        + 1j * np.interp(grid, pilots, values.imag)
                    )
        estimate[symbol] = full
    return SimpleNamespace(h_est=estimate)


# ---------------------------------------------------------------------------
# Array, topology and codebook primitives
# ---------------------------------------------------------------------------

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
    """Separable unit-norm response for one polarization block."""
    elevation = np.pi / 2.0 - float(zenith_rad)
    values = []
    for h in range(int(n_h)):
        for v in range(int(n_v)):
            phase = 2.0 * np.pi * (
                float(horizontal_spacing) * h * math.cos(elevation) * math.sin(float(azimuth_rad))
                + float(vertical_spacing) * v * math.sin(elevation)
            )
            values.append(np.exp(1j * phase))
    result = np.asarray(values, dtype=np.complex128)
    return result / math.sqrt(max(result.size, 1))


# ---------------------------------------------------------------------------
# First-party statistical channel source
# ---------------------------------------------------------------------------


@dataclass
class ChannelSample:
    h_serving_true: np.ndarray | None = None
    h_serving_est: np.ndarray | None = None
    h_interferers: np.ndarray | None = None
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

    def _effective_model(self, configured: str, is_los: bool) -> str:
        key = configured.upper().replace("_", "-")
        family = "CDL" if key.startswith("CDL") else "TDL"
        if is_los:
            return key if key in {f"{family}-D", f"{family}-E"} else f"{family}-D"
        return key if key in {f"{family}-A", f"{family}-B", f"{family}-C"} else f"{family}-C"

    def _channel(self, profile: ChannelProfile, rng: np.random.Generator, *,
                 n_time: int, n_rb: int, n_bs: int, n_ue: int, doppler_hz: float,
                 realization_index: int, link_aod_rad: float, link_aoa_rad: float,
                 link_zod_rad: float, link_zoa_rad: float) -> np.ndarray:
        powers = 10.0 ** (profile.powers_dB / 10.0)
        powers /= max(float(np.sum(powers)), _EPS)
        tau_rms = float(self.cfg.get("tau_rms_ns", 300.0) or 300.0) * 1e-9
        delays = profile.delays_norm * tau_rms
        scs = float(self.cfg.get("subcarrier_spacing", 30_000.0) or 30_000.0)
        freq = (np.arange(n_rb, dtype=np.float64) - (n_rb - 1.0) / 2.0) * 12.0 * scs
        interval = float(self.cfg.get("sample_interval_s", 5e-3) or 5e-3)
        times = np.arange(n_time, dtype=np.float64) * interval
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
                    element_spacing = float(
                        bs_subarray.get("ae_vertical_spacing_lambda", 0.67) or 0.67
                    )
                    tilt = -math.radians(
                        float(bs_subarray.get("fixed_downtilt_deg", 0.0) or 0.0)
                    )
                    offsets = np.arange(feed_count, dtype=np.float64) - (feed_count - 1.0) / 2.0
                    feed = np.exp(
                        2j * np.pi * element_spacing * offsets * math.sin(tilt)
                    ) / math.sqrt(feed_count)
                    element_response = np.exp(
                        2j * np.pi * element_spacing * offsets * math.sin(np.pi / 2.0 - zod)
                    )
                    bs_space = bs_space * np.vdot(feed, element_response)
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
        # Large-scale realizations also vary UE-side spatial correlation.  The
        # deterministic cycle is keyed by the global sample index so parallel
        # slicing is exact while Monte-Carlo batches cover both well- and
        # poorly-conditioned channels from their first few observations.
        if n_ue > 1:
            rho = 0.1 + 0.8 * (((int(realization_index) * 3) % 7) / 6.0)
            mixing = (1.0 - rho) * np.eye(n_ue) + rho * np.ones((n_ue, n_ue)) / n_ue
            h = np.einsum("...bu,uv->...bv", h, mixing, optimize=True)
        # Unit average coefficient power keeps link-level SNR semantics stable.
        h /= math.sqrt(max(float(np.mean(np.abs(h) ** 2)), _EPS))
        return h.astype(np.complex64)

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
        keep_interferer_h = bool(
            (self.cfg.get("measurements") or {}).get("interferer_channels", False)
            or self.cfg.get("store_interferer_channels", False)
        )
        bs_ant = dict(self.cfg.get("bs_antenna") or {})
        subarray = dict(bs_ant.get("fixed_vertical_subarray") or {})
        elements_per_port = int(subarray.get("elements_per_rf_port", 1) or 1)

        for local_index in range(self.num_samples):
            global_index = self._offset + local_index
            ue_id = global_index % self.num_ues
            position = positions[ue_id].copy()
            round_index = global_index // self.num_ues
            mobility_mode = str(self.cfg.get("mobility_mode", "static")).strip().lower()
            speed_mps = max(float(self.cfg.get("ue_speed_kmh", 3.0) or 0.0), 0.0) / 3.6
            if mobility_mode != "static" and speed_mps > 0.0:
                heading = math.radians(
                    float(self.cfg.get("ue_heading_deg", self.cfg.get("track_heading_deg", 0.0)) or 0.0)
                )
                travel = speed_mps * float(
                    self.cfg.get("sample_interval_s", 5e-3) or 5e-3
                ) * round_index
                position[0] += travel * math.cos(heading)
                position[1] += travel * math.sin(heading)
            rng_small = np.random.default_rng(np.random.SeedSequence([self._seed, 211, global_index]))
            rng_est = np.random.default_rng(np.random.SeedSequence([self._seed, 307, global_index]))

            site_state: dict[int, tuple[bool, float, float]] = {}
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
                    p_los = min(18.0 / d2 + math.exp(-d2 / 63.0) * (1.0 - 18.0 / d2), 1.0)
                    forced = scenario.endswith("_LOS")
                    qx = int(math.floor(float(position[0]) / 10.0))
                    qy = int(math.floor(float(position[1]) / 10.0))
                    los_rng = np.random.default_rng(
                        _seed_from_parts(self._seed, cell.site_id, qx, qy, 0x10A5)
                    )
                    los = bool(forced or los_rng.random() < p_los)
                    tau_ns = float(self.cfg.get("tau_rms_ns", 100.0 if los else 300.0) or 300.0)
                    lsp_rng = np.random.default_rng(
                        _seed_from_parts(self._seed, cell.site_id, qx, qy, 0x15F0)
                    )
                    sf = float(lsp_rng.normal(0.0, 2.0 if los else 3.0))
                    site_state[cell.site_id] = (los, tau_ns, sf)
                los, tau_ns, sf = site_state[cell.site_id]
                bearing = math.degrees(math.atan2(delta[1], delta[0]))
                offset = _circular_delta_deg(bearing, cell.azimuth_deg)
                effective_array = (
                    str(self.cfg.get("antenna_model_mode", "legacy_64"))
                    == "effective_subarray"
                )
                element_gain = (
                    8.0 + 10.0 * math.log10(max(elements_per_port, 1))
                    if effective_array else 0.0
                )
                gain = element_gain - min(12.0 * (offset / 65.0) ** 2, 30.0)
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
            h_dl = self._channel(profile, rng_small, n_time=n_time, n_rb=n_rb,
                                 n_bs=n_bs, n_ue=n_ue, doppler_hz=doppler,
                                 realization_index=global_index,
                                 link_aod_rad=link_aod, link_aoa_rad=link_aoa,
                                 link_zod_rad=link_zod, link_zoa_rad=link_zoa)

            est_mode = str(self.cfg.get("channel_est_mode", "ls_linear"))
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
                sigma = 10.0 ** (-est_snr / 20.0)
                noise_dl = (rng_est.standard_normal(h_dl.shape) + 1j * rng_est.standard_normal(h_dl.shape)) / math.sqrt(2)
                noise_ul = (rng_est.standard_normal(h_dl.shape) + 1j * rng_est.standard_normal(h_dl.shape)) / math.sqrt(2)
                h_dl_est = (h_dl + sigma * noise_dl).astype(np.complex64)
                h_ul_est = (h_dl + sigma * noise_ul).astype(np.complex64)
            h_ul = h_dl.copy()  # canonical v2: physical transpose, same stored BS/UE tensor
            site_models = [
                self._effective_model(configured_model, site_state[cell.site_id][0])
                for cell in sites
            ]

            h_intf = None
            if keep_interferer_h and len(sites) > 1:
                rows = []
                max_cells = max(
                    int(
                        self.cfg.get("max_per_ue_intf_cells", len(sites) - 1)
                        or (len(sites) - 1)
                    ),
                    0,
                )
                for k in range(len(sites)):
                    if max_cells <= 0:
                        break
                    if k == serving:
                        continue
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
                        np.random.SeedSequence([self._seed, 401, global_index, k])
                    )
                    cross = self._channel(
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
                    )
                    rows.append((cross * scale).astype(np.complex64))
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
            pattern_name = str(self.cfg.get("tdd_pattern", "DDDSU"))
            try:
                slots_pattern = get_tdd_pattern(pattern_name).slots
            except ValueError:
                slots_pattern = "".join(ch for ch in pattern_name if ch in "DSU") or "D"
            paired_dl_rs_slot = next(
                (idx for idx, direction in enumerate(slots_pattern) if direction in "DS"),
                0,
            )
            paired_ul_srs_slot = next(
                (idx for idx, direction in enumerate(slots_pattern) if direction in "US"),
                0,
            )
            explicit_srs_offset = self.cfg.get("srs_offset")
            srs_offset = (
                int(explicit_srs_offset)
                if explicit_srs_offset is not None
                else paired_ul_srs_slot
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
            paired = link == "BOTH"
            yield ChannelSample(
                h_serving_true=h_dl,
                h_serving_est=h_dl_est,
                h_interferers=h_intf,
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

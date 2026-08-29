"""Auditable UL-SRS link-budget, receiver-noise and Pre-SINR metrics.

Three power references are kept separate:

* thermal noise per active SRS RE (one subcarrier bandwidth);
* thermal noise per RB (12 subcarriers);
* received SRS power per actually transmitted comb RE.

Pre-SINR is an estimation-quality ratio, ``sum(|H_true|^2)`` divided by
``sum(|H_true-H_est|^2)``.  Powers are accumulated in the linear domain and
the optional temporal IIR also runs in the linear domain.  No fixed absolute
power epsilon is added, so scaling both channels by the same non-zero factor
does not change the result.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import hardware as hw
from .srs_waveform import srs_comb_indices_for_rbs

THERMAL_NOISE_PSD_DBM_HZ = -174.0
DEFAULT_RRU_NOISE_FIGURE_DB = 2.0
DEFAULT_TDD_RX_LOSS_DB = 1.0
DEFAULT_IIR_ALPHA = 0.2
DEFAULT_MIN_DB = -50.0
DEFAULT_MAX_DB = 50.0

__all__ = [
    "DEFAULT_IIR_ALPHA",
    "DEFAULT_MAX_DB",
    "DEFAULT_MIN_DB",
    "DEFAULT_RRU_NOISE_FIGURE_DB",
    "DEFAULT_TDD_RX_LOSS_DB",
    "LinearPreSinrIIR",
    "SrsLinkBudgetResult",
    "SrsNoiseLevels",
    "SrsPreSinrResult",
    "THERMAL_NOISE_PSD_DBM_HZ",
    "calibrated_nr_noise_levels",
    "combine_snr_sir_db",
    "dbm_to_mw",
    "open_loop_ul_tx_power_dbm",
    "presinr_db",
    "presinr_linear",
    "presinr_per_rb_db",
    "presinr_per_slot_db",
    "presinr_summary",
    "ratio_to_db",
    "srs_link_budget",
    "thermal_noise_dbm",
]


def _finite(name: str, value: float, *, positive: bool = False) -> float:
    out = float(value)
    if not math.isfinite(out) or (positive and out <= 0.0):
        relation = "positive " if positive else ""
        raise ValueError(f"{name} must be a finite {relation}value")
    return out


def dbm_to_mw(value_dbm: float) -> float:
    """Convert dBm to mW without changing the reference bandwidth."""
    value = _finite("value_dbm", value_dbm)
    return float(10.0 ** (value / 10.0))


def thermal_noise_dbm(
    bandwidth_hz: float,
    *,
    receiver_noise_figure_db: float,
    receiver_loss_db: float = 0.0,
    noise_psd_dbm_hz: float = THERMAL_NOISE_PSD_DBM_HZ,
) -> float:
    """Return ``kTB + receiver NF + receive loss`` for an explicit bandwidth."""
    bandwidth = _finite("bandwidth_hz", bandwidth_hz, positive=True)
    noise_figure = _finite("receiver_noise_figure_db", receiver_noise_figure_db)
    loss = _finite("receiver_loss_db", receiver_loss_db)
    psd = _finite("noise_psd_dbm_hz", noise_psd_dbm_hz)
    if noise_figure < 0.0 or loss < 0.0:
        raise ValueError("receiver noise figure/loss must be non-negative")
    return float(psd + 10.0 * math.log10(bandwidth) + noise_figure + loss)


@dataclass(frozen=True)
class SrsNoiseLevels:
    noise_psd_dbm_hz: float
    noise_per_re_dbm: float
    noise_per_rb_dbm: float
    noise_active_srs_dbm: float
    noise_full_allocation_dbm: float
    active_srs_re_count: int
    allocated_rb: int
    effective_receiver_nf_db: float

    @property
    def noise_per_re_mw(self) -> float:
        return dbm_to_mw(self.noise_per_re_dbm)

    def as_dict(self) -> dict[str, Any]:
        return {
            "noise_psd_dbm_hz": self.noise_psd_dbm_hz,
            "noise_per_re_dbm": self.noise_per_re_dbm,
            "noise_per_re_mw": self.noise_per_re_mw,
            "noise_per_rb_dbm": self.noise_per_rb_dbm,
            "noise_active_srs_dbm": self.noise_active_srs_dbm,
            "noise_full_allocation_dbm": self.noise_full_allocation_dbm,
            "active_srs_re_count": self.active_srs_re_count,
            "allocated_rb": self.allocated_rb,
            "effective_receiver_nf_db": self.effective_receiver_nf_db,
        }


def calibrated_nr_noise_levels(
    *,
    rb_indices: Sequence[int] | np.ndarray,
    subcarrier_spacing_hz: float = float(hw.COMPANY_SCS_HZ),
    k_tc: int = 2,
    comb_offset: int = 0,
    rru_noise_figure_db: float = DEFAULT_RRU_NOISE_FIGURE_DB,
    tdd_rx_loss_db: float = DEFAULT_TDD_RX_LOSS_DB,
) -> SrsNoiseLevels:
    """Return per-RE, per-RB, active-SRS and full-allocation noise anchors."""
    rbs = np.asarray(rb_indices)
    tones = srs_comb_indices_for_rbs(
        rbs, k_tc=int(k_tc), comb_offset=int(comb_offset)
    )
    allocated_rb = int(rbs.size)
    active_re = int(tones.size)
    scs = _finite("subcarrier_spacing_hz", subcarrier_spacing_hz, positive=True)
    nf = _finite("rru_noise_figure_db", rru_noise_figure_db)
    loss = _finite("tdd_rx_loss_db", tdd_rx_loss_db)
    if nf < 0.0 or loss < 0.0:
        raise ValueError("receiver noise figure/loss must be non-negative")
    per_re = thermal_noise_dbm(
        scs, receiver_noise_figure_db=nf, receiver_loss_db=loss
    )
    per_rb = thermal_noise_dbm(
        hw.COMPANY_SC_PER_RB * scs,
        receiver_noise_figure_db=nf,
        receiver_loss_db=loss,
    )
    active = per_re + 10.0 * math.log10(active_re)
    full = per_rb + 10.0 * math.log10(allocated_rb)
    return SrsNoiseLevels(
        noise_psd_dbm_hz=float(THERMAL_NOISE_PSD_DBM_HZ),
        noise_per_re_dbm=per_re,
        noise_per_rb_dbm=per_rb,
        noise_active_srs_dbm=float(active),
        noise_full_allocation_dbm=float(full),
        active_srs_re_count=active_re,
        allocated_rb=allocated_rb,
        effective_receiver_nf_db=float(nf + loss),
    )


def open_loop_ul_tx_power_dbm(
    pathloss_db: float,
    allocated_rb: int,
    *,
    ue_max_power_dbm: float = 23.0,
    p0_dbm: float = -96.0,
    alpha: float = 0.8,
) -> float:
    """TS 38.213-style engineering open-loop UL power-control equation.

    ``p0_dbm`` and ``alpha`` are configurable engineering parameters, not a
    claim that the standard mandates these defaults.
    """
    if isinstance(allocated_rb, (bool, np.bool_)):
        raise ValueError("allocated_rb must be a positive integer")
    rb_value = float(allocated_rb)
    if not math.isfinite(rb_value) or rb_value != math.floor(rb_value):
        raise ValueError("allocated_rb must be a positive integer")
    rb = int(rb_value)
    if rb < 1:
        raise ValueError("allocated_rb must be positive")
    pathloss = _finite("pathloss_db", pathloss_db)
    if isinstance(alpha, (bool, np.bool_)):
        raise ValueError("alpha must be in [0, 1]")
    alpha_value = _finite("alpha", alpha)
    if not 0.0 <= alpha_value <= 1.0:
        raise ValueError("alpha must be in [0, 1]")
    requested = (
        _finite("p0_dbm", p0_dbm)
        + alpha_value * pathloss
        + 10.0 * math.log10(rb)
    )
    return float(min(_finite("ue_max_power_dbm", ue_max_power_dbm), requested))


@dataclass(frozen=True)
class SrsLinkBudgetResult:
    ue_tx_power_dbm: float
    received_total_dbm: float
    received_per_active_re_dbm: float
    snr_per_active_re_db: float
    pathloss_db: float
    antenna_gain_db: float
    p0_dbm: float
    alpha: float
    k_tc: int
    rb_indices: tuple[int, ...]
    noise: SrsNoiseLevels

    def as_dict(self) -> dict[str, Any]:
        return {
            "ue_tx_power_dbm": self.ue_tx_power_dbm,
            "received_total_dbm": self.received_total_dbm,
            "received_per_active_re_dbm": self.received_per_active_re_dbm,
            "snr_per_active_re_db": self.snr_per_active_re_db,
            "pathloss_db": self.pathloss_db,
            "antenna_gain_db": self.antenna_gain_db,
            "p0_dbm": self.p0_dbm,
            "alpha": self.alpha,
            "k_tc": self.k_tc,
            "rb_indices": list(self.rb_indices),
            "noise": self.noise.as_dict(),
            "power_reference": (
                "total UE SRS power split uniformly across actual active comb REs"
            ),
        }


def srs_link_budget(
    *,
    pathloss_db: float,
    rb_indices: Sequence[int] | np.ndarray,
    k_tc: int = 2,
    comb_offset: int = 0,
    antenna_gain_db: float = 0.0,
    ue_max_power_dbm: float = 23.0,
    p0_dbm: float = -96.0,
    alpha: float = 0.8,
    subcarrier_spacing_hz: float = float(hw.COMPANY_SCS_HZ),
    rru_noise_figure_db: float = DEFAULT_RRU_NOISE_FIGURE_DB,
    tdd_rx_loss_db: float = DEFAULT_TDD_RX_LOSS_DB,
) -> SrsLinkBudgetResult:
    """Compute one SRS occasion's explicit TX/RX/noise reference planes."""
    raw_rbs = np.asarray(rb_indices)
    as_float = np.asarray(raw_rbs, dtype=np.float64).reshape(-1)
    if (
        as_float.size < 1
        or not np.all(np.isfinite(as_float))
        or not np.all(as_float == np.floor(as_float))
    ):
        raise ValueError("rb_indices must contain finite integers")
    rbs = as_float.astype(np.int64)
    noise = calibrated_nr_noise_levels(
        rb_indices=rbs,
        subcarrier_spacing_hz=subcarrier_spacing_hz,
        k_tc=k_tc,
        comb_offset=comb_offset,
        rru_noise_figure_db=rru_noise_figure_db,
        tdd_rx_loss_db=tdd_rx_loss_db,
    )
    tx = open_loop_ul_tx_power_dbm(
        pathloss_db,
        int(rbs.size),
        ue_max_power_dbm=ue_max_power_dbm,
        p0_dbm=p0_dbm,
        alpha=alpha,
    )
    gain = _finite("antenna_gain_db", antenna_gain_db)
    pathloss = _finite("pathloss_db", pathloss_db)
    received_total = tx - pathloss + gain
    received_per_re = received_total - 10.0 * math.log10(
        noise.active_srs_re_count
    )
    return SrsLinkBudgetResult(
        ue_tx_power_dbm=tx,
        received_total_dbm=float(received_total),
        received_per_active_re_dbm=float(received_per_re),
        snr_per_active_re_db=float(received_per_re - noise.noise_per_re_dbm),
        pathloss_db=pathloss,
        antenna_gain_db=gain,
        p0_dbm=float(p0_dbm),
        alpha=float(alpha),
        k_tc=int(k_tc),
        rb_indices=tuple(int(value) for value in rbs),
        noise=noise,
    )


def combine_snr_sir_db(snr_db: float, sir_db: float | None) -> float:
    """Combine same-reference S/N and S/I into S/(N+I) in linear power."""
    snr = _finite("snr_db", snr_db)
    snr_linear = 10.0 ** (snr / 10.0)
    if sir_db is None:
        return snr
    sir_raw = float(sir_db)
    if math.isinf(sir_raw):
        return snr if sir_raw > 0.0 else float("-inf")
    sir = _finite("sir_db", sir_raw)
    sir_linear = 10.0 ** (sir / 10.0)
    combined = 1.0 / (1.0 / snr_linear + 1.0 / sir_linear)
    return float(10.0 * math.log10(combined))


def _channels(
    h_true: np.ndarray,
    h_est: np.ndarray,
    *,
    allow_slots: bool,
) -> tuple[np.ndarray, np.ndarray]:
    true = np.asarray(h_true)
    estimate = np.asarray(h_est)
    if true.shape != estimate.shape:
        raise ValueError(
            f"true/estimated channel shapes differ: {true.shape} vs {estimate.shape}"
        )
    expected = (3, 4) if allow_slots else (3,)
    if true.ndim not in expected or any(size < 1 for size in true.shape):
        layouts = "[RB,BS,UE] or [slot,RB,BS,UE]" if allow_slots else "[RB,BS,UE]"
        raise ValueError(f"channel must have layout {layouts}; got {true.shape}")
    if not np.all(np.isfinite(true)) or not np.all(np.isfinite(estimate)):
        raise ValueError("true/estimated channel contains NaN or Inf")
    return (
        true.astype(np.complex128, copy=False),
        estimate.astype(np.complex128, copy=False),
    )


def _validate_bounds(min_db: float, max_db: float) -> tuple[float, float]:
    low = _finite("min_db", min_db)
    high = _finite("max_db", max_db)
    if low >= high:
        raise ValueError("min_db must be lower than max_db")
    return low, high


def _bounded_ratio(
    signal_power: np.ndarray | float,
    error_power: np.ndarray | float,
    *,
    min_db: float,
    max_db: float,
) -> np.ndarray:
    low, high = _validate_bounds(min_db, max_db)
    signal = np.maximum(np.asarray(signal_power, dtype=np.float64), 0.0)
    error = np.maximum(np.asarray(error_power, dtype=np.float64), 0.0)
    signal, error = np.broadcast_arrays(signal, error)
    ratio = np.empty(signal.shape, dtype=np.float64)
    positive_error = error > 0.0
    np.divide(signal, error, out=ratio, where=positive_error)
    ratio[~positive_error] = np.inf
    ratio[signal <= 0.0] = 0.0
    return np.clip(ratio, 10.0 ** (low / 10.0), 10.0 ** (high / 10.0))


def ratio_to_db(
    ratio: np.ndarray | float,
    *,
    min_db: float = DEFAULT_MIN_DB,
    max_db: float = DEFAULT_MAX_DB,
) -> np.ndarray | float:
    low, high = _validate_bounds(min_db, max_db)
    values = np.asarray(ratio, dtype=np.float64)
    bounded = np.clip(values, 10.0 ** (low / 10.0), 10.0 ** (high / 10.0))
    result = np.clip(10.0 * np.log10(bounded), low, high)
    return float(result) if result.ndim == 0 else result


def presinr_linear(h_true: np.ndarray, h_est: np.ndarray) -> float:
    true, estimate = _channels(h_true, h_est, allow_slots=True)
    signal = float(np.sum(np.abs(true) ** 2, dtype=np.float64))
    error = float(np.sum(np.abs(true - estimate) ** 2, dtype=np.float64))
    return float(
        _bounded_ratio(
            signal, error, min_db=DEFAULT_MIN_DB, max_db=DEFAULT_MAX_DB
        )
    )


def presinr_db(
    h_true: np.ndarray,
    h_est: np.ndarray,
    *,
    min_db: float = DEFAULT_MIN_DB,
    max_db: float = DEFAULT_MAX_DB,
) -> float:
    true, estimate = _channels(h_true, h_est, allow_slots=True)
    signal = float(np.sum(np.abs(true) ** 2, dtype=np.float64))
    error = float(np.sum(np.abs(true - estimate) ** 2, dtype=np.float64))
    return float(
        ratio_to_db(
            _bounded_ratio(signal, error, min_db=min_db, max_db=max_db),
            min_db=min_db,
            max_db=max_db,
        )
    )


def presinr_per_rb_db(
    h_true: np.ndarray,
    h_est: np.ndarray,
    *,
    min_db: float = DEFAULT_MIN_DB,
    max_db: float = DEFAULT_MAX_DB,
) -> np.ndarray:
    true, estimate = _channels(h_true, h_est, allow_slots=True)
    if true.ndim == 3:
        true = true[np.newaxis]
        estimate = estimate[np.newaxis]
    signal = np.sum(np.abs(true) ** 2, axis=(0, 2, 3), dtype=np.float64)
    error = np.sum(
        np.abs(true - estimate) ** 2, axis=(0, 2, 3), dtype=np.float64
    )
    return np.asarray(
        ratio_to_db(
            _bounded_ratio(signal, error, min_db=min_db, max_db=max_db),
            min_db=min_db,
            max_db=max_db,
        ),
        dtype=np.float32,
    )


@dataclass
class LinearPreSinrIIR:
    alpha: float = DEFAULT_IIR_ALPHA
    min_db: float = DEFAULT_MIN_DB
    max_db: float = DEFAULT_MAX_DB
    state_linear: float | None = None

    def __post_init__(self) -> None:
        self.alpha = float(self.alpha)
        if not 0.0 < self.alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self.min_db, self.max_db = _validate_bounds(self.min_db, self.max_db)

    def update_ratio(self, ratio: float) -> float:
        bounded = float(
            np.clip(
                float(ratio),
                10.0 ** (self.min_db / 10.0),
                10.0 ** (self.max_db / 10.0),
            )
        )
        self.state_linear = (
            bounded
            if self.state_linear is None
            else self.alpha * bounded + (1.0 - self.alpha) * self.state_linear
        )
        return float(
            ratio_to_db(
                self.state_linear, min_db=self.min_db, max_db=self.max_db
            )
        )

    def update(self, h_true_slot: np.ndarray, h_est_slot: np.ndarray) -> float:
        true, estimate = _channels(h_true_slot, h_est_slot, allow_slots=False)
        signal = float(np.sum(np.abs(true) ** 2, dtype=np.float64))
        error = float(np.sum(np.abs(true - estimate) ** 2, dtype=np.float64))
        ratio = float(
            _bounded_ratio(
                signal, error, min_db=self.min_db, max_db=self.max_db
            )
        )
        return self.update_ratio(ratio)


def presinr_per_slot_db(
    h_true: np.ndarray,
    h_est: np.ndarray,
    *,
    alpha: float = DEFAULT_IIR_ALPHA,
    min_db: float = DEFAULT_MIN_DB,
    max_db: float = DEFAULT_MAX_DB,
) -> np.ndarray:
    true, estimate = _channels(h_true, h_est, allow_slots=True)
    if true.ndim == 3:
        true = true[np.newaxis]
        estimate = estimate[np.newaxis]
    state = LinearPreSinrIIR(alpha=alpha, min_db=min_db, max_db=max_db)
    return np.asarray(
        [state.update(true[slot], estimate[slot]) for slot in range(true.shape[0])],
        dtype=np.float32,
    )


@dataclass(frozen=True)
class SrsPreSinrResult:
    instantaneous_wideband_db: float
    filtered_final_db: float
    per_slot_filtered_db: np.ndarray
    per_rb_db: np.ndarray
    signal_power_linear: float
    error_power_linear: float
    alpha: float
    bounds_db: tuple[float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "instantaneous_wideband_db": self.instantaneous_wideband_db,
            "filtered_final_db": self.filtered_final_db,
            "per_slot_filtered_db": self.per_slot_filtered_db.tolist(),
            "per_rb_db": self.per_rb_db.tolist(),
            "signal_power_linear": self.signal_power_linear,
            "error_power_linear": self.error_power_linear,
            "alpha": self.alpha,
            "bounds_db": list(self.bounds_db),
            "definition": "sum(|H_true|^2) / sum(|H_true-H_est|^2)",
            "temporal_filter_domain": "linear ratio",
            "fixed_absolute_power_epsilon": None,
        }


def presinr_summary(
    h_true: np.ndarray,
    h_est: np.ndarray,
    *,
    alpha: float = DEFAULT_IIR_ALPHA,
    min_db: float = DEFAULT_MIN_DB,
    max_db: float = DEFAULT_MAX_DB,
) -> SrsPreSinrResult:
    true, estimate = _channels(h_true, h_est, allow_slots=True)
    signal = float(np.sum(np.abs(true) ** 2, dtype=np.float64))
    error = float(np.sum(np.abs(true - estimate) ** 2, dtype=np.float64))
    slots = presinr_per_slot_db(
        true, estimate, alpha=alpha, min_db=min_db, max_db=max_db
    )
    return SrsPreSinrResult(
        instantaneous_wideband_db=presinr_db(
            true, estimate, min_db=min_db, max_db=max_db
        ),
        filtered_final_db=float(slots[-1]),
        per_slot_filtered_db=slots,
        per_rb_db=presinr_per_rb_db(
            true, estimate, min_db=min_db, max_db=max_db
        ),
        signal_power_linear=signal,
        error_power_linear=error,
        alpha=float(alpha),
        bounds_db=(float(min_db), float(max_db)),
    )

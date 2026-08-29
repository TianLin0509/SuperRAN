"""Allocator-driven SRS waveform observation for the fixed TDD carrier.

The resource allocator answers *where and when* a UE sounds.  This module
answers what the victim gNB receives on those resource elements:

``assignment -> low-PAPR/ZC sequence -> UL channel -> coherent interferers
             -> thermal noise -> LS de-spreading -> delay gate -> RB estimate``

The public channel input is deliberately explicit.  An interferer's channel
must be the UL channel from that UE to the victim gNB; a downlink channel from
an interfering BS to the desired UE is a different link and is rejected by
the API contract rather than silently reused.

Only the validated 100 MHz / 30 kHz / 272-RB SuperRAN carrier is bridged to the
allocator.  The receiver helpers keep K_TC={2,4,8} semantics so the cyclic
shift limits remain protocol-correct, but the current product assignment uses
K_TC=2 and two ports per occasion.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import hardware as hw
from . import physical
from .srs_resource import SrsResourceAssignment, SrsTransmissionLeg

_EPS = 1e-30

__all__ = [
    "SrsUlEvidence",
    "SrsWaveformConfig",
    "SrsWaveformObservation",
    "SrsWaveformPairObservation",
    "SrsWaveformSignal",
    "active_leg_at_slot",
    "assignment_rb_indices",
    "observe_srs_leg",
    "simulate_srs_pair",
    "srs_comb_indices_for_rbs",
    "srs_n_cs_max",
    "srs_port_sequences",
]


def _strict_int(name: str, value: int, *, minimum: int = 0) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    out = int(value)
    if out < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return out


def _finite_nonnegative(name: str, value: float, *, positive: bool = False) -> float:
    out = float(value)
    if not np.isfinite(out) or out < 0.0 or (positive and out <= 0.0):
        relation = "> 0" if positive else ">= 0"
        raise ValueError(f"{name} must be finite and {relation}")
    return out


def _ratio_db(numerator: float, denominator: float) -> float:
    if numerator <= 0.0:
        return float("-inf")
    if denominator <= 0.0:
        return float("inf")
    return float(10.0 * np.log10(numerator / denominator))


def srs_n_cs_max(k_tc: int) -> int:
    """Maximum cyclic-shift count from TS 38.211 Table 6.4.1.4.2-1."""
    comb = _strict_int("k_tc", k_tc, minimum=1)
    try:
        return {2: 8, 4: 12, 8: 6}[comb]
    except KeyError as exc:
        raise ValueError(f"k_tc must be 2, 4 or 8; got {k_tc}") from exc


def srs_comb_indices_for_rbs(
    rb_indices: Sequence[int] | np.ndarray,
    *,
    k_tc: int = 2,
    comb_offset: int = 0,
) -> np.ndarray:
    """Return ascending absolute carrier-subcarrier indices for selected RBs.

    The comb condition is applied on the absolute carrier grid, not on a
    compact vector.  This matters for K_TC=8, where consecutive 12-tone RBs
    alternate between one and two active tones.
    """
    comb = _strict_int("k_tc", k_tc, minimum=1)
    srs_n_cs_max(comb)  # validates the supported comb values
    rbs = np.asarray(rb_indices)
    if rbs.ndim != 1 or rbs.size < 1:
        raise ValueError("rb_indices must be a non-empty one-dimensional array")
    if not np.issubdtype(rbs.dtype, np.integer):
        as_float = np.asarray(rbs, dtype=np.float64)
        if not np.all(np.isfinite(as_float)) or not np.all(as_float == np.floor(as_float)):
            raise ValueError("rb_indices must contain finite integers")
        rbs = as_float.astype(np.int64)
    else:
        rbs = rbs.astype(np.int64, copy=False)
    if np.any(rbs < 0) or np.any(rbs >= hw.COMPANY_NUM_RB):
        raise ValueError(
            f"rb_indices must stay inside the 272-RB carrier; got {rbs.tolist()}"
        )
    if np.unique(rbs).size != rbs.size:
        raise ValueError("rb_indices must not contain duplicates")
    phase = _strict_int("comb_offset", comb_offset) % comb
    tones: list[np.ndarray] = []
    for rb in np.sort(rbs):
        start = int(rb) * hw.COMPANY_SC_PER_RB
        stop = start + hw.COMPANY_SC_PER_RB
        first = start + (phase - start) % comb
        tones.append(np.arange(first, stop, comb, dtype=np.int64))
    return np.concatenate(tones)


def assignment_rb_indices(
    assignment: SrsResourceAssignment,
    occurrence_index: int,
) -> np.ndarray:
    """Map one assignment occurrence to its absolute carrier RB indices.

    One occurrence is one complete two-leg 2T4R pair.  Both legs therefore
    return the same RB set, and the 17-hop index advances only on the next
    occurrence.
    """
    occurrence = _strict_int("occurrence_index", occurrence_index)
    if not bool(assignment.hopping):
        return np.arange(hw.COMPANY_NUM_RB, dtype=np.int64)
    phase = int(assignment.frequency_resource_id)
    if not 0 <= phase < hw.COMPANY_NUM_RBG:
        raise ValueError(
            f"frequency_resource_id must be 0..{hw.COMPANY_NUM_RBG - 1}; got {phase}"
        )
    base = tuple(int(x) for x in hw.COMPANY_SRS_17_HOP_ORDER_RBG)
    rotated = base[phase:] + base[:phase]
    rbg = int(rotated[occurrence % len(rotated)])
    start = rbg * hw.COMPANY_RB_PER_RBG
    return np.arange(start, start + hw.COMPANY_RB_PER_RBG, dtype=np.int64)


def active_leg_at_slot(
    assignment: SrsResourceAssignment,
    absolute_slot: int,
) -> tuple[SrsTransmissionLeg, int] | None:
    """Return ``(leg, occurrence_index)`` when the assignment transmits."""
    slot = _strict_int("absolute_slot", absolute_slot)
    hits: list[tuple[SrsTransmissionLeg, int]] = []
    for leg in assignment.legs:
        delta = slot - int(leg.offset_slots)
        if delta >= 0 and delta % int(assignment.period_slots) == 0:
            hits.append((leg, delta // int(assignment.period_slots)))
    if len(hits) > 1:
        raise RuntimeError(
            "one SRS assignment activates multiple antenna-switching legs in the same slot"
        )
    return hits[0] if hits else None


@dataclass(frozen=True)
class SrsWaveformConfig:
    """Receiver and impairment settings for one SRS waveform observation.

    Powers use one common linear normalisation.  They are absolute mW only
    when the supplied channel contains absolute voltage gain; otherwise they
    remain auditable relative powers and all reported ratios are still valid.
    """

    subcarrier_spacing_hz: float = float(hw.COMPANY_SCS_HZ)
    k_tc: int = 2
    fft_size: int = 4096
    noise_power_linear: float = 1e-3
    receiver_tau_rms_ns: float = 100.0
    delay_window_sigma: float = 6.0
    apply_delay_gate: bool = True
    group_hopping: bool = False
    sequence_hopping: bool = False
    seed: int = 0

    def __post_init__(self) -> None:
        scs = _finite_nonnegative(
            "subcarrier_spacing_hz", self.subcarrier_spacing_hz, positive=True
        )
        srs_n_cs_max(self.k_tc)
        fft_size = _strict_int("fft_size", self.fft_size, minimum=1)
        if fft_size < hw.COMPANY_NUM_RB * hw.COMPANY_SC_PER_RB:
            raise ValueError(
                "fft_size must contain all 3264 active carrier subcarriers"
            )
        if fft_size & (fft_size - 1):
            raise ValueError("fft_size must be a power of two")
        _finite_nonnegative("noise_power_linear", self.noise_power_linear, positive=True)
        _finite_nonnegative("receiver_tau_rms_ns", self.receiver_tau_rms_ns)
        _finite_nonnegative("delay_window_sigma", self.delay_window_sigma, positive=True)
        if not isinstance(self.apply_delay_gate, (bool, np.bool_)):
            raise ValueError("apply_delay_gate must be boolean")
        if not isinstance(self.group_hopping, (bool, np.bool_)):
            raise ValueError("group_hopping must be boolean")
        if not isinstance(self.sequence_hopping, (bool, np.bool_)):
            raise ValueError("sequence_hopping must be boolean")
        if self.group_hopping and self.sequence_hopping:
            raise ValueError("group_hopping and sequence_hopping are mutually exclusive")
        _strict_int("seed", self.seed)
        if not np.isclose(scs, hw.COMPANY_SCS_HZ, rtol=0.0, atol=1e-9):
            raise ValueError("the allocator bridge currently supports only 30 kHz SCS")


@dataclass(frozen=True)
class SrsWaveformSignal:
    """One UE transmission as observed at the victim gNB.

    ``channel_ul_rb`` is ``[272 RB, victim_gNB_rx, 4 UE ports]``.  For an
    interferer it is specifically the cross-link from that interfering UE to
    the victim gNB, even though ``assignment.cell_id`` names the UE's serving
    cell.
    """

    assignment: SrsResourceAssignment
    channel_ul_rb: np.ndarray
    n_srs_id: int
    tx_power_linear: float = 1.0
    timing_offset_s: float = 0.0
    cfo_hz: float = 0.0
    label: str = ""


@dataclass(frozen=True)
class SrsUlEvidence:
    """Raw pre-despreading UL interference evidence with stable axes."""

    interference_power_per_slot_rb: np.ndarray
    noise_power_linear: float
    srs_slot_indices: tuple[int, ...]
    srs_rb_indices: tuple[int, ...]
    schema: str = "superran-srs-ul-iot-v1"

    def __post_init__(self) -> None:
        values = np.asarray(self.interference_power_per_slot_rb, dtype=np.float64)
        if values.ndim != 2 or min(values.shape, default=0) < 1:
            raise ValueError(
                "interference_power_per_slot_rb must be non-empty [slot, RB]"
            )
        if not np.all(np.isfinite(values)) or np.any(values < 0.0):
            raise ValueError("UL interference evidence must be finite and non-negative")
        _finite_nonnegative("noise_power_linear", self.noise_power_linear, positive=True)
        if len(self.srs_slot_indices) != values.shape[0]:
            raise ValueError("srs_slot_indices must match the evidence slot axis")
        if len(self.srs_rb_indices) != values.shape[1]:
            raise ValueError("srs_rb_indices must match the evidence RB axis")
        if len(set(self.srs_slot_indices)) != len(self.srs_slot_indices):
            raise ValueError("srs_slot_indices must be unique")
        if len(set(self.srs_rb_indices)) != len(self.srs_rb_indices):
            raise ValueError("srs_rb_indices must be unique")

    @property
    def ul_iot_db_per_slot(self) -> np.ndarray:
        interference = np.mean(
            np.asarray(self.interference_power_per_slot_rb, dtype=np.float64), axis=1
        )
        noise = float(self.noise_power_linear)
        return 10.0 * np.log10((interference + noise) / noise)

    @property
    def ul_iot_db(self) -> float:
        """Final evidence row, 10*log10((I+N)/N), matching field practice."""
        return float(self.ul_iot_db_per_slot[-1])

    def fingerprint(self) -> str:
        metadata = json.dumps(
            {
                "schema": self.schema,
                "noise_power_linear": float(self.noise_power_linear),
                "srs_slot_indices": list(self.srs_slot_indices),
                "srs_rb_indices": list(self.srs_rb_indices),
                "shape": list(np.asarray(self.interference_power_per_slot_rb).shape),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        values = np.ascontiguousarray(
            self.interference_power_per_slot_rb, dtype="<f8"
        ).tobytes()
        return hashlib.sha256(metadata + b"\0" + values).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ul_iot_dB": self.ul_iot_db,
            "ul_iot_dB_per_slot": self.ul_iot_db_per_slot.tolist(),
            "ul_iot_interference_power_per_slot_rb": np.asarray(
                self.interference_power_per_slot_rb, dtype=np.float64
            ).tolist(),
            "ul_iot_noise_power_linear": float(self.noise_power_linear),
            "ul_iot_srs_slot_indices": list(self.srs_slot_indices),
            "ul_iot_srs_rb_indices": list(self.srs_rb_indices),
            "sha256": self.fingerprint(),
        }

    def write_npz(self, path: str | Path) -> dict[str, Any]:
        """Atomically write a compact, independently recomputable sidecar."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with temporary.open("wb") as handle:
                np.savez_compressed(
                    handle,
                    schema=np.asarray(self.schema),
                    interference_power_per_slot_rb=np.asarray(
                        self.interference_power_per_slot_rb, dtype=np.float64
                    ),
                    noise_power_linear=np.asarray(
                        self.noise_power_linear, dtype=np.float64
                    ),
                    slot_indices=np.asarray(self.srs_slot_indices, dtype=np.int64),
                    rb_indices=np.asarray(self.srs_rb_indices, dtype=np.int64),
                    reported_iot_db=np.asarray(self.ul_iot_db, dtype=np.float64),
                    evidence_sha256=np.asarray(self.fingerprint()),
                )
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        file_sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
        return {
            "schema": self.schema,
            "path": str(destination.resolve()),
            "bytes": destination.stat().st_size,
            "shape": list(np.asarray(self.interference_power_per_slot_rb).shape),
            "reported_iot_db": self.ul_iot_db,
            "evidence_sha256": self.fingerprint(),
            "file_sha256": file_sha256,
        }

    @classmethod
    def load_npz(cls, path: str | Path) -> SrsUlEvidence:
        """Load a sidecar and fail if axes, IoT or evidence hash drifted."""
        source = Path(path)
        with np.load(source, allow_pickle=False) as payload:
            schema = str(payload["schema"].item())
            if schema != "superran-srs-ul-iot-v1":
                raise ValueError(f"unsupported UL IoT evidence schema {schema!r}")
            evidence = cls(
                interference_power_per_slot_rb=np.asarray(
                    payload["interference_power_per_slot_rb"], dtype=np.float64
                ),
                noise_power_linear=float(payload["noise_power_linear"].item()),
                srs_slot_indices=tuple(
                    int(value) for value in np.asarray(payload["slot_indices"]).reshape(-1)
                ),
                srs_rb_indices=tuple(
                    int(value) for value in np.asarray(payload["rb_indices"]).reshape(-1)
                ),
                schema=schema,
            )
            reported_iot = float(payload["reported_iot_db"].item())
            stored_fingerprint = str(payload["evidence_sha256"].item())
        if abs(evidence.ul_iot_db - reported_iot) > 2e-4:
            raise ValueError(
                "reported UL IoT does not match the final evidence row: "
                f"{reported_iot} vs {evidence.ul_iot_db}"
            )
        if evidence.fingerprint() != stored_fingerprint:
            raise ValueError("UL IoT evidence SHA-256 mismatch")
        return evidence


@dataclass
class SrsWaveformObservation:
    """All intermediate quantities for one two-port SRS leg."""

    absolute_slot: int
    occurrence_index: int
    leg: SrsTransmissionLeg
    rb_indices: np.ndarray
    tone_indices: np.ndarray
    local_sequences: np.ndarray
    desired_received_re: np.ndarray
    interference_received_re: np.ndarray
    noise_received_re: np.ndarray
    received_re: np.ndarray
    h_true_rb: np.ndarray
    h_est_desired_rb: np.ndarray
    h_est_interference_rb: np.ndarray
    h_est_noise_rb: np.ndarray
    h_est_rb: np.ndarray
    desired_signal_power_per_rb: np.ndarray
    interference_power_per_rb: np.ndarray
    collider_labels: tuple[str, ...]
    raw_sir_db: float
    raw_sinr_db: float
    post_despread_sir_db: float
    post_despread_sinr_db: float
    nmse_db: float
    evidence: SrsUlEvidence

    def as_dict(self, *, include_arrays: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "absolute_slot": int(self.absolute_slot),
            "occurrence_index": int(self.occurrence_index),
            "leg": self.leg.as_dict(),
            "rb_indices": self.rb_indices.tolist(),
            "tone_count": int(self.tone_indices.size),
            "rx_ports": int(self.received_re.shape[1]),
            "collider_labels": list(self.collider_labels),
            "raw_sir_dB": float(self.raw_sir_db),
            "raw_sinr_dB": float(self.raw_sinr_db),
            "post_despread_sir_dB": float(self.post_despread_sir_db),
            "post_despread_sinr_dB": float(self.post_despread_sinr_db),
            "nmse_dB": float(self.nmse_db),
            "ul_iot_evidence": self.evidence.as_dict(),
        }
        if include_arrays:
            out.update({
                "tone_indices": self.tone_indices,
                "local_sequences": self.local_sequences,
                "desired_received_re": self.desired_received_re,
                "interference_received_re": self.interference_received_re,
                "noise_received_re": self.noise_received_re,
                "received_re": self.received_re,
                "h_true_rb": self.h_true_rb,
                "h_est_desired_rb": self.h_est_desired_rb,
                "h_est_interference_rb": self.h_est_interference_rb,
                "h_est_noise_rb": self.h_est_noise_rb,
                "h_est_rb": self.h_est_rb,
                "desired_signal_power_per_rb": self.desired_signal_power_per_rb,
                "interference_power_per_rb": self.interference_power_per_rb,
            })
        return out


@dataclass
class SrsWaveformPairObservation:
    """Two 2T legs assembled into one 4-port channel observation."""

    legs: tuple[SrsWaveformObservation, SrsWaveformObservation]
    rb_indices: np.ndarray
    h_true_rb: np.ndarray
    h_est_rb: np.ndarray
    nmse_db: float
    evidence: SrsUlEvidence

    def as_dict(self, *, include_arrays: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "leg_separation_slots": int(
                self.legs[1].absolute_slot - self.legs[0].absolute_slot
            ),
            "leg_separation_ms": float(
                (self.legs[1].absolute_slot - self.legs[0].absolute_slot) * 0.5
            ),
            "rb_indices": self.rb_indices.tolist(),
            "shape": list(self.h_est_rb.shape),
            "nmse_dB": float(self.nmse_db),
            "legs": [leg.as_dict(include_arrays=False) for leg in self.legs],
            "ul_iot_evidence": self.evidence.as_dict(),
        }
        if include_arrays:
            out["h_true_rb"] = self.h_true_rb
            out["h_est_rb"] = self.h_est_rb
        return out


def _validate_signal(signal: SrsWaveformSignal, *, rx_ports: int | None = None) -> np.ndarray:
    channel = np.asarray(signal.channel_ul_rb, dtype=np.complex128)
    if channel.ndim != 3:
        raise ValueError(
            "channel_ul_rb must have shape [272 RB, victim_gNB_rx, 4 UE ports]"
        )
    if channel.shape[0] != hw.COMPANY_NUM_RB:
        raise ValueError(
            f"channel_ul_rb first axis must be {hw.COMPANY_NUM_RB}; got {channel.shape}"
        )
    if channel.shape[2] < signal.assignment.n_ports:
        raise ValueError(
            f"channel_ul_rb needs {signal.assignment.n_ports} UE ports; got {channel.shape}"
        )
    if rx_ports is not None and channel.shape[1] != rx_ports:
        raise ValueError(
            "all desired/interfering UL channels must terminate at the same victim gNB: "
            f"RX axis {channel.shape[1]} != {rx_ports}"
        )
    if not np.all(np.isfinite(channel)):
        raise ValueError("channel_ul_rb contains NaN or Inf")
    n_srs_id = _strict_int("n_srs_id", signal.n_srs_id)
    if n_srs_id > 1023:
        raise ValueError("n_srs_id must be in 0..1023")
    _finite_nonnegative("tx_power_linear", signal.tx_power_linear, positive=True)
    if not np.isfinite(float(signal.timing_offset_s)):
        raise ValueError("timing_offset_s must be finite")
    if not np.isfinite(float(signal.cfo_hz)):
        raise ValueError("cfo_hz must be finite")
    return channel


def _group_sequence_number(
    n_srs_id: int,
    *,
    absolute_slot: int,
    symbol: int,
    sequence_length: int,
    config: SrsWaveformConfig,
) -> tuple[int, int]:
    # The 30-kHz carrier has 20 slots per 10-ms radio frame.  The SRS PRBS is
    # reinitialised every frame, so absolute simulation slots must first be
    # reduced to n_s,f^mu rather than fed into one ever-growing sequence.
    slot_in_frame = int(absolute_slot) % 20
    if config.group_hopping:
        offset = 8 * (14 * slot_in_frame + int(symbol))
        bits = np.asarray(physical.gold_sequence(int(n_srs_id) // 30, offset + 8))
        f_gh = sum(int(bits[offset + i]) * (1 << i) for i in range(8)) % 30
        return (f_gh + int(n_srs_id)) % 30, 0
    if config.sequence_hopping:
        # TS 38.211 enables v only for M_sc >= 6*N_sc^RB = 72.
        if sequence_length < 72:
            return int(n_srs_id) % 30, 0
        index = 14 * slot_in_frame + int(symbol)
        bits = np.asarray(physical.gold_sequence(int(n_srs_id), index + 1))
        return int(n_srs_id) % 30, int(bits[index])
    return int(n_srs_id) % 30, 0


def srs_port_sequences(
    *,
    n_srs_id: int,
    cyclic_shifts: Sequence[int],
    sequence_length: int,
    absolute_slot: int,
    symbol: int,
    config: SrsWaveformConfig,
) -> np.ndarray:
    """Generate explicit per-port SRS sequences ``[RE, port]``.

    The assignment carries the explicit CS index for each simultaneously
    transmitted logical port.  This preserves the approved four-CS product
    profile rather than reinterpreting those indices through a second implicit
    antenna-port mapping.
    """
    length = _strict_int("sequence_length", sequence_length, minimum=1)
    shifts = tuple(_strict_int("cyclic_shift", value) for value in cyclic_shifts)
    if not shifts:
        raise ValueError("cyclic_shifts cannot be empty")
    n_cs_max = srs_n_cs_max(config.k_tc)
    if any(value >= n_cs_max for value in shifts):
        raise ValueError(
            f"cyclic shifts must be in 0..{n_cs_max - 1} for K_TC={config.k_tc}"
        )
    sequence_id = _strict_int("n_srs_id", n_srs_id)
    if sequence_id > 1023:
        raise ValueError("n_srs_id must be in 0..1023")
    u, v = _group_sequence_number(
        sequence_id,
        absolute_slot=absolute_slot,
        symbol=symbol,
        sequence_length=length,
        config=config,
    )
    columns = [
        physical.srs_sequence(
            length=length,
            u=u,
            v=v,
            cyclic_shift=2.0 * np.pi * shift / n_cs_max,
        ).astype(np.complex128)
        for shift in shifts
    ]
    return np.column_stack(columns)


def _carrier_tone_to_fft_bin(tone_indices: np.ndarray, fft_size: int) -> np.ndarray:
    # Carrier tones are numbered from its lower edge.  Centre the 3264-tone
    # carrier around DC before mapping to NumPy's unshifted FFT ordering.
    carrier_tones = hw.COMPANY_NUM_RB * hw.COMPANY_SC_PER_RB
    return (np.asarray(tone_indices, dtype=np.int64) - carrier_tones // 2) % int(fft_size)


def _apply_offsets(
    signal_re: np.ndarray,
    input_tones: np.ndarray,
    output_tones: np.ndarray,
    *,
    timing_offset_s: float,
    cfo_hz: float,
    config: SrsWaveformConfig,
) -> np.ndarray:
    """Apply timing phase and OFDM-symbol CFO/ICI on the complete FFT grid."""
    values = np.asarray(signal_re, dtype=np.complex128)
    if values.ndim != 2 or values.shape[0] != input_tones.size:
        raise ValueError("signal_re must be [input SRS RE, victim_gNB_rx]")
    fft_size = int(config.fft_size)
    in_bins = _carrier_tone_to_fft_bin(input_tones, fft_size)
    out_bins = _carrier_tone_to_fft_bin(output_tones, fft_size)
    if np.unique(in_bins).size != in_bins.size:
        raise ValueError("input SRS tones alias on the configured FFT grid")
    grid = np.zeros((fft_size, values.shape[1]), dtype=np.complex128)
    signed_frequency = (
        np.asarray(input_tones, dtype=np.float64)
        - (hw.COMPANY_NUM_RB * hw.COMPANY_SC_PER_RB) / 2.0
    ) * float(config.subcarrier_spacing_hz)
    timing_phase = np.exp(-1j * 2.0 * np.pi * signed_frequency * float(timing_offset_s))
    grid[in_bins] = values * timing_phase[:, None]
    if abs(float(cfo_hz)) > _EPS:
        waveform = np.fft.ifft(grid, axis=0)
        samples = np.arange(fft_size, dtype=np.float64)
        sample_rate = fft_size * float(config.subcarrier_spacing_hz)
        waveform *= np.exp(
            1j * 2.0 * np.pi * float(cfo_hz) * samples / sample_rate
        )[:, None]
        grid = np.fft.fft(waveform, axis=0)
    return grid[out_bins]


def _transmitter_contribution(
    signal: SrsWaveformSignal,
    *,
    absolute_slot: int,
    active_leg: SrsTransmissionLeg,
    occurrence_index: int,
    output_tones: np.ndarray,
    config: SrsWaveformConfig,
    channel: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rb_indices = assignment_rb_indices(signal.assignment, occurrence_index)
    input_tones = srs_comb_indices_for_rbs(
        rb_indices, k_tc=config.k_tc, comb_offset=int(active_leg.comb_offset)
    )
    sequences = srs_port_sequences(
        n_srs_id=int(signal.n_srs_id),
        cyclic_shifts=active_leg.cyclic_shifts,
        sequence_length=int(input_tones.size),
        absolute_slot=absolute_slot,
        symbol=int(active_leg.symbol),
        config=config,
    )
    amplitude = math.sqrt(
        float(signal.tx_power_linear) / len(active_leg.antenna_ports)
    )
    h_re = channel[input_tones // hw.COMPANY_SC_PER_RB]
    tx_at_rx = np.zeros((input_tones.size, channel.shape[1]), dtype=np.complex128)
    for column, port in enumerate(active_leg.antenna_ports):
        tx_at_rx += amplitude * sequences[:, column, None] * h_re[:, :, int(port)]
    received = _apply_offsets(
        tx_at_rx,
        input_tones,
        output_tones,
        timing_offset_s=float(signal.timing_offset_s),
        cfo_hz=float(signal.cfo_hz),
        config=config,
    )
    return received, input_tones, sequences


def _delay_gate(
    ls_re: np.ndarray,
    tone_indices: np.ndarray,
    *,
    config: SrsWaveformConfig,
) -> np.ndarray:
    if not config.apply_delay_gate:
        return ls_re
    if tone_indices.size < 2:
        return ls_re
    first = int(tone_indices[0])
    positions = (tone_indices - first) // int(config.k_tc)
    if np.any(first + positions * int(config.k_tc) != tone_indices):
        raise ValueError("tone_indices do not lie on one configured SRS comb")
    transform_size = int(positions[-1]) + 1
    sparse = np.zeros((transform_size,) + ls_re.shape[1:], dtype=np.complex128)
    sparse[positions] = ls_re
    delay = np.fft.ifft(sparse, axis=0)
    delay_resolution_s = 1.0 / (
        transform_size * int(config.k_tc) * float(config.subcarrier_spacing_hz)
    )
    physical_window_s = (
        float(config.receiver_tau_rms_ns) * 1e-9 * float(config.delay_window_sigma)
    )
    keep = int(math.ceil(physical_window_s / max(delay_resolution_s, _EPS))) + 1
    cyclic_shift_spacing = max(
        1, int(math.floor(tone_indices.size / srs_n_cs_max(config.k_tc)))
    )
    keep = min(max(2, keep), max(2, cyclic_shift_spacing // 2), transform_size)
    window = np.zeros(transform_size, dtype=np.float64)
    window[:keep] = 1.0
    taper = min(4, max(0, keep - 1))
    if taper:
        phase = np.linspace(0.0, np.pi / 2.0, taper, endpoint=False)
        window[keep - taper:keep] = np.cos(phase) ** 2
    delay *= window.reshape((transform_size,) + (1,) * (ls_re.ndim - 1))
    return np.fft.fft(delay, axis=0)[positions]


def _collapse_re_to_rb(
    values_re: np.ndarray,
    tone_indices: np.ndarray,
    rb_indices: np.ndarray,
) -> np.ndarray:
    values = np.asarray(values_re)
    rows = []
    tone_rbs = tone_indices // hw.COMPANY_SC_PER_RB
    for rb in rb_indices:
        selected = values[tone_rbs == int(rb)]
        if selected.shape[0] < 1:
            raise RuntimeError(f"SRS comb has no active RE in RB {int(rb)}")
        rows.append(np.mean(selected, axis=0))
    return np.stack(rows, axis=0)


def _despread_component(
    received_re: np.ndarray,
    local_pilots: np.ndarray,
    tone_indices: np.ndarray,
    rb_indices: np.ndarray,
    *,
    config: SrsWaveformConfig,
) -> np.ndarray:
    estimates = []
    for port in range(local_pilots.shape[1]):
        pilot = local_pilots[:, port]
        denominator = np.maximum(np.abs(pilot) ** 2, _EPS)
        ls_re = received_re * pilot.conj()[:, None] / denominator[:, None]
        filtered = _delay_gate(ls_re, tone_indices, config=config)
        estimates.append(_collapse_re_to_rb(filtered, tone_indices, rb_indices))
    return np.stack(estimates, axis=-1)


def _power_per_rb(
    values_re: np.ndarray,
    tone_indices: np.ndarray,
    rb_indices: np.ndarray,
) -> np.ndarray:
    power_re = np.mean(np.abs(values_re) ** 2, axis=1)
    return np.asarray([
        float(np.mean(power_re[tone_indices // hw.COMPANY_SC_PER_RB == int(rb)]))
        for rb in rb_indices
    ])


def observe_srs_leg(
    desired: SrsWaveformSignal,
    *,
    leg_index: int,
    occurrence_index: int = 0,
    interferers: Sequence[SrsWaveformSignal] = (),
    config: SrsWaveformConfig | None = None,
) -> SrsWaveformObservation:
    """Simulate one real SRS occasion and retain every receive-stage quantity."""
    cfg = config or SrsWaveformConfig()
    desired_channel = _validate_signal(desired)
    leg_id = _strict_int("leg_index", leg_index)
    if leg_id >= len(desired.assignment.legs):
        raise ValueError(f"leg_index must be 0..{len(desired.assignment.legs) - 1}")
    occurrence = _strict_int("occurrence_index", occurrence_index)
    leg = desired.assignment.legs[leg_id]
    absolute_slot = int(leg.offset_slots) + occurrence * int(desired.assignment.period_slots)
    active = active_leg_at_slot(desired.assignment, absolute_slot)
    if active is None or int(active[0].leg_index) != leg_id or active[1] != occurrence:
        raise RuntimeError("desired assignment/slot mapping is internally inconsistent")
    rb_indices = assignment_rb_indices(desired.assignment, occurrence)
    tone_indices = srs_comb_indices_for_rbs(
        rb_indices, k_tc=cfg.k_tc, comb_offset=int(leg.comb_offset)
    )
    desired_received, desired_input_tones, desired_sequences = _transmitter_contribution(
        desired,
        absolute_slot=absolute_slot,
        active_leg=leg,
        occurrence_index=occurrence,
        output_tones=tone_indices,
        config=cfg,
        channel=desired_channel,
    )
    if not np.array_equal(desired_input_tones, tone_indices):
        raise RuntimeError("desired SRS tone map does not match its receive grid")
    pilot_amplitude = math.sqrt(
        float(desired.tx_power_linear) / len(leg.antenna_ports)
    )
    local_pilots = desired_sequences * pilot_amplitude

    interference_received = np.zeros_like(desired_received)
    collider_labels: list[str] = []
    for index, interferer in enumerate(interferers):
        channel = _validate_signal(interferer, rx_ports=desired_channel.shape[1])
        hit = active_leg_at_slot(interferer.assignment, absolute_slot)
        if hit is None:
            continue
        interferer_leg, interferer_occurrence = hit
        if int(interferer_leg.symbol) != int(leg.symbol):
            continue
        contribution, _, _ = _transmitter_contribution(
            interferer,
            absolute_slot=absolute_slot,
            active_leg=interferer_leg,
            occurrence_index=interferer_occurrence,
            output_tones=tone_indices,
            config=cfg,
            channel=channel,
        )
        interference_received += contribution
        if np.any(np.abs(contribution) > 1e-14):
            collider_labels.append(
                interferer.label
                or f"cell{interferer.assignment.cell_id}:ue{interferer.assignment.ue_id}:#{index}"
            )

    seed = np.random.SeedSequence([
        int(cfg.seed),
        int(absolute_slot),
        int(desired.assignment.cell_id),
        int(desired.assignment.ue_id),
        int(leg_id),
    ])
    rng = np.random.default_rng(seed)
    noise_sigma = math.sqrt(float(cfg.noise_power_linear) / 2.0)
    noise_received = noise_sigma * (
        rng.standard_normal(desired_received.shape)
        + 1j * rng.standard_normal(desired_received.shape)
    )
    received = desired_received + interference_received + noise_received

    h_est_desired = _despread_component(
        desired_received, local_pilots, tone_indices, rb_indices, config=cfg
    )
    h_est_interference = _despread_component(
        interference_received, local_pilots, tone_indices, rb_indices, config=cfg
    )
    h_est_noise = _despread_component(
        noise_received, local_pilots, tone_indices, rb_indices, config=cfg
    )
    h_est = h_est_desired + h_est_interference + h_est_noise
    h_true = desired_channel[rb_indices][:, :, np.asarray(leg.antenna_ports, dtype=np.intp)]

    desired_power_rb = _power_per_rb(desired_received, tone_indices, rb_indices)
    interference_power_rb = _power_per_rb(
        interference_received, tone_indices, rb_indices
    )
    desired_raw = float(np.mean(desired_power_rb))
    interference_raw = float(np.mean(interference_power_rb))
    noise_raw = float(cfg.noise_power_linear)
    desired_post = float(np.mean(np.abs(h_est_desired) ** 2))
    interference_post = float(np.mean(np.abs(h_est_interference) ** 2))
    noise_post = float(np.mean(np.abs(h_est_noise) ** 2))
    error = float(np.sum(np.abs(h_est - h_true) ** 2))
    reference = float(np.sum(np.abs(h_true) ** 2))
    nmse_db = _ratio_db(error, max(reference, _EPS))
    evidence = SrsUlEvidence(
        interference_power_per_slot_rb=interference_power_rb[None, :],
        noise_power_linear=noise_raw,
        srs_slot_indices=(absolute_slot,),
        srs_rb_indices=tuple(int(x) for x in rb_indices),
    )
    return SrsWaveformObservation(
        absolute_slot=absolute_slot,
        occurrence_index=occurrence,
        leg=leg,
        rb_indices=rb_indices,
        tone_indices=tone_indices,
        local_sequences=desired_sequences,
        desired_received_re=desired_received,
        interference_received_re=interference_received,
        noise_received_re=noise_received,
        received_re=received,
        h_true_rb=h_true,
        h_est_desired_rb=h_est_desired,
        h_est_interference_rb=h_est_interference,
        h_est_noise_rb=h_est_noise,
        h_est_rb=h_est,
        desired_signal_power_per_rb=desired_power_rb,
        interference_power_per_rb=interference_power_rb,
        collider_labels=tuple(collider_labels),
        raw_sir_db=_ratio_db(desired_raw, interference_raw),
        raw_sinr_db=_ratio_db(desired_raw, interference_raw + noise_raw),
        post_despread_sir_db=_ratio_db(desired_post, interference_post),
        post_despread_sinr_db=_ratio_db(
            desired_post, interference_post + noise_post
        ),
        nmse_db=nmse_db,
        evidence=evidence,
    )


def simulate_srs_pair(
    desired_by_leg: tuple[SrsWaveformSignal, SrsWaveformSignal],
    *,
    occurrence_index: int = 0,
    interferers_by_leg: tuple[
        Sequence[SrsWaveformSignal], Sequence[SrsWaveformSignal]
    ] = ((), ()),
    config: SrsWaveformConfig | None = None,
) -> SrsWaveformPairObservation:
    """Run both 2T occasions and assemble one ``[RB, gNB-RX, 4]`` estimate.

    Two desired signals are accepted because the physical channel can change
    during the 5-ms antenna-switching gap.  They must reference the same
    allocation, while their channel arrays may differ.
    """
    first_signal, second_signal = desired_by_leg
    if len(interferers_by_leg) != 2:
        raise ValueError("interferers_by_leg must contain one sequence per 2T leg")
    if first_signal.assignment != second_signal.assignment:
        raise ValueError("both desired legs must use the same SRS resource assignment")
    cfg = config or SrsWaveformConfig()
    legs = (
        observe_srs_leg(
            first_signal,
            leg_index=0,
            occurrence_index=occurrence_index,
            interferers=interferers_by_leg[0],
            config=cfg,
        ),
        observe_srs_leg(
            second_signal,
            leg_index=1,
            occurrence_index=occurrence_index,
            interferers=interferers_by_leg[1],
            config=cfg,
        ),
    )
    if not np.array_equal(legs[0].rb_indices, legs[1].rb_indices):
        raise RuntimeError("the two 2T legs did not sound the same RB set")
    if legs[0].h_est_rb.shape[:2] != legs[1].h_est_rb.shape[:2]:
        raise ValueError("both desired legs must terminate at the same victim gNB")
    rb_indices = legs[0].rb_indices
    n_rx = legs[0].h_est_rb.shape[1]
    h_true = np.zeros((rb_indices.size, n_rx, 4), dtype=np.complex128)
    h_est = np.zeros_like(h_true)
    for observation in legs:
        ports = np.asarray(observation.leg.antenna_ports, dtype=np.intp)
        h_true[:, :, ports] = observation.h_true_rb
        h_est[:, :, ports] = observation.h_est_rb
    error = float(np.sum(np.abs(h_est - h_true) ** 2))
    reference = float(np.sum(np.abs(h_true) ** 2))
    evidence = SrsUlEvidence(
        interference_power_per_slot_rb=np.stack([
            legs[0].interference_power_per_rb,
            legs[1].interference_power_per_rb,
        ]),
        noise_power_linear=float(cfg.noise_power_linear),
        srs_slot_indices=(legs[0].absolute_slot, legs[1].absolute_slot),
        srs_rb_indices=tuple(int(x) for x in rb_indices),
    )
    return SrsWaveformPairObservation(
        legs=legs,
        rb_indices=rb_indices,
        h_true_rb=h_true,
        h_est_rb=h_est,
        nmse_db=_ratio_db(error, max(reference, _EPS)),
        evidence=evidence,
    )

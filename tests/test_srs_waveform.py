"""Waveform-level SRS receiver, interference and UL-evidence invariants."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran.loader import Dataset  # noqa: E402
from superran.srs_resource import allocate_basic_srs_resources  # noqa: E402
from superran.srs_waveform import (  # noqa: E402
    SrsUlEvidence,
    SrsWaveformConfig,
    SrsWaveformSignal,
    active_leg_at_slot,
    assignment_rb_indices,
    observe_srs_leg,
    simulate_srs_pair,
    srs_comb_indices_for_rbs,
    srs_n_cs_max,
    srs_port_sequences,
)


def _flat_channel(n_rx: int = 4) -> np.ndarray:
    return np.ones((272, n_rx, 4), dtype=np.complex128)


def _quiet_config(**overrides: object) -> SrsWaveformConfig:
    values: dict[str, object] = {
        "noise_power_linear": 1e-20,
        "receiver_tau_rms_ns": 10.0,
        "seed": 17,
    }
    values.update(overrides)
    return SrsWaveformConfig(**values)  # type: ignore[arg-type]


def test_38211_cyclic_shift_limits_and_absolute_comb_mapping() -> None:
    assert {k: srs_n_cs_max(k) for k in (2, 4, 8)} == {2: 8, 4: 12, 8: 6}
    tones = srs_comb_indices_for_rbs(np.arange(8), k_tc=8, comb_offset=0)
    counts = np.bincount(tones // 12, minlength=8)
    assert counts.tolist() == [2, 1, 2, 1, 2, 1, 2, 1]
    assert np.all(tones % 8 == 0)


def test_assignment_bridge_keeps_both_legs_on_one_rbg_then_advances_hop() -> None:
    assignment = allocate_basic_srs_resources([0], adaptive_period=False)[0]
    first = assignment_rb_indices(assignment, 0)
    second = assignment_rb_indices(assignment, 1)
    assert first.tolist() == list(range(0, 16))
    assert second.tolist() == list(range(128, 144))
    assert active_leg_at_slot(assignment, 7) == (assignment.legs[0], 0)
    assert active_leg_at_slot(assignment, 17) == (assignment.legs[1], 0)
    assert active_leg_at_slot(assignment, 27) == (assignment.legs[0], 1)
    assert active_leg_at_slot(assignment, 8) is None


def test_existing_low_papr_sequence_is_used_with_explicit_port_cyclic_shifts() -> None:
    assignment = allocate_basic_srs_resources([0], adaptive_period=False)[0]
    sequences = srs_port_sequences(
        n_srs_id=31,
        cyclic_shifts=assignment.legs[0].cyclic_shifts,
        sequence_length=96,
        absolute_slot=7,
        symbol=assignment.symbol,
        config=_quiet_config(),
    )
    assert sequences.shape == (96, 2)
    assert np.max(np.abs(np.abs(sequences) - 1.0)) < 1e-6
    correlation = abs(np.vdot(sequences[:, 0], sequences[:, 1])) / 96.0
    assert correlation < 1e-6


def test_noise_free_two_leg_receiver_recovers_one_16rb_64x4_observation() -> None:
    assignment = allocate_basic_srs_resources([0], adaptive_period=False)[0]
    signal = SrsWaveformSignal(
        assignment=assignment,
        channel_ul_rb=_flat_channel(64),
        n_srs_id=7,
        label="desired",
    )
    result = simulate_srs_pair((signal, signal), config=_quiet_config())
    assert result.h_est_rb.shape == (16, 64, 4)
    assert result.rb_indices.tolist() == list(range(16))
    assert result.nmse_db < -140.0
    assert result.legs[1].absolute_slot - result.legs[0].absolute_slot == 10
    assert result.as_dict()["leg_separation_ms"] == pytest.approx(5.0)
    assert result.evidence.srs_slot_indices == (7, 17)
    assert result.evidence.interference_power_per_slot_rb.shape == (2, 16)
    assert len(result.evidence.fingerprint()) == 64


def test_raw_overlap_and_post_despread_interference_are_separate_evidence() -> None:
    assignments = allocate_basic_srs_resources([0, 1], adaptive_period=False)
    desired = SrsWaveformSignal(assignments[0], _flat_channel(), 7, label="desired")
    orthogonal = SrsWaveformSignal(
        replace(assignments[1], cell_id=1),
        _flat_channel(),
        7,
        label="other-CS-block",
    )
    same_cs = SrsWaveformSignal(
        replace(assignments[0], cell_id=1, ue_id=9),
        _flat_channel(),
        7,
        label="same-CS",
    )
    config = _quiet_config(noise_power_linear=1e-12)
    separated = observe_srs_leg(
        desired, leg_index=0, interferers=[orthogonal], config=config
    )
    contaminated = observe_srs_leg(
        desired, leg_index=0, interferers=[same_cs], config=config
    )
    assert separated.raw_sir_db == pytest.approx(0.0, abs=1e-10)
    assert contaminated.raw_sir_db == pytest.approx(0.0, abs=1e-10)
    assert separated.post_despread_sir_db > 140.0
    assert contaminated.post_despread_sir_db == pytest.approx(0.0, abs=1e-8)
    assert separated.collider_labels == ("other-CS-block",)
    expected_iot = 10.0 * np.log10(
        (np.mean(separated.interference_power_per_rb) + config.noise_power_linear)
        / config.noise_power_linear
    )
    assert separated.evidence.ul_iot_db == pytest.approx(expected_iot)


def test_timing_and_cfo_erode_cyclic_shift_orthogonality() -> None:
    assignments = allocate_basic_srs_resources([0, 1], adaptive_period=False)
    desired = SrsWaveformSignal(assignments[0], _flat_channel(), 11)
    base = replace(assignments[1], cell_id=1)
    config = _quiet_config(noise_power_linear=1e-12)
    aligned = observe_srs_leg(
        desired,
        leg_index=0,
        interferers=[SrsWaveformSignal(base, _flat_channel(), 11)],
        config=config,
    )
    with_cfo = observe_srs_leg(
        desired,
        leg_index=0,
        interferers=[SrsWaveformSignal(base, _flat_channel(), 11, cfo_hz=500.0)],
        config=config,
    )
    with_delay = observe_srs_leg(
        desired,
        leg_index=0,
        interferers=[
            SrsWaveformSignal(base, _flat_channel(), 11, timing_offset_s=2e-6)
        ],
        config=config,
    )
    assert aligned.post_despread_sir_db > 140.0
    assert with_cfo.post_despread_sir_db < 60.0
    assert with_delay.post_despread_sir_db < 20.0


def test_ul_evidence_is_axis_checked_and_tamper_evident() -> None:
    first = SrsUlEvidence(
        interference_power_per_slot_rb=np.asarray([[1.0, 2.0]]),
        noise_power_linear=0.5,
        srs_slot_indices=(7,),
        srs_rb_indices=(0, 1),
    )
    second = SrsUlEvidence(
        interference_power_per_slot_rb=np.asarray([[1.0, 2.1]]),
        noise_power_linear=0.5,
        srs_slot_indices=(7,),
        srs_rb_indices=(0, 1),
    )
    assert first.ul_iot_db == pytest.approx(10.0 * np.log10((1.5 + 0.5) / 0.5))
    assert first.fingerprint() != second.fingerprint()
    with pytest.raises(ValueError, match="RB axis"):
        SrsUlEvidence(
            interference_power_per_slot_rb=np.ones((1, 2)),
            noise_power_linear=1.0,
            srs_slot_indices=(7,),
            srs_rb_indices=(0,),
        )
    assignment = allocate_basic_srs_resources([0], adaptive_period=False)[0]
    with pytest.raises(ValueError, match="first axis"):
        observe_srs_leg(
            SrsWaveformSignal(assignment, np.ones((16, 4, 4)), 0),
            leg_index=0,
        )


def test_dataset_requires_real_ul_truth_unless_ideal_reciprocity_is_explicit() -> None:
    dataset = object.__new__(Dataset)
    dataset.dataset_id = "in_memory"
    dataset.summary = {"shape": {"N": 1}}
    dataset.__dict__["h_true"] = np.ones((1, 1, 272, 2, 4), dtype=np.complex64)
    dataset.__dict__["h_ul_true"] = None
    assignment = allocate_basic_srs_resources([0], adaptive_period=False)[0]
    with pytest.raises(ValueError, match="没有 h_ul_true"):
        dataset.srs_waveform(
            0,
            assignment,
            n_srs_id=0,
            config=_quiet_config(),
        )
    diagnostic = dataset.srs_waveform(
        0,
        assignment,
        n_srs_id=0,
        config=_quiet_config(),
        allow_ideal_reciprocity=True,
    )
    assert diagnostic.h_est_rb.shape == (16, 2, 2)
    assert diagnostic.nmse_db < -140.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

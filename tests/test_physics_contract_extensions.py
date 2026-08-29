"""Cross-cutting physics contracts adopted from newer reference pipelines."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import channelhub, generate, hardware, scene_assets, scenes, system  # noqa: E402
from superran import srs_metrics as sm  # noqa: E402
from superran.srs_waveform import SrsUlEvidence  # noqa: E402


def test_explicit_snapshot_interval_is_independent_of_radio_report_clocks() -> None:
    assert hardware.COMPANY_SNAPSHOT_INTERVAL_S == pytest.approx(5e-3)
    assert system.snapshot_interval_ms({"sample_interval_s": 5e-3}) == pytest.approx(5.0)
    assert system.snapshot_interval_ms({"sample_interval_s": 0.5e-3}) == pytest.approx(0.5)
    assert system.snapshot_interval_ms({
        "sample_interval_s": 5e-3,
        "srs_periodicity": 80,
        "csirs_periodicity": 40,
    }) == pytest.approx(5.0)
    # Legacy datasets retain the old inference only when the explicit clock is absent.
    assert system.snapshot_interval_ms({"srs_periodicity": 20}) == pytest.approx(10.0)
    for bad in (0.0, -1.0, float("nan"), float("inf"), True, "bad"):
        with pytest.raises(ValueError, match="sample_interval_s"):
            system.snapshot_interval_ms({"sample_interval_s": bad})


def test_paired_dataset_size_budget_counts_four_channel_tensors() -> None:
    base = {"num_rb": 16, "num_bs_tx_ant": 4, "num_ue_rx_ant": 2}
    single = generate.estimate_size_mb({**base, "link": "DL"}, 10)
    paired = generate.estimate_size_mb({**base, "link": "BOTH"}, 10)
    assert paired == pytest.approx(2.0 * single)


def test_active_source_passes_structural_superran_handshake() -> None:
    report = channelhub.probe_source_contract()
    assert report.compatible, report.as_dict()
    assert not report.blockers
    assert all(check["passed"] for check in report.checks.values())
    engines = {item.name: item for item in channelhub.probe_capabilities()}
    assert engines["internal_sim"].available is True


def test_nr_noise_references_keep_per_re_per_rb_and_active_srs_distinct() -> None:
    rbs = np.arange(16)
    levels = sm.calibrated_nr_noise_levels(
        rb_indices=rbs,
        subcarrier_spacing_hz=30_000.0,
        k_tc=2,
        rru_noise_figure_db=2.0,
        tdd_rx_loss_db=1.0,
    )
    assert levels.active_srs_re_count == 96
    assert levels.noise_per_re_dbm == pytest.approx(-126.2288, abs=1e-3)
    assert levels.noise_per_rb_dbm == pytest.approx(-115.4376, abs=1e-3)
    assert levels.noise_per_rb_dbm - levels.noise_per_re_dbm == pytest.approx(
        10.0 * math.log10(12.0)
    )
    assert levels.noise_active_srs_dbm - levels.noise_per_re_dbm == pytest.approx(
        10.0 * math.log10(96.0)
    )
    k8 = sm.calibrated_nr_noise_levels(rb_indices=np.arange(8), k_tc=8)
    assert k8.active_srs_re_count == 12


def test_srs_open_loop_power_and_active_re_link_budget_close_exactly() -> None:
    result = sm.srs_link_budget(
        pathloss_db=110.0,
        rb_indices=np.arange(16),
        k_tc=2,
        antenna_gain_db=0.0,
        p0_dbm=-96.0,
        alpha=0.8,
        ue_max_power_dbm=23.0,
    )
    expected_tx = -96.0 + 0.8 * 110.0 + 10.0 * math.log10(16.0)
    assert result.ue_tx_power_dbm == pytest.approx(expected_tx)
    assert result.received_total_dbm == pytest.approx(expected_tx - 110.0)
    assert result.received_per_active_re_dbm == pytest.approx(
        result.received_total_dbm - 10.0 * math.log10(96.0)
    )
    assert result.snr_per_active_re_db == pytest.approx(
        result.received_per_active_re_dbm - result.noise.noise_per_re_dbm
    )
    for bad in (True, 1.5, 0, -1):
        with pytest.raises(ValueError, match="allocated_rb"):
            sm.open_loop_ul_tx_power_dbm(110.0, bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="rb_indices"):
        sm.srs_link_budget(pathloss_db=110.0, rb_indices=[0.5, 1.5])
    assert sm.combine_snr_sir_db(10.0, float("inf")) == pytest.approx(10.0)
    assert sm.combine_snr_sir_db(10.0, float("-inf")) == float("-inf")


def test_presinr_is_scale_invariant_and_iir_runs_in_linear_power() -> None:
    true = np.ones((2, 4, 2, 1), dtype=np.complex128)
    estimate = true.copy()
    estimate[0] += math.sqrt(0.1)
    estimate[1] += 1.0
    summary = sm.presinr_summary(true, estimate, alpha=0.2)
    scaled = sm.presinr_summary(true * 1e-12, estimate * 1e-12, alpha=0.2)
    assert summary.instantaneous_wideband_db == pytest.approx(scaled.instantaneous_wideband_db)
    np.testing.assert_allclose(summary.per_rb_db, scaled.per_rb_db, atol=1e-6)
    # Slot ratios are 10 and 1. Linear IIR gives 0.2*1 + 0.8*10 = 8.2,
    # whereas an invalid dB-domain average would give 8 dB.
    assert summary.per_slot_filtered_db[0] == pytest.approx(10.0, abs=1e-5)
    assert summary.per_slot_filtered_db[1] == pytest.approx(
        10.0 * math.log10(8.2), abs=1e-5
    )
    assert summary.per_slot_filtered_db[1] != pytest.approx(8.0)


def test_ul_iot_sidecar_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    evidence = SrsUlEvidence(
        interference_power_per_slot_rb=np.asarray(
            [[0.1, 0.2], [0.3, 0.4]], dtype=np.float64
        ),
        noise_power_linear=0.05,
        srs_slot_indices=(7, 17),
        srs_rb_indices=(0, 1),
    )
    path = tmp_path / "sample.ul-iot.npz"
    report = evidence.write_npz(path)
    restored = SrsUlEvidence.load_npz(path)
    assert report["bytes"] < 100_000
    assert report["evidence_sha256"] == evidence.fingerprint()
    assert restored.fingerprint() == evidence.fingerprint()

    with np.load(path, allow_pickle=False) as payload:
        values = {key: np.asarray(payload[key]) for key in payload.files}
    values["reported_iot_db"] = values["reported_iot_db"] + 1.0
    with path.open("wb") as handle:
        np.savez_compressed(handle, **values)
    with pytest.raises(ValueError, match="does not match"):
        SrsUlEvidence.load_npz(path)


def test_scene_radio_revision_and_lock_are_stable_and_physics_sensitive(
    tmp_path: Path,
) -> None:
    meta = {
        "environment_prior": {"scene_revision": "geo-a"},
        "materials": {"concrete": {"scattering_coefficient": 0.3}},
        "buildings": [{
            "material": "concrete",
            "roof_material": "concrete",
            "glass_ratio": 0.0,
            "material_params": None,
        }],
    }
    first = scene_assets.radio_config_revision(meta)
    assert first == scene_assets.radio_config_revision(meta)
    changed = json.loads(json.dumps(meta))
    changed["materials"]["concrete"]["scattering_coefficient"] = 0.6
    assert first != scene_assets.radio_config_revision(changed)
    lock = scene_assets.scene_asset_lock(tmp_path / "scene")
    with lock:
        assert lock.is_locked
        assert lock.lock_file == scene_assets.scene_asset_lock(tmp_path / "scene").lock_file


def test_scene_cache_is_fingerprinted_reused_and_rebuilt_after_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = tmp_path / "catalogue"
    source = catalogue / "toy"
    source.mkdir(parents=True)
    (catalogue / "toy.json").write_text(
        json.dumps({"scene_id": "toy", "display_name": "Toy"}), encoding="utf-8"
    )
    (source / "scene.xml").write_text("<scene/>", encoding="utf-8")
    (source / "meta.json").write_text(json.dumps({
        "environment_prior": {
            "scene_revision": "geo-toy",
            "semantic_point_counts": {"building_facade": 10, "road": 4},
            "semantic_ids": {"building_facade": 1, "road": 2},
        },
        "materials": {"road_asphalt": {"count": 1, "scattering_coefficient": 0.2}},
        "buildings": [],
    }), encoding="utf-8")
    original_ply = b"ply\nformat ascii 1.0\nobj_info vtk\nend_header\n0 0 0\n"
    (source / "mesh.ply").write_bytes(original_ply)
    cache = tmp_path / "cache"
    monkeypatch.setattr(scenes, "scenes_dir", lambda: catalogue)
    monkeypatch.setattr(scenes, "scene_cache_dir", lambda: cache)

    first = scenes.prepare_scene("toy")
    assert first["cached"] is False
    assert first["source_fingerprint"].startswith("scene_")
    assert first["prepared_fingerprint"].startswith("scene_")
    assert first["radio_config_revision"].startswith("rf_")
    assert first["scene_fidelity"]["level"] == "L1_semantic"
    assert first["scene_fidelity"]["rt_layers"]["roads"] is True
    assert b"obj_info" in (source / "mesh.ply").read_bytes()
    prepared_ply = cache / "toy" / "mesh.ply"
    assert b"obj_info" not in prepared_ply.read_bytes()

    second = scenes.prepare_scene("toy")
    assert second["cached"] is True
    prepared_ply.write_bytes(prepared_ply.read_bytes() + b"tamper")
    rebuilt = scenes.prepare_scene("toy")
    assert rebuilt["cached"] is False
    assert b"tamper" not in prepared_ply.read_bytes()

    resolved = scenes.resolve_scene_config("toy")
    assert resolved["scene_source_fingerprint"] == rebuilt["source_fingerprint"]
    assert resolved["scene_radio_config_revision"] == rebuilt["radio_config_revision"]
    assert resolved["scene_fidelity"]["level"] == "L1_semantic"

    (cache / "toy" / scene_assets.SCENE_REBUILD_JOURNAL_FILENAME).write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="未完成"):
        scenes.prepare_scene("toy")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

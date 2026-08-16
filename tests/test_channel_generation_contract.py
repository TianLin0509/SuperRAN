"""信道生成窄腰契约：H_true/H_est、阵列维度和逐小区元数据。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superran import channelhub as ch  # noqa: E402
from superran import (  # noqa: E402
    gates,  # noqa: E402
    load,
    measure,
)
from superran import generate as gen  # noqa: E402
from superran import physical as ph  # noqa: E402
from superran import plan as pl  # noqa: E402


def test_explicit_preset_beats_classifier_hint_and_user_override_beats_both() -> None:
    draft, _ = pl.create_draft(
        "比较 PMI 与 SVD 预编码",
        preset="company_64t4r",
    )
    assert draft.params["link"] == "BOTH"

    overridden, _ = pl.create_draft(
        "比较 PMI 与 SVD 预编码",
        preset="company_64t4r",
        overrides={"link": "DL"},
    )
    assert overridden.params["link"] == "DL"


def test_precoding_source_gate_rejects_csirs_when_srs_was_declared() -> None:
    wrong = SimpleNamespace(precoding_csi_sources=np.array(["dl_csirs_estimate"]))
    right = SimpleNamespace(
        precoding_csi_sources=np.array(["ul_srs_estimate", "ul_srs_estimate"])
    )
    assert not gates._precoding_source_item(wrong, "ul_srs_estimate").passed
    assert gates._precoding_source_item(right, "ul_srs_estimate").passed


def test_paired_ul_srs_is_reciprocity_mapped_to_downlink_convention() -> None:
    h_dl = np.array([[[[1 + 2j], [3 - 4j]]]], dtype=np.complex64)
    sample = SimpleNamespace(
        h_dl_true=h_dl,
        h_ul_est=np.conj(h_dl),
        h_dl_est=h_dl * 0.5,
    )
    truth, precoding_est, dl_est, source = ch.downlink_and_precoding_channels(sample)
    np.testing.assert_array_equal(truth, h_dl)
    np.testing.assert_array_equal(precoding_est, h_dl)
    np.testing.assert_array_equal(dl_est, h_dl * 0.5)
    assert source == "ul_srs_estimate"


def test_comparison_csi_selector_keeps_srs_and_csirs_distinct() -> None:
    srs = np.full((2, 1, 1, 1, 1), 1 + 2j, dtype=np.complex64)
    csirs = np.full((2, 1, 1, 1, 1), 3 + 4j, dtype=np.complex64)
    ds = SimpleNamespace(h_est=srs, h_dl_est=csirs)
    np.testing.assert_array_equal(gates._precoding_csi_tensor(ds, "srs"), srs)
    np.testing.assert_array_equal(gates._precoding_csi_tensor(ds, "csirs"), csirs)
    assert gates._precoding_csi_tensor(ds, "ideal") is None
    with pytest.raises(ValueError, match="禁止静默回退"):
        gates._precoding_csi_tensor(SimpleNamespace(h_est=srs, h_dl_est=None), "csirs")


def test_builtin_comparison_clusters_repeated_snapshots_by_ue_position() -> None:
    h = np.ones((4, 1, 1, 1, 1), dtype=np.complex64)
    ds = SimpleNamespace(
        h_true=h,
        h_est=h,
        h_dl_est=h,
        h_interferers=None,
        ue_position=np.array(
            [[10.0, 0.0, 1.5], [20.0, 0.0, 1.5],
             [10.0, 0.0, 1.5], [20.0, 0.0, 1.5]]
        ),
        dataset_id="ds_cluster_test",
        config={},
    )
    result = gates.compare_arms(
        ds,
        {"name": "A", "method": "identity", "csi": "srs"},
        {"name": "B", "method": "identity", "csi": "srs"},
        snr_db=10.0,
    )
    assert result.raw_observations == 4
    assert result.paired.n == 2
    assert result.as_dict()["inference_unit"]["clustered_by"] == "ue_position"


def _fake_sample(*, h_est: np.ndarray | None, marker: float = 0.0) -> SimpleNamespace:
    h_true = np.full((1, 4, 2, 1), 1.0 + 1.0j, dtype=np.complex64)
    vectors = {
        "pathloss_all_db": [100.0 + marker, 110.0 + marker],
        "rx_power_all_dbm": [-57.0 - marker, -67.0 - marker],
        "antenna_gain_all_db": [0.0, -3.0],
        "is_los_all": [False, True],
        "los_probability_all": [0.2, 0.4],
        "sample_tau_rms_all_ns": [120.0, 80.0],
        "shadow_fading_all_db": [1.0, -2.0],
        "physical_site_group_ids": [0, 1],
        "effective_channel_model_all": ["CDL-C", "CDL-D"],
        "dl_interference_power_per_slot_per_cell_mw": [[0.0, 0.25 + marker]],
    }
    return SimpleNamespace(
        h_serving_true=h_true,
        h_serving_est=h_est,
        h_dl_true=None,
        h_dl_est=None,
        h_ul_true=None,
        h_ul_est=None,
        h_interferers=None,
        w_dl=None,
        ue_position=np.array([10.0, 20.0, 1.5]),
        sinr_dB=5.0,
        snr_dB=8.0,
        sir_dB=10.0,
        noise_power_dBm=-90.0,
        serving_cell_id=0,
        dl_rank=1,
        slot_duration_s=0.5e-3,
        ul_pre_sinr_dB=None,
        ul_snr_dB=None,
        ul_sinr_dB=None,
        ul_sir_dB=None,
        dl_sir_dB=None,
        num_interfering_ues=0,
        ssb_rsrp_dBm=None,
        ssb_sinr_dB=None,
        meta={
            **vectors,
            "serving_cell_index": 0,
            "dl_signal_power_mw": 1.0,
            "dl_thermal_noise_power_mw": 0.1,
            "dl_power_decomposition_version": "geometry_per_cell_v1",
            "effective_channel_model": "CDL-C",
            "channel_contract": {
                "ofdm_to_slot_reduction": "middle-symbol snapshot; no complex averaging"
            },
        },
    )


def test_missing_h_est_is_a_hard_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = _fake_sample(h_est=None)
    monkeypatch.setattr(gen.ch, "iter_samples", lambda *_args, **_kw: iter([sample]))
    with pytest.raises(RuntimeError, match="禁止静默把 h_true 复制成完美估计"):
        gen._collect(
            "fake", {"num_samples": 1, "channel_est_mode": "ls_linear"},
            want=1, lo=-np.inf, hi=np.inf, filtering=False,
        )


def test_collect_preserves_every_per_cell_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    h_est = np.full((1, 4, 2, 1), 0.9 + 0.8j, dtype=np.complex64)
    samples = [_fake_sample(h_est=h_est, marker=float(i)) for i in range(2)]
    monkeypatch.setattr(gen.ch, "iter_samples", lambda *_args, **_kw: iter(samples))
    payload, first_meta, stats = gen._collect(
        "fake", {"num_samples": 2}, want=2,
        lo=-np.inf, hi=np.inf, filtering=False,
    )
    assert stats["accepted"] == 2
    assert payload["h_true"].shape == payload["h_est"].shape == (2, 1, 4, 2, 1)
    for field in gen._VECTOR_META_FIELDS:
        expected = ((2, 1, 2) if field ==
                    "dl_interference_power_per_slot_per_cell_mw" else (2, 2))
        assert payload[f"metavec__{field}"].shape == expected
    assert np.array_equal(payload["meta__serving_cell_index"], np.array([0.0, 0.0]))
    assert first_meta["channel_contract"]["ofdm_to_slot_reduction"].startswith(
        "middle-symbol snapshot"
    )


def test_multicell_channelhub_site_state_contract_is_hard_gated() -> None:
    cfg = {"num_sites": 2, "sectors_per_site": 3}
    with pytest.raises(RuntimeError, match="site_state_policy"):
        ch._validate_internal_site_state_contract(SimpleNamespace(meta={}), cfg)

    meta = {
        "site_state_policy": "same_site_shared_cross_site_independent_v1",
        "physical_site_group_ids": [0, 0, 0, 1, 1, 1],
        "is_los_all": [True, True, True, False, False, False],
        "sample_tau_rms_all_ns": [50.0, 50.0, 50.0, 120.0, 120.0, 120.0],
        "shadow_fading_all_db": [1.5, 1.5, 1.5, -2.0, -2.0, -2.0],
    }
    ch._validate_internal_site_state_contract(SimpleNamespace(meta=meta), cfg)

    meta["sample_tau_rms_all_ns"] = [50.0, 51.0, 50.0, 120.0, 120.0, 120.0]
    with pytest.raises(RuntimeError, match="同一物理站"):
        ch._validate_internal_site_state_contract(SimpleNamespace(meta=meta), cfg)


def test_ue_panel_is_derived_from_ports_and_explicit_geometry_wins() -> None:
    cfg = {"num_ue_rx_ant": 4}
    panel, derived = gen._ensure_ue_panel(cfg)
    assert panel == [2, 1, 2]
    assert derived is True
    assert cfg["num_ue_rx_ant"] == 4

    explicit = {"num_ue_rx_ant": 2, "ue_panel": [1, 2, 2]}
    panel, derived = gen._ensure_ue_panel(explicit)
    assert panel == [1, 2, 2]
    assert derived is False
    assert explicit["num_ue_rx_ant"] == 4


def test_pdp_window_and_periodic_axis_avoid_wraparound_delay_inflation() -> None:
    n_rb = 272
    scs = 30_000.0
    delay_s = 13e-9
    rb = np.arange(n_rb, dtype=float)
    response = np.exp(-1j * 2.0 * np.pi * rb * 12.0 * scs * delay_s)
    h = response[None, :, None, None].astype(np.complex64)
    result = measure.power_delay_profile(h, subcarrier_spacing_hz=scs)
    # Hann 仪器核已经去嵌：单径不再带约 5.918 ns 的假扩展，更不能把
    # 周期末端泄漏误作数百 ns 的真实多径（最老实现约 474 ns）。
    assert result.rms_delay_spread_s * 1e9 < 0.01
    assert result.mean_delay_s * 1e9 == pytest.approx(13.0, abs=0.01)
    assert result.power_conservation_ratio == pytest.approx(1.0, abs=1e-12)


def test_pdp_two_path_moments_and_long_positive_delay_are_physical() -> None:
    n_rb = 272
    scs = 30_000.0
    df = 12.0 * scs
    rb = np.arange(n_rb, dtype=float)
    # 80/20 power at 0/500 ns -> mean=100 ns, RMS=200 ns exactly.
    response = (
        np.sqrt(0.8)
        + np.sqrt(0.2) * np.exp(-1j * 2.0 * np.pi * rb * df * 500e-9)
    )
    result = measure.power_delay_profile(
        response[None, :, None, None].astype(np.complex64),
        subcarrier_spacing_hz=scs,
    )
    assert result.mean_delay_s * 1e9 == pytest.approx(100.0, abs=0.01)
    assert result.rms_delay_spread_s * 1e9 == pytest.approx(200.0, abs=0.01)

    # 2 us is inside the 2.778 us unambiguous period.  A fixed signed branch
    # used to report it as -777.8 ns; circular unwrapping must keep it at 2 us.
    long_path = np.exp(-1j * 2.0 * np.pi * rb * df * 2e-6)
    long_result = measure.power_delay_profile(
        long_path[None, :, None, None].astype(np.complex64),
        subcarrier_spacing_hz=scs,
    )
    assert long_result.mean_delay_s * 1e9 == pytest.approx(2000.0, abs=0.01)
    assert long_result.rms_delay_spread_s * 1e9 < 0.01
    assert long_result.unambiguous_period_s == pytest.approx(1.0 / df)


def test_company_presets_use_one_rbg_17_hop_10_ms_srs() -> None:
    presets = pl.load_presets()
    for name in (
        "company_64t4r",
        "company_64t4r_multicell",
        "company_64t4r_legacy_array",
    ):
        cfg = presets[name]["config"]
        assert cfg["srs_periodicity"] == 20  # slots; 0.5 ms/slot at 30 kHz
        assert cfg["srs_c_srs"] == 63
        assert cfg["srs_b_srs"] == 1
        assert cfg["srs_b_hop"] == 0
        srs = ph.srs_config(
            cfg["num_rb"],
            c_srs=cfg["srs_c_srs"],
            b_srs=cfg["srs_b_srs"],
            b_hop=cfg["srs_b_hop"],
            periodicity=cfg["srs_periodicity"],
        )
        assert srs["rb_per_hop"] == 16
        assert srs["hopping_cycle_length"] == 17
        assert srs["periodicity_slots"] * 0.5 == pytest.approx(10.0)


def test_end_to_end_company_channel_is_64_by_4_with_real_estimate() -> None:
    cfg = dict(pl.load_presets()["company_64t4r"]["config"])
    cfg.update(
        {
            "num_ues": 1,
            "num_rb": 16,
            # Keep the smoke test small while remaining a valid 38.211
            # resource.  The preceding test hard-gates the real 272-RB preset.
            "srs_c_srs": 3,
            "srs_b_srs": 0,
            "srs_b_hop": 0,
            "num_ofdm_symbols": 14,
            "num_slots_per_sample": 1,
            "seed": 1701,
            "ue_seed": 1702,
            "measurements": {"ssb_rsrp": False},
        }
    )
    summary = gen.generate(cfg, num_samples=1, workers=1)
    dataset = load(summary["dataset_id"])
    assert dataset.h_true.shape == dataset.h_est.shape == (1, 1, 16, 64, 4)
    assert dataset.h_dl_est is not None
    assert dataset.h_dl_est.shape == dataset.h_true.shape
    np.testing.assert_array_equal(
        dataset.precoding_csi_sources, np.array(["ul_srs_estimate"])
    )
    assert np.isfinite(dataset.h_true).all()
    assert np.isfinite(dataset.h_est).all()
    assert not np.array_equal(dataset.h_true, dataset.h_est)
    assert float(dataset.estimation_error_nmse_db()[0]) < 0.0
    assert summary["ue_panel"] == [2, 1, 2]
    assert summary["ue_panel_derived"] is True
    assert dataset.channel_contract["h_est_missing_policy"].startswith("hard_error")
    assert dataset.channel_contract["ofdm_to_slot_reduction"].startswith(
        "middle-symbol snapshot"
    )
    assert "rs_opportunity_model" in dataset.channel_contract
    assert summary["rs_opportunity"]["slot_accurate"] is True
    assert summary["antenna_model"]["polarization_slant_angles_deg"] == [45.0, -45.0]
    assert summary["antenna_model"]["element_horizontal_hpbw_deg"] == 110.0
    assert summary["config"]["bs_antenna"]["element_pattern"][
        "polarization_slant_angles_deg"
    ] == [45.0, -45.0]
    assert dataset.channel_contract["h_true_role"].startswith("downlink")
    assert dataset.channel_contract["h_est_role"].startswith("gNB precoding CSI")
    effective = summary["effective_channel_model"]
    assert effective in {"CDL-C", "CDL-D"}
    np.testing.assert_array_equal(dataset.effective_channel_models, np.array([effective]))
    assert dataset.paths(index=0).model == effective
    assert summary["effective_channel_model_counts"] == {effective: 1}
    checks = {check.name: check for check in dataset.validate().checks}
    assert effective in checks["CDL 剖面对标 38.901"].detail
    assert "状态与剖面匹配 1/1" in checks["场景与信道模型自洽"].detail


def test_static_internal_sim_parallel_is_worker_count_invariant() -> None:
    cfg = {
        "source": "internal_sim",
        "scenario": "UMa_NLOS",
        "channel_model": "CDL-C",
        "num_sites": 1,
        "sectors_per_site": 1,
        "num_ues": 2,
        "num_interfering_ues": 0,
        "num_bs_tx_ant": 4,
        "num_bs_rx_ant": 4,
        "num_ue_tx_ant": 2,
        "num_ue_rx_ant": 2,
        "num_rb": 4,
        "num_ofdm_symbols": 1,
        "channel_est_mode": "ideal",
        "link": "DL",
        "mobility_mode": "static",
        "ue_speed_kmh": 0.0,
        "seed": 20260812,
        "measurements": {"ssb_rsrp": False},
    }
    serial_summary = gen.generate(dict(cfg), num_samples=4, workers=1)
    parallel_summary = gen.generate(dict(cfg), num_samples=4, workers=2)
    serial = load(serial_summary["dataset_id"])
    parallel = load(parallel_summary["dataset_id"])

    assert parallel_summary["parallel"]["workers"] == 2
    assert "逐样本、逐位一致" in parallel_summary["parallel"]["note"]
    np.testing.assert_array_equal(parallel.h_true, serial.h_true)
    np.testing.assert_array_equal(parallel.h_est, serial.h_est)
    np.testing.assert_array_equal(parallel.sinr_dB, serial.sinr_dB)


def test_parallel_semantics_gate_rejects_stateful_or_unindexed_sources() -> None:
    assert gen._parallel_exactness_blocker(
        "sionna_rt", {}, filtering=False
    ) is not None
    assert gen._parallel_exactness_blocker(
        "internal_sim",
        {"mobility_mode": "linear", "ue_speed_kmh": 120.0},
        filtering=False,
    ) is not None
    assert gen._parallel_exactness_blocker(
        "internal_sim", {"mobility_mode": "static"}, filtering=True
    ) is not None

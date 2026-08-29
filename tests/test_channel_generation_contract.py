"""信道生成窄腰契约：H_true/H_est、阵列维度和逐小区元数据。"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superran import (  # noqa: E402
    bridge,
    gates,  # noqa: E402
    interference,
    load,
    measure,
    provenance,
    results,
    scenario,
)
from superran import channelhub as ch  # noqa: E402
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
        h_est=np.ones((2, 1, 1, 1, 1)),
        precoding_csi_sources=np.array(["ul_srs_estimate", "ul_srs_estimate"]),
    )
    incomplete = SimpleNamespace(
        h_est=np.ones((2, 1, 1, 1, 1)),
        precoding_csi_sources=np.array(["ul_srs_estimate"]),
    )
    unverifiable = SimpleNamespace(
        precoding_csi_sources=np.array(["ul_srs_estimate"]),
    )
    assert not gates._precoding_source_item(wrong, "ul_srs_estimate").passed
    assert gates._precoding_source_item(right, "ul_srs_estimate").passed
    assert not gates._precoding_source_item(incomplete, "ul_srs_estimate").passed
    assert not gates._precoding_source_item(unverifiable, "ul_srs_estimate").passed


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
    assert ch.SUPERRAN_RECIPROCITY_CONTRACT.startswith("superran-tdd-")


def test_reciprocity_contract_rejects_axis_shape_mismatch() -> None:
    with pytest.raises(RuntimeError, match="同一 canonical 轴"):
        ch.ul_estimate_to_dl_precoding_csi(
            np.ones((1, 2, 4, 2), dtype=np.complex64),
            expected_shape=(1, 2, 8, 2),
        )


def test_comparison_csi_selector_keeps_srs_and_csirs_distinct() -> None:
    srs = np.full((2, 1, 1, 1, 1), 1 + 2j, dtype=np.complex64)
    csirs = np.full((2, 1, 1, 1, 1), 3 + 4j, dtype=np.complex64)
    ds = SimpleNamespace(
        h_est=srs, h_dl_est=csirs,
        precoding_csi_sources=np.array(["ul_srs_estimate"] * 2),
    )
    np.testing.assert_array_equal(gates._precoding_csi_tensor(ds, "srs"), srs)
    np.testing.assert_array_equal(gates._precoding_csi_tensor(ds, "csirs"), csirs)
    assert gates._precoding_csi_tensor(ds, "ideal") is None
    with pytest.raises(ValueError, match="禁止静默回退"):
        gates._precoding_csi_tensor(SimpleNamespace(h_est=srs, h_dl_est=None), "csirs")
    with pytest.raises(ValueError, match="禁止把 DL CSI-RS"):
        gates._precoding_csi_tensor(
            SimpleNamespace(
                h_est=srs,
                precoding_csi_sources=np.array(["dl_csirs_estimate"] * 2),
            ),
            "srs",
        )
    with pytest.raises(ValueError, match="标签数 1/2"):
        gates._precoding_csi_tensor(
            SimpleNamespace(
                h_est=srs,
                precoding_csi_sources=np.array(["ul_srs_estimate"]),
            ),
            "srs",
        )


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
        precoding_csi_sources=np.array(["ul_srs_estimate"] * 4),
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


def test_mobile_comparison_clusters_by_stable_ue_id_not_position() -> None:
    h = np.ones((4, 1, 1, 1, 1), dtype=np.complex64)

    class MobileDataset(SimpleNamespace):
        def scalar(self, name: str) -> np.ndarray:
            if name == "ue_id":
                return np.array([0, 1, 0, 1])
            raise KeyError(name)

    ds = MobileDataset(
        h_true=h,
        h_est=h,
        h_dl_est=h,
        h_interferers=None,
        ue_position=np.array(
            [[0.0, 0.0, 1.5], [10.0, 0.0, 1.5],
             [1.0, 0.0, 1.5], [11.0, 0.0, 1.5]]
        ),
        dataset_id="ds_mobile_cluster_test",
        config={"mobility_mode": "linear"},
        precoding_csi_sources=np.array(["ul_srs_estimate"] * 4),
    )
    result = gates.compare_arms(
        ds,
        {"name": "A", "method": "identity", "csi": "srs"},
        {"name": "B", "method": "identity", "csi": "srs"},
        snr_db=10.0,
    )
    assert result.raw_observations == 4
    assert result.paired.n == 2
    assert result.as_dict()["inference_unit"]["clustered_by"] == "ue_id"


def test_mobile_comparison_without_ue_id_is_blocked() -> None:
    h = np.ones((2, 1, 1, 1, 1), dtype=np.complex64)
    ds = SimpleNamespace(
        h_true=h,
        h_est=h,
        h_dl_est=h,
        h_interferers=None,
        ue_position=np.array([[0.0, 0.0, 1.5], [1.0, 0.0, 1.5]]),
        dataset_id="ds_mobile_no_id",
        config={"mobility_mode": "linear"},
        precoding_csi_sources=np.array(["ul_srs_estimate"] * 2),
    )
    result = gates.compare_arms(
        ds,
        {"name": "A", "method": "identity", "csi": "srs"},
        {"name": "B", "method": "identity", "csi": "srs"},
        snr_db=10.0,
    )
    assert not result.gate2.passed
    assert any(item.severity == "block" for item in result.gate2.items)


def _fake_sample(
    *,
    h_est: np.ndarray | None,
    marker: float = 0.0,
    w_dl: np.ndarray | None = None,
) -> SimpleNamespace:
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
        w_dl=w_dl,
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


def test_collect_ignores_source_precoder_and_records_superran_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h_est = np.full((1, 4, 2, 1), 0.9 + 0.8j, dtype=np.complex64)
    source_weight = np.ones((4, 2, 1), dtype=np.complex64)
    sample = _fake_sample(h_est=h_est, w_dl=source_weight)
    monkeypatch.setattr(gen.ch, "iter_samples", lambda *_args, **_kw: iter([sample]))
    payload, first_meta, stats = gen._collect(
        "fake", {"num_samples": 1}, want=1,
        lo=-np.inf, hi=np.inf, filtering=False,
    )
    assert "w_dl" not in payload
    assert stats["source_precoder_fields_ignored"] == 1
    contract = first_meta["channel_contract"]
    assert contract["reciprocity_contract_version"] == ch.SUPERRAN_RECIPROCITY_CONTRACT
    assert contract["canonical_channel_axes"] == [
        "time", "rb", "bs_port", "ue_port",
    ]
    assert "source-provided w_dl is ignored" in contract["precoder_ownership"]


def test_collect_synthesizes_stable_ue_id_from_first_party_iterator_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h_est = np.full((1, 4, 2, 1), 0.9 + 0.8j, dtype=np.complex64)
    samples = [_fake_sample(h_est=h_est, marker=float(i)) for i in range(4)]
    monkeypatch.setattr(gen.ch, "iter_samples", lambda *_args, **_kw: iter(samples))
    payload, _first_meta, _stats = gen._collect(
        "internal_sim",
        {
            "num_samples": 4,
            "num_ues": 2,
            "sample_index_offset": 3,
            "mobility_mode": "static",
        },
        want=4,
        lo=-np.inf,
        hi=np.inf,
        filtering=False,
    )
    np.testing.assert_array_equal(payload["meta__ue_id"], [1.0, 0.0, 1.0, 0.0])
    np.testing.assert_array_equal(
        payload["metastr__ue_id_source"],
        ["internal_sim_global_index"] * 4,
    )


def test_collect_labels_one_mobile_trajectory_as_one_ue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h_est = np.full((1, 4, 2, 1), 0.9 + 0.8j, dtype=np.complex64)
    samples = [_fake_sample(h_est=h_est, marker=float(i)) for i in range(3)]
    monkeypatch.setattr(gen.ch, "iter_samples", lambda *_args, **_kw: iter(samples))
    payload, _first_meta, _stats = gen._collect(
        "internal_sim",
        {"num_samples": 3, "mobility_mode": "linear", "ue_speed_kmh": 120.0},
        want=3,
        lo=-np.inf,
        hi=np.inf,
        filtering=False,
    )
    np.testing.assert_array_equal(payload["meta__ue_id"], [0.0, 0.0, 0.0])
    np.testing.assert_array_equal(
        payload["metastr__ue_id_source"],
        ["internal_sim_single_trajectory"] * 3,
    )



def test_collect_mirrors_source_layout_for_mobility_speed_edge_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 源端合同（internal_sim.iter_samples）：mobility!="static" 且速度为正才生成
    # 单条轨迹；速度 <=0 时退到静态多 UE 轮转布局。身份合成必须镜像它：
    # 显式 0 速 -> 静态轮转 id（不是"移动缺身份"的误 block）；
    # 速度键缺失 -> 源端默认 3.0 km/h -> 单轨迹 id=0。
    h_est = np.full((1, 4, 2, 1), 0.9 + 0.8j, dtype=np.complex64)
    samples = [_fake_sample(h_est=h_est, marker=float(i)) for i in range(4)]
    monkeypatch.setattr(gen.ch, "iter_samples", lambda *_args, **_kw: iter(samples))
    payload, _m, _s = gen._collect(
        "internal_sim",
        {"num_samples": 4, "num_ues": 2, "mobility_mode": "linear",
         "ue_speed_kmh": 0.0},
        want=4, lo=-np.inf, hi=np.inf, filtering=False,
    )
    np.testing.assert_array_equal(payload["meta__ue_id"], [0.0, 1.0, 0.0, 1.0])
    np.testing.assert_array_equal(
        payload["metastr__ue_id_source"], ["internal_sim_global_index"] * 4)

    samples2 = [_fake_sample(h_est=h_est, marker=float(i)) for i in range(3)]
    monkeypatch.setattr(gen.ch, "iter_samples", lambda *_args, **_kw: iter(samples2))
    payload2, _m2, _s2 = gen._collect(
        "internal_sim",
        {"num_samples": 3, "mobility_mode": "linear"},  # 速度键缺失
        want=3, lo=-np.inf, hi=np.inf, filtering=False,
    )
    np.testing.assert_array_equal(payload2["meta__ue_id"], [0.0, 0.0, 0.0])
    np.testing.assert_array_equal(
        payload2["metastr__ue_id_source"], ["internal_sim_single_trajectory"] * 3)

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
        assert srs["n_ports"] == 2
        assert srs["configured_cyclic_shift_count"] == 4
        assert srs["antenna_port_groups"] == [[0, 1], [2, 3]]
        assert srs["srs_transmissions_per_full_4port_sweep"] == 34
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
    np.testing.assert_array_equal(parallel.scalar("ue_id"), serial.scalar("ue_id"))
    np.testing.assert_array_equal(serial.scalar("ue_id"), [0.0, 1.0, 0.0, 1.0])


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


def test_reciprocity_end_to_end_receive_gain() -> None:
    """端到端互易增益：上游 SRS 约定 (h_ul = conj(h_dl)) 经 SuperRAN 版本化
    映射还原后，本地 SVD 打到真实信道上的谱效必须接近理想 CSI——
    共轭约定若在链路级整体反了，纯映射断言一个都抓不住，只有这条能抓。"""
    from superran import linklevel as lv

    rng = np.random.default_rng(20260817)
    h_dl = ((rng.standard_normal((2, 8, 8, 4))
             + 1j * rng.standard_normal((2, 8, 8, 4))) / np.sqrt(2))
    noise = ((rng.standard_normal(h_dl.shape)
              + 1j * rng.standard_normal(h_dl.shape)) / np.sqrt(2)) * 0.02
    h_ul_est = np.conj(h_dl) + noise  # 上游导出的 SRS 估计（合同轴）
    mapped = ch.ul_estimate_to_dl_precoding_csi(h_ul_est)

    ideal = lv.link_performance(h_dl, snr_db=20.0, method="svd")
    correct = lv.link_performance(h_dl, snr_db=20.0, method="svd",
                                  h_for_precoding=mapped)
    # 错误约定：不做共轭还原，直接拿 SRS 估计当下行 CSI（漏 conj）
    wrong = lv.link_performance(h_dl, snr_db=20.0, method="svd",
                                h_for_precoding=h_ul_est)

    assert correct.spectral_efficiency > wrong.spectral_efficiency, (
        f"正确共轭约定 {correct.spectral_efficiency:.3f} 必须优于错误约定 "
        f"{wrong.spectral_efficiency:.3f}")
    assert correct.spectral_efficiency > 0.9 * ideal.spectral_efficiency, (
        f"估计噪声预算内应接近理想：{correct.spectral_efficiency:.3f} vs "
        f"理想 {ideal.spectral_efficiency:.3f}")


def test_fourth_audit_evidence_guards_are_not_self_defeating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第四轮审查抓到的防线自身漏洞必须有反向回归。"""
    assert gates._identity_fold_status_is_blocking("mobility_missing_id")
    assert gates._identity_fold_status_is_blocking("partition_mismatch")
    assert not gates._identity_fold_status_is_blocking("unavailable")

    src = inspect.getsource(gen.generate)
    assert src.index("dropped_fields: list[str] = []") < src.index("if n_workers > 1:")
    assert "_install_failure" not in interference.install_geometry_capture.__code__.co_varnames

    snr = np.asarray([45.0])
    sir = np.asarray([49.9])
    np.testing.assert_allclose(
        scenario._probe_sinr_from_snr_sir(snr, sir, num_cells=1), snr)
    assert float(scenario._probe_sinr_from_snr_sir(
        snr, sir, num_cells=2)[0]) < float(snr[0])

    monkeypatch.setattr(bridge, "_SEEN_CAP", 3)
    bridge._seen.clear()
    assert not bridge._remember_nonce("n0")
    assert bridge._remember_nonce("n0")
    assert not bridge._remember_nonce("n1")
    assert not bridge._remember_nonce("n2")
    assert not bridge._remember_nonce("n3")
    assert bridge._seen == {"n3"}
    bridge._seen.clear()


def test_provenance_and_semantic_dataset_digest_contract() -> None:
    base = provenance.snapshot(source="unit-test")
    assert base["source_tree_sha256"] and base["source_file_count"]
    same = json.loads(json.dumps(base))
    assert provenance.compare(base, same)["status"] == "match"
    changed = json.loads(json.dumps(base))
    changed["physical_data"]["preset_bler_sha256"] = "changed"
    assert provenance.compare(base, changed)["status"] == "mismatch"
    legacy = json.loads(json.dumps(base))
    legacy_value = legacy["physical_data"].pop("preset_bler_sha256")
    legacy["physical_data"]["company_" + "bler_sha256"] = legacy_value
    assert provenance.compare(legacy, base)["status"] == "match"
    assert provenance.compare(None, base)["status"] == "unknown"

    summary = {
        "source": "internal_sim", "shape": {"N": 2},
        "config": {"scenario": "UMa_NLOS"}, "elapsed_s": 1.0,
    }
    digest = results._semantic_summary_sha256(summary)
    summary["elapsed_s"] = 99.0
    assert results._semantic_summary_sha256(summary) == digest
    summary["config"]["scenario"] = "UMi_NLOS"
    assert results._semantic_summary_sha256(summary) != digest


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

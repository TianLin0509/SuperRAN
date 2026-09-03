"""Unified company 64T/256T port-layout contract and end-to-end smoke."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superran import (  # noqa: E402
    algo_defs,
    algorithms,
    generate,
    load,
    measure,
    physical,
    plan,
)
from superran import (
    hardware as hw,
)
from superran import (
    linklevel as ll,
)


def _array(profile: str = "256t"):
    from superran.native import make_effective_array

    panel = [16, 8, 2] if profile == "256t" else [8, 4, 2]
    cfg = {
        "bs_panel": panel,
        "carrier_freq_hz": 2.6e9,
        "antenna_model_mode": "effective_subarray",
        "bs_antenna": hw.company_antenna_block(profile=profile),
    }
    return make_effective_array(cfg)


def test_product_drawing_order_and_coupling_matrix() -> None:
    arr = _array()
    F = arr.coupling_matrix()
    assert arr.rf_shape == (16, 8, 2)
    assert arr.physical_shape == (16, 48, 2)
    assert arr.port_order == "pol_h_v"
    assert arr.vertical_index_order == "top_to_bottom"
    assert F.shape == (1536, 256)
    assert np.all(np.count_nonzero(np.abs(F) > 1e-12, axis=0) == 6)
    assert np.all(np.count_nonzero(np.abs(F) > 1e-12, axis=1) == 1)
    np.testing.assert_allclose(F.conj().T @ F, np.eye(256), atol=1e-12)

    # Figure, converted to zero-based indices: 1/9/129 start the first H
    # column, second H column, and second-polarization block respectively.
    assert np.flatnonzero(F[:, 0]).tolist() == list(range(0, 6))
    assert np.flatnonzero(F[:, 8]).tolist() == list(range(48, 54))
    assert np.flatnonzero(F[:, 128]).tolist() == list(range(768, 774))
    pos = arr.physical_positions_lambda()
    assert pos[0, 2] > pos[1, 2]  # port/AE 1 is above port/AE 2
    assert pos[48, 1] - pos[0, 1] == pytest.approx(0.5)
    assert pos[1, 2] - pos[0, 2] == pytest.approx(-0.67)


def test_64t_uses_the_same_canonical_order_as_256t() -> None:
    arr = _array("64t")
    F = arr.coupling_matrix()
    assert arr.rf_shape == (8, 4, 2)
    assert arr.physical_shape == (8, 12, 2)
    assert arr.port_order == hw.COMPANY_CANONICAL_PORT_ORDER == "pol_h_v"
    assert (
        arr.vertical_index_order
        == hw.COMPANY_CANONICAL_VERTICAL_INDEX_ORDER
        == "top_to_bottom"
    )
    assert F.shape == (192, 64)
    np.testing.assert_allclose(F.conj().T @ F, np.eye(64), atol=1e-12)

    # r=p*32+h*4+v; e=p*96+h*12+v_phy.  These anchors are the 64T
    # counterpart of 256T ports 1/9/129.
    assert np.flatnonzero(F[:, 0]).tolist() == [0, 1, 2]
    assert np.flatnonzero(F[:, 4]).tolist() == [12, 13, 14]
    assert np.flatnonzero(F[:, 32]).tolist() == [96, 97, 98]
    assert hw.COMPANY_PORT_ORDER == hw.COMPANY_256T_PORT_ORDER
    assert hw.COMPANY_VERTICAL_INDEX_ORDER == hw.COMPANY_256T_VERTICAL_INDEX_ORDER


def test_layout_migration_is_an_exact_physical_permutation() -> None:
    from superran.native import PortIndex

    canonical = PortIndex(8, 4, 2, port_order="pol_h_v", vertical_index_order="top_to_bottom")
    legacy = PortIndex(8, 4, 2, port_order="h_v_pol", vertical_index_order="bottom_to_top")
    rng = np.random.default_rng(640256)
    h_old = rng.standard_normal((64, 4)) + 1j * rng.standard_normal((64, 4))
    w_old = rng.standard_normal((64, 3)) + 1j * rng.standard_normal((64, 3))
    h_new = canonical.permute_from_layout(h_old, legacy, axis=0)
    w_new = canonical.permute_from_layout(w_old, legacy, axis=0)
    np.testing.assert_allclose(
        w_new.conj().T @ h_new,
        w_old.conj().T @ h_old,
        atol=1e-12,
    )


def test_superran_owns_the_type_i_port_boundary_without_msg_runtime_import() -> None:
    """Offline Dataset.pmi() must not need the sibling MSG source tree."""
    from superran.native import PortIndex

    for n_h, n_v in ((8, 4), (16, 8)):
        for port_order, vertical_order in (
            ("pol_h_v", "top_to_bottom"),
            ("h_v_pol", "bottom_to_top"),
        ):
            expected = PortIndex(
                n_h,
                n_v,
                2,
                port_order=port_order,
                vertical_index_order=vertical_order,
            ).type1_to_canonical()
            actual = hw.type1_to_port_permutation(
                n_h,
                n_v,
                2,
                port_order=port_order,
                vertical_index_order=vertical_order,
            )
            np.testing.assert_array_equal(actual, expected)

    assert "msg_embedding" not in inspect.getsource(measure.pmi_type_i)


def test_type_i_search_is_invariant_across_the_legacy_boundary() -> None:
    from superran.native import PortIndex

    canonical = PortIndex(8, 4, 2, port_order="pol_h_v", vertical_index_order="top_to_bottom")
    legacy = PortIndex(8, 4, 2, port_order="h_v_pol", vertical_index_order="bottom_to_top")
    rng = np.random.default_rng(64214)
    h_new = (
        rng.standard_normal((2, 3, 64, 4))
        + 1j * rng.standard_normal((2, 3, 64, 4))
    ).astype(np.complex64)
    h_old = legacy.permute_from_layout(h_new, canonical, axis=2)
    r_new = measure.pmi_type_i(
        h_new,
        n_h=8,
        n_v=4,
        max_rank=1,
        port_order="pol_h_v",
        vertical_index_order="top_to_bottom",
    )
    r_old = measure.pmi_type_i(
        h_old,
        n_h=8,
        n_v=4,
        max_rank=1,
        port_order="h_v_pol",
        vertical_index_order="bottom_to_top",
    )
    w_old_as_new = canonical.permute_from_layout(r_old.precoder, legacy, axis=0)
    correlation = abs(np.vdot(r_new.precoder[:, 0], w_old_as_new[:, 0]))
    np.testing.assert_allclose(r_new.layer_gain_db, r_old.layer_gain_db, atol=1e-6)
    assert correlation > 0.999999


def test_type_i_and_csirs_are_applied_in_drawing_order() -> None:
    rng = np.random.default_rng(20260813)
    h = (
        rng.standard_normal((1, 4, 256, 4))
        + 1j * rng.standard_normal((1, 4, 256, 4))
    ).astype(np.complex64)
    result = measure.pmi_type_i(
        h,
        n_h=16,
        n_v=8,
        max_rank=2,
        port_order="pol_h_v",
        vertical_index_order="top_to_bottom",
    )
    assert result.precoder.shape == (256, result.rank)
    perf = ll.link_performance(
        h,
        snr_db=10.0,
        method="type1",
        max_rank=2,
        n_h=16,
        n_v=8,
        port_order="pol_h_v",
        vertical_index_order="top_to_bottom",
    )
    assert perf.spectral_efficiency > 0
    cb = physical.dft_codebook(
        16,
        8,
        2,
        port_order="pol_h_v",
        vertical_index_order="top_to_bottom",
    )
    assert cb.shape[1] == 256
    assert np.allclose(np.linalg.norm(cb, axis=1), 1.0)


def test_256t_direct_pmi_inference_does_not_fall_back_to_a_line_array() -> None:
    rng = np.random.default_rng(256)
    h = (
        rng.standard_normal((1, 1, 256, 2))
        + 1j * rng.standard_normal((1, 1, 256, 2))
    ).astype(np.complex64)
    result = measure.pmi_type_i(
        h,
        max_rank=1,
        port_order="pol_h_v",
        vertical_index_order="top_to_bottom",
    )
    assert result.layout == (16, 8)
    assert result.port_order == "pol_h_v"
    assert result.vertical_index_order == "top_to_bottom"


def test_algorithm_pages_report_both_company_profiles_as_physical() -> None:
    for panel, n_ports, feed in (([8, 4, 2], 64, "1 驱 3"), ([16, 8, 2], 256, "1 驱 6")):
        cfg = {"bs_panel": panel, "num_bs_tx_ant": n_ports}
        item = next(x for x in algorithms.algorithm_list(cfg) if x["key"] == "antenna_model")
        assert feed in item["choice"]
        assert "legacy_64" not in item["choice"]
        family = next(x for x in algo_defs.families(cfg) if x["key"] == "antenna_model")
        assert family["current"] == "effective_subarray"
        assert "pol_h_v" in family["caveat"]

    unknown = {"bs_panel": [32, 1, 2], "num_bs_tx_ant": 64}
    item = next(
        x for x in algorithms.algorithm_list(unknown) if x["key"] == "antenna_model"
    )
    assert "legacy_64" in item["choice"]
    family = next(x for x in algo_defs.families(unknown) if x["key"] == "antenna_model")
    assert family["current"] == "legacy_64"


def test_array_summary_never_mislabels_an_explicit_legacy_layout_as_canonical() -> None:
    cfg = {
        "bs_panel": [8, 4, 2],
        "antenna_model_mode": "effective_subarray",
        "bs_antenna": {
            "port_order": "h_v_pol",
            "vertical_index_order": "bottom_to_top",
            "fixed_vertical_subarray": {
                "elements_per_rf_port": 3,
                "ae_vertical_spacing_lambda": 0.67,
            },
        },
    }
    summary = hw.array_summary(cfg, "explicit_bs_antenna")
    assert summary["port_layout_contract_version"] == (
        "h_v_pol-bottom_to_top-legacy-v1"
    )


def test_calibration_id_versions_layout_and_downtilt() -> None:
    ant6 = hw.company_antenna_block(profile="64t", fixed_downtilt_deg=6.0)
    ant8 = hw.company_antenna_block(profile="64t", fixed_downtilt_deg=8.0)
    id6 = ant6["fixed_vertical_subarray"]["calibration_id"]
    id8 = ant8["fixed_vertical_subarray"]["calibration_id"]
    assert "pol-h-v-top-down" in id6
    assert id6.endswith("-dt6deg")
    assert id8.endswith("-dt8deg")
    assert id6 != id8


def test_256t_preset_resolves_to_physical_profile() -> None:
    cfg = dict(plan.load_presets()["massive_mimo_256t"]["config"])
    hw.apply_array_defaults(cfg)
    marker = hw.strip_markers(cfg)
    assert marker == "company_256t_1to6_1536ae"
    ant = cfg["bs_antenna"]
    assert ant["port_order"] == "pol_h_v"
    assert ant["vertical_index_order"] == "top_to_bottom"
    assert ant["fixed_vertical_subarray"]["elements_per_rf_port"] == 6
    assert ant["fixed_vertical_subarray"]["fixed_downtilt_deg"] == pytest.approx(
        hw.DEFAULT_ELECTRICAL_DOWNTILT_DEG
    )


def test_64t_classic_preset_resolves_to_unified_layout() -> None:
    cfg = dict(plan.load_presets()["company_64t4r"]["config"])
    cfg["bs_panel"] = [8, 4, 2]
    hw.apply_array_defaults(cfg)
    marker = hw.strip_markers(cfg)
    assert marker == "company_1to3_192ae"
    ant = cfg["bs_antenna"]
    assert ant["port_order"] == "pol_h_v"
    assert ant["vertical_index_order"] == "top_to_bottom"
    assert (
        ant["fixed_vertical_subarray"]["calibration_vertical_index_order"]
        == "top_to_bottom"
    )


@pytest.mark.parametrize(
    ("preset", "n_ports", "n_ae", "feed"),
    [
        ("company_64t4r", 64, 192, 3),
        ("massive_mimo_256t", 256, 1536, 6),
    ],
)
def test_internal_sim_minimal_company_array_generation(
    preset: str,
    n_ports: int,
    n_ae: int,
    feed: int,
) -> None:
    cfg = dict(plan.load_presets()[preset]["config"])
    cfg.update(
        {
            "num_sites": 1,
            "sectors_per_site": 1,
            "num_ues": 1,
            "num_interfering_ues": 0,
            "num_rb": 4,
            "num_ofdm_symbols": 1,
            "num_slots_per_sample": 1,
            "link": "DL",
            "channel_est_mode": "ideal",
            "measurements": {"ssb_rsrp": False},
            "seed": 256001,
            "ue_seed": 256002,
        }
    )
    for key in ("srs_c_srs", "srs_b_srs", "srs_b_hop"):
        cfg.pop(key, None)
    summary = generate.generate(cfg, num_samples=1, workers=1)
    ds = load(summary["dataset_id"])
    assert ds.h_true.shape == (1, 1, 4, n_ports, 4)
    md = summary["antenna_model"]
    assert md["port_order"] == "pol_h_v"
    assert md["vertical_index_order"] == "top_to_bottom"
    assert md["port_layout_contract_version"] == "pol_h_v-top_to_bottom-v1"
    assert md["elements_per_rf_port"] == feed
    assert md["physical_elements"] == n_ae
    assert summary["sample_meta"]["antenna_profile"] == (
        f"fixed_1to{feed}_vertical_subarray_{n_ports}T"
    )
    pmi = ds.pmi(index=0, max_rank=2)
    assert pmi.precoder.shape[0] == n_ports
    assert pmi.port_order == "pol_h_v"
    assert pmi.vertical_index_order == "top_to_bottom"
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

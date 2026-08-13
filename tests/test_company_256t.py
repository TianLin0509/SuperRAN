"""Company 256T product-drawing contract and SuperRAN end-to-end smoke."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from superran import hardware as hw  # noqa: E402
from superran import linklevel as ll  # noqa: E402
from superran import load  # noqa: E402
from superran import measure  # noqa: E402
from superran import physical  # noqa: E402
from superran import plan  # noqa: E402
from superran import generate  # noqa: E402


def _array():
    from superran import channelhub

    channelhub._ensure_path()
    from msg_embedding.phy_sim.effective_array import make_effective_array

    cfg = {
        "bs_panel": [16, 8, 2],
        "carrier_freq_hz": 2.6e9,
        "antenna_model_mode": "effective_subarray",
        "bs_antenna": hw.company_antenna_block(profile="256t"),
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
    )
    assert perf.spectral_efficiency > 0
    cb = physical.dft_codebook(16, 8, 2, port_order="pol_h_v")
    assert cb.shape[1] == 256
    assert np.allclose(np.linalg.norm(cb, axis=1), 1.0)


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


def test_internal_sim_minimal_256t_generation() -> None:
    cfg = dict(plan.load_presets()["massive_mimo_256t"]["config"])
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
    summary = generate.generate(cfg, num_samples=1, workers=1)
    ds = load(summary["dataset_id"])
    assert ds.h_true.shape == (1, 1, 4, 256, 4)
    md = summary["antenna_model"]
    assert md["port_order"] == "pol_h_v"
    assert md["vertical_index_order"] == "top_to_bottom"
    assert md["elements_per_rf_port"] == 6
    assert md["physical_elements"] == 1536
    assert summary["sample_meta"]["antenna_profile"] == (
        "fixed_1to6_vertical_subarray_256T"
    )
    pmi = ds.pmi(index=0, max_rank=2)
    assert pmi.precoder.shape[0] == 256

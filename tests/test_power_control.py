from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from superran import experience as ex
from superran import linkadapt as la
from superran import power_control as pc
from superran import system as sysm


def test_channelhub_power_terms_survive_dataset_storage(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source -> adapter -> NPZ -> loader preserves the exact S/N/I contract."""
    from superran.generate import generate
    from superran.loader import load

    monkeypatch.setenv("SUPERRAN_ARTIFACTS", str(tmp_path / "artifacts"))
    cfg = {
        "num_sites": 2,
        "sectors_per_site": 1,
        "num_ues": 1,
        "num_rb": 4,
        "num_ofdm_symbols": 1,
        "num_bs_tx_ant": 4,
        "num_bs_rx_ant": 4,
        "num_ue_tx_ant": 2,
        "num_ue_rx_ant": 2,
        "carrier_freq_hz": 3.5e9,
        "subcarrier_spacing": 30e3,
        "channel_model": "CDL-C",
        "channel_est_mode": "ideal",
        "num_interfering_ues": 0,
        "seed": 991,
        "scenario": "UMa_NLOS",
        "custom_site_positions": [
            {"x": 0.0, "y": 0.0, "z": 25.0},
            {"x": 500.0, "y": 0.0, "z": 25.0},
        ],
        "custom_ue_positions": [{"x": 100.0, "y": 0.0, "z": 1.5}],
        "mobility_mode": "static",
        "measurements": {"ssb_rsrp": False},
    }
    result = generate(cfg, num_samples=1, workers=1, collect_ssb=False)
    ds = load(result["dataset_id"])
    geometry = pc.geometry_from_dataset(ds)

    assert geometry.interference_power_mw.shape == (1, 1, 2)
    serving = int(geometry.serving_cell_index[0])
    assert geometry.interference_power_mw[0, 0, serving] == 0.0
    reconstructed = 10.0 * np.log10(
        geometry.signal_power_mw
        / (
            geometry.thermal_noise_power_mw
            + np.sum(geometry.interference_power_mw, axis=(1, 2))
        )
    )
    np.testing.assert_allclose(reconstructed, ds.sinr_dB, atol=1e-12, rtol=0.0)


def test_partial_override_balances_without_changing_user_values() -> None:
    cfg = pc.RbPowerControlConfig.from_raw(
        enabled=True, num_rb=272,
        overrides=[{"cell_index": 0, "rb": 0, "multiplier": 4.0}])
    q = cfg.resolve_profiles(2)
    assert q.shape == (2, 272)
    assert q[0, 0] == 4.0
    assert np.allclose(q[0, 1:], (272.0 - 4.0) / 271.0)
    assert np.sum(q[0], dtype=np.float64) == pytest.approx(272.0, abs=1e-12)
    assert np.array_equal(q[1], np.ones(272))


def test_override_validation_hard_fails_instead_of_renormalizing() -> None:
    with pytest.raises(ValueError, match="无法.*补偿"):
        pc.RbPowerControlConfig.from_raw(
            enabled=True, num_rb=16,
            overrides=[{"rb_start": 0, "rb_end": 3, "multiplier": 4.0}],
        ).resolve_profiles(1)
    with pytest.raises(ValueError, match="重叠"):
        pc.RbPowerControlConfig.from_raw(
            enabled=True, num_rb=16,
            overrides=[
                {"rb_start": 0, "rb_end": 3, "multiplier": 2.0},
                {"rb": 3, "multiplier": 0.5},
            ],
        ).resolve_profiles(1)
    with pytest.raises(ValueError, match="必须等于"):
        pc.RbPowerControlConfig.from_raw(
            enabled=True, num_rb=2,
            overrides=[{"rb": 0, "multiplier": 2.0},
                       {"rb": 1, "multiplier": 2.0}],
        ).resolve_profiles(1)
    with pytest.raises(ValueError, match="0.1,4"):
        pc.RbPowerControlConfig.from_raw(
            enabled=True, num_rb=16,
            overrides=[{"rb": 0, "multiplier": 4.01}])
    with pytest.raises(ValueError, match="只接受"):
        pc.RbPowerControlConfig.from_raw(
            enabled="sometimes", num_rb=16, overrides=[])
    with pytest.raises(ValueError, match="必须是布尔值"):
        pc.RbPowerControlConfig.from_raw(enabled=2, num_rb=16, overrides=[])


def test_exact_signal_and_per_cell_interference_coupling() -> None:
    profiles = np.array([
        [1.5, 0.5],   # serving cell
        [1.0, 1.0],
        [0.5, 1.5],   # one interferer changes independently
    ])
    c = pc.couple_rb_power(
        signal_power_mw=10.0, thermal_noise_power_mw=1.0,
        interference_power_per_cell_mw=np.array([0.0, 2.0, 4.0]),
        serving_cell_index=0, profiles=profiles, neighbor_utilization=0.5)
    expected_i = 0.5 * np.array([2.0 * 1.0 + 4.0 * 0.5,
                                 2.0 * 1.0 + 4.0 * 1.5])
    expected_sinr = np.array([1.5, 0.5]) * 10.0 / (1.0 + expected_i)
    assert np.allclose(c.controlled_interference_mw, expected_i)
    assert np.allclose(10.0 ** (c.geometric_sinr_db / 10.0), expected_sinr)
    baseline_d = 1.0 + 0.5 * 6.0
    assert np.allclose(
        c.channel_power_scale,
        np.array([1.5, 0.5]) * baseline_d / (1.0 + expected_i))


def test_low_level_coupling_rejects_non_conserving_profiles() -> None:
    with pytest.raises(ValueError, match="总和必须"):
        pc.couple_rb_power(
            signal_power_mw=1.0, thermal_noise_power_mw=0.1,
            interference_power_per_cell_mw=np.array([0.0, 0.2]),
            serving_cell_index=0,
            profiles=np.array([[2.0, 2.0], [1.0, 1.0]]),
            neighbor_utilization=1.0)
    with pytest.raises(ValueError, match="serving-cell"):
        pc.couple_rb_power(
            signal_power_mw=1.0, thermal_noise_power_mw=0.1,
            interference_power_per_cell_mw=np.array([0.01, 0.2]),
            serving_cell_index=0,
            profiles=np.array([[1.0, 1.0], [1.0, 1.0]]),
            neighbor_utilization=1.0)
    with pytest.raises(ValueError, match="形状不一致"):
        pc.couple_rb_power(
            signal_power_mw=1.0, thermal_noise_power_mw=0.1,
            interference_power_per_cell_mw=np.array([0.0, 0.2]),
            serving_cell_index=0, profiles=np.empty((2, 0)),
            neighbor_utilization=1.0)


def test_power_geometry_rejects_nonzero_serving_interference() -> None:
    with pytest.raises(ValueError, match="serving-cell"):
        pc.DownlinkPowerGeometry(
            serving_cell_index=np.array([0]), signal_power_mw=np.array([1.0]),
            thermal_noise_power_mw=np.array([0.1]),
            interference_power_mw=np.array([[[0.2, 0.3]]]))


def _flat_link_inputs(num_rb: int = 32):
    h = [np.ones((1, num_rb, 2, 1), dtype=np.complex64)]
    signal, noise, intf = 10.0, 1.0, 2.0
    geometry = pc.DownlinkPowerGeometry(
        serving_cell_index=np.array([0]), signal_power_mw=np.array([signal]),
        thermal_noise_power_mw=np.array([noise]),
        interference_power_mw=np.array([[[0.0, intf]]]))
    sinr = 10.0 * np.log10(signal / (noise + intf))
    sir = 10.0 * np.log10(signal / intf)
    return h, geometry, sinr, sir


def test_disabled_path_is_unchanged_and_enabled_keeps_rb_resolution() -> None:
    h, geometry, sinr, sir = _flat_link_inputs()
    baseline = sysm.build_link_tables(
        h, [sinr], geo_sir_db=[sir], max_rank=1, rb_per_rbg=16,
        neighbor_load=1.0, neighbor_load_jitter=0.0)
    disabled = sysm.build_link_tables(
        h, [sinr], geo_sir_db=[sir], max_rank=1, rb_per_rbg=16,
        neighbor_load=1.0, neighbor_load_jitter=0.0,
        rb_power_control=pc.RbPowerControlConfig(enabled=False, num_rb=32),
        power_geometry=geometry)
    assert np.array_equal(baseline[0].sinr_db, disabled[0].sinr_db)
    assert np.array_equal(baseline[0].mcs, disabled[0].mcs)

    cfg = pc.RbPowerControlConfig.from_raw(
        enabled=True, num_rb=32,
        overrides=[
            {"cell_index": 0, "rb_start": 0, "rb_end": 15,
             "multiplier": 1.5},
            {"cell_index": 0, "rb_start": 16, "rb_end": 31,
             "multiplier": 0.5},
            {"cell_index": 1, "rb_start": 0, "rb_end": 15,
             "multiplier": 0.5},
            {"cell_index": 1, "rb_start": 16, "rb_end": 31,
             "multiplier": 1.5},
        ])
    table = sysm.build_link_tables(
        h, [sinr], geo_sir_db=[sir], max_rank=1, rb_per_rbg=16,
        neighbor_load=1.0, neighbor_load_jitter=0.0,
        rb_power_control=cfg, power_geometry=geometry)[0]
    assert table.frequency_rows_per_rbg == 16
    assert table.h_true_rbg is not None and table.h_true_rbg.shape[1] == 32
    assert table.sinr_rbg_db is not None and table.sinr_rbg_db.shape == (1, 1, 2)
    # RBG0: desired up, interferer down. RBG1 is the opposite.
    assert table.sinr_rbg_db[0, 0, 0] > table.sinr_rbg_db[0, 0, 1]


def test_frequency_grant_uses_its_actual_rbg_bitmap() -> None:
    lookup = ex.TbsLookup.build(num_rbg=2, rb_per_rbg=16)
    table = SimpleNamespace(
        sinr_rbg_db=np.array([[[20.0, -5.0]]]),
        sinr_tx_rbg_db=np.array([[[20.0, -5.0]]]),
    )
    high = ex._frequency_su_values(
        table=table, snap=0, rank=1, indices=(0,), olla_db=0.0,
        olla_enabled=True, lookup=lookup, slot="D")
    low = ex._frequency_su_values(
        table=table, snap=0, rank=1, indices=(1,), olla_db=0.0,
        olla_enabled=True, lookup=lookup, slot="D")
    both = ex._frequency_su_values(
        table=table, snap=0, rank=1, indices=(0, 1), olla_db=0.0,
        olla_enabled=True, lookup=lookup, slot="D")
    assert high["mcs"] > low["mcs"]
    assert high["true"] == 20.0 and low["true"] == -5.0
    assert both["true"] == pytest.approx(7.5)


def test_experience_mcs_selector_uses_lookup_table_and_target() -> None:
    lookup = ex.TbsLookup.build(17, 16, mcs_table=3, target_bler=0.1)
    values = np.concatenate((
        np.linspace(-30.0, 40.0, 701),
        np.array([np.nan, -np.inf, np.inf]),
    ))
    legacy = np.asarray([
        la.select_mcs(float(value), table=3, target_bler=0.1).index
        for value in values
    ], dtype=int)
    selected = np.asarray([ex._select_mcs(float(value), lookup) for value in values])
    assert np.array_equal(selected, legacy)


def test_experience_mcs_selector_matches_legacy_at_threshold_boundaries() -> None:
    # 边界探针（C2 回归）：门限 ±1e-12 处快路径必须与 select_mcs 逐位等价——
    # linspace 网格扫不到 inclusive 边界行为，而缓存门限的边界语义一旦漂移，
    # 系统仿真会在同一个 SINR 上悄悄换档。
    lookup = ex.TbsLookup.build(17, 16, mcs_table=3, target_bler=0.1)
    cached = la._mcs_thresholds(3, 0.1, 20000, 1)
    assert cached is not None
    thresholds, _inclusive = cached
    thr = np.asarray(thresholds, dtype=float)
    probe = np.concatenate((thr - 1e-12, thr, thr + 1e-12))
    legacy = np.asarray([
        la.select_mcs(float(v), table=3, target_bler=0.1).index for v in probe
    ], dtype=int)
    selected = np.asarray([ex._select_mcs(float(v), lookup) for v in probe])
    assert np.array_equal(selected, legacy)


def test_long_run_fractional_prb_ledger_uses_scale_aware_tolerance() -> None:
    # Same totals accumulated in user-first and TTI-first order in a 5 s run.
    assert ex._resource_totals_close(78622.40000000094, 78622.39999999978)
    # The tolerance is still many orders below one physical RBG.
    assert not ex._resource_totals_close(78622.4, 78622.399)


def test_system_config_reports_frequency_and_spatial_constraints_separately() -> None:
    power = pc.RbPowerControlConfig.from_raw(enabled=True, num_rb=32)
    cfg = sysm.SystemConfig(
        num_rbg=2, rb_per_rbg=16, power_constraint="nebf",
        rb_power_control=power)
    data = cfg.as_dict()
    assert data["power_constraint"] == "nebf"
    assert data["rb_power_control"]["enabled"] is True
    assert data["rb_power_control"]["total_power_constraint"].startswith("sum")


def test_simulation_rejects_profile_mislabel_and_cross_cell_resource_pool() -> None:
    h = [np.ones((1, 16, 2, 1), dtype=np.complex64) for _ in range(2)]
    geometry = pc.DownlinkPowerGeometry(
        serving_cell_index=np.array([0, 1]),
        signal_power_mw=np.array([10.0, 10.0]),
        thermal_noise_power_mw=np.array([1.0, 1.0]),
        interference_power_mw=np.array([[[0.0, 2.0]], [[2.0, 0.0]]]))
    uniform = pc.RbPowerControlConfig.from_raw(enabled=True, num_rb=16)
    tables = sysm.build_link_tables(
        h, [10.0, 10.0], geo_sir_db=[10.0, 10.0], max_rank=1,
        rb_per_rbg=16, neighbor_load=1.0, neighbor_load_jitter=0.0,
        rb_power_control=uniform, power_geometry=geometry)

    shaped = pc.RbPowerControlConfig.from_raw(
        enabled=True, num_rb=16,
        overrides=[{"cell_index": 0, "rb": 0, "multiplier": 2.0}])
    with pytest.raises(ValueError, match="不是同一份 profile"):
        sysm.simulate(
            tables,
            sys_cfg=sysm.SystemConfig(
                evaluation_mode="experience", num_rbg=1, rb_per_rbg=16,
                duration_s=0.01, rb_power_control=shaped),
            traffic=sysm.TrafficConfig(model="full_buffer"),
            sched=sysm.SchedulerConfig(mu_enabled=False),
            kpi=sysm.KpiConfig(warmup_s=0.0))

    with pytest.raises(ValueError, match="不同 serving cell"):
        sysm.simulate(
            tables,
            sys_cfg=sysm.SystemConfig(
                evaluation_mode="experience", num_rbg=1, rb_per_rbg=16,
                duration_s=0.01, rb_power_control=uniform),
            traffic=sysm.TrafficConfig(model="full_buffer"),
            sched=sysm.SchedulerConfig(mu_enabled=False),
            kpi=sysm.KpiConfig(warmup_s=0.0))

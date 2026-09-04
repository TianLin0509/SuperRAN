"""载波依赖、Type-0 RBG 尾组与体验 PRB 对账回归。"""
from __future__ import annotations

import numpy as np
import pytest

from superran import carrier, generate, measure, mumimo, plan, rng, server, spec, system
from superran import csi_aging as ca
from superran import experience as ex


@pytest.mark.parametrize(
    ("num_rb", "cfg", "p", "sizes"),
    [
        (36, 1, 2, (2,) * 18),
        (36, 2, 4, (4,) * 9),
        (51, 2, 8, (8, 8, 8, 8, 8, 8, 3)),
        (106, 2, 16, (16, 16, 16, 16, 16, 16, 10)),
        (272, 2, 16, (16,) * 17),
        (273, 2, 16, (16,) * 17 + (1,)),
    ],
)
def test_type0_rbg_table_and_tail_are_exact(
    num_rb: int, cfg: int, p: int, sizes: tuple[int, ...]
) -> None:
    grid = carrier.CarrierGrid.from_config(
        {"subcarrier_spacing": 30_000, "rbg_size_config": cfg},
        num_rb=num_rb,
    )
    assert grid.nominal_rb_per_rbg == p
    assert grid.rbg_prb_sizes == sizes
    assert sum(grid.rbg_prb_sizes) == num_rb
    assert grid.as_dict()["excluded_num_rb"] == 0


def test_non_aligned_bwp_start_has_partial_first_and_last_groups() -> None:
    grid = carrier.CarrierGrid.from_config(
        {"subcarrier_spacing": 30_000, "rbg_size_config": 2, "bwp_start_rb": 3},
        num_rb=20,
    )
    assert grid.nominal_rb_per_rbg == 4
    assert grid.rbg_prb_sizes == (1, 4, 4, 4, 4, 3)
    assert grid.boundaries[0] == (0, 1)
    assert grid.boundaries[-1] == (17, 20)


@pytest.mark.parametrize("raw", [0, -30_000, "bad", 44_000, float("nan")])
def test_invalid_scs_never_falls_back_to_30_khz(raw: object) -> None:
    with pytest.raises(ValueError):
        carrier.CarrierGrid.from_config({"subcarrier_spacing": raw}, num_rb=51)


def test_bandwidth_override_invalidates_preset_num_rb_and_srs_geometry() -> None:
    draft, _ = plan.create_draft(
        "20 MHz 载波",
        preset="company_64t4r",
        overrides={"bandwidth_hz": 20_000_000.0},
    )
    assert "num_rb" not in draft.params
    assert all(key not in draft.params for key in (
        "srs_c_srs", "srs_b_srs", "srs_b_hop", "srs_n_rrc"))
    assert generate._rb_from_bandwidth(draft.params) == 51
    assert any("自动清除 num_rb=272" in row for row in draft.history)


def test_scs_override_keeps_slot_period_but_reports_changed_physical_time() -> None:
    draft, _ = plan.create_draft(
        "60 kHz SCS 载波",
        preset="company_64t4r",
        overrides={"subcarrier_spacing": 60_000},
    )
    assert draft.params["srs_periodicity"] == 20
    assert any(
        "SCS 改变后对应的毫秒数会变化" in row
        for row in draft.history
    )


def test_explicit_num_rb_override_is_preserved_as_custom_grid() -> None:
    draft, _ = plan.create_draft(
        "合成窄带探针",
        preset="company_64t4r",
        overrides={"bandwidth_hz": 20_000_000.0, "num_rb": 24},
    )
    assert draft.params["num_rb"] == 24
    assert all(key not in draft.params for key in (
        "srs_c_srs", "srs_b_srs", "srs_b_hop", "srs_n_rrc"))


def test_bwp_start_override_keeps_width_but_invalidates_srs_geometry() -> None:
    draft, _ = plan.create_draft(
        "非对齐 BWP 起点",
        preset="company_64t4r",
        overrides={"bwp_start_rb": 3},
    )
    assert draft.params["num_rb"] == 272
    assert all(key not in draft.params for key in (
        "srs_c_srs", "srs_b_srs", "srs_b_hop", "srs_n_rrc"))
    assert not any("自动清除 num_rb" in row for row in draft.history)


def test_company_tdd_grid_is_fixed_and_not_user_editable() -> None:
    grid = carrier.CarrierGrid.company_tdd(
        {
            "bandwidth_hz": 100_000_000.0,
            "subcarrier_spacing": 30_000,
        },
        num_rb=272,
    )
    assert grid.rbg_prb_sizes == (16,) * 17
    assert grid.user_configurable is False
    assert grid.standard_num_rb == 273
    assert grid.as_dict()["standard_tail_rb_omitted_before_generation"] == 1
    assert "num_rb" not in spec.editable_keys()
    assert "rbg_size_config" not in spec.editable_keys()

    bad_cases = (
        ({"bandwidth_hz": 20_000_000.0, "subcarrier_spacing": 30_000}, 272),
        ({"bandwidth_hz": 100_000_000.0, "subcarrier_spacing": 15_000}, 272),
        ({"bandwidth_hz": 100_000_000.0, "subcarrier_spacing": 30_000}, 273),
        ({"subcarrier_spacing": 30_000, "rbg_size_config": 1}, 272),
        ({"subcarrier_spacing": 30_000, "bwp_start_rb": 1}, 272),
    )
    for config, num_rb in bad_cases:
        with pytest.raises(ValueError, match="SuperRAN TDD"):
            carrier.CarrierGrid.company_tdd(config, num_rb=num_rb)


def test_system_adaptation_metadata_distinguishes_auto_and_override() -> None:
    out = server._system_adaptation_contract(
        target_bler=0.2,
        olla_step_up_db=0.01,
        olla_step_down_db=None,
        resolved_su_down=0.04,
        mu_olla_step_up_db=0.02,
        mu_olla_step_down_db=0.3,
        resolved_mu_down=0.3,
    )
    assert out["olla_configuration"]["su"]["step_down_source"] == (
        "auto_from_target_bler"
    )
    assert out["olla_configuration"]["mu"]["step_down_source"] == (
        "explicit_user_override"
    )
    assert out["olla_configuration"]["su"]["step_down_db"] == pytest.approx(0.04)
    assert out["olla_configuration"]["domain"] == "continuous_mcs_index"
    assert out["olla_configuration"]["su"]["step_down_mcs"] == pytest.approx(0.04)
    assert out["mcs_profile"]["table"] == 3
    assert out["mcs_profile"]["profile"] == "preset_20b_256qam"
    assert out["mcs_profile"]["cqi_to_mcs"] == [
        0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28]
    assert out["mcs_profile"]["scope"] == "experience_v2 fixed preset table"


def test_variable_rbg_tbs_uses_actual_bitmap_not_group_count() -> None:
    grid = carrier.CarrierGrid.from_config(
        {"subcarrier_spacing": 30_000}, num_rb=51)
    lookup = ex.TbsLookup.build(
        grid.num_rbg, grid.nominal_rb_per_rbg,
        rbg_prb_sizes=grid.rbg_prb_sizes,
    )
    tail = lookup.tbs_bytes_for_indices("D", 12, 2, (6,))
    full = lookup.tbs_bytes_for_indices("D", 12, 2, (0,))
    assert tail < full
    assert tail == lookup._tbs_for_prbs("D", 12, 2, 3)
    assert full == lookup._tbs_for_prbs("D", 12, 2, 8)
    need, fits = lookup.required_rbg_for_indices(
        "D", 12, 2, full, (6, 0, 1, 2, 3, 4, 5))
    assert (need, fits) == (2, True)


def test_tbs_plateau_on_one_prb_tail_keeps_exact_first_fit_inverse() -> None:
    grid = carrier.CarrierGrid.from_config(
        {"subcarrier_spacing": 30_000}, num_rb=273)
    lookup = ex.TbsLookup.build(
        grid.num_rbg, grid.nominal_rb_per_rbg,
        rbg_prb_sizes=grid.rbg_prb_sizes,
    )
    meta = lookup.as_dict()
    assert meta["non_decreasing"] is True
    assert meta["strictly_increasing"] is False
    row = lookup.row("D", 0, 1)
    assert row[-1] == row[-2]
    need, fits = lookup.required_rbg("D", 0, 1, int(row[-1]))
    assert (need, fits) == (17, True)


def test_tbs_lookup_rejects_unsupported_table_and_lossy_prb_sizes() -> None:
    with pytest.raises(ValueError, match="只支持 MCS table 3"):
        ex.TbsLookup.build(2, 8, mcs_table=2)
    with pytest.raises(ValueError, match="不能是布尔值或小数"):
        ex.TbsLookup.build(2, 8, rbg_prb_sizes=(8, 3.5))
    with pytest.raises(ValueError, match="不能是布尔值或小数"):
        ex.TbsLookup.build(2, 8, rbg_prb_sizes=(8, True))


@pytest.mark.parametrize(
    ("config", "num_rb"),
    [
        ({"subcarrier_spacing": 30_000, "rbg_size_config": 2.0}, 51),
        ({"subcarrier_spacing": 30_000, "bwp_start_rb": 3.5}, 51),
        ({"subcarrier_spacing": 30_000}, 51.5),
    ],
)
def test_carrier_grid_rejects_lossy_integer_coercion(
    config: dict[str, object], num_rb: object
) -> None:
    with pytest.raises(ValueError, match="必须是整数"):
        carrier.CarrierGrid.from_config(config, num_rb=num_rb)  # type: ignore[arg-type]


def test_boundary_helpers_reject_fractional_indices() -> None:
    with pytest.raises(ValueError, match="必须是整数"):
        carrier.validate_boundaries(8, ((0, 3.5), (3, 8)))
    with pytest.raises(ValueError, match="必须是整数"):
        carrier.uniform_boundaries(8, 2.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="必须是整数"):
        carrier.expand_rbg_values(
            (1.0, 2.0), ((0, 4), (4, 8)), num_rows=8.5  # type: ignore[arg-type]
        )


def test_bitmap_helpers_reject_lossy_integer_coercion() -> None:
    with pytest.raises(ValueError, match="RBG index 必须是整数"):
        carrier.prb_count((0.9,), (8, 8))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="PRB 数 必须是整数"):
        carrier.prb_count((0,), (8.5, 8))  # type: ignore[arg-type]

    lookup = ex.TbsLookup.build(2, 8)
    with pytest.raises(ValueError, match="RBG index 必须是整数"):
        lookup.tbs_bytes_for_indices("D", 12, 2, (0.9,))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="RBG index 必须是整数"):
        lookup.required_rbg_for_indices(
            "D", 12, 2, 100, (0, True)  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="num_prb 必须至少为 1"):
        lookup._tbs_for_prbs("D", 12, 2, 1.9)  # type: ignore[arg-type]


def test_mu_gain_rejects_mixed_ue_rb_grids_at_entry() -> None:
    h0 = np.ones((1, 51, 4, 2), dtype=np.complex128)
    h1 = np.ones((1, 52, 4, 2), dtype=np.complex128)
    with pytest.raises(ValueError, match="UE0=51 RB，UE1=52 RB"):
        system.measure_mu_gain([h0, h1], [10.0, 10.0])


def test_rbg_reduce_keeps_partial_tail_representative() -> None:
    h = np.arange(51, dtype=float)[:, None, None]
    grid = carrier.CarrierGrid.from_config(
        {"subcarrier_spacing": 30_000}, num_rb=51)
    reduced = mumimo.rbg_reduce(
        h, grid.nominal_rb_per_rbg, rbg_boundaries=grid.boundaries)
    assert reduced.shape == (7, 1, 1)
    np.testing.assert_array_equal(
        reduced[:, 0, 0], np.asarray(grid.representative_rb_indices))


def test_su_rank_adaptation_uses_true_partial_rbg_boundaries() -> None:
    # 8 个强 RB + 2 个深衰 RB。把它们错当一个 10-RB 组会被强 RB 淹没；
    # 正确的两个 RBG 口径会让尾组在 dB 域平均中显式产生影响。
    h = np.empty((1, 10, 1, 1), dtype=np.complex128)
    h[:, :8, 0, 0] = 10.0
    h[:, 8:, 0, 0] = 0.1
    exact = mumimo.su_rank_adaptation(
        h,
        noise_power=1.0,
        max_rank=1,
        rbg_boundaries=((0, 8), (8, 10)),
    )
    collapsed = mumimo.su_rank_adaptation(
        h,
        noise_power=1.0,
        max_rank=1,
        rbg_boundaries=((0, 10),),
    )
    assert exact.sinr_db == pytest.approx(0.0, abs=1e-10)
    assert collapsed.sinr_db > 15.0
    assert exact.mcs < collapsed.mcs


def test_system_config_rejects_fractional_or_boolean_prb_sizes() -> None:
    with pytest.raises(ValueError, match="不能是布尔值或小数"):
        system.SystemConfig(num_rbg=2, rbg_prb_sizes=(8, 3.5))
    with pytest.raises(ValueError, match="不能是布尔值或小数"):
        system.SystemConfig(num_rbg=2, rbg_prb_sizes=(8, True))


def test_srs_hopping_blocks_unverified_non_company_grid() -> None:
    with pytest.raises(ValueError, match="只验证了预置 272 PRB"):
        ca.validate_hopping_grid(ca.CsiConfig(hopping=True), (8, 8, 8, 8, 8, 8, 3))
    with pytest.raises(ValueError, match="必须全部是正整数"):
        ca.validate_hopping_grid(ca.CsiConfig(hopping=True), (16.9,) * 17)
    ca.validate_hopping_grid(ca.CsiConfig(hopping=False), (8, 8, 8, 8, 8, 8, 3))
    ca.validate_hopping_grid(ca.CsiConfig(hopping=True), (16,) * 17)


def test_type1_cache_has_bounded_256t_memory_risk() -> None:
    assert measure.type_i_codebook.cache_parameters()["maxsize"] == 4


def test_full_buffer_partial_grid_prb_accounting_is_exact() -> None:
    generator = np.random.default_rng(816)
    channels = [
        ((generator.standard_normal((2, 51, 4, 2))
          + 1j * generator.standard_normal((2, 51, 4, 2))) / np.sqrt(2))
        for _ in range(2)
    ]
    grid = carrier.CarrierGrid.from_config(
        {"subcarrier_spacing": 30_000}, num_rb=51)
    tables = system.build_link_tables(
        channels, [15.0, 15.0], max_rank=2,
        rb_per_rbg=grid.nominal_rb_per_rbg,
        rbg_boundaries=grid.boundaries,
    )
    cfg = system.SystemConfig(
        duration_s=0.01,
        tdd_pattern="DDDD", num_rbg=grid.num_rbg,
        rb_per_rbg=grid.nominal_rb_per_rbg,
        rbg_prb_sizes=grid.rbg_prb_sizes,
    )
    run = system.simulate(
        tables,
        sys_cfg=cfg,
        traffic=system.TrafficConfig(model="full_buffer"),
        sched=system.SchedulerConfig(olla_enabled=False, mu_enabled=False),
        kpi=system.KpiConfig(warmup_tti=0),
        rng=rng.RngBook(master_seed=816),
    )
    assert run.cell["serving_cell_prb_utilization"] == pytest.approx(1.0)
    assert run.cell["allocated_prb_equivalent"] == pytest.approx(
        run.cell["available_prb_equivalent"])
    assert run.cell["available_prb_equivalent"] == pytest.approx(
        cfg.num_tti * 51)
    assert sum(
        float(user["allocated_prb_equivalent_attributed"])
        for user in run.users
    ) == pytest.approx(run.cell["allocated_prb_equivalent"])
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

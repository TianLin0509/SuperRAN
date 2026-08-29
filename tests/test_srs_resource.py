"""Tests for the 2T4R / four-CS / 17-FDM SRS resource allocator."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran.srs_resource import (  # noqa: E402
    PCI_MOD3_COLOR_BY_SYMBOL_COMB,
    SRS_BBL_LEAVES,
    SRS_CYCLIC_SHIFT_COUNT,
    SRS_FREQUENCY_RESOURCE_COUNT,
    SRS_LEAF_ROLE_BY_SYMBOL_COMB,
    SrsResourceAllocator,
    SrsResourceRequest,
    allocate_basic_srs_resources,
    cross_cell_collision_report,
    pci_mod3_preference_order,
    pci_mod3_resource_color,
    resources_collide,
    srs_leaf_role,
)


def test_user_confirmed_table_excludes_bbl_and_has_eight_leaves_per_colour() -> None:
    expected_roles = {
        (10, 0): 0, (10, 1): 1,
        (11, 0): 2, (11, 1): "bbl",
        (12, 0): 1, (12, 1): 2,
        (13, 0): "bbl", (13, 1): 0,
    }
    assert SRS_LEAF_ROLE_BY_SYMBOL_COMB == expected_roles
    assert SRS_BBL_LEAVES == {(11, 1), (13, 0)}
    assert PCI_MOD3_COLOR_BY_SYMBOL_COMB == {
        key: value for key, value in expected_roles.items() if value != "bbl"
    }
    assert [srs_leaf_role(symbol, comb)
            for symbol in range(10, 14) for comb in (0, 1)] == [
        0, 1, 2, "bbl", 1, 2, "bbl", 0]
    with pytest.raises(ValueError, match="BBL"):
        pci_mod3_resource_color(11, 1)
    assert pci_mod3_preference_order(0) == (0,)
    assert pci_mod3_preference_order(1) == (1,)
    assert pci_mod3_preference_order(2) == (2,)

    # Four SRS opportunities in the 20-ms drawing, two ordinary leaves per
    # colour in each opportunity -> eight visible ordinary leaves per colour.
    per_opportunity = {
        colour: sum(value == colour for value in expected_roles.values())
        for colour in range(3)
    }
    assert per_opportunity == {0: 2, 1: 2, 2: 2}
    assert {colour: count * 4 for colour, count in per_opportunity.items()} == {
        0: 8, 1: 8, 2: 8}


def test_one_leaf_has_four_cs_and_carries_two_2t4r_ues() -> None:
    assert SRS_CYCLIC_SHIFT_COUNT == 4
    rows = allocate_basic_srs_resources(
        [0, 1], period_ms=10.0, n_ports_by_ue=4,
        cell_ids=0, pci_mod3_by_ue=0, adaptive_period=False)
    first, second = rows
    assert (first.symbol, first.comb_offset, first.frequency_resource_id) == (
        second.symbol, second.comb_offset, second.frequency_resource_id)
    assert first.cyclic_shifts == (0, 1)
    assert second.cyclic_shifts == (2, 3)
    assert first.resource_color == second.resource_color == 0
    assert not resources_collide(first, second)

    for row in rows:
        assert row.n_ports == 4
        assert row.tx_ports_per_occasion == 2
        assert row.antenna_port_groups == ((0, 1), (2, 3))
        assert len(row.legs) == 2
        assert row.legs[1].offset_slots - row.legs[0].offset_slots == 10
        assert row.legs[1].offset_ms - row.legs[0].offset_ms == pytest.approx(5.0)
        assert row.legs[0].frequency_resource_id == row.legs[1].frequency_resource_id
        assert row.legs[0].cyclic_shifts == row.legs[1].cyclic_shifts


def test_deterministic_intra_cell_allocation_is_collision_free() -> None:
    first = allocate_basic_srs_resources(
        list(range(40)), period_ms=10.0, n_ports_by_ue=4,
        cell_ids=0, adaptive_period=False)
    second = allocate_basic_srs_resources(
        list(range(40)), period_ms=10.0, n_ports_by_ue=4,
        cell_ids=0, adaptive_period=False)
    assert first == second
    assert len({
        (row.offset_slots, row.symbol, row.comb_offset,
         row.frequency_resource_id, row.cyclic_shifts)
        for row in first
    }) == len(first)
    assert not any(
        resources_collide(first[i], first[j])
        for i in range(len(first)) for j in range(i + 1, len(first))
    )
    assert all(
        (leg.symbol, leg.comb_offset) not in SRS_BBL_LEAVES
        for row in first for leg in row.legs)
    assert all(row.preference_tier == 0 for row in first)


def test_capacity_and_global_shortest_period_adaptation() -> None:
    allocator = SrsResourceAllocator()
    assert SRS_FREQUENCY_RESOURCE_COUNT == 17
    assert allocator.capacity_ues(period_ms=10.0, n_ports=4) == 68
    assert allocator.capacity_ues(period_ms=20.0, n_ports=4) == 136
    assert allocator.capacity_ues(period_ms=40.0, n_ports=4) == 272
    assert len(allocate_basic_srs_resources(
        range(4), period_ms=10.0, hopping=False,
        adaptive_period=False)) == 4
    with pytest.raises(RuntimeError, match="no global SRS period"):
        allocate_basic_srs_resources(
            range(5), period_ms=10.0, hopping=False,
            adaptive_period=False)

    fixed = allocate_basic_srs_resources(
        range(68), period_ms=10.0, adaptive_period=False)
    assert {row.period_ms for row in fixed} == {10.0}
    with pytest.raises(RuntimeError, match="no global SRS period"):
        allocate_basic_srs_resources(
            range(69), period_ms=10.0, adaptive_period=False)

    medium = allocate_basic_srs_resources(range(69), period_ms=10.0)
    assert {row.period_ms for row in medium} == {20.0}
    slow = allocate_basic_srs_resources(range(137), period_ms=10.0)
    assert {row.period_ms for row in slow} == {40.0}
    with pytest.raises(RuntimeError, match="own PCI-mod-3"):
        allocate_basic_srs_resources(range(273), period_ms=10.0)


def test_global_period_is_set_by_the_most_loaded_cell_without_colour_spill() -> None:
    # Cell 0 fits 10 ms, but cell 2 has 69 UEs and therefore forces every cell
    # to the same 20-ms period.  Spare colour-0 resources are never borrowed.
    ue_ids = list(range(10)) + list(range(69))
    cell_ids = [0] * 10 + [2] * 69
    rows = allocate_basic_srs_resources(
        ue_ids, cell_ids=cell_ids, pci_mod3_by_ue=[0] * 10 + [2] * 69)
    assert {row.period_ms for row in rows} == {20.0}
    assert all(row.resource_color == row.pci_mod3 for row in rows)
    assert all(row.preference_tier == 0 for row in rows)


def test_release_restores_resource_and_duplicate_request_is_idempotent() -> None:
    allocator = SrsResourceAllocator()
    request = SrsResourceRequest(ue_id=7, cell_id=3, period_ms=10.0, n_ports=4)
    assigned = allocator.allocate(request)
    assert allocator.allocate(request) == assigned
    assert allocator.release(3, 7) == assigned
    assert allocator.allocate(request) == assigned
    with pytest.raises(KeyError, match="no SRS assignment"):
        allocator.release(3, 99)


def test_collision_checks_both_legs_frequency_and_periodic_time() -> None:
    base = SrsResourceAllocator().allocate(
        SrsResourceRequest(ue_id=0, cell_id=0, pci_mod3=0, period_ms=10.0))
    same = replace(base, ue_id=1, cell_id=1)
    assert resources_collide(base, same)

    other_frequency_legs = tuple(
        replace(leg, frequency_resource_id=1) for leg in same.legs)
    other_frequency = replace(same, legs=other_frequency_legs)
    assert not resources_collide(base, other_frequency)

    # A 20-ms pair at offsets 27/37 still collides periodically with the
    # 10-ms pair at 7/17 when every other resource dimension is identical.
    shifted_legs = tuple(
        replace(leg, offset_slots=leg.offset_slots + 20,
                offset_ms=leg.offset_ms + 10.0)
        for leg in same.legs)
    periodic = replace(
        same, period_ms=20.0, period_slots=40, legs=shifted_legs)
    assert resources_collide(base, periodic)


def test_pci_mod3_own_colours_remove_light_load_cross_cell_collision() -> None:
    aligned = allocate_basic_srs_resources(
        [0, 0, 0], cell_ids=[0, 1, 2], pci_mod3_by_ue=[0, 0, 0],
        period_ms=10.0, adaptive_period=False)
    staggered = allocate_basic_srs_resources(
        [0, 0, 0], cell_ids=[0, 1, 2], pci_mod3_by_ue=[0, 1, 2],
        period_ms=10.0, adaptive_period=False)
    aligned_report = cross_cell_collision_report(aligned)
    staggered_report = cross_cell_collision_report(staggered)
    assert aligned_report.colliding_pair_count == 3
    assert aligned_report.pilot_interference_to_signal_ratio == pytest.approx(2.0)
    assert staggered_report.colliding_pair_count == 0
    assert staggered_report.pilot_interference_to_signal_ratio == 0.0
    assert staggered_report.ls_nmse_proxy < aligned_report.ls_nmse_proxy
    assert len({
        (row.symbol, row.comb_offset) for row in staggered
    }) == 3
    assert {row.cyclic_shifts for row in staggered} == {(0, 1)}


def test_input_contracts_fail_loudly() -> None:
    for ports in (1, 2, 3, 8):
        with pytest.raises(ValueError, match="n_ports"):
            SrsResourceRequest(ue_id=0, n_ports=ports)
    with pytest.raises(ValueError, match="period_ms"):
        SrsResourceRequest(ue_id=0, period_ms=15.0)
    with pytest.raises(ValueError, match="period_ms"):
        SrsResourceRequest(ue_id=0, period_ms=5.0)
    with pytest.raises(ValueError, match="same length"):
        allocate_basic_srs_resources([0, 1], n_ports_by_ue=[4])
    with pytest.raises(ValueError, match="ue_id.*唯一"):
        allocate_basic_srs_resources([0, 0], cell_ids=[3, 3])
    assert len(allocate_basic_srs_resources(
        [0, 0], cell_ids=[3, 4], pci_mod3_by_ue=[0, 1])) == 2
    with pytest.raises(ValueError, match="boolean"):
        allocate_basic_srs_resources([0], pci_mod3_by_ue=True)
    with pytest.raises(ValueError, match="adaptive_period"):
        allocate_basic_srs_resources([0], adaptive_period=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="share one pci_mod3"):
        allocate_basic_srs_resources(
            [0, 1], cell_ids=[0, 0], pci_mod3_by_ue=[0, 1])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

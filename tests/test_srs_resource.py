"""Tests for the fixed-carrier basic SRS resource allocator."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran.srs_resource import (  # noqa: E402
    SrsResourceAllocator,
    SrsResourceRequest,
    allocate_basic_srs_resources,
    cross_cell_collision_report,
    resources_collide,
)


def test_deterministic_intra_cell_allocation_is_collision_free() -> None:
    first = allocate_basic_srs_resources(
        list(range(12)), period_ms=10.0, n_ports_by_ue=4, cell_ids=0)
    second = allocate_basic_srs_resources(
        list(range(12)), period_ms=10.0, n_ports_by_ue=4, cell_ids=0)
    assert first == second
    assert len({(row.offset_slots, row.symbol, row.comb_offset,
                 row.cyclic_shifts) for row in first}) == len(first)
    assert not any(
        resources_collide(first[i], first[j])
        for i in range(len(first)) for j in range(i + 1, len(first))
    )


def test_four_port_ten_ms_pool_has_32_hard_capacity() -> None:
    allocator = SrsResourceAllocator()
    assert allocator.capacity_ues(period_ms=10.0, n_ports=4) == 32
    for ue in range(32):
        allocator.allocate(SrsResourceRequest(ue_id=ue, n_ports=4))
    with pytest.raises(RuntimeError, match="exhausted"):
        allocator.allocate(SrsResourceRequest(ue_id=32, n_ports=4))


def test_release_restores_resource_and_duplicate_request_is_idempotent() -> None:
    allocator = SrsResourceAllocator()
    request = SrsResourceRequest(ue_id=7, cell_id=3, period_ms=10.0, n_ports=2)
    assigned = allocator.allocate(request)
    assert allocator.allocate(request) == assigned
    assert allocator.release(3, 7) == assigned
    reassigned = allocator.allocate(request)
    assert reassigned == assigned
    with pytest.raises(KeyError, match="no SRS assignment"):
        allocator.release(3, 99)


def test_periodic_collision_uses_gcd_not_equal_offset_only() -> None:
    base = SrsResourceAllocator().allocate(SrsResourceRequest(ue_id=0, period_ms=10.0))
    # 10 ms offset 7 and 20 ms offset 27 coincide every 20 ms.
    colliding = replace(
        base, ue_id=1, period_ms=20.0, period_slots=40,
        offset_slots=27, offset_ms=13.5)
    orthogonal_time = replace(
        colliding, ue_id=2, offset_slots=17, offset_ms=8.5)
    assert resources_collide(base, colliding)
    assert not resources_collide(base, orthogonal_time)


def test_pci_mod3_preference_reduces_light_load_pilot_collision() -> None:
    aligned = allocate_basic_srs_resources(
        [0, 0, 0], cell_ids=[0, 1, 2], pci_mod3_by_ue=[0, 0, 0],
        period_ms=10.0, n_ports_by_ue=4)
    staggered = allocate_basic_srs_resources(
        [0, 0, 0], cell_ids=[0, 1, 2], pci_mod3_by_ue=[0, 1, 2],
        period_ms=10.0, n_ports_by_ue=4)
    aligned_report = cross_cell_collision_report(aligned)
    staggered_report = cross_cell_collision_report(staggered)
    assert aligned_report.colliding_pair_count == 3
    assert aligned_report.pilot_interference_to_signal_ratio == pytest.approx(2.0)
    assert staggered_report.colliding_pair_count == 0
    assert staggered_report.pilot_interference_to_signal_ratio == 0.0
    assert staggered_report.ls_nmse_proxy < aligned_report.ls_nmse_proxy


def test_input_contracts_fail_loudly() -> None:
    with pytest.raises(ValueError, match="n_ports"):
        SrsResourceRequest(ue_id=0, n_ports=3)
    with pytest.raises(ValueError, match="period_ms"):
        SrsResourceRequest(ue_id=0, period_ms=15.0)
    with pytest.raises(ValueError, match="same length"):
        allocate_basic_srs_resources([0, 1], n_ports_by_ue=[4])
    with pytest.raises(ValueError, match="boolean"):
        allocate_basic_srs_resources([0], pci_mod3_by_ue=True)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

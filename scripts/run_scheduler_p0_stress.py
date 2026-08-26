"""Moderate-duration stress runs for SRS and scheduler P0 contracts."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from run_scheduler_p0_validation import _pair_link, _su_table  # noqa: E402

from superran import system as sy  # noqa: E402
from superran.scheduler_resource import ResourceBudget, ResourceLedger  # noqa: E402
from superran.srs_resource import SrsResourceAllocator, SrsResourceRequest  # noqa: E402


def _frequency_stress() -> dict[str, object]:
    tables = []
    for ue in range(12):
        rows = np.roll(
            np.concatenate([np.full(6, 20.0), np.full(11, -2.0)]), ue % 17)
        tables.append(_su_table(ue, rows.tolist(), best_rank=1))
    start = time.perf_counter()
    run = sy.simulate(
        tables,
        sys_cfg=sy.SystemConfig(
            evaluation_mode="experience", duration_s=1.0,
            tdd_pattern="DDDSU", seed=20260825),
        traffic=sy.TrafficConfig(model="full_buffer"),
        sched=sy.SchedulerConfig(
            mu_enabled=False, olla_enabled=False,
            frequency_selective="on"),
        kpi=sy.KpiConfig(warmup_tti=0, tti_trace_mode="sampled",
                         tti_trace_max_points=64))
    wall = time.perf_counter() - start
    resource = run.cell["resource_ledger"]
    finalizer = run.cell["grant_finalizer"]
    assert resource["physical_overlap_violations"] == 0
    assert resource["max_layers_used"] <= 4
    assert finalizer["plan_final_mismatch_count"] == 0
    assert run.cell["frequency_selection"]["grant_count"] > 1_000
    return {
        "duration_s": 1.0, "num_ues": 12, "tti_count": 2_000,
        "wall_s": wall, "cell_served_mbps": run.cell["cell_served_mbps"],
        "frequency_selection": run.cell["frequency_selection"],
        "resource_ledger": resource, "grant_finalizer": finalizer,
        "trace_rows": len(run.diagnostics["tti_trace"]["rows"]),
        "verdict": "PASS",
    }


def _mu_stress() -> dict[str, object]:
    n_ue = 8
    bases = [20.0 - 0.5 * ue for ue in range(n_ue)]
    tables = [
        _su_table(ue, [bases[ue]] * 17, rank2_base=bases[ue])
        for ue in range(n_ue)]
    for left in range(n_ue):
        for right in range(left + 1, n_ue):
            link = _pair_link(
                left, right, bases[left], bases[right],
                correlation=0.1 + 0.02 * (right - left),
                corr_loss_db=-1.0 - 0.1 * (right - left))
            tables[left].mu_links[right] = link
            tables[right].mu_links[left] = link
    start = time.perf_counter()
    run = sy.simulate(
        tables,
        sys_cfg=sy.SystemConfig(
            evaluation_mode="experience", duration_s=0.5,
            tdd_pattern="D", seed=20260825),
        traffic=sy.TrafficConfig(model="full_buffer"),
        sched=sy.SchedulerConfig(
            mu_enabled=True, olla_enabled=False,
            frequency_selective="on"),
        kpi=sy.KpiConfig(warmup_tti=0, tti_trace_mode="sampled",
                         tti_trace_max_points=64))
    wall = time.perf_counter() - start
    scoring = run.cell["mu_candidate_scoring"]
    assert scoring["candidate_count"] > 5_000
    assert scoring["feasible_count"] > 5_000
    assert run.cell["resource_ledger"]["physical_overlap_violations"] == 0
    assert run.cell["resource_ledger"]["max_layers_used"] <= 4
    assert run.cell["grant_finalizer"]["plan_final_mismatch_count"] == 0
    return {
        "duration_s": 0.5, "num_ues": n_ue, "tti_count": 1_000,
        "wall_s": wall, "cell_served_mbps": run.cell["cell_served_mbps"],
        "mu_candidate_scoring": scoring,
        "su_mu_plan": run.cell["su_mu_plan"],
        "resource_ledger": run.cell["resource_ledger"],
        "grant_finalizer": run.cell["grant_finalizer"],
        "trace_rows": len(run.diagnostics["tti_trace"]["rows"]),
        "verdict": "PASS",
    }


def _srs_capacity_stress() -> dict[str, object]:
    allocator = SrsResourceAllocator()
    start = time.perf_counter()
    for ue in range(32):
        allocator.allocate(SrsResourceRequest(
            ue_id=ue, cell_id=0, period_ms=10.0, n_ports=4))
    exhausted = False
    try:
        allocator.allocate(SrsResourceRequest(
            ue_id=32, cell_id=0, period_ms=10.0, n_ports=4))
    except RuntimeError:
        exhausted = True
    wall = time.perf_counter() - start
    assert exhausted and len(allocator.assignments) == 32
    return {
        "period_ms": 10.0, "ports": 4, "allocated_ues": 32,
        "ue_33_hard_failed": exhausted, "wall_s": wall,
        "verdict": "PASS",
    }


def _ledger_transaction_stress() -> dict[str, object]:
    start = time.perf_counter()
    iterations = 10_000
    for tti in range(iterations):
        ledger = ResourceLedger(
            ResourceBudget(17, (16,) * 17, max_layers_per_rbg=4), tti=tti)
        first = ledger.reserve(
            grant_index=0, mode="SU", users=(0,), ranks=(2,),
            rbg_indices=(0, 1, 2))
        ledger.commit(first.reservation_id)
        second = ledger.reserve(
            grant_index=1, mode="MU", users=(1, 2), ranks=(1, 1),
            rbg_indices=(3, 4))
        ledger.rollback(second.reservation_id)
        snapshot = ledger.snapshot()
        assert snapshot.used_physical_prb == 48
        assert snapshot.used_logical_prb == 96
    wall = time.perf_counter() - start
    return {
        "iterations": iterations, "wall_s": wall,
        "transactions_per_second": iterations / max(wall, 1e-12),
        "verdict": "PASS",
    }


def main() -> Path:
    report = {
        "schema": "superran_scheduler_p0_stress_v1",
        "srs_capacity": _srs_capacity_stress(),
        "ledger_transactions": _ledger_transaction_stress(),
        "frequency_12ue_1s": _frequency_stress(),
        "mu_8ue_0p5s": _mu_stress(),
        "scope": (
            "synthetic deterministic stress for invariants and runtime; "
            "not a statistical field-performance conclusion"),
    }
    output = ROOT / "artifacts" / "results" / "scheduler_p0_stress.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"OUTPUT={output.resolve()}")
    return output


if __name__ == "__main__":
    main()

"""Reproducible direction-of-effect experiments for scheduler P0 modules."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import experience as exp  # noqa: E402
from superran import linkadapt as la  # noqa: E402
from superran import system as sy  # noqa: E402
from superran.scheduler_resource import ResourceBudget, ResourceLedger  # noqa: E402
from superran.srs_resource import (  # noqa: E402
    SrsResourceAllocator,
    allocate_basic_srs_resources,
    cross_cell_collision_report,
)


def _su_table(
    ue: int,
    rows: list[float],
    *,
    rank2_base: float | None = None,
    best_rank: int = 2,
) -> sy.UeLinkTable:
    wideband = float(np.mean(rows))
    rank2 = wideband if rank2_base is None else float(rank2_base)
    sinr = np.array([[wideband, rank2]], dtype=float)
    mcs = np.array([[
        la.select_mcs(wideband, table=3, target_bler=0.1).index,
        la.select_mcs(rank2, table=3, target_bler=0.1).index,
    ]], dtype=int)
    se = np.array([[
        la.MCS_TABLE_3[int(mcs[0, 0])].se,
        2.0 * la.MCS_TABLE_3[int(mcs[0, 1])].se,
    ]])
    rbg_rank1 = np.asarray(rows, dtype=float)
    rbg = np.stack([rbg_rank1, np.full(17, rank2)], axis=0)[None, ...]
    return sy.UeLinkTable(
        ue=ue, sinr_db=sinr, mcs=mcs, se=se,
        best_rank=np.array([best_rank], dtype=int),
        best_se=np.array([se[0, best_rank - 1]]),
        geo_sinr_db=wideband, outage=np.array([False]),
        sinr_tx_db=sinr.copy(), mcs_tx=mcs.copy(),
        sinr_rbg_db=rbg.copy(), sinr_tx_rbg_db=rbg.copy(),
        mcs_table=3, target_bler=0.1, power_constraint="nebf")


def _pair_link(
    left: int,
    right: int,
    left_base: float,
    right_base: float,
    *,
    correlation: float,
    corr_loss_db: float,
) -> sy.MuPairLink:
    power_loss = -10.0 * np.log10(2.0)
    true = np.array([[
        left_base + corr_loss_db + power_loss,
        right_base + corr_loss_db + power_loss,
    ]], dtype=float)
    corr = np.full((1, 2), float(corr_loss_db))
    return sy.MuPairLink(
        users=(left, right), rank_per_user=2,
        true_sinr_db=true, predicted_sinr_db=true.copy(),
        corr_loss_tx_db=corr.copy(), corr_loss_true_db=corr.copy(),
        power_loss_db=float(power_loss),
        correlation=np.array([correlation], dtype=float),
        leakage_ratio=np.array([0.01]), predicted_leakage_ratio=np.array([0.01]),
        power_constraint="nebf", precoder="zf",
        true_sinr_rbg_db=np.repeat(true[:, :, None], 17, axis=2),
        predicted_sinr_rbg_db=np.repeat(true[:, :, None], 17, axis=2),
        corr_loss_tx_rbg_db=np.full((1, 2, 17), corr_loss_db),
        corr_loss_true_rbg_db=np.full((1, 2, 17), corr_loss_db),
        receiver="lmmse", csi_error_variance=0.0,
        rzf_regularization=(0.0,))


def _frequency_experiment() -> dict[str, object]:
    tables = [
        _su_table(0, [22.0] * 8 + [-8.0] * 9, best_rank=1),
        _su_table(1, [-8.0] * 8 + [22.0] * 9, best_rank=1),
    ]
    cfg = sy.SystemConfig(
        evaluation_mode="experience", duration_s=0.1,
        tdd_pattern="D", seed=42)
    traffic = sy.TrafficConfig(model="full_buffer")
    kpi = sy.KpiConfig(warmup_tti=0, tti_trace_mode="off")
    results: dict[str, sy.SystemResult] = {}
    for mode in ("off", "on"):
        results[mode] = sy.simulate(
            tables, sys_cfg=cfg, traffic=traffic,
            sched=sy.SchedulerConfig(
                mu_enabled=False, olla_enabled=False,
                frequency_selective=mode),
            kpi=kpi)
    off = results["off"].cell
    on = results["on"].cell
    ratio = float(on["cell_served_mbps"] / off["cell_served_mbps"])
    assert ratio > 1.2
    assert on["scheduled_ues_per_busy_tti"] > off["scheduled_ues_per_busy_tti"]
    assert on["frequency_selection"][
        "incremental_predicted_useful_bytes_vs_sequential"] >= 0
    assert on["resource_ledger"]["physical_overlap_violations"] == 0
    assert on["grant_finalizer"]["plan_final_mismatch_count"] == 0
    return {
        "scenario": "two UEs have complementary 8/9-RBG strong subbands",
        "frequency_off_cell_served_mbps": off["cell_served_mbps"],
        "frequency_on_cell_served_mbps": on["cell_served_mbps"],
        "throughput_ratio_on_over_off": ratio,
        "scheduled_ues_per_busy_tti_off": off["scheduled_ues_per_busy_tti"],
        "scheduled_ues_per_busy_tti_on": on["scheduled_ues_per_busy_tti"],
        "frequency_kpi": on["frequency_selection"],
        "resource_ledger": on["resource_ledger"],
        "grant_finalizer": on["grant_finalizer"],
        "verdict": "PASS: frequency-selective scheduling is materially better than the sequential wideband baseline",
    }


def _srs_experiment() -> dict[str, object]:
    aligned = allocate_basic_srs_resources(
        [0, 0, 0], cell_ids=[0, 1, 2], pci_mod3_by_ue=[0, 0, 0],
        period_ms=10.0, n_ports_by_ue=4)
    staggered = allocate_basic_srs_resources(
        [0, 0, 0], cell_ids=[0, 1, 2], pci_mod3_by_ue=[0, 1, 2],
        period_ms=10.0, n_ports_by_ue=4)
    aligned_report = cross_cell_collision_report(aligned)
    staggered_report = cross_cell_collision_report(staggered)
    assert aligned_report.colliding_pair_count == 3
    assert staggered_report.colliding_pair_count == 0
    assert staggered_report.ls_nmse_proxy < aligned_report.ls_nmse_proxy
    allocator = SrsResourceAllocator()
    capacities = {
        str(int(period)): allocator.capacity_ues(
            period_ms=period, n_ports=4)
        for period in (10.0, 20.0, 40.0)
    }
    assert capacities == {"10": 68, "20": 136, "40": 272}
    adaptive_69 = allocate_basic_srs_resources(range(69), period_ms=10.0)
    assert {item.period_ms for item in adaptive_69} == {20.0}
    first = staggered[0]
    assert first.antenna_port_groups == ((0, 1), (2, 3))
    assert first.legs[1].offset_ms - first.legs[0].offset_ms == 5.0
    assert first.legs[0].frequency_resource_id == first.legs[1].frequency_resource_id
    return {
        "scenario": "three lightly loaded cells, one 2T4R UE per cell",
        "profile": {
            "configured_cs": 4,
            "tx_ports_per_occasion": 2,
            "logical_antenna_ports": 4,
            "frequency_resources": 17,
            "legs_per_ue": 2,
            "leg_gap_ms": 5.0,
            "hop_advance": "after_both_legs",
        },
        "capacity_ues_per_pci_colour": capacities,
        "adaptive_boundary": {
            "ue_count": 69,
            "selected_global_period_ms": adaptive_69[0].period_ms,
            "expected": "10ms capacity 68 -> atomically retry 20ms",
        },
        "same_phase": aligned_report.as_dict(),
        "pci_mod3_staggered": staggered_report.as_dict(),
        "same_phase_assignments": [item.as_dict() for item in aligned],
        "staggered_assignments": [item.as_dict() for item in staggered],
        "verdict": "PASS: PCI-mod3 hard partition removes exact two-leg pilot collisions in the light-load proxy",
    }


def _mu_experiment() -> dict[str, object]:
    bases = [20.0, 19.0, 18.0]
    tables = [
        _su_table(ue, [base] * 17, rank2_base=base)
        for ue, base in enumerate(bases)
    ]
    links = (
        _pair_link(0, 1, bases[0], bases[1], correlation=0.40, corr_loss_db=-5.0),
        _pair_link(0, 2, bases[0], bases[2], correlation=0.10, corr_loss_db=-1.0),
        _pair_link(1, 2, bases[1], bases[2], correlation=0.20, corr_loss_db=-2.0),
    )
    for link in links:
        left, right = link.users
        tables[left].mu_links[right] = link
        tables[right].mu_links[left] = link
    run = sy.simulate(
        tables,
        sys_cfg=sy.SystemConfig(
            evaluation_mode="experience", duration_s=0.01,
            tdd_pattern="D", seed=0),
        traffic=sy.TrafficConfig(model="full_buffer"),
        sched=sy.SchedulerConfig(
            mu_enabled=True, olla_enabled=False,
            frequency_selective="on", mu_corr_threshold=0.7),
        kpi=sy.KpiConfig(warmup_tti=0, tti_trace_mode="full"))
    first = run.diagnostics["tti_trace"]["rows"][0]
    decision = first["mu_candidate_decisions"][0]
    assert decision["anchor_ue"] == 0
    assert decision["selected_partner_ue"] == 2
    assert first["grants"][0]["partner_ue"] == 2
    assert run.cell["su_mu_plan"]["mu_selected"] > 0
    evaluations = {
        str(item["partner_ue"]): item for item in decision["evaluations"]}
    assert evaluations["2"]["useful_bytes_per_rbg"] > \
        evaluations["1"]["useful_bytes_per_rbg"]
    lookup = exp.TbsLookup.build(17, 16)
    rank_of = {u: int(tables[u].best_rank[0]) for u in (0, 1)}
    mcs_of = {
        u: int(tables[u].mcs_tx[0, rank_of[u] - 1]) for u in (0, 1)}
    base_of = {
        u: float(tables[u].sinr_tx_db[0, rank_of[u] - 1]) for u in (0, 1)}
    true_of = {
        u: float(tables[u].sinr_db[0, rank_of[u] - 1]) for u in (0, 1)}
    potential_of = {
        u: lookup.tbs_bytes("D", mcs_of[u], rank_of[u], 17) for u in (0, 1)}
    mixed_plan = exp._build_mu_plan(
        [0, 1], queue_bytes={0: 1_000, 1: 500_000}, lookup=lookup,
        slot="D", num_rbg=17, rank_of=rank_of, mcs_of=mcs_of,
        base_tx_sinr_of=base_of, mcs_without_olla_of=mcs_of,
        true_sinr_of=true_of, potential_of=potential_of,
        tables=tables, snap=0,
        sched=sy.SchedulerConfig(
            mu_enabled=True, olla_enabled=False, mu_corr_threshold=1.0),
        su_olla_db=np.zeros(3), mu_olla_db=np.zeros(3),
        blocked_data=False)
    mixed_grant = next(
        grant for grant in mixed_plan.grants if grant.mode == "MU")
    assert mixed_grant.n_rbg == min(max(mixed_grant.required_rbg), 17)
    return {
        "scenario": (
            "PF anchor UE0; UE1 is earlier but has CorrLoss -5 dB; "
            "UE2 is later with CorrLoss -1 dB"),
        "first_tti_decision": decision,
        "first_tti_final_grants": first["grants"],
        "cell_mu_candidate_scoring": run.cell["mu_candidate_scoring"],
        "su_mu_plan": run.cell["su_mu_plan"],
        "small_large_shared_bitmap": {
            "queue_bytes": [1_000, 500_000],
            "required_rbg": list(mixed_grant.required_rbg),
            "allocated_rbg": mixed_grant.n_rbg,
            "useful_bytes": list(mixed_grant.useful_bytes),
            "rule": (
                "continue shared MU bitmap until both queues fit "
                "or RBGs exhaust"),
        },
        "verdict": "PASS: the scorer skips the first feasible partner and selects the higher useful-byte-density pair",
    }


def _resource_transaction_experiment() -> dict[str, object]:
    ledger = ResourceLedger(
        ResourceBudget(17, (16,) * 17, max_layers_per_rbg=4), tti=7)
    pending = ledger.reserve(
        grant_index=0, mode="MU", users=(0, 1), ranks=(2, 2),
        rbg_indices=(0, 1, 2))
    before_rollback = ledger.snapshot().as_dict()
    ledger.rollback(pending.reservation_id)
    after_rollback = ledger.snapshot().as_dict()
    committed = ledger.reserve(
        grant_index=0, mode="MU", users=(0, 1), ranks=(2, 2),
        rbg_indices=(0, 1, 2))
    ledger.commit(committed.reservation_id)
    after_commit = ledger.snapshot().as_dict()
    assert before_rollback["used_physical_prb"] == 48
    assert before_rollback["used_logical_prb"] == 192
    assert after_rollback["used_physical_prb"] == 0
    assert after_commit["max_layers_used"] == 4
    return {
        "before_rollback": before_rollback,
        "after_rollback": after_rollback,
        "after_commit": after_commit,
        "verdict": "PASS: MU consumes shared physical PRB once, four logical layers, and rollback restores the empty ledger",
    }


def main() -> Path:
    report = {
        "schema": "superran_scheduler_p0_validation_v1",
        "carrier": "100 MHz @ 30 kHz; 272 PRB = 17 RBG x 16 PRB",
        "srs": _srs_experiment(),
        "frequency_selection": _frequency_experiment(),
        "mu_candidate_scoring": _mu_experiment(),
        "resource_transactions": _resource_transaction_experiment(),
        "explicit_scope": {
            "pdcch_cce": "not modelled",
            "srs_cross_cell": "allocator-level equal-power LS-NMSE proxy, not waveform simulation",
            "effective_sinr": "arithmetic mean in dB over granted RBGs",
        },
    }
    output = ROOT / "artifacts" / "results" / "scheduler_p0_validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"OUTPUT={output.resolve()}")
    return output


if __name__ == "__main__":
    main()

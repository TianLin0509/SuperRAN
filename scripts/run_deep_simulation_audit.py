"""Run the paired spectrum/system examples used by the detailed audit report.

The output is intentionally verbose: it carries one representative numerical
chain from geometry through precoding/SINR/rank and one multi-user TTI chain
from arrivals through PF/RBG/TBS/ACK.  The HTML report renders this JSON; it
does not invent example numbers separately.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from superwireless import gates, load
from superwireless import linklevel as ll
from superwireless import rng as rg
from superwireless import system as sy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "results" / "deep_simulation_audit.json"
SPECTRUM_DATASET = "ds_a0072031"
EXPERIENCE_DATASET = "ds_329edafb"
MASTER_SEED = 20260809
N_REP = 16


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def paired_block(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    paired = gates.paired_compare(a, b)
    g3 = gates.gate_conclusion(paired)
    return {"paired": paired.as_dict(), "gate3": g3.as_dict()}


def performance_trace(result: ll.LinkPerformance) -> dict[str, Any]:
    rb = np.asarray(result.sinr_per_rb_db)
    return {
        **result.as_dict(),
        "sinr_per_rb_db_first8": np.round(rb[:8], 4).tolist(),
        "sinr_per_rb_db_percentiles": {
            "p5": np.round(np.percentile(rb, 5, axis=0), 4).tolist(),
            "p50": np.round(np.percentile(rb, 50, axis=0), 4).tolist(),
            "p95": np.round(np.percentile(rb, 95, axis=0), 4).tolist(),
        },
    }


def weight_excerpt(h_est: np.ndarray, method: str, rank: int) -> dict[str, Any]:
    p = ll.compute_precoder(
        h_est, method=method, max_rank=max(1, int(rank)),
        forced_rank=max(1, int(rank)), rank_threshold=0.0)
    w = np.asarray(p.w[0, : min(8, p.w.shape[1]), :])
    return {
        "method": method,
        "shape": list(p.w.shape),
        "rank": p.rank,
        "indices": p.indices,
        "rb0_first8_ports_magnitude": np.round(np.abs(w), 6).tolist(),
        "rb0_first8_ports_phase_deg": np.round(np.angle(w, deg=True), 3).tolist(),
        "column_norm_rb0": np.round(np.linalg.norm(p.w[0], axis=0), 8).tolist(),
    }


def run_spectrum() -> dict[str, Any]:
    ds = load(SPECTRUM_DATASET)
    gate1 = ds.gate().as_dict()
    methods = ("svd", "svd_wideband", "type1")
    values = {m: np.empty(ds.n, dtype=float) for m in methods}
    ranks = {m: np.empty(ds.n, dtype=int) for m in methods}
    results: dict[str, list[ll.LinkPerformance]] = {m: [] for m in methods}
    operating_points = []

    for i in range(ds.n):
        op = ds.geometric_impairment(i)
        operating_points.append(op)
        for method in methods:
            result = ll.link_performance(
                ds.h_true[i], noise_power=op.noise_power,
                interference_cov=op.interference_cov,
                h_for_precoding=ds.h_est[i], method=method,
                receiver="mmse", max_rank=4,
                rank_selection="max_se", operating_point=op.as_dict())
            values[method][i] = result.spectral_efficiency
            ranks[method][i] = result.rank
            results[method].append(result)

    arm_srs = {
        "name": "SRS per-RB covariance/SVD", "method": "svd",
        "receiver": "mmse", "csi": "estimated", "dataset_id": ds.dataset_id,
        "config": ds.config,
    }
    arm_pmi = {
        "name": "Type-I-style wideband PMI", "method": "type1",
        "receiver": "mmse", "csi": "estimated", "dataset_id": ds.dataset_id,
        "config": ds.config,
    }
    # Parallel generation produced two fading observations per unique UE
    # position. They are repeated measurements, not 80 independent UEs;
    # inference therefore operates on the 40 position-cluster means. Raw
    # sample-level results remain available as a diagnostic only.
    position_list = [
        tuple(np.round(np.asarray(p, dtype=float), 6).tolist())
        for p in np.asarray(ds.ue_position)
    ]
    position_ids = np.empty(len(position_list), dtype=object)
    position_ids[:] = position_list
    srs_cluster, pmi_cluster, cluster_ids = gates.paired_cluster_means(
        values["svd"], values["type1"], position_ids)
    wide_cluster, pmi_wide_cluster, wide_cluster_ids = gates.paired_cluster_means(
        values["svd_wideband"], values["type1"], position_ids)
    primary = paired_block(srs_cluster, pmi_cluster)
    primary["gate2"] = gates.gate_comparison(
        arm_srs, arm_pmi,
        pilot_std_diff=float(np.std(srs_cluster - pmi_cluster, ddof=1)),
        n_samples=len(cluster_ids)).as_dict()
    cluster_size_hist = Counter(Counter(position_list).values())
    primary["independence_unit"] = "unique UE position cluster"
    primary["n_raw_observations"] = int(ds.n)
    primary["cluster_size_histogram"] = {
        str(size): int(count) for size, count in sorted(cluster_size_hist.items())
    }
    primary["sample_level_diagnostic"] = paired_block(
        values["svd"], values["type1"])
    controlled = paired_block(wide_cluster, pmi_wide_cluster)
    controlled["gate2"] = gates.gate_comparison(
        {**arm_srs, "name": "SRS wideband covariance", "method": "svd_wideband"},
        arm_pmi,
        pilot_std_diff=float(np.std(wide_cluster - pmi_wide_cluster, ddof=1)),
        n_samples=len(wide_cluster_ids)).as_dict()
    controlled["independence_unit"] = "unique UE position cluster"
    controlled["n_raw_observations"] = int(ds.n)
    controlled["sample_level_diagnostic"] = paired_block(
        values["svd_wideband"], values["type1"])

    diff = values["svd"] - values["type1"]
    rep = int(np.argmin(np.abs(diff - np.median(diff))))
    op = operating_points[rep]
    cov = np.asarray(op.interference_cov)
    eig = np.linalg.eigvalsh(cov).real if cov is not None else np.empty((0, 0))
    geo = ds.geometry
    representative = {
        "sample_index": rep,
        "selection": "paired SE difference nearest the sample median",
        "ue_position_m": np.round(ds.ue_position[rep], 4).tolist(),
        "distance_3d_m": float(geo["distance_3d_m"][rep]),
        "pathloss_db": float(geo["pathloss_dB"][rep]),
        "is_los": bool(geo["is_los"][rep]),
        "geometry_sinr_db": float(ds.scalar("sinr_dB")[rep]),
        "geometry_sir_db": float(ds.scalar("sir_dB")[rep]),
        "geometry_snr_field_db": float(ds.scalar("snr_dB")[rep]),
        "csi_nmse_db": float(ds.estimation_error_nmse_db()[rep]),
        "operating_point": op.as_dict(),
        "interference_covariance": {
            "shape": list(cov.shape),
            "mean_trace_per_rx_antenna": float(
                np.mean(np.trace(cov, axis1=1, axis2=2).real) / cov.shape[-1]),
            "effective_rank": ll.effective_rank(cov),
            "eigenvalue_mean": np.round(np.mean(eig, axis=0), 8).tolist(),
        },
        "methods": {
            m: {
                "performance": performance_trace(results[m][rep]),
                "weight": weight_excerpt(ds.h_est[rep], m, results[m][rep].rank),
            }
            for m in methods
        },
        "paired_difference_bit_s_hz": float(diff[rep]),
    }
    return {
        "dataset_id": ds.dataset_id,
        "prereg": ds.prereg,
        "gate1": gate1,
        "config": ds.config,
        "shape": ds.summary["shape"],
        "primary": primary,
        "controlled_wideband": controlled,
        "values": {m: np.round(v, 8).tolist() for m, v in values.items()},
        "rank_histograms": {
            m: {str(r): int(np.sum(rv == r)) for r in sorted(set(rv.tolist()))}
            for m, rv in ranks.items()
        },
        "representative": representative,
    }


def build_experience_tables(ds: Any) -> tuple[list[sy.UeLinkTable], float]:
    h = ds.h_true
    h_users = [np.asarray(h[i]) for i in range(h.shape[0])]
    sinr = [float(x) for x in np.asarray(ds.scalar("sinr_dB"))]
    sir = [float(x) for x in np.asarray(ds.scalar("sir_dB"))]
    csi = sy.ca.CsiConfig(
        enabled=True, srs_period_ms=10.0, hopping=True,
        processing_delay_ms=2.0)
    snap_ms = sy.snapshot_interval_ms(ds.config)
    load_rng = rg.RngBook(MASTER_SEED).generator("neighbor_load")
    t0 = time.perf_counter()
    tables = sy.build_link_tables(
        h_users, sinr, num_ues=int(ds.config["num_ues"]), geo_sir_db=sir,
        neighbor_load=0.3, csi=csi, snapshot_ms=snap_ms,
        load_jitter_rng=load_rng, precoder="svd")
    return tables, time.perf_counter() - t0


def run_one_experience(
    tables: list[sy.UeLinkTable], replication: int, accounting: str,
) -> sy.SystemResult:
    return sy.simulate(
        tables,
        sys_cfg=sy.SystemConfig(
            evaluation_mode="experience", duration_s=5.0,
            tdd_pattern="DDDSU", seed=MASTER_SEED,
            snapshot_update_ms=5.0),
        traffic=sy.TrafficConfig(
            model="mixed", file_bytes=500_000, arrival_rate_hz=5.0,
            small_ue_share=0.5, small_file_bytes=1_500,
            small_arrival_rate_hz=20.0, small_pdb_ms=20.0,
            large_pdb_ms=300.0),
        sched=sy.SchedulerConfig(
            algorithm="pf", pf_window_tti=100, pf_accounting=accounting,
            mu_enabled=False, olla_enabled=True, olla_speedup=1.0),
        kpi=sy.KpiConfig(
            warmup_tti=200, small_burst_policy="fractional_slot"),
        rng=rg.RngBook(MASTER_SEED, replication),
    )


def select_tti_trace(result: sy.SystemResult) -> dict[str, Any]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    source = (result.diagnostics.get("allocation_recent_sample")
              or result.diagnostics.get("allocation_sample", []))
    for row in source:
        groups[int(row["tti"])].append(row)
    chosen = None
    for tti, rows in groups.items():
        if (len(rows) > 1 and len({r["traffic_class"] for r in rows}) > 1
                and any(bool(r["ack"]) for r in rows)):
            chosen = tti
            break
    if chosen is None:
        chosen = next((t for t, rows in groups.items() if len(rows) > 1), None)
    if chosen is None:
        chosen = next(iter(groups), None)
    return {
        "tti": chosen,
        "allocations": [] if chosen is None else groups[chosen],
        "selection": ("first recent TTI with multiple UE, both traffic classes, "
                      "and at least one ACK"),
    }


def table_trace(table: sy.UeLinkTable, snapshot: int) -> dict[str, Any]:
    s = int(snapshot)
    return {
        "ue": int(table.ue),
        "snapshot": s,
        "geo_sinr_db": float(table.geo_sinr_db),
        "sir_db": float(table.sir_db),
        "iot_db": float(table.iot_db),
        "csi_lag_snapshots_mean": float(table.csi_lag_snapshots[s]),
        "rank_candidates": [
            {
                "rank": r + 1,
                "true_sinr_db": float(table.sinr_db[s, r]),
                "true_mcs": int(table.mcs[s, r]),
                "true_se": float(table.se[s, r]),
                "gnb_se": float(table.se_gnb[s, r]),
                "pmi_sinr_db": float(table.pmi_sinr_db[s, r]),
                "bf_gain_db": float(table.bf_gain_db[s, r]),
                "cqi": int(table.cqi_index_per_snapshot[s, r]),
                "tx_sinr_before_olla_db": float(table.sinr_tx_db[s, r]),
                "tx_mcs_before_olla": int(table.mcs_tx[s, r]),
            }
            for r in range(table.sinr_db.shape[1])
        ],
        "chosen_rank": int(table.best_rank[s]),
        "outage": bool(table.outage[s]),
    }


def compact_run(result: sy.SystemResult) -> dict[str, Any]:
    keys = (
        "small_queue_wait_ms_p95", "small_completion_delay_ms_p95",
        "small_pdb_miss_ratio", "large_burst_drb_throughput_mbps",
        "resource_utilization", "cell_served_mbps", "backlog_bytes",
        "accounting_error_pct", "pdb_decidable_arrival_objects",
        "pdb_right_censored_arrival_objects",
        "deadline_missed_incomplete_arrival_objects",
        "ue_experience_eligible", "ue_experience_measured",
        "ue_experience_measured_share",
    )
    return {
        "metrics": {k: result.cell.get(k) for k in keys},
        "offered_mbps": result.cell.get("offered_mbps"),
        "occupancy": result.cell.get("occupancy"),
        "multi_ue_tti_share": result.cell.get("multi_ue_tti_share"),
        "pf_accounting": result.cell.get("pf_accounting"),
        "byte_conservation": result.diagnostics.get("byte_conservation"),
        "notes": result.notes,
    }


def run_experience() -> dict[str, Any]:
    ds = load(EXPERIENCE_DATASET)
    gate1 = ds.gate().as_dict()
    tables, build_s = build_experience_tables(ds)
    metric_names = (
        "small_queue_wait_ms_p95", "small_completion_delay_ms_p95",
        "small_pdb_miss_ratio", "large_burst_drb_throughput_mbps",
        "resource_utilization", "cell_served_mbps", "backlog_bytes",
        "accounting_error_pct", "ue_experience_measured_share",
        "deadline_missed_incomplete_arrival_objects",
    )
    raw = {"a": {k: [] for k in metric_names}, "b": {k: [] for k in metric_names}}
    first_a = first_b = None
    elapsed = 0.0
    for rep in range(N_REP):
        t0 = time.perf_counter()
        a = run_one_experience(tables, rep, "scheduled_tbs")
        b = run_one_experience(tables, rep, "legacy_fullband")
        elapsed += time.perf_counter() - t0
        if first_a is None:
            first_a, first_b = a, b
        for key in metric_names:
            raw["a"][key].append(a.cell.get(key))
            raw["b"][key].append(b.cell.get(key))
    assert first_a is not None and first_b is not None

    comparisons = {}
    for key in metric_names:
        av = np.asarray(raw["a"][key], dtype=float)
        bv = np.asarray(raw["b"][key], dtype=float)
        comparisons[key] = paired_block(av, bv)

    arm_common = {
        "dataset_id": ds.dataset_id,
        "config": {
            "evaluation_mode": "experience", "duration_s": 5.0,
            "traffic_model": "mixed", "file_bytes": 500_000,
            "arrival_rate_hz": 5.0, "small_ue_share": 0.5,
            "small_file_bytes": 1_500, "small_arrival_rate_hz": 20.0,
            "small_pdb_ms": 20.0, "large_pdb_ms": 300.0,
            "scheduler": "pf", "pf_window_tti": 100,
            "neighbor_prb_util": 0.3, "neighbor_load_jitter": 0.05,
            "csi_aging": True, "srs_period_ms": 10.0,
            "srs_hopping": True, "csi_processing_delay_ms": 2.0,
            "precoder": "svd", "small_burst_policy": "fractional_slot",
            "master_seed": MASTER_SEED, "num_replications": N_REP,
        },
        "csi": "stale_srs_10ms_hopping", "method": "svd",
        "receiver": "mmse", "varies": ["pf_accounting"],
    }
    arm_a = {
        **arm_common, "name": "scheduled-TBS PF",
        "config": {**arm_common["config"], "pf_accounting": "scheduled_tbs"},
    }
    arm_b = {
        **arm_common, "name": "legacy full-band PF",
        "config": {**arm_common["config"], "pf_accounting": "legacy_fullband"},
    }
    primary_a = np.asarray(raw["a"]["small_queue_wait_ms_p95"], dtype=float)
    primary_b = np.asarray(raw["b"]["small_queue_wait_ms_p95"], dtype=float)
    gate2 = gates.gate_comparison(
        arm_a, arm_b,
        pilot_std_diff=float(np.std(primary_a - primary_b, ddof=1)),
        n_samples=N_REP).as_dict()

    trace_a = select_tti_trace(first_a)
    trace_b = select_tti_trace(first_b)
    ue_snap_pairs = {
        (int(row["ue"]), int(row["snapshot"]))
        for row in trace_a["allocations"] + trace_b["allocations"]
    }
    return {
        "dataset_id": ds.dataset_id,
        "gate1": gate1,
        "build_tables_s": build_s,
        "tti_loops_elapsed_s": elapsed,
        "n_replications": N_REP,
        "arm_a": arm_a,
        "arm_b": arm_b,
        "gate2": gate2,
        "raw": raw,
        "comparisons": comparisons,
        "first_run": {"a": compact_run(first_a), "b": compact_run(first_b)},
        "trace": {
            "a": trace_a,
            "b": trace_b,
            "link_tables": [table_trace(tables[u], s)
                            for u, s in sorted(ue_snap_pairs)],
            "tbs_lookup": first_a.diagnostics.get("tbs_lookup"),
            "crn_event_mapping": first_a.diagnostics.get("crn_event_mapping"),
            "byte_conservation": first_a.diagnostics.get("byte_conservation"),
        },
    }


def main() -> None:
    started = time.perf_counter()
    payload = {
        "report_version": "deep-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spectrum": run_spectrum(),
        "experience": run_experience(),
    }
    payload["elapsed_s"] = time.perf_counter() - started
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(jsonable(payload), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps({
        "output": str(OUT),
        "elapsed_s": payload["elapsed_s"],
        "spectrum_primary": payload["spectrum"]["primary"]["paired"],
        "experience_primary": payload["experience"]["comparisons"]
        ["small_queue_wait_ms_p95"]["paired"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

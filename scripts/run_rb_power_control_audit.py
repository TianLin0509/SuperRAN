"""Formal gate audit for frequency-domain RB power control.

This script deliberately separates three questions:

1. ChannelHub Door 1: can every stored SINR be reconstructed from absolute
   S/N/per-cell-I terms?
2. Causality: does boosting one cell help its own UE and hurt a victim UE on
   exactly the RBs where that cell is an interferer?
3. System A/B: with common random numbers, what changes when the serving cell
   moves power to RBG 0 while preserving total power?

Run from the repository root.  It writes UTF-8 JSON under
``output/rb_power_control`` and never mutates an existing dataset.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("SUPERRAN_NO_BROWSER", "1")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from superran import channelhub as ch  # noqa: E402
from superran import generate as gen  # noqa: E402
from superran import load, plan  # noqa: E402
from superran import power_control as pc  # noqa: E402
from superran import rng as rg  # noqa: E402
from superran import system as sysm  # noqa: E402

OUTPUT_DIR = ROOT / "output" / "rb_power_control"
DEFAULT_CROSS_DATASET = "ds_8cd531b0"
TARGET_NUM_UES = 4
TARGET_SAMPLES_PER_UE = 8
TARGET_NUM_SAMPLES = TARGET_NUM_UES * TARGET_SAMPLES_PER_UE
RB_PER_RBG = 16
NUM_REPLICATIONS = 8
MASTER_SEED = 841027
TARGET_POSITIONS = (
    {"x": 50.0, "y": 0.0, "z": 1.5},
    {"x": 100.0, "y": 15.0, "z": 1.5},
    {"x": 150.0, "y": -20.0, "z": 1.5},
    {"x": 220.0, "y": 10.0, "z": 1.5},
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _check(rows: list[dict[str, Any]], name: str, passed: bool,
           detail: Any) -> None:
    rows.append({"name": name, "passed": bool(passed), "detail": _jsonable(detail)})


def _require_gate(rows: list[dict[str, Any]], gate_name: str) -> None:
    failed = [row for row in rows if not row["passed"]]
    if failed:
        names = ", ".join(str(row["name"]) for row in failed)
        raise RuntimeError(f"{gate_name} failed: {names}")


def _target_config() -> dict[str, Any]:
    cfg = dict(plan.load_presets()["company_64t4r_multicell"]["config"])
    cfg.update({
        "num_ues": TARGET_NUM_UES,
        "num_ofdm_symbols": 1,
        "num_slots_per_sample": 1,
        "link": "DL",
        "channel_est_mode": "ideal",
        "max_per_ue_intf_cells": 0,
        "num_interfering_ues": 1,
        "ue_speed_kmh": 0.0,
        "seed": MASTER_SEED,
        "measurements": {"ssb_rsrp": False},
        "custom_ue_positions": [dict(x) for x in TARGET_POSITIONS],
    })
    return cfg


def _generate_target() -> tuple[str, dict[str, Any]]:
    ch.warmup()
    t0 = time.perf_counter()
    summary = gen.generate(
        _target_config(), num_samples=TARGET_NUM_SAMPLES, workers=1)
    return str(summary["dataset_id"]), {
        "elapsed_s": time.perf_counter() - t0,
        "summary": summary,
    }


def _door1(dataset_id: str) -> tuple[Any, pc.DownlinkPowerGeometry,
                                         list[dict[str, Any]]]:
    ds = load(dataset_id)
    geometry = pc.geometry_from_dataset(ds)
    rows: list[dict[str, Any]] = []
    h_true = np.asarray(ds.h_true)
    h_est = np.asarray(ds.h_est)
    intf = np.asarray(geometry.interference_power_mw, dtype=float)
    stored = np.asarray(ds.scalar("sinr_dB"), dtype=float)
    reconstructed = 10.0 * np.log10(
        geometry.signal_power_mw
        / (geometry.thermal_noise_power_mw + np.sum(intf, axis=(1, 2))))
    serving = np.asarray(geometry.serving_cell_index, dtype=int)
    own = intf[np.arange(serving.size), :, serving]
    ue_id = np.asarray(ds.scalar("ue_id"), dtype=int)
    counts = {int(u): int(np.count_nonzero(ue_id == u)) for u in np.unique(ue_id)}

    _check(rows, "shape_is_32x1x272x64x4",
           h_true.shape == (32, 1, 272, 64, 4), h_true.shape)
    _check(rows, "h_true_h_est_same_shape", h_true.shape == h_est.shape,
           {"h_true": h_true.shape, "h_est": h_est.shape})
    _check(rows, "channel_finite",
           bool(np.all(np.isfinite(h_true)) and np.all(np.isfinite(h_est))),
           {"h_true_nan": int(np.count_nonzero(~np.isfinite(h_true))),
            "h_est_nan": int(np.count_nonzero(~np.isfinite(h_est)))})
    _check(rows, "power_geometry_shape",
           intf.shape == (32, 1, 21), intf.shape)
    _check(rows, "power_terms_finite_and_physical", bool(
        np.all(np.isfinite(geometry.signal_power_mw))
        and np.all(geometry.signal_power_mw > 0)
        and np.all(np.isfinite(geometry.thermal_noise_power_mw))
        and np.all(geometry.thermal_noise_power_mw > 0)
        and np.all(np.isfinite(intf)) and np.all(intf >= 0)), {
            "signal_min": float(np.min(geometry.signal_power_mw)),
            "noise_min": float(np.min(geometry.thermal_noise_power_mw)),
            "interference_min": float(np.min(intf)),
            "interference_max": float(np.max(intf)),
        })
    _check(rows, "serving_interference_column_zero",
           bool(np.max(np.abs(own)) <= 1e-24),
           {"max_abs_mw": float(np.max(np.abs(own)))})
    error = np.abs(np.clip(reconstructed, -50.0, 50.0) - stored)
    _check(rows, "stored_sinr_reconstructs_from_S_N_sumI",
           bool(np.max(error) < 1e-10), {
               "max_abs_error_db": float(np.max(error)),
               "formula": "10log10(S/(N+sum_cell I_cell))",
           })
    _check(rows, "intercell_interference_nonzero",
           bool(np.any(np.sum(intf, axis=2) > 0)), {
               "nonzero_sample_slot": int(np.count_nonzero(np.sum(intf, axis=2) > 0)),
               "total_sample_slot": int(intf.shape[0] * intf.shape[1]),
           })
    _check(rows, "one_serving_cell_for_single_cell_scheduler",
           len(np.unique(serving)) == 1,
           {"serving_cells": np.unique(serving).tolist()})
    _check(rows, "eight_snapshots_per_ue",
           counts == {0: 8, 1: 8, 2: 8, 3: 8}, counts)
    _require_gate(rows, "Door 1")
    return ds, geometry, rows


def _choose_source_cell(geometry: pc.DownlinkPowerGeometry) -> tuple[int, np.ndarray,
                                                                      np.ndarray]:
    serving = np.asarray(geometry.serving_cell_index, dtype=int)
    intf = np.asarray(geometry.interference_power_mw[:, 0], dtype=float)
    best: tuple[float, int, np.ndarray, np.ndarray] | None = None
    for cell in range(geometry.num_cells):
        own = np.flatnonzero(serving == cell)
        victims = np.flatnonzero((serving != cell) & (intf[:, cell] > 0))
        if own.size == 0 or victims.size == 0:
            continue
        score = float(np.sum(intf[victims, cell]))
        row = (score, cell, own, victims)
        if best is None or row[0] > best[0]:
            best = row
    if best is None:
        raise RuntimeError("cross-cell dataset has no source/victim pair")
    return int(best[1]), best[2], best[3]


def _cross_cell_causality(dataset_id: str) -> dict[str, Any]:
    ds = load(dataset_id)
    geometry = pc.geometry_from_dataset(ds)
    n_rb = int(np.asarray(ds.h_true).shape[2])
    source, own_rows, victim_rows = _choose_source_cell(geometry)
    boost_count = max(1, n_rb // 4)
    shaped_cfg = pc.RbPowerControlConfig.from_raw(
        enabled=True, num_rb=n_rb,
        overrides=[{
            "cell_index": source,
            "rb_start": 0,
            "rb_end": boost_count - 1,
            "multiplier": 2.0,
        }])
    uniform_cfg = pc.RbPowerControlConfig.from_raw(enabled=True, num_rb=n_rb)
    q0 = uniform_cfg.resolve_profiles(geometry.num_cells)
    q1 = shaped_cfg.resolve_profiles(geometry.num_cells)
    deltas_db: list[np.ndarray] = []
    mean_i_errors: list[float] = []
    for sample in range(geometry.num_samples):
        kwargs = {
            "signal_power_mw": float(geometry.signal_power_mw[sample]),
            "thermal_noise_power_mw": float(geometry.thermal_noise_power_mw[sample]),
            "interference_power_per_cell_mw": geometry.interference_power_mw[sample, 0],
            "serving_cell_index": int(geometry.serving_cell_index[sample]),
            "neighbor_utilization": 0.3,
        }
        base = pc.couple_rb_power(profiles=q0, **kwargs)
        shaped = pc.couple_rb_power(profiles=q1, **kwargs)
        deltas_db.append(shaped.geometric_sinr_db - base.geometric_sinr_db)
        mean_i_errors.append(float(
            np.mean(shaped.controlled_interference_mw)
            - np.mean(base.controlled_interference_mw)))
    delta = np.asarray(deltas_db)
    victim_first = delta[victim_rows, 0]
    own_first = delta[own_rows, 0]
    balance_rb = boost_count
    victim_balance = delta[victim_rows, balance_rb]
    own_balance = delta[own_rows, balance_rb]
    checks: list[dict[str, Any]] = []
    _check(checks, "source_profile_sum_and_bounds", bool(
        abs(math.fsum(float(x) for x in q1[source]) - n_rb) < 1e-10
        and np.min(q1) >= 0.1 and np.max(q1) <= 4.0),
        pc.profile_summary(q1))
    _check(checks, "own_ue_boosted_rb_is_plus_3.0103_db",
           bool(np.allclose(own_first, 10.0 * np.log10(2.0), atol=1e-10)),
           own_first)
    _check(checks, "victim_ue_boosted_rb_loses_sinr",
           bool(np.all(victim_first < 0.0)), victim_first)
    _check(checks, "own_ue_compensation_rb_loses_sinr",
           bool(np.all(own_balance < 0.0)), own_balance)
    _check(checks, "victim_ue_compensation_rb_gains_sinr",
           bool(np.all(victim_balance > 0.0)), victim_balance)
    _check(checks, "mean_interference_power_is_conserved",
           bool(np.max(np.abs(mean_i_errors)) < 1e-18), {
               "max_abs_error_mw": float(np.max(np.abs(mean_i_errors))),
           })
    _require_gate(checks, "cross-cell causality")
    return {
        "dataset_id": dataset_id,
        "shape": list(np.asarray(ds.h_true).shape),
        "source_cell": source,
        "own_sample_indices": own_rows,
        "victim_sample_indices": victim_rows,
        "boosted_rb_range": [0, boost_count - 1],
        "auto_balance_multiplier": float(q1[source, boost_count]),
        "checks": checks,
    }


def _random_conserving_profile(rng: np.random.Generator, cells: int,
                               num_rb: int) -> np.ndarray:
    out = np.ones((cells, num_rb), dtype=float)
    for cell in range(cells):
        for _ in range(num_rb * 2):
            i, j = rng.choice(num_rb, size=2, replace=False)
            lo = max(0.1 - out[cell, i], out[cell, j] - 4.0)
            hi = min(4.0 - out[cell, i], out[cell, j] - 0.1)
            if hi <= lo:
                continue
            delta = float(rng.uniform(lo, hi))
            out[cell, i] += delta
            out[cell, j] -= delta
    return out


def _property_stress() -> dict[str, Any]:
    random = np.random.default_rng(20260810)
    worst_sum = 0.0
    worst_formula = 0.0
    cases = 500
    for _ in range(cases):
        cells = int(random.integers(2, 22))
        num_rb = int(random.integers(8, 273))
        q = _random_conserving_profile(random, cells, num_rb)
        worst_sum = max(worst_sum, float(np.max(np.abs(np.sum(q, axis=1) - num_rb))))
        serving = int(random.integers(cells))
        intf = random.lognormal(-3.0, 2.0, size=cells)
        intf[serving] = 0.0
        signal = float(random.lognormal(1.0, 2.0))
        noise = float(random.lognormal(-4.0, 1.0))
        util = float(random.uniform(0.0, 1.0))
        coupled = pc.couple_rb_power(
            signal_power_mw=signal,
            thermal_noise_power_mw=noise,
            interference_power_per_cell_mw=intf,
            serving_cell_index=serving,
            profiles=q,
            neighbor_utilization=util)
        direct = q[serving] * signal / (noise + util * (intf @ q))
        recovered = 10.0 ** (coupled.geometric_sinr_db / 10.0)
        rel = np.max(np.abs(recovered - direct) / np.maximum(direct, 1e-30))
        worst_formula = max(worst_formula, float(rel))
    checks: list[dict[str, Any]] = []
    _check(checks, "500_random_profiles_conserve_total_power",
           worst_sum < 1e-10, {"worst_sum_error": worst_sum})
    _check(checks, "500_random_exact_coupling_cases_match_direct_formula",
           worst_formula < 1e-12, {"worst_relative_error": worst_formula})
    _require_gate(checks, "property stress")
    return {"cases": cases, "checks": checks}


def _build_tables(ds: Any, geometry: pc.DownlinkPowerGeometry,
                  power_cfg: pc.RbPowerControlConfig) -> tuple[list[Any], float]:
    t0 = time.perf_counter()
    tables = sysm.build_link_tables(
        [np.asarray(ds.h_true[i]) for i in range(ds.h_true.shape[0])],
        [float(x) for x in np.asarray(ds.scalar("sinr_dB"))],
        h_for_precoding_users=[
            np.asarray(ds.h_est[i]) for i in range(ds.h_est.shape[0])],
        geo_sir_db=[float(x) for x in np.asarray(ds.scalar("sir_dB"))],
        neighbor_load=0.3,
        neighbor_load_jitter=0.0,
        num_ues=TARGET_NUM_UES,
        rb_per_rbg=RB_PER_RBG,
        csi=sysm.ca.CsiConfig(enabled=False),
        snapshot_ms=sysm.snapshot_interval_ms(ds.config),
        precoder="svd",
        power_constraint="ebf",
        mu_enabled=False,
        rb_power_control=power_cfg,
        power_geometry=geometry)
    return tables, time.perf_counter() - t0


def _table_checks(uniform_tables: list[Any], shaped_tables: list[Any],
                  shaped_profiles: np.ndarray) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    uniform_values = np.concatenate([
        np.asarray(table.sinr_rbg_db)[:, 0, :1].ravel()
        for table in uniform_tables])
    shaped_values = np.concatenate([
        np.asarray(table.sinr_rbg_db)[:, 0, :1].ravel()
        for table in shaped_tables])
    delta_first = shaped_values - uniform_values
    uniform_tail = np.concatenate([
        np.asarray(table.sinr_rbg_db)[:, 0, 1:].ravel()
        for table in uniform_tables])
    shaped_tail = np.concatenate([
        np.asarray(table.sinr_rbg_db)[:, 0, 1:].ravel()
        for table in shaped_tables])
    delta_tail = shaped_tail - uniform_tail
    expected_first = 10.0 * np.log10(2.0)
    expected_tail = 10.0 * np.log10(shaped_profiles[0, RB_PER_RBG])
    _check(rows, "uniform_arm_keeps_full_rb_resolution", all(
        table.frequency_rows_per_rbg == 16
        and table.h_true_rbg is not None
        and table.h_true_rbg.shape[1] == 272
        for table in uniform_tables), {
            "rows_per_rbg": [table.frequency_rows_per_rbg for table in uniform_tables],
            "cached_frequency_rows": [table.h_true_rbg.shape[1] for table in uniform_tables],
        })
    _check(rows, "four_ues_eight_snapshots_each",
           all(table.sinr_db.shape[0] == 8 for table in uniform_tables),
           [table.sinr_db.shape for table in uniform_tables])
    _check(rows, "rbg0_rank1_delta_matches_2x_power",
           bool(np.allclose(delta_first, expected_first, atol=0.02)), {
               "expected_db": expected_first,
               "min_db": float(np.min(delta_first)),
               "max_db": float(np.max(delta_first)),
           })
    _check(rows, "other_rbg_rank1_delta_matches_auto_balance",
           bool(np.allclose(delta_tail, expected_tail, atol=0.02)), {
               "expected_db": expected_tail,
               "min_db": float(np.min(delta_tail)),
               "max_db": float(np.max(delta_tail)),
           })
    _check(rows, "link_tables_are_finite", bool(all(
        np.all(np.isfinite(table.sinr_db))
        and np.all(np.isfinite(table.sinr_rbg_db))
        for table in [*uniform_tables, *shaped_tables])), {})
    _require_gate(rows, "link table")
    return rows


def _run_ab(uniform_tables: list[Any], shaped_tables: list[Any],
            uniform_cfg: pc.RbPowerControlConfig,
            shaped_cfg: pc.RbPowerControlConfig) -> dict[str, Any]:
    common = {
        "duration_s": 5.0,
        "scs_khz": 30,
        "num_rbg": 17,
        "rb_per_rbg": 16,
        "tdd_pattern": "DDDSU",
        "snapshot_update_ms": 5.0,
        "power_constraint": "ebf",
        "seed": MASTER_SEED,
    }
    sys_uniform = sysm.SystemConfig(rb_power_control=uniform_cfg, **common)
    sys_shaped = sysm.SystemConfig(rb_power_control=shaped_cfg, **common)
    traffic = sysm.TrafficConfig(
        model="mixed", file_bytes=500_000, arrival_rate_hz=20.0,
        small_ue_share=0.5, small_file_bytes=1_500,
        small_arrival_rate_hz=20.0)
    scheduler = sysm.SchedulerConfig(
        algorithm="pf", pf_accounting="scheduled_tbs", mu_enabled=False,
        olla_speedup=1.0, olla_warmup_speedup=1.0)
    kpi = sysm.KpiConfig(warmup_s=1.0,
                         small_burst_policy="fractional_slot")
    t0 = time.perf_counter()
    uniform = sysm.simulate_replications(
        uniform_tables, num_replications=NUM_REPLICATIONS,
        master_seed=MASTER_SEED, sys_cfg=sys_uniform,
        traffic=traffic, sched=scheduler, kpi=kpi)
    shaped = sysm.simulate_replications(
        shaped_tables, num_replications=NUM_REPLICATIONS,
        master_seed=MASTER_SEED, sys_cfg=sys_shaped,
        traffic=traffic, sched=scheduler, kpi=kpi)
    elapsed = time.perf_counter() - t0
    metrics = (
        ("cell_served_mbps", "Mbps"),
        ("cell_experienced_mbps", "Mbps"),
        ("ue_experienced_p5_mbps", "Mbps"),
        ("serving_cell_prb_utilization", "ratio"),
        ("bler_first_tx", "ratio"),
        ("avg_mcs", "index"),
    )
    comparisons: dict[str, Any] = {}
    for metric, unit in metrics:
        values_u = [float(run.cell[metric]) for run in uniform.runs]
        values_s = [float(run.cell[metric]) for run in shaped.runs]
        comparisons[metric] = rg.compare_replications(
            values_s, values_u, metric=metric, unit=unit,
            arm_a="shaped_cell0_rbg0_2x", arm_b="enabled_uniform_1x",
            books_a=shaped.books, books_b=uniform.books, require_crn=True)
        comparisons[metric]["uniform_values"] = values_u
        comparisons[metric]["shaped_values"] = values_s
    crn_ok = all(item.get("crn") is True for item in comparisons.values())
    if not crn_ok:
        raise RuntimeError("Door 3 failed: common-random-number pairing not proven")
    return {
        "elapsed_s": elapsed,
        "num_replications": NUM_REPLICATIONS,
        "master_seed": MASTER_SEED,
        "rng_runs": [book.as_dict() for book in uniform.books],
        "configuration": {
            "system_uniform": sys_uniform.as_dict(),
            "system_shaped": sys_shaped.as_dict(),
            "traffic": traffic.as_dict(),
            "scheduler": scheduler.as_dict(),
            "kpi": kpi.as_dict(),
            "neighbor_prb_util": 0.3,
            "csi_aging": "disabled to isolate RB-power causality",
            "mu": "disabled; separate smoke test is in unit regression",
        },
        "uniform_cell_summary": uniform.cell,
        "shaped_cell_summary": shaped.cell,
        "comparisons": comparisons,
        "gate_3_rule": (
            "directional claim requires paired 95% CI excluding zero AND the "
            "decision test in gates.PairedResult (Wilcoxon when available) p<0.05"
        ),
    }


def run(target_dataset: str | None, cross_dataset: str) -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    generation: dict[str, Any]
    if target_dataset:
        generation = {"reused_existing_dataset": True}
        target_id = target_dataset
    else:
        target_id, generation = _generate_target()

    ds, geometry, door1 = _door1(target_id)
    cross = _cross_cell_causality(cross_dataset)
    stress = _property_stress()

    uniform_cfg = pc.RbPowerControlConfig.from_raw(
        enabled=True, num_rb=int(ds.h_true.shape[2]))
    shaped_cfg = pc.RbPowerControlConfig.from_raw(
        enabled=True, num_rb=int(ds.h_true.shape[2]),
        overrides=[{
            "cell_index": 0,
            "rb_start": 0,
            "rb_end": 15,
            "multiplier": 2.0,
        }])
    profiles = shaped_cfg.resolve_profiles(geometry.num_cells)
    uniform_tables, build_uniform_s = _build_tables(ds, geometry, uniform_cfg)
    shaped_tables, build_shaped_s = _build_tables(ds, geometry, shaped_cfg)
    table_checks = _table_checks(uniform_tables, shaped_tables, profiles)
    ab = _run_ab(uniform_tables, shaped_tables, uniform_cfg, shaped_cfg)

    return {
        "audit": "rb_power_control_v1",
        "status": "passed",
        "started_unix_s": started,
        "finished_unix_s": time.time(),
        "target_dataset_id": target_id,
        "cross_cell_dataset_id": cross_dataset,
        "generation": generation,
        "door1": door1,
        "cross_cell_causality": cross,
        "property_stress": stress,
        "profiles": {
            "uniform": pc.profile_summary(
                uniform_cfg.resolve_profiles(geometry.num_cells)),
            "shaped": pc.profile_summary(profiles),
        },
        "link_table": {
            "uniform_build_s": build_uniform_s,
            "shaped_build_s": build_shaped_s,
            "checks": table_checks,
        },
        "ab": ab,
        "declared_limits": [
            "SystemResult is a single-cell scheduler; mixed serving-cell tables hard-fail.",
            "Neighbor activity is a common utilization scalar, not joint multi-cell TTI scheduling.",
            "Granted-RBG effective SINR uses arithmetic mean in dB, not calibrated EESM/MIESM.",
            "The formal A/B disables CSI aging and MU to isolate frequency-power causality.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-dataset", default=None)
    parser.add_argument("--cross-dataset", default=DEFAULT_CROSS_DATASET)
    parser.add_argument(
        "--output", default=str(OUTPUT_DIR / "rb_power_control_audit.json"))
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    result = run(args.target_dataset, args.cross_dataset)
    output.write_text(
        json.dumps(_jsonable(result), ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "target_dataset_id": result["target_dataset_id"],
        "cross_cell_dataset_id": result["cross_cell_dataset_id"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

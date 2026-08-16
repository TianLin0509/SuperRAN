"""Formal and diagnostic audit for experience_v2, MU and antenna power modes.

The script is deliberately restartable.  Link tables are cached only when the
dataset, the physical configuration and the relevant source files have the same
SHA-256 fingerprint; a code change therefore cannot silently reuse stale tables.

Examples
--------
python scripts/run_experience_mu_power_audit.py --stage pilot
python scripts/run_experience_mu_power_audit.py --stage formal
python scripts/run_experience_mu_power_audit.py --stage power
python scripts/run_experience_mu_power_audit.py --stage pmi
python scripts/run_experience_mu_power_audit.py --stage stress
python scripts/run_experience_mu_power_audit.py --stage reverse_pf
python scripts/run_experience_mu_power_audit.py --stage pf_sentinel
python scripts/run_experience_mu_power_audit.py --stage all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Windows shells launched outside a UTF-8 terminal can inherit cp1252.  Gate
# statements are Chinese, so make the audit runner's machine-readable output
# independent of the parent console code page.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

from superran import gates, load  # noqa: E402
from superran import linkadapt as la  # noqa: E402
from superran import rng as rg  # noqa: E402
from superran import system as sy  # noqa: E402
from superran.csi_aging import CsiConfig  # noqa: E402

DATASET_ID = os.environ.get("SUPERRAN_AUDIT_DATASET_ID", "ds_e8a577d8")
MASTER_SEED = 20260809
FORMAL_N = 16
PILOT_N = 8
FORMAL_LARGE_RATE_HZ = 8.0
FORMAL_SMALL_RATE_HZ = 30.0
FORMAL_TARGET_PRB_UTILIZATION = 0.50
FORMAL_LOAD_TOLERANCE = 0.03
FORMAL_CALIBRATION_SEED = 20260810
FORMAL_CALIBRATION_REPLICATIONS = 8
# 第一版 7 s / 3 s、现场步长在 replication 9 的稀疏 MU 模式只有 58/12
# 个半窗样本，且暴露出固定接收基 bug。修正为逐用户 LMMSE 后，仍需要更长
# 的统计窗来观察稀疏模式；协议在重跑正式 Gate 3 之前修订并冻结如下。
FORMAL_DURATION_S = 13.0
FORMAL_WARMUP_S = 5.0
FORMAL_WARMUP_SPEEDUP = 1.0
OUT_DIR = ROOT / "artifacts" / "results"
CACHE_DIR = OUT_DIR / "experience_mu_power_cache"
SOURCE_FILES = (
    "beamforming.py", "csi_aging.py", "experience.py", "linkadapt.py",
    "linklevel.py", "mumimo.py", "system.py",
)


@dataclass(frozen=True)
class TableProfile:
    power_constraint: str = "ebf"
    srs_period_ms: float = 10.0
    hopping: bool = True
    processing_delay_ms: float = 2.0
    csi_report_period_ms: float = 20.0
    csi_enabled: bool = True
    mu_enabled: bool = True
    neighbor_load: float = 0.3
    neighbor_load_jitter: float = 0.05

    @property
    def slug(self) -> str:
        csi = (f"srs{self.srs_period_ms:g}_{'hop' if self.hopping else 'nohop'}_"
               f"rep{self.csi_report_period_ms:g}"
               if self.csi_enabled else "current_estimated_csi")
        return f"{self.power_constraint}_{csi}_{'mu' if self.mu_enabled else 'su'}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value).__name__)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_jsonable),
        encoding="utf-8",
    )


def fingerprint(ds: Any, profile: TableProfile) -> str:
    h = hashlib.sha256()
    h.update(DATASET_ID.encode())
    h.update(json.dumps(asdict(profile), sort_keys=True).encode())
    summary = ROOT / "artifacts" / "datasets" / DATASET_ID / "summary.json"
    h.update(summary.read_bytes())
    for name in SOURCE_FILES:
        path = ROOT / "src" / "superran" / name
        h.update(name.encode())
        h.update(path.read_bytes())
    h.update(str(ds.h_true.shape).encode())
    h.update(str(ds.h_est.shape).encode())
    return h.hexdigest()


def load_inputs(ds: Any) -> tuple[list[np.ndarray], list[np.ndarray], list[float], list[float]]:
    h_true = np.asarray(ds.h_true)
    h_est = np.asarray(ds.h_est)
    eval_users = [np.asarray(h_true[i]) for i in range(h_true.shape[0])]
    prec_users = [np.asarray(h_est[i]) for i in range(h_est.shape[0])]
    sinr = np.asarray(ds.scalar("sinr_dB"), dtype=float).tolist()
    sir = np.asarray(ds.scalar("sir_dB"), dtype=float).tolist()
    return eval_users, prec_users, sinr, sir


def get_tables(ds: Any, profile: TableProfile) -> tuple[list[sy.UeLinkTable], dict[str, Any]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = fingerprint(ds, profile)
    cache = CACHE_DIR / f"{profile.slug}_{digest[:16]}.pkl"
    meta_path = cache.with_suffix(".json")
    if cache.exists() and meta_path.exists():
        t0 = time.perf_counter()
        with cache.open("rb") as fh:
            tables = pickle.load(fh)  # noqa: S301 - local fingerprinted cache
        return tables, {
            "cache": str(cache.resolve()), "cache_hit": True,
            "fingerprint": digest, "elapsed_s": time.perf_counter() - t0,
        }

    eval_users, prec_users, sinr, sir = load_inputs(ds)
    csi = (CsiConfig(
        enabled=True,
        srs_period_ms=profile.srs_period_ms,
        hopping=profile.hopping,
        processing_delay_ms=profile.processing_delay_ms,
        csi_report_period_ms=profile.csi_report_period_ms,
        periodic_trace_history=True,
    ) if profile.csi_enabled else None)
    t0 = time.perf_counter()
    tables = sy.build_link_tables(
        eval_users,
        sinr,
        h_for_precoding_users=prec_users,
        num_ues=int(ds.config["num_ues"]),
        geo_sir_db=sir,
        neighbor_load=profile.neighbor_load,
        neighbor_load_jitter=profile.neighbor_load_jitter,
        csi=csi,
        snapshot_ms=sy.snapshot_interval_ms(ds.config),
        load_jitter_rng=rg.RngBook(MASTER_SEED).generator("neighbor_load"),
        precoder="svd",
        power_constraint=profile.power_constraint,
        mu_enabled=profile.mu_enabled,
        mu_rank_per_user=2,
        mu_precoder="zf",
    )
    elapsed = time.perf_counter() - t0
    with cache.open("wb") as fh:
        pickle.dump(tables, fh, protocol=pickle.HIGHEST_PROTOCOL)
    meta = {
        "dataset_id": DATASET_ID,
        "profile": asdict(profile),
        "fingerprint": digest,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tables": len(tables),
        "snapshots": int(tables[0].sinr_db.shape[0]),
        "build_elapsed_s": elapsed,
        "precoding_csi_source": tables[0].precoding_csi_source,
    }
    write_json(meta_path, meta)
    return tables, {
        "cache": str(cache.resolve()), "cache_hit": False,
        "fingerprint": digest, "elapsed_s": elapsed,
    }


def traffic(large_rate: float = FORMAL_LARGE_RATE_HZ,
            small_rate: float = FORMAL_SMALL_RATE_HZ,
            small_share: float = 0.5) -> sy.TrafficConfig:
    return sy.TrafficConfig(
        model="mixed",
        file_bytes=500_000,
        arrival_rate_hz=float(large_rate),
        small_ue_share=float(small_share),
        small_file_bytes=1_500,
        small_arrival_rate_hz=float(small_rate),
        small_pdb_ms=20.0,
        large_pdb_ms=300.0,
    )


def scheduler(*, mu_enabled: bool, accounting: str = "scheduled_tbs",
              warmup_speedup: float = 10.0) -> sy.SchedulerConfig:
    return sy.SchedulerConfig(
        algorithm="pf",
        pf_window_tti=100,
        pf_accounting=accounting,
        mu_enabled=mu_enabled,
        max_mu_users=2,
        mu_rank_per_user=2,
        mu_corr_threshold=0.7,
        mu_precoder="zf",
        olla_enabled=True,
        olla_speedup=1.0,
        olla_warmup_speedup=float(warmup_speedup),
    )


def system_config(power: str = "ebf", duration_s: float = 5.0) -> sy.SystemConfig:
    return sy.SystemConfig(
        evaluation_mode="experience",
        duration_s=float(duration_s),
        tdd_pattern="DDDSU",
        seed=MASTER_SEED,
        snapshot_update_ms=5.0,
        power_constraint=power,
    )


def run_one(tables: list[sy.UeLinkTable], replication: int, *,
            mu_enabled: bool, large_rate: float = FORMAL_LARGE_RATE_HZ,
            small_rate: float = FORMAL_SMALL_RATE_HZ,
            small_share: float = 0.5, accounting: str = "scheduled_tbs",
            warmup_speedup: float = 10.0, duration_s: float = 5.0,
            warmup_s: float = 1.0, power: str = "ebf",
            traffic_cfg: sy.TrafficConfig | None = None) -> sy.SystemResult:
    return sy.simulate(
        tables,
        sys_cfg=system_config(power=power, duration_s=duration_s),
        traffic=(traffic_cfg if traffic_cfg is not None
                 else traffic(large_rate, small_rate, small_share)),
        sched=scheduler(
            mu_enabled=mu_enabled,
            accounting=accounting,
            warmup_speedup=warmup_speedup,
        ),
        kpi=sy.KpiConfig(warmup_s=float(warmup_s)),
        rng=rg.RngBook(MASTER_SEED, int(replication)),
    )


CELL_KEYS = (
    "small_queue_wait_ms_mean", "small_queue_wait_ms_p95",
    "small_immediate_service_ratio", "small_completion_delay_ms_p95",
    "small_pdb_miss_ratio", "large_flow_drb_throughput_p5_mbps",
    "large_burst_drb_throughput_mbps", "cell_served_mbps", "offered_mbps",
    "backlog_bytes", "occupancy", "resource_utilization", "bler_first_tx",
    "su_bler_first_tx", "mu_bler_first_tx", "mu_share", "mu_rbg_share",
    "mu_user_tx_share", "accounting_error_pct", "measurement_accounting_error_pct",
    "queue_wait_observed_share", "queue_wait_right_censored_arrival_objects",
    "olla_convergence",
    "su_mu_plan",
)


def slim(run: sy.SystemResult) -> dict[str, Any]:
    return {
        "cell": {key: run.cell.get(key) for key in CELL_KEYS},
        "byte_conservation": run.diagnostics.get("byte_conservation"),
        "measurement_window": run.diagnostics.get("measurement_window"),
        "queue_wait_observation": run.diagnostics.get("queue_wait_observation"),
        "adaptation": run.diagnostics.get("link_adaptation_by_phase"),
        "olla_start": run.diagnostics.get("olla_state_at_measurement_start"),
        "olla_final": run.diagnostics.get("olla_state_final"),
        "max_rbg_in_any_tti": run.diagnostics.get("max_rbg_in_any_tti"),
        "rbg_overlap_violations": run.diagnostics.get("rbg_overlap_violations"),
        "notes": run.notes,
        "elapsed_s": run.elapsed_s,
    }


def metric_values(runs: list[sy.SystemResult], key: str) -> np.ndarray:
    values = np.asarray([run.cell.get(key) for run in runs], dtype=float)
    if values.shape != (len(runs),) or np.any(~np.isfinite(values)):
        raise RuntimeError(f"{key} 含缺失或非有限值：{values}")
    return values


def run_pilot(ds: Any) -> dict[str, Any]:
    profile = TableProfile()
    tables, build = get_tables(ds, profile)
    cases = (
        ("su_field_steps", False, 1.0),
        ("adaptive_field_steps", True, 1.0),
        ("su_warmup10_measure1", False, 10.0),
        ("adaptive_warmup10_measure1", True, 10.0),
    )
    rows: dict[str, Any] = {}
    for name, mu_enabled, warmup_speedup in cases:
        print(f"PILOT {name}", flush=True)
        rows[name] = slim(run_one(
            tables, 0, mu_enabled=mu_enabled, large_rate=5.0,
            warmup_speedup=warmup_speedup))
    out = {
        "stage": "pilot", "dataset_id": DATASET_ID,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "build": build, "profile": asdict(profile), "cases": rows,
        "decision_rule": (
            "formal arm is eligible only if cell.olla_convergence."
            "all_active_modes_converged is true"),
    }
    path = OUT_DIR / "experience_mu_power_pilot.json"
    write_json(path, out)
    print(f"RESULT={path.resolve()}", flush=True)
    return out


def arm_description(
    mu_enabled: bool,
    *,
    traffic_cfg: sy.TrafficConfig | None = None,
) -> dict[str, Any]:
    resolved_traffic = traffic_cfg or traffic()
    config = {
        "evaluation_mode": "experience", "duration_s": FORMAL_DURATION_S,
        "warmup_s": FORMAL_WARMUP_S,
        "tdd_pattern": "DDDSU", "snapshot_update_ms": 5.0,
        "num_rbg": 17, "rb_per_rbg": 16,
        "traffic_model": "mixed",
        "large_arrival_rate_hz": FORMAL_LARGE_RATE_HZ,
        "small_arrival_rate_hz": FORMAL_SMALL_RATE_HZ,
        "interarrival_scale": float(resolved_traffic.interarrival_scale),
        "target_prb_utilization": FORMAL_TARGET_PRB_UTILIZATION,
        "load_calibration_reference_arm": "SU_MU_adaptive_classic_PF",
        "scheduler": "classic_pf", "pf_window_tti": 100,
        "pf_accounting": "scheduled_tbs",
        "mu_enabled": bool(mu_enabled), "max_mu_users": 2, "mu_rank_per_user": 2,
        "mu_corr_threshold": 0.7, "mu_precoder": "zf", "power_constraint": "ebf",
        "su_mu_rule": "PF-sort; force SU if all queues clear; else MU iff useful>=SU",
        "srs_period_ms": 10.0, "srs_hopping": True,
        "csi_processing_delay_ms": 2.0, "csi_report_period_ms": 20.0,
        "periodic_trace_history": True, "precoding_csi": "h_est",
        "neighbor_prb_util": 0.3, "neighbor_load_jitter": 0.05,
        "olla_warmup_speedup": FORMAL_WARMUP_SPEEDUP,
        "olla_measurement_speedup": 1.0,
    }
    return {
        "name": "SU_MU_adaptive_classic_PF" if mu_enabled else "SU_only_classic_PF",
        "dataset_id": DATASET_ID,
        "config": config,
        "csi": "estimated_causal_SRS",
        "method": "svd_zf" if mu_enabled else "svd",
        "receiver": "mmse",
        "varies": ["mu_enabled"],
    }


def _run_pair(tables: list[sy.UeLinkTable], replications: list[int], *,
              candidate_accounting: str = "scheduled_tbs",
              baseline_accounting: str = "scheduled_tbs",
              traffic_cfg: sy.TrafficConfig | None = None) -> tuple[
                  list[sy.SystemResult], list[sy.SystemResult], list[rg.RngBook]]:
    candidate: list[sy.SystemResult] = []
    baseline: list[sy.SystemResult] = []
    books: list[rg.RngBook] = []
    for i, rep in enumerate(replications, 1):
        print(f"PAIR {i}/{len(replications)} replication={rep}", flush=True)
        candidate.append(run_one(
            tables, rep, mu_enabled=True, accounting=candidate_accounting,
            warmup_speedup=FORMAL_WARMUP_SPEEDUP,
            duration_s=FORMAL_DURATION_S, warmup_s=FORMAL_WARMUP_S,
            traffic_cfg=traffic_cfg))
        baseline.append(run_one(
            tables, rep, mu_enabled=False, accounting=baseline_accounting,
            warmup_speedup=FORMAL_WARMUP_SPEEDUP,
            duration_s=FORMAL_DURATION_S, warmup_s=FORMAL_WARMUP_S,
            traffic_cfg=traffic_cfg))
        books.append(rg.RngBook(MASTER_SEED, rep))
    return candidate, baseline, books


def calibrate_formal_traffic(
    tables: list[sy.UeLinkTable], build: dict[str, Any]
) -> tuple[sy.TrafficConfig, dict[str, Any]]:
    """Calibrate once on the MU-adaptive reference arm, then freeze both arms.

    Calibrating each A/B arm independently would change offered traffic and
    destroy the paired comparison.  A seed disjoint from both the convergence
    pilot and the formal CRN replications prevents load tuning on the outcome.
    """
    calibration = sy.calibrate_traffic_to_prb(
        tables,
        target_prb_utilization=FORMAL_TARGET_PRB_UTILIZATION,
        axis="interarrival",
        tolerance=FORMAL_LOAD_TOLERANCE,
        max_iterations=6,
        probe_replications=2,
        formal_refinements=2,
        num_replications=FORMAL_CALIBRATION_REPLICATIONS,
        master_seed=FORMAL_CALIBRATION_SEED,
        sys_cfg=system_config(duration_s=FORMAL_DURATION_S),
        traffic=traffic(),
        sched=scheduler(
            mu_enabled=True,
            warmup_speedup=FORMAL_WARMUP_SPEEDUP,
        ),
        kpi=sy.KpiConfig(warmup_s=FORMAL_WARMUP_S),
    )
    payload = {
        "stage": "formal_load_calibration",
        "dataset_id": DATASET_ID,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reference_arm": "SU_MU_adaptive_classic_PF",
        "freeze_rule": (
            "calibrate on the reference arm once; use the identical resolved "
            "TrafficConfig in both CRN comparison arms"
        ),
        "calibration_master_seed": FORMAL_CALIBRATION_SEED,
        "comparison_master_seed": MASTER_SEED,
        "build": build,
        "calibration": calibration.as_dict(),
    }
    path = OUT_DIR / "experience_mu_50pct_load_calibration.json"
    write_json(path, payload)
    if calibration.status != "target_met":
        raise RuntimeError(
            "50% PRB 话务校准未进入预注册容差，正式 MU 门 2 已停止："
            f"{calibration.as_dict()}"
        )
    print(f"LOAD_CALIBRATION={path.resolve()}", flush=True)
    return calibration.calibrated_traffic, payload


def run_formal(ds: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    gate1 = gates.gate_channel(ds)
    print(gate1.text(), flush=True)
    if not gate1.passed:
        raise RuntimeError("门 1 未通过")
    tables, build = get_tables(ds, TableProfile())
    formal_traffic, load_calibration = calibrate_formal_traffic(tables, build)

    pilot_reps = list(range(100, 100 + PILOT_N))
    pilot_a, pilot_b, _ = _run_pair(
        tables, pilot_reps, traffic_cfg=formal_traffic)
    pilot_convergence = [
        {
            "replication": rep,
            "candidate": bool(a.cell["olla_convergence"][
                "all_active_modes_converged"]),
            "baseline": bool(b.cell["olla_convergence"][
                "all_active_modes_converged"]),
            "candidate_detail": a.cell["olla_convergence"],
            "baseline_detail": b.cell["olla_convergence"],
        }
        for rep, a, b in zip(pilot_reps, pilot_a, pilot_b, strict=True)
    ]
    if any(not row["candidate"] or not row["baseline"]
           for row in pilot_convergence):
        write_json(
            OUT_DIR / "experience_mu_power_pilot_gate2_diagnostics.json",
            {"stage": "pilot_gate2_failure", "dataset_id": DATASET_ID,
             "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "arms": {"candidate": arm_description(True),
                      "baseline": arm_description(False)},
             "convergence": pilot_convergence})
        raise RuntimeError(
            f"预先冻结的收敛 pilot 未全部通过，正式门 2 已停止：{pilot_convergence}")
    pilot_diff = (metric_values(pilot_a, "small_queue_wait_ms_p95")
                  - metric_values(pilot_b, "small_queue_wait_ms_p95"))
    pilot_std = float(np.std(pilot_diff, ddof=1)) if PILOT_N > 1 else 0.0
    arm_a = arm_description(True, traffic_cfg=formal_traffic)
    arm_b = arm_description(False, traffic_cfg=formal_traffic)
    gate2 = gates.gate_comparison(
        arm_a, arm_b, pilot_std_diff=pilot_std, n_samples=FORMAL_N)
    print(gate2.text(), flush=True)
    if not gate2.passed:
        raise RuntimeError("门 2 未通过")

    formal_reps = list(range(FORMAL_N))
    runs_a, runs_b, books = _run_pair(
        tables, formal_reps, traffic_cfg=formal_traffic)
    formal_health = [
        {
            "replication": rep,
            "candidate": {
                "olla_convergence": a.cell["olla_convergence"],
                "mu_bler_first_tx": a.cell["mu_bler_first_tx"],
                "mu_user_tx_share": a.cell["mu_user_tx_share"],
                "accounting_error_pct": a.cell["accounting_error_pct"],
                "measurement_accounting_error_pct": a.cell[
                    "measurement_accounting_error_pct"],
                "rbg_overlap_violations": a.diagnostics["rbg_overlap_violations"],
            },
            "baseline": {
                "olla_convergence": b.cell["olla_convergence"],
                "accounting_error_pct": b.cell["accounting_error_pct"],
                "measurement_accounting_error_pct": b.cell[
                    "measurement_accounting_error_pct"],
                "rbg_overlap_violations": b.diagnostics["rbg_overlap_violations"],
            },
        }
        for rep, a, b in zip(formal_reps, runs_a, runs_b, strict=True)
    ]
    if any(not bool(r.cell["olla_convergence"]["all_active_modes_converged"])
           for r in [*runs_a, *runs_b]):
        write_json(
            OUT_DIR / "experience_mu_power_formal_gate2_diagnostics.json",
            {"stage": "formal_gate2_failure", "dataset_id": DATASET_ID,
             "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "arms": {"candidate": arm_a, "baseline": arm_b},
             "health": formal_health})
        raise RuntimeError("正式运行有 OLLA 收敛门失败，门 3 已停止")
    if any(float(r.cell["accounting_error_pct"]) != 0.0
           or float(r.cell["measurement_accounting_error_pct"]) != 0.0
           or int(r.diagnostics["rbg_overlap_violations"]) != 0
           or int(r.diagnostics["max_rbg_in_any_tti"]) > 17
           for r in [*runs_a, *runs_b]):
        raise RuntimeError("正式运行违反字节或物理 RBG 不变量")

    metrics = {
        "small_queue_wait_ms_mean": "ms",
        "small_queue_wait_ms_p95": "ms",
        "small_immediate_service_ratio": "ratio",
        "small_completion_delay_ms_p95": "ms",
        "small_pdb_miss_ratio": "ratio",
        "large_flow_drb_throughput_p5_mbps": "Mbps",
        "cell_served_mbps": "Mbps",
        "resource_utilization": "ratio",
        "bler_first_tx": "ratio",
        "mu_rbg_share": "ratio",
    }
    comparisons: dict[str, Any] = {}
    raw: dict[str, Any] = {"candidate": {}, "baseline": {}}
    for key, unit in metrics.items():
        a = metric_values(runs_a, key)
        b = metric_values(runs_b, key)
        raw["candidate"][key] = a.tolist()
        raw["baseline"][key] = b.tolist()
        comparisons[key] = rg.compare_replications(
            a, b, metric=key, unit=unit,
            arm_a=arm_a["name"], arm_b=arm_b["name"],
            books_a=books, books_b=books,
        )
    primary = gates.paired_compare(
        np.asarray(raw["candidate"]["small_queue_wait_ms_p95"]),
        np.asarray(raw["baseline"]["small_queue_wait_ms_p95"]),
    )
    gate3 = gates.gate_conclusion(primary, expected_direction="negative")
    print(gate3.text(), flush=True)
    out = {
        "stage": "formal", "dataset_id": DATASET_ID,
        "prereg": ds.prereg,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_s": time.perf_counter() - t0,
        "primary_metric": "small_queue_wait_ms_p95",
        "direction": "candidate - baseline; negative is better",
        "formal_n": FORMAL_N,
        "pilot": {"replications": pilot_reps, "difference_ms": pilot_diff.tolist(),
                  "std_difference_ms": pilot_std,
                  "convergence": pilot_convergence},
        "gate1": gate1.as_dict(), "gate2": gate2.as_dict(),
        "gate3": gate3.as_dict(), "build": build,
        "traffic_calibration": load_calibration,
        "arms": {"candidate": arm_a, "baseline": arm_b},
        "crn": {"master_seed": MASTER_SEED, "replications": formal_reps,
                "event_mapping": "harq and scheduler tie-break indexed [TTI,UE]"},
        "raw": raw, "comparisons": comparisons,
        "formal_health": formal_health,
        "runs": {"candidate": [slim(r) for r in runs_a],
                 "baseline": [slim(r) for r in runs_b]},
    }
    path = OUT_DIR / "experience_mu_power_formal_n16.json"
    write_json(path, out)
    print(f"RESULT={path.resolve()}", flush=True)
    return out


def power_summary(tables: list[sy.UeLinkTable], mode: str) -> dict[str, Any]:
    diagnostics = [row for table in tables for row in (table.power_diagnostics or [])]
    pair_objects = {
        id(link): link for table in tables for link in table.mu_links.values()
    }.values()
    pair_links = list(pair_objects)
    cap = np.asarray([float(row.get("per_antenna_limit", np.nan))
                      for row in diagnostics])
    max_pa = np.asarray([float(row.get("max_per_antenna_power", np.nan))
                         for row in diagnostics])
    util = np.asarray([float(row.get("utilization_mean", np.nan))
                       for row in diagnostics])
    orth = np.asarray([float(row.get("orthogonality_error_mean", np.nan))
                       for row in diagnostics])
    return {
        "mode": mode,
        "mean_best_se": float(np.mean([t.best_se.mean() for t in tables])),
        "mean_total_power_utilization": float(np.nanmean(util)),
        "min_total_power_utilization": float(np.nanmin(util)),
        "max_per_antenna_over_cap_ratio": float(np.nanmax(max_pa / cap)),
        "mean_orthogonality_error": float(np.nanmean(orth)),
        "mu_pair_true_sinr_db_mean": float(np.mean(
            [link.true_sinr_db.mean() for link in pair_links])),
        "mu_pair_leakage_mean": float(np.mean(
            [link.leakage_ratio.mean() for link in pair_links])),
        "pairs": len(pair_links),
    }


def run_power(ds: Any) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    builds: dict[str, Any] = {}
    for mode in ("ebf", "pebf", "nebf"):
        print(f"POWER {mode}", flush=True)
        tables, build = get_tables(ds, TableProfile(power_constraint=mode))
        rows[mode] = power_summary(tables, mode)
        builds[mode] = build
    out = {
        "stage": "power", "dataset_id": DATASET_ID,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "matrix_convention": "Q[frequency, antenna, stream]; antenna power is row norm",
        "formal_dataset": rows, "builds": builds,
        "deterministic_reverse_controls": (
            "tests/test_physics_invariants.py verifies SU NEBF≈EBF≫PEBF and a "
            "correlated MU case with NEBF<PEBF"),
    }
    path = OUT_DIR / "experience_mu_power_modes.json"
    write_json(path, out)
    print(f"RESULT={path.resolve()}", flush=True)
    return out


def run_pmi(ds: Any) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    builds: dict[str, Any] = {}
    for period in (5.0, 10.0, 20.0, 40.0, 80.0):
        print(f"PMI report={period:g}ms", flush=True)
        profile = TableProfile(csi_report_period_ms=period, mu_enabled=False)
        tables, build = get_tables(ds, profile)
        run = run_one(
            tables, 0, mu_enabled=False,
            warmup_speedup=FORMAL_WARMUP_SPEEDUP,
            duration_s=FORMAL_DURATION_S, warmup_s=FORMAL_WARMUP_S,
            large_rate=FORMAL_LARGE_RATE_HZ,
            small_rate=FORMAL_SMALL_RATE_HZ)
        rows[f"{period:g}ms"] = slim(run)
        rows[f"{period:g}ms"]["cqi_report_sources_ue0"] = (
            tables[0].csi_report_source_snapshot.tolist())
        builds[f"{period:g}ms"] = build
    out = {
        "stage": "pmi", "dataset_id": DATASET_ID,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "periods_ms": [5, 10, 20, 40, 80],
        "default_ms": 20,
        "protocol": {
            "replications": 1, "replication_id": 0,
            "duration_s": FORMAL_DURATION_S, "warmup_s": FORMAL_WARMUP_S,
            "olla_warmup_speedup": FORMAL_WARMUP_SPEEDUP,
            "directional_conclusion_allowed": False,
        },
        "standard_boundary": (
            "38.331 configures CSI report periodicity in slots; 5 ms is allowed "
            "for some numerologies/configurations but is not a universal PMI period"),
        "rows": rows, "builds": builds,
    }
    path = OUT_DIR / "experience_pmi_period_sensitivity.json"
    write_json(path, out)
    print(f"RESULT={path.resolve()}", flush=True)
    return out


def run_stress(ds: Any) -> dict[str, Any]:
    tables, build = get_tables(ds, TableProfile())
    cases = {
        "no_arrivals": dict(large_rate=0.0, small_rate=0.0, small_share=0.5),
        "all_small_sparse": dict(large_rate=0.0, small_rate=20.0, small_share=1.0),
        "all_large_heavy": dict(large_rate=20.0, small_rate=0.0, small_share=0.0),
        "mixed_near_capacity": dict(
            large_rate=FORMAL_LARGE_RATE_HZ,
            small_rate=FORMAL_SMALL_RATE_HZ, small_share=0.5),
    }
    rows: dict[str, Any] = {}
    assertions: dict[str, bool] = {}
    for i, (name, kw) in enumerate(cases.items()):
        print(f"STRESS {name}", flush=True)
        run = run_one(
            tables, 900 + i, mu_enabled=True,
            warmup_speedup=FORMAL_WARMUP_SPEEDUP,
            duration_s=FORMAL_DURATION_S, warmup_s=FORMAL_WARMUP_S,
            **kw)
        rows[name] = slim(run)
        assertions[f"{name}_byte_conservation"] = (
            float(run.cell["accounting_error_pct"]) == 0.0
            and float(run.cell["measurement_accounting_error_pct"]) == 0.0)
        assertions[f"{name}_no_rbg_overlap"] = (
            int(run.diagnostics["rbg_overlap_violations"]) == 0
            and int(run.diagnostics["max_rbg_in_any_tti"]) <= 17)
    assertions["no_arrivals_zero_output"] = (
        rows["no_arrivals"]["cell"]["cell_served_mbps"] == 0.0
        and rows["no_arrivals"]["cell"]["backlog_bytes"] == 0)
    assertions["all_small_exercises_force_su_rule"] = (
        int(rows["all_small_sparse"]["cell"]["su_mu_plan"][
            "su_forced_all_queues_clear"]) > 0)
    if not all(assertions.values()):
        raise RuntimeError(f"压力场景不变量失败：{assertions}")
    out = {
        "stage": "stress", "dataset_id": DATASET_ID,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "build": build, "assertions": assertions, "cases": rows,
    }
    path = OUT_DIR / "experience_mu_power_stress.json"
    write_json(path, out)
    print(f"RESULT={path.resolve()}", flush=True)
    return out


def run_reverse_pf(ds: Any) -> dict[str, Any]:
    """Reverse control: deliberately restore full-band PF accounting."""
    tables, build = get_tables(ds, TableProfile())
    reps = list(range(200, 208))
    scheduled: list[sy.SystemResult] = []
    legacy: list[sy.SystemResult] = []
    for i, rep in enumerate(reps, 1):
        print(f"REVERSE_PF {i}/{len(reps)} replication={rep}", flush=True)
        common = {
            "large_rate": 5.0, "small_rate": 20.0,
            "warmup_speedup": 1.0,
            "duration_s": FORMAL_DURATION_S, "warmup_s": FORMAL_WARMUP_S,
        }
        scheduled.append(run_one(
            tables, rep, mu_enabled=False,
            accounting="scheduled_tbs", **common))
        legacy.append(run_one(
            tables, rep, mu_enabled=False,
            accounting="legacy_fullband", **common))
    if any(not bool(r.cell["olla_convergence"]["all_active_modes_converged"])
           for r in [*scheduled, *legacy]):
        raise RuntimeError("PF 反向控制有 OLLA 收敛门失败")
    a = metric_values(scheduled, "small_queue_wait_ms_p95")
    b = metric_values(legacy, "small_queue_wait_ms_p95")
    paired = gates.paired_compare(a, b)
    gate3 = gates.gate_conclusion(paired, expected_direction="negative")
    partial_scheduled = [
        row for run in scheduled for row in run.diagnostics["allocation_sample"]
        if int(row["n_rbg"]) < 17]
    partial_legacy = [
        row for run in legacy for row in run.diagnostics["allocation_sample"]
        if int(row["n_rbg"]) < 17]
    accounting_sentinel = (
        bool(partial_scheduled) and bool(partial_legacy)
        and all(int(row["pf_credit_bytes"]) == int(row["scheduled_bytes"])
                for row in partial_scheduled)
        and any(int(row["pf_credit_bytes"]) > int(row["scheduled_bytes"])
                for row in partial_legacy)
    )
    if not accounting_sentinel:
        raise RuntimeError("PF 反向口径没有真正改变 partial-band RU credit")
    out = {
        "stage": "reverse_pf", "dataset_id": DATASET_ID,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "build": build, "replications": reps,
        "direction": "scheduled_tbs - legacy_fullband; negative wait is expected",
        "scheduled_wait_ms": a.tolist(), "legacy_wait_ms": b.tolist(),
        "paired": paired.as_dict(), "gate3": gate3.as_dict(),
        "accounting_sentinel": accounting_sentinel,
        "partial_grants_checked": {
            "scheduled": len(partial_scheduled), "legacy": len(partial_legacy)},
        "runs": {"scheduled_tbs": [slim(r) for r in scheduled],
                 "legacy_fullband": [slim(r) for r in legacy]},
    }
    path = OUT_DIR / "experience_pf_accounting_reverse_control.json"
    write_json(path, out)
    print(gate3.text(), flush=True)
    print(f"RESULT={path.resolve()}", flush=True)
    return out


def _pf_sentinel_tables() -> list[sy.UeLinkTable]:
    """Two identical, non-outage links for an accounting-only A/B control."""
    tables: list[sy.UeLinkTable] = []
    for ue in range(2):
        sinr = np.full((2, 1), 15.0)
        mcs = np.full((2, 1), 12, dtype=int)
        se = np.full((2, 1), la.MCS_TABLES[3][12].se)
        tables.append(sy.UeLinkTable(
            ue=ue, sinr_db=sinr, mcs=mcs, se=se,
            best_rank=np.ones(2, dtype=int), best_se=se[:, 0],
            geo_sinr_db=15.0, outage=np.zeros(2, dtype=bool),
            iot_db=3.0, sir_db=12.0, se_gnb=se.copy(),
            best_se_gnb=se[:, 0].copy()))
    return tables


def run_pf_sentinel(_ds: Any) -> dict[str, Any]:
    """Deterministic reverse control where the PF accounting effect is observable."""
    tables = _pf_sentinel_tables()
    cfg = sy.SystemConfig(
        evaluation_mode="experience", duration_s=1.0,
        tdd_pattern="DDDSU", seed=9)
    tr = sy.TrafficConfig(
        model="mixed", small_ue_share=0.5,
        small_file_bytes=1_500, small_arrival_rate_hz=1_500.0,
        file_bytes=500_000, arrival_rate_hz=20.0)
    common = {
        "sys_cfg": cfg,
        "traffic": tr,
        "kpi": sy.KpiConfig(warmup_tti=0),
    }
    runs: dict[str, sy.SystemResult] = {}
    for accounting in ("scheduled_tbs", "legacy_fullband"):
        runs[accounting] = sy.simulate(
            tables,
            sched=sy.SchedulerConfig(
                algorithm="pf", mu_enabled=False, olla_enabled=False,
                pf_accounting=accounting),
            rng=rg.RngBook(123, 0), **common)

    correct = runs["scheduled_tbs"]
    wrong = runs["legacy_fullband"]
    partial_correct = [
        row for row in correct.diagnostics["allocation_sample"]
        if int(row["n_rbg"]) < 17]
    partial_wrong = [
        row for row in wrong.diagnostics["allocation_sample"]
        if int(row["n_rbg"]) < 17]
    assertions = {
        "crn_and_only_accounting_changed": (
            correct.config["rng"] == wrong.config["rng"]),
        "scheduled_tbs_partial_credit_exact": (
            bool(partial_correct)
            and all(int(row["pf_credit_bytes"]) == int(row["scheduled_bytes"])
                    for row in partial_correct)),
        "legacy_partial_credit_exceeds_scheduled": (
            bool(partial_wrong)
            and any(int(row["pf_credit_bytes"]) > int(row["scheduled_bytes"])
                    for row in partial_wrong)),
        "wrong_mean_wait_at_least_0p5ms_worse": (
            float(wrong.cell["small_queue_wait_ms_mean"])
            - float(correct.cell["small_queue_wait_ms_mean"]) > 0.5),
        "wrong_p95_wait_worse": (
            float(wrong.cell["small_queue_wait_ms_p95"])
            > float(correct.cell["small_queue_wait_ms_p95"])),
        "wrong_immediate_service_ratio_lower": (
            float(wrong.cell["small_immediate_service_ratio"])
            < float(correct.cell["small_immediate_service_ratio"])),
        "byte_conservation_both": (
            float(correct.cell["accounting_error_pct"]) == 0.0
            and float(wrong.cell["accounting_error_pct"]) == 0.0
            and float(correct.cell["measurement_accounting_error_pct"]) == 0.0
            and float(wrong.cell["measurement_accounting_error_pct"]) == 0.0),
    }
    if not all(assertions.values()):
        raise RuntimeError(f"PF 确定性反向哨兵失败：{assertions}")
    out = {
        "stage": "pf_sentinel",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "only PF Ravg credit differs; legacy_fullband must hurt small arrivals",
        "scenario": {
            "ues": 2, "sinr_db": 15.0, "mcs": 12,
            "duration_s": 1.0, "warmup_s": 0.0,
            "small_file_bytes": 1_500, "small_arrival_rate_hz": 1_500.0,
            "large_file_bytes": 500_000, "large_arrival_rate_hz": 20.0,
            "olla_enabled": False, "mu_enabled": False,
            "master_seed": 123, "replication": 0,
        },
        "assertions": assertions,
        "scheduled_tbs": slim(correct),
        "legacy_fullband": slim(wrong),
        "deltas_legacy_minus_correct": {
            "small_queue_wait_ms_mean": (
                float(wrong.cell["small_queue_wait_ms_mean"])
                - float(correct.cell["small_queue_wait_ms_mean"])),
            "small_queue_wait_ms_p95": (
                float(wrong.cell["small_queue_wait_ms_p95"])
                - float(correct.cell["small_queue_wait_ms_p95"])),
            "small_immediate_service_ratio": (
                float(wrong.cell["small_immediate_service_ratio"])
                - float(correct.cell["small_immediate_service_ratio"])),
        },
        "partial_grants_checked": {
            "scheduled_tbs": len(partial_correct),
            "legacy_fullband": len(partial_wrong),
        },
    }
    path = OUT_DIR / "experience_pf_accounting_deterministic_sentinel.json"
    write_json(path, out)
    print(f"RESULT={path.resolve()}", flush=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage", choices=(
            "pilot", "formal", "power", "pmi", "stress", "reverse_pf",
            "pf_sentinel", "all"),
        default="pilot")
    args = parser.parse_args()
    stages = ("pilot", "stress", "power", "pmi", "reverse_pf",
              "pf_sentinel", "formal") \
        if args.stage == "all" else (args.stage,)
    # The deterministic PF sentinel constructs its own synthetic link tables and
    # must remain runnable even when no persisted ChannelDataset is available.
    needs_dataset = any(stage != "pf_sentinel" for stage in stages)
    ds = load(DATASET_ID) if needs_dataset else None
    functions = {
        "pilot": run_pilot, "formal": run_formal, "power": run_power,
        "pmi": run_pmi, "stress": run_stress, "reverse_pf": run_reverse_pf,
        "pf_sentinel": run_pf_sentinel,
    }
    for stage in stages:
        functions[stage](ds)


if __name__ == "__main__":
    main()

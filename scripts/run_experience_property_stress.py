"""Randomized cross-configuration property stress for experience_v2.

This is not an effect-size experiment.  It deliberately varies power, CSI,
numerology, TDD and traffic while asserting invariants that must hold in every
run.  Results are written as an audit artifact; no directional benefit claim is
derived from these cases.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import csi_aging as ca  # noqa: E402
from superran import rng as rg  # noqa: E402
from superran import system as sy  # noqa: E402

OUT = ROOT / "artifacts" / "results" / "experience_randomized_property_stress.json"
N_CASES = 18


def _channels(seed: int, *, n_ue: int = 6, n_snap: int = 8) -> tuple[
    list[np.ndarray], list[np.ndarray], list[float], list[float]
]:
    random = np.random.default_rng(seed)
    h_true: list[np.ndarray] = []
    h_est: list[np.ndarray] = []
    geo_sinr: list[float] = []
    geo_sir: list[float] = []
    for ue in range(n_ue):
        gain = 10.0 ** (random.uniform(-5.0, 3.0) / 20.0)
        h = gain * (
            random.standard_normal((n_snap, 17, 8, 2))
            + 1j * random.standard_normal((n_snap, 17, 8, 2))
        ) / np.sqrt(2.0)
        error_scale = 0.01 + 0.03 * ((seed + ue) % 4)
        e = error_scale * (
            random.standard_normal(h.shape) + 1j * random.standard_normal(h.shape)
        ) / np.sqrt(2.0)
        h_true.append(h)
        h_est.append(h + e)
        geo_sinr.append(float(random.uniform(2.0, 22.0)))
        geo_sir.append(float(random.uniform(5.0, 18.0)))
    return h_true, h_est, geo_sinr, geo_sir


def _finite(value: Any) -> bool:
    return value is None or not isinstance(value, float) or np.isfinite(value)


def _run_case(case_id: int) -> dict[str, Any]:
    mode = ("ebf", "pebf", "nebf")[case_id % 3]
    scs = (15, 30, 60)[(case_id // 3) % 3]
    tdd = ("DDDD", "DDDSU", "DSU")[(case_id // 6) % 3]
    report_ms = (5.0, 20.0, 80.0)[case_id % 3]
    srs_ms = (5.0, 10.0, 20.0, 40.0)[case_id % 4]
    hopping = bool((case_id // 2) % 2)
    mu_enabled = bool(case_id % 4 != 0)
    precoder = "type1" if case_id % 5 == 0 else "svd"
    h_true, h_est, geo_sinr, geo_sir = _channels(70_000 + case_id)
    book = rg.RngBook(master_seed=83_117, replication=case_id)
    csi = ca.CsiConfig(
        srs_period_ms=srs_ms,
        hopping=hopping,
        processing_delay_ms=float(case_id % 4),
        csi_report_period_ms=report_ms,
        periodic_trace_history=True,
    )
    tables = sy.build_link_tables(
        h_true,
        geo_sinr,
        h_for_precoding_users=h_est,
        geo_sir_db=geo_sir,
        neighbor_load=(0.15, 0.30, 0.55)[case_id % 3],
        neighbor_load_jitter=0.10,
        load_jitter_rng=book.generator("neighbor_load"),
        max_rank=2,
        num_snapshots=8,
        rb_per_rbg=1,
        csi=csi,
        snapshot_ms=5.0,
        precoder=precoder,
        power_constraint=mode,
        mu_enabled=True,
        mu_rank_per_user=2,
        mu_precoder="zf" if case_id % 2 == 0 else "rzf",
    )
    system = sy.SystemConfig(
        evaluation_mode="experience",
        duration_s=0.30,
        scs_khz=scs,
        num_rbg=17,
        rb_per_rbg=16,
        tdd_pattern=tdd,
        snapshot_update_ms=5.0,
        power_constraint=mode,
        seed=case_id,
    )
    traffic = sy.TrafficConfig(
        model="mixed",
        small_ue_share=(0.25, 0.50, 0.75)[case_id % 3],
        small_file_bytes=(300, 1_500, 8_000)[case_id % 3],
        small_arrival_rate_hz=(30.0, 100.0, 250.0)[case_id % 3],
        file_bytes=(80_000, 250_000, 600_000)[case_id % 3],
        arrival_rate_hz=(0.5, 2.0, 6.0)[case_id % 3],
    )
    scheduler = sy.SchedulerConfig(
        algorithm="pf",
        pf_window_tti=(20, 100, 500)[case_id % 3],
        pf_accounting="scheduled_tbs",
        olla_enabled=bool(case_id % 2),
        mu_enabled=mu_enabled,
        max_mu_users=2,
        mu_rank_per_user=2,
        mu_corr_threshold=(0.35, 0.70, 1.00)[case_id % 3],
        mu_precoder="zf" if case_id % 2 == 0 else "rzf",
    )
    result = sy.simulate(
        tables,
        sys_cfg=system,
        traffic=traffic,
        sched=scheduler,
        kpi=sy.KpiConfig(warmup_tti=0),
        rng=book,
    )
    cell = result.cell
    diag = result.diagnostics
    checks: dict[str, bool] = {
        "byte_conservation": abs(float(cell["accounting_error_pct"])) < 1e-12,
        "measurement_conservation": (
            abs(float(cell["measurement_accounting_error_pct"])) < 1e-12
        ),
        "rbg_no_overlap": int(diag["rbg_overlap_violations"]) == 0,
        "rbg_within_band": int(diag["max_rbg_in_any_tti"]) <= 17,
        "resource_utilization_range": (
            -1e-12 <= float(cell["resource_utilization"]) <= 1.0 + 1e-12
        ),
        "occupancy_range": -1e-12 <= float(cell["occupancy"]) <= 1.0 + 1e-12,
        "mu_disabled_is_zero": (
            mu_enabled or abs(float(cell["mu_user_tx_share"])) < 1e-15
        ),
        "configured_power_mode_recorded": (
            result.config["system"]["power_constraint"] == mode
        ),
        "cell_scalars_finite": all(
            _finite(cell.get(key))
            for key in (
                "cell_served_mbps",
                "offered_mbps",
                "resource_utilization",
                "occupancy",
                "bler_first_tx",
                "mu_user_tx_share",
            )
        ),
    }
    allocations = list(diag["allocation_sample"])
    checks["pf_credit_is_scheduled_tbs"] = all(
        int(row["pf_credit_bytes"]) == int(row["scheduled_bytes"])
        for row in allocations
    )
    checks["allocation_bitmap_shape"] = all(
        int(row["n_rbg"]) == len(row["rbg_indices"])
        and len(set(row["rbg_indices"])) == len(row["rbg_indices"])
        and all(0 <= int(x) < 17 for x in row["rbg_indices"])
        for row in allocations
    )
    mu_groups: dict[int, list[dict[str, Any]]] = {}
    for row in allocations:
        group = row.get("mu_group_id")
        if group is not None:
            mu_groups.setdefault(int(group), []).append(row)
    checks["mu_group_shared_bitmap"] = all(
        len(rows) != 2
        or tuple(rows[0]["rbg_indices"]) == tuple(rows[1]["rbg_indices"])
        for rows in mu_groups.values()
    )
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise AssertionError(f"case {case_id} failed: {failed}")
    return {
        "case": case_id,
        "power_constraint": mode,
        "scs_khz": scs,
        "tdd_pattern": tdd,
        "precoder": precoder,
        "srs_period_ms": srs_ms,
        "srs_hopping": hopping,
        "csi_report_period_ms": report_ms,
        "mu_enabled": mu_enabled,
        "offered_mbps": float(cell["offered_mbps"]),
        "served_mbps": float(cell["cell_served_mbps"]),
        "backlog_bytes": int(cell["backlog_bytes"]),
        "resource_utilization": float(cell["resource_utilization"]),
        "bler_first_tx": float(cell["bler_first_tx"]),
        "mu_user_tx_share": float(cell["mu_user_tx_share"]),
        "allocation_samples": len(allocations),
        "checks": checks,
    }


def main() -> None:
    rows: list[dict[str, Any]] = []
    for case_id in range(N_CASES):
        print(f"PROPERTY {case_id + 1}/{N_CASES}")
        rows.append(_run_case(case_id))
    payload = {
        "stage": "randomized_property_stress",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": (
            "cross-configuration invariants only; not registered for directional benefit"
        ),
        "cases": rows,
        "summary": {
            "cases": len(rows),
            "checks_per_case": len(rows[0]["checks"]),
            "checks_passed": sum(
                int(passed) for row in rows for passed in row["checks"].values()
            ),
            "checks_total": sum(len(row["checks"]) for row in rows),
            "power_modes": sorted({row["power_constraint"] for row in rows}),
            "scs_khz": sorted({row["scs_khz"] for row in rows}),
            "tdd_patterns": sorted({row["tdd_pattern"] for row in rows}),
            "csi_report_period_ms": sorted(
                {row["csi_report_period_ms"] for row in rows}
            ),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
        newline="\n",
    )
    print(f"RESULT={OUT}")


if __name__ == "__main__":
    main()

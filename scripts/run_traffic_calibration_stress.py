"""Stress target-load calibration across targets, axes, and SU/MU modes.

All inputs are synthetic.  This script is an invariant/operability gate for the
calibration controller, not evidence of an algorithm performance gain.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import system as sy  # noqa: E402

OUT = ROOT / "artifacts" / "results" / "traffic_calibration_stress.json"
SIZE_CDF = ROOT / "presets" / "traffic" / "synthetic_packet_size.csv"
INTERVAL_CDF = ROOT / "presets" / "traffic" / "synthetic_interarrival_ms.csv"


@dataclass(frozen=True)
class Case:
    name: str
    target: float
    axis: sy.TrafficCalibrationAxis
    mu_enabled: bool
    master_seed: int


CASES = (
    Case("su_10_interarrival", 0.10, "interarrival", False, 9101),
    Case("su_30_interarrival", 0.30, "interarrival", False, 9102),
    Case("mu_50_interarrival", 0.50, "interarrival", True, 9103),
    Case("su_30_packet_size", 0.30, "packet_size", False, 9104),
    Case("mu_30_balanced", 0.30, "balanced", True, 9105),
)


def _tables() -> list[sy.UeLinkTable]:
    random = np.random.default_rng(89017)
    channels = [
        (
            random.standard_normal((8, 17, 8, 2))
            + 1j * random.standard_normal((8, 17, 8, 2))
        )
        / np.sqrt(2.0)
        for _ in range(6)
    ]
    return sy.build_link_tables(
        channels,
        [22.0, 19.0, 16.0, 12.0, 8.0, 4.0],
        max_rank=2,
        rb_per_rbg=16,
        power_constraint="ebf",
        mu_enabled=True,
        mu_rank_per_user=2,
        mu_precoder="zf",
    )


def _traffic() -> sy.TrafficConfig:
    return sy.TrafficConfig(
        model="cdf",
        interarrival_cdf_unit="ms",
        classes=(
            sy.TrafficClassConfig(
                name="video",
                ue_share=0.0,
                file_bytes=500_000,
                arrival_rate_hz=0.0,
                packet_size_cdf=str(SIZE_CDF),
                interarrival_cdf=str(INTERVAL_CDF),
                packet_size_scale=1.0,
                interarrival_scale=1.0,
                ue_ids=(0, 1, 2, 3),
            ),
            sy.TrafficClassConfig(
                name="xr",
                ue_share=0.0,
                file_bytes=1_500,
                arrival_rate_hz=0.0,
                pdb_ms=20.0,
                resource_type="delay_critical_GBR",
                is_small=True,
                packet_size_cdf=str(SIZE_CDF),
                interarrival_cdf=str(INTERVAL_CDF),
                packet_size_scale=0.12,
                interarrival_scale=0.45,
                ue_ids=(4, 5),
            ),
        ),
    )


def _checks(
    case: Case, calibration: sy.TrafficCalibrationResult
) -> dict[str, bool]:
    result = calibration.result
    util = float(result.cell["serving_cell_prb_utilization"]["mean"])
    distribution = result.cell["tti_occupied_rbg_distribution"]
    share_sum = sum(float(row["tti_share"]["mean"]) for row in distribution["bins"])
    initial = calibration.initial_traffic
    adjusted = calibration.calibrated_traffic
    factor = float(
        min(
            calibration.formal_history,
            key=lambda row: abs(
                float(row["measured_prb_utilization"]) - case.target
            ),
        )["offered_load_factor_vs_input"]
    )
    if case.axis == "interarrival":
        scale_equation = np.isclose(
            adjusted.interarrival_scale, initial.interarrival_scale / factor
        ) and np.isclose(adjusted.packet_size_scale, initial.packet_size_scale)
    elif case.axis == "packet_size":
        scale_equation = np.isclose(
            adjusted.packet_size_scale, initial.packet_size_scale * factor
        ) and np.isclose(adjusted.interarrival_scale, initial.interarrival_scale)
    else:
        root = np.sqrt(factor)
        scale_equation = np.isclose(
            adjusted.packet_size_scale, initial.packet_size_scale * root
        ) and np.isclose(
            adjusted.interarrival_scale, initial.interarrival_scale / root
        )
    run_checks = []
    for run in result.runs:
        attributed = sum(
            float(user["allocated_prb_equivalent_attributed"])
            for user in run.users
        )
        raw_distribution = run.cell["tti_occupied_rbg_distribution"]
        raw_share_sum = sum(
            float(row["tti_share"]) for row in raw_distribution["bins"]
        )
        run_checks.append(
            abs(float(run.cell["accounting_error_pct"])) < 1e-12
            and abs(float(run.cell["measurement_accounting_error_pct"])) < 1e-12
            and abs(attributed - float(run.cell["allocated_prb_equivalent"])) < 1e-9
            and abs(raw_share_sum - 1.0) <= 1e-3
        )
    return {
        "formal_status_target_met": calibration.status == "target_met",
        "formal_mean_within_tolerance": abs(util - case.target) <= calibration.tolerance,
        "formal_mean_is_reported_achieved": bool(
            np.isclose(util, calibration.as_dict()["achieved_prb_utilization"])
        ),
        "utilization_in_unit_interval": 0.0 <= util <= 1.0,
        # Each of 18 displayed bins is rounded to four decimals.  The maximum
        # possible accumulated display error is 18 * 0.00005 = 0.0009.
        "occupancy_distribution_sums_to_one_after_display_rounding": (
            abs(share_sum - 1.0) <= 1e-3
        ),
        "selected_axis_scale_equation": bool(scale_equation),
        "all_formal_runs_conserve_bytes_and_prb": all(run_checks),
        "mu_headline_definition_present": (
            "mu_paired_prb_share_of_used" in result.cell
        ),
    }


def _run(case: Case, tables: list[sy.UeLinkTable]) -> dict[str, Any]:
    scheduler = sy.SchedulerConfig(
        algorithm="pf",
        pf_accounting="scheduled_tbs",
        olla_enabled=True,
        mu_enabled=case.mu_enabled,
        max_mu_users=2,
        mu_rank_per_user=2,
        mu_corr_threshold=0.99,
        mu_precoder="zf",
    )
    calibration = sy.calibrate_traffic_to_prb(
        tables,
        target_prb_utilization=case.target,
        axis=case.axis,
        tolerance=0.05,
        max_iterations=7,
        probe_replications=2,
        formal_refinements=3,
        num_replications=6,
        master_seed=case.master_seed,
        sys_cfg=sy.SystemConfig(
            evaluation_mode="experience",
            duration_s=1.0,
            tdd_pattern="DDDSU",
            seed=case.master_seed,
        ),
        traffic=_traffic(),
        sched=scheduler,
        kpi=sy.KpiConfig(warmup_s=0.2),
    )
    checks = _checks(case, calibration)
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise AssertionError(f"{case.name} failed: {failed}")
    return {
        "case": case.name,
        "target_prb_utilization": case.target,
        "axis": case.axis,
        "mu_enabled": case.mu_enabled,
        "master_seed": case.master_seed,
        "calibration": calibration.as_dict(),
        "checks": checks,
    }


def main() -> None:
    tables = _tables()
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(CASES, 1):
        print(f"CALIBRATION {index}/{len(CASES)} {case.name}", flush=True)
        rows.append(_run(case, tables))
    payload = {
        "stage": "traffic_calibration_stress",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": (
            "synthetic invariant/operability gate; no field or directional-benefit claim"
        ),
        "cases": rows,
        "summary": {
            "cases": len(rows),
            "targets": sorted({row["target_prb_utilization"] for row in rows}),
            "axes": sorted({row["axis"] for row in rows}),
            "modes": sorted(
                {"MU" if row["mu_enabled"] else "SU" for row in rows}
            ),
            "checks_passed": sum(
                int(passed) for row in rows for passed in row["checks"].values()
            ),
            "checks_total": sum(len(row["checks"]) for row in rows),
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

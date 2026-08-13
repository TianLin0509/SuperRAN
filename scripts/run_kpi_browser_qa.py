"""Generate a reproducible two-tab KPI report for browser QA.

The channel and traffic CDFs in this script are synthetic.  The run validates
presentation and accounting contracts; it is not a field-performance claim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import kpi_view  # noqa: E402
from superran import system as sy  # noqa: E402

OUT = ROOT / "output" / "kpi-browser-qa.json"
SIZE_CDF = ROOT / "presets" / "traffic" / "synthetic_packet_size.csv"
INTERVAL_CDF = ROOT / "presets" / "traffic" / "synthetic_interarrival_ms.csv"


def _synthetic_tables() -> list[sy.UeLinkTable]:
    random = np.random.default_rng(20260809)
    channels = [
        (
            random.standard_normal((8, 24, 16, 4))
            + 1j * random.standard_normal((8, 24, 16, 4))
        )
        / np.sqrt(2.0)
        for _ in range(6)
    ]
    return sy.build_link_tables(
        channels,
        [22.0, 19.0, 16.0, 12.0, 8.0, 4.0],
        max_rank=4,
        rb_per_rbg=16,
        power_constraint="ebf",
        mu_enabled=True,
        mu_rank_per_user=2,
        mu_precoder="zf",
    )


def main() -> None:
    tables = _synthetic_tables()
    traffic = sy.TrafficConfig(
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
                packet_size_scale=1.2,
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
    system = sy.SystemConfig(
        evaluation_mode="experience",
        duration_s=1.2,
        tdd_pattern="DDDSU",
        seed=20260809,
    )
    scheduler = sy.SchedulerConfig(
        algorithm="pf",
        pf_accounting="scheduled_tbs",
        mu_enabled=True,
        max_mu_users=2,
        mu_rank_per_user=2,
        mu_corr_threshold=0.99,
        mu_precoder="zf",
    )
    kpi = sy.KpiConfig(warmup_s=0.2)
    calibration = sy.calibrate_traffic_to_prb(
        tables,
        target_prb_utilization=0.50,
        axis="interarrival",
        tolerance=0.04,
        max_iterations=5,
        probe_replications=2,
        formal_refinements=2,
        num_replications=8,
        master_seed=20260809,
        sys_cfg=system,
        traffic=traffic,
        sched=scheduler,
        kpi=kpi,
    )
    result = calibration.result.as_dict()
    result["dataset_id"] = "synthetic-kpi-browser-qa"
    result["traffic_calibration"] = calibration.as_dict()
    result.setdefault("notes", []).append(
        "本页只使用合成信道和示例 CDF，验证呈现、校准和统计口径；"
        "不代表公司 CDF、现网负载或算法收益。"
    )
    report = kpi_view.write_kpi_report(
        result,
        dataset_id=result["dataset_id"],
        serve=False,
        kpi_focus=[
            "serving_cell_prb_utilization",
            "mu_paired_prb_share_of_used",
            "first_packet_delay_ms_p95",
            "experienced_mbps",
            "XR",
        ],
    )
    manifest = {
        "report": report,
        "calibration": calibration.as_dict(),
        "headline": {
            key: result["cell"].get(key)
            for key in (
                "cell_experienced_mbps",
                "first_packet_delay_ms_p95",
                "serving_cell_prb_utilization",
                "mu_paired_prb_share_of_used",
            )
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report["html_path"])
    print(OUT)


if __name__ == "__main__":
    main()

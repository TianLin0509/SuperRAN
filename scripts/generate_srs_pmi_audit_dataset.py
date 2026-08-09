"""Generate the preregistered multi-cell dataset used by the SRS/PMI audit.

Run from the repository root.  The explicit ``__main__`` guard is required by
Windows multiprocessing; without it ``generate(..., workers=4)`` silently has
to fall back to the serial path.
"""
from __future__ import annotations

import json
from pathlib import Path

from superwireless import analysis, generate

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "artifacts" / "plans" / "SRS_PMI_AND_EXPERIENCE_AUDIT_PLAN.md"


CONFIG = {
    "source": "internal_sim",
    "scenario": "UMa_NLOS",
    "channel_model": "CDL-C",
    "num_sites": 7,
    "sectors_per_site": 3,
    "isd_m": 500.0,
    "num_ues": 10,
    "num_interfering_ues": 10,
    "num_bs_tx_ant": 64,
    "num_bs_rx_ant": 64,
    "num_ue_tx_ant": 4,
    "num_ue_rx_ant": 4,
    "bandwidth_hz": 100_000_000.0,
    "subcarrier_spacing": 30_000,
    "num_rb": 272,
    "carrier_freq_hz": 2_600_000_000.0,
    "link": "DL",
    "channel_est_mode": "ls_linear",
    "seed": 20260809,
    "ue_speed_kmh": 3.0,
    # One slot per sample keeps SIR/SINR on the same snapshot. This script is
    # for paired link-level inference, not for the system time trace. Parallel
    # generation can yield repeated fading draws at the same position, so the
    # audit clusters observations by UE position before significance tests.
    "num_slots_per_sample": 1,
    "bs_panel": [8, 4, 2],
    "antenna_model_mode": "effective_subarray",
    "bs_antenna": {
        "horizontal_port_spacing_lambda": 0.5,
        "reference_frequency_hz": 2_600_000_000.0,
        "element_pattern": {
            "source": "parametric_temporary",
            "horizontal_hpbw_deg": 65.0,
            "vertical_hpbw_deg": 65.0,
            "peak_gain_dbi": 8.0,
            "xpd_db": 8.0,
        },
        "fixed_vertical_subarray": {
            "elements_per_rf_port": 3,
            "ae_vertical_spacing_lambda": 0.67,
            "fixed_downtilt_deg": 6.0,
            "calibration_id": "company-64T-1to3-192ae-v1",
        },
    },
    "measurements": {
        "ssb_rsrp": False,
        "interferer_channels": True,
    },
}


def main() -> None:
    prereg = analysis.lock(
        primary_metric="spectral_efficiency",
        baseline="Type-I-style wideband PMI weight",
        csi_basis="estimated",
        metric_unit="bit/s/Hz",
        higher_is_better=True,
        secondary_metrics=[
            "rank_distribution",
            "capacity_bound",
            "wideband_loss",
            "codebook_quantization_loss",
        ],
        note=(
            "Paired SRS covariance/SVD vs Type-I-style PMI weight comparison. "
            "Both arms design weights from h_est and evaluate on h_true with "
            "the same geometric interference/noise operating point."
        ),
    )
    summary = generate.generate(
        CONFIG,
        num_samples=80,
        plan_markdown=PLAN_PATH.read_text(encoding="utf-8"),
        prereg_id=prereg.prereg_id,
        workers=4,
        collect_ssb=False,
    )
    print(json.dumps({
        "prereg_id": prereg.prereg_id,
        "dataset_id": summary["dataset_id"],
        "elapsed_s": summary["elapsed_s"],
        "shape": summary["shape"],
        "interference_modeled": summary["interference_modeled"],
        "parallel": summary["parallel"],
        "path": summary["path"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

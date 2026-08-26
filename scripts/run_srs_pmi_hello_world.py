"""Run the documented, preregistered SRS/SVD-vs-PMI Hello World.

The experiment deliberately starts with the company 64T4R single-cell preset.
It keeps the real 100 MHz/272-RB array and LS+LMMSE estimates, while isolating
neighbouring-cell interference.  It reports two deliberately different views:

* a same-SRS diagnostic that changes only SVD versus Type-I-style construction;
* the preregistered operational schemes, UL-SRS/SVD versus DL-CSI-RS/PMI,
  which explicitly declare both CSI source and method as tested variables.

A separate multicell run is required before a multicell claim can be made.

Run from the repository root::

    python -u scripts/run_srs_pmi_hello_world.py

The ``__main__`` guard is required for Windows multiprocessing.
Exit code 3 means the evidence was written but Gate 3 blocked a directional
claim; it is a valid audited outcome, not a data-generation crash.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from superran import (
    analysis,
    channelhub,
    gates,
    generate,
    loader,
    plan,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "SRS_PMI_HELLO_WORLD_RESULT.json"
PLAN_ARTIFACT = ROOT / "artifacts" / "SRS_PMI_HELLO_WORLD_PLAN.md"
NUM_UES = 10
NUM_SNAPSHOTS_PER_UE = 8
NUM_SAMPLES = NUM_UES * NUM_SNAPSHOTS_PER_UE

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _write_json(payload: dict) -> None:
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    warmup = channelhub.warmup()
    draft, profile = plan.create_draft(
        "在单小区预置 64T4R 全带场景，先用同一批上行 SRS 估计信道诊断"
        "逐 RBG 协方差/SVD 与 Type-I-style 码本的构造差异；再比较"
        "UL-SRS/SVD 与 DL-CSI-RS/PMI 两套真实方案的谱效",
        preset="company_64t4r",
        overrides={
            "num_ues": NUM_UES,
            "num_samples": NUM_SAMPLES,
            "num_slots_per_sample": 1,
            "link": "BOTH",
        },
    )
    draft, profile, _ = plan.revise_draft(
        draft.draft_id,
        design={
            "baseline": "DL-CSI-RS-derived Type-I-style PMI (MMSE)",
            "metric": "逐样本 spectral_efficiency [bit/s/Hz]",
            "scope": "单小区 64T4R、100 MHz、CDL-C、SRS LS+LMMSE 估计",
            "hypothesis": "不预设方向；Gate 3 通过后才允许报告有方向的收益",
        },
    )
    prereg = analysis.lock(
        draft_id=draft.draft_id,
        primary_metric="spectral_efficiency",
        metric_unit="bit/s/Hz",
        baseline="DL-CSI-RS-derived Type-I-style PMI (MMSE)",
        csi_basis="paired_ul_srs_vs_dl_csirs",
        higher_is_better=True,
        secondary_metrics=["cell_edge_spectral_efficiency"],
        note=(
            "Primary comparison is the operational scheme pair: SVD designed "
            "from reciprocity-mapped UL SRS versus Type-I-style PMI selected "
            "from DL CSI-RS. Both share h_true, MMSE receiver and per-sample "
            "operating point. A same-SRS diagnostic isolates codebook loss."
        ),
    )

    cfg, own = plan.resolved_config(draft)
    cfg.pop("num_samples", None)
    wanted = own.get("measurements_wanted") or ["channel"]
    plan_markdown = plan.render_plan_markdown(draft, profile, wanted)
    PLAN_ARTIFACT.write_text(plan_markdown, encoding="utf-8")

    summary = generate.generate(
        cfg,
        num_samples=NUM_SAMPLES,
        snr_range_dB=own.get("snr_range_dB"),
        plan_markdown=plan_markdown,
        draft_id=draft.draft_id,
        prereg_id=prereg.prereg_id,
        workers="auto",
        # SSB is not consumed by this paired precoder experiment.  This is an
        # explicit scope choice recorded in the dataset, not a silent fallback.
        collect_ssb=False,
    )

    dataset = loader.load(summary["dataset_id"])
    gate1 = gates.gate_channel(
        dataset,
        expected_precoding_csi_source="ul_srs_estimate",
    )
    result: dict = {
        "experiment": "SRS/SVD vs Type-I-style PMI Hello World",
        "warmup": warmup,
        "draft_id": draft.draft_id,
        "preregistration": prereg.as_dict(),
        "plan_path": str(PLAN_ARTIFACT),
        "dataset": summary,
        "gate1": gate1.as_dict(),
        "gate1_text": gate1.text(),
        "comparison": None,
        "status": "gate1_blocked" if not gate1.passed else "gate1_passed",
    }
    _write_json(result)
    if not gate1.passed:
        print(gate1.text())
        print(f"Gate 1 blocked; evidence written to {ARTIFACT}")
        return 2

    same_srs_diagnostic = dataset.compare_arms(
        {
            "name": "SRS estimate + per-RBG covariance/SVD",
            "method": "svd",
            "csi": "srs",
            "receiver": "mmse",
        },
        {
            "name": "SRS estimate + Type-I-style codebook",
            "method": "type1",
            "csi": "srs",
            "receiver": "mmse",
        },
    )
    comparison = dataset.compare_arms(
        {
            "name": "UL-SRS-derived covariance/SVD weight",
            "method": "svd",
            "csi": "srs",
            "receiver": "mmse",
            "varies": ["csi", "method"],
        },
        {
            "name": "DL-CSI-RS-derived Type-I-style PMI weight",
            "method": "type1",
            "csi": "csirs",
            "receiver": "mmse",
            "varies": ["csi", "method"],
        },
    )
    # The preregistration's primary metric refers to the operational scheme
    # pair, not to the same-SRS mechanism diagnostic.  Bind that distinction
    # explicitly so a later report cannot promote the diagnostic after seeing
    # its larger point estimate.
    comparison.identity = analysis.classify(prereg, "spectral_efficiency")
    same_srs_diagnostic.identity = {
        "status": "exploratory",
        "primary": False,
        "prereg_id": prereg.prereg_id,
        "why": (
            "same-SRS codebook mechanism diagnostic; the preregistered primary "
            "comparison is UL-SRS/SVD versus DL-CSI-RS/PMI"
        ),
    }
    result["same_srs_codebook_diagnostic"] = same_srs_diagnostic.as_dict()
    result["same_srs_codebook_diagnostic_text"] = same_srs_diagnostic.text()
    result["comparison"] = comparison.as_dict()
    result["comparison_text"] = comparison.text()
    result["status"] = "comparison_passed" if comparison.passed else "comparison_blocked"
    result["exit_code_contract"] = {
        "0": "operational comparison passed Gate 1/2/3",
        "2": "Gate 1 blocked",
        "3": "evidence written; operational Gate 3 blocked the claim",
    }
    _write_json(result)

    print(gate1.text())
    print(same_srs_diagnostic.text())
    print(comparison.text())
    print(f"Evidence written to {ARTIFACT}")
    return 0 if comparison.passed else 3


if __name__ == "__main__":
    raise SystemExit(main())

r"""Generate reproducible evidence for the channel-generation deep audit.

The script intentionally uses only the public SuperRAN generation and loading
APIs.  It produces three small but full-band datasets:

1. one-cell 64T4R CDL-C with a real LS estimate;
2. two physical sites / six sectors with stored interferer channels;
3. uplink SRS C_SRS=63/B_SRS=1, comparing one-hop LS with 17-hop concat.

Run from any directory::

    python -X utf8 scripts/run_channel_generation_audit.py

The evidence is written to ``artifacts/channel-generation-audit/evidence.json``.
Generated datasets remain in the normal SuperRAN dataset store and their
IDs are recorded in the evidence file.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import gates, generate, load, plan  # noqa: E402
from superran.native import (  # noqa: E402
    SRSResourceConfig,
    srs_hopping_cycle_length,
    srs_rb_indices,
    srs_sequence,
    zadoff_chu,
)

OUT_DIR = ROOT / "artifacts" / "channel-generation-audit"
EVIDENCE_PATH = OUT_DIR / "evidence.json"


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_state(repo: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=repo, text=True, encoding="utf-8"
        ).strip()

    status = run("status", "--short").splitlines()
    return {
        "path": str(repo),
        "head": run("rev-parse", "HEAD"),
        "dirty": bool(status),
        "changed_path_count": len(status),
    }


def _distribution(values: np.ndarray) -> dict[str, float | int | None]:
    a = np.asarray(values, dtype=np.float64)
    a = a[np.isfinite(a)]
    if not a.size:
        return {"n": 0, "min": None, "p5": None, "median": None,
                "mean": None, "p95": None, "max": None}
    p5, med, p95 = np.percentile(a, [5, 50, 95])
    return {
        "n": int(a.size),
        "min": float(a.min()),
        "p5": float(p5),
        "median": float(med),
        "mean": float(a.mean()),
        "p95": float(p95),
        "max": float(a.max()),
    }


def _nmse_db(h_est: np.ndarray, h_true: np.ndarray) -> np.ndarray:
    axes = tuple(range(1, h_true.ndim))
    err = np.sum(np.abs(h_est - h_true) ** 2, axis=axes)
    ref = np.sum(np.abs(h_true) ** 2, axis=axes)
    return 10.0 * np.log10(np.maximum(err / np.maximum(ref, 1e-30), 1e-30))


def _matrix_record(matrix: np.ndarray) -> dict[str, Any]:
    m = np.asarray(matrix)
    abs_db = 20.0 * np.log10(np.maximum(np.abs(m), 1e-12))
    selected = [(0, 0), (0, 3), (31, 1), (63, 3)]
    return {
        "shape": list(m.shape),
        "magnitude_db": np.round(abs_db, 5).tolist(),
        "selected_entries": [
            {
                "bs_port": i,
                "ue_port": j,
                "real": float(np.real(m[i, j])),
                "imag": float(np.imag(m[i, j])),
                "magnitude": float(np.abs(m[i, j])),
                "phase_deg": float(np.rad2deg(np.angle(m[i, j]))),
            }
            for i, j in selected
        ],
    }


def _rank_record(h: np.ndarray) -> dict[str, Any]:
    singular = np.linalg.svd(np.asarray(h), compute_uv=False)
    relative = singular / np.maximum(singular[..., :1], 1e-30)
    ranks = np.sum(relative > 1e-5, axis=-1)
    return {
        "threshold_relative_to_sigma1": 1e-5,
        "rank_distribution": {
            str(k): int(np.sum(ranks == k)) for k in range(1, h.shape[-1] + 1)
        },
        "full_rank_fraction": float(np.mean(ranks == h.shape[-1])),
        "mean_singular_values": np.mean(singular, axis=(0, 1, 2)).tolist(),
        "sigma4_over_sigma1": _distribution(relative[..., -1]),
    }


def _gate_record(dataset: Any) -> dict[str, Any]:
    result = gates.gate_channel(dataset).as_dict()
    result["warning_count"] = sum(
        1 for item in result["items"]
        if item["severity"] == "warn" and not item["passed"]
    )
    return result


def _base_company_config() -> dict[str, Any]:
    cfg = dict(plan.load_presets()["company_64t4r"]["config"])
    cfg.update(
        {
            "source": "internal_sim",
            "scenario": "UMa_NLOS",
            "channel_model": "CDL-C",
            "num_sites": 1,
            "sectors_per_site": 1,
            "num_bs_tx_ant": 64,
            "num_bs_rx_ant": 64,
            "num_ue_tx_ant": 4,
            "num_ue_rx_ant": 4,
            "num_rb": 272,
            "num_ofdm_symbols": 14,
            "num_slots_per_sample": 1,
            "carrier_freq_hz": 2_600_000_000.0,
            "subcarrier_spacing": 30_000,
            "measurements": {"ssb_rsrp": False},
        }
    )
    return cfg


def _single_cell_case() -> tuple[dict[str, Any], Any]:
    cfg = _base_company_config()
    cfg.update(
        {
            "num_ues": 8,
            "link": "DL",
            "channel_est_mode": "ls_linear",
            "seed": 202608101,
            "ue_seed": 202608102,
            "ue_speed_kmh": 3.0,
        }
    )
    summary = generate.generate(cfg, num_samples=8, workers=1, collect_ssb=False)
    ds = load(summary["dataset_id"])
    h = ds.h_true
    he = ds.h_est
    paths = ds.paths(index=0)
    middle_rb = h.shape[2] // 2
    case = {
        "purpose": "single-cell full-band 64T4R H_true/H_est contract",
        "dataset_id": ds.dataset_id,
        "dataset_path": str(ds.dir),
        "shape": list(h.shape),
        "dtype": str(h.dtype),
        "finite_true": bool(np.isfinite(h).all()),
        "finite_est": bool(np.isfinite(he).all()),
        "h_true_mean_power": float(np.mean(np.abs(h) ** 2)),
        "h_est_mean_power": float(np.mean(np.abs(he) ** 2)),
        "h_est_equals_h_true": bool(np.array_equal(he, h)),
        "configured_channel_model": summary["channel_model"],
        "effective_channel_model": summary["effective_channel_model"],
        "effective_channel_model_counts": summary["effective_channel_model_counts"],
        "nmse_db": _distribution(_nmse_db(he, h)),
        "rank": _rank_record(h),
        "representative_rb": middle_rb,
        "representative_h_true_64x4": _matrix_record(h[0, 0, middle_rb]),
        "representative_h_est_64x4": _matrix_record(he[0, 0, middle_rb]),
        "cdl_profile": {
            "model": paths.model,
            "num_components": int(paths.num_paths),
            "rays_per_non_specular_component": 20,
            "delays_ns": np.round(paths.delays_s * 1e9, 6).tolist(),
            "powers_db": np.round(paths.powers_db, 6).tolist(),
            "aod_deg": None if paths.aod_rad is None else np.round(
                np.rad2deg(paths.aod_rad), 6
            ).tolist(),
            "aoa_deg": None if paths.aoa_rad is None else np.round(
                np.rad2deg(paths.aoa_rad), 6
            ).tolist(),
        },
        "channel_contract": ds.channel_contract,
        "antenna_model": summary["antenna_model"],
        "ue_panel": summary["ue_panel"],
        "ue_panel_derived": summary["ue_panel_derived"],
        "sinr_db": _distribution(ds.sinr_dB),
        "rs_opportunity": summary["rs_opportunity"],
        "gate1": _gate_record(ds),
    }
    return case, ds


def _multicell_case() -> tuple[dict[str, Any], Any]:
    cfg = _base_company_config()
    cfg.update(
        {
            "num_sites": 2,
            "sectors_per_site": 3,
            "topology_layout": "linear",
            "isd_m": 500.0,
            "num_ues": 6,
            "link": "DL",
            "channel_est_mode": "ls_linear",
            "seed": 202608111,
            "ue_seed": 202608112,
            "store_interferer_channels": True,
            "max_per_ue_intf_cells": 5,
            "apply_interferer_precoding": False,
            "pdsch_load": 1.0,
            "measurements": {"ssb_rsrp": False, "interferer_channels": True},
        }
    )
    summary = generate.generate(cfg, num_samples=6, workers=1, collect_ssb=False)
    ds = load(summary["dataset_id"])
    geo = ds.cell_geometry
    groups = np.asarray(geo["physical_site_group_ids"])
    ds_all = np.asarray(geo["sample_tau_rms_all_ns"], dtype=float)
    sf_all = np.asarray(geo["shadow_fading_all_db"], dtype=float)
    los_all = np.asarray(geo["is_los_all"])

    def within_site_max(a: np.ndarray) -> float:
        diffs = []
        for row, grow in zip(a, groups, strict=True):
            for group in np.unique(grow):
                values = row[grow == group]
                diffs.append(float(np.max(values) - np.min(values)))
        return float(max(diffs, default=0.0))

    cross_site_lsp_diff = []
    for row_ds, row_sf, grow in zip(ds_all, sf_all, groups, strict=True):
        ids = np.unique(grow)
        cross_site_lsp_diff.append(
            [float(row_ds[grow == ids[0]][0] - row_ds[grow == ids[1]][0]),
             float(row_sf[grow == ids[0]][0] - row_sf[grow == ids[1]][0])]
        )

    case = {
        "purpose": "two physical sites / six sectors with link-specific propagation",
        "dataset_id": ds.dataset_id,
        "dataset_path": str(ds.dir),
        "serving_shape": list(ds.h_true.shape),
        "interferer_shape": None if ds.h_interferers is None else list(ds.h_interferers.shape),
        "cells_configured": summary["cells_configured"],
        "cells_actual": summary["cells_actual"],
        "interference_modeled": summary["interference_modeled"],
        "configured_channel_model": summary["channel_model"],
        "effective_channel_model_counts": summary["effective_channel_model_counts"],
        "physical_site_group_ids_first_sample": groups[0].tolist(),
        "first_sample_per_cell": {
            key: np.asarray(value)[0].tolist() for key, value in geo.items()
        },
        "cosited_invariants": {
            "delay_spread_within_site_max_diff_ns": within_site_max(ds_all),
            "shadow_fading_within_site_max_diff_db": within_site_max(sf_all),
            "los_within_site_max_diff": within_site_max(los_all.astype(float)),
            "cross_site_ds_sf_differences": cross_site_lsp_diff,
            "different_site_has_distinct_lsp_each_sample": bool(
                all(abs(d[0]) > 0.0 or abs(d[1]) > 0.0 for d in cross_site_lsp_diff)
            ),
        },
        "sir_db": _distribution(ds.scalar("sir_dB")),
        "sinr_db": _distribution(ds.sinr_dB),
        "snr_db": _distribution(ds.snr_dB),
        "rs_opportunity": summary["rs_opportunity"],
        "gate1": _gate_record(ds),
    }
    return case, ds


def _srs_case() -> tuple[dict[str, Any], Any]:
    base = _base_company_config()
    base.update(
        {
            "num_ues": 1,
            "link": "UL",
            "tdd_pattern": "UUUUU",
            "pilot_type_ul": "srs_zc",
            "srs_hopping_enabled": True,
            "srs_c_srs": 63,
            "srs_b_srs": 1,
            "srs_b_hop": 0,
            "srs_n_rrc": 0,
            "srs_comb": 2,
            "srs_periodicity": 1,
            "srs_group_hopping": False,
            "srs_sequence_hopping": False,
            "ue_speed_kmh": 0.0,
            "seed": 202608121,
            "ue_seed": 202608122,
        }
    )

    cfg_linear = dict(base, channel_est_mode="ls_linear")
    cfg_concat = dict(base, channel_est_mode="ls_hop_concat")
    linear_summary = generate.generate(
        cfg_linear, num_samples=4, workers=1, collect_ssb=False
    )
    concat_summary = generate.generate(
        cfg_concat, num_samples=4, workers=1, collect_ssb=False
    )
    linear = load(linear_summary["dataset_id"])
    concat = load(concat_summary["dataset_id"])

    resource = SRSResourceConfig(
        C_SRS=63,
        B_SRS=1,
        K_TC=2,
        n_RRC=0,
        b_hop=0,
        n_SRS_ID=0,
        T_SRS=1,
        T_offset=0,
    )
    cycle = srs_hopping_cycle_length(resource)
    hops = [srs_rb_indices(resource, slot, 0, 272) for slot in range(cycle)]
    hop_starts = [int(hop[0]) for hop in hops]
    union = np.unique(np.concatenate(hops))

    sequence = srs_sequence(
        n_SRS_ID=0,
        K_TC=2,
        n_cs=0,
        N_ap=1,
        Msc=16 * 12 // 2,
        slot=0,
        symbol=13,
        slots_per_frame=20,
    )
    zc = zadoff_chu(17, 89)
    corr = np.array([np.vdot(zc, np.roll(zc, shift)) for shift in range(len(zc))])

    true_diff = np.max(np.abs(linear.h_true - concat.h_true))
    case = {
        "purpose": "SRS/ZC and 17-hop full-band reconstruction",
        "linear_dataset_id": linear.dataset_id,
        "concat_dataset_id": concat.dataset_id,
        "linear_dataset_path": str(linear.dir),
        "concat_dataset_path": str(concat.dir),
        "shape": list(concat.h_true.shape),
        "same_h_true_for_estimator_comparison": bool(
            np.array_equal(linear.h_true, concat.h_true)
        ),
        "h_true_max_abs_difference": float(true_diff),
        "linear_nmse_db": _distribution(_nmse_db(linear.h_est, linear.h_true)),
        "hop_concat_nmse_db": _distribution(_nmse_db(concat.h_est, concat.h_true)),
        "hopping": {
            "c_srs": 63,
            "b_srs": 1,
            "b_hop": 0,
            "cycle_length": int(cycle),
            "rb_per_hop": [int(len(hop)) for hop in hops],
            "hop_starts": hop_starts,
            "hop_ranges": [[int(hop[0]), int(hop[-1])] for hop in hops],
            "unique_rb_count": int(len(union)),
            "full_band_exact_once": bool(
                len(union) == 272
                and np.array_equal(union, np.arange(272))
                and sum(len(hop) for hop in hops) == 272
            ),
            "acquisition_time_ms": float(cycle * 1 * 0.5),
        },
        "srs_sequence": {
            "length_subcarriers": int(len(sequence)),
            "constant_envelope_max_error": float(np.max(np.abs(np.abs(sequence) - 1.0))),
            "underlying_zc_root": 17,
            "underlying_zc_prime_length": 89,
            "zc_peak": float(abs(corr[0])),
            "zc_max_cyclic_sidelobe": float(np.max(np.abs(corr[1:]))),
        },
        "srs_active_in_slot": concat.scalar("srs_active_in_slot").astype(bool).tolist(),
        "channel_contract": concat.channel_contract,
        "linear_rs_opportunity": linear_summary["rs_opportunity"],
        "concat_rs_opportunity": concat_summary["rs_opportunity"],
        "gate1": _gate_record(concat),
        "known_model_boundary": (
            "current estimator models one representative SRS symbol and one SRS port; "
            "it is not an RE-level multi-port CDM receiver"
        ),
    }
    return case, concat


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "schema": "superran-channel-generation-audit-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
        },
        "repositories": {
            "superran": _git_state(ROOT),
        },
        "source_hashes": {
            "native.py": _sha256(ROOT / "src/superran/native.py"),
            "spec38901.py": _sha256(ROOT / "src/superran/spec38901.py"),
            "physical.py": _sha256(ROOT / "src/superran/physical.py"),
            "generate.py": _sha256(ROOT / "src/superran/generate.py"),
            "measure.py": _sha256(ROOT / "src/superran/measure.py"),
            "validate.py": _sha256(ROOT / "src/superran/validate.py"),
        },
        "standard_references": {
            "38.211": "ETSI TS 138 211 V18.4.0, SRS clauses 6.4.1.4.2-6.4.1.4.4",
            "38.901": "ETSI TR 138 901 V17.1.0, CDL clauses 7.5 and 7.7.1",
        },
    }

    single, _ = _single_cell_case()
    print(f"single-cell: {single['dataset_id']} gate={single['gate1']['passed']}")
    evidence["single_cell"] = single

    multi, _ = _multicell_case()
    print(f"multi-cell: {multi['dataset_id']} gate={multi['gate1']['passed']}")
    evidence["multi_cell"] = multi

    srs, _ = _srs_case()
    print(
        f"SRS: {srs['linear_dataset_id']} vs {srs['concat_dataset_id']} "
        f"gate={srs['gate1']['passed']}"
    )
    evidence["srs"] = srs

    invariants = {
        "single_shape_is_8x1x272x64x4": single["shape"] == [8, 1, 272, 64, 4],
        "single_true_and_est_are_finite": single["finite_true"] and single["finite_est"],
        "single_estimate_is_not_true_copy": not single["h_est_equals_h_true"],
        "single_all_rb_are_rank4": single["rank"]["full_rank_fraction"] == 1.0,
        "single_paths_uses_effective_profile": (
            single["cdl_profile"]["model"] == single["effective_channel_model"]
        ),
        "multicell_has_five_interferer_channels": multi["interferer_shape"] == [6, 5, 1, 272, 64, 4],
        "multicell_interference_enters_sinr": multi["interference_modeled"] is True,
        "cosited_sectors_share_lsp": (
            multi["cosited_invariants"]["delay_spread_within_site_max_diff_ns"] == 0.0
            and multi["cosited_invariants"]["shadow_fading_within_site_max_diff_db"] == 0.0
            and multi["cosited_invariants"]["los_within_site_max_diff"] == 0.0
        ),
        "different_sites_do_not_share_all_lsp": multi["cosited_invariants"][
            "different_site_has_distinct_lsp_each_sample"
        ],
        "srs_estimators_use_identical_h_true": srs["same_h_true_for_estimator_comparison"],
        "srs_17_hops_cover_272_rb_exactly_once": srs["hopping"]["full_band_exact_once"],
        "srs_sequence_has_constant_envelope": (
            srs["srs_sequence"]["constant_envelope_max_error"] < 1e-12
        ),
        "zc_has_zero_cyclic_sidelobes_numerically": (
            srs["srs_sequence"]["zc_max_cyclic_sidelobe"] < 1e-10
        ),
        "all_three_gate1_checks_pass": (
            single["gate1"]["passed"]
            and multi["gate1"]["passed"]
            and srs["gate1"]["passed"]
        ),
    }
    evidence["invariants"] = invariants
    evidence["overall_pass"] = all(invariants.values())

    EVIDENCE_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=_jsonable),
        encoding="utf-8",
    )
    print(f"evidence: {EVIDENCE_PATH}")
    print(f"overall_pass={evidence['overall_pass']}")
    if not evidence["overall_pass"]:
        failed = [name for name, passed in invariants.items() if not passed]
        raise SystemExit("failed invariants: " + ", ".join(failed))


if __name__ == "__main__":
    main()

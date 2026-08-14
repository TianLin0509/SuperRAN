r"""Randomized and analytic stress checks for ChannelHub channel generation.

This is deliberately separate from the three human-readable audit examples:
the examples explain the model, while this script tries to break its contracts
across profiles, seeds, OFDM grids, sites, and SRS bandwidth-tree rows.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CHANNELHUB = ROOT.parent / "MSG-Platform"
sys.path.insert(0, str(CHANNELHUB / "src"))

from msg_embedding.data.sources._interference_estimation import (  # noqa: E402
    estimate_channel_with_interference,
)
from msg_embedding.data.sources.internal_sim import InternalSimSource  # noqa: E402
from msg_embedding.ref_signals.srs import (  # noqa: E402
    SRS_BW_TABLE,
    SRSResourceConfig,
    srs_hopping_cycle_length,
    srs_rb_indices,
    srs_sequence,
)

OUT = ROOT / "artifacts" / "channel-generation-audit" / "stress.json"


def _sample(cfg: dict[str, Any]) -> Any:
    return next(InternalSimSource(cfg).iter_samples())


def _small_cfg(
    *, seed: int, profile: str = "CDL-C", num_symbols: int = 14
) -> dict[str, Any]:
    los = profile.upper().endswith(("-D", "-E"))
    return {
        "num_sites": 1,
        "sectors_per_site": 1,
        "num_ues": 1,
        "num_samples": 1,
        "num_interfering_ues": 0,
        "num_rb": 16,
        "num_ofdm_symbols": num_symbols,
        "num_slots_per_sample": 1,
        "bs_panel": [2, 2, 2],
        "ue_panel": [2, 1, 2],
        "num_bs_tx_ant": 8,
        "num_bs_rx_ant": 8,
        "num_ue_tx_ant": 4,
        "num_ue_rx_ant": 4,
        "scenario": "UMa_LOS" if los else "UMa_NLOS",
        "channel_model": profile,
        "link": "DL",
        "channel_est_mode": "ls_linear",
        "subcarrier_spacing": 30_000,
        "carrier_freq_hz": 2_600_000_000.0,
        "seed": seed,
        "ue_seed": seed + 10_000,
        "measurements": {"ssb_rsrp": False},
    }


def _cdl_seed_stress() -> dict[str, Any]:
    records = []
    failures: list[str] = []
    profiles = ("CDL-A", "CDL-B", "CDL-C", "CDL-D", "CDL-E")
    grids = (1, 4, 14)
    for p_idx, profile in enumerate(profiles):
        for trial in range(5):
            seed = 31_000 + 100 * p_idx + trial
            symbols = grids[(p_idx + trial) % len(grids)]
            cfg = _small_cfg(seed=seed, profile=profile, num_symbols=symbols)
            first = _sample(cfg)
            second = _sample(cfg)
            h = np.asarray(first.h_serving_true)
            he = np.asarray(first.h_serving_est)
            singular = np.linalg.svd(h, compute_uv=False)
            mean_power = float(np.mean(np.abs(h) ** 2))
            effective_profile = str(first.meta.get("effective_channel_model", ""))
            observed_los = bool(first.meta.get("is_los", False))
            profile_state_match = effective_profile.endswith(("-D", "-E")) == observed_los
            exact_repeat = bool(np.array_equal(h, second.h_serving_true))
            rank4 = bool(np.all(singular[..., -1] / singular[..., 0] > 1e-5))
            good = (
                h.shape == (1, 16, 8, 4)
                and he.shape == h.shape
                and np.isfinite(h).all()
                and np.isfinite(he).all()
                # The contract normalizes the full OFDM block before extracting
                # its middle symbol.  A single snapshot need not be exactly 1.
                and abs(mean_power - 1.0) <= 0.005
                and exact_repeat
                and rank4
                and not np.array_equal(h, he)
                and profile_state_match
            )
            if not good:
                failures.append(f"{profile}/seed={seed}/symbols={symbols}")
            records.append(
                {
                    "profile": profile,
                    "seed": seed,
                    "num_ofdm_symbols": symbols,
                    "shape": list(h.shape),
                    "mean_power": mean_power,
                    "effective_profile": effective_profile,
                    "observed_los": observed_los,
                    "profile_state_match": profile_state_match,
                    "finite": bool(np.isfinite(h).all() and np.isfinite(he).all()),
                    "exact_repeat": exact_repeat,
                    "rank4_all_rb": rank4,
                    "estimate_is_not_truth_copy": not np.array_equal(h, he),
                    "symbol_grid_approximate": bool(
                        first.meta.get("symbol_grid_approximate", False)
                    ),
                    "passed": good,
                }
            )
    return {
        "cases": len(records),
        "passed": not failures,
        "failures": failures,
        "power_min": min(r["mean_power"] for r in records),
        "power_max": max(r["mean_power"] for r in records),
        "records": records,
    }


def _tdl_seed_stress() -> dict[str, Any]:
    records = []
    failures: list[str] = []
    profiles = ("TDL-A", "TDL-B", "TDL-C", "TDL-D", "TDL-E")
    grids = (1, 4, 14)
    for p_idx, profile in enumerate(profiles):
        for trial in range(5):
            seed = 31_500 + 100 * p_idx + trial
            symbols = grids[(p_idx + trial) % len(grids)]
            cfg = _small_cfg(seed=seed, profile=profile, num_symbols=symbols)
            first = _sample(cfg)
            second = _sample(cfg)
            h = np.asarray(first.h_serving_true)
            he = np.asarray(first.h_serving_est)
            singular = np.linalg.svd(h, compute_uv=False)
            mean_power = float(np.mean(np.abs(h) ** 2))
            effective_profile = str(first.meta.get("effective_channel_model", ""))
            observed_los = bool(first.meta.get("is_los", False))
            profile_state_match = effective_profile.endswith(("-D", "-E")) == observed_los
            exact_repeat = bool(np.array_equal(h, second.h_serving_true))
            rank4 = bool(np.all(singular[..., -1] / singular[..., 0] > 1e-5))
            good = (
                h.shape == (1, 16, 8, 4)
                and he.shape == h.shape
                and np.isfinite(h).all()
                and np.isfinite(he).all()
                and abs(mean_power - 1.0) <= 0.005
                and exact_repeat
                and rank4
                and not np.array_equal(h, he)
                and profile_state_match
            )
            if not good:
                failures.append(f"{profile}/seed={seed}/symbols={symbols}")
            records.append(
                {
                    "profile": profile,
                    "seed": seed,
                    "num_ofdm_symbols": symbols,
                    "mean_power": mean_power,
                    "effective_profile": effective_profile,
                    "observed_los": observed_los,
                    "profile_state_match": profile_state_match,
                    "finite": bool(np.isfinite(h).all() and np.isfinite(he).all()),
                    "exact_repeat": exact_repeat,
                    "rank4_all_rb": rank4,
                    "estimate_is_not_truth_copy": not np.array_equal(h, he),
                    "passed": good,
                }
            )
    return {
        "cases": len(records),
        "passed": not failures,
        "failures": failures,
        "power_min": min(r["mean_power"] for r in records),
        "power_max": max(r["mean_power"] for r in records),
        "records": records,
    }


def _geometry_grid_stress() -> dict[str, Any]:
    fields = (
        "pathloss_dB",
        "distance_3d_m",
        "is_los",
        "los_probability",
        "rx_power_serving_dbm",
        "doppler_hz",
        "sample_tau_rms_ns",
        "antenna_gain_serving_db",
        "sinr_geometry_db",
        "sir_geometry_db",
    )
    records = []
    failures = []

    def exact_equal(left: Any, right: Any) -> bool:
        a = np.asarray(left)
        b = np.asarray(right)
        if a.shape != b.shape:
            return False
        if a.dtype.kind in "fc" or b.dtype.kind in "fc":
            try:
                return bool(np.array_equal(a, b, equal_nan=True))
            except TypeError:
                return False
        return bool(np.array_equal(a, b))

    for trial in range(5):
        seed = 32_000 + trial
        samples = {
            n: _sample(_small_cfg(seed=seed, profile="CDL-C", num_symbols=n))
            for n in (1, 4, 14)
        }
        mismatches: dict[str, list[Any]] = {}
        for field in fields:
            values = [samples[n].meta.get(field) for n in (1, 4, 14)]
            if not all(exact_equal(values[0], v) for v in values[1:]):
                mismatches[field] = values
        contracts_marked = (
            bool(samples[1].meta.get("symbol_grid_approximate"))
            and bool(samples[4].meta.get("symbol_grid_approximate"))
            and not bool(samples[14].meta.get("symbol_grid_approximate"))
        )
        passed = not mismatches and contracts_marked
        if not passed:
            failures.append(f"seed={seed}: {sorted(mismatches)} marked={contracts_marked}")
        records.append(
            {
                "seed": seed,
                "mismatched_geometry_fields": sorted(mismatches),
                "reduced_grids_marked_approximate": contracts_marked,
                "passed": passed,
            }
        )
    return {"cases": len(records), "passed": not failures,
            "failures": failures, "records": records}


def _multisite_stress() -> dict[str, Any]:
    records = []
    failures = []
    for trial in range(8):
        seed = 33_000 + trial
        cfg = _small_cfg(seed=seed, num_symbols=4)
        cfg.update(
            {
                "num_sites": 2,
                "sectors_per_site": 3,
                "topology_layout": "linear",
                "isd_m": 300.0,
                "num_rb": 8,
                "store_interferer_channels": True,
                "max_per_ue_intf_cells": 5,
                "apply_interferer_precoding": False,
                "pdsch_load": 1.0,
            }
        )
        sample = _sample(cfg)
        groups = np.asarray(sample.meta["physical_site_group_ids"])
        delay = np.asarray(sample.meta["sample_tau_rms_all_ns"], dtype=float)
        shadow = np.asarray(sample.meta["shadow_fading_all_db"], dtype=float)
        los = np.asarray(sample.meta["is_los_all"])
        expected_groups = np.array([0, 0, 0, 1, 1, 1])
        cosited = all(
            np.all(values[:3] == values[0]) and np.all(values[3:] == values[3])
            for values in (delay, shadow, los)
        )
        cross_site = bool(delay[0] != delay[3] or shadow[0] != shadow[3])
        shape = None if sample.h_interferers is None else list(sample.h_interferers.shape)
        passed = (
            np.array_equal(groups, expected_groups)
            and cosited
            and cross_site
            and shape == [5, 1, 8, 8, 4]
        )
        if not passed:
            failures.append(f"seed={seed}")
        records.append(
            {
                "seed": seed,
                "groups": groups.tolist(),
                "delay_ns": delay.tolist(),
                "shadow_db": shadow.tolist(),
                "los": los.astype(bool).tolist(),
                "interferer_shape": shape,
                "cosited_equal": cosited,
                "cross_site_distinct": cross_site,
                "passed": passed,
            }
        )
    return {"cases": len(records), "passed": not failures,
            "failures": failures, "records": records}


def _srs_bandwidth_tree_stress() -> dict[str, Any]:
    failures = []
    tested_resources = 0
    hopping_resources = 0
    max_cycle = 0
    for row in SRS_BW_TABLE:
        c_srs = int(row.c_srs)
        m_values = row.m_srs
        n_values = row.n
        if not all(m_values[b - 1] == m_values[b] * n_values[b] for b in (1, 2, 3)):
            failures.append(f"row {c_srs}: bandwidth tree factorization")
        for b_srs in range(4):
            cfg = SRSResourceConfig(
                C_SRS=c_srs,
                B_SRS=b_srs,
                K_TC=2,
                n_RRC=0,
                b_hop=b_srs,
                n_SRS_ID=19,
                T_SRS=1,
                T_offset=0,
            )
            rb = srs_rb_indices(cfg, 0, 0, m_values[0])
            tested_resources += 1
            if len(rb) != m_values[b_srs] or rb.min() < 0 or rb.max() >= m_values[0]:
                failures.append(f"row {c_srs}/B{b_srs}: no-hop bounds/width")

            if b_srs > 0:
                hop_cfg = SRSResourceConfig(
                    C_SRS=c_srs,
                    B_SRS=b_srs,
                    K_TC=2,
                    n_RRC=0,
                    b_hop=0,
                    n_SRS_ID=19,
                    T_SRS=1,
                    T_offset=0,
                )
                cycle = srs_hopping_cycle_length(hop_cfg)
                hops = [
                    srs_rb_indices(hop_cfg, slot, 0, m_values[0])
                    for slot in range(cycle)
                ]
                union = np.unique(np.concatenate(hops))
                hopping_resources += 1
                max_cycle = max(max_cycle, cycle)
                if len(union) != m_values[0] or any(len(x) != m_values[b_srs] for x in hops):
                    failures.append(f"row {c_srs}/B{b_srs}: hopping union/width")

    return {
        "table_rows": len(SRS_BW_TABLE),
        "resources": tested_resources,
        "hopping_resources": hopping_resources,
        "max_cycle": max_cycle,
        "passed": not failures,
        "failures": failures,
    }


def _srs_sequence_stress() -> dict[str, Any]:
    limits = {2: 8, 4: 12, 8: 6}
    failures = []
    count = 0
    max_envelope_error = 0.0
    for k_tc, n_cs_limit in limits.items():
        for n_ports in (1, 2, 4):
            for port in range(n_ports):
                for n_cs in (0, n_cs_limit - 1):
                    seq = srs_sequence(
                        n_SRS_ID=317,
                        K_TC=k_tc,
                        n_cs=n_cs,
                        N_ap=n_ports,
                        Msc=96,
                        slot=23,
                        symbol=13,
                        n_ap_index=port,
                        group_hopping=True,
                        slots_per_frame=20,
                    )
                    error = float(np.max(np.abs(np.abs(seq) - 1.0)))
                    max_envelope_error = max(max_envelope_error, error)
                    count += 1
                    if not np.isfinite(seq).all() or error >= 1e-12:
                        failures.append(
                            f"KTC={k_tc}/ports={n_ports}/port={port}/ncs={n_cs}"
                        )
    return {"cases": count, "max_envelope_error": max_envelope_error,
            "passed": not failures, "failures": failures}


def _noise_free_ls_counterexample() -> dict[str, Any]:
    rng = np.random.default_rng(34_000)
    h = (
        rng.standard_normal((14, 272, 8, 4))
        + 1j * rng.standard_normal((14, 272, 8, 4))
    ).astype(np.complex64)
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
    mask = np.zeros(14, dtype=bool)
    mask[13] = True
    errors = []
    for slot in (0, 1, 7, 16):
        hop = srs_rb_indices(resource, slot, 0, 272)
        pilot = np.exp(1j * np.linspace(0.0, 1.0, len(hop)))
        result = estimate_channel_with_interference(
            h_serving_true=h,
            h_interferers=None,
            pilots_serving=pilot,
            interferer_cell_ids=None,
            direction="UL",
            snr_dB=300.0,
            rng=np.random.default_rng(34_100 + slot),
            est_mode="ls_linear",
            valid_symbol_mask=mask,
            srs_rb_indices=hop,
            srs_slot=slot,
            srs_symbol=13,
            srs_num_rb=len(hop),
            pilot_symbol=13,
        )
        errors.append(float(np.max(np.abs(result.h_est[13, hop] - h[13, hop]))))
    return {
        "slots": [0, 1, 7, 16],
        "max_abs_errors": errors,
        "passed": max(errors) < 1e-5,
        "meaning": (
            "At effectively infinite SNR, LS must recover every actually piloted RB; "
            "this specifically catches local-pilot versus absolute-RB indexing bugs."
        ),
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema": "superran-channel-property-stress-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cdl_seed_stress": _cdl_seed_stress(),
        "tdl_seed_stress": _tdl_seed_stress(),
        "geometry_grid_stress": _geometry_grid_stress(),
        "multisite_stress": _multisite_stress(),
        "srs_bandwidth_tree_stress": _srs_bandwidth_tree_stress(),
        "srs_sequence_stress": _srs_sequence_stress(),
        "noise_free_ls_counterexample": _noise_free_ls_counterexample(),
    }
    suites = [value for value in evidence.values() if isinstance(value, dict) and "passed" in value]
    evidence["total_cases"] = sum(int(s.get("cases", s.get("resources", 1))) for s in suites)
    evidence["overall_pass"] = all(bool(s["passed"]) for s in suites)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "path": str(OUT),
            "total_cases": evidence["total_cases"],
            "overall_pass": evidence["overall_pass"],
            "suites": {k: v["passed"] for k, v in evidence.items()
                       if isinstance(v, dict) and "passed" in v},
        },
        ensure_ascii=False,
        indent=2,
    ))
    if not evidence["overall_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

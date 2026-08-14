"""Deterministic evidence bundle for the SRS/PDP/robust-weight audit.

This script deliberately exercises the public SuperRAN path and the
physical ChannelHub kernels used underneath it.  It writes one UTF-8 JSON file
that the two HTML reports consume; a failed invariant aborts the run instead of
silently publishing a partial comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANNELHUB = ROOT.parent / "MSG-Platform"
DEFAULT_DEPS = Path(r"C:\VibeData\Caches\channelhub-test-deps-20260810")
DEFAULT_OUTPUT = ROOT / "artifacts" / "srs_pdp_robust_audit_20260810.json"


def _bootstrap(channelhub_root: Path, deps_root: Path) -> None:
    for path in (ROOT / "src", channelhub_root / "src", deps_root):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _frequency_response(
    n_rb: int,
    paths: list[tuple[float, float]],
    subcarrier_spacing_hz: float,
) -> np.ndarray:
    """Return [1,RB,1,1] H(f) for (delay_s, linear_power) paths."""
    frequencies = np.arange(n_rb, dtype=np.float64) * 12.0 * subcarrier_spacing_hz
    response = np.zeros(n_rb, dtype=np.complex128)
    for delay_s, power in paths:
        response += math.sqrt(power) * np.exp(-2j * np.pi * frequencies * delay_s)
    return response[None, :, None, None]


def _wrap_error(value: float, expected: float, period: float) -> float:
    return abs((value - expected + period / 2.0) % period - period / 2.0)


def run(channelhub_root: Path, deps_root: Path, output: Path) -> dict[str, Any]:
    _bootstrap(channelhub_root, deps_root)

    from msg_embedding.channel_est import exponential_pdp_covariance
    from msg_embedding.data.sources._interference_estimation import (
        _srs_port_sequences,
        estimate_channel_with_interference,
    )
    from msg_embedding.phy_sim.effective_array import make_effective_array

    from superran import generate as gen
    from superran import hardware as hw
    from superran import linklevel as ll
    from superran import measure as meas
    from superran import mumimo as mu

    checks: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: Any) -> None:
        row = {"name": name, "passed": bool(condition), "detail": _jsonable(detail)}
        checks.append(row)
        if not condition:
            raise AssertionError(name + ": " + repr(detail))

    # ------------------------------------------------------------------
    # 1. 64 x 4 physical SRS observation and 38.211 port power contract.
    # ------------------------------------------------------------------
    t_symbols, n_rb_srs, n_ports, n_bs_rx = 14, 32, 4, 64
    srs_rng = np.random.default_rng(20260810)
    h_ul = (
        srs_rng.standard_normal((t_symbols, n_rb_srs, n_ports, n_bs_rx))
        + 1j * srs_rng.standard_normal((t_symbols, n_rb_srs, n_ports, n_bs_rx))
    ).astype(np.complex64)
    symbol_mask = np.zeros(t_symbols, dtype=bool)
    symbol_mask[13] = True
    rb_indices = np.arange(n_rb_srs, dtype=np.int64)
    srs_result = estimate_channel_with_interference(
        h_serving_true=h_ul,
        h_interferers=None,
        pilots_serving=np.ones(n_rb_srs, dtype=np.complex128),
        interferer_cell_ids=None,
        direction="UL",
        snr_dB=300.0,
        rng=np.random.default_rng(20260811),
        est_mode="ls_linear",
        valid_symbol_mask=symbol_mask,
        srs_rb_indices=rb_indices,
        srs_slot=4,
        srs_symbol=13,
        srs_num_rb=n_rb_srs,
        srs_K_TC=2,
        pilot_symbol=13,
    )
    srs_error = np.abs(srs_result.h_est[13] - h_ul[13])
    port_sequences = _srs_port_sequences(
        n_rb_srs, 37, n_ports, slot=4, symbol=13, K_TC=2
    )
    total_pilot_power = np.sum(np.abs(port_sequences) ** 2, axis=0)
    check(
        "64x4 SRS is recovered from one physical receive vector",
        float(np.max(srs_error)) < 5e-5,
        {"max_abs_error": float(np.max(srs_error))},
    )
    check(
        "SRS port scaling keeps total UE pilot power fixed",
        bool(np.allclose(total_pilot_power, 1.0, atol=1e-12)),
        {
            "min_sum_port_power": float(np.min(total_pilot_power)),
            "max_sum_port_power": float(np.max(total_pilot_power)),
        },
    )
    srs_physical = {
        "stored_orientation": "H_ul[symbol,RB,UE_port,BS_rx]",
        "matrix_at_one_rb": [n_bs_rx, n_ports],
        "generated_tensor_shape": list(h_ul.shape),
        "estimated_tensor_shape": list(srs_result.h_est.shape),
        "observation_equation": "Y_rx[k]=sum_p H_ul[k,p,:] X_srs[k,p]+I[k]+N[k]",
        "num_ports": n_ports,
        "num_bs_rx": n_bs_rx,
        "srs_comb": 2,
        "srs_tones_per_port": int(port_sequences.shape[1]),
        "max_abs_recovery_error": float(np.max(srs_error)),
        "mean_abs_recovery_error": float(np.mean(srs_error)),
        "total_pilot_power_min": float(np.min(total_pilot_power)),
        "total_pilot_power_max": float(np.max(total_pilot_power)),
    }

    # ------------------------------------------------------------------
    # 2. Four-port physical SRS: linear LS versus matched frequency LMMSE.
    # ------------------------------------------------------------------
    lmmse_rng = np.random.default_rng(100)
    n_rb_lmmse, n_bs_lmmse = 48, 16
    tau_rms = 220e-9
    rb_spacing = 12.0 * 30e3
    covariance = exponential_pdp_covariance(n_rb_lmmse, tau_rms, rb_spacing)
    eigval, eigvec = np.linalg.eigh(covariance)
    factor = eigvec @ np.diag(np.sqrt(np.maximum(eigval, 0.0)))
    z = (
        lmmse_rng.standard_normal((n_rb_lmmse, n_ports * n_bs_lmmse))
        + 1j * lmmse_rng.standard_normal((n_rb_lmmse, n_ports * n_bs_lmmse))
    ) / np.sqrt(2.0)
    h_freq = (factor @ z).reshape(n_rb_lmmse, n_ports, n_bs_lmmse)
    h_lmmse_true = np.broadcast_to(
        h_freq[None], (t_symbols, n_rb_lmmse, n_ports, n_bs_lmmse)
    ).copy().astype(np.complex64)
    pilot_positions = np.array([0, 2, 5, 9, 14, 20, 27, 35, 44, 47], dtype=np.int64)

    def estimate_mse(mode: str) -> float:
        estimate = estimate_channel_with_interference(
            h_serving_true=h_lmmse_true,
            h_interferers=None,
            pilots_serving=np.ones(len(pilot_positions), dtype=np.complex128),
            interferer_cell_ids=None,
            direction="UL",
            snr_dB=0.0,
            rng=np.random.default_rng(77),
            est_mode=mode,
            tau_rms_ns=tau_rms * 1e9,
            subcarrier_spacing=30e3,
            serving_cell_id=0,
            valid_symbol_mask=symbol_mask,
            srs_rb_indices=pilot_positions,
            srs_slot=4,
            srs_symbol=13,
            srs_K_TC=2,
            srs_num_rb=len(pilot_positions),
            pilot_symbol=13,
        )
        return float(np.mean(np.abs(estimate.h_est[13] - h_lmmse_true[13]) ** 2))

    mse_ls = estimate_mse("ls_linear")
    mse_lmmse = estimate_mse("ls_mmse")
    lmmse_reduction = 1.0 - mse_lmmse / mse_ls
    check(
        "matched LMMSE materially beats linear LS on physical four-port SRS",
        mse_lmmse < 0.6 * mse_ls,
        {"mse_ls": mse_ls, "mse_lmmse": mse_lmmse},
    )
    lmmse = {
        "snr_db": 0.0,
        "tau_rms_prior_ns": tau_rms * 1e9,
        "pilot_rb_positions": pilot_positions.tolist(),
        "mse_ls_linear": mse_ls,
        "mse_ls_lmmse": mse_lmmse,
        "mse_reduction_fraction": lmmse_reduction,
        "implemented_dimensions": ["frequency"],
        "time_interpolation": "linear",
        "spatial_covariance": "not used",
    }

    # ------------------------------------------------------------------
    # 3. Hardware F matrix and explicit +/-45 degree Jones basis.
    # ------------------------------------------------------------------
    array_cfg = {
        "bs_panel": list(hw.COMPANY_RF_PANEL),
        "carrier_freq_hz": hw.COMPANY_CARRIER_HZ,
        "bs_antenna": hw.company_antenna_block(),
    }
    array = make_effective_array(array_cfg)
    feed = array.coupling_matrix()
    feed_gram = feed.conj().T @ feed
    nnz_per_column = np.count_nonzero(np.abs(feed) > 1e-12, axis=0)
    support_per_row = np.count_nonzero(np.abs(feed) > 1e-12, axis=1)
    check(
        "F is a 192x64 one-to-three feed with orthonormal columns",
        feed.shape == (192, 64)
        and bool(np.all(nnz_per_column == 3))
        and bool(np.all(support_per_row == 1))
        and float(np.max(np.abs(feed_gram - np.eye(64)))) < 1e-12,
        {"shape": list(feed.shape), "gram_error": float(np.max(np.abs(feed_gram - np.eye(64))))},
    )
    slants = np.deg2rad(np.asarray(hw.COMPANY_POLARIZATION_SLANTS_DEG, dtype=float))
    jones = np.stack((np.cos(slants), np.sin(slants)), axis=1)
    jones_gram = jones @ jones.T
    check(
        "+45 and -45 Jones basis is orthonormal",
        bool(np.allclose(jones_gram, np.eye(2), atol=1e-12)),
        {"jones_gram": jones_gram},
    )
    elevation_deg = np.linspace(-20.0, 10.0, 3001)
    vertical_power = np.asarray([
        abs(array.effective_tx_steering(0.0, math.radians(float(el)), hw.COMPANY_CARRIER_HZ)[0]) ** 2
        for el in elevation_deg
    ])
    main_lobe_el = float(elevation_deg[int(np.argmax(vertical_power))])
    array_contract = {
        "rf_shape": list(array.rf_shape),
        "physical_shape": list(array.physical_shape),
        "feed_matrix_shape": list(feed.shape),
        "nonzeros_per_column_unique": np.unique(nnz_per_column).tolist(),
        "elements_driven_by_multiple_ports": int(np.sum(support_per_row > 1)),
        "max_abs_FhF_minus_I": float(np.max(np.abs(feed_gram - np.eye(64)))),
        "fixed_electrical_downtilt_config_deg": 6.0,
        "single_port_main_lobe_elevation_deg": main_lobe_el,
        "polarization_slants_deg": list(hw.COMPANY_POLARIZATION_SLANTS_DEG),
        "jones_vectors_theta_phi": jones,
        "jones_gram": jones_gram,
        "element_pattern": hw.company_antenna_block()["element_pattern"],
    }

    # ------------------------------------------------------------------
    # 4. Full company end-to-end data contract: DL truth + UL SRS estimate.
    # ------------------------------------------------------------------
    e2e_cfg = {
        **hw.company_carrier_defaults(),
        "source": "internal_sim",
        "scenario": "UMa_NLOS",
        "channel_model": "CDL-C",
        "num_sites": 1,
        "sectors_per_site": 1,
        "num_ues": 1,
        "num_ofdm_symbols": 14,
        "num_slots_per_sample": 1,
        "seed": 2711,
        "ue_seed": 2712,
        "channel_est_mode": "ls_mmse",
        "lmmse_prior_mode": "configured",
        "tdd_pattern": "DDDSU",
        "srs_periodicity": 10,
        "csirs_periodicity": 10,
        "measurements": {"ssb_rsrp": False},
    }
    e2e_summary = gen.generate(
        e2e_cfg, num_samples=1, workers=1, collect_ssb=False,
        plan_markdown="Deterministic SRS/PDP/robust-weight audit smoke sample.",
    )
    contract = e2e_summary["channel_contract"]
    sample_meta = e2e_summary["sample_meta"]
    antenna_model = e2e_summary["antenna_model"]
    element_pattern = {
        "source": antenna_model["element_pattern_source"],
        "horizontal_hpbw_deg": antenna_model["element_horizontal_hpbw_deg"],
        "vertical_hpbw_deg": antenna_model["element_vertical_hpbw_deg"],
        "is_measured": antenna_model["element_pattern_is_measured"],
    }
    check(
        "company data stores DL truth but uses UL SRS estimate for gNB precoding",
        contract["h_true_role"] == "downlink physical evaluation channel"
        and contract["h_est_role"].startswith("gNB precoding CSI")
        and contract["precoding_csi_sources"] == ["ul_srs_estimate"],
        contract,
    )
    check(
        "paired RS schedule resolves real DL and UL opportunities",
        bool(e2e_summary["rs_opportunity"]["slot_accurate"])
        and sample_meta["paired_dl_rs_slot"] != sample_meta["paired_ul_srs_slot"]
        and sample_meta["srs_offset_source"] == "auto_first_full_ul_slot",
        {
            "paired_dl_rs_slot": sample_meta["paired_dl_rs_slot"],
            "paired_ul_srs_slot": sample_meta["paired_ul_srs_slot"],
            "srs_offset": sample_meta["srs_offset"],
        },
    )
    check(
        "company defaults expose 64x4 paired channel and 110 degree temporary HPBW",
        e2e_summary["shape"]["BS_ant"] == 64
        and e2e_summary["shape"]["UE_ant"] == 4
        and element_pattern["horizontal_hpbw_deg"] == 110.0
        and antenna_model["polarization_slant_angles_deg"] == [45.0, -45.0]
        and antenna_model["fixed_downtilt_deg"] == 6.0
        and antenna_model["calibration_id"]
        == "company-64T-1to3-192ae-pol-h-v-top-down-v2-dt6deg",
        {"shape": e2e_summary["shape"], "element_pattern": element_pattern},
    )
    e2e = {
        "dataset_id": e2e_summary["dataset_id"],
        "shape": e2e_summary["shape"],
        "elapsed_s": e2e_summary["elapsed_s"],
        "channel_contract": contract,
        "rs_opportunity": e2e_summary["rs_opportunity"],
        "paired_dl_rs_slot": sample_meta["paired_dl_rs_slot"],
        "paired_ul_srs_slot": sample_meta["paired_ul_srs_slot"],
        "paired_rs_slot_gap": sample_meta["paired_rs_slot_gap"],
        "srs_periodicity": sample_meta["srs_periodicity"],
        "srs_offset": sample_meta["srs_offset"],
        "srs_offset_source": sample_meta["srs_offset_source"],
        "srs_first_ul_opportunity_slot": sample_meta[
            "srs_first_ul_opportunity_slot"
        ],
        "polarization_slants_deg": antenna_model["polarization_slant_angles_deg"],
        "element_pattern": element_pattern,
        "antenna_model": antenna_model,
    }

    # ------------------------------------------------------------------
    # 5. PDP analytic sentinels, including periodic-axis and power checks.
    # ------------------------------------------------------------------
    n_rb_pdp = 272
    scs = 30_000.0
    single_delays_ns = [0.0, 13.0, 100.0, 500.0, 2000.0]
    single_rows: list[dict[str, Any]] = []
    for delay_ns in single_delays_ns:
        pdp = meas.power_delay_profile(
            _frequency_response(n_rb_pdp, [(delay_ns * 1e-9, 1.0)], scs),
            subcarrier_spacing_hz=scs,
        )
        mean_error = _wrap_error(
            pdp.mean_delay_s, delay_ns * 1e-9, pdp.unambiguous_period_s
        )
        single_rows.append({
            "input_delay_ns": delay_ns,
            "mean_delay_ns": pdp.mean_delay_s * 1e9,
            "wrapped_mean_error_ns": mean_error * 1e9,
            "rms_delay_spread_ns": pdp.rms_delay_spread_s * 1e9,
            "power_conservation_ratio": pdp.power_conservation_ratio,
        })
    max_single_rms_ns = max(row["rms_delay_spread_ns"] for row in single_rows)
    max_single_mean_error_ns = max(row["wrapped_mean_error_ns"] for row in single_rows)
    check(
        "single-path PDP does not manufacture delay spread or negative long delay",
        max_single_rms_ns < 1e-3 and max_single_mean_error_ns < 0.05,
        {"rows": single_rows},
    )
    two_path = meas.power_delay_profile(
        _frequency_response(n_rb_pdp, [(0.0, 0.8), (500e-9, 0.2)], scs),
        subcarrier_spacing_hz=scs,
    )
    check(
        "two-path PDP matches analytic 100 ns mean and 200 ns RMS",
        abs(two_path.mean_delay_s - 100e-9) < 0.2e-9
        and abs(two_path.rms_delay_spread_s - 200e-9) < 0.2e-9,
        {
            "mean_delay_ns": two_path.mean_delay_s * 1e9,
            "rms_delay_spread_ns": two_path.rms_delay_spread_s * 1e9,
        },
    )
    check(
        "PDP preserves frequency-domain power after windowing",
        max(abs(row["power_conservation_ratio"] - 1.0) for row in single_rows) < 1e-12
        and abs(two_path.power_conservation_ratio - 1.0) < 1e-12,
        {"two_path_power_ratio": two_path.power_conservation_ratio},
    )
    pdp_evidence = {
        "n_rb": n_rb_pdp,
        "subcarrier_spacing_hz": scs,
        "rb_frequency_spacing_hz": 12.0 * scs,
        "delay_resolution_ns": two_path.delay_resolution_s * 1e9,
        "unambiguous_period_ns": two_path.unambiguous_period_s * 1e9,
        "window": two_path.window,
        "window_variance_correction_ns2": two_path.window_variance_correction_s2 * 1e18,
        "single_paths": single_rows,
        "two_path_80_20_0_500ns": {
            "mean_delay_ns": two_path.mean_delay_s * 1e9,
            "rms_delay_spread_ns": two_path.rms_delay_spread_s * 1e9,
            "power_conservation_ratio": two_path.power_conservation_ratio,
        },
    }

    # ------------------------------------------------------------------
    # 6. EBF/PEBF/NEBF and robust RZF are two independent design axes.
    # ------------------------------------------------------------------
    su_rng = np.random.default_rng(0)
    h_su = (
        su_rng.standard_normal((1, 17, 64, 1))
        + 1j * su_rng.standard_normal((1, 17, 64, 1))
    ) / np.sqrt(2.0)
    su_results = {
        mode: ll.link_performance(
            h_su, noise_power=0.1, method="svd", max_rank=1,
            power_constraint=mode,
        )
        for mode in ("ebf", "pebf", "nebf")
    }
    su_power = {
        mode: {
            "spectral_efficiency": result.spectral_efficiency,
            "power_diagnostics": result.power_diagnostics,
        }
        for mode, result in su_results.items()
    }
    check(
        "SU NEBF is close to EBF and materially above PEBF",
        abs(
            su_results["nebf"].spectral_efficiency
            / su_results["ebf"].spectral_efficiency - 1.0
        ) < 0.05
        and su_results["nebf"].spectral_efficiency
        > su_results["pebf"].spectral_efficiency + 1.5,
        su_power,
    )

    mu_rng = np.random.default_rng(0)
    h_shape = (1, 4, 4, 1)
    h_mu0 = (
        mu_rng.standard_normal(h_shape) + 1j * mu_rng.standard_normal(h_shape)
    ) / np.sqrt(2.0)
    h_mu1 = h_mu0 + 0.001 * (
        mu_rng.standard_normal(h_shape) + 1j * mu_rng.standard_normal(h_shape)
    ) / np.sqrt(2.0)
    mu_pebf = mu.mu_link_performance(
        [h_mu0, h_mu1], noise_power=1e-8, streams_per_user=1,
        criterion="all", precoder="zf", power_constraint="pebf",
    )
    mu_nebf = mu.mu_link_performance(
        [h_mu0, h_mu1], noise_power=1e-8, streams_per_user=1,
        criterion="all", precoder="zf", power_constraint="nebf",
    )
    check(
        "strongly correlated MU has a deterministic NEBF below PEBF counterexample",
        mu_nebf.sum_se < mu_pebf.sum_se
        and mu_nebf.leakage_ratio > 0.4
        and mu_pebf.leakage_ratio < 1e-10,
        {"pebf": mu_pebf.as_dict(), "nebf": mu_nebf.as_dict()},
    )

    robust_rng = np.random.default_rng(65)
    h_true_robust = (
        robust_rng.standard_normal((4, 1, 4, 8))
        + 1j * robust_rng.standard_normal((4, 1, 4, 8))
    ) / np.sqrt(2.0)
    error_std = 0.1
    h_est_robust = h_true_robust + error_std * (
        robust_rng.standard_normal(h_true_robust.shape)
        + 1j * robust_rng.standard_normal(h_true_robust.shape)
    ) / np.sqrt(2.0)
    rzf_noise_only = mu.mu_link_performance_from_effective(
        h_true_robust, h_est_robust, noise_power=0.01, precoder="rzf"
    )
    rzf_robust = mu.mu_link_performance_from_effective(
        h_true_robust, h_est_robust, noise_power=0.01, precoder="rzf",
        csi_error_variance=error_std ** 2,
    )
    w_default, p_default = mu.mu_precoder(
        h_est_robust, method="rzf", noise_power=0.01
    )
    w_zero, p_zero = mu.mu_precoder(
        h_est_robust, method="rzf", noise_power=0.01, csi_error_variance=0.0
    )
    check(
        "zero CSI-error robust RZF is bitwise backward compatible",
        bool(np.array_equal(w_default, w_zero) and np.array_equal(p_default, p_zero)),
        {"weights_equal": np.array_equal(w_default, w_zero)},
    )
    check(
        "robust RZF improves the fixed imperfect-CSI counterexample",
        rzf_robust.sum_se > rzf_noise_only.sum_se + 0.2,
        {
            "noise_only_sum_se": rzf_noise_only.sum_se,
            "robust_sum_se": rzf_robust.sum_se,
            "regularization": rzf_robust.rzf_regularization,
        },
    )
    power_and_robust = {
        "matrix_convention": "Q[frequency,antenna,stream]; per-antenna power is row-norm squared",
        "su_64t": su_power,
        "correlated_mu_counterexample": {
            "pebf": mu_pebf.as_dict(),
            "nebf": mu_nebf.as_dict(),
        },
        "robust_rzf_counterexample": {
            "error_std": error_std,
            "csi_error_variance": error_std ** 2,
            "noise_only": rzf_noise_only.as_dict(),
            "robust": rzf_robust.as_dict(),
            "sum_se_gain": rzf_robust.sum_se - rzf_noise_only.sum_se,
            "zero_variance_bitwise_compatible": bool(
                np.array_equal(w_default, w_zero) and np.array_equal(p_default, p_zero)
            ),
        },
        "axes_note": (
            "EBF/PEBF/NEBF enforce transmitter power geometry; robust RZF changes "
            "Gram-matrix loading for CSI uncertainty. They are independent choices."
        ),
    }

    critical_files = [
        ROOT / "src" / "superran" / "hardware.py",
        ROOT / "src" / "superran" / "generate.py",
        ROOT / "src" / "superran" / "measure.py",
        ROOT / "src" / "superran" / "mumimo.py",
        ROOT / "src" / "superran" / "system.py",
        channelhub_root / "src" / "msg_embedding" / "data" / "sources"
        / "_interference_estimation.py",
        channelhub_root / "src" / "msg_embedding" / "data" / "sources"
        / "internal_sim.py",
        channelhub_root / "src" / "msg_embedding" / "phy_sim" / "effective_array.py",
    ]
    payload = {
        "audit": {
            "title": "SRS, PDP and robust-weight deterministic audit",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "superran_root": ROOT,
            "channelhub_root": channelhub_root,
            "python": sys.version,
            "numpy": np.__version__,
            "all_checks_passed": all(row["passed"] for row in checks),
            "check_count": len(checks),
            "critical_file_sha256": {
                str(path): _sha256(path) for path in critical_files
            },
        },
        "checks": checks,
        "srs_physical_64x4": srs_physical,
        "srs_lmmse": lmmse,
        "array_and_polarization": array_contract,
        "company_e2e": e2e,
        "pdp": pdp_evidence,
        "power_and_robust_weights": power_and_robust,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channelhub-root", type=Path, default=DEFAULT_CHANNELHUB)
    parser.add_argument("--deps-root", type=Path, default=DEFAULT_DEPS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run(
        args.channelhub_root.resolve(), args.deps_root.resolve(), args.output.resolve()
    )
    print(json.dumps({
        "output": str(args.output.resolve()),
        "all_checks_passed": result["audit"]["all_checks_passed"],
        "check_count": result["audit"]["check_count"],
        "dataset_id": result["company_e2e"]["dataset_id"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

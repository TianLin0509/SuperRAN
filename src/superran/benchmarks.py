"""可审计的经典通信仿真基准。

这些 case 只回答“实现是否满足经典解析关系、标准合同或预注册统计判据”，
不回答现场性能，也不产生算法收益宣传数字。判据先冻结在
``presets/classic_benchmarks.json``，runner 不允许运行未登记 case。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import j0

from . import bler_curves as bc
from . import calibration as cal
from . import channelhub as ch
from . import csi_aging as ca
from . import experience as ex
from . import gates, provenance
from . import generate as gen
from . import linkadapt as la
from . import linklevel as ll
from . import mumimo as mu
from . import physical as ph
from . import rng as rg
from . import system as sy
from .loader import load

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC = _ROOT / "presets" / "classic_benchmarks.json"
DEFAULT_OUTPUT = _ROOT / "artifacts" / "results" / "classic_comm_benchmarks.json"
SUITE_VERSION = "superran-classic-comm-v1"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _provenance(spec_path: Path) -> dict[str, Any]:
    out = provenance.snapshot(source="classic_benchmarks")
    out.update({
        "spec_path": str(spec_path.resolve()),
        "spec_sha256": _sha256_file(spec_path),
        "git_capture_complete": bool(
            out.get("git_commit") and out.get("git_branch") is not None
            and out.get("git_diff_sha256") and out.get("source_tree_sha256")),
    })
    return out


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _case(
    case_id: str,
    title: str,
    checks: list[dict[str, Any]],
    metrics: dict[str, Any],
    *,
    gate: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "title": title,
        "status": "pass" if checks and all(x["passed"] for x in checks) else "fail",
        "checks": checks,
        "metrics": metrics,
        "gate": gate,
        "notes": list(notes or []),
    }


def _b01(seed: int) -> dict[str, Any]:
    del seed
    snr_db = np.asarray([-10.0, -5.0, 0.0, 5.0, 10.0, 20.0])
    h = np.ones((1, 8, 1, 1), dtype=np.complex64)
    measured = np.asarray([
        ll.link_performance(h, snr_db=float(s), method="svd", max_rank=1)
        .spectral_efficiency
        for s in snr_db
    ])
    expected = np.log2(1.0 + np.power(10.0, snr_db / 10.0))
    max_err = float(np.max(np.abs(measured - expected)))
    checks = [
        _check("SISO 容量等于 Shannon", max_err <= 1e-5, f"max error={max_err:.3e}"),
        _check("容量随 SNR 严格上升", bool(np.all(np.diff(measured) > 0)),
               f"values={np.round(measured, 6).tolist()}"),
    ]
    return _case("B01_awgn_shannon_siso", "AWGN SISO equals Shannon capacity", checks, {
        "snr_db": snr_db.tolist(), "measured": measured.tolist(),
        "expected": expected.tolist(), "max_abs_error_bit_per_s_hz": max_err,
    })


def _b02(seed: int) -> dict[str, Any]:
    random = np.random.default_rng(seed + 2)
    rows: list[dict[str, float]] = []
    for _ in range(24):
        h = ((random.standard_normal((1, 17, 8, 2))
              + 1j * random.standard_normal((1, 17, 8, 2))) / np.sqrt(2))
        svd = ll.link_performance(h, snr_db=10.0, method="svd", max_rank=2)
        type1 = ll.link_performance(
            h, snr_db=10.0, method="type1", max_rank=2,
            n_h=2, n_v=2, port_order="pol_h_v",
            vertical_index_order="top_to_bottom")
        rows.append({"svd": svd.spectral_efficiency,
                     "type1": type1.spectral_efficiency,
                     "bound": svd.capacity_bound})
    bound_bad = sum(r["svd"] > r["bound"] + 1e-6 for r in rows)
    type1_bad = sum(r["type1"] > r["svd"] + 1e-6 for r in rows)
    checks = [
        _check("SVD 不越容量上界", bound_bad == 0, f"violations={bound_bad}/24"),
        _check("Type-I-style 不超过完美 CSI SVD", type1_bad == 0,
               f"violations={type1_bad}/24"),
    ]
    return _case("B02_mimo_svd_upper_bound",
                 "Per-RB SVD is bounded by capacity and dominates the Type-I-style subset",
                 checks, {
                     "n": 24,
                     "mean_svd": float(np.mean([r["svd"] for r in rows])),
                     "mean_type1": float(np.mean([r["type1"] for r in rows])),
                     "mean_capacity_bound": float(np.mean([r["bound"] for r in rows])),
                     "bound_violation_count": bound_bad,
                     "type1_over_svd_violation_count": type1_bad,
                 })


def _matched_exponential_channel(random: np.random.Generator, *, n_rb: int,
                                 tau_rms_s: float, scs_hz: float) -> np.ndarray:
    delays = np.arange(24, dtype=float) * 40e-9
    powers = np.exp(-delays / tau_rms_s)
    powers /= powers.sum()
    taps = ((random.standard_normal(delays.size) + 1j * random.standard_normal(delays.size))
            * np.sqrt(powers / 2.0))
    frequencies = np.arange(n_rb, dtype=float) * 12.0 * scs_hz
    h_f = np.exp(-2j * np.pi * frequencies[:, None] * delays[None, :]) @ taps
    return h_f[None, :, None, None].astype(np.complex64)


def _b03(seed: int) -> dict[str, Any]:
    random = np.random.default_rng(seed + 3)
    tau = 300e-9
    ls_nmse: list[float] = []
    mmse_nmse: list[float] = []
    for i in range(16):
        h = _matched_exponential_channel(random, n_rb=64, tau_rms_s=tau, scs_hz=30e3)
        common = dict(snr_db=0.0, pilot_spacing=4, seed=seed + 3000 + i,
                      tau_rms_s=tau, scs_hz=30e3)
        ls_nmse.append(float(ph.estimate_channel(h, method="ls", **common)["nmse_db"]))
        mmse_nmse.append(float(ph.estimate_channel(h, method="mmse", **common)["nmse_db"]))
    paired = gates.paired_compare(mmse_nmse, ls_nmse)
    gate = gates.gate_conclusion(paired, expected_direction="negative")
    checks = [
        _check("门 3 通过", gate.passed, gate.text()),
        _check("LMMSE-LS 的 CI 全在零下", paired.ci_high < 0,
               f"CI=[{paired.ci_low:.3f},{paired.ci_high:.3f}] dB"),
    ]
    return _case("B03_lmmse_low_snr",
                 "Matched-prior LMMSE channel estimation suppresses low-SNR noise",
                 checks, {
                     "n": 16, "ls_nmse_db": ls_nmse, "lmmse_nmse_db": mmse_nmse,
                     "paired": paired.as_dict(),
                 }, gate=gate.as_dict(),
                 notes=["比较只对预注册的匹配指数 PDP、0 dB SNR 工况成立。"])


def _b04(seed: int) -> dict[str, Any]:
    target = 0.1
    up = 0.01
    down = sy.olla_step_down_for(target, up)
    base_sinr_db = 7.0
    p_by_mcs = np.asarray([
        float(np.atleast_1d(bc.get_curve(m, "newtx").evaluate(base_sinr_db))[0])
        for m in range(28)
    ])
    values: list[float] = []
    steps, warmup = 60_000, 10_000
    for rep in range(16):
        random = np.random.default_rng(np.random.SeedSequence([seed, 4, rep]))
        offset = 0.0
        n_nack = 0
        n_measured = 0
        for tti in range(steps):
            mcs = la.select_mcs(base_sinr_db + offset, table=3, target_bler=target).index
            nack = bool(random.random() < p_by_mcs[mcs])
            offset = max(-12.0, min(12.0, offset - down if nack else offset + up))
            if tti >= warmup:
                n_nack += int(nack)
                n_measured += 1
        values.append(n_nack / n_measured)
    stat = rg.summarize(values, "steady_state_first_tx_bler")
    lo, hi = stat.ci95
    checks = [
        _check("目标落在 replication 95% CI 内", lo <= target <= hi,
               f"target={target:.3f}, CI=[{lo:.4f},{hi:.4f}]"),
        _check("均值绝对误差不超过 0.02", abs(stat.mean - target) <= 0.02,
               f"mean={stat.mean:.4f}, abs_error={abs(stat.mean-target):.4f}"),
    ]
    return _case("B04_olla_target_bler", "OLLA converges to the configured target BLER",
                 checks, {
                     "target_bler": target, "step_up_db": up, "step_down_db": down,
                     "base_sinr_db": base_sinr_db, "steps": steps, "warmup": warmup,
                     "replication_values": values, "summary": stat.as_dict(),
                 })


def _pf_tables(seed: int) -> list[sy.UeLinkTable]:
    random = np.random.default_rng(seed + 5)
    channels: list[np.ndarray] = []
    geo: list[float] = []
    for u in range(8):
        h = ((random.standard_normal((8, 17, 16, 2))
              + 1j * random.standard_normal((8, 17, 16, 2))) / np.sqrt(2))
        h *= 10.0 ** (-u * 1.5 / 20.0)
        channels.append(h.astype(np.complex64))
        geo.append(24.0 - 2.5 * u)
    return sy.build_link_tables(
        channels, geo, num_ues=8, max_rank=2, rb_per_rbg=1,
        csi=ca.CsiConfig(enabled=False), target_bler=0.1)


def _jain(run: sy.SystemResult) -> float:
    values = np.asarray([float(row["served_mbps"]) for row in run.users])
    den = values.size * float(np.sum(values ** 2))
    return float(np.sum(values) ** 2 / den) if den > 0 else 0.0


def _b05(seed: int) -> dict[str, Any]:
    tables = _pf_tables(seed)
    cfg = sy.SystemConfig(
        evaluation_mode="capacity", duration_s=0.5, tdd_pattern="DDDD",
        num_rbg=17, rb_per_rbg=1, scs_khz=30)
    traffic = sy.TrafficConfig(model="full_buffer")
    kpi = sy.KpiConfig(trim="none", warmup_s=0.0)
    results: dict[str, sy.ReplicationResult] = {}
    for algorithm in ("pf", "max_ci", "rr"):
        results[algorithm] = sy.simulate_replications(
            tables, num_replications=8, master_seed=seed + 500,
            sys_cfg=cfg, traffic=traffic,
            sched=sy.SchedulerConfig(
                algorithm=algorithm, mu_enabled=False, olla_enabled=False),
            kpi=kpi)
    maxci_thp = [float(x.cell["cell_served_mbps"]) for x in results["max_ci"].runs]
    pf_thp = [float(x.cell["cell_served_mbps"]) for x in results["pf"].runs]
    pf_jain = [_jain(x) for x in results["pf"].runs]
    maxci_jain = [_jain(x) for x in results["max_ci"].runs]
    thp_cmp = rg.compare_replications(
        maxci_thp, pf_thp, metric="cell_served_mbps", unit="Mbps",
        arm_a="max-C/I", arm_b="PF",
        books_a=results["max_ci"].books, books_b=results["pf"].books)
    fair_cmp = rg.compare_replications(
        pf_jain, maxci_jain, metric="Jain fairness", unit="ratio",
        arm_a="PF", arm_b="max-C/I",
        books_a=results["pf"].books, books_b=results["max_ci"].books)
    checks = [
        _check("max-C/I 吞吐比较过 CRN 门 3",
               thp_cmp["verdict"] == "significant"
               and thp_cmp["ci95_of_effect"][0] > 0,
               thp_cmp["verdict_text"]),
        _check("PF 公平度比较过 CRN 门 3",
               fair_cmp["verdict"] == "significant"
               and fair_cmp["ci95_of_effect"][0] > 0,
               fair_cmp["verdict_text"]),
    ]
    return _case("B05_pf_throughput_fairness",
                 "PF occupies the throughput-fairness middle ground", checks, {
                     "maxci_vs_pf_throughput": thp_cmp,
                     "pf_vs_maxci_jain": fair_cmp,
                     "rr_jain": [_jain(x) for x in results["rr"].runs],
                 })


def _b06(seed: int) -> dict[str, Any]:
    del seed
    random_su = np.random.default_rng(0)
    h_su = ((random_su.standard_normal((1, 17, 64, 1))
             + 1j * random_su.standard_normal((1, 17, 64, 1))) / np.sqrt(2))
    su = {
        mode: ll.link_performance(
            h_su, noise_power=0.1, method="svd", max_rank=1,
            power_constraint=mode)
        for mode in ("ebf", "pebf", "nebf")
    }
    random_mu = np.random.default_rng(0)
    shape = (1, 4, 4, 1)
    h0 = ((random_mu.standard_normal(shape) + 1j * random_mu.standard_normal(shape))
          / np.sqrt(2))
    h1 = h0 + 0.001 * (
        random_mu.standard_normal(shape) + 1j * random_mu.standard_normal(shape)) / np.sqrt(2)
    pebf = mu.mu_link_performance(
        [h0, h1], noise_power=1e-8, streams_per_user=1,
        criterion="all", precoder="zf", power_constraint="pebf")
    nebf = mu.mu_link_performance(
        [h0, h1], noise_power=1e-8, streams_per_user=1,
        criterion="all", precoder="zf", power_constraint="nebf")
    ratio = su["nebf"].spectral_efficiency / su["ebf"].spectral_efficiency
    checks = [
        _check("SU NEBF≈EBF", abs(ratio - 1.0) < 0.05, f"ratio={ratio:.4f}"),
        _check("SU NEBF>PEBF",
               su["nebf"].spectral_efficiency > su["pebf"].spectral_efficiency + 1.5,
               f"NEBF={su['nebf'].spectral_efficiency:.3f}, "
               f"PEBF={su['pebf'].spectral_efficiency:.3f}"),
        _check("相关 MU 中 NEBF 破坏零陷",
               nebf.leakage_ratio > 0.4 and pebf.leakage_ratio < 1e-10,
               f"leakage NEBF={nebf.leakage_ratio:.3g}, PEBF={pebf.leakage_ratio:.3g}"),
        _check("相关 MU 存在 NEBF<PEBF",
               nebf.sum_se < pebf.sum_se,
               f"sum-SE NEBF={nebf.sum_se:.3f}, PEBF={pebf.sum_se:.3f}"),
    ]
    return _case("B06_per_antenna_power_constraints",
                 "EBF/PEBF/NEBF reproduce the expected SU and correlated-MU counterexample",
                 checks, {
                     "su": {k: v.as_dict() for k, v in su.items()},
                     "mu_nebf": nebf.as_dict(), "mu_pebf": pebf.as_dict(),
                 })


def _b07(seed: int) -> dict[str, Any]:
    random = np.random.default_rng(seed + 7)
    h_dl = ((random.standard_normal((2, 8, 8, 4))
             + 1j * random.standard_normal((2, 8, 8, 4))) / np.sqrt(2))
    noise = ((random.standard_normal(h_dl.shape) + 1j * random.standard_normal(h_dl.shape))
             / np.sqrt(2)) * 0.02
    h_ul_est = h_dl + noise
    mapped = ch.ul_estimate_to_dl_precoding_csi(h_ul_est)
    ideal = ll.link_performance(h_dl, snr_db=20.0, method="svd")
    correct = ll.link_performance(
        h_dl, snr_db=20.0, method="svd", h_for_precoding=mapped)
    wrong = ll.link_performance(
        h_dl, snr_db=20.0, method="svd", h_for_precoding=np.conj(h_ul_est))
    checks = [
        _check("正确互易映射优于漏共轭",
               correct.spectral_efficiency > wrong.spectral_efficiency,
               f"correct={correct.spectral_efficiency:.3f}, wrong={wrong.spectral_efficiency:.3f}"),
        _check("正确映射达到理想 CSI 的 90%",
               correct.spectral_efficiency >= 0.9 * ideal.spectral_efficiency,
               f"correct/ideal={correct.spectral_efficiency/ideal.spectral_efficiency:.3f}"),
    ]
    return _case("B07_tdd_srs_reciprocity",
                 "Transpose-only UL-SRS reciprocity preserves the canonical DL convention",
                 checks, {
                     "ideal_se": ideal.spectral_efficiency,
                     "correct_mapping_se": correct.spectral_efficiency,
                     "wrong_mapping_se": wrong.spectral_efficiency,
                 })


def _b08(seed: int) -> dict[str, Any]:
    del seed
    delays = np.asarray([0.0, 0.5, 1.0, 2.0, 4.0, 8.0])
    speed = 30.0
    carrier = 2.6e9
    fd = speed / 3.6 * carrier / 299_792_458.0
    measured = np.asarray([ca.jakes_correlation(float(x), speed, carrier) for x in delays])
    expected = np.abs(j0(2.0 * np.pi * fd * delays / 1000.0))
    max_err = float(np.max(np.abs(measured - expected)))
    ratio = ca.coherence_time_ms(3.0, carrier) / ca.coherence_time_ms(30.0, carrier)
    checks = [
        _check("Jakes 相关的单位与 J0 一致", max_err <= 1e-12,
               f"max error={max_err:.3e}"),
        _check("相干时间近似反比于速度", 8.0 <= ratio <= 12.0,
               f"T3/T30={ratio:.3f}"),
    ]
    return _case("B08_jakes_doppler_time_scale",
                 "Jakes correlation and coherence time use the correct Doppler time scale",
                 checks, {
                     "delay_ms": delays.tolist(), "measured_rho": measured.tolist(),
                     "expected_abs_j0": expected.tolist(), "max_j0_error": max_err,
                     "coherence_ratio_3_to_30_kmh": ratio,
                 })


def _b09(seed: int) -> dict[str, Any]:
    del seed
    lookup = ex.TbsLookup.build(17, 16, 0.7)
    diff = np.diff(lookup.values, axis=-1)
    strict = bool(np.all(diff > 0))
    one = lookup.tbs_bytes("D", 12, 2, 1)
    full = lookup.tbs_bytes("D", 12, 2, 17)
    nonlinearity = full / (17.0 * one) - 1.0
    checks = [
        _check("224 条 TBS 序列严格递增", strict,
               f"minimum increment={int(np.min(diff))} B"),
        _check("冻结点证明 TBS 不可按一个 RBG 线性外推",
               0.005 <= nonlinearity <= 0.02,
               f"one={one} B, full={full} B, delta={nonlinearity:.3%}"),
    ]
    return _case("B09_nr_tbs_rbg_monotonicity",
                 "38.214 TBS allocation is monotone but not safely linearized",
                 checks, {
                     "sequence_count": int(np.prod(lookup.values.shape[:-1])),
                     "all_strictly_increasing": strict,
                     "mcs12_rank2_one_rbg_bytes": one,
                     "mcs12_rank2_17_rbg_bytes": full,
                     "nonlinearity_fraction": nonlinearity,
                 })


def _b10(seed: int) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "source": "internal_sim", "scenario": "UMa_NLOS", "channel_model": "CDL-C",
        "carrier_freq_hz": 3.5e9, "bandwidth_hz": 20e6,
        "subcarrier_spacing": 30_000.0, "num_rb": 51,
        "num_sites": 1, "sectors_per_site": 1, "isd_m": 500.0,
        "num_ues": 8, "num_bs_tx_ant": 4, "num_bs_rx_ant": 4,
        "num_ue_rx_ant": 2, "num_ue_tx_ant": 2,
        "link": "DL", "channel_est_mode": "ls_linear",
        "mobility_mode": "static", "ue_speed_kmh": 3.0,
        "seed": int(seed + 10), "measurements": {"ssb_rsrp": False},
    }
    summary = gen.generate(cfg, num_samples=24, workers=1, collect_ssb=False)
    ds = load(summary["dataset_id"])
    gate = gates.gate_channel(ds)
    checks = [
        _check("门 1 无 blocker", gate.passed,
               "; ".join(x.detail for x in gate.blockers) or "no blockers"),
        _check("18 项体检全部执行", len(gate.items) == 18,
               f"n_checks={len(gate.items)}"),
    ]
    return _case("B10_tr38901_channel_gate",
                 "A freshly generated 38.901 channel passes the full independent gate set",
                 checks, {
                     "dataset_id": summary["dataset_id"],
                     "shape": summary["shape"],
                     "gate1": gate.as_dict(),
                     "calibration": cal.calibration_report(ds).as_dict(),
                 }, gate=gate.as_dict(),
                 notes=["warn/info 项完整保留；门 1 通过只代表无 blocking error。"])


_RUNNERS: dict[str, Callable[[int], dict[str, Any]]] = {
    "B01_awgn_shannon_siso": _b01,
    "B02_mimo_svd_upper_bound": _b02,
    "B03_lmmse_low_snr": _b03,
    "B04_olla_target_bler": _b04,
    "B05_pf_throughput_fairness": _b05,
    "B06_per_antenna_power_constraints": _b06,
    "B07_tdd_srs_reciprocity": _b07,
    "B08_jakes_doppler_time_scale": _b08,
    "B09_nr_tbs_rbg_monotonicity": _b09,
    "B10_tr38901_channel_gate": _b10,
}


def load_spec(path: str | os.PathLike[str] = DEFAULT_SPEC) -> dict[str, Any]:
    spec_path = Path(path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    ids = [str(row["id"]) for row in spec.get("cases", [])]
    if spec.get("suite_id") != SUITE_VERSION:
        raise ValueError(f"benchmark suite_id 不匹配：{spec.get('suite_id')!r}")
    if len(ids) != len(set(ids)) or set(ids) != set(_RUNNERS):
        raise ValueError(
            f"benchmark spec/runner 不一致：spec={ids} runner={sorted(_RUNNERS)}")
    if not spec.get("locked_before_first_run"):
        raise ValueError("benchmark 判据必须在第一次运行前锁定")
    if len(ids) < 6:
        raise ValueError("classic benchmark 至少需要 6 个独立 case")
    for row in spec["cases"]:
        missing = [
            key for key in ("title", "primary_metric", "expected", "sources")
            if not row.get(key)
        ]
        if missing or not all(
                isinstance(url, str) and url.startswith("https://")
                for url in row.get("sources", [])):
            raise ValueError(
                f"benchmark {row.get('id')} 缺少可审计判据/一手来源：{missing}")
    return spec


def run_suite(
    *,
    spec_path: str | os.PathLike[str] = DEFAULT_SPEC,
    case_ids: list[str] | None = None,
) -> dict[str, Any]:
    spec_file = Path(spec_path)
    spec = load_spec(spec_file)
    selected = list(case_ids or [str(row["id"]) for row in spec["cases"]])
    unknown = [name for name in selected if name not in _RUNNERS]
    if unknown:
        raise ValueError(f"未知 benchmark case：{unknown}")
    seed = int(spec["seed"])
    results: list[dict[str, Any]] = []
    for case_id in selected:
        started = time.perf_counter()
        try:
            result = _RUNNERS[case_id](seed)
        except Exception as exc:  # noqa: BLE001
            result = {
                "id": case_id, "title": case_id, "status": "error",
                "checks": [], "metrics": {}, "gate": None,
                "notes": [f"{type(exc).__name__}: {exc}"],
            }
        result["elapsed_s"] = round(time.perf_counter() - started, 3)
        results.append(result)
    return {
        "suite_id": SUITE_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "purpose": spec["purpose"],
        "seed": seed,
        "selected_case_ids": selected,
        "overall_status": (
            "pass" if results and all(row["status"] == "pass" for row in results)
            else "fail"),
        "n_pass": sum(row["status"] == "pass" for row in results),
        "n_fail": sum(row["status"] != "pass" for row in results),
        "provenance": _provenance(spec_file),
        "spec": spec,
        "results": results,
        "limitations": [
            "No company traffic CDF or field KPI was used.",
            "The preset BLER profile does not parameterize a TBS/rank/receiver axis.",
            "Passing this suite proves selected invariants, not field accuracy or universal performance.",
        ],
    }


def write_result(payload: dict[str, Any], path: str | os.PathLike[str] = DEFAULT_OUTPUT) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target.resolve()

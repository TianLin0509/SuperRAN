"""Reproducible runtime audit for SuperRAN's current high-cost paths.

This is a performance measurement, not a radio-performance experiment.  It uses
deterministic synthetic arrays and checks numerical identity before reporting any
speed ratio.  Results are written to ``artifacts/results/performance_audit.json``.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import linklevel as ll  # noqa: E402
from superran import measure  # noqa: E402
from superran import rng as rg  # noqa: E402
from superran import system as sy  # noqa: E402

OUT = ROOT / "artifacts" / "results" / "performance_audit.json"
_EPS = 1e-30


def _median_call(fn, repeats: int) -> tuple[float, Any]:
    times: list[float] = []
    value = None
    for _ in range(repeats):
        started = time.perf_counter()
        value = fn()
        times.append(time.perf_counter() - started)
    return float(np.median(times)), value


def _semantic_exact_differences(
    left: Any, right: Any, path: str = "", differences: list[str] | None = None,
) -> list[str]:
    """Exact finite-value comparison while treating equal non-finite classes alike."""
    out = differences if differences is not None else []
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            out.append(path + "/<keys>")
            return out
        for key in sorted(left):
            _semantic_exact_differences(left[key], right[key], path + "/" + str(key), out)
        return out
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            out.append(path + "/<length>")
            return out
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            _semantic_exact_differences(a, b, path + "/" + str(index), out)
        return out
    numeric = (int, float, np.integer, np.floating)
    if (isinstance(left, numeric) and not isinstance(left, (bool, np.bool_))
            and isinstance(right, numeric) and not isinstance(right, (bool, np.bool_))):
        a, b = float(left), float(right)
        if not np.isfinite(a) or not np.isfinite(b):
            same_class = ((np.isnan(a) and np.isnan(b))
                          or (np.isposinf(a) and np.isposinf(b))
                          or (np.isneginf(a) and np.isneginf(b)))
            if not same_class:
                out.append(path)
        elif a != b:
            out.append(path)
        return out
    if type(left) is not type(right) or left != right:
        out.append(path)
    return out


def _legacy_post_equalizer_sinr(
    h_eff: np.ndarray,
    noise_power: float,
    *,
    receiver: str,
    interference_cov: np.ndarray | None,
) -> np.ndarray:
    """Frozen pre-vectorization reference used only by this audit."""
    h_eff = np.asarray(h_eff)
    h_tf = h_eff[None] if h_eff.ndim == 3 else h_eff
    n_t, rb, rank, ue = h_tf.shape
    p_per_layer = 1.0 / max(rank, 1)
    out_tf = np.zeros((n_t, rb, rank), dtype=np.float64)
    for t in range(n_t):
        for f in range(rb):
            g = h_tf[t, f].conj().T
            r_n = np.eye(ue, dtype=np.complex128) * max(float(noise_power), _EPS)
            if interference_cov is not None:
                ic = np.asarray(interference_cov)
                r_uu = ic[f] if ic.ndim == 3 else ic
                if receiver == "irc":
                    r_n = r_n + r_uu
                else:
                    r_n = r_n + np.eye(ue, dtype=np.complex128) * (
                        float(np.real(np.trace(r_uu))) / max(ue, 1))
            r_inv = np.linalg.pinv(r_n)
            a = g.conj().T @ r_inv @ g
            if receiver in ("mmse", "irc"):
                diag = np.real(np.diag(np.linalg.pinv(
                    np.eye(rank, dtype=np.complex128) + p_per_layer * a)))
                out_tf[t, f] = np.maximum(
                    1.0 / np.maximum(diag, _EPS) - 1.0, 0.0)
            elif receiver == "zf":
                out_tf[t, f] = p_per_layer / np.maximum(
                    np.real(np.diag(np.linalg.pinv(a))), _EPS)
            elif receiver == "mrc":
                for k in range(rank):
                    gk = g[:, k]
                    signal = p_per_layer * float(
                        np.real(gk.conj() @ r_inv @ gk)) ** 2
                    leakage = sum(
                        p_per_layer
                        * abs(complex(gk.conj() @ r_inv @ g[:, j])) ** 2
                        for j in range(rank) if j != k)
                    noise = float(np.real(gk.conj() @ r_inv @ gk))
                    out_tf[t, f, k] = signal / max(leakage + noise, _EPS)
            else:  # pragma: no cover - fixed audit receiver list
                raise ValueError(receiver)
    if n_t == 1:
        return out_tf[0]
    return np.expm1(np.mean(np.log1p(out_tf), axis=0))


def _post_equalizer_benchmark() -> dict[str, Any]:
    random = np.random.default_rng(7)
    h = ((random.normal(size=(8, 272, 4, 4))
          + 1j * random.normal(size=(8, 272, 4, 4))) / np.sqrt(2))
    covariance = np.empty((272, 4, 4), dtype=np.complex128)
    for rb in range(272):
        a = ((random.normal(size=(4, 3)) + 1j * random.normal(size=(4, 3)))
             / np.sqrt(2))
        covariance[rb] = 0.02 * (a @ a.conj().T) + 0.001 * np.eye(4)
    rows: dict[str, Any] = {}
    for receiver in ("mmse", "irc", "zf", "mrc"):
        old_s, old = _median_call(
            lambda receiver=receiver: _legacy_post_equalizer_sinr(
                h, 0.1, receiver=receiver, interference_cov=covariance), 5)
        new_s, new = _median_call(
            lambda receiver=receiver: ll.post_equalizer_sinr(
                h, 0.1, receiver=receiver, interference_cov=covariance), 9)
        max_error = float(np.max(np.abs(np.asarray(old) - np.asarray(new))))
        rows[receiver] = {
            "legacy_python_loop_s": old_s,
            "batched_numpy_s": new_s,
            "speedup_x": old_s / max(new_s, 1e-12),
            "max_abs_error": max_error,
            "numerically_identical": bool(np.array_equal(old, new)),
            "allclose_1e_12": bool(np.allclose(old, new, rtol=1e-12, atol=1e-12)),
        }
    return {
        "shape": [8, 272, 4, 4],
        "meaning": "[snapshot,RB,rank,UE-Rx] post-equalizer benchmark",
        "receivers": rows,
    }


def _synthetic_tables() -> list[sy.UeLinkTable]:
    random = np.random.default_rng(20260809)
    channels = [
        ((random.standard_normal((8, 24, 16, 4))
          + 1j * random.standard_normal((8, 24, 16, 4))) / np.sqrt(2))
        for _ in range(6)
    ]
    return sy.build_link_tables(
        channels, [22.0, 19.0, 16.0, 12.0, 8.0, 4.0],
        max_rank=4, rb_per_rbg=16, power_constraint="nebf", mu_enabled=False)


def _replication_benchmark() -> dict[str, Any]:
    tables = _synthetic_tables()
    traffic = sy.TrafficConfig(model="full_buffer")
    scheduler = sy.SchedulerConfig(mu_enabled=False)
    rows: dict[str, Any] = {}
    for duration_s in (5.0, 50.0):
        cfg = sy.SystemConfig(
            duration_s=duration_s,
            power_constraint="nebf", seed=20260823)
        books = rg.replications(20260823, 8)
        def loop_serial(cfg=cfg, books=books):
            return [sy.simulate(
                tables, sys_cfg=cfg, traffic=traffic, sched=scheduler, rng=book)
                for book in books]

        def loop_thread(cfg=cfg, books=books):
            with ThreadPoolExecutor(max_workers=4) as pool:
                return list(pool.map(
                    lambda book, cfg=cfg: sy.simulate(
                        tables, sys_cfg=cfg, traffic=traffic,
                        sched=scheduler, rng=book), books))

        def product_serial(cfg=cfg):
            return sy.simulate_replications(
                tables, num_replications=8, master_seed=20260823,
                sys_cfg=cfg, traffic=traffic, sched=scheduler,
                replication_workers=1)

        def product_process(cfg=cfg):
            return sy.simulate_replications(
                tables, num_replications=8, master_seed=20260823,
                sys_cfg=cfg, traffic=traffic, sched=scheduler,
                replication_workers=4)

        loop_times = {"serial": [], "thread4": []}
        product_times = {"serial": [], "process4": []}
        loop_results: dict[str, list[sy.SystemResult]] = {}
        product_results: dict[str, sy.ReplicationResult] = {}
        for round_index in range(3):
            loop_order = (("serial", loop_serial), ("thread4", loop_thread))
            product_order = (("serial", product_serial), ("process4", product_process))
            if round_index % 2:
                loop_order = tuple(reversed(loop_order))
                product_order = tuple(reversed(product_order))
            for name, function in loop_order:
                started = time.perf_counter()
                loop_results[name] = function()
                loop_times[name].append(time.perf_counter() - started)
            for name, function in product_order:
                started = time.perf_counter()
                product_results[name] = function()
                product_times[name].append(time.perf_counter() - started)

        loop_serial_s = float(np.median(loop_times["serial"]))
        thread_s = float(np.median(loop_times["thread4"]))
        product_serial_s = float(np.median(product_times["serial"]))
        process_s = float(np.median(product_times["process4"]))
        reference = [
            run.cell["cell_served_mbps"] for run in loop_results["serial"]]
        thread_values = [
            run.cell["cell_served_mbps"] for run in loop_results["thread4"]]
        product_reference = product_results["serial"]
        process_result = product_results["process4"]
        product_differences = _semantic_exact_differences(
            product_reference.cell, process_result.cell, "cell")
        _semantic_exact_differences(
            product_reference.users, process_result.users, "users", product_differences)
        rows[f"{duration_s:g}s"] = {
            "num_replications": 8,
            "num_ues": len(tables),
            "num_tti": cfg.num_tti,
            "tti_loop_serial_s": loop_serial_s,
            "tti_loop_thread4_s": thread_s,
            "product_serial_s": product_serial_s,
            "product_process4_s": process_s,
            "thread_speedup_x": loop_serial_s / max(thread_s, 1e-12),
            "process_speedup_x": product_serial_s / max(process_s, 1e-12),
            "thread_bitwise_equal": reference == thread_values,
            "process_raw_python_equal": (
                product_reference.cell == process_result.cell
                and product_reference.users == process_result.users),
            "process_semantic_exact_equal": not product_differences,
            "process_difference_paths": product_differences[:20],
            "rounds": {"tti_loop": loop_times, "product": product_times},
            "product_parallel_metadata": process_result.parallel,
        }
    return rows


def _pmi_benchmark() -> dict[str, Any]:
    random = np.random.default_rng(11)
    rows: dict[str, Any] = {}
    for ports, n_h, n_v in ((64, 8, 4), (256, 16, 8)):
        h = ((random.normal(size=(1, 17, ports, 4))
              + 1j * random.normal(size=(1, 17, ports, 4))) / np.sqrt(2)).astype(
                  np.complex64)
        measure.type_i_codebook(n_h, 4, n_v, 4, dual_pol=True)
        elapsed, result = _median_call(
            lambda h=h, n_h=n_h, n_v=n_v: measure.pmi_type_i(
                h, n_h=n_h, n_v=n_v, max_rank=4,
                port_order="pol_h_v", vertical_index_order="top_to_bottom"), 5)
        rows[f"{ports}T"] = {
            "median_s": elapsed,
            "indices": result.indices,
            "codebook_columns": result.codebook_size,
            "rank": result.rank,
        }
    return rows


def main() -> None:
    started = time.perf_counter()
    payload = {
        "schema": "superran_performance_audit_v1",
        "scope": "runtime mechanisms only; no radio-performance claim",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "numpy": np.__version__,
        },
        "post_equalizer": _post_equalizer_benchmark(),
        "replication_parallelism": _replication_benchmark(),
        "pmi_search": _pmi_benchmark(),
    }
    payload["elapsed_s"] = time.perf_counter() - started
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()

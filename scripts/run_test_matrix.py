"""Run every SuperRAN test file as its documented direct entrypoint.

Unlike a monolithic pytest collection, this runner reports the active file, emits a
heartbeat, applies a per-file timeout, keeps one log per file, and always writes a JSON
summary.  It never searches for or terminates unrelated processes.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "test-matrix"

QUICK = (
    "test_carrier.py",
    "test_channel_generation_contract.py",
    "test_company_256t.py",
    "test_developer_guide.py",
    "test_lazy_imports.py",
    "test_mcp_server.py",
    "test_mumimo.py",
    "test_power_control.py",
    "test_results.py",
    "test_rng.py",
    "test_scheduler_p0.py",
    "test_srs_resource.py",
    "test_sysscenes.py",
    "test_system.py",
    "test_system_sim_tool.py",
    "test_benchmarks.py",
)
PHYSICS = (
    "test_csi_aging.py",
    "test_e2e.py",
    "test_gates.py",
    "test_interference.py",
    "test_linkadapt.py",
    "test_linklevel.py",
    "test_physics_contract_extensions.py",
    "test_physics_invariants.py",
    "test_raytracing.py",
    "test_srs_waveform.py",
)


def _kill_exact_tree(proc: subprocess.Popen[Any]) -> bool:
    if proc.poll() is not None:
        return True
    if os.name == "nt":
        killed = subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        return killed.returncode == 0
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        return True


def _run_one(name: str, *, timeout_s: float, heartbeat_s: float) -> dict[str, Any]:
    test_path = ROOT / "tests" / name
    if not test_path.is_file():
        return {"file": name, "status": "missing", "exit_code": None, "elapsed_s": 0.0}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / f"{test_path.stem}.log"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "SUPERRAN_NO_BROWSER": "1"}
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    start = time.perf_counter()
    print(f"START {name}", flush=True)
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        proc = subprocess.Popen(
            [sys.executable, str(test_path)], cwd=ROOT, env=env,
            stdout=log, stderr=subprocess.STDOUT, **kwargs,
        )
        next_heartbeat = start + heartbeat_s
        timed_out = False
        cleanup_ok = True
        while proc.poll() is None:
            now = time.perf_counter()
            if now - start > timeout_s:
                timed_out = True
                cleanup_ok = _kill_exact_tree(proc)
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=20)
                    cleanup_ok = False
                break
            if now >= next_heartbeat:
                print(f"HEARTBEAT {name} {now-start:.0f}s", flush=True)
                next_heartbeat = now + heartbeat_s
            time.sleep(0.25)
    elapsed = time.perf_counter() - start
    code = proc.returncode
    encoding_error = None
    try:
        text = log_path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        encoding_error = f"UTF-8 decode failed: {exc}"
        text = log_path.read_bytes().decode("utf-8", errors="replace")
    tail = "\n".join(text.splitlines()[-20:])
    status = (
        "timeout" if timed_out else
        "fail" if encoding_error else
        "pass" if code == 0 else "fail")
    print(f"END {name} {status} {elapsed:.1f}s", flush=True)
    return {
        "file": name, "status": status, "exit_code": code,
        "elapsed_s": round(elapsed, 3), "timeout_s": timeout_s,
        "log_path": str(log_path.resolve()), "tail": tail,
        "encoding_error": encoding_error, "cleanup_ok": cleanup_ok,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=("quick", "physics", "full"), default="quick")
    parser.add_argument("--timeout", type=float, default=1200.0, help="per-file timeout seconds")
    parser.add_argument("--heartbeat", type=float, default=30.0)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--output", default=str(OUT_DIR / "summary.json"))
    args = parser.parse_args()
    registered = tuple(QUICK + PHYSICS)
    discovered = tuple(sorted(path.name for path in (ROOT / "tests").glob("test_*.py")))
    duplicates = sorted({name for name in registered if registered.count(name) > 1})
    missing = sorted(set(discovered) - set(registered))
    stale = sorted(set(registered) - set(discovered))
    if duplicates or missing or stale:
        raise SystemExit(
            "test matrix catalogue drift: "
            f"duplicates={duplicates}, missing={missing}, stale={stale}"
        )
    selected = list(args.only) if args.only else (
        list(QUICK) if args.tier == "quick" else
        list(PHYSICS) if args.tier == "physics" else
        list(QUICK + PHYSICS)
    )
    if args.timeout <= 0 or args.heartbeat <= 0:
        raise SystemExit("timeout/heartbeat must be positive")
    started = time.time()
    output = Path(args.output)
    rows: list[dict[str, Any]] = []
    for name in selected:
        rows.append(_run_one(
            name, timeout_s=float(args.timeout), heartbeat_s=float(args.heartbeat)))
        _write_json_atomic(output, {
            "tier": args.tier, "status": "running", "files": rows,
            "remaining": selected[len(rows):],
        })
    payload = {
        "tier": args.tier, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_s": round(time.time() - started, 3), "files": rows,
        "n_pass": sum(row["status"] == "pass" for row in rows),
        "n_fail": sum(row["status"] != "pass" for row in rows),
        "status": "pass" if rows and all(row["status"] == "pass" for row in rows) else "fail",
    }
    _write_json_atomic(output, payload)
    print(json.dumps({
        "path": str(output.resolve()), "status": payload["status"],
        "n_pass": payload["n_pass"], "n_fail": payload["n_fail"],
        "elapsed_s": payload["elapsed_s"],
    }, ensure_ascii=False), flush=True)
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

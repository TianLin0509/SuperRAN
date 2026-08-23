"""实验血缘：代码、依赖和关键物理数据版本的不可变快照。"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import scipy

from . import bler_data_20b

_ROOT = Path(__file__).resolve().parents[2]
PROVENANCE_VERSION = "superran-provenance-v2"
_PRESET_BLER_SHA_KEY = "preset_bler_sha256"
_LEGACY_PRESET_BLER_SHA_KEY = "company_" + "bler_sha256"


def _git_bytes(args: list[str]) -> bytes | None:
    try:
        cp = subprocess.run(
            ["git", "-C", str(_ROOT), *args], check=True,
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bytes(cp.stdout)


def _git_text(args: list[str]) -> str | None:
    raw = _git_bytes(args)
    return None if raw is None else raw.decode("utf-8", errors="strict").strip()


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _source_tree_fingerprint() -> tuple[str | None, int | None]:
    """哈希运行语义树，补足 git diff 不含新文件的缺口。

    文档/测试改动不应迫使用户重生成数百 GB 信道；只纳入实际运行模块、预设和
    依赖合同。tracked 与 untracked(non-ignored) 一视同仁。
    """
    raw = _git_bytes(["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    if raw is None:
        return None, None
    names = [part.decode("utf-8", errors="strict") for part in raw.split(b"\0") if part]
    names = [
        name for name in names
        if name.replace("\\", "/").startswith(("src/superran/", "presets/"))
        or name.replace("\\", "/") == "pyproject.toml"
    ]
    digest = hashlib.sha256()
    count = 0
    for name in sorted(names):
        path = (_ROOT / name).resolve()
        try:
            path.relative_to(_ROOT.resolve())
        except ValueError:
            return None, None
        if not path.is_file():
            continue
        data = path.read_bytes()
        encoded_name = name.replace("\\", "/").encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "little"))
        digest.update(encoded_name)
        digest.update(len(data).to_bytes(8, "little"))
        digest.update(data)
        count += 1
    return digest.hexdigest(), count


def _collect_base() -> dict[str, Any]:
    """在模块导入线程采集不会随一次实验变化的血缘。"""
    if threading.current_thread() is not threading.main_thread():
        # Windows MCP 会把同步工具派到工作线程；工作线程首次 CreateProcess 曾
        # 实测挂死。宁可把 Git 状态标 unknown，也不在非主线程启动外部进程。
        return {
            "version": PROVENANCE_VERSION,
            "git_commit": None,
            "git_branch": None,
            "git_dirty": None,
            "git_dirty_path_count": None,
            "git_diff_sha256": None,
            "collection_warning": "provenance imported outside main thread; git capture skipped",
        }
    diff = _git_bytes(["diff", "--no-ext-diff", "--binary"])
    status = _git_text(["status", "--porcelain"])
    source_tree_sha256, source_file_count = _source_tree_fingerprint()
    return {
        "version": PROVENANCE_VERSION,
        "git_commit": _git_text(["rev-parse", "HEAD"]),
        "git_branch": _git_text(["branch", "--show-current"]),
        "git_dirty": bool(status),
        "git_dirty_path_count": len(status.splitlines()) if status else 0,
        "git_diff_sha256": (
            hashlib.sha256(diff).hexdigest() if diff is not None else None),
        "source_tree_sha256": source_tree_sha256,
        "source_file_count": source_file_count,
        "source_tree_scope": ["src/superran/**", "presets/**", "pyproject.toml"],
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": {
            "superran": _version("superran"),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "mcp": _version("mcp"),
            "sionna-rt": _version("sionna-rt"),
            "playwright": _version("playwright"),
        },
        "physical_data": {
            _PRESET_BLER_SHA_KEY: bler_data_20b.DATA_SHA256,
            "cdl_table_source": "3GPP TR 38.901 V17.0.0 tables 7.7.1-1..5",
            "carrier_contract": "superran-tdd-100m-30khz-272rb-17x16-v1",
        },
    }


_BASE_SNAPSHOT = _collect_base()


def snapshot(*, source: str | None = None) -> dict[str, Any]:
    """返回进程启动期缓存的血缘；不会在 MCP 工作线程启动子进程。"""
    out = copy.deepcopy(_BASE_SNAPSHOT)
    out["captured_at"] = time.time()
    out["source_adapter"] = source
    return out


def compare(dataset: dict[str, Any] | None, runtime: dict[str, Any]) -> dict[str, Any]:
    """比较数据生成时与当前运行时的关键血缘，返回 match/mismatch/unknown。"""
    if not dataset:
        return {
            "status": "unknown",
            "matches": None,
            "mismatches": ["dataset has no provenance (legacy artifact)"],
        }
    dataset = copy.deepcopy(dataset)
    physical = dataset.get("physical_data")
    if isinstance(physical, dict) and _PRESET_BLER_SHA_KEY not in physical:
        legacy_value = physical.get(_LEGACY_PRESET_BLER_SHA_KEY)
        if legacy_value is not None:
            physical[_PRESET_BLER_SHA_KEY] = legacy_value
    paths = (
        ("source_tree_sha256",),
        ("git_commit",),
        ("git_diff_sha256",),
        ("dependencies", "numpy"),
        ("dependencies", "scipy"),
        ("physical_data", _PRESET_BLER_SHA_KEY),
    )
    mismatches: list[str] = []
    unknown: list[str] = []
    for path in paths:
        a: Any = dataset
        b: Any = runtime
        for key in path:
            a = a.get(key) if isinstance(a, dict) else None
            b = b.get(key) if isinstance(b, dict) else None
        label = ".".join(path)
        if a is None or b is None:
            unknown.append(label)
        elif a != b:
            mismatches.append(f"{label}: dataset={a!r}, runtime={b!r}")
    if mismatches:
        status, matches = "mismatch", False
    elif unknown:
        status, matches = "unknown", None
    else:
        status, matches = "match", True
    return {
        "status": status,
        "matches": matches,
        "mismatches": mismatches,
        "unknown_fields": unknown,
    }

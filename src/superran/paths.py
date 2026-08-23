"""路径解析。所有产物落在项目 artifacts/ 下，可用环境变量整体挪走。"""
from __future__ import annotations

import os
import re
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _PKG_ROOT.parents[1]  # src/superran -> 项目根


def project_root() -> Path:
    return _PROJECT_ROOT


def artifacts_root() -> Path:
    p = Path(os.environ.get("SUPERRAN_ARTIFACTS", str(_PROJECT_ROOT / "artifacts")))
    p.mkdir(parents=True, exist_ok=True)
    return p


def datasets_dir() -> Path:
    p = artifacts_root() / "datasets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def drafts_dir() -> Path:
    p = artifacts_root() / "drafts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def presets_file() -> Path:
    return Path(
        os.environ.get("SUPERRAN_PRESETS", str(_PROJECT_ROOT / "presets" / "presets.yaml"))
    )


def results_dir() -> Path:
    """外部算法注册回来的结果。逐样本数值落 .npz，绝不进 MCP JSON。"""
    p = artifacts_root() / "results"
    p.mkdir(parents=True, exist_ok=True)
    return p


def prereg_dir() -> Path:
    """预注册的分析口径。一旦写下就不再原地改，改动一律新建。"""
    p = artifacts_root() / "prereg"
    p.mkdir(parents=True, exist_ok=True)
    return p


_HANDLE_RE = re.compile(r"[A-Za-z0-9_-]+")


def dataset_dir(dataset_id: str) -> Path:
    # 句柄是 MCP 工具的自由文本入参，不能当"自己人生成的安全串"——
    # 越界读（..、绝对路径）必须在这里拦死。
    if not _HANDLE_RE.fullmatch(str(dataset_id)):
        raise ValueError(f"非法 dataset_id {dataset_id!r}：只允许 [A-Za-z0-9_-]")
    return datasets_dir() / dataset_id

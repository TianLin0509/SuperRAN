"""路径解析。所有产物落在项目 artifacts/ 下，可用环境变量整体挪走。"""
from __future__ import annotations

import os
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


def dataset_dir(dataset_id: str) -> Path:
    return datasets_dir() / dataset_id

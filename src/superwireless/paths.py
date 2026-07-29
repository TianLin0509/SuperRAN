"""路径解析。所有产物落在项目 artifacts/ 下，可用环境变量整体挪走。"""
from __future__ import annotations

import os
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _PKG_ROOT.parents[1]  # src/superwireless -> 项目根


def project_root() -> Path:
    return _PROJECT_ROOT


def artifacts_root() -> Path:
    p = Path(os.environ.get("SUPERWIRELESS_ARTIFACTS", str(_PROJECT_ROOT / "artifacts")))
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
        os.environ.get("SUPERWIRELESS_PRESETS", str(_PROJECT_ROOT / "presets" / "presets.yaml"))
    )


def dataset_dir(dataset_id: str) -> Path:
    return datasets_dir() / dataset_id

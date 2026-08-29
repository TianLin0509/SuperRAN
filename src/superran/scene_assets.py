"""Fail-closed scene asset locking, fingerprints and RF revisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from filelock import FileLock

SCENE_LOCKS_DIRNAME = ".locks"
SCENE_REBUILD_JOURNAL_FILENAME = ".scene-rebuild-transaction.json"
SCENE_META_JOURNAL_FILENAME = ".scene-meta-transaction.json"

__all__ = [
    "SCENE_LOCKS_DIRNAME",
    "SCENE_META_JOURNAL_FILENAME",
    "SCENE_REBUILD_JOURNAL_FILENAME",
    "assert_scene_assets_recovered",
    "radio_config_revision",
    "scene_asset_lock",
    "scene_fidelity_from_meta",
    "scene_tree_fingerprint",
]


def scene_asset_lock(asset_dir: str | Path, *, timeout: float = 120.0) -> FileLock:
    """Return a stable process lock that survives scene directory replacement."""
    root = Path(asset_dir)
    lock_dir = root.parent / SCENE_LOCKS_DIRNAME
    lock_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(str(lock_dir / f"{root.name}.lock"), timeout=float(timeout))


def assert_scene_assets_recovered(asset_dir: str | Path) -> None:
    """Reject a cache carrying an interrupted geometry or metadata publish."""
    root = Path(asset_dir)
    journals = (
        root / SCENE_REBUILD_JOURNAL_FILENAME,
        root / SCENE_META_JOURNAL_FILENAME,
    )
    if any(path.is_file() for path in journals):
        raise RuntimeError(
            "检测到未完成的场景资产发布事务；拒绝猜测新旧几何/材质版本，"
            "请先重新准备该场景"
        )


def radio_config_revision(meta: dict[str, Any]) -> str:
    """Hash every metadata field that changes radio physics without new geometry."""
    prior = meta.get("environment_prior")
    scene_revision = prior.get("scene_revision") if isinstance(prior, dict) else None
    buildings = meta.get("buildings")
    building_radio: list[dict[str, Any]] = []
    if isinstance(buildings, list):
        for item in buildings:
            if not isinstance(item, dict):
                continue
            building_radio.append({
                "material": item.get("material"),
                "roof_material": item.get("roof_material"),
                "glass_ratio": item.get("glass_ratio"),
                "material_params": item.get("material_params"),
            })
    canonical = {
        "scene_revision": scene_revision,
        "materials": meta.get("materials"),
        "building_radio": building_radio,
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "rf_" + hashlib.sha256(encoded).hexdigest()


def scene_fidelity_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Describe which semantic environment layers actually reached RT assets."""
    prior = meta.get("environment_prior")
    prior = prior if isinstance(prior, dict) else {}
    raw_counts = prior.get("semantic_point_counts")
    counts = raw_counts if isinstance(raw_counts, dict) else {}
    raw_ids = prior.get("semantic_ids")
    semantic_ids = raw_ids if isinstance(raw_ids, dict) else {}
    materials = meta.get("materials")
    materials = materials if isinstance(materials, dict) else {}

    actual_semantics = sorted(
        (
            name
            for name, count in counts.items()
            if isinstance(name, str)
            and isinstance(count, (int, float))
            and not isinstance(count, bool)
            and count > 0
        ),
        key=lambda name: (int(semantic_ids.get(name, 1_000_000)), name),
    )

    def _present(semantic: str, material: str | None = None) -> bool:
        present = bool(counts.get(semantic, 0))
        if material is not None:
            config = materials.get(material)
            present = (
                present
                and isinstance(config, dict)
                and int(config.get("count", 0) or 0) > 0
            )
        return present

    layers = {
        "buildings": _present("building_facade") or _present("building_roof"),
        "water": _present("water", "p527_fresh_water"),
        "roads": _present("road", "road_asphalt"),
        "green": _present("green", "green_soil"),
        "vegetation": _present("vegetation", "veg"),
    }
    calibration = meta.get("material_calibration")
    calibration_status = (
        calibration.get("status", "uncalibrated")
        if isinstance(calibration, dict) else "uncalibrated"
    )
    has_environment = any(
        layers[name] for name in ("water", "roads", "green", "vegetation")
    )
    return {
        "schema_version": 1,
        "level": "L1_semantic" if has_environment else "L0_geometry",
        "rt_layers": layers,
        "point_cloud_semantics": actual_semantics,
        "calibration_status": str(calibration_status),
    }


def scene_tree_fingerprint(
    root: str | Path,
    *,
    exclude_names: frozenset[str] = frozenset({".prepared"}),
) -> str:
    """Hash relative paths and bytes for one immutable scene asset tree."""
    base = Path(root)
    if not base.is_dir():
        raise FileNotFoundError(f"scene asset directory does not exist: {base}")
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in base.rglob("*")
        if path.is_file()
        and path.name not in exclude_names
        and SCENE_LOCKS_DIRNAME not in path.relative_to(base).parts
    )
    if not files:
        raise ValueError(f"scene asset directory is empty: {base}")
    for path in files:
        if path.is_symlink():
            raise ValueError(f"scene asset tree contains a symbolic link: {path}")
        relative = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "little"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "little"))
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return "scene_" + digest.hexdigest()

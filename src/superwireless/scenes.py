"""射线追踪场景管理。

两类场景：

* **内置场景**（munich / etoile / florence / san_francisco）——Sionna 自带，
  开箱即用。
* **真实城市场景**（北京中关村、上海陆家嘴、深圳福田……）——ChannelHub 仓库里
  带 Mitsuba 场景文件，但那些 PLY 是 VTK 导出的，头部含 ``obj_info`` 字段，
  Mitsuba 3.8 的 PLY 解析器不认，会直接报错。

所以真实城市场景首次使用时要先"准备"：把场景目录复制到 artifacts 缓存并
清掉 PLY 头里的 ``obj_info`` 行。**不修改 ChannelHub 原文件。**
准备结果带缓存，同一场景只做一次。
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .channelhub import channelhub_root
from .paths import artifacts_root

# Sionna 自带的场景，不需要本地资产
BUILTIN_SCENES = ("munich", "etoile", "florence", "san_francisco")


@dataclass
class SceneInfo:
    """一个射线追踪场景。"""

    scene_id: str
    display_name: str
    description: str
    builtin: bool
    presets: dict[str, Any] = field(default_factory=dict)
    default_preset: str | None = None
    num_buildings: int | None = None
    mean_building_height_m: float | None = None
    coverage_m: list[float] | None = None
    needs_preparation: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "display_name": self.display_name,
            "description": self.description,
            "builtin": self.builtin,
            "presets": {
                k: {
                    "display_name": v.get("display_name"),
                    "num_sites": v.get("num_sites"),
                    "sectors_per_site": v.get("sectors_per_site"),
                    "num_cells": v.get("num_cells"),
                    "isd_m": v.get("isd_m"),
                    "carrier_freq_hz": v.get("carrier_freq_hz"),
                }
                for k, v in self.presets.items()
            },
            "default_preset": self.default_preset,
            "num_buildings": self.num_buildings,
            "mean_building_height_m": self.mean_building_height_m,
            "coverage_m": self.coverage_m,
            "needs_preparation": self.needs_preparation,
        }


def scenes_dir() -> Path:
    return channelhub_root() / "configs" / "scenes"


def scene_cache_dir() -> Path:
    p = artifacts_root() / "scenes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_scenes() -> list[SceneInfo]:
    """列出所有可用的射线追踪场景。"""
    out: list[SceneInfo] = []
    base = scenes_dir()
    if not base.is_dir():
        return out

    for f in sorted(base.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sid = d.get("scene_id", f.stem)
        builtin = sid in BUILTIN_SCENES
        has_assets = (base / sid / "scene.xml").is_file()
        out.append(
            SceneInfo(
                scene_id=sid,
                display_name=d.get("display_name", sid),
                description=d.get("description", ""),
                builtin=builtin,
                presets=d.get("presets", {}) or {},
                default_preset=d.get("default_preset"),
                num_buildings=d.get("num_buildings"),
                mean_building_height_m=d.get("mean_building_height_m"),
                coverage_m=d.get("coverage_m"),
                needs_preparation=(not builtin) and has_assets,
            )
        )
    return out


def get_scene(scene_id: str) -> SceneInfo | None:
    for s in list_scenes():
        if s.scene_id == scene_id:
            return s
    return None


# ---------------------------------------------------------------------------
# PLY 修复
# ---------------------------------------------------------------------------


def _strip_obj_info(path: Path) -> bool:
    """删掉 PLY 头部的 ``obj_info`` 行，返回是否改动过。

    PLY 头是 ASCII、正文可能是二进制，所以只在 ``end_header`` 之前动手，
    正文原样保留。
    """
    data = path.read_bytes()
    marker = b"end_header"
    idx = data.find(marker)
    if idx < 0:
        return False
    # 连同该行的换行符一起算作头部
    end = data.find(b"\n", idx)
    if end < 0:
        return False
    head, body = data[: end + 1], data[end + 1 :]

    if b"obj_info" not in head:
        return False

    lines = head.split(b"\n")
    kept = [ln for ln in lines if not ln.lstrip().startswith(b"obj_info")]
    path.write_bytes(b"\n".join(kept) + body)
    return True


def prepare_scene(scene_id: str, *, force: bool = False) -> dict[str, Any]:
    """准备真实城市场景，返回可直接用的 ``osm_path``。

    内置场景直接返回（Sionna 自带，无需准备）。真实城市场景会复制到
    artifacts 缓存并修掉 PLY 头，**不动 ChannelHub 原文件**。
    """
    info = get_scene(scene_id)
    if info is None:
        raise KeyError(
            f"未知场景 {scene_id!r}；可用：{[s.scene_id for s in list_scenes()]}"
        )
    if info.builtin:
        return {
            "scene_id": scene_id,
            "builtin": True,
            "prepared": True,
            "osm_path": None,
            "note": "Sionna 自带场景，无需本地资产",
        }

    src = scenes_dir() / scene_id
    if not (src / "scene.xml").is_file():
        raise FileNotFoundError(f"场景 {scene_id!r} 缺少 scene.xml：{src}")

    dst = scene_cache_dir() / scene_id
    stamp = dst / ".prepared"

    if stamp.is_file() and not force:
        return {
            "scene_id": scene_id,
            "builtin": False,
            "prepared": True,
            "cached": True,
            "osm_path": str(dst / "scene.xml"),
            **json.loads(stamp.read_text(encoding="utf-8")),
        }

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    ply_files = list(dst.rglob("*.ply"))
    fixed = sum(1 for p in ply_files if _strip_obj_info(p))

    meta = {
        "ply_total": len(ply_files),
        "ply_fixed": fixed,
        "note": (
            "已复制到缓存并清理 PLY 头部的 obj_info 字段"
            "（VTK 导出的写法，Mitsuba 3.8 不接受）。ChannelHub 原文件未改动。"
        ),
    }
    stamp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    return {
        "scene_id": scene_id,
        "builtin": False,
        "prepared": True,
        "cached": False,
        "osm_path": str(dst / "scene.xml"),
        **meta,
    }


def resolve_scene_config(scene_id: str, preset: str | None = None) -> dict[str, Any]:
    """把场景 + 站点预设翻译成 ChannelHub 的 sionna_rt 配置片段。

    真实城市场景会自动完成准备工作，并把 ``osm_path`` 解析成绝对路径
    （ChannelHub 的 profile 里写的是相对仓库根的路径）。
    """
    info = get_scene(scene_id)
    if info is None:
        raise KeyError(f"未知场景 {scene_id!r}")

    prep = prepare_scene(scene_id)
    cfg: dict[str, Any] = {"source": "sionna_rt"}

    if info.builtin:
        cfg["scenario"] = scene_id
    else:
        cfg["scenario"] = "custom_osm"
        cfg["osm_path"] = prep["osm_path"]
        name = preset or info.default_preset
        if name:
            cfg["scene_preset"] = f"{scene_id}_{name}"

    name = preset or info.default_preset
    body = info.presets.get(name or "", {})
    for key in ("num_sites", "sectors_per_site", "tx_height_m", "isd_m", "carrier_freq_hz"):
        if body.get(key) is not None:
            cfg[key] = body[key]

    return cfg

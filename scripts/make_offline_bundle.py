"""打一个离线安装包，给不能联网的机器用。

产出 ``dist/superran-offline-<包型>-<平台>-<pyver>.zip``，里面有：

* superran 全部源码、skill、测试、文档
* ``wheels/`` —— 依赖的 wheel 文件，装的时候 ``pip install --no-index --find-links wheels``
* ``INSTALL_AGENT.md`` —— 写给 AI agent 看的安装说明，用户把它丢给自己的 agent 就行
* ``开始安装.txt`` —— 给人看的一句话说明
* ``bundle-manifest.json`` —— 包型、commit、平台、各 wheel 与关键文件的 SHA-256

**默认打完整包。** 早先默认打的是轻量包（不含 numpy/scipy），结果它在全新 venv 里
连 ``pip install --no-index -e .`` 都起不来——先卡在缺构建后端 setuptools，
而报错只说 "pip subprocess to install build dependencies did not run successfully"，
完全看不出缺的是什么。轻量包现在必须显式 ``--thin``，包型也写进文件名。

**wheel 是平台相关的。** numpy/scipy/pydantic-core 都有编译好的二进制，
在 Windows 上下的包拿到 Linux 上装不了。所以文件名里带了平台和 Python 版本，
**必须在与目标机器同平台、同 Python 大版本的机器上打包**。跨平台用
``--platform`` / ``--python-version`` 让 pip 下别的平台的轮子（只对纯二进制
wheel 有效，且必须配 ``--only-binary=:all:``）。

用法::

    python scripts/make_offline_bundle.py               # 完整包，全新 venv 可全程离线装
    python scripts/make_offline_bundle.py --thin       # 轻量包，要求目标机已有 numpy/scipy
    python scripts/make_offline_bundle.py --no-wheels  # 只打源码

ChannelHub **默认不打包** —— 它没有开源许可证，默认保留所有权利，转发有法律风险。
确认自己有权分发时（公司内部本来就有这份代码、或已获授权）用
``--include-channelhub`` 打进去。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

# 打进包里的源码。artifacts/ 是生成物，.git 是历史，都不要。
INCLUDE = [
    "src", "scripts", "skills", "presets", "tests",
    "pyproject.toml", "README.md", "LICENSE",
    "INSTALL_AGENT.md", "SETUP.html", "CAPABILITIES.html", "SHOWCASE.html",
    "CLAUDE.md",
]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git", "artifacts"}

RUNTIME_DEPS = ["mcp>=1.0", "pydantic>=2.0", "pyyaml>=6.0", "structlog>=23.0"]
SCIENCE_DEPS = ["numpy>=1.24", "scipy>=1.10"]
# `pip install -e .` 会起一个隔离的构建环境去装 build-system.requires，
# 离线时这一步也得有轮子，否则连构建后端都起不来——**比缺 numpy 更早失败**，
# 且报错是 "pip subprocess to install build dependencies did not run successfully"，
# 完全看不出缺的是 setuptools。
BUILD_DEPS = ["setuptools>=68", "wheel"]

READ_ME_FIRST = """superran 离线安装包
========================

{KIND_NOTE}

不用自己照着敲命令。把下面这句话发给你的 AI coding agent
（Claude Code / Codex / 任何能读文件+跑命令的 agent），它会自己装完并验证：

    这个目录里是 superran 离线安装包，读 INSTALL_AGENT.md 按里面的步骤
    装好并验证，装完告诉我能不能用。

--------------------------------------------------------------------
需要你先准备好的两样东西
--------------------------------------------------------------------

1) Python >= 3.10

2) ChannelHub 源码（物理内核）
   本包**不含** ChannelHub —— 它没有开源许可证，不能随包转发。
   你需要自己拿到一份，判据是目录下存在：
       src/msg_embedding/data/contract.py
   放在本目录的兄弟目录下（叫 ChannelHub_main）会被自动发现；
   放别处就设环境变量 SUPERRAN_CHANNELHUB 指过去。

--------------------------------------------------------------------
包里有什么
--------------------------------------------------------------------

  INSTALL_AGENT.md   给 agent 看的安装说明（第一优先）
  SETUP.html         给人看的：由哪几块拼成、三种用法、排错
  CAPABILITIES.html  能产生哪些信道、能拿到哪些观察量
  SHOWCASE.html      实测演示与踩过的坑
  README.md          项目说明
  wheels/            依赖的 wheel（离线 pip 安装用）
  bundle-manifest.json  包型、commit、平台、各文件 SHA-256（可核对完整性）
  src/ scripts/ skills/ presets/ tests/

--------------------------------------------------------------------
真要手动装的话，三条命令
--------------------------------------------------------------------

  <你的python> -m pip install --no-index --find-links wheels -e .
  <你的agent CLI> mcp add superran -- <python绝对路径> <本目录绝对路径>/scripts/mcp_server.py
  cp -r skills/channel-sim ~/.claude/skills/

验证： <你的python> tests/test_e2e.py
"""


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# manifest 里记 hash 的关键文件：改一个字都会让 hash 变，接收方能核出来。
_MANIFEST_FILES = (
    "INSTALL_AGENT.md", "开始安装.txt", "pyproject.toml", "README.md",
    "skills/channel-sim/SKILL.md", "scripts/mcp_server.py",
    "presets/presets.yaml", "SETUP.html", "CAPABILITIES.html", "SHOWCASE.html",
)


def _git_commit(repo: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _build_manifest(stage: Path, kind: str, plat: str, py: str, args: Any) -> dict:
    """写一份可核对的清单。

    接收方拿到 zip 后能核三件事：包型对不对（thin 包别当 full 用）、
    文件有没有被改过、ChannelHub 到底在不在里面。
    """
    wheels_dir = stage / "wheels"
    wheels = []
    if wheels_dir.is_dir():
        for w in sorted(wheels_dir.iterdir()):
            if w.is_file():
                wheels.append(
                    {"name": w.name, "size": w.stat().st_size, "sha256": _sha256(w)}
                )
    files = {}
    for rel in _MANIFEST_FILES:
        p = stage / rel
        if p.is_file():
            files[rel] = _sha256(p)

    ch_dir = stage / "ChannelHub_main"
    return {
        "bundle_kind": kind,
        "self_contained": kind == "full",
        "requires_preinstalled": (
            [] if kind == "full" else ["numpy", "scipy"] if kind == "thin"
            else ["numpy", "scipy", "mcp", "pydantic", "pyyaml", "structlog", "setuptools"]
        ),
        "superran_commit": _git_commit(REPO),
        "build_time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "built_with_python": sys.version.split()[0],
        "target_platform": plat,
        "target_python": py,
        "mcp_compat": "1.x 与 2.x 均支持（server.py 做了兼容导入）",
        "includes_channelhub": ch_dir.is_dir(),
        "channelhub_commit": (
            _git_commit(Path(args.include_channelhub)) if args.include_channelhub else None
        ),
        "channelhub_marker_contract": (
            None if ch_dir.is_dir()
            else "接收方需自备含 src/msg_embedding/data/contract.py 的 ChannelHub 源码树"
        ),
        "wheels": wheels,
        "wheels_total_bytes": sum(w["size"] for w in wheels),
        "files": files,
        "verify_hint": (
            "核对方式：解压后对 files 里每个路径算 SHA-256，与本清单比对；"
            "wheels 同理。清单本身不含自己的 hash。"
        ),
    }


def _iter_files(root: Path):
    """遍历要打包的文件，跳过缓存与产物目录。"""
    for name in INCLUDE:
        p = root / name
        if not p.exists():
            print(f"  [跳过] {name} 不存在")
            continue
        if p.is_file():
            yield p, p.relative_to(root)
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for f in filenames:
                if f.endswith((".pyc", ".pyo")):
                    continue
                fp = Path(dirpath) / f
                yield fp, fp.relative_to(root)


def download_wheels(dest: Path, thin: bool, platform: str | None,
                    py_version: str | None) -> list[str]:
    """把依赖的 wheel 下到 dest。返回 pip 的告警（如果有）。"""
    dest.mkdir(parents=True, exist_ok=True)
    pkgs = list(RUNTIME_DEPS) + list(BUILD_DEPS)
    if not thin:
        pkgs += SCIENCE_DEPS
    cmd = [sys.executable, "-m", "pip", "download", "--dest", str(dest), *pkgs]
    if platform or py_version:
        # 跨平台下载时 pip 拒绝解析源码包，必须限定只要二进制
        cmd += ["--only-binary=:all:"]
        if platform:
            cmd += ["--platform", platform]
        if py_version:
            cmd += ["--python-version", py_version]
    print("  " + " ".join(cmd[:6]) + " ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:], file=sys.stderr)
        raise SystemExit("pip download 失败，见上面输出")
    return [ln for ln in r.stdout.splitlines() if "WARNING" in ln]


def main() -> None:
    ap = argparse.ArgumentParser(description="打 superran 离线安装包")
    ap.add_argument("--thin", action="store_true",
                    help="轻量包（约 16 MB）：不含 numpy/scipy。**要求目标机器已有科学计算栈**，"
                         "全新 venv 里装不上。默认打完整包")
    ap.add_argument("--no-wheels", action="store_true", help="不打依赖，只打源码")
    ap.add_argument("--include-channelhub", metavar="PATH", default=None,
                    help="把 ChannelHub 源码一起打进去。**确认自己有权分发再用**"
                         "——该仓库没有开源许可证")
    ap.add_argument("--platform", default=None,
                    help="目标平台的 wheel tag，如 win_amd64 / manylinux2014_x86_64")
    ap.add_argument("--python-version", default=None, help="目标 Python 版本，如 3.11")
    ap.add_argument("--out", default=None, help="输出的 zip 路径")
    args = ap.parse_args()

    tag_plat = args.platform or ("win_amd64" if sys.platform == "win32" else sys.platform)
    tag_py = args.python_version or f"{sys.version_info.major}.{sys.version_info.minor}"
    # 包型写进文件名。thin 包不是自包含的，名字里不说清会被当成 full 用，
    # 然后在目标机上撞一个看不懂的构建错误。
    kind = "nodeps" if args.no_wheels else ("thin" if args.thin else "full")
    out = Path(args.out) if args.out else (
        REPO / "dist" / f"superran-offline-{kind}-{tag_plat}-py{tag_py}.zip"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    stage = REPO / "dist" / "_stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    print(f"打包 superran → {out.name}")

    # 1. 依赖
    warnings: list[str] = []
    if args.no_wheels:
        print("[1/5] 跳过依赖（--no-wheels）")
    else:
        which = "运行时 + 构建" if args.thin else "运行时 + 构建 + numpy/scipy"
        print(f"[1/5] 下载 {which} 依赖的 wheel …")
        warnings = download_wheels(stage / "wheels", args.thin,
                                   args.platform, args.python_version)
        n = len(list((stage / "wheels").glob("*")))
        sz = sum(f.stat().st_size for f in (stage / "wheels").glob("*")) / 1e6
        print(f"       {n} 个文件，{sz:.1f} MB")

    # 2. ChannelHub（默认不打）
    if args.include_channelhub:
        chroot = Path(args.include_channelhub)
        marker = chroot / "src" / "msg_embedding" / "data" / "contract.py"
        if not marker.is_file():
            raise SystemExit(f"[2/5] {chroot} 看起来不是 ChannelHub（缺 {marker}）")
        print(f"[2/5] 打包 ChannelHub：{chroot}")
        print("       !! 该仓库没有 LICENSE 文件，默认保留所有权利。")
        print("       !! 你已用 --include-channelhub 声明自己有权分发。")
        for sub in ("src", "configs"):
            s = chroot / sub
            if s.exists():
                shutil.copytree(s, stage / "ChannelHub_main" / sub,
                                ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, "*.pyc"))
    else:
        print("[2/5] 不打包 ChannelHub（无开源许可证）——安装文档会指引用户自备")

    # 3. 源码 + 说明
    print("[3/5] 收集源码与文档 …")
    n_src = 0
    for src, rel in _iter_files(REPO):
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n_src += 1
    kind_note = {
        "full": (
            "本包为【完整包】：wheels/ 里含 numpy、scipy 与构建后端，\n"
            "   在**全新 venv** 里也能全程离线装上。"
        ),
        "thin": (
            "本包为【轻量包】：wheels/ 里**不含 numpy、scipy**。\n"
            "   要求目标机器的 Python 环境已经有这两个包，否则装不上。\n"
            "   纯净环境请改用完整包（打包时不加 --thin）。"
        ),
        "nodeps": (
            "本包【不含任何依赖 wheel】：需要目标机器能联网 pip，或另行准备轮子。"
        ),
    }[kind]
    (stage / "开始安装.txt").write_text(
        READ_ME_FIRST.replace("{KIND_NOTE}", kind_note), encoding="utf-8"
    )
    print(f"       {n_src} 个源码/文档文件")

    # 4. manifest —— 让接收方能核对包里的东西没被改过
    print("[4/5] 写 bundle-manifest.json …")
    manifest = _build_manifest(stage, kind, tag_plat, tag_py, args)
    (stage / "bundle-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"       记录 {len(manifest['wheels'])} 个 wheel、{len(manifest['files'])} 个关键文件的 SHA-256")

    # 5. 压缩
    print("[5/5] 压缩 …")
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirnames, filenames in os.walk(stage):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for f in filenames:
                fp = Path(dirpath) / f
                z.write(fp, fp.relative_to(stage))
    shutil.rmtree(stage)

    print()
    print(f"  产出   {out}")
    print(f"  体积   {out.stat().st_size / 1e6:.1f} MB")
    print(f"  适用   {tag_plat} / Python {tag_py}")
    if warnings:
        print("  pip 告警：")
        for w in warnings[:5]:
            print("   ", w.strip())
    if not args.include_channelhub:
        print()
        print("  提醒：包里没有 ChannelHub。接收方需要自备一份含")
        print("        src/msg_embedding/data/contract.py 的源码树。")


if __name__ == "__main__":
    main()

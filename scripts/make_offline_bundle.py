"""打一个离线安装包，给不能联网的机器用。

产出 ``dist/superwireless-offline-<平台>-<pyver>.zip``，里面有：

* superwireless 全部源码、skill、测试、文档
* ``wheels/`` —— 依赖的 wheel 文件，装的时候 ``pip install --no-index --find-links wheels``
* ``INSTALL_AGENT.md`` —— 写给 AI agent 看的安装说明，用户把它丢给自己的 agent 就行
* ``开始安装.txt`` —— 给人看的一句话说明

**wheel 是平台相关的。** numpy/scipy/pydantic-core 都有编译好的二进制，
在 Windows 上下的包拿到 Linux 上装不了。所以文件名里带了平台和 Python 版本，
**必须在与目标机器同平台、同 Python 大版本的机器上打包**。跨平台用
``--platform`` / ``--python-version`` 让 pip 下别的平台的轮子（只对纯二进制
wheel 有效，且必须配 ``--only-binary=:all:``）。

用法::

    python scripts/make_offline_bundle.py                    # 轻量依赖（15 MB）
    python scripts/make_offline_bundle.py --with-numpy       # 带 numpy/scipy（62 MB）
    python scripts/make_offline_bundle.py --no-wheels        # 只打源码（2 MB）

ChannelHub **默认不打包** —— 它没有开源许可证，默认保留所有权利，转发有法律风险。
确认自己有权分发时（公司内部本来就有这份代码、或已获授权）用
``--include-channelhub`` 打进去。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 打进包里的源码。artifacts/ 是生成物，.git 是历史，都不要。
INCLUDE = [
    "src", "scripts", "skills", "presets", "tests",
    "pyproject.toml", "README.md", "LICENSE",
    "INSTALL_AGENT.md", "SETUP.html", "CAPABILITIES.html", "SHOWCASE.html",
    "CLAUDE.md",
]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git", "artifacts"}

LIGHT_DEPS = ["mcp>=1.0", "pydantic>=2.0", "pyyaml>=6.0", "structlog>=23.0"]
HEAVY_DEPS = ["numpy>=1.24", "scipy>=1.10"]

READ_ME_FIRST = """superwireless 离线安装包
========================

不用自己照着敲命令。把下面这句话发给你的 AI coding agent
（Claude Code / Codex / 任何能读文件+跑命令的 agent），它会自己装完并验证：

    这个目录里是 superwireless 离线安装包，读 INSTALL_AGENT.md 按里面的步骤
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
   放别处就设环境变量 SUPERWIRELESS_CHANNELHUB 指过去。

--------------------------------------------------------------------
包里有什么
--------------------------------------------------------------------

  INSTALL_AGENT.md   给 agent 看的安装说明（第一优先）
  SETUP.html         给人看的：由哪几块拼成、三种用法、排错
  CAPABILITIES.html  能产生哪些信道、能拿到哪些观察量
  SHOWCASE.html      实测演示与踩过的坑
  README.md          项目说明
  wheels/            依赖的 wheel（离线 pip 安装用；--no-wheels 打包时没有）
  src/ scripts/ skills/ presets/ tests/

--------------------------------------------------------------------
真要手动装的话，三条命令
--------------------------------------------------------------------

  <你的python> -m pip install --no-index --find-links wheels -e .
  <你的agent CLI> mcp add superwireless -- <python绝对路径> <本目录绝对路径>/scripts/mcp_server.py
  cp -r skills/channel-sim ~/.claude/skills/

验证： <你的python> tests/test_e2e.py
"""


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


def download_wheels(dest: Path, with_numpy: bool, platform: str | None,
                    py_version: str | None) -> list[str]:
    """把依赖的 wheel 下到 dest。返回 pip 的告警（如果有）。"""
    dest.mkdir(parents=True, exist_ok=True)
    pkgs = LIGHT_DEPS + (HEAVY_DEPS if with_numpy else [])
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
    ap = argparse.ArgumentParser(description="打 superwireless 离线安装包")
    ap.add_argument("--with-numpy", action="store_true",
                    help="把 numpy/scipy 也打进去（+47 MB）。目标机器已有就不用")
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
    out = Path(args.out) if args.out else (
        REPO / "dist" / f"superwireless-offline-{tag_plat}-py{tag_py}.zip"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    stage = REPO / "dist" / "_stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    print(f"打包 superwireless → {out.name}")

    # 1. 依赖
    warnings: list[str] = []
    if args.no_wheels:
        print("[1/4] 跳过依赖（--no-wheels）")
    else:
        which = "轻量依赖 + numpy/scipy" if args.with_numpy else "轻量依赖"
        print(f"[1/4] 下载 {which} 的 wheel …")
        warnings = download_wheels(stage / "wheels", args.with_numpy,
                                   args.platform, args.python_version)
        n = len(list((stage / "wheels").glob("*")))
        sz = sum(f.stat().st_size for f in (stage / "wheels").glob("*")) / 1e6
        print(f"       {n} 个文件，{sz:.1f} MB")

    # 2. ChannelHub（默认不打）
    if args.include_channelhub:
        chroot = Path(args.include_channelhub)
        marker = chroot / "src" / "msg_embedding" / "data" / "contract.py"
        if not marker.is_file():
            raise SystemExit(f"[2/4] {chroot} 看起来不是 ChannelHub（缺 {marker}）")
        print(f"[2/4] 打包 ChannelHub：{chroot}")
        print("       !! 该仓库没有 LICENSE 文件，默认保留所有权利。")
        print("       !! 你已用 --include-channelhub 声明自己有权分发。")
        for sub in ("src", "configs"):
            s = chroot / sub
            if s.exists():
                shutil.copytree(s, stage / "ChannelHub_main" / sub,
                                ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, "*.pyc"))
    else:
        print("[2/4] 不打包 ChannelHub（无开源许可证）——安装文档会指引用户自备")

    # 3. 源码 + 说明
    print("[3/4] 收集源码与文档 …")
    n_src = 0
    for src, rel in _iter_files(REPO):
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n_src += 1
    (stage / "开始安装.txt").write_text(READ_ME_FIRST, encoding="utf-8")
    print(f"       {n_src} 个源码/文档文件")

    # 4. 压缩
    print("[4/4] 压缩 …")
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

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""版本号抬升 —— 由合并入口 merge_task.py 调用，工作位不要手动跑。

为什么必须在合并那一刻做：
    版本号写在两处（pyproject.toml 与 src/superran/__init__.py），这两行是
    **所有并行分支都要改的同两行**。分支自己无从知道它会是第几个合进 develop 的，
    那个信息只在合并那一刻才存在；合并本身由 merge_task.py 的锁串行化，
    所以把抬版本放在那里，并行分支之间的版本号冲突就不会发生。

为什么按行定点替换而不是解析后重写：
    只改版本号那一行，其余字节原样保留，合并提交里的版本改动一眼可审。
    认不出目标行（没有或多于一处）就报错退出，不猜。

用法：
    python scripts/bump_version.py              # patch +1（两处同步）
    python scripts/bump_version.py --set 0.2.0  # 指定版本；minor/major 须维护者同意
    python scripts/bump_version.py --print      # 只打印当前版本，不改文件
    python scripts/bump_version.py --check      # 只校验两处一致，不一致退出 1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# (相对路径, 匹配整行的正则)。正则的 group(2) 是版本号本身。
# pyproject 只认 [project] 表里顶格写的 `version = "x.y.z"`；
# ruff 的 `target-version` 因为有前缀不会被误中。
TARGETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pyproject.toml", re.compile(r'^(version\s*=\s*")([^"]*)(")([ \t\r]*)$', re.M)),
    ("src/superran/__init__.py", re.compile(r'^(__version__\s*=\s*")([^"]*)(")([ \t\r]*)$', re.M)),
)


class VersionError(RuntimeError):
    pass


def _read(root: Path, rel: str) -> str:
    path = root / rel
    if not path.is_file():
        raise VersionError(f"找不到版本文件：{rel}")
    # newline="" 保留原始换行（CRLF/LF），写回时字节不变。
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _match_one(text: str, pattern: re.Pattern[str], rel: str) -> re.Match[str]:
    found = list(pattern.finditer(text))
    if len(found) != 1:
        raise VersionError(f"{rel} 里的版本行应恰好 1 处，实际 {len(found)} 处；拒绝猜测改写。")
    return found[0]


def read_versions(root: Path = ROOT) -> dict[str, str]:
    """返回 {文件: 版本号}；任何一处认不出都报错。"""
    return {rel: _match_one(_read(root, rel), pat, rel).group(2) for rel, pat in TARGETS}


def current_version(root: Path = ROOT) -> str:
    """两处必须一致且是 x.y.z；否则报错，不替人选一个。"""
    versions = read_versions(root)
    distinct = set(versions.values())
    if len(distinct) != 1:
        detail = "；".join(f"{k}={v}" for k, v in versions.items())
        raise VersionError(f"两处版本号不一致：{detail}")
    value = distinct.pop()
    if not SEMVER.match(value):
        raise VersionError(f"版本号不是 x.y.z 形式：{value!r}")
    return value


def next_patch(version: str) -> str:
    m = SEMVER.match(version)
    if not m:
        raise VersionError(f"版本号不是 x.y.z 形式：{version!r}")
    major, minor, patch = (int(g) for g in m.groups())
    return f"{major}.{minor}.{patch + 1}"


def write_version(new: str, root: Path = ROOT) -> None:
    if not SEMVER.match(new):
        raise VersionError(f"目标版本不是 x.y.z 形式：{new!r}")
    # 先全部算好再统一写，任何一处认不出都不落盘，避免只改一半。
    staged: list[tuple[Path, str]] = []
    for rel, pat in TARGETS:
        text = _read(root, rel)
        m = _match_one(text, pat, rel)
        replaced = text[:m.start()] + m.group(1) + new + m.group(3) + m.group(4) + text[m.end():]
        staged.append((root / rel, replaced))
    for path, text in staged:
        with path.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description="同步抬升 pyproject.toml 与 superran.__version__")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--set", dest="set_to", metavar="X.Y.Z",
                       help="指定版本（minor/major 抬升须维护者同意）")
    group.add_argument("--print", dest="print_only", action="store_true", help="只打印当前版本")
    group.add_argument("--check", action="store_true", help="只校验两处一致")
    args = ap.parse_args(argv)
    try:
        old = current_version(root)
        if args.print_only or args.check:
            print(old if args.print_only else f"版本一致：{old}")
            return 0
        new = args.set_to if args.set_to else next_patch(old)
        if new == old:
            raise VersionError(f"目标版本与当前相同：{old}")
        write_version(new, root)
        if current_version(root) != new:
            raise VersionError("写回后读回的版本与目标不一致")
    except VersionError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"版本号 {old} → {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

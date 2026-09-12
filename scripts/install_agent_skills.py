"""Install/check repository-owned handbooks; retain backups and never follow links.

The simulation role derives the explicit-name superran alias from channel-sim.
Legacy member/lead roles remain available solely for archived workflows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLE_SKILLS = {
    "simulation": ("channel-sim", "superran"),
    "member": ("channel-sim", "superran-member-task"),
    "lead": ("channel-sim", "superran-member-task", "superran-lead"),
}
ALIAS_HEADER = '''---
name: superran
description: >
  SuperRAN 专用的无线信道仿真与实验编排。仅当用户在当前请求中显式写出
  “SuperRAN”（不区分大小写），或明确点名 $superran / “SuperRAN skill” 时使用。
  不得由通用无线通信主题、历史对话、工作区路径或 MCP 工具名自动触发。
---'''


def _is_link(path: Path) -> bool:
    if not os.path.lexists(path):
        return False
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _plain_parents(path: Path) -> None:
    for part in (path, *path.parents):
        if _is_link(part):
            raise ValueError(f"linked parent is not an install root: {part}")


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    if _is_link(root):
        raise ValueError(f"linked skill: {root}")
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            if _is_link(Path(current) / name):
                raise ValueError(f"link inside skill: {Path(current) / name}")
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _git_head() -> str | None:
    proc = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                          text=True, capture_output=True, encoding="utf-8", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _stage(name: str, stage: Path) -> Path:
    source = ROOT / "skills" / ("channel-sim" if name == "superran" else name)
    if not (source / "SKILL.md").is_file():
        raise ValueError(f"missing repository skill: {source}")
    _tree_sha256(source)  # Reject links before copytree can follow them.
    target = stage / name
    shutil.copytree(source, target)
    if name == "superran":
        text = (target / "SKILL.md").read_text(encoding="utf-8")
        sections = text.split("---", 2)
        if len(sections) != 3 or sections[0].strip():
            raise ValueError("canonical skill has no YAML frontmatter")
        (target / "SKILL.md").write_text(ALIAS_HEADER + sections[2], encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", default="simulation", choices=tuple(ROLE_SKILLS))
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--codex-home", help="Compatibility: install into HOME/skills")
    location.add_argument("--skills-root", help="Explicit skills directory for any Agent")
    parser.add_argument("--check", action="store_true", help="Report drift; do not install")
    parser.add_argument("--backup-root", help="Backups outside discoverable skills directories")
    parser.add_argument("--replace-links", action="store_true",
                        help="Rename a top-level skill link into backup; never edit its target")
    args = parser.parse_args()
    home = Path(args.codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().absolute()
    destination_root = Path(args.skills_root).expanduser().absolute() if args.skills_root else home / "skills"
    _plain_parents(destination_root)
    backup_root = Path(args.backup_root).expanduser().absolute() if args.backup_root else destination_root.parent / "superran-skill-backups"
    _plain_parents(backup_root)
    # Keep archived SKILL.md files outside skill discovery, including normalized '..'.
    if backup_root.resolve().is_relative_to(destination_root.resolve()):
        raise ValueError("backup root must be outside the skills root")
    installed = []
    changed = []
    scratch = ROOT / "artifacts" / "skill-install-staging"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as temp_dir:
        stage = Path(temp_dir)
        plans = []
        for name in ROLE_SKILLS[args.role]:
            source = _stage(name, stage)
            expected = _tree_sha256(source)
            destination = destination_root / name
            actual = None
            if os.path.lexists(destination):
                if _is_link(destination):
                    if not args.check and not args.replace_links:
                        raise ValueError(f"linked skill requires --replace-links: {destination}")
                elif destination.is_dir():
                    actual = _tree_sha256(destination)
                else:
                    raise ValueError(f"skill destination is not a directory: {destination}")
            plans.append((source, destination, expected, actual))
        if not args.check:
            destination_root.mkdir(parents=True, exist_ok=True)
        try:
            for source, destination, expected, actual in plans:
                row = {"name": destination.name, "path": str(destination),
                       "sha256": expected, "actual_sha256": actual, "matches": actual == expected}
                if not args.check and actual != expected:
                    backup_root.mkdir(parents=True, exist_ok=True)
                    backup_dir = Path(tempfile.mkdtemp(prefix=destination.name + "-", dir=backup_root))
                    previous = backup_dir / destination.name
                    existed = os.path.lexists(destination)
                    if existed:
                        os.replace(destination, previous)  # Rename entry; junction target untouched.
                    changed.append((destination, previous, existed))
                    shutil.copytree(source, destination)
                    if _tree_sha256(destination) != expected:
                        raise RuntimeError(f"copy verification failed: {destination.name}")
                    row.update(actual_sha256=expected, matches=True, backup=str(previous) if existed else None)
                installed.append(row)
            payload = {"status": "pass" if all(r["matches"] for r in installed) else "drift",
                       "role": args.role, "repository": str(ROOT), "repository_head": _git_head(),
                       "codex_home": str(home) if not args.skills_root else None,
                       "skills_root": str(destination_root), "check_only": args.check,
                       "installed": installed}
            if not args.check:
                manifest = destination_root.parent / "superran-team-skills.json"
                _write_json_atomic(manifest, payload)
                payload["manifest"] = str(manifest)
        except BaseException:
            for destination, previous, existed in reversed(changed):
                if os.path.lexists(destination):
                    # Only our newly installed, verified plain directory is removed.
                    _plain_parents(destination)
                    _tree_sha256(destination)
                    shutil.rmtree(destination)
                if existed:
                    os.replace(previous, destination)
            raise
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

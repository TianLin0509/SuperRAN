"""Install the repository-owned SuperRAN skills into one Codex home.

The repository remains the versioned source.  This installer is intentionally small
and idempotent: it updates known files without touching unrelated skills.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLE_SKILLS = {
    "member": ("channel-sim", "superran-member-task"),
    "lead": ("channel-sim", "superran-member-task", "superran-lead"),
}


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_head() -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        text=True, capture_output=True, encoding="utf-8", errors="strict", check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=tuple(ROLE_SKILLS))
    parser.add_argument(
        "--codex-home",
        help="Codex home for verification or non-default installs; defaults to CODEX_HOME or ~/.codex",
    )
    args = parser.parse_args()

    codex_home = Path(
        args.codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    ).expanduser().resolve()
    destination_root = codex_home / "skills"
    destination_root.mkdir(parents=True, exist_ok=True)

    installed: list[dict[str, str]] = []
    for name in ROLE_SKILLS[args.role]:
        source = ROOT / "skills" / name
        if not (source / "SKILL.md").is_file():
            raise SystemExit(f"missing repository skill: {source}")
        destination = destination_root / name
        shutil.copytree(source, destination, dirs_exist_ok=True)
        source_hash = _tree_sha256(source)
        installed_hash = _tree_sha256(destination)
        if source_hash != installed_hash:
            raise SystemExit(f"skill copy verification failed: {name}")
        installed.append({
            "name": name,
            "path": str(destination),
            "sha256": installed_hash,
        })

    payload: dict[str, object] = {
        "status": "pass",
        "role": args.role,
        "repository": str(ROOT),
        "repository_head": _git_head(),
        "codex_home": str(codex_home),
        "installed": installed,
    }
    manifest = codex_home / "superran-team-skills.json"
    _write_json_atomic(manifest, payload)
    payload["manifest"] = str(manifest)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

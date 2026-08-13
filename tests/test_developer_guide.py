"""Self-contained developer-guide coverage and drift contract."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "index.html"
GENERATOR = ROOT / "scripts" / "make_developer_guide.py"


def _html() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _meta(text: str) -> dict:
    match = re.search(r"window\.__DOC_META__=(\{.*?\});", text)
    assert match, "missing embedded documentation manifest"
    return json.loads(match.group(1))


def test_developer_guide_is_rebuilt_from_current_tree() -> None:
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_guide_is_offline_utf8_hash_routed_and_accessible() -> None:
    text = _html()
    assert text.startswith('<!doctype html>\n<html lang="zh-CN"')
    assert text.endswith("</html>\n")
    assert "�" not in text
    assert "SuperRAN 开发者文档" in text
    assert "同站扇区共享状态与 cluster seed" in text
    assert "14 symbol 先完成估计，再取中间 symbol 为 1 slot snapshot" in text
    assert "r=p·128+h·8+v" in text
    assert "F: 1536 × 256" in text
    assert "FᴴF=I₂₅₆" in text
    assert "top-to-bottom 只是编号，不能翻转物理下倾" in text
    assert "search-panel" in text and "#/experience" in text
    assert "prefers-reduced-motion" in text and "@media print" in text
    assert '<script src=' not in text and '<link rel="stylesheet"' not in text
    assert text.count('<figure class="diagram">') >= 10
    assert text.count('class="kx"') >= 20
    assert text.count('<article class="doc-page"') >= 25


def test_every_module_public_symbol_tool_test_skill_and_preset_is_carried() -> None:
    text = _html()
    meta = _meta(text)

    module_paths = sorted((ROOT / "src" / "superran").glob("*.py"))
    public_symbols: list[str] = []
    for path in module_paths:
        assert f'data-module="{path.stem}"' in text
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and not node.name.startswith("_"):
                public_symbols.append(node.name)
                assert f'<code>{node.name}</code>' in text

    server_tree = ast.parse(
        (ROOT / "src" / "superran" / "server.py").read_text(encoding="utf-8")
    )
    tools = [
        node.name for node in server_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("sr_")
    ]
    for name in tools:
        assert f'<code>{name}</code>' in text

    test_files = sorted((ROOT / "tests").glob("test_*.py"))
    for path in test_files:
        assert f'<code>{path.name}</code>' in text

    skill_files = [ROOT / "skills" / "channel-sim" / "SKILL.md"] + sorted(
        (ROOT / "skills" / "channel-sim" / "references").glob("*.md")
    )
    for path in skill_files:
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        assert f'<code>{rel}</code>' in text

    preset_count = 0
    for rel in ("presets/presets.yaml", "presets/system_presets.yaml"):
        data = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8")) or {}
        preset_count += len(data)
        for name in data:
            assert f'<code>{name}</code>' in text

    assert meta["modules"] == len(module_paths)
    assert meta["public_symbols"] == len(public_symbols)
    assert meta["mcp_tools"] == len(tools) == 34
    assert meta["test_files"] == len(test_files)
    assert meta["skill_files"] == len(skill_files)
    assert preset_count > 0

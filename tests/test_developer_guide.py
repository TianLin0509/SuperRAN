"""Self-contained developer-guide coverage and drift contract."""

from __future__ import annotations

import ast
import importlib.util
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


def _pages(text: str) -> list[dict]:
    match = re.search(r"window\.__DOC_PAGES__=(\[.*?\]);window\.__DOC_META__", text)
    assert match, "missing embedded documentation page manifest"
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
    assert 'data-page="pdp"' in text
    assert 'data-page="csi"' in text
    assert 'data-page="pmi"' in text
    assert 'data-page="bfgain"' in text
    assert 'data-page="powercontrol"' in text
    assert 'data-page="robust"' in text
    assert 'data-page="calibration"' in text
    assert 'data-page="agentloop"' in text
    assert 'data-page="raytracing"' in text
    assert 'data-page="referencesignals"' in text
    assert 'data-page="bler"' in text
    assert 'data-page="externalresults"' in text
    assert "RB 中心采样的硬边界" in text
    assert "鲁棒 RZF 与每天线功率约束是两个独立设计轴" in text
    assert "Type-I-style 不等于完整 38.214 Type-I" in text
    assert "PMIResult.rank 不是标准 RI 决策" in text
    assert "h_true 不能进当次 BF Gain" in text
    assert "CSI-RS DFT 波束码本不等于 PMI Type-I-style 列集合" in text
    assert "四个常被混称为“功控”的自由度" in text
    assert "体验 pair table 目前固定 equal" in text
    assert "每小区 Σq=N_RB" in text
    assert "校准、验证和算法统计不是一回事" in text
    assert "当前分类器是确定性关键词路由，不是 LLM 语义理解" in text
    assert "channel_generation_mode" in text and "tdl_fallback" in text
    assert "当前预置表系统路径采用前文明确的“RBG 内线性、跨 RBG" in text
    assert "预置表口径：一次 TTI 的 TB 就是一次 BLER 事件" in text
    assert "当前是单码字通用 TB-BLER 抽象，不展开 RE/TBS/CB" in text
    assert "只允许一次重传：默认 IR，可选 CC" in text
    assert "等效 MCS 只改 BLER 查表，不改空口发送参数" in text
    assert "BLER：MCS 表、曲线与 HARQ 复现" in text
    assert 'data-bler-reimplementation="complete"' in text
    assert "完整预置 MCS Table 3：28 档逐行可复算" in text
    assert "不依赖 SuperRAN 的 NumPy 参考实现" in text
    assert "完整 1,824 点机器可复制 JSON" in text
    assert "PASS: preset_20b_256qam reproduced" in text
    assert "preset_20b_256qam 全部 56 条原始 NewTx/ReTx BLER 瀑布曲线" in text
    assert "当前预置表系统路径不使用" in text
    assert "QAM constrained MI" in text and "不使用" in text
    assert "company_20b_256qam" not in text
    assert ("公" + "司 BLER") not in text and ("公" + "司曲线") not in text
    assert 'data-bler-atlas' in text
    assert text.count('class="diagram bler-threshold-chart"') == 1
    assert text.count('class="diagram bler-curve-atlas"') == 1
    assert 'data-hello-world="overview-entry"' in text
    overview_article = re.search(
        r'<article class="doc-page" data-page="overview".*?</article>', text, re.S
    )
    assert overview_article is not None
    assert 'data-hello-world="overview-entry"' in overview_article.group(0)
    assert 'data-hello-world="srs-vs-pmi"' in text
    assert 'data-product-surface="spec"' in text
    assert 'data-product-surface="kpi"' in text
    assert 'data-kpi-workbench="standard-output"' in text
    assert "交互配置 Mock · 仿真说明书" in text
    assert "Agent 自适应 KPI 工作台" in text
    assert "26 项小区 KPI、24 项用户 KPI" in text
    assert "应用到仿真" in text
    assert 'python -u scripts\\run_srs_pmi_hello_world.py' in text
    assert "主实验点估计" in text and "+0.7%" in text
    assert "Wilcoxon p=0.846" in text
    assert "证据已写出，但不能宣称收益" in text
    assert 'method_a=&quot;svd&quot;' in text
    assert 'method_b=&quot;type1&quot;' in text
    assert 'csi_a=&quot;srs&quot;' in text
    assert 'csi_b=&quot;csirs&quot;' in text
    assert 'varies=[&quot;csi&quot;, &quot;method&quot;]' in text
    assert 'sr_generate(draft_id=draft[&quot;draft_id&quot;], num_samples=80)' in text
    assert 'data = sr_generate(preset=&quot;company_64t4r_multicell&quot;' not in text
    assert "统计检验无法发现样本错配" in text
    assert "当前没有未分类模块" in text
    assert "search-panel" in text and "#/experience" in text
    assert 'data-reading-mode="compact"' in text
    assert 'id="reading-toggle"' in text and 'data-reading-choice="detailed"' in text
    assert "prefers-reduced-motion" in text and "@media print" in text
    assert '<script src=' not in text and '<link rel="stylesheet"' not in text
    # 这一页的首要打开方式是 file:// 双击。某些浏览器设置下对 file:// 的
    # localStorage 访问会直接抛 SecurityError；只要有一处没包 try/catch，
    # 整段初始化脚本就会中断，搜索、导航、目录、阅读深度**全部失效**——
    # 而页面看起来只是"主题没跟随系统"。逐处检查而不是只看有没有 try。
    for _m in re.finditer(r"localStorage\.(?:get|set)Item", text):
        _ctx = text[max(0, _m.start() - 160):_m.start()]
        assert "try{" in _ctx, (
            "localStorage 访问必须包在 try/catch 里（file:// 下可能抛 "
            f"SecurityError）；未受保护的调用在 …{text[_m.start()-70:_m.start()+40]}"
        )
    assert text.count('<figure class="diagram">') >= 10
    assert text.count('class="kx"') >= 20
    assert text.count('<article class="doc-page"') >= 25


def test_every_chapter_has_two_reading_depths_and_every_formula_is_explained() -> None:
    text = _html()
    meta = _meta(text)
    pages = _pages(text)

    assert len(pages) == meta["logical_pages"] == meta["detailed_pages"] == 39
    assert meta["annotated_formulas"] == 87
    assert meta["detailed_module_exemptions"] == 3
    assert meta["detailed_module_coverage"] == (
        meta["modules"] - meta["detailed_module_exemptions"]
    )
    assert text.count('class="detail-content"') == len(pages)

    for page in pages:
        key = re.escape(page["key"])
        article = re.search(
            rf'<article class="doc-page" data-page="{key}".*?</article>',
            text,
            flags=re.S,
        )
        assert article, f"missing article for {page['key']}"
        fragment = article.group(0)
        assert f'data-detail-for="{page["key"]}"' in fragment
        assert page["detailed_chars"] > page["compact_chars"] >= 500
        if page["reading_kind"] == "chapter":
            # 带宽是防"详情段失控膨胀"的漂移哨兵，不是物理断言；第四轮审查
            # 给模块补了真实文档细节（新 API/注释）， raytracing 章实测 3.256，
            # 上限随之校准到 3.3。
            assert 1.8 <= page["detail_ratio"] <= 3.3, (
                page["key"], page["detail_ratio"]
            )
        else:
            assert page["reading_kind"] == "reference"

    cards = re.findall(
        r'<figure class="formula-card".*?</figure>', text, flags=re.S
    )
    assert cards
    assert len(cards) == text.count('class="kx"')
    for card in cards:
        assert 'class="formula-explain"' in card
        assert 'class="symbol-list"' in card
        assert card.count("<dt>") >= 3
        assert "<dd>" in card


def test_bler_manual_payload_and_independent_reference_are_executable(
        tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "_superran_make_developer_guide", GENERATOR)
    assert spec is not None and spec.loader is not None
    guide = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = guide
    sys.path.insert(0, str(GENERATOR.parent))
    try:
        spec.loader.exec_module(guide)
    finally:
        sys.path.pop(0)

    payload_text = guide.bler_reproduction_payload()
    payload = json.loads(payload_text)
    rows = payload["raw_mcs_curve_rows"]
    assert payload["schema"] == "superran.preset_bler_profile.v1"
    assert len(payload["mcs_profile"]) == len(rows) == 28
    assert sum(len(row[2][3]) + len(row[3][3]) for row in rows) == 1_824
    assert payload["data_sha256"] == (
        __import__("hashlib").sha256(
            json.dumps(rows, separators=(",", ":")).encode()
        ).hexdigest()
    )

    (tmp_path / "preset_20b_256qam.json").write_text(
        payload_text, encoding="utf-8")
    script = tmp_path / "reproduce_bler.py"
    script.write_text(guide.BLER_REFERENCE_IMPLEMENTATION, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path,
        text=True, capture_output=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS: preset_20b_256qam reproduced" in proc.stdout


def test_every_module_public_symbol_tool_test_skill_and_preset_is_carried() -> None:
    text = _html()
    meta = _meta(text)

    module_paths = sorted((ROOT / "src" / "superran").glob("*.py"))
    detailed_exemptions = {"__init__", "katex", "mathml"}
    public_symbols: list[str] = []
    for path in module_paths:
        assert f'data-module="{path.stem}"' in text
        if path.stem not in detailed_exemptions:
            rel = str(path.relative_to(ROOT)).replace("\\", "/")
            assert f'<code>{rel}</code>' in text, f"no detailed chapter source for {rel}"
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
    for name in detailed_exemptions:
        assert f'<code>{name}.py</code>' in text
    assert meta["public_symbols"] == len(public_symbols)
    assert meta["mcp_tools"] == len(tools) == 34
    assert meta["test_files"] == len(test_files)
    assert meta["skill_files"] == len(skill_files)
    assert preset_count > 0
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

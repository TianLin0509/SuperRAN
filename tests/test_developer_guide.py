"""Self-contained developer-guide coverage and drift contract."""

from __future__ import annotations

import ast
import html as html_lib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
GUIDE = ROOT / "docs" / "index.html"
GENERATOR = ROOT / "scripts" / "make_developer_guide.py"
DETAILS = ROOT / "scripts" / "developer_guide_details.py"


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
    assert "Agent 自适应编排" in text
    # 计数从代码算出来，别再手写——手写的数字上一轮就漂过。
    from superran import kpi_view as _kv  # noqa: PLC0415

    assert (f"{len(_kv.CELL_KPIS)} 项小区 KPI、"
            f"{len(_kv.USER_KPIS)} 项用户 KPI") in text
    # 下行 AMC 全链章节：四条信息面、rank 策略、反馈时序与解码位置都要在页面上。
    assert 'data-page="dlamc"' in text
    assert "下行 AMC 全链" in text
    assert 'data-formula="F_CQI_IIR"' in text
    assert 'data-formula="F_GRANT_SINR"' in text
    assert 'data-formula="F_RANK_SE"' in text
    assert 'data-formula="F_RANK_SWITCH"' in text
    assert 'data-formula="F_HARQ_DELAY"' in text
    dlamc_start = text.index('<article class="doc-page" data-page="dlamc"')
    dlamc_end = text.index('<article class="doc-page" data-page="bler"', dlamc_start)
    dlamc = text[dlamc_start:dlamc_end]
    for required in (
        "四条信息面，混一条就出错",
        "把预测面当真实面用，等于让基站预知波束打没打准",
        "关掉 OLLA 只去掉最后这一步叠加",
        "从累计平均换成一阶 IIR 是一次口径变更",
        "Rank 从哪来",
        "三重防乒乓",
        "自适应模式的常数还没有现场标定",
        "ACK/NACK 要等上行时隙",
        "解码 SINR 只在实际授予的 RBG 上取",
        "小包用全带均值判误块，两个方向都会错",
        "当前不建模的东西",
        "15.1016",       # 端到端手算例子的中间量
        "amc_policy.py",
    ):
        assert required in dlamc, required
    assert dlamc.index("四条信息面，混一条就出错") < dlamc.index("当前不建模的东西")
    assert "应用到仿真" in text
    assert "多算法 KPI 对比与单 TTI 复盘" in text
    assert "sr_compare_system_results" in text
    assert "Holm step-down" in text
    assert "决策引擎、交互配置工作台与说明书闭环" in text
    assert text.count('data-real-ui-screenshot="true"') >= 6
    for name in (
        "spec-workbench-overview.png", "spec-workbench-config.png",
        "kpi-workbench-cell.png", "kpi-workbench-user.png",
        "kpi-workbench-comparison.png", "kpi-workbench-tti-drilldown.png",
    ):
        assert f'data-source="{name}"' in text
        raw = (ROOT / "docs" / "assets" / "ui" / name).read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) > 20_000
    assert text.count("data:image/png;base64,") >= 6
    assert "JSON/CSV 下载、摘要复制、页面截图、系统分享与打印/PDF" in text
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
    assert "共享 bitmap 修复后，旧 50% MU 证据只保留作历史 provenance" in text
    assert "50.77%" not in text and "3.3%～12.7%" not in text
    assert "SRS 请求/生效周期" in text
    assert "单小区资源池身份" in text
    assert "频选全带/剩余池审计" in text
    assert "SU 清空全部可服务队列" in text
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

    assert len(pages) == meta["logical_pages"] == meta["detailed_pages"]
    assert {"srsallocation", "schedulerp0"}.issubset(
        {page["key"] for page in pages})
    rendered_formulas = set(re.findall(r'data-formula="([^"]+)"', text))
    assert meta["annotated_formulas"] == len(rendered_formulas)
    assert 'data-formula="F_SRS_2T4R_STITCH"' in text
    srs_lag_card = re.search(
        r'<figure class="formula-card" data-formula="F_SRS_LAG".*?</figure>',
        text, re.S,
    )
    assert srs_lag_card is not None
    assert "u+D_{\\mathrm{proc}}\\le t" in srs_lag_card.group(0)
    assert "t-t^{\\star}_{m,b}(t)" in srs_lag_card.group(0)
    assert "t-t_{\\mathrm{last\\ SRS},b}-D" not in srs_lag_card.group(0)
    assert 'data-formula="F_SRS_RESOURCE_COLLISION"' in text
    assert 'data-formula="F_SRS_TOY_CONTAMINATION"' in text
    assert 'data-formula="F_SRS_TOY_BEAM"' in text
    assert 'data-formula="F_SRS_TOY_LINK_ADAPT"' in text
    assert 'data-formula="F_MU_CANDIDATE_SCORE"' in text
    # __init__ / katex / mathml / _lazy —— 四个纯基础设施模块由 API 图谱承载。
    assert meta["detailed_module_exemptions"] == 4
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
        assert fragment.count('class="worked-example"') == 1, (
            page["key"], "each compact/detailed chapter needs one concrete worked example"
        )
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


def test_detailed_examples_are_html_strings_not_tuple_repr() -> None:
    spec = importlib.util.spec_from_file_location(
        "_superran_developer_guide_details", DETAILS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    bad = {
        key: type(value.example).__name__
        for key, value in module.DETAIL_SPECS.items()
        if not isinstance(value.example, str)
    }
    assert bad == {}, f"detailed example rendered as Python repr: {bad}"


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
    # _lazy 是纯基础设施（模块懒加载代理），没有无线物理内容，与 katex/mathml 同类：
    # 由 API 图谱承载，不需要独立的详细章节。名单要与 make_developer_guide.py 的
    # DETAILED_MODULE_EXEMPTIONS 保持一致。
    detailed_exemptions = {"__init__", "katex", "mathml", "_lazy"}
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
    assert meta["mcp_tools"] == len(tools) == 35
    assert meta["test_files"] == len(test_files)
    assert meta["skill_files"] == len(skill_files)
    assert preset_count > 0

    install_text = (ROOT / "INSTALL_AGENT.md").read_text(encoding="utf-8")
    install_tool_count = re.search(r"# 期望：tools: (\d+)", install_text)
    assert install_tool_count and int(install_tool_count.group(1)) == len(tools)
    for role_skill in ("superran-member-task", "superran-lead", "channel-sim"):
        assert role_skill in install_text

    # Human-maintained entry documents must not lag the generator's actual scan.
    for rel in ("README.md", "CLAUDE.md"):
        entry = (ROOT / rel).read_text(encoding="utf-8")
        match = re.search(r"当前共 \*\*(\d+) 个可执行测试文件\*\*", entry)
        assert match and int(match.group(1)) == len(test_files), rel


def test_team_skill_installer_copies_role_scoped_verified_skills() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "install_agent_skills.py"),
             "--role", "lead", "--codex-home", temp_dir],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
            errors="strict", timeout=30, check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["status"] == "pass"
        assert {row["name"] for row in payload["installed"]} == {
            "channel-sim", "superran-member-task", "superran-lead"}
        for row in payload["installed"]:
            assert (Path(row["path"]) / "SKILL.md").is_file()
            assert len(row["sha256"]) == 64
        assert Path(payload["manifest"]).is_file()

    test_files = sorted((ROOT / "tests").glob("test_*.py"))
    matrix_source = (ROOT / "scripts" / "run_test_matrix.py").read_text(
        encoding="utf-8")
    matrix_tree = ast.parse(matrix_source)
    catalogued: list[str] = []
    for node in matrix_tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if set(names) & {"QUICK", "PHYSICS"} and isinstance(node.value, ast.Tuple):
                catalogued.extend(
                    value.value for value in node.value.elts
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                )
    assert sorted(catalogued) == [path.name for path in test_files]


def test_member_and_lead_pages_keep_role_install_and_rehearsal_boundaries() -> None:
    member_html = (ROOT / "docs" / "team" / "member-start.html").read_text(
        encoding="utf-8", errors="strict")
    lead_html = (ROOT / "docs" / "team" / "lead-start.html").read_text(
        encoding="utf-8", errors="strict")
    assert member_html.startswith("<!doctype html>\n")
    assert lead_html.startswith("<!doctype html>\n")

    member_match = re.search(
        r'<pre id="member-prompt">(.*?)</pre>', member_html, re.S)
    lead_match = re.search(r'<pre id="lead-prompt">(.*?)</pre>', lead_html, re.S)
    assert member_match and lead_match
    member_prompt = html_lib.unescape(member_match.group(1))
    lead_prompt = html_lib.unescape(lead_match.group(1))

    for required in (
        "upstream/develop", "--role member", "probe_source_contract",
        "superran-member-task/SKILL.md",
        "channel-sim/SKILL.md", "35 个工具", "[REHEARSAL]", "不得代替组长"):
        assert required in member_prompt
    assert "--role lead" not in member_prompt
    assert "同意合并 PR" not in member_prompt
    assert not re.search(r"\b[0-9a-f]{40}\b", member_prompt), \
        "member bootstrap must follow current develop, not a stale pinned SHA"

    for required in (
        "--role lead", "probe_source_contract", "superran-lead/SKILL.md", "channel-sim/SKILL.md",
        "同意合并 PR #N，HEAD <完整 SHA>", "[REHEARSAL]", "永远不得合并",
        "full regression", "35 个工具"):
        assert required in lead_prompt
    assert 'href="member-start.html"' in lead_html

    workflow = json.loads(
        (ROOT / "docs" / "team" / "workflow.json").read_text(encoding="utf-8"))
    assert workflow["development_branch"] == "develop"
    assert workflow["release_branch"] == "main"
    assert workflow["merge_method"] == "squash"
    assert workflow["mcp_tool_count"] == 35
    assert workflow["rehearsal_merge_allowed"] is False
    for key in ("member_skill", "lead_skill", "simulation_skill"):
        assert (ROOT / workflow[key]).is_file(), key

    validator = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_team_contract.py")],
        cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
        errors="strict", timeout=30, check=False,
    )
    assert validator.returncode == 0, validator.stdout + validator.stderr
    assert json.loads(validator.stdout)["status"] == "pass"


def test_public_docs_and_source_do_not_expose_restricted_provenance_labels() -> None:
    roots = [
        ROOT / "src" / "superran",
        ROOT / "scripts",
        ROOT / "skills",
        ROOT / "presets",
    ]
    files = [
        ROOT / "README.md", ROOT / "INSTALL_AGENT.md",
        ROOT / "docs" / "index.html",
    ]
    files.extend(ROOT.glob("*.html"))
    for root in roots:
        files.extend(
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {
                ".py", ".md", ".yaml", ".yml", ".html"}
        )
    offenders: list[str] = []
    restricted_chinese = "\u516c\u53f8"
    restricted_ascii = "air" + "view"
    for path in files:
        content = path.read_text(encoding="utf-8")
        if restricted_chinese in content or restricted_ascii in content.casefold():
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"restricted provenance labels remain in: {offenders}"


def test_srs_allocation_chapter_is_algorithm_first_and_carries_the_pci_table() -> None:
    text = _html()
    start = text.index('<article class="doc-page" data-page="srsallocation"')
    end = text.index('<article class="doc-page" data-page="csi"', start)
    page = text[start:end]
    required = (
        "为什么必须分配 SRS 资源",
        "PCI 是 Physical Cell ID",
        "标准能力与工程预置必须分开",
        "当前 100 MHz 普通 H 资源的 PCI 模3表",
        "sym10 / C0",
        "sym13 / C1",
        "多个小区到底在哪些维度错开",
        "全局周期自适应：先保隔离，再换容量",
        "BBL专用叶子",
        "工程基线4 CS",
        "68个2T4R UE",
        "136个2T4R UE",
        "272个2T4R UE",
        "slot7→17",
        "Toy example：两个接收维度就能看见“方向被带偏”",
        "SRS资源分配影响预编码MCS与BLER的因果链",
        "基站估计的 SVD−PMI BF Gain",
        "发送 MCS 看估计，误块判断看真实接收 SINR",
        "双腿assignment → 两组逐RBG lag → 拼接陈旧64×4 h_prec",
        "assignment → 绝对RE → Y=HsXs+ΣHiXi+N → LS+时延窗",
        "raw SIR与post-despread SIR",
        "UL IoT=10log10((I+N)/N)",
        "波形H-hat → CSI老化 → 系统调度与BLER/KPI",
        "尚未自动接通",
        "当前不得给最终收益百分比",
        "开发者实现映射与反向测试（通信原理读者可跳过）",
    )
    assert all(item in page for item in required)
    assert page.index("为什么必须分配 SRS 资源") < page.index(
        "开发者实现映射与反向测试")
    # Four SRS opportunities each render the exact table-driven row, including
    # the two bold BBL leaves that must never enter the ordinary pool.
    expected_row = [0, 1, 2, "bbl", 1, 2, "bbl", 0]
    from superran import spec as specm  # noqa: PLC0415
    from superran import srs_resource as srsres  # noqa: PLC0415

    assert [
        srsres.srs_leaf_role(symbol, comb)
        for symbol in range(10, 14) for comb in (0, 1)
    ] == expected_row
    assert srsres.SRS_CYCLIC_SHIFT_COUNT == 4
    assert srsres.SrsResourceAllocator().capacity_ues(
        period_ms=10.0, n_ports=4) == 68
    # The toy BLER figures are rendered from the live preset curve, not stale prose.
    import math  # noqa: PLC0415

    from superran import bler_curves as bc  # noqa: PLC0415

    polluted_sinr = 15.0 + 10.0 * math.log10(0.8)
    curve = bc.get_curve(16, "newtx")
    clean_bler = float(curve.evaluate(15.0)[0])
    polluted_bler = float(curve.evaluate(polluted_sinr)[0])
    assert f"{polluted_sinr:.2f} dB" in page
    assert f"{curve.required_sinr_db(0.1):.4f} dB" in page
    assert f"{100 * clean_bler:.2f}%" in page
    assert f"{100 * polluted_bler:.2f}%" in page
    srs_control = next(row for row in specm._EDITABLE if row[0] == "srs_period_ms")
    assert srs_control[3] == [10.0, 20.0, 40.0]
    adaptive_control = next(
        row for row in specm._EDITABLE if row[0] == "srs_period_adaptive")
    assert adaptive_control[3] == ["on", "off"]


def test_time_noise_presinr_and_scene_asset_contracts_are_visible() -> None:
    text = _html()
    assert "PreSINR、底噪与UL IoT是三套可复算口径" in text
    assert "−126.23 dBm" in text
    assert "−115.44 dBm" in text
    assert "sample_interval_s独立落盘" in text
    assert "外部信道源即使把自己的隐式默认改成0.5 ms" in text
    assert "源资产/准备后资产双SHA-256" in text
    assert "radio_config_revision" in text
    assert "L0_geometry" in text and "L1_semantic" in text
    assert "能 import 不等于能被 SuperRAN 正确消费" in text
    assert "probe_source_contract()" in text
    assert 'data-module="srs_metrics"' in text
    assert 'data-module="scene_assets"' in text


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

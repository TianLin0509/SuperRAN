"""Validate the dependency-free SuperRAN team workflow contract."""
from __future__ import annotations

import ast
import hashlib
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8", errors="strict")


def _prompt(page: str, prompt_id: str) -> str:
    match = re.search(
        rf'<pre id="{re.escape(prompt_id)}">(.*?)</pre>', page, re.S)
    if not match:
        raise ValueError(f"missing prompt: {prompt_id}")
    return html.unescape(match.group(1))


def _skill_name(text: str) -> str | None:
    if not text.startswith("---\n"):
        return None
    frontmatter_end = text.find("\n---\n", 4)
    if frontmatter_end < 0:
        return None
    match = re.search(r"^name:\s*([a-z0-9-]+)\s*$", text[4:frontmatter_end], re.M)
    return match.group(1) if match else None


def validate() -> list[str]:
    errors: list[str] = []

    workflow = json.loads(_read("docs/team/workflow.json"))
    expected = {
        "schema_version": "1.3.0",
        "development_branch": "develop",
        "release_branch": "main",
        "merge_method": "squash",
        "mcp_tool_count": 35,
        "rehearsal_merge_allowed": False,
    }
    for key, value in expected.items():
        if workflow.get(key) != value:
            errors.append(f"workflow.{key}={workflow.get(key)!r}, expected {value!r}")

    expected_author_modes = {
        "identity_selects_mode": False,
        "formal": {
            "marker": "TEAM_MODE: FORMAL",
            "merge_candidate_allowed": True,
            "owner_push_strategy": "upstream_topic_branch",
            "non_owner_push_strategy": "fork_topic_branch",
        },
        "rehearsal": {
            "marker": "TEAM_MODE: REHEARSAL",
            "explicit_only": True,
            "merge_candidate_allowed": False,
        },
        "lead_authored_review": {
            "fresh_agent_session_required": True,
            "isolated_worktree_required": True,
            "author_session_may_review": False,
        },
    }
    if workflow.get("author_modes") != expected_author_modes:
        errors.append("workflow.author_modes drifted from the explicit role contract")

    for key in ("member_skill", "lead_skill", "simulation_skill"):
        path = ROOT / workflow.get(key, "")
        if not path.is_file():
            errors.append(f"missing workflow skill path: {key}={path}")

    for folder in ("channel-sim", "superran-member-task", "superran-lead"):
        relative = f"skills/{folder}/SKILL.md"
        text = _read(relative)
        if _skill_name(text) != folder:
            errors.append(f"skill name/folder mismatch: {relative}")
        if re.search(r"\bTODO\b|PLACEHOLDER|\[TODO", text, re.I):
            errors.append(f"unfinished scaffold: {relative}")
        for link in re.findall(r"\]\((references/[^)]+)\)", text):
            if not (ROOT / "skills" / folder / link).is_file():
                errors.append(f"broken skill reference: {folder}/{link}")

    member_page = _read("docs/team/member-start.html")
    lead_page = _read("docs/team/lead-start.html")
    if not member_page.startswith("<!doctype html>\n"):
        errors.append("member page is not canonical UTF-8 HTML")
    if not lead_page.startswith("<!doctype html>\n"):
        errors.append("lead page is not canonical UTF-8 HTML")
    for page_name, page in (("member", member_page), ("lead", lead_page)):
        if ".textContent" not in page or ".innerText" in page:
            errors.append(
                f"{page_name} page copy control may return empty text from collapsed prompts"
            )
    member_prompt = _prompt(member_page, "member-prompt")
    lead_prompt = _prompt(lead_page, "lead-prompt")
    rehearsal_prompt = _prompt(lead_page, "rehearsal-prompt")

    for item in (
        "TEAM_MODE: FORMAL", "upstream/develop", "--role member", "probe_source_contract",
        "superran-member-task/SKILL.md", "channel-sim/SKILL.md", "35 个工具",
        "普通组员，也可能是组长本人", "GitHub 身份只决定推送路径",
        "绝不能因为登录账号是 Owner 就改成 REHEARSAL", "author/TianLin0509",
        "schema_version 不低于 1.3.0", "author_modes.identity_selects_mode=false",
        "非 Draft", "[REHEARSAL]", "另一个全新组长 Agent Session",
        "技术术语", "白话解释", "具体例子",
        "我理解你要的是", "不等于", "证明了什么、没有证明什么",
        "不能当作实现或性能证据", "交互式改动说明 HTML",
        "references/change-report.md", "任何新 commit 都要重生成",
        "不是审核通过", "默认不提交公开仓库"):
        if item not in member_prompt:
            errors.append(f"member prompt missing: {item}")
    if "--role lead" in member_prompt or "同意合并 PR" in member_prompt:
        errors.append("member prompt crossed into lead authority")
    if re.search(r"\b[0-9a-f]{40}\b", member_prompt):
        errors.append("member prompt pins a stale commit instead of current develop")
    if not member_prompt.strip().startswith("TEAM_MODE: FORMAL"):
        errors.append("formal Author prompt does not begin with TEAM_MODE: FORMAL")

    for item in (
        "--role lead", "probe_source_contract", "superran-lead/SKILL.md",
        "channel-sim/SKILL.md", "同意合并 PR #N，HEAD <完整 SHA>",
        "[REHEARSAL]", "永远不得合并", "full regression", "35 个工具",
        "最重要的正式 PR 提出者之一", "自己做实现", "TEAM_MODE: FORMAL",
        "author/TianLin0509", "另一个全新的组长 Agent Session",
        "TEAM_MODE: REHEARSAL", "绝不能根据 GitHub Owner 身份自动判断演练",
        "schema_version 不低于 1.3.0", "author_modes.identity_selects_mode=false",
        "技术术语", "白话解释", "具体例子", "我理解为", "不等于",
        "证明了什么、没有证明什么", "不能冒充当前证据",
        "交互式改动说明 HTML", "报告缺失/过期", "不能替代 diff"):
        if item not in lead_prompt:
            errors.append(f"lead prompt missing: {item}")
    if 'href="member-start.html"' not in lead_page:
        errors.append("lead page does not link the formal Author page")

    for item in (
        "TEAM_MODE: REHEARSAL", "GitHub 身份只决定推送路径",
        "绝不能用它推断 FORMAL 或 REHEARSAL", "Draft=true",
        "schema_version 不低于 1.3.0", "author_modes.identity_selects_mode=false",
        "[REHEARSAL]", "永远不得", "TEAM_MODE: FORMAL",
    ):
        if item not in rehearsal_prompt:
            errors.append(f"rehearsal prompt missing: {item}")
    if not rehearsal_prompt.strip().startswith("TEAM_MODE: REHEARSAL"):
        errors.append("rehearsal prompt does not begin with TEAM_MODE: REHEARSAL")

    obsolete_owner_inference = (
        "会识别 Owner 账号，" + "自动建立",
        "因为登录账号是 Owner，" + "Agent 会自动进入体验模式",
    )
    for phrase in obsolete_owner_inference:
        if phrase in member_page or phrase in lead_page:
            errors.append(f"obsolete Owner-to-rehearsal inference remains: {phrase}")

    install_text = _read("INSTALL_AGENT.md")
    tool_count = re.search(r"# 期望：tools: (\d+)", install_text)
    if not tool_count or int(tool_count.group(1)) != 35:
        errors.append("INSTALL_AGENT MCP tool count drift")
    for item in ("--role member", "--role lead", "probe_source_contract"):
        if item not in install_text:
            errors.append(f"INSTALL_AGENT missing: {item}")

    ast.parse(_read("scripts/install_agent_skills.py"))
    pr_template = _read(".github/pull_request_template.md")
    for item in ("核心物理流程", "反向对照", "PR HEAD 完整 SHA",
                 "Author 交互式改动说明 HTML", "[REHEARSAL]"):
        if item not in pr_template:
            errors.append(f"PR template missing: {item}")

    report = workflow.get("change_report", {})
    if report.get("required_for_author_pr") is not True:
        errors.append("Author change report is not required")
    if report.get("required_for_member_pr") is not True:
        errors.append("legacy member change-report compatibility flag drifted")
    if report.get("committed_by_default") is not False:
        errors.append("change report must stay out of the public repo by default")
    if report.get("must_match_remote_pr_head") is not True:
        errors.append("change report is not bound to current remote PR head")
    report_contract = ROOT / "skills" / "superran-member-task" / "references" / "change-report.md"
    if not report_contract.is_file():
        errors.append("missing change-report contract")
    else:
        report_text = report_contract.read_text(encoding="utf-8", errors="strict")
        for item in ("AUTHOR CHANGE REPORT", "output/change-reports", "what it proves",
                     "zero console errors", "no network requests", "SHA-256"):
            if item not in report_text:
                errors.append(f"change-report contract missing: {item}")
    action = _read(".github/workflows/team-contract.yml")
    if "python scripts/validate_team_contract.py" not in action:
        errors.append("team-contract workflow does not run validator")
    if "python scripts/install_agent_skills.py --role lead" not in action:
        errors.append("team-contract workflow does not exercise installer")

    return errors


def main() -> int:
    errors = validate()
    payload = {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "contract_sha256": hashlib.sha256(
            _read("docs/team/workflow.json").encode("utf-8")).hexdigest(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

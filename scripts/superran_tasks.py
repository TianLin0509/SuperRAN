#!/usr/bin/env python3
"""任务看板：一眼看清「我现在该做什么」。

    python scripts/superran_tasks.py [--no-open]

按「轮到谁」分组，不按分支名排列。每条都给出可直接复制的下一步命令。
状态全部自动推导，不需要人工维护任何清单。

只读：会 fetch 远端引用，不改任何分支、文件或提交。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_agent_report import CSS, REPORT_DIR, esc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PACK_DIR = Path.home() / "Desktop" / "claude-artifacts"
INBOX = REPO / "docs" / "inbox"

# 与 .agents/RISK.md 的红档表保持一致。改那张表时这里也要改。
RED_FILES = {
    "linkadapt.py", "amc_policy.py", "bler_curves.py", "bler_data_20b.py",
    "calibration.py", "scheduler_resource.py", "scheduler_frequency.py",
    "scheduler_mu.py", "scheduler_finalize.py", "scheduler_edf.py", "mumimo.py",
    "srs_resource.py", "srs_waveform.py", "srs_metrics.py", "csi_aging.py",
    "native.py", "channelhub.py", "generate.py", "spec38901.py", "carrier.py",
    "physical.py", "linklevel.py", "interference.py", "power_control.py",
    "beamforming.py", "rng.py", "gates.py", "system.py", "experience.py",
    "measure.py", "kpi_compare.py", "analysis.py",
}


def run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        r = subprocess.run(cmd, cwd=str(cwd or REPO), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=90)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def git(*a: str, cwd: Path | None = None) -> str:
    return run(["git", *a], cwd)


def open_prs() -> list[dict]:
    out = run(["gh", "pr", "list", "--state", "open", "--limit", "50", "--json",
               "number,title,headRefName,headRefOid,mergeable,mergeStateStatus,updatedAt"])
    try:
        return json.loads(out) if out else []
    except json.JSONDecodeError:
        return []


def risk_of(head: str) -> str:
    """按改动文件查 RISK.md 的表。红档要走内网评审。"""
    base = git("merge-base", "origin/develop", head)
    if not base:
        return "?"
    files = git("diff", "--name-only", base, head).splitlines()
    touched_code = False
    for f in files:
        name = Path(f).name
        if f.startswith("src/superran/"):
            touched_code = True
            if name in RED_FILES:
                return "红"
        elif f.startswith("tests/") or f.startswith("presets/"):
            touched_code = True
    return "黄" if touched_code else "绿"


def packs_by_sha() -> dict[str, Path]:
    """桌面上打过的审核包，按短 SHA 索引。"""
    out = {}
    for p in PACK_DIR.glob("SuperRAN-review-*.zip"):
        m = re.search(r"SuperRAN-review-([0-9a-f]{7})-", p.name)
        if m:
            out[m.group(1)] = p
    return out


def inbox_files() -> list[Path]:
    if not INBOX.exists():
        return []
    return [p for p in INBOX.glob("*.md") if p.name != "README.md"]


def classify(pr: dict, packs: dict, inbox: list[Path]) -> dict:
    """判定这条 PR 卡在哪一步、下一步该谁做。"""
    head, short = pr["headRefOid"], pr["headRefOid"][:7]
    risk = risk_of(head)
    conflicting = pr.get("mergeable") == "CONFLICTING"
    pack = packs.get(short)
    # 意见文件名里带短 SHA 就认为是这条 PR 的
    opinion = next((f for f in inbox if short in f.name), None)

    if conflicting:
        return dict(who="agent", state="需要同步主线", tone="warn", risk=risk,
                    why="和主线冲突了，冲突多半在机器生成的手册上",
                    action=f'读 C:\\Vibe\\Wireless\\SuperRAN\\.agents\\AUTHOR.md 按它工作。\n'
                           f'任务：把 PR #{pr["number"]} 同步到最新主线，'
                           f'docs/index.html 冲突用生成器重跑，不要手工解')
    if opinion:
        return dict(who="you", state="内网意见已回，待改", tone="warn", risk=risk,
                    why=f"意见在 {opinion.name}",
                    action=f'内网评审意见已放在 docs\\inbox\\{opinion.name}，'
                           f'按它修改并更新 PR #{pr["number"]}，附意见对照表')
    if risk == "红" and pack:
        age_h = (datetime.now().timestamp() - pack.stat().st_mtime) / 3600
        age = f"{age_h:.0f} 小时前打的" if age_h < 48 else f"{age_h / 24:.0f} 天前打的"
        # 脚本无法知道你有没有真的把包带走，所以给一个覆盖两种情况的说法
        return dict(who="pack", state="内网评审这一环", tone="warn", risk=risk,
                    why=f"包已就绪（{age}）：{pack.name}",
                    action=f"还没带走 → 把 {pack.name} 发给内网 Agent\n"
                           f"已经带走 → 等回复，**这期间别让 Agent 动这个分支**\n"
                           f"回来的 md 放进 docs\\inbox\\，文件名带上 {short}")
    if risk == "红" and not pack:
        return dict(who="you", state="红档，待打审核包", tone="bad", risk=risk,
                    why="动了物理核心，按流程要走内网评审",
                    action=f'powershell -File C:\\Vibe\\Wireless\\SuperRAN\\scripts\\'
                           f'superran_review_pack.ps1 {pr["headRefName"]}')
    return dict(who="you", state="待审核合并", tone="ok", risk=risk,
                why="CI 绿，可以交给审核 Agent",
                action=f'读 C:\\Vibe\\Wireless\\SuperRAN\\.agents\\REVIEWER.md 按它工作。\n'
                       f'审 PR #{pr["number"]}，通过就由你合并。\n'
                       f'PR head SHA：{head}\n风险档：{risk}')


def working_branches(pr_branches: set[str]) -> list[dict]:
    """有工作区但还没开 PR 的 —— agent 还在干活。"""
    items, cur = [], {}
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            if cur:
                items.append(cur)
            cur = {"path": line[9:], "branch": ""}
        elif line.startswith("branch "):
            cur["branch"] = line[7:].replace("refs/heads/", "")
    if cur:
        items.append(cur)

    out = []
    for w in items[1:]:                       # 第一个是主工作区
        b = w["branch"]
        name = Path(w["path"]).name
        if not b or b in pr_branches or name.startswith("review-"):
            continue
        dirty = len([x for x in git("status", "--porcelain",
                                    cwd=Path(w["path"])).splitlines() if x])
        ahead = git("rev-list", "--count", f"origin/develop..{b}") or "0"
        out.append(dict(name=name, branch=b, dirty=dirty, ahead=int(ahead)))
    return out


def card(title: str, body: str, note: str = "") -> str:
    n = f'<p style="color:var(--soft);font-size:13px;margin:0 0 14px">{note}</p>' if note else ""
    return '<div class="card"><h2>' + esc(title) + "</h2>" + n + body + "</div>"


def pr_rows(items: list[tuple[dict, dict]]) -> str:
    if not items:
        return "<p style='color:var(--soft)'>暂时没有。</p>"
    out = []
    for pr, c in items:
        out.append(
            '<div style="border-bottom:1px solid var(--line);padding:14px 0">'
            f'<div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">'
            f'<span class="badge {c["tone"]}">{esc(c["state"])}</span>'
            f'<b>#{pr["number"]}</b> {esc(pr["title"][:56])}'
            f'<span style="color:var(--soft);font-size:12px">'
            f'{esc(c["risk"])}档 · {esc(pr["headRefName"])} · {esc(pr["headRefOid"][:7])}</span>'
            "</div>"
            f'<p style="margin:6px 0 8px;font-size:14px">{esc(c["why"])}</p>'
            f'<pre style="font-size:12px">{esc(c["action"])}</pre>'
            "</div>")
    return "".join(out)


def build() -> str:
    git("fetch", "origin", "--prune", "--quiet")
    prs = open_prs()
    packs, inbox = packs_by_sha(), inbox_files()
    classified = [(pr, classify(pr, packs, inbox)) for pr in prs]

    mine = [x for x in classified if x[1]["who"] == "you"]
    agents = [x for x in classified if x[1]["who"] == "agent"]
    waiting = [x for x in classified if x[1]["who"] == "pack"]
    working = working_branches({pr["headRefName"] for pr in prs})

    if working:
        rows = "".join(
            f'<tr><td><b>{esc(w["name"])}</b></td><td>{esc(w["branch"])}</td>'
            f'<td>{w["ahead"]} 个提交</td>'
            f'<td>{"有 " + str(w["dirty"]) + " 个未提交" if w["dirty"] else "干净"}</td></tr>'
            for w in working)
        working_html = ('<div class="scroll"><table><tr><th>工作区</th><th>分支</th>'
                        f"<th>已做</th><th>状态</th></tr>{rows}</table></div>")
    else:
        working_html = "<p style='color:var(--soft)'>没有在干活的 Agent。</p>"

    head = (f'<p class="lead">你有 <b>{len(mine)}</b> 件事要做，'
            f'<b>{len(waiting)}</b> 件卡在内网评审这一环，'
            f'<b>{len(agents) + len(working)}</b> 件在 Agent 手上。</p>')
    if len(waiting) >= 3:
        head += ('<div class="tint"><h3>内网评审是当前的瓶颈</h3>'
                 f'有 {len(waiting)} 条红档 PR 都等着走内网这一圈。'
                 '不必一次全带走——挑物理风险最高的先送，其余可以先合，'
                 '有问题后面当新任务修。<b>流程是为你服务的，不是反过来。</b></div>')

    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>SuperRAN 任务看板</title><style>" + CSS +
            "pre{white-space:pre-wrap}</style></head>"
            '<body><div class="wrap"><h1>我现在该做什么</h1>'
            f'<p class="sub">{datetime.now():%Y-%m-%d %H:%M} · 状态自动推导，无需手工维护</p>'
            + card("总览", head)
            + card(f"① 轮到你了（{len(mine)}）", pr_rows(mine),
                   "复制下面的命令，开新会话粘贴进去就行。")
            + card(f"② 内网评审这一环（{len(waiting)}）", pr_rows(waiting),
                   "脚本判断不了你有没有真把包带走，所以两种情况的做法都写了。")
            + card(f"③ 要 Agent 处理（{len(agents)}）", pr_rows(agents))
            + card(f"④ Agent 还在干活（{len(working)}）", working_html,
                   "还没开 PR，等它自己交付。")
            + f'<p class="foot">由 scripts/superran_tasks.py 生成 · 只读 · '
              f'<a href="{esc((REPORT_DIR / "index.html").as_uri())}">全部报告</a></p>'
            "</div></body></html>")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "tasks.html"
    out.write_text(build(), encoding="utf-8")

    prs = open_prs()
    packs, inbox = packs_by_sha(), inbox_files()
    print()
    for pr in prs:
        c = classify(pr, packs, inbox)
        flag = {"you": "→ 轮到你", "pack": "  内网这环",
                "agent": "  等 Agent"}.get(c["who"], "  ?")
        print(f"  {flag}  #{pr['number']:<3} [{c['risk']}] {c['state']:<16} "
              f"{pr['title'][:38]}")
    print(f"\n绝对路径：{out}")
    if "--no-open" not in sys.argv:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

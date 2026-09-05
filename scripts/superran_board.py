#!/usr/bin/env python3
"""SuperRAN 状态看板。

一条命令看清：主线在哪、谁在干活、谁脏了、和 GitHub 差多少、最近改了什么。
维护者不需要记任何 git 命令。

    python scripts/superran_board.py [--no-fetch] [--no-open]

只读：除了刷新远端跟踪引用（fetch --prune），不改任何分支、文件或提交。
"""
from __future__ import annotations

import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_agent_report import CSS, REPORT_DIR, esc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MAIN_BRANCH = "develop"


def git(*args: str, cwd: Path | None = None) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd or REPO),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def worktrees() -> list[dict]:
    out, cur, items = git("worktree", "list", "--porcelain"), {}, []
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                items.append(cur)
            cur = {"path": line[9:], "branch": "", "sha": "", "detached": False}
        elif line.startswith("HEAD "):
            cur["sha"] = line[5:]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].replace("refs/heads/", "")
        elif line.strip() == "detached":
            cur["detached"] = True
    if cur:
        items.append(cur)

    for w in items:
        path = Path(w["path"])
        w["exists"] = path.exists()
        w["dirty"] = len([x for x in git("status", "--porcelain",
                                         cwd=path).splitlines() if x]) if w["exists"] else 0
        w["last"] = git("log", "-1", "--format=%cd|%s", "--date=format:%m-%d %H:%M",
                        cwd=path) if w["exists"] else ""
        name = path.name
        # `git worktree list` 总是把主工作区排在第一个；不能用脚本所在目录判断，
        # 否则从任务 worktree 里运行会把自己误认成主线。
        if w is items[0]:
            w["kind"], w["order"] = "主线", 0
        elif name.startswith("review-"):
            w["kind"], w["order"] = "审阅", 2
        else:
            w["kind"], w["order"] = "活动任务", 1
    items.sort(key=lambda w: (w["order"], w["path"]))
    return items


def ahead_behind(ref: str, base: str) -> tuple[int, int]:
    out = git("rev-list", "--left-right", "--count", f"{base}...{ref}")
    if not out:
        return (0, 0)
    try:
        behind, ahead = (int(x) for x in out.split())
        return (ahead, behind)
    except ValueError:
        return (0, 0)


def card(title: str, body: str) -> str:
    return '<div class="card"><h2>' + esc(title) + "</h2>" + body + "</div>"


def build(fetched: bool) -> str:
    wts = worktrees()
    main = next((w for w in wts if w["order"] == 0), None)
    upstream_ok = bool(git("rev-parse", "--verify", f"origin/{MAIN_BRANCH}"))

    # ---- 主线 ----
    if main:
        ahead, behind = (ahead_behind(main["sha"], f"origin/{MAIN_BRANCH}")
                         if upstream_ok else (0, 0))
        if main["branch"] != MAIN_BRANCH:
            state, tone = f'不在 {MAIN_BRANCH} 上（当前 {main["branch"] or "detached"}）', "bad"
        elif main["dirty"]:
            state, tone = f'有 {main["dirty"]} 个未提交文件', "warn"
        elif ahead or behind:
            state, tone = f"领先 {ahead} / 落后 {behind}", "warn"
        else:
            state, tone = "与 GitHub 一致", "ok"
        head = ('<p class="lead"><span class="badge ' + tone + '">' + esc(state)
                + "</span></p>"
                '<div class="meta"><span>路径 <b>' + esc(main["path"]) + "</b></span>"
                "<span>分支 <b>" + esc(main["branch"] or "detached") + "</b></span>"
                "<span>SHA <b>" + esc(main["sha"][:8]) + "</b></span>"
                "<span>最近提交 <b>"
                + esc(main["last"].split("|", 1)[0] if main["last"] else "-")
                + "</b></span></div>"
                "<p>" + esc(main["last"].split("|", 1)[-1] if main["last"] else "") + "</p>")
    else:
        head = "<p>未能读取主线状态。</p>"

    # ---- 工作区 ----
    rows = []
    for w in wts:
        if w["order"] == 0:
            continue
        ahead, behind = ahead_behind(w["sha"], f"origin/{MAIN_BRANCH}") if upstream_ok else (0, 0)
        if w["dirty"]:
            d_html = '<span class="badge warn">' + str(w["dirty"]) + " 个未提交</span>"
        else:
            d_html = '<span class="dot y"></span>干净'
        rows.append(
            "<tr><td>" + esc(w["kind"]) + "</td>"
            "<td><b>" + esc(Path(w["path"]).name) + "</b><br>"
            '<span class="d" style="color:var(--soft);font-size:12px">'
            + esc(w["branch"] or "detached " + w["sha"][:8]) + "</span></td>"
            "<td>" + d_html + "</td>"
            "<td>比主线 +" + str(ahead) + " / -" + str(behind) + "</td>"
            "<td>" + esc(w["last"].split("|", 1)[0] if w["last"] else "-") + "</td></tr>")
    wt_html = ('<div class="scroll"><table><tr><th>类型</th><th>工作区 / 分支</th>'
               "<th>状态</th><th>与主线差距</th><th>最近提交</th></tr>"
               + "".join(rows) + "</table></div>"
               '<div class="tint"><h3>怎么读这张表</h3>'
               "<b>活动任务</b>=有人正在做，别动。<b>审阅</b>=只读复核区，结论出完即可回收。"
               "干净且比主线 +0 的审阅区可以安全删除。</div>")

    # ---- 冻结分支 ----
    frozen = []
    for line in git("branch", "--format=%(refname:short)|%(objectname:short)|%(contents:subject)"
                    ).splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].split("/")[0] in ("salvage", "simplify", "wip"):
            frozen.append("<tr><td><code>" + esc(parts[0]) + "</code></td><td><code>"
                          + esc(parts[1]) + "</code></td><td>" + esc(parts[2]) + "</td></tr>")
    frozen_html = ('<div class="scroll"><table><tr><th>分支</th><th>SHA</th>'
                   "<th>说明</th></tr>" + "".join(frozen) + "</table></div>"
                   "<p style='color:var(--soft);font-size:13px'>"
                   "这些是冻结的抢救点，不会混进主线，也不要直接 cherry-pick，"
                   "要用就当作新任务重新实现。</p>") if frozen else ""

    # ---- 最近改动文档 ----
    changes_dir = REPO / "docs" / "changes"
    docs = sorted((p for p in changes_dir.glob("*.md") if not p.name.startswith("_")
                   and p.name != "README.md"), reverse=True)[:8]
    if docs:
        lis = "".join("<li><code>" + esc(p.name) + "</code></li>" for p in docs)
        changes_html = '<ul class="plain">' + lis + "</ul>"
    else:
        changes_html = ("<p style='color:var(--soft)'>还没有改动文档。"
                        "同步 GitHub 时按 <code>docs/changes/_TEMPLATE.md</code> 补齐。</p>")

    idx = REPORT_DIR / "index.html"
    reports_html = ('<p>全部 Agent 报告：<a href="' + esc(idx.as_uri()) + '">'
                    + esc(str(idx)) + "</a></p>") if idx.exists() else (
        "<p style='color:var(--soft)'>还没有报告。</p>")

    fetch_note = "已刷新远端" if fetched else "未刷新远端（--no-fetch）"

    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>SuperRAN 状态看板</title><style>" + CSS + "</style></head>"
            '<body><div class="wrap"><h1>SuperRAN 状态看板</h1>'
            '<p class="sub">' + datetime.now().strftime("%Y-%m-%d %H:%M") + " · "
            + fetch_note + "</p>"
            + card("主线", head)
            + card("工作区", wt_html)
            + (card("冻结分支", frozen_html) if frozen_html else "")
            + card("最近改动文档", changes_html)
            + card("报告", reports_html)
            + '<p class="foot">由 scripts/superran_board.py 生成 · 只读，不改任何分支</p>'
            "</div></body></html>")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    fetched = False
    if "--no-fetch" not in sys.argv:
        fetched = bool(git("fetch", "origin", "--prune", "--quiet") == "")
        fetched = git("rev-parse", "--verify", f"origin/{MAIN_BRANCH}") != ""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "board.html"
    out.write_text(build(fetched), encoding="utf-8")

    for w in worktrees():
        tag = {0: "主线", 1: "活动", 2: "审阅"}[w["order"]]
        flag = f"脏{w['dirty']}" if w["dirty"] else "干净"
        print(f"  [{tag}] {Path(w['path']).name:52} {w['branch'] or w['sha'][:8]:38} {flag}")

    print("\n绝对路径：" + str(out))
    if "--no-open" not in sys.argv:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

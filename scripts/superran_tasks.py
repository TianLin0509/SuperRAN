#!/usr/bin/env python3
"""任务看板（泳道式）—— 一屏看清每个任务走到哪了、轮到谁。

    python scripts/superran_tasks.py [--no-open]

数据来自 `C:\\VibeData\\SuperRAN-Tasks\\*.json`（Agent 每完成一步自己记一行），
PR 状态从 GitHub 实时补齐。已合并的任务从主区消失，收进历史区。

只读：会 fetch 远端引用，不改任何分支、文件或提交。
"""
from __future__ import annotations

import json
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_agent_report import CSS, REPORT_DIR, esc  # noqa: E402
from superran_task import LEDGER, STEPS, TERMINAL  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
INBOX = REPO / "docs" / "inbox"
NODES = ["实现", "内网审查", "修改", "PR 审核", "合并"]

# 台账里的步骤 → 泳道上第几个节点已完成
STEP_TO_NODE = {"实现": 1, "送内网": 1, "内网已回": 2, "修改": 3,
                "提PR": 3, "审核中": 4, "打回": 3, "已合并": 5}


def run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=90)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def load_tasks() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for p in sorted(LEDGER.glob("T*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def pr_states() -> dict[int, dict]:
    raw = run(["gh", "pr", "list", "--state", "all", "--limit", "60", "--json",
               "number,state,mergeable,mergeStateStatus,title"])
    try:
        return {p["number"]: p for p in json.loads(raw)} if raw else {}
    except json.JSONDecodeError:
        return {}


def inbox_for(tid: str) -> Path | None:
    if not INBOX.exists():
        return None
    return next((p for p in INBOX.glob("*.md")
                 if p.name != "README.md" and tid in p.name), None)


def state_of(t: dict, prs: dict) -> dict:
    """判定：走到第几个节点、轮到谁、下一步该干什么。"""
    last = t["events"][-1]["step"] if t["events"] else ""
    node = STEP_TO_NODE.get(last, 0)
    tid, title = t["id"], t["title"]
    pr = t.get("pr")
    pr_info = prs.get(pr) if pr else None
    opinion = inbox_for(tid)

    if last == "已合并":
        return dict(node=5, who="done", label="已合并", tone="ok",
                    why=f"完成于 {t['events'][-1]['at']}", action="")

    # 内网意见回来了但还没记 —— 文件系统比 Agent 的记录更靠谱
    if last == "送内网" and opinion:
        return dict(node=2, who="you", label="意见已回，待转交", tone="warn",
                    why=f"{opinion.name} 已经在收件箱里了",
                    action=f"请根据 C:\\Vibe\\Wireless\\SuperRAN\\.agents\\AUTHOR.md 展开工作。\n"
                           f"任务 {tid}：内网意见在 docs\\inbox\\{opinion.name}，"
                           f"按它修改，改完提 PR")
    if last == "送内网":
        return dict(node=2, who="you", label="等内网回复", tone="warn",
                    why="包已同步给内网。回来的 md 放进 docs\\inbox\\，文件名带上任务 ID",
                    action=f"内网给的 md → 存成 docs\\inbox\\{tid}_内网审核.md")
    if last == "实现":
        if t.get("risk") == "红":
            return dict(node=1, who="you", label="待送内网", tone="warn",
                        why="红档，按流程要走内网这一圈（约 20 分钟）",
                        action=f"右键 {tid}_{title}.zip 的 URL → AI HUB 同步选项")
        return dict(node=1, who="agent", label="待提 PR", tone="", risk_skip=True,
                    why=f"{t.get('risk') or '非红'}档，跳过内网，直接提 PR", action="")
    if last in ("内网已回", "打回"):
        return dict(node=node, who="you", label="待转交 Agent 修改", tone="warn",
                    why=t["events"][-1].get("note", ""),
                    action=f"请根据 C:\\Vibe\\Wireless\\SuperRAN\\.agents\\AUTHOR.md 展开工作。\n"
                           f"任务 {tid}：按 docs\\inbox\\ 里的意见修改，改完更新 PR")
    if last == "修改":
        return dict(node=3, who="agent", label="Agent 修改中", tone="", why="", action="")
    if last == "提PR":
        conflict = pr_info and pr_info.get("mergeable") == "CONFLICTING"
        if conflict:
            return dict(node=4, who="agent", label="PR 冲突，需同步主线", tone="bad",
                        why="冲突多半在机器生成的手册上，重新生成即可", action="")
        return dict(node=4, who="you", label="待转给合并 Agent", tone="warn",
                    why=f"PR #{pr} 已就绪" if pr else "",
                    action=f"请根据 C:\\Vibe\\Wireless\\SuperRAN\\.agents\\MERGER.md 展开工作。\n"
                           f"任务 {tid}，审 PR #{pr}，通过就由你合并。")
    if last == "审核中":
        return dict(node=4, who="agent", label="合并 Agent 审核中", tone="", why="", action="")
    return dict(node=0, who="agent", label="Agent 实现中", tone="", why="", action="")


def track(node: int, tone: str) -> str:
    """五个节点的进度条。当前节点按 tone 上色。"""
    cur = "stuck" if tone in ("warn", "bad") else "now"
    out = []
    for i in range(5):
        if i < node:
            cls = "done"
        elif i == node:
            cls = cur
        else:
            cls = ""
        out.append(f'<span class="node {cls}" title="{NODES[i]}"></span>')
        if i < 4:
            out.append(f'<span class="seg {"done" if i < node - 1 else ""}"></span>')
    return '<div class="track">' + "".join(out) + "</div>"


def lane(t: dict, s: dict) -> str:
    mine = s["who"] == "you"
    badge = {"红": "red", "黄": "yellow", "绿": "green"}.get(t.get("risk", ""), "green")
    act = (f'<pre class="act">{esc(s["action"])}</pre>' if s.get("action") else "")
    tag = ('<span class="badge mine">轮到你</span>' if mine
           else f'<span class="badge {s["tone"] or "idle"}">{esc(s["label"])}</span>')
    return (f'<div class="lane{" mine" if mine else ""}">'
            f'<div class="left"><span class="t">{esc(t["title"])}</span>'
            f'<span class="tid">{esc(t["id"])}</span> '
            f'<span class="badge {badge}">{esc(t.get("risk") or "?")}</span></div>'
            f"{track(s['node'], s['tone'])}"
            f'<div class="st">{tag}<small>{esc(s.get("why", ""))}</small></div>'
            f"</div>{act}")


def build() -> str:
    run(["git", "fetch", "origin", "--prune", "--quiet"])
    tasks, prs = load_tasks(), pr_states()
    rows = [(t, state_of(t, prs)) for t in tasks]

    active = [(t, s) for t, s in rows if s["who"] != "done"]
    done = [(t, s) for t, s in rows if s["who"] == "done"]
    active.sort(key=lambda x: (x[1]["who"] != "you", x[0]["id"]))
    mine = sum(1 for _, s in active if s["who"] == "you")

    if active:
        lanes = "".join(lane(t, s) for t, s in active)
    else:
        lanes = ('<p style="color:var(--soft)">没有进行中的任务。'
                 '让 Agent 跑 <code>superran_task.py new "标题"</code> 开一个。</p>')

    hist = "".join(
        f'<div class="hrow"><span class="tid">{esc(t["id"])}</span>'
        f'<b>{esc(t["title"])}</b>'
        f'<span class="age">{esc(s.get("why", ""))}</span></div>'
        for t, s in sorted(done, key=lambda x: x[0]["id"], reverse=True))

    head = (f'<p class="lead"><b>{mine}</b> 件事等你，'
            f'<b>{len(active) - mine}</b> 件在 Agent 手上，'
            f'已完成 <b>{len(done)}</b> 件。</p>'
            + ('<div class="tint">下面每条<b>橙色</b>的都轮到你了，'
               '框里的话复制到新会话就行。</div>' if mine else
               '<div class="tint">暂时没有需要你操作的，等 Agent 交付。</div>'))

    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="refresh" content="120">'
            "<title>SuperRAN 工作台</title><style>" + CSS + EXTRA + "</style></head>"
            '<body><div class="wrap"><h1>SuperRAN 工作台</h1>'
            f'<p class="sub">{datetime.now():%Y-%m-%d %H:%M} · 每 2 分钟自动刷新 · '
            "节点：实现 → 内网审查 → 修改 → PR 审核 → 合并</p>"
            '<div class="card">' + head + "</div>"
            '<div class="card"><h2>进行中</h2>' + lanes +
            '<div class="legend"><span><i class="ok"></i>已完成</span>'
            '<span><i class="ac"></i>进行中</span><span><i class="wn"></i>等你</span>'
            '<span><i class="id"></i>未开始</span></div></div>'
            + (f'<details class="card"><summary>历史（{len(done)} 件已完成）</summary>'
               f'<div style="margin-top:12px">{hist}</div></details>' if done else "")
            + f'<p class="foot">数据来自任务台账，Agent 每完成一步自己记一行 · '
              f'<a href="{esc((REPORT_DIR / "index.html").as_uri())}">全部报告</a></p>'
            "</div></body></html>")


EXTRA = """
.lane{display:grid;grid-template-columns:250px 1fr 210px;gap:14px;align-items:center;
padding:14px 0;border-bottom:1px solid var(--line)}
.lane .left .t{font-weight:600;font-size:14.5px;display:block;margin-bottom:2px}
.tid{font-family:"SF Mono",Consolas,monospace;font-size:11.5px;color:var(--soft)}
.lane.mine{background:var(--tint);border-left:3px solid var(--warn);
margin:0 -24px;padding:14px 24px 14px 21px;border-bottom:0}
.track{display:flex;align-items:center}
.node{width:11px;height:11px;border-radius:6px;background:var(--line);flex:none}
.node.done{background:var(--ok)}
.node.now{background:var(--accent);box-shadow:0 0 0 4px var(--accent);
outline:3px solid var(--card);outline-offset:-3px}
.node.stuck{background:var(--warn);box-shadow:0 0 0 4px var(--warn);
outline:3px solid var(--card);outline-offset:-3px}
.seg{height:2px;flex:1;background:var(--line)}
.seg.done{background:var(--ok)}
.st{font-size:13px;text-align:right}
.st small{display:block;color:var(--soft);font-size:11.5px;margin-top:3px}
.badge.idle{background:var(--line);color:var(--ink)}
.badge.red{background:var(--bad);color:#fff}
.badge.yellow{background:var(--warn);color:#1d1d1f}
.badge.green{background:var(--ok);color:#1d1d1f}
.badge.mine{background:var(--warn);color:#1d1d1f}
.act{background:var(--code-bg);color:var(--code-ink);border-radius:8px;
padding:11px 13px;font-size:12px;white-space:pre-wrap;word-break:break-word;
margin:0 0 14px}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--soft);
margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:5px;margin-right:5px}
.legend i.ok{background:var(--ok)}.legend i.ac{background:var(--accent)}
.legend i.wn{background:var(--warn)}.legend i.id{background:var(--line)}
.hrow{display:flex;gap:12px;align-items:baseline;padding:8px 0;
border-bottom:1px solid var(--line);font-size:13.5px}
.hrow:last-child{border-bottom:0}
.hrow .age{margin-left:auto;color:var(--soft);font-size:12px}
summary{cursor:pointer;font-weight:700;font-size:16px}
@media(max-width:900px){.lane{grid-template-columns:1fr;gap:8px}
.st{text-align:left}.lane.mine{margin:0;padding:14px 0 14px 12px}}
"""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "tasks.html"
    out.write_text(build(), encoding="utf-8")

    prs = pr_states()
    print()
    for t in load_tasks():
        s = state_of(t, prs)
        if s["who"] == "done":
            continue
        mark = "→ 轮到你 " if s["who"] == "you" else "         "
        print(f"  {mark}[{t.get('risk') or '?'}] {s['label']:<18} {t['title']}")
    print(f"\n绝对路径：{out}")
    if "--no-open" not in sys.argv:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

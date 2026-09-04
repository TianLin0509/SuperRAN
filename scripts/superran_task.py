#!/usr/bin/env python3
"""任务台账 —— 把一个任务的所有产物串成一条线。

以前同一个任务有四个不同的名字（分支、PR、zip、报告），只能靠短 SHA 勉强对上，
SHA 一改就断。现在每个任务有一个贯穿始终的 ID。

Agent 用法（每完成一步记一行，就一条命令）：

    python scripts/superran_task.py new "CQI 改事件驱动"      # 开工，返回任务 ID
    python scripts/superran_task.py log <ID> 实现 --report <html路径> --sha <SHA>
    python scripts/superran_task.py log <ID> 送内网 --zip <zip路径>
    python scripts/superran_task.py log <ID> 内网已回 --note "4 条意见，1 条 BLOCKED"
    python scripts/superran_task.py log <ID> 修改 --note "3 修 1 不采纳"
    python scripts/superran_task.py log <ID> 提PR --pr 21
    python scripts/superran_task.py log <ID> 审核中
    python scripts/superran_task.py log <ID> 已合并 --sha <squash SHA>
    python scripts/superran_task.py log <ID> 打回 --note "HARQ 时序仍有问题"

台账放在仓库外（`C:\\VibeData\\SuperRAN-Tasks\\`），所以不进 git、不会冲突、
多个 Agent 并行写各自的文件也不打架。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LEDGER = Path(r"C:\VibeData\SuperRAN-Tasks")
REPO = Path(__file__).resolve().parent.parent

# 顺序即流程。改这里等于改流程，网页会跟着变。
STEPS = ["实现", "送内网", "内网已回", "修改", "提PR", "审核中", "已合并"]
TERMINAL = {"已合并"}

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

# 常见无线术语 → 短英文，让 ID 也能看懂在干嘛
HINTS = [
    ("事件驱动", "event-driven"), ("多进程", "multiproc"), ("进程", "process"),
    ("开销", "overhead"), ("口径", "metric"), ("统计", "metric"),
    ("调度", "sched"), ("信道", "channel"), ("反馈", "feedback"),
    ("重传", "retx"), ("时序", "timing"), ("公平", "fairness"),
    ("吞吐", "throughput"), ("时延", "latency"), ("功率", "power"),
    ("波束", "beam"), ("极化", "polar"), ("阵列", "array"), ("校准", "calib"),
]
KEEP = re.compile(r"[A-Za-z0-9]+")


def slug(title: str) -> str:
    """从中文标题里挤出一个能看懂在干嘛的英文短名。

    英文缩写最多取 2 个 —— 取 3 个的话像「TBS 扣掉 DM-RS 与 PDCCH 开销」会变成
    `tbs-dm-rs`，把「开销」这个真正说明在干嘛的词挤掉了。
    """
    parts = [m.group(0).lower() for m in KEEP.finditer(title)][:2]
    for cn, en in HINTS:
        if len(parts) >= 3:
            break
        if cn in title and en not in parts:
            parts.append(en)
    return "-".join(parts)[:28].strip("-") or "task"


def git(*a: str) -> str:
    try:
        r = subprocess.run(["git", *a], cwd=str(REPO), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def guess_risk(branch: str) -> str:
    """按改动文件查 RISK.md 的表。查不到就留空，让 Agent 自己填。"""
    base = git("merge-base", "origin/develop", branch)
    if not base:
        return ""
    touched = False
    for f in git("diff", "--name-only", base, branch).splitlines():
        if f.startswith("src/superran/"):
            touched = True
            if Path(f).name in RED_FILES:
                return "红"
        elif f.startswith(("tests/", "presets/")):
            touched = True
    return "黄" if touched else "绿"


def load(tid: str) -> dict:
    p = LEDGER / f"{tid}.json"
    if not p.exists():
        raise SystemExit(f"找不到任务 {tid}。先跑 new，或用 list 看有哪些。")
    return json.loads(p.read_text(encoding="utf-8"))


def save(t: dict) -> Path:
    LEDGER.mkdir(parents=True, exist_ok=True)
    p = LEDGER / f"{t['id']}.json"
    p.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def cmd_new(args) -> int:
    tid = f"T{datetime.now():%Y%m%d}-{slug(args.title)}"
    n = 2
    while (LEDGER / f"{tid}.json").exists():
        tid = f"T{datetime.now():%Y%m%d}-{slug(args.title)}-{n}"
        n += 1
    t = {"id": tid, "title": args.title, "risk": args.risk or "",
         "branch": tid, "pr": None, "seat": args.seat or "",
         "created": datetime.now().strftime("%Y-%m-%d %H:%M"), "events": []}
    save(t)

    print(f"\n任务 ID：{tid}")
    print(f"标题：  {args.title}\n")
    print("这个 ID 要贯穿到底，四样东西都用它：")
    print(f"  分支名   {tid}")
    print(f"  PR 标题  [{tid}] {args.title}")
    print(f"  审核包   {tid}_{args.title}.zip")
    print(f"  内网意见 docs\\inbox\\{tid}_内网审核.md")
    print(f"\n建工作区：")
    print(f"  git worktree add -b {tid} "
          f"C:\\Vibe\\Worktrees\\SuperRAN\\{tid} develop")
    return 0


def cmd_log(args) -> int:
    t = load(args.id)
    if args.step not in STEPS and args.step != "打回":
        raise SystemExit(f"步骤只能是：{' / '.join(STEPS)} / 打回")

    ev = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "step": args.step}
    for k in ("note", "report", "zip", "sha", "seat"):
        if getattr(args, k, None):
            ev[k] = getattr(args, k)
    t["events"].append(ev)

    if args.pr:
        t["pr"] = int(args.pr)
    if args.seat:
        t["seat"] = args.seat
    if not t.get("risk"):
        t["risk"] = guess_risk(t["branch"]) or ""

    p = save(t)
    print(f"已记录：{args.id} → {args.step}")
    if args.step in TERMINAL:
        print("任务结束，看板会把它移到历史区。")
    print(f"绝对路径：{p}")
    return 0


def cmd_list(args) -> int:
    if not LEDGER.exists():
        print("还没有任务。")
        return 0
    rows = []
    for p in sorted(LEDGER.glob("T*.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        last = t["events"][-1]["step"] if t["events"] else "未开工"
        rows.append((t["id"], t.get("risk", "?"), last, t["title"]))
    if not rows:
        print("还没有任务。")
        return 0
    print()
    for tid, risk, last, title in rows:
        mark = "  " if last in TERMINAL else "→ "
        print(f"  {mark}{tid:<30} [{risk}] {last:<8} {title}")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="SuperRAN 任务台账")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="开一个新任务，返回贯穿始终的 ID")
    n.add_argument("title", help="中文标题，一句话说清在干嘛")
    n.add_argument("--risk", choices=["红", "黄", "绿"])
    n.add_argument("--seat", help="你的席位，如 claude1 / codex2")
    n.set_defaults(func=cmd_new)

    g = sub.add_parser("log", help="记一步")
    g.add_argument("id")
    g.add_argument("step", help=" / ".join(STEPS) + " / 打回")
    for opt in ("note", "report", "zip", "sha", "seat"):
        g.add_argument(f"--{opt}")
    g.add_argument("--pr", type=int)
    g.set_defaults(func=cmd_log)

    ls = sub.add_parser("list", help="列出所有任务")
    ls.set_defaults(func=cmd_list)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

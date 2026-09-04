#!/usr/bin/env python3
"""把一次改动打包成「审核包」，带进内网给内网 Agent 审。

    python scripts/superran_review_pack.py <分支名或SHA> [--base develop]

内网 Agent 不能联网，也跑不了这个仓库。所以包里要自带：改动说明、完整 diff、
改动后的完整文件（让它能读上下文）、实现者报告，以及审核合同本身。

生成的 docs/index.html 有 3.7 MB 且是机器生成的，diff 里排除掉——
实测排除后体积能小 15 到 20 倍。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path.home() / "Desktop" / "claude-artifacts"

# 机器生成的产物：diff 里排除，否则包会大 15-20 倍且没有审阅价值
GENERATED = [
    ":(exclude)docs/index.html",
    ":(exclude)artifacts/**",
    ":(exclude)output/**",
]

# 只把这些目录下改动过的文件收进「完整文件」，其余靠 diff 就够。
# 不收 scripts/：文档生成器单文件就有 400 KB，占了包的大半却对物理审核没用。
FULL_FILE_DIRS = ("src/", "tests/")

# 单个文件超过这个大小就不收全文，让审核者去看 diff。避免一两个巨型文件撑爆包
MAX_FULL_FILE_BYTES = 200_000


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：\n{r.stderr.strip()}")
    return r.stdout


def find_author_report(sha: str, ref: str) -> tuple[Path | None, str | None]:
    """找 Author 报告。返回 (路径, 绑定的SHA)。

    先按 SHA 精确找；找不到就退而求其次按分支名找最近的一份，并把它绑定的 SHA
    一起返回——报告陈旧不等于没价值，但**必须让审核者知道它对不上**。
    """
    try:
        import json
        sys.path.insert(0, str(REPO / "scripts"))
        from make_agent_report import REPORT_DIR
        manifest = REPORT_DIR / "index.json"
        if not manifest.exists():
            return (None, None)
        entries = [e for e in json.loads(manifest.read_text(encoding="utf-8"))
                   if e.get("role") == "author"]
        for e in entries:
            if e.get("sha", "").startswith(sha[:7]):
                p = REPORT_DIR / e["file"]
                if p.exists():
                    return (p, e.get("sha", ""))
        # 退而求其次：同一分支最近的一份
        same = sorted((e for e in entries if e.get("branch", "") == ref),
                      key=lambda e: e.get("date", ""), reverse=True)
        if same:
            p = REPORT_DIR / same[0]["file"]
            if p.exists():
                return (p, same[0].get("sha", ""))
    except Exception:
        pass
    return (None, None)


def build_summary(base: str, head: str, ref: str, changed: list[str]) -> str:
    commits = git("log", "--format=%H|%s", f"{base}..{head}").strip().splitlines()
    stat = git("diff", "--numstat", base, head, "--", ".", *GENERATED).strip().splitlines()

    rows = []
    for line in stat:
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append(f"| `{parts[2]}` | +{parts[0]} | −{parts[1]} |")

    commit_lines = []
    for c in commits:
        h, _, subject = c.partition("|")
        commit_lines.append(f"- `{h[:9]}` {subject}")

    return f"""# 改动说明

## 审的是哪一版

- **完整 SHA**：`{head}`
- **分支 / 引用**：`{ref}`
- **基线（改动前）**：`{base}`
- **打包时间**：{datetime.now().strftime("%Y-%m-%d %H:%M")}

> 报告里必须抄上完整 SHA。外面的代码可能已经往前走了，
> 对方靠它判断你的意见是不是已经过时。

## 这次一共 {len(commits)} 个提交

{chr(10).join(commit_lines)}

## 改了哪些文件（已排除机器生成的 `docs/index.html`）

| 文件 | 增 | 删 |
|---|---|---|
{chr(10).join(rows)}

## 包里有什么

| 文件 | 是什么 |
|---|---|
| `00-开始读这里.md` | 你的工作合同。**先读它。** |
| `01-改动说明.md` | 本文件 |
| `02-完整diff.patch` | 这次改动的完整 diff（已排除生成物） |
| `03-改动后完整文件/` | 被改过的源文件的**完整内容**，用来读上下文 |
| `04-Author报告.html` | 实现者自己写的报告。**它的声称需要你核对**，不是证据 |
| `05-项目规范/` | 仓库的物理合同与风险分档，判断口径时参考 |

## 你的两个先天限制

1. **跑不了代码。** 需要运行才能确认的，标为"需要对方实测确认"，不要断言。
2. **看到的是快照。** 上面那个 SHA 就是它的时间戳。
"""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="要审的分支名或 SHA")
    ap.add_argument("--base", default="develop", help="基线，默认 develop")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    args = ap.parse_args()

    head = git("rev-parse", args.ref).strip()
    base = git("merge-base", args.base, head).strip()
    short = head[:7]

    changed = [f for f in git("diff", "--name-only", base, head, "--", ".",
                              *GENERATED).strip().splitlines() if f]
    if not changed:
        raise SystemExit(f"{args.ref} 相对 {args.base} 没有可审的改动")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"SuperRAN-review-{short}-{datetime.now():%Y%m%d}.zip"

    contract = (REPO / ".agents" / "COMPANY_REVIEW.md").read_text(encoding="utf-8")
    summary = build_summary(base, head, args.ref, changed)
    diff = git("diff", base, head, "--", ".", *GENERATED)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00-开始读这里.md", contract)
        z.writestr("01-改动说明.md", summary)
        z.writestr("02-完整diff.patch", diff)

        skipped = []
        for f in changed:
            if not f.startswith(FULL_FILE_DIRS):
                continue
            try:
                content = git("show", f"{head}:{f}")
            except SystemExit:
                continue          # 这次删掉的文件，diff 里已经有了
            if len(content.encode("utf-8")) > MAX_FULL_FILE_BYTES:
                skipped.append(f)
                continue
            z.writestr(f"03-改动后完整文件/{f}", content)
        if skipped:
            z.writestr("03-改动后完整文件/_未收录的大文件.md",
                       "这些文件太大没收全文，改动内容看 `02-完整diff.patch`：\n\n"
                       + "\n".join(f"- `{f}`" for f in skipped) + "\n")

        for name in ("CLAUDE.md", ".agents/RISK.md", ".agents/TESTING.md"):
            p = REPO / name
            if p.exists():
                z.writestr(f"05-项目规范/{Path(name).name}", p.read_text(encoding="utf-8"))

        report, report_sha = find_author_report(head, args.ref)
        if report:
            z.write(report, "04-Author报告.html")
            if not report_sha.startswith(head[:7]):
                z.writestr("04-Author报告-注意.md",
                           f"""# 注意：这份 Author 报告与被审的代码对不上

- 被审的代码：`{head}`
- 报告绑定的：`{report_sha or "（未记录）"}`

实现者出报告之后又提交了新的改动，所以报告里的结论**只覆盖到它自己那一版**。

把它当作"实现者的意图说明"来读，**不要当成对当前代码的验证证据**。
报告里声称验证过的东西，在当前这一版上可能已经不成立了——
这本身就是一条值得写进你审核报告的发现。
""")

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"\n打包完成（{size_mb:.1f} MB，{len(changed)} 个改动文件）\n")
    print(f"绝对路径：{zip_path}\n")
    print("────────── 把下面这段连同 zip 一起发给内网 Agent ──────────\n")
    print(f"这是 SuperRAN 一次改动的审核包（版本 {short}）。")
    print("先读里面的 00-开始读这里.md，按它的规矩工作。")
    print("任务：拿参照实现做基准，判断这次改动在物理上对不对，")
    print("     按合同里的模板写一份 Markdown 审核报告给我。")
    print("\n──────────────────────────────────────────────────────────\n")
    main_repo = git("worktree", "list", "--porcelain").splitlines()[0][9:]
    print(f"它给你 md 之后，复制到：{Path(main_repo) / 'docs' / 'inbox'}")
    print("然后对本地 Agent 说：处理 docs\\inbox 里的内网审核报告")
    if not report:
        print("\n注意：没找到这个分支的 Author 报告，包里缺 04。"
              "确认实现者是否已经出过报告。")
    elif not report_sha.startswith(head[:7]):
        print(f"\n注意：找到的 Author 报告绑定的是 {report_sha[:7]}，"
              f"不是被审的 {short}。包里已附说明，提醒审核者别把它当验证证据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

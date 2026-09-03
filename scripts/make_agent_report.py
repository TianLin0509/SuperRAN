#!/usr/bin/env python3
"""SuperRAN Agent 报告生成器。

Agent 只写一份结构化 JSON，本脚本负责排版、写盘并刷新总索引。
不要让 Agent 手写 HTML —— 每次手写都会漂移，也浪费 token。

    python scripts/make_agent_report.py <report.json>

JSON 字段见 .agents/AUTHOR.md 与 .agents/REVIEWER.md，缺省字段自动跳过该节。
"""
from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPORT_DIR = Path(r"C:\VibeData\Artifacts\Reports\SuperRAN")
KATEX_HEADER = Path.home() / ".claude" / "snippets" / "katex-inline-header.html"

VERDICT_STYLE = {
    "PASS": ("通过", "ok"),
    "DONE": ("完成", "ok"),
    "REVISE": ("需返工", "warn"),
    "BLOCKED": ("阻断", "bad"),
    "DISPUTE": ("争议待裁", "warn"),
}

CSS = """
:root{--bg:#fafafa;--card:#fff;--ink:#1d1d1f;--soft:#6e6e73;--line:#d2d2d7;
--accent:#0071e3;--ok:#34c759;--warn:#ff9f0a;--bad:#ff3b30;
--tint:#f0f6ff;--tint-ink:#1d1d1f;--code-bg:#1d1d1f;--code-ink:#f5f5f7}
@media (prefers-color-scheme:dark){:root{--bg:#1d1d1f;--card:#2c2c2e;--ink:#f5f5f7;
--soft:#aeaeb2;--line:#38383a;--accent:#0a84ff;--ok:#30d158;--warn:#ffd60a;--bad:#ff453a;
--tint:#243044;--tint-ink:#f5f5f7}}
*{box-sizing:border-box}
body{margin:0;padding:40px 20px;background:var(--bg);color:var(--ink);
font-family:-apple-system,"PingFang SC",system-ui,sans-serif;line-height:1.7;
-webkit-font-smoothing:antialiased}
.wrap{max-width:880px;margin:0 auto}
h1{font-size:30px;font-weight:700;letter-spacing:-.02em;margin:0 0 6px}
h2{font-size:19px;font-weight:700;letter-spacing:-.01em;margin:0 0 14px}
.sub{color:var(--soft);font-size:14px;margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:24px;margin:0 0 16px}
.badge{display:inline-block;padding:3px 12px;border-radius:6px;font-size:13px;
font-weight:700;color:#fff}
.badge.ok{background:var(--ok)}
.badge.warn{background:var(--warn);color:#1d1d1f}
.badge.bad{background:var(--bad)}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 26px}
.meta span{background:var(--card);border:1px solid var(--line);border-radius:6px;
padding:4px 10px;font-size:12px;color:var(--soft)}
.meta b{color:var(--ink);font-weight:600}
.lead{font-size:17px;line-height:1.75;margin:0}
ol.chain{margin:0;padding-left:0;list-style:none;counter-reset:s}
ol.chain li{counter-increment:s;position:relative;padding-left:38px;margin-bottom:14px}
ol.chain li::before{content:counter(s);position:absolute;left:0;top:3px;width:24px;
height:24px;border-radius:12px;background:var(--accent);color:#fff;font-size:12px;
font-weight:700;display:flex;align-items:center;justify-content:center}
ol.chain b{display:block}
ol.chain .d{color:var(--soft);font-size:14px}
ul.plain{margin:0;padding-left:20px}
ul.plain li{margin-bottom:8px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);
vertical-align:top}
th{color:var(--soft);font-weight:600;font-size:12px;letter-spacing:.03em}
tr:last-child td{border-bottom:none}
.tint{background:var(--tint);color:var(--tint-ink);border-radius:10px;
padding:16px 18px;margin-top:14px}
.tint h3{margin:0 0 8px;font-size:14px;font-weight:700}
.scroll{overflow-x:auto}
details{margin-top:14px;border:1px solid var(--line);border-radius:10px;
background:var(--card)}
summary{cursor:pointer;padding:12px 18px;font-size:14px;font-weight:600;
color:var(--soft)}
details[open] summary{border-bottom:1px solid var(--line)}
details .body{padding:16px 18px}
pre{background:var(--code-bg);color:var(--code-ink);border-radius:8px;
padding:14px 16px;overflow-x:auto;font-family:"SF Mono",Consolas,monospace;
font-size:12.5px;line-height:1.6;margin:0;white-space:pre-wrap;word-break:break-word}
code{font-family:"SF Mono",Consolas,monospace;font-size:13px}
.dot{display:inline-block;width:8px;height:8px;border-radius:4px;margin-right:7px}
.dot.y{background:var(--ok)}
.dot.n{background:var(--bad)}
a{color:var(--accent)}
.foot{color:var(--soft);font-size:12px;text-align:center;margin-top:28px}
"""


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def section(title: str, body: str) -> str:
    return '<div class="card"><h2>' + esc(title) + "</h2>" + body + "</div>"


def render(d: dict) -> str:
    verdict = str(d.get("verdict", "DONE")).upper()
    label, tone = VERDICT_STYLE.get(verdict, (verdict, "warn"))
    role_cn = "审核报告" if d.get("role") == "reviewer" else "实现报告"
    when = d.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M")
    title = d.get("title", "SuperRAN 报告")

    meta = []
    for key, name in (("branch", "分支"), ("sha", "SHA"), ("risk", "风险档"),
                      ("seat", "席位"), ("mechanism", "物理机制")):
        if d.get(key):
            val = str(d[key])[:12] if key == "sha" else d[key]
            meta.append("<span>" + name + " <b>" + esc(val) + "</b></span>")

    parts = [
        "<h1>" + esc(title) + "</h1>",
        '<p class="sub">' + role_cn + " · " + esc(when)
        + ' · <span class="badge ' + tone + '">' + esc(label) + "</span></p>",
        '<div class="meta">' + "".join(meta) + "</div>",
    ]

    if d.get("conclusion"):
        parts.append(section("结论", '<p class="lead">' + esc(d["conclusion"]) + "</p>"))

    if d.get("chain"):
        items = "".join(
            "<li><b>" + esc(c.get("step", "")) + "</b>"
            '<span class="d">' + esc(c.get("detail", "")) + "</span></li>"
            for c in d["chain"]
        )
        parts.append(section("改了哪个无线环节（物理因果链）",
                             '<ol class="chain">' + items + "</ol>"))

    if d.get("changes"):
        rows = "".join(
            "<tr><td><b>" + esc(c.get("what", "")) + "</b></td><td>"
            + esc(c.get("why", "")) + "</td></tr>"
            for c in d["changes"]
        )
        parts.append(section(
            "做了什么",
            '<div class="scroll"><table><tr><th>改动</th><th>为什么</th></tr>'
            + rows + "</table></div>"))

    if d.get("findings"):
        rows = "".join(
            "<tr><td><b>" + esc(f.get("issue", "")) + "</b></td><td>"
            + esc(f.get("impact", "")) + "</td><td>"
            + esc(f.get("evidence", "")) + "</td></tr>"
            for f in d["findings"]
        )
        parts.append(section(
            "发现的问题",
            '<div class="scroll"><table><tr><th>问题</th>'
            "<th>物理后果 / KPI 影响</th><th>证据</th></tr>"
            + rows + "</table></div>"))

    evidence = d.get("evidence") or []
    not_proved = d.get("not_proved") or []
    if evidence or not_proved:
        body = ""
        if evidence:
            rows = "".join(
                '<tr><td><span class="dot '
                + ("y" if e.get("proved", True) else "n")
                + '"></span>' + esc(e.get("claim", "")) + "</td><td>"
                + esc(e.get("how", "")) + "</td></tr>"
                for e in evidence
            )
            body += ('<div class="scroll"><table>'
                     "<tr><th>结论</th><th>怎么验的</th></tr>"
                     + rows + "</table></div>")
        if not_proved:
            lis = "".join("<li>" + esc(x) + "</li>" for x in not_proved)
            body += ('<div class="tint"><h3>没有证明什么</h3>'
                     '<ul class="plain">' + lis + "</ul></div>")
        parts.append(section("证据及其边界", body))

    ratchet = d.get("ratchet")
    if ratchet:
        if ratchet.get("verified_red"):
            mark = '<span class="dot y"></span>已验证：revert 修复后该测试变红'
        else:
            mark = ('<span class="dot n"></span>'
                    "<b>未验证红态 —— 不满足棘轮要求</b>")
        parts.append(section(
            "棘轮测试（防止同类问题复发）",
            "<p><code>" + esc(ratchet.get("test", "")) + "</code></p><p>"
            + mark + "</p>"))

    for key, sec_title in (("risks", "剩余风险"), ("decisions", "需要你决定什么")):
        if d.get(key):
            lis = "".join("<li>" + esc(x) + "</li>" for x in d[key])
            body = '<ul class="plain">' + lis + "</ul>"
            if key == "decisions":
                body = '<div class="tint">' + body + "</div>"
            parts.append(section(sec_title, body))

    if d.get("handoff"):
        parts.append(section("转发给 Reviewer（原样复制这段）",
                             "<pre>" + esc(d["handoff"]) + "</pre>"))

    for det in d.get("details") or []:
        parts.append(
            "<details><summary>" + esc(det.get("label", "技术细节"))
            + '</summary><div class="body"><pre>'
            + esc(det.get("content", "")) + "</pre></div></details>")

    parts.append('<p class="foot">SuperRAN · 由 scripts/make_agent_report.py 生成 · '
                 '<a href="index.html">返回总索引</a></p>')

    doc = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           "<title>" + esc(title) + "</title><style>" + CSS + "</style>@@KATEX@@"
           '</head><body><div class="wrap">' + "".join(parts)
           + "</div></body></html>")

    # 单个 $ 太容易误伤（路径、金额），所以显式 "math": true 或出现块级定界符才注入
    needs_math = bool(d.get("math")) or bool(re.search(r"\$\$|\\\(|\\\[", doc))
    if needs_math and KATEX_HEADER.exists():
        doc = doc.replace("@@KATEX@@", KATEX_HEADER.read_text(encoding="utf-8"))
    return doc.replace("@@KATEX@@", "")


def load_manifest() -> list:
    path = REPORT_DIR / "index.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(entries: list) -> None:
    (REPORT_DIR / "index.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_index(entries: list) -> Path:
    entries = sorted(entries, key=lambda e: e.get("date", ""), reverse=True)
    rows = []
    for e in entries:
        label, tone = VERDICT_STYLE.get(
            str(e.get("verdict", "")).upper(), (e.get("verdict", ""), "warn"))
        rows.append(
            "<tr><td>" + esc(str(e.get("date", ""))[:16]) + "</td>"
            '<td><a href="' + esc(e.get("file", "")) + '">'
            + esc(e.get("title", "")) + "</a></td>"
            "<td>" + ("审核" if e.get("role") == "reviewer" else "实现") + "</td>"
            "<td>" + esc(e.get("risk", "")) + "</td>"
            '<td><span class="badge ' + tone + '">' + esc(label) + "</span></td>"
            "<td><code>" + esc(str(e.get("sha", ""))[:8]) + "</code></td></tr>")

    doc = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           "<title>SuperRAN 报告总索引</title><style>" + CSS + "</style></head>"
           '<body><div class="wrap"><h1>SuperRAN 报告总索引</h1>'
           '<p class="sub">共 ' + str(len(entries)) + " 份 · 最近更新 "
           + datetime.now().strftime("%Y-%m-%d %H:%M") + "</p>"
           '<div class="card"><div class="scroll"><table>'
           "<tr><th>时间</th><th>标题</th><th>类型</th><th>风险</th>"
           "<th>结论</th><th>SHA</th></tr>" + "".join(rows)
           + "</table></div></div>"
           '<p class="foot">每份报告写入时自动追加到这里</p>'
           "</div></body></html>")

    out = REPORT_DIR / "index.html"
    out.write_text(doc, encoding="utf-8")
    return out


def main() -> int:
    # Windows 控制台默认 cp1252，不重设会在打印中文路径时崩掉
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    raw_slug = payload.get("slug") or payload.get("title", "report")
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", raw_slug).strip("-")[:40]
    role = "reviewer" if payload.get("role") == "reviewer" else "author"
    name = stamp + "-" + role + "-" + slug + ".html"

    out = REPORT_DIR / name
    out.write_text(render(payload), encoding="utf-8")

    entries = [e for e in load_manifest() if e.get("file") != name]
    entries.append({
        "file": name,
        "title": payload.get("title", ""),
        "role": role,
        "verdict": payload.get("verdict", ""),
        "risk": payload.get("risk", ""),
        "sha": payload.get("sha", ""),
        "branch": payload.get("branch", ""),
        "date": payload.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save_manifest(entries)
    index = refresh_index(entries)

    print("绝对路径：" + str(out))
    print("绝对路径：" + str(index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

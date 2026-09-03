"""用真实 Chromium 打开生成的开发者文档，检查页面真的能读。

`docs/index.html` 是自包含单文件站点：导航、搜索、阅读深度切换和 KaTeX 升级
全靠内嵌脚本。**这些都不是结构检查能覆盖的**——HTML 结构完全合法、
`node --check` 也过，页面照样可能在浏览器里白屏或者公式退回裸 LaTeX。
`tests/test_developer_guide.py` 管结构与计数，这个脚本管"打开之后长什么样"。

检查项（任一不过就非零退出）：

* 指定章节在 1440 / 768 / 375 三个视口下都能定位到、可见；
* 没有横向溢出（`scrollWidth > clientWidth` 是移动端最常见的破相）；
* 每个 `.kx` 容器都升级成了 KaTeX（`kx-ok`），没有停在 MathML 兜底上；
* 页面没有 console error / pageerror；
* 内嵌 SVG 真的画出了框和文字，不是一个空的 `<figure>`。

    python scripts/run_docs_browser_qa.py                # 默认查 dlamc 章
    python scripts/run_docs_browser_qa.py --page bfgain  # 换一章
    python scripts/run_docs_browser_qa.py --shots out/   # 顺便存截图

浏览器可执行文件按顺序找：`--chromium` 参数 → `PLAYWRIGHT_CHROMIUM`
环境变量 → `%LOCALAPPDATA%\\ms-playwright` 下已缓存的 `chromium-*`。
找不到就明确报缺什么，**不静默跳过**——静默跳过的视觉门等于没有。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "index.html"
VIEWPORTS = (("desktop", 1440, 900), ("tablet", 768, 1024), ("mobile", 375, 812))

PROBE = """() => {
    const a = document.querySelector('article[data-page="__PAGE__"]');
    if (!a) return {found: false};
    const kx = a.querySelectorAll('.kx');
    let rendered = 0;
    kx.forEach(e => { if (e.classList.contains('kx-ok')) rendered++; });
    const svgs = a.querySelectorAll('figure.diagram svg');
    let boxes = 0, texts = 0;
    svgs.forEach(s => {
        boxes += s.querySelectorAll('rect').length;
        texts += s.querySelectorAll('text').length;
    });
    const h1 = a.querySelector('h1');
    return {
        found: true,
        visible: !a.hasAttribute('hidden'),
        title: h1 ? h1.textContent : '',
        formulaCount: kx.length,
        katexRendered: rendered,
        diagrams: svgs.length,
        svgRects: boxes,
        svgTexts: texts,
        tables: a.querySelectorAll('table').length,
        callouts: a.querySelectorAll('.callout').length,
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
    };
}"""


def find_chromium(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"--chromium 指向的文件不存在：{path}")
        return path
    env = os.environ.get("PLAYWRIGHT_CHROMIUM", "").strip()
    if env:
        path = Path(env)
        if not path.is_file():
            raise SystemExit(f"PLAYWRIGHT_CHROMIUM 指向的文件不存在：{path}")
        return path
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    cache = Path(local) / "ms-playwright"
    # 本机常已经有别的工具装过 chromium；优先用它，不为跑一次检查再下一份。
    candidates = sorted(cache.glob("chromium-*/chrome-win64/chrome.exe"))
    candidates += sorted(cache.glob("chromium-*/chrome-linux/chrome"))
    candidates += sorted(cache.glob("chromium-*/chrome-mac/Chromium.app/"
                                    "Contents/MacOS/Chromium"))
    if not candidates:
        raise SystemExit(
            f"没找到 Chromium。装一个（playwright install chromium）"
            f"或用 --chromium / PLAYWRIGHT_CHROMIUM 指路；搜过的目录：{cache}")
    return candidates[-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", default="dlamc", help="要检查的逻辑页 key")
    parser.add_argument("--chromium", default=None)
    parser.add_argument("--shots", default=None, help="截图输出目录")
    parser.add_argument("--settle-ms", type=int, default=1800,
                        help="等 KaTeX 升级脚本跑完的时间")
    parser.add_argument(
        "--mark", action="store_true",
        help="全部通过时写 %%TEMP%%/.e2e-tested 标记（本机 commit 钩子读它）。"
             "只在真的通过时写；有任何 problem 都不写。")
    args = parser.parse_args()

    if not GUIDE.is_file():
        raise SystemExit(f"文档还没生成：{GUIDE}（先跑 make_developer_guide.py）")
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:  # noqa: BLE001
        raise SystemExit(
            "缺 playwright（pip install -e .[dev] 或 pip install playwright）"
        ) from exc

    exe = find_chromium(args.chromium)
    shots = Path(args.shots).resolve() if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    report: dict[str, object] = {
        "guide": str(GUIDE), "page": args.page, "chromium": str(exe)}
    errors: list[str] = []
    probe = PROBE.replace("__PAGE__", args.page)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(exe))
        try:
            for name, width, height in VIEWPORTS:
                page = browser.new_page(
                    viewport={"width": width, "height": height})
                page.on("console", lambda m: (
                    errors.append(f"{m.type}: {m.text}")
                    if m.type == "error" else None))
                page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
                page.goto(f"{GUIDE.as_uri()}#/{args.page}", wait_until="load")
                page.wait_for_timeout(args.settle_ms)
                info = page.evaluate(probe)
                info["horizontal_overflow"] = bool(
                    int(info.get("scrollWidth", 0))
                    > int(info.get("clientWidth", 0)) + 1)
                if shots:
                    shot = shots / f"{args.page}-{name}.png"
                    page.screenshot(path=str(shot),
                                    full_page=(name == "desktop"))
                    info["screenshot"] = str(shot)
                report[name] = info
                page.close()
        finally:
            browser.close()

    report["console_errors"] = errors
    problems: list[str] = []
    for name, _w, _h in VIEWPORTS:
        info = report[name]
        assert isinstance(info, dict)
        if not info.get("found"):
            problems.append(f"{name}: 页面上没有 data-page={args.page!r} 这一章")
            continue
        if not info.get("visible"):
            problems.append(f"{name}: 章节存在但没有显示出来")
        if info.get("horizontal_overflow"):
            problems.append(
                f"{name}: 横向溢出 {info['scrollWidth']} > {info['clientWidth']}")
        if int(info.get("katexRendered", 0)) < int(info.get("formulaCount", 0)):
            problems.append(
                f"{name}: KaTeX 只升级了 "
                f"{info.get('katexRendered')}/{info.get('formulaCount')} 条公式")
        if int(info.get("diagrams", 0)) and not int(info.get("svgTexts", 0)):
            problems.append(f"{name}: 内嵌 SVG 一个文字都没画出来")
    if errors:
        problems.append(f"console 报错 {len(errors)} 条：{errors[:3]}")

    report["problems"] = problems
    if args.mark and not problems:
        # 本机的 commit 钩子要求 UI 文件必须有真实浏览器测试记录。它认
        # ``%TEMP%/.e2e-tested`` 里 2 小时内的时间戳。**只有真的全过才写**，
        # 并把是谁写的记进去——标记要能追回到一次具体的、可复跑的检查。
        import tempfile
        import time

        marker = Path(tempfile.gettempdir()) / ".e2e-tested"
        marker.write_text(json.dumps({
            "ts": time.time(),
            "tool": "scripts/run_docs_browser_qa.py",
            "page": args.page,
            "viewports": [f"{w}x{h}" for _n, w, h in VIEWPORTS],
            "chromium": str(exe),
        }, ensure_ascii=False), encoding="utf-8")
        report["e2e_marker"] = str(marker)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("PROBLEMS:", problems if problems else "none")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

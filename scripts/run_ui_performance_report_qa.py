"""Real-browser QA for the overnight UI/performance handoff report."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "artifacts" / "reports" / "SUPERRAN_UI_PERFORMANCE_DEEP_DIVE.html"
OUT = ROOT / "output" / "ui-performance-report-qa.json"


def main() -> None:
    result: dict = {"report": str(REPORT), "browser_backend": "", "viewports": {}, "errors": []}
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
            result["browser_backend"] = "playwright_chromium"
        except PlaywrightError as exc:
            if "Executable doesn't exist" not in str(exc):
                raise
            browser = pw.chromium.launch(channel="msedge", headless=True)
            result["browser_backend"] = "system_msedge_fallback"
        for name, viewport in {
            "desktop": {"width": 1440, "height": 900},
            "mobile": {"width": 375, "height": 812},
        }.items():
            page = browser.new_page(viewport=viewport)
            page.on("pageerror", lambda exc, n=name: result["errors"].append(
                f"{n}:page:{exc}"))
            page.on("console", lambda msg, n=name: result["errors"].append(
                f"{n}:console:{msg.type}:{msg.text}") if msg.type == "error" else None)
            page.goto(REPORT.resolve().as_uri(), wait_until="load")
            text = page.locator("body").inner_text()
            overflow = page.evaluate(
                "document.documentElement.scrollWidth-document.documentElement.clientWidth")
            checks = {
                "horizontal_overflow_px": overflow,
                "section_count": page.locator("section[id]").count(),
                "embedded_screenshot_count": page.locator("figure[data-source] img").count(),
                "research_source_count": page.locator(".sources a[href^='https://']").count(),
                "navigation_link_count": page.locator("nav a[href^='#']").count(),
                "contains_implemented_and_rejected": (
                    "本轮已落地优化点" in text and "没有落地的“优化”" in text),
                "contains_user_inputs": "你提供后可大幅优化的信息" in text,
            }
            screenshot = REPORT.with_name(
                f"SUPERRAN_UI_PERFORMANCE_DEEP_DIVE-{name}.png")
            page.screenshot(path=screenshot, full_page=False)
            checks["screenshot"] = str(screenshot)
            checks["pass"] = bool(
                overflow <= 0
                and checks["section_count"] >= 8
                and checks["embedded_screenshot_count"] == 4
                and checks["research_source_count"] >= 8
                and checks["navigation_link_count"] >= 8
                and checks["contains_implemented_and_rejected"]
                and checks["contains_user_inputs"]
            )
            result["viewports"][name] = checks
            page.close()
        browser.close()
    result["pass"] = bool(
        not result["errors"] and all(row["pass"] for row in result["viewports"].values()))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT)
    if not result["pass"]:
        raise SystemExit("UI/performance report QA failed")


if __name__ == "__main__":
    main()

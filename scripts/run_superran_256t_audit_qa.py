"""Real-Chromium responsive and interaction QA for the final audit HTML."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "artifacts" / "SUPERRAN_256T_MIGRATION_AUDIT.html"
OUT = ROOT / "artifacts" / "superran-256t-audit-qa"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    OUT.mkdir(parents=True, exist_ok=True)
    viewports = {
        "desktop": {"width": 1440, "height": 900},
        "tablet": {"width": 768, "height": 1024},
        "mobile": {"width": 390, "height": 844},
    }
    result: dict = {"report": str(REPORT), "viewports": {}, "errors": []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, viewport in viewports.items():
            page = browser.new_page(viewport=viewport)
            page.on(
                "console",
                lambda msg, n=name: result["errors"].append(
                    f"{n}:console:{msg.type}:{msg.text}"
                )
                if msg.type == "error"
                else None,
            )
            page.on(
                "pageerror",
                lambda exc, n=name: result["errors"].append(f"{n}:page:{exc}"),
            )
            page.goto(REPORT.as_uri(), wait_until="load")
            page.wait_for_selector("#verdict")
            page.locator('a[href="#mapping"]').click()
            page.wait_for_function("location.hash === '#mapping'")
            page.locator("#mapping").wait_for(state="visible")
            metrics = page.evaluate(
                """() => ({
                  documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                  sectionCount: document.querySelectorAll('section[id]').length,
                  navTargetCount: [...document.querySelectorAll('.sidebar a[href^="#"]')]
                    .filter(a => document.querySelector(a.getAttribute('href'))).length,
                  navCount: document.querySelectorAll('.sidebar a[href^="#"]').length,
                  svgCount: document.querySelectorAll('svg[viewBox]').length,
                  markerCount: (document.body.innerText.match(/__[A-Z_]+__/g) || []).length,
                  title: document.title,
                  currentHash: location.hash,
                  heroWidth: document.querySelector('.hero').getBoundingClientRect().width,
                  viewportWidth: innerWidth
                })"""
            )
            screenshot = OUT / f"{name}.png"
            page.screenshot(path=str(screenshot), full_page=False)
            checks = {
                **metrics,
                "screenshot": str(screenshot),
                "pass": bool(
                    metrics["documentOverflow"] <= 1
                    and metrics["sectionCount"] == 11
                    and metrics["navTargetCount"] == metrics["navCount"] == 11
                    and metrics["svgCount"] >= 3
                    and metrics["markerCount"] == 0
                    and metrics["currentHash"] == "#mapping"
                    and metrics["heroWidth"] <= metrics["viewportWidth"] + 1
                ),
            }
            result["viewports"][name] = checks
            page.close()
        browser.close()
    result["pass"] = bool(
        all(item["pass"] for item in result["viewports"].values())
        and not result["errors"]
    )
    manifest = OUT / "qa.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

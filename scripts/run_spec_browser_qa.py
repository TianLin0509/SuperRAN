"""Render and exercise the real SuperRAN run-before specification workbench."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import plan, spec  # noqa: E402

OUT = ROOT / "output" / "spec-browser-qa.json"
DOC_ASSET_DIR = ROOT / "docs" / "assets" / "ui"


def _valid_image(path: Path) -> tuple[bool, str]:
    raw = path.read_bytes()
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return path.stat().st_size > 20_000, ".png"
    if b"<svg" in raw[:500]:
        return path.stat().st_size > 20_000, ".svg"
    return False, path.suffix.lower()


def main() -> None:
    cfg = dict(plan.load_presets()["company_64t4r_multicell"]["config"])
    result = spec.write_spec(
        cfg,
        num_samples=100,
        user_set=["power_constraint", "channel_est_mode", "num_samples"],
        title="SuperRAN · 64T SRS/PMI 运行前工作台",
        highlight=["power_constraint", "channel_est_mode", "srs_period_ms"],
        serve=True,
        open_browser=False,
    )
    DOC_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    download_dir = ROOT / "output" / "spec-browser-downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    qa: dict = {
        "viewports": {}, "errors": [], "browser_backend": "", "report": result}
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
            qa["browser_backend"] = "playwright_chromium"
        except PlaywrightError as exc:
            if "Executable doesn't exist" not in str(exc):
                raise
            browser = pw.chromium.launch(channel="msedge", headless=True)
            qa["browser_backend"] = "system_msedge_fallback"
        for name, viewport in {
            "desktop": {"width": 1440, "height": 900},
            "mobile": {"width": 375, "height": 812},
        }.items():
            page = browser.new_page(viewport=viewport, accept_downloads=True)
            page.on(
                "pageerror", lambda exc, n=name: qa["errors"].append(f"{n}:page:{exc}"))
            page.on(
                "console",
                lambda msg, n=name: qa["errors"].append(
                    f"{n}:console:{msg.type}:{msg.text}")
                if msg.type == "error" else None,
            )
            page.goto(result["url"] or Path(result["html_path"]).resolve().as_uri(),
                      wait_until="load")
            page.wait_for_selector(".tabs")
            overflow_overview = page.evaluate(
                "document.documentElement.scrollWidth-document.documentElement.clientWidth")
            overview_shot = ROOT / "artifacts" / "specs" / f"spec-browser-qa-{name}-overview.png"
            page.screenshot(path=overview_shot, full_page=False)
            if name == "desktop":
                page.screenshot(
                    path=DOC_ASSET_DIR / "spec-workbench-overview.png", full_page=False)

            page.locator('label[for="tb2"]').click()
            page.wait_for_selector("#pn2", state="visible")
            original_isd = float(page.locator('[data-k="isd_m"]').input_value())
            page.locator('[data-k="isd_m"]').fill(str(original_isd + 100.0))
            page.wait_for_function(
                "document.querySelector('#change-count').textContent.startsWith('1 ')")
            impact_active = page.locator(".impact-chip.on").count()
            apply_visible = page.locator("#ap").is_visible()
            overflow_config = page.evaluate(
                "document.documentElement.scrollWidth-document.documentElement.clientWidth")
            config_shot = ROOT / "artifacts" / "specs" / f"spec-browser-qa-{name}-config.png"
            page.screenshot(path=config_shot, full_page=False)
            if name == "desktop":
                page.screenshot(
                    path=DOC_ASSET_DIR / "spec-workbench-config.png", full_page=False)

            checks = {
                "tab_count": page.locator('.tabs>label[for^="tb"]').count(),
                "action_count": page.locator(
                    ".page-actions .action-btn,.page-actions .download-menu>summary").count(),
                "download_count": page.locator("[data-download]").count(),
                "topology_visible": page.locator(".hero svg").first.is_visible(),
                "config_panel_visible": page.locator("#pn2").is_visible(),
                "impact_active_stages": impact_active,
                "apply_button_visible_on_loopback": apply_visible,
                "horizontal_overflow_px": max(overflow_overview, overflow_config),
                "overview_screenshot": str(overview_shot),
                "config_screenshot": str(config_shot),
                "config_download_valid": True,
                "screenshot_download_valid": True,
            }
            if name == "desktop":
                page.locator(".download-menu>summary").click()
                with page.expect_download() as event:
                    page.locator('[data-download="config.json"]').click()
                config_path = download_dir / event.value.suggested_filename
                event.value.save_as(config_path)
                config = json.loads(config_path.read_text(encoding="utf-8"))
                checks["config_download_valid"] = bool(
                    config.get("power_constraint") == "nebf"
                    and int(config.get("num_rb", 272)) == 272
                )
                with page.expect_download(timeout=30_000) as event:
                    page.locator('[data-action="screenshot"]').click()
                image_path = download_dir / event.value.suggested_filename
                event.value.save_as(image_path)
                valid_image, image_format = _valid_image(image_path)
                checks["screenshot_download_valid"] = valid_image
                checks["screenshot_format"] = image_format
            checks["pass"] = bool(
                checks["tab_count"] == 7
                and checks["action_count"] == 5
                and checks["download_count"] == 2
                and checks["topology_visible"]
                and checks["config_panel_visible"]
                and checks["impact_active_stages"] == 4
                and checks["apply_button_visible_on_loopback"]
                and checks["horizontal_overflow_px"] <= 0
                and checks["config_download_valid"]
                and checks["screenshot_download_valid"]
            )
            qa["viewports"][name] = checks
            page.close()
        browser.close()
    qa["pass"] = bool(
        not qa["errors"] and all(row["pass"] for row in qa["viewports"].values()))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["html_path"])
    print(OUT)
    if not qa["pass"]:
        raise SystemExit("Specification browser QA failed; inspect output/spec-browser-qa.json")


if __name__ == "__main__":
    main()

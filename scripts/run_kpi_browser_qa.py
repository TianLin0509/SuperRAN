"""Generate a reproducible two-tab KPI report for browser QA.

The channel and traffic CDFs in this script are synthetic.  The run validates
presentation and accounting contracts; it is not a field-performance claim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import kpi_view  # noqa: E402
from superran import system as sy  # noqa: E402

OUT = ROOT / "output" / "kpi-browser-qa.json"
DOC_ASSET_DIR = ROOT / "docs" / "assets" / "ui"
SIZE_CDF = ROOT / "presets" / "traffic" / "synthetic_packet_size.csv"
INTERVAL_CDF = ROOT / "presets" / "traffic" / "synthetic_interarrival_ms.csv"


def _browser_qa(html_path: str) -> dict:
    """Exercise both KPI tabs in isolated Chromium at desktop/mobile widths."""
    report: dict = {"viewports": {}, "errors": [], "browser_backend": ""}
    DOC_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    target = Path(html_path).resolve().as_uri()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
            report["browser_backend"] = "playwright_chromium"
        except PlaywrightError as exc:
            # 开发机常有系统 Edge、但没有额外下载 Playwright Chromium。
            # 使用同一 Chromium API 的系统通道可完成等价 DOM/布局 QA；降级留痕。
            if "Executable doesn't exist" not in str(exc):
                raise
            browser = pw.chromium.launch(channel="msedge", headless=True)
            report["browser_backend"] = "system_msedge_fallback"
        for name, viewport in {
            "desktop": {"width": 1440, "height": 900},
            "mobile": {"width": 375, "height": 812},
        }.items():
            page = browser.new_page(viewport=viewport)
            page.on(
                "console",
                lambda msg, n=name: report["errors"].append(
                    f"{n}:console:{msg.type}:{msg.text}"
                )
                if msg.type == "error"
                else None,
            )
            page.on(
                "pageerror",
                lambda exc, n=name: report["errors"].append(f"{n}:page:{exc}"),
            )
            page.goto(target, wait_until="load")
            page.wait_for_selector(".scope-tabs")
            page.wait_for_selector("#cell-panel", state="visible")

            cell_checked = page.locator("#scope-cell").is_checked()
            cell_visible = page.locator("#cell-panel").is_visible()
            user_hidden_before = not page.locator("#user-panel").is_visible()
            priority_visible = page.locator(".priority").is_visible()
            priority_compact = (
                not page.locator(".priority details").evaluate("el=>el.open")
                and page.locator(".priority").bounding_box()["height"]
                < (180 if name == "desktop" else 260)
            )
            tab_count = page.locator('.scope-tabs label[for^="scope-"]').count()
            action_count = page.locator(
                ".page-actions .action-btn,.page-actions .download-menu>summary").count()
            download_count = page.locator(".page-actions [data-download]").count()
            cell_text = page.locator("#cell-panel").inner_text()
            overflow_before = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            cell_shot = ROOT / "artifacts" / "kpi" / f"kpi-browser-qa-{name}-cell.png"
            page.screenshot(path=str(cell_shot), full_page=False)
            if name == "desktop":
                page.screenshot(
                    path=str(DOC_ASSET_DIR / "kpi-workbench-cell.png"), full_page=False)

            page.locator('label[for="scope-user"]').click()
            page.wait_for_function("document.querySelector('#scope-user').checked")
            page.wait_for_selector("#user-panel", state="visible")
            user_checked = page.locator("#scope-user").is_checked()
            user_visible = page.locator("#user-panel").is_visible()
            cell_hidden_after = not page.locator("#cell-panel").is_visible()
            user_metric_panels = page.locator("#user-panel .metric-panel").count()
            overflow_after = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            user_shot = ROOT / "artifacts" / "kpi" / f"kpi-browser-qa-{name}-user.png"
            page.screenshot(path=str(user_shot), full_page=False)
            if name == "desktop":
                page.screenshot(
                    path=str(DOC_ASSET_DIR / "kpi-workbench-user.png"), full_page=False)

            action_checks = {
                "action_button_count": action_count,
                "download_option_count": download_count,
                "json_download_valid": True,
                "cell_csv_download_valid": True,
                "screenshot_download_valid": True,
            }
            if name == "desktop":
                downloads = ROOT / "output" / "kpi-browser-downloads"
                downloads.mkdir(parents=True, exist_ok=True)
                page.locator(".download-menu>summary").click()
                with page.expect_download() as event:
                    page.locator('[data-download="result.json"]').click()
                json_path = downloads / "result.json"
                event.value.save_as(json_path)
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                action_checks["json_download_valid"] = bool(
                    payload.get("schema") == "superran_kpi_export_v1"
                    and payload.get("result", {}).get("cell")
                )
                page.locator(".download-menu>summary").click()
                with page.expect_download() as event:
                    page.locator('[data-download="cell.csv"]').click()
                csv_path = downloads / "cell.csv"
                event.value.save_as(csv_path)
                csv_text = csv_path.read_text(encoding="utf-8-sig")
                action_checks["cell_csv_download_valid"] = bool(
                    "scope,key,label,mean" in csv_text
                    and "serving_cell_prb_utilization" in csv_text
                )
                with page.expect_download(timeout=30_000) as event:
                    page.locator('[data-action="screenshot"]').click()
                image_path = downloads / event.value.suggested_filename
                event.value.save_as(image_path)
                image_bytes = image_path.read_bytes()
                action_checks["screenshot_format"] = image_path.suffix.lower()
                action_checks["screenshot_download_valid"] = bool(
                    image_path.stat().st_size > 20_000
                    and (image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
                         or b"<svg" in image_bytes[:500])
                )

            checks = {
                "cell_checked_initially": cell_checked,
                "cell_visible_initially": cell_visible,
                "user_hidden_initially": user_hidden_before,
                "agent_priority_visible": priority_visible,
                "agent_priority_compact": priority_compact,
                "scope_tab_count": tab_count,
                "cell_has_prb_utilization": "本小区 PRB 利用率" in cell_text,
                "cell_has_mu_share": "MU 配对占已用 PRB" in cell_text,
                "user_checked_after_click": user_checked,
                "user_visible_after_click": user_visible,
                "cell_hidden_after_click": cell_hidden_after,
                "user_metric_panels": user_metric_panels,
                "horizontal_overflow_px": max(overflow_before, overflow_after),
                "cell_screenshot": str(cell_shot),
                "user_screenshot": str(user_shot),
                **action_checks,
            }
            checks["pass"] = bool(
                cell_checked
                and cell_visible
                and user_hidden_before
                and priority_visible
                and priority_compact
                and tab_count == 2
                and action_count == 5
                and download_count == 3
                and checks["cell_has_prb_utilization"]
                and checks["cell_has_mu_share"]
                and user_checked
                and user_visible
                and cell_hidden_after
                and user_metric_panels > 0
                and checks["horizontal_overflow_px"] <= 0
                and checks["json_download_valid"]
                and checks["cell_csv_download_valid"]
                and checks["screenshot_download_valid"]
            )
            report["viewports"][name] = checks
            page.close()
        browser.close()
    report["pass"] = bool(
        not report["errors"]
        and all(v["pass"] for v in report["viewports"].values())
    )
    return report


def _synthetic_tables() -> list[sy.UeLinkTable]:
    random = np.random.default_rng(20260809)
    channels = [
        (
            random.standard_normal((8, 24, 16, 4))
            + 1j * random.standard_normal((8, 24, 16, 4))
        )
        / np.sqrt(2.0)
        for _ in range(6)
    ]
    return sy.build_link_tables(
        channels,
        [22.0, 19.0, 16.0, 12.0, 8.0, 4.0],
        max_rank=4,
        rb_per_rbg=16,
        power_constraint="nebf",
        mu_enabled=True,
        mu_rank_per_user=2,
        mu_precoder="zf",
    )


def main() -> None:
    tables = _synthetic_tables()
    traffic = sy.TrafficConfig(
        model="cdf",
        interarrival_cdf_unit="ms",
        classes=(
            sy.TrafficClassConfig(
                name="video",
                ue_share=0.0,
                file_bytes=500_000,
                arrival_rate_hz=0.0,
                packet_size_cdf=str(SIZE_CDF),
                interarrival_cdf=str(INTERVAL_CDF),
                packet_size_scale=1.2,
                interarrival_scale=1.0,
                ue_ids=(0, 1, 2, 3),
            ),
            sy.TrafficClassConfig(
                name="xr",
                ue_share=0.0,
                file_bytes=1_500,
                arrival_rate_hz=0.0,
                pdb_ms=20.0,
                resource_type="delay_critical_GBR",
                is_small=True,
                packet_size_cdf=str(SIZE_CDF),
                interarrival_cdf=str(INTERVAL_CDF),
                packet_size_scale=0.12,
                interarrival_scale=0.45,
                ue_ids=(4, 5),
            ),
        ),
    )
    system = sy.SystemConfig(
        duration_s=1.2,
        tdd_pattern="DDDSU",
        seed=20260809,
    )
    scheduler = sy.SchedulerConfig(
        algorithm="pf",
        pf_accounting="scheduled_tbs",
        mu_enabled=True,
        max_mu_users=2,
        mu_rank_per_user=2,
        mu_corr_threshold=0.99,
        mu_precoder="zf",
    )
    kpi = sy.KpiConfig(warmup_s=0.2)
    calibration = sy.calibrate_traffic_to_prb(
        tables,
        target_prb_utilization=0.50,
        axis="interarrival",
        tolerance=0.04,
        max_iterations=5,
        probe_replications=2,
        formal_refinements=2,
        num_replications=8,
        master_seed=20260809,
        sys_cfg=system,
        traffic=traffic,
        sched=scheduler,
        kpi=kpi,
    )
    result = calibration.result.as_dict()
    result["dataset_id"] = "synthetic-kpi-browser-qa"
    result["traffic_calibration"] = calibration.as_dict()
    if str(calibration.as_dict().get("status")) != "target_met":
        raise SystemExit(
            "话务校准未达标（status="
            f"{calibration.as_dict().get('status')!r}），"
            "本页不能作为目标利用率的校准证据")
    result.setdefault("notes", []).append(
        "本页只使用合成信道和示例 CDF，验证呈现、校准和统计口径；"
        "不代表实测 CDF、现网负载或算法收益。"
    )
    report = kpi_view.write_kpi_report(
        result,
        dataset_id=result["dataset_id"],
        serve=False,
        kpi_focus=[
            "serving_cell_prb_utilization",
            "mu_paired_prb_share_of_used",
            "first_packet_delay_ms_p95",
            "experienced_mbps",
            "XR",
        ],
    )
    browser_qa = _browser_qa(report["html_path"])
    manifest = {
        "report": report,
        "calibration": calibration.as_dict(),
        "browser_qa": browser_qa,
        "headline": {
            key: result["cell"].get(key)
            for key in (
                "cell_experienced_mbps",
                "first_packet_delay_ms_p95",
                "serving_cell_prb_utilization",
                "mu_paired_prb_share_of_used",
            )
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report["html_path"])
    print(OUT)
    if not browser_qa["pass"]:
        raise SystemExit("KPI browser QA failed; inspect output/kpi-browser-qa.json")


if __name__ == "__main__":
    main()

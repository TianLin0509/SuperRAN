"""Real-browser QA for the 3-arm KPI comparison and one-TTI drill-down.

All channels and traffic are synthetic.  This script validates product/data
contracts only; it never publishes an algorithm-performance conclusion.
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

from superran import (  # noqa: E402
    kpi_compare,
    kpi_view,
)
from superran import system as sy  # noqa: E402

OUT = ROOT / "output" / "kpi-compare-browser-qa.json"
DOC_ASSET_DIR = ROOT / "docs" / "assets" / "ui"


def _tables() -> list[sy.UeLinkTable]:
    random = np.random.default_rng(20260824)
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
        mu_enabled=False,
    )


def _result(
    tables: list[sy.UeLinkTable], *, label: str, scheduler: sy.SchedulerConfig
) -> dict:
    result = sy.simulate_replications(
        tables,
        num_replications=8,
        master_seed=20260824,
        sys_cfg=sy.SystemConfig(
            duration_s=0.8, tdd_pattern="DDDSU"),
        traffic=sy.TrafficConfig(
            model="mixed",
            file_bytes=220_000,
            arrival_rate_hz=2.5,
            small_ue_share=0.5,
            small_file_bytes=1_500,
            small_arrival_rate_hz=45.0,
            small_pdb_ms=20.0,
        ),
        sched=scheduler,
        kpi=sy.KpiConfig(
            warmup_s=0.1, tti_trace_mode="sampled", tti_trace_max_points=96),
    ).as_dict()
    result["dataset_id"] = "synthetic-kpi-compare-browser-qa"
    result["algorithm"] = {
        "label": label,
        "scheduler": scheduler.algorithm,
        "mu_enabled": scheduler.mu_enabled,
        "precoder": "svd",
        "power_constraint": "nebf",
    }
    result.setdefault("notes", []).append(
        "合成信道与合成话务只验证多算法页面、CRN 和 TTI 钻取；不代表任何算法收益。")
    return result


def _generate() -> tuple[dict, list[dict]]:
    tables = _tables()
    arms = [
        ("经典 PF", sy.SchedulerConfig(
            algorithm="pf", pf_accounting="scheduled_tbs", mu_enabled=False)),
        ("QoS-PF 候选", sy.SchedulerConfig(
            algorithm="qos_pf", pf_accounting="scheduled_tbs", mu_enabled=False,
            qos_delay_exponent=1.0)),
        ("Round Robin", sy.SchedulerConfig(
            algorithm="rr", pf_accounting="scheduled_tbs", mu_enabled=False)),
    ]
    single_reports: list[dict] = []
    for label, scheduler in arms:
        result = _result(tables, label=label, scheduler=scheduler)
        single_reports.append(kpi_view.write_kpi_report(
            result, dataset_id=result["dataset_id"], serve=False))
    comparison = kpi_compare.write_comparison_report(
        [report["result_id"] for report in single_reports],
        baseline_result_id=single_reports[0]["result_id"],
        primary_kpi="cell_experienced_mbps",
        title="合成三算法 · KPI 对比与 TTI 复盘",
        serve=False,
    )
    return comparison, single_reports


def _browser_qa(html_path: str) -> dict:
    report: dict[str, object] = {"viewports": {}, "errors": [], "browser_backend": ""}
    DOC_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    target = Path(html_path).resolve().as_uri()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
            report["browser_backend"] = "playwright_chromium"
        except PlaywrightError as exc:
            if "Executable doesn't exist" not in str(exc):
                raise
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            report["browser_backend"] = "system_msedge_fallback"
        for name, viewport in {
            "desktop": {"width": 1440, "height": 900},
            "mobile": {"width": 375, "height": 812},
        }.items():
            page = browser.new_page(viewport=viewport)
            page.on(
                "console",
                lambda message, n=name: report["errors"].append(
                    f"{n}:console:{message.type}:{message.text}")
                if message.type == "error" else None,
            )
            page.on(
                "pageerror",
                lambda error, n=name: report["errors"].append(f"{n}:page:{error}"),
            )
            page.goto(target, wait_until="load")
            page.wait_for_selector('[data-panel="overview"]', state="visible")
            tab_count = page.locator(".work-tabs button").count()
            arm_count = page.locator("[data-arm-toggle]").count()
            baseline_disabled = page.locator("[data-arm-toggle]:disabled").count() == 1
            overview_svg = page.locator("#cell-chart svg").count() == 1
            page.locator("#cell-metric").select_option("serving_cell_prb_utilization")
            percent_format = "%" in page.locator("#headline-cards .arm-card strong").first.inner_text()

            page.locator('[data-tab="matrix"]').click()
            page.wait_for_selector('[data-panel="matrix"]', state="visible")
            matrix_deltas = page.locator("#kpi-matrix .matrix-delta").count()

            page.locator('[data-tab="users"]').click()
            page.wait_for_selector('[data-panel="users"]', state="visible")
            user_cdf = page.locator("#user-chart polyline").count()

            page.locator('[data-tab="trend"]').click()
            page.wait_for_selector('[data-panel="trend"]', state="visible")
            trace_lines = page.locator("#trace-chart polyline").count()
            trace_points = page.locator("#trace-chart .trace-point").count()
            grant_points = page.locator(
                '#trace-chart .trace-point[data-has-grants="true"]')
            if grant_points.count():
                # Dense adjacent event points can overlap.  The last SVG point is the
                # visible top-most hit target; exact TTI selection is also available
                # through the dropdown and keyboard-focused circles.
                grant_points.last.click()
            page.wait_for_selector('[data-panel="tti"]', state="visible")
            tti_cards = page.locator("#tti-details .tti-card").count()
            grant_tables = page.locator("#tti-details .grant-table").count()

            page.locator('[data-tab="gates"]').click()
            page.wait_for_selector('[data-panel="gates"]', state="visible")
            gate_rows = page.locator('[data-panel="gates"] tbody tr').count()
            prereg_is_exploratory = (
                "exploratory_unregistered"
                in page.locator('[data-panel="gates"]').inner_text()
            )
            overflow = page.evaluate(
                "document.documentElement.scrollWidth-document.documentElement.clientWidth")
            screenshot = ROOT / "artifacts" / "kpi" / f"kpi-compare-qa-{name}.png"
            page.screenshot(path=str(screenshot), full_page=False)
            if name == "desktop":
                page.locator('[data-tab="overview"]').click()
                page.wait_for_selector('[data-panel="overview"]', state="visible")
                page.screenshot(
                    path=str(DOC_ASSET_DIR / "kpi-workbench-comparison.png"),
                    full_page=False,
                )
                page.locator('[data-tab="tti"]').click()
                page.wait_for_selector('[data-panel="tti"]', state="visible")
                page.screenshot(
                    path=str(DOC_ASSET_DIR / "kpi-workbench-tti-drilldown.png"),
                    full_page=False,
                )

            downloads_ok = True
            download_count = page.locator(".page-actions [data-download]").count()
            if name == "desktop":
                out_dir = ROOT / "output" / "kpi-compare-downloads"
                out_dir.mkdir(parents=True, exist_ok=True)
                for key, marker in (
                    ("comparison.json", '"schema": "superran_kpi_comparison_v1"'),
                    ("summary.csv", "effect_vs_baseline"),
                    ("tti-trace.csv", "grants_json"),
                ):
                    page.locator(".download-menu>summary").click()
                    with page.expect_download() as event:
                        page.locator(f'[data-download="{key}"]').click()
                    path = out_dir / key
                    event.value.save_as(path)
                    downloads_ok &= marker in path.read_text(encoding="utf-8-sig")

            checks = {
                "tab_count": tab_count,
                "algorithm_count": arm_count,
                "baseline_cannot_be_hidden": baseline_disabled,
                "overview_bar_chart": overview_svg,
                "percent_kpi_is_formatted_as_percent": percent_format,
                "kpi_matrix_delta_cells": matrix_deltas,
                "user_cdf_series": user_cdf,
                "tti_trace_series": trace_lines,
                "tti_trace_points": trace_points,
                "same_tti_algorithm_cards": tti_cards,
                "grant_detail_tables": grant_tables,
                "gate_candidate_rows": gate_rows,
                "unregistered_demo_stays_exploratory": prereg_is_exploratory,
                "download_count": download_count,
                "downloads_valid": downloads_ok,
                "horizontal_overflow_px": overflow,
                "screenshot": str(screenshot),
            }
            checks["pass"] = bool(
                tab_count == 6
                and arm_count == 3
                and baseline_disabled
                and overview_svg
                and percent_format
                and matrix_deltas > 0
                and user_cdf == 3
                and trace_lines == 3
                and trace_points > 0
                and tti_cards == 3
                and grant_tables > 0
                and gate_rows == 2
                and prereg_is_exploratory
                and download_count == 3
                and downloads_ok
                and overflow <= 0
            )
            report["viewports"][name] = checks
            page.close()
        browser.close()
    report["pass"] = bool(
        not report["errors"]
        and all(row["pass"] for row in report["viewports"].values())
    )
    return report


def main() -> None:
    comparison, single_reports = _generate()
    browser_qa = _browser_qa(comparison["html_path"])
    manifest = {
        "comparison": comparison,
        "single_result_ids": [report["result_id"] for report in single_reports],
        "browser_qa": browser_qa,
        "scope": "synthetic channel/traffic; UI and evidence-contract QA only",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(comparison["html_path"])
    print(OUT)
    if not browser_qa["pass"]:
        raise SystemExit("KPI comparison browser QA failed; inspect output manifest")


if __name__ == "__main__":
    main()

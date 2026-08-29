"""Real-Chromium interaction and responsive-layout QA for docs/index.html."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "index.html"
OUT = ROOT / "artifacts" / "developer-guide-qa"


def main() -> None:
    # KaTeX diagnostics can contain combining Unicode marks.  Keep reports
    # printable even when PowerShell starts Python with a cp1252 stdout.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    OUT.mkdir(parents=True, exist_ok=True)
    viewports = {
        "desktop": {"width": 1440, "height": 900},
        "tablet": {"width": 768, "height": 1024},
        "mobile": {"width": 375, "height": 812},
    }
    report: dict = {
        "guide": str(GUIDE), "viewports": {}, "errors": [], "browser_backend": ""}

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
            report["browser_backend"] = "playwright_chromium"
        except PlaywrightError as exc:
            if "Executable doesn't exist" not in str(exc):
                raise
            browser = pw.chromium.launch(channel="msedge", headless=True)
            report["browser_backend"] = "system_msedge_fallback"
        for name, viewport in viewports.items():
            # A fresh page per viewport prevents scroll/focus/transition state from
            # leaking between responsive-layout checks.
            page = browser.new_page(viewport=viewport)
            page.on("console", lambda msg, n=name: report["errors"].append(
                f"{n}:console:{msg.type}:{msg.text}") if msg.type == "error" else None)
            page.on("pageerror", lambda exc, n=name: report["errors"].append(
                f"{n}:page:{exc}"))
            page.goto(GUIDE.as_uri() + "#/overview", wait_until="load")
            page.evaluate("localStorage.removeItem('superran-doc-depth-v1')")
            page.reload(wait_until="load")
            page.wait_for_selector('.doc-page[data-page="overview"]:not([hidden])')

            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            visible_page_count = page.locator(".doc-page:not([hidden])").count()
            product_surface_count = page.locator(
                '.doc-page[data-page="overview"] [data-product-surface]:visible'
            ).count()
            product_surface_links = page.locator(
                '.doc-page[data-page="overview"] [data-product-surface] a[href^="#/"]'
            ).count()
            overview_hello_count = page.locator(
                '.doc-page[data-page="overview"] '
                '[data-hello-world="overview-entry"]:visible'
            ).count()
            overview_hello_action_count = page.locator(
                '.doc-page[data-page="overview"] '
                '[data-hello-world="overview-entry"] .hello-actions a:visible'
            ).count()
            overview_product_shot = None
            overview_mobile_shot = None
            overview_mobile_hello_shot = None
            if name == "desktop":
                overview_product_shot = OUT / "overview-products-desktop.png"
                page.locator(
                    '.doc-page[data-page="overview"] .product-showcase'
                ).screenshot(path=str(overview_product_shot))
            elif name == "mobile":
                overview_mobile_shot = OUT / "overview-mobile.png"
                page.screenshot(path=str(overview_mobile_shot), full_page=False)
                overview_mobile_hello_shot = OUT / "overview-hello-mobile.png"
                page.locator(
                    '.doc-page[data-page="overview"] .overview-hello'
                ).screenshot(path=str(overview_mobile_hello_shot))

            page.goto(GUIDE.as_uri() + "#/quickstart", wait_until="load")
            page.wait_for_selector('.doc-page[data-page="quickstart"]:not([hidden])')
            quickstart_hello_count = page.locator(
                '.doc-page[data-page="quickstart"] [data-hello-world="srs-vs-pmi"]:visible'
            ).count()
            quickstart_text = page.locator(
                '.doc-page[data-page="quickstart"]'
            ).inner_text()
            quickstart_contract = all(token in quickstart_text for token in (
                'method_a="svd"', 'method_b="type1"',
                'csi_a="srs"', 'csi_b="csirs"',
                'varies=["csi", "method"]',
                'sr_generate(draft_id=draft["draft_id"], num_samples=80)',
                'python -u scripts\\run_srs_pmi_hello_world.py',
                '证据已写出，但不能宣称收益',
            ))
            quickstart_overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            quickstart_shot = None
            if name == "desktop":
                quickstart_shot = OUT / "quickstart-hello-world.png"
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(quickstart_shot), full_page=False)

            page.goto(GUIDE.as_uri() + "#/antenna", wait_until="load")
            page.wait_for_selector('.doc-page[data-page="antenna"]:not([hidden])')
            formula_count = page.locator('.doc-page[data-page="antenna"] .kx-ok').count()
            compact_detail_visible = page.locator(
                '.doc-page[data-page="antenna"] .detail-content:visible'
            ).count()
            compact_toc_count = page.locator("#toc-links a").count()
            page.locator("#reading-toggle").click()
            page.wait_for_function(
                "document.documentElement.dataset.readingMode === 'detailed'"
            )
            detailed_detail_visible = page.locator(
                '.doc-page[data-page="antenna"] .detail-content:visible'
            ).count()
            formula_explanations_visible = page.locator(
                '.doc-page[data-page="antenna"] .formula-explain:visible'
            ).count()
            detailed_toc_count = page.locator("#toc-links a").count()
            antenna_shot = None
            formula_shot = None
            if name == "desktop":
                antenna_shot = OUT / "antenna-desktop.png"
                page.screenshot(path=str(antenna_shot), full_page=True)
                formula_shot = OUT / "formula-card-desktop.png"
                page.locator(
                    '.doc-page[data-page="antenna"] .formula-card'
                ).first.screenshot(path=str(formula_shot))

            # Reading depth is a site-wide preference and must survive a reload.
            page.reload(wait_until="load")
            page.wait_for_selector('.doc-page[data-page="antenna"]:not([hidden])')
            depth_persisted = page.locator("html").get_attribute(
                "data-reading-mode"
            ) == "detailed"
            page.locator(
                '.doc-page[data-page="antenna"] '
                '[data-reading-choice="compact"]'
            ).click()
            page.wait_for_function(
                "document.documentElement.dataset.readingMode === 'compact'"
            )

            search = page.locator("#search")
            search.fill("NEBF 每天线")
            page.wait_for_selector("#search-panel:not([hidden])")
            search_hits = page.locator("#search-panel .search-result").count()
            search.fill("")

            # The code-audit chapters must work as first-class routes, not only
            # appear as terms in the API appendix.
            page.goto(GUIDE.as_uri() + "#/pdp", wait_until="load")
            page.wait_for_selector('.doc-page[data-page="pdp"]:not([hidden])')
            pdp_formula_count = page.locator(
                '.doc-page[data-page="pdp"] .kx-ok'
            ).count()
            pdp_diagram_visible = page.locator(
                '.doc-page[data-page="pdp"] figure.diagram:visible'
            ).count() >= 1
            pdp_overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )

            page.goto(GUIDE.as_uri() + "#/robust", wait_until="load")
            page.wait_for_selector('.doc-page[data-page="robust"]:not([hidden])')
            page.locator(
                '.doc-page[data-page="robust"] [data-reading-choice="detailed"]'
            ).click()
            page.wait_for_function(
                "document.documentElement.dataset.readingMode === 'detailed'"
            )
            robust_formula_count = page.locator(
                '.doc-page[data-page="robust"] .kx-ok'
            ).count()
            robust_explanations_visible = page.locator(
                '.doc-page[data-page="robust"] .formula-explain:visible'
            ).count()
            robust_overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            pdp_shot = robust_shot = robust_formula_shot = None
            if name == "desktop":
                pdp_shot = OUT / "pdp-desktop.png"
                page.goto(GUIDE.as_uri() + "#/pdp", wait_until="load")
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(pdp_shot), full_page=False)
                robust_shot = OUT / "robust-desktop.png"
                page.goto(GUIDE.as_uri() + "#/robust", wait_until="load")
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(robust_shot), full_page=False)
                robust_formula_shot = OUT / "robust-formula-card-desktop.png"
                page.locator(
                    '.doc-page[data-page="robust"] .formula-card'
                ).first.screenshot(path=str(robust_formula_shot))
            page.locator(
                '.doc-page[data-page="robust"] [data-reading-choice="compact"]'
            ).click()
            page.wait_for_function(
                "document.documentElement.dataset.readingMode === 'compact'"
            )

            page.goto(GUIDE.as_uri() + "#/pmi", wait_until="load")
            page.wait_for_selector('.doc-page[data-page="pmi"]:not([hidden])')
            page.locator(
                '.doc-page[data-page="pmi"] [data-reading-choice="detailed"]'
            ).click()
            page.wait_for_function(
                "document.documentElement.dataset.readingMode === 'detailed'"
            )
            pmi_formula_count = page.locator(
                '.doc-page[data-page="pmi"] .kx-ok'
            ).count()
            pmi_explanations_visible = page.locator(
                '.doc-page[data-page="pmi"] .formula-explain:visible'
            ).count()
            pmi_diagram_visible = page.locator(
                '.doc-page[data-page="pmi"] figure.diagram:visible'
            ).count() >= 1
            pmi_overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )

            page.goto(GUIDE.as_uri() + "#/powercontrol", wait_until="load")
            page.wait_for_selector(
                '.doc-page[data-page="powercontrol"]:not([hidden])'
            )
            power_formula_count = page.locator(
                '.doc-page[data-page="powercontrol"] .kx-ok'
            ).count()
            power_explanations_visible = page.locator(
                '.doc-page[data-page="powercontrol"] .formula-explain:visible'
            ).count()
            power_diagram_visible = page.locator(
                '.doc-page[data-page="powercontrol"] figure.diagram:visible'
            ).count() >= 1
            power_overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            pmi_shot = power_shot = None
            if name == "desktop":
                pmi_shot = OUT / "pmi-desktop.png"
                page.goto(GUIDE.as_uri() + "#/pmi", wait_until="load")
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(pmi_shot), full_page=False)
                power_shot = OUT / "powercontrol-desktop.png"
                page.goto(GUIDE.as_uri() + "#/powercontrol", wait_until="load")
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(power_shot), full_page=False)
            page.locator(
                '.doc-page[data-page="powercontrol"] '
                '[data-reading-choice="compact"]'
            ).click()
            page.wait_for_function(
                "document.documentElement.dataset.readingMode === 'compact'"
            )

            # Newly promoted capability clusters must be usable as complete
            # chapters in both reading modes.  Check the actual route, diagram,
            # rendered formulas, detailed explanations and responsive width.
            chapter_expectations = {
                "agentloop": 3,
                "kpi": 3,
                "raytracing": 4,
                "referencesignals": 3,
                "bler": 6,
                "externalresults": 4,
                "srs": 5,
                "srsallocation": 6,
                "schedulerp0": 3,
            }
            new_chapter_checks = {}
            for chapter_key, minimum_formulas in chapter_expectations.items():
                page.goto(GUIDE.as_uri() + f"#/{chapter_key}", wait_until="load")
                page.wait_for_selector(
                    f'.doc-page[data-page="{chapter_key}"]:not([hidden])'
                )
                page.locator(
                    f'.doc-page[data-page="{chapter_key}"] '
                    '[data-reading-choice="detailed"]'
                ).click()
                page.wait_for_function(
                    "document.documentElement.dataset.readingMode === 'detailed'"
                )
                chapter_formula_count = page.locator(
                    f'.doc-page[data-page="{chapter_key}"] .kx-ok'
                ).count()
                chapter_explanations = page.locator(
                    f'.doc-page[data-page="{chapter_key}"] '
                    '.formula-explain:visible'
                ).count()
                chapter_formula_overflow = page.locator(
                    f'.doc-page[data-page="{chapter_key}"] '
                    '.formula-card:visible .formula-expression'
                ).evaluate_all(
                    "els => els.length ? Math.max(...els.map(e => "
                    "e.scrollWidth - e.clientWidth)) : 0"
                )
                chapter_diagram = page.locator(
                    f'.doc-page[data-page="{chapter_key}"] '
                    'figure.diagram:visible'
                ).count() >= 1
                chapter_detail = page.locator(
                    f'.doc-page[data-page="{chapter_key}"] '
                    '.detail-content:visible'
                ).count() == 1
                chapter_extra = {}
                chapter_extra_pass = True
                if chapter_key == "bler":
                    chapter_extra = {
                        "threshold_chart_count": page.locator(
                            '.doc-page[data-page="bler"] .bler-threshold-chart:visible'
                        ).count(),
                        "curve_atlas_count": page.locator(
                            '.doc-page[data-page="bler"] .bler-curve-atlas:visible'
                        ).count(),
                        "curve_summary_rows": page.locator(
                            '.doc-page[data-page="bler"] '
                            '.detail-data-atlas table[data-bler-curve-summary] tbody tr'
                        ).count(),
                    }
                    chapter_extra_pass = bool(
                        chapter_extra["threshold_chart_count"] == 1
                        and chapter_extra["curve_atlas_count"] == 1
                        and chapter_extra["curve_summary_rows"] == 28
                    )
                chapter_overflow = page.evaluate(
                    "document.documentElement.scrollWidth - "
                    "document.documentElement.clientWidth"
                )
                chapter_shot = None
                chapter_formula_shot = None
                chapter_atlas_shot = None
                chapter_threshold_shot = None
                chapter_toy_shot = None
                chapter_status_shot = None
                chapter_grid_shot = None
                chapter_capacity_shot = None
                if name == "desktop":
                    chapter_shot = OUT / f"{chapter_key}-desktop.png"
                    page.evaluate("window.scrollTo(0, 0)")
                    page.screenshot(path=str(chapter_shot), full_page=False)
                    chapter_formula_shot = OUT / f"{chapter_key}-formula-card.png"
                    page.locator(
                        f'.doc-page[data-page="{chapter_key}"] .formula-card'
                    ).first.screenshot(path=str(chapter_formula_shot))
                    if chapter_key == "bler":
                        chapter_threshold_shot = OUT / "bler-thresholds-desktop.png"
                        page.locator(
                            '.doc-page[data-page="bler"] .bler-threshold-chart'
                        ).screenshot(path=str(chapter_threshold_shot))
                        chapter_atlas_shot = OUT / "bler-curves-desktop.png"
                        page.locator(
                            '.doc-page[data-page="bler"] .bler-curve-atlas'
                        ).screenshot(path=str(chapter_atlas_shot))
                    if chapter_key == "srsallocation":
                        # Locator screenshots stitch tall elements across viewports;
                        # hide the fixed top bar so it is not repeated inside the
                        # captured toy example and mistaken for content overlap.
                        page.locator(".topbar").evaluate(
                            "el => { el.dataset.qaVisibility = el.style.visibility; "
                            "el.style.visibility = 'hidden'; }"
                        )
                        chapter_grid_shot = OUT / "srsallocation-resource-grid.png"
                        page.locator(
                            '.doc-page[data-page="srsallocation"] '
                            '.srs-grid-table'
                        ).screenshot(path=str(chapter_grid_shot))
                        chapter_capacity_shot = OUT / "srsallocation-capacity.png"
                        page.locator(
                            '.doc-page[data-page="srsallocation"] '
                            '.srs-capacity-model'
                        ).screenshot(path=str(chapter_capacity_shot))
                        chapter_toy_shot = OUT / "srsallocation-toy-example.png"
                        page.locator(
                            '.doc-page[data-page="srsallocation"] '
                            '.srs-toy-example'
                        ).screenshot(path=str(chapter_toy_shot))
                        chapter_status_shot = OUT / "srsallocation-status.png"
                        page.locator(
                            '.doc-page[data-page="srsallocation"] '
                            '.srs-implementation-status'
                        ).screenshot(path=str(chapter_status_shot))
                        page.locator(".topbar").evaluate(
                            "el => { el.style.visibility = "
                            "el.dataset.qaVisibility || ''; "
                            "delete el.dataset.qaVisibility; }"
                        )
                new_chapter_checks[chapter_key] = {
                    "formula_count": chapter_formula_count,
                    "minimum_formulas": minimum_formulas,
                    "formula_explanations_visible": chapter_explanations,
                    "formula_internal_overflow_px": chapter_formula_overflow,
                    "diagram_visible": chapter_diagram,
                    "detail_visible": chapter_detail,
                    "horizontal_overflow_px": chapter_overflow,
                    "screenshot": str(chapter_shot) if chapter_shot else None,
                    "formula_screenshot": (
                        str(chapter_formula_shot) if chapter_formula_shot else None
                    ),
                    "atlas_screenshot": (
                        str(chapter_atlas_shot) if chapter_atlas_shot else None
                    ),
                    "threshold_screenshot": (
                        str(chapter_threshold_shot) if chapter_threshold_shot else None
                    ),
                    "toy_screenshot": (
                        str(chapter_toy_shot) if chapter_toy_shot else None
                    ),
                    "status_screenshot": (
                        str(chapter_status_shot) if chapter_status_shot else None
                    ),
                    "resource_grid_screenshot": (
                        str(chapter_grid_shot) if chapter_grid_shot else None
                    ),
                    "capacity_screenshot": (
                        str(chapter_capacity_shot) if chapter_capacity_shot else None
                    ),
                    **chapter_extra,
                    "pass": bool(
                        chapter_formula_count >= minimum_formulas
                        and chapter_explanations >= minimum_formulas
                        and chapter_formula_overflow <= 1
                        and chapter_diagram
                        and chapter_detail
                        and chapter_overflow <= 1
                        and chapter_extra_pass
                    ),
                }
                page.locator(
                    f'.doc-page[data-page="{chapter_key}"] '
                    '[data-reading-choice="compact"]'
                ).click()
                page.wait_for_function(
                    "document.documentElement.dataset.readingMode === 'compact'"
                )

            old_theme = page.locator("html").get_attribute("data-theme")
            page.locator("#theme").click()
            new_theme = page.locator("html").get_attribute("data-theme")
            page.locator("#theme").click()

            if viewport["width"] <= 820:
                page.locator("#menu").click()
                menu_open = "menu-open" in (page.locator("body").get_attribute("class") or "")
                # Click the exposed backdrop strip, outside the 286 px drawer.
                page.locator("#backdrop").click(
                    position={"x": viewport["width"] - 5, "y": 20}
                )
                page.wait_for_function("!document.body.classList.contains('menu-open')")
                page.wait_for_timeout(250)  # wait for the 200 ms drawer transition
            else:
                menu_open = page.locator(".sidebar").is_visible()

            target = "experience" if name == "mobile" else "overview"
            page.goto(GUIDE.as_uri() + f"#/{target}", wait_until="load")
            page.wait_for_selector(f'.doc-page[data-page="{target}"]:not([hidden])')
            hero_box = page.locator(
                f'.doc-page[data-page="{target}"] .page-hero'
            ).bounding_box()
            hero_within_viewport = bool(
                hero_box
                and hero_box["x"] >= -1
                and hero_box["x"] + hero_box["width"] <= viewport["width"] + 1
            )
            shot = OUT / f"{name}.png"
            page.screenshot(path=str(shot), full_page=False)

            checks = {
                "visible_page_count": visible_page_count,
                "horizontal_overflow_px": overflow,
                "product_surface_count": product_surface_count,
                "product_surface_links": product_surface_links,
                "overview_hello_count": overview_hello_count,
                "overview_hello_action_count": overview_hello_action_count,
                "quickstart_hello_count": quickstart_hello_count,
                "quickstart_contract": quickstart_contract,
                "quickstart_horizontal_overflow_px": quickstart_overflow,
                "katex_formula_count": formula_count,
                "compact_detail_visible": compact_detail_visible,
                "detailed_detail_visible": detailed_detail_visible,
                "formula_explanations_visible": formula_explanations_visible,
                "detailed_toc_growth": detailed_toc_count - compact_toc_count,
                "depth_persisted": depth_persisted,
                "search_hits": search_hits,
                "pdp_formula_count": pdp_formula_count,
                "pdp_diagram_visible": pdp_diagram_visible,
                "pdp_horizontal_overflow_px": pdp_overflow,
                "robust_formula_count": robust_formula_count,
                "robust_explanations_visible": robust_explanations_visible,
                "robust_horizontal_overflow_px": robust_overflow,
                "pmi_formula_count": pmi_formula_count,
                "pmi_explanations_visible": pmi_explanations_visible,
                "pmi_diagram_visible": pmi_diagram_visible,
                "pmi_horizontal_overflow_px": pmi_overflow,
                "power_formula_count": power_formula_count,
                "power_explanations_visible": power_explanations_visible,
                "power_diagram_visible": power_diagram_visible,
                "power_horizontal_overflow_px": power_overflow,
                "new_chapters": new_chapter_checks,
                "theme_changed": old_theme != new_theme,
                "navigation_available": menu_open,
                "hero_within_viewport": hero_within_viewport,
                "screenshot": str(shot),
            }
            if antenna_shot is not None:
                checks["antenna_screenshot"] = str(antenna_shot)
            if overview_product_shot is not None:
                checks["overview_product_screenshot"] = str(overview_product_shot)
            if overview_mobile_shot is not None:
                checks["overview_mobile_screenshot"] = str(overview_mobile_shot)
            if overview_mobile_hello_shot is not None:
                checks["overview_mobile_hello_screenshot"] = str(overview_mobile_hello_shot)
            if quickstart_shot is not None:
                checks["quickstart_screenshot"] = str(quickstart_shot)
            if formula_shot is not None:
                checks["formula_screenshot"] = str(formula_shot)
            if pdp_shot is not None:
                checks["pdp_screenshot"] = str(pdp_shot)
            if robust_shot is not None:
                checks["robust_screenshot"] = str(robust_shot)
            if robust_formula_shot is not None:
                checks["robust_formula_screenshot"] = str(robust_formula_shot)
            if pmi_shot is not None:
                checks["pmi_screenshot"] = str(pmi_shot)
            if power_shot is not None:
                checks["powercontrol_screenshot"] = str(power_shot)
            checks["pass"] = bool(
                visible_page_count == 1
                and overflow <= 1
                and product_surface_count == 2
                and product_surface_links == 2
                and overview_hello_count == 1
                and overview_hello_action_count == 3
                and quickstart_hello_count == 1
                and quickstart_contract
                and quickstart_overflow <= 1
                and formula_count >= 3
                and compact_detail_visible == 0
                and detailed_detail_visible == 1
                and formula_explanations_visible >= 3
                and detailed_toc_count > compact_toc_count
                and depth_persisted
                and search_hits >= 1
                and pdp_formula_count >= 3
                and pdp_diagram_visible
                and pdp_overflow <= 1
                and robust_formula_count >= 3
                and robust_explanations_visible >= 3
                and robust_overflow <= 1
                and pmi_formula_count >= 5
                and pmi_explanations_visible >= 5
                and pmi_diagram_visible
                and pmi_overflow <= 1
                and power_formula_count >= 5
                and power_explanations_visible >= 5
                and power_diagram_visible
                and power_overflow <= 1
                and all(item["pass"] for item in new_chapter_checks.values())
                and checks["theme_changed"]
                and menu_open
                and hero_within_viewport
            )
            report["viewports"][name] = checks
            page.close()

        # Audit the whole logical site, not just the three screenshot routes.
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda msg: report["errors"].append(
            f"site:console:{msg.type}:{msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: report["errors"].append(f"site:page:{exc}"))
        page.goto(GUIDE.as_uri() + "#/overview", wait_until="load")
        site = page.evaluate(
            """
            () => {
              const defs = window.__DOC_PAGES__ || [];
              const articles = [...document.querySelectorAll('.doc-page')];
              const articleByKey = new Map(articles.map(a => [a.dataset.page, a]));
              const formulaErrors = [];
              for (const el of document.querySelectorAll('.kx[data-tex]')) {
                try {
                  katex.renderToString(el.dataset.tex, {
                    displayMode: el.dataset.display === '1',
                    throwOnError: true,
                    strict: 'error',
                    output: 'htmlAndMathml'
                  });
                } catch (err) {
                  formulaErrors.push({tex: el.dataset.tex, error: String(err)});
                }
              }

              const brokenRoutes = [];
              for (const link of document.querySelectorAll('a[href^="#/"]')) {
                const href = link.getAttribute('href');
                const [key, section] = href.slice(2).split('/');
                const target = articleByKey.get(key);
                if (!target || (section && !target.querySelector('#' + CSS.escape(section)))) {
                  brokenRoutes.push(href);
                }
              }
              const duplicateHeadingIds = [];
              for (const article of articles) {
                const ids = [...article.querySelectorAll('h2[id],h3[id]')].map(h => h.id);
                const dup = [...new Set(ids.filter((id, i) => ids.indexOf(id) !== i))];
                if (dup.length) duplicateHeadingIds.push({page: article.dataset.page, ids: dup});
              }
              const weakPages = articles.filter(a =>
                !a.querySelector('.page-hero h1') ||
                !a.querySelector('.page-nav') ||
                a.innerText.trim().length < 500
              ).map(a => a.dataset.page);
              const badDiagrams = [...document.querySelectorAll('figure.diagram')]
                .filter(f => !f.querySelector('svg[viewBox]') || !f.querySelector('figcaption'))
                .map(f => f.closest('.doc-page')?.dataset.page || '?');
              const externalAssets = [...document.querySelectorAll(
                'script[src],link[rel="stylesheet"][href],img[src]'
              )].map(e => e.src || e.href).filter(url => !String(url).startsWith('data:'));
              const missingDetailPages = articles.filter(a =>
                !a.querySelector(':scope > .detail-content[data-detail-for="' +
                  CSS.escape(a.dataset.page) + '"]')
              ).map(a => a.dataset.page);
              const badFormulaDocs = [...document.querySelectorAll('figure.formula-card')]
                .filter(f =>
                  !f.querySelector('.formula-explain > strong') ||
                  !f.querySelector('.symbol-list') ||
                  f.querySelectorAll('.symbol-list dt').length < 3
                ).map(f => f.dataset.formula || '?');
              const badChapterRatios = defs.filter(p =>
                p.reading_kind === 'chapter' &&
                (Number(p.detail_ratio) < 1.8 || Number(p.detail_ratio) > 3.3)
              ).map(p => ({key:p.key, ratio:p.detail_ratio}));
              return {
                declared_pages: defs.length,
                article_pages: articles.length,
                unique_page_keys: new Set(articles.map(a => a.dataset.page)).size,
                formulas: document.querySelectorAll('.kx[data-tex]').length,
                katex_rendered: document.querySelectorAll('.kx.kx-ok').length,
                formula_errors: formulaErrors,
                route_links: document.querySelectorAll('a[href^="#/"]').length,
                broken_routes: brokenRoutes,
                duplicate_heading_ids: duplicateHeadingIds,
                weak_pages: weakPages,
                diagrams: document.querySelectorAll('figure.diagram').length,
                bad_diagrams: badDiagrams,
                detail_pages: document.querySelectorAll('.detail-content').length,
                missing_detail_pages: missingDetailPages,
                formula_cards: document.querySelectorAll('figure.formula-card').length,
                bad_formula_docs: badFormulaDocs,
                bad_chapter_ratios: badChapterRatios,
                external_assets: externalAssets,
                unresolved_markers: (document.body.innerText.match(/\b(?:TODO|TBD|FIXME)\b/g) || []).length
              };
            }
            """
        )
        site["pass"] = bool(
            site["declared_pages"] == site["article_pages"]
            and site["article_pages"] == site["unique_page_keys"]
            and site["formulas"] > 0
            and site["katex_rendered"] == site["formulas"]
            and not site["formula_errors"]
            and not site["broken_routes"]
            and not site["duplicate_heading_ids"]
            and not site["weak_pages"]
            and not site["bad_diagrams"]
            and site["detail_pages"] == site["article_pages"]
            and not site["missing_detail_pages"]
            and site["formula_cards"] == site["formulas"]
            and not site["bad_formula_docs"]
            and not site["bad_chapter_ratios"]
            and not site["external_assets"]
            and site["unresolved_markers"] == 0
        )
        report["full_site"] = site
        page.close()

        browser.close()

    report["pass"] = (
        all(v["pass"] for v in report["viewports"].values())
        and report["full_site"]["pass"]
        and not report["errors"]
    )
    manifest = OUT / "qa.json"
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

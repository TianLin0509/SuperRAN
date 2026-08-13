"""Real-Chromium interaction and responsive-layout QA for docs/index.html."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
    report: dict = {"guide": str(GUIDE), "viewports": {}, "errors": []}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, viewport in viewports.items():
            # A fresh page per viewport prevents scroll/focus/transition state from
            # leaking between responsive-layout checks.
            page = browser.new_page(viewport=viewport)
            page.on("console", lambda msg, n=name: report["errors"].append(
                f"{n}:console:{msg.type}:{msg.text}") if msg.type == "error" else None)
            page.on("pageerror", lambda exc, n=name: report["errors"].append(
                f"{n}:page:{exc}"))
            page.goto(GUIDE.as_uri() + "#/overview", wait_until="load")
            page.wait_for_selector('.doc-page[data-page="overview"]:not([hidden])')

            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            visible_page_count = page.locator(".doc-page:not([hidden])").count()
            page.goto(GUIDE.as_uri() + "#/antenna", wait_until="load")
            page.wait_for_selector('.doc-page[data-page="antenna"]:not([hidden])')
            formula_count = page.locator('.doc-page[data-page="antenna"] .kx-ok').count()

            search = page.locator("#search")
            search.fill("NEBF 每天线")
            page.wait_for_selector("#search-panel:not([hidden])")
            search_hits = page.locator("#search-panel .search-result").count()
            search.fill("")

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
                "katex_formula_count": formula_count,
                "search_hits": search_hits,
                "theme_changed": old_theme != new_theme,
                "navigation_available": menu_open,
                "hero_within_viewport": hero_within_viewport,
                "screenshot": str(shot),
            }
            checks["pass"] = bool(
                visible_page_count == 1
                and overflow <= 1
                and formula_count >= 3
                and search_hits >= 1
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
              )].map(e => e.src || e.href);
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

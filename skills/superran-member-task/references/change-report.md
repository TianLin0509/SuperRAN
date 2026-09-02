# User-facing change report

Read this after a PR exists or its head changes. The report is the Author's explanation
material for the member and lead; it is not a Reviewer approval or release claim.

## Identity and delivery

Generate a strict UTF-8, self-contained HTML file at:

`output/change-reports/PR-<number>-<short-slug>-<head12>.html`

The report must bind to the current GitHub PR number, base branch/SHA, and full remote
head SHA. If the PR receives another commit, the report is stale and must be regenerated
under the new head name before handoff.

Do not commit the report by default. The repository is public and a report may contain
local paths, internal assumptions, or evidence not intended for publication. Give the
member the absolute path, SHA-256, size, PR URL, and bound head SHA so they can send the
HTML file to the lead together with the PR link. Publish or commit a sanitized report
only when the lead explicitly authorizes that separate action.

Place this banner on the first screen:

`AUTHOR CHANGE REPORT — WAITING FOR LEAD REVIEW`

For rehearsal PRs use:

`REHEARSAL AUTHOR REPORT — DO NOT MERGE`

Never title or style an Author report as if the lead already approved it.

## What the page must answer

The sample can be short for a small change, but it must make these decisions visible:

1. **Outcome first** — what is done, partial, not done, and awaiting the lead; show PR,
   base/head, diff size, test terminal state, and report identity.
2. **Human intent → implementation** — map every confirmed task-card item or lead
   decision to its implementation state. Do not silently omit rejected or deferred work.
3. **Core flow** — show the relevant input → processing → output or cross-layer physics
   chain. Use a compact inline SVG, flow, or table only when it improves understanding.
4. **Each material change** — show **before / now / why**, with the canonical technical
   term, plain-language explanation, current-task example, and evidence anchor.
5. **One worked example when useful** — a tiny shape, unit, timeline, or hand-checkable
   calculation that exposes the mechanism. Label it illustrative unless its numbers came
   from cited current-head output.
6. **Verification and negative controls** — for each important command/check, show the
   terminal state, what it proves, and what it does not prove. Distinguish mechanism
   checks, regressions, Gate 1/2/3, and publishable conclusions.
7. **Remaining risks and decisions** — list known limitations, explicitly deferred work,
   disagreements, failed or unavailable evidence, and exactly what the lead must decide.
8. **Developer detail** — files, symbols, commands, logs, and hashes may be placed in a
   collapsed section so they do not dominate the human story.

## Evidence rules

- Build the report from the actual PR diff, current GitHub metadata, current-head test
  artifacts, and the confirmed task card. Never reconstruct facts from the Author's
  memory alone.
- Mark each claim as code fact, human decision, Agent interpretation, or unverified.
- Do not turn examples, screenshots, historical output, toy numbers, or a green CI badge
  into physical evidence.
- Comparative numbers require the applicable `channel-sim` gates. If Gate 3 did not
  pass, reproduce its statement without reframing a direction or percentage.
- Do not paste huge logs or raw arrays. Link or name the evidence path and show the
  decision-relevant excerpt.
- Redact credentials, tokens, private URLs, personal data, and unnecessary absolute
  paths. State when the report is not safe for public publication.

## Interaction and visual QA

Use a clear first screen, sticky or compact navigation for long pages, readable status
colors that also have text labels, and collapsed technical detail. Keep it usable at
desktop and mobile widths. Avoid decorative interaction that does not help a decision.

The page must be offline and self-contained: inline CSS/JS/necessary SVG, system fonts,
no network requests, and `<link rel="icon" href="data:,">`. Target under 1 MiB; if larger,
report why and what is embedded.

Before handoff:

1. serve it from a temporary `127.0.0.1` HTTP server;
2. open it in a real browser;
3. inspect desktop and narrow/mobile layouts;
4. exercise navigation, tabs, filters, or collapsible sections that exist;
5. require zero console errors/warnings and no failed network requests;
6. capture a screenshot and verify no clipping, overlap, broken formula, or unreadable
   text;
7. re-read the PR head and invalidate the page if the SHA changed during QA.

## Final member handoff

Return only:

- 做成了什么
- PR 链接与 PR HEAD 完整 SHA
- 交互式改动说明 HTML 的绝对路径、SHA-256、大小
- 当前测试结论，以及分别证明了什么 / 没有证明什么
- 需要组长决定什么（没有就写“无”）
- 下一步：请把 PR 链接和 HTML 文件一起发给组长

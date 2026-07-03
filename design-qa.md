# Design QA

Source visual:

- `C:\Users\ice\.codex\generated_images\019f213c-0e25-76c3-822f-2fd2cffa12e5\ig_0b3bab5e60eaf317016a471eb92fec8191b4c230a080a618af.png`

Final implementation evidence:

- Desktop screenshot: `C:\Users\ice\Documents\Codex\2026-07-02\wo-x\work\design-qa\console-desktop-1487x1058.png`
- Tablet screenshot: `C:\Users\ice\Documents\Codex\2026-07-02\wo-x\work\design-qa\console-tablet-1280x720.png`
- Mobile screenshot: `C:\Users\ice\Documents\Codex\2026-07-02\wo-x\work\design-qa\console-mobile-390x844.png`
- Full desktop comparison: `C:\Users\ice\Documents\Codex\2026-07-02\wo-x\work\design-qa\comparison-desktop-full.png`
- Focused first-fold comparison: `C:\Users\ice\Documents\Codex\2026-07-02\wo-x\work\design-qa\comparison-desktop-focused.png`

Test state:

- API mode: local demo BFF, backed by the real FastAPI app.
- Active run: `run_2ef90c61642e`.
- Scenario source: real `/api/console/run-scenario` call through the frontend BFF.
- No static incident, citation, audit, memory, or monitor mock data is used by the console.

Visual comparison notes:

- The implementation keeps the selected Learning Ops Split structure: deep green rail, two-layer command/context header, left monitor queue, center run timeline, right evidence panel, and compact bottom trace status.
- The implementation differs where production data requires it: the alert queue shows persisted monitor clusters, the run timeline shows the actual trace returned by the backend, and citations contain the local knowledge base content.
- The focused first-fold comparison was used for header density, rail alignment, context item spacing, alert/timeline/evidence column proportions, state pills, and action button placement.
- Mobile has no direct source visual; it was verified as a responsive adaptation of the same console rather than a separate design direction.

Responsive probes:

- `1487x1058`: `bodyScrollWidth=1487`, no stale selected alert, no loading state.
- `1280x720`: `bodyScrollWidth=1280`, evidence panel intentionally moves below the main run area, no loading state.
- `390x844`: `bodyScrollWidth=390`, `documentScrollWidth=390`, zero critical timeline/card clipping, no loading state.

Fixes made during QA:

- Tightened desktop step pills so all six stages fit beside the action controls.
- Wrapped long timeline titles and mobile timeline summaries instead of clipping them.
- Cleared stale alert selection when opening a run directly, and added BFF-side validation so an alert is only active if it contains the selected run.
- Reset the Run ID input scroll position on submit so the operator sees the `run_...` prefix.
- Moved the 1280px layout to the responsive two-column/tablet mode to avoid horizontal scrolling.
- Reworked mobile top controls, rail navigation, and run metrics so the page does not exceed the viewport.

Open issues:

- No P0/P1/P2 visual issues remain.
- Minor P3: some generated/local Chinese knowledge-base snippets are dense in narrow mobile cards, but they wrap without clipping and remain readable.

final result: passed

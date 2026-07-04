# Product Design record

The repository now ships a production-shaped Next.js operator console in
`frontend/`. The design direction is a restrained operations tool: dense,
scannable, low decoration, with the first screen focused on real incident
investigation rather than a marketing landing page.

## Current product surface

The console should help an on-call operator or Agent beginner answer:

- What happened in a specific conversation or run?
- Which intent, route, tools, retrieval hits, policy findings, and monitor event caused an alert?
- Did a tool fail because of auth, schema, timeout, upstream 5xx, replay, or missing retrieval?
- Is a tool failure isolated, or is it an SLA/error-rate pattern across the audit log?
- Did retrieval fail because of query rewrite, candidate recall, reranking, source selection, or dropped candidates?
- Which regression file should receive the real failure sample?
- Has someone acknowledged, investigated, or resolved a monitor alert?

## Backend API map

| Console area | Backend endpoint | Why it matters |
| --- | --- | --- |
| Run trace | `GET /api/v1/agent/runs/{run_id}` | Shows intent, route, retrieval, tools, spans, and LLM fallback status. |
| Run search | `GET /api/v1/admin/runs` | Durable search over persisted run summaries. |
| Incident bundle | `GET /api/v1/admin/incidents/runs/{run_id}?include_memory=true` | One response with run, monitor events, tool audit, and optional memory replay. |
| Tool audit | `GET /api/v1/admin/tools/audit?trace_id=...` | Durable tool facts without raw arguments or PII. |
| Tool audit summary | `GET /api/v1/admin/tools/audit/summary?...` | Tool-level failure/SLA aggregate without raw arguments or hashes. |
| Knowledge diagnostics | `POST /api/v1/admin/knowledge/search` | Runs the real knowledge adapter and returns safe snippets plus rewrite/stage/drop telemetry. |
| Monitor summary | `GET /api/v1/admin/monitor/summary?source=event_store` | Aggregates live quality by risk, intent, failure type, grounded rate, and alerts. |
| Monitor events | `GET /api/v1/admin/monitor/events?source=event_store` | Raw structured monitor events for sampling and replay. |
| Alert triage | `GET/POST /api/v1/admin/monitor/alerts/{alert_key}/triage` | Append-only ack/investigate/resolve workflow. |
| Event log | `GET /api/v1/admin/events?conversation_id=...` | Auditable event stream for messages, runs, monitor, and triage. |
| Memory replay | `GET /api/v1/admin/conversations/{conversation_id}/memory/replay` | Rebuilds conversation facts after restart. |

## Design QA evidence

Recent local browser QA screenshots, saved outside the committed repo:

- `work/design-qa/console-tools-desktop-1487x1058.png`
- `work/design-qa/console-tools-mobile-390x844.png`
- `work/design-qa/console-tools-mobile-viewport-390x844.png`
- `work/design-qa/console-tools-mobile-workspace-390x844.png`
- `work/design-qa/console-knowledge-desktop-1487x1058.png`
- `work/design-qa/console-knowledge-mobile-390x844.png`
- `work/design-qa/console-knowledge-mobile-workspace-390x844.png`
- `work/design-qa/console-knowledge-mobile-hits-390x844.png`

Checks performed:

- `Tools` rail becomes the active workspace, not only an evidence tab.
- Desktop Tools workbench shows real persisted audit records, backend-derived
  failure stats, tool summaries, and no inline error.
- Mobile viewport has no page-level horizontal overflow; form fields and long
  IDs stay within their containers.
- Selecting an audit card hydrates the shared trace/evidence workflow by
  `trace_id`.
- `Knowledge` rail becomes the active workspace and keeps the shared evidence
  area on citations.
- Desktop and mobile Knowledge workbench states render real adapter results:
  query rewrite, candidate stage counts, selected snippets, long source URIs,
  and score badges without page-level horizontal overflow.

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
- Which monitor events sit behind this alert, and do they cluster by failure
  type, intent, risk level, grounding, policy status, or human-review pressure?
- Which regression file should receive the real failure sample?
- Which operation should happen next, and is it safe for a cron/on-call bot to
  execute automatically?
- Are the service objectives healthy, at risk, breached, or missing enough data?
- Has someone acknowledged, investigated, or resolved a monitor alert?
- Did proactive alert delivery fail, and should a dead-letter row be replayed or closed?

## Backend API map

| Console area | Backend endpoint | Why it matters |
| --- | --- | --- |
| Run trace | `GET /api/v1/agent/runs/{run_id}` | Shows intent, route, retrieval, tools, spans, and LLM fallback status. |
| Run search | `GET /api/v1/admin/runs` | Durable search over persisted run summaries. |
| Incident bundle | `GET /api/v1/admin/incidents/runs/{run_id}?include_memory=true` | One response with run, monitor events, tool audit, and optional memory replay. |
| Incident brief | `GET /api/v1/admin/incidents/runs/{run_id}/brief` | Backend-generated sanitized Markdown handoff for tickets or on-call notes. |
| Tool audit | `GET /api/v1/admin/tools/audit?trace_id=...` | Durable tool facts without raw arguments or PII. |
| Tool audit summary | `GET /api/v1/admin/tools/audit/summary?...` | Tool-level failure/SLA aggregate without raw arguments or hashes. |
| Knowledge diagnostics | `POST /api/v1/admin/knowledge/search` | Runs the real knowledge adapter and returns safe snippets plus rewrite/stage/drop telemetry. |
| Monitor summary | `GET /api/v1/admin/monitor/summary?source=event_store` | Aggregates live quality by risk, intent, failure type, grounded rate, and alerts. |
| Monitor events | `GET /api/v1/admin/monitor/events?source=event_store` | Raw structured monitor events for sampling and replay. |
| Monitor drilldown | `GET /api/v1/admin/monitor/drilldown?source=event_store&alert_key=...` | Event-level alert investigation with failure, intent, and risk buckets. |
| Alert delivery ledger | `GET /api/v1/admin/monitor/alert-deliveries`; `POST .../{delivery_id}/requeue`; `POST .../{delivery_id}/close` | Durable webhook outbox handling with operator replay/close for dead-letter rows. |
| Alert triage | `GET/POST /api/v1/admin/monitor/alerts/{alert_key}/triage` | Append-only ack/investigate/resolve workflow. |
| SLO report | `GET /api/v1/admin/operations/slo-report` | Service-objective status, observed aggregates, and error-budget remaining for on-call review. |
| Operations automation | `GET /api/v1/admin/operations/automation-plan` | Prioritized next-action plan with runnable commands, scopes, guardrails, and auto-execution safety labels. |
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
- `work/design-qa/console-monitor-drilldown-desktop-1360x900.png`
- `work/design-qa/console-monitor-drilldown-mobile-390x844.png`

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
- Alerts workbench switches between `Queue` and `Drilldown` without adding a
  new rail item.
- Alerts workbench includes a `Delivery` tab for durable outbox rows, with
  replay/close actions only on dead-letter rows.
- Desktop and mobile Monitor Drilldown states render real event-store results:
  active alert key, backend stats, failure buckets, monitor event cards, and
  no page-level horizontal overflow.
- Clicking a monitor event card hydrates the shared run/evidence workflow for
  that event's `run_id`.

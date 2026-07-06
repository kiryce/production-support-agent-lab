# Frontend Console

This project now includes a real operations console in `frontend/`. It is a
Next.js app with server-side BFF routes under `/api/console/*`.

The browser never calls the FastAPI service directly. It calls the Next.js BFF,
and the BFF calls the real Agent API:

- Local learning mode sends `X-Demo-User` and `X-Demo-Role`.
- Production mode injects `X-Internal-Auth`, signed `X-Actor-*` claims, and
  request signatures when `FRONTEND_REQUEST_SIGNATURE_REQUIRED=true`.
- Production mode also protects `/` and `/api/console/*` with browser Basic Auth
  before any BFF route can sign a backend request. Missing
  `FRONTEND_CONSOLE_USERNAME` or `FRONTEND_CONSOLE_PASSWORD` fails closed with
  `401`. Placeholder values are rejected, and the password must be at least 16
  characters.
- Production write requests to `/api/console/*` also require same-origin browser
  evidence. The middleware accepts an exact same-origin `Origin` header or
  `Sec-Fetch-Site: same-origin`, and rejects cross-site writes or writes without
  same-origin evidence with `403` even when Basic Auth credentials are valid.
- Production BFF signing also fails closed when any backend actor setting is
  missing or placeholder-shaped. `AGENT_API_BASE_URL`, `APP_TENANT_ID`,
  `APP_INTERNAL_API_KEY`, `APP_ACTOR_SIGNATURE_SECRET`,
  `FRONTEND_ACTOR_USER_ID`, `FRONTEND_ACTOR_ROLES`, and
  `FRONTEND_ACTOR_SCOPES` must be explicit; there is no default admin-scope
  fallback in production mode. `APP_INTERNAL_API_KEY` and
  `APP_ACTOR_SIGNATURE_SECRET` must each be at least 32 characters.
- No fake incident, alert, citation, memory, or tool-audit data is hardcoded in
  the UI. Empty screens mean the backend returned no events.

`GET /api/console/snapshot` is the console's live read model. The browser polls
that BFF route while the tab is visible, pauses while hidden, and refreshes
immediately when the tab becomes visible again. The BFF prefers persisted
`source=event_store` monitor data and falls back to `source=live` only when the
event-store summary cannot be read. The UI uses the client fetch time to show
fresh/degraded/refreshing/paused/stale/failed status. `Degraded` means the
snapshot loaded but one or more optional read models failed, so the top bar and
banner call out partial evidence while keeping fresh-state actions available.
Stale or failed snapshots still block state-changing alert or delivery actions.

## Local Learning Run

Start the backend first:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m uvicorn support_agent_lab.api.main:app --reload
```

Then start the console:

```powershell
cd frontend
pnpm install
$env:AGENT_API_BASE_URL="http://127.0.0.1:8000"
$env:FRONTEND_AUTH_MODE="demo"
$env:DEMO_ACTOR_USER_ID="user_demo"
$env:DEMO_ACTOR_ROLE="admin"
pnpm dev
```

Open:

```text
http://127.0.0.1:3000
```

If the event store is empty, click `Run Scenario`. That action still calls the
real local FastAPI endpoints:

1. `POST /api/v1/chat/sessions`
2. `POST /api/v1/chat/messages`
3. `GET /api/v1/admin/monitor/summary?source=event_store`
4. `GET /api/v1/admin/monitor/triage/metrics?source=event_store`
   The snapshot BFF retries monitor summary and triage reads with `source=live`
   only when persisted event-store reads are unavailable, so local development
   still shows current process events without inventing console data.
5. `GET /api/v1/admin/monitor/review-worker/summary` when `Overview` shows
   async monitor review worker heartbeat status and the latest cycle counts.
6. `GET /api/v1/admin/incidents/runs/{run_id}?include_memory=true`
7. `GET /api/v1/admin/incidents/runs/{run_id}/brief` when the `Brief`
   panel copies or downloads a backend-generated sanitized Markdown handoff.
8. `GET /api/v1/admin/incidents/runs/{run_id}/timeline` when the `Brief`
   panel renders the sanitized investigation timeline.
9. `GET /api/v1/admin/runs` when the `Runs` workbench searches persisted
   history.
10. `GET /api/v1/admin/tools/audit` and
   `GET /api/v1/admin/tools/audit/summary` when the `Tools` workbench searches
   persisted tool calls and SLA/failure aggregates.
11. `POST /api/v1/admin/knowledge/search` when the `Knowledge` workbench runs
   a retrieval diagnostic query.
12. `GET /api/v1/admin/knowledge/summary` when the `Knowledge` workbench opens
   and shows the configured index/provider status.
13. `POST /api/v1/admin/monitor/alert-deliveries/dispatch` when the `Delivery`
   tab runs `Dispatch now` against the durable alert outbox.
14. `GET /api/v1/admin/monitor/alert-webhook-receipts` when the `Receipts`
   tab inspects signed inbound webhook receipt summaries by alert key or
   delivery id.
15. `GET /api/v1/admin/monitor/drilldown` when the `Alerts` workbench switches
   from queue triage to event-level investigation by alert key, intent, risk,
   failure type, grounding, policy status, and human-review state.
16. `POST /api/v1/admin/evals/regression-drafts` when an operator turns a
   selected monitor event or response-feedback record into a copyable eval-case
   draft.
17. `POST /api/v1/admin/event-store/backups` when the `Settings` workbench
   creates a verified SQLite backup.
17. `POST /api/v1/admin/event-store/restore-drills` when the `Settings`
   workbench proves the latest verified backup can be opened and health-checked.
18. `POST /api/v1/admin/event-store/retention` when the `Settings` workbench
   previews or applies the conservative retention policy. Apply calls include
   backend-issued backup, restore-drill, and preview tokens plus explicit
   confirmation; the BFF refuses tokenless apply requests before proxying.
19. `GET /api/v1/admin/event-store/operations` when `Settings` loads the
   durable event-store operation ledger for backup, restore-drill, retention
   preview, retention apply, and authenticated guard rejections.
20. `GET /api/v1/admin/conversations/{conversation_id}/memory/replay` when
   the `Memory` workbench rebuilds a conversation from append-only events.
   The snapshot BFF may also fetch `/api/v1/admin/events` for event counts and
   ordering, but it strips raw payload, actor, user, conversation, and run ids
   before returning event summaries to the browser.
21. `GET /api/v1/admin/feedback` and
   `GET /api/v1/admin/feedback/summary` when the `Feedback` workbench reviews
   user/operator ratings linked to persisted runs.
22. `GET /api/v1/admin/feedback/review-queue` when the `Feedback` workbench
   shows unresolved, unassigned, stale, and reviewed backlog metrics.
23. `GET /api/v1/admin/feedback/{feedback_id}/reviews` and
   `POST /api/v1/admin/feedback/{feedback_id}/reviews` when the `Feedback`
   workbench loads or records the append-only operator review trail. Review
   writes send the current review-state fingerprint so stale tabs cannot
   append obsolete feedback decisions.
24. `GET /api/v1/admin/promotion/decisions` and
   `POST /api/v1/admin/promotion/decisions` when `Settings` shows or records
   append-only release decisions tied to a fresh promotion-gate snapshot.
25. `GET /api/v1/admin/operations/slo-report` when `Overview` and `Settings`
   show service objectives, error-budget remaining, and breached/watch/no-data
   counts.
26. `GET /api/v1/admin/operations/automation-plan` when `Settings` shows the
   read-only next-action queue for monitor, delivery, release, eval, feedback,
   tool-audit, and retrieval follow-up.
27. `POST /api/v1/admin/operations/automation-executions` when the Settings BFF
   records completed or failed auto-safe action execution, and
   `GET /api/v1/admin/operations/automation-executions` when the Settings
   history panel or external audit/history integrations list sanitized
   execution records. `GET /api/v1/admin/operations/automation-executions/summary`
   feeds the Settings execution-health strip and SLO evidence.
28. `GET /api/v1/admin/audit/export` when `Settings` downloads sanitized
   NDJSON for SIEM or warehouse ingestion.
29. `GET /api/v1/admin/audit/export-batches/summary` when `Overview` and
   `Settings` show durable audit export batch status, manifest file, counts,
   bytes, checksum, and partial flag.

## Production Run

Use the console as a trusted server-side gateway:

```text
AGENT_API_BASE_URL=http://app:8000
FRONTEND_AUTH_MODE=production
FRONTEND_CONSOLE_USERNAME=operator
FRONTEND_CONSOLE_PASSWORD=replace_with_real_console_password_min_16_chars
FRONTEND_ACTOR_USER_ID=console_operator
FRONTEND_ACTOR_ROLES=admin
FRONTEND_ACTOR_SCOPES=crm:read,order:read,shipping:read,ticket:write,kb:read,feedback:write,admin:read,admin:write,audit:read,events:read,eval:read,eval:run,feedback:read,knowledge:diagnose,memory:replay,monitor:read,monitor:write
FRONTEND_REQUEST_SIGNATURE_REQUIRED=true
APP_TENANT_ID=your_tenant
APP_INTERNAL_API_KEY=your_internal_gateway_secret_min_32_chars
APP_ACTOR_SIGNATURE_SECRET=your_actor_signature_secret_min_32_chars
```

Do not prefix secrets with `NEXT_PUBLIC_`. Next.js only needs them in middleware
or route handlers on the server side. `FRONTEND_CONSOLE_*` credentials are the
browser entry guard for the console; replace the sample password with a rotated
secret of at least 16 characters. `FRONTEND_ACTOR_*` is the backend actor the BFF
uses after the browser has been authenticated. Production mode rejects missing
actor values, placeholder secrets, `APP_TENANT_ID=demo_tenant`, short internal
API keys, and short actor signature secrets before it calls the Agent API.

With Docker Compose:

```bash
docker compose up --build
```

The compose file binds backend `8000` and console `3000` to `127.0.0.1` by
default. Put an authenticated reverse proxy or gateway in front before exposing
either service outside the host. To start the optional Prometheus service for
local production-style monitoring, run:

```bash
docker compose --profile observability up --build
```

Prometheus then listens on `9090` and scrapes the backend container at
`app:8000`. The compose port is bound to `127.0.0.1:9090`, so expose it through
a protected network path or SSH tunnel when you are not running on your own
machine.

To start the background alert dispatcher and async monitor review worker with
the same SQLite event store, run:

```bash
docker compose --profile alerts up --build
```

That profile starts `support-agent-alert-dispatcher` and
`support-agent-monitor-review-worker`; the console stays a trusted operator
surface rather than the only process responsible for durable monitoring work.

To start the durable sanitized audit export batch worker with the same SQLite
event store and shared `./data` volume, run:

```bash
docker compose --profile audit up --build
```

That profile starts `support-agent-audit-export-worker`, which writes NDJSON
and manifest files under `APP_AUDIT_EXPORT_DIR` and records batch health in
the event-store operation ledger.

## What The Console Shows

- Monitor alert queue from `MonitorSummary`.
- Triage health from persisted monitor and triage events. The strip shows active
  alerts, unassigned active alerts, new events since the latest triage action,
  stale active alerts, P0/P1 pressure, oldest active alert age, and MTTA.
  A resolved alert with fresh events is treated as active again so recurrence is
  visible in the queue; silenced alerts remain hidden from the active queue.
- Snapshot freshness in the top action bar. `Live` polls the real BFF snapshot
  while the tab is visible; `Paused` keeps the current snapshot but still shows
  its age. `Degraded` shows when optional read models such as SLO, promotion,
  automation, delivery, or incident evidence fail while the main snapshot still
  loads. `Stale` or `Failed` blocks acknowledge, assign, resolve, delivery
  dispatch, replay, and close actions until the snapshot is refreshed. Queue
  cards that appear or change between snapshots receive a compact `new alert`
  or `updated` badge.
- Guarded triage writes. The console sends an `expectedAlert` snapshot with
  every acknowledge, assign, investigate, and resolve action. The BFF forwards
  it as backend `expected_alert`; if the alert status, owner, count, latest
  monitor event time, latest triage event id, or new-events flag changed since
  the operator loaded the snapshot, the backend returns `409 Conflict`. The UI
  refreshes evidence and keeps the operator note so the user can review the new
  state before retrying.
- Guarded feedback review writes. The console sends an `expectedReview`
  fingerprint with each feedback review action, derived from the compact review
  queue projection or the loaded review trail. The BFF forwards it as backend
  `expected_review`; if another operator already appended a review event, the
  backend returns `409 Conflict`, refreshes the trail/backlog evidence, and
  keeps the operator note for a deliberate retry.
- Alert delivery health from `GET /api/v1/admin/monitor/alert-deliveries/summary`.
  The strip shows whether proactive webhook delivery is disabled, queued,
  degraded, failed, or ok, using the durable delivery outbox rather than live
  UI state. `claimed` means a dispatcher currently holds a short lease; `dead`
  means the delivery exceeded the configured max attempts and needs operator
  action. The same strip also shows dispatcher heartbeat status and last-seen
  age, so an operator can distinguish a healthy empty outbox from a missing or
  stale background worker. When signed receipt tracking is enabled, the `Receipt`
  metric shows covered eligible sent deliveries such as `3/4`; deliveries still
  inside `APP_MONITOR_ALERT_WEBHOOK_RECEIPT_GRACE_SECONDS` are shown as pending
  receipt evidence, while older sent rows without receipts degrade the strip.
- Monitor review worker health from `GET /api/v1/admin/monitor/review-worker/summary`.
  The Overview strip shows whether the async worker is `active`, `stale`,
  `missing`, or unavailable, using durable heartbeat rows rather than browser
  state. The backend summary includes only aggregate cycle counts such as
  inspected, reviewed, skipped, and failed runs; it does not expose worker id,
  run id, user text, tool arguments, or retrieved snippets.
- Audit export batch health from `GET /api/v1/admin/audit/export-batches/summary`.
  The Overview strip shows whether the latest durable sanitized export batch
  is `fresh`, `stale`, `missing`, `failed`, or unavailable. The Settings
  workbench shows latest file name, manifest file, record count, byte count,
  checksum prefix, ledger status, error type, and partial flag without full
  filesystem paths, actors, customer text, tool arguments, or automation
  command bodies/results.
- Delivery ledger from `GET /api/v1/admin/monitor/alert-deliveries`. The
  `Delivery` workbench tab filters outbox rows by status and lets an operator
  run `Dispatch now`, replay/requeue dead rows, or close `dead` rows through
  the BFF. `Dispatch now` calls the real backend outbox dispatcher, then
  refreshes the ledger and alert delivery health strip. In production, the
  `support-agent-alert-dispatcher` worker or Compose `alerts` profile should
  run this cycle continuously; the console button is the operator fallback.
  The browser still calls only `/api/console/*`; production signing and request
  nonces stay inside the server-side `agentFetch` proxy.
- Webhook receipt ledger from `GET /api/v1/admin/monitor/alert-webhook-receipts`.
  The `Receipts` workbench tab searches by alert key or exact delivery id and
  shows receiving-side proof: delivery id, alert key, severity, body hash,
  duplicate count, sample counts, and first/last received timestamps. It is
  read-only and intentionally does not show webhook body, headers, reason text,
  source address, user-agent, signature value, or sample ids.
- Monitor drilldown from persisted `monitor.reviewed` events. It reuses the
  alert queue context, shows backend bucket aggregates, and opens a sampled
  run through the same trace/evidence panel. For the selected event, it can
  request a backend-generated regression eval draft and copy the strict JSON
  without writing to repo files from production.
- Run workbench backed by persisted `agent.run.completed` events. It searches
  by run text, gateway request id, parent trace id, user, conversation, intent,
  route, status, and tool error code, then opens the same trace/evidence
  investigation view.
- Tools workbench backed by persisted `tool_audit_records`. It filters by tool,
  trace, request, actor, status, error code, replay state, and time window; the
  SLA stats come from the backend summary endpoint, not from only the visible
  page of rows.
- Knowledge workbench backed by the same knowledge adapter the agent uses. It
  sends operator queries through the BFF, returns snippets instead of full
  document bodies, and exposes rewrite queries, stage counts, selected sources,
  dropped candidates, and top-score signals for recall debugging. When opened,
  it lazily loads the index/provider summary and shows only status, backend,
  document count, chunk count, restricted document/chunk counts, and last-ingest
  time. It does not display SQLite absolute paths, raw source paths, full chunk
  text, metadata, required scope names, headers, API keys, or actor claims.
- Memory workbench backed by the append-only event store. It accepts any
  conversation id, calls the backend replay endpoint through the BFF, and shows
  rebuilt facts, working summary, open questions, recent messages, replayed
  run count, and ignored event count without mutating live memory.
- Feedback workbench backed by persisted `agent.response.feedback` and
  `agent.response.feedback.reviewed` events. It filters ratings by run,
  user, conversation, rating, and time window, shows reason aggregates and
  backend-derived review backlog metrics, records append-only operator review
  states with assignee and note, and can generate a regression draft from the
  selected feedback record.
- Settings workbench for release and event-store operations. It expands the
  read-only promotion gate into per-check readiness, monitor, tool-audit,
  feedback, and eval evidence, records approve/reject/defer decisions as
  append-only audit events, shows durable audit export batch health, downloads
  sanitized audit NDJSON, creates verified backups through a label-only BFF call, runs restore drills through a
  token-only BFF call, previews retention, and only enables apply after a
  verified backup, passed restore drill, matching dry-run report, server-issued
  tokens, and operator confirmation. The backend also requires the restore
  drill token for direct API apply calls and rejects stale previews with
  `409 Conflict`; the browser never sends
  filesystem paths to the backend. The same screen shows the durable
  event-store operation ledger with operation/status filters and a refresh
  control. Ledger rows are fetched from `GET /api/v1/admin/event-store/operations`
  and contain safe summaries only: file names, path hashes, token hashes,
  candidate/deleted counts, and short rejection/error details. Settings also
  shows the operations automation execution ledger with action-kind, status,
  source, and actor filters. Those rows are fetched from
  `GET /api/v1/admin/operations/automation-executions` and show command
  method/path, sanitized query, body keys/hash, command fingerprint, result
  summary, actor, source, and timestamp without exposing raw command bodies or
  raw automation results. A separate 24h execution-health strip shows total,
  failed, rejected, failure rate, and latest failure source/action kind from
  `GET /api/v1/admin/operations/automation-executions/summary`.
- Service Objectives in `Settings` uses `GET /api/v1/admin/operations/slo-report`
  to display grounded rate, policy compliance, human-review pressure, active
  P0/P1 alerts, tool failure rate, negative feedback, eval freshness, MTTA, and
  alert delivery health, plus async monitor review worker health. Each row shows the status and remaining error budget
  from backend aggregates, not from browser-only math.
- Operations Automation in `Settings` uses `GET /api/v1/admin/operations/automation-plan`
  to show prioritized actions with command method/path, required scopes, and
  whether an external runner may safely auto-execute them. The plan is read-only:
  it can recommend dispatching alert deliveries, generating an incident brief,
  inspecting sent deliveries missing receipt proof, drafting a regression eval,
  or keeping promotion blocked, but it never mutates triage, delivery, eval, or
  release state by itself.
- Queue workbench controls for severity, status, search, new-event filtering,
  and severity/newest/count sorting.
- Shareable investigation URLs for `runId`, `alertKey`, active workspace,
  evidence tab, and queue filters. Pasting the URL restores the same incident
  context and reloads the matching backend snapshot.
- Operations overview for active alerts, P0/P1 pressure, readiness, grounded
  rate, policy compliance, the latest persisted staging eval gate status, the
  async monitor review worker heartbeat, audit export batch health, SLO report
  status, and the read-only promotion gate status.
- Incident brief with owner, risk, recommended next actions, readiness checks,
  promotion checks, latest eval gate audit, recent gate history, and backend
  generated Markdown that can be copied or downloaded without message content,
  tool payloads, retrieval body text, memory facts, or feedback comments.
- Incident timeline via `GET /api/v1/admin/incidents/runs/{run_id}/timeline`.
  It orders sanitized event-store rows, tool audit rows, feedback, triage,
  alert delivery, alert webhook receipt summaries, and eval-gate evidence so
  operators can see what happened before reading raw trace details. Webhook
  bodies, headers, reason text, and sample ids stay out of the timeline.
- Agent run timeline from `AgentRunTrace`.
- Retrieval citations from `run.retrieval.selected_context`.
- Tool audit from `tool_audit_records`.
- Policy findings and monitor events.
- Memory replay from append-only events.
- Triage history and write actions via `POST /api/v1/admin/monitor/alerts/{alert_key}/triage`.
- Staging eval gate via `POST /api/v1/admin/evals/staging`, plus typed history from
  `GET /api/v1/admin/evals/gates`. Each run appends suite-level
  `eval.gate.completed` events for golden, security, tool failure, memory,
  routing, monitor, and retrieval checks, then appends one aggregate record
  for the whole gate. Records include case summaries, actor, trigger, run/alert
  context, duration, and status; they intentionally do not persist full answer
  text. Bundled eval endpoints are rejected in production mode, so the console
  can expose the control without weakening production safety.
- Promotion gate via `GET /api/v1/admin/promotion/gate`. The console uses it
  as a read-only preflight that combines readiness, monitor triage metrics,
  tool audit failure rate, feedback negative rate, and the latest aggregate
  staging eval gate. It returns `passed`, `warn`, or `blocked` with evidence
  for each check; it does not run evals or change alert triage state.
- SLO report via `GET /api/v1/admin/operations/slo-report`. It returns
  `slo_report.v1` with objective status, target, observed aggregate, and error
  budget remaining. It is useful for on-call review because no-data objectives
  are visible rather than silently treated as healthy. Automation execution
  failure rate and async monitor review worker health are included so cron,
  on-call bot, API, worker, and console automation failures can block service
  objectives instead of hiding in history rows.
- Operations automation plan via `GET /api/v1/admin/operations/automation-plan`.
  It combines the same production evidence with alert delivery, incident brief,
  receipt-gap, regression-draft, retrieval, and staging-eval recommendations.
  Each action returns a runnable backend command plus scopes and guardrails, so
  teams can connect cron or an on-call bot without inventing a separate rules engine.
  The Settings UI can run only server-generated `auto-safe` actions through
  `POST /api/console/operations/automation-actions`: the BFF re-fetches the
  current plan, matches the action id, rejects manual actions, and checks the
  backend command against a small allowlist before forwarding it. Browser input
  is never treated as an arbitrary admin proxy path. Completed and failed
  command executions are recorded through
  `POST /api/v1/admin/operations/automation-executions`; the response shows the
  audit record id when the ledger write succeeds. The same Settings screen can
  refresh and filter recent execution records, so cron, on-call bot, console,
  and API-triggered automation share one operator-visible audit trail.
- Promotion decisions via `POST /api/v1/admin/promotion/decisions`. The backend
  recomputes the gate, stores the decision and gate snapshot as
  `release.promotion.decision`, and rejects non-override approval while the gate
  is blocked.
- Audit export via `GET /api/v1/admin/audit/export`. The BFF streams NDJSON
  summary rows from events, tool audit records, event-store operation ledger
  rows, and operations automation execution ledger rows; raw messages,
  comments, tool arguments, automation command paths, query values, bodies,
  raw automation results, operation tokens, full filesystem paths, and eval
  answers are not included.
  For unattended SIEM or warehouse ingestion, the production path is the
  `support-agent-audit-export-worker` batch plus manifest. The console reads
  `/api/v1/admin/audit/export-batches/summary` so operators can see whether the
  latest manifest is fresh and complete before trusting downstream ingestion.

The console is intentionally detail-heavy because it is meant to teach how a
production-shaped agent behaves across intent detection, routing, tools, RAG,
memory, safety, monitoring, and incident response.

## Operator Workflow

1. Check the top-bar snapshot freshness before changing state. If it says
   `Stale` or `Failed`, click `Refresh` or resume `Live` before acknowledging,
   assigning, resolving, dispatching, replaying, or closing anything.
2. Start in the alert queue and keep the default `Active` status filter on.
3. Read the `Triage Health` strip before opening a single incident. `New` means
   an alert had fresh monitor events after the latest operator action; do not
   resolve it until the new sample is checked.
4. Read `Alert Delivery` before assuming the on-call path is covered. `Webhook off`
   means proactive delivery is intentionally disabled; `Dispatch failed` means
   open the `Delivery` tab, click `Dispatch now`, and inspect failed/dead rows
   before resolving P0/P1 work.
5. Switch the `Alerts` workbench to `Receipts` after dispatching or during an
   end-to-end webhook test to confirm the receiver accepted signed deliveries.
   Search by the active alert key for recent proof, or paste a delivery id for
   exact idempotency/duplicate checks.
6. Switch the `Alerts` workbench to `Drilldown` when you need to inspect the
   actual monitor events behind an alert, compare failure buckets, or open a
   sampled run from the event list.
7. Switch to `Runs` when you need historical investigation across users,
   conversations, routes, or tool error codes.
8. Switch to `Tools` when the problem is a timeout, upstream error, replay, or
   suspected idempotency issue; open any audit row to hydrate its full run.
9. Switch to `Knowledge` when the answer has weak citations, missing grounding,
   or a suspected recall/rerank/query-rewrite issue.
10. Switch to `Memory` when a later turn forgot facts, merged the wrong order,
   or behaved differently after a restart. Use `Current run` to replay the
   active conversation, or paste any conversation id from a support ticket.
11. Use alert search to find a run, owner, alert reason, or event id.
12. Assign the alert before investigation so ownership is explicit.
13. Copy the browser URL when handing off to another operator; it preserves the
   selected run, alert, workspace, evidence tab, and queue filters.
14. Open `Brief` first for the operator summary and recommended next actions.
15. Drill into `Citations`, `Tool Audit`, and `Memory` only when the brief points
   at missing grounding, tool failures, or replay questions.
16. In `Drilldown`, select the monitor event and use `Draft eval` to preview a
   regression case. The backend chooses the closest file, such as
   `security_regression.json` or `tool_failure_regression.json`, and validates
   the draft against the strict eval schema.
17. Run the eval gate in local/staging before promoting prompt, routing, tool, or
   policy changes. Check the persisted history row so the reviewer can see who
   ran it, when, against which run/alert context, and whether any cases failed.
18. Use `Settings` before release: inspect `Release Preflight` and do not
   promote while readiness, monitor, tool-audit, feedback, or eval checks are blocked.
   In `Operations Automation`, run only `auto-safe` actions from a fresh
   snapshot; manual actions still require the normal operator workflow.
19. Use `Settings` before manual cleanup: create a verified backup, run restore
   drill, preview retention, then apply only after reviewing the table-level
   candidate counts. After each step, refresh the `Operation Ledger` and confirm
   the completed/rejected rows match the action you intended to take.
20. Resolve only after the triage note explains customer impact and mitigation.

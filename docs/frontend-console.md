# Frontend Console

This project now includes a real operations console in `frontend/`. It is a
Next.js app with server-side BFF routes under `/api/console/*`.

The browser never calls the FastAPI service directly. It calls the Next.js BFF,
and the BFF calls the real Agent API:

- Local learning mode sends `X-Demo-User` and `X-Demo-Role`.
- Production mode injects `X-Internal-Auth`, signed `X-Actor-*` claims, and
  request signatures when `FRONTEND_REQUEST_SIGNATURE_REQUIRED=true`.
- No fake incident, alert, citation, memory, or tool-audit data is hardcoded in
  the UI. Empty screens mean the backend returned no events.

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
5. `GET /api/v1/admin/incidents/runs/{run_id}?include_memory=true`
6. `GET /api/v1/admin/incidents/runs/{run_id}/brief` when the `Brief`
   panel copies or downloads a backend-generated sanitized Markdown handoff.
7. `GET /api/v1/admin/runs` when the `Runs` workbench searches persisted
   history.
8. `GET /api/v1/admin/tools/audit` and
   `GET /api/v1/admin/tools/audit/summary` when the `Tools` workbench searches
   persisted tool calls and SLA/failure aggregates.
9. `POST /api/v1/admin/knowledge/search` when the `Knowledge` workbench runs
   a retrieval diagnostic query.
10. `POST /api/v1/admin/monitor/alert-deliveries/dispatch` when the `Delivery`
   tab runs `Dispatch now` against the durable alert outbox.
11. `GET /api/v1/admin/monitor/drilldown` when the `Alerts` workbench switches
   from queue triage to event-level investigation by alert key, intent, risk,
   failure type, grounding, policy status, and human-review state.
12. `POST /api/v1/admin/evals/regression-drafts` when an operator turns a
   selected monitor event or response-feedback record into a copyable eval-case
   draft.
13. `POST /api/v1/admin/event-store/backups` when the `Settings` workbench
   creates a verified SQLite backup.
14. `POST /api/v1/admin/event-store/retention` when the `Settings` workbench
   previews or applies the conservative retention policy.
15. `GET /api/v1/admin/conversations/{conversation_id}/memory/replay` when
   the `Memory` workbench rebuilds a conversation from append-only events.
16. `GET /api/v1/admin/feedback` and
   `GET /api/v1/admin/feedback/summary` when the `Feedback` workbench reviews
   user/operator ratings linked to persisted runs.
17. `GET /api/v1/admin/promotion/decisions` and
   `POST /api/v1/admin/promotion/decisions` when `Settings` shows or records
   append-only release decisions tied to a fresh promotion-gate snapshot.
18. `GET /api/v1/admin/operations/slo-report` when `Overview` and `Settings`
   show service objectives, error-budget remaining, and breached/watch/no-data
   counts.
19. `GET /api/v1/admin/operations/automation-plan` when `Settings` shows the
   read-only next-action queue for monitor, delivery, release, eval, feedback,
   tool-audit, and retrieval follow-up.
20. `GET /api/v1/admin/audit/export` when `Settings` downloads sanitized
   NDJSON for SIEM or warehouse ingestion.

## Production Run

Use the console as a trusted server-side gateway:

```text
AGENT_API_BASE_URL=http://app:8000
FRONTEND_AUTH_MODE=production
FRONTEND_ACTOR_USER_ID=console_operator
FRONTEND_ACTOR_ROLES=admin
FRONTEND_ACTOR_SCOPES=crm:read,order:read,shipping:read,ticket:write,kb:read,feedback:write,admin:read,admin:write,audit:read,events:read,eval:read,eval:run,feedback:read,knowledge:diagnose,memory:replay,monitor:read,monitor:write
FRONTEND_REQUEST_SIGNATURE_REQUIRED=true
APP_TENANT_ID=your_tenant
APP_INTERNAL_API_KEY=your_internal_gateway_secret
APP_ACTOR_SIGNATURE_SECRET=your_actor_signature_secret_min_32_chars
```

Do not prefix secrets with `NEXT_PUBLIC_`. Next.js only needs them in route
handlers on the server side.

With Docker Compose:

```bash
docker compose up --build
```

The backend listens on `8000`; the console listens on `3000`. To start the
optional Prometheus service for local production-style monitoring, run:

```bash
docker compose --profile observability up --build
```

Prometheus then listens on `9090` and scrapes the backend container at
`app:8000`. The compose port is bound to `127.0.0.1:9090`, so expose it through
a protected network path or SSH tunnel when you are not running on your own
machine.

## What The Console Shows

- Monitor alert queue from `MonitorSummary`.
- Triage health from persisted monitor and triage events. The strip shows active
  alerts, unassigned active alerts, new events since the latest triage action,
  stale active alerts, P0/P1 pressure, oldest active alert age, and MTTA.
  A resolved alert with fresh events is treated as active again so recurrence is
  visible in the queue; silenced alerts remain hidden from the active queue.
- Alert delivery health from `GET /api/v1/admin/monitor/alert-deliveries/summary`.
  The strip shows whether proactive webhook delivery is disabled, queued,
  degraded, failed, or ok, using the durable delivery outbox rather than live
  UI state. `claimed` means a dispatcher currently holds a short lease; `dead`
  means the delivery exceeded the configured max attempts and needs operator
  action.
- Delivery ledger from `GET /api/v1/admin/monitor/alert-deliveries`. The
  `Delivery` workbench tab filters outbox rows by status and lets an operator
  run `Dispatch now`, replay/requeue dead rows, or close `dead` rows through
  the BFF. `Dispatch now` calls the real backend outbox dispatcher, then
  refreshes the ledger and alert delivery health strip. The browser still
  calls only `/api/console/*`; production signing and request nonces stay
  inside the server-side `agentFetch` proxy.
- Monitor drilldown from persisted `monitor.reviewed` events. It reuses the
  alert queue context, shows backend bucket aggregates, and opens a sampled
  run through the same trace/evidence panel. For the selected event, it can
  request a backend-generated regression eval draft and copy the strict JSON
  without writing to repo files from production.
- Run workbench backed by persisted `agent.run.completed` events. It searches
  by run text, user, conversation, intent, route, status, and tool error code,
  then opens the same trace/evidence investigation view.
- Tools workbench backed by persisted `tool_audit_records`. It filters by tool,
  trace, request, actor, status, error code, replay state, and time window; the
  SLA stats come from the backend summary endpoint, not from only the visible
  page of rows.
- Knowledge workbench backed by the same knowledge adapter the agent uses. It
  sends operator queries through the BFF, returns snippets instead of full
  document bodies, and exposes rewrite queries, stage counts, selected sources,
  dropped candidates, and top-score signals for recall debugging.
- Memory workbench backed by the append-only event store. It accepts any
  conversation id, calls the backend replay endpoint through the BFF, and shows
  rebuilt facts, working summary, open questions, recent messages, replayed
  run count, and ignored event count without mutating live memory.
- Settings workbench for release and event-store operations. It expands the
  read-only promotion gate into per-check readiness, monitor, tool-audit,
  feedback, and eval evidence, records approve/reject/defer decisions as
  append-only audit events, downloads sanitized audit NDJSON, creates verified
  backups through a label-only BFF call, previews retention, and only enables
  apply after a verified backup, a dry-run report, and operator confirmation.
  The browser never sends filesystem paths to the backend.
- Service Objectives in `Settings` uses `GET /api/v1/admin/operations/slo-report`
  to display grounded rate, policy compliance, human-review pressure, active
  P0/P1 alerts, tool failure rate, negative feedback, eval freshness, MTTA, and
  alert delivery health. Each row shows the status and remaining error budget
  from backend aggregates, not from browser-only math.
- Operations Automation in `Settings` uses `GET /api/v1/admin/operations/automation-plan`
  to show prioritized actions with command method/path, required scopes, and
  whether an external runner may safely auto-execute them. The plan is read-only:
  it can recommend dispatching alert deliveries, generating an incident brief,
  drafting a regression eval, or keeping promotion blocked, but it never mutates
  triage, delivery, eval, or release state by itself.
- Queue workbench controls for severity, status, search, new-event filtering,
  and severity/newest/count sorting.
- Shareable investigation URLs for `runId`, `alertKey`, active workspace,
  evidence tab, and queue filters. Pasting the URL restores the same incident
  context and reloads the matching backend snapshot.
- Operations overview for active alerts, P0/P1 pressure, readiness, grounded
  rate, policy compliance, the latest persisted staging eval gate status, the
  SLO report status, and the read-only promotion gate status.
- Incident brief with owner, risk, recommended next actions, readiness checks,
  promotion checks, latest eval gate audit, recent gate history, and backend
  generated Markdown that can be copied or downloaded without message content,
  tool payloads, retrieval body text, memory facts, or feedback comments.
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
  are visible rather than silently treated as healthy.
- Operations automation plan via `GET /api/v1/admin/operations/automation-plan`.
  It combines the same production evidence with alert delivery, incident brief,
  regression-draft, retrieval, and staging-eval recommendations. Each action
  returns a runnable backend command plus scopes and guardrails, so teams can
  connect cron or an on-call bot without inventing a separate rules engine.
- Promotion decisions via `POST /api/v1/admin/promotion/decisions`. The backend
  recomputes the gate, stores the decision and gate snapshot as
  `release.promotion.decision`, and rejects non-override approval while the gate
  is blocked.
- Audit export via `GET /api/v1/admin/audit/export`. The BFF streams NDJSON
  summary rows from events and tool audit records; raw messages, comments,
  tool arguments, and eval answers are not included.

The console is intentionally detail-heavy because it is meant to teach how a
production-shaped agent behaves across intent detection, routing, tools, RAG,
memory, safety, monitoring, and incident response.

## Operator Workflow

1. Start in the alert queue and keep the default `Active` status filter on.
2. Read the `Triage Health` strip before opening a single incident. `New` means
   an alert had fresh monitor events after the latest operator action; do not
   resolve it until the new sample is checked.
3. Read `Alert Delivery` before assuming the on-call path is covered. `Webhook off`
   means proactive delivery is intentionally disabled; `Dispatch failed` means
   open the `Delivery` tab, click `Dispatch now`, and inspect failed/dead rows
   before resolving P0/P1 work.
4. Switch the `Alerts` workbench to `Drilldown` when you need to inspect the
   actual monitor events behind an alert, compare failure buckets, or open a
   sampled run from the event list.
5. Switch to `Runs` when you need historical investigation across users,
   conversations, routes, or tool error codes.
6. Switch to `Tools` when the problem is a timeout, upstream error, replay, or
   suspected idempotency issue; open any audit row to hydrate its full run.
7. Switch to `Knowledge` when the answer has weak citations, missing grounding,
   or a suspected recall/rerank/query-rewrite issue.
8. Switch to `Memory` when a later turn forgot facts, merged the wrong order,
   or behaved differently after a restart. Use `Current run` to replay the
   active conversation, or paste any conversation id from a support ticket.
9. Use alert search to find a run, owner, alert reason, or event id.
10. Assign the alert before investigation so ownership is explicit.
11. Copy the browser URL when handing off to another operator; it preserves the
   selected run, alert, workspace, evidence tab, and queue filters.
12. Open `Brief` first for the operator summary and recommended next actions.
13. Drill into `Citations`, `Tool Audit`, and `Memory` only when the brief points
   at missing grounding, tool failures, or replay questions.
14. In `Drilldown`, select the monitor event and use `Draft eval` to preview a
   regression case. The backend chooses the closest file, such as
   `security_regression.json` or `tool_failure_regression.json`, and validates
   the draft against the strict eval schema.
15. Run the eval gate in local/staging before promoting prompt, routing, tool, or
   policy changes. Check the persisted history row so the reviewer can see who
   ran it, when, against which run/alert context, and whether any cases failed.
16. Use `Settings` before release: inspect `Release Preflight` and do not
   promote while readiness, monitor, tool-audit, feedback, or eval checks are blocked.
17. Use `Settings` before manual cleanup: create a verified backup, preview
   retention, then apply only after reviewing the table-level candidate counts.
18. Resolve only after the triage note explains customer impact and mitigation.

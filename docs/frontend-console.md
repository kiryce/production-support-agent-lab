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
4. `GET /api/v1/admin/incidents/runs/{run_id}?include_memory=true`
5. `GET /api/v1/admin/runs` when the `Runs` workbench searches persisted
   history.
6. `GET /api/v1/admin/tools/audit` and
   `GET /api/v1/admin/tools/audit/summary` when the `Tools` workbench searches
   persisted tool calls and SLA/failure aggregates.
7. `POST /api/v1/admin/knowledge/search` when the `Knowledge` workbench runs
   a retrieval diagnostic query.

## Production Run

Use the console as a trusted server-side gateway:

```text
AGENT_API_BASE_URL=http://app:8000
FRONTEND_AUTH_MODE=production
FRONTEND_ACTOR_USER_ID=console_operator
FRONTEND_ACTOR_ROLES=admin
FRONTEND_ACTOR_SCOPES=crm:read,order:read,shipping:read,ticket:write,kb:read,admin:read,audit:read,events:read,eval:run,knowledge:diagnose,memory:replay,monitor:read,monitor:write
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

The backend listens on `8000`; the console listens on `3000`.

## What The Console Shows

- Monitor alert queue from `MonitorSummary`.
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
- Queue workbench controls for severity, status, search, new-event filtering,
  and severity/newest/count sorting.
- Operations overview for active alerts, P0/P1 pressure, readiness, grounded
  rate, policy compliance, and staging eval status.
- Incident brief with owner, risk, recommended next actions, readiness checks,
  and a copyable Markdown handoff.
- Agent run timeline from `AgentRunTrace`.
- Retrieval citations from `run.retrieval.selected_context`.
- Tool audit from `tool_audit_records`.
- Policy findings and monitor events.
- Memory replay from append-only events.
- Triage history and write actions via `POST /admin/monitor/alerts/{alert_key}/triage`.
- Staging eval gate via `POST /admin/evals/golden`. The backend rejects this in
  production mode, so the console can expose the control without weakening
  production safety.

The console is intentionally detail-heavy because it is meant to teach how a
production-shaped agent behaves across intent detection, routing, tools, RAG,
memory, safety, monitoring, and incident response.

## Operator Workflow

1. Start in the alert queue and keep the default `Active` status filter on.
2. Switch to `Runs` when you need historical investigation across users,
   conversations, routes, or tool error codes.
3. Switch to `Tools` when the problem is a timeout, upstream error, replay, or
   suspected idempotency issue; open any audit row to hydrate its full run.
4. Switch to `Knowledge` when the answer has weak citations, missing grounding,
   or a suspected recall/rerank/query-rewrite issue.
5. Use alert search to find a run, owner, alert reason, or event id.
6. Assign the alert before investigation so ownership is explicit.
7. Open `Brief` first for the operator summary and recommended next actions.
8. Drill into `Citations`, `Tool Audit`, and `Memory` only when the brief points
   at missing grounding, tool failures, or replay questions.
9. Run the eval gate in local/staging before promoting prompt, routing, tool, or
   policy changes.
10. Resolve only after the triage note explains customer impact and mitigation.

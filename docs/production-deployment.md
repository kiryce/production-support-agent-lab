# Production deployment

Production mode does not use local fixtures or deterministic model output. It requires real service configuration and fails during startup if required integrations are missing.

## Required environment

```text
APP_ENV=production
APP_TENANT_ID=your_real_tenant
APP_REQUIRE_PRODUCTION=true
APP_MODEL_PROVIDER=openai
APP_OPENAI_MODEL=gpt-5.5
OPENAI_API_KEY=...
APP_BUSINESS_API_BASE_URL=https://support-backend.example.com
APP_BUSINESS_API_KEY=...
APP_KNOWLEDGE_BACKEND=auto
APP_KNOWLEDGE_API_BASE_URL=https://knowledge.example.com
APP_KNOWLEDGE_API_KEY=...
APP_KNOWLEDGE_DATABASE_URL=sqlite:///./data/knowledge/support-agent-knowledge.db
APP_KNOWLEDGE_INGEST_SOURCE_DIR=./examples/knowledge
APP_KNOWLEDGE_CHUNK_CHARS=1200
APP_KNOWLEDGE_CHUNK_OVERLAP_CHARS=160
APP_KNOWLEDGE_FTS_ENABLED=true
APP_KNOWLEDGE_MIN_READY_DOCUMENTS=1
APP_INTERNAL_API_KEY=...
APP_ACTOR_SIGNATURE_SECRET=replace_with_real_actor_signature_secret_min_32_chars
APP_ACTOR_SIGNATURE_MAX_AGE_SECONDS=300
APP_REQUEST_SIGNATURE_REQUIRED=true
APP_RATE_LIMIT_ENABLED=true
APP_RATE_LIMIT_BACKEND=auto
APP_RATE_LIMIT_REQUESTS_PER_MINUTE=600
APP_RATE_LIMIT_BURST=600
APP_HTTP_TIMEOUT_MS=5000
APP_BUSINESS_API_RETRY_ATTEMPTS=2
APP_BUSINESS_API_RETRY_BACKOFF_MS=100
APP_BUSINESS_API_CIRCUIT_FAILURE_THRESHOLD=5
APP_BUSINESS_API_CIRCUIT_RESET_SECONDS=30
APP_KNOWLEDGE_API_RETRY_ATTEMPTS=2
APP_KNOWLEDGE_API_RETRY_BACKOFF_MS=100
APP_KNOWLEDGE_API_CIRCUIT_FAILURE_THRESHOLD=5
APP_KNOWLEDGE_API_CIRCUIT_RESET_SECONDS=30
APP_LLM_TIMEOUT_MS=15000
APP_LLM_RETRY_ATTEMPTS=2
APP_LLM_RETRY_BACKOFF_MS=250
APP_LLM_CIRCUIT_FAILURE_THRESHOLD=5
APP_LLM_CIRCUIT_RESET_SECONDS=30
APP_READINESS_DEEP_CHECKS=true
APP_DATABASE_URL=sqlite:///./data/production/support-agent-lab.db
APP_EVENT_RETENTION_DAYS=365
APP_TOOL_AUDIT_RETENTION_DAYS=180
APP_IDEMPOTENCY_RETENTION_DAYS=30
APP_ALERT_DELIVERY_RETENTION_DAYS=90
APP_MONITOR_REVIEW_WORKER_HEARTBEAT_STALE_SECONDS=180
APP_AUDIT_EXPORT_DIR=./data/audit-exports
APP_AUDIT_EXPORT_BATCH_STALE_SECONDS=86400
```

`APP_DATABASE_URL` currently supports SQLite. It stores the append-only event log, monitor triage events, tool idempotency records, tool audit records, alert delivery outbox, dispatcher heartbeats, async monitor review worker heartbeats, inbound alert webhook receipt summaries, event-store operation ledger rows, audit export batch ledger rows, and operations automation execution ledger rows. `SQLiteEventStore` enables WAL mode for the database file, plus a 5-second busy timeout, `synchronous=NORMAL`, and foreign-key enforcement on each connection so the backend, alert dispatcher, monitor review worker, and audit export worker can share the same database file more reliably in a single-instance deployment or staging environment. For multi-instance production, replace `SQLiteEventStore` with a Postgres/Kafka-backed implementation before scaling horizontally.

Retention knobs are intentionally conservative. They control the default window for event rows, durable tool audit rows, tool idempotency replay rows, terminal alert-delivery rows, and alert webhook receipt summaries. Event rows are never deleted by the retention operation unless the operator explicitly sets `include_events=true` or passes `--include-events` in the CLI.

## Business API contract

`HTTPBusinessClient` expects your internal support backend to expose:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/customers/{user_id}` | Return masked, verified customer profile. |
| `GET` | `/orders?customer_id=<id>&status=<optional>` | Search orders for the authenticated actor. |
| `GET` | `/orders/{order_id}` | Return one authorized order. |
| `GET` | `/shipments/{logistics_id}` | Return shipment tracking status. |
| `POST` | `/tickets` | Create support or handoff ticket. |
| `GET` | `/knowledge/search?query=<text>&limit=<n>` | Search knowledge base snippets. |
| `GET` | `/health` | Return 2xx when the business API can serve authenticated tool calls. |

Minimal response shapes:

- `GET /customers/{user_id}` returns `customer_id`, `name`, `tier`, optional `email_masked` / `phone_masked`, and `verified`.
- `GET /orders` returns `{"orders": [...]}` or a bare order list. Each order contains `order_id`, `customer_id`, `status`, `product`, `amount`, `currency`, optional `delivered_at` / `logistics_id`, and `returnable`.
- `GET /orders/{order_id}` returns one order object with the same order fields.
- `GET /shipments/{logistics_id}` returns `logistics_id`, `status`, `latest_event`, and `eta`. If it also returns `customer_id`, the adapter performs an extra ownership check.
- `POST /tickets` receives `customer_id`, `title`, `description`, `priority`, and `tags`; it returns `ticket_id`, `status`, and `created_at`.

The Agent may send `customer_id: "SELF"` in tool arguments. In production the HTTP adapter resolves that value through `GET /customers/{actor_user_id}` before calling `/orders` or `/tickets`, so your business API should not receive the literal string `SELF`.

The adapter sends these headers on every tool request:

```text
Authorization: Bearer <APP_BUSINESS_API_KEY>
X-Tenant-Id: <tenant>
X-Actor-User-Id: <user>
X-Actor-Roles: <roles>
X-Actor-Scopes: <scopes>
X-Request-Id: <request>
X-Trace-Id: <agent run>
X-Parent-Trace-Id: <gateway trace, when present>
traceparent: <W3C trace context, when parent trace is a valid trace id>
Idempotency-Key: <for write tools>
```

`/health` is a readiness probe, not an actor-scoped tool call. It sends service authentication plus tenant/request/trace headers so infrastructure can verify dependency reachability without pretending to be an end user.

The public API binds request correlation before auth, signature verification, and rate limiting. If the gateway supplies safe `X-Request-Id` and `X-Trace-Id` values, every response echoes them. If it supplies a valid W3C `traceparent`, the service uses the W3C trace id as `X-Trace-Id` / `parent_trace_id` and returns a service `traceparent` on the response. Missing or unsafe correlation values are replaced with bounded generated IDs, and malformed `traceparent` values are not reflected. Chat runs store `request_id` and `parent_trace_id` in `AgentRunTrace`, return `X-Agent-Run-Id`, and propagate the request id plus parent trace to business and knowledge adapters. When `parent_trace_id` is a W3C trace id, those adapters also send `traceparent` downstream. This gives operators a single chain across gateway logs, API responses, agent trace, tool audit, upstream service logs, and APM traces without putting high-cardinality trace IDs into Prometheus labels.

The business-tool headers are downstream context headers. The public Agent API has a separate ingress contract below: the trusted gateway must sign the inbound actor claims before this service trusts them. If your business backend also wants to reject header tampering at its own edge, give it an equivalent JWT/HMAC contract there too.

Production HTTP tools normalize upstream failures before the model sees them: `401 -> UNAUTHORIZED`, `403 -> FORBIDDEN`, `404 -> NOT_FOUND`, `409 -> CONFLICT`, `429 -> RATE_LIMITED`, `5xx -> UPSTREAM_ERROR`, timeout -> `TIMEOUT`, network failure -> `UPSTREAM_UNAVAILABLE`, invalid JSON -> `UPSTREAM_ERROR`.

The business adapter has a small production resilience layer. Safe reads and `/health` are attempted up to `APP_BUSINESS_API_RETRY_ATTEMPTS` times with exponential backoff starting at `APP_BUSINESS_API_RETRY_BACKOFF_MS`. Each attempt uses `APP_HTTP_TIMEOUT_MS`; the tool registry timeout is sized to cover the full attempt plus backoff budget. `POST /tickets` is retried only when the request has an `Idempotency-Key`; the business backend must treat that key as a real idempotency key before you enable production traffic. Retryable failures are `429`, `5xx`, timeout, network failure, and transient invalid JSON. Repeated retryable failures open an in-process circuit after `APP_BUSINESS_API_CIRCUIT_FAILURE_THRESHOLD` failures and keep it open for `APP_BUSINESS_API_CIRCUIT_RESET_SECONDS`; while open, tool calls fail fast with `UPSTREAM_UNAVAILABLE`.

Your backend should still enforce tenant isolation and resource ownership. The Agent has route-level `allowed_tools`, `ToolBroker` enforces scopes, and the HTTP adapter performs basic defense-in-depth checks, but production authorization must live in the business service too. Path identifiers such as `user_id`, `order_id`, and `logistics_id` are schema-validated and encoded before being placed in upstream URLs; keep the same rule when adding tools.

User input is checked by `PolicyEngine` before it is written to memory or the event log. Phone numbers and email addresses are stored as redacted tokens, while the trace keeps structured policy findings such as `PII_IN_INPUT`. If you add new PII classes, extend `PolicyEngine.redact_pii` and add a regression test before accepting production traffic.

## API authentication

Production API requests must come through a trusted gateway. The gateway authenticates the end user, then forwards:

```text
X-Internal-Auth: <APP_INTERNAL_API_KEY>
X-Actor-User-Id: <authenticated user id>
X-Actor-Roles: user,admin
X-Actor-Scopes: crm:read,order:read,shipping:read,ticket:write,kb:read,feedback:write
X-Actor-Timestamp: <unix timestamp>
X-Actor-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp>
X-Request-Id: <gateway request id, optional but recommended>
X-Trace-Id: <gateway trace id, optional but recommended>
traceparent: <W3C trace context, optional but preferred when available>
X-Request-Nonce: <unique request nonce>
X-Request-Body-SHA256: <sha256 of the exact HTTP request body bytes>
X-Request-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp/nonce/method/path/body hash>
```

`X-Demo-User` and `X-Demo-Role` are local-only teaching headers. In production they do not authenticate requests, and local fixture identities such as `user_demo` and `user_guest` are rejected. `X-Actor-Scopes` is required and should be the gateway's minimum capability set for this actor; missing or empty scopes fail closed.

`X-Actor-Signature` is an HMAC-SHA256 signature produced by the gateway with `APP_ACTOR_SIGNATURE_SECRET`. The canonical signed fields are: signature version `v1`, tenant id, user id, canonical comma-separated roles, canonical comma-separated scopes, and Unix timestamp. Roles and scopes are trimmed and empty entries are removed, but their order is preserved and not sorted. `X-Actor-Signature` may be the bare hex digest or `sha256=<digest>`. The service rejects missing signatures, invalid signatures, and timestamps outside `APP_ACTOR_SIGNATURE_MAX_AGE_SECONDS` so that a downstream proxy or client cannot add `admin`, broaden scopes, or swap user ids after the gateway has authenticated the request.

When `APP_REQUEST_SIGNATURE_REQUIRED=true`, or when it is unset and `APP_REQUIRE_PRODUCTION=true`, the service also requires `X-Request-Nonce`, `X-Request-Body-SHA256`, and `X-Request-Signature` on non-health endpoints. `APP_REQUIRE_PRODUCTION=true` fails startup if request signatures are explicitly disabled. The request signature binds the same actor claims to the actual HTTP method, path plus query string, request body hash, and nonce. The nonce is recorded in SQLite `api_request_nonces` until the actor signature time window expires, so a captured signed request cannot be replayed inside the normal timestamp window. In scaled production, move this nonce table to Redis/Postgres with the same unique key: tenant, actor user id, nonce.

This is a deployable baseline for a trusted internal gateway. Higher-risk deployments should also add mTLS/JWT at the gateway boundary and central nonce storage across all replicas.

This HMAC protects the Agent API ingress boundary. It is not a replacement for tenant/resource authorization inside the business service. `ToolBroker` enforces business tool scopes before every tool call, and your business API must still enforce tenant/resource ownership.

## Rate limiting

Production enables a token-bucket limiter by default unless `APP_RATE_LIMIT_ENABLED=false` is explicitly set. When `APP_REQUIRE_PRODUCTION=true`, explicitly disabling it fails startup. With `APP_RATE_LIMIT_BACKEND=auto`, local mode uses an in-process bucket and production / require-production mode uses SQLite `api_rate_limits`, so workers sharing the same event-store file share the same limit state. The limiter keys by tenant, actor user id, and endpoint family (`chat`, `admin`, `admin-evals`, or `api`). Health, readiness, docs, OpenAPI, and metrics endpoints are exempt.

Tune:

```text
APP_RATE_LIMIT_ENABLED=true
APP_RATE_LIMIT_BACKEND=auto
APP_RATE_LIMIT_REQUESTS_PER_MINUTE=600
APP_RATE_LIMIT_BURST=600
```

Limited requests return `429` with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-Request-Id`, and `X-Trace-Id`. Successful limited requests also include `X-RateLimit-Limit` and `X-RateLimit-Remaining` so a gateway or frontend can surface pressure before hard failure.

`APP_RATE_LIMIT_BACKEND=memory` is useful for local teaching and single-process tests, but startup rejects it when `APP_REQUIRE_PRODUCTION=true`. `APP_RATE_LIMIT_BACKEND=sqlite` forces the durable bucket in any environment. The SQLite bucket is included in backup/restore schema verification and old idle buckets are pruned during token consumption, but it is still a single database-file coordination point. For multi-replica production, move the token bucket state to Redis, Postgres, or your API gateway and keep the same key dimensions: tenant id, actor user id, and endpoint family.

Use the bundled signer to generate headers for production smoke tests:

```bash
export APP_TENANT_ID=your_real_tenant
export APP_INTERNAL_API_KEY=your_internal_gateway_secret
export APP_ACTOR_SIGNATURE_SECRET=your_actor_signature_secret_min_32_chars
python scripts/sign_actor_headers.py \
  --user-id user_prod \
  --roles user \
  --scopes "crm:read,order:read,shipping:read,ticket:write,kb:read,feedback:write" \
  --method POST \
  --path /api/v1/chat/sessions \
  --body '{"user_id":"user_prod"}' \
  --format curl
```

The console-script form is `support-agent-sign-headers`. Both paths call `support_agent_lab.security.actor_signature`, which is also used by the FastAPI verifier.

Admin role is not a wildcard. Production admin endpoints also require explicit management scopes:

| Endpoint family | Required scope |
| --- | --- |
| `/api/v1/admin/tools` | `admin:read` |
| `GET /api/v1/admin/tools/audit` | `audit:read` |
| `GET /api/v1/admin/tools/audit/summary` | `audit:read` |
| `POST /api/v1/admin/knowledge/search` | `knowledge:diagnose` |
| `GET /api/v1/admin/runs` | `events:read`; supports `request_id` and `parent_trace_id` filters for gateway-to-run investigations. |
| `GET /api/v1/admin/incidents/runs/{run_id}` | `events:read`, `monitor:read`, `audit:read`; add `memory:replay` when `include_memory=true` |
| `GET /api/v1/admin/incidents/runs/{run_id}/brief` | `events:read`, `monitor:read`, `audit:read`; add `memory:replay` when `include_memory=true`. Returns sanitized Markdown plus structured evidence. |
| `GET /api/v1/admin/incidents/runs/{run_id}/timeline` | `events:read`, `monitor:read`, `audit:read`, `feedback:read`. Returns a sanitized chronological incident timeline. |
| `/api/v1/admin/monitor/summary` | `monitor:read` |
| `/api/v1/admin/monitor/events` | `monitor:read` |
| `GET /api/v1/admin/monitor/drilldown` | `monitor:read` |
| `GET /api/v1/admin/monitor/triage/metrics` | `monitor:read` |
| `GET /api/v1/admin/monitor/review-worker/summary` | `monitor:read` |
| `GET /api/v1/admin/monitor/alert-deliveries/summary` | `monitor:read` |
| `GET /api/v1/admin/monitor/alert-deliveries` | `monitor:read` |
| `GET /api/v1/admin/monitor/alert-deliveries/receipt-gaps` | `monitor:read`. Lists sent delivery rows that exceeded the receipt grace period without receiver proof. |
| `GET /api/v1/admin/monitor/alert-webhook-receipts` | `monitor:read` |
| `POST /api/v1/admin/monitor/alert-deliveries/dispatch` | `monitor:write` |
| `POST /api/v1/admin/monitor/alert-deliveries/{delivery_id}/requeue` | `monitor:write` |
| `POST /api/v1/admin/monitor/alert-deliveries/{delivery_id}/close` | `monitor:write` |
| `GET /api/v1/admin/monitor/alerts/{alert_key}/triage` | `monitor:read` |
| `POST /api/v1/admin/monitor/alerts/{alert_key}/triage` | `monitor:write` |
| `/api/v1/admin/events` | `events:read`; add `feedback:read` when `event_type` is omitted or is `agent.response.feedback` / `agent.response.feedback.reviewed`, because raw payloads may include feedback comments or review notes. |
| `POST /api/v1/admin/event-store/backups` | `admin:write`, `audit:read`, `events:read`; backend chooses the configured backup directory. |
| `POST /api/v1/admin/event-store/restore-drills` | `admin:write`, `audit:read`, `events:read`; requires a server-issued `backup_token`, then copies the backup to a scratch database and verifies it without overwriting live data. |
| `POST /api/v1/admin/event-store/retention` | `admin:write`, `audit:read`, `events:read`; dry-run by default. |
| `GET /api/v1/admin/event-store/operations` | `admin:read`, `audit:read`, `events:read`; returns the durable event-store operation ledger without granting write access. |
| `GET /api/v1/admin/audit/export-batches/summary` | `audit:read`, `events:read`; returns durable sanitized audit export batch health without full filesystem paths. |
| `POST /api/v1/admin/evals/regression-drafts` | `events:read`, `monitor:read`; add `feedback:read` when `feedback_id` is supplied |
| `POST /api/v1/admin/evals/golden` | `eval:run`; local/staging only. Disabled when `APP_ENV=production`. |
| `POST /api/v1/admin/evals/staging` | `eval:run`; local/staging only. Runs bundled golden/security/tool/memory/routing/monitor/retrieval suites and appends suite + aggregate gate records. Disabled when `APP_ENV=production`. |
| `GET /api/v1/admin/evals/gates` | `eval:read` |
| `GET /api/v1/admin/feedback` | `feedback:read` |
| `GET /api/v1/admin/feedback/summary` | `feedback:read` |
| `GET /api/v1/admin/feedback/review-queue` | `feedback:read`. Returns compact current-state/backlog projection without comments or review notes. |
| `GET /api/v1/admin/feedback/{feedback_id}/reviews` | `feedback:read` |
| `POST /api/v1/admin/feedback/{feedback_id}/reviews` | `feedback:read`, `feedback:write` |
| `GET /api/v1/admin/promotion/gate` | `admin:read`, `monitor:read`, `audit:read`, `eval:read`, `feedback:read`. Read-only release preflight. |
| `GET /api/v1/admin/operations/slo-report` | `admin:read`, `monitor:read`, `audit:read`, `eval:read`, `feedback:read`. Read-only service objectives and error-budget report. |
| `GET /api/v1/admin/operations/automation-plan` | `admin:read`, `monitor:read`, `audit:read`, `events:read`, `eval:read`, `feedback:read`. Read-only next-action plan with runnable commands and guardrails. |
| `POST /api/v1/admin/operations/automation-executions` | `admin:write`. Records a sanitized automation action execution ledger row. |
| `GET /api/v1/admin/operations/automation-executions` | `admin:read`, `audit:read`, `events:read`. Lists sanitized automation action execution ledger rows. |
| `GET /api/v1/admin/operations/automation-executions/summary` | `admin:read`, `audit:read`, `events:read`. Returns bounded execution health aggregates for SLOs, console, metrics, and audit review. |
| `GET /api/v1/admin/promotion/decisions` | `admin:read`, `audit:read` |
| `POST /api/v1/admin/promotion/decisions` | `admin:write`, `admin:read`, `monitor:read`, `audit:read`, `eval:read`, `feedback:read`. Recomputes the release preflight and writes an append-only decision event. |
| `GET /api/v1/admin/audit/export` | `audit:read`, `events:read`. Returns sanitized `application/x-ndjson` for SIEM or warehouse ingestion. |
| `/api/v1/admin/conversations/{conversation_id}/memory/replay` | `memory:replay` |

`GET /api/v1/agent/runs/{run_id}` lets the original actor inspect their own run trace. Cross-user incident review must use an admin actor with `events:read`, and the endpoint falls back to the SQLite event store when live in-process run state has been cleared.

`GET /api/v1/admin/incidents/runs/{run_id}/brief` uses the same investigation
boundary as the incident bundle, then returns `incident_brief.v1`: title,
risk label, recommended actions, safe structured evidence, and copyable
Markdown. It includes run id, conversation id, intent, route, monitor failure
types, tool error codes, citation counts, tool-audit counts, and memory replay
counts. It deliberately excludes message content, tool arguments, tool payloads,
tool error messages, retrieval body text, memory facts, and feedback comments,
so the result can be attached to a ticket or handoff channel without exporting
the full customer transcript.

`GET /api/v1/admin/incidents/runs/{run_id}/timeline` returns
`incident_timeline.v1`: a chronological, sanitized investigation stream built
from event-store rows, durable tool audit rows, feedback, triage, alert
delivery, and eval-gate records. It shows event type, title, status/tone,
hashed correlation ids, and compact evidence such as tool name, error code,
feedback reason, risk level, delivery status, or eval counts. It deliberately
excludes message content, tool arguments, tool payloads, tool error text,
retrieval bodies, memory facts, feedback comments, feedback review notes,
triage notes, and alert delivery error text.

`POST /api/v1/agent/runs/{run_id}/feedback` lets the original actor attach a positive or negative rating, normalized reason codes, and a short comment to their own persisted run. It requires `feedback:write`, appends an `agent.response.feedback` event, and does not mutate the run trace. `source=user` is the default. `source=operator` or `source=qa` requires an admin actor; cross-user feedback must use one of those non-user sources so operators do not impersonate end users.

`GET /api/v1/admin/feedback/review-queue` is the production feedback backlog projection. It derives current status, unresolved count, unassigned unresolved count, stale unresolved count, latest assignee, and review counts from append-only events. The projection deliberately omits feedback comments, review notes, and raw event payloads; use the single-record review trail only when an operator opens a specific feedback record.

`GET/POST /api/v1/admin/feedback/{feedback_id}/reviews` is the production feedback triage loop. Reviews are append-only `agent.response.feedback.reviewed` events with status (`acknowledged`, `investigating`, `resolved`, or `dismissed`), assignee, actor, and operator note. The original feedback event is never mutated, so audit export, incident timeline, regression-draft generation, and promotion checks can replay the same history. Review writes may include `expected_review` with the current status, review count, latest review id/time, and assignee seen by the operator. The API recomputes the current review state before appending; if another operator has already changed the trail, it returns `409 Conflict` and does not write a stale review action.

`POST /api/v1/admin/evals/regression-drafts` can also accept `feedback_id` with the associated `run_id`. The service loads the persisted run, message events, monitor events, and feedback event, then returns a copyable `EvalCase` draft tagged with the feedback rating and reasons. It is still read-only; operators should review answer-level assertions before committing the draft to `examples/evals/*.json`.

Eval gate history is stored as append-only `eval.gate.completed` events and
returned through the typed `GET /api/v1/admin/evals/gates` endpoint. Records
include actor, trigger, suite, run/alert context, status, duration, failed case
ids, and compact case observations, but not full eval answer text.

`GET /api/v1/admin/promotion/gate` is the read-only release preflight used by
the console and automation. It combines readiness, monitor triage metrics,
tool audit summary, response feedback summary, and the latest
`gate_name=staging`, `runner=aggregate` eval gate. It returns `passed`, `warn`,
or `blocked` plus threshold evidence for each check. High feedback negative
rate blocks promotion, while thin feedback volume warns reviewers that the
online signal is not yet strong. It never runs bundled evals, writes triage
events, or returns raw tool arguments, raw monitor events, or eval answer text.

`GET /api/v1/admin/operations/slo-report` is the read-only service objective
view for on-call and release review. It returns `slo_report.v1` with individual
objective rows for grounded rate, policy compliance, human-review pressure,
active P0/P1 alerts, tool failure rate, negative feedback rate, staging eval
freshness, triage MTTA, alert delivery health, async monitor review worker
health, and automation execution failure rate. Each row includes target,
observed aggregate, status (`met`, `at_risk`, `breached`, or `no_data`), and
`error_budget_remaining` when that math is meaningful. No-data is explicit so
missing evidence cannot look healthy. The endpoint uses only aggregates and
does not return messages, tool arguments, payloads, retrieval bodies, memory
facts, feedback comments, automation action ids, or raw eval answers.

`GET /api/v1/admin/operations/automation-plan` is the read-only production
next-action planner used by the console `Settings` workbench and external
automation. It reuses the promotion gate window, then adds active alert
pressure, webhook/outbox health, dead-letter deliveries, incident-brief and
regression-draft opportunities, missing alert receipt evidence, tool-audit
failure rate, negative feedback, retrieval grounding, and staging-eval
freshness. The response schema is
`ops_automation.v1`. Each action includes priority, detail, `safe_to_auto_execute`,
required scopes, an optional method/path/query/body command, evidence, and
guardrails. The endpoint itself never dispatches webhooks, changes triage,
requeues deliveries, records release decisions, or runs evals; a cron job or
on-call bot must explicitly call a returned command with the listed scope.
When the console BFF executes an `auto-safe` action, it also records
`POST /api/v1/admin/operations/automation-executions`. The ledger stores the
authenticated actor, action kind, status, command method/path/query summary,
body key list, body hash, result summary, and bounded error detail; it does not
store raw command body, raw result payload, browser-supplied actor fields,
signatures, tokens, tool arguments, or customer text. Audit export hashes the
error detail instead of emitting it. Operators can list the same ledger through
`GET /api/v1/admin/operations/automation-executions`, filtering by action kind,
status, source, actor, time window, limit, and sort order; the console Settings
panel uses that API for its execution-history view. The companion
`GET /api/v1/admin/operations/automation-executions/summary` endpoint defaults
to the latest 24 hours and aggregates total/completed/failed/rejected counts,
failure rate, source counts, action-kind counts, and latest failure metadata
without raw command bodies, raw results, actors, action ids, or error details.
The SLO report, console execution-health strip, and Prometheus metrics all use
that bounded summary.

`POST /api/v1/admin/promotion/decisions` is the mutable release audit action.
It recalculates the promotion gate, stores the resulting gate snapshot with the
actor, target version, decision, and note as a `release.promotion.decision`
event, then returns the stored record. Approval is rejected when the gate is
`blocked` unless the request includes `override_blocked=true` and an
`override_reason`. This endpoint records the operator decision; it does not
deploy code, shift traffic, or call an external CD system.

`GET /api/v1/admin/audit/export` exports NDJSON records with
`schema_version=audit_export.v1`. It combines append-only event rows, durable
tool-audit rows, event-store operation ledger rows, and operations automation
execution ledger rows. It keeps tenant and record metadata, hashes
user/conversation/run/action correlation ids, and summarizes only safe machine
fields such as event type, status, rating, decision, operation name, action
kind, command fingerprint, tool name, latency, error code, failure type, and
policy code. It deliberately omits user text, feedback comments, tool
arguments, raw operation tokens, raw automation command body, raw automation
result payload, full filesystem paths, knowledge snippets, and eval answer
text.

For production SIEM or warehouse ingestion, run
`support-agent-audit-export-worker --interval-seconds 86400 --json` or start
the Compose `audit` profile with
`docker compose --profile audit up --build`. The worker reuses the same
sanitized export rows, writes `.ndjson` plus `.manifest.json` under
`APP_AUDIT_EXPORT_DIR`, and records `operation=audit_export_batch` in
`event_store_operations`. The manifest contains batch id, tenant id, file
names, path hashes, SHA-256, byte count, record count, record-type counts,
window/options, `previous_cursor`, `high_water_cursor`,
`cursor_advance_allowed`, and `partial`. It never stores full local paths,
actor ids, user text, tool arguments, raw automation command bodies/results,
or eval answers. By default, the next cycle reuses the latest compatible
completed non-partial batch cursor and exports only rows whose stable
`(created_at, record_type, id)` key is greater than that cursor. Use
`--no-incremental` for an intentional full or manual-window rerun.
`GET /api/v1/admin/audit/export-batches/summary`, the console
Overview/Settings surfaces, `/metrics`, and the bundled Prometheus rules read
the operation ledger to report fresh/stale/missing/failed and partial status.
If `partial=true` or `cursor_advance_allowed=false`, downstream consumers
should not advance watermarks from that manifest; rerun with a narrower time
window or higher `--limit`.

## Event Store Operations

Create an online SQLite backup before every manual retention run or release
that changes persistence code:

```bash
python scripts/event_store_ops.py \
  --database-url sqlite:///./data/production/support-agent-lab.db \
  backup \
  --output ./data/backups/support-agent-lab-$(date +%Y%m%d%H%M%S).db
```

The backup command uses SQLite's online backup API, then runs `pragma
quick_check` and verifies the required tables exist in the copied database.
The same backup operation is exposed as
`POST /api/v1/admin/event-store/backups`. The HTTP body accepts only a short
label plus `overwrite` / `verify` flags; the backend writes under
`APP_EVENT_STORE_BACKUP_DIR` so operators cannot choose arbitrary filesystem
paths through the console. The HTTP API always performs verification before
issuing a backup token, even if a caller asks to skip verification.
Production readiness also probes this directory: `/api/v1/ready` creates it if
needed, writes and reads a temporary probe file, and deletes the probe before
returning. A mis-mounted or read-only backup volume returns `not_ready`.

Run a restore drill before treating the backup as operationally usable:

```bash
python scripts/event_store_ops.py \
  --database-url sqlite:///./data/production/support-agent-lab.db \
  restore-drill \
  --backup ./data/backups/support-agent-lab-YYYYmmddHHMMSS.db \
  --tenant-id your_real_tenant
```

The CLI copies the backup to a temporary restore database unless
`--restore-output` is supplied. It then runs `quick_check`, required schema
verification, table counts, a tenant high-water mark query, and the event-store
`health_check` write probe with rollback. The HTTP equivalent is
`POST /api/v1/admin/event-store/restore-drills`; it accepts only the
server-issued `backup_token` from the backup response, never an arbitrary path,
and it never overwrites the live database.

Preview retention first:

```bash
python scripts/event_store_ops.py \
  --database-url sqlite:///./data/production/support-agent-lab.db \
  retention \
  --tenant-id your_real_tenant
```

Use the console or admin API for production retention apply after checking the
backup, restore-drill report, and retention dry-run JSON. A direct CLI apply in
production is treated as an emergency local bypass: it refuses to run unless the
operator adds `--unsafe-local-apply`, and the refusal is written to the same
operation ledger.

Emergency-only direct CLI apply:

```bash
python scripts/event_store_ops.py \
  --database-url sqlite:///./data/production/support-agent-lab.db \
  retention \
  --tenant-id your_real_tenant \
  --apply \
  --unsafe-local-apply
```

By default, retention deletes only expired request nonces, old tool idempotency
rows, old tool audit rows, and terminal `sent` / `closed` alert-delivery rows.
It does not delete pending, failed, in-progress, or dead alert deliveries, and
it skips the append-only event log unless `--include-events` is explicitly set.
The same operation is exposed as `POST /api/v1/admin/event-store/retention` for
trusted operators with `admin:write`, `audit:read`, and `events:read`.
The console `Settings` workbench calls backup, restore-drill, and retention
endpoints through its server-side BFF. The UI requires a passed restore drill
before enabling retention apply, and backend retention apply remains gated by a
verified backup, passed restore drill, dry-run preview, and explicit operator
confirmation:
`dry_run=false` must include the server-issued `backup_token`, matching
`restore_drill_token`, matching `preview_token`, and `apply_confirmed=true`.
The API validates that all tokens belong to the same tenant and actor, the
restore drill was run against the same backup token, the backup file still
exists under `APP_EVENT_STORE_BACKUP_DIR`, the retention parameters are
unchanged, and the event-store high-water mark still matches the preview. Any
mismatch returns `409 Conflict` without deleting rows.

Every authenticated event-store API operation and every direct
`event_store_ops.py` CLI operation writes a separate `event_store_operations`
ledger row. Completed backups, restore drills, retention previews, and
retention applies are recorded, as are guard rejections and execution failures.
The row stores actor id, operation, status, timestamp, and a safe summary:
backup file name plus path hash, restore-drill table counts and token hash when
the API flow is used, retention parameters and table-level candidate/deleted
counts, or a short error detail. It never stores raw backup/restore/preview
tokens or full filesystem paths. The CLI actor defaults to `event_store_cli`
and can be set with `--actor-user-id`. The table is included in backup and
restore-drill schema verification, but it is intentionally excluded from
`retention_high_water_mark`; otherwise the act of writing audit rows would
invalidate the backup/preview guard tokens. Operators can review it through
`GET /api/v1/admin/event-store/operations`, the Settings operation ledger, or
the audit NDJSON export.

The API and CLI also share a durable `event_store_operation_locks` lease table.
Backup, restore-drill, retention preview, and retention apply all acquire the
same tenant-scoped `event_store_maintenance` lock before reading high-water
marks, issuing operation tokens, or deleting rows. A concurrent maintenance
request returns `409 Conflict` from HTTP or a non-zero CLI exit, and the
operation ledger records the active operation, expiry time, and an owner hash
without exposing raw owner ids. The default lease is 1800 seconds and is
controlled by `APP_EVENT_STORE_OPERATION_LOCK_TTL_SECONDS`; expired locks are
removed atomically before a new operation is allowed to proceed. The lock table
is included in backup and restore-drill schema verification, but it is excluded
from retention high-water marks for the same reason as the operation ledger.

Monitor summary, events, and drilldown endpoints support `source=event_store`,
`created_after`, `created_before`, and `order=desc|asc` for durable production
investigation after a process restart. `GET /api/v1/admin/monitor/drilldown`
also accepts `alert_key`, `intent`, `risk_level`, `failure_type`,
`needs_human_review`, `grounded`, `policy_compliant`, `include_healthy`, and
`limit`; it returns the matching monitor events plus backend-derived failure,
intent, and risk buckets.

The frontend console uses the same evidence through `GET /api/console/snapshot`.
Its live snapshot guard prefers `source=event_store`, falls back to
`source=live` only when persisted summary reads are unavailable, and displays
the client-observed snapshot age in the top bar. When that snapshot becomes
stale or a refresh fails, the console keeps read-only search, filtering, and
export available but blocks alert triage and alert-delivery mutations until a
fresh snapshot is loaded. This guard is read-only: it does not write triage
events, dispatch outbox rows, or change promotion-gate evidence by itself.
In production, the Next.js middleware also protects `/` and `/api/console/*`
with Basic Auth using `FRONTEND_CONSOLE_USERNAME` and
`FRONTEND_CONSOLE_PASSWORD`. Missing credentials, placeholder values, or
passwords shorter than 16 characters fail closed with `401`, so the BFF cannot
silently expose its high-scope `FRONTEND_ACTOR_*` backend identity to any browser
that can reach port `3000`. The BFF signing layer also fails closed before
calling the Agent API unless `AGENT_API_BASE_URL`, `APP_TENANT_ID`,
`APP_INTERNAL_API_KEY`, `APP_ACTOR_SIGNATURE_SECRET`, `FRONTEND_ACTOR_USER_ID`,
`FRONTEND_ACTOR_ROLES`, and `FRONTEND_ACTOR_SCOPES` are all explicit
non-placeholder values. `APP_TENANT_ID=demo_tenant` is rejected in production
frontend auth, and `FRONTEND_ACTOR_SCOPES` has no default high-scope fallback.
Alert triage writes add a second server-side guard: `POST
/api/v1/admin/monitor/alerts/{alert_key}/triage` may include `expected_alert`
with the status, assignee, count, `last_seen_at`, `last_triage_event_id`, and
`new_events_since_triage` values seen by the operator. The API recomputes the
current alert from persisted monitor and triage events before appending a new
triage event; if any expected field differs, it returns `409 Conflict` and
does not write a stale operator action.

`GET /api/v1/admin/monitor/triage/metrics` is the compact production health
view for on-call handoff. It reads the same persisted `monitor.reviewed` and
`monitor.alert.triaged` event streams, then returns only aggregate fields:
active alerts, unresolved ownership, new events since triage, stale active
alerts, severity/status counts, health status, MTTA, and MTTR. It intentionally
does not return raw monitor events, sample run ids, event summaries, or triage
notes.

`support-agent-monitor-review-worker` is the durable async companion for
`OnlineMonitorAgent`. The request path still reviews normal chat responses
synchronously, but the worker can backfill missing `monitor.reviewed` events
from persisted `agent.run.completed` rows after process restarts, transient
request failures, or operator-triggered replay. Each cycle uses a tenant-scoped
operation lock, selects only completed runs that do not already have a
same-tenant `monitor.reviewed` event, reconstructs a minimal response from the
persisted trace, and appends the monitor event idempotently. Run it with
`support-agent-monitor-review-worker --interval-seconds 30 --json` or the
Compose `alerts` profile. `GET /api/v1/admin/monitor/review-worker/summary`
returns only heartbeat health, active/stale worker counts, latest cycle counts,
and last success/error type; it does not expose worker id, user text, tool
arguments, retrieved snippets, or run ids. The stale threshold is
`APP_MONITOR_REVIEW_WORKER_HEARTBEAT_STALE_SECONDS`, defaulting to 180 seconds.

`support-agent-audit-export-worker` is the durable batch companion for
sanitized audit exports. Run it with
`support-agent-audit-export-worker --interval-seconds 86400 --json` or the
Compose `audit` profile. Each cycle acquires the same
`event_store_maintenance` operation lock used by backup, restore-drill, and
retention, then writes an NDJSON file, a manifest, and an
`audit_export_batch` operation ledger row. Lock conflicts are rejected and
audited without creating partial files; failures write a safe error type and
path hash. Completed non-partial cycles advance a stable high-water cursor;
partial cycles do not, so the next incremental run falls back to the last
complete compatible cursor instead of silently skipping rows. `GET
/api/v1/admin/audit/export-batches/summary` returns recent batch health, last
file names, counts, size, checksum, high-water cursor, cursor advance flag,
and partial status, but not full paths, actor ids, worker ids, customer text,
or tool arguments. The stale threshold is
`APP_AUDIT_EXPORT_BATCH_STALE_SECONDS`, defaulting to 86400 seconds.

`POST /api/v1/admin/monitor/alert-deliveries/dispatch` is the explicit outbox
dispatcher for proactive alert notification. It projects active P0/P1 alerts
from persisted monitor events, inserts one durable outbox row per
`tenant_id + alert_key + alert_last_seen_at + destination`, then atomically
claims eligible due rows before posting to `APP_MONITOR_ALERT_WEBHOOK_URL`.
Claim leases prevent duplicate sends when two dispatchers run at the same time.
Before each webhook POST, the dispatcher atomically refreshes the current row's
lease and skips the send if another worker has already reclaimed it, so a slow
batch cannot turn an expired claim into a duplicate notification.
Failures set `next_attempt_at` with exponential backoff; rows that reach
`APP_MONITOR_ALERT_MAX_ATTEMPTS` move to `dead` and stop retrying until an
operator intervenes. Delivery payloads are signed with
`APP_MONITOR_ALERT_WEBHOOK_SECRET` and contain only alert key, severity, reason,
sample run/event ids, and timing metadata. They do not include raw customer
text, tool arguments, or eval answer text. Use
`GET /api/v1/admin/monitor/alert-deliveries/summary` for the console/operator
health strip and `GET /api/v1/admin/monitor/alert-deliveries` for the delivery
ledger, including `pending`, `in_progress`, `failed`, `sent`, `dead`, and
`closed` rows. The summary also includes dispatcher heartbeat fields such as
`dispatcher_status`, `dispatcher_last_seen_at`, active/stale worker counts, and
the configured stale threshold. When the signed receipt receiver is enabled,
the summary also includes receipt coverage fields: received receipt count,
duplicate receipt count, eligible sent-with-receipt count, eligible
sent-without-receipt count, recent sent rows still inside
`APP_MONITOR_ALERT_WEBHOOK_RECEIPT_GRACE_SECONDS`, and the oldest unconfirmed
sent timestamp. Operators can use `POST .../{delivery_id}/requeue` to move a
`dead` row back to `pending` with attempts reset, or `POST .../{delivery_id}/close`
to mark the dead-letter handled without pretending it was delivered. Both
actions append audit events with the operator actor id and note.

For end-to-end local or internal-environment drills, enable
`APP_MONITOR_ALERT_WEBHOOK_RECEIVER_ENABLED=true` and point
`APP_MONITOR_ALERT_WEBHOOK_URL` at `/api/v1/webhooks/monitor/alerts` on the same
service or an internal receiver deployment. This external webhook route is
exempt from actor/request signatures because webhook gateways do not send
`X-Actor-*` claims, but it has its own `X-PSA-*` HMAC envelope using
`APP_MONITOR_ALERT_WEBHOOK_SECRET`, timestamp freshness, and body hash checks.
Accepted deliveries are recorded idempotently in `alert_webhook_receipts`; the
ledger stores delivery id, alert key, severity, hashes, counts, duplicate count,
and timestamps, not raw webhook body, headers, reason text, or sample ids. Use
`GET /api/v1/admin/monitor/alert-webhook-receipts` to inspect receipt summaries.
The console `Receipts` tab reads the same endpoint through its signed BFF and
returns only delivery id, alert key, severity, body hash, counts, and timestamps
to the browser. `/metrics` exports the same coverage signal as low-cardinality
gauges, including `support_agent_alert_webhook_receiver_enabled`,
`support_agent_alert_delivery_sent_without_receipt`, and
`support_agent_alert_delivery_recent_sent_pending_receipt`; the bundled
Prometheus rule only pages on missing receipts when the local receiver is
explicitly enabled.

For unattended production delivery, run the same cycle with
`support-agent-alert-dispatcher --interval-seconds 30 --json` or start the
Compose `alerts` profile with `docker compose --profile alerts up --build`.
The worker reads the same SQLite event store and uses the same outbox claim
leases as the admin endpoint. In production mode it exits non-zero when the
event store or alert webhook is not configured, so a process manager can detect
misconfiguration instead of silently running a no-op worker. Each cycle writes a
durable `alert_dispatcher_heartbeats` row before and after dispatch; the stale
threshold is `APP_MONITOR_ALERT_DISPATCHER_HEARTBEAT_STALE_SECONDS` and defaults
to 180 seconds. The API summary and `/metrics` report whether the dispatcher is
`active`, `stale`, or `missing`, without using `worker_id` as a Prometheus label.
Its JSON log line is a sanitized count summary plus delivery ids; it does not
print alert keys, sample run ids, webhook payloads, customer text, or triage notes.

`POST /api/v1/admin/evals/regression-drafts` is production-allowed because it
is read-only. It loads the persisted run, selected monitor event, and message
events, then returns a strict `EvalCase` JSON draft plus the recommended target
file. It never appends events, runs evals, or writes `examples/evals/*.json`;
operators should copy the draft into a reviewed PR and run evals in CI or
staging.

Example monitor operator:

```text
X-Actor-Roles: admin
X-Actor-Scopes: monitor:read,monitor:write,events:read,audit:read,feedback:read
X-Actor-Timestamp: <unix timestamp>
X-Actor-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp>
```

Example incident investigator:

```text
X-Actor-Roles: admin
X-Actor-Scopes: events:read,monitor:read,audit:read,knowledge:diagnose,memory:replay,feedback:read
X-Actor-Timestamp: <unix timestamp>
X-Actor-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp>
```

Business admin scopes are separate from management API scopes. `crm:admin` can read another user's customer profile; `order:admin` can read/search another customer's orders. `roles=admin` alone does not grant either.

The bundled `/api/v1/admin/evals/golden` endpoint runs only `examples/evals/golden_core.json`. The bundled `/api/v1/admin/evals/staging` endpoint runs the same release-oriented regression suites used by the console: golden, security, tool failure, memory, routing, monitor, and retrieval. Production rejects both with `409` so demo users and fixture-shaped cases cannot accidentally hit real CRM/OMS systems. Run regression evals offline in CI, or against a staging sandbox whose users, orders, and knowledge base are intentionally seeded for eval.

## MCP

`MCPToolAdapter` defaults to gateway mode: calls must pass `tenant_id`, authenticated `user_id`, and explicit scopes. The bundled `support_agent_lab.mcp.server` is local-only and explicitly opts into demo defaults; production mode refuses to start it to avoid defaulting to `user_demo`. Production MCP gateways should also pass request/trace ids and explicit idempotency keys for write tools.

## SQLite knowledge index

`APP_KNOWLEDGE_BACKEND=sqlite` uses `SQLiteKnowledgeIndex` instead of an
external Knowledge API. It is a real durable index, not a fixture: documents are
read from files, chunked, written to `APP_KNOWLEDGE_DATABASE_URL`, and searched
through the same `RetrievalTrace` contract that the agent and console use.

```bash
python scripts/knowledge_index_ops.py --database-url sqlite:///./data/knowledge/support-agent-knowledge.db --tenant-id your_real_tenant --json ingest --source ./examples/knowledge --source-label policies --replace
python scripts/knowledge_index_ops.py --database-url sqlite:///./data/knowledge/support-agent-knowledge.db --tenant-id your_real_tenant --json search "shipping delay"
python scripts/knowledge_index_ops.py --database-url sqlite:///./data/knowledge/support-agent-knowledge.db --tenant-id your_real_tenant --json stats
```

For same-tenant documents that should only be visible to a subset of operators
or agents, ingest them with `--required-scope`:

```bash
python scripts/knowledge_index_ops.py --database-url sqlite:///./data/knowledge/support-agent-knowledge.db --tenant-id your_real_tenant --json ingest --source ./internal-playbooks --source-label lead-playbooks --required-scope support:lead --replace
python scripts/knowledge_index_ops.py --database-url sqlite:///./data/knowledge/support-agent-knowledge.db --tenant-id your_real_tenant --json search "goodwill refund" --actor-scope support:lead
```

`SQLiteKnowledgeIndex.search` filters chunks by
`RetrievalContext.actor_scopes` before lexical scoring and citation selection.
Missing context fails closed for scoped documents: public documents remain
searchable, but documents with `required_scopes` are hidden unless every
required scope is present. Re-ingesting unchanged content with changed
`required_scopes` rewrites the document/chunk ACL instead of being skipped as a
same-hash duplicate.

Production readiness calls the adapter `health_check`. For SQLite, readiness
requires at least `APP_KNOWLEDGE_MIN_READY_DOCUMENTS` indexed documents. The
admin `GET /api/v1/admin/knowledge/summary` endpoint and the console Knowledge
workbench expose only provider, status, counts, timestamps, database file name,
path hash, and restricted document/chunk counts. They do not expose source
paths, raw document content, chunk metadata, required scope names, API keys, or
tenant/actor headers.

Use the SQLite backend for single-instance production, staging, or teams that
want a deployable baseline before operating a separate retrieval platform. For
higher traffic, large corpora, or policy models that need row-level joins beyond
scope subset checks, move the same contract behind the HTTP Knowledge API and
keep the console diagnostics unchanged.

## Knowledge API contract

`HTTPKnowledgeIndex` expects `GET /health` to return 2xx for readiness and `GET /knowledge/search` to return either:

```json
{
  "hits": [
    {
      "document_id": "shipping_policy_v2",
      "chunk_id": "shipping_policy_v2:0",
      "title": "Shipping policy",
      "content": "Grounded support policy text...",
      "score": 0.92,
      "source_uri": "kb://policies/shipping_policy_v2",
      "metadata": {"version": "v2"}
    }
  ]
}
```

or a bare list with the same hit shape.

Every `/knowledge/search` request can include downstream retrieval context:

```text
Authorization: Bearer <APP_KNOWLEDGE_API_KEY>
X-Tenant-Id: <tenant>
X-Actor-User-Id: <actor user id>
X-Actor-Roles: <comma separated roles>
X-Actor-Scopes: <comma separated scopes>
X-Request-Id: <request id for this retrieval call>
X-Trace-Id: <agent run id or kbdiag_* diagnostic trace id>
```

Your knowledge service should treat these as service-to-service context headers,
not as a replacement for its own edge authentication. Use them for tenant
filtering, document ACLs, retrieval audit logs, and correlation with Agent run
traces. Chat traffic uses the Agent run id as `X-Trace-Id`; admin diagnostic
search uses a `kbdiag_*` trace id and the operator's actor claims.

For operator diagnostics, the Agent API exposes
`POST /api/v1/admin/knowledge/search`. That endpoint calls the configured
knowledge adapter but returns a deliberately smaller DTO: query rewrites,
selected sources, candidate stage counts, dropped candidate ids, and selected
snippets. It does not return raw hit `content` or `metadata`, so console users
can debug retrieval without exposing complete policy documents or accidental
upstream payload fields.

If your knowledge service already has retrieval telemetry, include these
optional top-level fields in the `/knowledge/search` response:

```json
{
  "rewritten_queries": ["shipping delay policy", "late delivery support"],
  "candidates_by_stage": {"bm25": 20, "vector": 12, "reranked": 5, "selected": 3},
  "dropped_candidates": ["shipping_policy_v1:0", "faq_late_delivery:2"]
}
```

When those fields are absent or malformed, the adapter falls back to safe counts
derived from the returned hits, so the agent can still answer and the console
can still show a trace.

The knowledge adapter also has a small resilience layer. `/health` and
`/knowledge/search` are attempted up to `APP_KNOWLEDGE_API_RETRY_ATTEMPTS`
times with exponential backoff starting at `APP_KNOWLEDGE_API_RETRY_BACKOFF_MS`.
Retryable failures are `429`, `5xx`, timeout, network failure, and transient
invalid JSON. Repeated retryable failures open an in-process circuit after
`APP_KNOWLEDGE_API_CIRCUIT_FAILURE_THRESHOLD` failures and keep it open for
`APP_KNOWLEDGE_API_CIRCUIT_RESET_SECONDS`; while open, retrieval fails fast with
an observable `knowledge_circuit_open` retrieval trace instead of blocking chat
traffic behind a broken upstream.

## LLM provider

Production uses `OpenAIResponsesProvider`, which calls the OpenAI Responses API through the official Python SDK. The provider receives the tool-grounded draft, trace context, citations, intent, and route, then produces the final support answer.

If the model provider times out or raises an upstream error, `LLMGateway` retries
bounded transient failures according to `APP_LLM_RETRY_ATTEMPTS` and
`APP_LLM_RETRY_BACKOFF_MS`. If the provider still fails, it returns the already
constructed grounded draft and records `fallback_used=true` plus the provider
error type in `trace.llm_calls`. The user still receives a conservative answer
based on retrieved policy and tool results instead of a 500 caused by the model
layer. Repeated retryable model failures open an in-process circuit after
`APP_LLM_CIRCUIT_FAILURE_THRESHOLD` failures and keep it open for
`APP_LLM_CIRCUIT_RESET_SECONDS`; while open, generation fails fast to the
grounded draft. Production deployments should still alert on fallback rate and
add a provider-routing or fallback-model policy before higher traffic.

Local deterministic output is allowed only when `APP_ENV` is not production. This keeps tests stable without allowing production traffic to silently use local fixtures.

## Liveness and readiness

The service exposes two health endpoints:

| Endpoint | Purpose | Dependency checks |
| --- | --- | --- |
| `/api/v1/health` | Liveness: the FastAPI process is running. | None. |
| `/api/v1/ready` | Readiness: the service can take traffic. | Config, event store, production backup-directory write probe, and, when deep checks are enabled, OpenAI model access, business API `/health`, and knowledge API `/health`. |
| `/metrics` | Prometheus-style scrape endpoint for production dashboards and alerts. | No active dependency probes; it reads local aggregate state and event-store summaries. |

In production, deep readiness checks are enabled by default when `APP_READINESS_DEEP_CHECKS` is unset. `.env.example` sets it explicitly to `true`. Docker `HEALTHCHECK` targets `/api/v1/ready`, not `/api/v1/health`, so a container is not marked healthy while core dependencies are unavailable. The `event_store_backup_dir` check is not a deep external call; it runs for every production readiness request because backup creation is required before guarded retention apply.

The `business_api` and `knowledge_api` readiness details include adapter circuit
state, failure count, threshold, and retry attempts. Use that detail during
incidents: `circuit=closed` means calls are flowing, `circuit=open` means the
adapter is failing fast until the reset window, and `circuit=half_open` means
the next upstream call is probing recovery.

The `llm` readiness detail follows the same pattern and also reports the
configured timeout. A failed model readiness check includes circuit state so
operators can distinguish a provider outage from a locally open breaker.

You can force or skip deep checks per request:

```bash
curl "http://127.0.0.1:8000/api/v1/ready?deep=true"
curl "http://127.0.0.1:8000/api/v1/ready?deep=false"
```

`GET /metrics` returns Prometheus text format and is exempt from request
signatures and per-actor rate limits so normal scrapers can call it without
minting nonces. Protect it with internal networking, mTLS, or gateway ACLs. The
endpoint intentionally exports only aggregate, low-cardinality signals such as
HTTP request counts by method/route family/status, rate-limit decision counts,
monitor event counts, monitor triage health by status/severity, active/stale
alert counts, MTTA/MTTR, alert delivery outbox counts by status/severity,
alert delivery health, alert dispatcher heartbeat health, async monitor review
worker heartbeat health and latest cycle counts, audit export batch health,
last record/byte/partial counts, feedback review backlog counts by current
status, stale/unassigned unresolved feedback counts, automation execution
totals/failure rate/source/status counts, grounded/policy/human-review rates,
tool audit totals and latency summaries, adapter circuit state, LLM fallback
counts, effective rate-limit backend, and rate-limit configuration. It does
not include user ids, assignees, trace ids, alert keys, triage notes, feedback
comments, review notes, automation action ids, raw automation command
bodies/results, raw tool arguments, request bodies, retrieved snippets, full
filesystem paths, checksum labels, or monitor summaries.

The minimal Prometheus example lives in `deploy/prometheus/prometheus.yml`, the
production alert rules live in `deploy/prometheus/support-agent-alerts.yml`, and
the matching operator runbook is `docs/alerting-runbook.md`. Prometheus should
scrape the backend API and load the rule file:

```yaml
rule_files:
  - /etc/prometheus/rules/support-agent-alerts.yml

scrape_configs:
  - job_name: support-agent-api
    metrics_path: /metrics
    static_configs:
      - targets: ["app:8000"]
```

For a local or single-node deployment, `docker compose --profile observability up --build` starts Prometheus, mounts both files read-only, keeps its TSDB in the
`prometheus-data` volume, and binds the UI to `127.0.0.1:9090`. The compose
service keeps Prometheus lifecycle endpoints disabled. In Kubernetes or a
managed Prometheus setup, replace `app:8000` with the service DNS name for the
backend API and keep the Prometheus UI behind internal access controls.

Every rule links to a runbook section and uses only low-cardinality labels such
as status, severity, adapter, route family, method, and decision. Do not add
alert keys, run ids, user ids, assignees, notes, or trace ids as Prometheus
labels; keep those details inside the authenticated console and incident APIs.
The bundled rules include sanitized audit export batch stale, failed, and
partial coverage through `support_agent_audit_export_batch_*` metrics.

## Startup checks

`Settings.validate_production_ready()` requires:

- `APP_ENV=production` when `APP_REQUIRE_PRODUCTION=true`
- real `APP_TENANT_ID`, not `demo_tenant`
- `APP_MODEL_PROVIDER=openai`
- `OPENAI_API_KEY`
- `APP_BUSINESS_API_BASE_URL`
- `APP_BUSINESS_API_KEY`
- `APP_KNOWLEDGE_BACKEND=http|sqlite` for explicit production deployments. The
  default `auto` resolves to HTTP in production.
- For HTTP knowledge: `APP_KNOWLEDGE_API_BASE_URL` and
  `APP_KNOWLEDGE_API_KEY`.
- For SQLite knowledge: `APP_KNOWLEDGE_DATABASE_URL=sqlite:///...` and an
  ingested index that satisfies `APP_KNOWLEDGE_MIN_READY_DOCUMENTS`.
- `APP_INTERNAL_API_KEY`
- `APP_ACTOR_SIGNATURE_SECRET` with at least 32 characters
- `APP_REQUEST_SIGNATURE_REQUIRED=true`; it is implied when `APP_REQUIRE_PRODUCTION=true` and the field is unset, and startup fails if it is explicitly set to `false`
- `APP_RATE_LIMIT_ENABLED=true`; it is implied in production when unset, and startup fails if it is explicitly set to `false` while `APP_REQUIRE_PRODUCTION=true`
- `APP_RATE_LIMIT_BACKEND=auto`; production resolves this to SQLite-backed rate-limit buckets until you move the state to Redis/Postgres/API gateway
- `APP_DATABASE_URL=sqlite:///...` until another event-store adapter is implemented

Business and Knowledge API resilience knobs are optional but should be set deliberately for each environment:

- `APP_BUSINESS_API_RETRY_ATTEMPTS`
- `APP_BUSINESS_API_RETRY_BACKOFF_MS`
- `APP_BUSINESS_API_CIRCUIT_FAILURE_THRESHOLD`
- `APP_BUSINESS_API_CIRCUIT_RESET_SECONDS`
- `APP_KNOWLEDGE_API_RETRY_ATTEMPTS`
- `APP_KNOWLEDGE_API_RETRY_BACKOFF_MS`
- `APP_KNOWLEDGE_API_CIRCUIT_FAILURE_THRESHOLD`
- `APP_KNOWLEDGE_API_CIRCUIT_RESET_SECONDS`
- `APP_LLM_RETRY_ATTEMPTS`
- `APP_LLM_RETRY_BACKOFF_MS`
- `APP_LLM_CIRCUIT_FAILURE_THRESHOLD`
- `APP_LLM_CIRCUIT_RESET_SECONDS`

If proactive monitor alert delivery is enabled, startup also validates:

- `APP_MONITOR_ALERT_WEBHOOK_ENABLED=true`
- `APP_MONITOR_ALERT_WEBHOOK_URL` pointing at the on-call/webhook gateway
- `APP_MONITOR_ALERT_WEBHOOK_SECRET` with at least 32 characters
- `APP_MONITOR_ALERT_MAX_ATTEMPTS`, `APP_MONITOR_ALERT_BACKOFF_BASE_SECONDS`,
  `APP_MONITOR_ALERT_BACKOFF_MAX_SECONDS`, and `APP_MONITOR_ALERT_CLAIM_LEASE_SECONDS`
  sized for your on-call webhook's reliability and timeout behavior
- If the signed receiver is enabled, set
  `APP_MONITOR_ALERT_WEBHOOK_RECEIVER_ENABLED=true`,
  `APP_MONITOR_ALERT_WEBHOOK_RECEIVER_MAX_AGE_SECONDS`, and
  `APP_MONITOR_ALERT_WEBHOOK_RECEIPT_GRACE_SECONDS` to match your expected
  gateway latency and clock skew.

If any are missing, unsupported, or still look like placeholders such as `replace_with...`, `your_...`, or `example.com`, startup raises a `RuntimeError`. This is intentional.

## Production smoke test

Do not prove production mode by checking only that the container starts. Verify:

```bash
python scripts/run_release_check.py --production-config
python scripts/run_release_check.py --include-docker
python scripts/run_release_check.py \
  --production-config \
  --prod-smoke \
  --base-url https://your-staging-agent.example.com \
  --smoke-user-id user_prod \
  --smoke-admin-id admin_prod \
  --smoke-message "Where is my most recent order?"
```

The default release check is deterministic and local. `--prod-smoke` is intentionally explicit because it calls a deployed service and can reach your real OpenAI, business, and knowledge integrations through `/api/v1/ready?deep=true` and `/api/v1/chat/messages`.

- GitHub Actions passes for unit tests, golden/security/tool/memory/routing evals, monitor eval, retrieval challenge, production request signer smoke test, and Docker image build.
- `.env` uses `APP_ENV=production` and `APP_REQUIRE_PRODUCTION=true`.
- Business URLs are real internal services, not local fixtures or placeholder domains. Knowledge is either a real HTTP service or an ingested SQLite index.
- Removing `OPENAI_API_KEY`, `APP_BUSINESS_API_BASE_URL`, or the configured knowledge backend requirement makes startup/readiness fail.
- `GET /api/v1/ready?deep=true` reaches OpenAI, Business API `/health`, the configured knowledge backend health check, and the SQLite event store.
- During a controlled staging failure, repeated Business API `5xx` responses open the adapter circuit and `/api/v1/ready?deep=true` reports `business_api` as failed with `circuit=open`.
- During a controlled staging failure, repeated HTTP Knowledge API `5xx` responses open the adapter circuit and retrieval traces show `knowledge_circuit_open`; `/api/v1/ready?deep=true` reports `knowledge_api` as failed with `circuit=open`. For SQLite knowledge, test an empty index against `APP_KNOWLEDGE_MIN_READY_DOCUMENTS` and verify readiness fails before chat traffic uses it.
- Removing `APP_ACTOR_SIGNATURE_SECRET`, using a placeholder value, or setting a short secret makes startup fail.
- `python scripts/sign_actor_headers.py --user-id user_prod --roles user --scopes "crm:read,order:read,shipping:read,ticket:write,kb:read,feedback:write" --method POST --path /api/v1/chat/sessions --body '{"user_id":"user_prod"}' --format curl` emits signed actor and request headers when the gateway secrets are present in the environment.
- Changing `X-Actor-User-Id`, `X-Actor-Roles`, or `X-Actor-Scopes` after signing makes the request fail with `401`.
- Changing the path, body, body hash, or reusing the same `X-Request-Nonce` makes the request fail with `401`.
- Calling `/api/v1/admin/evals/golden` or `/api/v1/admin/evals/staging` in production fails with `409`; bundled eval remains a CI/staging concern, not a live production tool call.
- A production `/api/v1/chat/messages` request creates matching `X-Trace-Id` / `X-Request-Id` entries in your business backend logs.
- The returned `trace_id` can query `/api/v1/admin/tools/audit?trace_id=...` with `audit:read`, and the records contain hashes/status/latency but no raw arguments, PII, tokens, or full upstream payloads.
- The same `trace_id` can query `/api/v1/admin/incidents/runs/{trace_id}` and return the persisted run, monitor events, tool audit records, and optional memory replay after live process state is cleared.
- `X-Demo-User` / `X-Demo-Role` do not authenticate production requests.
- Repeating a write tool call with the same idempotency key after process restart replays the first result instead of creating a second ticket.
- Replayed write-tool audit records show `replayed=true` while preserving the same `idempotency_key_hash`.
- Two concurrent write tool calls with the same idempotency key do not both reach the business side effect; one call should reserve the operation and the other should receive a retryable `CONFLICT` or replay the completed result.

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
APP_KNOWLEDGE_API_BASE_URL=https://knowledge.example.com
APP_KNOWLEDGE_API_KEY=...
APP_INTERNAL_API_KEY=...
APP_ACTOR_SIGNATURE_SECRET=replace_with_real_actor_signature_secret_min_32_chars
APP_ACTOR_SIGNATURE_MAX_AGE_SECONDS=300
APP_REQUEST_SIGNATURE_REQUIRED=true
APP_RATE_LIMIT_ENABLED=true
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
```

`APP_DATABASE_URL` currently supports SQLite. It stores the append-only event log, monitor triage events, tool idempotency records, tool audit records, and alert delivery outbox. That is enough for a single-instance deployment or staging environment. For multi-instance production, replace `SQLiteEventStore` with a Postgres/Kafka-backed implementation before scaling horizontally.

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
Idempotency-Key: <for write tools>
```

`/health` is a readiness probe, not an actor-scoped tool call. It sends service authentication plus tenant/request/trace headers so infrastructure can verify dependency reachability without pretending to be an end user.

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
X-Actor-Scopes: crm:read,order:read,shipping:read,ticket:write,kb:read
X-Actor-Timestamp: <unix timestamp>
X-Actor-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp>
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

Production enables an in-process token-bucket limiter by default unless `APP_RATE_LIMIT_ENABLED=false` is explicitly set. When `APP_REQUIRE_PRODUCTION=true`, explicitly disabling it fails startup. The limiter keys by tenant, actor user id, and endpoint family (`chat`, `admin`, `admin-evals`, or `api`). Health and readiness endpoints are exempt.

Tune:

```text
APP_RATE_LIMIT_ENABLED=true
APP_RATE_LIMIT_REQUESTS_PER_MINUTE=600
APP_RATE_LIMIT_BURST=600
```

Limited requests return `429` with `Retry-After`, `X-RateLimit-Limit`, and `X-RateLimit-Remaining`. Successful limited requests also include `X-RateLimit-Limit` and `X-RateLimit-Remaining` so a gateway or frontend can surface pressure before hard failure.

This built-in limiter is intentionally single-instance, matching the current SQLite production baseline. For multi-replica production, move the token bucket state to Redis or your API gateway and keep the same key dimensions: tenant id, actor user id, and endpoint family.

Use the bundled signer to generate headers for production smoke tests:

```bash
export APP_TENANT_ID=your_real_tenant
export APP_INTERNAL_API_KEY=your_internal_gateway_secret
export APP_ACTOR_SIGNATURE_SECRET=your_actor_signature_secret_min_32_chars
python scripts/sign_actor_headers.py \
  --user-id user_prod \
  --roles user \
  --scopes "crm:read,order:read,shipping:read,ticket:write,kb:read" \
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
| `GET /api/v1/admin/runs` | `events:read` |
| `GET /api/v1/admin/incidents/runs/{run_id}` | `events:read`, `monitor:read`, `audit:read`; add `memory:replay` when `include_memory=true` |
| `/api/v1/admin/monitor/summary` | `monitor:read` |
| `/api/v1/admin/monitor/events` | `monitor:read` |
| `GET /api/v1/admin/monitor/drilldown` | `monitor:read` |
| `GET /api/v1/admin/monitor/triage/metrics` | `monitor:read` |
| `GET /api/v1/admin/monitor/alert-deliveries/summary` | `monitor:read` |
| `GET /api/v1/admin/monitor/alert-deliveries` | `monitor:read` |
| `POST /api/v1/admin/monitor/alert-deliveries/dispatch` | `monitor:write` |
| `POST /api/v1/admin/monitor/alert-deliveries/{delivery_id}/requeue` | `monitor:write` |
| `POST /api/v1/admin/monitor/alert-deliveries/{delivery_id}/close` | `monitor:write` |
| `GET /api/v1/admin/monitor/alerts/{alert_key}/triage` | `monitor:read` |
| `POST /api/v1/admin/monitor/alerts/{alert_key}/triage` | `monitor:write` |
| `/api/v1/admin/events` | `events:read` |
| `POST /api/v1/admin/evals/regression-drafts` | `events:read`, `monitor:read` |
| `POST /api/v1/admin/evals/golden` | `eval:run`; local/staging only. Disabled when `APP_ENV=production`. |
| `POST /api/v1/admin/evals/staging` | `eval:run`; local/staging only. Runs bundled golden/security/tool/memory/routing/monitor/retrieval suites and appends suite + aggregate gate records. Disabled when `APP_ENV=production`. |
| `GET /api/v1/admin/evals/gates` | `eval:read` |
| `GET /api/v1/admin/promotion/gate` | `admin:read`, `monitor:read`, `audit:read`, `eval:read`. Read-only release preflight. |
| `/api/v1/admin/conversations/{conversation_id}/memory/replay` | `memory:replay` |

`GET /api/v1/agent/runs/{run_id}` lets the original actor inspect their own run trace. Cross-user incident review must use an admin actor with `events:read`, and the endpoint falls back to the SQLite event store when live in-process run state has been cleared.

Eval gate history is stored as append-only `eval.gate.completed` events and
returned through the typed `GET /api/v1/admin/evals/gates` endpoint. Records
include actor, trigger, suite, run/alert context, status, duration, failed case
ids, and compact case observations, but not full eval answer text.

`GET /api/v1/admin/promotion/gate` is the read-only release preflight used by
the console and automation. It combines readiness, monitor triage metrics,
tool audit summary, and the latest `gate_name=staging`, `runner=aggregate`
eval gate. It returns `passed`, `warn`, or `blocked` plus threshold evidence for
each check. It never runs bundled evals, writes triage events, or returns raw
tool arguments, raw monitor events, or eval answer text.

Monitor summary, events, and drilldown endpoints support `source=event_store`,
`created_after`, `created_before`, and `order=desc|asc` for durable production
investigation after a process restart. `GET /api/v1/admin/monitor/drilldown`
also accepts `alert_key`, `intent`, `risk_level`, `failure_type`,
`needs_human_review`, `grounded`, `policy_compliant`, `include_healthy`, and
`limit`; it returns the matching monitor events plus backend-derived failure,
intent, and risk buckets.

`GET /api/v1/admin/monitor/triage/metrics` is the compact production health
view for on-call handoff. It reads the same persisted `monitor.reviewed` and
`monitor.alert.triaged` event streams, then returns only aggregate fields:
active alerts, unresolved ownership, new events since triage, stale active
alerts, severity/status counts, health status, MTTA, and MTTR. It intentionally
does not return raw monitor events, sample run ids, event summaries, or triage
notes.

`POST /api/v1/admin/monitor/alert-deliveries/dispatch` is the explicit outbox
dispatcher for proactive alert notification. It projects active P0/P1 alerts
from persisted monitor events, inserts one durable outbox row per
`tenant_id + alert_key + alert_last_seen_at + destination`, then atomically
claims eligible due rows before posting to `APP_MONITOR_ALERT_WEBHOOK_URL`.
Claim leases prevent duplicate sends when two dispatchers run at the same time.
Failures set `next_attempt_at` with exponential backoff; rows that reach
`APP_MONITOR_ALERT_MAX_ATTEMPTS` move to `dead` and stop retrying until an
operator intervenes. Delivery payloads are signed with
`APP_MONITOR_ALERT_WEBHOOK_SECRET` and contain only alert key, severity, reason,
sample run/event ids, and timing metadata. They do not include raw customer
text, tool arguments, or eval answer text. Use
`GET /api/v1/admin/monitor/alert-deliveries/summary` for the console/operator
health strip and `GET /api/v1/admin/monitor/alert-deliveries` for the delivery
ledger, including `pending`, `in_progress`, `failed`, `sent`, `dead`, and
`closed` rows. Operators can use `POST .../{delivery_id}/requeue` to move a
`dead` row back to `pending` with attempts reset, or `POST .../{delivery_id}/close`
to mark the dead-letter handled without pretending it was delivered. Both
actions append audit events with the operator actor id and note.

`POST /api/v1/admin/evals/regression-drafts` is production-allowed because it
is read-only. It loads the persisted run, selected monitor event, and message
events, then returns a strict `EvalCase` JSON draft plus the recommended target
file. It never appends events, runs evals, or writes `examples/evals/*.json`;
operators should copy the draft into a reviewed PR and run evals in CI or
staging.

Example monitor operator:

```text
X-Actor-Roles: admin
X-Actor-Scopes: monitor:read,monitor:write,events:read,audit:read
X-Actor-Timestamp: <unix timestamp>
X-Actor-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp>
```

Example incident investigator:

```text
X-Actor-Roles: admin
X-Actor-Scopes: events:read,monitor:read,audit:read,knowledge:diagnose,memory:replay
X-Actor-Timestamp: <unix timestamp>
X-Actor-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp>
```

Business admin scopes are separate from management API scopes. `crm:admin` can read another user's customer profile; `order:admin` can read/search another customer's orders. `roles=admin` alone does not grant either.

The bundled `/api/v1/admin/evals/golden` endpoint runs only `examples/evals/golden_core.json`. The bundled `/api/v1/admin/evals/staging` endpoint runs the same release-oriented regression suites used by the console: golden, security, tool failure, memory, routing, monitor, and retrieval. Production rejects both with `409` so demo users and fixture-shaped cases cannot accidentally hit real CRM/OMS systems. Run regression evals offline in CI, or against a staging sandbox whose users, orders, and knowledge base are intentionally seeded for eval.

## MCP

`MCPToolAdapter` defaults to gateway mode: calls must pass `tenant_id`, authenticated `user_id`, and explicit scopes. The bundled `support_agent_lab.mcp.server` is local-only and explicitly opts into demo defaults; production mode refuses to start it to avoid defaulting to `user_demo`. Production MCP gateways should also pass request/trace ids and explicit idempotency keys for write tools.

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
| `/api/v1/ready` | Readiness: the service can take traffic. | Config, event store, and, when deep checks are enabled, OpenAI model access, business API `/health`, and knowledge API `/health`. |
| `/metrics` | Prometheus-style scrape endpoint for production dashboards and alerts. | No active dependency probes; it reads local aggregate state and event-store summaries. |

In production, deep readiness checks are enabled by default when `APP_READINESS_DEEP_CHECKS` is unset. `.env.example` sets it explicitly to `true`. Docker `HEALTHCHECK` targets `/api/v1/ready`, not `/api/v1/health`, so a container is not marked healthy while core dependencies are unavailable.

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
alert delivery health, grounded/policy/human-review rates, tool audit totals
and latency summaries, adapter circuit state, LLM fallback counts, and
rate-limit configuration. It does not include user ids, assignees, trace ids,
alert keys, triage notes, raw tool arguments, request bodies, retrieved
snippets, or monitor summaries.

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

## Startup checks

`Settings.validate_production_ready()` requires:

- `APP_ENV=production` when `APP_REQUIRE_PRODUCTION=true`
- real `APP_TENANT_ID`, not `demo_tenant`
- `APP_MODEL_PROVIDER=openai`
- `OPENAI_API_KEY`
- `APP_BUSINESS_API_BASE_URL`
- `APP_BUSINESS_API_KEY`
- `APP_KNOWLEDGE_API_BASE_URL`
- `APP_KNOWLEDGE_API_KEY`
- `APP_INTERNAL_API_KEY`
- `APP_ACTOR_SIGNATURE_SECRET` with at least 32 characters
- `APP_REQUEST_SIGNATURE_REQUIRED=true`; it is implied when `APP_REQUIRE_PRODUCTION=true` and the field is unset, and startup fails if it is explicitly set to `false`
- `APP_RATE_LIMIT_ENABLED=true`; it is implied in production when unset, and startup fails if it is explicitly set to `false` while `APP_REQUIRE_PRODUCTION=true`
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
- Business and knowledge URLs are real internal services, not local fixtures or placeholder domains.
- Removing `OPENAI_API_KEY`, `APP_BUSINESS_API_BASE_URL`, or `APP_KNOWLEDGE_API_BASE_URL` makes startup fail.
- `GET /api/v1/ready?deep=true` reaches OpenAI, Business API `/health`, Knowledge API `/health`, and the SQLite event store.
- During a controlled staging failure, repeated Business API `5xx` responses open the adapter circuit and `/api/v1/ready?deep=true` reports `business_api` as failed with `circuit=open`.
- During a controlled staging failure, repeated Knowledge API `5xx` responses open the adapter circuit and retrieval traces show `knowledge_circuit_open`; `/api/v1/ready?deep=true` reports `knowledge_api` as failed with `circuit=open`.
- Removing `APP_ACTOR_SIGNATURE_SECRET`, using a placeholder value, or setting a short secret makes startup fail.
- `python scripts/sign_actor_headers.py --user-id user_prod --roles user --scopes "crm:read,order:read,shipping:read,ticket:write,kb:read" --method POST --path /api/v1/chat/sessions --body '{"user_id":"user_prod"}' --format curl` emits signed actor and request headers when the gateway secrets are present in the environment.
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

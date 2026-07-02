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
APP_HTTP_TIMEOUT_MS=5000
APP_LLM_TIMEOUT_MS=15000
APP_READINESS_DEEP_CHECKS=true
APP_DATABASE_URL=sqlite:///./data/production/support-agent-lab.db
```

`APP_DATABASE_URL` currently supports SQLite. It stores the append-only event log, monitor triage events, tool idempotency records, and tool audit records. That is enough for a single-instance deployment or staging environment. For multi-instance production, replace `SQLiteEventStore` with a Postgres/Kafka-backed implementation before scaling horizontally.

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

Your backend should still enforce tenant isolation and resource ownership. The Agent has route-level `allowed_tools`, `ToolBroker` enforces scopes, and the HTTP adapter performs basic defense-in-depth checks, but production authorization must live in the business service too. Path identifiers such as `user_id`, `order_id`, and `logistics_id` are schema-validated and encoded before being placed in upstream URLs; keep the same rule when adding tools.

## API authentication

Production API requests must come through a trusted gateway. The gateway authenticates the end user, then forwards:

```text
X-Internal-Auth: <APP_INTERNAL_API_KEY>
X-Actor-User-Id: <authenticated user id>
X-Actor-Roles: user,admin
X-Actor-Scopes: crm:read,order:read,shipping:read,ticket:write,kb:read
X-Actor-Timestamp: <unix timestamp>
X-Actor-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp>
```

`X-Demo-User` and `X-Demo-Role` are local-only teaching headers. In production they do not authenticate requests, and local fixture identities such as `user_demo` and `user_guest` are rejected. `X-Actor-Scopes` is required and should be the gateway's minimum capability set for this actor; missing or empty scopes fail closed.

`X-Actor-Signature` is an HMAC-SHA256 signature produced by the gateway with `APP_ACTOR_SIGNATURE_SECRET`. The canonical signed fields are: signature version `v1`, tenant id, user id, canonical comma-separated roles, canonical comma-separated scopes, and Unix timestamp. Roles and scopes are trimmed and empty entries are removed, but their order is preserved and not sorted. `X-Actor-Signature` may be the bare hex digest or `sha256=<digest>`. The service rejects missing signatures, invalid signatures, and timestamps outside `APP_ACTOR_SIGNATURE_MAX_AGE_SECONDS` so that a downstream proxy or client cannot add `admin`, broaden scopes, or swap user ids after the gateway has authenticated the request.

This is a deployable baseline for a trusted internal gateway. High-risk deployments should add gateway-side nonce or `jti` replay tracking for signed admin requests.

This HMAC protects the Agent API ingress boundary. It is not a replacement for tenant/resource authorization inside the business service. `ToolBroker` enforces business tool scopes before every tool call, and your business API must still enforce tenant/resource ownership.

Use the bundled signer to generate headers for production smoke tests:

```bash
export APP_TENANT_ID=your_real_tenant
export APP_INTERNAL_API_KEY=your_internal_gateway_secret
export APP_ACTOR_SIGNATURE_SECRET=your_actor_signature_secret_min_32_chars
python scripts/sign_actor_headers.py \
  --user-id user_prod \
  --roles user \
  --scopes "crm:read,order:read,shipping:read,ticket:write,kb:read" \
  --format curl
```

The console-script form is `support-agent-sign-headers`. Both paths call `support_agent_lab.security.actor_signature`, which is also used by the FastAPI verifier.

Admin role is not a wildcard. Production admin endpoints also require explicit management scopes:

| Endpoint family | Required scope |
| --- | --- |
| `/api/v1/admin/tools` | `admin:read` |
| `GET /api/v1/admin/tools/audit` | `audit:read` |
| `GET /api/v1/admin/incidents/runs/{run_id}` | `events:read`, `monitor:read`, `audit:read`; add `memory:replay` when `include_memory=true` |
| `/api/v1/admin/monitor/summary` | `monitor:read` |
| `/api/v1/admin/monitor/events` | `monitor:read` |
| `GET /api/v1/admin/monitor/alerts/{alert_key}/triage` | `monitor:read` |
| `POST /api/v1/admin/monitor/alerts/{alert_key}/triage` | `monitor:write` |
| `/api/v1/admin/events` | `events:read` |
| `/api/v1/admin/evals/golden` | `eval:run` |
| `/api/v1/admin/conversations/{conversation_id}/memory/replay` | `memory:replay` |

Example monitor operator:

```text
X-Actor-Roles: admin
X-Actor-Scopes: monitor:read,monitor:write,events:read,audit:read
X-Actor-Timestamp: <unix timestamp>
X-Actor-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp>
```

Example release engineer:

```text
X-Actor-Roles: admin
X-Actor-Scopes: eval:run,events:read
X-Actor-Timestamp: <unix timestamp>
X-Actor-Signature: sha256=<HMAC over tenant/user/roles/scopes/timestamp>
```

Business admin scopes are separate from management API scopes. `crm:admin` can read another user's customer profile; `order:admin` can read/search another customer's orders. `roles=admin` alone does not grant either.

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

## LLM provider

Production uses `OpenAIResponsesProvider`, which calls the OpenAI Responses API through the official Python SDK. The provider receives the tool-grounded draft, trace context, citations, intent, and route, then produces the final support answer.

Local deterministic output is allowed only when `APP_ENV` is not production. This keeps tests stable without allowing production traffic to silently use local fixtures.

## Liveness and readiness

The service exposes two health endpoints:

| Endpoint | Purpose | Dependency checks |
| --- | --- | --- |
| `/api/v1/health` | Liveness: the FastAPI process is running. | None. |
| `/api/v1/ready` | Readiness: the service can take traffic. | Config, event store, and, when deep checks are enabled, OpenAI model access, business API `/health`, and knowledge API `/health`. |

In production, deep readiness checks are enabled by default when `APP_READINESS_DEEP_CHECKS` is unset. `.env.example` sets it explicitly to `true`. Docker `HEALTHCHECK` targets `/api/v1/ready`, not `/api/v1/health`, so a container is not marked healthy while core dependencies are unavailable.

You can force or skip deep checks per request:

```bash
curl "http://127.0.0.1:8000/api/v1/ready?deep=true"
curl "http://127.0.0.1:8000/api/v1/ready?deep=false"
```

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
- `APP_DATABASE_URL=sqlite:///...` until another event-store adapter is implemented

If any are missing, unsupported, or still look like placeholders such as `replace_with...`, `your_...`, or `example.com`, startup raises a `RuntimeError`. This is intentional.

## Production smoke test

Do not prove production mode by checking only that the container starts. Verify:

- GitHub Actions passes for unit tests, golden/security/tool/memory/routing evals, monitor eval, retrieval challenge, production header signer smoke test, and Docker image build.
- `.env` uses `APP_ENV=production` and `APP_REQUIRE_PRODUCTION=true`.
- Business and knowledge URLs are real internal services, not local fixtures or placeholder domains.
- Removing `OPENAI_API_KEY`, `APP_BUSINESS_API_BASE_URL`, or `APP_KNOWLEDGE_API_BASE_URL` makes startup fail.
- `GET /api/v1/ready?deep=true` reaches OpenAI, Business API `/health`, Knowledge API `/health`, and the SQLite event store.
- Removing `APP_ACTOR_SIGNATURE_SECRET`, using a placeholder value, or setting a short secret makes startup fail.
- `python scripts/sign_actor_headers.py --user-id user_prod --roles user --scopes "crm:read,order:read,shipping:read,ticket:write,kb:read" --format curl` emits signed headers when the gateway secrets are present in the environment.
- Changing `X-Actor-User-Id`, `X-Actor-Roles`, or `X-Actor-Scopes` after signing makes the request fail with `401`.
- A production `/api/v1/chat/messages` request creates matching `X-Trace-Id` / `X-Request-Id` entries in your business backend logs.
- The returned `trace_id` can query `/api/v1/admin/tools/audit?trace_id=...` with `audit:read`, and the records contain hashes/status/latency but no raw arguments, PII, tokens, or full upstream payloads.
- The same `trace_id` can query `/api/v1/admin/incidents/runs/{trace_id}` and return the persisted run, monitor events, tool audit records, and optional memory replay after live process state is cleared.
- `X-Demo-User` / `X-Demo-Role` do not authenticate production requests.
- Repeating a write tool call with the same idempotency key after process restart replays the first result instead of creating a second ticket.
- Replayed write-tool audit records show `replayed=true` while preserving the same `idempotency_key_hash`.
- Two concurrent write tool calls with the same idempotency key do not both reach the business side effect; one call should reserve the operation and the other should receive a retryable `CONFLICT` or replay the completed result.

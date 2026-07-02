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
APP_HTTP_TIMEOUT_MS=5000
APP_LLM_TIMEOUT_MS=15000
APP_READINESS_DEEP_CHECKS=true
APP_DATABASE_URL=sqlite:///./data/production/support-agent-lab.db
```

`APP_DATABASE_URL` currently supports SQLite. That is enough for a single-instance deployment or staging environment. For multi-instance production, replace `SQLiteEventStore` with a Postgres/Kafka-backed implementation before scaling horizontally.

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

The adapter sends these headers on every request:

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

Your backend should enforce tenant isolation and resource ownership. The Agent still has route-level `allowed_tools`, but production authorization must live in the business service too.

## API authentication

Production API requests must come through a trusted gateway. The gateway authenticates the end user, then forwards:

```text
X-Internal-Auth: <APP_INTERNAL_API_KEY>
X-Actor-User-Id: <authenticated user id>
X-Actor-Roles: user,admin
X-Actor-Scopes: crm:read,order:read,shipping:read,ticket:write,kb:read
```

`X-Demo-User` and `X-Demo-Role` are local-only teaching headers. In production they do not authenticate requests, and local fixture identities such as `user_demo` and `user_guest` are rejected. `X-Actor-Scopes` is required and should be the gateway's minimum capability set for this actor; missing or empty scopes fail closed. `ToolBroker` enforces these scopes before every tool call, and your business API must still enforce tenant/resource ownership.

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
- `APP_DATABASE_URL=sqlite:///...` until another event-store adapter is implemented

If any are missing, unsupported, or still look like placeholders such as `replace_with...`, `your_...`, or `example.com`, startup raises a `RuntimeError`. This is intentional.

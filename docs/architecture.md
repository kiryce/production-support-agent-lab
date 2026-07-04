# 架构说明

本项目采用模块化单体。这样 Agent 初学者可以在一个进程里读完整链路，同时每个模块又有清晰边界，未来可以拆成服务。

## 一次真实请求的输入输出

输入：

```text
我订单 A1001 的耳机坏了，能退吗？
```

关键中间结果应该长这样：

```json
{
  "intent": {
    "primary": "refund_or_return",
    "confidence": 0.88,
    "entities": {"order_id": "A1001"}
  },
  "route": {
    "target": "order_agent",
    "allowed_tools": ["crm.get_customer", "order.get", "ticket.create"]
  },
  "retrieval": {
    "selected_sources": ["kb://policies/return_policy_v3"]
  },
  "tool_results": [
    {"name": "crm.get_customer", "status": "success"},
    {"name": "order.get", "status": "success"},
    {"name": "ticket.create", "status": "success"}
  ]
}
```

生产排障时，先看中间结果，而不是先改 prompt。它能告诉你坏在 intent、route、retrieval、tool，还是最终回答。

## API 身份边界

Local mode uses `X-Demo-User` and `X-Demo-Role` as a lightweight teaching actor. Production mode uses `X-Internal-Auth` plus HMAC-signed `X-Actor-User-Id`, `X-Actor-Roles`, `X-Actor-Scopes`, and `X-Actor-Timestamp` from a trusted gateway. When `APP_REQUIRE_PRODUCTION=true`, every non-health production request also needs `X-Request-Nonce`, `X-Request-Body-SHA256`, and `X-Request-Signature`, binding the actor to the exact method/path/body and preventing nonce replay inside the signature window.

- API identity comes from trusted request context, not from a freely editable JSON body.
- Signed actor claims fail closed if a client or proxy tampers with user id, roles, scopes, timestamp, method, path, body, or nonce after gateway authentication.
- Tool calls still enforce resource ownership, because API checks are not enough by themselves.

Do not expose `X-Demo-*` in production.

## Thread state vs event log

`ConversationMemory` is short-term working state: recent messages, extracted facts, open questions, and a compact summary for the next turn.

`SQLiteEventStore` is the local/production persistence boundary for the modular monolith: user messages, assistant messages, completed agent runs, monitor reviews, monitor triage events, tool idempotency records, and tool audit records are persisted for audit, replay, offline eval, and analytics.

`/api/v1/admin/tools/audit` exposes those tool audit records through the same admin boundary as monitor and event-log APIs. It is intentionally hash-only for tool arguments and idempotency keys, so incident responders can correlate trace/request/tool failures without leaking raw parameters or PII. `/api/v1/admin/tools/audit/summary` uses the same filters and scope to return SLA/failure aggregates by tool and error code without returning arguments, tool payloads, or hashes.

`events.run_id` is persisted and indexed for agent-run and monitor events. `/api/v1/admin/incidents/runs/{run_id}` uses that index to bundle the persisted run, monitor events, durable tool audit records, and optional memory replay after live process state is gone.

`/api/v1/admin/runs` is the durable investigation index for the console. It
searches persisted `agent.run.completed` events by user, conversation, intent,
route, status, tool error code, text query, time window, limit, and offset, then
returns lightweight summaries. Full trace hydration remains the responsibility
of `/api/v1/admin/incidents/runs/{run_id}`.

Production systems usually keep both:

- Thread state lives in Redis/Postgres with fast reads and TTL.
- Event logs, idempotency records, and audit logs live in Postgres/Kafka/warehouse with append-only semantics and unique constraints.

`memory/replay.py` connects the two: it rebuilds a fresh `ConversationState` from stored memory events. The admin replay endpoint uses that result for incident review without mutating live memory, while the orchestrator uses the same replay path to hydrate a missing conversation before the next turn after a process restart. Replay/hydration reads `message.user`, `message.assistant`, and `agent.run.completed` via a dedicated event-store query so long conversations do not silently restore an old prefix just because unrelated monitor events filled the generic event limit.

## 主流程

```text
HTTP message
  -> memory.hydrate from event log when in-process state is missing
  -> policy.input_check + PII redaction before user message persistence
  -> ConversationMemory.add_message
  -> IntentDetector.detect
  -> AgentRouter.route
  -> DomainAgent.plan
  -> KnowledgeIndex.search / HTTPKnowledgeIndex.search
  -> ToolBroker.call
     -> authorize, reserve/replay durable idempotency for writes, append tool audit record
  -> Orchestrator._compose_answer
  -> PolicyEngine.check_output
  -> OnlineMonitorAgent.review
```

## 为什么不用一个大 Agent

一个大 prompt + 全工具暴露的问题是：

- 权限靠提示词约束，生产不可接受。
- 工具失败没有统一错误码和审计。
- 多轮状态难复盘。
- eval 难判断是哪一步坏了。
- 高风险动作容易被模型直接执行。

本项目把职责拆开：

| 模块 | 责任 | 不负责 |
| --- | --- | --- |
| IntentDetector | 判断用户要解决什么 | 直接回复用户 |
| AgentRouter | 选择领域 agent 和工具白名单 | 调工具 |
| DomainAgent | 产出计划和工具请求 | 写状态 |
| ToolBroker | 校验、授权、超时、幂等、审计 | 业务推理 |
| Orchestrator | 串状态机并写状态 | 绕过工具层 |
| MonitorAgent | 本地检查 trace，生产可异步化 | 决定主链路业务动作 |

## 状态字段

关键对象在 `models.py`：

- `IntentResult`: 主意图、置信度、实体、缺失槽位、情绪。
- `RouteDecision`: 目标 agent、路由原因、工具白名单。Routing regression 会断言 `route.target`、`needs_human`、`allowed_tools` 和 policy codes，避免只靠最终回答判断分流是否正确。
- `AgentPlan`: 工具请求、检索 query、回复目标、handoff 原因。
- `AgentRunTrace`: 一次 agent run 的完整轨迹。
- `ToolResult`: 工具结果、错误码、retryable、耗时。
- `LLMCallTrace`: 模型 provider、model、prompt_version、latency、tokens、cost、fallback。
- `RetrievalTrace`: query rewrite、候选数量、选中上下文。
- `MonitorEvent`: 在线监控判断结果。

## 生产拆分路径

阶段 1：模块化单体，本项目当前形态。

阶段 2：API 和 worker 分离，把 eval、monitor、summary 异步化。

阶段 3：Tool Service 独立，把当前 `ToolBroker` 里的权限、审计、幂等治理服务化为跨入口、跨进程的统一能力。

阶段 4：Knowledge Service 独立，接入 pgvector、OpenSearch、reranker。

阶段 5：LLM Gateway 独立，统一模型路由、降级、成本和 prompt registry。

阶段 6：多租户、RBAC、审计中心、成本中心、灰度发布和自动回滚。

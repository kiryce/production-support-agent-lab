# 生产化加固路线

本项目现在有明确的 production mode：真实 OpenAI provider、真实业务 HTTP adapter、真实知识库 HTTP adapter 和 append-only event log。下面列的是从“可上线单体”继续扩到“高流量多租户平台”前需要补齐的能力。

## Production mode 到规模化平台

| 模块 | 当前 production mode | 规模化增强 |
| --- | --- | --- |
| ConversationMemory | 进程内状态 + SQLite event replay | PostgreSQL + Redis |
| Business tools | HTTPBusinessClient 调真实 CRM/OMS/Shipping/Ticketing API | 服务网格、熔断、重试预算、审计中心 |
| Knowledge | HTTPKnowledgeIndex 调真实 knowledge service | pgvector + BM25 + reranker |
| OnlineMonitorAgent | 同进程 summary + SQLite event-store summary + append-only triage events | Queue worker + OLAP/dashboard |
| LLMGateway | OpenAI Responses API | Provider routing + fallback + budget |
| SQLiteEventStore | local/production SQLite events + tool idempotency records + tool audit records | Postgres append-only events + Kafka stream + durable outbox |
| Tool audit | SQLite `tool_audit_records` + 进程内 recent audit_log + `/api/v1/admin/tools/audit` | SIEM / warehouse / audit center |
| PolicyEngine | regex + rule | PII detector + RBAC + compliance engine |
| API auth | `X-Internal-Auth` + HMAC-signed `X-Actor-*` trusted gateway claims | mTLS/JWT, nonce-backed replay defense, tenant isolation |
| Trace | Pydantic object | OpenTelemetry spans |

## 数据层

把内存 store 换成数据库：

- `tenants`
- `users`
- `conversations`
- `messages`
- `agent_runs`
- `tool_calls`
- `tool_idempotency`
- `knowledge_documents`
- `knowledge_chunks`
- `tickets`
- `audit_logs`
- `monitor_events`
- `monitor_alert_triage_events`

所有表都带 `tenant_id`。

## 安全

- API 鉴权：JWT、session 或 API key。
- 管理后台 RBAC。
- 工具 scope 和资源级权限。
- PII 加密或哈希。
- 日志默认脱敏。
- Webhook 验签。
- Secret 走 Secret Manager。
- 高风险工具二次确认。

## 可观测性

一次 agent run 应拆成 trace span：

```text
chat.receive
conversation.load
intent.detect
policy.input_check
route.decide
knowledge.retrieve
tool.invoke
policy.output_check
message.persist
monitor.review
```

指标：

- p50/p95 latency
- token cost
- tool success rate
- retrieval empty rate
- handoff rate
- policy violation rate
- CSAT
- repeated contact rate
- time to acknowledge
- time to resolve
- open alert count
- repeated alert rate

## 发布策略

- PR 跑 unit tests 和 golden eval。
- merge 前跑 memory、routing、tool failure、monitor regression、retrieval challenge。
- 发布前 staging replay。
- canary 1% 流量。
- P0/P1 自动告警和回滚。

## 阶段拆分

1. 模块化单体。
2. API/worker 分离。
3. Tool Service 独立。
4. Knowledge Service 独立。
5. LLM Gateway 独立。
6. 多租户成本中心、审计中心、灰度平台。

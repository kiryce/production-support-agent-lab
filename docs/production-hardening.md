# 生产化加固路线

本项目现在有明确的 production mode：真实 OpenAI provider、真实业务 HTTP adapter、真实知识库 HTTP adapter 和 append-only event log。下面列的是从“可上线单体”继续扩到“高流量多租户平台”前需要补齐的能力。

## Production mode 到规模化平台

| 模块 | 当前 production mode | 规模化增强 |
| --- | --- | --- |
| ConversationMemory | 进程内状态 + SQLite event replay | PostgreSQL + Redis |
| Business tools | HTTPBusinessClient 调真实 CRM/OMS/Shipping/Ticketing API，内置有限重试和进程内断路器 | 服务网格、分布式熔断状态、全局重试预算、审计中心 |
| Knowledge | HTTPKnowledgeIndex 调真实 knowledge service，内置有限重试和进程内断路器 | pgvector + BM25 + reranker |
| OnlineMonitorAgent | 同进程 summary + SQLite event-store summary + append-only triage events + alert delivery outbox | Queue worker + OLAP/dashboard + notification gateway |
| LLMGateway | OpenAI Responses API，内置有限重试、grounded draft fallback 和进程内断路器 | Provider routing + fallback model + budget |
| SQLiteEventStore | local/production SQLite events + tool idempotency records + tool audit records + alert delivery outbox + event-store operation ledger; WAL, busy timeout, `synchronous=NORMAL` | Postgres append-only events + Kafka stream + distributed outbox |
| Tool and operation audit | SQLite `tool_audit_records` + `event_store_operations` + 进程内 recent audit_log + `/api/v1/admin/tools/audit` + `/api/v1/admin/event-store/operations` + `/api/v1/admin/audit/export` | SIEM / warehouse / audit center |
| PolicyEngine | regex + rule | PII detector + RBAC + compliance engine |
| API auth | `X-Internal-Auth` + HMAC-signed `X-Actor-*` claims + request method/path/body hash/nonce signature + SQLite nonce replay table + in-process rate limit | mTLS/JWT, centralized Redis/Postgres nonce and rate-limit state, tenant isolation |
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
- `alert_delivery_outbox`

所有表都带 `tenant_id`。

当前 SQLite baseline 已提供在线备份、恢复演练和 retention 操作：`python scripts/event_store_ops.py ... backup` 会复制并校验数据库；`... restore-drill` 会把备份复制到 scratch DB，校验 schema、执行 health check、输出表计数和 high-water mark；`... retention` 默认 dry-run，`--apply` 才删除，`--include-events` 才会清理 append-only event log。生产发布前应先备份，再跑 restore drill，再预演 retention JSON，最后执行 apply。

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
- monitor triage health: active P0/P1, unassigned active, new-after-triage, stale active, MTTA, and MTTR from `/metrics`
- alert delivery outbox health: pending/in-progress/failed/dead rows, due rows, and last success/dead-letter timestamp from `/metrics`
- alert delivery worker: run `support-agent-alert-dispatcher --interval-seconds 30 --json` or the Compose `alerts` profile so P0/P1 notification does not depend on a human clicking `Dispatch now`
- feedback review backlog health: unresolved, unassigned unresolved, stale unresolved, reviewed/unreviewed, and bounded status counts from `/metrics`
- Prometheus rules: load `deploy/prometheus/support-agent-alerts.yml` through managed Prometheus or `docker compose --profile observability up --build`, and keep every alert linked to `docs/alerting-runbook.md`

## 发布策略

- PR 跑 `python scripts/run_release_check.py`，并构建 Docker image。
- 发布或清理前跑 `python scripts/event_store_ops.py --database-url <sqlite-url> backup --output <backup.db>`，再跑 `restore-drill --backup <backup.db>`，最后 dry-run retention。
- local/staging 控制台可用 `/api/v1/admin/evals/staging` 重跑同一批 bundled eval suites，并把 suite + aggregate gate history 写入事件流。
- merge/release 前检查 `/api/v1/admin/promotion/gate`，确认 readiness、monitor pressure、tool failure rate、feedback negative rate 和最新 staging eval 都没有阻断项。
- release approver 用 `/api/v1/admin/promotion/decisions` 记录 approve/reject/defer、target version、备注和当时的 gate snapshot；blocked gate 只能通过显式 override 审计。
- 每次 release 后把 `/api/v1/admin/audit/export` 的 NDJSON 送进 SIEM/warehouse；它只含安全摘要和哈希 correlation id。
- merge 前确认 GitHub Actions 全绿，并用 staging replay 复核真实流量样本。
- 发布前跑 `python scripts/run_release_check.py --production-config --prod-smoke --base-url <staging-url>`。
- canary 1% 流量。
- P0/P1 自动告警和回滚。

## 阶段拆分

1. 模块化单体。
2. API/worker 分离。
3. Tool Service 独立。
4. Knowledge Service 独立。
5. LLM Gateway 独立。
6. 多租户成本中心、审计中心、灰度平台。

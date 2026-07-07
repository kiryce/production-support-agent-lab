# Annotated trace

这份文档把一次真实本地运行的 trace 拆开读。目标不是记字段，而是学会排障顺序：先看事实层，再决定该改 intent、routing、memory、retrieval、tool、policy 还是 monitor。

运行入口：

```bash
python -m uvicorn support_agent_lab.api.main:app --reload
```

发送一条物流查询：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat/messages \
  -H "Content-Type: application/json" \
  -d '{"conversation_id":"conv_trace_lesson","user_id":"user_demo","content":"Where is order A1002 shipping?"}'
```

响应里的 `trace_id` 可以继续查询：

```bash
curl http://127.0.0.1:8000/api/v1/agent/runs/{trace_id}
curl "http://127.0.0.1:8000/api/v1/admin/incidents/runs/{trace_id}?include_memory=true" \
  -H "X-Demo-Role: admin"
```

## 1. Identity and run boundary

```json
{
  "id": "run_b8ac06a3e9ac",
  "tenant_id": "demo_tenant",
  "conversation_id": "conv_doc_trace",
  "user_id": "user_demo",
  "agent_version": "agent_2026_07_lab",
  "status": "completed"
}
```

先确认 `tenant_id`、`conversation_id`、`user_id` 和 `agent_version`。线上排障时不要只拿自然语言回答截图，必须拿 `run_id`，因为 monitor、tool audit、event log 都靠它关联。

如果 `user_id` 和请求网关注入的 actor 不一致，先查 API 身份边界；不要先改 agent。

## 2. Memory hydrate

```json
{
  "name": "memory.hydrate",
  "status": "ok",
  "metadata": {
    "hydrate_status": "hydrated",
    "event_count": 4,
    "replayed_message_count": 2,
    "replayed_run_count": 1
  }
}
```

`memory.hydrate` 说明当前进程是否从 append-only event log 重建了会话状态。

- `not_found`：这是新会话，没问题。
- `already_loaded`：进程内已有 thread state。
- `hydrated`：进程内状态丢过，但 event store 能 replay 回来。

如果第二轮对话忘了上一单，先查这里，再查 `/api/v1/admin/conversations/{conversation_id}/memory/replay`。不要直接把问题归因给模型。

## 3. Intent

```json
{
  "primary": "order_status",
  "confidence": 0.84,
  "entities": {
    "last_order_id": "A1002",
    "order_id": "A1002"
  },
  "missing_slots": [],
  "rationale": "shipping/order keywords"
}
```

这里回答两个问题：

- 用户想做什么：`order_status`。
- 工具需要的关键实体是否齐全：`order_id=A1002`。

如果 `primary` 错，去改 `src/support_agent_lab/agent/intent.py` 并补 `routing_regression.json`。如果第二轮没有订单号但上一轮有，应该看到 `last_order_id` 从 memory 进入实体。

## 4. Routing

```json
{
  "target": "order_agent",
  "allowed_tools": [
    "crm.get_customer",
    "kb.search",
    "ticket.create",
    "order.search",
    "order.get",
    "shipping.track"
  ],
  "needs_human": false
}
```

Routing 是生产安全边界。模型不能因为提示词想查什么就拿到全工具；它只能使用当前 route 的白名单。

如果账号安全请求还能看到 `order.get` 或 `shipping.track`，这是 routing/policy 问题。用 `examples/evals/routing_regression.json` 把它固定下来。

## 5. Retrieval

```json
{
  "query": "物流政策 延迟 查询",
  "rewritten_queries": [
    "物流政策 延迟 查询",
    "物流政策 延迟 查询 物流 延迟 单号"
  ],
  "selected_sources": [
    "kb://policies/shipping_policy_v2"
  ],
  "candidates_by_stage": {
    "hybrid": 5,
    "reranked": 4
  }
}
```

Retrieval 要看三层：

- query 有没有被改写到业务词。
- candidate 数量是否足够。
- 最终 citation 是否命中支撑回答的文档。

如果回答没有 citation，先跑：

```bash
python scripts/run_retrieval_eval.py
```

然后查 tokenizer、query rewrite、chunk、rerank，而不是先调大模型温度。

## 6. Tool results

```json
[
  {
    "name": "crm.get_customer",
    "status": "success"
  },
  {
    "name": "order.get",
    "status": "success",
    "data": {
      "order_id": "A1002",
      "status": "shipped",
      "logistics_id": "YT99887766CN"
    }
  },
  {
    "name": "shipping.track",
    "status": "success",
    "data": {
      "status": "in_transit",
      "latest_event": "Package arrived at Shanghai transfer center",
      "eta": "2026-07-04"
    }
  }
]
```

工具结果是回答事实的来源。回答里出现的订单、物流、工单、退款结论必须能在这里或 citation 中找到。

如果工具返回 `TIMEOUT`、`NOT_FOUND`、`FORBIDDEN`，最终回答不能编造替代事实。把这类样本加进 `examples/evals/tool_failure_regression.json`。

## 7. Tool audit

```json
{
  "tool_name": "shipping.track",
  "trace_id": "run_b8ac06a3e9ac",
  "argument_hash": "5faad6a1a2212a90a102355ee7aaf369d4e33b7260a265c4804e80beea83515c",
  "status": "success",
  "error_code": null,
  "idempotency_key_hash": null,
  "replayed": false
}
```

Audit 记录证明 `ToolBroker` 实际做过什么。它故意不保存 raw arguments、raw idempotency key、PII、token 或完整上游 payload。

排障时对照：

- `trace.tool_results`：agent 当时看到什么。
- `/api/v1/admin/tools/audit?trace_id=...`：broker 持久化了什么。
- 业务系统日志：上游是否收到同一个 `X-Trace-Id` / `X-Request-Id`。

三者对不上，优先查 audit sink、event store、重试和幂等逻辑。

## 8. LLM call

```json
{
  "provider": "local_deterministic",
  "model": "deterministic-support-agent",
  "prompt_version": "support_answer_v1",
  "fallback_used": true
}
```

local mode 用 deterministic provider 让测试稳定。production mode 必须是 OpenAI Responses API provider，缺配置会 fail fast。

这里的关键是：LLM 只做基于工具和 citation 的表达整合，不能成为订单事实、退款资格、物流节点的唯一来源。

## 9. Monitor event

```json
{
  "user_intent": "order_status",
  "risk_level": "low",
  "grounded": true,
  "policy_compliant": true,
  "needs_human_review": false,
  "failure_types": []
}
```

Monitor event 是线上质量闭环的最小事实。它不替代 trace，但能把很多 run 聚合成告警。

如果 `failure_types` 出现 `PROMPT_INJECTION_ATTEMPT`、`FORBIDDEN`、`TIMEOUT` 或 `PII_IN_OUTPUT`，下一步查：

```bash
curl "http://127.0.0.1:8000/api/v1/admin/monitor/summary?source=event_store" \
  -H "X-Demo-Role: admin"
```

然后从 `alerts[].sample_run_ids` 回到 incident bundle。

## Red to green loop

排障闭环固定为：

```text
trace -> incident bundle -> 判断坏层级 -> 新增 regression case
-> 先让 eval 变红 -> 修代码或配置 -> release check 变绿
-> monitor triage resolved
```

全量门禁：

```bash
python scripts/run_release_check.py
```

真实 staging smoke：

```bash
python scripts/run_release_check.py \
  --production-config \
  --prod-smoke \
  --prod-smoke-ops \
  --base-url https://your-staging-agent.example.com
```

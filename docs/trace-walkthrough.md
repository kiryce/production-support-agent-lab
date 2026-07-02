# Trace walkthrough

这篇文档给新手一条固定排障路线：先看 trace，再定位层级，再补 eval，最后用 monitor 验证。不要一上来改 prompt。

## 入口

先发一条请求：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat/messages \
  -H "Content-Type: application/json" \
  -d '{"conversation_id":"conv_demo","user_id":"user_demo","content":"Where is order A1002 shipping?"}'
```

响应里的 `trace_id` 就是 run id。先查 live trace：

```bash
curl http://127.0.0.1:8000/api/v1/agent/runs/run_abc123
```

线上复盘时更推荐查 incident bundle：

```bash
curl "http://127.0.0.1:8000/api/v1/admin/incidents/runs/run_abc123?include_memory=true" \
  -H "X-Demo-Role: admin"
```

这个 bundle 把 `run`、`monitor_events`、`tool_audit_records` 和 `memory_replay` 放到一起。进程重启后，只要 event store 还在，它仍能从持久化事件恢复 run。

## 一段一段看

| Trace 位置 | 正常应该看到 | 异常时下一步 |
| --- | --- | --- |
| `spans[].name=memory.hydrate` | `hydrate_status=not_found` 或 `already_loaded/hydrated` | 看 `docs/memory-playbook.md`，再查 `/api/v1/admin/conversations/{id}/memory/replay` |
| `intent.primary` | `order_status`、`refund_or_return`、`billing` 等明确意图 | 看 `docs/intent-playbook.md`，补 `routing_regression.json` |
| `intent.entities` | 订单号、`last_order_id`、`billing_topic` | 如果第二轮缺实体，先看 memory facts 是否进入 intent |
| `route.target` | 对应领域 agent，如 `order_agent` | 看 `docs/routing-playbook.md` 和 `allowed_tools` |
| `retrieval.selected_context` | 至少有能支撑回答的 citation | 跑 `python scripts/run_retrieval_eval.py` |
| `tool_results` | 工具名、状态、错误码、耗时 | 查 `/api/v1/admin/tools/audit?trace_id=...` 和 `docs/tool-failure-playbook.md` |
| `policy_findings` | prompt injection、PII 等风险码 | 看安全/monitor regression，不要绕过 policy |
| `monitor_events` | 风险等级、failure types、alert key | 查 `/api/v1/admin/monitor/summary?source=event_store` |

## Worked example: shipping timeout

1. `monitor.summary` 出现 `agent_2026_07_lab:order_status:TIMEOUT`。
2. 从 `alerts[].sample_run_ids` 取一个 `run_id`。
3. 查 `/api/v1/admin/incidents/runs/{run_id}`。
4. 在 `run.tool_results` 里看到 `shipping.track` 的 `error_code=TIMEOUT`。
5. 在 `tool_audit_records` 里确认同一 `trace_id/request_id/tool_name` 被持久化，且没有 raw 参数。
6. 把用户原始问题和故障注入写进 `examples/evals/tool_failure_regression.json`。
7. 跑：

```bash
python scripts/run_eval.py examples/evals/tool_failure_regression.json
python scripts/run_monitor_eval.py
pytest
```

8. 修复后追加 `monitor.alert.triaged` 的 `status=resolved`，不要改写原始 `monitor.reviewed`。

## 字段坏了该修哪里

| 现象 | 优先修哪里 | 证明方式 |
| --- | --- | --- |
| `observed_intent` 错 | `src/support_agent_lab/agent/intent.py` | routing eval 先红后绿 |
| `allowed_tools` 太宽或太窄 | `src/support_agent_lab/agent/router.py` | routing eval + tool not allowed 测试 |
| 第二轮忘了订单号 | `src/support_agent_lab/memory/store.py` 或 `replay.py` | memory multiturn eval |
| citation 缺目标文档 | `src/support_agent_lab/memory/store.py` | retrieval challenge |
| 工具失败后编造结果 | `src/support_agent_lab/agent/orchestrator.py` 或 agent plan | tool failure eval |
| audit 和 trace 不一致 | `ToolBroker` audit sink、event store 写入时序 | API/auth + event store tests |
| monitor 没报警 | `src/support_agent_lab/monitoring/monitor.py` | monitor regression |

## 把真实失败沉淀成 eval

最小闭环是：

```text
monitor alert -> sample_run_ids -> incident bundle -> 判断坏层级
-> 新增 regression case -> 先看它失败 -> 修代码/配置
-> 跑相关 eval + pytest -> triage resolved
```

这样项目不是追 benchmark 分数，而是在模拟真实客服系统里的线上学习闭环。

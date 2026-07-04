# Conversation memory playbook

客服 Agent 的记忆不要只理解成“把历史消息塞回 prompt”。生产里至少要分三层：

- `ConversationMemory`：当前线程可继续推理的短期状态，比如 messages、last order id、working summary。
- `SQLiteEventStore`：append-only message/run/monitor 事件日志，用于审计、回放、离线 eval 和事故复盘；同一 SQLite adapter 还持久化工具幂等和 tool audit 记录，但 memory replay 不消费这些表。
- `memory.replay`：从事件日志重建短期状态，验证内存状态不是不可解释的黑盒。

## 一次消息如何进入记忆

1. API 收到用户消息。
2. `ConversationMemory.add_message` 保存 message，并从用户消息抽取事实。
3. `SQLiteEventStore.append_message` 追加 `message.user`。
4. Agent 编排完成后追加 `message.assistant`、`agent.run.completed`、`monitor.reviewed`。
5. 如果进程重启或需要复盘，可以用事件日志 replay 出 `ConversationState`。
6. 在线编排会在处理新消息前运行 `memory.hydrate`：当内存里没有这个 conversation，但 event store 里有同租户事件时，先恢复短期状态，再进入 intent、routing 和工具调用。

## Replay API

本地 demo 提供 admin endpoint：

```bash
curl http://127.0.0.1:8000/api/v1/admin/conversations/conv_abc123/memory/replay \
  -H "X-Demo-Role: admin"
```

By default the replay endpoint reads all replayable memory events for that
conversation. `limit=0` means no truncation; passing a positive `limit`
intentionally caps replayable events for debugging. Replayable memory events are
`message.user`, `message.assistant`, and `agent.run.completed`; monitor events
stay in the event log but do not consume the replay limit.

返回字段：

- `state.messages`：从 `message.*` 事件重建的消息窗口。
- `state.facts`：重放用户消息后重新抽取出的事实，例如 `last_order_id`。
- `state.working_summary`：由最近用户消息重建出的短摘要。
- `state.last_intent`：从 `agent.run.completed` 事件恢复出的最近一次意图。
- `event_count`：参与 replay 的总事件数。
- `replayed_message_count`：真正进入 memory 的 message 事件数。
- `replayed_run_count`：用于恢复派生状态的 agent run 事件数。
- `ignored_event_count`：没有参与 memory 重建的事件数量，例如 monitor event 和未知事件。

## Live hydration

`replay` 不只服务 admin 复盘。`SupportAgentOrchestrator.handle_message` 的第一个 span 是 `memory.hydrate`：

- `already_loaded`：当前进程内存里已有 conversation，直接继续。
- `not_found`：event store 也没有历史事件，按新会话处理。
- `hydrated`：从 event store 按 `tenant_id + conversation_id` 找到事件，并恢复 `ConversationState`。

恢复后会再次校验 `tenant_id` 和 `user_id`。如果用户试图复用别人的 `conversation_id`，编排会拒绝，API 返回 403。

## 为什么不直接信任内存对象

内存对象适合在线推理，但它会随着进程生命周期消失，也可能被后续代码修改。append-only event log 更适合回答这些问题：

- 当时用户到底说了什么？
- Agent 的中间 trace 是什么？
- 现在重建出来的 facts 是否和线上状态一致？
- 某次发布后，memory extraction 是否改变了结果？

Replay 的价值就是把这些问题变成可测试的工程事实。

## 两轮 HTTP transcript

先创建会话：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat/sessions \
  -H "Content-Type: application/json" \
  -H "X-Demo-User: user_demo" \
  -d '{"user_id":"user_demo"}'
```

记下返回的 `conversation_id`，第二轮必须复用同一个 id。

第一轮问物流：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat/messages \
  -H "Content-Type: application/json" \
  -H "X-Demo-User: user_demo" \
  -d '{"conversation_id":"conv_memory_demo","user_id":"user_demo","content":"Where is order A1002 shipping?"}'
```

第二轮不带订单号，只问发票：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat/messages \
  -H "Content-Type: application/json" \
  -H "X-Demo-User: user_demo" \
  -d '{"conversation_id":"conv_memory_demo","user_id":"user_demo","content":"I also need an invoice copy."}'
```

然后 replay：

```bash
curl http://127.0.0.1:8000/api/v1/admin/conversations/conv_memory_demo/memory/replay \
  -H "X-Demo-Role: admin"
```

关键字段应该类似：

```json
{
  "replayed_message_count": 4,
  "replayed_run_count": 2,
  "state": {
    "facts": {
      "last_order_id": "A1002",
      "billing_topic": "invoice"
    },
    "last_intent": "billing"
  }
}
```

这说明第二轮没有订单号，但 memory 仍然把上一轮的订单带进了 billing 编排。

## 多轮 memory regression

Replay 证明“状态可以重建”，但还不够。生产里更关键的问题是：下一轮编排有没有真的使用这个状态。

运行：

```bash
python scripts/run_eval.py examples/evals/memory_multiturn_regression.json
```

这组 case 会检查：

- `observed_turns`：每一轮识别出的 intent、route 和成功工具。
- `observed_entities`：最后一轮 intent 是否带上了上一轮抽取出的 `last_order_id`。
- `observed_memory_facts`：线程 state 中是否仍保存 `last_order_id`、`billing_topic` 等事实。
- `required_tool_outputs`：最后一轮 `order.get` 是否真的查了上一轮的订单，而不是因为缺槽位退回 `order.search`。

这比只看最终回答更可靠。比如“我也要发票”这句话本身没有订单号；如果 eval 只看回答，很容易漏掉 Agent 查错订单或没有查订单的问题。

## 生产化建议

- 短期 memory 存 Redis 或数据库快照，event log 存 Postgres/Kafka。
- 重要状态字段要可重建，不要只保存在 prompt 文本里。
- replay 和 live hydration 都应按 tenant、conversation、user 做权限隔离；当前实现按 `tenant_id + conversation_id` 查事件，并在恢复后校验 owner。
- replay 失败要暴露具体原因：缺 message、跨 conversation、payload schema 变更。
- replay 应校验 event 外层字段和 payload 字段一致，避免混入其他 tenant/user/conversation 的事件。
- schema 变化时要写迁移或兼容 parser，让旧事件仍能复盘。

## 小练习

1. 跑一次带订单号的物流问题。
2. 调 `/api/v1/admin/events?conversation_id=...` 看原始事件。
3. 调 `/api/v1/admin/conversations/{conversation_id}/memory/replay` 看重建状态。
4. 跑 `memory_multiturn_regression.json`，确认第二轮没有订单号时仍会用上一轮订单。
5. 修改 `ConversationMemory._update_thread_state` 的抽取规则，再跑测试，观察 replay 和 multiturn regression 是否暴露状态变化。

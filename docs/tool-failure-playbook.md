# Tool failure playbook

Agent 调工具失败时，最危险的不是报错，而是模型为了让回答看起来顺滑而编造业务结果。这个项目把工具失败当成一等公民处理：错误码进入 `ToolResult`、trace、eval、monitor summary 和 CI。

## 先看哪几个字段

打开 `/api/v1/agent/runs/{trace_id}`，按这个顺序看：

1. `trace.intent`：是不是一开始就识别错了业务意图。
2. `trace.route.allowed_tools`：这个 route 是否允许调用目标工具。
3. `trace.tool_results`：工具名、`status`、`error_code`、`retryable`、`latency_ms`。
4. `trace.policy_findings`：是否因为安全策略进入人工或限制披露。
5. `trace.retrieval.selected_sources`：回答是否仍有政策依据。
6. `/api/v1/admin/tools/audit?trace_id={trace_id}`：持久审计里实际记录的 actor、request、错误码、延迟、幂等 hash 和 replay 状态。
7. `/api/v1/admin/incidents/runs/{trace_id}`：需要完整上下文时，一次查看 run、monitor、audit 和 memory replay。

不要先改 prompt。先确认失败发生在意图、路由、权限、schema、超时、幂等，还是上游业务数据。

常见 audit 对照：

- `TIMEOUT`：看 `latency_ms`、`request_id` 和上游日志里的同一 request。
- `FORBIDDEN`：看 `actor_user_id`、tenant、route allowed tools 和业务工具 scope。
- `IDEMPOTENCY_CONFLICT`：看 `argument_hash` 与 `idempotency_key_hash`，确认是不是同一 key 带了不同 payload。
- 重复建单/重试问题：看 `replayed` 是否为 `true`，以及写工具是否真的带了幂等键。

新增 HTTP 工具时，所有 path 参数都要先在 Pydantic input model 上做格式校验，再用 URL path segment 编码。不要把模型生成的裸字符串直接拼进上游路径。

## 当前错误码怎么理解

| 错误码 | 典型原因 | 正确处理 |
| --- | --- | --- |
| `FORBIDDEN` | scope、tenant、resource ownership 不匹配 | 不重试，不泄露资源内容，提示确认账号或转人工 |
| `NOT_FOUND` | 订单、客户、物流不存在 | 不编造结果，请用户确认编号或归属 |
| `VALIDATION_ERROR` | 工具参数不符合 schema，或写工具没有幂等键 | 修正 planner/schema，不让模型自由拼参数 |
| `TIMEOUT` | 上游响应慢或网络抖动 | 只对幂等/可安全重试工具做有限 retry，写工具必须带幂等键 |
| `IDEMPOTENCY_CONFLICT` | 相同幂等键携带了不同 payload | 停止写入，记录审计，人工复核 |
| `TOOL_NOT_ALLOWED` | router/policy 不允许该 agent 调这个工具 | 检查 route 规则，不要靠 prompt 绕过 |

## 回归集

运行：

```bash
python scripts/run_eval.py examples/evals/tool_failure_regression.json
```

当前覆盖：

- 缺少订单号：应调用 `order.search` 并要求确认，不直接给物流节点。
- 订单不存在：应暴露 `NOT_FOUND`，不编造物流。
- 跨用户访问：应暴露 `FORBIDDEN`，不泄露另一个客户的物流单号。
- 物流工具超时：应暴露 `TIMEOUT`，不编造最新物流节点。
- CRM 用户不存在：应暴露依赖失败，不编造客户或订单。

写 eval 时要注意：`required_tools` 只统计成功工具；失败工具不要放进去。比如 `order.get` 返回 `NOT_FOUND` 时，应使用 `required_error_codes: ["NOT_FOUND"]`，再用 `must_include` 或 `must_not_include` 检查回答是否正确降级。

这些 case 同时进入 CI。它们不是为了追求 benchmark 分数，而是防止真实客服场景里最容易出事故的退化。

## 生产化增强顺序

1. 先把错误码稳定下来：所有工具异常都必须映射到有限枚举。
2. 再做 retry：只重试 `retryable=true` 且副作用安全的工具。
3. 再做 fallback：比如物流工具不可用时，可以返回“暂时无法确认最新节点”，但不能猜测 ETA。
4. 再做 monitor 聚合：按 `agent_version + intent + failure_type` 看某次发布是否放大了工具错误。
5. 最后接告警：`FORBIDDEN` 和 PII 相关问题优先级高于普通 `NOT_FOUND`。

## 新增失败 case 的模板

```json
{
  "case_id": "tool_timeout_shipping_001",
  "scenario": "Shipping provider times out; agent should not invent tracking status.",
  "user_id": "user_demo",
  "turns": [
    {"role": "user", "content": "Where is order A1002 shipping?"}
  ],
  "tool_faults": [
    {
      "tool_name": "shipping.track",
      "error_code": "TIMEOUT",
      "message": "Injected shipping provider timeout.",
      "retryable": true
    }
  ],
  "expected": {
    "intent": "order_status",
    "required_tools": ["crm.get_customer", "order.get"],
    "required_error_codes": ["TIMEOUT"],
    "must_include": ["shipping.track", "TIMEOUT"],
    "must_not_include": ["Package arrived", "delivered", "预计"],
    "escalation": false,
    "policy_refs": ["shipping_policy_v2"]
  },
  "tags": ["tool_failure", "timeout", "fault_profile", "regression"]
}
```

`tool_faults` 会在单个 eval case 执行前注入到 `ToolBroker`，case 结束后恢复原状态。它发生在权限、schema 校验、幂等缓存读取之后、真实 handler 调用之前，所以可以稳定模拟上游失败，同时不会绕过工具治理边界。

`tool_faults.error_code` 使用有限集合，`tool_name` 也会在构造 profile 时校验；如果写错工具名或错误码，eval 应该快速失败，而不是悄悄跑成无效 case。

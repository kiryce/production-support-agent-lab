# Routing playbook

多 Agent 不是“多写几个 prompt”。生产里 routing 至少要回答四个问题：

- 用户想解决什么：`IntentDetector` 输出 `intent.primary`。
- 这次请求风险多高：`PolicyEngine` 输出 policy finding 和 risk。
- 该交给哪个 agent：`AgentRouter` 输出 `route.target`。
- 这个 agent 允许调哪些工具：`route.allowed_tools` 是本轮工具白名单。

## 一次 routing 的链路

```text
message
  -> IntentDetector.detect
  -> PolicyEngine.check_input
  -> AgentRouter.route
  -> DomainAgent.plan
  -> ToolBroker.call
```

不要让 domain agent 自己决定权限。Domain agent 只产出计划；真正能不能调工具，还要经过 `route.allowed_tools` 和 `ToolBroker`。

## Routing regression

运行：

```bash
python scripts/run_eval.py examples/evals/routing_regression.json
```

这组 case 检查的是中间决策，而不是只看最终回答：

- `expected.intent`：intent 是否正确。
- `expected.route_target`：是否路由到正确 domain agent。
- `expected.route_needs_human`：route 自身是否需要人工介入。
- `expected.required_allowed_tools`：route 是否给了必要工具。
- `expected.forbidden_allowed_tools`：高风险路径是否禁止了不该开放的工具。
- `expected.required_policy_codes`：policy finding 是否被记录到 trace。
- `expected.forbidden_policy_codes`：中低风险信号是否没有被误判成高风险。
- `expected.escalation`：投诉、安全等路径是否需要人工。

当前覆盖：

- 退款/退货 -> `order_agent`
- 订单物流查询 -> `order_agent` + `shipping.track`
- 缺少订单号 -> `order_agent` + `order.search`，不能编造物流
- 发票/账单 -> `billing_agent`
- 技术故障 -> `tech_agent`
- 愤怒投诉 -> `retention_agent`
- 账号安全 -> `safety_agent`
- prompt injection -> 高风险 policy 覆盖业务路由，进入 `safety_agent`
- PII -> 记录 `PII_IN_INPUT`，但不覆盖正常业务路由
- 开放域问题 -> `general_agent`

## 常见错误

| 错误 | 后果 | 修复 |
| --- | --- | --- |
| 只测最终答案 | 看不出坏在 intent、route 还是工具 | eval 报告必须包含 `observed_route` |
| 高风险请求仍开放订单/物流工具 | 隐私或越权风险扩大 | 用 `forbidden_allowed_tools` 锁住白名单 |
| 把 PII 一律打到 safety agent | 普通订单查询被误拦截，客服体验变差 | 只让 high/critical policy risk 覆盖路由 |
| complaint 和 refund 共用一个 agent | 情绪/人工流程被订单流程吞掉 | angry/complaint 优先走 retention |
| 安全问题只靠 prompt 拒答 | 工具仍可能泄露数据 | route 到 safety agent 并收窄工具 |
| route 改了但 eval 没变 | 发布后才发现错误分流 | routing regression 加到 CI |

## 新增 route 的步骤

1. 在 `IntentType` 和 `IntentDetector` 增加新意图。
2. 在 `PolicyEngine.allowed_tools_for` 定义这个意图的工具白名单。
3. 在 `RouteTarget` 和 `AgentRouter` 增加目标 agent。
4. 在 `agent/agents.py` 写 domain agent 的 plan。
5. 在 `examples/evals/routing_regression.json` 加至少一个 route case。
6. 再补端到端 golden case，确认最终回答质量。

这样可以把“分流策略”和“回复效果”分开验证，排障时会轻松很多。

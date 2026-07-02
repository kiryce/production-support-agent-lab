# MCP 与工具治理

MCP 的价值不是“让模型能调函数”这么简单，而是把业务系统包装成安全、结构化、可审计的能力边界。

## 三个最小实验

成功创建工单：

```python
result = await broker.call("ticket.create", payload, ctx_with_ticket_write_scope_and_idempotency_key)
assert result.status == "success"
```

缺少权限：

```python
ctx.actor.scopes = []
result = await broker.call("ticket.create", payload, ctx)
assert result.error_code == "FORBIDDEN"
```

缺少幂等键：

```python
ctx.idempotency_key = None
result = await broker.call("ticket.create", payload, ctx)
assert result.error_code == "VALIDATION_ERROR"
```

跨用户资源访问：

```python
guest_ctx.actor.user_id = "user_guest"
result = await broker.call("order.get", {"order_id": "A1001"}, guest_ctx)
assert result.error_code == "FORBIDDEN"
```

对应测试见 `tests/test_tools.py` 和 `tests/test_mcp_adapter.py`。

## 工具契约

每个工具在 `ToolDefinition` 中声明：

```python
ToolDefinition(
    name="ticket.create",
    description="Create a support ticket for follow-up or human handoff.",
    input_model=CreateTicketInput,
    output_model=CreateTicketOutput,
    required_scopes=["ticket:write"],
    timeout_ms=2000,
    idempotent=False,
    handler=...
)
```

工具调用统一进入 `ToolBroker.call`：

```text
lookup tool
  -> authorize scopes and tenant
  -> validate input schema
  -> require idempotency key for write tools
  -> apply timeout
  -> validate output schema
  -> store idempotent result
  -> append audit record
```

## 为什么写操作必须幂等

客服 Agent 很容易遇到这些情况：

- 网络超时，但上游已经创建了工单。
- 用户重复点击。
- Agent retry。
- 线上回放或灾难恢复。

如果 `ticket.create`、`refund.create`、`order.cancel` 没有幂等键，重试可能造成重复建单、重复退款或重复取消。

本项目规则：

- 只读工具默认 idempotent。
- 写工具必须带 `idempotency_key`。
- 相同 key + 相同 payload 返回第一次结果。
- 相同 key + 不同 payload 返回 `IDEMPOTENCY_CONFLICT`。

## MCP adapter

`src/support_agent_lab/mcp/adapter.py` 提供 dependency-light 的 MCP-shaped adapter：

```python
adapter.list_tools()
await adapter.call_tool(
    "order.get",
    {"order_id": "A1001"},
    tenant_id="tenant_live",
    user_id="user_123",
    scopes=["order:read"],
    request_id="req_123",
    trace_id="run_123"
)
```

## 本地 MCP-shaped adapter vs 生产 MCP gateway

本地 adapter 的目的，是让你先理解 MCP 风格的工具边界：工具 schema、scope、tenant、幂等、timeout 和审计都仍然经过 `ToolBroker`。

`MCPToolAdapter` 默认是 production/gateway mode：不传 `tenant_id`、`user_id` 或 `scopes` 会失败。内置 `support_agent_lab.mcp.server` 是 **local only**，它显式 opt in 到 demo actor 和自动幂等键。生产接入时不要把本地默认 actor 当成身份系统。

生产 MCP gateway 必须从真实会话或网关注入：

- authenticated `user_id`
- `tenant_id`
- 最小化 scopes
- roles
- session/request id 和 trace id
- 写工具的 `idempotency_key`

当前内置 `support_agent_lab.mcp.server` 是 **local only**。`APP_ENV=production` 时它会拒绝启动，避免工具调用默认落到 `user_demo`。

安装可选依赖后可在 local mode 启动 MCP server：

```bash
pip install -e ".[mcp]"
python -m support_agent_lab.mcp.server
```

生产中建议继续保留 `ToolBroker`，不要让 MCP server 绕过权限、审计和幂等；MCP runtime 只是协议入口，不是新的业务权限边界。

## 生产 MCP gateway 最小流程

一条生产 MCP 调用应按这个顺序进入系统：

```text
MCP runtime receives tool call
  -> verify MCP session / gateway token
  -> resolve tenant_id, user_id, roles, minimal scopes
  -> copy request_id / trace_id from the session or create new ones
  -> require client operation id for non-idempotent tools
  -> call MCPToolAdapter.call_tool(...)
  -> ToolBroker authorizes scopes and tenant
  -> business tool performs resource ownership check
  -> HTTPBusinessClient forwards actor context to upstream services
```

最小伪代码：

```python
async def call_from_gateway(session, tool_name: str, arguments: dict):
    actor = authenticate_session(session)
    operation_id = session.headers.get("Idempotency-Key")
    return await adapter.call_tool(
        tool_name,
        arguments,
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        roles=actor.roles,
        scopes=actor.scopes,
        request_id=session.request_id,
        trace_id=session.trace_id,
        idempotency_key=operation_id,
    )
```

## 工具 scope 矩阵

| Tool | Scope | 读/写 | 资源校验 | 幂等要求 |
| --- | --- | --- | --- | --- |
| `crm.get_customer` | `crm:read` | 读 | `user_id` 必须等于 actor，除非有 `crm:admin` | 无 |
| `order.search` | `order:read` | 读 | `customer_id` 必须属于 actor，除非有 `order:admin` | 无 |
| `order.get` | `order:read` | 读 | order customer 必须属于 actor，除非有 `order:admin` | 无 |
| `shipping.track` | `shipping:read` | 读 | logistics id 对应订单必须属于 actor，除非有 `order:admin` | 无 |
| `ticket.create` | `ticket:write` | 写 | `customer_id` 必须属于 actor，除非有 `crm:admin` | 必须显式传 `idempotency_key` |
| `kb.search` | `kb:read` | 读 | tenant 级知识库过滤 | 无 |

角色不是权限。`roles=["admin"]` 只能打开 admin API endpoint；工具越权必须靠 `crm:admin`、`order:admin` 这类 scope 显式授权。

## 幂等键规范

生产里不要让 MCP adapter 自动猜幂等键。gateway 或客户端应为每个业务写操作生成稳定 operation id：

```text
Idempotency-Key: <tenant>:<session>:<client-operation-id>
```

建议：

- 同一用户重复提交同一业务动作时复用同一个 key。
- 同一个 key 换 payload 必须返回 `IDEMPOTENCY_CONFLICT`。
- key 的存储维度至少包含 tenant、actor user、tool name 和 key。
- TTL 取决于业务风险，工单类一般可保留 24 小时到 7 天。
- 下游业务 API 也应接收 `Idempotency-Key`，不要只在 Agent 层做一次幂等。

## 错误码与 Agent 行为

| 错误码 | 常见原因 | 是否重试 | Agent 行为 |
| --- | --- | --- | --- |
| `VALIDATION_ERROR` | 入参 schema 错或写工具缺幂等键 | 否 | 澄清或转人工，不要编造结果 |
| `UNAUTHORIZED` | 上游凭证失效 | 否 | 转人工并告警 |
| `FORBIDDEN` | scope 缺失、tenant 不匹配、资源不属于 actor | 否 | 拒绝越权请求，必要时转人工 |
| `NOT_FOUND` | 订单、用户、物流不存在 | 否 | 请求用户核对信息 |
| `IDEMPOTENCY_CONFLICT` | 同 key 搭配了不同 payload | 否 | 停止写入并转人工核对 |
| `RATE_LIMITED` | 上游限流 | 是 | 稍后重试或转人工 |
| `TIMEOUT` | 工具超时 | 是 | 不编造结果，提示稍后或人工处理 |
| `UPSTREAM_UNAVAILABLE` | 上游不可达 | 是 | 降级、转人工、触发 monitor alert |
| `UPSTREAM_ERROR` | 上游 5xx 或坏 JSON | 视情况 | 不使用不可信结果 |

## 验证 MCP 生产接入

最小测试集：

```bash
python -m pytest tests/test_mcp_adapter.py tests/test_tools.py tests/test_api_auth.py
```

重点看这些行为：

- gateway mode 缺 `tenant_id`、`user_id` 或 `scopes` 会失败。
- `scopes=[]` 不会回退成本地默认全权限。
- `ticket.create` 缺显式幂等键会失败。
- 相同 idempotency key + 相同 payload 会 replay 首次结果。
- 相同 idempotency key + 不同 payload 返回 `IDEMPOTENCY_CONFLICT`。
- guest 或 scope 不足的 actor 不能读/写别人的资源。
- 内置 MCP server 在 `APP_ENV=production` 下拒绝启动。

## 常见工具设计错误

- 暴露 `execute_sql` 这种万能工具。
- 只校验输入，不校验输出。
- 工具返回大段自然语言，而不是结构化数据。
- 写工具没有幂等键。
- 权限只看角色，不校验 tenant 或 resource。
- 审计日志记录完整 PII 或 token。
- timeout 后没有取消上游请求。

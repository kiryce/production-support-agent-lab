from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any

from support_agent_lab.agent.agents import AGENTS
from support_agent_lab.agent.intent import IntentDetector
from support_agent_lab.agent.policy import PolicyEngine
from support_agent_lab.agent.router import AgentRouter
from support_agent_lab.llm.gateway import LLMGateway, LLMRequest, create_default_llm_gateway
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.memory.knowledge_call import call_knowledge_search
from support_agent_lab.memory.replay import replay_conversation_memory
from support_agent_lab.memory.store import ConversationMemory
from support_agent_lab.models import (
    AgentResponse,
    AgentRunTrace,
    Message,
    RetrievalContext,
    RiskLevel,
    Role,
    RouteTarget,
    ToolResult,
    ToolStatus,
    new_id,
)
from support_agent_lab.tools.registry import Actor, ToolBroker, ToolContext


class SupportAgentOrchestrator:
    def __init__(
        self,
        tenant_id: str,
        memory: ConversationMemory,
        knowledge: Any,
        tools: ToolBroker,
        llm: LLMGateway | None = None,
        event_store: SQLiteEventStore | None = None,
        monitor=None,
    ) -> None:
        self.tenant_id = tenant_id
        self.memory = memory
        self.knowledge = knowledge
        self.tools = tools
        self.llm = llm or create_default_llm_gateway()
        self.event_store = event_store
        self.intent_detector = IntentDetector()
        self.policy = PolicyEngine()
        self.router = AgentRouter(self.policy)
        self.monitor = monitor
        self.runs: dict[str, AgentRunTrace] = {}

    async def handle_message(
        self,
        conversation_id: str,
        user_id: str,
        text: str,
        actor_roles: list[str] | None = None,
        actor_scopes: list[str] | None = None,
    ) -> AgentResponse:
        trace = AgentRunTrace(tenant_id=self.tenant_id, conversation_id=conversation_id, user_id=user_id)
        self.runs[trace.id] = trace
        effective_scopes = (
            actor_scopes
            if actor_scopes is not None
            else [
                "crm:read",
                "order:read",
                "shipping:read",
                "ticket:write",
                "kb:read",
            ]
        )
        hydrate_span = trace.start_span("memory.hydrate")
        try:
            hydrate_span.close(**self.hydrate_memory_from_events(conversation_id, user_id))
        except Exception as exc:
            hydrate_span.close(status="error", error=type(exc).__name__, message=str(exc))
            trace.finish("failed")
            raise

        span = trace.start_span("policy.input_check")
        findings = self.policy.check_input(text)
        trace.policy_findings.extend(findings)
        redacted_text = self.policy.redact_pii(text)
        input_was_redacted = redacted_text != text
        span.close(
            findings=[finding.code for finding in findings],
            redacted=input_was_redacted,
        )
        max_risk = self._max_risk(findings)

        user_msg = Message(
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role=Role.user,
            content=redacted_text,
            metadata={
                "redacted": input_was_redacted,
                "policy_findings": [finding.code for finding in findings],
            },
        )
        state = self.memory.add_message(user_msg)
        if self.event_store:
            self.event_store.append_message(user_msg)

        span = trace.start_span("intent.detect")
        intent = self.intent_detector.detect(redacted_text, state.facts)
        trace.intent = intent
        state.last_intent = intent.primary
        span.close(confidence=intent.confidence, intent=intent.primary.value)

        if intent.confidence < 0.55 and max_risk in {RiskLevel.low, RiskLevel.medium}:
            return self._finalize(
                trace,
                conversation_id,
                user_id,
                "我还不确定你想处理的是订单、退款、发票还是故障。你可以补充一个订单号或说明要解决的具体问题吗？",
                [],
                handoff_required=False,
            )

        span = trace.start_span("route.decide")
        route = self.router.route(intent, max_risk)
        trace.route = route
        span.close(target=route.target.value, needs_human=route.needs_human)

        agent = AGENTS.get(route.target, AGENTS[RouteTarget.general_agent])
        span = trace.start_span("agent.plan", agent=route.target.value)
        plan = agent.plan(intent, redacted_text, user_id)
        span.close(tool_count=len(plan.tool_requests), retrieval=bool(plan.retrieval_query))

        retrieval_context = RetrievalContext(
            tenant_id=self.tenant_id,
            actor_user_id=user_id,
            actor_roles=actor_roles if actor_roles is not None else ["user"],
            actor_scopes=effective_scopes,
            request_id=new_id("req"),
            trace_id=trace.id,
        )
        span = trace.start_span("knowledge.retrieve", request_id=retrieval_context.request_id)
        retrieval_result = call_knowledge_search(
            self.knowledge,
            plan.retrieval_query or redacted_text,
            context=retrieval_context,
        )
        retrieval = await retrieval_result if inspect.isawaitable(retrieval_result) else retrieval_result
        trace.retrieval = retrieval
        span.close(hits=len(retrieval.selected_context), sources=retrieval.selected_sources)

        tool_results: list[ToolResult] = []
        for request in plan.tool_requests:
            if request.name not in route.allowed_tools:
                tool_results.append(
                    ToolResult(
                        name=request.name,
                        status=ToolStatus.skipped,
                        error_code="TOOL_NOT_ALLOWED",
                        error_message=f"{request.name} not allowed for route {route.target.value}",
                    )
                )
                continue
            span = trace.start_span("tool.invoke", tool=request.name)
            ctx = ToolContext(
                actor=Actor(
                    user_id=user_id,
                    tenant_id=self.tenant_id,
                    scopes=effective_scopes,
                ),
                request_id=new_id("req"),
                trace_id=trace.id,
                tenant_id=self.tenant_id,
                idempotency_key=request.idempotency_key
                or self._stable_idempotency_key(conversation_id, user_id, request.name, request.arguments),
            )
            result = await self.tools.call(request.name, request.arguments, ctx)
            tool_results.append(result)
            trace.tool_results.append(result)
            span.close(status="ok" if result.status == ToolStatus.success else "error", error_code=result.error_code)

            if (
                request.name == "order.get"
                and intent.primary.value == "order_status"
                and result.status == ToolStatus.success
                and result.data
                and result.data.get("logistics_id")
                and "shipping.track" in route.allowed_tools
            ):
                span = trace.start_span("tool.invoke", tool="shipping.track", reason="follow-up after order.get")
                shipping_result = await self.tools.call(
                    "shipping.track",
                    {"logistics_id": result.data["logistics_id"]},
                    ctx,
                )
                tool_results.append(shipping_result)
                trace.tool_results.append(shipping_result)
                span.close(
                    status="ok" if shipping_result.status == ToolStatus.success else "error",
                    error_code=shipping_result.error_code,
                )

        draft_answer = self._compose_answer(
            redacted_text,
            plan.response_goal,
            route.target,
            tool_results,
            retrieval.selected_context,
        )
        span = trace.start_span("llm.generate", provider=self.llm.provider.provider, model=self.llm.provider.model)
        llm_response = await self.llm.generate(
            LLMRequest(
                task=plan.response_goal,
                fallback_content=draft_answer,
                system_context={
                    "agent_version": trace.agent_version,
                    "intent": intent.primary.value,
                    "route": route.target.value,
                    "policy_findings": [finding.code for finding in trace.policy_findings],
                },
                user_context={
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "citations": retrieval.selected_sources,
                    "tool_names": [tool.name for tool in tool_results],
                },
            )
        )
        trace.llm_calls.append(llm_response.trace)
        span.close(
            latency_ms=llm_response.trace.latency_ms,
            prompt_version=llm_response.trace.prompt_version,
            fallback_used=llm_response.trace.fallback_used,
            input_tokens=llm_response.trace.input_tokens,
            output_tokens=llm_response.trace.output_tokens,
        )
        answer = llm_response.content
        output_findings = self.policy.check_output(answer, high_risk=route.needs_human)
        trace.policy_findings.extend(output_findings)
        return self._finalize(
            trace,
            conversation_id,
            user_id,
            answer,
            retrieval.selected_context[:2],
            handoff_required=route.needs_human or bool(plan.handoff_reason),
            handoff_reason=plan.handoff_reason,
        )

    def _compose_answer(self, user_text: str, goal: str, target: RouteTarget, tools: list[ToolResult], citations) -> str:
        successful = {tool.name: tool for tool in tools if tool.status == ToolStatus.success}
        failed = [tool for tool in tools if tool.status == ToolStatus.failed]
        customer = successful.get("crm.get_customer")
        customer_name = customer.data.get("name") if customer and customer.data else "你好"
        prefix = f"{customer_name}，" if customer_name != "你好" else "你好，"
        if failed:
            first = failed[0]
            return (
                f"{prefix}我尝试调用 {first.name} 时遇到 {first.error_code}。"
                "我不会编造订单、物流或退款结果；你可以确认账号和订单归属，或转人工处理。"
            )

        if target == RouteTarget.order_agent:
            order = successful.get("order.get")
            if not order:
                search = successful.get("order.search")
                if search and search.data and search.data.get("orders"):
                    first = search.data["orders"][0]
                    return (
                        f"{prefix}我找到了最近的订单 {first['order_id']}（{first['product']}）。"
                        "为了避免处理错订单，请你确认要处理的是这个订单，或直接发我订单号。"
                    )
                return f"{prefix}我需要订单号才能继续查询。你可以发送类似 A1001 的订单号。"
            data = order.data or {}
            source = citations[0].title if citations else "知识库政策"
            if "退" in user_text or "坏" in user_text or "质量" in user_text:
                ticket = successful.get("ticket.create")
                ticket_text = f"我也创建了售后工单 {ticket.data['ticket_id']}，" if ticket and ticket.data else ""
                return (
                    f"{prefix}我查到订单 {data['order_id']} 是 {data['product']}，当前状态为 {data['status']}。"
                    f"根据《{source}》，质量问题在签收后 30 天内可以申请退换货。"
                    f"{ticket_text}我不会直接承诺退款金额；下一步会由专员核验照片和签收时间。"
                )
            logistics_id = data.get("logistics_id")
            shipping = successful.get("shipping.track")
            if logistics_id and shipping and shipping.data:
                return (
                    f"{prefix}订单 {data['order_id']} 的物流单号是 {logistics_id}，"
                    f"最新节点：{shipping.data['latest_event']}，预计 {shipping.data['eta']} 前送达。"
                )
            return f"{prefix}订单 {data['order_id']} 当前状态为 {data['status']}。"

        if target == RouteTarget.billing_agent:
            return (
                f"{prefix}发票或账单问题需要先核对订单和企业信息。"
                "如果是抬头或税号错误，我会建议创建发票修改工单；通常电子发票会在付款后 24 小时内开具。"
            )

        if target == RouteTarget.tech_agent:
            source = citations[0].title if citations else "故障排查知识库"
            return (
                f"{prefix}可以先按《{source}》排查：重置蓝牙配对、清洁充电触点、尝试固件升级。"
                "如果仍然无效，再结合订单状态走售后检测，这样不会把可自行恢复的问题误判成退货。"
            )

        if target in {RouteTarget.retention_agent, RouteTarget.safety_agent}:
            ticket = successful.get("ticket.create")
            ticket_text = f"我已经创建工单 {ticket.data['ticket_id']}。" if ticket and ticket.data else "我会把这段情况转交人工。"
            return (
                f"{prefix}我理解这个问题已经影响到你了。{ticket_text}"
                "接下来会由人工专员复核处理；在复核前，我不会展示或修改敏感账户信息。"
            )

        if not citations:
            return f"{prefix}我还没有找到可引用的知识库依据。你可以补充订单号、产品名或截图，我会按客服流程继续处理。"
        source = citations[0].title
        return f"{prefix}我参考了《{source}》。你的问题可以继续补充订单号、产品名或截图，我会按客服流程继续处理。"

    def _finalize(
        self,
        trace: AgentRunTrace,
        conversation_id: str,
        user_id: str,
        content: str,
        citations,
        handoff_required: bool,
        handoff_reason: str | None = None,
    ) -> AgentResponse:
        trace.finish("completed")
        message = Message(
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role=Role.assistant,
            content=content,
            metadata={"handoff_required": handoff_required, "handoff_reason": handoff_reason},
        )
        self.memory.add_message(message)
        if self.event_store:
            self.event_store.append_message(message)
            self.event_store.append_agent_run(trace)
        response = AgentResponse(
            message=message,
            trace=trace,
            citations=citations,
            handoff_required=handoff_required,
            handoff_reason=handoff_reason,
        )
        if self.monitor:
            monitor_event = self.monitor.review(response)
            if self.event_store:
                self.event_store.append_monitor_event(monitor_event, tenant_id=self.tenant_id)
        return response

    def hydrate_memory_from_events(
        self,
        conversation_id: str,
        user_id: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if conversation_id in self.memory.states:
            state = self.memory.states[conversation_id]
            if state.tenant_id != self.tenant_id or state.user_id != user_id:
                raise PermissionError("Conversation belongs to a different tenant or user")
            return {"hydrate_status": "already_loaded", "event_count": 0, "replayed_message_count": 0}
        if not self.event_store:
            return {"hydrate_status": "no_event_store", "event_count": 0, "replayed_message_count": 0}

        events = self.event_store.list_conversation_memory_events(
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            limit=limit,
        )
        if not events:
            return {"hydrate_status": "not_found", "event_count": 0, "replayed_message_count": 0}

        replay = replay_conversation_memory(events)
        if replay.state.tenant_id != self.tenant_id or replay.state.user_id != user_id:
            raise PermissionError("Conversation belongs to a different tenant or user")
        self.memory.states[conversation_id] = replay.state
        return {
            "hydrate_status": "hydrated",
            "event_count": replay.event_count,
            "replayed_message_count": replay.replayed_message_count,
            "replayed_run_count": replay.replayed_run_count,
        }

    def _max_risk(self, findings) -> RiskLevel:
        order = {RiskLevel.low: 0, RiskLevel.medium: 1, RiskLevel.high: 2, RiskLevel.critical: 3}
        risk = RiskLevel.low
        for finding in findings:
            if order[finding.risk_level] > order[risk]:
                risk = finding.risk_level
        return risk

    def _stable_idempotency_key(
        self,
        conversation_id: str,
        user_id: str,
        tool_name: str,
        arguments: dict,
    ) -> str | None:
        if not any(part in tool_name for part in ["create", "cancel", "update", "add", "upsert"]):
            return None
        payload = json.dumps(
            {
                "tenant_id": self.tenant_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "tool_name": tool_name,
                "arguments": arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Role(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class IntentType(str, Enum):
    order_status = "order_status"
    refund_or_return = "refund_or_return"
    billing = "billing"
    technical_issue = "technical_issue"
    complaint = "complaint"
    account_security = "account_security"
    general_question = "general_question"
    unknown = "unknown"


class RouteTarget(str, Enum):
    order_agent = "order_agent"
    billing_agent = "billing_agent"
    tech_agent = "tech_agent"
    retention_agent = "retention_agent"
    safety_agent = "safety_agent"
    general_agent = "general_agent"
    human = "human"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ToolStatus(str, Enum):
    success = "success"
    failed = "failed"
    skipped = "skipped"


class Message(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    tenant_id: str
    conversation_id: str
    user_id: str
    role: Role
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntentResult(BaseModel):
    primary: IntentType
    confidence: float = Field(ge=0, le=1)
    secondary: list[IntentType] = Field(default_factory=list)
    entities: dict[str, str] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    sentiment: Literal["calm", "confused", "frustrated", "angry"] = "calm"
    urgency: Literal["normal", "urgent"] = "normal"
    rationale: str = ""


class PolicyFinding(BaseModel):
    code: str
    risk_level: RiskLevel
    message: str
    should_block: bool = False
    should_escalate: bool = False


class RouteDecision(BaseModel):
    target: RouteTarget
    reason: str
    allowed_tools: list[str]
    needs_human: bool = False


class RetrievalHit(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    content: str
    score: float
    source_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalTrace(BaseModel):
    query: str
    rewritten_queries: list[str]
    selected_sources: list[str]
    candidates_by_stage: dict[str, int]
    selected_context: list[RetrievalHit]
    dropped_candidates: list[str] = Field(default_factory=list)


class ToolRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str
    idempotency_key: str | None = None
    requires_confirmation: bool = False


class ToolResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tool"))
    name: str
    status: ToolStatus
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    latency_ms: int = 0


class LLMCallTrace(BaseModel):
    provider: str
    model: str
    prompt_version: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float = 0.0
    fallback_used: bool = False


class AgentPlan(BaseModel):
    target_agent: RouteTarget
    tool_requests: list[ToolRequest] = Field(default_factory=list)
    retrieval_query: str | None = None
    clarification_question: str | None = None
    handoff_reason: str | None = None
    response_goal: str


class TraceSpan(BaseModel):
    name: str
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    status: Literal["ok", "error"] = "ok"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def close(self, status: Literal["ok", "error"] = "ok", **metadata: Any) -> None:
        self.ended_at = utc_now()
        self.status = status
        self.metadata.update(metadata)


class AgentRunTrace(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    tenant_id: str
    conversation_id: str
    user_id: str
    agent_version: str = "agent_2026_07_lab"
    intent: IntentResult | None = None
    route: RouteDecision | None = None
    retrieval: RetrievalTrace | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    llm_calls: list[LLMCallTrace] = Field(default_factory=list)
    policy_findings: list[PolicyFinding] = Field(default_factory=list)
    spans: list[TraceSpan] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    status: Literal["running", "completed", "failed"] = "running"

    def start_span(self, name: str, **metadata: Any) -> TraceSpan:
        span = TraceSpan(name=name, metadata=metadata)
        self.spans.append(span)
        return span

    def finish(self, status: Literal["completed", "failed"] = "completed") -> None:
        self.status = status
        self.completed_at = utc_now()


class AgentResponse(BaseModel):
    message: Message
    trace: AgentRunTrace
    citations: list[RetrievalHit] = Field(default_factory=list)
    handoff_required: bool = False
    handoff_reason: str | None = None


class ConversationState(BaseModel):
    tenant_id: str
    conversation_id: str
    user_id: str
    messages: list[Message] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    working_summary: str = ""
    open_questions: list[str] = Field(default_factory=list)
    last_intent: IntentType | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class MonitorEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mon"))
    conversation_id: str
    run_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    agent_version: str
    user_intent: IntentType
    risk_level: RiskLevel
    grounded: bool
    policy_compliant: bool
    pii_leak: bool = False
    needs_human_review: bool = False
    failure_types: list[str] = Field(default_factory=list)
    summary: str


class EvalExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: IntentType | None = None
    route_target: RouteTarget | None = None
    route_needs_human: bool | None = None
    required_tools: list[str] = Field(default_factory=list)
    required_allowed_tools: list[str] = Field(default_factory=list)
    forbidden_allowed_tools: list[str] = Field(default_factory=list)
    required_error_codes: list[str] = Field(default_factory=list)
    required_policy_codes: list[str] = Field(default_factory=list)
    forbidden_policy_codes: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    escalation: bool | None = None
    policy_refs: list[str] = Field(default_factory=list)


ToolFaultErrorCode = Literal[
    "VALIDATION_ERROR",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "NOT_FOUND",
    "CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "RATE_LIMITED",
    "TIMEOUT",
    "UPSTREAM_UNAVAILABLE",
    "UPSTREAM_ERROR",
    "INTERNAL_ERROR",
    "TOOL_NOT_ALLOWED",
    "TOOL_NOT_FOUND",
]


class EvalToolFault(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    error_code: ToolFaultErrorCode
    message: str = "Injected eval tool fault."
    retryable: bool = False
    delay_ms: int = Field(default=0, ge=0, le=5000)
    times: int = Field(default=1, ge=1, le=10)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    scenario: str
    locale: str = "zh-CN"
    user_id: str = "user_demo"
    turns: list[dict[str, str]]
    expected: EvalExpectation
    tool_faults: list[EvalToolFault] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class EvalCaseResult(BaseModel):
    case_id: str
    passed: bool
    score: float
    failures: list[str] = Field(default_factory=list)
    observed_intent: IntentType
    observed_route: RouteTarget | None = None
    observed_route_needs_human: bool | None = None
    observed_allowed_tools: list[str] = Field(default_factory=list)
    observed_tools: list[str]
    observed_error_codes: list[str] = Field(default_factory=list)
    observed_policy_codes: list[str] = Field(default_factory=list)
    answer: str


class EvalReport(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    total: int
    passed: int
    score: float
    results: list[EvalCaseResult]

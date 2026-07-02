from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from support_agent_lab.api.auth import (
    RequestActor,
    get_request_actor,
    require_admin,
    require_same_user,
    require_scope,
)
from support_agent_lab.bootstrap import AppContainer, create_container
from support_agent_lab.api.readiness import ReadinessResponse, check_readiness
from support_agent_lab.api.request_signature import (
    RequestSignatureError,
    read_body_and_restore,
    request_signature_required,
    reserve_request_nonce,
    verify_request_signature,
)
from support_agent_lab.memory.event_store import StoredEvent
from support_agent_lab.memory.replay import MemoryReplayResult, replay_conversation_memory
from support_agent_lab.models import (
    AgentResponse,
    AgentRunTrace,
    Message,
    MonitorAlertStatus,
    MonitorAlertTriageEvent,
    MonitorEvent,
    new_id,
)
from support_agent_lab.monitoring.monitor import MonitorSummary, summarize_monitor_events
from support_agent_lab.tools.registry import ToolAuditRecord
from support_agent_lab.config import get_settings


class CreateSessionRequest(BaseModel):
    user_id: str | None = None


class CreateSessionResponse(BaseModel):
    conversation_id: str
    user_id: str


class ChatMessageRequest(BaseModel):
    conversation_id: str
    user_id: str | None = None
    content: str = Field(min_length=1, max_length=5000)


class ChatMessageResponse(BaseModel):
    message: Message
    trace_id: str
    handoff_required: bool
    citations: list[dict]


class TriageMonitorAlertRequest(BaseModel):
    status: MonitorAlertStatus | None = None
    assignee_user_id: str | None = Field(default=None, max_length=128)
    note: str = Field(default="", max_length=1000)


class IncidentRunBundle(BaseModel):
    run: AgentRunTrace
    run_source: str
    monitor_events: list[MonitorEvent]
    tool_audit_records: list[ToolAuditRecord]
    memory_replay: MemoryReplayResult | None = None


container = create_container()


def get_container() -> AppContainer:
    return container


def create_app() -> FastAPI:
    app = FastAPI(
        title="Production Support Agent Lab",
        version="0.1.0",
        description="A production-shaped customer support agent for learning agent engineering.",
    )

    @app.middleware("http")
    async def production_request_signature_middleware(request: Request, call_next):
        settings = get_settings()
        if request_signature_required(settings, request.url.path):
            body = await read_body_and_restore(request)
            try:
                verified = verify_request_signature(settings=settings, request=request, body=body)
                reserve_request_nonce(settings, verified)
            except RequestSignatureError as exc:
                return JSONResponse(status_code=401, content={"detail": str(exc)})
        return await call_next(request)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/ready")
    async def ready(
        deps: Annotated[AppContainer, Depends(get_container)],
        deep: Annotated[bool | None, Query()] = None,
    ) -> ReadinessResponse:
        report = await check_readiness(deps, deep=deep)
        if report.status != "ok":
            return JSONResponse(status_code=503, content=report.model_dump(mode="json"))
        return report

    @app.post("/api/v1/chat/sessions")
    def create_session(
        body: CreateSessionRequest,
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> CreateSessionResponse:
        require_same_user(body.user_id, actor)
        return CreateSessionResponse(conversation_id=new_id("conv"), user_id=actor.user_id)

    @app.post("/api/v1/chat/messages")
    async def chat(
        body: ChatMessageRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> ChatMessageResponse:
        require_same_user(body.user_id, actor)
        existing = deps.memory.states.get(body.conversation_id)
        if existing:
            require_same_user(existing.user_id, actor)
        try:
            response = await deps.orchestrator.handle_message(
                conversation_id=body.conversation_id,
                user_id=actor.user_id,
                text=body.content,
                actor_scopes=actor.scopes,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return ChatMessageResponse(
            message=response.message,
            trace_id=response.trace.id,
            handoff_required=response.handoff_required,
            citations=[hit.model_dump(mode="json") for hit in response.citations],
        )

    @app.get("/api/v1/conversations/{conversation_id}/messages")
    def list_messages(
        conversation_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> list[Message]:
        if conversation_id not in deps.memory.states:
            try:
                hydrate = deps.orchestrator.hydrate_memory_from_events(conversation_id, actor.user_id)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            if hydrate["hydrate_status"] in {"no_event_store", "not_found"}:
                raise HTTPException(status_code=404, detail="Conversation not found")
        state = deps.memory.states[conversation_id]
        require_same_user(state.user_id, actor)
        return state.messages

    @app.get("/api/v1/agent/runs/{run_id}")
    def get_run(
        run_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ):
        if run_id not in deps.orchestrator.runs:
            raise HTTPException(status_code=404, detail="Run not found")
        run = deps.orchestrator.runs[run_id]
        require_same_user(run.user_id, actor)
        return run

    @app.get("/api/v1/admin/tools")
    def list_tools(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ):
        require_admin(actor)
        require_scope(actor, "admin:read")
        return deps.tools.registry.list_tools()

    @app.get("/api/v1/admin/tools/audit")
    def list_tool_audit_records(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        tool_name: Annotated[str | None, Query()] = None,
        actor_user_id: Annotated[str | None, Query()] = None,
        trace_id: Annotated[str | None, Query()] = None,
        request_id: Annotated[str | None, Query()] = None,
        status: Annotated[str | None, Query(pattern="^(success|failed|skipped)$")] = None,
        error_code: Annotated[str | None, Query()] = None,
        replayed: Annotated[bool | None, Query()] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    ) -> list[ToolAuditRecord]:
        require_admin(actor)
        require_scope(actor, "audit:read")
        if not deps.event_store:
            return []
        return deps.event_store.list_tool_audit_records(
            tenant_id=deps.settings.app_tenant_id,
            tool_name=tool_name,
            actor_user_id=actor_user_id,
            trace_id=trace_id,
            request_id=request_id,
            status=status,
            error_code=error_code,
            replayed=replayed,
            created_after=created_after.isoformat() if created_after else None,
            created_before=created_before.isoformat() if created_before else None,
            limit=limit,
            order=order,
        )

    @app.get("/api/v1/admin/monitor/events")
    def monitor_events(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[str, Query(pattern="^(live|event_store)$")] = "live",
        conversation_id: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[MonitorEvent]:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        if source == "event_store":
            if not deps.event_store:
                raise HTTPException(status_code=404, detail="Event store is not configured")
            return deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                conversation_id=conversation_id,
                limit=limit,
            )
        if conversation_id:
            return [event for event in deps.monitor.events if event.conversation_id == conversation_id][:limit]
        return deps.monitor.events[:limit]

    @app.get("/api/v1/admin/incidents/runs/{run_id}")
    def incident_run_bundle(
        run_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        include_memory: Annotated[bool, Query()] = True,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> IncidentRunBundle:
        require_admin(actor)
        require_scope(actor, "events:read")
        require_scope(actor, "monitor:read")
        require_scope(actor, "audit:read")
        if include_memory:
            require_scope(actor, "memory:replay")

        run_source = "live"
        run = deps.orchestrator.runs.get(run_id)
        if run is None and deps.event_store:
            run = deps.event_store.get_agent_run_trace(
                run_id,
                tenant_id=deps.settings.app_tenant_id,
                limit=limit,
            )
            run_source = "event_store" if run else run_source
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        monitor_source = (
            deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                run_id=run_id,
                limit=limit,
            )
            if deps.event_store
            else deps.monitor.events[:limit]
        )
        monitor_events = [event for event in monitor_source if event.run_id == run_id]
        tool_audit_records = (
            deps.event_store.list_tool_audit_records(
                tenant_id=deps.settings.app_tenant_id,
                trace_id=run_id,
                limit=limit,
            )
            if deps.event_store
            else []
        )
        memory_replay = None
        if include_memory and deps.event_store:
            events = deps.event_store.list_events(
                tenant_id=deps.settings.app_tenant_id,
                conversation_id=run.conversation_id,
                limit=limit,
            )
            if events:
                try:
                    memory_replay = replay_conversation_memory(events)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

        return IncidentRunBundle(
            run=run,
            run_source=run_source,
            monitor_events=monitor_events,
            tool_audit_records=tool_audit_records,
            memory_replay=memory_replay,
        )

    @app.get("/api/v1/admin/monitor/summary")
    def monitor_summary(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        source: Annotated[str, Query(pattern="^(live|event_store)$")] = "live",
        conversation_id: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 500,
    ) -> MonitorSummary:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        if source == "event_store":
            if not deps.event_store:
                raise HTTPException(status_code=404, detail="Event store is not configured")
            events = deps.event_store.list_monitor_events(
                tenant_id=deps.settings.app_tenant_id,
                conversation_id=conversation_id,
                limit=limit,
            )
            triage_events = deps.event_store.list_monitor_alert_triage_events(
                tenant_id=deps.settings.app_tenant_id,
                limit=limit,
            )
            return summarize_monitor_events(events, triage_events=triage_events)
        if conversation_id:
            return summarize_monitor_events(
                [event for event in deps.monitor.events if event.conversation_id == conversation_id][:limit]
            )
        return summarize_monitor_events(deps.monitor.events[:limit])

    @app.get("/api/v1/admin/monitor/alerts/{alert_key}/triage")
    def monitor_alert_triage_events(
        alert_key: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[MonitorAlertTriageEvent]:
        require_admin(actor)
        require_scope(actor, "monitor:read")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        return deps.event_store.list_monitor_alert_triage_events(
            tenant_id=deps.settings.app_tenant_id,
            alert_key=alert_key,
            limit=limit,
        )

    @app.post("/api/v1/admin/monitor/alerts/{alert_key}/triage")
    def triage_monitor_alert(
        alert_key: str,
        body: TriageMonitorAlertRequest,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> MonitorAlertTriageEvent:
        require_admin(actor)
        require_scope(actor, "monitor:write")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        note = body.note.strip()
        if body.status is None and body.assignee_user_id is None and not note:
            raise HTTPException(status_code=400, detail="At least one triage field is required")
        events = deps.event_store.list_monitor_events(
            tenant_id=deps.settings.app_tenant_id,
            limit=500,
        )
        summary = summarize_monitor_events(events)
        if alert_key not in {alert.key for alert in summary.alerts}:
            raise HTTPException(status_code=404, detail="Monitor alert not found")
        triage_event = MonitorAlertTriageEvent(
            alert_key=alert_key,
            status=body.status,
            assignee_user_id=body.assignee_user_id,
            actor_user_id=actor.user_id,
            note=note,
        )
        deps.event_store.append_monitor_alert_triage(
            triage_event,
            tenant_id=deps.settings.app_tenant_id,
        )
        return triage_event

    @app.post("/api/v1/admin/evals/golden")
    async def run_golden_eval(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ):
        require_admin(actor)
        require_scope(actor, "eval:run")
        if deps.settings.is_production:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The bundled golden eval uses lab fixtures and is disabled in production. "
                    "Run offline evals in CI or a staging sandbox instead."
                ),
            )
        from support_agent_lab.evals.runner import load_cases, run_cases

        cases = load_cases("examples/evals/golden_core.json")
        return await run_cases(cases, deps.orchestrator)

    @app.get("/api/v1/admin/events")
    def list_events(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        conversation_id: Annotated[str | None, Query()] = None,
        event_type: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[StoredEvent]:
        require_admin(actor)
        require_scope(actor, "events:read")
        if not deps.event_store:
            return []
        return deps.event_store.list_events(
            tenant_id=deps.settings.app_tenant_id,
            conversation_id=conversation_id,
            event_type=event_type,
            limit=limit,
        )

    @app.get("/api/v1/admin/conversations/{conversation_id}/memory/replay")
    def replay_memory(
        conversation_id: str,
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> MemoryReplayResult:
        require_admin(actor)
        require_scope(actor, "memory:replay")
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        events = deps.event_store.list_events(
            tenant_id=deps.settings.app_tenant_id,
            conversation_id=conversation_id,
            limit=limit,
        )
        if not events:
            raise HTTPException(status_code=404, detail="Conversation events not found")
        try:
            return replay_conversation_memory(events)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()

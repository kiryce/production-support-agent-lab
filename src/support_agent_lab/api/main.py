from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from support_agent_lab.api.auth import RequestActor, get_request_actor, require_admin, require_same_user
from support_agent_lab.bootstrap import AppContainer, create_container
from support_agent_lab.api.readiness import ReadinessResponse, check_readiness
from support_agent_lab.memory.event_store import StoredEvent
from support_agent_lab.memory.replay import MemoryReplayResult, replay_conversation_memory
from support_agent_lab.models import AgentResponse, Message, MonitorEvent, new_id
from support_agent_lab.monitoring.monitor import MonitorSummary


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


container = create_container()


def get_container() -> AppContainer:
    return container


def create_app() -> FastAPI:
    app = FastAPI(
        title="Production Support Agent Lab",
        version="0.1.0",
        description="A production-shaped customer support agent for learning agent engineering.",
    )

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
        response = await deps.orchestrator.handle_message(
            conversation_id=body.conversation_id,
            user_id=actor.user_id,
            text=body.content,
            actor_scopes=actor.scopes,
        )
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
        return deps.tools.registry.list_tools()

    @app.get("/api/v1/admin/monitor/events")
    def monitor_events(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> list[MonitorEvent]:
        require_admin(actor)
        return deps.monitor.events

    @app.get("/api/v1/admin/monitor/summary")
    def monitor_summary(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ) -> MonitorSummary:
        require_admin(actor)
        return deps.monitor.summarize()

    @app.post("/api/v1/admin/evals/golden")
    async def run_golden_eval(
        deps: Annotated[AppContainer, Depends(get_container)],
        actor: Annotated[RequestActor, Depends(get_request_actor)],
    ):
        require_admin(actor)
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
        if not deps.event_store:
            return []
        return deps.event_store.list_events(
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
        if not deps.event_store:
            raise HTTPException(status_code=404, detail="Event store is not configured")
        events = deps.event_store.list_events(conversation_id=conversation_id, limit=limit)
        if not events:
            raise HTTPException(status_code=404, detail="Conversation events not found")
        try:
            return replay_conversation_memory(events)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()

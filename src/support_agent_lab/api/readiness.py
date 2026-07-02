from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from support_agent_lab.bootstrap import AppContainer
from support_agent_lab.memory.http_knowledge import HTTPKnowledgeIndex


CheckStatus = Literal["ok", "failed", "skipped"]
OverallStatus = Literal["ok", "not_ready"]


class ReadinessCheck(BaseModel):
    name: str
    status: CheckStatus
    detail: str = ""


class ReadinessResponse(BaseModel):
    status: OverallStatus
    environment: str
    deep: bool
    checks: list[ReadinessCheck] = Field(default_factory=list)


async def check_readiness(container: AppContainer, deep: bool | None = None) -> ReadinessResponse:
    use_deep_checks = _use_deep_checks(container, deep)
    checks = [
        _check_config(container),
        _check_event_store(container),
    ]
    if use_deep_checks:
        checks.extend(
            [
                await _check_llm(container),
                await _check_business_api(container),
                await _check_knowledge_api(container),
            ]
        )
    else:
        checks.extend(
            [
                ReadinessCheck(name="llm", status="skipped", detail="deep checks disabled"),
                ReadinessCheck(name="business_api", status="skipped", detail="deep checks disabled"),
                ReadinessCheck(name="knowledge_api", status="skipped", detail="deep checks disabled"),
            ]
        )
    overall: OverallStatus = "ok" if all(check.status != "failed" for check in checks) else "not_ready"
    return ReadinessResponse(
        status=overall,
        environment=container.settings.app_env,
        deep=use_deep_checks,
        checks=checks,
    )


def _use_deep_checks(container: AppContainer, deep: bool | None) -> bool:
    if deep is not None:
        return deep
    if container.settings.app_readiness_deep_checks is not None:
        return container.settings.app_readiness_deep_checks
    return container.settings.is_production


def _check_config(container: AppContainer) -> ReadinessCheck:
    try:
        container.settings.validate_production_ready()
    except Exception as exc:  # pragma: no cover - guarded at startup, kept for explicit readiness.
        return ReadinessCheck(name="config", status="failed", detail=str(exc))
    return ReadinessCheck(name="config", status="ok", detail="settings validated")


def _check_event_store(container: AppContainer) -> ReadinessCheck:
    if not container.event_store:
        if container.settings.is_production:
            return ReadinessCheck(name="event_store", status="failed", detail="event store is required in production")
        return ReadinessCheck(name="event_store", status="skipped", detail="event store not configured")
    try:
        container.event_store.health_check()
    except Exception as exc:
        return ReadinessCheck(name="event_store", status="failed", detail=str(exc))
    return ReadinessCheck(name="event_store", status="ok", detail="sqlite schema and write probe passed")


async def _check_llm(container: AppContainer) -> ReadinessCheck:
    try:
        await container.llm.health_check()
    except Exception as exc:
        return ReadinessCheck(name="llm", status="failed", detail=str(exc))
    return ReadinessCheck(
        name="llm",
        status="ok",
        detail=f"{container.llm.provider.provider}:{container.llm.provider.model}",
    )


async def _check_business_api(container: AppContainer) -> ReadinessCheck:
    if not container.business_client:
        if container.settings.is_production:
            return ReadinessCheck(name="business_api", status="failed", detail="business client missing")
        return ReadinessCheck(name="business_api", status="skipped", detail="local mode uses in-process fixtures")
    try:
        await container.business_client.health_check(container.settings.app_tenant_id)
    except Exception as exc:
        return ReadinessCheck(name="business_api", status="failed", detail=str(exc))
    return ReadinessCheck(name="business_api", status="ok", detail="business /health reachable")


async def _check_knowledge_api(container: AppContainer) -> ReadinessCheck:
    if not isinstance(container.knowledge, HTTPKnowledgeIndex):
        if container.settings.is_production:
            return ReadinessCheck(name="knowledge_api", status="failed", detail="HTTP knowledge adapter missing")
        return ReadinessCheck(name="knowledge_api", status="skipped", detail="local mode uses in-process knowledge")
    try:
        await container.knowledge.health_check()
    except Exception as exc:
        return ReadinessCheck(name="knowledge_api", status="failed", detail=str(exc))
    return ReadinessCheck(name="knowledge_api", status="ok", detail="knowledge /health reachable")

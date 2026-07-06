from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from support_agent_lab.bootstrap import AppContainer
from support_agent_lab.memory.http_knowledge import HTTPKnowledgeIndex
from support_agent_lab.memory.sqlite_knowledge import SQLiteKnowledgeIndex


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
        _check_event_store_backup_dir(container),
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


def _check_event_store_backup_dir(container: AppContainer) -> ReadinessCheck:
    if not container.settings.is_production:
        return ReadinessCheck(
            name="event_store_backup_dir",
            status="skipped",
            detail="production-only backup directory probe skipped",
        )
    if not container.event_store:
        return ReadinessCheck(
            name="event_store_backup_dir",
            status="skipped",
            detail="event store is not configured",
        )
    try:
        backup_dir = Path(container.settings.app_event_store_backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        if not backup_dir.is_dir():
            raise RuntimeError("configured path is not a directory")
        probe_path = backup_dir / f".readiness-probe-{uuid4().hex}.tmp"
        try:
            probe_path.write_text("ok", encoding="utf-8")
            if probe_path.read_text(encoding="utf-8") != "ok":
                raise RuntimeError("write probe could not be read back")
        finally:
            probe_path.unlink(missing_ok=True)
    except Exception as exc:
        return ReadinessCheck(
            name="event_store_backup_dir",
            status="failed",
            detail=f"backup directory probe failed: {type(exc).__name__}",
        )
    return ReadinessCheck(
        name="event_store_backup_dir",
        status="ok",
        detail="configured backup directory write probe passed",
    )


async def _check_llm(container: AppContainer) -> ReadinessCheck:
    try:
        await container.llm.health_check()
    except Exception as exc:
        return ReadinessCheck(name="llm", status="failed", detail=f"{exc}; {_llm_circuit_detail(container)}")
    return ReadinessCheck(
        name="llm",
        status="ok",
        detail=f"{container.llm.provider.provider}:{container.llm.provider.model}; {_llm_circuit_detail(container)}",
    )


def _llm_circuit_detail(container: AppContainer) -> str:
    if not hasattr(container.llm, "circuit_status"):
        return "circuit=missing"
    status = container.llm.circuit_status()
    return (
        f"circuit={status['state']}, "
        f"failures={status['failure_count']}/{status['failure_threshold']}, "
        f"retry_attempts={status['retry_attempts']}, "
        f"timeout_ms={status['timeout_ms']}"
    )


async def _check_business_api(container: AppContainer) -> ReadinessCheck:
    if not container.business_client:
        if container.settings.is_production:
            return ReadinessCheck(name="business_api", status="failed", detail="business client missing")
        return ReadinessCheck(name="business_api", status="skipped", detail="local mode uses in-process fixtures")
    try:
        await container.business_client.health_check(container.settings.app_tenant_id)
    except Exception as exc:
        return ReadinessCheck(
            name="business_api",
            status="failed",
            detail=f"{exc}; {_business_circuit_detail(container)}",
        )
    return ReadinessCheck(
        name="business_api",
        status="ok",
        detail=f"business /health reachable; {_business_circuit_detail(container)}",
    )


def _business_circuit_detail(container: AppContainer) -> str:
    if not container.business_client:
        return "circuit=missing"
    status = container.business_client.circuit_status()
    return (
        f"circuit={status['state']}, "
        f"failures={status['failure_count']}/{status['failure_threshold']}, "
        f"retry_attempts={status['retry_attempts']}"
    )


async def _check_knowledge_api(container: AppContainer) -> ReadinessCheck:
    if isinstance(container.knowledge, SQLiteKnowledgeIndex):
        try:
            await container.knowledge.health_check(
                min_documents=container.settings.app_knowledge_min_ready_documents,
            )
        except Exception as exc:
            return ReadinessCheck(
                name="knowledge_api",
                status="failed",
                detail=f"{exc}; {_knowledge_circuit_detail(container)}",
            )
        return ReadinessCheck(
            name="knowledge_api",
            status="ok",
            detail=f"sqlite knowledge index ready; {_knowledge_circuit_detail(container)}",
        )
    if not isinstance(container.knowledge, HTTPKnowledgeIndex):
        if container.settings.is_production:
            return ReadinessCheck(name="knowledge_api", status="failed", detail="HTTP knowledge adapter missing")
        return ReadinessCheck(name="knowledge_api", status="skipped", detail="local mode uses in-process knowledge")
    try:
        await container.knowledge.health_check()
    except Exception as exc:
        return ReadinessCheck(
            name="knowledge_api",
            status="failed",
            detail=f"{exc}; {_knowledge_circuit_detail(container)}",
        )
    return ReadinessCheck(
        name="knowledge_api",
        status="ok",
        detail=f"knowledge /health reachable; {_knowledge_circuit_detail(container)}",
    )


def _knowledge_circuit_detail(container: AppContainer) -> str:
    if isinstance(container.knowledge, SQLiteKnowledgeIndex):
        summary = container.knowledge.summary()
        return (
            f"backend=sqlite, documents={summary.document_count}, "
            f"chunks={summary.chunk_count}, fts_enabled={summary.fts_enabled}"
        )
    if not isinstance(container.knowledge, HTTPKnowledgeIndex):
        return "backend=memory"
    status = container.knowledge.circuit_status()
    return (
        "backend=http, "
        f"circuit={status['state']}, "
        f"failures={status['failure_count']}/{status['failure_threshold']}, "
        f"retry_attempts={status['retry_attempts']}"
    )

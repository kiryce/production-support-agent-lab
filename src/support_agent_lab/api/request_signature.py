from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hmac import compare_digest

from fastapi import Request

from support_agent_lab.config import Settings
from support_agent_lab.memory.event_store import SQLiteEventStore
from support_agent_lab.security.actor_signature import (
    body_sha256,
    canonical_request_claims,
    sign_request_claims,
)


REQUEST_SIGNATURE_EXEMPT_PATHS = {
    "/api/v1/health",
    "/api/v1/ready",
    "/docs",
    "/openapi.json",
}


class RequestSignatureError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedRequestSignature:
    tenant_id: str
    actor_user_id: str
    nonce: str
    request_hash: str
    expires_at: str


def request_signature_required(settings: Settings, path: str) -> bool:
    if path in REQUEST_SIGNATURE_EXEMPT_PATHS:
        return False
    return settings.require_request_signature


async def read_body_and_restore(request: Request) -> bytes:
    body = await request.body()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive  # FastAPI/Starlette caches body, but restore keeps downstream parsers safe.
    return body


def verify_request_signature(
    *,
    settings: Settings,
    request: Request,
    body: bytes,
) -> VerifiedRequestSignature:
    headers = request.headers
    user_id = _required_header(headers.get("x-actor-user-id"), "X-Actor-User-Id")
    roles_header = _required_header(headers.get("x-actor-roles"), "X-Actor-Roles")
    scopes_header = _required_header(headers.get("x-actor-scopes"), "X-Actor-Scopes")
    timestamp = _required_header(headers.get("x-actor-timestamp"), "X-Actor-Timestamp")
    nonce = _required_header(headers.get("x-request-nonce"), "X-Request-Nonce")
    provided_body_hash = _required_header(headers.get("x-request-body-sha256"), "X-Request-Body-SHA256")
    provided_signature = _required_header(headers.get("x-request-signature"), "X-Request-Signature")
    if not settings.app_actor_signature_secret:
        raise RequestSignatureError("APP_ACTOR_SIGNATURE_SECRET is required for request signatures.")
    if len(nonce) < 16:
        raise RequestSignatureError("X-Request-Nonce must be at least 16 characters.")

    issued_at = _parse_timestamp(timestamp)
    if abs(time.time() - issued_at) > settings.app_actor_signature_max_age_seconds:
        raise RequestSignatureError("Request signature is expired.")

    actual_body_hash = body_sha256(body)
    if not compare_digest(provided_body_hash, actual_body_hash):
        raise RequestSignatureError("X-Request-Body-SHA256 does not match the request body.")

    method = request.method.upper()
    path = _path_with_query(request)
    expected = sign_request_claims(
        secret=settings.app_actor_signature_secret,
        tenant_id=settings.app_tenant_id,
        user_id=user_id,
        roles_header=roles_header,
        scopes_header=scopes_header,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=path,
        body_hash=provided_body_hash,
    )
    normalized = provided_signature.removeprefix("sha256=")
    if not compare_digest(normalized, expected):
        raise RequestSignatureError("Request signature is invalid.")

    canonical = canonical_request_claims(
        tenant_id=settings.app_tenant_id,
        user_id=user_id,
        roles_header=roles_header,
        scopes_header=scopes_header,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=path,
        body_hash=provided_body_hash,
    )
    expires_at = datetime.fromtimestamp(issued_at, tz=timezone.utc) + timedelta(
        seconds=settings.app_actor_signature_max_age_seconds
    )
    return VerifiedRequestSignature(
        tenant_id=settings.app_tenant_id,
        actor_user_id=user_id,
        nonce=nonce,
        request_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        expires_at=expires_at.isoformat(),
    )


def reserve_request_nonce(settings: Settings, verified: VerifiedRequestSignature) -> None:
    store = SQLiteEventStore.from_url(settings.app_database_url)
    if store is None:
        raise RequestSignatureError("Request nonce replay defense requires a SQLite event store.")
    reserved = store.reserve_api_request_nonce(
        tenant_id=verified.tenant_id,
        actor_user_id=verified.actor_user_id,
        nonce=verified.nonce,
        request_hash=verified.request_hash,
        expires_at=verified.expires_at,
    )
    if not reserved:
        raise RequestSignatureError("X-Request-Nonce has already been used.")


def _path_with_query(request: Request) -> str:
    query = request.url.query
    return f"{request.url.path}?{query}" if query else request.url.path


def _required_header(value: str | None, name: str) -> str:
    if not value:
        raise RequestSignatureError(f"Production requests must include {name}.")
    return value


def _parse_timestamp(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise RequestSignatureError("X-Actor-Timestamp must be a Unix timestamp.") from exc

from __future__ import annotations

import hashlib
import time
from hmac import compare_digest, new as hmac_new
from typing import Annotated

from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from support_agent_lab.config import get_settings

DEFAULT_USER_SCOPES = [
    "crm:read",
    "order:read",
    "shipping:read",
    "ticket:write",
    "kb:read",
]
DEFAULT_ADMIN_SCOPES = [
    *DEFAULT_USER_SCOPES,
    "admin:read",
    "audit:read",
    "events:read",
    "eval:run",
    "memory:replay",
    "monitor:read",
    "monitor:write",
]


class RequestActor(BaseModel):
    user_id: str
    roles: list[str]
    scopes: list[str] = []

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles


def get_request_actor(
    x_demo_user: Annotated[str | None, Header(alias="X-Demo-User")] = None,
    x_demo_role: Annotated[str | None, Header(alias="X-Demo-Role")] = None,
    x_internal_auth: Annotated[str | None, Header(alias="X-Internal-Auth")] = None,
    x_actor_user_id: Annotated[str | None, Header(alias="X-Actor-User-Id")] = None,
    x_actor_roles: Annotated[str | None, Header(alias="X-Actor-Roles")] = None,
    x_actor_scopes: Annotated[str | None, Header(alias="X-Actor-Scopes")] = None,
    x_actor_timestamp: Annotated[str | None, Header(alias="X-Actor-Timestamp")] = None,
    x_actor_signature: Annotated[str | None, Header(alias="X-Actor-Signature")] = None,
) -> RequestActor:
    settings = get_settings()
    if settings.is_production:
        return _get_production_actor(
            expected_key=settings.app_internal_api_key,
            provided_key=x_internal_auth,
            user_id=x_actor_user_id,
            roles_header=x_actor_roles,
            scopes_header=x_actor_scopes,
            actor_signature_secret=settings.app_actor_signature_secret,
            actor_signature_timestamp=x_actor_timestamp,
            actor_signature=x_actor_signature,
            actor_signature_max_age_seconds=settings.app_actor_signature_max_age_seconds,
            tenant_id=settings.app_tenant_id,
        )
    roles = [role.strip() for role in (x_demo_role or "user").split(",") if role.strip()]
    scopes = DEFAULT_ADMIN_SCOPES if "admin" in roles else DEFAULT_USER_SCOPES
    return RequestActor(user_id=x_demo_user or "user_demo", roles=roles, scopes=scopes)


def _get_production_actor(
    *,
    expected_key: str | None,
    provided_key: str | None,
    user_id: str | None,
    roles_header: str | None,
    scopes_header: str | None = None,
    actor_signature_secret: str | None = None,
    actor_signature_timestamp: str | None = None,
    actor_signature: str | None = None,
    actor_signature_max_age_seconds: int = 300,
    tenant_id: str = "demo_tenant",
) -> RequestActor:
    if not expected_key or not provided_key or not compare_digest(provided_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production requests must be authenticated by the trusted gateway.",
        )
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production requests must include X-Actor-User-Id.",
        )
    if user_id in {"user_demo", "user_guest"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production requests must not use local demo identities.",
        )
    roles = [role.strip() for role in (roles_header or "user").split(",") if role.strip()]
    scopes = [scope.strip() for scope in (scopes_header or "").split(",") if scope.strip()]
    if not scopes:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production requests must include X-Actor-Scopes from the trusted gateway.",
        )
    _verify_actor_signature(
        secret=actor_signature_secret,
        provided_signature=actor_signature,
        timestamp=actor_signature_timestamp,
        user_id=user_id,
        roles_header=roles_header,
        scopes_header=scopes_header,
        tenant_id=tenant_id,
        max_age_seconds=actor_signature_max_age_seconds,
    )
    return RequestActor(user_id=user_id, roles=roles, scopes=scopes)


def sign_actor_claims(
    *,
    secret: str,
    tenant_id: str,
    user_id: str,
    roles_header: str | None,
    scopes_header: str | None,
    timestamp: str,
) -> str:
    canonical = "\n".join(
        [
            "v1",
            tenant_id,
            user_id,
            _canonical_csv(roles_header or "user"),
            _canonical_csv(scopes_header or ""),
            timestamp,
        ]
    )
    return hmac_new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_actor_signature(
    *,
    secret: str | None,
    provided_signature: str | None,
    timestamp: str | None,
    user_id: str,
    roles_header: str | None,
    scopes_header: str | None,
    tenant_id: str,
    max_age_seconds: int,
) -> None:
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production actor claims must be signed by the trusted gateway.",
        )
    if not provided_signature or not timestamp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production requests must include X-Actor-Timestamp and X-Actor-Signature.",
        )
    try:
        issued_at = int(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Actor-Timestamp must be a Unix timestamp.",
        ) from exc
    if abs(time.time() - issued_at) > max_age_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production actor signature is expired.",
        )
    expected = sign_actor_claims(
        secret=secret,
        tenant_id=tenant_id,
        user_id=user_id,
        roles_header=roles_header,
        scopes_header=scopes_header,
        timestamp=timestamp,
    )
    normalized = provided_signature.removeprefix("sha256=")
    if not compare_digest(normalized, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Production actor signature is invalid.",
        )


def _canonical_csv(value: str) -> str:
    return ",".join(item.strip() for item in value.split(",") if item.strip())


def require_same_user(request_user_id: str | None, actor: RequestActor) -> None:
    if request_user_id is None:
        return
    if request_user_id != actor.user_id and not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request user_id must match authenticated actor",
        )


def require_admin(actor: RequestActor) -> None:
    if not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required.",
        )


def require_scope(actor: RequestActor, scope: str) -> None:
    if scope in actor.scopes:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Missing required scope: {scope}",
    )


DemoActor = RequestActor
get_demo_actor = get_request_actor

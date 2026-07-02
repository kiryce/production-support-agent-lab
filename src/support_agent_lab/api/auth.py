from __future__ import annotations

from hmac import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from support_agent_lab.config import get_settings

DEFAULT_USER_SCOPES = ["crm:read", "order:read", "shipping:read", "ticket:write", "kb:read"]


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
) -> RequestActor:
    settings = get_settings()
    if settings.is_production:
        return _get_production_actor(
            expected_key=settings.app_internal_api_key,
            provided_key=x_internal_auth,
            user_id=x_actor_user_id,
            roles_header=x_actor_roles,
            scopes_header=x_actor_scopes,
        )
    roles = [role.strip() for role in (x_demo_role or "user").split(",") if role.strip()]
    return RequestActor(user_id=x_demo_user or "user_demo", roles=roles, scopes=DEFAULT_USER_SCOPES)


def _get_production_actor(
    *,
    expected_key: str | None,
    provided_key: str | None,
    user_id: str | None,
    roles_header: str | None,
    scopes_header: str | None = None,
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
    return RequestActor(user_id=user_id, roles=roles, scopes=scopes)


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


DemoActor = RequestActor
get_demo_actor = get_request_actor

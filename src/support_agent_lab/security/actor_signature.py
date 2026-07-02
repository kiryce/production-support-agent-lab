from __future__ import annotations

import hashlib
import time
from secrets import token_urlsafe
from collections.abc import Sequence
from hmac import new as hmac_new

ACTOR_SIGNATURE_PREFIX = "sha256="
ACTOR_SIGNATURE_VERSION = "v1"
REQUEST_SIGNATURE_VERSION = "v1"


def canonical_csv(value: str | Sequence[str] | None, *, default: str = "") -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = value
    return ",".join(str(item).strip() for item in items if str(item).strip())


def canonical_actor_claims(
    *,
    tenant_id: str,
    user_id: str,
    roles_header: str | Sequence[str] | None,
    scopes_header: str | Sequence[str] | None,
    timestamp: str,
) -> str:
    return "\n".join(
        [
            ACTOR_SIGNATURE_VERSION,
            tenant_id,
            user_id,
            canonical_csv(roles_header, default="user"),
            canonical_csv(scopes_header),
            timestamp,
        ]
    )


def sign_actor_claims(
    *,
    secret: str,
    tenant_id: str,
    user_id: str,
    roles_header: str | Sequence[str] | None,
    scopes_header: str | Sequence[str] | None,
    timestamp: str,
) -> str:
    canonical = canonical_actor_claims(
        tenant_id=tenant_id,
        user_id=user_id,
        roles_header=roles_header,
        scopes_header=scopes_header,
        timestamp=timestamp,
    )
    return hmac_new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def body_sha256(body: bytes | str | None = b"") -> str:
    if body is None:
        body = b""
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def canonical_request_claims(
    *,
    tenant_id: str,
    user_id: str,
    roles_header: str | Sequence[str] | None,
    scopes_header: str | Sequence[str] | None,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body_hash: str,
) -> str:
    return "\n".join(
        [
            REQUEST_SIGNATURE_VERSION,
            tenant_id,
            user_id,
            canonical_csv(roles_header, default="user"),
            canonical_csv(scopes_header),
            timestamp,
            nonce,
            method.upper(),
            path,
            body_hash,
        ]
    )


def sign_request_claims(
    *,
    secret: str,
    tenant_id: str,
    user_id: str,
    roles_header: str | Sequence[str] | None,
    scopes_header: str | Sequence[str] | None,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body_hash: str,
) -> str:
    canonical = canonical_request_claims(
        tenant_id=tenant_id,
        user_id=user_id,
        roles_header=roles_header,
        scopes_header=scopes_header,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=path,
        body_hash=body_hash,
    )
    return hmac_new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def format_actor_signature(digest: str) -> str:
    return digest if digest.startswith(ACTOR_SIGNATURE_PREFIX) else f"{ACTOR_SIGNATURE_PREFIX}{digest}"


def build_actor_headers(
    *,
    internal_api_key: str,
    signature_secret: str,
    tenant_id: str,
    user_id: str,
    roles: str | Sequence[str] | None = "user",
    scopes: str | Sequence[str] | None = None,
    timestamp: str | int | None = None,
) -> dict[str, str]:
    issued_at = str(timestamp if timestamp is not None else int(time.time()))
    roles_header = canonical_csv(roles, default="user")
    scopes_header = canonical_csv(scopes)
    digest = sign_actor_claims(
        secret=signature_secret,
        tenant_id=tenant_id,
        user_id=user_id,
        roles_header=roles_header,
        scopes_header=scopes_header,
        timestamp=issued_at,
    )
    return {
        "X-Internal-Auth": internal_api_key,
        "X-Actor-User-Id": user_id,
        "X-Actor-Roles": roles_header,
        "X-Actor-Scopes": scopes_header,
        "X-Actor-Timestamp": issued_at,
        "X-Actor-Signature": format_actor_signature(digest),
    }


def build_signed_request_headers(
    *,
    internal_api_key: str,
    signature_secret: str,
    tenant_id: str,
    user_id: str,
    roles: str | Sequence[str] | None = "user",
    scopes: str | Sequence[str] | None = None,
    method: str,
    path: str,
    body: bytes | str | None = b"",
    timestamp: str | int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    headers = build_actor_headers(
        internal_api_key=internal_api_key,
        signature_secret=signature_secret,
        tenant_id=tenant_id,
        user_id=user_id,
        roles=roles,
        scopes=scopes,
        timestamp=timestamp,
    )
    request_nonce = nonce or token_urlsafe(24)
    request_body_hash = body_sha256(body)
    digest = sign_request_claims(
        secret=signature_secret,
        tenant_id=tenant_id,
        user_id=user_id,
        roles_header=headers["X-Actor-Roles"],
        scopes_header=headers["X-Actor-Scopes"],
        timestamp=headers["X-Actor-Timestamp"],
        nonce=request_nonce,
        method=method,
        path=path,
        body_hash=request_body_hash,
    )
    headers.update(
        {
            "X-Request-Nonce": request_nonce,
            "X-Request-Body-SHA256": request_body_hash,
            "X-Request-Signature": format_actor_signature(digest),
        }
    )
    return headers

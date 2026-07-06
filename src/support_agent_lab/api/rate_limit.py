from __future__ import annotations

import math
import time
from dataclasses import dataclass

from fastapi import Request

from support_agent_lab.config import Settings
from support_agent_lab.memory.event_store import SQLiteEventStore


@dataclass
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int = 0


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class InMemoryRateLimiter:
    """Process-local token bucket for single-instance deployments."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}

    def check(self, key: str, *, requests_per_minute: int, burst: int) -> RateLimitDecision:
        now = self._clock()
        refill_per_second = requests_per_minute / 60
        bucket = self._buckets.get(key)
        if not bucket:
            bucket = _Bucket(tokens=float(burst), updated_at=now)
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(burst), bucket.tokens + elapsed * refill_per_second)
        bucket.updated_at = now

        if bucket.tokens < 1:
            retry_after = math.ceil((1 - bucket.tokens) / refill_per_second) if refill_per_second > 0 else 60
            self._buckets[key] = bucket
            return RateLimitDecision(
                allowed=False,
                limit=requests_per_minute,
                remaining=0,
                retry_after_seconds=max(1, retry_after),
            )

        bucket.tokens -= 1
        self._buckets[key] = bucket
        return RateLimitDecision(
            allowed=True,
            limit=requests_per_minute,
            remaining=max(0, math.floor(bucket.tokens)),
        )

    def reset(self) -> None:
        self._buckets.clear()


class SQLiteRateLimiter:
    """SQLite-backed token bucket shared by local workers using the same event store."""

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._stores: dict[str, SQLiteEventStore] = {}

    def check(
        self,
        database_url: str,
        key: str,
        *,
        requests_per_minute: int,
        burst: int,
    ) -> RateLimitDecision:
        store = self._store_for_url(database_url)
        decision = store.consume_api_rate_limit_token(
            bucket_key=key,
            requests_per_minute=requests_per_minute,
            burst=burst,
            now_epoch_seconds=self._clock(),
        )
        return RateLimitDecision(
            allowed=bool(decision["allowed"]),
            limit=requests_per_minute,
            remaining=int(decision["remaining"]),
            retry_after_seconds=int(decision["retry_after_seconds"]),
        )

    def reset(self) -> None:
        self._stores.clear()

    def _store_for_url(self, database_url: str) -> SQLiteEventStore:
        store = self._stores.get(database_url)
        if store:
            return store
        store = SQLiteEventStore.from_url(database_url)
        if store is None:
            raise RuntimeError("SQLite rate limit backend requires sqlite:/// APP_DATABASE_URL")
        self._stores[database_url] = store
        return store


def should_rate_limit(settings: Settings, path: str) -> bool:
    if not settings.rate_limit_enabled:
        return False
    return not _is_exempt_path(path)


def rate_limit_backend(settings: Settings) -> str:
    if settings.app_rate_limit_backend == "sqlite":
        return "sqlite"
    if settings.app_rate_limit_backend == "memory":
        return "memory"
    if settings.is_production or settings.app_require_production:
        return "sqlite"
    return "memory"


def rate_limit_key(settings: Settings, request: Request) -> str:
    actor_user_id = (
        request.headers.get("X-Actor-User-Id")
        or request.headers.get("X-Demo-User")
        or _client_host(request)
    )
    return ":".join(
        [
            settings.app_tenant_id,
            _safe_key_part(actor_user_id),
            route_family(request.url.path),
        ]
    )


def _is_exempt_path(path: str) -> bool:
    return path in {"/api/v1/health", "/api/v1/ready", "/metrics"} or path.startswith("/docs") or path.startswith("/openapi")


def route_family(path: str) -> str:
    if path in {"/api/v1/health", "/api/v1/ready"}:
        return "health"
    if path == "/metrics":
        return "metrics"
    if path.startswith("/api/v1/webhooks"):
        return "webhooks"
    if path.startswith("/api/v1/chat"):
        return "chat"
    if path.startswith("/api/v1/admin/evals"):
        return "admin-evals"
    if path.startswith("/api/v1/admin"):
        return "admin"
    return "api"


def _client_host(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown-client"


def _safe_key_part(value: str) -> str:
    stripped = value.strip()
    return stripped.replace(":", "_")[:128] or "unknown"

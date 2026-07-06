from fastapi.testclient import TestClient

from support_agent_lab.api.main import app
from support_agent_lab.api.rate_limit import SQLiteRateLimiter, rate_limit_backend
from support_agent_lab.config import Settings, get_settings


def _db_url(path) -> str:
    return f"sqlite:///{path}"


def _reset_rate_limit_state() -> None:
    get_settings.cache_clear()
    app.state.rate_limiter.reset()
    app.state.sqlite_rate_limiter.reset()


def test_rate_limit_backend_auto_uses_sqlite_for_production():
    assert rate_limit_backend(Settings(app_env="local")) == "memory"
    assert rate_limit_backend(Settings(app_env="production")) == "sqlite"
    assert rate_limit_backend(Settings(app_require_production=True)) == "sqlite"
    assert rate_limit_backend(Settings(app_env="production", app_rate_limit_backend="memory")) == "memory"
    assert rate_limit_backend(Settings(app_rate_limit_backend="sqlite")) == "sqlite"


def test_sqlite_rate_limiter_shares_bucket_across_instances(tmp_path):
    database_url = _db_url(tmp_path / "events.db")
    first_limiter = SQLiteRateLimiter(clock=lambda: 100.0)
    second_limiter = SQLiteRateLimiter(clock=lambda: 100.1)
    refilled_limiter = SQLiteRateLimiter(clock=lambda: 161.0)

    first = first_limiter.check(
        database_url,
        "tenant:user:chat",
        requests_per_minute=1,
        burst=1,
    )
    second = second_limiter.check(
        database_url,
        "tenant:user:chat",
        requests_per_minute=1,
        burst=1,
    )
    refilled = refilled_limiter.check(
        database_url,
        "tenant:user:chat",
        requests_per_minute=1,
        burst=1,
    )

    assert first.allowed is True
    assert first.remaining == 0
    assert second.allowed is False
    assert second.retry_after_seconds >= 1
    assert refilled.allowed is True


def test_require_production_auto_rate_limit_persists_after_limiter_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_REQUIRE_PRODUCTION", "true")
    monkeypatch.setenv("APP_TENANT_ID", "tenant_prod")
    monkeypatch.setenv("APP_DATABASE_URL", _db_url(tmp_path / "events.db"))
    monkeypatch.setenv("APP_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("APP_RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("APP_RATE_LIMIT_BURST", "1")
    _reset_rate_limit_state()
    try:
        client = TestClient(app)
        first = client.post(
            "/api/v1/chat/sessions",
            json={},
            headers={"X-Demo-User": "user_prod"},
        )
        app.state.rate_limiter.reset()
        app.state.sqlite_rate_limiter.reset()
        second = client.post(
            "/api/v1/chat/sessions",
            json={},
            headers={"X-Demo-User": "user_prod"},
        )
    finally:
        _reset_rate_limit_state()

    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "1"
    assert first.headers["X-RateLimit-Remaining"] == "0"
    assert second.status_code == 429
    assert second.json()["detail"] == "Rate limit exceeded"

import pytest

from support_agent_lab.bootstrap import create_container
from support_agent_lab.config import Settings
from support_agent_lab.config import get_settings
from support_agent_lab.llm.gateway import create_llm_gateway


ACTOR_SIGNATURE_SECRET = "actor-signing-secret-with-32-byte-minimum"


def test_production_mode_requires_real_provider_and_integrations():
    settings = Settings(app_env="production")

    with pytest.raises(RuntimeError, match="Production mode is not ready"):
        settings.validate_production_ready()


def test_production_mode_rejects_local_deterministic_llm():
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="local_deterministic",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret=ACTOR_SIGNATURE_SECRET,
    )

    with pytest.raises(RuntimeError, match="APP_MODEL_PROVIDER=openai"):
        settings.validate_production_ready()


def test_production_mode_accepts_real_integration_config():
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret=ACTOR_SIGNATURE_SECRET,
    )

    settings.validate_production_ready()


def test_production_mode_rejects_placeholder_values():
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="replace_with_real_key",
        app_business_api_base_url="https://support-backend.example.com",
        app_business_api_key="replace_with_real_service_token",
        app_knowledge_api_base_url="https://knowledge.example.com",
        app_knowledge_api_key="replace_with_real_knowledge_token",
        app_internal_api_key="replace_with_real_internal_gateway_secret",
        app_actor_signature_secret="replace_with_real_actor_signature_secret",
    )

    with pytest.raises(RuntimeError, match="placeholder"):
        settings.validate_production_ready()


def test_require_production_rejects_accidental_local_mode():
    settings = Settings(app_env="local", app_require_production=True)

    with pytest.raises(RuntimeError, match="APP_REQUIRE_PRODUCTION"):
        settings.validate_production_ready()


def test_require_production_enables_request_signature_by_default():
    settings = Settings(app_env="production", app_require_production=True)

    assert settings.require_request_signature is True
    assert Settings(app_env="production", app_require_production=True, app_request_signature_required=False).require_request_signature is False
    assert Settings(app_env="production", app_require_production=False).require_request_signature is False


def test_production_mode_rejects_demo_tenant():
    settings = Settings(
        app_env="production",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret=ACTOR_SIGNATURE_SECRET,
    )

    with pytest.raises(RuntimeError, match="APP_TENANT_ID"):
        settings.validate_production_ready()


def test_production_mode_rejects_missing_adapter_keys():
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret=ACTOR_SIGNATURE_SECRET,
    )

    with pytest.raises(RuntimeError, match="APP_BUSINESS_API_KEY"):
        settings.validate_production_ready()


def test_production_mode_rejects_unsupported_event_store_url():
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret=ACTOR_SIGNATURE_SECRET,
        app_database_url="postgresql://events.internal/support",
    )

    with pytest.raises(RuntimeError, match="APP_DATABASE_URL"):
        settings.validate_production_ready()


def test_openai_provider_requires_api_key():
    settings = Settings(app_model_provider="openai", openai_api_key=None)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        create_llm_gateway(settings)


def test_production_mode_requires_actor_signature_secret():
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key="internal-test-key",
    )

    with pytest.raises(RuntimeError, match="APP_ACTOR_SIGNATURE_SECRET"):
        settings.validate_production_ready()


def test_production_mode_rejects_short_actor_signature_secret():
    settings = Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key="internal-test-key",
        app_actor_signature_secret="short-secret",
    )

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        settings.validate_production_ready()


def test_production_container_uses_http_integrations_not_demo_store(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_TENANT_ID", "tenant_live")
    monkeypatch.setenv("APP_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("APP_BUSINESS_API_BASE_URL", "https://business.internal.test")
    monkeypatch.setenv("APP_BUSINESS_API_KEY", "business-token")
    monkeypatch.setenv("APP_KNOWLEDGE_API_BASE_URL", "https://knowledge.internal.test")
    monkeypatch.setenv("APP_KNOWLEDGE_API_KEY", "knowledge-token")
    monkeypatch.setenv("APP_INTERNAL_API_KEY", "internal-test-key")
    monkeypatch.setenv("APP_ACTOR_SIGNATURE_SECRET", ACTOR_SIGNATURE_SECRET)
    monkeypatch.setenv("APP_DATABASE_URL", f"sqlite:///{tmp_path / 'events.db'}")
    get_settings.cache_clear()
    try:
        container = create_container()
    finally:
        get_settings.cache_clear()

    assert container.store is None
    assert container.event_store is not None
    assert container.tools.idempotency_store is container.event_store
    assert container.tools.audit_sink is container.event_store
    assert container.llm.provider.provider == "openai"
    assert {tool["name"] for tool in container.tools.registry.list_tools()} >= {
        "crm.get_customer",
        "order.get",
        "shipping.track",
        "ticket.create",
        "kb.search",
    }

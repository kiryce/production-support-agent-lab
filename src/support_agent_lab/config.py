from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["local", "test", "production", "prod"] = "local"
    app_tenant_id: str = "demo_tenant"
    app_require_production: bool = False
    app_model_provider: str = "local_deterministic"
    app_database_url: str = "sqlite:///./data/local/support-agent-lab.db"
    app_enable_mcp: bool = False
    app_openai_model: str = "gpt-5.5"
    openai_api_key: str | None = None
    app_business_api_base_url: str | None = None
    app_business_api_key: str | None = None
    app_knowledge_api_base_url: str | None = None
    app_knowledge_api_key: str | None = None
    app_internal_api_key: str | None = None
    app_actor_signature_secret: str | None = None
    app_actor_signature_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    app_request_signature_required: bool | None = None
    app_rate_limit_enabled: bool | None = None
    app_rate_limit_backend: Literal["auto", "memory", "sqlite"] = "auto"
    app_rate_limit_requests_per_minute: int = Field(default=600, ge=1, le=60000)
    app_rate_limit_burst: int = Field(default=600, ge=1, le=60000)
    app_http_timeout_ms: int = Field(default=5000, ge=500, le=60000)
    app_business_api_retry_attempts: int = Field(default=2, ge=1, le=5)
    app_business_api_retry_backoff_ms: int = Field(default=100, ge=0, le=5000)
    app_business_api_circuit_failure_threshold: int = Field(default=5, ge=1, le=50)
    app_business_api_circuit_reset_seconds: int = Field(default=30, ge=1, le=3600)
    app_knowledge_api_retry_attempts: int = Field(default=2, ge=1, le=5)
    app_knowledge_api_retry_backoff_ms: int = Field(default=100, ge=0, le=5000)
    app_knowledge_api_circuit_failure_threshold: int = Field(default=5, ge=1, le=50)
    app_knowledge_api_circuit_reset_seconds: int = Field(default=30, ge=1, le=3600)
    app_llm_timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    app_llm_retry_attempts: int = Field(default=2, ge=1, le=5)
    app_llm_retry_backoff_ms: int = Field(default=250, ge=0, le=5000)
    app_llm_circuit_failure_threshold: int = Field(default=5, ge=1, le=50)
    app_llm_circuit_reset_seconds: int = Field(default=30, ge=1, le=3600)
    app_readiness_deep_checks: bool | None = None
    app_monitor_alert_webhook_enabled: bool = False
    app_monitor_alert_webhook_url: str | None = None
    app_monitor_alert_webhook_secret: str | None = None
    app_monitor_alert_webhook_receiver_enabled: bool = False
    app_monitor_alert_webhook_receiver_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    app_monitor_alert_webhook_timeout_ms: int = Field(default=3000, ge=500, le=30000)
    app_monitor_alert_min_severity: Literal["P0", "P1", "P2", "P3"] = "P1"
    app_monitor_alert_max_attempts: int = Field(default=3, ge=1, le=20)
    app_monitor_alert_backoff_base_seconds: int = Field(default=60, ge=1, le=3600)
    app_monitor_alert_backoff_max_seconds: int = Field(default=900, ge=1, le=86400)
    app_monitor_alert_claim_lease_seconds: int = Field(default=120, ge=10, le=3600)
    app_monitor_alert_dispatcher_heartbeat_stale_seconds: int = Field(default=180, ge=30, le=86400)
    app_event_retention_days: int = Field(default=365, ge=30, le=3650)
    app_tool_audit_retention_days: int = Field(default=180, ge=30, le=3650)
    app_idempotency_retention_days: int = Field(default=30, ge=1, le=3650)
    app_alert_delivery_retention_days: int = Field(default=90, ge=7, le=3650)
    app_event_store_backup_dir: str = "./data/backups"
    app_event_store_operation_lock_ttl_seconds: int = Field(default=1800, ge=60, le=86400)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    def validate_production_ready(self) -> None:
        if self.app_require_production and not self.is_production:
            raise RuntimeError("APP_REQUIRE_PRODUCTION=true requires APP_ENV=production")
        if not self.is_production:
            return
        missing: list[str] = []
        if self.app_tenant_id == "demo_tenant" or self._looks_like_placeholder(self.app_tenant_id):
            missing.append("APP_TENANT_ID must be a real tenant id")
        if self.app_model_provider != "openai":
            missing.append("APP_MODEL_PROVIDER=openai")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.app_business_api_base_url:
            missing.append("APP_BUSINESS_API_BASE_URL")
        if not self.app_business_api_key:
            missing.append("APP_BUSINESS_API_KEY")
        if not self.app_knowledge_api_base_url:
            missing.append("APP_KNOWLEDGE_API_BASE_URL")
        if not self.app_knowledge_api_key:
            missing.append("APP_KNOWLEDGE_API_KEY")
        if not self.app_internal_api_key:
            missing.append("APP_INTERNAL_API_KEY")
        if not self.app_actor_signature_secret:
            missing.append("APP_ACTOR_SIGNATURE_SECRET")
        if not self.app_database_url.startswith("sqlite:///"):
            missing.append("APP_DATABASE_URL must use sqlite:/// until another event store adapter is configured")
        if self._looks_like_placeholder(self.openai_api_key):
            missing.append("OPENAI_API_KEY must not be a placeholder")
        if self._looks_like_placeholder(self.app_business_api_base_url):
            missing.append("APP_BUSINESS_API_BASE_URL must not be a placeholder")
        if self._looks_like_placeholder(self.app_business_api_key):
            missing.append("APP_BUSINESS_API_KEY must not be a placeholder")
        if self._looks_like_placeholder(self.app_knowledge_api_base_url):
            missing.append("APP_KNOWLEDGE_API_BASE_URL must not be a placeholder")
        if self._looks_like_placeholder(self.app_knowledge_api_key):
            missing.append("APP_KNOWLEDGE_API_KEY must not be a placeholder")
        if self._looks_like_placeholder(self.app_internal_api_key):
            missing.append("APP_INTERNAL_API_KEY must not be a placeholder")
        if self._looks_like_placeholder(self.app_actor_signature_secret):
            missing.append("APP_ACTOR_SIGNATURE_SECRET must not be a placeholder")
        if self.app_actor_signature_secret and len(self.app_actor_signature_secret) < 32:
            missing.append("APP_ACTOR_SIGNATURE_SECRET must be at least 32 characters")
        if self.app_require_production and self.app_request_signature_required is False:
            missing.append("APP_REQUEST_SIGNATURE_REQUIRED must not be false when APP_REQUIRE_PRODUCTION=true")
        if self.app_require_production and self.app_rate_limit_enabled is False:
            missing.append("APP_RATE_LIMIT_ENABLED must not be false when APP_REQUIRE_PRODUCTION=true")
        if self.app_require_production and self.app_rate_limit_backend == "memory":
            missing.append("APP_RATE_LIMIT_BACKEND must not be memory when APP_REQUIRE_PRODUCTION=true")
        if self.app_monitor_alert_webhook_enabled:
            if not self.app_monitor_alert_webhook_url:
                missing.append("APP_MONITOR_ALERT_WEBHOOK_URL")
            elif self._looks_like_placeholder(self.app_monitor_alert_webhook_url):
                missing.append("APP_MONITOR_ALERT_WEBHOOK_URL must not be a placeholder")
            if not self.app_monitor_alert_webhook_secret:
                missing.append("APP_MONITOR_ALERT_WEBHOOK_SECRET")
            elif self._looks_like_placeholder(self.app_monitor_alert_webhook_secret):
                missing.append("APP_MONITOR_ALERT_WEBHOOK_SECRET must not be a placeholder")
            elif len(self.app_monitor_alert_webhook_secret) < 32:
                missing.append("APP_MONITOR_ALERT_WEBHOOK_SECRET must be at least 32 characters")
        if self.app_monitor_alert_webhook_receiver_enabled:
            if not self.app_monitor_alert_webhook_secret:
                missing.append("APP_MONITOR_ALERT_WEBHOOK_SECRET")
            elif self._looks_like_placeholder(self.app_monitor_alert_webhook_secret):
                missing.append("APP_MONITOR_ALERT_WEBHOOK_SECRET must not be a placeholder")
            elif len(self.app_monitor_alert_webhook_secret) < 32:
                missing.append("APP_MONITOR_ALERT_WEBHOOK_SECRET must be at least 32 characters")
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Production mode is not ready; missing required config: {joined}")

    @property
    def require_request_signature(self) -> bool:
        if self.app_request_signature_required is not None:
            return self.app_request_signature_required
        return self.is_production and self.app_require_production

    @property
    def rate_limit_enabled(self) -> bool:
        if self.app_rate_limit_enabled is not None:
            return self.app_rate_limit_enabled
        return self.is_production

    def _looks_like_placeholder(self, value: str | None) -> bool:
        if not value:
            return False
        lowered = value.lower()
        return any(marker in lowered for marker in ["replace_with", "example.com", "your_"])


@lru_cache
def get_settings() -> Settings:
    return Settings()

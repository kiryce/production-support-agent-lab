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
    app_http_timeout_ms: int = Field(default=5000, ge=500, le=60000)
    app_llm_timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    app_readiness_deep_checks: bool | None = None

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
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Production mode is not ready; missing required config: {joined}")

    def _looks_like_placeholder(self, value: str | None) -> bool:
        if not value:
            return False
        lowered = value.lower()
        return any(marker in lowered for marker in ["replace_with", "example.com", "your_"])


@lru_cache
def get_settings() -> Settings:
    return Settings()

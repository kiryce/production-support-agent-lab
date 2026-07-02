from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from pydantic import BaseModel, Field

from support_agent_lab.config import Settings, get_settings
from support_agent_lab.models import LLMCallTrace


class LLMRequest(BaseModel):
    prompt_version: str = "support_answer_v1"
    task: str
    fallback_content: str
    system_context: dict = Field(default_factory=dict)
    user_context: dict = Field(default_factory=dict)


class LLMResponse(BaseModel):
    content: str
    trace: LLMCallTrace


class LLMProvider(Protocol):
    provider: str
    model: str

    async def generate(self, request: LLMRequest) -> LLMResponse:
        ...

    async def health_check(self) -> None:
        ...


class ProductionConfigError(RuntimeError):
    pass


@dataclass
class LocalDeterministicProvider:
    """Local-only provider used by tests and onboarding.

    Production mode refuses to use this provider. It exists so regression tests
    can stay deterministic while the production wiring uses real model calls.
    """

    provider: str = "local_deterministic"
    model: str = "deterministic-support-agent"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        started = perf_counter()
        input_tokens = estimate_tokens(
            " ".join(
                [
                    request.task,
                    str(request.system_context),
                    str(request.user_context),
                ]
            )
        )
        output_tokens = estimate_tokens(request.fallback_content)
        trace = LLMCallTrace(
            provider=self.provider,
            model=self.model,
            prompt_version=request.prompt_version,
            latency_ms=int((perf_counter() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,
            fallback_used=True,
        )
        return LLMResponse(content=request.fallback_content, trace=trace)

    async def health_check(self) -> None:
        return None


@dataclass
class OpenAIResponsesProvider:
    api_key: str
    model: str
    timeout_ms: int = 15_000
    provider: str = "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        from openai import AsyncOpenAI

        started = perf_counter()
        client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout_ms / 1000)
        input_text = "\n\n".join(
            [
                f"Task: {request.task}",
                f"System context: {request.system_context}",
                f"User context: {request.user_context}",
                f"Grounded draft from tools and retrieval: {request.fallback_content}",
            ]
        )
        response = await client.responses.create(
            model=self.model,
            instructions=(
                "You are a production customer-support agent. Use only the provided "
                "tool and retrieval context. Do not invent order, invoice, shipment, "
                "refund, or account-security facts."
            ),
            input=input_text,
        )
        content = response.output_text
        trace = LLMCallTrace(
            provider=self.provider,
            model=self.model,
            prompt_version=request.prompt_version,
            latency_ms=int((perf_counter() - started) * 1000),
            input_tokens=estimate_tokens(input_text),
            output_tokens=estimate_tokens(content),
            cost_usd=0.0,
            fallback_used=False,
        )
        return LLMResponse(content=content, trace=trace)

    async def health_check(self) -> None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout_ms / 1000)
        await client.models.retrieve(self.model)


@dataclass
class LLMGateway:
    provider: LLMProvider
    timeout_ms: int = 15_000

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return await asyncio.wait_for(self.provider.generate(request), timeout=self.timeout_ms / 1000)

    async def health_check(self) -> None:
        return await asyncio.wait_for(self.provider.health_check(), timeout=self.timeout_ms / 1000)


def create_default_llm_gateway() -> LLMGateway:
    return create_llm_gateway(get_settings())


def create_llm_gateway(settings: Settings) -> LLMGateway:
    if settings.app_model_provider == "openai":
        if not settings.openai_api_key:
            raise ProductionConfigError("OPENAI_API_KEY is required when APP_MODEL_PROVIDER=openai")
        return LLMGateway(
            provider=OpenAIResponsesProvider(
                api_key=settings.openai_api_key,
                model=settings.app_openai_model,
                timeout_ms=settings.app_llm_timeout_ms,
            ),
            timeout_ms=settings.app_llm_timeout_ms,
        )
    if settings.is_production:
        raise ProductionConfigError("Production mode requires APP_MODEL_PROVIDER=openai")
    if settings.app_model_provider == "local_deterministic":
        return LLMGateway(provider=LocalDeterministicProvider(), timeout_ms=settings.app_llm_timeout_ms)
    raise ProductionConfigError(f"Unknown APP_MODEL_PROVIDER: {settings.app_model_provider}")


def estimate_tokens(text: str) -> int:
    # Good enough for cost trend demos; real gateways should use model tokenizers.
    return max(1, len(text) // 4)

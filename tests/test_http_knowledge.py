import httpx
import pytest

from support_agent_lab.memory.http_knowledge import HTTPKnowledgeIndex


@pytest.mark.asyncio
async def test_http_knowledge_parses_hits_and_sends_auth_header():
    seen_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "document_id": "invoice_policy_v1",
                        "chunk_id": "invoice_policy_v1:0",
                        "title": "Invoice policy",
                        "content": "Invoices are issued within 24 hours.",
                        "score": 0.91,
                        "source_uri": "kb://invoice_policy_v1",
                    }
                ]
            },
        )

    index = HTTPKnowledgeIndex(
        base_url="https://knowledge.internal.test",
        api_key="knowledge-token",
        transport=httpx.MockTransport(handler),
    )

    trace = await index.search("invoice", limit=1)

    assert seen_headers["authorization"] == "Bearer knowledge-token"
    assert trace.selected_sources == ["kb://invoice_policy_v1"]
    assert trace.selected_context[0].document_id == "invoice_policy_v1"


@pytest.mark.asyncio
async def test_http_knowledge_parses_optional_upstream_trace_fields():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "document_id": "return_policy_v2",
                        "chunk_id": "return_policy_v2:4",
                        "title": "Return policy",
                        "content": "Damaged goods can be returned within 30 days.",
                        "score": 0.87,
                        "source_uri": "kb://return_policy_v2",
                    }
                ],
                "rewritten_queries": ["damaged order return", "broken item refund"],
                "candidates_by_stage": {"bm25": 15, "vector": 9, "reranked": 3, "selected": 1},
                "dropped_candidates": ["return_policy_v1:0", "shipping_policy_v3:2"],
            },
        )

    index = HTTPKnowledgeIndex(
        base_url="https://knowledge.internal.test",
        transport=httpx.MockTransport(handler),
    )

    trace = await index.search("headphones broken", limit=1)

    assert trace.rewritten_queries == ["damaged order return", "broken item refund"]
    assert trace.candidates_by_stage == {"bm25": 15, "vector": 9, "reranked": 3, "selected": 1}
    assert trace.dropped_candidates == ["return_policy_v1:0", "shipping_policy_v3:2"]


@pytest.mark.asyncio
async def test_http_knowledge_ignores_unsafe_trace_payload():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "document_id": "invoice_policy_v1",
                        "title": "Invoice policy",
                        "content": "Invoices are issued within 24 hours.",
                        "source_uri": "kb://invoice_policy_v1",
                    }
                ],
                "rewritten_queries": {"unexpected": "shape"},
                "candidates_by_stage": {"vector": -1, "selected": True, "reranked": 1.5},
                "dropped_candidates": [None, {"id": "unsafe"}],
            },
        )

    index = HTTPKnowledgeIndex(
        base_url="https://knowledge.internal.test",
        transport=httpx.MockTransport(handler),
    )

    trace = await index.search("invoice", limit=1)

    assert trace.rewritten_queries == ["invoice"]
    assert trace.candidates_by_stage == {"http": 1, "selected": 1}
    assert trace.dropped_candidates == []


@pytest.mark.asyncio
async def test_http_knowledge_returns_observable_trace_on_upstream_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    index = HTTPKnowledgeIndex(
        base_url="https://knowledge.internal.test",
        transport=httpx.MockTransport(handler),
    )

    trace = await index.search("invoice")

    assert trace.selected_context == []
    assert trace.selected_sources == []
    assert trace.candidates_by_stage["knowledge_http_503"] == 1
    assert trace.dropped_candidates == ["knowledge_http_503"]


@pytest.mark.asyncio
async def test_http_knowledge_returns_observable_trace_on_bad_payload():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    index = HTTPKnowledgeIndex(
        base_url="https://knowledge.internal.test",
        transport=httpx.MockTransport(handler),
    )

    trace = await index.search("invoice")

    assert trace.selected_context == []
    assert trace.candidates_by_stage["knowledge_bad_payload"] == 1

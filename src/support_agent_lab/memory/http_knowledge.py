from __future__ import annotations

import httpx

from support_agent_lab.models import RetrievalHit, RetrievalTrace


class HTTPKnowledgeIndex:
    """Production knowledge adapter backed by an HTTP knowledge service.

    Expected endpoint:
      GET /knowledge/search?query=<text>&limit=<n>

    Response can be either {"hits": [...]} or a bare list of hit objects.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_ms: int = 5000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout_ms / 1000
        self.transport = transport

    async def search(self, query: str, limit: int = 4) -> RetrievalTrace:
        headers = self._headers()
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=headers,
                transport=self.transport,
            ) as client:
                response = await client.get("/knowledge/search", params={"query": query, "limit": limit})
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException:
            return self._empty_trace(query, "knowledge_timeout")
        except httpx.HTTPStatusError as exc:
            return self._empty_trace(query, f"knowledge_http_{exc.response.status_code}")
        except httpx.HTTPError:
            return self._empty_trace(query, "knowledge_unavailable")
        except ValueError:
            return self._empty_trace(query, "knowledge_bad_payload")
        try:
            raw_hits = payload.get("hits", payload) if isinstance(payload, dict) else payload
            hits = [self._parse_hit(item) for item in raw_hits[:limit]]
        except (KeyError, TypeError, ValueError):
            return self._empty_trace(query, "knowledge_bad_payload")
        return RetrievalTrace(
            query=query,
            rewritten_queries=[query],
            selected_sources=[hit.source_uri for hit in hits],
            candidates_by_stage={"http": len(raw_hits), "selected": len(hits)},
            selected_context=hits,
            dropped_candidates=[
                str(item.get("chunk_id") or item.get("id") or index)
                for index, item in enumerate(raw_hits[limit:], start=limit)
                if isinstance(item, dict)
            ],
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def health_check(self) -> None:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._headers(),
                transport=self.transport,
            ) as client:
                response = await client.get("/health")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError("Knowledge API readiness check failed") from exc

    def _empty_trace(self, query: str, reason: str) -> RetrievalTrace:
        return RetrievalTrace(
            query=query,
            rewritten_queries=[query],
            selected_sources=[],
            candidates_by_stage={reason: 1, "selected": 0},
            selected_context=[],
            dropped_candidates=[reason],
        )

    def _parse_hit(self, item: dict) -> RetrievalHit:
        return RetrievalHit(
            document_id=str(item["document_id"]),
            chunk_id=str(item.get("chunk_id") or f"{item['document_id']}:0"),
            title=str(item.get("title") or item["document_id"]),
            content=str(item["content"]),
            score=float(item.get("score", 1.0)),
            source_uri=str(item.get("source_uri") or item.get("url") or ""),
            metadata=dict(item.get("metadata") or {}),
        )

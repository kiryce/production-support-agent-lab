import { describe, expect, it, vi, afterEach } from "vitest";
import { GET } from "../app/api/console/knowledge/summary/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("knowledge summary BFF route", () => {
  it("forwards to the backend knowledge summary endpoint", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "knowledge_index_summary.v1",
          provider: "sqlite",
          status: "ready",
          tenant_id: "demo_tenant",
          document_count: 2,
          chunk_count: 8,
          source_count: 1,
          last_ingested_at: "2026-07-06T00:00:00.000Z",
          last_updated_at: "2026-07-06T00:00:00.000Z",
          fts_enabled: true,
          database_file: "support-agent-knowledge.db",
          database_path_hash: "abc123",
          min_ready_documents: 1
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );

    const response = await GET();

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload.provider).toBe("sqlite");
    expect(payload.document_count).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [target, init] = fetchMock.mock.calls[0];
    expect(new URL(String(target)).pathname).toBe("/api/v1/admin/knowledge/summary");
    expect(init?.method).toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("returns backend errors without a client-controlled path", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Missing required scope: knowledge:diagnose" }), {
        status: 403,
        headers: { "Content-Type": "application/json" }
      })
    );

    const response = await GET();

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({
      detail: "Missing required scope: knowledge:diagnose"
    });
    const [target] = fetchMock.mock.calls[0];
    expect(String(target)).toBe("http://agent.internal/api/v1/admin/knowledge/summary");
  });
});

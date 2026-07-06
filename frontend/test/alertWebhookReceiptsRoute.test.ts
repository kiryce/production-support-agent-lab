import type { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/api/console/monitor/alert-webhook-receipts/route";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("alert webhook receipts BFF route", () => {
  it("forwards bounded receipt queries with backend parameter names", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json([
        {
          tenant_id: "demo_tenant",
          delivery_id: "deliv_1",
          alert_key: "agent:order:TIMEOUT",
          severity: "P1",
          body_hash: "body_hash_1",
          signature_hash: "signature_hash_1",
          source_hash: "source_hash_1",
          user_agent_hash: "agent_hash_1",
          alert_count: 1,
          sample_event_count: 1,
          sample_run_count: 1,
          duplicate_count: 0,
          first_received_at: "2026-07-04T00:00:00.000Z",
          last_received_at: "2026-07-04T00:00:00.000Z",
          created_at: "2026-07-04T00:00:00.000Z",
          updated_at: "2026-07-04T00:00:00.000Z"
        }
      ])
    );

    const response = await GET(
      getRequest(
        "/api/console/monitor/alert-webhook-receipts?alertKey=agent%3Aorder%3ATIMEOUT&deliveryId=deliv_1&limit=9999&order=asc&rawBody=leak"
      )
    );

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload).toHaveLength(1);
    expect(payload[0]).toMatchObject({
      delivery_id: "deliv_1",
      alert_key: "agent:order:TIMEOUT",
      body_hash: "body_hash_1"
    });
    expect(payload[0]).not.toHaveProperty("tenant_id");
    expect(payload[0]).not.toHaveProperty("signature_hash");
    expect(payload[0]).not.toHaveProperty("source_hash");
    expect(payload[0]).not.toHaveProperty("user_agent_hash");
    const [target, init] = fetchMock.mock.calls[0];
    const url = new URL(String(target));
    expect(init?.method).toBe("GET");
    expect(url.pathname).toBe("/api/v1/admin/monitor/alert-webhook-receipts");
    expect(url.searchParams.get("alert_key")).toBe("agent:order:TIMEOUT");
    expect(url.searchParams.get("delivery_id")).toBe("deliv_1");
    expect(url.searchParams.get("limit")).toBe("200");
    expect(url.searchParams.get("order")).toBe("asc");
    expect(url.searchParams.has("rawBody")).toBe(false);
  });

  it("signs production receipt queries with actor and request headers", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "production";
    process.env.APP_TENANT_ID = "tenant_live";
    process.env.APP_INTERNAL_API_KEY = "internal_key";
    process.env.APP_ACTOR_SIGNATURE_SECRET = "receipt_route_secret_min_32_chars";
    process.env.FRONTEND_ACTOR_USER_ID = "console_operator";
    process.env.FRONTEND_ACTOR_ROLES = "admin";
    process.env.FRONTEND_ACTOR_SCOPES = "monitor:read";
    process.env.FRONTEND_REQUEST_SIGNATURE_REQUIRED = "true";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json([]));

    const response = await GET(
      getRequest("/api/console/monitor/alert-webhook-receipts?deliveryId=deliv_1&limit=25")
    );

    expect(response.status).toBe(200);
    const [target, init] = fetchMock.mock.calls[0];
    const url = new URL(String(target));
    const headers = init?.headers as Record<string, string>;
    expect(url.pathname).toBe("/api/v1/admin/monitor/alert-webhook-receipts");
    expect(url.searchParams.get("delivery_id")).toBe("deliv_1");
    expect(url.searchParams.get("limit")).toBe("25");
    expect(headers["X-Internal-Auth"]).toBe("internal_key");
    expect(headers["X-Actor-User-Id"]).toBe("console_operator");
    expect(headers["X-Actor-Scopes"]).toBe("monitor:read");
    expect(headers["X-Request-Body-SHA256"]).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
    expect(headers["X-Request-Nonce"]).toBeTruthy();
    expect(headers["X-Request-Signature"]).toMatch(/^sha256=/);
  });

  it("rejects unsupported sort order before calling the backend", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const response = await GET(getRequest("/api/console/monitor/alert-webhook-receipts?order=random"));

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "order must be asc or desc" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes backend errors through the console route", async () => {
    process.env.AGENT_API_BASE_URL = "http://agent.internal";
    process.env.FRONTEND_AUTH_MODE = "demo";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ detail: "missing monitor scope" }, { status: 403 })
    );

    const response = await GET(getRequest("/api/console/monitor/alert-webhook-receipts?limit=25"));

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ detail: "missing monitor scope" });
  });
});

function getRequest(path: string) {
  return { nextUrl: new URL(`http://console.local${path}`) } as unknown as NextRequest;
}

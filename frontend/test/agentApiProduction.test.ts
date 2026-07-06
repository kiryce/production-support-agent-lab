import { afterEach, describe, expect, it, vi } from "vitest";
import { agentFetch, getConsoleConnection } from "../src/server/agentApi";

const ORIGINAL_ENV = { ...process.env };
const INTERNAL_API_KEY = "internal-api-key-with-32-byte-minimum";

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("production agent API gateway config", () => {
  it("signs production requests only when explicit actor config is present", async () => {
    useProductionFrontendEnv();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ ok: true }));

    const result = await agentFetch<{ ok: boolean }>("/api/v1/admin/tools", {
      query: { limit: 10 }
    });

    expect(result.ok).toBe(true);
    const [target, init] = fetchMock.mock.calls[0];
    const headers = init?.headers as Record<string, string>;
    expect(String(target)).toBe("http://agent.internal/api/v1/admin/tools?limit=10");
    expect(headers["X-Internal-Auth"]).toBe(INTERNAL_API_KEY);
    expect(headers["X-Actor-User-Id"]).toBe("console_operator");
    expect(headers["X-Actor-Roles"]).toBe("admin");
    expect(headers["X-Actor-Scopes"]).toBe("admin:read,monitor:read");
    expect(headers["X-Actor-Signature"]).toMatch(/^sha256=/);
    expect(headers["X-Request-Nonce"]).toBeTruthy();
    expect(headers["X-Request-Signature"]).toMatch(/^sha256=/);
  });

  it("exposes the explicit production actor in the console connection", () => {
    useProductionFrontendEnv();

    expect(getConsoleConnection()).toEqual({
      label: "Production API",
      authMode: "production",
      actorUserId: "console_operator",
      actorRole: "admin"
    });
  });

  it("fails closed when production actor scopes are missing", async () => {
    useProductionFrontendEnv();
    delete process.env.FRONTEND_ACTOR_SCOPES;
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(agentFetch("/api/v1/admin/tools")).rejects.toMatchObject({
      status: 500,
      detail: "FRONTEND_ACTOR_SCOPES"
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed for placeholder secrets and local tenant ids", async () => {
    useProductionFrontendEnv();
    process.env.APP_ACTOR_SIGNATURE_SECRET = "replace_with_real_actor_signature_secret";
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(agentFetch("/api/v1/admin/tools")).rejects.toMatchObject({
      status: 500,
      detail: "APP_ACTOR_SIGNATURE_SECRET"
    });

    process.env.APP_ACTOR_SIGNATURE_SECRET = "frontend-actor-secret-with-32-chars";
    process.env.APP_TENANT_ID = "demo_tenant";
    await expect(agentFetch("/api/v1/admin/tools")).rejects.toMatchObject({
      status: 500,
      detail: "APP_TENANT_ID"
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed when the internal API key is too short", async () => {
    useProductionFrontendEnv();
    process.env.APP_INTERNAL_API_KEY = "short-internal-key";
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(agentFetch("/api/v1/admin/tools")).rejects.toMatchObject({
      status: 500,
      detail: "APP_INTERNAL_API_KEY"
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

function useProductionFrontendEnv() {
  process.env.AGENT_API_BASE_URL = "http://agent.internal";
  process.env.FRONTEND_AUTH_MODE = "production";
  process.env.APP_TENANT_ID = "tenant_live";
  process.env.APP_INTERNAL_API_KEY = INTERNAL_API_KEY;
  process.env.APP_ACTOR_SIGNATURE_SECRET = "frontend-actor-secret-with-32-chars";
  process.env.FRONTEND_ACTOR_USER_ID = "console_operator";
  process.env.FRONTEND_ACTOR_ROLES = "admin";
  process.env.FRONTEND_ACTOR_SCOPES = "admin:read, monitor:read";
  process.env.FRONTEND_REQUEST_SIGNATURE_REQUIRED = "true";
}

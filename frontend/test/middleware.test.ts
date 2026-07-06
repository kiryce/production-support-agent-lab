import { NextRequest } from "next/server";
import { afterEach, describe, expect, it } from "vitest";
import { middleware } from "../middleware";

const ORIGINAL_ENV = { ...process.env };

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
});

describe("console middleware", () => {
  it("allows demo mode without browser credentials", () => {
    process.env.FRONTEND_AUTH_MODE = "demo";

    const response = middleware(request("/api/console/snapshot"));

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("fails closed in production when console credentials are missing", async () => {
    process.env.FRONTEND_AUTH_MODE = "production";
    delete process.env.FRONTEND_CONSOLE_USERNAME;
    delete process.env.FRONTEND_CONSOLE_PASSWORD;

    const response = middleware(request("/"));

    expect(response.status).toBe(401);
    expect(response.headers.get("www-authenticate")).toContain("Basic");
    expect(await response.text()).toBe("Console authentication is not configured.");
  });

  it("fails closed in production with placeholder or weak console credentials", async () => {
    process.env.FRONTEND_AUTH_MODE = "production";
    process.env.FRONTEND_CONSOLE_USERNAME = "operator";
    process.env.FRONTEND_CONSOLE_PASSWORD = "replace_with_real_console_password_min_16_chars";

    const placeholder = middleware(request("/"));
    process.env.FRONTEND_CONSOLE_PASSWORD = "short";
    const weak = middleware(request("/"));

    expect(placeholder.status).toBe(401);
    expect(weak.status).toBe(401);
    expect(await weak.text()).toBe("Console authentication is not configured.");
  });

  it("rejects production console requests without valid basic auth", async () => {
    process.env.FRONTEND_AUTH_MODE = "production";
    process.env.FRONTEND_CONSOLE_USERNAME = "operator";
    process.env.FRONTEND_CONSOLE_PASSWORD = "correct-password";

    const missing = middleware(request("/api/console/snapshot"));
    const wrong = middleware(
      request("/api/console/snapshot", {
        authorization: basic("operator", "wrong-password")
      })
    );

    expect(missing.status).toBe(401);
    expect(wrong.status).toBe(401);
    expect(await wrong.text()).toBe("Console authentication is required.");
  });

  it("allows production console requests with matching basic auth", () => {
    process.env.FRONTEND_AUTH_MODE = "production";
    process.env.FRONTEND_CONSOLE_USERNAME = "operator";
    process.env.FRONTEND_CONSOLE_PASSWORD = "correct-password";

    const response = middleware(
      request("/api/console/snapshot", {
        authorization: basic("operator", "correct-password")
      })
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });
});

function request(path: string, headers?: Record<string, string>) {
  return new NextRequest(`http://console.local${path}`, { headers });
}

function basic(user: string, password: string) {
  return `Basic ${Buffer.from(`${user}:${password}`, "utf-8").toString("base64")}`;
}

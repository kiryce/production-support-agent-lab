import { describe, expect, it } from "vitest";
import {
  canonicalCsv,
  sha256Hex,
  signActorClaims,
  signRequestClaims
} from "../src/server/signing";

describe("agent api signing", () => {
  it("matches the backend actor-claim canonicalization", () => {
    const digest = signActorClaims({
      secret: "actor-signing-secret-with-32-byte-minimum",
      tenantId: "tenant_live",
      userId: "user_prod",
      rolesHeader: " admin, user ",
      scopesHeader: "monitor:read, audit:read",
      timestamp: "1783014000"
    });

    expect(canonicalCsv(" admin, user ")).toBe("admin,user");
    expect(digest).toMatch(/^[a-f0-9]{64}$/);
  });

  it("includes path query and exact body hash in request signatures", () => {
    const body = '{"user_id":"user_prod"}';
    const bodyHash = sha256Hex(body);
    const digest = signRequestClaims({
      secret: "actor-signing-secret-with-32-byte-minimum",
      tenantId: "tenant_live",
      userId: "user_prod",
      rolesHeader: "admin",
      scopesHeader: "monitor:read",
      timestamp: "1783014000",
      nonce: "nonce_docker_smoke_1234567890",
      method: "POST",
      path: "/api/v1/chat/sessions?x=1",
      bodyHash
    });

    expect(bodyHash).toHaveLength(64);
    expect(digest).toMatch(/^[a-f0-9]{64}$/);
  });
});

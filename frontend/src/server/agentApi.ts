import type { JsonValue } from "@/src/shared/types";
import {
  canonicalCsv,
  formatSignature,
  nonce,
  sha256Hex,
  signActorClaims,
  signRequestClaims
} from "./signing";

type AgentMethod = "GET" | "POST";
type QueryValue = string | number | boolean | null | undefined;

const ADMIN_SCOPES = [
  "crm:read",
  "order:read",
  "shipping:read",
  "ticket:write",
  "kb:read",
  "admin:read",
  "audit:read",
  "events:read",
  "eval:run",
  "memory:replay",
  "monitor:read",
  "monitor:write"
].join(",");

export class AgentApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: string
  ) {
    super(message);
  }
}

export function getAuthMode(): "demo" | "production" {
  const explicit = process.env.FRONTEND_AUTH_MODE?.toLowerCase();
  if (explicit === "production") {
    return "production";
  }
  if (explicit === "demo") {
    return "demo";
  }
  return process.env.APP_ENV?.toLowerCase() === "production" ? "production" : "demo";
}

export function getConsoleConnection() {
  const authMode = getAuthMode();
  return {
    label:
      process.env.FRONTEND_CONNECTION_LABEL ??
      (authMode === "demo" ? "Local API" : "Production API"),
    authMode,
    actorUserId:
      authMode === "demo"
        ? process.env.DEMO_ACTOR_USER_ID ?? "user_demo"
        : process.env.FRONTEND_ACTOR_USER_ID ?? "console_operator",
    actorRole:
      authMode === "demo"
        ? process.env.DEMO_ACTOR_ROLE ?? "admin"
        : process.env.FRONTEND_ACTOR_ROLES ?? "admin"
  };
}

export async function agentFetch<T>(
  path: string,
  options: {
    method?: AgentMethod;
    query?: Record<string, QueryValue>;
    body?: JsonValue;
  } = {}
): Promise<T> {
  const method = options.method ?? "GET";
  const baseUrl = process.env.AGENT_API_BASE_URL ?? "http://127.0.0.1:8000";
  const target = new URL(path, baseUrl);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== null && value !== undefined) {
      target.searchParams.set(key, String(value));
    }
  }

  const bodyText = options.body === undefined ? "" : JSON.stringify(options.body);
  const headers = buildHeaders(method, `${target.pathname}${target.search}`, bodyText);
  const response = await fetch(target, {
    method,
    headers,
    body: bodyText ? bodyText : undefined,
    cache: "no-store"
  });
  const text = await response.text();
  const payload = parsePayload(text);

  if (!response.ok) {
    const detail =
      typeof payload === "object" &&
      payload !== null &&
      "detail" in payload &&
      typeof payload.detail === "string"
        ? payload.detail
        : text || response.statusText;
    throw new AgentApiError(
      `Agent API ${method} ${path} failed with ${response.status}`,
      response.status,
      detail
    );
  }

  return payload as T;
}

function buildHeaders(method: AgentMethod, pathWithQuery: string, bodyText: string) {
  const headers: Record<string, string> = {
    Accept: "application/json"
  };
  if (bodyText) {
    headers["Content-Type"] = "application/json";
  }

  if (getAuthMode() === "demo") {
    headers["X-Demo-User"] = process.env.DEMO_ACTOR_USER_ID ?? "user_demo";
    headers["X-Demo-Role"] = process.env.DEMO_ACTOR_ROLE ?? "admin";
    return headers;
  }

  const internalApiKey = requireEnv("APP_INTERNAL_API_KEY");
  const signatureSecret = requireEnv("APP_ACTOR_SIGNATURE_SECRET");
  const tenantId = process.env.APP_TENANT_ID ?? "demo_tenant";
  const userId = process.env.FRONTEND_ACTOR_USER_ID ?? "console_operator";
  const rolesHeader = canonicalCsv(process.env.FRONTEND_ACTOR_ROLES ?? "admin", "admin");
  const scopesHeader = canonicalCsv(process.env.FRONTEND_ACTOR_SCOPES ?? ADMIN_SCOPES);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const actorDigest = signActorClaims({
    secret: signatureSecret,
    tenantId,
    userId,
    rolesHeader,
    scopesHeader,
    timestamp
  });

  headers["X-Internal-Auth"] = internalApiKey;
  headers["X-Actor-User-Id"] = userId;
  headers["X-Actor-Roles"] = rolesHeader;
  headers["X-Actor-Scopes"] = scopesHeader;
  headers["X-Actor-Timestamp"] = timestamp;
  headers["X-Actor-Signature"] = formatSignature(actorDigest);

  if (process.env.FRONTEND_REQUEST_SIGNATURE_REQUIRED !== "false") {
    const requestNonce = nonce();
    const bodyHash = sha256Hex(bodyText);
    const requestDigest = signRequestClaims({
      secret: signatureSecret,
      tenantId,
      userId,
      rolesHeader,
      scopesHeader,
      timestamp,
      nonce: requestNonce,
      method,
      path: pathWithQuery,
      bodyHash
    });
    headers["X-Request-Nonce"] = requestNonce;
    headers["X-Request-Body-SHA256"] = bodyHash;
    headers["X-Request-Signature"] = formatSignature(requestDigest);
  }

  return headers;
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new AgentApiError(`${name} is required for production frontend auth`, 500, name);
  }
  return value;
}

function parsePayload(text: string): unknown {
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export function issueFrom(error: unknown) {
  if (error instanceof AgentApiError) {
    return { status: error.status, detail: error.detail };
  }
  return {
    status: 500,
    detail: error instanceof Error ? error.message : "Unknown console API error"
  };
}

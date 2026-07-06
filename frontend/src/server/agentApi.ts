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

const PLACEHOLDER_MARKERS = ["replace_with", "your_", "example", "..."];

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
  if (authMode === "production") {
    const config = productionFrontendConfig();
    return {
      label: process.env.FRONTEND_CONNECTION_LABEL ?? "Production API",
      authMode,
      actorUserId: config.userId,
      actorRole: config.rolesHeader
    };
  }
  return {
    label: process.env.FRONTEND_CONNECTION_LABEL ?? "Local API",
    authMode,
    actorUserId: process.env.DEMO_ACTOR_USER_ID ?? "user_demo",
    actorRole: process.env.DEMO_ACTOR_ROLE ?? "admin"
  };
}

export async function agentFetch<T>(
  path: string,
  options: {
    method?: AgentMethod;
    query?: Record<string, QueryValue>;
    body?: JsonValue;
    responseType?: "json" | "text";
  } = {}
): Promise<T> {
  const method = options.method ?? "GET";
  const authMode = getAuthMode();
  const productionConfig = authMode === "production" ? productionFrontendConfig() : null;
  const baseUrl =
    productionConfig?.baseUrl ?? process.env.AGENT_API_BASE_URL ?? "http://127.0.0.1:8000";
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
  const payload = options.responseType === "text" && response.ok ? text : parsePayload(text);

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

  const config = productionFrontendConfig();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const actorDigest = signActorClaims({
    secret: config.signatureSecret,
    tenantId: config.tenantId,
    userId: config.userId,
    rolesHeader: config.rolesHeader,
    scopesHeader: config.scopesHeader,
    timestamp
  });

  headers["X-Internal-Auth"] = config.internalApiKey;
  headers["X-Actor-User-Id"] = config.userId;
  headers["X-Actor-Roles"] = config.rolesHeader;
  headers["X-Actor-Scopes"] = config.scopesHeader;
  headers["X-Actor-Timestamp"] = timestamp;
  headers["X-Actor-Signature"] = formatSignature(actorDigest);

  if (process.env.FRONTEND_REQUEST_SIGNATURE_REQUIRED !== "false") {
    const requestNonce = nonce();
    const bodyHash = sha256Hex(bodyText);
    const requestDigest = signRequestClaims({
      secret: config.signatureSecret,
      tenantId: config.tenantId,
      userId: config.userId,
      rolesHeader: config.rolesHeader,
      scopesHeader: config.scopesHeader,
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

function productionFrontendConfig() {
  return {
    baseUrl: requireProductionEnv("AGENT_API_BASE_URL"),
    tenantId: requireProductionTenantId(),
    internalApiKey: requireProductionEnv("APP_INTERNAL_API_KEY"),
    signatureSecret: requireProductionEnv("APP_ACTOR_SIGNATURE_SECRET", { minLength: 32 }),
    userId: requireProductionEnv("FRONTEND_ACTOR_USER_ID"),
    rolesHeader: requireProductionCsv("FRONTEND_ACTOR_ROLES"),
    scopesHeader: requireProductionCsv("FRONTEND_ACTOR_SCOPES")
  };
}

function requireProductionEnv(name: string, options: { minLength?: number } = {}): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new AgentApiError(`${name} is required for production frontend auth`, 500, name);
  }
  if (looksLikePlaceholder(value)) {
    throw new AgentApiError(`${name} must not be a placeholder`, 500, name);
  }
  if (options.minLength && value.length < options.minLength) {
    throw new AgentApiError(
      `${name} must be at least ${options.minLength} characters`,
      500,
      name
    );
  }
  return value;
}

function requireProductionTenantId(): string {
  const tenantId = requireProductionEnv("APP_TENANT_ID");
  if (tenantId === "demo_tenant") {
    throw new AgentApiError(
      "APP_TENANT_ID must not be demo_tenant in production frontend auth",
      500,
      "APP_TENANT_ID"
    );
  }
  return tenantId;
}

function requireProductionCsv(name: string): string {
  const value = requireProductionEnv(name);
  const canonical = canonicalCsv(value);
  if (!canonical) {
    throw new AgentApiError(`${name} must include at least one value`, 500, name);
  }
  return canonical;
}

function looksLikePlaceholder(value: string) {
  const lowered = value.toLowerCase();
  return PLACEHOLDER_MARKERS.some((marker) => lowered.includes(marker));
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

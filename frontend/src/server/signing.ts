import crypto from "node:crypto";

export const ACTOR_SIGNATURE_PREFIX = "sha256=";
export const ACTOR_SIGNATURE_VERSION = "v1";
export const REQUEST_SIGNATURE_VERSION = "v1";

export function canonicalCsv(
  value: string | readonly string[] | null | undefined,
  defaultValue = ""
): string {
  const source = value ?? defaultValue;
  const items = Array.isArray(source) ? source : String(source).split(",");
  return items.map((item) => String(item).trim()).filter(Boolean).join(",");
}

export function sha256Hex(body: string | Buffer | null | undefined = ""): string {
  return crypto.createHash("sha256").update(body ?? "").digest("hex");
}

export function formatSignature(digest: string): string {
  return digest.startsWith(ACTOR_SIGNATURE_PREFIX)
    ? digest
    : `${ACTOR_SIGNATURE_PREFIX}${digest}`;
}

export function signActorClaims(input: {
  secret: string;
  tenantId: string;
  userId: string;
  rolesHeader: string | readonly string[] | null | undefined;
  scopesHeader: string | readonly string[] | null | undefined;
  timestamp: string;
}): string {
  const canonical = [
    ACTOR_SIGNATURE_VERSION,
    input.tenantId,
    input.userId,
    canonicalCsv(input.rolesHeader, "user"),
    canonicalCsv(input.scopesHeader),
    input.timestamp
  ].join("\n");
  return crypto.createHmac("sha256", input.secret).update(canonical).digest("hex");
}

export function signRequestClaims(input: {
  secret: string;
  tenantId: string;
  userId: string;
  rolesHeader: string | readonly string[] | null | undefined;
  scopesHeader: string | readonly string[] | null | undefined;
  timestamp: string;
  nonce: string;
  method: string;
  path: string;
  bodyHash: string;
}): string {
  const canonical = [
    REQUEST_SIGNATURE_VERSION,
    input.tenantId,
    input.userId,
    canonicalCsv(input.rolesHeader, "user"),
    canonicalCsv(input.scopesHeader),
    input.timestamp,
    input.nonce,
    input.method.toUpperCase(),
    input.path,
    input.bodyHash
  ].join("\n");
  return crypto.createHmac("sha256", input.secret).update(canonical).digest("hex");
}

export function nonce(): string {
  return crypto.randomBytes(24).toString("base64url");
}

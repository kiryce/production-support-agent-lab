import { NextRequest, NextResponse } from "next/server";

const CONSOLE_AUTH_REALM = "Production Support Agent Console";

export const config = {
  matcher: ["/", "/api/console/:path*"]
};

export function middleware(request: NextRequest) {
  if (getFrontendAuthMode() !== "production") {
    return NextResponse.next();
  }

  const expectedUser = process.env.FRONTEND_CONSOLE_USERNAME;
  const expectedPassword = process.env.FRONTEND_CONSOLE_PASSWORD;
  if (credentialsConfigError(expectedUser, expectedPassword)) {
    return unauthorized("Console authentication is not configured.");
  }

  if (!basicAuthMatches(request.headers.get("authorization"), expectedUser!, expectedPassword!)) {
    return unauthorized("Console authentication is required.");
  }

  return NextResponse.next();
}

function getFrontendAuthMode(): "demo" | "production" {
  const explicit = process.env.FRONTEND_AUTH_MODE?.toLowerCase();
  if (explicit === "production") {
    return "production";
  }
  if (explicit === "demo") {
    return "demo";
  }
  return process.env.APP_ENV?.toLowerCase() === "production" ? "production" : "demo";
}

function credentialsConfigError(user: string | undefined, password: string | undefined) {
  if (!user || !password || password.length < 16) {
    return true;
  }
  return looksLikePlaceholder(user) || looksLikePlaceholder(password);
}

function looksLikePlaceholder(value: string) {
  const lowered = value.toLowerCase();
  return ["replace_with", "your_", "example"].some((marker) => lowered.includes(marker));
}

function basicAuthMatches(
  authorization: string | null,
  expectedUser: string,
  expectedPassword: string
) {
  const prefix = "Basic ";
  if (!authorization?.startsWith(prefix)) {
    return false;
  }
  const credentials = decodeBasicCredentials(authorization.slice(prefix.length));
  if (!credentials) {
    return false;
  }
  return credentials.user === expectedUser && credentials.password === expectedPassword;
}

function decodeBasicCredentials(encoded: string) {
  try {
    const decoded = atob(encoded);
    const separatorIndex = decoded.indexOf(":");
    if (separatorIndex < 0) {
      return null;
    }
    return {
      user: decoded.slice(0, separatorIndex),
      password: decoded.slice(separatorIndex + 1)
    };
  } catch {
    return null;
  }
}

function unauthorized(message: string) {
  return new NextResponse(message, {
    status: 401,
    headers: {
      "Cache-Control": "no-store",
      "WWW-Authenticate": `Basic realm="${CONSOLE_AUTH_REALM}", charset="UTF-8"`
    }
  });
}

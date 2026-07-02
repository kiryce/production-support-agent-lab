from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from collections.abc import Sequence

from support_agent_lab.security.actor_signature import build_actor_headers, build_signed_request_headers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate HMAC-signed production actor headers for gateway smoke tests.",
    )
    parser.add_argument("--tenant-id", default=os.getenv("APP_TENANT_ID"))
    parser.add_argument("--internal-api-key", default=os.getenv("APP_INTERNAL_API_KEY"))
    parser.add_argument("--signature-secret", default=os.getenv("APP_ACTOR_SIGNATURE_SECRET"))
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--roles", default="user")
    parser.add_argument("--scopes", required=True)
    parser.add_argument("--timestamp")
    parser.add_argument("--nonce")
    parser.add_argument("--method", help="HTTP method to bind a request signature, for example POST.")
    parser.add_argument("--path", help="Path and query to bind, for example /api/v1/chat/messages.")
    parser.add_argument("--body", default="", help="Raw request body to hash and bind. Defaults to empty.")
    parser.add_argument("--body-file", help="Read the request body from a file instead of --body.")
    parser.add_argument("--format", choices=["plain", "curl", "json"], default="plain")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    missing = [
        name
        for name, value in [
            ("APP_TENANT_ID or --tenant-id", args.tenant_id),
            ("APP_INTERNAL_API_KEY or --internal-api-key", args.internal_api_key),
            ("APP_ACTOR_SIGNATURE_SECRET or --signature-secret", args.signature_secret),
        ]
        if not value
    ]
    if missing:
        parser.error(f"missing required values: {', '.join(missing)}")

    if bool(args.method) != bool(args.path):
        parser.error("--method and --path must be provided together for request signatures")
    body = Path(args.body_file).read_bytes() if args.body_file else args.body
    if args.method and args.path:
        headers = build_signed_request_headers(
            internal_api_key=args.internal_api_key,
            signature_secret=args.signature_secret,
            tenant_id=args.tenant_id,
            user_id=args.user_id,
            roles=args.roles,
            scopes=args.scopes,
            method=args.method,
            path=args.path,
            body=body,
            timestamp=args.timestamp,
            nonce=args.nonce,
        )
    else:
        headers = build_actor_headers(
            internal_api_key=args.internal_api_key,
            signature_secret=args.signature_secret,
            tenant_id=args.tenant_id,
            user_id=args.user_id,
            roles=args.roles,
            scopes=args.scopes,
            timestamp=args.timestamp,
        )

    if args.format == "json":
        print(json.dumps(headers, indent=2, ensure_ascii=False))
        return 0
    if args.format == "curl":
        for name, value in headers.items():
            print(f'-H "{name}: {value}"')
        return 0
    for name, value in headers.items():
        print(f"{name}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

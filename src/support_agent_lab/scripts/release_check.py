from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from support_agent_lab.config import Settings
from support_agent_lab.evals.suites import STAGING_EVAL_SUITES, EvalSuiteSpec
from support_agent_lab.security.actor_signature import build_signed_request_headers


SMOKE_ENV = {
    "APP_TENANT_ID": "tenant_live",
    "APP_INTERNAL_API_KEY": "internal-key",
    "APP_ACTOR_SIGNATURE_SECRET": "actor-signing-secret-with-32-byte-minimum",
}

AGENT_SCOPES = "crm:read,order:read,shipping:read,ticket:write,kb:read,feedback:write"
ADMIN_SCOPES = "admin:read,admin:write,events:read,monitor:read,audit:read,eval:read,feedback:read,memory:replay"
DOCKER_IMAGE = "production-support-agent-lab:release-check"
REQUIRED_FRONTEND_ENV_VARS = (
    "FRONTEND_CONSOLE_USERNAME",
    "FRONTEND_CONSOLE_PASSWORD",
    "FRONTEND_ACTOR_USER_ID",
    "FRONTEND_ACTOR_ROLES",
    "FRONTEND_ACTOR_SCOPES",
    "APP_TENANT_ID",
    "APP_INTERNAL_API_KEY",
    "APP_ACTOR_SIGNATURE_SECRET",
)
REQUIRED_FRONTEND_LINT_DEPS = (
    "@next/eslint-plugin-next",
    "eslint-plugin-react",
    "eslint-plugin-react-hooks",
)


@dataclass(frozen=True)
class GateStep:
    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)


def build_steps(include_docker: bool = False) -> list[GateStep]:
    steps = [
        GateStep("package health", [sys.executable, "-m", "pip", "check"]),
        GateStep(
            "production request signer smoke",
            [
                sys.executable,
                "-m",
                "support_agent_lab.scripts.sign_actor_headers",
                "--user-id",
                "user_prod",
                "--roles",
                "user",
                "--scopes",
                AGENT_SCOPES,
                "--timestamp",
                "1783014000",
                "--nonce",
                "nonce_release_check_1234567890",
                "--method",
                "POST",
                "--path",
                "/api/v1/chat/sessions",
                "--body",
                '{"user_id":"user_prod"}',
                "--format",
                "json",
            ],
            env=SMOKE_ENV,
        ),
        GateStep("unit tests", [sys.executable, "-m", "pytest"]),
        *[_release_eval_step(suite) for suite in STAGING_EVAL_SUITES],
    ]
    if include_docker:
        steps.extend(
            [
                GateStep("docker image build", ["docker", "build", "-t", DOCKER_IMAGE, "."]),
                GateStep(
                    "docker signer smoke",
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-e",
                        f"APP_TENANT_ID={SMOKE_ENV['APP_TENANT_ID']}",
                        "-e",
                        f"APP_INTERNAL_API_KEY={SMOKE_ENV['APP_INTERNAL_API_KEY']}",
                        "-e",
                        f"APP_ACTOR_SIGNATURE_SECRET={SMOKE_ENV['APP_ACTOR_SIGNATURE_SECRET']}",
                        DOCKER_IMAGE,
                        "python",
                        "scripts/sign_actor_headers.py",
                        "--user-id",
                        "user_prod",
                        "--roles",
                        "user",
                        "--scopes",
                        AGENT_SCOPES,
                        "--timestamp",
                        "1783014000",
                        "--nonce",
                        "nonce_docker_release_check_1234567890",
                        "--method",
                        "POST",
                        "--path",
                        "/api/v1/chat/sessions",
                        "--body",
                        '{"user_id":"user_prod"}',
                        "--format",
                        "json",
                    ],
                ),
            ]
        )
    return steps


def _release_eval_step(suite: EvalSuiteSpec) -> GateStep:
    if suite.runner == "agent":
        command = [sys.executable, "scripts/run_eval.py", suite.path]
    elif suite.runner == "monitor":
        command = [sys.executable, "scripts/run_monitor_eval.py", suite.path]
    else:
        command = [sys.executable, "scripts/run_retrieval_eval.py", suite.path]
    return GateStep(suite.release_step_name, command)


def validate_repo_root(root: Path) -> None:
    required_paths = [
        "pyproject.toml",
        "scripts/run_eval.py",
        "scripts/run_monitor_eval.py",
        "scripts/run_retrieval_eval.py",
        "scripts/event_store_ops.py",
        "docker-compose.yml",
        "frontend/middleware.ts",
        "frontend/package.json",
        "frontend/pnpm-lock.yaml",
        *[suite.path for suite in STAGING_EVAL_SUITES],
    ]
    missing = [path for path in required_paths if not (root / path).exists()]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"release check must run from the repository root; missing: {joined}")


def validate_deployment_policy(root: Path) -> None:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised only with incomplete dev installs
        raise RuntimeError("PyYAML is required for deployment policy checks; install .[dev]") from exc

    compose_path = root / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    services = compose.get("services") or {}
    app = services.get("app") or {}
    frontend = services.get("frontend") or {}

    if app.get("ports") != ["127.0.0.1:8000:8000"]:
        raise RuntimeError("docker-compose app port must be bound to 127.0.0.1:8000:8000")
    if frontend.get("ports") != ["127.0.0.1:3000:3000"]:
        raise RuntimeError("docker-compose frontend port must be bound to 127.0.0.1:3000:3000")

    frontend_env = frontend.get("environment") or {}
    for name in REQUIRED_FRONTEND_ENV_VARS:
        value = frontend_env.get(name)
        if not isinstance(value, str) or not value.startswith(f"${{{name}:?") or ":-" in value:
            raise RuntimeError(f"docker-compose frontend {name} must use required interpolation")

    package_path = root / "frontend" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    dev_dependencies = package.get("devDependencies") or {}
    lock_text = (root / "frontend" / "pnpm-lock.yaml").read_text(encoding="utf-8")
    for name in REQUIRED_FRONTEND_LINT_DEPS:
        if name not in dev_dependencies:
            raise RuntimeError(f"frontend package.json must declare {name} for reproducible lint")
        if name not in lock_text:
            raise RuntimeError(f"frontend pnpm-lock.yaml must include {name} for frozen installs")

    middleware = (root / "frontend" / "middleware.ts").read_text(encoding="utf-8").lower()
    for token in ("sec-fetch-site", "origin", "same-origin", "unsafe_methods"):
        if token not in middleware:
            raise RuntimeError("frontend middleware must keep same-origin write protection")


def validate_production_config(root: Path) -> None:
    env_file = root / ".env"
    settings = Settings(_env_file=env_file if env_file.exists() else None)
    settings.validate_production_ready()


def load_settings(root: Path) -> Settings:
    env_file = root / ".env"
    return Settings(_env_file=env_file if env_file.exists() else None)


def production_headers(
    settings: Settings,
    user_id: str,
    roles: str,
    scopes: str,
    *,
    method: str,
    path: str,
    body: bytes | str = b"",
) -> dict[str, str]:
    if not settings.app_internal_api_key or not settings.app_actor_signature_secret:
        raise RuntimeError("APP_INTERNAL_API_KEY and APP_ACTOR_SIGNATURE_SECRET are required")
    return build_signed_request_headers(
        internal_api_key=settings.app_internal_api_key,
        signature_secret=settings.app_actor_signature_secret,
        tenant_id=settings.app_tenant_id,
        user_id=user_id,
        roles=roles,
        scopes=scopes,
        method=method,
        path=path,
        body=body,
    )


def run_production_smoke(
    *,
    root: Path,
    base_url: str,
    user_id: str,
    admin_user_id: str,
    message: str,
    timeout_seconds: float,
) -> None:
    settings = load_settings(root)
    settings.validate_production_ready()
    base_url = base_url.rstrip("/")

    with httpx.Client(base_url=base_url, timeout=timeout_seconds) as client:
        ready = client.get("/api/v1/ready?deep=true")
        _require_json_status(ready, 200, "deep readiness")
        ready_body = ready.json()
        if ready_body.get("status") != "ok":
            raise RuntimeError(f"deep readiness returned {ready_body.get('status')}: {ready_body}")

        session_body = _json_body({"user_id": user_id})
        user_headers = production_headers(
            settings,
            user_id=user_id,
            roles="user",
            scopes=AGENT_SCOPES,
            method="POST",
            path="/api/v1/chat/sessions",
            body=session_body,
        )
        user_headers["Content-Type"] = "application/json"
        tampered_headers = dict(user_headers)
        tampered_headers["X-Actor-Scopes"] = f"{AGENT_SCOPES},admin:read"
        tampered = client.post(
            "/api/v1/chat/sessions",
            headers=tampered_headers,
            content=session_body,
        )
        if tampered.status_code != 401:
            raise RuntimeError(
                "signed actor tamper check failed: "
                f"expected 401 after changing scopes, got {tampered.status_code} {tampered.text[:300]}"
            )

        session = client.post(
            "/api/v1/chat/sessions",
            headers=user_headers,
            content=session_body,
        )
        _require_json_status(session, 200, "create chat session")
        conversation_id = session.json()["conversation_id"]

        chat_body = _json_body(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "content": message,
            }
        )
        chat_headers = production_headers(
            settings,
            user_id=user_id,
            roles="user",
            scopes=AGENT_SCOPES,
            method="POST",
            path="/api/v1/chat/messages",
            body=chat_body,
        )
        chat_headers["Content-Type"] = "application/json"
        chat = client.post(
            "/api/v1/chat/messages",
            headers=chat_headers,
            content=chat_body,
        )
        _require_json_status(chat, 200, "chat message")
        trace_id = chat.json()["trace_id"]

        audit_path = f"/api/v1/admin/tools/audit?trace_id={trace_id}&limit=100"
        audit = client.get(
            audit_path,
            headers=production_headers(
                settings,
                user_id=admin_user_id,
                roles="admin",
                scopes=ADMIN_SCOPES,
                method="GET",
                path=audit_path,
            ),
        )
        _require_json_status(audit, 200, "tool audit lookup")

        audit_summary_path = f"/api/v1/admin/tools/audit/summary?trace_id={trace_id}"
        audit_summary = client.get(
            audit_summary_path,
            headers=production_headers(
                settings,
                user_id=admin_user_id,
                roles="admin",
                scopes=ADMIN_SCOPES,
                method="GET",
                path=audit_summary_path,
            ),
        )
        _require_json_status(audit_summary, 200, "tool audit summary lookup")
        if audit_summary.json().get("total_calls", 0) <= 0:
            raise RuntimeError("tool audit summary did not include persisted calls")

        incident_path = f"/api/v1/admin/incidents/runs/{trace_id}?include_memory=true"
        incident = client.get(
            incident_path,
            headers=production_headers(
                settings,
                user_id=admin_user_id,
                roles="admin",
                scopes=ADMIN_SCOPES,
                method="GET",
                path=incident_path,
            ),
        )
        _require_json_status(incident, 200, "incident bundle lookup")
        incident_body = incident.json()
        if incident_body.get("run", {}).get("id") != trace_id:
            raise RuntimeError(f"incident bundle did not return run {trace_id}")

    print(f"production smoke passed for trace {trace_id}")


def _require_json_status(response: httpx.Response, expected: int, step_name: str) -> None:
    if response.status_code != expected:
        raise RuntimeError(
            f"{step_name} failed: expected HTTP {expected}, got {response.status_code} {response.text[:500]}"
        )
    try:
        response.json()
    except ValueError as exc:
        raise RuntimeError(f"{step_name} did not return JSON: {response.text[:500]}") from exc


def _json_body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def run_step(step: GateStep, root: Path) -> int:
    env = os.environ.copy()
    env.update(step.env)
    print(f"\n==> {step.name}")
    print(" ".join(step.command))
    completed = subprocess.run(step.command, cwd=root, env=env, check=False)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic release gate used before publishing this agent service.",
    )
    parser.add_argument("--cwd", default=".", help="Repository root. Defaults to the current directory.")
    parser.add_argument(
        "--production-config",
        action="store_true",
        help="Also validate the current .env/env vars as production-ready. This does not call upstream APIs.",
    )
    parser.add_argument(
        "--include-docker",
        action="store_true",
        help="Also build the production Docker image and run the signer smoke inside it.",
    )
    parser.add_argument(
        "--prod-smoke",
        action="store_true",
        help="After deterministic checks, call a real deployed production service. Requires --base-url.",
    )
    parser.add_argument("--base-url", help="Base URL for --prod-smoke, for example https://agent.example.com.")
    parser.add_argument("--smoke-user-id", default="user_prod", help="Existing production/staging user id.")
    parser.add_argument("--smoke-admin-id", default="admin_prod", help="Existing production/staging admin actor id.")
    parser.add_argument(
        "--smoke-message",
        default="Where is my most recent order?",
        help="Message sent during --prod-smoke. Pick a safe staging customer query.",
    )
    parser.add_argument(
        "--smoke-timeout-seconds",
        type=float,
        default=30.0,
        help="HTTP timeout for each --prod-smoke request.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.cwd).resolve()
    try:
        validate_repo_root(root)
        print("==> deployment policy validation")
        validate_deployment_policy(root)
        if args.prod_smoke and not args.base_url:
            raise RuntimeError("--prod-smoke requires --base-url")
        if args.production_config:
            print("==> production config validation")
            validate_production_config(root)
    except RuntimeError as exc:
        print(f"release check configuration failed: {exc}", file=sys.stderr)
        return 2

    for step in build_steps(include_docker=args.include_docker):
        exit_code = run_step(step, root)
        if exit_code != 0:
            print(f"\nrelease check failed at: {step.name}", file=sys.stderr)
            return exit_code

    if args.prod_smoke:
        try:
            print("\n==> production smoke")
            run_production_smoke(
                root=root,
                base_url=args.base_url,
                user_id=args.smoke_user_id,
                admin_user_id=args.smoke_admin_id,
                message=args.smoke_message,
                timeout_seconds=args.smoke_timeout_seconds,
            )
        except RuntimeError as exc:
            print(f"\nproduction smoke failed: {exc}", file=sys.stderr)
            return 1

    print("\nrelease check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

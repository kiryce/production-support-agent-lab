import json
from types import SimpleNamespace

import httpx
import pytest
import yaml

from support_agent_lab.config import Settings
from support_agent_lab.scripts import release_check


def test_default_release_gate_covers_all_regression_suites():
    steps = release_check.build_steps()
    names = [step.name for step in steps]

    assert names == [
        "package health",
        "production request signer smoke",
        "unit tests",
        "golden eval",
        "security regression eval",
        "tool failure regression eval",
        "memory multiturn regression eval",
        "routing regression eval",
        "monitor regression eval",
        "retrieval challenge eval",
    ]
    assert all("docker" not in step.name for step in steps)


def test_docker_release_gate_steps_are_opt_in():
    steps = release_check.build_steps(include_docker=True)
    names = [step.name for step in steps]
    docker_signer = steps[-1].command

    assert names[-2:] == ["docker image build", "docker signer smoke"]
    assert "--nonce" in docker_signer
    assert "--method" in docker_signer
    assert "--path" in docker_signer
    assert "--body" in docker_signer


def test_release_gate_stops_at_first_failed_step(monkeypatch):
    calls = []

    def fake_run(command, cwd, env, check):
        calls.append(command)
        return SimpleNamespace(returncode=0 if len(calls) == 1 else 7)

    monkeypatch.setattr(release_check.subprocess, "run", fake_run)

    exit_code = release_check.main(["--cwd", "."])

    assert exit_code == 7
    assert len(calls) == 2


def test_release_gate_returns_configuration_error_for_bad_root(tmp_path):
    exit_code = release_check.main(["--cwd", str(tmp_path)])

    assert exit_code == 2


def test_prod_smoke_requires_explicit_base_url(monkeypatch):
    monkeypatch.setattr(release_check, "run_step", lambda step, root: 0)

    exit_code = release_check.main(["--cwd", ".", "--prod-smoke"])

    assert exit_code == 2


def test_prod_smoke_ops_requires_prod_smoke():
    exit_code = release_check.main(["--cwd", ".", "--prod-smoke-ops"])

    assert exit_code == 2


def test_smoke_only_requires_prod_smoke():
    exit_code = release_check.main(["--cwd", ".", "--smoke-only"])

    assert exit_code == 2


def test_smoke_only_skips_local_steps_and_can_require_ops_readiness(monkeypatch):
    def fail_if_local_step_runs(step, root):
        raise AssertionError(f"unexpected local release gate step: {step.name}")

    captured = {}
    monkeypatch.setattr(release_check, "run_step", fail_if_local_step_runs)
    monkeypatch.setattr(release_check, "validate_production_config", lambda root: None)

    def fake_smoke(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(release_check, "run_production_smoke", fake_smoke)

    exit_code = release_check.main(
        [
            "--cwd",
            ".",
            "--smoke-only",
            "--production-config",
            "--prod-smoke",
            "--prod-smoke-ops",
            "--base-url",
            "https://staging.agent.test",
        ]
    )

    assert exit_code == 0
    assert captured["base_url"] == "https://staging.agent.test"
    assert captured["ops_readiness"] is True


def test_production_smoke_can_require_ops_readiness(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *, base_url, timeout):
            assert base_url == "https://staging.agent.test"
            assert timeout == 3.0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, path, headers=None):
            calls.append(("GET", path, headers))
            if path == "/api/v1/ready?deep=true&ops=true":
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "deep": True,
                        "ops": True,
                        "checks": [
                            {"name": "config", "status": "ok"},
                            {"name": "alert_dispatcher_worker", "status": "skipped"},
                            {"name": "monitor_review_worker", "status": "ok"},
                            {"name": "audit_export_batch", "status": "ok"},
                        ],
                    },
                )
            if path.startswith("/api/v1/admin/tools/audit/summary"):
                return httpx.Response(200, json={"total_calls": 1})
            if path.startswith("/api/v1/admin/tools/audit"):
                return httpx.Response(200, json=[])
            if path.startswith("/api/v1/admin/incidents/runs/run_smoke"):
                return httpx.Response(200, json={"run": {"id": "run_smoke"}})
            raise AssertionError(f"unexpected GET {path}")

        def post(self, path, headers=None, content=None):
            calls.append(("POST", path, headers))
            if path == "/api/v1/chat/sessions" and "admin:read" in headers.get("X-Actor-Scopes", ""):
                return httpx.Response(401, json={"detail": "bad signature"})
            if path == "/api/v1/chat/sessions":
                return httpx.Response(200, json={"conversation_id": "conv_smoke"})
            if path == "/api/v1/chat/messages":
                return httpx.Response(200, json={"trace_id": "run_smoke"})
            raise AssertionError(f"unexpected POST {path}")

    monkeypatch.setattr(release_check, "load_settings", lambda root: _production_settings())
    monkeypatch.setattr(release_check.httpx, "Client", FakeClient)

    release_check.run_production_smoke(
        root=release_check.Path("."),
        base_url="https://staging.agent.test/",
        user_id="user_prod",
        admin_user_id="admin_prod",
        message="Where is my order?",
        timeout_seconds=3.0,
        ops_readiness=True,
    )

    assert calls[0][0:2] == ("GET", "/api/v1/ready?deep=true&ops=true")


def test_ops_readiness_confirmation_rejects_older_response_shape():
    with pytest.raises(RuntimeError, match="ops=true"):
        release_check._require_ops_readiness_confirmed(
            {
                "status": "ok",
                "deep": True,
                "checks": [
                    {"name": "config", "status": "ok"},
                ],
            }
        )


def test_ops_readiness_confirmation_requires_async_loop_checks():
    with pytest.raises(RuntimeError, match="missing checks"):
        release_check._require_ops_readiness_confirmed(
            {
                "status": "ok",
                "deep": True,
                "ops": True,
                "checks": [
                    {"name": "config", "status": "ok"},
                    {"name": "monitor_review_worker", "status": "ok"},
                    {"name": "audit_export_batch", "status": "ok"},
                ],
            }
        )


def test_deployment_policy_accepts_current_repo():
    release_check.validate_deployment_policy(release_check.Path("."))


def test_tag_release_runs_ops_smoke_before_creating_github_release():
    workflow = yaml.safe_load(release_check.Path(".github/workflows/release.yml").read_text(encoding="utf-8"))
    release_job = workflow["jobs"]["release"]
    steps = release_job["steps"]
    names = [step.get("name") for step in steps]
    smoke_index = names.index("Staging ops smoke")
    github_release_index = names.index("Create GitHub release")
    smoke_step = steps[smoke_index]

    assert release_job["environment"] == "staging"
    assert smoke_index < github_release_index
    assert "--smoke-only" in smoke_step["run"]
    assert "--prod-smoke" in smoke_step["run"]
    assert "--prod-smoke-ops" in smoke_step["run"]
    assert "STAGING_AGENT_BASE_URL" in smoke_step["env"]


def test_deployment_policy_rejects_public_compose_ports(tmp_path):
    _write_policy_tree(tmp_path)
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        compose_path.read_text(encoding="utf-8").replace("127.0.0.1:8000:8000", "8000:8000"),
        encoding="utf-8",
    )

    try:
        release_check.validate_deployment_policy(tmp_path)
    except RuntimeError as exc:
        assert "127.0.0.1:8000:8000" in str(exc)
    else:
        raise AssertionError("expected deployment policy to reject public backend port")


def test_deployment_policy_rejects_default_frontend_actor_scope(tmp_path):
    _write_policy_tree(tmp_path)
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        compose_path.read_text(encoding="utf-8").replace(
            "${FRONTEND_ACTOR_SCOPES:?set FRONTEND_ACTOR_SCOPES}",
            "${FRONTEND_ACTOR_SCOPES:-admin:read,admin:write}",
        ),
        encoding="utf-8",
    )

    try:
        release_check.validate_deployment_policy(tmp_path)
    except RuntimeError as exc:
        assert "FRONTEND_ACTOR_SCOPES" in str(exc)
    else:
        raise AssertionError("expected deployment policy to reject default frontend scopes")


def _write_policy_tree(root):
    frontend_dir = root / "frontend"
    frontend_dir.mkdir()
    (root / "docker-compose.yml").write_text(
        """
services:
  app:
    ports:
      - "127.0.0.1:8000:8000"
  frontend:
    environment:
      FRONTEND_CONSOLE_USERNAME: ${FRONTEND_CONSOLE_USERNAME:?set FRONTEND_CONSOLE_USERNAME}
      FRONTEND_CONSOLE_PASSWORD: ${FRONTEND_CONSOLE_PASSWORD:?set FRONTEND_CONSOLE_PASSWORD}
      FRONTEND_ACTOR_USER_ID: ${FRONTEND_ACTOR_USER_ID:?set FRONTEND_ACTOR_USER_ID}
      FRONTEND_ACTOR_ROLES: ${FRONTEND_ACTOR_ROLES:?set FRONTEND_ACTOR_ROLES}
      FRONTEND_ACTOR_SCOPES: ${FRONTEND_ACTOR_SCOPES:?set FRONTEND_ACTOR_SCOPES}
      APP_TENANT_ID: ${APP_TENANT_ID:?set APP_TENANT_ID}
      APP_INTERNAL_API_KEY: ${APP_INTERNAL_API_KEY:?set APP_INTERNAL_API_KEY}
      APP_ACTOR_SIGNATURE_SECRET: ${APP_ACTOR_SIGNATURE_SECRET:?set APP_ACTOR_SIGNATURE_SECRET}
    ports:
      - "127.0.0.1:3000:3000"
""".lstrip(),
        encoding="utf-8",
    )
    (frontend_dir / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {
                    "@next/eslint-plugin-next": "15.5.20",
                    "eslint-plugin-react": "7.37.5",
                    "eslint-plugin-react-hooks": "5.2.0",
                }
            }
        ),
        encoding="utf-8",
    )
    (frontend_dir / "pnpm-lock.yaml").write_text(
        """
importers:
  .:
    devDependencies:
      '@next/eslint-plugin-next':
        version: 15.5.20
      eslint-plugin-react:
        version: 7.37.5
      eslint-plugin-react-hooks:
        version: 5.2.0
""".lstrip(),
        encoding="utf-8",
    )
    (frontend_dir / "middleware.ts").write_text(
        "const UNSAFE_METHODS = new Set();\n"
        "request.headers.get('origin');\n"
        "request.headers.get('sec-fetch-site') === 'same-origin';\n",
        encoding="utf-8",
    )


def _production_settings() -> Settings:
    return Settings(
        app_env="production",
        app_tenant_id="tenant_live",
        app_model_provider="openai",
        openai_api_key="sk-test",
        app_business_api_base_url="https://business.internal.test",
        app_business_api_key="business-token",
        app_knowledge_api_base_url="https://knowledge.internal.test",
        app_knowledge_api_key="knowledge-token",
        app_internal_api_key="internal-api-key-with-32-byte-minimum",
        app_actor_signature_secret="actor-signing-secret-with-32-byte-minimum",
        app_database_url="sqlite:///./data/staging/support-agent-lab.db",
    )

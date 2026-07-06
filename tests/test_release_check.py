import json
from types import SimpleNamespace

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


def test_deployment_policy_accepts_current_repo():
    release_check.validate_deployment_policy(release_check.Path("."))


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

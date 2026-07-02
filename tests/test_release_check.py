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

    assert names[-2:] == ["docker image build", "docker signer smoke"]


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

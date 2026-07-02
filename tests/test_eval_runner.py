import pytest

from support_agent_lab.bootstrap import create_container
from support_agent_lab.evals.runner import load_cases, run_cases


@pytest.mark.asyncio
async def test_golden_core_eval_passes():
    container = create_container()
    cases = load_cases("examples/evals/golden_core.json")

    report = await run_cases(cases, container.orchestrator)

    assert report.total == 5
    assert report.passed == 5


@pytest.mark.asyncio
async def test_security_regression_eval_passes():
    container = create_container()
    cases = load_cases("examples/evals/security_regression.json")

    report = await run_cases(cases, container.orchestrator)

    assert report.total == 2
    assert report.passed == 2


@pytest.mark.asyncio
async def test_tool_failure_regression_eval_passes():
    container = create_container()
    cases = load_cases("examples/evals/tool_failure_regression.json")

    report = await run_cases(cases, container.orchestrator)

    assert report.total == 5
    assert report.passed == 5


@pytest.mark.asyncio
async def test_routing_regression_eval_passes():
    container = create_container()
    cases = load_cases("examples/evals/routing_regression.json")

    report = await run_cases(cases, container.orchestrator)

    assert report.total == 10
    assert report.passed == 10

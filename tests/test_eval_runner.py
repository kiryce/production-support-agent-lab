import pytest

from support_agent_lab.bootstrap import create_container
from support_agent_lab.evals.monitor_runner import load_suite as load_monitor_suite
from support_agent_lab.evals.monitor_runner import report_exit_code as monitor_report_exit_code
from support_agent_lab.evals.monitor_runner import run_suite as run_monitor_suite
from support_agent_lab.evals.runner import report_exit_code as eval_report_exit_code
from support_agent_lab.evals.runner import load_cases, run_cases


@pytest.mark.asyncio
async def test_golden_core_eval_passes():
    container = create_container()
    cases = load_cases("examples/evals/golden_core.json")

    report = await run_cases(cases, container.orchestrator)

    assert report.total == 5
    assert report.passed == 5
    assert eval_report_exit_code(report) == 0
    assert eval_report_exit_code(report.model_copy(update={"passed": 4})) == 1


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
async def test_memory_multiturn_regression_eval_passes():
    container = create_container()
    cases = load_cases("examples/evals/memory_multiturn_regression.json")

    report = await run_cases(cases, container.orchestrator)

    assert report.total == 2
    assert report.passed == 2


@pytest.mark.asyncio
async def test_routing_regression_eval_passes():
    container = create_container()
    cases = load_cases("examples/evals/routing_regression.json")

    report = await run_cases(cases, container.orchestrator)

    assert report.total == 10
    assert report.passed == 10


@pytest.mark.asyncio
async def test_monitor_regression_eval_passes():
    container = create_container()
    suite = load_monitor_suite("examples/evals/monitor_regression.json")

    report = await run_monitor_suite(suite, container.orchestrator)

    assert report.passed
    assert report.summary.total_events == 5
    assert report.summary.by_failure_type["PROMPT_INJECTION_ATTEMPT"] == 1
    assert report.summary.by_failure_type["FORBIDDEN"] == 1
    assert report.summary.by_failure_type["TIMEOUT"] == 1
    assert monitor_report_exit_code(report) == 0
    assert monitor_report_exit_code(report.model_copy(update={"passed": False})) == 1

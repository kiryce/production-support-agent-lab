from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from support_agent_lab.bootstrap import create_container
from support_agent_lab.models import EvalToolFault
from support_agent_lab.monitoring.monitor import MonitorAlert, MonitorSummary
from support_agent_lab.tools.registry import ToolFault, ToolFaultProfile, ToolRegistry


class MonitorEvalTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    user_id: str = "user_demo"
    content: str
    tool_faults: list[EvalToolFault] = Field(default_factory=list)


class MonitorAlertExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["P0", "P1", "P2", "P3"]
    reason_contains: str
    min_count: int = Field(default=1, ge=1)


class MonitorEvalExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_events: int
    by_risk_level: dict[str, int] = Field(default_factory=dict)
    by_intent: dict[str, int] = Field(default_factory=dict)
    by_failure_type: dict[str, int] = Field(default_factory=dict)
    grounded_rate: float | None = None
    policy_compliance_rate: float | None = None
    human_review_rate: float | None = None
    required_alerts: list[MonitorAlertExpectation] = Field(default_factory=list)


class MonitorEvalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str
    scenario: str
    turns: list[MonitorEvalTurn]
    expected: MonitorEvalExpectation


class MonitorEvalReport(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    suite_id: str
    passed: bool
    score: float
    failures: list[str] = Field(default_factory=list)
    summary: MonitorSummary


def load_suite(path: str | Path) -> MonitorEvalSuite:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return MonitorEvalSuite.model_validate(data)


async def run_suite(suite: MonitorEvalSuite, orchestrator) -> MonitorEvalReport:
    orchestrator.monitor.events.clear()
    previous_fault_profile = orchestrator.tools.fault_profile
    try:
        for turn in suite.turns:
            orchestrator.tools.fault_profile = _build_fault_profile(turn.tool_faults, orchestrator.tools.registry)
            await orchestrator.handle_message(
                conversation_id=turn.conversation_id,
                user_id=turn.user_id,
                text=turn.content,
            )
    finally:
        orchestrator.tools.fault_profile = previous_fault_profile

    summary = orchestrator.monitor.summarize()
    failures = _check_summary(suite.expected, summary)
    score = max(0.0, 1.0 - 0.2 * len(failures))
    return MonitorEvalReport(
        suite_id=suite.suite_id,
        passed=not failures,
        score=score,
        failures=failures,
        summary=summary,
    )


def _build_fault_profile(tool_faults: list[EvalToolFault], registry: ToolRegistry) -> ToolFaultProfile | None:
    if not tool_faults:
        return None
    profile = ToolFaultProfile()
    for fault in tool_faults:
        registry.get(fault.tool_name)
        for _ in range(fault.times):
            profile.add(
                fault.tool_name,
                ToolFault(
                    error_code=fault.error_code,
                    message=fault.message,
                    retryable=fault.retryable,
                    delay_ms=fault.delay_ms,
                ),
            )
    return profile


def _check_summary(expected: MonitorEvalExpectation, summary: MonitorSummary) -> list[str]:
    failures: list[str] = []
    if summary.total_events != expected.total_events:
        failures.append(f"total_events expected {expected.total_events}, got {summary.total_events}")
    _check_counter("by_risk_level", expected.by_risk_level, summary.by_risk_level, failures)
    _check_counter("by_intent", expected.by_intent, summary.by_intent, failures)
    _check_counter("by_failure_type", expected.by_failure_type, summary.by_failure_type, failures)
    _check_rate("grounded_rate", expected.grounded_rate, summary.grounded_rate, failures)
    _check_rate(
        "policy_compliance_rate",
        expected.policy_compliance_rate,
        summary.policy_compliance_rate,
        failures,
    )
    _check_rate("human_review_rate", expected.human_review_rate, summary.human_review_rate, failures)
    for alert in expected.required_alerts:
        if not _has_alert(summary.alerts, alert):
            failures.append(
                f"required alert missing: severity={alert.severity}, reason_contains={alert.reason_contains}"
            )
    return failures


def _check_counter(
    name: str,
    expected: dict[str, int],
    observed: dict[str, int],
    failures: list[str],
) -> None:
    for key, count in expected.items():
        got = observed.get(key, 0)
        if got != count:
            failures.append(f"{name}.{key} expected {count}, got {got}")


def _check_rate(name: str, expected: float | None, observed: float, failures: list[str]) -> None:
    if expected is not None and abs(observed - expected) > 0.0001:
        failures.append(f"{name} expected {expected}, got {observed}")


def _has_alert(alerts: list[MonitorAlert], expected: MonitorAlertExpectation) -> bool:
    return any(
        alert.severity == expected.severity
        and alert.count >= expected.min_count
        and expected.reason_contains in alert.reason
        for alert in alerts
    )


async def async_main(path: str) -> MonitorEvalReport:
    container = create_container()
    suite = load_suite(path)
    report = await run_suite(suite, container.orchestrator)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return report


def report_exit_code(report: MonitorEvalReport) -> int:
    return 0 if report.passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline monitor evals.")
    parser.add_argument("path", nargs="?", default="examples/evals/monitor_regression.json")
    args = parser.parse_args(argv)
    report = asyncio.run(async_main(args.path))
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())

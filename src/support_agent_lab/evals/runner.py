from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from support_agent_lab.bootstrap import create_container
from support_agent_lab.models import EvalCase, EvalCaseResult, EvalReport, EvalTurnObservation, ToolStatus, new_id
from support_agent_lab.tools.registry import ToolFault, ToolFaultProfile, ToolRegistry


def load_cases(path: str | Path) -> list[EvalCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [EvalCase.model_validate(item) for item in data]


async def run_cases(cases: list[EvalCase], orchestrator) -> EvalReport:
    results: list[EvalCaseResult] = []
    for case in cases:
        previous_fault_profile = orchestrator.tools.fault_profile
        orchestrator.tools.fault_profile = _build_fault_profile(case, orchestrator.tools.registry)
        conversation_id = new_id("eval_conv")
        response = None
        turn_observations: list[EvalTurnObservation] = []
        try:
            for turn_index, turn in enumerate(case.turns):
                if turn["role"] != "user":
                    continue
                response = await orchestrator.handle_message(
                    conversation_id=conversation_id,
                    user_id=case.user_id,
                    text=turn["content"],
                )
                turn_observations.append(
                    EvalTurnObservation(
                        turn_index=turn_index,
                        intent=response.trace.intent.primary,
                        route=response.trace.route.target if response.trace.route else None,
                        tools=[
                            tool.name
                            for tool in response.trace.tool_results
                            if tool.status == ToolStatus.success
                        ],
                        error_codes=[
                            tool.error_code for tool in response.trace.tool_results if tool.error_code
                        ],
                    )
                )
        finally:
            orchestrator.tools.fault_profile = previous_fault_profile
        assert response is not None
        state = orchestrator.memory.states.get(conversation_id)
        observed_memory_facts = dict(state.facts) if state else {}
        observed_tools = [
            tool.name for tool in response.trace.tool_results if tool.status == ToolStatus.success
        ]
        observed_error_codes = [
            tool.error_code for tool in response.trace.tool_results if tool.error_code
        ]
        observed_policy_codes = [finding.code for finding in response.trace.policy_findings]
        failures = _check_case(
            case,
            response,
            observed_tools,
            observed_error_codes,
            observed_policy_codes,
            observed_memory_facts,
            turn_observations,
        )
        score = max(0.0, 1.0 - 0.2 * len(failures))
        results.append(
            EvalCaseResult(
                case_id=case.case_id,
                passed=not failures,
                score=score,
                failures=failures,
                observed_intent=response.trace.intent.primary,
                observed_confidence=response.trace.intent.confidence,
                observed_entities=response.trace.intent.entities,
                observed_missing_slots=response.trace.intent.missing_slots,
                observed_route=response.trace.route.target if response.trace.route else None,
                observed_route_needs_human=response.trace.route.needs_human if response.trace.route else None,
                observed_allowed_tools=response.trace.route.allowed_tools if response.trace.route else [],
                observed_memory_facts=observed_memory_facts,
                observed_turns=turn_observations,
                observed_tools=observed_tools,
                observed_error_codes=observed_error_codes,
                observed_policy_codes=observed_policy_codes,
                answer=response.message.content,
            )
        )
    passed = sum(1 for result in results if result.passed)
    return EvalReport(total=len(results), passed=passed, score=passed / max(len(results), 1), results=results)


def _check_case(
    case: EvalCase,
    response,
    observed_tools: list[str],
    observed_error_codes: list[str],
    observed_policy_codes: list[str],
    observed_memory_facts: dict,
    turn_observations: list[EvalTurnObservation],
) -> list[str]:
    failures: list[str] = []
    expected = case.expected
    answer = response.message.content
    if expected.intent and response.trace.intent.primary != expected.intent:
        failures.append(f"intent expected {expected.intent.value}, got {response.trace.intent.primary.value}")
    if expected.min_confidence is not None and response.trace.intent.confidence < expected.min_confidence:
        failures.append(
            f"confidence expected >= {expected.min_confidence}, got {response.trace.intent.confidence}"
        )
    for key, value in expected.required_entities.items():
        got = response.trace.intent.entities.get(key)
        if got != value:
            failures.append(f"entity {key} expected {value}, got {got}")
    for slot in expected.required_missing_slots:
        if slot not in response.trace.intent.missing_slots:
            failures.append(f"missing slot not observed: {slot}")
    for slot in expected.forbidden_missing_slots:
        if slot in response.trace.intent.missing_slots:
            failures.append(f"forbidden missing slot observed: {slot}")
    for key, value in expected.required_memory_facts.items():
        got = observed_memory_facts.get(key)
        if got != value:
            failures.append(f"memory fact {key} expected {value}, got {got}")
    observed_route = response.trace.route.target if response.trace.route else None
    observed_allowed_tools = response.trace.route.allowed_tools if response.trace.route else []
    if expected.route_target and observed_route != expected.route_target:
        got = observed_route.value if observed_route else "None"
        failures.append(f"route expected {expected.route_target.value}, got {got}")
    observed_route_needs_human = response.trace.route.needs_human if response.trace.route else None
    if expected.route_needs_human is not None and observed_route_needs_human != expected.route_needs_human:
        failures.append(
            f"route needs_human expected {expected.route_needs_human}, got {observed_route_needs_human}"
        )
    for tool in expected.required_allowed_tools:
        if tool not in observed_allowed_tools:
            failures.append(f"allowed tool missing from route: {tool}")
    for tool in expected.forbidden_allowed_tools:
        if tool in observed_allowed_tools:
            failures.append(f"forbidden tool present in route: {tool}")
    for tool in expected.required_tools:
        if tool not in observed_tools:
            failures.append(f"required tool not called: {tool}")
    for error_code in expected.required_error_codes:
        if error_code not in observed_error_codes:
            failures.append(f"required error code not observed: {error_code}")
    for tool_output in expected.required_tool_outputs:
        if not _has_tool_output(response.trace.tool_results, tool_output.tool_name, tool_output.path, tool_output.equals):
            failures.append(
                f"tool output missing: {tool_output.tool_name}.{tool_output.path} == {tool_output.equals}"
            )
    _check_expected_turns(expected.expected_turns, turn_observations, failures)
    for code in expected.required_policy_codes:
        if code not in observed_policy_codes:
            failures.append(f"required policy code not observed: {code}")
    for code in expected.forbidden_policy_codes:
        if code in observed_policy_codes:
            failures.append(f"forbidden policy code observed: {code}")
    for text in expected.must_include:
        if text not in answer:
            failures.append(f"answer missing: {text}")
    for text in expected.must_not_include:
        if text in answer:
            failures.append(f"answer included forbidden text: {text}")
    if expected.escalation is not None and response.handoff_required != expected.escalation:
        failures.append(f"escalation expected {expected.escalation}, got {response.handoff_required}")
    selected_doc_ids = [hit.document_id for hit in response.citations]
    for doc_id in expected.policy_refs:
        if doc_id not in selected_doc_ids:
            failures.append(f"missing citation doc: {doc_id}")
    return failures


def _check_expected_turns(expected_turns, observed_turns: list[EvalTurnObservation], failures: list[str]) -> None:
    by_index = {turn.turn_index: turn for turn in observed_turns}
    for expected in expected_turns:
        observed = by_index.get(expected.turn_index)
        if not observed:
            failures.append(f"expected turn {expected.turn_index} was not observed")
            continue
        if expected.intent and observed.intent != expected.intent:
            failures.append(
                f"turn {expected.turn_index} intent expected {expected.intent.value}, got {observed.intent.value}"
            )
        if expected.route_target and observed.route != expected.route_target:
            got = observed.route.value if observed.route else "None"
            failures.append(f"turn {expected.turn_index} route expected {expected.route_target.value}, got {got}")
        for tool in expected.required_tools:
            if tool not in observed.tools:
                failures.append(f"turn {expected.turn_index} required tool not called: {tool}")
        for tool in expected.forbidden_tools:
            if tool in observed.tools:
                failures.append(f"turn {expected.turn_index} forbidden tool was called: {tool}")
        for error_code in expected.required_error_codes:
            if error_code not in observed.error_codes:
                failures.append(f"turn {expected.turn_index} required error code not observed: {error_code}")


def _has_tool_output(tool_results, tool_name: str, path: str, expected_value) -> bool:
    for result in tool_results:
        if result.name != tool_name or result.status != ToolStatus.success or result.data is None:
            continue
        if _get_path(result.data, path) == expected_value:
            return True
    return False


def _get_path(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _build_fault_profile(case: EvalCase, registry: ToolRegistry) -> ToolFaultProfile | None:
    if not case.tool_faults:
        return None
    profile = ToolFaultProfile()
    for fault in case.tool_faults:
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


async def async_main(path: str) -> EvalReport:
    container = create_container()
    cases = load_cases(path)
    report = await run_cases(cases, container.orchestrator)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return report


def report_exit_code(report: EvalReport) -> int:
    return 0 if report.passed == report.total else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline agent evals.")
    parser.add_argument("path", nargs="?", default="examples/evals/golden_core.json")
    args = parser.parse_args(argv)
    report = asyncio.run(async_main(args.path))
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())

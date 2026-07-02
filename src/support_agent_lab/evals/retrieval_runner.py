from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from support_agent_lab.memory.store import KnowledgeIndex


class RetrievalExpectation(BaseModel):
    required_doc_ids: list[str] = Field(default_factory=list)
    required_top_doc_id: str | None = None
    required_rewrite_terms: list[str] = Field(default_factory=list)
    min_candidates: int = 1


class RetrievalEvalCase(BaseModel):
    case_id: str
    scenario: str
    query: str
    locale: str = "zh-CN"
    expected: RetrievalExpectation
    tags: list[str] = Field(default_factory=list)


class RetrievalEvalCaseResult(BaseModel):
    case_id: str
    passed: bool
    score: float
    failures: list[str] = Field(default_factory=list)
    selected_doc_ids: list[str]
    rewritten_queries: list[str]
    candidates_by_stage: dict[str, int]


class RetrievalEvalReport(BaseModel):
    total: int
    passed: int
    score: float
    results: list[RetrievalEvalCaseResult]


def load_cases(path: str | Path) -> list[RetrievalEvalCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RetrievalEvalCase.model_validate(item) for item in data]


def run_cases(cases: list[RetrievalEvalCase], knowledge: KnowledgeIndex | None = None) -> RetrievalEvalReport:
    knowledge = knowledge or KnowledgeIndex()
    results: list[RetrievalEvalCaseResult] = []
    for case in cases:
        trace = knowledge.search(case.query)
        selected_doc_ids = [hit.document_id for hit in trace.selected_context]
        failures = _check_case(case, selected_doc_ids, trace.rewritten_queries, trace.candidates_by_stage)
        score = max(0.0, 1.0 - 0.2 * len(failures))
        results.append(
            RetrievalEvalCaseResult(
                case_id=case.case_id,
                passed=not failures,
                score=score,
                failures=failures,
                selected_doc_ids=selected_doc_ids,
                rewritten_queries=trace.rewritten_queries,
                candidates_by_stage=trace.candidates_by_stage,
            )
        )
    passed = sum(1 for result in results if result.passed)
    return RetrievalEvalReport(total=len(results), passed=passed, score=passed / max(len(results), 1), results=results)


def _check_case(
    case: RetrievalEvalCase,
    selected_doc_ids: list[str],
    rewritten_queries: list[str],
    candidates_by_stage: dict[str, int],
) -> list[str]:
    failures: list[str] = []
    expected = case.expected
    for doc_id in expected.required_doc_ids:
        if doc_id not in selected_doc_ids:
            failures.append(f"missing required document: {doc_id}")
    if expected.required_top_doc_id:
        top_doc_id = selected_doc_ids[0] if selected_doc_ids else None
        if top_doc_id != expected.required_top_doc_id:
            failures.append(f"top document expected {expected.required_top_doc_id}, got {top_doc_id}")
    for term in expected.required_rewrite_terms:
        if not any(term in query for query in rewritten_queries):
            failures.append(f"rewrite missing term: {term}")
    if candidates_by_stage.get("hybrid", 0) < expected.min_candidates:
        failures.append(
            f"candidate count below minimum: {candidates_by_stage.get('hybrid', 0)} < {expected.min_candidates}"
        )
    return failures


def report_exit_code(report: RetrievalEvalReport) -> int:
    return 0 if report.passed == report.total else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run retrieval challenge evals.")
    parser.add_argument("path", nargs="?", default="examples/evals/retrieval_challenge.json")
    args = parser.parse_args(argv)
    report = run_cases(load_cases(args.path))
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())

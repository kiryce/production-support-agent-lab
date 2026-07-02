from support_agent_lab.evals.retrieval_runner import load_cases, report_exit_code, run_cases


def test_retrieval_challenge_eval_passes():
    cases = load_cases("examples/evals/retrieval_challenge.json")

    report = run_cases(cases)

    assert report.total == 5
    assert report.passed == 5
    assert all(result.selected_doc_ids for result in report.results)
    assert report_exit_code(report) == 0
    assert report_exit_code(report.model_copy(update={"passed": 4})) == 1

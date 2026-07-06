from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "deploy" / "prometheus" / "support-agent-alerts.yml"
PROMETHEUS_CONFIG_PATH = ROOT / "deploy" / "prometheus" / "prometheus.yml"
RUNBOOK_PATH = ROOT / "docs" / "alerting-runbook.md"
COMPOSE_PATH = ROOT / "docker-compose.yml"
README_PATH = ROOT / "README.md"
PRODUCTION_DEPLOYMENT_PATH = ROOT / "docs" / "production-deployment.md"
FRONTEND_CONSOLE_PATH = ROOT / "docs" / "frontend-console.md"

EXPECTED_ALERTS = {
    "SupportAgentDown",
    "SupportAgentHighHttp5xxRate",
    "SupportAgentRateLimitBlocking",
    "SupportAgentMonitorCritical",
    "SupportAgentMonitorDegraded",
    "SupportAgentActiveP0P1Alerts",
    "SupportAgentNewEventsAfterTriage",
    "SupportAgentStaleActiveAlerts",
    "SupportAgentAlertDeliveryFailed",
    "SupportAgentAlertDeliveryBacklog",
    "SupportAgentAlertDispatcherStale",
    "SupportAgentMonitorReviewWorkerStale",
    "SupportAgentMonitorReviewWorkerFailures",
    "SupportAgentAuditExportBatchStale",
    "SupportAgentAuditExportBatchFailed",
    "SupportAgentAlertDeliveryReceiptMissing",
    "SupportAgentFeedbackReviewStale",
    "SupportAgentFeedbackReviewUnassigned",
    "SupportAgentToolFailureRateHigh",
    "SupportAgentLLMFallbackRateHigh",
    "SupportAgentCircuitOpen",
}

EXPORTED_METRICS_USED_BY_RULES = {
    "support_agent_alert_delivery_due_records",
    "support_agent_alert_delivery_health_status",
    "support_agent_alert_delivery_sent_without_receipt",
    "support_agent_alert_dispatcher_health_status",
    "support_agent_alert_webhook_receiver_enabled",
    "support_agent_adapter_circuit_open",
    "support_agent_feedback_review_queue_stale_unresolved",
    "support_agent_feedback_review_queue_unassigned_unresolved",
    "support_agent_http_requests_total",
    "support_agent_llm_circuit_open",
    "support_agent_llm_fallback_rate",
    "support_agent_monitor_triage_active_alerts_by_severity",
    "support_agent_monitor_triage_health_status",
    "support_agent_monitor_triage_new_events_since_triage",
    "support_agent_monitor_triage_stale_active_alerts",
    "support_agent_monitor_review_worker_health_status",
    "support_agent_monitor_review_worker_last_failed_runs",
    "support_agent_audit_export_batch_health_status",
    "support_agent_audit_export_batch_last_partial",
    "support_agent_rate_limit_decisions_total",
    "support_agent_tool_calls_window",
    "support_agent_tool_failure_rate",
}

FORBIDDEN_HIGH_CARDINALITY_TOKENS = {
    "actor_user_id",
    "alert_key",
    "argument_hash",
    "assignee_user_id",
    "conversation_id",
    "destination_hash",
    "event_id",
    "idempotency_key_hash",
    "last_triage_event_id",
    "note",
    "operator_action_by",
    "payload_hash",
    "request_id",
    "run_id",
    "sample_event_ids",
    "sample_run_ids",
    "trace_id",
    "user_id",
}


def test_prometheus_alert_rules_are_parseable_and_complete():
    data = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))

    assert list(data) == ["groups"]
    assert len(data["groups"]) == 1
    group = data["groups"][0]
    assert group["name"] == "support-agent-production"
    assert group["interval"] == "30s"
    rules = group["rules"]

    assert {rule["alert"] for rule in rules} == EXPECTED_ALERTS
    for rule in rules:
        assert rule["expr"].strip()
        assert re.fullmatch(r"\d+[smhd]", rule["for"])
        assert rule["labels"]["service"] == "production-support-agent-lab"
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert {"summary", "description", "runbook_url"} <= set(rule["annotations"])
        assert rule["annotations"]["runbook_url"].endswith(f"#{_anchor(rule['alert'])}")


def test_prometheus_alert_rules_reference_exported_low_cardinality_metrics():
    data = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))

    for rule in data["groups"][0]["rules"]:
        expr = rule["expr"]
        referenced_metrics = set(re.findall(r"\b(support_agent_[a-zA-Z0-9_]+)\b", expr))
        assert referenced_metrics <= EXPORTED_METRICS_USED_BY_RULES, rule["alert"]
        combined_text = "\n".join(
            [
                rule["alert"],
                expr,
                "\n".join(rule["labels"]),
                "\n".join(rule["annotations"].values()),
            ]
        )
        for token in FORBIDDEN_HIGH_CARDINALITY_TOKENS:
            assert token not in combined_text, rule["alert"]


def test_prometheus_alert_rules_have_matching_runbook_sections():
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    headings = {
        heading.strip()
        for heading in re.findall(r"^## (SupportAgent[^\n]+)$", runbook, flags=re.MULTILINE)
    }

    assert headings == EXPECTED_ALERTS


def test_prometheus_config_loads_support_agent_rules_and_scrapes_metrics():
    data = yaml.safe_load(PROMETHEUS_CONFIG_PATH.read_text(encoding="utf-8"))

    assert data["global"]["scrape_interval"] == "30s"
    assert data["global"]["evaluation_interval"] == "30s"
    assert data["rule_files"] == ["/etc/prometheus/rules/support-agent-alerts.yml"]
    scrape = data["scrape_configs"][0]
    assert scrape["job_name"] == "support-agent-api"
    assert scrape["metrics_path"] == "/metrics"
    assert scrape["static_configs"][0]["targets"] == ["app:8000"]


def test_docker_compose_wires_optional_prometheus_observability_profile():
    data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    services = data["services"]
    assert {"app", "frontend", "alert-dispatcher", "monitor-review-worker", "audit-export-worker", "prometheus"} <= set(services)
    assert "profiles" not in services["app"]
    assert "profiles" not in services["frontend"]

    dispatcher = services["alert-dispatcher"]
    assert dispatcher["build"] == "."
    assert dispatcher["profiles"] == ["alerts"]
    assert dispatcher["depends_on"] == ["app"]
    assert dispatcher["env_file"] == [".env"]
    assert "./data:/app/data" in dispatcher["volumes"]
    assert dispatcher["command"] == [
        "support-agent-alert-dispatcher",
        "--interval-seconds",
        "30",
        "--json",
    ]
    assert dispatcher["healthcheck"] == {"disable": True}
    assert dispatcher["restart"] == "unless-stopped"

    review_worker = services["monitor-review-worker"]
    assert review_worker["build"] == "."
    assert review_worker["profiles"] == ["alerts"]
    assert review_worker["depends_on"] == ["app"]
    assert review_worker["env_file"] == [".env"]
    assert "./data:/app/data" in review_worker["volumes"]
    assert review_worker["command"] == [
        "support-agent-monitor-review-worker",
        "--interval-seconds",
        "30",
        "--json",
    ]
    assert review_worker["healthcheck"] == {"disable": True}
    assert review_worker["restart"] == "unless-stopped"

    audit_export_worker = services["audit-export-worker"]
    assert audit_export_worker["build"] == "."
    assert audit_export_worker["profiles"] == ["audit"]
    assert audit_export_worker["depends_on"] == ["app"]
    assert audit_export_worker["env_file"] == [".env"]
    assert "./data:/app/data" in audit_export_worker["volumes"]
    assert audit_export_worker["command"] == [
        "support-agent-audit-export-worker",
        "--interval-seconds",
        "86400",
        "--json",
    ]
    assert audit_export_worker["healthcheck"] == {"disable": True}
    assert audit_export_worker["restart"] == "unless-stopped"

    prometheus = services["prometheus"]
    assert prometheus["image"] == "prom/prometheus:v3.13.0"
    assert prometheus["profiles"] == ["observability"]
    assert prometheus["depends_on"] == ["app"]
    assert prometheus["ports"] == ["127.0.0.1:9090:9090"]
    assert "--config.file=/etc/prometheus/prometheus.yml" in prometheus["command"]
    assert "--storage.tsdb.path=/prometheus" in prometheus["command"]
    assert "--web.enable-lifecycle" not in prometheus["command"]
    assert {
        "./deploy/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro",
        "./deploy/prometheus/support-agent-alerts.yml:/etc/prometheus/rules/support-agent-alerts.yml:ro",
        "prometheus-data:/prometheus",
    } <= set(prometheus["volumes"])
    assert "prometheus-data" in data["volumes"]


def test_prometheus_compose_docs_stay_consistent():
    readme = README_PATH.read_text(encoding="utf-8")
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    deployment = PRODUCTION_DEPLOYMENT_PATH.read_text(encoding="utf-8")
    frontend = FRONTEND_CONSOLE_PATH.read_text(encoding="utf-8")

    for text in (readme, runbook, deployment, frontend):
        assert "docker compose --profile observability up --build" in text
        assert "9090" in text

    assert "docker compose --profile alerts up --build" in readme
    assert "docker compose --profile alerts up --build" in deployment
    assert "support-agent-alert-dispatcher" in frontend
    assert "support-agent-alert-dispatcher" in runbook
    assert "support-agent-monitor-review-worker" in frontend
    assert "support-agent-monitor-review-worker" in runbook
    assert "support-agent-audit-export-worker" in frontend
    assert "support-agent-audit-export-worker" in runbook
    assert "docker compose --profile audit up --build" in readme
    assert "docker compose --profile audit up --build" in deployment

    for text in (runbook, deployment, frontend):
        assert "app:8000" in text

    for text in (readme, deployment):
        assert "deploy/prometheus/prometheus.yml" in text
        assert "deploy/prometheus/support-agent-alerts.yml" in text
        assert "docs/alerting-runbook.md" in text

    assert "deploy/prometheus/support-agent-alerts.yml" in runbook

    for text in (runbook, deployment):
        assert "prometheus-data" in text
        assert "read-only" in text


def _anchor(value: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", value.lower()).replace(" ", "-")

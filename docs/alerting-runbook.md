# Alerting Runbook

This runbook pairs with `deploy/prometheus/support-agent-alerts.yml`. The rules only use aggregate metrics from `/metrics`; detailed investigation still happens through the authenticated console and admin APIs.

## Load The Rules

Prometheus can load the rules with:

```yaml
rule_files:
  - /etc/prometheus/rules/support-agent-alerts.yml
```

Scrape the backend API, not the frontend:

```yaml
scrape_configs:
  - job_name: support-agent-api
    metrics_path: /metrics
    static_configs:
      - targets: ["app:8000"]
```

For local or single-node deployment, run `docker compose --profile observability up --build`. Compose mounts `deploy/prometheus/prometheus.yml` and
`deploy/prometheus/support-agent-alerts.yml` read-only, stores Prometheus data in
the `prometheus-data` volume, binds Prometheus to `127.0.0.1:9090`, and leaves
Prometheus lifecycle endpoints disabled. In Kubernetes or managed Prometheus,
replace `app:8000` with the backend service DNS name.

Keep `/metrics` behind internal networking, mTLS, or gateway ACLs. It intentionally skips request signatures and actor rate limits so Prometheus can scrape without minting nonces.

## SupportAgentDown

Meaning: Prometheus cannot scrape the backend API.

First response:

- Check `/api/v1/health` and `/api/v1/ready?deep=false`.
- Inspect container/process restarts and load balancer target health.
- If `/ready` fails but `/health` passes, inspect event store and configuration before restarting.

Escalate when: the target is down for more than one scrape interval after a restart or deploy rollback.

## SupportAgentHighHttp5xxRate

Meaning: more than 5% of API requests returned 5xx over the last 5 minutes.

First response:

- Check recent app logs by `route_family` and status.
- Query `/api/v1/ready?deep=true` to distinguish app faults from upstream dependency failures.
- Inspect `support_agent_adapter_circuit_*` and `support_agent_llm_circuit_*` in `/metrics`.

Escalate when: 5xx continues after disabling the failing upstream integration or rolling back the last deploy.

## SupportAgentRateLimitBlocking

Meaning: the ingress limiter is blocking sustained traffic.

First response:

- Check whether the blocked traffic is expected load, automation, or abuse.
- Compare `support_agent_rate_limit_decisions_total{decision="blocked"}` by route family.
- Raise limits only after confirming backend dependencies are healthy.

Escalate when: legitimate users or the console cannot complete core workflows.

## SupportAgentMonitorCritical

Meaning: monitor triage health is critical, usually an active or recurring P0.

First response:

- Open the console alert queue with `Active` filters.
- Use `/api/v1/admin/monitor/triage/metrics?source=event_store` to confirm active P0 pressure.
- Open incident drilldown from the alert, then inspect the incident bundle and policy findings.
- Acknowledge the alert and assign an owner before mitigation.

Escalate when: P0 involves PII output, policy bypass, or repeated unsafe automated handling.

## SupportAgentMonitorDegraded

Meaning: monitor triage has active alerts or new events after triage.

First response:

- Check active alert count, unassigned active alert count, and new-after-triage count.
- Prioritize P1 before P2/P3, then stale active alerts.
- Convert recurring failures into regression eval cases before resolving.

Escalate when: the same alert recurs after a fix or impacts multiple customer intents.

## SupportAgentActiveP0P1Alerts

Meaning: at least one P0 or P1 alert has stayed active for 5 minutes.

First response:

- Open the alert queue and filter by severity P0/P1.
- Check `sample_run_ids` in the authenticated monitor summary or incident bundle.
- If the alert is safety or policy related, pause the affected agent version or route to human review.

Escalate when: the alert affects production users or crosses tenant/security boundaries.

## SupportAgentNewEventsAfterTriage

Meaning: an alert has newer monitor events after the latest triage action.

First response:

- Treat resolved alerts with new events as recurrence, not as closed work.
- Compare the newest monitor events against the last triage note.
- Add or update a regression eval before marking the alert resolved again.

Escalate when: recurrence follows a deploy, prompt change, model change, or upstream adapter change.

## SupportAgentStaleActiveAlerts

Meaning: an active alert is older than the configured stale threshold.

First response:

- Assign an owner if the alert is unassigned.
- Add a triage note explaining customer impact and current mitigation.
- Move the alert to resolved only after the fix is verified by eval or replay.

Escalate when: stale P0/P1 alerts remain active after one on-call handoff.

## SupportAgentAlertDeliveryFailed

Meaning: proactive alert notification failed or dead-lettered, so on-call may not receive alerts.

First response:

- Open the console Delivery tab or call `/api/v1/admin/monitor/alert-deliveries`.
- Check `dead`, `failed`, and `in_progress` rows.
- If a row is `sent` but the downstream system claims no notification arrived,
  open the console Receipts tab or call
  `/api/v1/admin/monitor/alert-webhook-receipts?delivery_id=<delivery_id>` to
  verify whether the signed receiver recorded the delivery. No receipt means
  inspect receiver enablement, `X-PSA-*` signature secret, timestamp skew, and
  ingress/network path; duplicate receipts indicate retry/idempotency behavior,
  not another raw payload.
- Requeue dead rows after the webhook destination is healthy, or close them with an operator note if handled elsewhere.

Escalate when: alert delivery is failed while monitor triage health is degraded or critical.

## SupportAgentAlertDeliveryBacklog

Meaning: due alert delivery rows have not been dispatched for 15 minutes.

First response:

- Check whether `support-agent-alert-dispatcher` is running, or whether the
  Compose `alerts` profile is enabled.
- Open the console `Delivery` tab and click `Dispatch now`, or run
  `POST /api/v1/admin/monitor/alert-deliveries/dispatch?source=event_store`
  from a trusted operator context if the worker is unhealthy.
- Check webhook URL, signing secret, timeout, and backoff settings.
- Look for expired in-progress leases if a dispatcher crashed.

Escalate when: the backlog grows or blocks P0/P1 notification.

## SupportAgentAlertDispatcherStale

Meaning: proactive alert delivery is enabled, but the durable dispatcher heartbeat is missing or stale.

First response:

- Confirm the `support-agent-alert-dispatcher` process is running, or that the
  Compose `alerts` profile is enabled.
- Check `/api/v1/admin/monitor/alert-deliveries/summary` for
  `dispatcher_status`, `dispatcher_last_seen_at`, and outbox backlog.
- Use `Dispatch now` as a temporary operator action, then restart or reschedule
  the worker so P0/P1 notification does not depend on manual clicks.
- If the worker is running but stale, check its database URL, webhook config,
  process logs, and whether it can write the shared event-store file.

Escalate when: dispatcher heartbeat is stale while P0/P1 monitor alerts or due delivery rows exist.

## SupportAgentAlertDeliveryReceiptMissing

Meaning: the signed alert webhook receiver is enabled, but at least one sent
delivery is older than `APP_MONITOR_ALERT_WEBHOOK_RECEIPT_GRACE_SECONDS` and
has no matching row in `alert_webhook_receipts`.

First response:

- Open the console `Delivery` tab and check the `Receipt` metric; `3/4` means
  three eligible sent deliveries have receiver proof and one does not.
- Call `/api/v1/admin/monitor/alert-deliveries/receipt-gaps?order=asc` or run
  the `Inspect alert deliveries missing receipts` automation action to list the
  exact sent delivery rows that exceeded the grace period.
- Open the console `Receipts` tab or call
  `/api/v1/admin/monitor/alert-webhook-receipts?delivery_id=<delivery_id>` for
  the missing delivery id from the delivery ledger.
- Check that `APP_MONITOR_ALERT_WEBHOOK_URL` points at the intended receiver,
  `APP_MONITOR_ALERT_WEBHOOK_RECEIVER_ENABLED=true` on that receiver, and both
  sides share the same `APP_MONITOR_ALERT_WEBHOOK_SECRET`.
- Inspect timestamp skew against
  `APP_MONITOR_ALERT_WEBHOOK_RECEIVER_MAX_AGE_SECONDS`; stale `X-PSA-Timestamp`
  values are rejected before a receipt is recorded.
- Check ingress, TLS, proxy, and firewall logs between the dispatcher and
  receiver. Duplicate receipts are retry/idempotency evidence, not a second raw
  payload.

Escalate when: receipt-missing count grows, affects P0/P1 alerts, or appears
after an ingress, secret-rotation, dispatcher, or receiver deploy.

## SupportAgentFeedbackReviewStale

Meaning: unresolved response feedback is older than the feedback review stale threshold.

First response:

- Open the console `Feedback` workbench and sort/filter the unresolved backlog.
- Assign an owner, then open the associated run trace before changing status.
- If the feedback points to a real agent failure, generate a regression draft before marking it resolved.

Escalate when: stale negative feedback affects production users, repeats across reasons, or remains stale after one on-call handoff.

## SupportAgentFeedbackReviewUnassigned

Meaning: at least one unresolved response feedback record has no current owner.

First response:

- Open the console `Feedback` workbench and inspect the `Unassigned` count.
- Assign an owner through the append-only review trail.
- Prioritize negative feedback and feedback linked to active monitor alerts.

Escalate when: unassigned feedback keeps growing or the backlog includes security, policy, refund, billing, or account-access complaints.

## SupportAgentToolFailureRateHigh

Meaning: more than 10% of audited tool calls failed in a meaningful traffic window.

First response:

- Query `/api/v1/admin/tools/audit/summary`.
- Bucket failures by tool name and top error code.
- Use incident bundle traces to confirm the agent did not fabricate business results after tool failure.

Escalate when: the failing tool is required for order, billing, refund, or account-security workflows.

## SupportAgentLLMFallbackRateHigh

Meaning: LLM fallback is above 5%.

First response:

- Check provider status, timeout settings, and retry/circuit metrics.
- Confirm fallback answers remain safe and grounded.
- Run staging evals before moving traffic back to the primary model after an outage.

Escalate when: fallback answers lose required citations or trigger monitor alerts.

## SupportAgentCircuitOpen

Meaning: an adapter or LLM circuit breaker is open.

First response:

- Identify whether `adapter="business"`, `adapter="knowledge"`, or the LLM circuit opened.
- Check upstream health and recent retry failures.
- Keep traffic on fallback or human handoff until the circuit half-opens and readiness is healthy.

Escalate when: the circuit protects a dependency required by core workflows or remains open after upstream recovery.

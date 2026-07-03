"use client";

import type { FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  AlertTriangle,
  Bell,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Database,
  Download,
  Filter,
  Layers,
  Loader2,
  Play,
  RefreshCw,
  Route as RouteIcon,
  Search,
  Settings,
  ShieldCheck,
  User,
  Wrench,
  X
} from "lucide-react";
import type {
  AgentRunTrace,
  ConsoleSnapshot,
  IncidentRunBundle,
  JsonValue,
  MonitorAlert,
  MonitorEvent,
  PolicyFinding,
  RetrievalHit,
  StoredEvent,
  ToolAuditRecord,
  ToolResult,
  TraceSpan
} from "@/src/shared/types";

const LOCAL_SCENARIO =
  "My order A1001 headphones arrived broken. Can I return them or get help?";

const STEPS: Array<{ id: TimelineStepId; label: string; icon: LucideIcon }> = [
  { id: "message", label: "Message", icon: ClipboardList },
  { id: "intent", label: "Intent", icon: Search },
  { id: "route", label: "Route", icon: RouteIcon },
  { id: "tools", label: "Tools", icon: Wrench },
  { id: "retrieval", label: "Retrieval", icon: BookOpen },
  { id: "answer", label: "Answer", icon: Check },
  { id: "monitor", label: "Monitor", icon: Activity }
];

type TimelineStepId =
  | "message"
  | "intent"
  | "route"
  | "tools"
  | "retrieval"
  | "answer"
  | "monitor";

type EvidenceTab = "citations" | "tool-audit" | "memory" | "triage";

type LoadInput = {
  runId?: string | null;
  alertKey?: string | null;
};

type TimelineStep = {
  id: TimelineStepId;
  title: string;
  eyebrow: string;
  time: string;
  duration: string;
  status: "ok" | "warn" | "error" | "empty";
  chips: string[];
  body: ReactNode;
};

export default function Home() {
  const [snapshot, setSnapshot] = useState<ConsoleSnapshot | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedAlertKey, setSelectedAlertKey] = useState<string | null>(null);
  const [runQuery, setRunQuery] = useState("");
  const [severityFilter, setSeverityFilter] = useState("all");
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("citations");
  const [expandedSteps, setExpandedSteps] = useState<Set<TimelineStepId>>(
    () => new Set(["message", "retrieval", "monitor"])
  );
  const [rawOpen, setRawOpen] = useState(false);
  const [expandedCitations, setExpandedCitations] = useState(false);
  const [scenarioText, setScenarioText] = useState(LOCAL_SCENARIO);
  const [triageNote, setTriageNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSnapshot = useCallback(async (input: LoadInput = {}) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (input.runId) {
        params.set("runId", input.runId);
      }
      if (input.alertKey) {
        params.set("alertKey", input.alertKey);
      }
      const response = await fetch(`/api/console/snapshot?${params.toString()}`, {
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Console snapshot failed");
      }
      const nextSnapshot = data as ConsoleSnapshot;
      setSnapshot(nextSnapshot);
      setSelectedRunId(nextSnapshot.activeRunId);
      setSelectedAlertKey(nextSnapshot.activeAlertKey);
      setRunQuery(nextSnapshot.activeRunId ?? "");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Console snapshot failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  const activeAlert = useMemo(() => {
    if (!snapshot?.summary.alerts.length || !snapshot.activeAlertKey) {
      return null;
    }
    return snapshot.summary.alerts.find((alert) => alert.key === snapshot.activeAlertKey) ?? null;
  }, [snapshot]);

  const filteredAlerts = useMemo(() => {
    const alerts = snapshot?.summary.alerts ?? [];
    if (severityFilter === "all") {
      return alerts;
    }
    return alerts.filter((alert) => alert.severity === severityFilter);
  }, [severityFilter, snapshot]);

  const timelineSteps = useMemo(
    () => buildTimeline(snapshot?.incident ?? null),
    [snapshot?.incident]
  );

  const run = snapshot?.incident?.run ?? null;
  const isDemo = snapshot?.connection.authMode !== "production";

  async function runScenario() {
    setActionBusy("scenario");
    setError(null);
    try {
      const response = await fetch("/api/console/run-scenario", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: scenarioText })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Scenario failed");
      }
      await loadSnapshot({ runId: data.message.trace_id });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Scenario failed");
    } finally {
      setActionBusy(null);
    }
  }

  async function submitTriage(status: string) {
    if (!snapshot?.activeAlertKey) {
      return;
    }
    setActionBusy(status);
    setError(null);
    try {
      const response = await fetch("/api/console/triage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          alertKey: snapshot.activeAlertKey,
          status,
          note: triageNote || `${status} from PSA Lab Console`
        })
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail ?? "Triage update failed");
      }
      setTriageNote("");
      await loadSnapshot({
        alertKey: snapshot.activeAlertKey,
        runId: snapshot.activeRunId
      });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Triage update failed");
    } finally {
      setActionBusy(null);
    }
  }

  function chooseAlert(alert: MonitorAlert) {
    const runId = alert.sample_run_ids[0] ?? null;
    setSelectedAlertKey(alert.key);
    setSelectedRunId(runId);
    void loadSnapshot({ alertKey: alert.key, runId });
  }

  function submitRunLookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextRun = runQuery.trim();
    const lookupInput = event.currentTarget.querySelector<HTMLInputElement>("input");
    lookupInput?.setSelectionRange(0, 0);
    if (lookupInput) {
      lookupInput.scrollLeft = 0;
    }
    lookupInput?.blur();
    setSelectedRunId(nextRun || null);
    setSelectedAlertKey(null);
    void loadSnapshot({ runId: nextRun || null });
  }

  function exportJson() {
    if (!snapshot) {
      return;
    }
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], {
      type: "application/json"
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${snapshot.activeRunId ?? "psa-console"}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function toggleStep(step: TimelineStepId) {
    setExpandedSteps((current) => {
      const next = new Set(current);
      if (next.has(step)) {
        next.delete(step);
      } else {
        next.add(step);
      }
      return next;
    });
  }

  return (
    <main className="console-shell">
      <Rail
        onSelect={(target) => {
          if (target === "tools") {
            setEvidenceTab("tool-audit");
          }
          if (target === "memory") {
            setEvidenceTab("memory");
          }
          if (target === "alerts") {
            setSeverityFilter("all");
          }
          if (target === "settings") {
            setRawOpen((value) => !value);
          }
        }}
      />

      <section className="console-main">
        <header className="topbar">
          <div className="brand-lockup">
            <div className="brand-mark">
              <Layers size={24} />
            </div>
            <div>
              <strong>PSA Lab</strong>
              <span>Production Support</span>
            </div>
          </div>

          <div className="stepper" aria-label="Agent run stages">
            {STEPS.slice(0, 6).map((step, index) => {
              const Icon = step.icon;
              const current = step.id === "intent";
              return (
                <button
                  className={`step-pill ${current ? "is-current" : ""}`}
                  key={step.id}
                  type="button"
                  title={`Focus ${step.label}`}
                  onClick={() => toggleStep(step.id)}
                >
                  <span>{index + 1}</span>
                  <Icon size={14} />
                  {step.label}
                </button>
              );
            })}
          </div>

          <div className="top-actions">
            <button
              className="ghost-button"
              type="button"
              onClick={() => void loadSnapshot({ runId: selectedRunId, alertKey: selectedAlertKey })}
              disabled={loading}
              title="Refresh console data"
            >
              <RefreshCw size={16} />
              Refresh
            </button>
            <form className="run-lookup" onSubmit={submitRunLookup}>
              <input
                aria-label="Run ID"
                value={runQuery}
                onChange={(event) => setRunQuery(event.target.value)}
                placeholder="run_id"
              />
              {runQuery ? (
                <button
                  type="button"
                  title="Clear run id"
                  onClick={() => {
                    setRunQuery("");
                    setSelectedRunId(null);
                    void loadSnapshot({ alertKey: selectedAlertKey });
                  }}
                >
                  <X size={14} />
                </button>
              ) : null}
            </form>
            <button className="ghost-button" type="button" onClick={exportJson} disabled={!snapshot}>
              <Download size={16} />
              Export JSON
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={!isDemo || actionBusy === "scenario"}
              onClick={() => void runScenario()}
              title={isDemo ? "Run a real local API scenario" : "Disabled in production auth mode"}
            >
              {actionBusy === "scenario" ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
              Run Scenario
            </button>
          </div>
        </header>

        <section className="contextbar">
          <ContextItem
            label="API Connection"
            value={snapshot?.connection.label ?? "Agent API"}
            detail={snapshot?.monitorSource === "live" ? "Live process memory" : "Event store"}
            state={snapshot?.health?.status === "ok" ? "ok" : "warn"}
          />
          <ContextItem
            label="Actor Mode"
            value={snapshot?.connection.actorRole ?? "admin"}
            detail={snapshot?.connection.actorUserId ?? "console"}
            icon={User}
          />
          <ContextItem
            label="Scopes"
            value="monitor, audit, memory"
            detail="BFF signed server-side"
            icon={ShieldCheck}
          />
          <ContextItem
            label="Run ID"
            value={run?.id ?? selectedRunId ?? "No run selected"}
            detail={run ? `${run.status} from ${snapshot?.incident?.run_source}` : "Waiting for events"}
          />
          <ContextItem
            label="Conversation"
            value={run?.conversation_id ?? "None"}
            detail={run ? run.user_id : "No active trace"}
          />
          <div className="context-actions">
            <button
              className="secondary-button"
              type="button"
              disabled={!activeAlert || actionBusy === "acknowledged"}
              onClick={() => void submitTriage("acknowledged")}
            >
              <Check size={16} />
              Acknowledge
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={!activeAlert || actionBusy === "resolved"}
              onClick={() => void submitTriage("resolved")}
            >
              <Check size={16} />
              Resolve
            </button>
          </div>
        </section>

        {error ? <div className="error-strip">{error}</div> : null}

        <section className="workspace">
          <aside className="alerts-panel">
            <div className="panel-heading">
              <div>
                <span>Monitor Alert Queue</span>
                <strong>{snapshot?.summary.total_events ?? 0} events</strong>
              </div>
              <label className="filter-control">
                <Filter size={14} />
                <select
                  value={severityFilter}
                  onChange={(event) => setSeverityFilter(event.target.value)}
                  aria-label="Filter alerts by severity"
                >
                  <option value="all">All</option>
                  <option value="P0">P0</option>
                  <option value="P1">P1</option>
                  <option value="P2">P2</option>
                  <option value="P3">P3</option>
                </select>
              </label>
            </div>

            <div className="alert-list">
              {loading && !snapshot ? <LoadingBlock /> : null}
              {!loading && snapshot && filteredAlerts.length === 0 ? (
                <EmptyQueue
                  isDemo={isDemo}
                  scenarioText={scenarioText}
                  onScenarioText={setScenarioText}
                  onRunScenario={() => void runScenario()}
                  busy={actionBusy === "scenario"}
                />
              ) : null}
              {filteredAlerts.map((alert) => (
                <button
                  type="button"
                  className={`alert-card severity-${alert.severity.toLowerCase()} ${
                    alert.key === activeAlert?.key ? "is-selected" : ""
                  }`}
                  key={alert.key}
                  onClick={() => chooseAlert(alert)}
                >
                  <div className="alert-card-top">
                    <span className="severity-dot" />
                    <strong>{alert.severity}</strong>
                    <time>{formatTime(alert.last_seen_at)}</time>
                  </div>
                  <span className="alert-title">{alert.reason}</span>
                  <span className="alert-meta">
                    {alert.sample_run_ids[0] ?? "no run"} · {alert.count} event
                    {alert.count === 1 ? "" : "s"}
                  </span>
                  <span className="tag-row">
                    <Badge>{alert.status}</Badge>
                    {alert.new_events_since_triage ? <Badge tone="warn">new events</Badge> : null}
                  </span>
                </button>
              ))}
            </div>

            <div className="queue-footer">
              <span>
                {filteredAlerts.length} of {snapshot?.summary.alerts.length ?? 0}
              </span>
              <button type="button" onClick={() => setSeverityFilter("all")}>
                View all
                <ChevronRight size={15} />
              </button>
            </div>
          </aside>

          <section className="run-panel">
            <div className="run-heading">
              <div>
                <span>Agent Run</span>
                <h1>{run?.id ?? "No run selected"}</h1>
              </div>
              <Badge tone={statusTone(run?.status)}>{run?.status ?? "idle"}</Badge>
              <Metric label="Duration" value={runDuration(run)} />
              <Metric
                label="Outcome"
                value={
                  run?.policy_findings.some((finding) => finding.should_block)
                    ? "Policy blocked"
                    : run
                      ? "Answer recorded"
                      : "No trace"
                }
              />
            </div>

            {snapshot?.issues.length ? (
              <div className="issue-row">
                {snapshot.issues.slice(0, 3).map((issue) => (
                  <Badge tone="warn" key={`${issue.status}-${issue.detail}`}>
                    {issue.status}: {issue.detail}
                  </Badge>
                ))}
              </div>
            ) : null}

            {run ? (
              <div className="timeline">
                {timelineSteps.map((step, index) => (
                  <TimelineCard
                    key={step.id}
                    step={step}
                    index={index}
                    expanded={expandedSteps.has(step.id)}
                    onToggle={() => toggleStep(step.id)}
                  />
                ))}
              </div>
            ) : (
              <NoRunState
                isDemo={isDemo}
                scenarioText={scenarioText}
                onScenarioText={setScenarioText}
                onRunScenario={() => void runScenario()}
                busy={actionBusy === "scenario"}
              />
            )}

            {rawOpen && snapshot ? (
              <section className="raw-trace">
                <div className="panel-heading compact">
                  <span>Raw Trace</span>
                  <button type="button" onClick={() => setRawOpen(false)}>
                    <X size={14} />
                  </button>
                </div>
                <pre>{JSON.stringify(snapshot.incident?.run ?? snapshot, null, 2)}</pre>
              </section>
            ) : null}
          </section>

          <aside className="evidence-panel">
            <div className="evidence-heading">
              <div>
                <span>Evidence</span>
                <strong>{run ? run.conversation_id : "No conversation"}</strong>
              </div>
            </div>
            <div className="tabs" role="tablist" aria-label="Evidence tabs">
              {([
                ["citations", "Citations"],
                ["tool-audit", "Tool Audit"],
                ["memory", "Memory"],
                ["triage", "Triage"]
              ] as Array<[EvidenceTab, string]>).map(([id, label]) => (
                <button
                  type="button"
                  role="tab"
                  aria-selected={evidenceTab === id}
                  className={evidenceTab === id ? "is-active" : ""}
                  onClick={() => setEvidenceTab(id)}
                  key={id}
                >
                  {label}
                </button>
              ))}
            </div>

            <EvidenceContent
              tab={evidenceTab}
              snapshot={snapshot}
              expandedCitations={expandedCitations}
              onToggleCitations={() => setExpandedCitations((value) => !value)}
              triageNote={triageNote}
              onTriageNote={setTriageNote}
              onSubmitTriage={(status) => void submitTriage(status)}
              busy={actionBusy}
            />
          </aside>
        </section>

        <footer className="bottombar">
          <StatusPill label="Environment" value={snapshot?.connection.authMode ?? "demo"} ok />
          <StatusPill label="Monitor source" value={snapshot?.monitorSource ?? "event_store"} />
          <StatusPill label="Model" value={run?.llm_calls[0]?.model ?? "not called"} />
          <StatusPill label="User ID" value={run?.user_id ?? snapshot?.connection.actorUserId ?? "user_demo"} />
          <StatusPill label="Session" value={run?.conversation_id ?? "none"} />
          <button className="raw-toggle" type="button" onClick={() => setRawOpen((value) => !value)}>
            Show raw trace
            <span className={rawOpen ? "toggle is-on" : "toggle"} />
          </button>
        </footer>
      </section>
    </main>
  );
}

function Rail({ onSelect }: { onSelect: (target: string) => void }) {
  const items: Array<[string, string, LucideIcon, number | null]> = [
    ["runs", "Runs", Play, null],
    ["alerts", "Alerts", Bell, 7],
    ["tools", "Tools", Wrench, null],
    ["knowledge", "Knowledge", BookOpen, null],
    ["memory", "Memory", Database, null],
    ["settings", "Settings", Settings, null]
  ];
  return (
    <aside className="rail">
      <div className="rail-logo">
        <Layers size={26} />
      </div>
      <nav>
        {items.map(([id, label, Icon, count]) => (
          <button
            className={id === "runs" ? "is-active" : ""}
            type="button"
            key={id}
            onClick={() => onSelect(id)}
            title={label}
          >
            <Icon size={19} />
            <span>{label}</span>
            {count ? <b>{count}</b> : null}
          </button>
        ))}
      </nav>
    </aside>
  );
}

function ContextItem({
  label,
  value,
  detail,
  state,
  icon: Icon
}: {
  label: string;
  value: string;
  detail: string;
  state?: "ok" | "warn";
  icon?: LucideIcon;
}) {
  return (
    <div className="context-item">
      <span>{label}</span>
      <strong>
        {state ? <i className={`state-dot ${state}`} /> : Icon ? <Icon size={16} /> : null}
        {value}
      </strong>
      <small>{detail}</small>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function TimelineCard({
  step,
  index,
  expanded,
  onToggle
}: {
  step: TimelineStep;
  index: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <article className={`timeline-row step-${step.status}`}>
      <div className="timeline-marker">
        <span>{index + 1}</span>
      </div>
      <div className="timeline-time">
        <time>{step.time}</time>
        <small>{step.duration}</small>
      </div>
      <div className="step-card">
        <button type="button" className="step-summary" onClick={onToggle}>
          <div>
            <span>{step.eyebrow}</span>
            <strong>{step.title}</strong>
          </div>
          <div className="chip-row">
            {step.chips.slice(0, 3).map((chip) => (
              <Badge key={chip}>{chip}</Badge>
            ))}
            {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
          </div>
        </button>
        {expanded ? <div className="step-body">{step.body}</div> : null}
      </div>
    </article>
  );
}

function EvidenceContent({
  tab,
  snapshot,
  expandedCitations,
  onToggleCitations,
  triageNote,
  onTriageNote,
  onSubmitTriage,
  busy
}: {
  tab: EvidenceTab;
  snapshot: ConsoleSnapshot | null;
  expandedCitations: boolean;
  onToggleCitations: () => void;
  triageNote: string;
  onTriageNote: (value: string) => void;
  onSubmitTriage: (status: string) => void;
  busy: string | null;
}) {
  const incident = snapshot?.incident ?? null;
  if (!incident) {
    return <PanelEmpty title="No incident selected" detail="Run a scenario or select a monitor alert." />;
  }

  if (tab === "tool-audit") {
    return <ToolAudit records={incident.tool_audit_records} tools={incident.run.tool_results} />;
  }
  if (tab === "memory") {
    return <MemoryPanel incident={incident} rawEvents={snapshot?.rawEvents ?? []} />;
  }
  if (tab === "triage") {
    return (
      <TriagePanel
        events={snapshot?.triageEvents ?? []}
        alertKey={snapshot?.activeAlertKey ?? null}
        note={triageNote}
        onNote={onTriageNote}
        onSubmit={onSubmitTriage}
        busy={busy}
      />
    );
  }
  return (
    <CitationsPanel
      hits={incident.run.retrieval?.selected_context ?? []}
      findings={incident.run.policy_findings}
      monitorEvents={incident.monitor_events}
      expanded={expandedCitations}
      onToggle={onToggleCitations}
    />
  );
}

function CitationsPanel({
  hits,
  findings,
  monitorEvents,
  expanded,
  onToggle
}: {
  hits: RetrievalHit[];
  findings: PolicyFinding[];
  monitorEvents: MonitorEvent[];
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="evidence-stack">
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Citations ({hits.length} used)</strong>
          <button type="button" onClick={onToggle}>
            {expanded ? "Collapse" : "Expand all"}
            {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </button>
        </div>
        {hits.length ? (
          hits.map((hit, index) => (
            <article className="citation-row" key={`${hit.document_id}-${hit.chunk_id}`}>
              <div className="index-badge">{index + 1}</div>
              <div>
                <strong>{hit.title}</strong>
                <span>{hit.source_uri}</span>
                <p>{expanded ? hit.content : truncate(hit.content, 170)}</p>
              </div>
              <Metric label="Score" value={hit.score.toFixed(2)} />
            </article>
          ))
        ) : (
          <PanelEmpty title="No citations" detail="This run did not return retrieval context." />
        )}
      </section>

      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Policy Findings ({findings.length})</strong>
        </div>
        {findings.length ? (
          findings.map((finding) => (
            <article className={`policy-box risk-${finding.risk_level}`} key={finding.code}>
              <AlertTriangle size={18} />
              <div>
                <strong>{finding.code}</strong>
                <span>{finding.message}</span>
                <small>
                  {finding.risk_level} · block={String(finding.should_block)} · escalate=
                  {String(finding.should_escalate)}
                </small>
              </div>
            </article>
          ))
        ) : (
          <PanelEmpty title="No policy findings" detail="The policy engine did not flag this run." />
        )}
      </section>

      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Monitor Signal</strong>
        </div>
        {monitorEvents.length ? (
          monitorEvents.map((event) => (
            <article className="signal-row" key={event.id}>
              <Badge tone={riskTone(event.risk_level)}>{event.risk_level}</Badge>
              <strong>{event.summary}</strong>
              <span>
                grounded={String(event.grounded)} · compliant={String(event.policy_compliant)}
              </span>
              {event.failure_types.length ? (
                <div className="tag-row">
                  {event.failure_types.map((failure) => (
                    <Badge tone="warn" key={failure}>
                      {failure}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </article>
          ))
        ) : (
          <PanelEmpty title="No monitor events" detail="The online monitor has not reviewed this run." />
        )}
      </section>
    </div>
  );
}

function ToolAudit({
  records,
  tools
}: {
  records: ToolAuditRecord[];
  tools: ToolResult[];
}) {
  const visible = records.length ? records : [];
  return (
    <div className="evidence-stack">
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Tool Audit ({visible.length})</strong>
        </div>
        {visible.length ? (
          visible.map((record) => (
            <article className="audit-row" key={record.id}>
              <Wrench size={17} />
              <div>
                <strong>{record.tool_name}</strong>
                <span>
                  {record.status} · {record.latency_ms}ms · replayed={String(record.replayed)}
                </span>
                <small>{record.error_code ?? record.request_id}</small>
              </div>
            </article>
          ))
        ) : (
          <PanelEmpty title="No persisted audit rows" detail="Tool results are still shown below." />
        )}
      </section>
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Run Tool Results ({tools.length})</strong>
        </div>
        {tools.length ? (
          tools.map((tool) => (
            <article className="tool-result-row" key={tool.id}>
              <Badge tone={tool.status === "success" ? "success" : "warn"}>{tool.status}</Badge>
              <div>
                <strong>{tool.name}</strong>
                <span>{tool.error_code ?? `${tool.latency_ms}ms`}</span>
              </div>
            </article>
          ))
        ) : (
          <PanelEmpty title="No tool calls" detail="This route did not invoke tools." />
        )}
      </section>
    </div>
  );
}

function MemoryPanel({
  incident,
  rawEvents
}: {
  incident: IncidentRunBundle;
  rawEvents: StoredEvent[];
}) {
  const replay = incident.memory_replay;
  if (!replay) {
    return <PanelEmpty title="Memory replay unavailable" detail="No replayable event stream was returned." />;
  }
  const facts = Object.entries(replay.state.facts);
  return (
    <div className="evidence-stack">
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Replay Summary</strong>
        </div>
        <div className="memory-grid">
          <Metric label="Events" value={String(replay.event_count)} />
          <Metric label="Messages" value={String(replay.replayed_message_count)} />
          <Metric label="Runs" value={String(replay.replayed_run_count)} />
          <Metric label="Ignored" value={String(replay.ignored_event_count)} />
        </div>
      </section>
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Conversation Memory</strong>
        </div>
        <p className="summary-copy">{replay.state.working_summary || "No working summary stored."}</p>
        <div className="key-value-list">
          {facts.length ? (
            facts.map(([key, value]) => (
              <div key={key}>
                <span>{key}</span>
                <strong>{stringifyValue(value)}</strong>
              </div>
            ))
          ) : (
            <span className="muted">No facts captured.</span>
          )}
        </div>
      </section>
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Append-only Events ({rawEvents.length})</strong>
        </div>
        <div className="event-list">
          {rawEvents.slice(0, 8).map((event) => (
            <div key={event.id}>
              <Badge>{event.event_type}</Badge>
              <span>{formatTime(event.created_at)}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function TriagePanel({
  events,
  alertKey,
  note,
  onNote,
  onSubmit,
  busy
}: {
  events: Array<{
    id: string;
    status: string | null;
    actor_user_id: string;
    assignee_user_id: string | null;
    note: string;
    created_at: string;
  }>;
  alertKey: string | null;
  note: string;
  onNote: (value: string) => void;
  onSubmit: (status: string) => void;
  busy: string | null;
}) {
  return (
    <div className="evidence-stack">
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>Triage Action</strong>
        </div>
        <textarea
          value={note}
          onChange={(event) => onNote(event.target.value)}
          placeholder="Add an operator note"
          rows={4}
        />
        <div className="triage-actions">
          <button
            className="secondary-button"
            type="button"
            disabled={!alertKey || busy === "investigating"}
            onClick={() => onSubmit("investigating")}
          >
            Investigating
          </button>
          <button
            className="primary-button"
            type="button"
            disabled={!alertKey || busy === "resolved"}
            onClick={() => onSubmit("resolved")}
          >
            Resolve
          </button>
        </div>
      </section>
      <section className="evidence-card">
        <div className="evidence-card-head">
          <strong>History ({events.length})</strong>
        </div>
        {events.length ? (
          events.map((event) => (
            <article className="triage-row" key={event.id}>
              <Badge tone={event.status === "resolved" ? "success" : "warn"}>
                {event.status ?? "note"}
              </Badge>
              <div>
                <strong>{event.actor_user_id}</strong>
                <span>{formatTime(event.created_at)}</span>
                <p>{event.note || "No note"}</p>
              </div>
            </article>
          ))
        ) : (
          <PanelEmpty title="No triage history" detail="No operator has updated this alert yet." />
        )}
      </section>
    </div>
  );
}

function EmptyQueue({
  isDemo,
  scenarioText,
  onScenarioText,
  onRunScenario,
  busy
}: {
  isDemo: boolean;
  scenarioText: string;
  onScenarioText: (value: string) => void;
  onRunScenario: () => void;
  busy: boolean;
}) {
  return (
    <div className="empty-queue">
      <ShieldCheck size={25} />
      <strong>No monitor alerts</strong>
      <span>The backend returned an empty alert queue.</span>
      <textarea
        value={scenarioText}
        onChange={(event) => onScenarioText(event.target.value)}
        disabled={!isDemo}
        rows={4}
      />
      <button className="primary-button" type="button" disabled={!isDemo || busy} onClick={onRunScenario}>
        {busy ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
        Run local scenario
      </button>
    </div>
  );
}

function NoRunState(props: {
  isDemo: boolean;
  scenarioText: string;
  onScenarioText: (value: string) => void;
  onRunScenario: () => void;
  busy: boolean;
}) {
  return (
    <section className="no-run">
      <ClipboardList size={34} />
      <h2>No trace selected</h2>
      <p>The console is connected, but the backend did not return a run for the current selection.</p>
      <textarea
        value={props.scenarioText}
        onChange={(event) => props.onScenarioText(event.target.value)}
        disabled={!props.isDemo}
        rows={3}
      />
      <button
        className="primary-button"
        type="button"
        disabled={!props.isDemo || props.busy}
        onClick={props.onRunScenario}
      >
        {props.busy ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
        Run local scenario
      </button>
    </section>
  );
}

function LoadingBlock() {
  return (
    <div className="loading-block">
      <Loader2 className="spin" size={24} />
      <span>Loading agent events</span>
    </div>
  );
}

function PanelEmpty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="panel-empty">
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function Badge({
  children,
  tone = "neutral"
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warn" | "danger";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function StatusPill({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <span className="status-pill">
      <small>{label}</small>
      {ok ? <i className="state-dot ok" /> : null}
      <strong>{value}</strong>
    </span>
  );
}

function buildTimeline(incident: IncidentRunBundle | null): TimelineStep[] {
  const run = incident?.run;
  const messages = incident?.memory_replay?.state.messages ?? [];
  const userMessage = [...messages].reverse().find((message) => message.role === "user");
  const assistantMessage = [...messages].reverse().find((message) => message.role === "assistant");

  if (!run) {
    return [];
  }

  const span = (name: string) => run.spans.find((item) => item.name === name);
  const toolFailures = run.tool_results.filter((tool) => tool.status !== "success");
  const findings = run.policy_findings.filter((finding) => finding.should_block || finding.should_escalate);

  return [
    {
      id: "message",
      title: userMessage ? truncate(userMessage.content, 96) : "No user message event",
      eyebrow: "Message",
      time: formatTime(userMessage?.created_at ?? run.created_at),
      duration: spanDuration(span("policy.input_check")),
      status: userMessage ? "ok" : "empty",
      chips: userMessage?.metadata.redacted ? ["redacted"] : ["received"],
      body: (
        <div className="detail-block">
          <p>{userMessage?.content ?? "The event stream did not include a user message."}</p>
          <KeyValues values={userMessage?.metadata ?? {}} />
        </div>
      )
    },
    {
      id: "intent",
      title: run.intent?.primary ?? "Intent unavailable",
      eyebrow: "Intent",
      time: formatTime(span("intent.detect")?.started_at ?? run.created_at),
      duration: spanDuration(span("intent.detect")),
      status: run.intent ? "ok" : "empty",
      chips: run.intent ? [`confidence ${run.intent.confidence.toFixed(2)}`, run.intent.sentiment] : [],
      body: (
        <div className="detail-block">
          <p>{run.intent?.rationale || "No intent rationale returned."}</p>
          <KeyValues
            values={{
              entities: run.intent?.entities ?? {},
              missing_slots: run.intent?.missing_slots ?? [],
              urgency: run.intent?.urgency ?? "unknown"
            }}
          />
        </div>
      )
    },
    {
      id: "route",
      title: run.route?.target ?? "Route unavailable",
      eyebrow: "Route",
      time: formatTime(span("route.decide")?.started_at ?? run.created_at),
      duration: spanDuration(span("route.decide")),
      status: run.route?.needs_human ? "warn" : run.route ? "ok" : "empty",
      chips: run.route ? [run.route.needs_human ? "human review" : "automated"] : [],
      body: (
        <div className="detail-block">
          <p>{run.route?.reason ?? "No routing decision returned."}</p>
          <div className="tag-row">
            {(run.route?.allowed_tools ?? []).map((tool) => (
              <Badge key={tool}>{tool}</Badge>
            ))}
          </div>
        </div>
      )
    },
    {
      id: "tools",
      title: `${run.tool_results.length} tool call${run.tool_results.length === 1 ? "" : "s"}`,
      eyebrow: "Tools",
      time: formatTime(span("tool.invoke")?.started_at ?? run.created_at),
      duration: `${sumLatency(run.tool_results)}ms`,
      status: toolFailures.length ? "error" : run.tool_results.length ? "ok" : "empty",
      chips: toolFailures.length ? [`${toolFailures.length} failed`] : [`${run.tool_results.length} total`],
      body: (
        <div className="tool-list">
          {run.tool_results.length ? (
            run.tool_results.map((tool) => (
              <div className="tool-line" key={tool.id}>
                <Badge tone={tool.status === "success" ? "success" : "warn"}>{tool.status}</Badge>
                <strong>{tool.name}</strong>
                <span>{tool.error_code ?? `${tool.latency_ms}ms`}</span>
              </div>
            ))
          ) : (
            <span className="muted">No tools were invoked for this route.</span>
          )}
        </div>
      )
    },
    {
      id: "retrieval",
      title: run.retrieval?.query ?? "No retrieval trace",
      eyebrow: "Retrieval",
      time: formatTime(span("knowledge.retrieve")?.started_at ?? run.created_at),
      duration: spanDuration(span("knowledge.retrieve")),
      status: run.retrieval ? "ok" : "empty",
      chips: run.retrieval
        ? [`${run.retrieval.selected_context.length} chunks`, `${run.retrieval.selected_sources.length} sources`]
        : [],
      body: (
        <div className="detail-block">
          <KeyValues values={run.retrieval?.candidates_by_stage ?? {}} />
          <div className="tag-row">
            {(run.retrieval?.selected_sources ?? []).map((source) => (
              <Badge key={source}>{source}</Badge>
            ))}
          </div>
        </div>
      )
    },
    {
      id: "answer",
      title: assistantMessage ? truncate(assistantMessage.content, 96) : "No assistant message event",
      eyebrow: "Answer",
      time: formatTime(assistantMessage?.created_at ?? run.completed_at ?? run.created_at),
      duration: run.llm_calls[0] ? `${run.llm_calls[0].latency_ms}ms` : "n/a",
      status: findings.length ? "warn" : assistantMessage ? "ok" : "empty",
      chips: run.llm_calls[0]
        ? [run.llm_calls[0].model, run.llm_calls[0].fallback_used ? "fallback" : "primary"]
        : [],
      body: (
        <div className="detail-block">
          <p>{assistantMessage?.content ?? "No assistant message was replayed from the event store."}</p>
          {run.llm_calls[0] ? (
            <KeyValues
              values={{
                provider: run.llm_calls[0].provider,
                input_tokens: run.llm_calls[0].input_tokens,
                output_tokens: run.llm_calls[0].output_tokens,
                cost_usd: run.llm_calls[0].cost_usd
              }}
            />
          ) : null}
        </div>
      )
    },
    {
      id: "monitor",
      title: incident.monitor_events[0]?.summary ?? "No monitor review",
      eyebrow: "Monitor Review",
      time: formatTime(incident.monitor_events[0]?.timestamp ?? run.completed_at ?? run.created_at),
      duration: "online",
      status: incident.monitor_events.some((event) => event.needs_human_review) ? "warn" : "ok",
      chips: incident.monitor_events[0]
        ? [incident.monitor_events[0].risk_level, `${incident.monitor_events.length} event(s)`]
        : [],
      body: (
        <div className="detail-block">
          {incident.monitor_events.length ? (
            incident.monitor_events.map((event) => (
              <div className="monitor-line" key={event.id}>
                <Badge tone={riskTone(event.risk_level)}>{event.risk_level}</Badge>
                <span>{event.summary}</span>
              </div>
            ))
          ) : (
            <span className="muted">No monitor review was returned for this run.</span>
          )}
        </div>
      )
    }
  ];
}

function KeyValues({ values }: { values: Record<string, JsonValue> }) {
  const entries = Object.entries(values);
  if (!entries.length) {
    return null;
  }
  return (
    <div className="key-values">
      {entries.map(([key, value]) => (
        <div key={key}>
          <span>{key}</span>
          <strong>{stringifyValue(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function stringifyValue(value: JsonValue): string {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function truncate(value: string, max: number) {
  return value.length > max ? `${value.slice(0, max - 1)}...` : value;
}

function formatTime(value: string | null | undefined) {
  if (!value) {
    return "n/a";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "n/a";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function spanDuration(span: TraceSpan | undefined) {
  if (!span?.ended_at) {
    return "n/a";
  }
  const start = new Date(span.started_at).getTime();
  const end = new Date(span.ended_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) {
    return "n/a";
  }
  const ms = Math.max(0, end - start);
  return ms > 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function runDuration(run: AgentRunTrace | null) {
  if (!run?.completed_at) {
    return run ? "running" : "n/a";
  }
  const start = new Date(run.created_at).getTime();
  const end = new Date(run.completed_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) {
    return "n/a";
  }
  const ms = Math.max(0, end - start);
  return ms > 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function sumLatency(tools: ToolResult[]) {
  return tools.reduce((total, tool) => total + tool.latency_ms, 0);
}

function statusTone(status: string | undefined): "neutral" | "success" | "warn" | "danger" {
  if (status === "completed") {
    return "success";
  }
  if (status === "failed") {
    return "danger";
  }
  return "neutral";
}

function riskTone(risk: string): "neutral" | "success" | "warn" | "danger" {
  if (risk === "critical" || risk === "high") {
    return "danger";
  }
  if (risk === "medium") {
    return "warn";
  }
  if (risk === "low") {
    return "success";
  }
  return "neutral";
}

import type { AlertSort, AlertStatusFilter } from "./ops";

export const WORKSPACE_MODES = ["alerts", "runs", "tools", "knowledge", "settings"] as const;
export const EVIDENCE_TABS = ["brief", "citations", "tool-audit", "memory", "triage"] as const;
export const ALERT_SEVERITIES = ["all", "P0", "P1", "P2", "P3"] as const;

const ALERT_STATUSES: AlertStatusFilter[] = [
  "active",
  "all",
  "open",
  "acknowledged",
  "investigating",
  "resolved",
  "silenced"
];
const ALERT_SORTS: AlertSort[] = ["severity", "newest", "count"];

export type WorkspaceMode = (typeof WORKSPACE_MODES)[number];
export type EvidenceTab = (typeof EVIDENCE_TABS)[number];
export type AlertSeverityFilter = (typeof ALERT_SEVERITIES)[number];

export type ConsoleUrlState = {
  runId: string | null;
  alertKey: string | null;
  workspace: WorkspaceMode;
  tab: EvidenceTab;
  severity: AlertSeverityFilter;
  status: AlertStatusFilter;
  query: string;
  sort: AlertSort;
  onlyNew: boolean;
};

export const DEFAULT_CONSOLE_URL_STATE: ConsoleUrlState = {
  runId: null,
  alertKey: null,
  workspace: "alerts",
  tab: "brief",
  severity: "all",
  status: "active",
  query: "",
  sort: "severity",
  onlyNew: false
};

export function parseConsoleState(input: URLSearchParams | string): ConsoleUrlState {
  const params = normalizeParams(input);
  return {
    runId: cleaned(params.get("runId")),
    alertKey: cleaned(params.get("alertKey")),
    workspace: enumValue(params.get("workspace"), WORKSPACE_MODES, DEFAULT_CONSOLE_URL_STATE.workspace),
    tab: enumValue(params.get("tab"), EVIDENCE_TABS, DEFAULT_CONSOLE_URL_STATE.tab),
    severity: enumValue(params.get("severity"), ALERT_SEVERITIES, DEFAULT_CONSOLE_URL_STATE.severity),
    status: enumValue(params.get("status"), ALERT_STATUSES, DEFAULT_CONSOLE_URL_STATE.status),
    query: cleaned(params.get("q")) ?? DEFAULT_CONSOLE_URL_STATE.query,
    sort: enumValue(params.get("sort"), ALERT_SORTS, DEFAULT_CONSOLE_URL_STATE.sort),
    onlyNew: parseBoolean(params.get("new"))
  };
}

export function serializeConsoleState(state: ConsoleUrlState): string {
  const params = new URLSearchParams();
  setOptional(params, "runId", state.runId);
  setOptional(params, "alertKey", state.alertKey);
  setIfChanged(params, "workspace", state.workspace, DEFAULT_CONSOLE_URL_STATE.workspace);
  setIfChanged(params, "tab", state.tab, DEFAULT_CONSOLE_URL_STATE.tab);
  setIfChanged(params, "severity", state.severity, DEFAULT_CONSOLE_URL_STATE.severity);
  setIfChanged(params, "status", state.status, DEFAULT_CONSOLE_URL_STATE.status);
  setIfChanged(params, "q", state.query.trim(), DEFAULT_CONSOLE_URL_STATE.query);
  setIfChanged(params, "sort", state.sort, DEFAULT_CONSOLE_URL_STATE.sort);
  if (state.onlyNew) {
    params.set("new", "1");
  }
  return params.toString();
}

function normalizeParams(input: URLSearchParams | string) {
  if (input instanceof URLSearchParams) {
    return input;
  }
  const trimmed = input.startsWith("?") ? input.slice(1) : input;
  return new URLSearchParams(trimmed);
}

function cleaned(value: string | null) {
  const next = value?.trim() ?? "";
  return next || null;
}

function enumValue<const Values extends readonly string[]>(
  value: string | null,
  values: Values,
  fallback: Values[number],
): Values[number] {
  const next = cleaned(value);
  return next && values.includes(next) ? next : fallback;
}

function parseBoolean(value: string | null) {
  return value === "1" || value === "true";
}

function setOptional(params: URLSearchParams, key: string, value: string | null) {
  const next = value?.trim();
  if (next) {
    params.set(key, next);
  }
}

function setIfChanged(params: URLSearchParams, key: string, value: string, fallback: string) {
  if (value !== fallback) {
    params.set(key, value);
  }
}

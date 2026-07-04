export type JsonValue =
  | null
  | string
  | number
  | boolean
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonRecord = { [key: string]: JsonValue };

export type ReadinessCheck = {
  name: string;
  status: "ok" | "failed" | "skipped";
  detail: string;
};

export type ReadinessResponse = {
  status: "ok" | "not_ready";
  environment: string;
  deep: boolean;
  checks: ReadinessCheck[];
};

export type IntentResult = {
  primary: string;
  confidence: number;
  secondary: string[];
  entities: Record<string, string>;
  missing_slots: string[];
  sentiment: string;
  urgency: string;
  rationale: string;
};

export type RouteDecision = {
  target: string;
  reason: string;
  allowed_tools: string[];
  needs_human: boolean;
};

export type RetrievalHit = {
  document_id: string;
  chunk_id: string;
  title: string;
  content: string;
  score: number;
  source_uri: string;
  metadata: JsonRecord;
};

export type RetrievalTrace = {
  query: string;
  rewritten_queries: string[];
  selected_sources: string[];
  candidates_by_stage: Record<string, number>;
  selected_context: RetrievalHit[];
  dropped_candidates: string[];
};

export type ToolResult = {
  id: string;
  name: string;
  status: "success" | "failed" | "skipped";
  data: JsonRecord | null;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean;
  latency_ms: number;
};

export type LlmCallTrace = {
  provider: string;
  model: string;
  prompt_version: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  fallback_used: boolean;
  error_type: string | null;
};

export type PolicyFinding = {
  code: string;
  risk_level: string;
  message: string;
  should_block: boolean;
  should_escalate: boolean;
};

export type TraceSpan = {
  name: string;
  started_at: string;
  ended_at: string | null;
  status: "ok" | "error";
  metadata: JsonRecord;
};

export type AgentRunTrace = {
  id: string;
  tenant_id: string;
  conversation_id: string;
  user_id: string;
  agent_version: string;
  intent: IntentResult | null;
  route: RouteDecision | null;
  retrieval: RetrievalTrace | null;
  tool_results: ToolResult[];
  llm_calls: LlmCallTrace[];
  policy_findings: PolicyFinding[];
  spans: TraceSpan[];
  created_at: string;
  completed_at: string | null;
  status: "running" | "completed" | "failed";
};

export type AgentRunSearchItem = {
  id: string;
  conversation_id: string;
  user_id: string;
  agent_version: string;
  intent: string | null;
  route: string | null;
  status: "running" | "completed" | "failed";
  created_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  tool_count: number;
  failed_tool_count: number;
  tool_error_codes: string[];
  policy_codes: string[];
  citation_count: number;
  llm_call_count: number;
  needs_human: boolean;
};

export type AgentRunSearchResponse = {
  items: AgentRunSearchItem[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

export type MonitorEvent = {
  id: string;
  conversation_id: string;
  run_id: string;
  timestamp: string;
  agent_version: string;
  user_intent: string;
  alert_key: string | null;
  risk_level: string;
  grounded: boolean;
  policy_compliant: boolean;
  pii_leak: boolean;
  needs_human_review: boolean;
  failure_types: string[];
  summary: string;
};

export type MonitorAlert = {
  severity: "P0" | "P1" | "P2" | "P3";
  key: string;
  count: number;
  reason: string;
  first_seen_at: string;
  last_seen_at: string;
  sample_event_ids: string[];
  sample_run_ids: string[];
  status: string;
  assignee_user_id: string | null;
  last_triage_event_id: string | null;
  last_triage_at: string | null;
  last_triage_note: string | null;
  new_events_since_triage: boolean;
};

export type MonitorSummary = {
  total_events: number;
  by_risk_level: Record<string, number>;
  by_intent: Record<string, number>;
  by_failure_type: Record<string, number>;
  grounded_rate: number;
  policy_compliance_rate: number;
  human_review_rate: number;
  alerts: MonitorAlert[];
};

export type Message = {
  id: string;
  tenant_id: string;
  conversation_id: string;
  user_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: string;
  metadata: JsonRecord;
};

export type ConversationState = {
  tenant_id: string;
  conversation_id: string;
  user_id: string;
  messages: Message[];
  facts: JsonRecord;
  working_summary: string;
  open_questions: string[];
  last_intent: string | null;
  updated_at: string;
};

export type MemoryReplayResult = {
  conversation_id: string;
  state: ConversationState;
  event_count: number;
  replayed_message_count: number;
  replayed_run_count: number;
  ignored_event_count: number;
};

export type ToolAuditRecord = {
  id: string;
  tool_name: string;
  tenant_id: string;
  actor_user_id: string;
  request_id: string;
  trace_id: string;
  argument_hash: string;
  status: "success" | "failed" | "skipped";
  latency_ms: number;
  error_code: string | null;
  idempotency_key_hash: string | null;
  replayed: boolean;
  created_at: string | null;
};

export type StoredEvent = {
  id: string;
  tenant_id: string;
  conversation_id: string | null;
  user_id: string | null;
  run_id: string | null;
  event_type: string;
  payload: JsonRecord;
  created_at: string;
};

export type IncidentRunBundle = {
  run: AgentRunTrace;
  run_source: string;
  monitor_events: MonitorEvent[];
  tool_audit_records: ToolAuditRecord[];
  memory_replay: MemoryReplayResult | null;
};

export type ToolDefinition = {
  name: string;
  description: string;
  input_schema: JsonRecord;
  output_schema: JsonRecord;
  required_scopes: string[];
  timeout_ms: number;
  idempotent: boolean;
};

export type ApiIssue = {
  status: number;
  detail: string;
};

export type ConsoleSnapshot = {
  health: JsonRecord | null;
  ready: ReadinessResponse | null;
  summary: MonitorSummary;
  monitorSource: "event_store" | "live";
  activeAlertKey: string | null;
  activeRunId: string | null;
  incident: IncidentRunBundle | null;
  triageEvents: MonitorAlertTriageEvent[];
  rawEvents: StoredEvent[];
  tools: ToolDefinition[];
  issues: ApiIssue[];
  connection: {
    label: string;
    authMode: "demo" | "production";
    actorUserId: string;
    actorRole: string;
  };
};

export type MonitorAlertTriageEvent = {
  id: string;
  alert_key: string;
  status: string | null;
  assignee_user_id: string | null;
  actor_user_id: string;
  note: string;
  created_at: string;
};

export type EvalCaseResult = {
  case_id: string;
  passed: boolean;
  score: number;
  failures: string[];
  observed_intent: string;
  observed_confidence: number | null;
  observed_route: string | null;
  observed_route_needs_human: boolean | null;
  observed_tools: string[];
  observed_error_codes: string[];
  observed_policy_codes: string[];
  answer: string;
};

export type EvalReport = {
  total: number;
  passed: number;
  score: number;
  results: EvalCaseResult[];
};

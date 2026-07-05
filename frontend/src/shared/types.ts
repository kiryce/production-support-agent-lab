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

export type KnowledgeSearchHit = {
  document_id: string;
  chunk_id: string;
  title: string;
  score: number;
  source_uri: string;
  content_snippet: string;
};

export type KnowledgeSearchResponse = {
  query: string;
  rewritten_queries: string[];
  selected_sources: string[];
  candidates_by_stage: Record<string, number>;
  selected_context: KnowledgeSearchHit[];
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

export type MonitorDrilldownStats = {
  total_events: number;
  matching_events: number;
  alerted_events: number;
  high_risk_events: number;
  ungrounded_events: number;
  policy_violations: number;
  human_review_events: number;
  pii_leak_events: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
};

export type MonitorDrilldownBucket = {
  key: string;
  count: number;
  rate: number;
  latest_at: string | null;
  sample_run_ids: string[];
};

export type MonitorDrilldownResponse = {
  source: "event_store" | "live";
  summary: MonitorSummary;
  active_alert: MonitorAlert | null;
  stats: MonitorDrilldownStats;
  events: MonitorEvent[];
  failure_buckets: MonitorDrilldownBucket[];
  intent_buckets: MonitorDrilldownBucket[];
  risk_buckets: MonitorDrilldownBucket[];
};

export type MonitorTriageMetricsResponse = {
  source: "event_store" | "live";
  generated_at: string;
  window: {
    conversation_id: string | null;
    created_after: string | null;
    created_before: string | null;
    limit: number;
    order: "asc" | "desc";
    first_seen_at: string | null;
    last_seen_at: string | null;
  };
  total_events: number;
  healthy_events: number;
  alerted_events: number;
  alert_rate: number;
  grounded_rate: number;
  policy_compliance_rate: number;
  human_review_rate: number;
  high_risk_events: number;
  critical_events: number;
  ungrounded_events: number;
  policy_violations: number;
  human_review_events: number;
  pii_leak_events: number;
  by_risk_level: Record<string, number>;
  by_intent: Record<string, number>;
  by_failure_type: Record<string, number>;
  by_alert_failure_type: Record<string, number>;
  alert_count: number;
  active_alert_count: number;
  resolved_alert_count: number;
  silenced_alert_count: number;
  assigned_alert_count: number;
  untriaged_alert_count: number;
  unassigned_active_alert_count: number;
  new_events_since_triage_count: number;
  stale_active_alert_count: number;
  stale_threshold_seconds: number;
  by_severity: Record<"P0" | "P1" | "P2" | "P3", number>;
  active_by_severity?: Partial<Record<"P0" | "P1" | "P2" | "P3", number>>;
  by_status: Record<string, number>;
  worst_active_severity: "P0" | "P1" | "P2" | "P3" | null;
  health_status: "ok" | "degraded" | "critical";
  mtta_seconds: number | null;
  mttr_seconds: number | null;
  oldest_active_alert_at: string | null;
  latest_triage_at: string | null;
};

export type EvalCaseDraft = {
  case_id: string;
  scenario: string;
  locale?: string;
  user_id?: string;
  turns: Array<{ role: string; content: string }>;
  expected: JsonRecord;
  tool_faults?: JsonRecord[];
  tags?: string[];
};

export type RegressionDraftRequest = {
  run_id: string;
  monitor_event_id?: string | null;
  feedback_id?: string | null;
  failure_type?: string | null;
  source?: "event_store" | "live";
};

export type RegressionDraftResponse = {
  target_file: string;
  draft_type: "eval_case";
  draft: EvalCaseDraft;
  draft_json: string;
  source: {
    run_id: string;
    run_source: string;
    monitor_source: string;
    monitor_event_ids: string[];
    feedback_id?: string | null;
    feedback_rating?: FeedbackRating | null;
    feedback_reasons?: string[];
    conversation_id: string;
    alert_key: string | null;
  };
  redactions: string[];
  warnings: string[];
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

export type ToolAuditErrorSummary = {
  error_code: string;
  count: number;
};

export type ToolAuditToolSummary = {
  tool_name: string;
  total_calls: number;
  failed_calls: number;
  replayed_calls: number;
  failure_rate: number;
  average_latency_ms: number | null;
  max_latency_ms: number | null;
  top_error_code: string | null;
  last_seen_at: string | null;
};

export type ToolAuditSummary = {
  total_calls: number;
  failed_calls: number;
  replayed_calls: number;
  failure_rate: number;
  average_latency_ms: number | null;
  max_latency_ms: number | null;
  window_start: string | null;
  window_end: string | null;
  top_error_codes: ToolAuditErrorSummary[];
  tools: ToolAuditToolSummary[];
};

export type ToolAuditSearchResponse = {
  records: ToolAuditRecord[];
  summary: ToolAuditSummary;
  limit: number;
  order: "asc" | "desc";
};

export type FeedbackRating = "positive" | "negative";

export type AgentFeedback = {
  id: string;
  tenant_id: string;
  conversation_id: string;
  run_id: string;
  user_id: string;
  rating: FeedbackRating;
  reasons: string[];
  comment: string;
  source: "user" | "operator" | "qa";
  created_at: string;
};

export type FeedbackReasonSummary = {
  reason: string;
  count: number;
};

export type FeedbackSummary = {
  total_count: number;
  positive_count: number;
  negative_count: number;
  negative_rate: number;
  counts_by_reason: FeedbackReasonSummary[];
  window_start: string | null;
  window_end: string | null;
};

export type FeedbackSearchResponse = {
  items: AgentFeedback[];
  summary: FeedbackSummary;
  limit: number;
  order: "asc" | "desc";
};

export type PromotionGateCheck = {
  name: string;
  status: "passed" | "warn" | "blocked";
  detail: string;
  evidence: JsonRecord;
};

export type PromotionGateResponse = {
  status: "passed" | "warn" | "blocked";
  generated_at: string;
  environment: string;
  source: "event_store" | "live";
  window_hours: number;
  thresholds: {
    max_active_p0p1_alerts: number;
    max_active_alerts: number;
    max_tool_failure_rate: number;
    max_feedback_negative_rate: number;
    max_eval_age_hours: number;
    min_tool_calls: number;
    min_feedback_count: number;
  };
  checks: PromotionGateCheck[];
  readiness: ReadinessResponse;
  monitor: MonitorTriageMetricsResponse;
  tool_audit: ToolAuditSummary;
  feedback: FeedbackSummary;
  latest_eval_gate: EvalGateRecord | null;
};

export type MonitorAlertDeliverySummary = {
  status: "ok" | "queued" | "degraded" | "failed" | "disabled" | "unknown";
  webhook_enabled: boolean;
  pending_count: number;
  in_progress_count: number;
  failed_count: number;
  dead_count: number;
  closed_count: number;
  oldest_pending_at: string | null;
  next_attempt_at: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
};

export type AlertDeliveryStatus =
  | "pending"
  | "in_progress"
  | "sent"
  | "failed"
  | "dead"
  | "closed";

export type AlertDeliveryRecord = {
  id: string;
  tenant_id: string;
  alert_key: string;
  severity: "P0" | "P1" | "P2" | "P3";
  channel: "webhook";
  destination_hash: string;
  status: AlertDeliveryStatus;
  alert_first_seen_at: string;
  alert_last_seen_at: string;
  alert_count: number;
  reason: string;
  sample_event_ids: string[];
  sample_run_ids: string[];
  payload_hash: string;
  attempt_count: number;
  next_attempt_at: string | null;
  last_attempt_at: string | null;
  delivered_at: string | null;
  dead_lettered_at: string | null;
  locked_until: string | null;
  locked_by: string | null;
  operator_action: string | null;
  operator_action_at: string | null;
  operator_action_by: string | null;
  operator_action_note: string | null;
  response_status_code: number | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export type AlertDispatchReport = {
  webhook_enabled: boolean;
  enqueued_count: number;
  existing_count: number;
  skipped_count: number;
  claimed_count: number;
  attempted_count: number;
  sent_count: number;
  failed_count: number;
  dead_count: number;
  deliveries: AlertDeliveryRecord[];
};

export type SQLiteBackupReport = {
  source_path: string;
  backup_path: string;
  size_bytes: number;
  page_count: number;
  started_at: string;
  completed_at: string;
  verified: boolean;
  verification_detail: string;
};

export type RetentionTableReport = {
  table_name: string;
  cutoff_at: string | null;
  candidate_count: number;
  deleted_count: number;
  action: string;
  reason: string;
};

export type EventStoreRetentionReport = {
  tenant_id: string;
  dry_run: boolean;
  include_events: boolean;
  vacuum_requested: boolean;
  vacuum_performed: boolean;
  started_at: string;
  completed_at: string;
  tables: RetentionTableReport[];
  total_candidates: number;
  total_deleted: number;
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
  triageMetrics: MonitorTriageMetricsResponse | null;
  promotionGate: PromotionGateResponse | null;
  monitorAlertDelivery: MonitorAlertDeliverySummary | null;
  triageEvents: MonitorAlertTriageEvent[];
  evalGateLatest: EvalGateRecord | null;
  evalGateRecords: EvalGateRecord[];
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

export type EvalGateCaseSummary = {
  case_id: string;
  passed: boolean;
  score: number;
  failures: string[];
  observed_intent: string;
  observed_route: string | null;
  observed_error_codes: string[];
  observed_policy_codes: string[];
};

export type EvalGateRecord = {
  id: string;
  tenant_id: string;
  gate_name: string;
  runner: "agent" | "monitor" | "retrieval" | "aggregate";
  suite_id: string;
  suite_path: string;
  environment: string;
  actor_user_id: string | null;
  trigger: "api" | "cli" | "console";
  status: "passed" | "failed" | "error";
  total: number | null;
  passed: number | null;
  score: number | null;
  failed_case_ids: string[];
  case_results: EvalGateCaseSummary[];
  error_message: string | null;
  run_id: string | null;
  alert_key: string | null;
  started_at: string;
  completed_at: string;
  duration_ms: number;
  metadata: JsonRecord;
  created_at: string;
};

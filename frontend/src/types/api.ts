// ═══════════════════════════════════════════
// Source & Metadata
// ═══════════════════════════════════════════

export type SourceType = 'doc' | 'code' | 'sql';

export interface SourceMetadata {
  // doc fields
  title?: string;
  source_url?: string;
  doc_type?: string;
  req_id?: string;
  version?: string;
  updated_at?: string;
  // code fields
  file_path?: string;
  line_start?: number;
  line_end?: number;
  function_name?: string;
  class_name?: string;
  language?: string;
  repo?: string;
  commit_hash?: string;
  author?: string;
  // sql fields
  table_name?: string;
  column_name?: string;
  data_type?: string;
  column_comment?: string;
  business_meaning?: string;
  enum_values?: Record<string, string>;
}

export interface Source {
  source_type: SourceType;
  content: string;
  metadata: SourceMetadata;
  relevance_score: number;
  retrieval_method: 'exact' | 'grep' | 'semantic' | 'hybrid' | 'cache';
}

// ═══════════════════════════════════════════
// Degradation
// ═══════════════════════════════════════════

export type DegradationLevel = 0 | 1 | 2 | 3 | 4;

export interface DegradationInfo {
  level: DegradationLevel;
  trigger_reason?: string;
  affected_workers?: Array<'doc' | 'code' | 'sql' | 'supervisor' | 'synthesis'>;
  user_notice?: string;
  auto_recovery_eta?: string;
}

// ═══════════════════════════════════════════
// Entities & Classification
// ═══════════════════════════════════════════

export interface ExtractedEntities {
  module?: string;
  req_ids?: string[];
  time_range?: string;
  version?: string;
  table_names?: string[];
  column_names?: string[];
  metrics?: string[];
  group_by?: string;
  code_refs?: string[];
  person?: string;
  doc_types?: string[];
}

export interface Classification {
  sources: SourceType[];
  is_cross_source: boolean;
  entities: ExtractedEntities;
}

// ═══════════════════════════════════════════
// Source Freshness
// ═══════════════════════════════════════════

export type FreshnessStatus = 'fresh' | 'stale' | 'unknown';

export interface SourceFreshness {
  status: FreshnessStatus;
  last_indexed_at?: string;
  lag_seconds?: number;
  target_lag_seconds?: number;
  document_count?: number;
}

export interface DataFreshness {
  checked_at: string;
  sources: {
    doc: SourceFreshness;
    code: SourceFreshness;
    sql: SourceFreshness;
  };
}

// ═══════════════════════════════════════════
// Query
// ═══════════════════════════════════════════

export interface QueryRequest {
  query: string;
  session_id?: string;
  max_sources?: SourceType[];
  timeout_ms?: number;
}

export interface QueryResponse {
  query_id: string;
  answer: string;
  sources: Source[];
  degradation: DegradationInfo;
  latency_ms: number;
  data_freshness?: DataFreshness;
  suggested_followups?: string[];
  sql_executed?: string;
  needs_confirmation?: boolean;
  confirmation_prompt?: {
    sql: string;
    tables_affected: string[];
    risk_level: 'low' | 'medium' | 'high';
    risk_reasons: string[];
  };
}

export interface QueryRecord {
  query_id: string;
  session_id?: string;
  query_text: string;
  answer?: string;
  sources?: Source[];
  classification?: Classification;
  degradation?: DegradationInfo;
  sql_executed?: string;
  latency_ms?: number;
  user_feedback?: 'positive' | 'negative' | 'none';
  created_at: string;
}

// ═══════════════════════════════════════════
// Session
// ═══════════════════════════════════════════

export interface SessionRecord {
  session_id: string;
  turns: QueryRecord[];
  created_at: string;
  updated_at: string;
}

// ═══════════════════════════════════════════
// Feedback
// ═══════════════════════════════════════════

export interface FeedbackRequest {
  query_id: string;
  rating: 'positive' | 'negative';
  reason?: 'inaccurate' | 'incomplete' | 'irrelevant' | 'too_slow' | 'other';
  comment?: string;
}

// ═══════════════════════════════════════════
// SQL Confirmation
// ═══════════════════════════════════════════

export interface SQLConfirmationRequest {
  query_id: string;
  action: 'confirm' | 'modify';
  modified_sql?: string;
}

// ═══════════════════════════════════════════
// SSE Events
// ═══════════════════════════════════════════

export type SSEEventType =
  | 'classification'
  | 'worker_start'
  | 'worker_progress'
  | 'worker_result'
  | 'synthesis'
  | 'done'
  | 'error'
  | 'confirmation_required';

export type WorkerName = 'doc' | 'code' | 'sql';

export interface SSEClassificationEvent {
  sources: SourceType[];
  is_cross_source: boolean;
  entities: ExtractedEntities;
  completeness: string;
  elapsed_ms: number;
}

export interface SSEWorkerStartEvent {
  worker: WorkerName;
  timestamp: string;
}

export interface SSEWorkerProgressEvent {
  worker: WorkerName;
  status: string;
  query_used: string;
  elapsed_ms: number;
}

export interface SSEWorkerResultEvent {
  worker: WorkerName;
  result_count: number;
  top_sources?: Source[];
  retrieval_method: string;
  elapsed_ms: number;
}

export interface SSESynthesisEvent {
  chunk: string;
  citations: Array<{ text: string; source_type: SourceType; url?: string }>;
  elapsed_ms: number;
}

export interface SSEDoneEvent {
  query_id: string;
  latency_ms: number;
  degradation: DegradationInfo;
  suggested_followups?: string[];
}

export interface SSEErrorEvent {
  code: string;
  message: string;
  retryable: boolean;
  degradation?: DegradationInfo;
}

export interface SSEConfirmationRequiredEvent {
  sql: string;
  tables_affected: string[];
  risk_level: 'low' | 'medium' | 'high';
  risk_reasons: string[];
}

export interface SSEEventMap {
  classification: SSEClassificationEvent;
  worker_start: SSEWorkerStartEvent;
  worker_progress: SSEWorkerProgressEvent;
  worker_result: SSEWorkerResultEvent;
  synthesis: SSESynthesisEvent;
  done: SSEDoneEvent;
  error: SSEErrorEvent;
  confirmation_required: SSEConfirmationRequiredEvent;
}

// ═══════════════════════════════════════════
// Pagination
// ═══════════════════════════════════════════

export interface Pagination {
  offset: number;
  limit: number;
  total: number;
  has_more: boolean;
}

export interface PaginatedResponse<T> {
  items: T[];
  pagination: Pagination;
}

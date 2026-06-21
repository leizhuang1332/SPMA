import { API_BASE_URL, DEFAULT_TIMEOUT_MS } from './constants';
import type {
  QueryRequest, QueryResponse, QueryRecord, SessionRecord,
  FeedbackRequest, SQLConfirmationRequest,
  PaginatedResponse, SessionHistoryResponse,
} from '@/types/api';

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: { message: res.statusText } }));
    throw new Error(err.error?.message ?? res.statusText);
  }
  return res.json();
}

export function submitQuery(req: QueryRequest): Promise<QueryResponse> {
  return fetchJSON<QueryResponse>('/query', {
    method: 'POST',
    body: JSON.stringify({ ...req, timeout_ms: req.timeout_ms ?? DEFAULT_TIMEOUT_MS }),
  });
}

export function getQuery(queryId: string): Promise<QueryRecord> {
  return fetchJSON<QueryRecord>(`/query/${queryId}`);
}

export function listQueries(
  sessionId?: string, offset?: number, limit?: number, hasFeedback?: string,
): Promise<PaginatedResponse<QueryRecord>> {
  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  if (offset !== undefined) params.set('offset', String(offset));
  if (limit !== undefined) params.set('limit', String(limit));
  if (hasFeedback) params.set('has_feedback', hasFeedback);
  return fetchJSON<PaginatedResponse<QueryRecord>>(`/query?${params}`);
}

export function confirmSQL(queryId: string, req: SQLConfirmationRequest): Promise<QueryResponse> {
  return fetchJSON<QueryResponse>(`/query/${queryId}/confirm`, {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function createSession(title?: string): Promise<{ session_id: string; created_at: string }> {
  return fetchJSON('/sessions', {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
}

export function getSession(sessionId: string): Promise<SessionRecord> {
  return fetchJSON<SessionRecord>(`/sessions/${sessionId}`);
}

export function getSessionHistory(
  sessionId: string,
  params?: { limit?: number; offset?: number },
): Promise<SessionHistoryResponse> {
  const sp = new URLSearchParams();
  if (params?.limit) sp.set('limit', String(params.limit));
  if (params?.offset) sp.set('offset', String(params.offset));
  const qs = sp.toString();
  return fetchJSON<SessionHistoryResponse>(
    `/sessions/${sessionId}/history${qs ? `?${qs}` : ''}`,
  );
}

export function deleteSession(sessionId: string): Promise<void> {
  return fetchJSON<void>(`/sessions/${sessionId}`, { method: 'DELETE' });
}

export function submitFeedback(feedback: FeedbackRequest): Promise<{ id: string; message: string }> {
  return fetchJSON('/feedback', {
    method: 'POST',
    body: JSON.stringify(feedback),
  });
}

export function getSourcesStatus(): Promise<{ sources: { doc: unknown; code: unknown; sql: unknown } }> {
  return fetchJSON('/sources/status');
}

export function getDegradationStatus(): Promise<{ current: unknown; history_24h: unknown[] }> {
  return fetchJSON('/health/degradation');
}

'use client';

import React, { createContext, useContext, useReducer } from 'react';
import type {
  SessionRecord, Source, DegradationInfo,
  WorkerName, SourceType, QueryRecord,
  SSEClassificationEvent, SSEWorkerProgressEvent, SSEWorkerResultEvent,
  SSESynthesisEvent, SSEDoneEvent, SSEErrorEvent, SSEConfirmationRequiredEvent,
  DataFreshness,
} from '@/types/api';

// ═══════════════════════════════════════════
// State Types
// ═══════════════════════════════════════════

export type DetailPanelMode = 'idle' | 'progress' | 'sources';

export interface SubStepState {
  name: string;
  message: string;
  status: 'pending' | 'running' | 'done';
  stats?: { found?: number; round?: number };
}

export interface WorkerState {
  status: 'idle' | 'running' | 'done' | 'timeout' | 'error' | 'waiting_confirmation';
  elapsed_ms?: number;
  result_count?: number;
  progress_status?: string;
  query_used?: string;
  retrieval_method?: string;
  error_message?: string;
  sub_steps: SubStepState[];
  current_step?: string;
}

export interface SupervisorState {
  status: 'idle' | 'done';
  elapsed_ms?: number;
  sources?: SourceType[];
  is_cross_source?: boolean;
}

export interface SynthesisState {
  status: 'idle' | 'running' | 'done';
  chunks: string[];
  citations: Array<{ text: string; source_type: SourceType; url?: string }>;
}

export interface QueryState {
  phase: 'idle' | 'classifying' | 'retrieving' | 'synthesizing' | 'waiting_confirmation' | 'done' | 'error';
  queryId?: string;
  supervisor: SupervisorState;
  workers: Record<WorkerName, WorkerState>;
  synthesis: SynthesisState;
  degradation: DegradationInfo | null;
  error: { code: string; message: string } | null;
  thinking: {
    chunks: string[];
    isStreaming: boolean;
  };
  result: {
    answer: string;
    sources: Source[];
    suggested_followups: string[];
    sql_executed?: string;
    data_freshness?: DataFreshness;
    latency_ms?: number;
  } | null;
  confirmationPrompt: SSEConfirmationRequiredEvent | null;
  elapsed_ms: number;
}

export interface AppState {
  sessions: SessionRecord[];
  currentSessionId: string | null;
  pendingQuery: string;
  currentQuery: QueryState;
  detailPanelMode: DetailPanelMode;
  highlightedSourceIndex: number | null;
}

const initialWorkerState: WorkerState = { status: 'idle', sub_steps: [], current_step: undefined };

const initialState: AppState = {
  sessions: [],
  currentSessionId: null,
  pendingQuery: '',
  currentQuery: {
    phase: 'idle',
    supervisor: { status: 'idle' },
    workers: {
      doc: { ...initialWorkerState },
      code: { ...initialWorkerState },
      sql: { ...initialWorkerState },
    },
    synthesis: { status: 'idle', chunks: [], citations: [] },
    degradation: null,
    error: null,
    thinking: { chunks: [], isStreaming: false },
    result: null,
    confirmationPrompt: null,
    elapsed_ms: 0,
  },
  detailPanelMode: 'idle',
  highlightedSourceIndex: null,
};

// ═══════════════════════════════════════════
// Actions
// ═══════════════════════════════════════════

type Action =
  | { type: 'SET_SESSIONS'; sessions: SessionRecord[] }
  | { type: 'SET_CURRENT_SESSION'; sessionId: string | null }
  | { type: 'REMOVE_SESSION'; sessionId: string }
  | { type: 'ADD_SESSION'; session: SessionRecord }
  | { type: 'QUERY_START'; query: string }
  | { type: 'SET_SESSION_TURNS'; sessionId: string; turns: QueryRecord[]; total: number }
  | { type: 'SSE_CLASSIFICATION'; data: SSEClassificationEvent }
  | { type: 'SSE_WORKER_START'; worker: WorkerName }
  | { type: 'SSE_WORKER_PROGRESS'; worker: WorkerName; data: SSEWorkerProgressEvent }
  | { type: 'SSE_WORKER_RESULT'; worker: WorkerName; data: SSEWorkerResultEvent }
  | { type: 'SSE_WORKER_TIMEOUT'; worker: WorkerName; message: string }
  | { type: 'SSE_SYNTHESIS_CHUNK'; data: SSESynthesisEvent }
  | { type: 'SSE_DONE'; data: SSEDoneEvent; sources: Source[]; dataFreshness?: DataFreshness }
  | { type: 'SSE_ERROR'; data: SSEErrorEvent }
  | { type: 'SSE_CONFIRMATION_REQUIRED'; data: SSEConfirmationRequiredEvent }
  | { type: 'SSE_THINKING'; chunk: string }
  | { type: 'SSE_KEEPALIVE' }
  | { type: 'SSE_WORKER_STEP'; worker: WorkerName; step: string; message: string; stats?: { found?: number; round?: number } }
  | { type: 'QUERY_CONFIRMATION_RESOLVED' }
  | { type: 'QUERY_CANCEL' }
  | { type: 'SET_DETAIL_MODE'; mode: DetailPanelMode }
  | { type: 'HIGHLIGHT_SOURCE'; index: number | null }
  | { type: 'SET_ELAPSED'; elapsed: number }
  | { type: 'RESET_QUERY' };

// ═══════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════

function updateSubStep(
  existing: SubStepState[],
  stepName: string,
  message: string,
  stats?: { found?: number; round?: number },
): SubStepState[] {
  // Mark previous 'running' steps as 'done'
  const withPreviousDone: SubStepState[] = existing.map(s => {
    const status: SubStepState['status'] = s.status === 'running' ? 'done' : s.status;
    return { ...s, status };
  });
  // Check if step already exists
  const idx = withPreviousDone.findIndex(s => s.name === stepName);
  if (idx >= 0) {
    const updated = [...withPreviousDone];
    updated[idx] = { ...updated[idx], status: 'running' as SubStepState['status'], message, stats };
    return updated;
  }
  return [...withPreviousDone, { name: stepName, message, status: 'running' as SubStepState['status'], stats }];
}

// ═══════════════════════════════════════════
// Reducer
// ═══════════════════════════════════════════

function appReducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_SESSIONS':
      return { ...state, sessions: action.sessions };

    case 'SET_CURRENT_SESSION':
      return { ...state, currentSessionId: action.sessionId };

    case 'REMOVE_SESSION':
      return {
        ...state,
        sessions: state.sessions.filter(s => s.session_id !== action.sessionId),
        currentSessionId: state.currentSessionId === action.sessionId ? null : state.currentSessionId,
      };

    case 'ADD_SESSION':
      return {
        ...state,
        sessions: state.sessions.some(s => s.session_id === action.session.session_id)
          ? state.sessions.map(s => s.session_id === action.session.session_id ? action.session : s)
          : [action.session, ...state.sessions],
      };

    case 'QUERY_START':
      return {
        ...state,
        pendingQuery: action.query,
        currentQuery: { ...initialState.currentQuery, phase: 'classifying' },
        detailPanelMode: 'progress',
      };

    case 'SSE_CLASSIFICATION':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          phase: 'retrieving',
          supervisor: {
            status: 'done',
            elapsed_ms: action.data.elapsed_ms,
            sources: action.data.sources,
            is_cross_source: action.data.is_cross_source,
          },
        },
      };

    case 'SSE_WORKER_START':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          workers: {
            ...state.currentQuery.workers,
            [action.worker]: { status: 'running', sub_steps: [], current_step: undefined } as WorkerState,
          },
        },
      };

    case 'SSE_WORKER_PROGRESS':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          workers: {
            ...state.currentQuery.workers,
            [action.worker]: {
              ...state.currentQuery.workers[action.worker],
              status: 'running',
              progress_status: action.data.status,
              query_used: action.data.query_used,
              elapsed_ms: action.data.elapsed_ms,
            },
          },
        },
      };

    case 'SSE_WORKER_RESULT':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          workers: {
            ...state.currentQuery.workers,
            [action.worker]: {
              status: 'done',
              elapsed_ms: action.data.elapsed_ms,
              result_count: action.data.result_count,
              retrieval_method: action.data.retrieval_method,
            },
          },
        },
      };

    case 'SSE_WORKER_TIMEOUT':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          workers: {
            ...state.currentQuery.workers,
            [action.worker]: {
              ...state.currentQuery.workers[action.worker],
              status: 'timeout',
              error_message: action.message,
            },
          },
        },
      };

    case 'SSE_THINKING':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          thinking: {
            chunks: [...state.currentQuery.thinking.chunks, action.chunk],
            isStreaming: true,
          },
        },
      };

    case 'SSE_KEEPALIVE':
      return state;

    case 'SSE_WORKER_STEP':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          workers: {
            ...state.currentQuery.workers,
            [action.worker]: {
              ...state.currentQuery.workers[action.worker],
              status: 'running' as const,
              current_step: action.step,
              sub_steps: updateSubStep(
                state.currentQuery.workers[action.worker]?.sub_steps ?? [],
                action.step,
                action.message,
                action.stats,
              ),
            },
          },
        },
      };

    case 'SSE_SYNTHESIS_CHUNK':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          phase: 'synthesizing',
          synthesis: {
            status: 'running',
            chunks: [...state.currentQuery.synthesis.chunks, action.data.chunk],
            citations: [
              ...state.currentQuery.synthesis.citations,
              ...(Array.isArray(action.data.citations) ? action.data.citations : []),
            ],
          },
        },
      };

    case 'SSE_DONE': {
      const completedTurn: QueryRecord = {
        query_id: action.data.query_id,
        session_id: state.currentSessionId ?? '',
        query_text: state.pendingQuery,
        answer: state.currentQuery.synthesis.chunks.join(''),
        sources: action.sources,
        latency_ms: action.data.latency_ms,
        user_feedback: 'none',
        created_at: new Date().toISOString(),
      };

      const updatedSessions = state.sessions.map(s =>
        s.session_id === state.currentSessionId
          ? { ...s, turns: [...(s.turns ?? []), completedTurn] }
          : s
      );

      return {
        ...state,
        sessions: updatedSessions,
        pendingQuery: '',
        currentQuery: {
          ...state.currentQuery,
          phase: 'done' as const,
          thinking: { ...state.currentQuery.thinking, isStreaming: false },
          queryId: action.data.query_id,
          synthesis: { ...state.currentQuery.synthesis, status: 'done' as const },
          degradation: action.data.degradation,
          result: {
            answer: completedTurn.answer!,
            sources: action.sources,
            suggested_followups: action.data.suggested_followups ?? [],
            data_freshness: action.dataFreshness,
            latency_ms: action.data.latency_ms,
          },
        },
        detailPanelMode: 'sources' as const,
      };
    }

    case 'SSE_ERROR':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          error: { code: action.data.code, message: action.data.message },
          degradation: action.data.degradation ?? state.currentQuery.degradation,
        },
      };

    case 'SSE_CONFIRMATION_REQUIRED':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          phase: 'waiting_confirmation',
          workers: {
            ...state.currentQuery.workers,
            sql: { ...state.currentQuery.workers.sql, status: 'waiting_confirmation' },
          },
          confirmationPrompt: action.data,
        },
      };

    case 'QUERY_CONFIRMATION_RESOLVED':
      return {
        ...state,
        currentQuery: {
          ...state.currentQuery,
          phase: 'retrieving',
          workers: {
            ...state.currentQuery.workers,
            sql: { status: 'running', sub_steps: [], current_step: undefined } as WorkerState,
          },
          confirmationPrompt: null,
        },
      };

    case 'QUERY_CANCEL':
      return {
        ...state,
        currentQuery: { ...initialState.currentQuery, phase: 'idle' },
        detailPanelMode: 'idle',
      };

    case 'SET_DETAIL_MODE':
      return { ...state, detailPanelMode: action.mode };

    case 'HIGHLIGHT_SOURCE':
      return { ...state, highlightedSourceIndex: action.index };

    case 'SET_ELAPSED':
      return { ...state, currentQuery: { ...state.currentQuery, elapsed_ms: action.elapsed } };

    case 'SET_SESSION_TURNS':
      return {
        ...state,
        sessions: state.sessions.map(s =>
          s.session_id === action.sessionId
            ? { ...s, turns: action.turns }
            : s
        ),
      };

    case 'RESET_QUERY':
      return { ...state, currentQuery: initialState.currentQuery, detailPanelMode: 'idle' };

    default:
      return state;
  }
}

// ═══════════════════════════════════════════
// Context
// ═══════════════════════════════════════════

interface AppContextValue {
  state: AppState;
  dispatch: React.Dispatch<Action>;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);
  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  );
}

export function useAppContext() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppContext must be used within AppProvider');
  return ctx;
}

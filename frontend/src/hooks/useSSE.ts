'use client';

import { useCallback, useRef } from 'react';
import { useAppContext } from '@/context/app-context';
import { API_BASE_URL, SSE_RECONNECT_BACKOFF_MS } from '@/lib/constants';
import type {
  SSEEventMap,
  SSEClassificationEvent,
  SSEWorkerStartEvent,
  SSEWorkerProgressEvent,
  SSEWorkerResultEvent,
  SSESynthesisEvent,
  SSEDoneEvent,
  SSEErrorEvent,
  SSEConfirmationRequiredEvent,
  SourceType,
  Source,
  DataFreshness,
} from '@/types/api';

// Parse SSE lines manually since we use fetch + ReadableStream
function parseSSELine(line: string): { event?: string; data?: string } | null {
  if (line.startsWith('event: ')) {
    return { event: line.slice(7).trim() };
  }
  if (line.startsWith('data: ')) {
    return { data: line.slice(6) };
  }
  return null;
}

export function useSSE() {
  const { dispatch } = useAppContext();
  const abortRef = useRef<AbortController | null>(null);
  const reconnectCount = useRef(0);

  const cancelQuery = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: 'QUERY_CANCEL' });
  }, [dispatch]);

  const startQuery = useCallback(async (
    query: string,
    sessionId?: string | null,
    maxSources?: SourceType[],
  ) => {
    // Reset for new query
    reconnectCount.current = 0;
    dispatch({ type: 'QUERY_START' });

    const controller = new AbortController();
    abortRef.current = controller;

    const body = JSON.stringify({
      query,
      session_id: sessionId ?? undefined,
      max_sources: maxSources,
      timeout_ms: 30000,
    });

    try {
      const response = await fetch(`${API_BASE_URL}/query/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body,
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        dispatch({
          type: 'SSE_ERROR',
          data: { code: 'HTTP_ERROR', message: `HTTP ${response.status}`, retryable: response.status >= 500 },
        });
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer

        for (const line of lines) {
          if (line.trim() === '') {
            // Empty line means end of event — skip for now
            currentEvent = '';
            continue;
          }

          const parsed = parseSSELine(line);
          if (!parsed) continue;

          if (parsed.event) {
            currentEvent = parsed.event;
          } else if (parsed.data && currentEvent) {
            try {
              const data = JSON.parse(parsed.data);
              handleSSEEvent(currentEvent, data, dispatch);
            } catch {
              // Skip unparseable events
            }
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return;

      dispatch({
        type: 'SSE_ERROR',
        data: {
          code: 'NETWORK_ERROR',
          message: err instanceof Error ? err.message : 'Unknown error',
          retryable: true,
        },
      });

      // Auto-reconnect (exponential backoff)
      if (reconnectCount.current < 3) {
        const delay = SSE_RECONNECT_BACKOFF_MS[reconnectCount.current] ?? 8000;
        reconnectCount.current++;
        setTimeout(() => {
          startQuery(query, sessionId, maxSources);
        }, delay);
      }
    }
  }, [dispatch]);

  return { startQuery, cancelQuery };
}

// Dispatch SSE events to the reducer
function handleSSEEvent(
  event: string,
  data: unknown,
  dispatch: ReturnType<typeof useAppContext>['dispatch'],
) {
  switch (event) {
    case 'classification':
      dispatch({ type: 'SSE_CLASSIFICATION', data: data as SSEClassificationEvent });
      break;
    case 'worker_start': {
      const ws = data as SSEWorkerStartEvent;
      dispatch({ type: 'SSE_WORKER_START', worker: ws.worker });
      break;
    }
    case 'worker_progress': {
      const wp = data as SSEWorkerProgressEvent;
      dispatch({ type: 'SSE_WORKER_PROGRESS', worker: wp.worker, data: wp });
      break;
    }
    case 'worker_result': {
      const wr = data as SSEWorkerResultEvent;
      dispatch({ type: 'SSE_WORKER_RESULT', worker: wr.worker, data: wr });
      break;
    }
    case 'synthesis':
      dispatch({ type: 'SSE_SYNTHESIS_CHUNK', data: data as SSESynthesisEvent });
      break;
    case 'done': {
      const doneData = data as SSEDoneEvent;
      // Extract sources from worker results — in a real app these come from the done event
      dispatch({
        type: 'SSE_DONE',
        data: doneData,
        sources: (data as { sources?: Source[] }).sources ?? [],
        dataFreshness: (data as { data_freshness?: DataFreshness }).data_freshness,
      });
      break;
    }
    case 'error':
      dispatch({ type: 'SSE_ERROR', data: data as SSEErrorEvent });
      break;
    case 'confirmation_required':
      dispatch({ type: 'SSE_CONFIRMATION_REQUIRED', data: data as SSEConfirmationRequiredEvent });
      break;
  }
}

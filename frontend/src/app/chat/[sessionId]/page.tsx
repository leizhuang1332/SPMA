'use client';

import { useEffect, useRef } from 'react';
import { useParams } from 'next/navigation';
import { useAppContext } from '@/context/app-context';
import * as api from '@/lib/api';
import type { QueryRecord } from '@/types/api';
import AppLayout from '@/components/layout/app-layout';

export default function ChatSessionPage() {
  const params = useParams();
  const sessionId = params.sessionId as string;
  const { dispatch } = useAppContext();
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;

    const loadHistory = async () => {
      // Abort previous in-flight request from Strict Mode double-mount
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      dispatch({ type: 'SET_CURRENT_SESSION', sessionId });

      try {
        const { turns, total } = await api.getSessionHistory(
          sessionId,
          { limit: 20, offset: 0 },
          controller.signal,
        );

        if (!cancelled && !controller.signal.aborted) {
          const records: QueryRecord[] = turns.map((t, i) => ({
            query_id: `${sessionId}-turn-${i}`,
            session_id: sessionId,
            query_text: t.query_text,
            answer: t.answer,
            user_feedback: 'none',
            created_at: new Date().toISOString(),
          }));
          dispatch({ type: 'SET_SESSION_TURNS', sessionId, turns: records, total });
        }
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;

        console.error('Failed to load session history:', err);
        // Fallback: try old API
        try {
          const session = await api.getSession(sessionId, controller.signal);
          if (!cancelled && !controller.signal.aborted) {
            dispatch({ type: 'ADD_SESSION', session });
          }
        } catch (fallbackErr) {
          if (!cancelled && !controller.signal.aborted) {
            console.error(fallbackErr);
          }
        }
      }
    };

    loadHistory();

    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [sessionId, dispatch]);

  return <AppLayout />;
}

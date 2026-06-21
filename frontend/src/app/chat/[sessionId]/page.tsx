'use client';

import { useEffect } from 'react';
import { useParams } from 'next/navigation';
import { useAppContext } from '@/context/app-context';
import * as api from '@/lib/api';
import type { QueryRecord } from '@/types/api';
import AppLayout from '@/components/layout/app-layout';

export default function ChatSessionPage() {
  const params = useParams();
  const sessionId = params.sessionId as string;
  const { dispatch } = useAppContext();

  useEffect(() => {
    if (sessionId) {
      dispatch({ type: 'SET_CURRENT_SESSION', sessionId });

      api.getSessionHistory(sessionId, { limit: 20, offset: 0 })
        .then(({ turns, total }) => {
          const records: QueryRecord[] = turns.map((t, i) => ({
            query_id: `${sessionId}-turn-${i}`,
            session_id: sessionId,
            query_text: t.query_text,
            answer: t.answer,
            user_feedback: 'none',
            created_at: new Date().toISOString(),
          }));
          dispatch({ type: 'SET_SESSION_TURNS', sessionId, turns: records, total });
        })
        .catch((err) => {
          console.error('Failed to load session history:', err);
          // Fallback: try old API
          api.getSession(sessionId)
            .then(session => {
              dispatch({ type: 'ADD_SESSION', session });
            })
            .catch(console.error);
        });
    }
  }, [sessionId]);

  return <AppLayout />;
}

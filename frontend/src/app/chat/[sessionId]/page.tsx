'use client';

import { useEffect } from 'react';
import { useParams } from 'next/navigation';
import { useAppContext } from '@/context/app-context';
import * as api from '@/lib/api';
import AppLayout from '@/components/layout/app-layout';

export default function ChatSessionPage() {
  const params = useParams();
  const sessionId = params.sessionId as string;
  const { state, dispatch } = useAppContext();

  useEffect(() => {
    if (sessionId) {
      dispatch({ type: 'SET_CURRENT_SESSION', sessionId });
      // Fetch session history from API
      api.getSession(sessionId)
        .then(session => {
          // Add/update session in the list
          const exists = state.sessions.some(s => s.session_id === sessionId);
          if (!exists) {
            dispatch({ type: 'ADD_SESSION', session });
          }
        })
        .catch(console.error);
    }
  }, [sessionId]);

  return <AppLayout />;
}

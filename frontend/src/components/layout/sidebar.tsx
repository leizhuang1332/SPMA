'use client';

import { useEffect, useRef } from 'react';
import { useAppContext } from '@/context/app-context';
import SessionList from '@/components/session/session-list';
import SystemStatusBar from '@/components/session/system-status-bar';
import * as api from '@/lib/api';

// Retry delays for session list loading (ms)
const RETRY_DELAYS_MS = [1000, 2000];
const MAX_RETRIES = RETRY_DELAYS_MS.length;

export default function Sidebar() {
  const { dispatch } = useAppContext();
  const abortRef = useRef<AbortController | null>(null);
  const retryCount = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 加载会话列表（支持 abort 和失败重试）
  useEffect(() => {
    let cancelled = false;

    const loadSessions = async () => {
      // Abort previous in-flight request
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const sessions = await api.listSessions({ limit: 50 }, controller.signal);
        if (!cancelled && !controller.signal.aborted) {
          dispatch({ type: 'SET_SESSIONS', sessions });
          retryCount.current = 0;
        }
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;

        if (retryCount.current < MAX_RETRIES) {
          const delay = RETRY_DELAYS_MS[retryCount.current];
          retryCount.current++;
          console.warn(
            `listSessions 失败，将在 ${delay}ms 后重试 (${retryCount.current}/${MAX_RETRIES})`,
            err,
          );
          retryTimer.current = setTimeout(loadSessions, delay);
        } else {
          console.error('listSessions 全部重试失败', err);
          dispatch({ type: 'SESSIONS_LOAD_ERROR' });
        }
      }
    };

    loadSessions();

    return () => {
      cancelled = true;
      abortRef.current?.abort();
      if (retryTimer.current !== null) {
        clearTimeout(retryTimer.current);
        retryTimer.current = null;
      }
    };
  }, [dispatch]);

  const handleNewSession = async () => {
    dispatch({ type: 'RESET_QUERY' });
    try {
      const { session_id } = await api.createSession();
      // 立即获取完整 SessionRecord 并加入列表，确保侧边栏实时显示
      const session = await api.getSession(session_id);
      dispatch({ type: 'ADD_SESSION', session });
      dispatch({ type: 'SET_CURRENT_SESSION', sessionId: session_id });
      window.history.pushState(null, '', `/chat/${session_id}`);
    } catch {
      dispatch({ type: 'SET_CURRENT_SESSION', sessionId: null });
      window.history.pushState(null, '', '/');
    }
  };

  return (
    <>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-4" style={{ borderBottom: '0.5px solid var(--border)', minHeight: '48px' }}>
        <div className="w-7 h-7 bg-[var(--primary)] text-white rounded-[7px] grid place-items-center font-bold text-[15px] flex-shrink-0">
          S
        </div>
        <span className="font-semibold text-[13px] text-[var(--foreground)] flex-1">SPMA</span>
        <button
          onClick={handleNewSession}
          className="w-7 h-7 rounded-[7px] grid place-items-center text-base text-[var(--muted-foreground)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--primary)] transition-colors active:scale-[0.94]"
          aria-label="新建会话"
        >
          +
        </button>
      </div>

      {/* Session List */}
      <SessionList />

      {/* System Status */}
      <SystemStatusBar />
    </>
  );
}

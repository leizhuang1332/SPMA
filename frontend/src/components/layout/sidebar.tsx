'use client';

import { useAppContext } from '@/context/app-context';
import SessionList from '@/components/session/session-list';
import SystemStatusBar from '@/components/session/system-status-bar';
import * as api from '@/lib/api';

export default function Sidebar() {
  const { dispatch } = useAppContext();

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

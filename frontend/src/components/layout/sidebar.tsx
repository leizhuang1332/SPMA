'use client';

import { useAppContext } from '@/context/app-context';
import SessionList from '@/components/session/session-list';
import SystemStatusBar from '@/components/session/system-status-bar';

export default function Sidebar() {
  const { dispatch } = useAppContext();

  const handleNewSession = () => {
    dispatch({ type: 'RESET_QUERY' });
    dispatch({ type: 'SET_CURRENT_SESSION', sessionId: null });
    window.history.pushState(null, '', '/');
  };

  return (
    <>
      {/* Header */}
      <div className="flex items-center justify-between px-3.5 py-3" style={{ borderBottom: '0.5px solid var(--border)' }}>
        <div className="flex items-center gap-2">
          <div className="w-[22px] h-[22px] bg-[var(--primary)] rounded-md flex items-center justify-center text-white font-bold text-[11px]">
            S
          </div>
          <span className="font-semibold text-[13px] text-[var(--foreground)] tracking-tight">SPMA</span>
        </div>
        <button
          onClick={handleNewSession}
          className="w-[22px] h-[22px] rounded-md bg-[var(--muted)] flex items-center justify-center text-[var(--primary)] text-sm hover:bg-[var(--primary-bg)] transition-colors"
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

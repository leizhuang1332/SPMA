'use client';

import { useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useAppContext } from '@/context/app-context';
import SessionItem from './session-item';
import * as api from '@/lib/api';

export default function SessionList() {
  const { state, dispatch } = useAppContext();
  const router = useRouter();
  const [search, setSearch] = useState('');

  const filtered = state.sessions.filter(s => {
    const text = s.first_query_text ?? s.turns?.[0]?.query_text ?? '';
    return text.toLowerCase().includes(search.toLowerCase());
  });

  const handleSelect = (sessionId: string) => {
    dispatch({ type: 'SET_CURRENT_SESSION', sessionId });
    router.push(`/chat/${sessionId}`);
  };

  const handleDelete = (sessionId: string) => {
    if (!confirm('确定删除此会话？')) return;
    api.deleteSession(sessionId).then(() => {
      dispatch({ type: 'REMOVE_SESSION', sessionId });
    }).catch(console.error);
  };

  const handleRetry = useCallback(() => {
    api.listSessions({ limit: 50 })
      .then(sessions => dispatch({ type: 'SET_SESSIONS', sessions }))
      .catch(() => dispatch({ type: 'SESSIONS_LOAD_ERROR' }));
  }, [dispatch]);

  // Error state: all retries exhausted
  if (state.sessionsLoadError && state.sessions.length === 0) {
    return (
      <div className="flex-1 flex flex-col min-h-0 px-2">
        <div className="flex-1 flex flex-col items-center justify-center gap-3 px-4">
          <p className="text-[11px] text-[var(--muted-foreground)] text-center">
            会话列表加载失败
          </p>
          <button
            onClick={handleRetry}
            className="px-3 py-1.5 text-[11px] rounded-[7px] bg-[var(--primary)] text-white hover:opacity-90 transition-opacity active:scale-[0.97]"
          >
            点击重试
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 px-2">
      <input
        type="text"
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="搜索会话…"
        className="mx-2 mb-2 px-2.5 py-1.5 text-[10px] rounded-[7px] bg-[var(--bg-tertiary)] text-[var(--foreground)] placeholder:text-[var(--text-tertiary)] outline-none focus:ring-1 focus:ring-[var(--primary)]"
        style={{ borderColor: 'var(--border)' }}
      />
      <div className="flex-1 overflow-y-auto space-y-0.5 px-1">
        {filtered.map(s => (
          <SessionItem
            key={s.session_id}
            session={s}
            isActive={s.session_id === state.currentSessionId}
            onClick={() => handleSelect(s.session_id)}
            onDelete={() => handleDelete(s.session_id)}
          />
        ))}
        {filtered.length === 0 && !state.sessionsLoadError && (
          <p className="text-[10px] text-[var(--muted-foreground)] text-center py-8">暂无会话</p>
        )}
      </div>
    </div>
  );
}

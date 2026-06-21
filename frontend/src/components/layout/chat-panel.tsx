'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTheme } from 'next-themes';
import { useAppContext } from '@/context/app-context';
import { useSSE } from '@/hooks/useSSE';
import { useKeyboard } from '@/hooks/useKeyboard';
import ChatInput from '@/components/chat/chat-input';
import MessageList from '@/components/chat/message-list';
import type { SourceType } from '@/types/api';

export default function ChatPanel() {
  const { state, dispatch } = useAppContext();
  const router = useRouter();
  const { startQuery, cancelQuery } = useSSE();
  const { setTheme, resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Handle fill-input custom events from EmptyState and FollowupPills
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as string;
      const textarea = document.querySelector('textarea') as HTMLTextAreaElement;
      if (textarea) {
        textarea.value = detail;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        textarea.focus();
      }
    };
    window.addEventListener('fill-input', handler);
    return () => window.removeEventListener('fill-input', handler);
  }, []);

  // Keyboard shortcuts
  useKeyboard([
    {
      key: 'k', metaKey: true, handler: () => {
        dispatch({ type: 'RESET_QUERY' });
        dispatch({ type: 'SET_CURRENT_SESSION', sessionId: null });
        router.push('/');
      },
    },
    {
      key: 't', metaKey: true, shiftKey: true, handler: () => {
        setTheme(resolvedTheme === 'dark' ? 'light' : 'dark');
      },
    },
    {
      key: 'Escape', handler: () => {
        if (state.currentQuery.phase === 'waiting_confirmation') {
          cancelQuery();
        }
      },
    },
  ]);

  const handleSubmit = (query: string, sources?: SourceType[]) => {
    startQuery(query, state.currentSessionId, sources);
  };

  const sessionTitle = state.currentSessionId
    ? state.sessions.find(s => s.session_id === state.currentSessionId)?.turns?.[0]?.query_text ?? '会话'
    : null;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3" style={{ borderBottom: '0.5px solid var(--border)', minHeight: '48px' }}>
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <h1 className="font-semibold text-[14px] text-[var(--foreground)] truncate">
            {sessionTitle ?? 'SPMA 智能问答'}
          </h1>
          {state.currentSessionId && (
            <span className="text-[11px] text-[var(--muted-foreground)]">
              {state.sessions.find(s => s.session_id === state.currentSessionId)?.turns?.length ?? 0} 轮
            </span>
          )}
        </div>
        <button
          onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
          className="w-[30px] h-[30px] rounded-[7px] grid place-items-center text-[15px] hover:bg-[var(--bg-tertiary)] transition-all active:scale-[0.92]"
          aria-label={mounted ? (resolvedTheme === 'dark' ? '切换浅色主题' : '切换深色主题') : '切换主题'}
          suppressHydrationWarning
        >
          {mounted ? (resolvedTheme === 'dark' ? '☀️' : '🌙') : null}
        </button>
      </div>

      {/* Messages */}
      <MessageList />

      {/* Input */}
      <ChatInput
        onSubmit={handleSubmit}
        disabled={['classifying', 'retrieving', 'synthesizing', 'waiting_confirmation'].includes(state.currentQuery.phase)}
      />
    </div>
  );
}

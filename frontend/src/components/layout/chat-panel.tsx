'use client';

import { useEffect } from 'react';
import { useTheme } from 'next-themes';
import { useAppContext } from '@/context/app-context';
import { useSSE } from '@/hooks/useSSE';
import { useKeyboard } from '@/hooks/useKeyboard';
import ChatInput from '@/components/chat/chat-input';
import MessageList from '@/components/chat/message-list';
import type { SourceType } from '@/types/api';

export default function ChatPanel() {
  const { state, dispatch } = useAppContext();
  const { startQuery, cancelQuery } = useSSE();
  const { setTheme, resolvedTheme } = useTheme();

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
        window.history.pushState(null, '', '/');
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
      <div className="flex items-center justify-between px-4 py-2.5 text-[12px]" style={{ borderBottom: '0.5px solid var(--border)' }}>
        <div className="flex items-center gap-2">
          <span className="font-semibold text-[var(--foreground)]">
            {sessionTitle ?? '新会话'}
          </span>
          {state.currentSessionId && (
            <span className="text-[10px] text-[var(--muted-foreground)]">
              {state.sessions.find(s => s.session_id === state.currentSessionId)?.turns?.length ?? 0} 轮对话
            </span>
          )}
        </div>
        <button
          onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
          className="px-1.5 py-0.5 rounded text-sm hover:bg-[var(--muted)] transition-colors"
          aria-label="切换主题"
        >
          🌓
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

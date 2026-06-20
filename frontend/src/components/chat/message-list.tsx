'use client';

import { useRef } from 'react';
import { useAppContext } from '@/context/app-context';
import { useAutoScroll } from '@/hooks/useAutoScroll';
import UserMessage from './user-message';
import AIAnswer from './ai-answer';
import MessageActions from './message-actions';
import FollowupPills from './followup-pills';
import EmptyState from './empty-state';
import * as api from '@/lib/api';

export default function MessageList() {
  const { state } = useAppContext();
  const containerRef = useRef<HTMLDivElement>(null);
  const session = state.sessions.find(s => s.session_id === state.currentSessionId);
  const turns = session?.turns ?? [];
  const currentPhase = state.currentQuery.phase;

  // Derive suggested followups from the current query result (last SSE_DONE event)
  const suggestedFollowups = state.currentQuery.result?.suggested_followups ?? [];

  useAutoScroll(containerRef, [turns.length, state.currentQuery.synthesis.chunks.length]);

  // Empty state: no session selected
  if (!state.currentSessionId) {
    const handleExampleClick = (query: string) => {
      window.dispatchEvent(new CustomEvent('fill-input', { detail: query }));
    };
    return <EmptyState onExampleClick={handleExampleClick} />;
  }

  const handleLike = () => {
    // Optimistic UI update
  };

  const handleDislike = (reason?: string, comment?: string) => {
    if (state.currentQuery.queryId) {
      api.submitFeedback({
        query_id: state.currentQuery.queryId,
        rating: 'negative',
        reason: reason as 'inaccurate' | 'incomplete' | 'irrelevant' | 'too_slow' | 'other',
        comment,
      }).catch(console.error);
    }
  };

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text).catch(console.error);
  };

  const handlePillClick = (pill: string) => {
    window.dispatchEvent(new CustomEvent('fill-input', { detail: pill }));
  };

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto px-4 py-4"
      style={{ scrollBehavior: 'smooth' }}
    >
      {turns.map((turn, i) => {
        // User turn: always has query_text
        const userPart = (
          <UserMessage key={`user-${i}`} text={turn.query_text} />
        );

        // AI turn: rendered if answer exists
        const hasAnswer = (turn.answer?.length ?? 0) > 0;
        const isLastTurn = i === turns.length - 1;

        if (!hasAnswer) {
          return <div key={i}>{userPart}</div>;
        }

        return (
          <div key={i}>
            {userPart}
            <div className="group">
              <AIAnswer text={turn.answer!} />
              {isLastTurn && suggestedFollowups.length > 0 && (
                <FollowupPills pills={suggestedFollowups} onSelect={handlePillClick} />
              )}
              <MessageActions
                onLike={handleLike}
                onDislike={handleDislike}
                onCopy={() => handleCopy(turn.answer!)}
              />
            </div>
          </div>
        );
      })}

      {/* Loading indicator for active query phases */}
      {(['classifying', 'retrieving', 'synthesizing', 'waiting_confirmation'] as string[]).includes(currentPhase) && (
        <div className="flex gap-3 mb-4">
          <div className="w-7 h-7 rounded-md bg-[var(--muted)] flex items-center justify-center text-[13px] flex-shrink-0">
            S
          </div>
          <div
            className="flex items-center gap-3 bg-[var(--muted)] border rounded-[10px] px-4 py-3"
            style={{ borderColor: 'var(--border)' }}
          >
            <div className="w-[18px] h-[18px] border-2 border-[var(--border)] border-t-[var(--primary)] rounded-full animate-spin" />
            <span className="text-[var(--muted-foreground)] text-[13px]">
              {currentPhase === 'classifying' && '正在理解你的问题…'}
              {currentPhase === 'retrieving' && '正在检索多源数据…'}
              {currentPhase === 'synthesizing' && '正在生成回答…'}
              {currentPhase === 'waiting_confirmation' && '等待 SQL 确认…'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

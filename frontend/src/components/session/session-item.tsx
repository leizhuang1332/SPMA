'use client';

import { cn } from '@/lib/utils';
import type { SessionRecord } from '@/types/api';

interface SessionItemProps {
  session: SessionRecord;
  isActive: boolean;
  onClick: () => void;
  onDelete: () => void;
}

export default function SessionItem({ session, isActive, onClick, onDelete }: SessionItemProps) {
  const firstQuery = session.first_query_text ?? session.turns?.[0]?.query_text ?? '新会话';
  const turnCount = session.turns?.length ?? 0;
  const updatedAt = session.updated_at
    ? new Date(session.updated_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
    : '';

  return (
    <div
      onClick={onClick}
      className={cn(
        'group relative px-3 py-2.5 rounded-lg cursor-pointer transition-colors duration-150',
        isActive
          ? 'bg-[var(--primary-bg)] text-[var(--primary)]'
          : 'hover:bg-[var(--muted)] text-[var(--foreground)]',
      )}
    >
      <div className="text-[11px] font-medium leading-tight truncate pr-5">
        {firstQuery.length > 30 ? firstQuery.slice(0, 30) + '…' : firstQuery}
      </div>
      <div className="flex justify-between mt-1">
        <span className={cn('text-[10px]', isActive ? 'text-[var(--primary)]/60' : 'text-[var(--muted-foreground)]')}>
          {turnCount}轮 · {updatedAt}
        </span>
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity text-[var(--muted-foreground)] hover:text-[var(--danger)] text-xs"
        aria-label="删除会话"
      >
        ×
      </button>
    </div>
  );
}

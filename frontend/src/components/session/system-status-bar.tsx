'use client';

import { useAppContext } from '@/context/app-context';
import { DEGRADATION_MESSAGES } from '@/lib/constants';

export default function SystemStatusBar() {
  const { state } = useAppContext();
  const level = state.currentQuery.degradation?.level ?? 0;

  return (
    <div className="px-3 py-3 text-[10px]" style={{ borderTop: '0.5px solid var(--border)' }}>
      <div className="flex items-center gap-1.5 mb-1 text-[var(--muted-foreground)]">
        <span
          className="inline-block w-1.5 h-1.5 rounded-full flex-shrink-0"
          style={{ background: level === 0 ? 'var(--success)' : level >= 4 ? 'var(--danger)' : 'var(--warning)' }}
        />
        <span className="font-medium text-[var(--foreground)]">
          {DEGRADATION_MESSAGES[level] ?? `L${level}`}
        </span>
      </div>
      <div className="text-[var(--muted-foreground)]">
        <span>📄 文档最新</span>
        <span className="mx-auto">·</span>
        <span>💻 代码最新</span>
        <span className="mx-auto">·</span>
        <span>🗄️ DB {level >= 3 ? '不可用' : '最新'}</span>
      </div>
    </div>
  );
}

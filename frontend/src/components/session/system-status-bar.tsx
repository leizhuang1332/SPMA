'use client';

import { useAppContext } from '@/context/app-context';
import { DEGRADATION_MESSAGES } from '@/lib/constants';

export default function SystemStatusBar() {
  const { state } = useAppContext();
  const level = state.currentQuery.degradation?.level ?? 0;

  return (
    <div className="px-3.5 py-2.5 text-[10px]" style={{ borderTop: '0.5px solid var(--border)' }}>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[var(--muted-foreground)]">系统状态</span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-1.5 h-1.5 rounded-full"
            style={{ background: level === 0 ? 'var(--success)' : 'var(--warning)' }}
          />
          <span style={{ color: level === 0 ? 'var(--success)' : 'var(--warning)' }}>
            {DEGRADATION_MESSAGES[level] ?? `L${level}`}
          </span>
        </span>
      </div>
      <div className="flex justify-between text-[9px] text-[var(--muted-foreground)]">
        <span>📄 文档: 最新</span>
        <span>💻 代码: 最新</span>
        <span>🗄️ SQL: 最新</span>
      </div>
    </div>
  );
}

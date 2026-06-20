'use client';

import { useAppContext } from '@/context/app-context';
import type { FreshnessStatus } from '@/types/api';

export default function DataFreshness() {
  const { state } = useAppContext();
  const freshness = state.currentQuery.result?.data_freshness?.sources;

  const sourceItems: Array<{ key: string; label: string; status: FreshnessStatus; lag?: number }> = [
    {
      key: 'doc',
      label: '📄 文档',
      status: freshness?.doc?.status ?? 'unknown',
      lag: freshness?.doc?.lag_seconds,
    },
    {
      key: 'code',
      label: '💻 代码',
      status: freshness?.code?.status ?? 'unknown',
      lag: freshness?.code?.lag_seconds,
    },
    {
      key: 'sql',
      label: '🗄️ 数据库',
      status: freshness?.sql?.status ?? 'unknown',
      lag: freshness?.sql?.lag_seconds,
    },
  ];

  return (
    <div className="p-3 bg-[var(--muted)] rounded-[10px] mx-3 mb-3 text-[10px]">
      {sourceItems.map(item => (
        <div key={item.key} className="flex items-center gap-1.5 mb-0.5 last:mb-0 text-[var(--muted-foreground)]">
          <span>{item.label}</span>
          <span className="flex-1" />
          <span
            className="font-medium"
            style={{
              color:
                item.status === 'fresh' ? 'var(--success)' :
                item.status === 'stale' ? 'var(--warning)' :
                'var(--muted-foreground)',
            }}
          >
            {item.status === 'fresh' ? '最新' : item.status === 'stale' ? '延迟' : '未知'}
            {item.lag !== undefined ? ` · ${item.lag}s` : ''}
          </span>
        </div>
      ))}
    </div>
  );
}

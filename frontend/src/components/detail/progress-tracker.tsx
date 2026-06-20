'use client';

import { useAppContext } from '@/context/app-context';
import type { WorkerName } from '@/types/api';
import { QUERY_BUDGET_SECONDS } from '@/lib/constants';

interface ProgressNode {
  id: string;
  label: string;
  worker?: WorkerName;
}

const NODES: ProgressNode[] = [
  { id: 'supervisor', label: 'Supervisor 分类' },
  { id: 'doc', label: '文档 Worker', worker: 'doc' },
  { id: 'code', label: '代码 Worker', worker: 'code' },
  { id: 'sql', label: 'SQL Worker', worker: 'sql' },
  { id: 'synthesis', label: 'Synthesis 合成' },
];

export default function ProgressTracker() {
  const { state } = useAppContext();
  const { currentQuery } = state;
  const elapsed = currentQuery.elapsed_ms / 1000;

  const getNodeStatus = (node: ProgressNode) => {
    if (node.id === 'supervisor') {
      if (currentQuery.supervisor.status === 'done') return 'completed';
      if (currentQuery.phase !== 'idle') return 'running';
      return 'pending';
    }
    if (node.id === 'synthesis') {
      if (currentQuery.synthesis.status === 'done') return 'completed';
      if (currentQuery.synthesis.status === 'running') return 'running';
      return 'pending';
    }
    if (node.worker) {
      const w = currentQuery.workers[node.worker];
      if (w.status === 'done') return 'completed';
      if (w.status === 'running') return 'running';
      if (w.status === 'timeout' || w.status === 'error') return 'warning';
      if (w.status === 'waiting_confirmation') return 'warning';
      return 'pending';
    }
    return 'pending';
  };

  const getNodeDetail = (node: ProgressNode): string => {
    if (node.id === 'supervisor') {
      return currentQuery.supervisor.elapsed_ms ? `${currentQuery.supervisor.elapsed_ms}ms` : '';
    }
    if (node.id === 'synthesis') {
      if (currentQuery.synthesis.status === 'running') return '生成中…';
      return '';
    }
    if (node.worker) {
      const w = currentQuery.workers[node.worker];
      if (w.status === 'done') return `${w.elapsed_ms ?? 0}ms · ${w.result_count ?? 0}条`;
      if (w.status === 'running') return w.progress_status ?? '检索中…';
      if (w.status === 'timeout') return w.error_message ?? '超时';
      if (w.status === 'waiting_confirmation') return '等待确认';
      if (w.status === 'error') return w.error_message ?? '错误';
      return '即将启动…';
    }
    return '';
  };

  return (
    <div className="p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)] mb-2 px-1">
        查询进度
      </div>

      <div className="flex flex-col gap-1">
        {NODES.map(node => {
          const status = getNodeStatus(node);
          const detail = getNodeDetail(node);

          return (
            <div key={node.id}>
              <div
                className="flex items-center gap-2 px-2 py-1.5 rounded-md text-[11px] transition-colors"
                style={{
                  color:
                    status === 'completed' ? 'var(--success)' :
                    status === 'running' ? 'var(--primary)' :
                    status === 'warning' ? 'var(--warning)' :
                    'var(--muted-foreground)',
                  backgroundColor:
                    status === 'running' ? 'var(--primary-bg)' :
                    status === 'warning' ? 'rgba(255,149,0,0.08)' :
                    'transparent',
                }}
              >
                <span className="w-4 text-center text-xs flex-shrink-0">
                  {status === 'completed' ? '✓' :
                   status === 'running' ? '◉' :
                   status === 'warning' ? '⚠️' : '·'}
                </span>
                <span className="flex-1">{node.label}</span>
                <span className="text-[10px]" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {detail}
                </span>
              </div>

              {/* Progress bar for running workers */}
              {status === 'running' && node.worker && (
                <div className="h-[3px] bg-[var(--muted)] rounded-full mx-2 mb-0.5 overflow-hidden" style={{ marginLeft: '24px' }}>
                  <div
                    className="h-full bg-[var(--primary)] rounded-full transition-all duration-500"
                    style={{ width: `${Math.min((currentQuery.workers[node.worker]?.elapsed_ms ?? 0) / 30, 95)}%` }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Time Budget */}
      <div className="mt-3 px-3 py-2 bg-[var(--primary-bg)] border-l-[3px] border-l-[var(--primary)] rounded-r-md text-[11px] font-medium text-[var(--primary)]" style={{ fontVariantNumeric: 'tabular-nums' }}>
        ⏱ 已用 {elapsed.toFixed(1)}s / 预算 {QUERY_BUDGET_SECONDS}s
      </div>
    </div>
  );
}

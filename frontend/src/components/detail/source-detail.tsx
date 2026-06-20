'use client';

import { useAppContext } from '@/context/app-context';
import type { Source } from '@/types/api';

export default function SourceDetail() {
  const { state, dispatch } = useAppContext();
  const sources = state.currentQuery.result?.sources ?? [];
  const highlighted = state.highlightedSourceIndex;

  const handleHighlight = (index: number | null) => {
    dispatch({ type: 'HIGHLIGHT_SOURCE', index });
  };

  if (sources.length === 0) {
    return (
      <div className="p-3">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)] mb-2 px-1">
          来源详情
        </div>
        <p className="text-[10px] text-[var(--muted-foreground)] text-center py-4">暂无来源</p>
      </div>
    );
  }

  return (
    <div className="p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)] mb-2 px-1">
        来源详情
      </div>

      <div className="flex flex-col gap-2">
        {sources.map((source, i) => (
          <div
            key={i}
            onMouseEnter={() => handleHighlight(i)}
            onMouseLeave={() => handleHighlight(null)}
            className="bg-[var(--card)] border rounded-[10px] p-3 cursor-pointer transition-all duration-200 hover:shadow-md"
            style={{
              borderColor: highlighted === i ? 'var(--primary)' : 'var(--border)',
              boxShadow: highlighted === i ? '0 0 0 2px var(--primary-bg)' : undefined,
              transform: highlighted === i ? 'scale(1.02)' : undefined,
            }}
          >
            {/* Header */}
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm flex-shrink-0">
                {source.source_type === 'doc' ? '📄' : source.source_type === 'code' ? '💻' : '🗄️'}
              </span>
              <span className="font-medium text-[12px] text-[var(--primary)] truncate flex-1 min-w-0">
                {source.metadata.title ?? source.metadata.file_path ?? source.metadata.table_name ?? '未知来源'}
              </span>
            </div>

            {/* Meta */}
            <div className="text-[10px] text-[var(--muted-foreground)] mb-2">
              {source.metadata.version && `v${source.metadata.version} · `}
              {source.metadata.author && `${source.metadata.author} · `}
              相关度 {(source.relevance_score * 100).toFixed(0)}%
            </div>

            {/* Snippet */}
            <div className="text-[11px] text-[var(--muted-foreground)] leading-relaxed max-h-[60px] overflow-hidden relative">
              {source.content}
            </div>

            {/* Actions */}
            <div className="flex gap-2 mt-2 text-[10px]">
              <button className="text-[var(--primary)] px-2 py-1 rounded-md hover:bg-[var(--primary-bg)] transition-colors">
                ↗ 打开原文
              </button>
              <button className="text-[var(--primary)] px-2 py-1 rounded-md hover:bg-[var(--primary-bg)] transition-colors">
                📋 复制引用
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

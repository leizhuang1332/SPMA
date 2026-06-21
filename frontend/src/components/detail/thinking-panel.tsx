'use client';

import { useState, useRef, useEffect } from 'react';

interface ThinkingPanelProps {
  chunks: string[];
  isStreaming: boolean;
  defaultCollapsed?: boolean;
}

export default function ThinkingPanel({
  chunks,
  isStreaming,
  defaultCollapsed = true,
}: ThinkingPanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const contentRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when expanded and streaming
  useEffect(() => {
    if (!collapsed && isStreaming && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [chunks, collapsed, isStreaming]);

  // Don't render if no content and not streaming
  if (chunks.length === 0 && !isStreaming) {
    return null;
  }

  const totalTokens = chunks.join('').length;

  return (
    <div className="my-2">
      {collapsed ? (
        <button
          onClick={() => setCollapsed(false)}
          className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs
                     bg-[var(--thinking-bg)] border border-[var(--thinking-border)]
                     text-[var(--thinking-fg)] hover:opacity-80
                     transition-opacity cursor-pointer w-full"
        >
          <span className="text-sm">🧠</span>
          <span className="flex-1 text-left">
            {isStreaming ? '模型思考中…' : '模型思考过程'}
          </span>
          <span className="text-[10px] opacity-60">
            {totalTokens > 0 ? `${totalTokens} chars` : ''}
          </span>
          <span className="text-[10px] opacity-60 ml-1">展开 ▾</span>
        </button>
      ) : (
        <div className="rounded-md border border-[var(--thinking-border)]
                        bg-[var(--thinking-bg)] overflow-hidden">
          <button
            onClick={() => setCollapsed(true)}
            className="flex items-center gap-2 px-3 py-1.5 w-full text-xs
                       text-[var(--thinking-fg)] font-semibold
                       border-b border-[var(--thinking-border)]
                       hover:opacity-80 transition-opacity"
          >
            <span className="text-sm">🧠</span>
            <span className="flex-1 text-left">模型思考过程</span>
            <span className="text-[10px] opacity-60">折叠 ▴</span>
          </button>
          <div
            ref={contentRef}
            className="px-3 py-2 text-xs text-[var(--thinking-fg)]
                       italic leading-relaxed max-h-[200px] overflow-y-auto
                       whitespace-pre-wrap"
          >
            {chunks.join('')}
            {isStreaming && (
              <span className="inline-block w-2 h-4 bg-[var(--primary)]
                              animate-pulse ml-0.5 align-middle" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

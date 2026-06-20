'use client';

import { useState, useRef, useCallback } from 'react';
import { useAppContext } from '@/context/app-context';
import { useKeyboard, getModifierKey } from '@/hooks/useKeyboard';
import { SOURCE_OPTIONS, CHAT_INPUT_MAX_ROWS } from '@/lib/constants';
import { cn } from '@/lib/utils';
import type { SourceType } from '@/types/api';

interface ChatInputProps {
  onSubmit: (query: string, sources?: SourceType[]) => void;
  disabled?: boolean;
}

export default function ChatInput({ onSubmit, disabled }: ChatInputProps) {
  const { state } = useAppContext();
  const [value, setValue] = useState('');
  const [selectedSource, setSelectedSource] = useState<string>('all');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const hasContent = value.trim().length > 0;
  const mod = getModifierKey();

  const handleSubmit = useCallback(() => {
    if (!hasContent || disabled) return;
    const sources = selectedSource === 'all'
      ? undefined
      : [selectedSource as SourceType];
    onSubmit(value.trim(), sources);
    setValue('');
  }, [value, selectedSource, hasContent, disabled, onSubmit]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
    // Auto-resize
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  };

  return (
    <div className="px-4 py-3" style={{ borderTop: '0.5px solid var(--border)' }}>
      {/* Source Selector */}
      <div className="inline-flex bg-[var(--muted)] rounded-md p-0.5 mb-2 gap-px">
        {SOURCE_OPTIONS.map(opt => (
          <button
            key={opt.key}
            onClick={() => setSelectedSource(opt.key)}
            className={cn(
              'px-3 py-1.5 rounded-[5px] text-[11px] font-medium transition-all duration-200',
              selectedSource === opt.key
                ? 'bg-[var(--background)] text-[var(--foreground)] shadow-sm'
                : 'text-[var(--muted-foreground)] hover:text-[var(--foreground)]',
            )}
            role="radio"
            aria-checked={selectedSource === opt.key}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Input Row */}
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          placeholder="输入问题…"
          rows={1}
          disabled={disabled}
          className="flex-1 resize-none px-3.5 py-2 rounded-[18px] border bg-[var(--muted)] text-[13px] text-[var(--foreground)] placeholder:text-[var(--muted-foreground)] outline-none focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--ring)] disabled:opacity-50"
          style={{ borderColor: 'var(--border)', minHeight: '38px', maxHeight: '120px' }}
        />
        <button
          onClick={handleSubmit}
          disabled={disabled || !hasContent}
          className={cn(
            'w-[38px] h-[38px] rounded-full flex items-center justify-center text-base flex-shrink-0 transition-all duration-200',
            hasContent
              ? 'bg-[var(--primary)] text-white shadow-[0_2px_12px_rgba(0,122,255,0.35)] hover:opacity-90'
              : 'bg-[var(--muted)] text-[var(--muted-foreground)]',
          )}
          aria-label="发送消息"
        >
          ↑
        </button>
      </div>

      {/* Shortcut Hint */}
      <div className="text-[10px] text-[var(--muted-foreground)] text-right mt-1">
        <kbd className="inline-block bg-[var(--muted)] px-1 py-px rounded border text-[10px] font-sans" style={{ borderColor: 'var(--border)' }}>
          {mod}K
        </kbd>
        {' '}新建 ·{' '}
        <kbd className="inline-block bg-[var(--muted)] px-1 py-px rounded border text-[10px] font-sans" style={{ borderColor: 'var(--border)' }}>
          {mod}Enter
        </kbd>
        {' '}发送 ·{' '}
        <kbd className="inline-block bg-[var(--muted)] px-1 py-px rounded border text-[10px] font-sans" style={{ borderColor: 'var(--border)' }}>
          {mod}/
        </kbd>
        {' '}切换源 ·{' '}
        <kbd className="inline-block bg-[var(--muted)] px-1 py-px rounded border text-[10px] font-sans" style={{ borderColor: 'var(--border)' }}>
          Esc
        </kbd>
        {' '}取消
      </div>
    </div>
  );
}

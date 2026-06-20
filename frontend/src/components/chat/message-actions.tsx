'use client';

import { useState } from 'react';
import { cn } from '@/lib/utils';

interface MessageActionsProps {
  onLike: () => void;
  onDislike: (reason?: string, comment?: string) => void;
  onCopy: () => void;
}

export default function MessageActions({ onLike, onDislike, onCopy }: MessageActionsProps) {
  const [liked, setLiked] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedbackComment, setFeedbackComment] = useState('');

  const handleLike = () => {
    setLiked(!liked);
    onLike();
  };

  const handleDislike = () => {
    setShowFeedback(true);
  };

  const handleFeedbackSubmit = (reason: string) => {
    onDislike(reason, feedbackComment);
    setShowFeedback(false);
    setFeedbackComment('');
  };

  return (
    <div className="flex gap-1 mt-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
      <button
        onClick={handleLike}
        className={cn(
          'w-7 h-7 rounded-md flex items-center justify-center text-[13px] transition-colors hover:bg-[var(--muted)]',
          liked ? 'text-[var(--primary)]' : 'text-[var(--muted-foreground)]',
        )}
        aria-label="点赞"
      >
        👍
      </button>
      <div className="relative">
        <button
          onClick={handleDislike}
          className="w-7 h-7 rounded-md flex items-center justify-center text-[13px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]"
          aria-label="点踩"
        >
          👎
        </button>
        {showFeedback && (
          <div className="absolute bottom-full left-0 mb-2 bg-[var(--card)] border rounded-lg p-3 shadow-lg z-10 min-w-[200px]" style={{ borderColor: 'var(--border)' }}>
            <p className="text-[11px] text-[var(--muted-foreground)] mb-2">请选择原因：</p>
            {[
              { key: 'inaccurate', label: '不准确' },
              { key: 'incomplete', label: '不完整' },
              { key: 'irrelevant', label: '不相关' },
              { key: 'too_slow', label: '太慢' },
            ].map(r => (
              <button
                key={r.key}
                onClick={() => handleFeedbackSubmit(r.key)}
                className="block w-full text-left px-2 py-1 text-[11px] rounded hover:bg-[var(--muted)] transition-colors"
              >
                {r.label}
              </button>
            ))}
            <textarea
              value={feedbackComment}
              onChange={e => setFeedbackComment(e.target.value)}
              placeholder="补充说明（可选）…"
              className="w-full mt-2 px-2 py-1 text-[10px] rounded border bg-[var(--muted)] resize-none outline-none focus:ring-1 focus:ring-[var(--primary)]"
              style={{ borderColor: 'var(--border)' }}
              rows={2}
            />
            <div className="flex gap-2 mt-2">
              <button
                onClick={() => handleFeedbackSubmit('other')}
                className="px-2 py-1 text-[10px] bg-[var(--primary)] text-white rounded hover:opacity-90"
              >
                提交
              </button>
              <button
                onClick={() => setShowFeedback(false)}
                className="px-2 py-1 text-[10px] text-[var(--muted-foreground)] rounded hover:bg-[var(--muted)]"
              >
                取消
              </button>
            </div>
          </div>
        )}
      </div>
      <button
        onClick={onCopy}
        className="w-7 h-7 rounded-md flex items-center justify-center text-[13px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]"
        aria-label="复制"
      >
        📋
      </button>
    </div>
  );
}

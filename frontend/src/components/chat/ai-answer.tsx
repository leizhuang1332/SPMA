'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';

interface AIAnswerProps {
  text: string;
}

export default function AIAnswer({ text }: AIAnswerProps) {
  const components: Components = {
    code({ className, children, ...props }) {
      const match = /language-(\w+)/.exec(className || '');
      const isInline = !match;
      if (isInline) {
        return (
          <code
            className="font-mono text-[12px] bg-[var(--muted)] px-1 py-0.5 rounded"
            {...props}
          >
            {children}
          </code>
        );
      }
      return (
        <pre className="bg-[#1c1c1e] text-[#e4e4e7] p-3 rounded-md overflow-x-auto font-mono text-[12px] leading-relaxed my-3">
          <code className={className} {...props}>
            {children}
          </code>
        </pre>
      );
    },
    table({ children }) {
      return (
        <div className="overflow-x-auto my-2">
          <table className="w-full border-collapse text-[12px]">{children}</table>
        </div>
      );
    },
    th({ children }) {
      return (
        <th className="text-left px-2.5 py-1.5 font-semibold text-[var(--muted-foreground)] text-[11px]" style={{ borderBottom: '0.5px solid var(--border)' }}>
          {children}
        </th>
      );
    },
    td({ children }) {
      return (
        <td className="px-2.5 py-1.5" style={{ borderBottom: '0.5px solid var(--border)' }}>
          {children}
        </td>
      );
    },
    blockquote({ children }) {
      return (
        <blockquote className="border-l-[3px] border-l-[var(--primary)] pl-3 text-[var(--muted-foreground)] my-2">
          {children}
        </blockquote>
      );
    },
  };

  return (
    <div className="flex gap-3 mb-4">
      {/* Avatar */}
      <div className="w-7 h-7 rounded-md bg-[var(--muted)] flex items-center justify-center text-[13px] flex-shrink-0">
        S
      </div>
      {/* Card */}
      <div className="flex-1 min-w-0 bg-[var(--muted)] border rounded-[10px] px-4 py-3 text-[13px] leading-relaxed" style={{ borderColor: 'var(--border)' }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={components}
        >
          {text}
        </ReactMarkdown>
      </div>
    </div>
  );
}

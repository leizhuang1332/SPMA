'use client';

interface UserMessageProps {
  text: string;
}

export default function UserMessage({ text }: UserMessageProps) {
  return (
    <div className="flex justify-end mb-4">
      <div className="max-w-[70%] bg-[var(--primary)] text-white px-3.5 py-2.5 rounded-[14px_14px_3px_14px] text-[13px] leading-relaxed shadow-sm">
        {text}
      </div>
    </div>
  );
}

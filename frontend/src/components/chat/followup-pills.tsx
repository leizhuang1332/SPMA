'use client';

interface FollowupPillsProps {
  pills: string[];
  onSelect: (pill: string) => void;
}

export default function FollowupPills({ pills, onSelect }: FollowupPillsProps) {
  if (!pills.length) return null;

  return (
    <div className="flex flex-wrap gap-2 mt-3">
      {pills.map((pill, i) => (
        <button
          key={i}
          onClick={() => onSelect(pill)}
          className="px-3 py-1.5 rounded-full bg-[var(--muted)] border text-[11px] text-[var(--foreground)] cursor-pointer transition-all duration-150 hover:border-[var(--primary)] hover:bg-[var(--primary-bg)] hover:-translate-y-px active:scale-95"
          style={{ borderColor: 'var(--border)' }}
        >
          {pill}
        </button>
      ))}
    </div>
  );
}

'use client';

import { useAppContext } from '@/context/app-context';
import { DEGRADATION_MESSAGES } from '@/lib/constants';

export default function DegradationBanner() {
  const { state } = useAppContext();
  const degradation = state.currentQuery.degradation;

  if (!degradation || degradation.level === 0) return null;

  const isL4 = degradation.level === 4;

  if (isL4) {
    return (
      <div className="fixed inset-0 bg-[var(--background)] z-50 flex items-center justify-center">
        <div className="text-center max-w-sm px-6">
          <div className="text-4xl mb-4">⚠️</div>
          <h2 className="text-lg font-semibold text-[var(--foreground)] mb-2">服务暂不可用</h2>
          <p className="text-[13px] text-[var(--muted-foreground)] mb-4">
            当前所有数据源不可用，请稍后重试。
            {degradation.auto_recovery_eta && (
              <span> 预计恢复时间：{degradation.auto_recovery_eta}</span>
            )}
          </p>
          <button
            onClick={() => window.location.reload()}
            className="px-6 py-2 bg-[var(--primary)] text-white rounded-full text-[13px] hover:opacity-90 transition-opacity"
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 px-3.5 py-2.5 rounded-md mb-3 text-[13px] animate-slide-down"
      style={{
        backgroundColor: 'var(--warning-bg, rgba(255,149,0,0.08))',
        border: '1px solid var(--warning)',
      }}
    >
      <span className="text-[15px] flex-shrink-0">⚠️</span>
      <span className="flex-1 font-medium text-[var(--foreground)]">
        {degradation.user_notice ?? DEGRADATION_MESSAGES[degradation.level] ?? `降级 L${degradation.level}`}
      </span>
      {degradation.level > 0 && (
        <span className="px-2 py-0.5 rounded-full bg-[var(--warning)] text-white text-[10px] font-semibold flex-shrink-0">
          L{degradation.level}
        </span>
      )}
    </div>
  );
}

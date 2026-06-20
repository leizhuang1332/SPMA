'use client';

import { useAppContext } from '@/context/app-context';
import ProgressTracker from '@/components/detail/progress-tracker';
import SourceDetail from '@/components/detail/source-detail';
import DataFreshness from '@/components/detail/data-freshness';

export default function DetailPanel() {
  const { state } = useAppContext();
  const mode = state.detailPanelMode;

  if (mode === 'progress') {
    return (
      <div className="flex flex-col h-full overflow-y-auto">
        <ProgressTracker />
      </div>
    );
  }

  if (mode === 'sources') {
    return (
      <div className="flex flex-col h-full overflow-y-auto">
        <SourceDetail />
        <DataFreshness />
      </div>
    );
  }

  // Idle state
  return (
    <div className="flex items-center justify-center h-full text-[11px] text-[var(--muted-foreground)] text-center px-4">
      <span>提交问题后展示<br />来源详情与进度</span>
    </div>
  );
}

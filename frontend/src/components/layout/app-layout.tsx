'use client';

import { SIDEBAR_WIDTH, DETAIL_PANEL_WIDTH } from '@/lib/constants';
import Sidebar from './sidebar';
import ChatPanel from './chat-panel';
import DetailPanel from './detail-panel';

export default function AppLayout() {
  return (
    <div className="flex h-screen overflow-hidden bg-[var(--background)]">
      {/* Left Sidebar */}
      <aside
        className="glass-sidebar flex-shrink-0 border-r flex flex-col"
        style={{ width: SIDEBAR_WIDTH, borderColor: 'var(--border)' }}
      >
        <Sidebar />
      </aside>

      {/* Center Chat */}
      <main className="flex-1 flex flex-col min-w-0 bg-[var(--background)]">
        <ChatPanel />
      </main>

      {/* Right Detail Panel */}
      <aside
        className="glass-sidebar flex-shrink-0 border-l flex flex-col"
        style={{ width: DETAIL_PANEL_WIDTH, borderColor: 'var(--border)' }}
      >
        <DetailPanel />
      </aside>
    </div>
  );
}

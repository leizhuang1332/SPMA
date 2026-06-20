'use client';

interface EmptyStateProps {
  onExampleClick: (query: string) => void;
}

export default function EmptyState({ onExampleClick }: EmptyStateProps) {
  const examples = [
    { query: '支付回调接口为什么在生产环境返回502？涉及哪些代码和需求变更？', icon: '🔍', label: '支付回调502 — 需求溯源 + 代码定位' },
    { query: '查询最近30天订单表payment_status=FAILED的分布，按小时聚合', icon: '🗄️', label: 'SQL 查询 — 订单失败分布（触发确认流程）' },
    { query: '降级测试 — 模拟检索超时场景', icon: '⚠️', label: '降级测试 — 模拟 Worker 超时' },
  ];

  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-center max-w-[420px]">
        <div className="w-12 h-12 bg-[var(--primary)] text-white rounded-[10px] flex items-center justify-center text-[22px] font-bold mx-auto mb-4">
          S
        </div>
        <p className="text-base font-semibold text-[var(--foreground)] mb-1">SPMA 企业级智能问答</p>
        <p className="text-[13px] text-[var(--muted-foreground)] mb-4">多源 RAG 检索 — 文档 · 代码仓库 · 数据库</p>
        <div className="flex flex-col gap-2">
          {examples.map((ex, i) => (
            <button
              key={i}
              onClick={() => onExampleClick(ex.query)}
              className="px-3.5 py-2.5 rounded-[10px] bg-[var(--muted)] border text-left text-[13px] text-[var(--foreground)] cursor-pointer transition-all duration-150 hover:border-[var(--primary)] hover:bg-[var(--primary-bg)] hover:-translate-y-px active:scale-[0.985]"
              style={{ borderColor: 'var(--border)' }}
            >
              <span className="mr-1.5">{ex.icon}</span> {ex.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

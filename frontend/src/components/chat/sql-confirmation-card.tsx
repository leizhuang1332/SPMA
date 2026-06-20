'use client';

import { useState } from 'react';
import { useAppContext } from '@/context/app-context';
import * as api from '@/lib/api';

export default function SQLConfirmationCard() {
  const { state, dispatch } = useAppContext();
  const prompt = state.currentQuery.confirmationPrompt;
  const [editing, setEditing] = useState(false);
  const [modifiedSql, setModifiedSql] = useState('');

  if (!prompt) return null;

  const handleConfirm = () => {
    if (!state.currentQuery.queryId) return;
    api.confirmSQL(state.currentQuery.queryId, {
      query_id: state.currentQuery.queryId,
      action: editing ? 'modify' : 'confirm',
      modified_sql: editing ? modifiedSql : undefined,
    }).catch(console.error);
    dispatch({ type: 'QUERY_CONFIRMATION_RESOLVED' });
  };

  const handleCancel = () => {
    dispatch({ type: 'QUERY_CONFIRMATION_RESOLVED' });
    // In production, this would send a cancel confirmation to the API
  };

  const handleModify = () => {
    setEditing(true);
    setModifiedSql(prompt.sql);
  };

  const riskColor = {
    low: 'var(--success)',
    medium: 'var(--warning)',
    high: 'var(--danger)',
  }[prompt.risk_level] ?? 'var(--warning)';

  return (
    <div className="my-3 rounded-[10px] overflow-hidden animate-slide-up" style={{ border: '2px solid var(--warning)' }}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 font-semibold text-[13px]" style={{ backgroundColor: 'var(--warning-bg, rgba(255,149,0,0.08))' }}>
        <span>⚠️</span>
        <span>高风险 SQL — 需要你确认</span>
        <span className="px-2 py-0.5 rounded-full text-white text-[10px] font-semibold" style={{ backgroundColor: riskColor }}>
          {prompt.risk_level === 'high' ? '高风险' : prompt.risk_level === 'medium' ? '中风险' : '低风险'}
        </span>
      </div>

      {/* Body */}
      <div className="p-4 bg-[var(--card)]">
        {editing ? (
          <textarea
            value={modifiedSql}
            onChange={e => setModifiedSql(e.target.value)}
            className="w-full min-h-[80px] font-mono text-[12px] p-3 rounded-md border resize-y outline-none"
            style={{
              backgroundColor: '#1c1c1e',
              color: '#e4e4e7',
              borderColor: 'var(--primary)',
            }}
          />
        ) : (
          <pre className="bg-[#1c1c1e] text-[#e4e4e7] p-3 rounded-md overflow-x-auto font-mono text-[12px] leading-relaxed mb-3">
            {prompt.sql}
          </pre>
        )}

        {/* Meta */}
        <div className="flex gap-4 text-[11px] text-[var(--muted-foreground)] mb-3">
          <span>📊 {prompt.tables_affected.join(', ')}</span>
          {prompt.risk_reasons.length > 0 && (
            <span>⚠️ {prompt.risk_reasons[0]}</span>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2">
          <button
            onClick={handleConfirm}
            className="px-4 py-2 rounded-full bg-[var(--primary)] text-white text-[12px] font-medium hover:opacity-90 transition-opacity active:scale-95"
          >
            ✓ {editing ? '提交修改' : '确认执行'}
          </button>
          {!editing && (
            <button
              onClick={handleModify}
              className="px-4 py-2 rounded-full bg-[var(--muted)] text-[var(--foreground)] border text-[12px] font-medium hover:bg-[var(--background)] transition-colors active:scale-95"
              style={{ borderColor: 'var(--border)' }}
            >
              ✎ 修改 SQL
            </button>
          )}
          <button
            onClick={handleCancel}
            className="px-4 py-2 rounded-full text-[var(--danger)] text-[12px] font-medium hover:bg-[var(--danger-bg)] transition-colors active:scale-95"
            style={{ '--danger-bg': 'rgba(255,59,48,0.08)' } as React.CSSProperties}
          >
            ✕ 取消
          </button>
        </div>
      </div>
    </div>
  );
}

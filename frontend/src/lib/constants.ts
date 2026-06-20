export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || 'https://spma.internal.example.com/api/v1';

export const DEFAULT_TIMEOUT_MS = 30000;
export const SSE_MAX_RECONNECT_ATTEMPTS = 3;
export const SSE_RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000];
export const QUERY_BUDGET_SECONDS = 10;
export const CHAT_INPUT_MAX_ROWS = 6;
export const SIDEBAR_WIDTH = 210;
export const DETAIL_PANEL_WIDTH = 250;

export const SOURCE_OPTIONS = [
  { key: 'all', label: '全部源' },
  { key: 'doc', label: '📄 文档' },
  { key: 'code', label: '💻 代码' },
  { key: 'sql', label: '🗄️ SQL' },
] as const;

export const EXAMPLE_QUESTIONS = [
  '📋 "REQ-187 改了哪些代码和数据库表？"',
  '📊 "上周新增用户数是多少？按渠道分组"',
  '🔍 "oauth.py 中 TokenService 的调用链是怎样的？"',
];

export const DEGRADATION_MESSAGES: Record<number, string> = {
  0: 'L0 全功能',
  1: 'L1 LLM 降级 — 已切换至本地模型',
  2: 'L2 检索降级 — 关键词搜索',
  3: 'L3 缓存兜底 — 返回热点问答',
  4: 'L4 静态兜底 — 服务暂不可用',
};

export const FEEDBACK_REASONS = [
  { key: 'inaccurate', label: '不准确' },
  { key: 'incomplete', label: '不完整' },
  { key: 'irrelevant', label: '不相关' },
  { key: 'too_slow', label: '太慢' },
  { key: 'other', label: '其他' },
] as const;

# SPMA 前端核心问答体验设计规格

> 日期：2026-06-20 | 状态：待审查 | 范围：核心问答体验（主对话 + 会话管理 + 反馈）

## 一、设计目标

为 SPMA（企业级多源 RAG 智能问答系统）构建核心问答前端，覆盖用户从提问到获取答案的完整链路，消除检索等待焦虑，提供专业克制的 macOS 原生风格体验。

### 用户画像

- **产品经理**：需求溯源——某个功能为什么这么做？历史版本演化？
- **开发工程师**：故障定位——bug 关联哪个需求？涉及哪些代码和表？

---

## 二、技术选型

| 决策 | 选择 | 理由 |
|------|------|------|
| 框架 | Next.js 14 (App Router) | SSR 可选，文件路由，SEO 友好 |
| 语言 | TypeScript strict | 类型安全，减少运行时错误 |
| UI 库 | shadcn/ui | 无运行时依赖，CSS 变量主题，Tree-shakable |
| 样式 | Tailwind CSS + CSS Variables | shadcn/ui 原生方案，主题切换零成本 |
| 状态管理 | React Context + useReducer | 范围可控，无需引入 Redux |
| SSE 客户端 | EventSource API + 自定义 hook | 流式查询原生支持 |
| Markdown | react-markdown + remark-gfm | 回答正文渲染 + GFM 表格支持 |
| 代码高亮 | rehype-highlight | 代码/SQL 片段语法高亮 |
| 动画 | CSS transitions + framer-motion | 微交互流畅，macOS 风格过渡 |
| 字体 | SF Pro (系统) + JetBrains Mono (代码) | macOS 原生体验 |

---

## 三、页面路由

```
/                         主对话页（首页，默认无会话）
  └─ /chat/:sessionId     指定会话的对话页（加载历史）
```

只有两个核心路由。会话管理、来源详情、反馈均在主页面内通过面板/弹窗承载，无需跳转。

---

## 四、布局架构：三栏布局

```
┌──────────────┬──────────────────────────┬──────────────┐
│  210px 左栏   │      flex 1 中栏          │  250px 右栏   │
│              │                          │              │
│  Logo + 新建  │  会话标题 + 操作           │  来源详情     │
│  搜索会话     │  ─────────────────────── │  ──────────  │
│  会话列表     │  消息流（虚拟滚动）         │  来源卡片     │
│  ├ 选中高亮   │  ├ 用户气泡               │  ├ PRD 文档   │
│  ├ 标题+时间  │  ├ AI 回答（Markdown）    │  ├ 代码文件   │
│  └ 轮次数    │  │  ├ 内联引用标注          │  └ 数据表     │
│              │  │  ├ SQL 块（可编辑）      │              │
│  系统状态栏   │  │  ├ 追问建议 pills       │  数据源新鲜度  │
│  L0 全功能   │  │  └ 操作（👍👎📋）       │  文档·代码·DB │
│  数据源健康   │  │                        │              │
│              │  ├ 降级横幅（内联）         │              │
│              │  │                        │              │
│              │  输入区                    │              │
│              │  ├ 数据源 segmented ctrl   │              │
│              │  ├ 文本框 + 发送按钮        │              │
│              │  └ 快捷键提示               │              │
└──────────────┴──────────────────────────┴──────────────┘
```

右栏有两种模式：
- **进度模式**（查询处理中）：SSE 实时追踪，各 Worker 状态 + 进度条 + 耗时
- **详情模式**（查询完成后）：来源卡片列表 + 数据源新鲜度

---

## 五、视觉风格：macOS 原生

### 色彩系统

| Token | 浅色值 | 深色值 | 用途 |
|-------|--------|--------|------|
| `--primary` | `#007AFF` | `#0A84FF` | 按钮、链接、选中态 |
| `--primary-bg` | `rgba(0,122,255,0.08)` | `rgba(10,132,255,0.15)` | 选中底色、标签背景 |
| `--warning` | `#FF9500` | `#FF9F0A` | 降级横幅、警告标记 |
| `--success` | `#34C759` | `#30D158` | 健康状态、成功标记 |
| `--danger` | `#FF3B30` | `#FF453A` | 错误、删除 |
| `--bg-primary` | `#FFFFFF` | `#1C1C1E` | 主背景 |
| `--bg-secondary` | `rgba(0,0,0,0.02)` | `rgba(255,255,255,0.04)` | 卡片/侧栏背景 |
| `--sidebar-bg` | `rgba(250,250,250,0.7)` | `rgba(30,30,32,0.7)` | 毛玻璃侧栏 |
| `--border` | `rgba(0,0,0,0.06)` | `rgba(255,255,255,0.06)` | 分割线/边框 |
| `--text-primary` | `#1D1D1F` | `#F5F5F7` | 正文 |
| `--text-secondary` | `#86868B` | `#98989D` | 辅助文字 |

### 字体阶梯

| 层级 | 大小 | 字重 | 行高 | 用途 |
|------|------|------|------|------|
| 页面标题 | 20px | 600 | 1.3 | 管理后台（本阶段不用） |
| 段落标题 | 16px | 600 | 1.4 | 回答中的标题 |
| 正文 | 13px | 400 | 1.6 | 对话正文 |
| 辅助文字 | 11px | 400 | 1.4 | 元数据、时间戳 |
| 代码 | 12px | 400 | 1.5 | SF Mono / JetBrains Mono |

### 圆角 & 间距

- 卡片圆角：10px
- 控件圆角：7px
- 按钮/输入框：全圆角（18-20px）
- 基础间距单位：4px（组件内）8px（组件间）
- 侧栏毛玻璃：`backdrop-filter: blur(20px)`

### 主题切换

- 默认跟随系统 `prefers-color-scheme`
- 用户手动切换后存入 `localStorage`，下次访问优先读取
- 切换通过 CSS 变量瞬时完成，无闪烁

---

## 六、组件树

```
AppLayout (三栏容器 + 主题 Provider)
│
├─ Sidebar (左栏 — 毛玻璃半透明)
│   ├─ SidebarHeader
│   │   ├─ Logo (SPMA 蓝底白字方块)
│   │   └─ NewSessionButton (+)
│   ├─ SessionSearch (搜索历史会话)
│   ├─ SessionList (虚拟滚动)
│   │   └─ SessionItem[]
│   │       ├─ 标题 (首轮查询自动生成)
│   │       ├─ 轮次数
│   │       ├─ 时间戳
│   │       └─ 删除按钮 (hover 显示)
│   └─ SystemStatusBar
│       ├─ 降级级别指示器 (L0-L4)
│       └─ 数据源健康摘要 (📄💻🗄️)
│
├─ ChatPanel (中栏 — 主对话区)
│   ├─ ChatHeader
│   │   ├─ 当前会话标题
│   │   ├─ 轮次计数
│   │   └─ 主题切换按钮 (🌓)
│   ├─ MessageList
│   │   ├─ EmptyState (无会话时：Logo + 搜索框 + 示例问题)
│   │   ├─ UserMessage (右对齐蓝色气泡)
│   │   └─ AIAnswer (左对齐卡片)
│   │       ├─ AnswerContent (react-markdown 渲染)
│   │       ├─ CitationInline[] (内联引用标记，hover 联动右栏)
│   │       ├─ SQLBlock (语法高亮 + 复制 + 修改按钮)
│   │       ├─ SQLConfirmationCard (高风险 SQL 确认)
│   │       ├─ DegradationBanner (内联降级横幅)
│   │       ├─ FollowupPills (建议追问，stagger 滑入)
│   │       └─ MessageActions (👍 👎 📋)
│   └─ ChatInput
│       ├─ SourceSelector (segmented control: 全部源|文档|代码|SQL)
│       ├─ TextArea (自动增高 + ⌘Enter 发送)
│       └─ SendButton (圆形 ↑ 按钮，有内容时蓝色光晕)
│
└─ DetailPanel (右栏 — 毛玻璃半透明)
    ├─ ProgressTracker (查询处理中)
    │   ├─ SupervisorStatus
    │   ├─ WorkerStatus[] (Doc/Code/SQL 各自进度条)
    │   ├─ SynthesisStatus
    │   └─ TimeBudgetIndicator (已用 Xs/10s)
    ├─ SourceDetail[] (查询完成后)
    │   └─ SourceCard
    │       ├─ 来源类型图标
    │       ├─ 标题 + 元数据
    │       ├─ 内容片段（可展开）
    │       └─ 操作 (打开原文 / 复制引用)
    └─ DataFreshness (数据源新鲜度)
```

---

## 七、数据流

### 查询生命周期

```
用户输入 → ChatInput (POST /query with stream=true)
  │
  ├─ 即时反馈: 用户消息滑入 → AI 占位卡片 → 右栏切为进度模式
  │
  ├─ SSE 事件流处理:
  │   ├─ event: classification
  │   │   → ProgressTracker 标记 Supervisor ✓
  │   │   → 中栏占位卡片显示实体抽取结果
  │   │
  │   ├─ event: worker_start × 3
  │   │   → ProgressTracker 显示三个 Worker 条目
  │   │   → 各条目显示"检索中…"动画
  │   │
  │   ├─ event: worker_progress (多次)
  │   │   → 对应 Worker 进度条更新
  │   │   → 中栏思考文案轮换（"文档命中12条…正在评估完备度…"）
  │   │
  │   ├─ event: worker_result × 3
  │   │   → Worker 标记 ✓ + 结果数量 + 耗时
  │   │   → 如有超时，标记 ⚠️ + 用户提示
  │   │
  │   ├─ event: synthesis (多次 chunk)
  │   │   → 中栏 AI 回答逐 chunk 流式渲染（Markdown）
  │   │   → 引用标注即时生成
  │   │
  │   ├─ event: confirmation_required
  │   │   → SSL 暂停 → 中栏弹出 SQL 确认卡片
  │   │   → 右栏 SQL Worker 显示 ⏸ "等待确认"
  │   │   → 用户操作后 POST /query/{id}/confirm → 流恢复
  │   │
  │   ├─ event: error
  │   │   → 中栏内联降级横幅 + 右栏标注
  │   │   → 流继续，不中断
  │   │
  │   └─ event: done
  │       → 回答完成，移除光标动画
  │       → 右栏 crossfade 切换为来源详情列表
  │       → 追问建议 pills stagger 滑入
  │
  └─ 反馈: 用户 👍/👎 → POST /feedback
```

### 会话管理

```
GET /sessions (列表) → Sidebar SessionList
POST /sessions (新建) → 生成 session_id → 清空中栏
GET /sessions/{id} (历史) → 加载完整对话 → 消息列表回填
DELETE /sessions/{id} (删除) → 确认对话框 → 从列表移除
```

---

## 八、交互设计：五阶段进度反馈

### 阶段 0→1：输入提交

- **触发**：用户点击发送或 ⌘Enter
- **反馈**：
  - 发送按钮缩小消失（200ms scale-out）
  - 用户消息从底部 slide-up 进入消息流
  - AI 占位卡片 fade-in，显示旋转加载指示器
  - 右栏从"提交问题后展示来源"空态切换到进度模式

### 阶段 2：Supervisor 分类（< 200ms）

- **中栏**：占位卡片内容更新——"正在理解你的问题…" → "识别为跨源查询" → "提取实体: 支付回调, 502, 需求变更" → "派发至 3 个 Worker"
- **右栏**：进度树——Supervisor ✓ 180ms → Doc/Code/SQL 条目出现，初始状态"即将启动…"

### 阶段 3：并行检索（0.5s - 3s）

- **中栏**：
  - 音波动效（三条竖线交替伸缩）
  - 思考文案随时间轮换，模拟"思考过程"：
    - "0.5s · 正在搜索 PRD 文档…"
    - "0.8s · 文档检索命中 12 条，正在评估完备度…"
    - "1.2s · 代码仓库 ripgrep 搜索中…"
    - "1.8s · 数据库 Schema 匹配完成，正在生成 SQL…"
- **右栏**：
  - 三个 Worker 条目各自独立进度条（蓝色填充动画）
  - 当前操作描述实时更新（"向量搜索 + BM25 混合" → "评估完备度" → "线索重搜"）
  - 时间预算指示器：⏱ 已用 X.Xs / 预算 10s（蓝色背景 + 左边框）

### 阶段 4：逐个完成（3s - 5s）

- **中栏**：Synthesis chunk 逐段流式渲染 Markdown（标题 → 段落 → 代码块 → 引用标注）
- **右栏**：
  - 完成的 Worker 标记 ✓ + 耗时 + 结果数（绿色）
  - 超时的 Worker 标记 ⚠️ + 超时原因（琥珀色）
  - Synthesis 条目显示旋转动画 + "生成中…"
- **降级处理**：如有 Worker 超时，中栏顶部插入琥珀色内联横幅，右栏同步标注

### 阶段 5：完成

- **中栏**：
  - 闪烁光标消失
  - 完整 Markdown 回答渲染完毕
  - 追问建议 pills 从底部 stagger 滑入（每个 50ms 延迟）
  - 操作栏可见（👍 👎 📋）
- **右栏**：
  - 进度模式 crossfade(300ms) 切换到来源详情列表
  - 3 个来源卡片依次出现
  - 底部数据源新鲜度更新

### SQL 确认中断

- **暂停**：SSE 流在 `confirmation_required` 事件后暂停
- **中栏**：SQL 确认卡片嵌入对话流（琥珀色边框 + 代码高亮 + 影响范围 + 确认/修改/取消三按钮）
- **右栏**：SQL Worker 条目变为 ⏸"等待确认"状态（琥珀色背景 + 边框）
- **恢复**：用户确认后 POST `/query/{id}/confirm` → SSE 流从 SQL Worker 继续 → 正常进入 Synthesis → 完成
- **取消**：SQL 不执行，流直接进入 Synthesis（基于 Doc/Code 结果生成回答）

---

## 九、微交互清单

| 交互点 | 触发条件 | 动画效果 |
|--------|---------|---------|
| 发送按钮 | 输入框有内容 | 蓝色光晕 pulse + ⌘Enter badge 高亮 |
| 消息进入 | 新建消息 | 从底部 slide-up (300ms ease-out) |
| 自动滚动 | 新消息到达 | scroll-smooth 到底部（用户上滑时不抢滚动） |
| 引用联动 | hover 引用标记 `[PRD §5.2]` | 右栏对应卡片高亮 + scale(1.02) |
| 来源展开 | 点击来源卡片 | 内容区展开/折叠 (200ms) |
| 追问 pills | 回答完成后 | stagger 滑入（依次 50ms 延迟） |
| 点赞 | 点击 👍 | scale(1.2) → scale(1) bounce + 蓝色填充 |
| 点踩 | 点击 👎 | 弹出原因选择器（不准确/过时/不完整/太慢）+ 可选评论框 |
| 降级横幅 | 降级触发 | 从顶部 slide-down (300ms) |
| 降级恢复 | L0 恢复 | slide-up 消失 (300ms) |
| 数据源切换 | 点击 segmented control | 选中项凸起 + 阴影过渡 (200ms ease-out) |
| 主题切换 | 点击 🌓 | CSS 变量瞬时切换，无过渡动画 |
| 进度→详情 | 查询 done | crossfade (300ms) |
| 会话 hover | 鼠标悬停会话项 | 删除按钮 fade-in |
| 会话删除 | 点击删除 | 确认对话框 → 会话项 slide-left 消失 |
| 键盘快捷键 | 全局监听 | ⌘K 新会话 · ⌘Enter 发送 · ⌘/ 指定源 · Esc 取消 |

---

## 十、错误 & 边界状态

### 网络错误

- SSE 连接断开：中栏顶部 toast"连接中断，正在重连…" + 自动重连（指数退避 1s/2s/4s/8s）
- 重连成功：从断点继续（通过 GET `/query/{id}` 获取完整结果）
- 重连失败（3次后）：显示"网络不可用，请检查连接" + 手动重试按钮

### 超时处理

- 整体超时（10s）：中栏显示部分结果 + 标注"⏱ 查询超时，已返回部分结果"
- 单个 Worker 超时（2-3s）：内联横幅 + 右栏标记 ⚠️ + 缺失源标注

### 空状态

- 首次使用：中栏展示 Logo + 搜索框 + 3 个示例问题
- 无会话：左栏仅显示"新建会话"按钮
- 无搜索结果：中栏显示"未找到相关信息，请调整查询条件"

### 降级（L0-L4）

| 级别 | 名称 | UI 表现 |
|------|------|---------|
| L0 | 全功能 | 无提示，绿色 L0 badge |
| L1 | LLM 降级 | 琥珀色横幅"已切换至本地 Qwen3 模型" + 标签"Qwen3-8B" |
| L2 | 检索降级 | 横幅"检索降级为关键词搜索" |
| L3 | 缓存兜底 | 横幅"当前返回缓存的热点问答" + "更新于 X 分钟前" |
| L4 | 静态兜底 | 全屏提示"服务暂不可用，请稍后重试" + 静态 FAQ 链接 |

---

## 十一、性能策略

- **虚拟滚动**：消息列表和会话列表使用 `@tanstack/react-virtual`
- **SSE 解析**：使用原生 `EventSource`，避免引入额外依赖
- **Markdown 渲染**：`react-markdown` 的 `components` prop 自定义渲染，代码块懒加载语法高亮
- **主题切换**：纯 CSS 变量，零 JS 开销
- **Bundle**：shadcn/ui 按需引入，避免全量导入

---

## 十二、前端开发 AI Coding 提示词

以下提示词可直接用于 AI 辅助编码，按开发阶段组织。

### 阶段 1：项目初始化

```
使用 Next.js 14 App Router + TypeScript 初始化项目。
技术栈：shadcn/ui (new-york style) + Tailwind CSS + CSS Variables。

1. 配置 shadcn/ui，使用 macOS 风格设计 token：
   - 主色 --primary: #007AFF (浅色) / #0A84FF (深色)
   - 背景 --background: #FFFFFF (浅) / #1C1C1E (深)
   - 边框 --border: rgba(0,0,0,0.06) (浅) / rgba(255,255,255,0.06) (深)
   - 圆角 --radius: 0.625rem

2. 字体配置：
   - 正文 font-sans: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Inter', sans-serif
   - 代码 font-mono: 'SF Mono', 'JetBrains Mono', monospace

3. 实现双主题切换：
   - 使用 next-themes，默认跟随系统
   - 在 layout.tsx 中包裹 ThemeProvider
   - 在 globals.css 中定义 .dark 和 .light 的 CSS 变量

4. 目录结构：
   src/
   ├── app/          # Next.js App Router
   │   ├── layout.tsx
   │   ├── page.tsx
   │   └── chat/[sessionId]/page.tsx
   ├── components/
   │   ├── layout/   # AppLayout, Sidebar, ChatPanel, DetailPanel
   │   ├── chat/     # ChatInput, MessageList, UserMessage, AIAnswer
   │   ├── detail/   # ProgressTracker, SourceDetail, DataFreshness
   │   └── ui/       # shadcn/ui 组件
   ├── hooks/        # useSSE, useSession, useTheme
   ├── lib/          # api client, types, utils
   └── types/        # API 类型定义
```

### 阶段 2：API 类型与客户端

```
根据以下 OpenAPI 定义生成完整的 TypeScript 类型和 API 客户端。

核心类型包括：
- QueryRecord: { query_id, session_id, query_text, answer, sources, classification, degradation, sql_executed, latency_ms, user_feedback, created_at }
- Source: { source_type: 'doc'|'code'|'sql', content, metadata, relevance_score, retrieval_method }
- DegradationInfo: { level: 0-4, trigger_reason, affected_workers, user_notice, auto_recovery_eta }
- SessionRecord: { session_id, turns: QueryRecord[], created_at, updated_at }
- FeedbackSubmission: { query_id, rating: 'positive'|'negative', reason?, comment? }
- SSE 事件类型：
  classification: { sources, is_cross_source, entities, completeness, elapsed_ms }
  worker_start: { worker: 'doc'|'code'|'sql', timestamp }
  worker_progress: { worker, status, query_used, elapsed_ms }
  worker_result: { worker, result_count, top_sources, retrieval_method, elapsed_ms }
  synthesis: { chunk, citations, elapsed_ms }
  done: { query_id, latency_ms, degradation, suggested_followups }
  error: { code, message, retryable, degradation }
  confirmation_required: { sql, tables_affected, risk_level, risk_reasons }

API 函数：
- submitQuery(query, sessionId?, maxSources?, timeoutMs?): Promise<QueryResponse>
- submitQueryStream(query, sessionId?, maxSources?): SSE EventSource
- getQuery(queryId): Promise<QueryRecord>
- listQueries(sessionId?, offset?, limit?): Promise<{items, pagination}>
- confirmSQL(queryId, action, modifiedSql?): Promise<QueryResponse>
- createSession(title?): Promise<{session_id, created_at}>
- getSession(sessionId): Promise<SessionRecord>
- deleteSession(sessionId): Promise<void>
- submitFeedback(queryId, rating, reason?, comment?): Promise<void>

请实现完整的类型定义文件 src/types/api.ts 和 API 客户端 src/lib/api.ts。
```

### 阶段 3：三栏布局

```
实现 AppLayout 三栏布局组件，macOS 原生风格。

要求：
1. 左栏 (210px) 和右栏 (250px) 使用毛玻璃效果：
   - background: rgba(250,250,250,0.7) / rgba(30,30,32,0.7)
   - backdrop-filter: blur(20px)
   - border-right / border-left: 1px solid var(--border)

2. 中栏 flex-1，白色/深灰背景，包含 ChatHeader + MessageList + ChatInput

3. 右栏根据查询状态切换两种模式：
   - 查询进行中：ProgressTracker
   - 查询完成后：SourceDetail 列表 + DataFreshness

4. 使用 React Context 管理全局状态：
   - currentSession
   - currentQuery (进行中的查询状态)
   - detailPanelMode: 'idle' | 'progress' | 'sources'

5. 响应式处理：
   - ≥1280px: 完整三栏
   - 1024-1279px: 右栏可折叠
   - <1024px: 左栏折叠为抽屉，右栏变为底部 sheet

请实现 src/components/layout/AppLayout.tsx 及子组件。
```

### 阶段 4：主对话区

```
实现 ChatPanel 核心组件。

ChatInput 要求：
1. SegmentedControl 数据源选择器：
   - macOS 风格：灰色底 + 选中项白色凸起带阴影
   - 四个选项：全部源 | 📄 文档 | 💻 代码 | 🗄️ SQL
   - 选中项平滑过渡动画 200ms ease-out

2. 文本输入框：
   - 全圆角 (border-radius: 18px)
   - 自动增高（最大 6 行）
   - ⌘Enter 发送

3. 发送按钮：
   - 圆形 (28px)，蓝色背景
   - 向上箭头 ↑ 图标
   - 输入框有内容时蓝色光晕 box-shadow pulse 动画
   - 无内容时灰色 disabled

MessageList 要求：
1. 使用 @tanstack/react-virtual 虚拟滚动
2. 新消息自动 scroll-smooth 到底部
3. 用户向上滚动查看历史时不抢滚动（检测 scrollTop）
4. 空状态：居中显示 Logo + 搜索框 + 3 个示例问题卡片

UserMessage 要求：
- 右对齐，蓝色背景 (#007AFF/#0A84FF)，白色文字
- 圆角 14px 14px 3px 14px（气泡风格）
- 从底部 slide-up 进入 (300ms ease-out)

AIAnswer 要求：
- 左对齐，卡片样式（var(--bg-secondary) + border）
- 圆角 10px
- 使用 react-markdown 渲染 Markdown 内容
- 自定义渲染器：
  - 代码块：SF Mono 字体 + rehype-highlight 语法高亮
  - 引用链接：可点击，hover 高亮右栏对应来源
  - 表格：GFM 表格样式
- 内联引用标记：蓝色背景标签，hover 触发右栏联动
- 追问建议 pills：回答完成后 stagger 滑入

MessageActions 要求：
- 👍（点赞）：点击 bounce 动画 + 蓝色填充
- 👎（点踩）：点击弹出原因选择器（不准确/过时/不完整/太慢）+ 可选评论输入框
- 📋（复制）：复制完整回答到剪贴板 + "已复制" toast

请实现 src/components/chat/ 下的所有组件。
```

### 阶段 5：SSE 流式查询与进度追踪

```
实现 SSE 流式查询 hook 和 ProgressTracker 组件。

useSSE Hook 要求：
1. 使用 EventSource API 连接 /query/stream
2. 解析 SSE 事件类型：classification, worker_start, worker_progress, worker_result, synthesis, done, error, confirmation_required
3. 返回状态：
   {
     phase: 'idle' | 'classifying' | 'retrieving' | 'synthesizing' | 'done' | 'error',
     supervisor: { status, elapsed_ms },
     workers: { doc: WorkerState, code: WorkerState, sql: WorkerState },
     synthesis: { chunks: string[], citations: Citation[] },
     degradation: DegradationInfo | null,
     error: SSEError | null,
     queryResult: QueryResponse | null,
   }
4. 支持 abort() 取消查询
5. SSE 断开自动重连（指数退避 1s/2s/4s/8s，最多 3 次）

ProgressTracker 组件要求：
1. 进度树结构，垂直排列各阶段状态
2. 已完成：绿色 ✓ + 耗时
3. 进行中：蓝色旋转动画 + 进度条 + 当前操作文案
4. 等待中：灰色半透明
5. 超时/错误：⚠️ 琥珀色 + 原因说明
6. 时间预算指示器：固定底部，蓝色背景 + 左边框，显示"已用 X.Xs / 预算 10s"
7. 中栏思考文案轮换（根据 worker_progress 事件更新）

请实现 src/hooks/useSSE.ts 和 src/components/detail/ProgressTracker.tsx。
```

### 阶段 6：SQL 确认与右栏来源详情

```
实现 SQL 确认卡片和来源详情面板。

SQLConfirmationCard 要求：
1. 琥珀色双线边框，嵌入对话流中
2. 头部：⚠️ 图标 + "高风险 SQL — 需要你确认" + 风险等级 badge
3. SQL 代码块：深色背景 + 语法高亮
4. 元数据行：📊 影响表名 + 预估行数
5. 三个操作按钮：
   - ✓ 确认执行（主按钮，蓝色）
   - ✎ 修改 SQL（次要按钮，进入可编辑模式）
   - ✕ 取消（文字按钮，红色，取消后跳过 SQL 直接合成）
6. 修改模式：代码块变为可编辑 textarea，底部显示"✓ 提交修改"按钮

SourceDetail 组件要求：
1. 每个来源一张卡片：
   - 白色卡片 + 微阴影 + 10px 圆角
   - 类型图标 + 标题（蓝色）+ 元数据行（灰色小字）
   - 内容片段预览（可展开/折叠）
   - 底部操作：打开原文 ↗ / 复制引用
2. 卡片与中栏引用标记联动：中栏 hover 引用 → 右栏对应卡片高亮 scale(1.02)
3. DataFreshness 固定在底部：
   - 📄 文档: 最新 · 延迟 32s
   - 💻 代码: 最新 · 延迟 8s
   - 🗄️ 数据库: 最新 · 延迟 120s
   - 非最新状态标黄，不可用标红

请实现 src/components/chat/SQLConfirmationCard.tsx 和 src/components/detail/SourceDetail.tsx。
```

### 阶段 7：会话管理

```
实现左栏会话管理功能。

Sidebar 要求：
1. 毛玻璃背景 backdrop-blur
2. 顶部：SPMA Logo (蓝色方块 + S) + 新建按钮 (+)
3. 搜索框：小型圆角输入框，实时过滤会话列表
4. 会话列表：
   - 选中会话：蓝色淡底 + 蓝色文字
   - 每条显示：标题（首轮查询自动生成）+ 轮次数 + 相对时间
   - hover 显示删除按钮 (⋯)
   - 点击切换会话，消息列表加载历史
5. 底部 SystemStatusBar：
   - 降级级别指示器：绿色圆点 + "L0 全功能"
   - 数据源健康摘要：📄 最新 · 💻 最新 · 🗄️ 最新

SessionList 要求：
1. 使用虚拟滚动
2. 从 GET /sessions 获取列表
3. 搜索过滤前端实现
4. 支持无限滚动分页（offset/limit）

请实现 src/components/layout/Sidebar.tsx 及子组件。
```

### 阶段 8：降级横幅与错误处理

```
实现系统降级和错误处理的 UI 组件。

DegradationBanner 要求：
1. 非阻塞内联横幅，嵌入对话流
2. 根据降级级别显示不同样式：
   - L1 (LLM降级)：琥珀色边框 + "已切换至本地 Qwen3 模型"
   - L2 (检索降级)：琥珀色边框 + "检索降级为关键词搜索"
   - L3 (缓存兜底)：琥珀色边框 + "返回缓存热点问答" + 缓存时间
   - L4 (静态兜底)：红色全屏提示
3. 从顶部 slide-down 进入 (300ms)
4. L0 恢复时 slide-up 消失
5. 包含预计恢复时间（如服务端提供）

ErrorToast 要求：
1. 网络错误：顶部 toast "连接中断，正在重连…"
2. 重连失败："网络不可用" + 手动重试按钮
3. 查询错误："查询失败" + 错误描述 + 重试按钮

请实现 src/components/chat/DegradationBanner.tsx 和 src/components/ui/ErrorToast.tsx。
```

### 阶段 9：动画与微交互

```
使用 framer-motion 实现所有微交互动画。

动画清单：
1. 消息进入：从底部 slide-up，spring 动画
2. 追问 suggestions: stagger 滑入（每个 50ms 延迟）
3. 发送按钮：有内容时蓝色 shadow pulse，无内容时灰色
4. 点赞 bounce：scale 1→1.2→1
5. 降级横幅：slide-down / slide-up
6. 来源卡片 hover：scale(1.02)
7. segmented control 切换：选中项位置平滑移动
8. 右栏模式切换：progress→sources crossfade (300ms)
9. 进度条填充：linear 动画
10. 加载指示器：音波动效（三条竖线交替 scaleY）

请创建 src/lib/animations.ts 统一管理 framer-motion variants。
```

### 阶段 10：键盘快捷键

```
实现全局键盘快捷键系统。

快捷键映射：
- ⌘K / Ctrl+K：新建会话
- ⌘Enter / Ctrl+Enter：发送消息
- ⌘/ / Ctrl+/：聚焦数据源选择器
- ⌘[ / Ctrl+[：上一个会话
- ⌘] / Ctrl+]：下一个会话
- Esc：取消当前操作（SQL确认/反馈弹窗）
- ⌘Shift+T / Ctrl+Shift+T：切换主题

使用 useKeyboard hook，自动根据操作系统显示 ⌘ 或 Ctrl。
在 ChatInput 下方显示快捷键提示（灰色小字）。

请实现 src/hooks/useKeyboard.ts。
```

---

## 十三、成功标准

- **首屏加载**：< 1.5s (Lighthouse Performance ≥ 90)
- **查询体验**：用户提交到首字节反馈 < 200ms（消息滑入 + 占位卡片出现）
- **流式渲染**：Synthesis chunk 到达延迟 < 50ms
- **主题切换**：瞬时（纯 CSS 变量，无 JS 开销）
- **键盘可用**：所有核心操作支持键盘快捷键
- **浏览器兼容**：Chrome 90+, Firefox 90+, Safari 15+, Edge 90+

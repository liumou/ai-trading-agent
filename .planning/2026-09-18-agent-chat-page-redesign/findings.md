# Findings: Agent 对话页重新设计

> 探索阶段发现，2026-09-18 记录。所有内容来自对现有代码的只读核查。

## 现状代码结构

### 对话页 `frontend/app/chat/page.tsx`（383 行单文件）
核心区块与组件：
1. **PageHeader**（复用全局组件，title + subtitle）
2. **左侧会话列表**（桌面 w-56 边框右分隔；移动端抽屉）
   - 每条：title 截断 + symbol（10px）+ hover 出现删除按钮
   - 选定态：`bg-primary/10 font-medium`
3. **顶部工具栏**（`flex-wrap` 平铺）
   - 移动端：Menu 图标（打开历史抽屉）
   - 3 个 Select：symbol（w-28）、timeframe（w-24，M15/H1/H4/D1）、mode（w-28，single/experts）
   - 2 个 preset 按钮：trading_plan / report（secondary, sm, 带 FileText 图标）
   - experts 模式提示一行小字（11px muted）
4. **消息流**（`flex-1 min-h-0 overflow-y-auto rounded-xl border p-4 space-y-3`）
   - `ChatMessageBubble`：user 右对齐 `bg-primary`，assistant 左对齐 `bg-muted`，`rounded-2xl px-4 py-2.5`，`max-w-[85%]`，纯文本 `whitespace-pre-wrap`
   - **无头像、无时间戳、无角色标识、无流式打字、无 markdown 渲染**
   - `RunPanel`（`components/chat/run-panel.tsx`）：运行状态徽章、turns/duration、取消/重试按钮、`run.response` 或 partial_response、**agent 列表**（agent_id + 状态 + model + report/summary）、**执行事件流**（`#seq` 前缀的 11px 时间线）、预算配置说明
   - submitting 时：一行 `Loader2 转圈 + "thinking" 文案`
5. **输入区**：`Input`（Enter 发送，防 IME 组合）+ 发送 Button（提交中变 Loader）
6. **免责声明**：10px muted 一行

### 支撑文件
- `components/chat/run-panel.tsx`（177 行）：状态/事件/agent 的展示，`RunPanel` 组件
- `components/chat/use-run-detail.ts`：run 轮询 hook（cursor 增量、指数退避、断线标记）
- `components/chat/run-state.ts`：isActiveRun / mergeEvents / eventCursor / createRequestId / localStorage 选择恢复
- `lib/api.ts` 386-507 行：AgentChat 全部 UI 类型
  - `AgentChatMessage{id, role, content, tool_calls, duration_s, created_at}`
  - `AgentChatMode` = single | experts
  - `AgentChatPreset` = trading_plan | report
  - `AgentChatRun`、`AgentChatRunAgent{agent_id, role, model, status, report, summary}`、`AgentChatEvent{sequence, event_type, payload, created_at, agent_id, execution_id}`
  - `AgentChatRunDetail{run, events, agents, next_cursor}`
  - 已有 `listSessionRuns(id)` 但页面未使用

### V2 规划背景（.planning/2026-09-17-agent/chat-v2-plan.md）
- **A+B 已获批并基本完成**：4 张新表（agent_chat_runs、agent_chat_agent_runs、agent_chat_events 等）、202+轮询、统一终态、超时治理
- **P4（页面与测试报告）** 尚未完成 —— 本任务就是 P4 的"页面"部分
- 既有多 Agent 展示数据（agents 报告、事件时间线）已在后端就绪，前端呈现力不足

## 设计系统（globals.css + 组件库）

### 视觉语言（Wise 风格）
- 主色 Wise Green `#9fe870`，primary-foreground `#163300`，hover `#cdffad`
- 卡片 `--card`（#fff / dark #1a1c18），圆角 `--radius 0.75rem`（radius-xl=1.05rem）
- 边框哲学：`ring-border`（0.5px 阴影描边）、`card-hover`（translateY -2px）、`glow-hover`
- 玻璃态 `.glass`（dark 下 bl12 + 卡片色半透明）
- 渐变：`.wise-gradient`（#9fe870→#8bd85e）、`.wise-gradient-text`
- 动画：`animate-fade-in`/`slide-in`/`scale-in`/`shimmer`/`badge-in`，排版 `heading-1/2/3`、`page-enter`
- 网格底纹 `trading-floor-grid`（黑色 + 绿色网格，留给深色 K 线区）

### 成熟组件（33 个 UI 组件）
布局：`PageHeader`、`PageInstructions`、`Breadcrumb`
反馈：`EmptyState`、`StatCard`、`Badge`、`Skeleton`、`Progress`、`Tooltip`、`ErrorBoundary`、`ConnectionStatus`
表单：`Input`、`Select`、`Button`、`Switch`、`Tabs`、`Slider`
其他：`Card`、`DataTable`、`Dialog`、`DropdownMenu`、`CommandPalette`、`ScrollArea`

### 其他页面范式
- **activity 页**：左侧时间线带主题色圆点（trade/signal/sentiment/...类别色）+ 右侧结构化卡；按日分组；`EmptyState`；`translateServerText` 处理服务端多语言文字
- **dashboard 页**：符号页签（SymbolTabs）+ 时间框架选择器 + StatCard 阵列 + 图表（AreaChart stroke #9fe870 渐变填充）+ Card 包边
- **insights/activity**：`PageHeader`（常配 breadcrumb/subtitle）+ `PageInstructions` 折叠提示条

## 探索发现的差距（初步）

1. **气泡无头像/时间戳/角色标识** — 对话页缺身份锚点，流向不清
2. **position funding/分块割裂** — users 气泡与 assistant 报告、RunPanel（技术状态面板）挤在一个 vertical stack，缺少"对话流 + 运行细节"的分层
3. **markdown 不渲染** — Agent 报告（带标题/列表/表格）原样 pre-wrap 输出，阅读性差
4. **空状态弱** — 仅一行居中文字，缺失"开场卡片/建议提问/快捷 preset 引导"
5. **影响点缺失** — 消息流顶部平铺 3 个 Select + 2 个按钮，工具栏信息层级弱
6. **thinking 指示弱** — 只有一行 loader 文字，缺 Agents 参与进度（which agent 在做什么）
7. **移动端抽屉仅有历史** — 工具/模式选择在窄屏有换行堆叠
8. **RunPanel 信息密度像调试面板** — 11px 事件流、agent 报告、预算平铺，属于"开发者视图"而非"交易者视图"
9. **会话列表缺时间戳/最近消息预览** — 需要第二个视图（见到列表时无法判断哪个会话新鲜）

## 待确认开放问题

- 是否引入 markdown 渲染（react-markdown？还是轻量 HTML 白名单）—— 安全考虑
- 事件时间线的定位：折叠的"过程审计"还是常驻？
- 消息流时间戳格式：Bangkok 时区（activity 页同款 TH_TZ）
- 移动端工具栏如何重排（SymbolTabs 复用？）
## 第二轮评审核查补充（2026-09-18）

- `EmptyState` props 核实：`{icon: LucideIcon, heading, description?, action?, className?}` — 综合方案引用一致 ✅
- 后端 `backend/tests/unit/test_agent_chat.py:139` 测试用 `mode="free"` 创建会话 — D3 修复方向（前端改传 "free"）与既有测试一致，不会破坏测试 ✅
- 前端无任何测试基础设施（vitest/jest 均无配置文件，仅 node_modules 依赖内测试）— D1 决策属实
- 会话列表接口 `list_sessions` 返回 updated_at 但前端未渲染 — "会话预览"无数据画饼确认

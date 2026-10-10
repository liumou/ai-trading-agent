# Task Plan: 历史记录页面分页处理

## Goal

历史记录页 `/history` 做分页：数据量增大后不再一次性渲染全部交易，改用分页展示。复用现有 `DataTable` 组件（内置排序+分页），保持 cumulativePnl 图表、summaryStats 汇总、CSV 导出依赖的全量数据语义不变。

## Next Step

全部完成：实现 + 验证 + 代码审查（COMMENT）均通过，无待办。可提交。

## Current Phase

全部完成

## Phases

### Phase 1: 需求与现状分析 ✅
- [x] 定位后端 `/api/history/trades`：已有 `limit`/`offset`/`total`，分页在内存中做（DB 全量 + MT5 全量合并后切片）
- [x] 定位前端 history/page.tsx：一次性拉 `limit: 200` 渲染 ScrollArea，无分页 UI
- [x] 确认 DataTable 组件（components/ui/data-table.tsx）自带排序+分页，可直接复用
- [x] 确认 history.json 翻译结构（zh/en）
- [x] 识别依赖全量 trades 的消费点：summaryStats、cumulativePnl 图表、CSV 导出、hasReason/hasSentiment 列探测
- **Status:** complete

### Phase 2: 前端分页改造（history/page.tsx）
- [x] 分析 DataTable 与现有手写 Table 的差异 → 决策：**手写轻量分页**（DataTable 是死代码、通用列模型会破坏现有结构）
- [x] 新增 page/pageSize state + pagedTrades 切片（pageSize=20）
- [x] 表格渲染 pagedTrades；条件列探测（hasReason/hasSentiment）基于全量 trades（列一致性不随页跳动）
- [x] 汇总条（summaryStats + 合计盈亏）保持基于全量 trades，分页状态解耦
- [x] 保留 EmptyState 空态 + ScrollArea 外层容器 + 横向 overflow
- [x] 筛选条件（days/symbolFilter）变化重置 page=0
- [x] 越界保护 safePage=min(page, totalPages-1)（归档/刷新后条数变少 page 越界 → 钳制末页；分页控件与切片统一用 safePage）
- [x] 分页控件：上一页/页码/下一页 + 「第 a-b 条/共 z 条」计数 + aria-label
- [x] 翻译新增 paginationShowing/paginationPage/paginationPrev/paginationNext（zh/en 对齐）
- [x] CSV 导出保持全量 trades（不导出当前页）
- **Status:** complete

### Phase 3: 验证
- [x] tsc --noEmit 通过（0 错误）
- [x] npm run build 通过（/history 正常编译）
- [x] 后端测试无回归（test_api_history.py 6 passed）
- [x] 分页边界逻辑验证（45 条→3 页、0 条→1 页、恰 20 条→不分页）
- [x] zh/en 翻译 key 对齐（EN-only/ZH-only 均空）
- [x] 代码审查（code-reviewer，COMMENT：无 CRITICAL/HIGH/MEDIUM，3 LOW + 2 INFO）
  - [x] LOW-1 归档后 setPage(0) 回到第一页（避免 page 陈旧导致后续跳页）
  - [x] LOW-2 分页条改 nav + aria-current="page" + aria-label（无障碍增强，新增 paginationLabel 翻译 key）
  - [x] LOW-3 import 折行（lucide 图标拆两行，行长 <120）
  - [x] 修复合标签 bug（div→nav 后闭合标签同步 nav）
  - [x] tsc --noEmit 通过、npm run build 通过、翻译 48 key 对齐
  - INFO-1 保留：前端分页基于 limit:200 全量拉，超 200 条需后续服务端分页（既有 API 限制，不阻塞）
  - INFO-2 保留：eslint 既有 warning（fetchData 缺 t 依赖），非本次引入
- **Status:** complete

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| **手写轻量分页**（page/pageSize state + slice），不复用 DataTable | 调查发现 DataTable（components/ui/data-table.tsx）是**死代码**——项目里没有任何页面使用它，未经验证。且历史页结构特殊（条件列 hasReason/hasSentiment、汇总条、AI badge、颜色、EmptyState），硬塞进 DataTable 通用列模型需重写整页且丢失现有结构 |
| 前端本地分页（全量拉取 + 客户端切片），不改成服务器端分页 | MT5 合并通道无分页语义（Bridge 只按 days 过滤全量拉），服务器端分页会破坏合并排序精确性；且 cumulativePnl 图表/CSV 依赖全量数据，前端分页可保持这些逻辑零改动 |
| 汇总条/图表/CSV 基于全量 trades，与分页状态解耦 | 避免翻页导致统计、图表、导出不完整 |
| 分页替代 ScrollArea 的纵向滚动需求 | 每页 ~20 条后无需 400px 高滚动区；保留外层容器 + 横向 overflow 防窄屏列溢出 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| （无） | | |

## Notes

- 分页 state（当前页）与全量数据 state（allTrades）分离，翻页只影响表格视图。
- 后端 `/trades` 已支持 limit/offset/total，本方案不改后端（除非用户要求服务器端分页）。
- DataTable 默认 pageSize=10，可传 pageSize 调整（如 20）。

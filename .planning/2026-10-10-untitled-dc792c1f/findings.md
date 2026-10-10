# Findings — 历史记录页面分页处理

## 需求原文
> 历史记录页面，需要做分页处理。

## 现状（后端 `/api/history/trades`）

文件：`backend/app/api/routes/history.py`

- **已有分页参数**：`limit: int = Query(100, ge=1, le=1000)`、`offset: int = Query(0, ge=0)`
- **返回已有 `total`**：`{"trades": paginated, "total": len(rows)}`
- **分页在内存中做**（非 SQL 级）：
  1. DB 通道：`select(Trade)` **全量**拉取（无 LIMIT/OFFSET）
  2. MT5 合并通道：`connector.get_history(days)` **全量**拉取桥历史（无分页语义，Bridge 只按 days 过滤）
  3. 合并去重、按 open_time desc 排序后 `rows[offset:offset+limit]`
- **问题**：
  - 数据量大时每页都重复拉 DB 全量 + MT5 全量，offset 越深开销越大（MT5 通道每次都是全量 HTTP 拉桥）
  - `total` 返回全量计数（OK），但计数后仍全量处理
  - DB 侧无 SQL 分页（LIMIT/OFFSET），随 trades 增长内存占用线性增长

## 现状（前端 `frontend/app/history/page.tsx`）

- 一次性 `getTradeHistory({ days, symbol: sym, limit: 200 })`，**未传 offset**
- 全部渲染到 `ScrollArea`（h-[400px]~500px），**无分页 UI**
- `trades` state 承载全量数据，被多处消费：
  - `summaryStats`（底部合计：total/wins/losses/rate）
  - cumulativePnl 图表（performance tab，遍历全部 trades 累加）
  - CSV 导出（基于当前 trades 全量）
  - `hasReason`/`hasSentiment` 列探测（遍历当前 trades）
- 前端 `getTradeHistory` 类型已支持 `offset?: number`

## 现状（测试 `backend/tests/integration/test_api_history.py`）

- `test_get_trades`：仅断言 status 200 + trades 键存在
- `test_get_trades_uses_bridge_new_fields`：字段契约
- **无分页语义测试**（offset/limit 组合、total 正确性、跨 DB+MT5 的分页一致性）

## 关键设计约束

1. **分页后 `trades` state 不再全量** —— cumulativePnl 图表、summaryStats、CSV 导出依赖全量
2. **MT5 合并通道无分页语义**：Bridge `get_history(days)` 只按 days 过滤，无法只取一页。若要真正的服务器端分页，MT5 通道要么全量拉（DB 分页后合并再切，offset 越大越浪费），要么去掉 MT5 合并通道
3. **DB 通道可 SQL 级分页**（LIMIT/OFFSET），但需先 COUNT 求 total
4. **合并后排序的分页一致性**：DB 行 + MT5 行合并排序后切片，天然需要全量在内存 —— 除非降级为「先 DB 分页，MT5 合并只追加到当前页之后」或完全放弃 MT5 合并的分页精确性

## 备选方案（评估）

### 方案 A：纯前端分页（最小改动）
- 后端不动（保留现有内存分页，但 limit 上限已 1000）
- 前端一次性拉 `limit: 1000`（上限）全量 → 本地 state 分页（每页 ~20-50 条）→ 渲染当前页 + 分页控件
- cumulativePnl/summaryStats/CSV 继续消费全量 `trades`，逻辑零改动
- **优点**：改动极小、图表/CSV/统计全部天然正确、无需动后端
- **缺点**：1000 条上限内有效；超过 1000 条（days=365 大量交易）会截断；MT5 通道仍是全量拉

### 方案 B：服务器端分页（DB SQL 分页 + MT5 合并特殊处理）
- DB 通道加 `count()` + `LIMIT/OFFSET`
- MT5 通道：全量拉（无法避免），与 DB 行合并后排序切片 —— offset 深处仍全量拉桥，仅省 DB 内存
- 前端用 `page`/`pageSize` 翻页，每页请求 offset=(page-1)*size
- cumulativePnl/CSV 需要单独的全量端点或保留全量拉取（二次请求）
- **优点**：DB 侧省内存，未来可扩展
- **缺点**：MT5 合并通道无法真正分页，offset 深仍全量拉桥；图表/CSV 需要额外全量请求或缓存策略，改动大

### 方案 C：混合（推荐基础）—— 前端分页 + 全量数据单独拉取
- 前端：`trades`（当前页）+ `allTrades`（全量，limit 1000）分离
  - 表格渲染当前页，分页控件翻页
  - summaryStats/cumulativePnl/CSV 用 `allTrades`
- 后端：可选地加 `count()` 优化 total（对 DB-only 场景）；MT5 合并通道保持现状
- **优点**：UI 分页体验 + 统计/图表/CSV 不依赖翻页状态；改动集中在前端
- **缺点**：仍是全量拉（1000 上限），但满足当前规模（GOLD/OIL/BTC/USDJPY 日交易量级）

> 注：当前交易品种只有 4 个（GOLD/OIL/BTC/USDJPY），days=90 上限下即使每天几十笔也就几千条。1000 上限可能不够。需确认是否需要突破 1000（如拉全量统计用独立端点）。

## MT5 合并通道是否保留分页精确性？

- 若追求「跨 DB+MT5 合并排序后精确分页」，必须全量拉 + 内存排序切片（现状），服务器端分页无从谈起
- 若接受「DB 分页 + MT5 行追加」，会破坏合并排序的唯一性（MT5 行插在页中间）
- **结论**：MT5 合并通道的存在决定分页只能是「全量拉 + 前端分页」或「后端内存分页」，SQL 级分页对合并场景意义有限

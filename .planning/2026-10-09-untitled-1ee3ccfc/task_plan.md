# Task Plan: 历史风控审查结果查询功能（前端 + 后端）

> 2026-10-09 由用户提出：「风控审查结果有没有入库？是否需要入库？我想要知道历史风控审查结果。请设计这个功能，包括前端和后端。」方案已获用户批准后进入实施。

## Goal
在不新增表/迁移的前提下，基于已入库的 `order_audits.review` JSON 数据，提供可过滤/分页的历史风控审查结果查询：后端增强 `GET /api/trading/reviews`（时间/状态/结论/品种过滤 + offset 分页 + total/stats），前端新增 `/manual-reviews` 侧边栏页面（过滤条 + 统计摘要 + 表格 + 详情 Dialog）。

## Next Step
全部 Phase 完成。等待用户浏览器端到端验收（后端 8002 已运行、鉴权已启用，HTTP/浏览器验证需用户登录；服务层对真实库已冒烟通过）。

## Current Phase
Phase 4 ✅（后端 pytest 62 通过 + 前端 build 通过 + 真实库服务层冒烟通过）

## Phases

### Phase 1: 调研与方案（已批准）
- [x] 确认审查结果已入库：`order_audits.review` JSON 列，`_apply_verdict` 写入（manual_order_gate.py:333-343），迁移 a9b8c7d6e5f4 刻意不建 order_reviews 表
- [x] 确认现状缺口：`GET /api/trading/reviews` 仅 limit 参数；前端 `getManualReviews` 从未被调用；i18n 键 reviewHistory/confidence 已预埋
- [x] 用户批准方案（独立页面 /manual-reviews，过滤=时间+状态+结论+品种）
- **Status:** complete

### Phase 2: 后端实现（无迁移、无新表）
- [x] `manual_order_gate.py` `list_reviews` 扩展：days/status/symbol/verdict（内存过滤）/offset/limit；SQL 过滤 source+account_login+created_at≥cutoff+status+symbol；返回 {reviews,total,stats}
- [x] 模块级 `_verdict_of(a)`：llm.verdict → systemone.converge（dict 含 verdict 键 / 字符串两种形状都兼容）→ status 映射；`_STATUS_VERDICT_MAP`
- [x] `_audit_to_dict` 新增便捷字段 verdict/provider/confidence/rule_flags（纯新增键，向后兼容）
- [x] 路由 `manual_trading.py` `GET /reviews` Query 参数透传 + verdict 非法值 422 + symbol 经 get_canonical_symbol 归一化；响应含 total/stats
- [x] 测试：新文件 test_manual_review_history.py（8 用例：载荷/推导/便捷字段/days/status/symbol/verdict/分页/账号隔离）；路由层 2 新用例（参数透传 + 422）
- **Status:** complete

### Phase 3: 前端实现
- [x] `lib/api.ts`：`ManualReview` 补 systemone/便捷字段 + `ManualReviewsResponse`/`ManualReviewsStats`；`getManualReviews(params)` 全参数
- [x] 新组件 `components/trading/ReviewBadges.tsx`（VerdictBadge/StatusBadge）+ `ReviewHistoryDialog.tsx`（订单上下文/结论/规则检查明细表）
- [x] 新页面 `app/manual-reviews/page.tsx`：过滤条（7/30/90/365、状态、结论、品种）+ 统计摘要 + 表格（时间/ID/品种/方向/手数/状态/结论/引擎/置信度/摘要/详情）+ 上一页/下一页 + EmptyState/Skeleton
- [x] 侧边栏 trading 组加 manual-reviews 项（ShieldCheck 图标）+ nav.json zh/en 键 + messages/{zh,en}/manual-reviews.json（40+ 键）
- [x] 交易页标题区加「审查历史」链接（复用预埋 trading.reviewHistory 键）
- **Status:** complete

### Phase 4: 验证
- [x] 后端：全量 unit 966 passed / 6 failed 全在 test_multi_agent.py（既有非回归，与本次改动文件零交集）；本次新增/相关 62 用例全绿
- [x] `cd frontend && npm run build` 通过（修复 ManualReview.fill_price 未声明类型）；/manual-reviews 路由已注册
- [x] 真实库服务层冒烟：30 天 174 条（approved 11 / caution 17 / rejected 146）；verdict 过滤/分页/status 兜底正确；typesafe_jev 行 provider/confidence/checks 填充正确；发现 converge 实为 dict（含 verdict 键），已补兼容分支
- [ ] 浏览器端到端验收（需用户登录；后端 8002 运行中）—— 留给用户
- **Status:** complete（浏览器验收待用户）

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 不新增表/迁移，增强现有端点与 UI | review 数据已入库且结构够用（迁移 a9b8c7d6e5f4 评审决定）；verdict 过滤走内存（单用户量小，先例 history.py:182） |
| 独立侧边栏页面 /manual-reviews | 符合现有 history/activity 页面惯例；交易页已 827 行不宜再塞 |
| 便捷字段加进 _audit_to_dict（verdict/provider/confidence/rule_flags） | 纯新增键向后兼容，前端轮询不受影响；避免前端重复解析 review JSON |
| 只查 source='manual' 通道 | 与现有端点语义一致；strategy/ai_agent 通道不在本期范围 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| init-session 中文名生成 slug 为 untitled-1ee3ccfc | 1 | 目录名无实际影响，task_plan 标题已手工写入；PLAN_ID=2026-10-09-untitled-1ee3ccfc |

## Reference
- 存储：backend/app/db/models.py:258-291（OrderAudit，review JSON 列）
- 写入：backend/app/services/manual_order_gate.py:333-343（_apply_verdict）、:786-801（_create_audit）
- 服务层：manual_order_gate.py:647-655（list_reviews）、:861-876（_audit_to_dict）
- 路由：backend/app/api/routes/manual_trading.py:92-95
- 前端 API：frontend/lib/api.ts:118-141（ManualReview / getManualReviews）
- 页面先例：frontend/app/history/page.tsx、frontend/app/price-alerts/page.tsx
- i18n：messages/{zh,en}/trading.json:60-61（reviewHistory/confidence 预埋键）、nav.json

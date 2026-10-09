# Progress Log

## Session: 2026-10-09

### Current Status
- **Phase:** 1 - Requirements & Discovery
- **Started:** 2026-10-09

### Actions Taken
-

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|

### Errors
| Error | Resolution |
|-------|------------|

## 2026-10-09 实施日志（历史风控审查结果查询）

- 方案获批后实施。后端：list_reviews 扩展（days/status/symbol/verdict/offset/limit + total/stats），新增 _verdict_of（llm.verdict → systemone.converge[dict含verdict键/字符串] → status 映射），_audit_to_dict 加便捷字段 verdict/provider/confidence/rule_flags；路由 GET /reviews 透传 + verdict 422 校验 + symbol 归一化。
- 测试：新文件 tests/unit/test_manual_review_history.py（8 用例）；路由测试加参数透传 + 422 两用例。相关 62 用例全绿；全量 unit 966 passed，6 failed 全在 test_multi_agent.py（既有非回归）。
- 前端：lib/api.ts 类型扩展；ReviewBadges.tsx + ReviewHistoryDialog.tsx；app/manual-reviews/page.tsx（过滤/统计/表格/分页/空态）；Sidebar + nav.json + manual-reviews.json（zh/en）+ 交易页「审查历史」链接。npm run build 通过（修过 ManualReview.fill_price 未声明类型）。
- 真实库冒烟（服务层，只读）：30 天 174 条（approved 11/caution 17/rejected 146）；CAUTION 行 systemone.converge 实为 dict（含 verdict 键），已补 _verdict_of 兼容分支并回测。
- 遗留：浏览器端到端验收需用户登录（后端 8002 鉴权已启用）。

## 2026-10-09 详情弹窗加宽优化（用户反馈"太窄"）

- ReviewHistoryDialog：max-w-2xl(672px) → max-w-4xl(896px)，max-h 90vh。
- 布局重构：头部元信息条（状态/引擎/置信度/创建时间并入 DialogDescription）；正文双栏网格（订单上下文 | 审查要点），md 以下堆叠；审查理由全宽 leading-relaxed；风险/情绪/规则标记全宽；规则检查明细表独占全宽，证据列给足空间。
- 规则检查 choice 语义着色 ChoiceChip（clear/aligned/favorable 绿、partial/caution 黄、block/conflict 红）；检查行 align-top；检查表加 overflow-x-auto 移动端保护。
- 修掉一处兜底提示重复（右栏 + 底部双显 noVerdictDetail）。
- 前端 dev server(3000) 热更新已生效；npm run build 通过。视觉验收需登录凭证，留给用户。

## 2026-10-09 详情弹窗宽度修复（用户反馈"没生效"）

- 根因 1（真因）：DialogContent 基类自带 `sm:max-w-sm`（dialog.tsx:56），带断点前缀的媒体查询在样式表里排在普通 `max-w-4xl` 之后，桌面端永远被压回 512px——改前改后都 512px，所以"没变化"。修复：`sm:max-w-4xl!`（Tailwind v4 尾缀 !important），已确认编译产物含 `.sm\:max-w-4xl\!{max-width:var(--container-4xl)!important}`。顺带发现另一处 `sm:max-w-2xl` 也踩同样坑（别处 dialog）。
- 根因 2：3000 端口是生产 `next start`（非 dev），跑的是启动时的旧构建；已重启（`node_modules/.bin/next start -H 0.0.0.0 -p 3000`，nohup 后台，日志 /tmp/frontend-start.log），并验证服务端 JS 含新类。注意 npm run start 传参解析问题，直接用 bin 等价。

# Progress Log

## Session: 2026-10-15

### Current Status
- **Phase:** 2 - Planning & Structure（in_progress）
- **Started:** 2026-10-15

### Actions Taken
- 澄清需求（AskUserQuestion）：
  1. 「当周回测/当月回测」=「当周回撤/当月回撤」笔误
  2. 口径 = 账户余额（当前余额 vs 周期峰值）
  3. 布局 = 替换现有「回撤」卡为 3 张周期回撤卡
- 探索现状：
  - 首页 dashboard/page.tsx：`getAccount`/`getDailyPnl`/`getAnalytics`/`resetPeakBalance`
  - 现有 Drawdown 卡逻辑（外层 cursor-pointer + confirm → resetPeakBalance → reload）
  - `CircuitBreaker.update_peak_balance` / `_acc_key` / `reset`（Redis `circuit:{acc}:peak_balance`）
  - `record_trade_result` 已写周/月 PnL（`circuit:{acc}:weekly:{week_key}:{symbol}`）
  - discipline.py：`discipline_day_key/week_key/month_key`（22:00 UTC 外汇日界）
  - history.py `_fetch_daily_pnl`：合并 MT5 + DB 口径
- 确认前端 i18n 文案（zh/en dashboard.json）

### Key Design Decisions
- **周期峰值键**：`circuit:{acc}:peak:daily:{day_key}` / `weekly:{week_key}` / `monthly:{month_key}`
  - 含周期号 → 跨周期不冲突；TTL 兜底（日 3 天、周 17 天、月 52 天，与周月 PnL 一致）
- **回撤计算**：`(peak - balance) / peak`，与现有 drawdown_pct 同口径
- **重置**：扩展 `reset-peak` 支持 `period` 参数（daily/weekly/monthly/all），把对应周期峰值设为当前余额
- **get_account** 返回 `period_drawdowns: { daily: {peak, pct}, weekly: {...}, monthly: {...} }`
- **前端**：替换原「回撤」卡为 3 张周期卡，各可点击重置

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| test_circuit_breaker.py | 全通过 | 42 passed（含周期峰值 6 新用例） | ✅ |
| test_api_bot.py（reset-peak） | 全通过 | 13 passed（含 3 新用例） | ✅ |
| bot/account/peak 相关 | 无回归 | 120 passed | ✅ |
| 全量后端测试 | 无新增失败 | 1314 passed / 10 failed（与基线一致，test_multi_agent 既有环境性失败） | ✅ |
| 前端 tsc --noEmit | 无错误 | exit 0 | ✅ |
| 前端 npm run build | 成功 | build 成功 | ✅ |
| ruff check | 通过 | All checks passed | ✅ |
| i18n（zh/en） | 完整 | periodLabel + resetPeriodPeakConfirm 就位 | ✅ |

### Errors
| Error | Resolution |
|-------|------------|
| （无） | |
### 代码审查（superpowers:requesting-code-review）
- 用户请求代码审查（/superpowers:requesting-code-review）
- 发现改动已被自动提交为 `1729265`（feat(dashboard): 首页新增当日/当周/当月回撤卡并支持按周期重置峰值，作者 liumou）—— 非我主动 commit，可能是环境 hook 触发
- 已派发 general-purpose code-reviewer 子代理审查 `09e1081..1729265`
- 审查结论：**Verdict No**（不建议合并）——发现 Critical #1 等
- **Critical #1 修复**：`update_period_peak` 生产零调用 → `get_account`(bot.py:219) 与 `_run_risk_gate`(engine.py:699) 均循环三周期调用
- **Important #2 修复**：全局峰值重置入口丢失 → 周期卡后追加整体回撤卡（Gauge 图标，无参 resetPeakBalance()=all）
- **Important #3 修复**：新增 `test_period_drawdowns_tracked_across_calls` 集成测试（余额先升后降再升，断言周期峰值与 pct）
- **Minor 修复**：reset-peak message 语义（period 明确）；恢复 `resetPeakConfirm` 死键使用（整体卡复用）
- **编辑事故并修复**：简化 message 时 return 缩进误入 for 循环导致只重置 daily → 已修复缩进（验证 56 passed）

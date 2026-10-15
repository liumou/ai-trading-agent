# Findings

## 2026-10-15 — 首页增加当日回撤/当周/当月数据 + 可重置

### 现状盘点（已完成探索）
- 首页 `frontend/app/dashboard/page.tsx`：
  - `getAccount()` → `{ balance, equity, ..., peak_balance, drawdown_pct }`（整体从峰值回撤）
  - `getDailyPnl()` → `{ daily_pnl, trade_count, wins, losses }`（当日已实现盈亏）
  - `getAnalytics(undefined, 30)` → 30 天性能分析（含 max_drawdown_pct）
  - 已有 `resetPeakBalance()` 重置整体峰值
  - Stats 卡片区：`grid-cols-3 lg:grid-cols-6`，现有 6 张卡（未实现PnL、当日PnL、持仓数、状态、回撤Drawdown）
- 后端：
  - `bot.py:get_account`（~150-213 行）计算 `peak_balance` / `drawdown_pct`：
    - 调 `CircuitBreaker.update_peak_balance(redis, balance, account_login=...)` 更新整体峰值
    - 用 Redis `circuit:peak_balance` 存储（`bot.py:reset_peak_balance` 351 行直接 `redis.set("circuit:peak_balance", str(balance))`）
  - `bot.py:_reset_peak_balance`（345-352 行）`POST /api/bot/reset-peak`
  - `analytics.py:_compute_performance`：基于已平仓 Trade 计算 max_drawdown_pct

### 关键歧义（需用户澄清）
1. 「当周回测」「当月回测」中的「回测」：
   - 解读 A：笔误，实为「回撤」（当周回撤 / 当月回撤 = 从周期峰值回落幅度），可重置周期峰值 → 类似现有 reset-peak 的扩展，实现轻量
   - 解读 B：真的是「回测」（backtest），即本周/本月期间跑回测引擎的结果展示 → 重功能，且「重置回测数据」语义奇怪
2. 若为解读 A，数据口径：基于账户余额（balance vs 周期峰值）还是基于已实现 PnL？

### 现有基础设施
- `CircuitBreaker.update_peak_balance` 已做整体峰值跟踪（按 account_login 隔离）
- 手动交易纪律门禁已有「当日亏损」计数（discipline_gate），按 22:00 UTC 外汇日界
- 时区约定：`parse_bridge_time_to_naive_utc` + 外汇日界 22:00 UTC（CLAUDE.md Known Issues）

### 最终实现（已完成）
**后端**：
- `CircuitBreaker` 新增 4 个静态方法：
  - `_period_peak_key(account_login, period)` → `circuit:{acc}:peak:{period}:{period_key}`
  - `update_period_peak(redis, balance, period, account_login)` → 跟踪周期峰值（TTL：日 3 天/周 17 天/月 52 天）
  - `get_period_drawdowns(redis, balance, account_login)` → `{period: {peak, pct}}`
  - `reset_period_peak(redis, period, account_login, new_peak)` → 重置周期峰值
- 周期号复用 discipline 的 `discipline_day_key/week_key/month_key`（22:00 UTC 外汇日界）
- `bot.py:get_account` 返回 `period_drawdowns`（paper 分支给全 0；MT5 分支从 CircuitBreaker 读）
- `bot.py:reset_peak_balance` 新增 `ResetPeakRequest{period}`（daily/weekly/monthly/all），缺省 all（向后兼容），全局峰值也按 `_acc_key` 账号隔离写

**前端**：
- `api.ts:resetPeakBalance(period?)` 支持可选 period
- `dashboard/page.tsx`：替换原「回撤」单卡为 3 张周期回撤卡（当日/当周/当月），各显示回撤 % + 峰值，点击弹确认后重置对应周期；网格改 `grid-cols-2 sm:grid-cols-4 xl:grid-cols-8`；loading skeleton 同步 8 张
- i18n：zh/en 新增 `periodLabel.daily/weekly/monthly` + `resetPeriodPeakConfirm`

**测试**：
- `test_circuit_breaker.py`：+6 周期峰值用例（新高/无数据/含峰值/重置/账号隔离/key 结构）
- `test_api_bot.py`：+3 reset-peak 用例（空 body=all/指定 period/非法 422），mock_engine 补 `account_login="0"`
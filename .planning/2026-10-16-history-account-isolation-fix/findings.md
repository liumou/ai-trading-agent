# Findings & Decisions

## Requirements
-

## Research Findings
-

## Technical Decisions
| Decision | Rationale |
|----------|-----------|

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
-

## 2026-10-16 — 初步探索发现（主会话直查）

### 泄漏点清单（初判）
1. **`/api/history/trades` DB 查询无 account_login 过滤**（history.py:58）
   `select(Trade).where(Trade.open_time >= cutoff, Trade.is_archived.is_(False))` — 查所有账号的 bot 交易。
2. **MT5 merge 分支**（history.py:138-194）：`engine.connector.get_history()` 只返回当前 Bridge 活跃账号的历史，
   `merge_account_login` 标的都是当前账号 —— 此分支本身不泄漏（deal 数据物理上只来自当前账号）。
3. **`_review_only_rows`**（history.py:214-305）：`owned = {"0"} | 全部登记账号`（history.py:261-265）)
   — 有意设计「跨账号回看复盘」（docstring 明确说明），是特性不是泄漏。但 pending 用户确认。
4. **`/api/history/daily-pnl`**（history.py:332-387）：MT5 live 只来自当前账号；但 **DB 分支无账号过滤**（357-365），
   跨账号 DB 记录全并入当日盈亏。
5. **`/api/history/performance`**（history.py:390-453）：**DB 分支无账号过滤**（406)，MT5 分支只当前账号。

### 「当前账号」权威判定
- `BotManager.current_account_login`（manager.py:53, set at :184 `set_current_account`）
- 引擎侧：`engine.account_login`（engine.py:222, :257 set_account_login）
- DB 侧：`symbol_config_service.get_current_account_login(db)`（symbol_config_service.py:52）— 取 `MT5Account.is_active && !is_deleted` 的 login
- manual_trading.py:34 `_account_login(request)` — 从 `request.app.state.manager.current_account_login` 取
- trade_reviews.py `_resolve_owned_account` — 注册账号归属校验（IDOR 防护）
- 引擎 `_save_trade`（engine.py:1873-1878）写 `trade.account_login = engine.account_login or "0"`

### 关键语义
- 旧数据 `account_login` 可能为 "0"（legacy bucket）或 None → 过滤时必须含 "0" 桶，否则老记录全消失
- MT5 merge 已天然只含当前账号数据；DB 却混入所以他账号 bot 记录 → 切换后「查询到其他账号的历史」

## 2026-10-16 — 补充确认（主会话第二轮）

### 「当前账号」权威来源与既有范式
- **trade_reviews.py:124 `_resolve_owned_account(request, client_account_login)`**
  - 归属策略：客户端传入账号**优先且生效**（需已登记 mt5_accounts 或 '0' 遗留桶），否则降级 manager 当前活跃账号
  - 这就是多账号历史可见性的既有范式：**不是只看当前活跃账号，而是「已登记账号集合」**
- **`_review_only_rows` 的 owned 集合 = {"0"} ∪ 全部登记账号**（history.py:261-265），有 docstring 明确背书（跨账号回看复盘是有意特性）
- `Trade.account_login` 有索引（models.py:120-122），`UniqueConstraint("account_login","ticket")`
- `_get_engine(symbol=None)` = 第一个引擎；`engine.account_login` 同步自 manager
- 历史测试 fixture 不设 account_login（默认 '0'）
- daily-pnl 有 15s Redis 缓存（键 `cache:daily_pnl:{symbol}`，不含账号维度 —— 单 owner 下当前账号即唯一账号，可接受）

### 设计要点（Phase 2 输入）
1. **`/api/history/trades` DB 分支过滤**：核心修复。语义选择：
   - 方案 A：只回当前活跃账号 + '0' 遗留桶
   - 方案 B：用户请求的 account_login 参数（经 `_resolve_owned_account` 校验）缺省当前账号
   - trade_reviews 既有范式支持 B（前端 history 行带 account_login，复盘徽章跨账号可见）
   - review_only 行的 owned 集合要与此保持一致
2. **daily-pnl / performance DB 分支**同样过滤当前活跃账号（+ '0' 桶？需要决策）
3. **向后兼容**：老数据 account_login='0'，若只过滤当前账号则老记录消失 → **必须含 '0' 桶**
4. MT5 merge / review_only 的行为依赖决策：切到 B 账号时 '0' 桶 trades 继续显示（且与账号无关的 MT5 deal 是否显示）
5. 测试计划：test_api_history.py 增加跨账号隔离用例（A 账号 trade 不出现在 B 账号查询里、'0' 桶始终可见、review_only 不回归）

## 2026-10-16 — explore 代理证据链（已交叉验证，与主会话发现一致）

### 完整泄漏点清单
| 端点 | 位置 | 账号过滤 |
|------|------|----------|
| `/api/history/trades` DB 分支 | history.py:58 | ❌ 无（**主泄漏点**） |
| 同上 raw SQL fallback | history.py:75-82 | ❌ 无 |
| `/api/history/daily-pnl` DB 分支 | history.py:357-364 | ❌ 无 |
| `/api/history/performance` DB 分支 | history.py:406 | ❌ 无 |
| `/api/analytics/performance` | analytics.py:38+, `_compute_performance`:69 | ❌ 无 |
| `/api/analytics` slippage | analytics.py:290+ | ❌ 无 |
| `/api/positions` | positions.py:17 | 无过滤但数据来自 bridge → 天然隔离 |
| `/api/history/trades` MT5 merge | history.py:138-194 | 天然隔离（bridge 只回当前账号）+ 打标 merge_account_login |
| `_review_only_rows` | history.py:214-305 | 有意跨账号（owned = '0' ∪ 全部登记账号） |
| `/api/trade-reviews/*` | trade_reviews.py | ✅ `_resolve_owned_account` 统一校验 |

### 唯一正确范式（既有）
`trade_reviews._resolve_owned_account`（trade_reviews.py:124-161）：
客户端账号优先（需登记在 mt5_accounts 或 '0' 桶）→ 否则降级 `manager.current_account_login`（不 fail-open）
注释明确：旧实现强制=当前活跃账号会丢掉切换前账号的历史复盘（生产实例 110351809 复盘在切回 336773771 后消失）——**收紧为「仅当前账号」会重蹈此坑**。

### 前端
- history/page.tsx:140 `getTradeHistory({days, symbol, limit:200})` — 不传账号
- api.ts:211-213 签名无 account 参数
- 复盘徽章用行内透传的 `t.account_login` 反查（page.tsx:187/229）

### 测试缺口
- test_api_history.py 无「多账号存在时断言隔离」的负向测试 → 泄漏场景未被覆盖
- test_review_account_scope.py 已覆盖 `_resolve_owned_account` 全矩阵 + owned 边界

### 待决策（影响实现）
1. owned 集合定义：'0' ∪ 登记账号（保留跨账号回看）vs 仅当前账号（严格隔离）
2. 修复范围：只 /api/history/* vs 含 analytics/performance、daily-pnl

## 2026-10-16 — 手动交易风控探索结论（explore 代理，已交叉验证）

### 两处真实漏洞
1. **`GET /api/trading/reviews/{review_id}` IDOR**（manual_trading.py:124-130 → manual_order_gate.py:695-699 → _load_audit:950-954）
   - 按 id 直查无账号过滤；`_audit_to_dict`（:1014-1037）输出 account_login/review(LLM verdict)/ticket/fill_price 等敏感字段
   - 枚举 id 可读所有账号的审查记录 —— **用户报告「风控查控数据没隔离」的根因**
   - 正确范式参照 trade_reviews.py `_resolve_owned_account`（注释明言「归属按 account_login 校验（IDOR）」）

2. **`POST /api/trading/orders/{id}/confirm` 跨账号执行**（manual_trading.py:87-91 → manual_order_gate.py:421-457）
   - 传了 account_login 但服务层未校验 `audit.account_login == 传入`
   - `audit_account = audit.account_login` 作为 preflight 风险参数，但执行走全局 connector（bridge 当前账号）→ 风控参数与执行账号错位

### 已确认隔离正确（无需改动）
- GET /reviews（list_reviews SQL 带 `OrderAudit.account_login ==`）
- GET /orders、DELETE /orders/{ticket}、PUT /orders/{ticket}、PUT /positions/{ticket}（bridge 天然隔离 + _verify_order_ticket）
- POST /positions/{ticket}/close（position_close 校验 ticket 归属当前账号）
- GET /discipline/status、GET /discipline/stats（Redis key / DB 查询均带 account_login）
- discipline:cfg:* 纪律配置 = 全局共享（有意设计）

### 修复方案（TDD）
1. `_load_audit(audit_id, account_login=None)`：加可选账号过滤；`get_review(review_id, account_login)` 传入
   → 路由 get_review 传 `_account_login(request)`；账号不匹配 → 404（不可枚举确认存在性）
2. `confirm_and_execute`：进入锁前校验 `str(audit.account_login) == str(account_login)`（归一化），不匹配 → 拒绝
3. 补测试：跨账号 get_review 404、跨账号 confirm 拒绝、本账号正常路径不回归

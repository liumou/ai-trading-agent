# Progress — MT5 账号实时切换功能

## Session Log

### 2026-09-18 — 调研 + 计划生成
- 初始化计划目录 `2026-09-18-mt5`（PLAN_ID=2026-09-18-mt5）。
- **Phase 1 调研完成**：
  - MT5 Bridge（`mt5_bridge/main.py`）是唯一账号持有者，凭据来自 env，启动时 `mt5.login()`，重连复用启动 env。
  - 后端 `MT5BridgeConnector` 是无状态单例 HTTP 客户端，单一 `mt5_bridge_url` + API key。
  - 引擎 `BotEngine` 共享 connector；`BotManager.reload_engines()` 热重载引擎但不换 connector。
  - `HealthMonitor` 依赖单一 connector 心跳。
  - 无账号模型/表/凭据存储；integration 页仅 URL + API key（存 Vault secrets）。
  - MT5 SDK 约束：`mt5.login()` 可同终端切换账号，不可并行多账号。
  - 完整发现见 `findings.md`。
- **需求澄清（AskUserQuestion）**：单账号热切换 / 同券商多账号 / 自动暂停+恢复 / 全栈交付。
- **Phase 2 计划生成**：产出完整 7 阶段实施计划（Bridge 端点 → 后端 CRUD+切换 → connector 适配 → 前端 UI → 测试文档）。
- **状态**：计划已写入 `task_plan.md`，**等待用户审批后执行**。

## Test Results
- （未开始实施，暂无测试）

## 评审进行中（2026-09-18）

用户要求对 task_plan.md 做评审并迭代。已派 critic 子代理独立评审（后台运行中）。同时自行验证了关键技术疑点：

- **已验证（支撑计划）**：`mt5.login()` 可同终端切换，无需 shutdown（官方文档）。MCP broker 独立 connector 同 URL，切换在 Bridge 内部做，无需改。
- **已发现计划缺陷（待合并迭代）**：
  1. Phase 4 与设计决策矛盾：`mt5_accounts` 新表 vs 复用 `secrets` 表，密码存储方案未统一。
  2. `reload_engines()` 不重建已有引擎、不清 `_known_tickets` → 切换后需强制重建引擎 + 清状态。
  3. `first_engine` 强引用依赖（backtest/data/ml/HistoricalDataCollector 绑定旧 market_data）→ 需处理。
  4. 健康监控切换期间可能误判暂停/恢复 → 需协调。
  5. 引擎 paper/风控/ATR 状态切换后需清理。
  6. 切换期间应抑制健康监控；切换后重跑规格校验。

### critic 评审完成（2026-09-18）

独立 critic（oh-my-claudecode:critic）完成深度评审，**2 CRITICAL + 5 HIGH + 7 MEDIUM + 4 LOW**，并升级对抗模式复核。核心发现：

- **C1**：`mt5.login()` 同终端切换假设未被验证（现网 ensure_connected 从未走已连接下 login 分支；锁 MetaTrader5==5.0.4424 版本未考证；mock 覆盖不到唯一需真实验证的行为）。→ 新增 **Phase 0 spike（阻断项）**。
- **C2**：`/health` 用 terminal_info() 判定健康而非账号登录 → 切换失败后无账号状态会被健康监控误判健康并自动恢复交易（安全反转）。→ `/health` 加 logged_in + 切换窗口抑制自动恢复。
- **H1**：`manager.stop()`+reload 后引擎停在 STOPPED，计划没写 `manager.start()` → 切换成功但永不交易（静默失败）。→ 显式 start + active_count 断言。
- **H2**：切换锁与 `_engines_lock` 是两把锁；MCP `broker.place_order` 无引擎状态/切换门禁 → 切换瞬间下单落在错误账号。→ 锁合并 + Redis `switching:in_progress` 门禁 + 在途订单 drain。
- **H3**：circuit_breaker 的 key 仅按 symbol，`peak_balance` 全局单键无 TTL → 切换制造跨账号日损污染/不可恢复停盘。→ circuit key 加账号维度。
- **H4**：`Trade.ticket` 全局唯一，跨账号 ticket 可重复 → 唯一冲突 + orphan 误 adopt。→ `(account_login, ticket)` 复合唯一 + 对账按账号限定。
- **H5**：MT5 密码写 `secrets` 表会被 runner `_get_decrypted_secrets()` 注入到所有 sandbox runner 进程（明文）。→ 独立 `mt5_accounts` 表，不进 runner 注入集。
- **M1**：凭据存储自相矛盾（独立表 vs secrets 表）→ 定稿独立表。M2：switch 端点鉴权留打开项 → 强制 verify_api_key。M3：Bridge 无测试基建 → 新增。M5：positions 缓存污染。M6：顺序错误。M7：新账号持仓未定 → 不自动 adopt。

### 计划迭代至 v2

- `task_plan.md` 重写为 v2：新增 Phase 0（SDK spike 阻断项）、修复 C1/C2/H1/H2/H3/H4/H5、M1-M7、风险表扩充、设计决策 8 条。
- **等待用户审批 v2**。批准后先执行 Phase 0（SDK spike），结论通过才进入 Phase 3。

### v2 已获用户审批，Phase 0 开始（2026-09-18）

- 用户批准 v2 计划。
- Phase 0 为阻断项（SDK spike），需真实 Windows VPS + 真实账号运行——**用户侧执行**。
- 已交付：
  - `mt5_bridge/spike_switch_account.py` — 验证脚本（initialize+login A → 已连接下直接 login B → 失败回滚 → 挂起检测，不下订单）。
  - `mt5_bridge/PHASE0_SPIKE_README.md` — VPS 执行说明（含判定标准）。
- 语法验证通过（ast.parse）。本机 macOS 无法 import MetaTrader5，仅 VPS 可跑。
- **待用户**：在 VPS 跑脚本，把 conclusion + 关键 checks 贴回，确认是否弹窗。据此判定进入 Phase 3 或重新选型。

### Phase 0 通过 + Phase 3 完成（2026-09-19）

- **用户反馈**：mt5.login() 同终端切换账号**验证成功**，已能切到新账号。Phase 0 spike 通过 → 进入 Phase 3。
- **Phase 3（Bridge 端点）完成**：
  - `mt5_bridge/main.py`：
    - `ensure_connected()` 收紧：终端存活但 account_info() 为 None 视为未连接（C2）。
    - 新增 `switch_account()` 辅助函数（同终端 mt5.login 切换）。
    - `/health` 三分支 ok/degraded/disconnected + `logged_in` 字段（C2 修复）。
    - `/account` 返回 login + server。
    - 新增 `/account/switch` POST（强制 verify_api_key；成功返回快照+previous；失败返回 error+previous+current，Bridge 端不做自动回滚——由后端决定）。
  - 测试基建（M3）：`tests/conftest.py`（sys.modules 注入 mock MetaTrader5，属性链统一）+ `tests/test_switch_account.py` **7 个测试全部通过**。
  - 语法 + pytest 验证通过。
- **Phase 3 遗留**：CI 加 bridge 测试 job（并入 Phase 7）。

### Phase 4 完成（2026-09-19）

- **模型**：`Trade` 复合唯一 `(account_login, ticket)` + account_login 列（H4）；`BotEvent` + account_login；新增 `MT5Account` 模型（密码 Vault AES-256-GCM 加密，独立于 secrets 表——H5）。
- **迁移**：`z0a1b2c3d4e5_add_account_isolation`（trades/bot_events 加列 + mt5_accounts 表，存量回填 '0'）。
- **路由**：`accounts.py`（list/create/update/delete/current，make_authed_router 鉴权，不泄露密码）；main.py 注册。
- **测试**：`tests/unit/test_accounts.py` 7/7 通过；现有 27 个相关测试回归通过。

### Phase 5 代码完成（2026-09-19，全量测试确认中）

- **`account_switch.py`**（AccountSwitchService）：H2 锁+门禁+drain；H1 显式 start + active_count 断言；失败不恢复引擎 + 回滚 + 审计；规格刷新 + volume 回填。
- **H3**：circuit_breaker 全部 key 加账号维度（向后兼容）。
- **H4 收尾**：engine.account_login + _save_trade 注入 + _reconcile_once 账号限定（M7）。
- **H2 门禁**：broker.place_order 检查 switching:in_progress。
- **connector.switch_account()** 公开方法。
- **accounts /switch 端点** + main.py 挂 service。
- **测试**：`test_account_switch.py` 4/4 通过；circuit/risk/accounts 59 通过。

### Phase 6 + 7 完成（2026-09-19）— 全功能交付

- **Phase 6（前端）**：`frontend/app/accounts/page.tsx`（列表/新增/切换/删除 + 活跃徽章 + 确认弹窗）；翻译 `accounts.json`（zh/en）+ `nav.json` 加 accounts；Sidebar 加 `/accounts`（Wallet icon）。tsc ✅ build ✅（/accounts 路由注册）。
- **Phase 7（测试+文档）**：
  - 相关测试全集 81 通过；Bridge 测试 7/7；accounts 7/7；switch 4/4。
  - CLAUDE.md 更新（account_switch.py、accounts.py、accounts 页、circuit 账号隔离）。
  - CI 加 bridge job（Linux mock MT5，MetaTrader5 仅 Windows 故不装）。
  - 全量 pytest：851 passed / 9 failed（multi_agent 模型 ID 断言依赖会话 provider=deepseek-v4-flash、backtest 数据断言、ml 模块隔离——**均与本次改动无关**，既有环境问题）。
  - 无 TODO/FIXME/skip 残留。

## 最终交付总结

**MT5 账号实时切换（单账号热切换）全栈实现**：
- **Phase 0**：SDK spike 验证通过（mt5.login 同终端切换可行）。
- **Phase 3**：Bridge `/account/switch` + `/health` logged_in 语义（C2 安全修复）+ 测试基建（7 测试）。
- **Phase 4**：数据模型账号隔离（Trade 复合唯一 H4、BotEvent account_login、MT5Account 表 H5）+ CRUD 路由（7 测试）。
- **Phase 5**：AccountSwitchService（H2 锁+门禁+drain、H1 显式恢复+断言、H3 风控账号隔离、H4 对账限定、M5 缓存清理、LOW2 审计）+ connector.switch_account + broker 门禁（4 测试）。
- **Phase 6**：前端账号管理页（tsc+build 通过）。
- **Phase 7**：CLAUDE.md + CI bridge job + 全量测试。

**待办（用户侧）**：VPS 部署 Bridge 新代码 → Railway 后端迁移 → 前端联调 → git 提交。

## code-reviewer 评审问题修复（2026-09-19）

请求 `/superpowers:requesting-code-review` 后，code-reviewer 复评发现 **2 Critical + 5 Important**，均已修复并验证：

### Critical（高置信阻断，均已实证确认）
- **C1**：`account_switch.py:155-156` active_count 断言对真实 `get_status()` 结构（`{symbols, active_count, total_count, ...}`）调 `.get("state")` → 每次成功切换都抛 `AttributeError`。**修复**：改为 `status.get("active_count", 0)` 读取聚合计数；整个 `switch()` 主流程包进 `try/finally`，`finally` 统一清门禁（成功/失败/异常出口都复位）。
- **C2**：Alembic 迁移双 head（`z0a1b2c3d4e5` 误挂 `y5z6a7b8c9d0` 分叉点）。**修复**：`down_revision` 改为链末 `b8c9d0e1f2a3`。验证：`alembic heads` 单 head，`alembic history` 全链线性。

### Important（均已修复）
- **I1**：`datetime.now(timezone.utc)` 写 naive 列 → 改 `datetime.utcnow()`。
- **I2**：H3 风控账号隔离未接线。**修复**：engine 加 `set_account_login()`（更新 account_login + 重建带账号维度的 circuit_breaker）；manager `set_current_account()` 改调它；engine.py 的 `update_peak_balance`/`is_global_triggered`/`is_drawdown_halted`、bot.py 的 `update_peak_balance` 均传 `account_login=self.account_login`。
- **I3**：Bridge 断线重连翻回 env 账号。**修复**：Bridge 维护模块级 `_active_*`（最近活跃账号，初始 env）；`switch_account()` 成功后更新；`ensure_connected()` 重连用最近活跃账号。补回归测试。
- **I4**：测试 mock 结构失真（`get_status()` 假结构导致 C1 漏网）。**修复**：`test_account_switch.py` mock 改为真实聚合结构。
- **I5**：MCP 门禁只拦 `place_order`。**修复**：抽 `_switching_in_progress()` helper，`modify_position`/`close_position` 也检查切换门禁。补 `TestSwitchingWindowGate` 4 个测试。

### 额外修复
- **Bridge 测试基建缺陷**：`BRIDGE_API_KEY` 未配置时 `verify_api_key` 直接 503，导致 `/account/switch` 用例在无 env 环境下全挂。**修复**：`conftest.py` 在 `main` import 前 `os.environ.setdefault("BRIDGE_API_KEY", "test-key")`。

### 验证结果
- Bridge 测试：**8/8 通过**（含新增 I3 回归用例）。
- 后端：account_switch 4/4、accounts/circuit/risk/strategy_switch 83 通过、mcp_broker_guard 9/9（含新增 I5 门禁用例）、guardrails 34 通过。**相关测试全集 126 passed**。
- `alembic heads` 单 head 确认。
- 改动文件 py_compile 通过。
- 后端全量单测回归：733 passed / 8 failed，失败均为**既有环境问题**（multi_agent 模型 ID 断言依赖会话 provider、backtest 数据断言、SDK 模拟差异），与本次改动无关。

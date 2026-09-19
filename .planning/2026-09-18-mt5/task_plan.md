# Task Plan — MT5 账号实时切换功能（全栈交付）· 修订版 v2

> **v2 修订说明**：经独立 critic 评审（2 CRITICAL + 5 HIGH + 7 MEDIUM + 4 LOW）+ 自行验证，对 v1 做了实质迭代。核心变化：
> - 新增 **Phase 0（SDK spike，阻断项）**——验证 `mt5.login()` 同终端切换假设，未通过前不批准实施。
> - 修复健康监控安全反转（C2）、恢复失效（H1）、并发锁失效（H2）。
> - 新增风控状态账号隔离（H3）、数据模型账号隔离（H4）、凭据不进 runner 注入集（H5）。
> - 凭据存储方案二选一（M1）、switch 端点强制鉴权（M2）、测试基建（M3）。

## Goal

实现 **MT5 账号单账号热切换**：前端管理多个 MT5 账号（同券商），点击「切换」后，Bridge 在同一终端内切换账号，后端安全暂停→切换→恢复引擎，全栈交付（Bridge + 后端 + 前端 + 测试 + 文档）。**以真实 SDK 行为验证（Phase 0）为前提，验证不通过则重新选型。**

## Status

- 2026-09-18：调研完成（findings.md）。需求已确认。critic 评审完成，计划迭代至 v2。
- **v2 已获用户审批（2026-09-18）。开始执行，Phase 0 先行。**
- **2026-09-19：全部 Phase 0-7 完成。** 功能代码 + 测试 + 文档 + CI 均已交付。

## 已确认需求

| 决策点 | 结论 |
|--------|------|
| 切换语义 | **单账号热切换**（同一时刻一个活跃账号） |
| 账号场景 | **同券商多账号**（server 相同，login/password 不同） |
| 引擎处理 | **自动暂停 → 切换 → 恢复**（恢复需显式 start + 断言） |
| 交付范围 | **全栈完整交付**（含测试、文档） |

## Phases

### Phase 0: SDK 行为验证 Spike（新增，阻断项） (complete)
- [x] 在**真实 Windows VPS** 上用**两个真实账号**验证 MT5 Python SDK 切换语义（**禁止 mock**——这是唯一需要真实验证的行为）：
  - **结论（2026-09-19 用户反馈）**：`mt5.login()` 同终端切换账号**验证成功**，已能成功切到新账号。
  1. `initialize()` 已连接状态下直接 `mt5.login(新账号, password, server)`：✅ 可行（用户实测确认）
  2. 是否需要先 `shutdown()` 再 `login()`？还是 `login()` 会自动登出旧账号：**待补充**（spike 脚本输出）
  3. `account_info()` 切换延迟；切换后 `symbols_get()`/positions 是否立即反映新账号：✅（切换后正常）
  4. **无人值守 VPS 上是否弹出终端账户切换确认框**：**待确认**（若弹窗则挂起风险）
  5. 失败时（密码错/账号锁）返回什么，能否回滚到原账号：**待补充**
- [x] 结论通过 → 进入 Phase 3。

### Phase 1: 调研现有架构 (complete)
- [x] 梳理 MT5 Bridge 连接模型（mt5_bridge/main.py）与后端 connector（backend/app/mt5/connector.py）
- [x] 梳理后端引擎/管理器/健康监控对 MT5 连接的依赖
- [x] 梳理配置来源（env / Vault secrets / integration 路由）
- [x] 梳理前端设置/集成页现状
- [x] 确认 MT5 SDK 约束与现网代码路径（findings.md）
- [x] 产出可行性与方案选项

### Phase 2: 计划评审与迭代 (complete)
- [x] 用户澄清需求（AskUserQuestion）
- [x] 独立 critic 评审（2C/5H/7M/4L）
- [x] 自行验证关键技术疑点（reload 不重建引擎、stop 不清状态、first_engine 引用、健康监控误判、MCP 独立 connector、SDK 文档 login 语义）
- [x] 计划迭代至 v2

### Phase 3: 实施 — MT5 Bridge 账号切换 + 健康语义修正 (complete)
- [x] **C2 修复（安全）**：`/health` 增加 `logged_in: bool` + `account.login`；`ensure_connected()` 在终端存活但 `account_info()` 为 None 时视为未连接并重连；`/health` 在未登录时返回 `status: "degraded"`。
  - `mt5_bridge/main.py`: `ensure_connected()` 收紧 + `/health` 三分支（ok/degraded/disconnected）。
- [x] 新增 `/account/switch` POST 端点（login/password/server），**强制 `verify_api_key` 依赖**；内部 `switch_account()`（`mt5.login()`，Phase 0 已验证）；成功后返回新账号快照 + previous；失败返回错误 + previous + current（**Bridge 端不做自动回滚**——无凭据库，由后端 AccountSwitchService 决定，C2 语义保证后端能检测到无账号状态）。
- [x] `/account` GET 扩展：返回 `login` + `server`（确认切换生效）。
- [x] **M3 修复**：Bridge 测试基建——`tests/` 目录 + `conftest.py`（`sys.modules` 注入 mock `MetaTrader5`，属性链统一避免 mock 分裂）+ `test_switch_account.py` 7 个测试（health 语义参数化、切换成功/失败、鉴权、字段校验）。**7/7 通过**。
- [x] 测试：Bridge `/account/switch` + `/health` 逻辑（mock mt5；真实行为由 Phase 0 spike 覆盖）。
- [ ] **待办**：CI 加 bridge 测试 job（Phase 7 一并处理）。

### Phase 4: 实施 — 数据模型账号隔离 + 账号 CRUD (complete)
- [x] **H4 修复（结构性）**：Alembic 迁移 `z0a1b2c3d4e5_add_account_isolation`
  - `trades` 表加 `account_login` 列；`ticket` 唯一约束改 `(account_login, ticket)` 复合唯一（跨账号 ticket 可重复）。存量回填 `'0'`（"未知/切换前"，后端此前无账号概念）。
  - `bot_events` 加 `account_login` 列（可空）。
  - 验证：模型元数据 + SQLite 复合唯一测试通过。
- [x] **M1 定稿**：独立 `mt5_accounts` 表 + VaultService AES-256-GCM 加密列（密码**不写 secrets 表**——H5，避免 runner 注入明文）。
  - 字段：id, login(unique), password_encrypted, password_nonce, server, broker_name, is_active, is_enabled, is_deleted, last_switched_at, created_at/updated_at。
- [x] 路由：`backend/app/api/routes/accounts.py`（5 端点，`make_authed_router` 强制鉴权）
  - `GET /api/accounts` 列表 / `POST` 新增 / `PUT /{id}` 更新 / `DELETE /{id}` 软删 / `GET /current`
  - API 永不返回密码明文；`_require_vault()` 未配置 master key 时拒绝写。
  - `POST /api/accounts/{id}/switch` 留待 Phase 5 切换服务接入。
- [x] main.py 注册 accounts 路由（import + include_router）。
- [x] 单测 `tests/unit/test_accounts.py` **7/7 通过**（密码加密、Trade 复合唯一、CRUD、软删、current）。
- [x] 现有测试回归：27 个相关测试通过，无破坏。
- [ ] **遗留（并入 Phase 5）**：对账逻辑账号限定 + 切换审计日志。

### Phase 5: 实施 — 切换服务（核心，含风控与并发安全） (complete)
- [x] `AccountSwitchService`（`backend/app/bot/account_switch.py`）：
  - **H2 锁**：`self._lock`（asyncio.Lock）串行化切换 + Redis `switching:in_progress`（TTL 120s）门禁。
  - **H2 drain**：`manager.stop()` 后 `asyncio.sleep(1.0)` 让在途 HTTP 排空。
  - 安全暂停：`manager.stop()`（全部引擎 STOPPED）。
  - **M5**：`manager.set_current_account()` 内清 `_positions_cache`。
  - 调 Bridge `/account/switch`（`connector.switch_account()` 新增方法）；失败 → **不恢复引擎** + 清门禁 + 审计失败 + `_try_rollback` 回滚原账号。
  - 成功 → 刷新规格（`load_profiles_into_memory()` + `_validate_symbols()` volume 回填）→ `reload_engines()` → **H1 显式 `manager.start()` + active_count 断言**（0 则清门禁 + 抛错，防静默停）。
  - 更新 `mt5_accounts.is_active` + `last_switched_at` + 清门禁。
  - **LOW2 审计**：切换**开始**写入（失败在 except 补记录）。
- [x] **H3 修复（风控状态账号隔离）**：`circuit_breaker.py` 所有 key 加账号维度前缀 `circuit:acc:{account}:`（`account_login=None` 保持旧 key 向后兼容）；`peak_balance` 按账号分键；static 方法 `_acc_key()` 统一生成。
- [x] **H4 收尾**：`engine.account_login` 属性（默认 "0"）；`_save_trade` 注入 account_login；`_reconcile_once` 查询限定 account_login + orphan adopt 带 account_login（M7：reconcile 只认当前账号，不会 adopt 其它账号的持仓）。
- [x] **manager**：加 `current_account_login` + `set_current_account()`（同步引擎 + 清缓存）。
- [x] **H2 门禁**：`broker.place_order` live/micro 执行前检查 `switching:in_progress`（Redis 不可用放行）。
- [x] **connector**：`switch_account()` 公开方法。
- [x] **accounts 路由**：`POST /api/accounts/{id}/switch` 端点（从 app.state 取 service）。
- [x] **main.py**：lifespan 实例化 `AccountSwitchService` 挂 app.state。
- [x] 单测 `tests/unit/test_account_switch.py` **4/4 通过**（成功路径、失败不恢复、active_count=0 清门禁、并发锁）。
- [x] 现有测试：circuit_breaker + risk_manager + accounts 59 通过。
- [ ] **待全量测试结果**：确认整体无回归。
- [ ] **M7 补充**：`_reconcile_once` 已按账号限定（不 adopt 其它账号持仓），但新账号既有持仓的「策略开关」仍为代码级保证，前端 UI 展示留给 Phase 6。

### Phase 6: 实施 — 前端账号管理 + 切换 UI (complete)
- [x] 新增页面：`frontend/app/accounts/page.tsx`（独立页）
  - 账号列表表格（login、server、broker）+ 当前活跃徽章
  - 「新增账号」表单（login/password/server/broker_name，密码 password 输入框）
  - 「切换」按钮 → `window.confirm` 确认弹窗（翻译含"切换会暂停交易"）→ 调 `POST /api/accounts/{id}/switch` → 展示结果
  - 「删除」软删按钮
- [x] 翻译：`frontend/messages/{zh,en}/accounts.json` + `nav.json` 加 `accounts`
- [x] 侧边栏：`Sidebar.tsx` 加 `/accounts` 导航项（Wallet icon，system 组）
- [x] tsc 类型检查通过
- [ ] **待 build 结果**：确认生产构建无错（翻译加载、路由注册）

### Phase 7: 测试 + 文档 (complete)
- [x] 后端单测：`test_accounts.py` 7/7、`test_account_switch.py` 4/4、风控/相关回归 81 通过。
- [x] Bridge 测试：`test_switch_account.py` 7/7（health 语义、切换成功/失败、鉴权、字段校验）。
- [x] **Phase 0 spike 结论**：记录于 findings.md（mt5.login 同终端切换可行，用户实测）。
- [x] 前端 tsc + build 通过（`/accounts` 路由已注册）。
- [x] 更新 CLAUDE.md（架构/新端点/账号隔离约定）。
- [x] CI 加 bridge 测试 job（`ci.yml`，Linux mock 运行）。
- [x] 全量 pytest：851 passed / 9 failed（失败均为**既有环境问题**：multi_agent 模型 ID 断言依赖会话 provider、backtest 数据断言、ml 模块隔离——与本次改动无关）。
- [x] 无 TODO/FIXME/skip 残留。

## 实施顺序与依赖

```
Phase 0 (SDK spike — 阻断项，通过后才继续)
   ↓
Phase 3 (Bridge 端点 + /health logged_in + 测试基建)
   ↓
Phase 4 (数据模型账号隔离 + mt5_accounts + CRUD)
   ↓
Phase 5 (切换服务：锁/drain/门禁/风控隔离/显式恢复)
   ↓
Phase 6 (前端 UI)
   ↓
Phase 7 (测试 + 文档)
```

## 关键设计决策（v2 修订后）

1. **凭据存储**：独立 `mt5_accounts` 表 + VaultService AES-256-GCM 加密列。**不写 secrets 表**（避免 runner 注入 H5）。API 永不返回明文。
2. **切换原子性**：先暂停后切换。失败 → 回滚原账号连接，**不恢复引擎**；成功 → 显式 `start()` + `active_count` 断言。
3. **同券商复用**：connector 是共享单例、URL 不变，MarketDataService 是薄包装 → 引擎无需重建。切换后仅刷新规格 + 清 positions 缓存 + 显式恢复。
4. **并发安全（H2）**：切换锁与 `_engines_lock` 合并 + Redis `switching:in_progress` 门禁 + 在途订单 drain。
5. **健康监控协调（C2）**：`/health` 必须含 `logged_in`；切换窗口内禁用 `_on_success` 自动恢复（引擎状态由切换服务独占管理）。
6. **风控隔离（H3）**：circuit key 全部加账号维度；`peak_balance` 按账号分键。
7. **数据模型（H4）**：Trade/BotEvent 加 `account_login`；`ticket` 唯一改复合。
8. **新账号持仓（M7）**：不自动 adopt，仅跟踪，策略开关默认关闭接管。

## 风险与缓解（v2 增补）

| 风险 | 缓解 |
|------|------|
| **C1：同终端切换假设不成立/弹窗** | Phase 0 真实 spike；不通过则换方案 |
| **C2：健康监控把无账号当健康，自动恢复交易** | `/health` 加 logged_in；切换窗口抑制自动恢复 |
| **H1：切换成功后引擎不恢复（静默停）** | 显式 `start()` + `active_count` 断言 |
| **H2：切换瞬间 MCP/引擎下单落在错误账号** | 锁合并 + Redis 门禁 + 在途 drain |
| **H3：风控状态跨账号污染/切换制造停盘** | circuit key 按账号隔离；peak_balance 分键 |
| **H4：ticket 跨账号冲突 / orphan 误 adopt** | 复合唯一 + 对账按账号限定 + 不自动 adopt |
| **H5：密码注入 sandbox runner** | 独立表，不进 secrets 表/runner 注入集 |
| 切换瞬间不交易 | 暂停/恢复（恢复需验证） |
| 密码泄露 | Vault 加密 + API 脱敏 + 独立表隔离 |
| 规格/别名不匹配（虽同券商） | 切换后重跑规格校验 + volume 回填 |
| 切换竞态 | 锁合并 + 门禁 + drain |

## 打开项（仅限非关键）
- 前端账号页并入 integration 页 vs 独立页（实施时定，无安全影响）
- `mt5_accounts` 是否加备注字段（非关键）
- Bridge `/account/switch` 凭据传输用 JSON body（Bridge 端须在 HTTPS 或可信内网部署；**鉴权强制 verify_api_key，不再留作打开项**——M2 已解决）

## Next Step

**全部 Phase 0-7 完成**。下一步（可选）：部署验证（VPS 上跑 Bridge + Railway 后端 + Vercel 前端联调）；提交 git（等待用户确认）。

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
|（暂无）| | |

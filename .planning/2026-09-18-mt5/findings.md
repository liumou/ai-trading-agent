# Findings — MT5 账号实时切换功能调研

## 调研日期
2026-09-18

## 结论摘要

**技术上可行**，但受 MetaTrader5 SDK 约束有硬限制。账号**当前完全由 MT5 Bridge（Windows VPS）持有**，后端无账号概念、无账号表、无凭据存储。实现方式取决于"切换"的确切含义，需用户澄清。

## 现有架构（代码证据）

### 1. MT5 Bridge 是唯一账号持有者
- 文件：`mt5_bridge/main.py`（Windows VPS 运行）
- 账号参数全部来自环境变量，启动时一次性登录：
  ```python
  MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
  MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
  MT5_SERVER = os.getenv("MT5_SERVER", "")
  # startup() 中： mt5.initialize(MT5_PATH) → mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
  ```
- **运行时重连 `ensure_connected()`（main.py:51）仍复用启动时环境变量**——无法在进程内换账号。
- `watchdog.py` 负责进程崩溃自动重启，重启时仍读同一环境变量。

### 2. 后端 Connector 完全无状态、单一目标
- 文件：`backend/app/mt5/connector.py`
- 构造时读 `settings.mt5_bridge_url` + `settings.mt5_bridge_api_key`，指向**单一** Bridge。
- 单例：`main.py:263 connector = MT5BridgeConnector()`，lifespan 内创建一次。
- 所有引擎共享此 connector（`manager.py:42 self.connector = connector`，`_build_engine` 复用）。

### 3. 引擎依赖
- `BotEngine`（`bot/engine.py:164-167`）：`self.connector` → `MarketDataService(connector)` + `OrderExecutor(connector)`。
- `BotManager.reload_engines()`（manager.py:284）：已有"按 DB symbol_configs 热重载引擎"，但**重载只换引擎对象，不换 connector**（`_build_engine` 仍用 `self.connector`）。
- `HealthMonitor`（bot/health_monitor.py）依赖单一 connector 心跳，连续 3 次失败暂停全部交易。

### 4. 无账号模型/表/凭据
- `models.py` 无账号表；`symbol_configs` 是品种级（broker_alias/asset/service 等），与账号无关。
- **integration 配置**（`api/routes/integration.py:215`）：MT5 仅暴露 `Bridge URL` + `API Key` 两个字段，保存到 Vault `secrets` 表（加密）。
- config.py：`mt5_bridge_url`/`mt5_bridge_api_key` 单值。
- **Vault secrets 保存后不改变运行中的 settings/connector**——仅影响下次启动/读配置。无账号概念。

### 5. 前端
- integration 页仅显示 MT5 Bridge 连接状态 + 编辑 URL/API key，无账号字段。
- 无任何"账号切换"UI。

## MT5 SDK 硬约束（关键）

MetaTrader5 Python API：
- `mt5.initialize(path)` 绑定**一个**终端进程。
- `mt5.login(login, password, server)` 可在**同一终端内**切换账号（登 不同账号）。
- **不能同时连接两个不同账号**——进程级单账号（同一时刻只能有一个激活账号）。
- 切换账号会重置 `account_info()` / `positions_get()` / history；不同券商品种集合（`symbols_get()`）与规格会变。

## 三种候选方案（待用户选择语义）

### 方案 A：同 Bridge 内热切换（推荐，最轻）
- Bridge 端新增 `/account/switch` 端点（POST login/password/server），内部 `mt5.login()` 重连。
- 后端新增账号 CRUD + 切换 API，前端新增"多账号列表 + 切换" UI。
- 优点：单 VPS 单进程，改动集中，可用于 demo。
- 限制：**同一时刻只有一个活跃账号**；切换时交易引擎须安全暂停（平仓/暂停→切换→加载新账号 symbol/规格→重启引擎）；不同账号品种/规格不同，`SYMBOL_PROFILES`/market/spec 需刷新；有交易风险（切换瞬间不交易）。

### 方案 B：多 Bridge 实例（每账号一个）
- 每账号在 VPS 上跑一个 Bridge 实例（不同端口/不同 env），用目录/端口隔离。
- 后端支持 `mt5_bridge_url` 变为 **每账号多 URL**，引擎按账号绑定 connector。
- 优点：真正并行多账号，互不影响；可同时交易。
- 成本：基础设施多实例、watchdog 多份、凭据多份、后端 connector 路由/管理复杂度显著上升。

### 方案 C：仅配置界面 + 重启（最简）
- 不做实时切换；在 integration/设置页配置多账号，切换需重启 Bridge（watchdog 重启）或重启后端读取新 env。
- 成本最低，但不是"实时"。

## 风险与影响面（任意方案通用）
1. **切换瞬间不交易**：引擎必须安全停顿，避免在换账号时下单。
2. **品种/规格不匹配**：不同账号的 `symbols_get()`、symbol spec、volume_min/max、别名不同，`SYMBOL_PROFILES` 与 `symbol_configs` 可能失效需要重新校验。
3. **风控/持仓对账**：切换后 positions/history 是另一账号的，影响 circuit breaker、daily loss、ML 缓存。
4. **ML 模型/特征**：按 symbol 训练，切换账号后同一 symbol 的行情来源（不同券商可能报价不同）会偏移。
5. **魔力号/订单审计**：magic 需保持，避免误认脏订单。
6. **凭据安全**：账号密码需入 Vault（AES-256-GCM），不得明文。

## 打开项（待用户确认）
1. 切换是**单账号热切换**（一次只活一个）还是**多账号并行**？
2. 是**自营 VPS 内部账号**（同一券商多账号）还是**跨券商**（不同 server）？
3. 切换时对**现存持仓**的处理：保留 B 手动安排，还是强平/暂停？
4. 目标是否只是**前端 UI 演示**能力，还是生产交易？
5. 是否需要前端 UI 新增交互，还是要后端 API 即可？

## 评审前置验证（2026-09-18，补充）

### 切换后必须处理的隐式状态（评审补充项，代码证据）

1. **`reload_engines()` 不重建已有引擎**：对已有 symbol 只调 `apply_profile()`（仅更新参数，不重建 `market_data`/`executor`/connector 引用）。切换后引擎不会自动换账号语境——需**显式重建引擎**或新增"切换上下文"机制。
   - 证据：`manager.py:284-353`（to_update 分支只 `apply_profile`）；`engine.py:250 apply_profile`。
2. **`engine.stop()` 是幂等空操作**：只改 state + 记事件，**不清 `_known_tickets`**（已跟踪持仓 ticket）、paper 持仓、风控计数、ATR 记忆。切换后 `stop()` 再 `start()` 时，`start()` 会用**新账号**的当前持仓重新播种 `_known_tickets`（`engine.py:start`），但旧遗留状态（paper/风控）需显式清理。
   - 证据：`engine.py` stop（196-230 行区域）只改 state。
3. **`first_engine` 强引用依赖**：`HistoricalDataCollector(first_engine.market_data)`、`backtest.set_market_data(first_engine.market_data)`、`data.set_collector`、`ml.set_ml_deps` 全部绑定**第一个引擎的 market_data**。若切换后重建 first_engine，这些收集器仍指向旧 market_data 的 connector 实例。
   - 证据：`main.py:287, 299, 325-328`；`/health` 也依赖 first_engine（`main.py:628-635`）。
4. **健康监控会误判切换**：`health_monitor.check()` 每 30s 调 `/health`，Bridge 切换瞬间可能返回 disconnected → `_consecutive_failures++` → 达到 3 次进 `_is_degraded` → **自动暂停**引擎；切换完成 `/health` 恢复 → `_on_success` **自动 resume**。与我们的暂停/恢复冲突，需**切换期间抑制健康监控**（或切换期间视为 planned）。
   - 证据：`health_monitor.py` _on_success/_on_failure。
5. **MCP 工具用独立 `_connector`**：`mcp_server/tools/broker.py:26` `init_mcp_tools` 时 `_connector = MT5BridgeConnector()`（URL 相同，非 main.py 单例）。因 URL 不变、切换在 Bridge 内部做，MCP 工具**无需改** —— 支撑「单 Bridge 内切换」方向。
6. **切换后规格刷新入口**：`load_profiles_into_memory()`（`symbol_config_service.py:81`）+ `_validate_symbols_at_startup`（`main.py:107`）现有可复用。切换后应重跑规格校验（同券商下别名/规格基本一致，但需刷新 volume_min/max 回填）。

### MT5 SDK 关键语义验证（2026-09-18，官方文档）

- **`mt5.login(login, password, server)` 可在 `initialize()` 后直接切换账号，无需 `shutdown()`**。
  - 官方文档（MQL5 Python Integration / login）：示例即 `initialize()` 后直接 `mt5.login(account)`，password/server 可选（缺省用终端数据库/上次服务器）。
  - 佐证：计划 Phase 3「同终端内热切换」技术假设成立。
- **切换影响**：`account_info()` / `positions_get()` / history 全部变成新账号的；`symbols_get()` 反映新账号的 Market Watch 订阅（同券商下基本一致，但切换后应重新校验 volume_min/max 回填）。

## 佐证引用
- `mt5_bridge/main.py`:20-22, 51-62, 89-103（env 登录、ensure_connected 复用 env）
- `backend/app/mt5/connector.py`:21-36（单例、URL/Key 构造时读 settings）
- `backend/app/bot/manager.py`:42, 258-260, 284（共享 connector、reload 不重建 connector）
- `backend/app/bot/health_monitor.py`:13, 24（单一 connector 心跳）
- `backend/app/api/routes/integration.py`:215, 250-251（MT5 仅 URL+Key，存 vault）
- `backend/app/config.py`:159-160（单值 bridge url/key）
- `backend/app/db/models.py`:399（symbol_configs 品种级，无账号）
# 交易信号诊断报告 — 为什么长时间没有产生交易信号

**诊断时间**：2026-09-16 12:30 CST（04:30 UTC）
**代码版本**：`5ad1357`（main 分支）
**诊断对象**：GOLD 引擎（`symbol_configs` 中唯一启用的品种）、远端 Postgres `100.72.200.33:15432/mt5`、远端 Redis `100.72.200.33:16379/2`、MT5 Bridge `192.168.3.47:8001`

---

## 0. 结论速览（TL;DR）

机器人不是"没找到机会"，而是**下单能力被三重开关同时锁死**，而且**后端进程已在 12:12 退出**。
当前配置下，无论行情怎么走，系统都**不可能**产生任何真实成交。

| # | 锁 | 当前实际值 | 证据 | 后果 |
|---|---|---|---|---|
| **1** | 交易模式锁 | Redis `trading_mode = ai_autonomous` | Redis key + `audit_log` id=58 | 策略引擎整条路径**从不执行**，连"信号检测"都不会发生 |
| **2** | Agent 角色锁 | `AGENT_MODE` 未配置 → `single` | `backend/.env` 无该键、`config.py:284` 默认 | 单 Agent 是"分析师"，系统提示词**明令禁止**下单 |
| **3** | 下单闸门锁 | `rollout_mode=micro` + `LLM_ALLOW_LIVE=false` + `LLM_PROVIDER=openai_compat` | Redis `guardrails:rollout_mode` + `.env` | 即使 AI 想下单，`openai_loop` 的 AC-11 预检**直接拒单** |
| **4** | 进程状态 | 后端已退出（PID 50489 消失） | `bot.log` 最后一行 12:12:16；8002 端口无监听 | 现在连"分析"都没有在跑 |

**一句话**：`ai_autonomous` 关掉了策略引擎，但 `single` 模式的 AI 只会写分析报告、不会下单，而 `LLM_ALLOW_LIVE=false` 又把 AI 下单的路也封了 —— 两条路都不通，所以"跑很久零信号"。

**最近一次真实信号**：`2026-09-14 10:12:18`（"BUY signal on GOLD"），1 秒后产生一笔纸面单。
**此后至今（约 50 小时）零信号、零成交。**

---

## 1. 诊断方法

| 手段 | 具体做什么 |
|---|---|
| 只读 SQL | `symbol_configs` / `bot_events` / `audit_log` / `trades` / `ai_usage_logs` / `ohlcv_data` 全量快照 |
| 只读 Redis | 13 个 key 全量 dump（`trading_mode`、`guardrails:*`、`circuit:*`） |
| 日志解析 | `backend/logs/bot.log`（28MB，覆盖 09-14 21:40 → 09-16 12:12）按关键字聚合 |
| 真实行情回放 | 从 MT5 Bridge 拉 300 根 GOLD M15，直接调用仓库里的策略类，看"如果解禁会不会有信号" |
| 进程/端口 | `ps`、`lsof`、4 台主机的 `/health` 探测 |

---

## 2. 交易信号 → 成交的完整规则链

这是"信号产生的规则"的权威清单（策略模式下）。**任何一步不通过，本次 K 线就不会有任何动作。**

| 步骤 | 位置 | 判定条件 | 当前取值 |
|---|---|---|---|
| 1 | `scheduler.py:360` `_candle_job` | M15 收线触发（cron `0,15,30,45`）；引擎 `state==RUNNING`；`is_market_open(symbol)` | ✅ 正常触发（每 15 分钟） |
| 2 | `scheduler.py:368` | 从 Redis 读 `trading_mode`，**覆盖** env 配置 | ❌ **`ai_autonomous`** → 走 else 分支，**不调用** `process_candle()` |
| 3 | `engine.py:384` | `if trading_mode=="ai_autonomous" or strategy is None: return` | ❌ 即使被调用也会立即返回 |
| 4 | `engine.py:403-414` | 熔断：单品种日亏、全局日亏、距峰值回撤 `max_drawdown_from_peak=15%` | ✅ 未触发（峰值 12073.18 / 现 11569.18 = 回撤 4.17%） |
| 5 | `engine.py:417` `_detect_regime` | 多周期 + HMM 行情状态识别 | ✅ 正常（`trending`） |
| 6 | `engine.py:420-426` | 宏观事件临近 `EVENT_BLOCK_HOURS=2` | ⚠️ 只减仓（`EVENT_LOT_FACTOR=0.5`），不阻断 |
| 7 | `engine.py:623` `_generate_signal` | 取 `DEFAULT_OHLCV_BARS=200` 根 M15 → `strategy.calculate(df)` | — |
| 8 | `engine.py:644` | `signal = int(df.iloc[-2]["signal"])`，**只认"刚收盘那一根"**，为 0 即放弃 | 实测最近一根 = 0 |
| 9 | `engine.py:649-659` | MTF 过滤（`use_mtf_filter=True`）：H1 EMA21 斜率与信号方向相反 → 阻断 | 当前 H1 = 上行 |
| 10 | `engine.py:435` `_get_ai_sentiment` | 需 `use_ai_filter=True` 且有情绪数据 | ✅ 有（但常态为 neutral） |
| 11 | `risk/manager.py:292` `can_open_trade` | ①并发上限 `max_concurrent_trades=3` ②日亏 `max_daily_loss`（GOLD=2%）③AI 情绪反向且 `confidence ≥ 有效阈值` | ⚠️ 情绪反向时才拦 |
| 11b | `risk/manager.py:255` | 有效阈值 = `ai_confidence_threshold(0.7)` + 时段加成 + `ranging +0.05` + 回撤加成(`>5% +0.05`,`>10% +0.10`) + 近期胜率低 `+0.10`，夹在 `[0.56, 0.95]` | 基准 **0.7** |
| 12 | `engine.py:769-814` | 组合杠杆上限 `max_portfolio_leverage=3.0`；相关性冲突检查 | 空仓 → 通过 |
| 13 | `engine.py:474-557` | **确认门**：可用数据源 ≥3 才生效，通过数需 ≥ `max(2, ceil(源数/2))`；来源=quant / ML / regime / R:R / AI | 常态要求 3/5 |
| 14 | `engine.py:866` `_size_and_place_order` | ATR SL/TP：`sl=1.5×ATR`、`tp=5.0×ATR`（GOLD 的 DB 值）；手数：固定手数 或 风险模型（≥20 笔平仓单走 Kelly，否则 `max_risk_per_trade=1%`），再乘 warmup / 连亏 / 波动率 / regime 系数，最后按券商手数网格向下取整 | — |
| 15 | `engine.py:949` | **纸面**（`paper_trade=true`）或真实下单（`executor.place_order` → Bridge `/order`） | GOLD 当前 `paper_trade=true` |
| 16 | `engine.py:1015` | 落库 `trades` + 事件 `TRADE_OPENED` | — |

**关键点**：步骤 8 的设计意味着"信号只在交叉发生的那一根 K 线有效"。ema_crossover 只在 `ema20` 上/下穿 `ema50` 的那一根给 ±1，**其它所有 K 线都是 0**。

---

## 3. 实测证据

### 3.1 Redis —— 决策状态的唯一真相源

```
trading_mode                = ai_autonomous          ← 锁 1
guardrails:rollout_mode     = micro                  ← 锁 3
enable_auto_strategy_switch = 1
circuit:peak_balance        = 12073.18
metrics:counter:mt5_bridge_errors = 2418
session:agent:GOLD:2026-09-16 = {... "regime": "trending", "rsi": 70.76, "zscore": 2.1637 ...}
pending_trades:*            (不存在)
```

`main.py:368-377` 会在**每次重启时**从 Redis 恢复 `trading_mode`：

```python
cached_mode = await redis_client.get("trading_mode")
if mode in ("strategy", "ai_autonomous"):
    settings.trading_mode = mode          # ← 重启也甩不掉这个状态
```

**所以这把锁是"粘性"的：重启后端不会自动恢复策略交易。**

### 3.2 数据库

| 表 | 行数 | 说明 |
|---|---|---|
| `bot_events` | 327 | `AI_ANALYSIS` 210、`STOPPED` 44、`STARTED` 41、`SIGNAL_DETECTED` 16、`TRADE_OPENED` 8、`AI_AGENT_ERROR` 5、`ERROR` 3 |
| `trades` | **0** | 没有任何成交记录（含归档） |
| `ai_usage_logs` | 490 | 09-13: 44 次(成功 12) / 09-14: 356(304) / 09-15: 81(81) / 09-16: 9(8) |
| `ml_prediction_logs` | 15 | ML 推理日志几乎没在增长 |
| `ohlcv_data` | 140821 | GOLD M15 最新一根 = **2026-09-15 00:00** ← 入库已停 |

`audit_log` 中的模式变更（全部是 owner 通过前端 `/api/bot/strategy` 触发）：

| id | 时间(UTC) | 变更 |
|---|---|---|
| 15 | 09-13 18:49:05 | GOLD → **ai_autonomous** |
| 16 | 09-13 18:49:17 | BTCUSD → ai_autonomous |
| 38 | 09-14 11:34:23 | GOLD → **ai_autonomous** |
| 41 | 09-14 12:16:20 | BTCUSD → ai_autonomous |
| 58 | **09-16 03:20:40** | GOLD → **ai_autonomous**（当前状态） |

`rollout_mode_changed`：09-14 11:30:17 `shadow→paper`、09-14 11:31:30 `paper→micro`（即当前 `micro`）。

### 3.3 日志（`backend/logs/bot.log`，覆盖 09-14 21:40 → 09-16 12:12）

| 关键字 | 次数 | 含义 |
|---|---|---|
| `Signal detected` | **0** | 整个日志周期内一次信号都没有 |
| `PAPER trade` | **0** | 一次下单都没有 |
| `process_candle` | **0** | 策略主函数**从未被调用**（连它内部的 skip 日志都没出现） |
| `Redis trading_mode read failed (fallback 'ai_autonomous')` | 3 | 09-15 19:30/19:45/20:00 —— **Redis 掉线时兜底值本身就是 ai_autonomous** |
| `Error 61 connecting to 100.72.200.33:16379` | 36 | 远端 Redis 连接被拒（集中在 09-15 19:24–20:00） |
| `Heartbeat check error: Can't reconnect until invalid transaction is rolled back` | 每 30 秒 | 共享 DB 会话中毒，长期带病运行 |
| `Macro context failed: session is in 'prepared' state` | 多次 | 同一个会话中毒问题 |

日志最后一行：`2026-09-16 12:12:16.291 | INFO | app.api.websocket - WebSocket client disconnected`
→ 之后**再无日志**，PID 50489 消失，`127.0.0.1:8002` 无监听。

### 3.4 MT5 Bridge（一切正常）

```
GET /health    → {"status":"ok","mt5":{"login":336773771,"server":"XMGlobal-MT5 9"}}
GET /account   → {"balance":11569.18,"equity":11569.18,"margin":0.0,"profit":0.0}
GET /tick/GOLD_→ {"bid":4327.54,"ask":4328.10,"spread":0.56}
```

**券商侧不是问题**：连接正常、有余额、无持仓、点差正常。

### 3.5 真实行情上回放策略（证明"策略代码本身是好的"）

用 Bridge 的 300 根 GOLD M15 直接调用仓库里的策略类：

| 策略 | 300 根内信号数 | 占比 | 折算（96 根/天） | 最近一根 |
|---|---|---|---|---|
| `ema_crossover`（GOLD 默认） | 7 ~ 11 | 2.3% ~ 3.7% | ≈ 2 ~ 3.5 个/天 | 0 |
| `breakout` | 10 | 3.3% | ≈ 3.2 个/天 | 0 |
| `mean_reversion` | 21 | 7.0% | ≈ 6.7 个/天 | 0 |
| `rsi_filter` | 1 ~ 3 | 0.3% ~ 1.0% | ≈ 0.3 ~ 1 个/天 | 0 |
| `ml_signal`（ML 模型已就绪） | 23 | 7.7% | ≈ 7.4 个/天 | 0 |

（数字随 300 根滑动窗口变化，两次采样区间 2.3%~3.7%；`backend/scripts/diagnose_no_signal.py` 可随时复跑。）

结论：
1. **策略可用** —— 历史区间里持续在产生交叉信号；
2. **信号天然稀疏** —— ema_crossover 平均约 10 小时才给一次；
3. 引擎只认 `df.iloc[-2]`（刚收线那一根），**错过即永久错过**，不补发。

---

## 4. 时间线（关键节点，UTC）

| 时间 | 事件 | 来源 |
|---|---|---|
| 09-13 18:48-18:49 | 配置 GOLD：`paper_trade=true`、并发 2、日亏 2%；GOLD/BTCUSD 策略设为 **ai_autonomous** | `audit_log` 13-16 |
| 09-14 00:11 → 10:12 | **最后一批真实活动**：8 次 `SIGNAL_DETECTED` + 8 笔 `PAPER` 开仓（`@2050.0`） | `bot_events` 51-146 |
| **09-14 10:12:18** | **最后一次信号**："BUY signal on GOLD"；1 秒后 PAPER 开仓 0.04 手 | `bot_events` 144-146 |
| 09-14 11:30-11:34 | rollout `shadow→paper→micro`；auto-strategy-switch 打开；**GOLD → ai_autonomous** | `audit_log` 30-38 |
| 09-14 11:44 / 12:16 | BTCUSD 启动后也设为 ai_autonomous（Redis 是**全局键**，一起把 GOLD 拖进 AI 模式） | `audit_log` 39-41 |
| 09-14 22:18 | MT5 Bridge 连续 3 次不可达 → 暂停 | `bot_events` 243 |
| 09-15 01:27-01:28 | Bridge 恢复自动重启 + "Bot started (ema_crossover)" | `bot_events` 244-245 |
| 09-15 19:24-20:00 | 远端 Redis 连接被拒（36 次），模式兜底值 = ai_autonomous | `bot.log` |
| 09-16 03:04 | Bridge 恢复自动重启 | `bot_events` 323 |
| 09-16 03:16-04:00 | AI 每 15 分钟出分析报告（`AI_ANALYSIS`），**全篇只有分析、没有下单** | `bot_events` 324-327 |
| 09-16 03:20:40 | 前端再次把 GOLD 设为 **ai_autonomous**（当前状态） | `audit_log` 58 |
| **09-16 12:12:16** | **后端退出**（日志终止、端口释放、PID 消失） | `bot.log` |

---

## 5. 为什么"很久没有信号"——逐层归因

### 5.1 主因：三重锁形成死锁（100% 解释零成交）

```
                 ┌──────────────────────────────┐
   行情/K线 ───► │  trading_mode = ai_autonomous │───► 策略引擎被关闭
                 └──────────────────────────────┘     （不产生任何信号）
                              │
                              ▼
                 ┌──────────────────────────────┐
                 │  agent_mode = single         │───► AI 只做分析报告
                 │  system_prompt: "You are a   │     （提示词：MUST NOT
                 │  market analyst, NOT a       │      call place_order）
                 │  decision-maker"             │
                 └──────────────────────────────┘
                              │
                              ▼
                 ┌──────────────────────────────┐
                 │ rollout=micro +              │───► 即使 AI 真的调用
                 │ LLM_ALLOW_LIVE=false +       │     place_order，也会被
                 │ provider=openai_compat       │     "TRADE BLOCKED" 拦截
                 └──────────────────────────────┘
```

逐条证据：

1. **策略路径被关**：`scheduler.py:388` 只有 `trading_mode == "strategy"` 时才 `await engine.process_candle()`。Redis 里是 `ai_autonomous`，所以走 `else` 分支，只做 `_detect_regime()` + `_run_ai_agent()`。日志里 `process_candle` 出现 **0 次** 与此完全吻合。
   另外 `engine.py:384` 还有第二道闸（`or self.strategy is None`）—— 前端把策略设为 `ai_autonomous` 时会把 `engine.strategy` 置为 `None`（`bot.py:221`），双重保险地关闭策略。

2. **AI 路径被角色限制**：`backend/.env` **没有** `AGENT_MODE`，默认 `single`（`config.py:284`）。单 Agent 用的提示词 `mcp_server/system_prompt.md` 明确写着：

   > You are a market analyst, **NOT a decision-maker** ... **You do NOT place orders or execute trades.** The strategy engine handles execution.
   > MUST NOT call place_order, modify_position, or close_position

   而 `_build_user_message()` 给单 Agent 的任务描述也是："Trading is handled by the strategy engine."（`agent_config.py:133`）
   → **AI 被明确告知"别交易，交给策略引擎"**，问题是策略引擎刚被关掉了。

3. **AI 路径被 rollout 限制**：`mcp_server/agents/openai_loop.py:108-129` 的 `_rollout_allows_trade()`：

   ```python
   if mode in ("micro", "live") and not settings.llm_allow_live:
       return False, "rollout=micro: 非 Claude provider 默认只允许 shadow/paper，需显式设置 LLM_ALLOW_LIVE=true 才放开（AC-11）"
   ```
   当前 `LLM_PROVIDER=openai_compat`（DeepSeek）+ `rollout_mode=micro` + `LLM_ALLOW_LIVE=false`
   → 任何 `place_order` / `modify_position` / `close_position` 调用会被标记 `blocked: "rollout"`、`executed: False`，并回给模型 "TRADE BLOCKED: ..."（`openai_loop.py:329-337`）。

4. **重启也解不开**：`main.py:368` 每次启动都从 Redis 恢复 `trading_mode`；Redis 掉线时兜底值又是 `ai_autonomous`（`scheduler.py:364` + 09-15 19:30 的日志）。所以"重启大法"无效。

5. **进程本身也已停止**：12:12 之后连分析都没有了。

### 5.2 次因：即使解锁，信号也稀少（给人"永远不触发"的体感）

以 GOLD 默认的 `ema_crossover(20,50)` 为例：

- 只在 `ema20` 上/下穿 `ema50` 的那**一根** K 线产生 ±1，其它全为 0；
- 实测 **7 / 300 根 ≈ 2.3%**，M15 折算 **≈ 2.2 次/天**；
- 引擎只在收线时刻读取 `df.iloc[-2]`，且要求引擎当时处于 `RUNNING`；
- 通过后还要再连过 4 道门（MTF 过滤 → AI 情绪 → 风控/相关性 → 确认门 3/5）。

**概率相乘后**：一天 2 次原始信号 → 能走到下单的往往 0~1 次。叠加"模式锁定"，就是 0。

### 5.3 历史上真实发生过的"信号消失"因素

| 因素 | 实例 | 影响 |
|---|---|---|
| MT5 Bridge 不可达 | 09-14 22:18、09-15 04:38、09-15 08:18 | 引擎置 PAUSED，直到恢复才自动 resume |
| 远端 Redis 掉线 | 09-15 19:24–20:00（36 次 Error 61） | 熔断/模式/持仓状态全部读写失败 |
| 上游数据源故障 | 09-14 05:33–11:00 AI 反复报"数据不可用" | `_generate_signal` 拿不到 OHLCV 直接 return |
| 非交易时段/周末 | `is_market_open()` 拦截 | 收市后与周末无 candle 任务 |
| 熔断触发的自动暂停 | 09-14 10:12 `Auto-resumed after circuit breaker cooldown` | 冷却期内不交易 |

---

## 6. 次级隐患（与"零信号"不完全同因，但必须修）

| # | 隐患 | 证据 | 风险 |
|---|---|---|---|
| 1 | **`trades` 表 0 行，但有 8 条 `TRADE_OPENED` 事件** | 全表 count=0；`_recover_pending_trades` 每 5 分钟报错 | 成交/绩效/Kelly 统计全部失真（Kelly 需 ≥20 笔才启用，永远用不了） |
| 2 | **纸面成交价 `@2050.0`**，当时金价 ≈4290 | `bot_events` 51-146；`ohlcv_data` 无 1900-2200 区间数据 | 纸面模拟不可信；需查当时 `tick` 来源（疑似品种别名归一化 bug，`f43b4af` 之前） |
| 3 | `rollout` 读取不一致 | `broker.py:174` 用同步 `get_rollout_mode()`（读 env），而 `openai_loop` 用 Redis 持久值 | 前端降级为 `paper` 后，工具层可能仍按 env 的 `micro` 执行 |
| 4 | heartbeat 每 30 秒报"会话中毒" | `bot.log` 持续刷 | DB 会话污染，长期带病，遇错即崩 |
| 5 | MCP 工具回调后端 401 | AI 报告内 "Daily P&L: 无法获取（后端 401）" | Agent 拿不到组合状态，分析质量下降（`5ad1357` 修了一部分，需复验） |
| 6 | LLM 偶发超时 | `AI_AGENT_ERROR` 5 次（最新 09-16 03:45 `agent loop failed (timeout or exception)`） | 分析缺失；LLM 端点 `100.78.102.105:8317` 在局域网，稳定性依赖网络 |
| 7 | `ohlcv_data` 入库停在 09-15 00:00 | SQL 汇总 | 历史回测/ML 训练数据源断流 |
| 8 | 仅 GOLD 启用（其余 4 品种 `is_enabled=false`） | `symbol_configs` | 组合级策略/相关性风控实质只有单品种 |
| 9 | GOLD 的 `tp_atr_mult=5.0`（DB）≠ 默认 2.0 | `symbol_configs` | R:R = 5:1，确认门/风控口径随之变化，需确认是否符合预期 |
| 10 | `MT5BridgeConnector.close()` 在 httpx ≥0.28 上抛 `AttributeError` | `connector.py:40` 调 `self._client.close()`（应为 `aclose()`）；诊断脚本已实测复现 | 任何走 `close()` 的优雅关闭路径都会异常（子进程/测试/脚本） |
| 11 | 未加载 DB profile 的进程会**静默丢失**别名映射 | `to_broker_alias('GOLD')` 在未加载时返回 `'GOLD'`（正确应为 `'GOLD_'`）→ tick/OHLCV 全空 | `services/symbol_config_service.py:81` 已注明"每个会打 Bridge 的进程都必须加载"；MCP stdio 子进程 / runner 如漏调用，表现为"券商没数据"，极易误判为行情故障 |
| 12 | 从未出现 `TRADE_BLOCKED` 事件 | `bot_events` 全表无该类型 | 说明历史上没有任何信号走到"风控/相关性/确认门"才被拦 —— 所有信号一旦产生就直接成交（配合锁 1 解释：策略模式下确实只差最后一步） |
---

## 7. 修复方案

### 7.1 方案 A（推荐，最快）：恢复"策略交易"

目标：让 `trading_mode` 回到 `strategy`，把策略引擎重新接上。

```bash
# 1) 先确认后端在跑（当前它已经退了）
cd backend && ./start-backend.sh        # 或 .venv/bin/uvicorn app.main:app --port 8002

# 2) 把 GOLD 的策略从 ai_autonomous 改回真实策略（前端"策略"下拉选 ema_crossover 亦可）
#    该接口会：写 Redis trading_mode="strategy" + 重建 engine.strategy
curl -X PUT http://localhost:8002/api/bot/strategy \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer <JWT>" \
  -d '{"symbol":"GOLD","name":"ema_crossover"}'

# 3) 兜底做法（等价于第 2 步的模式位）：
#    redis-cli -h 100.72.200.33 -p 16379 -n 2 DEL trading_mode

# 4) 启动引擎
curl -X POST 'http://localhost:8002/api/bot/start?symbol=GOLD' -H "Authorization: Bearer <JWT>"

# 5) 确认模式/策略已生效
curl -s 'http://localhost:8002/api/bot/status?symbol=GOLD' -H "Authorization: Bearer <JWT>" | python3 -m json.tool | head -30
```

⚠️ **注意 `paper_trade`**：GOLD 当前 `paper_trade=true`（09-15 03:01:54 设置）。恢复策略后仍只会产生**纸面单**、不会真正打到券商。需要实盘要显式关掉：

```bash
curl -X PUT http://localhost:8002/api/bot/settings \
  -H 'Content-Type: application/json' -H "Authorization: Bearer <JWT>" \
  -d '{"symbol":"GOLD","paper_trade":false}'
```

### 7.2 方案 B：真的想用"AI 自主交易"

必须**四个条件同时**满足，否则 AI 仍然一单都发不出去：

```bash
# backend/.env
AGENT_MODE=multi            # orchestrator 才有 place_order 权限（single 是纯分析师）
LLM_ALLOW_LIVE=true         # 解除 AC-11 对 openai_compat 的下单拦截
# 并且 rollout 不能是 shadow/paper：
#   PUT /api/rollout/mode  {"mode":"micro"}  或 {"mode":"live","confirm":true}
```

重启后端后自查：
`/api/bot/status` 应显示 `strategy: ai_autonomous`（AI 模式）；
浏览器"AI 活动"页应能看到 orchestrator 的决策与下单记录；
Redis 中 `guardrails:rollout_mode` 与 `.env` 的 `LLM_ALLOW_LIVE` 要一致。

### 7.3 建议的代码级修复（防止同类问题再发生）

| 优先级 | 位置 | 建议 |
|---|---|---|
| P0 | `app/bot/manager.py`（启动自检） | 启动时若 `trading_mode=ai_autonomous` **且** `agent_mode=single` **且** `LLM_ALLOW_LIVE=false` → 判定为"无人可下单"，置 `PAUSED` 并发 Telegram 告警（fail-closed 而不是静默空转） |
| P0 | `app/api/routes/bot.py:214` | `name=="ai_autonomous"` 时校验 `agent_mode`/`LLM_ALLOW_LIVE`/`rollout_mode`，不满足直接 400，并给出所需配置 |
| P1 | `app/bot/scheduler.py:388` | `ai_autonomous` 分支下，若 agent 无权下单，则退化为 `process_candle()`（或在 UI 显式提示"AI 模式当前无法下单"） |
| P1 | `mcp_server/tools/broker.py:174` | 改用 `await _guardrails.get_persisted_rollout_mode()`，与 `openai_loop` 口径统一（否则 UI 降级不生效） |
| P1 | `app/runner/heartbeat.py` + `app/ai/context_builder.py:57` | 用独立 `async_session` 并在异常时 `rollback()`，消除"prepared state / invalid transaction"中毒 |
| P1 | `app/bot/engine.py:1630` `_save_trade` | 补监控：交易落库失败要发告警（当前只写 log + Redis 兜底，Redis 也挂时**静默丢失**，与 `trades` 表 0 行吻合） |
| P2 | 前端 Dashboard | 显著位置显示"当前谁能下单：策略引擎 / AI orchestrator / 无人"以及 `paper_trade` 状态 |
| P2 | `app/mt5/connector.py:40` | `self._client.close()` → `await self._client.aclose()`（httpx ≥0.28 已移除 `close()`） |
| P2 | `app/runner/*` + `mcp_server` 启动路径 | 显式调用 `load_profiles_into_memory()`，避免别名映射缺失导致"行情全空" |
| P2 | 数据采集 | 巡检 `ohlcv_data` 入库链路（已停在 09-15 00:00） |

### 7.4 修复后的验收清单

```bash
# ① 模式已回策略
redis-cli -h 100.72.200.33 -p 16379 -n 2 GET trading_mode        # 期望: strategy

# ② 引擎在跑
curl -s localhost:8002/api/bot/status?symbol=GOLD | jq '.state'  # 期望: RUNNING

# ③ 收线后有 SIGNAL_DETECTED / 或者至少能看到策略评估日志
grep -c 'process_candle' backend/logs/bot.log                    # 期望: >0（DEBUG 级）

# ④ 有信号时确认门/风控不拦（看是否出现 Trade blocked）
grep 'Trade blocked\|Confirmation gate' backend/logs/bot.log | tail

# ⑤ 成交落到 trades 表
psql "$DATABASE_URL_SYNC" -c "select count(*), max(created_at) from trades;"
```

---

## 8. 附录：本次诊断的复现命令

```bash
# Redis 全量状态
backend/.venv/bin/python - <<'EOF'
import asyncio, redis.asyncio as redis
async def m():
    r = redis.from_url("redis://100.72.200.33:16379/2", decode_responses=True)
    for k in sorted(await r.keys("*")):
        print(k, "=", await r.get(k) if await r.type(k)=="string" else await r.type(k))
    await r.aclose()
asyncio.run(m())
EOF

# 关键事件时间线
psql "$DATABASE_URL_SYNC" -c "
select id, created_at, event_type, left(message,90)
from bot_events
where event_type in ('SIGNAL_DETECTED','TRADE_OPENED','TRADE_BLOCKED','ERROR','AI_AGENT_ERROR')
order by id desc limit 20;"

# 模式变更审计
psql "$DATABASE_URL_SYNC" -c "
select id, created_at, resource, detail
from audit_log where action in ('bot_strategy_change','rollout_mode_changed') order by id;"

# 日志里的决定性证据
grep -c 'Signal detected'       backend/logs/bot.log     # 0
grep -c 'PAPER trade'           backend/logs/bot.log     # 0
grep -c 'process_candle'        backend/logs/bot.log     # 0
grep 'trading_mode read failed' backend/logs/bot.log | tail -3

# 策略在真实行情上的信号频率 —— 一键诊断（推荐）
# 输出：模式锁 / 下单权限 / 链路 / 策略信号频率 / 历史事件 / VERDICT
cd backend && .venv/bin/python scripts/diagnose_no_signal.py GOLD
```

> 诊断脚本 `backend/scripts/diagnose_no_signal.py` 为**只读**：不写库、不下单、不改 Redis。
> 它会自动加载 DB 品种 profile（别名解析前置条件）并实测 tick/OHLCV/策略信号。

---

## 9. 一页纸行动建议

1. **今天**：执行 §7.1 —— 把 GOLD 策略从 `ai_autonomous` 改成 `ema_crossover`（或任一真实策略），启动后端与引擎。这是"零信号"的**唯一主因**。
2. **同时**：决定要不要纸面单（`paper_trade`）—— 想真成交就设为 `false`。
3. **当天**：确认 §7.4 的 5 项验收。
4. **本周**：修 §6 的 4 个 P0/P1（fail-closed 自检、rollout 口径统一、会话中毒、落库告警），并复核 09-14 的 `@2050.0` 纸面价异常与 `trades` 表空表问题。
5. **可选**：如果确实要切换成"AI 自主交易"（方案 B），务必一次性配齐 `AGENT_MODE=multi` + `LLM_ALLOW_LIVE=true` + `rollout≥micro`，否则场景会重演。

> **一句话记住**：`ai_autonomous` 只负责"关掉策略引擎"，它**不会**自动让 AI 去下单 ——
> 只有 `AGENT_MODE=multi` 的 orchestrator 才持有 `place_order` 权限。二者必须在同一时间被打通。
# 模式切换配置手册 — 策略交易 / AI 自主交易自由切换

> 适用版本：`5ad1357`（main）· 更新：2026-09-17
> 前置阅读：`docs/SIGNAL-DIAGNOSIS-2026-09-16.md`（了解为什么会有这些锁）
>
> 本手册覆盖 4 个模式开关、所有合法组合、切换途径（UI / API / Redis / env）、
> 每种切换的验收方法与回滚手段。所有命令在 `backend/` 目录下执行。

---

## 1. 全景：4 个开关，各管一段

```
                     ┌─────────────────────────────────────────────┐
  ① trading_mode ──► │ 谁来交易？                                   │
     (Redis,持久)     │   strategy      → 策略引擎（信号→风控→下单）  │
                      │   ai_autonomous → AI Agent（关闭策略引擎）    │
                      └─────────────────────────────────────────────┘
                                      │
                     ┌─────────────────────────────────────────────┐
  ② AGENT_MODE ────► │ AI 是什么角色？（仅 ai_autonomous 模式相关）   │
     (env,需重启)     │   single → 纯分析师，【禁止】下单             │
                      │   multi  → orchestrator，有 place_order 权  │
                      └─────────────────────────────────────────────┘
                                      │
                     ┌─────────────────────────────────────────────┐
  ③ rollout_mode ──► │ 真钱还是模拟？（管所有下单出口）               │
     (UI/API,即时)    │   shadow → 只记录，不执行                     │
                      │   paper  → 模拟成交（假 ticket，无真钱）      │
                      │   micro  → 真实下单，强制 ≤0.01 手            │
                      │   live   → 真实下单，目标风险全开              │
                      └─────────────────────────────────────────────┘
                                      │
                     ┌─────────────────────────────────────────────┐
  ④ LLM_ALLOW_LIVE ► │ 非 Claude 模型的额外闸（AC-11）               │
     (env,需重启)     │   rollout ∈ {micro,live} 时必须为 true       │
                      │   rollout ∈ {shadow,paper} 时无所谓          │
                      └─────────────────────────────────────────────┘

  另有一个易混淆项：paper_trade（/api/bot/settings）
     → 只管【策略引擎】下单是否发给券商（引擎内部模拟撮合）
     → 对 AI 下单完全无效！AI 的"模拟"由 ③ rollout=paper 控制
```

### 记忆口诀

> **①定谁做、②定 AI 能不能做、③定真假钱、④给非 Claude 模型再上一道闸。**

---

## 2. 合法组合矩阵（照着抄就行）

| # | trading_mode | AGENT_MODE | rollout | LLM_ALLOW_LIVE | 效果 | 需重启? |
|---|---|---|---|---|---|---|
| A1 | `strategy` | 任意 | `paper` | 任意 | **策略信号 + 纸面成交**（引擎内部模拟） | 否 |
| A2 | `strategy` | 任意 | `micro` | 任意 | **策略信号 + 真实微单**（≤0.01 手） | 否 |
| A3 | `strategy` | 任意 | `live` | 任意 | 策略信号 + 真实全量下单 | 否 |
| B1 | `ai_autonomous` | `multi` | `paper` | 任意 | **AI 自主 + 模拟成交**（推荐先跑通） | 仅 AGENT_MODE |
| B2 | `ai_autonomous` | `multi` | `micro` | **true** | **AI 自主 + 真实微单**（≤0.01 手，当前你的配置） | 仅 AGENT_MODE |
| B3 | `ai_autonomous` | `multi` | `live` | **true** | AI 全权真实交易（高风险） | 仅 AGENT_MODE |
| C1 | `ai_autonomous` | `multi` | `shadow` | 任意 | AI 决策只记录、不下单（观察期） | 仅 AGENT_MODE |
| D1 | `strategy` + AI 分析 | `single` | 任意 | 任意 | 策略交易 + AI 只出分析报告（架构默认） | — |

### 🚫 死锁组合（会出现"跑了很久零成交"，但日志无报错）

| trading_mode | AGENT_MODE | rollout | LLM_ALLOW_LIVE | 结果 |
|---|---|---|---|---|
| `ai_autonomous` | `single` | 任意 | 任意 | **无人下单**：AI 只写分析（09-14 10:12 → 09-16 的根因） |
| `ai_autonomous` | `multi` | `micro`/`live` | `false` | orchestrator 调 place_order 被 AC-11 拦（`TRADE BLOCKED`） |
| 任意 | 任意 | 重启后未重设 rollout | — | ⚠️ 见 §7：工具层读 env，重启后回落 `shadow`，**订单静默不执行** |
| `strategy` | — | — | — | 引擎 STOPPED（后端重启后不会自动恢复，需手动启动） |

### 现场速查（2026-09-17 07:35 实测）

```
trading_mode = ai_autonomous   agent_mode = multi     rollout = micro
llm_allow_live = True          → 组合 B2：AI 自主 + 真实微单（≤0.01 手）
bot_state = STOPPED            → 只差「启动」这一步
```
---

## 3. 切换途径总览（先读这张表，后面是分步操作）

| 开关 | 真相源 | UI 位置 | API | 生效时机 |
|---|---|---|---|---|
| `trading_mode` | **Redis** `trading_mode`（重启持久） | 仪表盘 → 策略下拉 | `PUT /api/bot/strategy` | 即时 |
| 策略名/参数 | 引擎内存 + Redis | 仪表盘 → 策略 | `PUT /api/bot/strategy` | 即时 |
| `AGENT_MODE` | **env**（`.env`） | 无 UI | 改 `.env` | **重启后端** |
| `LLM_ALLOW_LIVE` | **env**（`.env`） | 无 UI | 改 `.env` | **重启后端** |
| `rollout_mode` | UI/API 同时写 env+Redis；⚠️ 工具层只读 env（见 §7） | 设置 → Rollout | `PUT /api/rollout/mode` | 即时（但见 §7 重启坑） |
| `paper_trade` | 引擎内存 | 仪表盘 → 设置 | `PUT /api/bot/settings` | 即时（策略引擎路径） |
| 风控参数（max_lot 等） | 引擎内存 | 仪表盘 → 设置 | `PUT /api/bot/settings` | 即时 |
| `enable_auto_strategy_switch` | Redis | 设置 | `PUT /api/bot/settings` | 即时 |
| 启动/停止引擎 | 引擎内存 | 仪表盘 | `POST /api/bot/start` `/stop` | 即时 |

**获取 JWT**（以下 curl 都需要）：

```bash
TOKEN=$(curl -s -X POST http://localhost:8002/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<你的密码>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
# 之后统一加 -H "Authorization: Bearer $TOKEN"
```

---

## 4. 模式 A：策略信号交易（经典模式）

**行为**：每根 M15 收线 → 策略算信号（ema20/50 交叉、breakout…）→ H1 多周期过滤 →
AI 情绪过滤（阈值 0.7）→ 风控/相关性 → 确认门（5 源过 3）→ 下单。
AI（single）只写分析报告，不干预交易。

### 切到策略模式

```bash
# 1) 选策略（这一步会自动把 trading_mode 写回 strategy）
curl -X PUT http://localhost:8002/api/bot/strategy \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{"symbol":"GOLD","name":"ema_crossover"}'
# 可选策略: ema_crossover / breakout / mean_reversion / rsi_filter / ml_signal
#          / ensemble / dca / grid / momentum_rank / pair_spread / risk_parity

# 2) 决定真钱 or 模拟（paper_trade 只管策略引擎这条路）
curl -X PUT http://localhost:8002/api/bot/settings \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{"symbol":"GOLD","paper_trade":true}'    # true=纸面 / false=真实

# 3) 启动引擎（后端重启过就必须重新点）
curl -X POST 'http://localhost:8002/api/bot/start?symbol=GOLD' -H "Authorization: Bearer $TOKEN"
```

### 可调参数

```bash
curl -X PUT http://localhost:8002/api/bot/settings \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{"symbol":"GOLD",
       "timeframe":"M15",
       "lot_mode":"auto",              # auto=风险模型 / fixed=固定手数
       "fixed_lot":0.05,               # lot_mode=fixed 时生效
       "max_lot":0.1,
       "max_risk_per_trade":0.01,      # 单笔风险 1%（auto 模式）
       "max_daily_loss":0.02,          # 日亏 2% 停
       "max_concurrent_trades":3,
       "use_ai_filter":true,           # AI 情绪过滤开关
       "ai_confidence_threshold":0.7,  # 情绪过滤阈值
       "enable_auto_strategy_switch":true}'
```

### 验收

```bash
curl -s "http://localhost:8002/api/bot/status?symbol=GOLD" -H "Authorization: Bearer $TOKEN" | \
  python3 -m json.tool | grep -E 'state|strategy|paper_trade'
# 期望: state=RUNNING, strategy=ema_crossover
# 收线后: bot_events 出现 SIGNAL_DETECTED；paper 模式出现 "PAPER" 开仓事件
```

---

## 5. 模式 B：AI 自主交易

### B1 = AI 自主 + 模拟（推荐起步）

```bash
# 1) .env 两行（AGENT_MODE 必须重启才生效）
#    AGENT_MODE=multi
#    # LLM_ALLOW_LIVE 在 paper 下不需要（AC-11 只拦 micro/live）

# 2) rollout 切 paper（注意只能逐级切换，见 §6）
curl -X PUT http://localhost:8002/api/rollout/mode \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{"mode":"paper"}'

# 3) 重启后端（加载 AGENT_MODE）
./stop-all.sh && ./start-backend.sh

# 4) 重启后必做：重新设一次 rollout（原因见 §7）+ 启动引擎
curl -X PUT http://localhost:8002/api/rollout/mode \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{"mode":"paper"}'
curl -X POST 'http://localhost:8002/api/bot/start?symbol=GOLD' -H "Authorization: Bearer $TOKEN"
```

### B2 = AI 自主 + 真实微单（你当前的选择）

```bash
# .env:
#   AGENT_MODE=multi
#   LLM_ALLOW_LIVE=true
# rollout 保持 micro，重启后端，重设 rollout，启动引擎（同上 3/4 步）
```

### 真单模式下自动生效的硬限制（guardrails，代码级不可绕过）

| 限制 | 值 | 代码 |
|---|---|---|
| 单笔手数 | micro 强制 ≤ **0.01** | `guardrails.py:47 MICRO_MAX_LOT` |
| 单品种/总持仓 | ≤3 / ≤5 | `:21-22` |
| 日亏 | ≥3% 拒单；周亏 ≥7% | `:25-26` |
| 连亏 | 5 笔暂停 | `:27` |
| 频率 | 每小时 ≤5 笔；间隔 ≥120s | `:30-31` |
| 点差 | >均值 3 倍拒单 | `:32` |
| 每周期下单数 | `LLM_MAX_ORDERS_PER_LOOP=1`（1~3） | `.env` |

### 验收（下一个 M15 收线后 1~5 分钟）

```bash
# 1) multi-agent 全流程日志
grep -E 'Orchestrator|Tool: place_order|MICRO MODE|TRADE BLOCKED' logs/bot.log | tail -20
# 期望: [Orchestrator] Starting multi-agent analysis: GOLD M15
#       （若决定交易）[Agent] Tool: place_order + MICRO MODE: lot capped ... to 0.01

# 2) AI 决策事件（HOLD 也正常，有单则看 TRADE_OPENED）
psql "$DATABASE_URL_SYNC" -c "select id,created_at,event_type,left(message,80) from bot_events order by id desc limit 5;"

# 3) 真实仓位（micro 模式在 MT5 终端可直接看到）
curl -s -H 'x-bridge-key: local_bridge_key_2024' http://192.168.3.47:8001/positions
```

**判断"管道通但 AI 选择不做" vs "被拦"**：
- 日志有 `[Agent] Tool: place_order` → 管道通，AI 主动交易了
- 日志有 `rollout block:` / `TRADE BLOCKED` → 被闸拦了，按 §8 排查
- 只有 `log_decision`、无 place_order → AI 决策 HOLD（正常市场判断）

---

## 6. rollout 的逐级切换规则（API 会强制校验）

`PUT /api/rollout/mode` 不允许跳跃，只能沿下面的箭头走（回退随时可以）：

```
shadow ──► paper ──► micro ──► live
   ▲          ▲         ▲ │
   └──────────┴─────────┘ │
        （任意回退）        └── live 只能去 shadow/micro
```

合法转移表（`rollout.py:63-70`）：`shadow→paper`；`paper→shadow|micro`；`micro→shadow|paper|live`；`live→shadow|micro`。

**当前你在 micro**：可以直接去 `shadow` / `paper` / `live`，无需逐级。

---

## 7. ⚠️ 重启后的 rollout 陷阱（最重要的一节）

代码里 rollout 有**两套读取口径**：

| 位置 | 读取方式 | 重启后的值 |
|---|---|---|
| `agents/openai_loop.py:120`（下单预检 AC-11） | **Redis** 持久值 | `micro` ✅（Redis 持久） |
| `tools/broker.py:174/279/320`（真正执行下单/改单/平仓） | **env** `os.environ["ROLLOUT_MODE"]` | **`shadow`** ❌（env 不持久！） |

**后果**：后端重启后，orchestrator 的下单会通过 AC-11 预检，却在工具层被当作
`shadow` 拦截 —— 日志只有一句 `"Shadow mode: order logged for review, not sent to broker"`，
**订单静默不执行，无任何报错**。

### 三种规避方法（选一）

```bash
# 方法 1（推荐，每次重启后做一次）：重新设一遍 rollout
#   UI: 设置 → Rollout → 选 micro → 保存
curl -X PUT http://localhost:8002/api/rollout/mode \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{"mode":"micro"}'
# 该接口会同时写 os.environ（本进程）+ Redis，两边立即一致

# 方法 2：启动前导出环境变量（写进 start-backend.sh 顶部）
export ROLLOUT_MODE=micro

# 方法 3（根治）：改代码，broker.py 三处 get_rollout_mode() 改为
#   await _guardrails.get_persisted_rollout_mode()
```

### 验证当前进程的工具层口径

```bash
# 无需登录（直读进程环境最准确）
ps eww -p $(lsof -nP -iTCP:8002 -sTCP:LISTEN -t | head -1) | tr ' ' '\n' | grep ROLLOUT_MODE
# 无输出 = env 没设 = 工具层认为是 shadow（AI 单不会执行！）
```

---

## 8. 故障排查速查表

| 现象 | 原因 | 处置 |
|---|---|---|
| AI_ANALYSIS 一直有，但从无 TRADE_OPENED | `AGENT_MODE=single`（分析师不下单） | `.env` 加 `AGENT_MODE=multi` → 重启后端 |
| 日志有 `rollout block:` / `TRADE BLOCKED` | AC-11：micro/live 需要 `LLM_ALLOW_LIVE=true` | `.env` 改 true → 重启；或 rollout 降为 paper |
| 日志有 `Shadow mode: order logged for review` | §7 重启坑：工具层 env 回落 shadow | 重设 rollout（§7 方法 1/2/3） |
| `bot_state=STOPPED`，收线后什么都没跑 | 后端重启后引擎不自动恢复 | 点启动 / `POST /api/bot/start?symbol=GOLD` |
| 策略模式无 SIGNAL_DETECTED | 引擎 STOPPED / Bridge 断连 / 市场收市 / 信号本就稀疏（ema_crossover ≈2~3.5 次/天） | 跑诊断脚本定位 |
| `MT5 Bridge unreachable ... trading paused` | Bridge/Windows 主机断连 | 引擎自动 PAUSED，恢复后自动 resume |
| 日志 `Redis trading_mode read failed (fallback ...)` | 远端 Redis 掉线 | 兜底值=上次生效模式；恢复后自动续用 |
| AI 单成交但仪表盘历史绩效没统计 | AI 订单不落 `trades` 表（`broker.py` 不写库） | 已知缺陷；看 MT5 终端/账户为准 |
| 想停掉一切 | — | `POST /api/bot/emergency-stop?symbol=GOLD`（平仓+停机） |

---

## 9. 紧急止损与回滚

```bash
# 1) 紧急停机：平掉 GOLD 全部持仓 + 引擎停止
curl -X POST 'http://localhost:8002/api/bot/emergency-stop?symbol=GOLD' -H "Authorization: Bearer $TOKEN"

# 2) 全局紧急刹车（所有品种）
curl -X POST 'http://localhost:8002/api/bot/emergency-stop' -H "Authorization: Bearer $TOKEN"

# 3) 立即冻结新单（AI 还在跑也不会成交）：rollout 降级
curl -X PUT http://localhost:8002/api/rollout/mode \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{"mode":"shadow"}'

# 4) 断 LLM（AI 完全失联，只剩策略引擎——若 trading_mode=strategy）
#    .env: LLM_BASE_URL 置空或指向不可达地址 → LLM 熔断器自动跳过

# 5) 保底：直接停后端（持仓仍留在 MT5，需手动处理）
./stop-all.sh
```

回滚优先级建议：`emergency-stop`（平仓）＞ rollout→shadow（冻结新单）＞ bot/stop（停引擎）＞ 停进程。

---

## 10. 每次切换后的统一验收清单

```bash
# ① 模式三件套
cd backend && .venv/bin/python scripts/diagnose_no_signal.py GOLD
#   重点看 §1/§2 两节的结论行 + VERDICT

# ② rollout 两端口径一致
curl -s http://localhost:8002/api/rollout/mode -H "Authorization: Bearer $TOKEN"
ps eww -p $(lsof -nP -iTCP:8002 -sTCP:LISTEN -t | head -1) | tr ' ' '\n' | grep ROLLOUT_MODE

# ③ 引擎在跑
curl -s http://localhost:8002/health | python3 -m json.tool
#   期望: mt5_connected=true, redis_connected=true, bot_state=RUNNING

# ④ 等一个 M15 周期，看决策
grep -E 'Orchestrator|AI agent \[GOLD\]|place_order|TRADE BLOCKED|Shadow mode' logs/bot.log | tail
```

---

## 11. 当前配置快照与建议（2026-09-17）

```
组合 B2：AI 自主 + 真实微单
  trading_mode  = ai_autonomous   (Redis)
  AGENT_MODE    = multi           (.env, 07:33 已加载)
  LLM_ALLOW_LIVE= true            (.env)
  rollout       = micro           (Redis + 需按 §7 重设 env)
  paper_trade   = true (GOLD)     ← 对 AI 无效；建议保持（防止引擎 trailing 干扰 AI 仓位）
  max_lot       = 0.1  (micro 下仍会被压到 0.01)
```

**待办**：① 启动机器人（STOPPED）；② 按 §7 重设一次 rollout；③ 已知缺陷待修：
AI 订单不落 `trades` 表、broker.py rollout 双口径、Telegram 通知 token 未配置。
# AI 自主交易止盈/止损调研报告

**日期**: 2026-09-20
**范围**: AI 自主交易（`trading_mode="ai_autonomous"`）及其共享的止盈止损（TP/SL）体系
**目的**: 调研止盈止损如何定义、为什么这么设置、有何依据

---

## 一、结论摘要

1. **AI 自主交易的 SL/TP 不是 AI 独立发明的逻辑**——它与策略引擎共用同一个计算核心 `RiskManager.calculate_sl_tp()`，统一采用 **ATR × 品种倍数**：默认 **止损 = 1.5×ATR、止盈 = 2.0×ATR**（隐含盈亏比 1.33:1）；BTCUSD 例外为 2.0/3.0（盈亏比 1.5:1）。
2. **方法学有明确依据**（ATR 定止损 = 把止损放在当前噪声尺度之外，业界先例为 Turtle 系统的 N 基准止损、Chandelier Exit），且有文档成文；但 **1.5/2.0 这两个具体数字找不到原始推导依据**——它是 2026-04-11 多品种提交（`1ea70f9`）不加说明带入的出厂默认。唯一佐证是文档里一句"外部评测"引文（GOLD 上 1.5×ATR 止损有 64% 概率 6 小时内被正常波动扫掉），该数据**只有文字引用、无原始评测文件，且被开发者版审计标记"需本系统复核"**。
3. **另有一套完全解耦的 ML 训练标签体系**（`ml_tp_pips`/`ml_sl_pips`，固定绝对点数），只用于训练打标签，**不控制实盘止盈止损**；两套口径不一致被项目自己登记为结构性缺陷 **D4**（修复排期 N6）。
4. 持仓期内还有一层动态管理：浮盈 0.5×ATR 移保本、1.0×ATR 起 Chandelier 移动止损、2.0×ATR 后收紧步进。

---

## 二、AI 自主交易里 SL/TP 是怎么定义的（机制）

### 2.1 执行链路

AI 自主模式下，交易由 agent 任务驱动（engine 的 `process_candle` 在 AI 自主模式下直接跳过，`backend/app/bot/engine.py:396-400`），通过 MCP 工具完成"算 SL/TP → 下单"：

```
calculate_atr → calculate_sl_tp → calculate_lot_size → place_order
                                                   ↓
                         order_preflight（券商手数网格/日亏熔断/rollout 模式）
                                    + guardrails（SL/TP 方向 sanity check）
```

- **谁算 SL/TP**：多 agent 编排里只有 Risk Analyst 拥有 `calculate_sl_tp`/`calculate_lot_size` 工具（`backend/mcp_server/agents/risk_analyst.py:44-53`），算完写进报告文本；唯一有下单权的 orchestrator 被 prompt 明令"使用 Risk Analyst 建议的手数和 SL/TP"（`backend/mcp_server/agents/orchestrator.py:93-99`、prompt L69）。
- **计算公式**（`backend/app/risk/manager.py:192-253`，注释写明"这是下单与确认门的唯一口径来源"）：

```
sl_distance = ATR × sl_atr_mult × regime_sl_factor        （sl_mode="atr" 默认）
             [可选 clamp 到 [sl_floor, sl_cap]]
tp_distance = ATR × tp_atr_mult × regime_tp_factor        （tp_mode="atr" 默认）
             [tp_mode="rr" 时: tp_distance = target_r_multiple × 实际止损距离]
BUY:  sl = 入场价 - sl_distance,  tp = 入场价 + tp_distance
SELL: 反向
```

- 市场状态（regime）系数（`backend/app/strategy/regime.py:72-98`）会把距离再缩放：

| 市场状态 | SL 系数 | TP 系数 | 语义 |
|---|---|---|---|
| 趋势 + 高波动 | ×1.3 | ×1.5 | 宽止损，让利润奔跑 |
| 趋势 + 低波动 | ×1.0 | ×1.2 | — |
| 震荡 | ×0.8 | ×0.8 | 窄止损窄止盈 |
| 正常 | ×1.0 | ×1.0 | — |

### 2.2 品种参数从哪来

- 出厂默认在 `backend/app/config.py:7-72` 的 `SYMBOL_PROFILES`：**GOLD / OILCash / USDJPY = 1.5/2.0，BTCUSD = 2.0/3.0**。
- DB 表 `symbol_configs`（`backend/app/db/models.py:459-468`，server_default 同为 1.5/2.0）会覆盖出厂值——**实际以数据库为准**（DB → 运行时配置加载：`backend/app/services/symbol_config_service.py:18-78`）。
- 表单约束仅 `0 < 倍数 ≤ 10`（`backend/app/api/routes/symbols.py:65-66`），系统不强制盈亏比。
- ⚠️ 实证案例：`docs/SIGNAL-DIAGNOSIS-2026-09-16.md:60,265` 记录线上 GOLD 曾被配成 `tp_atr_mult=5.0`（盈亏比 5:1），脱离出厂默认——**实际 AI 交易按 DB 值走，任何值都可能被运营改上去**。

### 2.3 SL/TP 与手数、风险的联动（ATR 法的自洽性核心）

手数公式（engine.py:936 调用手数计算）：`lot = 风险预算(默认 1%) ÷ (sl_distance + 滑点) ÷ contract_size`。

**止损越宽 → 手数越小 → 单笔亏损金额恒定**。ATR 法与此天然配套，这是它站得住脚的核心理由（详见 §4）。

### 2.4 持仓期的动态 SL/TP（ATR 系）

参数定义：`backend/app/constants.py:66-79`；实现：`backend/app/bot/engine.py:1503-1589`（`_apply_trailing_stops`）。

| 阶段 | 触发条件 | 动作 |
|---|---|---|
| 保本 | 浮盈 > 0.5×ATR（`BREAKEVEN_ATR_MULT=0.5`） | SL 移至入场价 |
| 部分止盈 | 浮盈 ≥ 1.0×ATR（`partial_tp_atr_mult`，`enable_partial_tp` 默认关） | 平仓重开半仓 |
| Chandelier 移动止损 | 浮盈 ≥ 1.0×ATR（`DEFAULT_TRAILING_START_ATR`）起 | SL = 现价 ∓ 仓位ATR × 0.5 步进（`DEFAULT_TRAILING_STEP_ATR`） |
| 利润锁定收紧 | 浮盈 ≥ 2.0×ATR（`PROFIT_LOCK_ATR_MULT`） | 步进收紧到 0.3×ATR（`TIGHT_TRAIL_STEP_ATR`） |
| 时间止损 | `max_position_duration_hours>0` | 超时强平（默认关闭） |

另有交易时段（session）系数：`SESSION_PROFILES`（config.py:139-145，`use_session_profiles` 默认 False）：asian 1.2/1.5、london 1.5/2.0、overlap 1.8/2.5、ny 1.5/2.0、off 1.0/1.2。

### 2.5 AI 路径的防线（与策略引擎路径不同）

- **策略引擎路径**有 `ConfirmationGate`（`backend/app/ai/confirmation_gate.py:144`，其中一票要求 **R:R ≥ 1.5**）把关；
- **AI 路径没有声比门**，只有 `backend/mcp_server/guardrails.py:196-217` 对 SL/TP 做**方向合法性**校验（SL>0、BUY 时 SL<入场价<TP、SELL 反向）——**不强制 AI 走 ATR 公式**。若 orchestrator 不遵守 Risk Analyst 建议，AI 理论上可附带任意 SL/TP。这是 AI 通道在风控口径上的一个软肋。
- 下单统一先过 `order_preflight`（`backend/app/services/order_preflight.py:117-305`）：券商手数网格归一、日亏/连亏熔断、rollout 模式（shadow 仅记录 / paper 模拟 / micro·live 要求 `settings.llm_allow_live=True`）。

---

## 三、ML 训练标签的口径（另一套体系）

- `ml_tp_pips`/`ml_sl_pips`：**固定绝对点数**（非波动率动态），× `pip_value` 得价格距离（`backend/app/ml/features.py:185-259` 的 `build_labels`）。出厂值及注释依据（config.py）：

| 品种 | ml_tp/ml_sl_pips | pip_value | 价格距离 | 注释依据 |
|---|---|---|---|---|
| GOLD | 10.0 / 10.0 | 1.0 | $10 | `# ~$10 move on XAUUSD ~$3,000` |
| OILCash | 0.5 / 0.5 | 10.0 | $5 | `# ~$0.50 move on WTI ~$70` |
| BTCUSD | 500.0 / 500.0 | 1.0 | $500 | `# ~$500 move on BTC ~$100,000`；`ml_forward_bars=5`（"BTC moves fast"）、`ml_timeframe="H1"` |
| USDJPY | 0.3 / 0.3 | 100.0 | 30（≈30 pips） | `# ~30 pips on USDJPY ~145` |

  即注释给的是"该品种常见波动的数量级"，**没有更严格的推导**。

- **`ml_sl_pips` 是死参数**：`features.py:220` 明示 `sl_pips` 惰性（"reserved for meta-labeling — barriers stay symmetric on purpose"），对称双屏障 + 同根 K 线双触标记 HOLD（`build_labels` 长 docstring L190-219 解释了为何对称：非对称会注入 sl/(tp+sl) 的标签先验；为何同 bar 双触=HOLD：OHLC 无法知 intra-bar 顺序）。
- 合理性护栏（`backend/app/ml/barrier_validation.py:18-23`）：屏障距离 / 单根 K 线平均波幅 `mean(high-low)` 的比值——推荐 **0.5–1.5×**、告警 0.3–3×、硬拒 [0.15, 6.0]×；训练端配套诊断 `_barrier_diagnosis`（trainer.py:54-115，含 BTCUSD 实测：500 ≈ 0.96× 波幅 → BUY 34% / HOLD 29% / SELL 37%）。
- 事故实证：BTCUSD 曾配 `ml_tp_pips=15`（≈0.03× 波幅）→ HOLD 坍缩 → 训练产出恒 HOLD（见记忆栈 ml-train-hold-missing）。
- 标签**不落库**，只在训练时即时计算（定时重训 `scheduler.py:985-1001` 每周一 04:00 UTC、手动 API `api/routes/ml.py:126-147`）；与实盘 ATR 口径**无任何联动** → 结构性缺陷 **D4**（`docs/SYMBOL-PARAMETERS-TECH.md:116`）。

---

## 四、"为什么这么设置"——依据核查

### 4.1 有依据的部分（方法论层，`docs/SYMBOL-PARAMETERS.md` §3/§5/§6）

- **§6.1 用 ATR 定止损的理由**：止损永远放在当前噪声尺度之外——平静时自动收紧、剧烈时自动放宽、跨品种可比；且与手数公式配套，单笔亏损金额恒定。点名 **Turtle 系统的 N 基准止损、Chandelier Exit** 为业界惯例（量化圈公认做法，非本项目原创）。
- **盈亏比口径** = `tp_atr_mult ÷ sl_atr_mult`（ATR 约掉）；出厂 1.5/2.0 即 1.33:1。文档同时给出**运营者推荐 5:1 配置（1.5/7.5）** 及代价：盈亏平衡胜率 = 1/(1+R) = **16.7%**——即出厂默认 1.33:1 需 43% 胜率盈亏平衡，而 5:1 只需 16.7%。
- regime/session 系数、动态止盈止损参数（0.5/1.0/2.0 ATR）名称与语义均有清晰定义，属"波动率自适应"设计的细化。
- 回测与实盘同口径（`backend/app/backtest/risk_factory.py:21-47` 从 `SYMBOL_PROFILES` 读取同一配置，模块 docstring 记载此前 8 处裸 `RiskManager(...)` 默认值不一致问题已修复为单入口）。

### 4.2 依据存疑的部分（具体数值层）

- **§6.2 / 审计缺陷 D1**：出厂 1.5×ATR 可能过窄——文引"外部评测"GOLD 上 **1.5×ATR 止损 6 小时内被正常波动扫掉的概率 64%，2.0×=53%，3.0×=36%**。但①原始评测文件不在仓库；②开发者版审计 D1 标注"**需本系统复核**"。
- **出厂默认 1.5/2.0 的原始推导：未找到**。git 历史显示自 2026-04-11 `1ea70f9`（"Add multi-symbol trading support"）起无注释带入；2026-09-14 `322f123`（新增 clamp/R 模式，migration `w3x4y5z6a7b8`）也只解释新模式、未溯因旧默认。
- **BTCUSD 为何独用 2.0/3.0**：同样无文字说明（只能推断为比特币波动特性，但无依据可引）。
- **默认 1.33:1 与文档推荐 5:1 不一致**：文档把 5:1 当运营建议，系统默认却停在 1.33:1——中间缺乏"默认到底该取多少"的定论。
- `.env.example` 无任何 sl_atr/tp_atr 环境变量——SL/TP 完全以 DB `symbol_configs` 为准，出厂默认只作首次建库种子。

### 4.3 与设置直接相关的已知风险

| 编号 | 问题 | 出处 |
|---|---|---|
| D1 | 止损过窄（1.5×ATR，64% 被扫概率为外部引文、待复核） | `SYMBOL-PARAMETERS-TECH.md` L113 |
| D4 | 训练固定屏障 vs 实盘 ATR 倍数，口径不一致 | `SYMBOL-PARAMETERS-TECH.md` L116 |
| D5 | 移动止损使用入场时的陈旧 ATR | `SYMBOL-PARAMETERS-TECH.md` |
| — | AI 路径 guardrails 只查方向合法性，不强制 ATR 公式（无声比门） | `mcp_server/guardrails.py:196-217` |
| — | 线上 DB 值可大幅偏离出厂（GOLD tp=5.0 实例） | `SIGNAL-DIAGNOSIS-2026-09-16.md:265` |
| N5 | OIL/USDJPY 出厂 `pip_value` 通不过自家校验器，影响滑点缓冲与 ml 标签换算 | `SYMBOL-PARAMETERS-TECH.md` A10 |

---

## 五、关键代码/文档索引

| 用途 | 位置 |
|---|---|
| SL/TP 唯一计算实现（默认 1.5/2.0） | `backend/app/risk/manager.py:192-253`（默认值 L71-72） |
| 策略引擎下单调用点 | `backend/app/bot/engine.py:927,965`（`_size_and_place_order`） |
| 策略引擎确认门（R:R≥1.5 软票） | `backend/app/ai/confirmation_gate.py:142-155` |
| AI 通道 MCP 工具 | `backend/mcp_server/tools/risk.py:79-106`、`backend/mcp_server/server.py:104-130` |
| AI 下单硬闸门 | `backend/app/services/order_preflight.py:117-305` |
| SL/TP 方向校验 | `backend/mcp_server/guardrails.py:196-217` |
| 品种出厂/会话系数 | `backend/app/config.py:7-72,139-145` |
| DB 表与迁移 | `backend/app/db/models.py:459-468`、`alembic/versions/q7r8s9t0u1v2`、`alembic/versions/w3x4y5z6a7b8_add_sl_tp_modes.py` |
| ML 标签与护栏 | `backend/app/ml/features.py:185-259`、`ml/barrier_validation.py:18-23`、`ml/trainer.py:54-115` |
| 移动止损/保本/部分止盈参数 | `backend/app/constants.py:66-79`、`bot/engine.py:1503-1589` |
| 方法学与依据文档 | `docs/SYMBOL-PARAMETERS.md`（§3/§5/§6）、`docs/SYMBOL-PARAMETERS-TECH.md`（D 组/N 组） |

---

## 六、一句话总结

AI 自主交易的止盈止损在机制上是 **ATR × 品种倍数** 的统一口径（默认 SL=1.5×ATR、TP=2.0×ATR），方法学有成熟的业界依据（Turtle / Chandelier + 与手数联动的恒定单笔风险）；但**默认数值 1.5/2.0 本身没有本仓库内的推导或回测支撑**，唯一引用的外部评测数据（64% 被扫概率）既无原始文件、也未经本系统复核——这是该设置最需要补强实证的地方。
# Findings — Laya 请求参数正确性审计（对照 QuantDinger JEV）

> 日期：2026-09-22 ｜ 分支：feat/laya-research-and-integration
> 范围：逐接口核对 laya 请求参数是否正确/合理、行情数据覆盖、能否判断出想要结果；
> 参考基线：QuantDinger 对 JEV（TypeSafe System One）的 Decision Context V2 调用。
> 只读审计：未改任何代码。证据来源 = 源码核实 + 2026-09-22 真实数据/模型回放实验。

---

## 0. 结论速览（TL;DR）

1. **结构层全部正确**：三个接口的白名单校验、confidence 重定义、畸形回落、fail-closed 降级链都没有问题。
2. **证据层不完整**：6 问闸门两条路径（ManualGate / engine）的证据面都远薄于 JEV；且
   `render_laya_state_prose` 是**按 engine 快照形状写的**，ManualGate 快照走 prose 时
   点差/买卖价/情绪/净值/日盈亏/SL/TP/lot 等字段**键不匹配被静默丢弃**（关键缺陷）。
3. **有行情数据的接口**：只有 6 问闸门有；情绪/策略两接口无行情（策略属纯文本任务，合理；
   情绪缺少价格/时间基准，且 3000 字符在 512 token 硬上限下实际只喂头部）。
4. **能否判断出想要结果**：实测不能。2026-09-22 回放：0.6 阈值 100% ESCALATE；prose 后
   data_quality sufficient 61% 但各 horizon 方向命中不显著（p≈0.2-0.8）；探针 P3 强多头+SELL
   被错误放行；阈值扫描/问句重写均救不了。**瓶颈是未微调模型能力，不是数据量**；但当前输入
   连「模型能读到的完整证据」都不是（见 §2.3 键不匹配）。
5. **优化顺序**：Phase 0 修渲染键不匹配 → Phase 1 补证据面（对齐 JEV，零额外 I/O） →
   Phase 2 多周期/问句/收敛对照实验 → Phase 3 模型面决策（微调 / 换托管 JEV / 放弃 6 问）→
   Phase 4 观测与上线门。详见 task_plan.md（**需用户批准后执行**）。

---

## 1. JEV 参考基线：QuantDinger Decision Context V2

来源：`/private/tmp/QuantDinger/backend_api_python/app/services/ai_decision_context.py`
+ `ai_decision_filter.py`（JEV_QUESTIONS）+ `README_CN.md`「JEV 驱动的交易前决策」。

### 1.1 JEV state 结构（`build_strategy_decision_context`）

```jsonc
{
  "context_version": 2,
  "strategy": { "name", "timeframe", "direction_mode", "parameters": {...} },
  "market_evidence": {
    "captured_at_utc", "market", "symbol", "exchange_id", "market_type",
    "primary_timeframe", "requested_timeframes", "available_timeframes",
    "fresh_timeframes", "stale_timeframes", "data_quality",
    "timeframes": { "<tf>": {
        "timeframe", "available", "bars", "latest_bar_time_utc", "data_age_seconds",
        "interval_seconds", "stale_after_seconds", "is_stale", "last_close",
        "reference_price_deviation_pct", "return_1_bar_pct", "return_5_bar_pct",
        "return_20_bar_pct", "trend", "ma5", "ma10", "ma20", "rsi14", "rsi_state",
        "macd_state", "macd_histogram", "atr14", "atr14_pct", "volume_ratio_20",
        "price_position_20", "support", "resistance"
    } }
  },
  "portfolio_risk": {
    "position_count", "gross_notional", "unrealized_pnl",
    "positions": [{"symbol","side","size","entry_price","current_price","unrealized_pnl"}],
    "strategy_equity", "initial_capital", "drawdown_from_initial_pct",
    "entry_percent", "gross_exposure_pct"
  },
  "strategy_performance": {
    "today_realized_pnl", "lifetime_realized_pnl", "completed_exits",
    "recent_exit_pnl": [...], "consecutive_losses"
  },
  "protection": { "take_profit_price", "stop_loss_price", "margin_mode" },
  "order_budget": {...}
}
```

- 每次调用拉 **2-3 个周期**（primary + 上两级 ladder，`_decision_timeframes`），每周期约 25 个字段。
- 有**数据新鲜度**（data_age_seconds / is_stale / 全市场 data_quality）供 execution 问句。
- 有**敞口/回撤/连续亏损**供 risk 问句；有 **TP/SL/预算**供保护判定。

### 1.2 JEV 6 问 vs 本项目 6 问（同源）

JEV_QUESTIONS 与本项目 `LAYA_GATE_QUESTIONS` 的 6 个 key 完全一致：
`data_quality / signal_alignment / market_regime / risk_check / execution_quality / entry_decision`，
instructions 与候选集也几乎逐字一致（本项目显然是按 JEV 契约改写的）。
**差别只在 state 证据面**：JEV 喂了上面整棵 Context V2；本项目只喂 6-8 个标量或实时报价。

---

## 2. 三入口逐参数审计

### 2.1 情绪预筛 `laya_sentiment_choice`（news_sentiment.py → laya_runtime.py:287）

| 项 | 现状 | 评价 |
|---|---|---|
| state | `{symbol, headlines[:3000]}` | 结构正确；无行情/无时间上下文 |
| question | 3 分类 bullish/bearish/neutral，criteria 含 "price expected to rise/fall" | 任务自足，但 criteria 承诺了价格方向语义却无价格基准 |
| 白名单/阈值 | SENTIMENT_LABELS；0.85 max-class（调用方） | 正确 |
| 截断 | `headlines[:3000]` 字符截断 | **问题**：laya 硬上限 512 token（head ≤192，state 只占余量），3000 字符实际只喂头部 ~≤350 token，尾部不可见 |
| 行情 | 无 | 对"纯新闻情绪"分类非必需；但对"新闻→涨跌"方向判读缺基准 |
| 可判定性 | 未用真实数据评估过该通道 | 无证据；typed-decisions 0.36 表明未微调模型分类能力弱 |

**结论**：参数结构正确、降级正确；证据不完整（截断浪费 + 无时间/价格基准）。优先级低。

### 2.2 策略抽取 `laya_strategy_choice`（laya_runtime.py:341）

| 项 | 现状 | 评价 |
|---|---|---|
| state | `{decision[:3000]}` | 合理——纯文本分类任务，行情不需要 |
| question | 6 类（trend_following/mean_reversion/breakout/momentum_rank/hold/ai_autonomous） | 合理；momentum_rank 与 breakout、trend_following 语义有重叠，小模型易混淆 |
| 白名单/阈值 | STRATEGY_LABELS；0.6 | 正确；keyword 兜底兜得住 |
| 截断 | `decision[:3000]` | 同 512 token 截断问题（但决策文本通常 <512，影响小） |
| 可判定性 | 未用真实数据评估过该通道 | 无证据；失败降级安全 |

**结论**：三个接口里最合理的；风险低。优先级低。

### 2.3 6 问闸门 `laya_gate_review`（laya_gate.py:327）

state 有两条来源，字段差异很大：

| 快照字段 | ManualGate 路径（manual_order_gate.py:669 `_build_snapshot`） | engine 路径（laya_engine_observation.py:137 `build_laya_engine_snapshot`） |
|---|---|---|
| order | review_id/symbol/type/lot/**sl**/**tp** | signal/signal_label/symbol/timeframe/side（**无 lot/sl/tp**） |
| account | balance/equity/floating_profit/realized_daily_pnl | balance/positions_count/daily_pnl/recent_win_rate |
| positions | {symbol,type,lot,profit} | {symbol,type,volume,profit}（≤5，无 entry/current 价） |
| recent_trades | deals[:10]（含 profit/lot/time） | **恒空 []**（用 win_rate 摘要代替） |
| rule_flags | 情绪化规则旗标（含 no_stop_loss 等） | **恒空 []** |
| market | bid/ask/spread/avg_spread/sentiment（实时报价） | last_close/change_1_pct/change_5_pct/vol_14/range_position_50/price_vs_sma9/price_vs_sma21 + symbol/timeframe（M15 摘要） |

#### ⚠️ 关键缺陷：prose 渲染与 ManualGate 快照键不匹配

`laya_state_prose=True`（默认）时，`laya_gate_review` 用 `render_laya_state_prose(snapshot)`
渲染 state。该渲染器只读以下键（laya_gate.py:248-324 源码核实）：

- account：`balance` / `positions_count` / `daily_pnl` / `recent_win_rate`
- market：`last_close` / `change_1_pct` / `change_5_pct` / `vol_14` / `range_position_50` / `price_vs_sma9` / `price_vs_sma21`
- order：`side` / `symbol` / `timeframe` / `signal`（**不含 lot/sl/tp**）
- positions/recent_trades 各 ≤4 条压缩、rule_flags ≤8

而 ManualGate 快照的键是 `equity/floating_profit/realized_daily_pnl`、`bid/ask/spread/avg_spread/sentiment`、
`order.lot/sl/tp` —— **全部不匹配被静默丢弃**。后果（ManualGate 路径、prose 默认开）：

- 喂给模型的实际证据 ≈ 订单方向/符号/周期 + balance + 持仓压缩 + 规则旗标；
- **equity/浮盈/日盈亏、点差/买卖价/情绪、SL/TP/lot 全部丢失**；
- 而 6 问里 `risk_check` 明确要求评估 "stop-loss presence and protection"、
  `execution_quality` 明确要求评估 "price freshness, spread, order type" —— 这些问句在该路径上**无对应输入**。

即：文档 §3.3 写的「state = snapshot 子集原样」在 prose 模式下不成立，实际是
「engine 快照形状专用渲染」。

#### 每问证据充足性（两路径 × 6 问）

| question | ManualGate 路径 | engine 路径 | JEV 参考 |
|---|---|---|---|
| data_quality | 可答（但无新鲜度元数据，只能靠直觉） | 可答（同左） | 有 data_age/is_stale/data_quality |
| signal_alignment | **不可答**（无趋势/动量证据，只有点差+情绪；prose 下连点差都丢） | 部分可答（8 标量可粗判趋势） | 多周期趋势+指标+方向模式 |
| market_regime | **不可答**（无趋势/波动/成交量证据） | 部分可答（有波动/区间位置；无成交量/指标态） | RSI/MACD/ATR/volume/支撑阻力+多周期 |
| risk_check | 部分可答（lot/balance/positions/rule_flags/deals 在 JSON 下有；prose 丢 lot/sl/tp/equity） | **不可答**（无仓位大小/止损/敞口/回撤/连续亏损） | 敞口/回撤/连续亏损/entry_percent 全有 |
| execution_quality | 部分可答（JSON 下有 spread/bid/ask；无新鲜度；prose 丢） | **不可答**（无任何执行数据） | data_age/is_stale/reference deviation |
| entry_decision | 依赖上 5 问 → 不可靠 | 不可靠（实测近似抛硬币） | 全证据支撑 |

#### 缺失证据总清单（对照 JEV，8 项）

1. 多周期（H1/H4）—— 本项目 regime.py 已有 M15/H1/H4 拉取能力，但未进 laya state；
2. 成交量（volume_ratio）—— build_market_summary 未算；df 是否有 tick_volume 待验证；
3. 指标态（RSI/MACD/ATR/MA5/10/20/支撑阻力）—— `build_features`（ml/features.py）已有 40+ 特征能力，未用于 laya；
4. 数据新鲜度（latest_bar_time_utc/data_age_seconds/is_stale）—— df index 可零 I/O 算，未提供；
5. 敞口/净值/回撤（gross_exposure_pct/drawdown/equity）—— 未提供；
6. 连续亏损/近期成交盈亏（consecutive_losses/recent_exit_pnl）—— engine 路径 recent_trades 恒空；
7. 订单 lot/SL/TP —— engine 观察点（`_check_trade_permission`）无此信息，ManualGate 有但 prose 丢；
8. 策略参数/信号理由 —— 只有 signal/signal_label，无 direction_mode/parameters。

**结论**：6 问闸门是问题核心。结构正确，但两路径证据都「喂不全」，且 ManualGate prose 路径
存在真实键不匹配缺陷。优先级高。

---

## 3. "丢给 laya 能否判断出想要结果" —— 实测证据（2026-09-22）

来自 `.planning/2026-09-22-laya-mt5/` 回放（360 样本 × A/B、240 × C1/C2、真实模型隔离 venv）：

- 生产 JSON 快照（A）：data_quality sufficient 0/60；**0.6 阈值 100% ESCALATE**（各问 max-class 概率 0.28-0.58，近均匀）。
- prose（C1，240 样本）：sufficient 147/240（61%）、ECE(entry,2h) 0.068；但方向命中 vs 基率
  2h 49.0% vs 47.5%（p=0.81）、6h 60.2% vs 53.3%（p=0.25）、1d 57.1% vs 51.2%（p=0.33）→ **无统计显著技能**。
- 增强输入（B：+H1 摘要/ATR/RSI/近 12 根收盘）：判定分布零变化（全 ESCALATE），逐问被推向
  signal_alignment 100% conflict、entry_decision 100% reject → **加数据在未微调模型上无效**。
- 能力探针：P1 强多头+BUY → conflict(0.347)/pass；P2 强空头+SELL → conflict/adverse/reject（long-only 偏差）；
  P3 强多头+SELL（应拒）→ **pass（错误放行）**。对齐感知损坏、多空理解有偏差、entry 近似抛硬币。
- 阈值扫描：任何阈值下 APPROVED 不可达（依赖 signal_alignment=aligned，模型从不输出）；
  REJECTED 命中 0.43-0.47 ≈ 基率 0.475 → veto 零预测价值。
- 问句重写（80 样本+3 探针）：market_regime 全 adverse、entry BUY 全 pass/SELL 全 reject → 救不了。

**结论**：
- 当前 laya（未微调）**无法完成交易判定**；输入完整化是必要条件但不是充分条件。
- 格式（JSON→prose）是唯一实测有效的改进（已落地）；阈值/问句调优无效。
- 但即便要复测"微调后"效果，也须先把证据喂全（Phase 0/1），否则微调/评估都建立在不完整输入上。

---

## 4. 逐接口参数正确/合理评分

| 接口 | 结构正确性 | 证据完整性 | 任务匹配 | 可判定性 | 优先级 |
|---|---|---|---|---|---|
| sentiment | ✅ | ⚠️ 60%（截断浪费、无时间/价格基准） | ✅ 轻任务 | ❓ 未评估 | 低 |
| strategy | ✅ | ✅ 80%（纯文本够用） | ✅ | ❓ 未评估 | 低 |
| gate ManualGate | ✅ | ❌ ~50%（prose 键丢失 → 实际 ~35%） | ❌ 缺趋势/新鲜度 | ❌ 实测不能 | 高 |
| gate engine | ✅ | ❌ ~45% | ❌ 缺执行/敞口/止损 | ❌ 实测不能 | 高 |

---

## 5. 参考资料

- QuantDinger：`/private/tmp/QuantDinger/backend_api_python/app/services/ai_decision_context.py`、
  `ai_decision_filter.py`（JEV_QUESTIONS）、`README_CN.md`（JEV 决策流程图、Decision Context V2）
- 本项目：`backend/app/ai/laya_runtime.py`、`laya_gate.py`、`laya_engine_observation.py`、
  `backend/app/services/manual_order_gate.py:669`、`backend/app/strategy/regime.py`（M15/H1/H4 拉取）、
  `backend/app/ml/features.py`（40+ 特征）、`docs/LAYA-REQUEST-RESPONSE-SPEC.md`
- 既有回放证据：`.planning/2026-09-22-laya-mt5/findings.md`、`deliverables/laya-data-audit.md`

---

## 6. Phase 0/1 落地记录与 512-token 实测（2026-09-22 追加）

### 6.1 已实施的代码改动（用户批准后执行）
- `laya_gate.render_laya_state_prose`：ManualGate 快照键兼容（order.lot/sl/tp/type、
  account.equity/floating_profit/realized_daily_pnl、market.bid/ask/spread/avg_spread/sentiment）、
  dict 形态 rule_flags（取 detail/flag，截断 80 字符）、positions/trades 支持 lot 与 entry/current。
- `laya_engine_observation.build_market_summary(df, timeframe)`：+ma5/10/20、rsi14、atr14+atr_pct、
  macd_state+macd_histogram、support/resistance、volume_ratio_20（tick_volume/volume 兼容）、
  latest_bar_time_utc/data_age_seconds/is_stale（df index + timeframe 算，零 I/O）。
- `laya_engine_observation.build_laya_engine_snapshot`：+`context_version: 2`、
  `recent_profits → recent_exit_pnl[:10] + consecutive_losses`；`_compact_position` 归一化
  entry/current 价（兼容 price_open/price_current）。
- `engine.py`：把已预取的平仓盈亏 `recent_profits` 透传观测器（复用同一只读查询，零额外 I/O）。

### 6.2 512-token 实测（真实 laya tokenizer + build_sequence，逐问验证）
验证脚本：`/tmp/verify_laya_tokens.py`（用 laya 0.3.4 的 `build_sequence(tok, state, q, 512, 192)`）。

| 快照 | 渲染字符 | state token | 最坏 head room | 最长完整序列 | 截断? |
|---|---|---|---|---|---|
| engine-max（5 持仓+全指标+新鲜度） | 851 | 343 | 413（risk_check） | 441 | 否 |
| manual-max（4 持仓+4 成交+8 旗标+报价+指标） | 877 | 364 | 413（risk_check） | 462 | 否 |

- 6 问全部 `opts_ok=True`（选项无丢弃）；全部 `state_truncated=False`；最长完整序列 441/462 < 512。
- 护栏：`_PROSE_BUDGET_CHARS=900`（≈350-375 token < room 413，留 ~40 token 余量）；
  超预算先丢 flags→trades→positions 行，再硬截断（保 order/account/market 核心证据）。

### 6.3 C3/C4 全量（n=240，seed 42，真实模型；与 C1 同 240 个 idx 配对）

| 变体 | 0.6 阈值判定 | data_quality | market_regime | risk_check | execution_quality | entry_decision | ECE(entry,2h) |
|---|---|---|---|---|---|---|---|
| c1（原 prose） | 100% ESCALATE | sufficient 147/partial 93 | favorable 237 | clear 240 | clear 240 | pass 98/reject 142 | 0.068 |
| c3（Phase 1 增强 prose） | 100% ESCALATE | **partial 240** | **adverse 228** | block 29/caution 2 | block 108/caution 4 | pass 167/reject 73 | 0.076 |
| c4（c3+H1） | 100% ESCALATE | sufficient 70/partial 129/insufficient 41 | adverse 189 | block 108 | block 108 | **reject 219/pass 21** | 0.133 |

**C3 vs C1 配对翻转（同 idx）**：data_quality 147/240、market_regime **225/240**、
execution_quality 112/240、entry_decision 163/240、risk_check 31/240、signal_alignment 5/240。

**方向技能（entry_pass 命中率 vs 基率，单侧 exact p）**：
| 变体 | 2h | 6h | 1d |
|---|---|---|---|
| c1 | 49.0% vs 47.5% (p=0.42) | 60.2% vs 53.3% (p=0.10) | 57.1% vs 51.2% (p=0.14) |
| c3 | 49.1% vs 47.5% (p=0.37) | 51.5% vs 53.3% (p=0.71) | 48.5% vs 51.2% (p=0.79) |
| c4 | 57.1% vs 47.5% (n=21, p=0.25) | 61.9% vs 53.3% (n=21, p=0.29) | 57.1% vs 51.2% (n=21, p=0.38) |

**结论（统计级）**：
- 证据面完整化（Phase 1）**没有**给未微调模型带来方向技能：全部 p≥0.10，C3 的 6h/1d 甚至略低于基率；
- 反而把逐问判定打乱（market_regime 225/240 翻转、data_quality 从 sufficient 147 跌到 0）——
  模型对"证据足不足"的判定不随证据量变化，只随文本形态抖动 → **输入面改动解决不了模型能力问题**；
- C4（+H1）只是更保守（entry reject 219/240），pass 样本只剩 21 个，无统计意义；
- 结论强化：Phase 3（领域微调 / 托管 JEV / 放弃 6 问）仍是唯一解锁点；
  Phase 0/1 的价值 = 证据确实送进模型（512 token 内不截断）+ 为微调后的复测提供正确输入基线。

### 6.4 Phase 3-A 本地微调（head-only，合成标签）执行与复测（2026-09-22）

**训练**（`backend/scripts/laya_finetune.py`；600 行 × 3 epochs，head-only，26.5M 可训练参数，
CPU ~19s/step，总 ~98 min；产出 `backend/models/laya_ft/`，`laya.load` 实测可加载）：

| 阶段 | val_loss | val_acc | dq | sa | regime | risk | exec | entry |
|---|---|---|---|---|---|---|---|---|
| base | 1.3281 | 0.3375 | 0.20 | 0.20 | 0.385 | 0.235 | 0.52 | 0.485 |
| epoch 3 | 0.7317 | 0.6617 | 0.845 | 0.64 | 0.62 | 0.47 | 0.96 | **0.435** |

- `entry_decision` 校验精度停在 0.435（低于 base 0.485）——标签=6h 未来方向，信息上不可约，
  训练只能逼近多数类 → 标签上限即能力上限的第一个信号。

**FT vs base 复测**（n=240，seed 42，c5 生产 prose 变体，同 240 idx 配对，阈值 0.6）：

| 指标 | base | FT | 判定 |
|---|---|---|---|
| verdict（th=0.6） | 100% ESCALATE | 100% ESCALATE | 无可用判定 |
| entry_pass 2h | 47.3% vs 基率 47.5% (p=0.55) | 47.6% vs 47.5% (p=0.51) | 无方向技能 |
| entry_pass 6h | 53.1% (p=0.55) | 52.8% (p=0.59) | 无 |
| entry_pass 1d | 51.1% (p=0.55) | 50.0% (p=0.67) | 无 |
| 命中集合（2h/6h/1d） | — | 与 base 逐根相同（both=114/128/123，McNemar p=1.0） | 零变化 |
| ECE(entry,2h) | 0.1615 | 0.0676 | 校准改善但非技能 |
| 阈值扫描 | 0.3→REJECTED 3 | 0.3→ESCALATE 48/REJECTED 189/CAUTION 3 | 低阈值出封锁非放行 |

**逐问标签翻转（FT 学会了合成规则，非市场规律）**：
- data_quality: base partial 237/sufficient 3 → FT sufficient 239/partial 1（翻转 236/240）
- signal_alignment: base conflict 166/aligned 45 → FT aligned 228/conflict 9（翻转 186/240）
- risk_check: base clear 225/block 11 → FT block 232/caution 6（翻转 230/240）
- entry_decision: base pass 239/reject 1 → FT pass 212/reject 28（翻转 29/240）
- 翻转方向完全对齐合成标签规则（dq=新鲜度/数据可得、sa=change_5/sma21 对齐、risk=保守封锁、
  exec=clear）→ 模型记忆的是我们编码的规则，而不是市场的可预测性。

**结论（统计级）**：
- head-only 微调 + 合成标签**没有解锁方向技能**：三周期 entry_pass 全部 p≥0.51 且命中集合与 base
  逐根一致（p=1.0）；判定在运维阈值 0.6 下仍 100% ESCALATE，闸门保持 fail-closed；
- ECE 改善（0.162→0.068）说明模型对"规则答案"更自信了，但规则本身无超额收益；
- 根因：合成标签（signal 用 change_5/sma21、entry 用未来 6h 方向）是可学习的**确定规则**，
  且规则与未来价格的相关性≈基率 → 微调只是把基率搬进标签空间，能力上限被标签上限锁死；
- 回到 Phase 3 决策：**建议 B（托管 JEV，与 QuantDinger 同源、已校准）或 C（放弃 6 问判定，
  降级为情绪/策略通道）**；若继续微调，唯一有信息量的路径是真实成交盈亏/风控标签 + 大样本 +
  GPU 全参/深参（超出本机算力与当前验收范围，且需先有真实标签标注管线）。

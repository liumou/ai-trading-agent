# Findings — Laya 输入数据量与真实 MT5 成功率调研

## 1. 调用 laya 时实际喂的数据（2026-09-22 代码核实）

### 通道 A：情绪三分类（laya_runtime.laya_sentiment_choice）
- state = `{"symbol": "GOLD", "headlines": headlines[:3000]}`
- 只有 3000 字符以内的新闻标题；**无行情、无账户、无时间上下文**。

### 通道 B/C：6 问闸门（laya_gate.laya_gate_review / laya_engine_observation.build_laya_engine_snapshot）
- `order`: signal / signal_label / symbol / timeframe / side（5 字段）
- `account`: balance / positions_count / daily_pnl / recent_win_rate（≤4 字段，recent_win_rate 可空）
- `positions`: ≤5 条，每条压缩为 symbol/type/volume/profit（H1 修复后兼容 dict）
- `recent_trades`: **engine 路径恒为空列表**（用 recent_win_rate 摘要代替，观测零额外 I/O 约束）
- `rule_flags`: 恒为空列表
- `market`: 确定性预计算摘要，**≤8 个标量**：
  last_close / change_1_pct / change_5_pct / vol_14 / range_position_50 /
  price_vs_sma9 / price_vs_sma21 + symbol/timeframe
  - 来源：engine 已取的 M15 df（`DEFAULT_OHLCV_BARS`，调用点显示 200/100/60 不等），
    `build_market_summary` 只取 close 序列算近 1/5 根涨跌、14 根波动率、50 根区间位置、SMA9/21 比值。
  - **未喂**：原始 OHLCV、成交量、H1/H4 多周期（regime.py 里有 M15/H1/H4 三周期拉取，但未进 laya state）、
    RSI/EMA/ATR 等指标、Spread/执行条件实时值（execution_quality 问句无对应输入）。

## 2. 数据充分性初步判断（待 Phase 3 实验证实）
- 8 个标量摘要 vs QuantDinger Decision Context V2（多周期行情+指标+持仓+敞口+净值+回撤+保护+执行条件）
  相比明显偏薄；`execution_quality` 问题无价差/滑点输入，只能靠模型先验。
- 情绪通道无行情上下文，判断“新闻 → 黄金涨跌”缺价格基准。

## 3. 成功率测试现状
- **laya 本体（6 问闸门）基于真实 MT5 数据的成功率测试：未做过。**
- 已有证据：
  - LightGBM AUC=0.739（合成 34,542 条，laya_synth_baseline.py）——基线模型非 laya；
  - laya 未微调（外部：typed-decisions 0.36≈随机；JEV 是托管校准模型）；
  - ECE 0.466 过自信（旧调研记录，待复核）；
  - 单测全 mocked；_laya_api_verify.py 仅 1 个情绪样本的 API 兼容验证。
- 观测期（4 周 / ≥150 样本，laya_engine_observations 专表）刚启动，尚无足量样本。

## 4. 真实数据可用性（执行 Phase 1 前预查）
- Postgres `ohlcv_data`：GOLD M15/H1 历史（MT5 collector 写入；只读加载用例已存在
  `laya_synth_baseline.py::load_from_db`，default_transaction_read_only 硬保证）。
- MT5 Bridge：`get_ohlcv_range(symbol, timeframe, from, to)` 可拉历史（在线可用性待冒烟）。
- 真实 trades ~10-30 笔 → 盈亏口径无统计效力，成功率需用一致率/方向准确率口径。

## 5. 提高空间候选（Phase 3 对照验证）
- 数据面：+ 多周期摘要（H1/H4）、+ 更长窗口、+ recent_trades/rule_flags 实值、
  + build_features 40+ 特征快照、情绪通道 + 行情上下文。
- 校准面：温度校准（ECE 0.466 过自信）；阈值面：min_confidence 扫描。
- 模型面：领域微调（RLCD/synthetic）边际收益须 > 确定性门控。

## 6. 数据格式审计（2026-09-22 追加，回答“丢给 laya 的格式对不对”）

### 6.1 模型实际看到的 prompt 结构（laya 0.3.4 源码核实）
- `agent.system_one(state, questions)` → `common.build_sequence` 渲染为：
  `[CLS] <type> question: <instructions> [SEP] [MASK] opt0 [MASK] opt1 ... [SEP] <state> [SEP]`
- 硬上限：`max_len=512` tokens；head（指令+选项）≤`head_max_len=192`，state 只占余量。
- state 序列化：dict → `json.dumps(state, ensure_ascii=False)`（纯 JSON，数字即数字）。
- 实测 A 变体 state=191 tokens（完整可见，未被截断）；B 变体 state=398 tokens → **被截成残缺 JSON**。

### 6.2 证据格式对判定的影响（60 样本同种子实测，真实模型）
| 变体 | 证据形态 | data_quality sufficient | market_regime favorable | entry conf≥0.6 | ECE(entry,2h) |
|---|---|---|---|---|---|
| A | 生产 JSON 快照（8 标量摘要） | 0/60 | 7/60 | 22/60 | 0.131 |
| C1 | 同证据改自然语言短句 | **39/60** | **60/60** | 3/60 | **0.077** |
| C2 | C1 + 20 根真实 K 线文本 | 21/60 | 29/60 | — | 0.115 |

- 结论：模型判“证据不足”的主因是**格式**（JSON 数字读不懂），不是数据量；prose 后模型明确认可证据。
- C2 说明“不是越多越好”：加原始 K 线文本后模型更保守（insufficient/adverse 上升）。
- 0.6 阈值下 C1 仍 100% ESCALATE（各问置信度均值 0.37-0.54），需阈值降到 ~0.35-0.45 才出判定。

### 6.3 C1（prose）240 样本全量结果
- data_quality sufficient 147/240（61%）；market_regime favorable 237/240；
  signal_alignment conflict 236/240（该问坏）；entry_decision reject 142/pass 98。
- 方向命中：entry_pass vs base —— 2h 49.0% vs 47.5%（p=0.81）、6h 60.2% vs 53.3%（p=0.25）、
  1d 57.1% vs 51.2%（p=0.33）→ **无统计显著技能**；ECE(entry,2h) 0.068（校准改善）。
- 判定：0.6 阈值 100% ESCALATE；阈值 0.3 → REJECTED 138 / CAUTION 94 / ESCALATE 8（无 APPROVED）。

### 6.4 能力探针（手工构造极端一致文本，真实模型 3 例）
| 探针 | 期望 | 实际 |
|---|---|---|
| P1 强多头+BUY | aligned/pass | signal_alignment=**conflict**(0.347)；regime=favorable；entry=pass(0.600) |
| P2 强空头+SELL | aligned/pass | signal_alignment=conflict；regime=**adverse**(long-only 偏差)；entry=reject |
| P3 强多头+SELL(反向) | conflict/reject | signal_alignment=conflict；regime=favorable；entry=**pass**(错误放行) |

结论：**未微调的 laya 无法完成这些交易判定** —— 对齐感知损坏（从不输出 aligned）、多空理解
长多偏差、entry_decision 近似抛硬币。prose 格式解决“证据认可”，模型能力解决不了，需领域微调。

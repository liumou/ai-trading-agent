# Laya 输入数据量与真实 MT5 成功率调研报告

- 日期：2026-09-22
- 数据：Postgres `ohlcv_data` 真实 GOLD M15（MT5 collector 写入）34,552 根，2025-04-01 → 2026-09-17；
  MT5 Bridge（XMGlobal-MT5 9）冒烟通过，实时价 4368.3 与库一致。
- 模型：真实 `convaiinnovations/laya`（421M，权重本地缓存，隔离 venv，CPU），**无任何 mock**。
- 实验：360 个真实历史开仓信号样本（EMA9/21 交叉 + 动量确定性规则，与生产确定性链同源），每个样本按
  生产同构快照跑 laya 6 问 + 收敛器；变体 A=生产现状输入，变体 B=增强输入。
- 复现：`backend/scripts/laya_real_data_eval.py`（推理）+ `backend/scripts/laya_eval_analyze.py`（分析）。

## 1. 调用 laya 时给的数据（量化清单）

| 通道 | 喂入内容 | 量级 |
|---|---|---|
| 情绪三分类 | `symbol + headlines[:3000]` | 3000 字符标题，无行情 |
| 6 问闸门（ManualGate/engine 观测） | order(5) + account(≤4) + positions(≤5 压缩) + market 摘要 | **8 个标量字段** |
| market 摘要字段 | last_close / change_1_pct / change_5_pct / vol_14 / range_position_50 / price_vs_sma9 / price_vs_sma21 | 源自 ≤200 根 M15 的确定性计算（`DEFAULT_OHLCV_BARS=200`，实际回看长度随调用点 60-200 不等） |
| 缺失 | 原始 OHLCV、成交量、多周期 H1/H4、指标（RSI/ATR/EMA）、价差/滑点、recent_trades（engine 路径恒空）、rule_flags（恒空） | — |

## 2. 数据是否足够

- **laya 自评证据不足**：360 样本中 `data_quality` = partial 344 / insufficient 15 / sufficient **1**。
  模型明确给出“当前证据部分/不足”。
- **增强输入（变体 B：+H1 摘要、ATR14/RSI14、波动趋势、近 12 根原始收盘价）不解决能力问题**：
  - 判定分布在阈值 0.4/0.5/0.6 下零变化（均 100% ESCALATE，flips=0）；
  - 逐问分布被推向负面：signal_alignment 从 57% conflict → **100% conflict**；entry_decision   100% reject（A 组 pass 147 例；B 组 0 例）；
  - 说明模型未吸收数值上下文（近均匀概率分布 + 对输入格式敏感），瓶颈在模型本身而非数据量。
- 结论：生产输入偏薄（真实 trade/执行类上下文全缺），但**单纯加大数据不提高判定质量**。

## 3. 成功率（真实数据实测，已测试）

**结论：当前模型在该任务上无可统计技能，成功率 ≈ 抛硬币（基率）。**

| 指标 | 数值 |
|---|---|
| 6 问收敛判定 | **360/360 ESCALATE**（min_confidence=0.6 及 0.4/0.5/0.7 全部）→ 影子层完全惰性，全部交还 LLM |
| 阈值降到 0.3 | 仅释放 ESCALATE 326 + REJECTED 31 + CAUTION 3（90% 仍 ESCALATE，因 insufficient 规则优先） |
| entry_decision 方向命中（pass 组 vs 基率） | 2h：46.9% vs 46.4%（z=0.11, p=0.91）；6h：48.3% vs 50.3%；1d：49.7% vs 43.3%（z=1.30, p=0.19）→ **所有 horizon 均无显著差异** |
| entry_reject 方向命中 | 2h 46.0%、6h 51.6%、1d 39.0% —— 拒绝也零信息量 |
| 置信度质量 | 除 entry_decision 外 5 问 max-class 概率均值 0.29-0.38，p(conf≥0.6)=0；entry_decision 43% 样本 conf≥0.6 但**全部是 reject**（pass 无高置信） |
| 校准 | ECE(entry,2h)=0.147，ECE(signal_alignment)=0.153，ECE(market_regime)=0.167（0.12+，校准差） |
| 单次推理延迟 | 中位 1.47s（A）/ 2.55s（B）——与“高频预筛快”的定位不符 |

## 4. 提高空间（证据支撑，按收益排序）

1. **模型面（最大瓶颈）**：概率分布接近均匀（0.28-0.58），说明模型没有任务特定知识；
   typed-decisions 0.36≈随机的结论在真实 GOLD 数据上被复现。**必须领域微调/对齐后才能谈成功率**。
2. **校准面**：ECE 0.12-0.17；但校准只改善置信度可信度，不解决“无技能”本身。
3. **数据面**：生产输入确实偏薄（模型自评 partial）；但变体 B 证明加数据在未微调模型上无效 ——
   应在微调后再回测输入增强。若微调，建议给 laya 结构化特征向量而非原始数字文本。
4. **阈值面**：min_confidence 0.6→0.3 只释放 31/360 REJECTED，边际收益极小；insufficient 规则
   （信号证据不足→ESCALATE）占主导，调阈值改不动它。
5. **观测期影响**：`laya_engine_observations` 与 `manual_shadow_reviews` 当前均 0 行；
   按本结果，影子观测将大概率得到“100% ESCALATE / 无分歧” —— 观测本身无法提供收敛决策所需信号，
   建议先解决模型面，或把观测变量改为记录逐问 label/置信度分布（本轮已产出的口径）。

## 5. 局限

- 信号复现用 EMA9/21 规则（生产无历史 signal 记录）；参照链为确定性规则简化版（非完整 TradeGate）。
- 账户/持仓/近期成交历史不可得（`trades` 表仅 17 笔且几乎全为 BTCUSD），回放使用中性账户状态，
  recent_trades/rule_flags 为空 —— 与生产 engine 观测路径一致，如实记录。
- 单机 CPU 推理，时间盒限制抽样 n=360（按月分层）。

## 6. 补充：证据格式审计（2026-09-22，回答“丢给 laya 的格式对不对 / 能否加证据”）

### 6.1 模型实际看到的输入
- `agent.system_one(state, questions)` 渲染模板（laya 0.3.4 源码）：
  `[CLS]<type> question: <instructions>[SEP][MASK]选项文本...[SEP]<state>[SEP]`，**512 token 硬上限**
  （指令+选项 ≤192，state 只能占余量 ~300）。
- 我们 A 变体 state=191 token，完整可见（未截断）；变体 B state=398 token → 被截成残缺 JSON。
- state 序列化 = `json.dumps(...)`：order/account/positions/recent_trades/rule_flags/market(8 标量)。

### 6.2 实测：证据格式 vs 判定质量（60 样本同种子）
| 变体 | 形态 | data_quality=sufficient | regime=favorable | ECE(entry,2h) |
|---|---|---|---|---|
| A | 生产 JSON | 0/60 | 7/60 | 0.131 |
| C1 | 同证据→自然语言短句 | 39/60 | 60/60 | 0.077 |
| C2 | C1+20 根真实 K 线文本 | 21/60 | 29/60 | 0.115 |

**证据不足的主因是格式，不是数据量**：JSON 数字模型“读不懂”，prose 后模型明确认可证据。
但 C2 证明“不是越多越好”——原始 K 线文本让模型更保守。

### 6.3 C1 全量（240 样本）成功率
- 判定：0.6 阈值仍 100% ESCALATE（置信度均值 0.37-0.54）；阈值 0.3 → REJECTED 138/CAUTION 94/ESCALATE 8。
- 方向命中（entry_pass vs base）：2h 49.0% vs 47.5%（p=0.81）；6h 60.2% vs 53.3%（p=0.25）；
  1d 57.1% vs 51.2%（p=0.33）→ **无统计显著技能**。
- 校准改善：ECE(entry,2h) 0.131 → 0.068。

### 6.4 能力探针（完美证据下模型仍错）
- 强多头+BUY → signal_alignment=conflict（从不输出 aligned）；强空头+SELL → regime=adverse
  （long-only 偏差）；强多头+SELL（应拒）→ entry=pass（错误放行）。
- **结论：未微调 laya 无交易判定能力；prose 格式解决“证据认可”，领域微调才能解决“判定正确”。**

### 6.5 行动建议（按优先级）
1. ✅ **已实施（2026-09-22）**：生产 6 问 state 默认改自然语言短句渲染
   `render_laya_state_prose`（`app/ai/laya_gate.py`），配置 `laya_state_prose=True`（`app/config.py`）。
   实测最大快照 prose=268 token，完整序列 347<512，无截断；`laya_gate_review` 与 engine 观测路径
   （复用同入口）自动生效。回归：laya 相关单测 157 passed / 1 skipped。
2. min_confidence 按问校准（0.35-0.45 区间扫描），或只让 entry_decision/risk_check 参与 veto；
3. signal_alignment 问题重写或删除（模型无对齐能力）；market_regime 修正 long-only 偏差需微调；
4. 领域微调（RLCD/合成标签）后才谈真实成功率与上线。

## 7. 阈值扫描与问句重写实验（2026-09-22 追加，真实数据/模型）

### 7.1 阈值扫描（C1 prose 240 样本，离线，复用逐问置信度）
| 阈值 | 判定分布 | REJECTED 2h 命中 | CAUTION 2h 命中 |
|---|---|---|---|
| 0.30 | REJECTED 138 / CAUTION 94 / ESCALATE 8 | 0.457 | 0.489 |
| 0.35 | ESCALATE 196 / CAUTION 25 / REJECTED 19 | 0.474 | 0.480 |
| ≥0.40 | 全 ESCALATE | — | — |
- **APPROVED 在任何阈值下都不可能出现**（依赖 signal_alignment=aligned，模型从不输出）；
- REJECTED 命中 0.43-0.47 ≈ 基率 0.475 → 作为 veto 会拦截 ~58% 信号且**零预测价值**。
- 结论：阈值调优救不了，问题在模型能力。

### 7.2 问句重写（80 真实样本 + 3 探针，真实推理）
重写三问：signal_alignment（方向跟随）、market_regime（方向相对）、entry_decision（显式矛盾）。
- 80 样本：signal_alignment aligned 7/80（BUY 才有，仍以 conflict 64 为主）；
  market_regime **adverse 80/80**（方向相对措辞反而更糟）；entry_decision **BUY 全 pass / SELL 全 reject**（纯方向决定，零判别力）。
- 探针（极端一致文本）：P1 强多头+BUY → 仍 conflict(0.50)/adverse/pass；P2 强空头+SELL → 仍 conflict/adverse/reject；
  P3 强多头+SELL（应拒）→ 仍 conflict/adverse/**pass（错误放行）**。
- 结论：**问句重写无法修复未微调模型的能力缺陷**；复现脚本 `scripts/laya_question_rewrite_experiment.py`。

### 7.3 综合结论（证据链闭环）
格式（JSON→prose）是**唯一**经实测有效的改进（已落地）；阈值调优与问句重写均不能产生可用判定；
**领域微调（或换模型）是唯一有希望的路径**。上线/观测期决策应等待微调后的复测。

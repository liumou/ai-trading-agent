# Findings — Laya 全量替换 LLM 可行性核实

## 需求
用户假设：「所有 LLM 调用都用 laya 实现，laya 报错时用 LLM 兜底」是否可行，给出最终改造方案计划。

## 1. 当前 LLM 调用面（代码核实，2026-09-22）

全项目只有两条 LLM 通道：
- **通道 A** `app/ai/client.py` — `complete_json_async`（简单补全），3 个调用点
- **通道 B** `mcp_server/agents/base.py` — `run_agent_loop`（agent 循环，带 MCP 工具），8 个调用点

### 逐调用点核实

| # | 调用点 | 输出契约 | laya 可表达？ | 分级 |
|---|--------|----------|---------------|------|
| 1 | `news_sentiment.py:168` 情绪分析 | sentiment 三分类 + score 连续 + confidence + key_factors[] | 仅 sentiment 三分类 ✅；score/key_factors/上下文加权 ❌ | **B 部分** |
| 2 | `strategy_optimizer.py:118` 参数优化 | suggested_params 7 个连续数值 + assessment 长文 + confidence | ❌ 连续数值直接进实盘 | **D** |
| 3 | `manual_order_gate.py:174` 手动单审查 | verdict 三分类 + confidence + risk_flags + emotional_indicators + reasoning，全部入库审计 | verdict ✅；其余字段审计关键路径 ❌ | **B 部分** |
| 4 | `orchestrator.py:209` 编排器 | 唯一执行权；place_order/modify/close/log_decision 强制审计；融合 4 份报告 | ❌ 工具调用 + 多步循环 + 审计 | **D** |
| 5 | `technical_analyst.py:63` 技术分析 | Signal + Trend/Momentum/Volatility/Key Levels/Reasoning 长文；先调用指标工具 | ❌ 工具 + 数值综合 + 长文 | **D** |
| 6 | `fundamental_analyst.py:64` 基本面 | Bias + 长文 | ❌ | **D** |
| 7 | `risk_analyst.py:91` 风险评估 | Verdict + 长文 | ❌ | **D** |
| 8 | `reflector.py:103` 反思循环 | 14 工具多步反思 + 记忆条目长文 | ❌ | **D** |
| 9 | `chat_agent.py:174` 自由问答 | 对话响应 | ❌ 自由文本生成 | **D** |
| 10 | `chat_workflow.py` 6 角色会话 | 多角色自由文本 | ❌ | **D** |
| 11 | `agent_config.py:94` single_agent | 决策 + strategy_used（strategy_used 已接 laya choice✅） | 决策长文 ❌ | **D** |

### 定量结论（沿用 09-21 穷尽盘点）
- 按调用点：A=0 完全可替换 / B=2 部分可替换 / D=9 不可替换
- 按每日调用量：**B≈14% / D≈86%**

## 2. 用户方案「laya 全覆盖 + LLM 兜底」的判定

**判定：不可行。核心原因不是「报错」，而是「无法产出」——**
- laya 是 choice/score/noul 三原语的结构化判定引擎，**不做自由文本生成、不调工具、不消费数值行情序列、不输出连续参数**。
- 对 D 类调用点，laya 不是「偶尔报错」，而是**结构性无法生成输出** → 兜底必然 100% 触发 = 仍是纯 LLM，零成本收益；若强行用 laya 的低质量输出进实盘 = 灾难风险（trillion 级真钱路径）。
- 「laya 报错→LLM 兜底」模式只对 **laya 与 LLM 语义等价的输出**成立（三分类标签层），这正是已落地的 L0。

## 3. 正确的最终形态：分层混合架构

| 层 | 定义 | 调用点 | 落地状态 |
|----|------|--------|----------|
| L0 纯分类层 | laya 直接替换，LLM 兜底 | sentiment 三分类、strategy 名抽取 | ✅ 已落地（默认关） |
| L1 分类+审计补全 | laya 给 verdict，确定性代码补 reasoning/risk_flags 保审计；LLM 深析变影子 | manual_order_gate | 🔶 可做（影子预筛） |
| L2 重构后可用 | specialist 先重构成确定性代码输出（detect_regime 已是规则表），orchestrator 快筛才可 laya | technical/fundamental/risk + orchestrator 快筛 | 🔶 需先重构 |
| D 不可替换 | 工具循环/连续数值/长文/审计关键路径 | optimizer、orchestrator 执行段、reflector、chat | ❌ 永久保留 LLM |

## 4. 关键证据（来自 09-21 调研 + 代码）
- orchestrator 系统提示明确是唯一执行权 + log_decision MANDATORY —— laya 无工具调用能力。
- optimizer 7 个连续参数经 PARAM_RANGES 钳位后直接进实盘 —— laya 无法产出连续值。
- manual gate 的 reasoning/risk_flags/emotional 全部入库审计（stored["llm"]）—— laya 无长文生成，需模板化补全保审计。
- specialist 的输入输出本质是 `run_full_analysis` 指标数学（学习 detect_regime 已是纯规则表）——「先确定性重构，再谈 laya 快筛」是最有价值中间路径。
- 经济学：laya 频次预筛省 token（sentiment 高置信省一次 Claude 调用）；agent 路径是 token 成本主要来源（不传 max_tokens，仅 max_turns 约束）。

## 5. 前置数据条件（3.9 微调之前）
- Trade Gate LightGBM AUC=0.739（合成样本 34,542 条）→ 数据基础成立。
- 尚需：purge-gap 修复（标签窗口重叠导致 AUC 虚高）、真实 trades 积累（仅 ~11 笔）。
- 若微调 laya 走 Kaggle RLCD：成本 = GPU + 4-5h；边际收益须 > 确定性门控才值得。

---

## 6. 外部证据复核：QuantDinger（2026-09-22，用户提供）

> 复核对象：https://github.com/OpenByteInc/QuantDinger.git（已 clone 至 /private/tmp/QuantDinger）
> 用户主张：「QuantDinger 就是用 jev 模型来做，llm 来兜底的」。

### 6.1 核实结果（代码级）

**用户主张属实，但范围是关键**：QuantDinger 用 JEV（System One 模型，与 laya 同类）替换的**不是「所有 LLM」**，而是**「开仓前决策门」（pre-trade entry filter）这一个窄决策面**，LLM 作兜底。其余 LLM 用途（fast_analysis / strategy_review / ai_chat / research_agent 等）**仍是纯 LLM，未替换**。

证据：
- `backend_api_python/app/services/ai_decision_filter.py` — `JEV_QUESTIONS` 定义 6 个 choice 问题：data_quality / signal_alignment / market_regime / risk_check / execution_quality / entry_decision。
- `docs/trading/LIVE_TRADING_SAFETY.md` — 「Optional JEV System One entry filter」：只过滤**开仓**；平仓/止损/止盈/紧急减仓**绕过**；JEV 不可用 → LLM 兜底 → 两者皆不可用 → **fail-open 放行**并记录。
- `ai_decision_filter.evaluate()` — 仅 `ENTRY_ACTIONS`（open_long/open_short/add/buy/sell）；`EXCLUDED_STRATEGY_TYPES`（grid/dca/martingale）跳过。
- `ai_decision_context.py` — **Decision Context V2**：确定性代码预计算多周期行情/指标/持仓/敞口/净值/回撤/最近盈亏/保护/执行条件，JEV 只消费「已算好的结构化 state」做 choice，不自己算指标。
- 判定收敛是**确定性规则**（`_evaluate_jev` 末尾）：entry_decision=pass 且 risk!=block 且 execution!=block 且非 signal_conflict+regime adverse 高置信 → PASS，否则 REJECT。
- 校验链：choice ∈ 白名单、probabilities 完整且和为 1、choice==argmax、confidence ≥ min（默认 0.55）→ 任一失败视为畸形，抛错走兜底。

### 6.2 对「laya 全覆盖 + LLM 兜底」的修正判定

| 主张 | 判定 | 依据 |
|------|------|------|
| 「所有 LLM 用 laya/JEV 实现」 | ❌ 不可行，**QuantDinger 自己也没这么做** | 其分析/复盘/聊天/研究仍纯 LLM；JEV 只换开仓门 |
| 「交易决策路径的判定面 laya 化 + LLM 兜底」 | ✅ 可行，已有生产级先例 | QuantDinger 用 6 问 choice + 确定性收敛 + LLM 兜底 + fail-open |
| 「laya 不能消费数值行情」 | ⚠️ 修正：**可以**，前提是确定性代码预计算特征成结构化 state | QuantDinger 的 Decision Context V2 = 我们的 build_features/context_builder 同类形态 |

### 6.3 修正后的结论

- **全量替换（所有 LLM 调用）依然不可行** —— QuantDinger 也没全替换；D 类（连续数值进实盘/长文/工具循环/审计关键路径）是结构性障碍。
- **但「把交易判定面全部 laya 化 + LLM 兜底」是可行且被生产验证的**，且 QuantDinger 范式（6 问 choice + 确定性收敛 + fail-open 兜底链）可以直接映射到本项目：
  - manual_order_gate 的 verdict 判定 → JEV 式 6 问（data_quality/signal/regime/risk/execution/entry）
  - 本项目已有等价物：context_builder（状态）+ TradeGate（LightGBM 判定）+ 确定性风控规则 —— 缺的只是「laya 判定层」和「LLM 兜底链」。
- **设计差异（须注意）**：QuantDinger 对 AI 故障 **fail-open（放行）**；本项目 manual_order_gate 是 **fail-closed（拒绝）** 的审计关键路径。laya 化后应保留 fail-closed 语义（审计性），或先在影子模式对比一致率再决定。

### 6.4 QuantDinger 范式 → 本项目映射

| QuantDinger 构件 | 本项目对应物 | 差距 |
|------------------|--------------|------|
| JEV_QUESTIONS 6 问 choice | 无（现有 manual_order_gate 是单 verdict LLM 问） | 需新增 laya 6 问定义 |
| Decision Context V2（确定性预计算） | `context_builder` + `build_features` + 风控快照 | 基本齐备，需组装为 laya state |
| 确定性收敛器（pass/reject 规则） | engine 风控规则 + TradeGate | 齐备 |
| LLM 兜底链（JEV→LLM→fail-open） | manual_order_gate 已有 LLM 主判 | 需改为 laya 主判 + LLM 兜底 |
| 置信度/白名单校验 | laya_runtime.predict_choice 已有 | 齐备 |
| 影子验证数据 | 无 | 需加一致率对比报表 |

---

## 7. 四路评审汇总（2026-09-22）

评审报告：`.planning/2026-09-22-laya-llm/reviews/{architect,security,validation,external-evidence}.md`

### 7.1 架构评审（architect.md）
- C1 Phase 4 影子「一致率」无 LLM 参照——engine 开仓路径无 LLM 判定 → 影子期测「laya vs TradeGate+确定性链」分歧率。
- C2 lay a 放松确定性风控回归路径——防火墙不变量「只能收紧」未覆盖 laya → veto-only 收紧层。
- H1 6 问→三值收敛未定义（JEV 二值 vs 本项目三态 fail-closed 冲突）。
- H2 主判会降级审计证据 → enumerate 期保留 laya+LLM 并行入库。
- H3 冷加载 136s + predict 无 wait_for + `_review` 只给 LLM 包超时 → 预热 + 30s wait_for。
- H4 「复用 context_builder」不成立——AIContextBuilder 产散文 prompt 且仅 engine 侧 → 新建 `build_laya_state`。
- H5 阈值刻度不兼容（max-class vs 熵置信度）→ 本项目刻度重新校准。
- M1 6 问应单次前向批处理；M2 只留 3 问给 laya；M3 手动单低频 → 离线回放；M4 新增 laya_unavailable 分类；M5 致命分歧（laya 放行被 LLM 拒）= 0 为准入门槛。

### 7.2 安全评审（security.md）
- C1 计划「laya 主判」推翻 `risk/manager.py:328` 已落地不变量「概率模型不得作风控拦截依据」→ **veto-only**：laya APPROVE 仍须 LLM 深析；laya REJECT 须佐证后终局。
- H1 审计维度（emotional/reasoning）丢失 → 落库 `stored["laya"]` 结构化 + 显式 not_assessed，禁模板「无」。
- H2 QuantDinger fail-open 被误读为 laya 主判辩护——语义正交 → 补对照表 + 三态 CAUTION 映射。
- H3 权重供应链无 root-of-trust（import laya 顶层执行任意代码）→ pin revision + sha256 + 构建期下载。
- H4 影子隔离未定义（_reject_inline 会写 TRADE_BLOCKED + WS 推送）→ shadow=true 不推送。
- M1 阈值语义不可比 → per-question 校准；M2 predict_choice 缺 sum/键集/NaN 校验 → 补齐；M3 sentiment 丢 engine 溯源；M4 模型回归静默停摆 → 拒绝率漂移告警。

### 7.3 评估验收评审（validation.md）
- C-1 收敛器未定义（6 问二值 → 三分类映射缺失，CAUTION 无定义）→ 开工第一件事冻结。
- C-2 验收门槛未量化 → n≥300 + Wilson 下限≥0.85 + 兜底率≤5% + 灰度 10→50→100% + kill switch。
- H-1 LLM verdict≠ground truth（trades 仅 11 笔）→ 规则负样本回放 + 100% 分歧人工复核。
- H-2 snapshot 未落库 → 影子期落 state_snapshot 供回放。
- H-3 影子非干扰性无保障 → laya/LLM 并发 + 超时 + 硬不变量测试。
- H-4 现有测试零覆盖 → 补收敛/兜底/CAUTION/审计连续性用例。
- M-1 修正：「被拒单不落表」不完全成立——manual gate 先建审计再拦截，REJECTED 有行；硬闸门拒的单无 verdict。
- M-2 成本收益：enforce 跳 LLM 单笔省 ~300 tokens，收益在延迟/可用性而非 cost。
- M-3 回归基线修正：相关文件 43 passed/1 skipped；全量 876/23 failed（环境/顺序型，与本次无关）。
- M-4 裸一致率虚高 → 3×3 混淆矩阵 + Kappa。
- M-5 DB 膨胀 ~7MB/年 → 影子表 schema 明示。

### 7.4 外部证据复核（external-evidence.md）
- 主体：findings 第 6 节解读与源码一致，无方向性错误。
- H1 模型就绪度不直接外推——JEV 是托管校准模型；本项目 laya 未微调 → LightGBM 0.739 作基线 + 影子门槛。
- H2 fail-open 语义冲突（JEV allow-by-default vs 本项目 fail-closed）→ 重写判据与收敛器，审计问 insufficient→拒绝/兜底。
- H3 「校验链已齐备」不实（无 sum-to-one/键集校验/按问阈值）→ 落地 JEV 五校验适配层。
- M4 阈值表述精度：0.55 只强制 entry/risk/execution 三问，signal/regime 仅用于合取阻断。
- M5「生产验证」应称「代码级验证/生产级设计」（仓库仅 11 单测）。
- M6 云/本地差异：预载预热/权重持久化/内存预算/零外发。
- M7 可抄细节：decision_key 内容寻址缓存、max_calls_per_run=25、point-in-time stale_after=3×interval、幂等审计信封、按策略 opt-in + 回测绕过。
- L8 判据 crypto 绑定 → 改写 MT5 语义；L9 JEV 仅结构同构，宜称「同类 System One」。

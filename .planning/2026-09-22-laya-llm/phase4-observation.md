# Phase 4 设计：engine 开仓侧「只观测、不改行为」

## 目标（大白话）
自动交易机器人（engine 路径）开仓前现在靠 **TradeGate（LightGBM 模型）+ 确定性风控规则** 把关，没有 LLM 参与。
Phase 4 想在开仓前**偷偷加一台只记录、不发言的旁听席**：让 laya 也看一眼同样的行情状态、给出它的"该不该开仓"判断，
然后把 **laya 的判断 和 现有规则的判断** 记下来对比，看它们分歧大不大、分歧在什么情况下出现。
**全程不改任何交易行为**——不开仓/停单开关保持原样，laya 故障也绝不影响交易。

## 为什么只观测
- engine 路径没有 LLM 判定可参照（架构评审 C1），不能像 ManualGate 那样算"laya vs LLM 一致率"。
- 只能对比 laya vs 「TradeGate + 确定性链」的分歧率。分歧大 → 说明 laya 与现有风控认知不一致，需要人工看案例；
  分歧小 → 说明 laya 只是重复现有规则，继续投入价值低。
- 若观测数据证明有价值，再收敛为与 ManualGate 相同的 **veto-only 收紧层**；否则 Phase 4 到此为止，不再往下投入。

## 事实基础（已核实代码）
- 开仓许可点：`backend/app/bot/engine.py:804` `_check_trade_permission`
- TradeGate 已存在 shadow/enforce 双开关（`trade_gate_shadow` / `trade_gate_enforce`），弃权 `(None, 0.0)` 绝不阻断
- TradeGate 用「上一根已确认 bar」`df.iloc[:-1]` 做特征（与 laya 应同源）
- 确定性链其余部分：仓位上限 / 当日亏损 / 相关性冲突（同一函数内继续往下）
- 风险不变量 C4（`risk/manager.py:328`）：概率模型不得作风控拦截依据——laya 观测与收紧都必须遵守

## 观测点与记录内容（新增 `LayaEngineObservation` 表）
每次 engine 开仓许可检查时（TradeGate 判定之后、返回之前）记录一行：
- `ts` / `signal_label`
- `chain_verdict`：TradeGate can_trade（True/False/None=弃权）
- `chain_prob`：TradeGate 置信概率
- `laya_verdict`：laya 收敛器最终判定（APPROVED/CAUTION/REJECTED/ESCALATE/UNAVAILABLE）
- `laya_confidence` / `laya_answers`（6 问逐题结果，JSON）
- `final_allowed`：本次检查最终是否放行（含后续确定性规则）
- `agreement`：laya 与 chain 是否一致（定义见下）
- 不记录、不推送任何阻止/放行的副作用

## 分歧定义
- **收紧分歧（重点）**：chain 放行 / laya 判 REJECTED（或 ESCALATE）→ 潜在"laya 会拦住但现有规则放行"案例，人工复核
- **放松分歧**：chain 不放行 / laya 判 APPROVED → 潜在"laya 会放行但现有规则拦住"案例，人工复核
- **弃权率**：laya UNAVAILABLE / chain abstain 占比（衡量可观测性）

## 落地方式（最小侵入）
1. `backend/app/ai/laya_engine_observation.py`：纯函数——从同一份 OHLCV df + 上下文构造 laya state，跑 6 问 + 收敛器，返回结构化记录
2. `backend/app/bot/engine.py:_check_trade_permission` 内加一个调用点：`asyncio.create_task` 或同步 best-effort 落库，**任何异常只记日志、绝不影响返回结果**
3. 新表 + alembic 迁移（沿用 Phase 3.3 的专表模式，与 ManualShadowReview 并列）
4. 配置：`laya_gate_engine_shadow=True`（2026-09-22 用户批准打开，默认 True；kill switch 改回 false）+ `laya_engine_report.py` CLI
5. 报表指标：分歧率、收紧分歧数、放松分歧数、弃权率、延迟——**不设 PASS/FAIL 门槛，只出描述性统计**

## 样本与时间盒
- 数据来自真实信号自然累积，速度取决于策略频率，无法预估天数
- **时间盒 4 周**：满 4 周或样本 ≥150 条即出第一份报告
- 报告结论三选一：① 分歧小且案例有解释 → 停止，不收敛；② 收紧分歧集中且可解释 → 提案收敛为 veto-only（单独审批）；③ 分歧杂乱无规律 → 停止，归因 laya 未校准

## 明确不做
- 不做 enforce / 不改变开仓行为 / 不新增并行 gate 形态
- 不重复 TradeGate 已有 shadow/enforce 结构（它是 LightGBM，Phase 4 是纯观测对比）
- 不动 Phase 3 的 ManualGate 影子
- 不做校准（那是 Phase 5）

## 验收
- 代码评审：观测点无副作用（laya 异常零影响、无 enforce 开关生效路径）
- 单测：观测纯函数 5-8 例；hook 故障注入 2-3 例（laya 崩 → final_allowed 不变）
- 手工验证：本地 paper/shadow 模式跑 1 天，确认记录落库且行为日志与开关前一致

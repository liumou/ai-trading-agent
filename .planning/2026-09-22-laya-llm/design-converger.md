# 设计冻结：Laya 6 问收敛器（Phase 3.0 产物）

> 2026-09-22 · 依据四路评审（architect/security/validation/external-evidence）
> 语义：veto-only 收紧层 —— laya 只收紧、不放松；APPROVE 不跳过 LLM；REJECT 须佐证。

## 1. 6 问定义（GOLD/M15/MT5 语义，非 crypto）

| key | choice 候选 | 语义 |
|---|---|---|
| data_quality | sufficient / partial / insufficient | 行情/账户/成交数据是否新鲜完整（纯审计，不参与收敛） |
| signal_alignment | aligned / mixed / conflict / insufficient | 订单方向 vs 多周期趋势/动量证据 |
| market_regime | favorable / neutral / adverse / insufficient | 当前 regime 是否适合开仓 |
| risk_check | clear / caution / block / insufficient | 仓位/保证金/回撤/连亏/保护（SL/TP） |
| execution_quality | clear / caution / block / insufficient | 价差/滑点/挂单价合理性/执行条件 |
| entry_decision | pass / reject | 综合开仓判定（二值） |

## 2. 收敛规则（纯函数 converge_laya_verdict）

输入：{question_key: {label, confidence, entropy_confidence, probabilities}}，全 6 问。
输出：{verdict: APPROVED|CAUTION|REJECTED|ESCALATE, confidence, reasons[], decision_chain}

优先级（自上而下第一条命中即返回）：
1. **畸形/低置信 → ESCALATE**：任一强制问（signal_alignment/market_regime/risk_check/execution_quality/entry_decision）label 不在候选、confidence 缺失或 < 阈值、probabilities 缺失 → 交 LLM（fail-closed 语义）。
2. **确定性 block → REJECTED**：risk_check=block 或 execution_quality=block 或 entry_decision=reject（硬拦截，先于 AI）。
3. **方向合取 → REJECTED**：signal_alignment=conflict ∧ market_regime=adverse（双高置信 ≥ 阈值）。
4. **全 pass 且高置信 → APPROVED**：entry=pass ∧ risk=clear ∧ execution=clear ∧ signal≠conflict ∧ regime≠adverse（仍需 LLM 深析，不跳过）。
5. **其余 → CAUTION**（mixed/neutral/partial/低置信但未畸形）。

注：data_quality=insufficient → 标记 reasons + ESCALATE（审计问，不做拒绝依据）。

## 3. verdict 级置信度聚合

- confidence = min(强制问中已返回的 max-class 概率)；无则 None → ESCALATE。
- entropy_confidence 保留为参考字段（非判定依据）。

## 4. 验收门槛（影子期，Phase 3.3 量化）

- n≥300 有效样本；致命分歧（laya APPROVED 而 LLM REJECTED）0 例，单侧 95% 上限 ≤1%；
- 一致率 Wilson 下限 ≥0.85；兜底率（ESCALATE/不可用）≤5%；灰度 10→50→100%。

## 5. 判定链（decision_chain 审计字段）

["hard_gates", "laya:6q", "converger", "llm"] —— 落库 `stored["laya"]`：
model/revision/阈值/6 问概率/收敛器输出/延迟；emotional 维度显式 `not_assessed` 或由 LLM 提供。

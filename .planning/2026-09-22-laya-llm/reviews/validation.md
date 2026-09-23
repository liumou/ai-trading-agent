# Validation Review — Phase 3 影子一致率方案与验收标准评审

- 评审对象：`.planning/2026-09-22-laya-llm/task_plan.md`（v2）、`findings.md`
- 评审视角：评估与验收（可测性、可量化验收、测试策略、覆盖缺口、评估成本）
- 评审日期：2026-09-22
- 证据基线（实测）：
  - `backend/tests/unit/test_laya_runtime.py` + `test_manual_order_gate.py` 合跑：**43 passed, 1 skipped**（1 skipped = 真实 laya 模型测试，本机无权重缓存跳过）
  - `backend/tests/unit` 全量：**876 passed, 23 failed, 1 skipped**；23 个失败全部集中在 `test_openai_agent_loop.py` / `test_multi_agent.py` / `test_provider.py`，且单测隔离运行时单个用例可 pass（套件顺序/环境依赖型，与 laya/manual gate 无关）
  - 计划中「现有 59 passed 不回归」的基数**不可复现**（见发现 M-3）

---

## 1. 影子一致率方案可测性

### 1.1 必须先定义的契约：6 问 → 3 级 verdict 的收敛器

计划沿用了 QuantDinger 的 6 问（data_quality / signal_alignment / market_regime / risk_check / execution_quality / entry_decision），但 **QuantDinger 的收敛器输出是二值 pass/reject**（`_evaluate_jev`：entry=pass 且 risk!=block 且 execution!=block 且非 signal_conflict+regime_adverse → allowed），而本项目 manual gate 的 LLM verdict 是**三分类 APPROVED / CAUTION / REJECTED**（`_VERDICTS`，manual_order_gate.py:46；CAUTION 对应 PENDING_CONFIRM 二次确认流程，TTL 120s）。

**没有这个收敛映射，「laya vs LLM verdict 对比」就是未定义指标。** 必须在影子模式之前冻结：

- 建议规则（与 LLM 语义对齐）：
  - REJECTED：entry_decision=reject，或 risk_check=block，或 execution_quality=block，或 signal_conflict+regime_adverse 双高置信（照搬 QuantDinger directional_block）
  - CAUTION：risk_check=caution 或 execution_quality=caution，或现有 warn 级 rule_flags 命中（如 no_stop_loss / revenge_trade_window 的 warn 态）
  - APPROVED：其余
- CAUTION 是 UX/确认流状态，不是纯风险判断——收敛器必须显式给出 CAUTION 的判定路径，否则影子报表的 CAUTION 类一致率恒为 0（laya 永不产 CAUTION）。
- 置信度聚合：laya_runtime 已把 confidence 重定义为「所选类最大类概率」（laya_runtime.py:104-116），6 问各自有 confidence；verdict 级 confidence 需定义聚合（建议：entry_decision 的概率为主，block 类问题取 min）。
- 审计连续性：`_normalize_verdict`（manual_order_gate.py:222）产出 verdict/confidence/risk_flags/emotional_indicators/reasoning 入库 `review["llm"]`；laya 主判后这些字段必须**模板化补全**（findings 已指出 laya 不产长文），否则审计行/前端字段（reason/kind/retryable）会断。

### 1.2 指标集（影子报表必须包含，缺一不可）

| 指标 | 定义 | 备注 |
|---|---|---|
| 原始三分类一致率 | laya verdict == LLM verdict / N | 头号数字，但会被 CAUTION 缺失和「硬闸门后窄带样本」稀释 |
| 混淆矩阵（3×3） | APPROVED/CAUTION/REJECTED × 同 | 每类灵敏度可见；类不均衡（多为 APPROVED）时必须逐类看 |
| 危险分歧率 | laya=APPROVED 且 LLM=REJECTED | **唯一致命方向**（fail-closed 真钱路径的 false-approve），单独计数 + 单侧 CI |
| 误杀率 | laya=REJECTED 且 LLM=APPROVED | 可用性/UX 成本，容许小比例但要记录 |
| 逐问一致率 | 6 问各自 choice 一致率 | data_quality/execution_quality 在硬闸门通过后大概率恒为 sufficient/clear，会虚高总一致率 |
| Cohen's Kappa（3 类） | 去随机一致 | 头号数字的补充 |
| 置信度分布 | laya 各问 confidence 的分布/分位数 | 判断阈值 0.55/0.85 是否合理 |
| 兜底率 | laya 不可用/畸形/低置信 → 走 LLM 的占比 | enforce 后 = LLM 调用率，直接决定成本 |
| 延迟 | laya p50/p95/p99、LLM p50/p95 | 影子下 laya 不得阻塞 LLM 路径 |
| 成本 | 每单 LLM token（有则记）/ laya CPU 耗时 | `complete_json_async` 当前不返回 usage（ai/client.py:42-51），成本只能按 prompt 长度+max_tokens=300 估算，或升级 provider 接口 |
| 分歧人工复核 | 每个分歧 case 的复核结论 | LLM 不是 ground truth，分歧必须人看 |

### 1.3 样本量与对比基准

- **对比基准合理性**：LLM verdict 只能作为「一致性代理基准」，不能作为正确性 ground truth——LLM 本身未经结果标签验证（findings 记录真实 trades 仅 ~11 笔）。影子一致率衡量的是 laya 与 LLM 的**行为一致性**，不是**判定质量**。这对「切 enforce」而言是必要非充分条件。
- **致命方向零容忍**：对「laya=APPROVED & LLM=REJECTED」做单侧 Clopper-Pearson 95% 上限 ≤ 1%，需要 n≈300 且 0 例发生。
- **总一致率**：目标点估计 ≥0.90 且 Wilson 95% 下限 ≥0.85 → n≈300（±2.5% 半宽）；Kappa ≥0.70。
- **现实瓶颈——样本积累速度**：manual gate 是人工下单通道，量级低（估算 5-20 单/日进入 LLM 审查）。n=300 按 10 单/日需 **30 个交易日 ≈ 6 周**。计划必须写明：影子期时长预期 + 达到 n 前的「数据不足」处置（不得用低 n 下结论）。
- **负样本问题修正**：任务描述「被拒单不落表」对本项目**不成立**——`submit_order` 先 `_create_audit` 再走硬闸门/情绪规则/LLM（manual_order_gate.py:106-146），REJECTED 单有完整审计行（status=REJECTED + reject_kind + rule_flags）。真正的缺口是：**硬闸门/情绪规则直接拒的单没有 LLM verdict**，无法进 laya-vs-LLM 对比；且 `_build_snapshot` 的完整快照**未落库**（review JSON 只存 rule_flags 与 llm verdict），历史单无法离线重放。
- **拒绝质量的正解**（三层）：
  1. 规则负样本回放：把已被确定性规则（martingale/复仇窗口等）拦截的历史单输入 laya，测 laya 的 risk_check/entry_decision 灵敏度——**可立即用现有 order_audits 做，不依赖 LLM**；
  2. 结果标签：已执行 APPROVED 单的后续 PnL/止损命中 vs laya 预测拒绝——需要 outcome 标签积累（真实数据极少，仅作参考轨）；
  3. 人工复核分歧：100% 分歧 case 人工裁决后，把裁决结果作为增强标签回填。

### 1.4 存储设计

- **新增影子表**（如 `manual_shadow_reviews`）：audit_id FK、laya_verdict、laya_confidence、probabilities(JSON)、逐问结果(JSON)、llm_verdict、llm_confidence、agreement、laya_latency_ms、llm_latency_ms、fallback_reason、created_at。理由：order_audits.review 是 JSON 列，报表聚合查询慢；影子明细进专表，order_audits.review 只加 `review["laya"]` 摘要（additive，不破坏前端轮询契约）。
- **离线回放前提**：现在完整 snapshot 不落库 → 影子期必须同时落 `state_snapshot`（或渲染后的 prompt 文本），否则无法回放历史样本凑样本量、也无法事后复现分歧 case。
- DB 膨胀量级：手动量级 20 单/日 × ~1-2KB ≈ 7MB/年，可忽略；但 Phase 4 若对引擎通道全量影子，必须换聚合表/采样。

---

## 2. 验收标准可量化性

计划现状：「影子验证 → 一致率高后切 enforce」——**「高」未定义，验收不可判**。给出可落地的验收门槛草案：

| 门槛 | 数值 | 依据 |
|---|---|---|
| 有效样本 | n ≥ 300（两路 verdict 均存在） | 致命方向 0/300 才够单侧 1% 上限 |
| 三分类一致率 | 点估计 ≥0.90，Wilson 95% 下限 ≥0.85 | ±2.5% 半宽需 n≈292 |
| 危险分歧（laya APPROVED & LLM REJECTED） | 0 例，95% 单侧上限 ≤1% | fail-closed 真钱路径 |
| Kappa | ≥0.70 | 3 类、APPROVED 主导时点一致率虚高 |
| 逐问一致率 | entry_decision/risk_check ≥0.90；其余报告但不作门槛 | 硬闸门后窄带 |
| 兜底率 | ≤5%（laya 不可用+畸形+低置信合计） | enforce 后即 LLM 调用率 |
| 延迟 | laya p95 ≤ 700ms（CPU 实测 200-500ms）且 enforce 后总判定 p95 < 现状 LLM 审查 | 否则无收益 |
| 分歧复核 | 100% 分歧人工复核归档 | LLM 非 ground truth |
| 结果标签（尽力而为） | 影子期若有 ≥30 笔已执行单 outcome，附加统计对比 | 真实数据少，仅参考 |

关键语义：**验收门槛要分层**——「一致率达标」只是 Phase 3 影子期出口；「切 enforce」还须加：
1. 致命方向 0 例（带 CI）持续 2 个报告周期；
2. 灰度路线（feature flag：shadow → 10% → 50% → 100%，每档 ≥1 周）；
3. 回滚预案（kill switch，enforce 故障即时回 LLM 主判，fail-closed 语义下这是安全回退方向）；
4. enforce 后仍需持续影子（laya 与 LLM 继续双跑一段或抽样双跑），防漂移。

另外必须写明：**enforce 下 laya 高置信 PASS 是否跳过 LLM**。QuantDinger 是「JEV 通过即不再调 LLM」；本项目若同样处理，省的是 manual gate 每次 ~300 max_tokens 的调用——手动量级下年化 token 节省有限（主要收益是延迟/可用性而非钱），计划要显式做这个决策并量化收益预期，否则「成本对比」无意义。

---

## 3. 测试策略

### 3.1 laya 6 问单元测试（mock 层）

复用 `test_laya_runtime.py` 的 `_mock_runtime` + patch `settings` 形态，新增覆盖：

1. 6 问全合法 → 收敛器产出正确 verdict（PASS 矩阵：risk=block 拒、execution=block 拒、signal_conflict+regime_adverse 拒、risk/execution=caution → CAUTION）；
2. 任一问 label 出白名单 → 整体兜底 LLM（不部分采用）；
3. probabilities 缺失/空/和≠1 → 畸形兜底；
4. choice != argmax → 畸形兜底（laya_runtime.py 已有该防御，6 问路径必须复用）；
5. 任一关键问 confidence < 阈值 → 兜底；
6. 缺 question key（返回部分 answers）→ 兜底；
7. runtime 不可用 / `_load` 抛错 / predict 抛 RuntimeError → 兜底；
8. **影子非干扰性**：laya 兜底/超时/崩溃时，LLM 路径结果与无 laya 时**完全一致**（这是影子模式的硬不变量，必须测）；
9. 收敛器 CAUTION 映射与 verdict 级 confidence 聚合；
10. 模板化 reasoning/risk_flags 生成（审计字段连续性：`review["laya"]` 与 `review["llm"]` 同构）；
11. 一致率/Kappa 计算纯函数单测（3×3 矩阵、边界输入）。

### 3.2 真实模型验证

沿用 `TestRealLayaAPI`（importorskip + 本地权重缓存，无权重自动 skip）：
- 用 `_build_snapshot` 同构 fixture 组装 state，跑真实 6 问，断言：6 个 label 均在白名单、confidence ∈ [0,1]、probabilities 完整且和为 1、确定性（同输入两次结果一致）。

### 3.3 故障/畸形/低置信注入

- laya 故障：mock `_load` 抛错、predict 挂起超时（shadow 路径不得阻塞 LLM）、HF 下载失败 → available=False。
- 畸形：逐问白名单外 label、空 probabilities、choice≠argmax、answers 缺键、confidence 越界。
- 低置信：逐问低于阈值边界值（0.55 语义：`<` 与 `≥` 边界各一例，参考 `TestStrategyChoiceThreshold`）。

### 3.4 回归基线（见发现 M-3）

Phase 3 的回归门应**显式固定子集**：`test_laya_runtime.py` + `test_manual_order_gate.py`（当前 43 passed + 1 skipped）为主回归面；全量套件当前有 23 个顺序/环境依赖型失败（openai_agent_loop / multi_agent / provider 单测隔离可过、全量跑挂），不可作为「不回归」的判据，须先修套件隔离或圈定白名单。

### 3.5 覆盖缺口（评审维度 4）

- `test_laya_runtime.py` 已覆盖：默认关、白名单外 label 回落、低置信回落、predict 失败回落、单例重置、真实模型（skip）、策略阈值边界、config 默认值。**缺口**：无 6 问/verdict 收敛、无影子一致性计算、无模板化补全、无 CAUTION 映射。
- `test_manual_order_gate.py` 已覆盖（25 用例）：硬闸门不烧 LLM、switching fail-closed、情绪 block 规则、APPROVED 执行、CAUTION/confirm/TTL/状态校验、LLM 畸形/超时 fail-closed、状态漂移、SL/TP 锚定规则、rollout shadow/paper 拦截、LLM 拒单证据合并。**缺口**：无任何 laya 参与路径（影子层完全未测）、无 shadow 报表写入断言、无 laya+LLM 双跑并发/非阻塞断言。

---

## 4. 评估成本（评审维度 5）

- **影子期每单双跑**：LLM 成本不变（原路径）+ laya CPU 200-500ms（`asyncio.to_thread`，不入事件循环）。手动单量级下**绝对成本可忽略**；真正的成本是：① 影子表 + 报表查询/人工复核的维护；② 若 enforce 后 laya 高置信 PASS 跳过 LLM，才产生 token 节省——manual gate max_tokens=300，单笔节省上限 ~300 tokens，需量化后决策。
- **延迟**：laya 推理不得位于 LLM 判定关键路径；影子下应 laya 与 LLM 并发跑，laya 结果仅异步落表（带超时）。
- **DB**：见 1.4，影子表 ~7MB/年（手动量级）；若 Phase 4 扩到引擎通道需重新评估。
- **失败注入的测试成本**：真实模型测试依赖 1.7GB 权重缓存，CI 不装（现有 skip 机制正确）；建议加「确定性小模型/单测固定 state」做结构验证，真实模型只做发布前人工冒烟。

---

## 5. 分级发现清单

### Critical

- **C-1 收敛器未定义：6 问 → 三分类 verdict 的映射缺失，影子一致率指标无法计算。**
  证据：QuantDinger `_evaluate_jev` 输出二值 pass/reject；本项目 `_VERDICTS` 是三分类且 CAUTION 是确认流 UX 状态（manual_order_gate.py:46, 209-217）。建议：Phase 3 开工第一件事冻结收敛规则（1.1 草案），含 CAUTION 映射与 verdict 级置信度聚合，并写单测锁定。
- **C-2 验收门槛未量化：「一致率高后切 enforce」不可判。**
  证据：task_plan.md Phase 3/4 无任何阈值、CI、样本量、灰度与回滚定义。建议：采用第 2 节门槛草案（n≥300、致命方向 0 例+单侧上限≤1%、一致率下限、兜底率≤5%、灰度+kill switch）。

### High

- **H-1 LLM verdict 被默认为 ground truth，但无结果标签支撑，一致率高≠判定质量高。**
  证据：真实 trades ~11 笔（findings）；影子对比只能证明行为一致性。建议：一致率仅作第一道门；叠加规则负样本回放（历史 REJECTED 单输入 laya 测灵敏度，现有 order_audits 即可做）+ 分歧人工复核 + 尽力而为的结果标签轨。
- **H-2 完整 snapshot 未落库，历史样本无法离线回放，样本量瓶颈无解。**
  证据：`_build_snapshot` 结果只进 LLM prompt（manual_order_gate.py:527），review JSON 仅存 rule_flags+llm。建议：影子期落 `state_snapshot` 或渲染 prompt，使回放可复现。
- **H-3 影子非干扰性无测试保障：laya 故障/超时不得改变 LLM 路径结果。**
  建议：加「有/无 laya 时 LLM 判定结果一致」的硬不变量测试（3.1-8），并对 laya 推理设超时、与 LLM 并发而非串行。
- **H-4 现有 test_manual_order_gate.py 与 test_laya_runtime.py 完全不覆盖 laya 主判/影子链路。**
  证据：gate 25 用例全部走 `ai_client.complete_json_async` mock，无 laya；laya 测试只有 sentiment/strategy 两场景。建议：按 3.1 补齐 6 问收敛、兜底、CAUTION 映射、审计字段连续性用例。

### Medium

- **M-1 负样本前提修正：manual gate 的 REJECTED 单是落表的（order_audits），但硬闸门/情绪规则拒的单无 LLM verdict，且无 outcome 标签。**
  建议：拒绝质量评估用「规则负样本回放 + 已执行单 outcome」双轨，不要用「被拒单不落表」的错误前提设计。
- **M-2 成本收益未量化：enforce 跳 LLM 的单笔节省仅 ~300 tokens（max_tokens=300），手动量级下年化收益极小。**
  建议：enforce 决策理由应落在延迟/可用性而非 token 成本；计划需显式回答「高置信 PASS 是否跳 LLM」。
- **M-3 回归基线不可复现：「59 passed」与实测不符。**
  证据：laya+gate 两文件 43 passed/1 skipped；全量 876 passed/23 failed（失败为套件顺序/环境依赖型，单测隔离可过）。建议：固定子集回归门（3.4），并在 CI 白名单圈定全量套件失败集，避免把无关失败当回归。
- **M-4 逐问一致率缺失会虚高头号数字。**
  证据：硬闸门通过后 data_quality/execution_quality 大概率恒为 sufficient/clear。建议：报表必含 3×3 混淆矩阵、逐问一致率与 Kappa，禁用裸一致率单指标下结论。
- **M-5 影子成本与 DB 膨胀无预估。**
  建议：写明影子表 schema 与 ~7MB/年量级；Phase 4 扩展引擎通道前重估（见 1.4）。

### Low

- **L-1 无分层灰度与回滚预案定义。**
  建议：feature flag（shadow → 10% → 50% → 100%，每档 ≥1 周）+ kill switch + enforce 后抽样双跑防漂移（第 2 节）。
- **L-2 真实模型测试依赖 1.7GB 权重，CI 必然 skip，回归价值有限。**
  建议：保持 importorskip；新增「确定性小模型/固定 state」结构验证用例，真实模型只做发布前人工冒烟（3.2）。
- **L-3 报表口径未定义（周报字段、归属人、分歧复核归档位置）。**
  建议：第 1.2 指标表 + 周报模板写入计划，分歧复核结论回填影子表作为增强标签。

---

## 6. 结论

影子一致率方案**方向正确且是本项目唯一安全的过渡路径**，但当前计划停留在「做报表」的粒度：**收敛器、指标集、样本量、验收阈值、负样本策略、存储与回放、灰度回滚**七个要素均未定义，按现状实施无法得出「是否切 enforce」的可判结论。本评审给出可直接并入 task_plan.md Phase 3/4 的量化草案（第 1、2 节），建议先补 C-1/C-2 再开工。

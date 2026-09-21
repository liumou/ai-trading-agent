# Architecture Review — Laya 交易判定面改造方案（v2）

- **评审对象**: `.planning/2026-09-22-laya-llm/task_plan.md`（v2 计划）、`findings.md`、`progress.md`
- **评审代码**: `backend/app/ai/laya_runtime.py`、`backend/app/services/manual_order_gate.py`、`backend/app/bot/engine.py`（`_check_trade_permission` 804-1040）、`backend/app/ml/trade_gate.py`、`backend/app/risk/manager.py`
- **外部参照**: `/private/tmp/QuantDinger/backend_api_python/app/services/ai_decision_filter.py`、`ai_decision_context.py`、`docs/trading/LIVE_TRADING_SAFETY.md`
- **日期**: 2026-09-22
- **结论**: **方向可行（Phase 3），但 v2 计划在「收敛语义」「影子验证基准」「降级链时序」「state 组装」四处存在设计空洞，直接实施会导致：Phase 3 审计语义漂移、Phase 4 无法验证、laya 可能放松确定性风控。** 建议先补齐下述 C/H 级定义，再进入实施。

---

## 0. 事实基线（代码核实）

- 本项目 laya 已有完整运行时封装：懒加载单例、`threading.Lock` 串行化加载、`device="cpu"`、`asyncio.to_thread` 推理、白名单 + argmax 校验、None=降级语义（`laya_runtime.py:52-150`）。
- manual gate 是 fail-closed 审计关键路径：硬闸门只能收紧不能放松（`manual_order_gate.py:3-23`），LLM 超时/畸形 → `_reject_inline(kind="llm_unavailable", retryable=True)` + `AI_AGENT_ERROR`（`manual_order_gate.py:169-220`）。
- engine 侧 gate 全部是 fail-open 语义：TradeGate 弃权/异常 → 放行（`engine.py:825-872`，`trade_gate.py:24-28`）；风险链是确定性代码（`risk/manager.py:292` `can_open_trade`）。
- QuantDinger 范式（参照）：6 问 choice 单次请求、白名单+prob 校验+confidence≥0.55、确定性收敛（entry=pass ∧ risk≠block ∧ execution≠block ∧ ¬(signal=conflict ∧ regime=adverse 双高置信)）、JEV→LLM→fail-open（`ai_decision_filter.py:252-340`；`LIVE_TRADING_SAFETY.md:40`）。
- **关键差异**：QuantDinger 是 fail-open + 二值 PASS/REJECT；本项目 manual gate 是 fail-closed + 三值 APPROVED/CAUTION/REJECTED；engine gate 是 fail-open + 双开关（shadow/enforce）。

---

## 1. Phase 3「manual gate 6 问判定」架构合理性

### 1.1 接口设计 — 合理但缺批处理与超时

- `predict_choice(state, question_key, question)` 是单问封装，6 问若逐个调用 = 6 次前向（CPU 6×200-500ms = 1.2-3s）。laya 的 `system_one(state, questions)` 支持一次前向批处理（`laya_runtime.py:87-91` 的 `predict` 已接受 questions dict），QuantDinger 也是 6 问一次请求。**应新增「批处理 6 问 + 逐问校验」入口**，而非 6 次 `predict_choice`。
- `predict` 无超时保护：`asyncio.to_thread(self._predict_sync, ...)` 若 laya 推理挂起，会永久占用一个线程池 worker 且调用方无限等待。QuantDinger 对 JEV HTTP 有 `timeout` clamp（1-30s，默认 8s，`ai_decision_filter.py:258-268`）。**必须给 predict 加 `asyncio.wait_for`（建议 30s 上限）**，且超时走 None→LLM 兜底而非崩溃。

### 1.2 state 组装 — 「复用 context_builder」不成立（High）

- `_build_snapshot`（`manual_order_gate.py:527-552`）只含 order/account/positions/recent_trades/rule_flags/market（bid/ask/spread/avg_spread/sentiment）。
- **缺少 signal_alignment / market_regime 两问所需的多周期行情与指标**（QuantDinger Decision Context V2 明确预计算多周期行情/指标/敞口/净值/回撤/最近盈亏/保护/执行条件，`ai_decision_context.py`）。
- 计划写的「复用 context_builder」不成立：`AIContextBuilder.build_full_context`（`context_builder.py`）产出的是**给 LLM 的散文 prompt 段**，且只在 engine 侧装配；`ManualOrderGate.__init__` 只注入 connector/redis/ai_client，没有 market_data/DB 会话/AIContextBuilder。
- 建议：为 manual gate 新建专用 laya state 组装器（`build_laya_state(snapshot, ohlcv_features)`）：snapshot 已有字段 + connector.get_ohlcv 派生特征（connector 有 `get_ohlcv`，`mt5/connector.py:109`）+ 确定性风控数值（drawdown/WR/敞口由 `_emotion_flags` 等已有计算扩展）。不要字面复用 context_builder。

### 1.3 确定性收敛器 — 三值映射未定义（High）

- JEV 收敛是**二值**（PASS/REJECT）且「missing evidence 不得 reject」（`ai_decision_filter.py:38-39, 303-327`）。manual gate 需要 **APPROVED/CAUTION/REJECTED 三值**，且当前 `_normalize_verdict` 白名单就是三值（`manual_order_gate.py:222-234`）。
- 计划未定义：CAUTION 从哪来（risk=caution？execution=caution？signal=mixed？）、data_quality=insufficient 时是放行还是回落、entry=reject 是否唯一 REJECTED 来源。
- **fail-closed 语义冲突**：QuantDinger 的「insufficient 不拒绝」在本项目意味着「不拒绝」可能被当作通过——manual gate 不变量 #1（只能收紧不能放松）没有给 laya 定义位置。laya 判定若可把硬闸门 warn 级标志（如 loss_streak/near_frequency_limit）「宽松化」即违约。
- 建议：把收敛器写成**纯函数**（`converge_laya_verdict(answers, thresholds, rule_flags) -> (verdict, reason, tighten_ok)`），规则单调：确定性 block 标志 → REJECTED 优先；data_quality/signal/regime/risk 任一 insufficient → 回落 LLM（不直接放行）；entry=pass ∧ risk∈{clear,caution} ∧ execution≠block → APPROVED/CAUTION 按 risk/execution=caution 细分。纯函数便于单测（现测试面 `test_manual_order_gate.py` 已很全）。

### 1.4 置信度阈值语义（High）

- 本地 `predict_choice` 把 confidence 重定义为 **max-class 概率**（`laya_runtime.py:101-135`），entropy 置信度另存为 `entropy_confidence`；QuantDinger 的 `min_confidence=0.55` 作用于 **JEV 归一化熵置信度**（`ai_decision_filter.py:297-312`）。
- 两个刻度不可互换：max-prob 在 p=0.87 时熵置信度仅 ~0.59，直接把 0.55 套 max-prob 上阈值几乎恒过 → 6 问 per-question 阈值必须用本项目刻度**重新校准**（或改用 entropy_confidence 对齐 QuantDinger 语义）。计划未定义任何阈值。

---

## 2. Phase 4「engine 开仓许可判定面」— 过度设计风险最高

### 2.1 影子验证无 LLM 参照（Critical）

- engine 开仓路径**当前没有 LLM 判定**：`_check_trade_permission`（`engine.py:804-1040`）= TradeGate + 确定性风控（`risk/manager.py:292`）+ 组合敞口 + 相关性；LLM 只以 `ai_sentiment` 输入出现，且 C4 修复显式让 laya 行绕过过滤（`risk/manager.py:325-332`）。
- 因此「影子验证 → 一致率高后切 enforce」（`task_plan.md` Phase 4）的**一致率没有可比对基准**。若比 laya vs 确定性链/TradeGate → 测的是冗余度（risk 类问题同源特征必然高一致，无意义）；若比 laya vs ai_autonomous orchestrator 长文决策 → 输出契约不同（verdict vs 自由文本+工具调用），不可比。
- 建议：Phase 4 影子阶段改测「laya 6 问 vs（TradeGate+确定性链结果）」的**分歧率与分歧方向**（laya 放行而确定性链拦截 = 危险分歧，必须归零才可 enforce）；或把语义一致性验证集中在有 LLM 参照的 manual gate 影子期，engine 侧先只做延迟/成本观测。

### 2.2 统一判定器边界不清（Critical）

- 计划「TradeGate + 确定性风控 + laya 6 问收敛为统一判定器」：三层 gate 重叠严重。laya 的 risk_check 复判 max_concurrent/daily_loss/敞口/回撤（确定性代码已算），execution_quality 复判 spread/价格新鲜度（manual gate 已有 avg_spread/浮动利润等）。**laya 复判结果若参与决策，存在 laya 放行而确定性闸本应拦截的回归路径**——不变量 #1 没有覆盖 laya。
- 三处 gate 的故障语义不同：TradeGate 弃权/异常 = 放行（`engine.py:842, 872`），确定性风控 = 拦截，manual gate = fail-closed。计划未定义 laya 在 engine 侧故障时是 fail-open（沿用 TradeGate 惯例）还是 fail-closed（沿用 manual gate 惯例）。**混用语义不决，enforce 期必然出现「laya 挂了停单 vs 放单」的方向性争议。**
- 建议（防过度设计）：**不要把 laya 做成第三道并列 gate**。两个可选项：a) laya 6 问作为「确定性链 + TradeGate 通过后」的收紧-only 咨询层（只能把 allow→block，绝不放宽），复用现有 shadow/enforce 双开关；b) Phase 4 砍掉，只做 Phase 3 manual gate + engine 侧 laya 情绪/策略的既有 L0 面。当前证据（manual gate 低频、engine 无 LLM 参照）不足以支撑 b) 之外的第三种形态。

---

## 3. 与现有代码的整合冲突

### 3.1 laya_runtime 懒加载/线程安全/事件循环

- 现有封装本身合格（锁 + to_thread + CPU 固定 + try-import 降级）。
- 三个缺口：
  1. **冷加载 ~136s**（`laya_runtime.py:8`）发生在首次 predict 的 worker 线程内。manual gate 的 review 是后台 task，首个 review 会被阻塞 136s（LLM 兜底时序混乱：laya 未就绪时全部走 LLM，就绪后突然变 laya 主判，影子基线被污染）。engine 侧同理。**建议启动期后台预热 + `available` 状态机区分「未加载/可用/失败」。**
  2. `_load` 失败 → `_available=False` **进程内永久禁用**（`laya_runtime.py:75-79`）：一次瞬时网络失败（HF 下载）会静默把 laya 关到重启。建议区分「依赖缺失（永久）」与「加载失败（可重试）」并配告警指标。
  3. 并发首载：多个 coroutine 同时进 `_load` 会串行阻塞在 `threading.Lock` 上，各占一个线程池 worker → 可能耗尽 `asyncio.to_thread` 默认池（min(32, cpu+4)）。预热可消除。

### 3.2 manual gate fail-closed 审计语义

- 当前 `_review` 只对 LLM 调用包 `wait_for`（`manual_order_gate.py:170` 注释明确「不能包住执行段」）；laya 调用需**独立超时 + 独立失败分类**：外层 `_review_and_maybe_execute` 的 except 会把 laya 异常标成 `kind="llm_unavailable"`（`manual_order_gate.py:158-166`）——字段语义错位，审计里分不清是 laya 还是 LLM 挂。
- 建议：laya 路径新增 `kind="laya_unavailable"`（retryable）+ `AI_AGENT_ERROR` + `stored["laya"]["error"]` 入库；LLM 兜底仍走现有 `llm_unavailable` 分支。
- **影子模式不破坏现有语义**：影子 = laya 与 LLM 都跑、都入库（`stored["laya"]` + `stored["llm"]`），LLM 判定仍权威——这是纯增量，合规。

### 3.3 双开关（shadow/enforce）

- TradeGate 的 shadow/enforce + 弃权不阻断模式（`config.py:325-330`，`engine.py:825-869`）是成熟先例，Phase 3/4 沿用即可，**前提是**：enforce 期 laya 故障语义必须显式声明（见 2.2），且 manual gate 的 enforce 与「防火墙不变量 #1 只收紧」绑定。
- laya 主判 + LLM 兜底会改变审计证据强度：当前每笔人工单都过 LLM 深析并入库 reasoning/risk_flags/emotional（`stored["llm"]`，`manual_order_gate.py:201`）；laya 高置信直接批后，LLM 深析只在兜底时发生，`stored["llm"]` 在批路径消失（findings.md 已提「模板化补全保审计」，但计划没写 enforcement 阶段是否仍保留 LLM 并行深析）。考虑到 review 是后台异步（90s 不阻塞用户提交），**成本收益比存疑：省的是 token，代价是审计证据降级**。建议 enforce 期保留「laya 批 + LLM 深析并行入库」至少到一致率统计达标。

### 3.4 C4 先例必须延续

- `risk/manager.py:325-332` 已确立「laya max-prob 与 LLM 自报置信度刻度不同，概率模型不得作风控拦截依据」。Phase 3/4 的 6 问收敛若把 laya 结果直接喂给确定性闸门，等于绕过 C4 不变量。**laya 判定只能作为独立判定层（自身阈值把关），不得改写确定性风控数值。**

---

## 4. 降级链完整性

### 4.1 期望链

`laya 报错/超时/低置信/畸形输出 → None → LLM 兜底（现有 90s wait_for）→ 两者皆挂 → manual gate: fail-closed reject（kind=llm_unavailable/laya_unavailable）`。

### 4.2 缺口

- **时序污染**：冷加载 136s 内 laya 全量走 LLM（影子基线前段污染）；加载成功后突然切换主判（影子一致率报表断档）。见 3.1.1。
- **超时缺口**：laya predict 无 wait_for（见 1.1）；QuantDinger 有 8s clamp。延迟预算上，laya 200-500ms + LLM 90s 兜底在 review 后台路径可接受（用户侧已返 PENDING_REVIEW），但**无超时保护的 laya 挂起会把 review task 拖到无限期**，且不留审计。
- **部分畸形策略未定义**：6 问中 4 问合法、2 问畸形 → 整链回落 LLM（建议，与 QuantDinger 全有或全无一致）还是部分采用？计划未写。建议**全有或全无**，避免局部采用引入不可解释混合判定。
- **失败方向**：engine 侧 laya 故障方向（放行/停单）未决（见 2.2）；manual gate 侧已明确 fail-closed，建议在 task_plan 中显式写「两者皆挂 → reject + retryable」，与现状一致。

---

## 5. 可测试性 — 影子一致率能否验证「不比 LLM 差」

### 5.1 样本量问题（Medium）

- manual gate 是真钱手动单，低频（每天个位数）；「一致率不比 LLM 差」在 verdict 级别的统计功效需要数百~数千样本，现实周期以月计。engine 信号高频（M15 bot）但无 LLM 参照（见 2.1）。
- **建议**：a) 影子期在 manual gate 同时跑双判定并全量入库（`stored["laya"]` + `stored["llm"]` 同 review_id 关联）；b) **离线回放**历史 `OrderAudit.review` 快照（`_audit_to_dict` 可还原）跑 laya，快速获得统计量；c) 引擎侧用「laya vs 确定性链+TradeGate」分歧率做高频观测。

### 5.2 度量定义（Medium）

- 只看 verdict 一致率会掩盖**方向性不对称风险**：laya 批了 LLM 拒的（真钱危险）与 laya 拒了 LLM 批的（保守）不等价。建议：
  - 成本矩阵（approve-approve / reject-reject / approve-vs-reject 双向），
  - **准入门槛：影子期 laya 对 LLM-REJECTED 样本的放行率 = 0**（严格），
  - 三值下另看 CAUTION 边界（laya 把 CAUTION 判成 APPROVED 是主要风险带）。
- confidence 校准：laya max-prob 需对真实成交结果做 Brier/log-loss 复核（Phase 5 的 AUC 复核轨道可吸收）；仅 ~11 笔真实 trades，统计上无意义，离线回放优先。

### 5.3 可测性本身良好

- 现有测试基建可直接扩展：`test_laya_runtime.py` 用 `MagicMock(spec=LayaRuntime)` 隔离单例；`test_manual_order_gate.py` 覆盖 fail-closed 各分支；`test_trade_gate.py` 覆盖弃权语义。**前提是收敛器做成纯函数**（见 1.3），否则 6 问收敛逻辑只能通过集成测试覆盖。
- 建议新增：收敛器纯函数单测（含三值映射表 + insufficient 回落 + 单调性不变量）、laya 批处理入口的逐问畸形测试、影子双入库审计字段断言、延迟埋点（laya/LLM 各自耗时入库，供延迟/成本对比报表）。

---

## 6. 分级发现清单

### Critical
- **C1. Phase 4 影子验证无 LLM 参照，一致率定义不成立** — engine 开仓路径无 LLM 判定（`engine.py:804-1040`，LLM 仅以 sentiment 输入且 C4 让 laya 行绕过，`risk/manager.py:325-332`）。修改建议：影子期改测「laya vs TradeGate+确定性链」分歧率（laya 放行而确定性链拦截须归零才可 enforce），语义一致率集中在 manual gate 影子期验证。
- **C2. 「统一判定器」边界不清，存在 laya 放松确定性风控的回归路径** — 防火墙不变量 #1「只能收紧不能放松」（`manual_order_gate.py:3-23`）未给 laya 定义位置；三层 gate（TradeGate/确定性风控/laya 6 问）重叠且故障语义不同（engine fail-open `engine.py:872` vs manual fail-closed）。修改建议：laya 6 问作「确定性链通过后的收紧-only 咨询层」，故障方向在 task_plan 显式声明；或砍掉 Phase 4 并列 gate 形态。

### High
- **H1. 6 问 → 三值 verdict 收敛映射未定义** — JEV 收敛是二值且「insufficient 不拒绝」（`ai_decision_filter.py:303-327`），与 manual gate 三值 fail-closed（`manual_order_gate.py:222-234`）冲突；CAUTION 产生条件、insufficient 回落规则未定义。修改建议：收敛器写成纯函数，规则单调（确定性 block 优先 REJECTED；任一 insufficient 回落 LLM；entry=pass ∧ risk≠block ∧ execution≠block 才可批）。
- **H2. laya 主判 + LLM 兜底会降级审计证据** — 当前每笔人工单全量 LLM 深析入库（`stored["llm"]`，`manual_order_gate.py:201`）；laya 批路径将失去 reasoning/risk_flags/emotional 证据（findings.md 已承认需模板补全）。修改建议：enforce 期保留「laya 批 + LLM 深析并行入库」直到一致率达标；laya 6 问原始 answers/probabilities 全量入库（`stored["laya"]`）。
- **H3. 降级链时序与超时缺口** — 冷加载 ~136s（`laya_runtime.py:8`）污染影子基线段；predict 无 wait_for（QuantDinger 有 8s clamp，`ai_decision_filter.py:258-268`）；`_review` 只给 LLM 包超时（`manual_order_gate.py:170`）。修改建议：启动期后台预热；predict 加 30s `asyncio.wait_for`；`_load` 失败区分为「依赖缺失（永久）/加载失败（可重试）」并告警。
- **H4. state 组装「复用 context_builder」不成立** — `AIContextBuilder` 产出散文 prompt 段且只在 engine 侧装配（`context_builder.py`）；`ManualOrderGate` 只注入 connector/redis/ai_client（`manual_order_gate.py:50-53`），`_build_snapshot`（`manual_order_gate.py:527-552`）缺 OHLCV/regime/drawdown/WR。修改建议：新建专用 `build_laya_state(snapshot, ohlcv_features)`（connector.get_ohlcv 可用，`mt5/connector.py:109`）。
- **H5. 置信度阈值刻度不兼容** — 本地 confidence 是 max-class 概率（`laya_runtime.py:101-135`），QuantDinger min_confidence=0.55 作用于熵置信度（`ai_decision_filter.py:297-312`）；直接套用会恒过阈值。修改建议：6 问 per-question 阈值用本项目刻度校准（或改用 entropy_confidence 对齐 JEV 语义）。

### Medium
- **M1. 6 问应单次前向批处理** — `predict` 已支持 questions dict（`laya_runtime.py:87-91`），逐个 `predict_choice` = 6 次前向（1.2-3s CPU）。建议新增批处理 + 逐问校验入口。
- **M2. data_quality/risk_check/execution_quality 大部分可确定性计算** — freshness/spread/rule_flags/敞口已有代码（`_emotion_flags`、preflight、TradeGate），laya 复判无信息增益且有与确定性结果不一致风险。建议仅把 signal_alignment/market_regime/entry_decision 作 laya 判定，其余代码填充进 state/收敛规则。
- **M3. 影子一致率样本量不足** — manual 单低频；建议离线回放历史 OrderAudit 快照 + engine 高频分歧率观测补统计功效。
- **M4. 失败分类错位** — laya 异常会被外层 except 标成 `kind="llm_unavailable"`（`manual_order_gate.py:158-166`）。建议新增 `kind="laya_unavailable"` + `stored["laya"]["error"]` 入库。
- **M5. 一致性度量需方向性成本矩阵** — 单看一致率掩盖「laya 批 LLM 拒」的真钱风险。建议：准入门槛 = 影子期 laya 对 LLM-REJECTED 放行率 0；另监控 CAUTION↔APPROVED 边界。

### Low
- **L1. 6 问文案/候选集未本地化** — 可直接借鉴 QuantDinger `JEV_QUESTIONS`（`ai_decision_filter.py:28-130`）并适配 symbol/timeframe 语境。
- **L2. probabilities/entropy_confidence 应全量入库** — 为 Phase 5 校准（Brier/log-loss）留数据。
- **L3. `_latest_sentiment`（`manual_order_gate.py:570-580`）缺 engine/时间戳字段** — Phase 3 若以 sentiment 作 data_quality 输入，需带来源与新鲜度。
- **L4. QuantDinger 的 ENTRY_ACTIONS/EXCLUDED_STRATEGY_TYPES 跳过语义勿照搬** — manual gate 无策略维度，勿引入「跳过审查」路径；engine 侧 ai_autonomous 无 strategy 时需等价定义。

---

## 7. 推荐落地顺序（供计划修正参考）

1. **先补定义再写码**：6 问批处理接口 + 收敛器纯函数 + 三值映射表 + per-question 阈值校准（C2/H1/H5/M1）。
2. **Phase 3 影子先上**：双判定并行入库（`stored["laya"]` + `stored["llm"]`）、延迟埋点、离线回放历史快照建立一致率基线（H2/M3/M5）。
3. **engine 侧先只做观测**：laya 6 问影子 + 分歧率统计，enforce 决策延后到 manual gate 语义一致率达标且分歧方向归零（C1）。
4. **Phase 4 收敛为「收紧-only 咨询层」**或整体降级为可选轨道，避免三层 gate 叠加的过度设计（C2）。

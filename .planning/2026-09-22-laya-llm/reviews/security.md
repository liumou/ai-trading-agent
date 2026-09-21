# 安全与风控评审报告 — laya 主判 + LLM 兜底（manual gate / engine 判定面）

- **评审对象**：`.planning/2026-09-22-laya-llm/task_plan.md`（v2 计划）、`findings.md`（含第 6 节 QuantDinger 复核）
- **评审范围**：真钱交易系统（MT5 实盘）「交易判定面」由 LLM 主判改为 laya（本地 System One choice 引擎）主判 + LLM 兜底
- **评审日期**：2026-09-22
- **评审者**：安全与风控评审员（默认模型）
- **只读范围**：本报告仅写入 `.planning/2026-09-22-laya-llm/reviews/security.md`，未修改任何业务代码与规划文件

## 执行摘要

计划 v2 的总体方向正确（保留 fail-closed、影子先行、拒绝「全量替换」），但存在 **1 个 Critical、4 个 High、4 个 Medium、4 个 Low** 发现。核心矛盾：

1. **「laya 主判」与项目已落地的不变量「概率模型不得作风控拦截依据」直接冲突**（`backend/app/risk/manager.py:328`）。该不变量已通过 C4 修复落实为「laya 情绪行不得参与 AI 情绪闸门」「Claude 深析行才可参与」（`backend/app/bot/engine.py:787-801`、`backend/app/risk/manager.py:330-345`）。Phase 3 若按「laya 高置信直接裁决、LLM 只兜底低置信/故障」落地，等于把该不变量在**最关键的审计闸门**（manual gate）上推翻。
2. QuantDinger 的 fail-open + JEV 主判**不能**作为本项目 laya 主判的辩护证据：两者语义正交。JEV 是可选开仓过滤门（默认放行、错过单只损失机会成本），最终收敛是确定性规则、min-conf 只约束拒绝侧问题、「缺失证据不得拒绝」。本项目 manual gate 是 fail-closed 的最终闸门（REJECTED 终局不可覆盖），laya 分类误差的后果方向完全不同。
3. 正确的安全映射是 **laya 只收紧、不放松**：laya 高置信 APPROVE 不得跳过 LLM 深析（保住 `stored["llm"]` 审计字段和情绪/风险维度）；laya 高置信 REJECT 必须经 LLM 复核或确定性规则佐证后才可终局；laya 低置信/异常/失败一律回退 LLM 主判（fail-closed 不变）。影子阶段 laya 结果全部落库对照但不参与判定。

## 代码事实基线（评审依据）

- manual gate 契约（`backend/app/services/manual_order_gate.py`）：fail-closed（第 3 条不变量）、REJECTED 终局（第 2 条）、`stored["llm"]` 落库 verdict/confidence/risk_flags/emotional_indicators/reasoning（`_review:169-220`）、`_normalize_verdict:222-240` 白名单校验（畸形即拒）、CAUTION→PENDING_CONFIRM 120s TTL、执行前重验硬状态（`_execute_approved`）。
- 调研不变量（`backend/app/risk/manager.py:326-345`）：「概率模型不得作风控拦截依据」；laya 行（`engine=="laya"`）在 AI 情绪闸门直接 `return True, "OK"` 放行，不参与拦截。
- engine 情绪溯源（`backend/app/bot/engine.py:787-801`）：`_get_ai_sentiment` 随行携带 `engine`（llm|laya），下游据此区分两种 confidence 刻度。
- TradeGate（`backend/app/bot/engine.py:828-865`）：LightGBM 概率门，shadow 只记录不否决、enforce 才否决、弃权（None）绝不阻断、gate 故障放行——**概率模型与阻断语义解耦**的先例。
- laya_runtime（`backend/app/ai/laya_runtime.py`）：`predict_choice:93-135` 重新定义 confidence=最大类概率、保留 `entropy_confidence`、choice≠argmax 视为畸形返回 None；**缺 probabilities 键集校验、sum≈1 校验、NaN/finite 校验**（对比 QuantDinger `_validate_choice_answer:440-471`）。
- QuantDinger（`/private/tmp/QuantDinger/backend_api_python/app/services/ai_decision_filter.py`）：JEV 6 问仅过滤 `ENTRY_ACTIONS` 开仓、grid/dca/martingale 排除；`_evaluate_jev` 收敛为确定性规则（pass 且 risk/execution 非 block 且非 signal_conflict+regime adverse 高置信）；min_confidence（默认 0.55）**只约束 `entry_decision/risk_check/execution_quality` 三问**；`LIVE_TRADING_SAFETY.md` 明确 JEV/LLM 皆不可用 → fail-open 放行并记录，平仓/止损/止盈绕过。
- 配置（`backend/app/config.py:308-319`）：`laya_confidence_threshold=0.85`（情绪 3 类预筛）、`laya_strategy_confidence_threshold=0.6`（策略 6 类抽取）、`laya_model=convaiinnovations/laya`（无 revision/checksum pin）。
- 供应链：`backend/requirements.txt:41` `laya==0.3.4`（已 pin），`transformers>=4.45,<5`、`huggingface-hub>=0.26,<2`（宽范围）；`backend/Dockerfile:30` `snapshot_download('convaiinnovations/laya', ...)` 无 revision pin；权重 ~1.7GB；`laya_runtime._load` 运行期可经 `HF_ENDPOINT`/默认端点下载。
- 快照（`manual_order_gate.py:527-554`）：`order{symbol,type,lot,sl,tp}/account{...}/positions[]/recent_trades[]/rule_flags[]/market{bid,ask,spread,avg_spread,sentiment{label,score}}`；`comment` 只进 `review` 不进快照；`market.sentiment` 来自 Redis `sentiment:latest:{symbol}`（`_latest_sentiment:559-571`），**无 engine 溯源、无 score 范围/NaN 校验、无长度 cap**。
- RSS 清洗（`backend/app/ai/news_sentiment.py` `_clean_headlines`）：去指令分隔符（---、```、<|、|>、###）、单条 ≤200 字符、总量截断 3000；laya 预筛命中（conf≥0.85）写 DB 审计行（带 `engine=laya`、raw_response 含 probabilities）并写 Redis 缓存，score 用 label 量化映射（bullish 0.5 / bearish -0.5 / neutral 0.0）。

---

## 评审维度 1：真钱风险与「概率模型不得作风控拦截依据」不变量

### 1.1 不变量是否被计划违反

**是（条件性，取决于 Phase 3 的落地语义）**。计划措辞「laya 主判 + LLM 兜底链（保留 fail-closed 审计语义，影子模式先行）」存在两种读法：

- **读法 A（危险）**：laya 高置信 → 直接裁决 APPROVED/REJECTED；LLM 只在 laya 低置信/故障时兜底。这是「概率模型作为真钱拦截依据」的直接实现，违反 `risk/manager.py:328` 不变量，且把 `stored["llm"]`（reasoning/emotional_indicators）从审计关键路径上拿掉。
- **读法 B（安全）**：laya 只作收紧侧预筛（veto + pre-screen），所有 binding 判定仍经 LLM 或确定性规则，影子先行累积证据。这与现有 TradeGate（shadow/enforce 拆分、弃权不阻断）和 C4 修复一脉相承。

findings.md 第 6.3 节已正确识别「fail-open vs fail-closed 冲突」并选择影子先行，但 task_plan Phase 3/4 未把「laya 不得作为最终 APPROVE/REJECT 的唯一依据」写成硬约束。**计划必须显式采用读法 B**。

关键差异（QuantDinger ≠ 本项目）：

| 维度 | QuantDinger | 本项目 manual gate |
|---|---|---|
| 闸门性质 | 可选开仓过滤门 | 最终审计闸门（单提交必经） |
| 故障语义 | fail-open（放行并记录） | fail-closed（拒单可重试） |
| 误拒后果 | 错过一单（机会成本） | 合法手动单被终局拒绝（REJECTED 不可覆盖） |
| 误放后果 | JEV 误放=策略本来要开的单 | laya 误放=跳过 LLM 深析的实盘单（读法 A 下） |
| 收敛器 | 确定性规则 + 「缺失证据不得拒绝」 | 计划未定义 6 问的确定性收敛规则 |
| 置信度用途 | 只约束拒绝侧 3 问，min 0.55 | 计划未定义 per-question 阈值 |

不对称性结论：**laya 的分类误差在「收紧」方向（拒掉不该拒的）只造成可用性问题（可修复：加 LLM 复核），在「放松」方向（放掉该拦的）直接变成真钱风险且绕过既有 LLM 审查——两者不能等价看待**。因此哪怕影子一致率 99%，也不允许 laya 高置信 APPROVE 跳过 LLM。

### 1.2 fail-closed vs fail-open 冲突的处理是否正确

**部分正确，有一处未决**。计划保留 fail-closed 是正确的：手动单被拒可重试，阻断成本低，且审计完整性要求高。但有两处未决：

1. **laya REJECT 的终局性问题**：现有 LLM REJECT 是终局（不变量 2），`_reject_inline` 以 `kind="ai_rejected"` 落库。若 laya REJECT 同样直接终局，一次 laya 模型回归/状态编码 bug（如行情断流 → data_quality=insufficient 被当拒因）会让手动交易静默停摆，用户看到的是误导性的「AI review rejected」。**建议：laya REJECT 需 LLM 佐证（LLM 同拒 → 终局；LLM 不拒 → 降级 CAUTION 或回退 LLM 主判）；只有确定性规则（preflight/emotion rule_flags block）可不经 LLM 终局**。
2. **影子模式期间的行为守恒**：影子 laya 行不得改变现有 review 状态机、不得推送 TRADE_BLOCKED 事件/WS/通知、不得影响 `_audit_to_dict` 的 kind/retryable 还原（`manual_order_gate.py:716-731`）。计划未定义影子行的标识与状态隔离。

---

## 评审维度 2：审计完整性

### 2.1 verdict/reasoning/risk_flags/emotional_indicators 的入库语义

现状（LLM 主判）审计字段全部落库 `stored["llm"]`（`_review:201`）。laya 主判后字段去向必须分层定义：

- **verdict**：laya 6 问收敛可产出，但需确定性收敛器（参照 QuantDinger `_evaluate_jev`）而不是裸 choice；二分类 choice 无法表达 CAUTION（`risk_check=caution`/`execution_quality=caution` → CAUTION 的三态映射必须显式定义，QuantDinger 只输出 pass/reject，此为本项目特有差距）。
- **reasoning（模板化）**：模板化 reasoning 本身**不破坏**审计价值——laya 6 问的 choice+probabilities+model 版本+阈值是**完全可复现**的结构化推理，比自由文本 LLM reasoning 更适合取证（可离线重放）。破坏审计价值的唯一方式是**只存模板文本、不存结构化判决数据**。建议 `stored["laya"] = {model_id, model_revision, thresholds, per_question:{choice, probabilities, confidence, entropy_confidence}, convergence, latency_ms, shadow}`，模板文本仅用于前端展示。
- **risk_flags**：已有确定性来源（`_emotion_flags` 的 `rule_flags`），laya 无长文生成也不应输出 flags；计划应明确 laya 6 问中的 risk_check 映射到 rule_flags 的方式（如 risk_check=block 时引用对应 rule_flag id），避免两个来源不一致。
- **emotional_indicators**：**这是最脆的审计字段**。它是情绪化交易审计的关键信号（配 `_emotion_flags`/事后 accountability）。laya 无法生成 → 模板化后该维度退化为「无」，审计下钻能力丧失。若 laya 高置信 APPROVE 跳过 LLM，该字段恒为空，等于静默移除一项审计信号。**建议：emotional 维度要么在 6 问中显式建模（如 question: emotion_risk：revenge/martingale/fomo 的 choice），要么 CAUTION/边界行强制 LLM 补全；laya 行该字段置「engine=laya, not_assessed」显式标记缺省，而不是模板写「无」**。

### 2.2 审计一致性风险

- `_audit_to_dict` 与前端轮询契约（`reason=error_message`、`kind/retryable` 从 review JSON 还原）对 laya 行未定义：`review.llm` 键名误导下游解析（laya 行没有 `stored["llm"]` 时，依赖 `review.llm.reasoning` 的消费者会拿到 None）。建议引入 `review.judge = {engine: "laya"|"llm", model, ...}` + `review.chain = ["laya", "llm", "final"]` 显式记录判定链。
- **confidence 刻度混用**：现有 `stored["llm"].confidence` 是 Claude 自报置信度，laya 是 max-class 概率。两者写入同一 review JSON 的不同键位可容忍，但任何跨行聚合报表（平均置信度、阈值滑点）必须按 `engine` 分层；已有 C4 教训（`risk/manager.py:326-345`）应复制到报表层。
- 影子行入库量：每个手动单 review 增加一个 6 问 JSON 块（含 probabilities），量级几 KB，可接受；但不要把影子 laya 拒绝写成 `BotEvent(TRADE_BLOCKED)`（现状 `_reject_inline` 的行为），否则审计事件表和前端告警被影子污染。

---

## 评审维度 3：攻击面

### 3.1 RSS 标题（已清洗，但注意预筛与判定两个消费方）

`_clean_headlines` 已剥离指令注入分隔符并截断（`news_sentiment.py:28-45`）：对 LLM 提示注入是必要且充分的缓解；对 laya（本地模型、无工具、无自由文本输出）注入的破坏面更小——影响极限是**偏置情绪分类**。但存在二级放大链：

- `sentiment:latest:{symbol}` 由预筛/LLM 两条路径共写（`news_sentiment.py:120-175`），manual gate 的 `_latest_sentiment:559-571` 只取 `label/score`，**丢弃 engine 溯源**。laya 行的 score 是量化映射（0.5/-0.5/0.0），LLM 行是连续值（任意 [-1,1]），两种刻度混进同一个快照字段，且 `_latest_sentiment` 对 score **无类型/范围/NaN 校验**。若 laya 6 问里有一问消费 market.sentiment（大概率有 signal_alignment 类问题），一个 NaN 或畸形 score 进入 state，可能产生不可预期分类。**建议：快照 sentiment 携带 engine、score clamp [-1,1]、NaN/超长拒绝、label 白名单；6 问设计把 sentiment 定位为弱辅助信号，不作为 block 依据**（与 QuantDinger「缺失证据不得拒绝」一致）。

### 3.2 manual gate 快照中用户可控字段

快照组成（`_build_snapshot:527-554`）：用户可控 = `symbol/order_type/lot/sl/tp`（均经 `preflight_order` 校验、数值钳位），`comment` 已从快照排除（只进 `review`，≤60 字符）。`recent_trades/positions/market tick` 来自券商，`rule_flags` 为确定性规则。**结论：直接注入面窄，且攻击者本来就是下自己单的用户，操纵 own-params 去翻 verdict 的威胁模型无实际收益**。真正的残余风险是 (a) 情绪缓存投毒（3.1）与 (b) 恶意构造参数使 laya 系统性误判从而影响共享账户状态——本项目账号隔离（per-account lock）把 (b) 限制在本账号。**建议保持 comment 不进 state；对 laya state 全字段上线前做 schema 校验（类型/长度/范围/NaN）**。

### 3.3 laya 权重/依赖供应链

- **HF 模型无 revision/checksum pin**（`config.py:308-319`、`Dockerfile:30`、`laya_runtime._load:69-85`）：`convaiinnovations/laya` 未 pin commit，`snapshot_download` 运行期/构建期都取最新快照；权重或仓库被替换/回滚后，下次冷加载静默换模型行为。**这是真钱判定面的 root-of-trust，优先级高**。
- **运行期网络**：`HF_ENDPOINT` 或默认端点下载在 `_load` 内发生（`laya_runtime.py:74-80`），运行环境若有出网能力，下载源可变（镜像劫持/中间人）且无校验。
- **`import laya` 在模块顶层**（`laya_runtime.py:21-27`）：pip 包 `laya==0.3.4` 已 pin，但 import 时点即执行任意代码；依赖兄弟包（`transformers>=4.45,<5`、`huggingface-hub>=0.26,<2`）宽范围未锁 hash，依赖解析漂移会改变 import 生态。
- **缓解建议**：(1) 固定 `settings.laya_model` 为 `convaiinnovations/laya@<commit-sha>` 并在构建/启动时校验 safetensors 的 sha256 清单；(2) 权重只允许构建期下载（Docker 层），运行期禁用出网下载（缺缓存即 `available=False` → 回退 LLM，fail-closed 语义不变）；(3) 依赖锁 min-hash 或固定兄弟依赖已知良好版本；(4) 镜像内做 SBOM/基线哈希审计。

---

## 评审维度 4：置信度阈值

### 4.1 三个阈值的语义不可比

- `laya_confidence_threshold=0.85`（`config.py:312`）用于情绪**三分类** max-class 概率预筛——只在「是否省一次 LLM 调用」上起作用，误判成本低（该层不拦截，见 `risk/manager.py:330-334`）。**当前是合理配置**。
- `laya_strategy_confidence_threshold=0.6`（`config.py:314`）用于**六分类**策略名抽取——只影响展示/记录（`strategy_used`），0.6 偏低但风险可控（白名单 + keyword 兜底）。
- QuantDinger `min_confidence=0.55` 用于**拒绝侧 3 问**的每问置信度，且下游还有确定性收敛 + 「缺失证据不得拒绝」兜底。**它是「拒绝必须有足够证据」的门槛，不是「通过的依据」**。

**结论：manual gate 6 问不能照搬任何一个数字**。二分类 `entry_decision` 的 max-class 概率天然 ≥0.5，0.85 的 max-class 在二分类上和三分类 0.85 的判别力完全不同（类越多、概率被摊薄，阈值可比性差）。

### 4.2 max-class 概率 vs 熵置信度的语义

- max-class 概率 = 「模型对所选类的把握」，适合预筛（差校准也不致命，因为不拦截）。
- 熵置信度 `1 - H(p)/log(k)`（`laya_runtime.py:103-107` 保留为 `entropy_confidence`）= 「概率分布有多决定性」，**与校准无关**，更适合作为拦截依据的**辅助**信号（拒绝侧要求「决定性 + 具体证据」）。
- 两者联合使用的最小安全组合：**拒绝侧** = per-question choice ∈ 确定性拒因集合（risk=block / execution=block / signal_conflict+regime adverse）**且** 该问 max-class ≥ 阈值 **且** entropy_confidence ≥ 阈值2；**通过侧** = 只作收紧预筛，不做 binding。这才是 QuantDinger 语义在本项目的正确翻译。
- 另注意 `predict_choice` 当前缺 **NaN/finite** 校验：`max(probabilities.values())` 遇 NaN 时后续比较全部 False（NaN 比较恒假），`choice==argmax` 防御可能在 NaN 下意外放行、`>= threshold` 对 NaN 恒 False 走回退——两条路径都「静默」，应在运行时层显式 `math.isfinite` 拒绝。这同时影响情绪/策略预筛与未来 gate。

### 4.3 校准前提

计划在 Phase 3 前应补一道门槛：laya 的 max-class 概率需在**本项目数据分布**上验证校准（可靠性曲线/ECE），而不是假定 HF 权重自带校准。TradeGate AUC=0.739 且 purge-gap 未修（findings 第 5 节）——判定面用概率阈值前，先修 purge-gap 并积累真实样本，否则阈值没有可解释性。

---

## 评审维度 5：影子验证本身的风险

1. **静默行为改变**：影子 laya 接入点若在 `_review` 内与 LLM 串行，laya 冷加载 ~136s（`laya_runtime.py:38` docstring）发生在 `asyncio.create_task` 的审查任务里，会拖慢首个审查（`LLM_REVIEW_TIMEOUT_S` 只包 LLM 调用本身，`_review:169-176`，不会误取消执行段——这点设计正确）；`asyncio.to_thread` 用默认线程池，136s 阻塞会临时占满线程池，影响其它 async 任务。**建议：启动时预热加载 + 专用线程池 + 影子 laya 与 LLM 并发执行、laya 结果不阻塞判定**。
2. **异常被吞**：laya 路径全部「异常→None→回退 LLM」是正确降级，但计划未定义**观测**：laya 不可用率/畸形率/超时率/拒绝率漂移（如一天内 laya REJECT 比例突增）必须作为健康指标/告警，否则「影子永远没异常」只是「没人统计异常」。C4 教训（`engine` 溯源）同样适用于影子报表：一致性报表必须按 verdict 分层 + 按 rule_flags/情绪标签分层，否则「总体一致率 95%」可能掩盖「风险单上 100% 不一致」。
3. **DB/事件膨胀与污染**：影子 laya 拒绝若复用 `_reject_inline`/`_log_event(TRADE_BLOCKED)`，会写误导性审计事件并 WS 推送；影子 6 问 JSON 每单几 KB 可接受，但要约定 probabilities 定点截断存储。**建议：影子行 `stored["laya"].shadow=true`、状态机不变、不事件不推送；报表聚合走独立查询，远离业务事件表**。
4. **一致率的统计前提不成立**：手动单样本量极小（findings 提及真钱路径审计行少），laya vs LLM 影子一致率在低样本下无统计功效；且两模型共享同一快照切片/同一情绪缓存，系统性偏差会传染「一致」（伪一致率 → 过早切 enforce 的风险）。**建议：enforce 切换须设置最小样本数 + 分层一致率下限 + 独立校验集（不参与训练/调参的数据）。**

---

## 分级发现清单

### Critical

- **C1 — laya 高置信直接裁决会推翻「概率模型不得作风控拦截依据」不变量**。
  证据：`backend/app/risk/manager.py:328`（不变量注释）、`:330-345`（laya 行跳过 AI 情绪拦截）；`task_plan.md` Phase 3「laya 主判 + LLM 兜底」未定义「laya 不得作为最终 APPROVE/REJECT 唯一依据」。
  建议：显式采用 veto-only 语义——laya 高置信 APPROVE 仍须过 LLM 深析（保住 `stored["llm"]` 审计字段）；laya REJECT 须 LLM 佐证或确定性规则佐证后才终局；laya 低置信/失败/异常一律回退 LLM（fail-closed 不变）。Phase 4 engine 层同规则（并入现有确定性风控 + TradeGate shadow/enforce 拆分）。

### High

- **H1 — laya 主判跳过 LLM 时 `emotional_indicators` 与 reasoning 审计维度丢失**。
  证据：`manual_order_gate.py:201`（`stored["llm"]` 全字段落库）、`:222-240`（`_normalize_verdict` 契约）；findings 第 4 节承认需「模板化补全」。
  建议：laya 行落库结构化判决 `stored["laya"]={model,revision,thresholds,per_question,convergence,latency_ms}`（模板文本仅为展示层）；emotional 维度显式建模为 6 问之一或 CAUTION/边界行强制 LLM 补全；缺失字段显式标记 `not_assessed`，禁止用模板「无」掩盖。

- **H2 — QuantDinger 的 fail-open 先例被误读为「laya 主判可行」的辩护**。
  证据：`ai_decision_filter.py`（JEV 可选过滤门 + 确定性收敛 + 缺失证据不得拒绝）、`LIVE_TRADING_SAFETY.md`（JEV/LLM 皆不可用 → fail-open 放行）。
  建议：在计划中补一张「QuantDinger vs manual gate 语义对照表」（见维度 1.1），把 min-conf 的「拒绝侧门槛」语义原样翻译：laya 只收紧、缺失证据不得拒绝、确定性收敛器必须显式定义（含三态 CAUTION 映射——QuantDinger 没有此态）。

- **H3 — laya 权重/依赖供应链无 root-of-trust**。
  证据：`config.py:308-319`（无 revision）、`Dockerfile:30`（`snapshot_download` 无 pin）、`laya_runtime.py:21-27`（顶层 import 任意代码）、`requirements.txt:41` + 宽范围兄弟依赖。
  建议：pin `revision=<sha>` + safetensors sha256 清单校验；权重仅构建期下载、运行期只读缓存（缺缓存即 `available=False` 回退 LLM）；依赖锁 min-hash；镜像 SBOM。

- **H4 — 影子模式的状态/事件/观测隔离未定义，存在静默污染与异常吞没**。
  证据：`manual_order_gate.py:716-731`（前端轮询依赖 `kind/retryable`）、`_reject_inline`（写 TRADE_BLOCKED 事件 + WS 推送）、`_review` 内串行接入点未定义。
  建议：影子行 `shadow=true`、状态机不变、不写事件不推送；laya 不可用/畸形/超时/拒绝率漂移作为健康指标告警；laya 与 LLM 并发执行、冷加载移出请求路径。

### Medium

- **M1 — 0.85/0.6/0.55 阈值语义不可比，6 问门槛被默认可搬运**。
  证据：`config.py:312-314`（3 类/6 类）、`ai_decision_filter.py`（每问 0.55 且仅拒绝侧 3 问）。
  建议：per-question 阈值在本项目数据上校准（ECE/可靠性曲线 + purge-gap 修复后），拒绝侧用「choice∈确定性拒因集合 + max-class 阈值 + entropy_confidence 阈值」联合判定。

- **M2 — `laya_runtime.predict_choice` 缺 probabilities 完备性与 finite 校验**。
  证据：`laya_runtime.py:115-127`（无 sum≈1/keyset/isfinite），对比 `ai_decision_filter.py:451-466`（`set(raw_probabilities) != options` + sum≈1 + choice==argmax）。
  建议：补齐（键集==白名单、sum≈1±1e-3、每值∈[0,1]、全部 `math.isfinite`），畸形一律返回 None 走回退——情绪预筛可缓，gate 前必须。

- **M3 — 快照 `market.sentiment` 无 provenance/范围校验，laya 6 问消费前有刻度混淆与投毒面**。
  证据：`manual_order_gate.py:559-571`（只取 label/score）、`news_sentiment.py:120-175`（laya 量化 score vs LLM 连续 score 同写 Redis）。
  建议：快照 sentiment 携带 `engine` + label 白名单 + score clamp[-1,1] + NaN/长度拒绝；6 问把 sentiment 定位为弱辅助信号。

- **M4 — 「REJECTED 终局 + laya 主判」组合下 misjudge 变为静默停摆**。
  证据：`manual_order_gate.py` 不变量 2（REJECTED 无 override）；`laya_runtime.py:30-85`（加载失败/模型回归路径）。
  建议：laya REJECT 需 LLM 佐证或确定性证据；保留 `llm_unavailable`（基础设施）与 `ai_rejected`（分析结论）事件语义区分（现状已区分，勿在 laya 路径合并）；拒绝率漂移告警。

### Low

- **L1 — `_latest_sentiment` 无长度 cap，Redis 异常值膨胀快照**。
  建议：label ≤64 字符、score 数值化，读后整体大小限制。

- **L2 — 审计键名 `review.llm` 对 laya 行误导下游解析**（`_audit_to_dict`、前端轮询）。
  建议：引入 `review.judge`/`review.chain` 显式判定链，`review.llm` 仅存 LLM 行。

- **L3 — 影子一致率低样本/共享偏差下的伪一致**（findings 第 5 节数据基础）。
  建议：enforce 切换设最小样本数 + 分层一致率下限 + 独立校验集；报表按 verdict/rule_flags/情绪分层输出混淆矩阵。

- **L4 — per-question debug 日志 + 6 问 JSON 入库的膨胀**。
  建议：聚合指标代替逐行日志；probabilities 定点截断；影子行与业务事件表分离。

---

## 结论与切换条件

**总体判定**：方向可行，但「laya 主判」的落地语义必须收敛为 **veto-only 预筛（读法 B）**；在 C1/H2 解决前不得进入 enforce。建议的变更闸门顺序：

1. Phase 3 仅做影子（laya 不参与判定、不写误导事件、健康指标齐全）；
2. 影子报告达到统计门槛（样本数、分层一致率、误放/误拒方向差异）后，先放开「laya REJECT 预提示」（不终局），观察误拒噪音；
3. 只有确定性风控 + LLM 深析仍保留在 binding 路径的前提下，才允许讨论 enforce 阈值/收敛器；laya APPROVE 侧在任何阶段都不允许跳过 LLM。

---

### 附：评审方法与证据索引

本次评审只读以下文件（未修改任何文件）：`task_plan.md`、`findings.md`、`backend/app/services/manual_order_gate.py`、`backend/app/ai/laya_runtime.py`、`backend/app/bot/engine.py`（`_get_ai_sentiment`/`_check_trade_permission`）、`backend/app/risk/manager.py`（C4 逻辑）、`backend/app/ai/news_sentiment.py`、`backend/app/config.py`、`backend/requirements.txt`、`backend/Dockerfile`、`/private/tmp/QuantDinger/backend_api_python/app/services/ai_decision_filter.py`、`/private/tmp/QuantDinger/docs/trading/LIVE_TRADING_SAFETY.md`。

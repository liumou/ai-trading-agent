# 外部证据复核报告：QuantDinger JEV ↔ laya 方案（2026-09-22）

复核人：外部证据复核员（默认模型）
复核对象：`.planning/2026-09-22-laya-llm/task_plan.md`、`findings.md`（第 6 节）
对照证据：`/private/tmp/QuantDinger`（origin `https://github.com/OpenByteInc/QuantDinger.git`，HEAD `96cec2d`）
- `README_CN.md` L359-439（JEV 决策流程）
- `backend_api_python/app/services/ai_decision_filter.py`（746 行，JEV_QUESTIONS / _evaluate_jev / _evaluate_llm / 校验链）
- `backend_api_python/app/services/ai_decision_context.py`（479 行，Decision Context V2）
- `backend_api_python/app/services/ai_decision.py`（LiveAIDecisionClient：decision_key 缓存 / max_calls_per_run / billing）
- `backend_api_python/tests/test_ai_decision_filter.py`（11 例，含校准置信度测试）
- `docs/trading/LIVE_TRADING_SAFETY.md`、`app/services/billing_config.py`
- 本项目 `backend/app/ai/laya_runtime.py`、`.planning/2026-09-21-laya-research/findings.md`

## 结论摘要

**findings.md 第 6 节对 QuantDinger 的整体解读与源码一致**：JEV 只替换「开仓前决策门」这一窄决策面、6 问 choice、Decision Context V2 确定性预计算、JEV→LLM→fail-open 兜底链、校验链五项主体全部属实。复核发现 **3 处精度偏差**（其中 2 处影响计划设计，列为 High）与 **4 类漏抄工程细节**（列为 Medium/Low）。核心结论方向（全量替换不可行、判定面 laya 化 + LLM 兜底可行）维持不变，但计划需补上两个高影响前提：**① 基础 laya 的模型就绪度（校准/微调）不可与托管 JEV 外推；② JEV 的 allow-by-default 语义与项目 fail-closed 审计路径的冲突必须显式中和**。

## Q1. findings.md §6 与源码逐条核对

| findings §6 主张 | 源码证据 | 判定 |
|---|---|---|
| JEV 只做开仓门（pre-trade entry filter） | `ENTRY_ACTIONS={open_long,open_short,add_long,add_short,buy,sell}`；`close_long` → `skipped` / `exit_orders_are_not_filtered`（`evaluate()` L152-161 + `test_exit_orders_bypass_ai`）；LIVE_TRADING_SAFETY「exits, stop loss, take profit, emergency risk reduction bypass the filter」 | ✅ 准确。补充：add/buy/sell（加仓）也被过滤；`EXCLUDED_STRATEGY_TYPES={grid,dca,martingale,layered_martingale}` 整体跳过 |
| 其余 LLM（fast_analysis / strategy_review / ai_chat / research_agent）仍纯 LLM | JEV 过滤器仅被 `quick_trade_ai.py`、`strategy_v2/live_execution.py`、`alpaca.py` 调用；research_agent 等仍走 `LLMService` | ✅ 准确 |
| 6 问 choice | `JEV_QUESTIONS` 恰 6 键（data_quality / signal_alignment / market_regime / risk_check / execution_quality / entry_decision），`JEV_CHECK_OPTIONS` 白名单 | ✅ 准确 |
| Decision Context V2 确定性预计算 | `ai_decision_context.py`：`context_version=2`；多周期（阶梯 1m→1w）K 线汇总 + 指标（MA/RSI/MACD/ATR/volume/position/support-resistance）；position snapshot（count/gross_notional/unrealized_pnl）；equity/initial_capital/drawdown/exposure；strategy_performance（今日/累计已实现盈亏、连亏数）；protection；order_budget；`_cached_market_rows` 复用 K 线缓存 | ✅ 准确 |
| JEV→LLM→fail-open 兜底链 | `evaluate()`：JEV 异常 append `failures` → LLM；LLM 异常 → `allowed=True`（`ai_provider_unavailable`/`error_allowed`）；两者皆不可用 → `skipped` + 放行；全程 `_persist` 审计 | ✅ 准确 |
| 校验链：白名单 / 和为 1 / argmax / confidence ≥ 0.55 | `_validate_choice_answer`：choice∈options；`set(probabilities)==options`（完整且无多余）；每值∈[0,1]；`abs(sum-1)>0.001` 报错；`probabilities[choice]==max`；confidence 必须存在 | ✅ 主体准确 |
| 「confidence ≥ min(0.55) 任一失败视为畸形」 | `min_confidence`（默认 0.55，可配 `JEV_MIN_CONFIDENCE`）**只对 entry_decision / risk_check / execution_quality 三问强制**；signal_alignment / market_regime 置信仅用于 `directional_block` 合取（conflict∧adverse∧两者≥min）；data_quality 完全**不参与收敛**（仅审计展示） | ⚠️ 精度偏差（High-3） |
| 确定性收敛规则 | `allowed = entry=="pass" ∧ risk!="block" ∧ execution!="block" ∧ ¬directional_block`；reason 分级（risk_block / execution_block / signal_conflict / entry_reject） | ✅ 准确 |
| JEV 不可用→LLM 兜底→都不可用→fail-open 并记录 | 同上；另有 billing 分支：`billing_insufficient_credits` → `skipped` + 放行且**不调用 provider**；provider 不可用时 `_refund_credits` 退款 | ✅ 准确（补充 billing 分支） |
| 「QuantDinger 生产验证（…）」 | 仓库为功能实现 + 11 个单测，无真实生产运行/回测证据 | ⚠️ 「生产验证」过强，宜称「代码级验证/生产级设计」（Medium-5） |

**findings §6 未提到但计划应知悉的源码细节**：
1. **opt-in 语义**：过滤器需每策略/每请求显式 `ai_decision_filter=true` 才启用；回测路径用 `BacktestAIDecisionClient` 完全绕过外部 AI（`ai_decision.py`）。
2. **超时**：JEV 默认 8s（`JEV_TIMEOUT_SECONDS`，clamp 1–30s）；LLM 兜底默认 10s（`AI_DECISION_TIMEOUT_SECONDS`，clamp 1–30s）；`latency_ms` 入库。
3. **JEV 置信度是独立返回的校准值**，不等于最大类概率：`test_calibrated_jev_confidence_accepts_clear_entry` 中 risk_check `clear=0.71` 但 `confidence=0.57`，按 0.57 判阈。
4. **审计信封**：每条决策持久化 `decision_uid/provider/model/decision/allowed/confidence/reason/fallback_reason/probabilities_json/checks_json/request_snapshot/billing_json/latency_ms`，`ON CONFLICT DO NOTHING` 幂等；skip 也有明确 reason（`filter_disabled` / `exit_orders_are_not_filtered` / `strategy_type_not_supported` / `ai_not_configured` / `billing_insufficient_credits`）。

## Q2. JEV 云端 API vs laya 本地权重

**JEV = 云端 API**（证据 `_jev_config` L501-526）：默认 `base_url=https://api.typesafe.ai/v1`，`JEV_API_KEY`（Bearer 认证）、`JEV_MODEL=jev-latest`，`POST /systemone`。QuantDinger 侧配套**信用/计费系统**：`billing_config.cost_ai_decision_filter=1`（信用点）；调用前 `check_and_consume`，provider 不可用 `_refund_credits` 退款，额度不足 `skipped` + 放行 + 记录。

**laya = 本地权重**（证据 09-21 findings §1/§7 + 项目 `laya_runtime.py`）：HF `convaiinnovations/laya`（421M ModernBERT-large / 322M mmBERT，fp32 ~1.6–1.7GB）；CPU 单次 200–500ms；项目实测冷加载 **~136s（含构建）**；首次加载需联网（HF 端点国内需 `HF_ENDPOINT` 镜像）；无按次计费、无数据外发。

**对影子验证的含义**：
- laya 本地推理零边际调用费 → 影子可**全量、无配额压力**；JEV 每次调用计费（先扣费、失败退款）→ 影子/生产都要走信用系统。本项目影子层无此成本约束，但 **LLM 兜底仍按次计费** → 应照抄 `max_calls_per_run`（默认 25/run）类预算与内容寻址缓存（见 Q5.1）。
- 影子吞吐约束不同：laya = CPU 延迟（200–500ms/次，6 问一次前向）；JEV = 8s 外部超时 + 服务可用性。
- **数据主权**：laya 状态不出本机（GOLD MT5 持仓/盈亏/策略参数不外发）；JEV 需把完整 Decision Context 发给第三方 → 若本项目未来走云端需第三方数据处理评估，本地 laya 反而是优势。
- **故障形态不同，兜底设计不能照搬**：JEV 故障 = 网络/API 中断/额度/计费（外部随机故障）；laya 故障 = 首载下载失败/OOM/依赖缺失/`USE_TF=0` 死锁（本地确定性故障，项目 runtime 已 try-import 降级）。生产部署需补：镜像内权重持久化预热（避免首载 136s 与联网依赖）、内存预算（GB 级）。

## Q3. JEV 与 laya 是否同源 / 模型形态可比性

**无法从 QuantDinger 仓库确认 JEV 权重基于 laya**：`JEV_MODEL` 是不透明标识 `jev-latest`，代码只做 HTTP 调用；仓库内无 "laya" 字样；README 只链接 TypeSafe 文档（`docs.typesafe.ai`，`POST /v1/systemone`）。对外只能表述为「同类 System One 引擎」，不能称「同源」。

**结构同构证据充分（形态可比）**：
- 请求形态：QuantDinger 发 `{"model","state","questions"}`；laya 包 API 即 `agent.system_one(state, questions)`（项目 `laya_runtime._predict_sync` 直接调用同名方法）。
- 响应信封：JEV 解析 `payload["answers"][question] = {choice, probabilities, confidence}`；laya 返回 `result["answers"][key] = {choice, probabilities, confidence}`（`laya_runtime.predict_choice` 按同一信封解析）。
- choice + 完整概率 + 置信度三原语一致。

**置信度语义不可比（关键差异）**：
- JEV 返回**独立校准 confidence 字段**（测试证明 ≠ max-prob，且可直接判阈）。
- laya 原生 confidence 为归一化熵（`1 - H(p)/log k`），09-21 调研实测**默认过自信**（ECE 0.466 → 温度重拟合后 0.081）；项目 runtime 已把 confidence 重定义为 **max-prob**（更保守但**非校准**）。
- 含义：JEV 的 0.55 阈值语义（3/6 问）不能直接搬到 laya 的 max-prob 上；必须先在本地数据上做温度校准或改用校准后概率判阈，否则阈值形同虚设（见 High-1/High-3）。

## Q4. 6 问范式迁移性（GOLD M15 MT5）

**6 问骨架可迁移**：data_quality / signal_alignment / market_regime / risk_check / execution_quality / entry_decision 是通用开仓前门控，与项目「manual gate 判定 + context_builder + TradeGate」结构对应。

**判据深度绑定 crypto 语境，需重写**：
- `risk_check` 判据点名 leverage / exposure / drawdown / loss-streak / protection / order budget（合约保证金语义）；
- `execution_quality` 判据点名 price freshness / reference-price deviation / order type / market type(spot/swap) / protection；
- `signal_alignment` 判据点名「every available timeframe in context.market_evidence」——其多周期阶梯（1m→1w）与 GOLD M15 的周期结构不同。
- 项目侧应重写为 MT5 语义：spread / swap / stop-out / margin / session / news 事件、M15 周期阶梯（与 `_decision_timeframes` 类似定义）。

**两个高影响点（计划未处理）**：
1. **JEV 门是 allow-by-default，与项目 fail-closed 冲突（High-2）**：`entry_decision` 指令明示「Reject only for concrete evidence of a directional contradiction, material portfolio risk, or unsafe execution. Missing evidence alone must not reject」；收敛规则只在明确矛盾/风险/执行问题时拒；`data_quality` 完全不入收敛。这是服务 fail-open 哲学（保证 AI 故障不阻断交易/退出）的设计。项目 `manual_order_gate` 是 **fail-closed 审计关键路径**（09-21 调研：verdict 白名单 + fail-closed + reasoning/risk_flags/emotional 全字段入库）。计划只说「保留 fail-closed 审计语义」但未规定如何中和 JEV 的 allow 偏差——若照抄 6 问措辞与收敛式，主判官会被翻转为 allow-by-default。
2. **模型就绪度前提缺失（High-1）**：QuantDinger 的 JEV 是托管生产模型（对这套问题做过对齐/校准）；本项目是**未微调的基础 laya**（09-21 调研：基础 checkpoint 在 typed-decisions 基准 0.36 ≈ 随机 0.318 / 多数类 0.461；英文 checkpoint 对非英文崩溃且高置信犯错）。6 问交易门对基础 laya 是全新 OOD 任务 → 影子一致率预期接近随机。Phase 3 应把「微调/校准达标」设为前置或设定一致率准入门槛，而不是默认「范式可行 = 模型可行」。

## Q5. 计划漏抄但应抄的工程细节

1. **每运行外部调用预算 + 内容寻址缓存**（`ai_decision.py` `LiveAIDecisionClient`）：`decision_key = sha256(strategy_run_id:profile:model:prompt_hash:input_hash:symbol:as_of:output)`；命中缓存不调 LLM 不花钱；`max_calls_per_run` 默认 25，超限 → `ai.callBudgetExceeded`。→ 项目 laya→LLM 兜底链应照抄：按 run 设兜底预算 + 同 state 去重缓存，`as_of` 进 key 保证 point-in-time 去重正确。注意：该机制在 `ai_decision.py`（策略回调的 LLM 结构化判定），**不在** JEV 过滤器内；两层都要借鉴。
2. **计费/信用系统**（`ai_decision_filter` + `billing_service`）：调用前扣费、provider 不可用退款、额度不足不调 provider 直接 skip+放行+记录。→ 云端 JEV/付费 LLM 兜底必须抄；laya 本地推理不需要，但 LLM 兜底成本需进影子报表（计划已有成本对比 ✅）。
3. **Decision Context 的 point-in-time 约束**（`ai_decision_context.py`）：`captured_at_utc`、`data_age_seconds`、`stale_after_seconds = 3×interval`、`is_stale`、`fresh/stale_timeframes`、`data_quality` 汇总（complete/partial/unavailable）；决策时间线展示。→ 项目 `context_builder`/`build_features` 应补同样的时效标记，特征只取 as_of 前数据（防未来数据），审计存 `request_snapshot`。
4. **校验链完整性（按问分档）**：probabilities 集合==白名单选项集（不多不少）、sum≈1（容差 0.001）、choice==argmax、confidence 存在、**3/6 问强制阈值**（entry/risk/execution），signal/regime 仅用于定向阻断合取，data_quality 纯审计。→ 项目 `laya_runtime.predict_choice` 目前只有 argmax + label 白名单（在调用方），**无 sum-to-one、无选项集完整校验、无按问阈值**；计划映射表写「齐备」不实。
5. **超时与幂等审计**：JEV 8s / LLM 10s clamp；决策 uuid + provider/model/decision/allowed/confidence/reason/fallback_reason/probabilities_json/checks_json/request_snapshot/billing_json/latency_ms 全量入库、`ON CONFLICT DO NOTHING`；skip 也有明确 reason。→ 项目 manual gate 审计需扩展同一信封（laya provider、checks、probabilities、fallback_reason、latency）。
6. **按策略 opt-in + 回测绕过**：`ai_decision_filter` 标志每策略/每请求启用；`BacktestAIDecisionClient` 回测零外部调用。→ 项目应在策略配置加 gate 开关，影子/回测路径不触发 LLM 兜底。

## 分级发现清单

### Critical
- 无。源码与计划核心结论一致，无导致方向性错误的矛盾。

### High
- **H1 模型就绪度**：计划把「QuantDinger 范式可行」外推为「基础 laya 6 问可用」，缺校准/微调前提（JEV 是托管校准模型，本项目 laya 未微调：typed-decisions 0.36≈随机、ECE 0.466 过自信、英文外崩溃）。证据：09-21 findings §2、`laya_runtime.py` docstring。建议：Phase 3 前置「LightGBM 0.739 AUC 门控为基线 + 影子一致率准入门槛」，未达标不 enforce；微调按 Phase 5 数据条件。
- **H2 语义冲突**：JEV 门 allow-by-default（entry_decision「Missing evidence alone must not reject」+ 收敛只在明确矛盾/风险/执行问题时拒 + data_quality 不入收敛）与项目 manual_order_gate 的 fail-closed 审计语义冲突，计划只声明未规定如何中和。证据：`ai_decision_filter.py` entry_decision 指令与 `allowed` 收敛式、09-21 findings manual gate 审计关键路径。建议：重写 6 问判据与收敛器（audit-critical 问 insufficient → 拒绝/兜底），影子阶段统计「laya pass / LLM reject」反向分歧率。
- **H3 校验链「齐备」不实**：计划映射表称置信度/白名单校验已齐备，但 `predict_choice` 无 sum-to-one、无选项集完整校验、无按问阈值，且 confidence 重定义为 max-prob（非校准），与 JEV 独立校准 confidence 不可比（测试：clear=0.71 / confidence=0.57）。证据：`laya_runtime.py:96-135`、`test_calibrated_jev_confidence_accepts_clear_entry`。建议：按 JEV 校验链五项落地适配层，阈值默认更保守或先做温度校准。

### Medium
- **M4 校验链表述精度**：findings §6「confidence ≥ min(0.55) 任一失败视为畸形」不精确——阈值只强制 entry/risk/execution 三问，signal/regime 仅用于 conflict∧adverse 合取阻断，data_quality 不参与收敛。证据：`_evaluate_jev` L286-320。建议：设计文档按「3/6 强制阈值 + 定向合取 + 1 问审计」表述，避免实现成 6 问全阈值误伤。
- **M5 表述过强**：「QuantDinger 生产验证」仓库内无真实生产证据，只有实现 + 11 个单测。证据：`test_ai_decision_filter.py`、`git log`。建议：改称「代码级验证/生产级设计」。
- **M6 云/本地运维差异未纳入**：JEV = API key/计费/额度/退款/8s 超时；laya = 冷加载 136s/权重 1.6-1.7GB/首载联网/HF 镜像/CPU 200-500ms/无计费无外发。证据：`_jev_config`、`laya_runtime.py`、09-21 findings §7。建议：生产部署章节补 laya 预载预热、镜像权重持久化、内存预算；影子全量零成本优势写明。
- **M7 漏抄工程细节**：每 run LLM 预算（`max_calls_per_run=25`）+ `decision_key` 内容寻址缓存、Decision Context point-in-time 时效标记（`stale_after=3×interval`）、超时 clamp、幂等审计信封（request_snapshot/billing_json/latency_ms）、按策略 opt-in + 回测绕过。证据：`ai_decision.py` L206-296、`ai_decision_context.py`、`_persist`。建议：纳入 Phase 3/4 设计清单（详见 Q5）。

### Low
- **L8 判据 crypto 绑定**：6 问判据（leverage/exposure/market_type spot-swap/reference-price deviation/多周期阶梯）需重写为 GOLD M15 MT5 语义（spread/swap/stop-out/margin/session/news）。证据：`JEV_QUESTIONS` criteria。建议：Phase 3 定义问题时逐条改写判据与周期阶梯。
- **L9 同源表述**：README 确认存在「TypeSafe Jev / JEV System One」（README_CN L359-439、README.md L394-478），但仓库无法证明 JEV 权重基于 laya；仅结构同构（/systemone + answers 信封 + choice/probabilities/confidence）。建议：对外表述用「同类 System One」而非「同源」。

## 证据文件索引
- `/private/tmp/QuantDinger/backend_api_python/app/services/ai_decision_filter.py`：L21-22（ENTRY_ACTIONS/EXCLUDED_STRATEGY_TYPES）、L24-120（JEV_QUESTIONS）、L146-250（evaluate 兜底链）、L252-335（_evaluate_jev 收敛）、L417-475（校验链）、L501-526（_jev_config）、L528-590（billing consume/refund）
- `/private/tmp/QuantDinger/backend_api_python/app/services/ai_decision_context.py`：L177-226（summarize_market_bars 时效字段）、L230-268（build_market_evidence）、L300-330（_position_snapshot）、L372-413（build_strategy_decision_context context_version=2）
- `/private/tmp/QuantDinger/backend_api_python/app/services/ai_decision.py`：L206-219（decision_key 内容寻址）、L219-225（max_calls_per_run）、L350-365（_consume_credit）
- `/private/tmp/QuantDinger/backend_api_python/tests/test_ai_decision_filter.py`：L170-210（test_calibrated_jev_confidence_accepts_clear_entry）、L244-270（test_malformed_jev_answer_falls_back_to_llm）、L325-345（test_low_confidence_jev_result_falls_back_to_llm）、L363-390（test_insufficient_credits_skip_provider_without_blocking_order）
- `/private/tmp/QuantDinger/backend_api_python/app/services/billing_config.py`：L15（cost_ai_decision_filter=1）
- `/private/tmp/QuantDinger/README_CN.md` L359-439；`/private/tmp/QuantDinger/docs/trading/LIVE_TRADING_SAFETY.md`
- `/Users/liumou/PycharmProjects/ai-trading-agent/backend/app/ai/laya_runtime.py`：L96-135（predict_choice 校验与 confidence 重定义）
- `/Users/liumou/PycharmProjects/ai-trading-agent/.planning/2026-09-21-laya-research/findings.md` §1/§2/§7/§8/§14

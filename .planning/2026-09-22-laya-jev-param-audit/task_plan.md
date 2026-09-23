# Task Plan: Laya 请求参数对齐 JEV 优化计划（批准后执行）

## Goal
分阶段优化 laya 三个接口的请求参数，把 6 问闸门的证据面对齐 QuantDinger JEV
Decision Context V2，同时保持影子非干扰不变量（H-3）与 fail-closed 降级语义。
每阶段以真实数据/模型复测为验收 gate；**本计划须用户批准后才开始执行代码改动**。

## 原则（约束）
- 只改影子输入 / 渲染 / 摘要纯函数；不改交易行为、不阻塞关键路径、不新增依赖。
- 影子不干扰：laya 故障/超时绝不影响 LLM 路径与真实订单（现有 H-3 不变量）。
- 每阶段可独立交付与回滚；验收标准见各阶段。
- 模型面（微调 / 换托管模型）是唯一解锁点；其余阶段是"必要但不充分"的前置。

## Phase 0：修复 prose 渲染字段键不匹配（低风险，纯渲染）
- 目标：ManualGate 路径 prose state 不再静默丢证据。
- 任务：
  1. `render_laya_state_prose` 兼容 ManualGate 快照键：account `equity/floating_profit/realized_daily_pnl`、
     order `lot/sl/tp`、market `bid/ask/spread/avg_spread/sentiment`（短句化，遵守 512 token 预算）；
  2. 复用 `_field` 容错取键；新增单测：ManualGate 形状快照渲染含关键字段、engine 形状输出不变；
  3. 回归 laya 相关测试（现有 157 passed / 1 skipped 基线）。
- 验收：ManualGate 快照 prose 含 balance/equity/日盈亏/点差/SL/lot/情绪；最大快照 token < 512；测试全绿。
- 风险：低。依赖：无。
- **状态：完成**（2026-09-22；单测 6 个新增；laya 回归 194 passed/1 skipped；token 实测见 findings §6.2）。

## Phase 1：engine 路径证据面增强（对齐 JEV，零额外 I/O）
- 目标：market/portfolio/performance/execution 四块补足，全部来自已取数据（df、positions、DB 预取窗口）。
- 任务：
  1. `build_market_summary` 扩展 ~18-22 字段：+ rsi14、atr14、atr_pct、macd_state、price_position_50、
     ma5/10/20、support/resistance（近 50 根高低）、latest_bar_time_utc + data_age_seconds + is_stale
     （从 df index 计算，零 I/O）；volume_ratio 视 df 是否有 tick_volume 而定（无则跳过并文档化）；
  2. portfolio_risk：positions 扩展 entry/current/profit（先验证 `order_executor.get_open_positions`
     返回 dict 是否含 entry_price/current_price；无则保持现状并文档化缺口）；+ equity（balance+浮盈，若可得）；
     drawdown/gross_exposure 仅在有净值数据时加入；
  3. strategy_performance：把 recent_win_rate 升级为 recent_exit_pnl[:N] + consecutive_losses
     （复用 engine 现有 recent_wr 预取窗口的只读 DB 查询，不增加关键路径等待）；
  4. execution_quality 输入：喂第 1 条产出的 data_age/is_stale（"价格新鲜度"）；无 tick/spread 时该问
     在 engine 路径仅能回答新鲜度 → 决策点（改问句措辞 或 该问不参与 engine 收敛）留到 Phase 2 实验；
  5. prose 渲染扩展对应字段 + 长度预算单测更新。
- 验收：engine 快照 state 覆盖 JEV 核心子集（多周期摘要 18+ 字段/新鲜度/敞口/连续亏损）；
  最大快照 < 512 token；单测全绿；240 样本复测（变体 C3）记录判定分布变化。
- 风险：中（token 预算、df 无 volume 列、positions 无 entry/current）。依赖：Phase 0。
- **状态：完成**（market 18+ 字段/新鲜度/敞口/连续亏损均已进 snapshot；900 字符护栏 + 真实 tokenizer 验证）。

## Phase 2：多周期与问句/收敛策略对照实验（实验驱动，先离线）
- 目标：验证增强输入在未微调模型上的效果，决定问句/收敛器去留。
- 任务：
  1. 离线变体 C3（Phase 1 增强）+ C4（C3 + H1 摘要，复用 regime.py 的 H1 拉取，仅回放脚本、不进生产影子）：
     240 样本真实模型回放（复用 `laya_real_data_eval.py` 协议）；
  2. 统计 verdict 分布、逐问 label/置信度、方向命中（2h/6h/1d）、ECE、与确定性链分歧；
  3. 依据结果决定：signal_alignment 去留、market_regime 措辞、execution_quality 是否参与收敛、分问阈值；
4. 结论写入 findings + 报告。
- 验收：C3/C4 vs A/C1 对比表 + 统计检验；每问明确"保留/重写/删除/降级为审计"建议。
- 风险：中（预期增强输入在未微调模型上仍无显著技能——这正是要证实/证伪的）。依赖：Phase 1。
- **状态：完成**（n=240 全量；对比表 + 显著性见 findings §6.3）。
  结论：C3/C4 无方向技能（p≥0.10）；增强输入只打乱逐问判定（regime 225/240 翻转、
  data_quality sufficient 147→0）；C4 仅更保守。→ 输入面到此为止，进入 Phase 3 决策。

## Phase 3：模型面决策（解锁点，需用户单独批准）
- 选项 A：领域微调本地 laya（RLCD / 合成标签管线）—— 评估数据标注成本、算力、验收协议（微调后重跑 C1 协议）；
- 选项 B：切换托管 JEV（TypeSafe systemone API，QuantDinger 同源、托管校准）—— 评估延迟/成本/网络依赖
  （本项目 Railway CPU 环境、无外网保证）；
- 选项 C：保持 laya 仅情绪/策略通道，6 问交易判定回退 LLM/确定性链（放弃 gate）。
- 本阶段只出评估报告（工作量/风险/证据需求），不实施。
- **状态：Phase 3-A（本地微调）已执行完毕且验收未达标**——head-only 微调学会了合成标签规则，
  但持出集上无方向技能（entry_pass 2h/6h/1d 全部 p≥0.51，McNemar p=1.0），判定仍 100% ESCALATE。
  结论：合成标签（signal=change_5/sma21、entry=未来 6h 方向）本身可学习但无超额收益，能力上限被
  标签上限锁死；若继续微调，唯一可能有价值的路径是真实成交盈亏标签 + 更大/更久数据（超出当前
  范围与算力）。
- **B/C 评估报告已完成**：`phase3-bc-report.md`（B=托管 TypeSafe JEV，6 问契约与本项目完全同构、
  可原样复用 state，估 2-4 人日、需凭据+外网+计费核实；C=停用 6 问判定保留情绪/策略通道，生产现状
  即 de-facto C，估 0.5-1 人日、零行为变化）。**待用户拍板**：是否有 JEV_API_KEY / 是否批准 B 的
  连通冒烟与 240 样本回放 / 或批准 C 落地。

### Phase 3-A（用户 2026-09-22 选定：本地 laya 微调）执行子计划
模型事实：编码器 ModernBERT-large（hidden 1024，~395M）；2 层决策头；`DecisionModel.forward`
直接暴露每候选 logits；`collate_items` 支持 label（官方训练路径）；本机 CPU-only（MPS 不可用，6 线程）。

- **Step 3A.1 数据集（合成标签，确定性规则，读库只读）**：`backend/scripts/laya_finetune_data.py`
  - 复用 eval 回放（GOLD M15，EMA9/21 信号，200 根回看），按月分层抽样 n≈3000、seed 42；
  - 每样本：Phase 1 增强 snapshot（含 recent_profits 扰动、模拟 staleness）→ `render_laya_state_prose`；
  - 逐问确定性标签（无泄漏：signal/regime 只用 change_5/sma21/vol；entry 用 6h 未来方向，容差 0.05%）；
  - 时序留出：最后 20% 月份作 val（防邻近样本泄漏）。
- **Step 3A.2 训练**：`backend/scripts/laya_finetune.py`（零新依赖，CPU）
  - 默认 head-only（`detach_encoder=True`）+ 可选解冻末 N 层；AdamW；CE over 候选 logits；
  - 校验集 CE/accuracy；产出 `backend/models/laya_ft/*`（safetensors + rl_agent_config + tokenizer + encoder，
    可被 `laya.load(path)` 直接加载）。
- **Step 3A.3 验收（微调后复测）**：`laya_real_data_eval.py` 加 `--model-dir`；持出集 n=240 c1 协议
  对比 base vs FT：verdict 分布、signal_alignment aligned 率、entry_pass 方向命中 vs 基率（显著性）、ECE。
- 验收标准：FT 在持出集上 entry_pass 命中率显著 > 基率（p<0.05）或 verdict 分布出现可用判定
  （非 100% ESCALATE）+ ECE 不劣于 0.07；否则回到 Phase 3 决策（B/C）。
- 风险：CPU 训练慢（预期 head-only 数百到数千步可行）；合成标签与实际目标存在偏差（文档化）；
  ModernBERT-large 全参微调本机不可行（>1 天），只做 head-only / 浅层解冻。
- 状态：3A.1 数据集完成（train 2505/val 495）；3A.2 训练完成（600 行 × 3 epochs，head-only，
  val acc 0.34→0.66）；3A.3 复测完成（n=240 c5 配对比 base：entry_pass 无显著提升 p≥0.51、
  判定仍 100% ESCALATE）→ **验收未达标**，回到 Phase 3 决策（B/C）。详见 findings §6.4。

## Phase 4：观测与上线门（依赖 Phase 3 结果）
- 影子观测期（现有 `laya_engine_observations` 表）继续收集；证据面升级后新增判定质量对比维度；
- 一致率/危险分歧阈值达成（如一致率 ≥70% 且收紧分歧可控）才考虑 enforce；报表字段随 Phase 1 扩展。

## 验收总口径
- 每阶段可独立交付/回滚；全部完成后：3 接口 state 与 JEV 证据面对齐、缺失清单（findings §2.3 八项）逐项闭环或文档化豁免、
  复测报告与统计检验齐全、影子不变量测试全绿。

## Decisions（执行前需用户逐条确认）
| # | 决定 | 依据 |
|---|---|---|
| 1 | Phase 0 先行（渲染键兼容） | ManualGate prose 静默丢证据，纯渲染低风险 |
| 2 | Phase 1 只加零额外 I/O 摘要字段 | 观测零额外 I/O 约束（评审 H-3/H-4） |
| 3 | Phase 2 离线实验后才动问句/收敛器 | 实测证明问句/阈值调优在未微调模型上无效，须先有增强输入证据 |
| 4 | Phase 3 模型面单独批准 | 涉及成本/外部依赖/产品方向 |

## Errors / 风险登记
| 风险 | 缓解 |
|---|---|
| 512 token 预算冲突 | 字段短句化 + 长度预检单测（现有渲染预算单测扩展） |
| df 无 volume 列 | volume 字段跳过并文档化，不硬凑 |
| positions dict 无 entry/current | 保持现状并文档化缺口；Phase 2 实验给出影响 |
| 已存影子样本可比性 | 如需，给 state 加 context_version（同 JEV），报表按版本分流 |
| 未微调模型无技能（实证） | Phase 0/1 只是必要条件；Phase 3 才是解锁点，计划如实标注 |

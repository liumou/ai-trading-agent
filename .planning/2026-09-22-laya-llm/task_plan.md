# Task Plan: Laya 交易判定改造方案（v3 — 经四路评审修订）

## Goal
在真钱交易系统（MT5/GOLD）中，把「交易判定面」以 **laya（System One choice 引擎）veto-only 收紧层** + LLM 深析 + 确定性风控的方式落地：只收紧、不放松、不跳过审计。

## Next Step
观测开关已打开（2026-09-22 用户批准）：代码默认 `laya_gate_engine_shadow=True` + `.env` 设 `LAYA_ENABLED=true` / `LAYA_GATE_SHADOW=true` / `LAYA_GATE_ENGINE_SHADOW=true` / `LAYA_HF_ENDPOINT=hf-mirror` → **待部署**（`alembic upgrade head` 建 `laya_engine_observations` + 重建/重启容器）→ 进入 **4 周观测期**；满 4 周或 ≥150 样本出分歧报告（scripts/laya_engine_report.py），再决定收敛为 veto-only 或终止。

## Current Phase
Phase 2（complete，评审修订完成）→ Phase 3 待用户批准

## 四路评审结论（2026-09-22）
评审报告：`.planning/2026-09-22-laya-llm/reviews/{architect,security,validation,external-evidence}.md`
共识：方向可行（Phase 3 影子先行），但 **v2 的「laya 主判、LLM 兜底」必须下调为「veto-only 收紧」**；收敛器、验收门槛、影子基准、审计字段四处在落地前必须定义。

## Phases

### Phase 3.0: 设计冻结（complete）
- [x] 定义收敛器**纯函数**（backend/app/ai/laya_gate.py，17 单测通过）：6 问（二值 pass/reject 语义）→ 三分类 APPROVED/CAUTION/REJECTED 映射 + CAUTION 确认流映射（QuantDinger 无此态，本项目 `_VERDICTS` 三分类必须自定义）
- [ ] verdict 级置信度聚合规则（max-class 概率刻度，非熵置信度）
- [x] 验收门槛量化：n≥300 样本、致命分歧 0 例 + 单侧 95% 上限 ≤1%、一致率 Wilson 下限 ≥0.85、兜底率 ≤5%（见 design-converger.md）
- [x] laya 6 问判据改写为 GOLD/M15/MT5 语义（见 design-converger.md 6 问表）
- **Status:** pending

### Phase 3.1: laya_runtime 校验链补齐（complete）
- [x] `predict_choice` 补 JEV 级五校验：选项键集完整、probabilities sum≈1、NaN/finite、choice==argmax、label 白名单（畸形一律回退，防 NaN 下意外放行）
- [x] 单次前向批处理 6 问（新增 `predict_choices`，单测验证一次 predict 调用）
- [x] `asyncio.wait_for` 超时（laya_gate_predict_timeout_s=30）+ `warmup()` 预热 + 配置 laya_gate_shadow/enforce/threshold
- **Status:** pending

### Phase 3.2: ManualGate 影子验证（veto-only 语义，complete）
- [x] laya 作「确定性链通过后的收紧-only 咨询层」：**laya APPROVE 不跳过 LLM 深析**；laya REJECT 须 LLM/确定性规则佐证后才终局（安全 C1 / 架构 C2）
- [x] 影子隔离：只落 `stored["laya"]`，状态机不变、不推送（测试验证 laya 故障不改变 LLM 路径）
- [x] 审计：落库结构化 `stored["laya"]`（engine/decision/confidence/reasons/checks/answers/latency）+ 保留 LLM 全量深析并行入库；emotional 显式 `not_assessed_by_laya`
- [x] 新增 `kind="laya_unavailable"` 语义：影子层 `decision:"UNAVAILABLE"` 独立留痕，不占用 `llm_unavailable`
- **Status:** pending

### Phase 3.3: 影子报表与准入门槛（complete）
- [x] 指标：3×3 混淆矩阵、逐问一致率 + Kappa、致命分歧（laya PASS / LLM REJECT 反向放行率 = 0）、延迟、兜底率
- [x] 样本补充：影子期全量落 `state_snapshot`（manual_shadow_reviews 专表）供离线回放与分歧人工复核
- [x] 灰度：laya_gate_rollout_pct 配置（0-100）+ enforce=False 即 kill switch；报表 CLI 输出验收门槛 PASS/FAIL
- **Status:** pending

### Phase 4: engine 开仓侧（降级为观测先行，用户已批准 2026-09-22，实施完成）
- [x] 设计文档：`phase4-observation.md`（观测点/分歧定义/时间盒 4 周/最小侵入）
- [x] 仅观测：测「laya vs TradeGate+确定性链」**分歧率**（engine 路径无 LLM 判定参照，C1 修正）
- [x] 不建并列 gate 形态；若观测有价值，收敛为同款 veto-only 收紧层，否则终止
- [x] 实施：`laya_engine_observation.py`（市场摘要/快照/分歧分类纯函数 + 零副作用观测器）、`laya_engine_report.py`（报表纯函数 + CLI）、`LayaEngineObservation` 专表 + 迁移 `c2d3e4f5a6b7`、配置 `laya_gate_engine_shadow`（默认 False）、engine wrapper（`_check_trade_permission` → inner 改名 + 观测生命周期）
- **Status:** complete（待观测期数据）

### Phase 5: 数据基础 + 校准（前置条件未变）
- [ ] purge-gap 修复重测 AUC（LightGBM 0.739 作 laya 后的基线对比）
- [ ] 温度校准（ECE 0.466 过自信；per-question，阈值刻度不可比时拒绝侧用「确定性拒因 + max-class + entropy_confidence」联合）
- [ ] 权重供应链：pin HF revision + safetensors sha256 清单 + 仅构建期下载（`import laya` 顶层执行，须 root-of-trust）
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| v2「laya 主判」→ v3「veto-only 收紧层」 | 安全 C1：高置信直接裁决推翻 `risk/manager.py:328`「概率模型不得作风控拦截依据」不变量；架构 C2：只收紧不放松 |
| 6 问筛选：signal_alignment/market_regime/entry_decision 交 laya；data_quality/risk_check/execution_quality 大多确定性可算，不复判 | 架构 M2：无信息增益的问题不花前向 |
| 验收门槛量化（n≥300/致命分歧 0/Wilson≥0.85/兜底率≤5%/灰度渐进） | 验证 C-2：原「一致率高后切 enforce」不可判 |
| 收敛器先冻结并写单测 | 验证 C-1：6 问二值 → 三值映射缺失则影子指标无法计算 |
| LLM verdict 不作唯一 ground truth | 验证 H-1：真实 trades ~11 笔，一致率高≠质量高；叠加规则回放 + 分歧人工复核 |
| 模型就绪度不直接外推 QuantDinger | 外部 H1：JEV 是托管校准模型，本项目 laya 未微调（typed-decisions 0.36≈随机） |
| archive: 决策缓存 decision_key / max_calls_per_run / point-in-time stale_after 采纳 | 外部 M7：防重复调用与过期状态判定 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 子代理角色模型路由不可用（model_not_found） | 改用默认模型重发；4 路全部完成 |
| 并发上限（thread limit） | 先等待/关闭已完成代理再补发，最终 4 路齐 |
| 测试基线不可复现（之前称 59 passed） | 实测相关文件 43 passed/1 skipped；全量 876 passed/23 failed 且失败与本次无关——回归门固定子集 |

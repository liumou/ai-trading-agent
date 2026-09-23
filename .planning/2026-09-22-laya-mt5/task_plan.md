---

# Task Plan: Laya 输入数据量与真实 MT5 成功率调研

## Goal
用**真实 MT5 历史数据**（Postgres `ohlcv_data`，由 MT5 collector 写入；必要时 MT5 Bridge
`get_ohlcv_range`）回答四个问题，并给出可复现证据：
1. 调用 laya 时，我们实际喂了多少/什么数据？
2. 这些数据是否足够支撑开仓判定？
3. 成功率是否被测试过？成功率是多少？
4. 是否有提高空间？具体在哪？

## Next Step
**调研已完成**（Phase 1-4 全部执行，真实数据 + 真实模型，无 mock）。
补充审计结论：证据不足的主因是**格式**（JSON→prose 后 sufficient 0→61%），但未微调模型
无交易判定能力（能力探针证伪）。行动建议见 `deliverables/laya-data-audit.md` §6.5。

## Current Phase
Phase 4 — 报告完成（含 §6 格式审计与行动建议；交付：报告 + 三个复现脚本）

## 已知代码事实（2026-09-22 只读代码核实，详见 findings.md）
- **情绪通道** `laya_runtime.laya_sentiment_choice`：state 仅 `{symbol, headlines[:3000]}`，
  截断 3000 字符，无任何行情/账户数据。
- **6 问闸门**（`laya_gate.laya_gate_review` + `laya_engine_observation`）：state =
  order + account{balance, positions_count, daily_pnl, recent_win_rate} +
  positions(≤5, 压缩) + market 摘要（**≤8 个标量字段**：last_close / change_1_pct /
  change_5_pct / vol_14 / range_position_50 / price_vs_sma9 / price_vs_sma21，
  源自 ≤200 根 M15 的确定性计算）。无原始 OHLCV、无多周期（H1/H4 代码线存在但
  未喂给 laya）、`recent_trades=[]`、`rule_flags=[]`（最终摘要替代，观测零额外 I/O 约束）。
- **既有“成功率”证据**：
  - LightGBM AUC **0.739**（`laya_synth_baseline.py`，合成样本 34,542 条）——是基线模型，**不是 laya 本体**。
  - laya 未微调（外部证据 typed-decisions 0.36 ≈ 随机；JEV 是托管校准模型，本项目 laya 不是）。
  - laya 6 问**尚无基于真实 MT5 数据的成功率测试**；观测期（4 周 / ≥150 样本）刚起步。
  - 现有单测/pytest 均为 mocked layer，`_laya_api_verify.py` 只验证 1 个情绪样本的 API 兼容性。
- **真实数据源**：`ohlcv_data` 表（GOLD M15/H1 等，只读查询用例已存在
  `laya_synth_baseline.py::load_from_db`）；MT5 Bridge `get_ohlcv_range`。

## Phases

### Phase 1: 真实 MT5 数据盘点（完成）
- [x] `ohlcv_data` 盘点：GOLD M15 34,552 根（2025-04-01→2026-09-17，~457 天，周末缺口正常）；
      GOLD H1 8,666；另 BTCUSD/GER40/US30 亦有数据
- [x] MT5 Bridge 冒烟通过（GOLD_ 别名，实时价 4368.3，账号 XMGlobal-MT5 9 已登录）
- [x] 抽样策略：按月分层、固定种子、n=360（EMA9/21 规则信号非 0 的 17,535 个有效 bar 中抽样）
- **Status:** complete

### Phase 2: 真实数据回放实验（完成）
- [x] 360 样本 × 变体 A/B，生产同构 snapshot（`build_laya_engine_snapshot` + `build_market_summary`）
- [x] 真实 laya 隔离 venv 推理（权重缓存，中位 1.47s/次）
- [x] 指标：verdict 分布、逐问分布/置信度、方向命中率（2h/6h/1d）、ECE 校准、阈值扫描、与参照链分歧
- **Status:** complete

### Phase 3: 输入充分性对照实验（完成）
- [x] 变体 B = A + H1 摘要 + ATR14/RSI14 + 波动趋势 + 近 12 根原始收盘价（真实数据确定性预计算）
- [x] 结果：增强输入在 0.4-0.6 阈值下判定分布零变化（全 ESCALATE），逐问被推向更负面
      （signal_alignment 100% conflict、entry_decision 100% reject）→ 瓶颈在模型，不在数据量
- **Status:** complete

### Phase 4: 报告与建议（完成）
- [x] `deliverables/laya-data-audit.md`（数据量清单、成功率 + 统计检验、充分性结论、提高空间建议）
- [x] 复现脚本：`backend/scripts/laya_real_data_eval.py` + `backend/scripts/laya_eval_analyze.py`
- **Status:** complete

## 成功率口径（先定义再算）
- 真实 trades 仅 ~10-30 笔，不能直接用“交易盈亏”当成功率。
- 主口径：laya 判定 vs 确定性链 / 未来标签 的**一致性 + 方向准确率** + 基率对照；
- 若数据允许（标签可靠），补 AUC/ECE 作为校准性指标。

## 约束
- 全程只读：不写生产库、不开仓、不推送、不改 .env；laya 推理用权重缓存（hf-mirror）
- 不新增依赖；复用现有 `laya_synth_baseline.py` 的只读 DB 加载路径与 `laya_engine_report`
- 复现性强：脚本化 + 固定种子/抽样；报告落盘

## Deliverables
- `.planning/2026-09-22-laya-mt5/findings.md`（证据持续更新）
- `deliverables/laya-data-audit.md`（最终报告）
- `backend/scripts/laya_real_data_eval.py`（回放实验复现脚本，只读）

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 计划须经用户批准后执行 | 用户 2026-09-22 明确要求“计划经过我同意，才能执行” |
| 成功率主口径 = 与确定性链/未来标签的一致率，而非交易盈亏 | 真实 trades 太少（~11 笔），盈亏口径无统计效力 |
| 用 Postgres ohlcv_data（MT5 写入）作为“真实 MT5 数据”主要来源 | 已有只读加载用例，避免依赖 Bridge 在线可用性 |
| 需要真实 laya 模型推理（隔离 venv /tmp/laya-api-venv，权重已缓存） | 单测 mock 不能回答“成功率” |
| 用 EMA9/21 规则信号复现“系统想开仓”的判定输入（生产无历史 signal 记录） | 与生产确定性链同源；文档化局限 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| （空，待执行期补充） | |

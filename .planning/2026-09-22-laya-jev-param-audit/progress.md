# Progress — Laya 请求参数对齐 JEV（审计 + 计划）

## 2026-09-22
- [x] 恢复规划状态：既有 `.planning/2026-09-22-laya-mt5/`（数据量/成功率调研）已完成；
      新建本计划目录并 pin `.planning/.active_plan`。
- [x] 只读代码核实（本项目）：三入口 state 构造（laya_runtime.py）、6 问定义与收敛器（laya_gate.py）、
      ManualGate 快照（manual_order_gate.py:669）、engine 快照与 build_market_summary（laya_engine_observation.py）、
      regime.py 多周期能力、ml/features.py 40+ 特征、engine 观察点上下文（_check_trade_permission）。
- [x] 只读代码核实（QuantDinger /private/tmp/QuantDinger）：ai_decision_context.py（Decision Context V2）、
      ai_decision_filter.py（JEV_QUESTIONS）、README_CN.md（JEV 流程）。
- [x] 关键发现：prose 渲染键与 ManualGate 快照不匹配 → 默认 prose 模式下 ManualGate 路径静默丢
      点差/买卖价/情绪/净值/日盈亏/SL/TP/lot 证据（findings §2.3）。
- [x] 复用 2026-09-22 回放证据回答"能否判断出结果"：不能（100% ESCALATE、P3 错误放行、命中≈基率）。
- [x] 写出 findings.md（逐接口参数审计 + JEV 对照 + 缺失清单）与 task_plan.md（Phase 0-4，批准后执行）。
- [x] **用户批准计划**（追加强调 512 token 上下文上限）。
- [x] Phase 0：`render_laya_state_prose` 兼容 ManualGate 快照键（order lot/sl/tp/type、
      account equity/floating_profit/realized_daily_pnl、market bid/ask/spread/avg_spread/sentiment、
      dict 形态 rule_flags、positions/trades 的 lot 与 entry/current 兼容）；
      新增单测 6 个（ManualGate 渲染 5 + 预算护栏 1）；laya 相关回归 194 passed / 1 skipped。
- [x] Phase 1：`build_laya_engine_snapshot` 增加 `context_version`、`recent_profits`
      → `recent_exit_pnl[:10]` + `consecutive_losses`（复用 engine 已预取的平仓盈亏，零额外 I/O）；
      `build_market_summary(df, timeframe)` 扩展：ma5/10/20、rsi14、atr14+atr_pct、macd_state+histogram、
      support/resistance、volume_ratio_20、latest_bar_time_utc/data_age_seconds/is_stale；
      `_compact_position` 归一化 entry_price/current_price（兼容 MT5 price_open/price_current）。
- [x] Phase 1 预算护栏：prose 渲染 900 字符硬上限（先丢 flags→trades→positions 再硬截断），
      移除冗余 ISO 时间戳；**真实 laya tokenizer 验证**（build_sequence 512/192 逐问）：
      engine-max state=343 token（full=441）、manual-max state=364 token（full=462），
      最坏 head room=413，全部不截断、选项完整（opts_ok=True）。
- [x] Phase 2：eval 脚本新增 c3/c4 变体；n=240 全量完成（seed 42，与 C1 同 240 个 idx 配对）。
      结论：证据面增强无方向技能（全部 p≥0.10），逐问判定被大幅打乱（regime 225/240 翻转、
      data_quality sufficient 147→0）；C4(+H1) 仅更保守（entry reject 219）。详见 findings §6.3。
- [ ] **Phase 3（模型面）待用户决策**：A 领域微调 / B 换托管 JEV / C 放弃 6 问交易判定。
- [x] Phase 3 决策（用户）：**A 本地 laya 微调**。确认模型结构（ModernBERT-large + 2 层头，
      collate_items/label 为官方训练路径；本机 CPU-only 6 线程，MPS 不可用）→ 采用 head-only/
      浅层解冻策略。写子计划 Step 3A.1-3A.3（task_plan.md）。
- [x] Step 3A.1 完成：`laya_finetune_data.py` 生成 train 2505 / val 495（合成确定性标签，
      时序留出最后 20% 月份；修复 fresh/stale 语义——回放 index 归一化到决策时刻）。
      标签分布：dq sufficient 79%/partial 21%；sa aligned 68%/conflict 24%；entry pass 47%/reject 53%。
- [x] Step 3A.2 训练脚本：`laya_finetune.py`（head-only/解冻 N 层、CE over 候选 logits、
      collate_items 官方 label 通道、产出 laya.load 兼容 checkpoint）。步时校准：16.3s/step（bs=8，48 items/step）。
- [x] Step 3A.2 训练完成：600 行 × 3 epochs，head-only（26.5M 可训练参数），~19s/step，
      总时长 ~98 min；val acc 0.3375→0.6617（per-q：dq 0.845 / sa 0.64 / regime 0.62 /
      risk 0.47 / exec 0.96 / entry 0.435——entry 标签=未来 6h 方向，不可约，训到 0.435 即上限）。
      产出 `backend/models/laya_ft/`（laya.load 兼容，已实测加载成功）。
      修复：`laya_finetune.py` 保存段 `cache = snapshot_download(...)` 返回 str 导致 `str/str`
      崩溃，改为 `Path(...)`（model.safetensors 在崩溃前已写出，手动补齐 tokenizer/encoder/config）。
- [x] Step 3A.3 FT vs base 复测完成（n=240，seed 42，c5 变体，同 240 idx 配对）：
      **验收未达标**。entry_pass 2h 47.6% vs 基率 47.5% (p=0.51)、6h 52.8% vs 53.3% (p=0.59)、
      1d 50.0% vs 51.2% (p=0.67)；三周期命中集合与 base 逐根一致（McNemar p=1.0）；
      判定在运维阈值 0.6 仍 100% ESCALATE（仅 0.3 阈值出现 REJECTED 189，=封锁而非可用判定）。
      ECE(entry,2h) 0.1615→0.0676（校准变好，但那是学会合成规则后的过度自信，非方向技能）。
- [x] 结论：合成标签微调学到的是**规则本身**（dq sufficient 3→239、sa aligned 45→228、
      risk block 11→232 等全部翻转到合成规则），但规则无超额收益 → 标签上限即能力上限。
      按计划回 Phase 3 决策：**建议 B（托管 JEV，QuantDinger 同源已校准）或 C（降级为情绪/策略通道）**；
      真实成交盈亏标签 + 大量数据的 FT 超出本机算力/当前范围。

- [ ] **Phase 3 决策（B/C）待用户拍板**：B/C 评估报告已产出（`phase3-bc-report.md`），
      含 QuantDinger 同源 JEV 调用机制、本项目接入工作量/风险/验收口径、C 的零行为变化落地范围。

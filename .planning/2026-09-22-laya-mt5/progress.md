# Progress — Laya 输入数据量与真实 MT5 成功率调研

## 2026-09-22
- [x] 恢复规划状态：确认新任务独立于既有 `2026-09-22-laya-llm`（其观测期仍在跑），
      init-session 建新计划目录 `.planning/2026-09-22-laya-mt5/` 并 pin `.active_plan`。
- [x] 只读代码核实：laya 输入数据面（情绪通道 state、6 问闸门 snapshot）、
      成功率测试现状、真实数据源（ohlcv_data / Bridge）→ 已写入 findings.md。
- [x] 编写 task_plan.md（Phases 1-4：盘点 → 回放 → 充分性对照 → 报告）。
- [x] **用户批准计划**（2026-09-22；追加要求：测试用真实数据，不用 mock）。
- [x] Phase 1：真实数据盘点 —— GOLD M15 34,552 根（2025-04→2026-09）；Bridge 冒烟通过；
      trades 表仅 17 笔（多为 BTCUSD），账户/持仓历史不可得 → 回放用中性账户 + 空 recent_trades（如实）。
- [x] Phase 2：编写 `backend/scripts/laya_real_data_eval.py`，360 样本 × 变体 A/B 真实 laya 推理
      （隔离 venv、权重缓存、只读 DB）。30 样本冒烟 → 全量完成。
- [x] Phase 2 诊断：100% ESCALATE 根因 = 模型 6 问 max-class 概率 0.28-0.58（近均匀）低于 0.6 阈值，
      insufficient 标签次之；模型自评 data_quality=partial 344/360。
- [x] Phase 3：变体 B（+H1/ATR/RSI/原始收盘价）判定分布零变化（全 ESCALATE），逐问被推向
      signal_alignment 100% conflict、entry_decision 100% reject → 加数据在未微调模型上无效。
- [x] Phase 4：统计检验（entry_pass vs 基率：2h p=0.91 / 6h p=0.69 / 1d p=0.19，无显著技能）；
      报告 `deliverables/laya-data-audit.md` 完成。
- [x] 追加审计（用户追问“格式/加证据”）：源码级确认渲染模板与 512 token 截断；
      实测 A=191 token 完整可见、B=398 token 被截成残缺 JSON。
- [x] 格式对照实验：C1（同证据 prose 文本）data_quality sufficient 0→61%、ECE 0.131→0.068；
      C2（+20 根真实 K 线）更保守；C1 240 样本各 horizon 方向命中仍不显著（p=0.81/0.25/0.33）。
- [x] 能力探针：完美对齐文本仍判 conflict、强空头+SELL 判 adverse（long-only 偏差）、
      强多头+SELL 错误 pass → 未微调模型无交易判定能力。
- [x] 报告与 findings 已更新（§6 格式审计 + 行动建议）。
- [x] 生产落地（用户批准）：`render_laya_state_prose()` + `laya_state_prose=True` 默认开启；
      单测 8 个新增（渲染纯函数 5 + review 走 prose/json 各 1 + 长度预算 1）；laya 相关回归
      157 passed/1 skipped；真实 tokenizer 验证最大快照 268 token（完整序列 347<512 无截断）。
- [x] eval 脚本兼容：变体 A/B 显式 `settings.laya_state_prose=False` 保持旧 JSON 审计口径。
- [x] 阈值扫描（C1 prose 240 样本离线）：REJECTED 无技能（2h 0.457≈基率 0.475）、APPROVED 任何阈值不可达。
- [x] 问句重写实验（80 真实样本+3 探针）：signal_alignment aligned 7/80（微弱）、
      market_regime 全 adverse、entry_decision BUY 全 pass/SELL 全 reject → 重写救不了能力缺陷。
- [x] 复现脚本 `backend/scripts/laya_question_rewrite_experiment.py`；报告 §7 证据链闭环。

# Findings: 审查最近 2 次提交（9515039 + e672e27）

## 审查元信息
- **范围:** `adaf850..9515039`（2 提交，22 文件，+818 / -268）
- **分支:** feat/laya-research-and-integration
- **日期:** 2026-09-22
- **方法:** OCR 委派模式 → 逐文件 diff 审查 → 交叉验证（读源文件）→ 测试运行
- **对照基准:** `.planning/2026-09-21-code-review-laya-branch-feat-laya-resear/`（上一轮审查，已 complete）

## 背景
这两个提交是上一轮审查 Phase 4 修复的**落地提交**：
- `e672e27` = 上一轮审查的头提交（第三轮开发 + 死代码清理）
- `9515039` = 上一轮审查后新增，包含全部 Phase 4 修复代码 + 审查文档 + pkl 入库 + .gitignore 调整

因此本轮审查的核心价值 = **验证修复落地质量**（而非重复发现上一轮已修的问题）。

## 验证结论（Phase 2 实测）

| 交互点 | 结论 | 证据 |
|--------|------|------|
| C1 schema 对齐 | ✅ 正确 | `build_features` 只读 `tick_volume`（features.py:88），缺列填 0；`TradeGate.predict` 对旧模型注入 `volume=tick_volume` 别名列（trade_gate.py）|
| C2 弃权第三态 | ✅ 正确 | `predict` 返回 `(None, 0.0)` 弃权，engine.py shadow 记录不阻断；`joblib.load` 失败 fail-open |
| bar N-1 语义 | ✅ 正确 | signal 用 `df.iloc[-2]`（engine.py:760），gate_df = `ohlcv.iloc[:-1]` 使 gate 也看 iloc[-2] |
| df 复用 | ✅ 正确 | `_check_trade_permission(df=df)` 复用 `_generate_signal` 的 df，避免二次 OHLCV 往返 |
| C4 AI 来源排除 | ✅ 正确 | `engine` 字段携带（llm\|laya），risk/manager 对 laya 行跳过 AI 过滤，ConfirmationGate 排除 laya 投票 |
| 旧缓存兼容 | ✅ 正确 | `SentimentResult(**data)` 旧缓存无 engine 字段 → 默认 "llm" |
| 残留引用 | ✅ 干净 | `trade_gate_enabled` / `complete_async` 全仓零命中 |
| 测试 | ✅ 通过 | **57 passed, 1 skipped**（4 个核心测试文件）|

## 审查发现

### High
无。

### Medium

#### M1. trade_gate.py 缺文件尾换行
- **位置:** `backend/app/ml/trade_gate.py` 末尾（`\ No newline at end of file`）
- **问题:** 违反 POSIX 文本文件惯例；ruff/格式化工具可能告警。
- **修复:** 补末尾换行符。

#### M2. laya_strategy_choice confidence 与 label 错位风险（理论）
- **位置:** `backend/app/ai/laya_runtime.py:115`
- **问题:** `max_prob = float(max(probabilities.values()))` 假设 laya 返回的 `choice` = probabilities 的 argmax。若 laya 输出 choice ≠ argmax（异常情况），confidence 会与 label 错位（阈值判断失真）。
- **现状:** 实测 laya 0.3.4 返回 choice = argmax，测试 pin 住该假设。低风险。
- **建议:** 可加防御：校验 `probabilities[label]` 是否 ≈ max_prob，不匹配则视为低置信降级。

#### M3. test_config_default_is_false 测试隔离性
- **位置:** `backend/tests/unit/test_laya_runtime.py:62-65`
- **问题:** 断言 `settings.laya_enabled is False` 依赖环境无 `LAYA_ENABLED=true`。开发机 `.env` 若配置了该变量会误失败。
- **现状:** CI 无 `.env` 不受影响；上一轮修复意图（防 C1 再翻 True）有效。
- **建议:** 可改为 patch `settings` 或断言默认构造值，提升隔离性。低优先级。

### Low（静默丢弃，仅记录）
- `.gitignore` 双重豁免 `!backend/models/trade_gate.pkl`（`backend/models/*` 后 + `*.pkl` 后）—— 后者冗余但无害。
- `_load` joblib.load 失败用 `logger.warning`（fail-open 设计，可接受）。
- progress.md 中 `LAY A_MODEL_CACHE_DIR` 笔误（实际代码正确）。

## 评估
**可以提交/合入。** 上一轮 Critical 修复全部落地正确，测试完备（57 passed），无新回归。本轮发现仅 3 个 Medium（1 个纯格式、2 个理论性/隔离性），均为可选修复，不阻塞合入。

## 修复结果（2026-09-22 用户批准后执行）
| 项 | 状态 | 验证 |
|----|------|------|
| M1 文件尾换行 | ✅ 已修 | `printf '\n'` 追加；tail -c 确认 `0.0\n` |
| M2 choice≠argmax 防御 | ✅ 已修 | `predict_choice` 校验 probabilities[label]；2 个新测试 |
| M3 测试环境隔离 | ✅ 已修 | 断言 `Settings.model_fields["laya_enabled"].default is False`，不实例化 |
- 回归：**59 passed, 1 skipped**（4 核心文件），py_compile 语法全过

## 残留风险（已知，非本次引入）
- `trend_following` 不在 STRATEGIES 实现注册表（仅展示用，strategy_switch 接通前无害）—— 上一轮已记录。
- `test_config_default_is_false` 依赖环境（M3）。
- baseline AUC 结构性泄漏（TimeSeriesSplit 标签重叠）—— 上一轮 C4 已记录，config 注释标注「待 purge-gap」。
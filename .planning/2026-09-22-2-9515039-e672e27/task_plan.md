# Task Plan: 审查最近 2 次提交（9515039 + e672e27）

## Goal
对最近 2 次提交（`9515039`「更新.gitignore并添加代码审查文档」+ `e672e27`「第三轮开发并清理死代码」）做严格代码审查，产出分级问题清单与修复计划，**经用户批准后**才执行修复。

## Next Step
无——全部修复完成并通过回归测试。可向用户交付。

## Current Phase
Phase 4（修复，完成）

## 审查范围

### 基线/头
- **Base:** `adaf850`（分支第 8 提交，trade_gate 接入引擎）
- **Head:** `9515039`（HEAD）
- 共 2 个提交，22 个变更文件（+818 / -268）
- **重要背景**：这两个提交是上一轮审查（`.planning/2026-09-21-code-review-laya-branch-feat-laya-resear/`，已 complete）Phase 4 修复的**落地提交**。其中 `e672e27` 是上一轮审查头提交；`9515039` 在上一轮审查后新增，包含全部 Phase 4 修复代码 + 审查文档 + pkl 入库。

### 可审查文件（OCR 识别 14 个）
| 文件 | 变更 |
|------|------|
| `backend/app/ml/trade_gate.py` | +103/-28（C1/C2/I1/I4 修复落地）|
| `backend/app/bot/engine.py` | +69/-23（TradeGate shadow/enforce + C4 AI 来源）|
| `backend/app/ai/laya_runtime.py` | +25/-7（I3 置信阈值 + I4 momentum_rank）|
| `backend/app/ai/client.py` | -21（死代码清理 complete_async）|
| `backend/app/config.py` | +12/-5（C1 laya_enabled revert False + I1 双开关）|
| `backend/app/risk/manager.py` | +8（C4 laya 行跳过 AI 过滤）|
| `backend/mcp_server/agent_config.py` | +5/-4（I4 momentum_rank 对齐）|
| `backend/scripts/laya_synth_baseline.py` | +9/-1（C1 排除元组加 volume）|
| `backend/tests/unit/test_trade_gate.py` | +137/-15（legacy volume schema 测试）|
| `backend/tests/unit/test_laya_runtime.py` | +48/-1（I3 阈值测试 + C1 默认断言）|
| `backend/tests/unit/test_agent_config_strategy_extract.py` | +4/-3 |
| `backend/tests/unit/test_llm_lang.py` | +4/-1 |
| `backend/Dockerfile` | +5/-2（C5 路径对齐 + snapshot_download）|
| `.gitignore` | +6/-1（pkl 入库豁免）|

## Phases

### Phase 0: 范围确认
- [x] 确认最近 2 次提交 = `9515039` + `e672e27`（`adaf850..HEAD`）
- [x] `ocr delegate preview` 确认 14 个可审查文件
- [x] 读取上一轮审查计划（`.planning/2026-09-21-code-review-laya-branch-feat-laya-resear/`）作为对照基准
- **Status:** complete

### Phase 1: 静态审查（逐文件读 diff）
- [x] trade_gate.py —— C1 legacy volume 别名注入 + C2 弃权第三态 + I4 可信度校验
- [x] engine.py —— shadow/enforce 双开关 + df 复用 + bar N-1 语义对齐 + C4 AI 来源排除
- [x] laya_runtime.py —— I3 置信阈值 + I4 momentum_rank 白名单一致
- [x] config.py / risk/manager.py / agent_config.py / baseline 脚本 / Dockerfile / .gitignore
- [x] 测试文件（trade_gate / laya_runtime / agent_config / llm_lang）
- **Status:** complete

### Phase 2: 交叉验证关键交互
- [x] `build_features` 只读 `tick_volume`，缺失填 0 —— 验证 C1 契约对齐正确
- [x] `strategy.calculate(df)` 保留原始 OHLCV 列（`df.copy()` 只增列）—— gate_df 含所需列
- [x] `_get_ai_sentiment` 携带 `engine`；risk/manager laya 行 `return True` 提前返回—— 逻辑等价安全
- [x] `SentimentResult(**data)` 旧缓存（无 engine 字段）→ 默认 "llm" 兼容
- [x] `signal_label` 是 `_check_trade_permission` 参数，作用域正确
- [x] 残留引用检查：`trade_gate_enabled` / `complete_async` 全仓零命中
- [x] 核心测试运行：**57 passed, 1 skipped**（trade_gate + laya_runtime + agent_config + llm_lang）
- **Status:** complete

### Phase 3: 结论汇总与分级
- [x] 汇总审查发现，按 High/Medium/Low 分级
- [x] 输出 findings.md
- **Status:** complete

### Phase 4: 修复（等待用户批准）
- [x] 按用户批准清单执行修复（M1/M2/M3）
- [x] 回归测试（59 passed, 1 skipped + py_compile 语法全过）
- **Status:** complete

## 审查发现摘要（详见 findings.md）

### High
无。

### Medium
1. **trade_gate.py 缺文件尾换行**（`\ No newline at end of file`）—— 违反 POSIX 惯例，ruff 可能告警。
2. **`laya_strategy_choice` 的 confidence 与 label 错位风险（理论）**—— `max(probabilities.values())` 假设 choice = argmax；若 laya 返回的 choice 不是最大概率类，confidence 会与 label 错位。实测 laya 0.3.4 choice=argmax，测试 pin 住该假设。低风险，可加防御性校验（choice 的 probability 应≥max）。
3. **`test_config_default_is_false` 断言 settings 实际值** —— 若开发机 `.env` 设了 `LAYA_ENABLED=true` 会误失败；测试隔离性可改进（patch 真实 settings）。

### Low（静默丢弃）
- `.gitignore` 双重 `!backend/models/trade_gate.pkl`（`backend/models/*` 后 + `*.pkl` 后）—— 后者冗余但无害。
- `_load` 中 `joblib.load` 失败日志用 `logger.warning` 而非 error（fail-open 是设计，可接受）。
- Dockerfile `LAY A_MODEL_CACHE_DIR` 在 progress.md 中笔误（`LAY A`）—— 实际代码无此问题。

## 审查重点（Checklist）
- **正确性**：C1 schema 对齐、C2 弃权语义、I3 阈值、I4 白名单一致
- **回归风险**：上一轮修复是否引入新问题（bar 语义、df 复用、shadow 记录）
- **降级路径**：gate 故障 fail-open、laya 不可用回退
- **测试质量**：测试覆盖真实路径还是 mock、边界覆盖
- **部署一致性**：Dockerfile 路径对齐、pkl 入库豁免、env.example

## Decisions Made
| Decision | Rationale |
|----------|------------|
| 审查范围 = `adaf850..9515039`（最近 2 次提交） | 用户明确要求「最近2次提交」 |
| 复用上一轮审查基准，不重复已修复项 | 避免重复劳动，聚焦修复落地质量 |
| 新开独立计划目录而非复用旧计划 | 独立任务，planning-with-files 要求 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| 无 | - | - |
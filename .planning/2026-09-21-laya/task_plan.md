# Task Plan: Laya 集成代码审查（PR #1，分支 feat/laya-research-and-integration）

## Goal
对提交 `a517dc0`（Laya 决策引擎集成，默认关闭）做一次**严格、成体系的代码审查**，按 `superpowers:requesting-code-review` + `planning-with-files` 规范，产出分级（Critical/Important/Minor）问题清单与结论，经用户批准后修复。

## 审查范围
| 文件 | 变更类型 |
|---|---|
| `backend/app/ai/laya_runtime.py` | 新增（144 行，推理运行时单例） |
| `backend/app/ai/news_sentiment.py` | 修改（+59 行，情绪预筛接线） |
| `backend/app/config.py` | 修改（+12 行，laya_* 配置） |
| `backend/scripts/laya_synth_baseline.py` | 新增（159 行，LightGBM 基线脚本） |
| `backend/tests/unit/test_laya_runtime.py` | 新增（113 行，4 个用例） |
| `.planning/2026-09-21-laya-research/` | 调研证据（不入库，仅上下文） |

审查重点：**正确性、并发安全、降级路径、安全性（RSS 注入）、配置一致性、测试质量、死代码**。

## Next Step
Phase 4b 完成：真实 laya API 测试通过，发现并修复 confidence 语义 bug。待用户决定提交/推送。

## Current Phase
Phase 4b（完成）→ 待用户决定提交

## Phases

### Phase 1: 逐文件静态审查（发现问题）
- [x] `laya_runtime.py`：懒加载/线程安全/`predict` vs `_predict_sync`/异常吞没/单例
- [x] `news_sentiment.py`：预筛分支逻辑、score 近似映射、Redis 缓存、DB 审计一致性、`settings` 导入
- [x] `config.py`：`laya_*` 字段类型/默认值/与既有配置风格一致性
- [x] `laya_synth_baseline.py`：SQL 注入、label 泄漏（symmetric barrier 已注释）、`.env` 解析、read_only 保证
- [x] `test_laya_runtime.py`：patch 目标是否正确、测试隔离、`get_laya_runtime` 全局单例污染
- **Status:** complete

### Phase 2: 交叉验证（关键交互）
- [x] 验证 `laya_runtime.laya_sentiment_choice` 是否真异步（当前同步、未走 `to_thread`——可能阻塞事件循环）
- [x] 验证 `build_features`/`build_labels` 索引对齐、`tick_volume` 缺失列处理
- [x] 验证 `sentiment_features.py` 是否消费 `NewsSentiment` DB 表（预筛不写 DB 的后果）
- [x] 验证 `config.settings` 在测试中如何被 mock（全局 patch 隔离性）
- **Status:** complete

### Phase 3: 结论汇总与分级
- [x] 按 Critical / Important / Minor 分级输出
- [x] 每条附 `file:line`、失败场景、建议修复
- **Status:** complete

### Phase 4: 修复（仅 Critical/Important，经用户批准）
- [x] 修复 laya_runtime 同步阻塞问题（C1）：`laya_sentiment_choice` 改 async + `predict_choice` 走 to_thread
- [x] 修复预筛 label/confidence 校验（I2）+ 补写 DB 审计行（I1）
- [x] 修复 baseline 脚本列名/timeframe/limit clamp（I3）+ 单类别折崩溃
- [x] 测试：9 laya（+5 真实路径）+ 67 全量回归通过；baseline --csv 全链路跑通
- **Status:** complete

### Phase 5: 交付
- [x] 汇总审查结论到 progress.md
- [ ] 报告用户 + 提交决定
- **Status:** in_progress

## Decisions Made
| Decision | Rationale |
|----------|------------|
| 审查提交 a517dc0（而非整个分支） | PR #1 范围即该提交，.planning 其他改动不入库 |
| 独立计划目录 `2026-09-21-laya` | 与 `2026-09-21-laya-research`（调研）分离，审查可回滚 |
| 修复前必须用户批准 | 用户明确要求「计划需要我同意才能执行」 |

## Errors Encountered
| Error | Resolution |
|-------|------------|

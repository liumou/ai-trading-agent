# Task Plan: 审查 feat/laya-research-and-integration 分支全部提交

## Goal
对分支 `feat/laya-research-and-integration`（`33b1d13..e672e27`，10 个提交）做一次严格、成体系的代码审查，产出分级（Critical/Important/Minor）问题清单与合并结论，**经用户批准后**才执行修复。

## Next Step
等待用户批准本审查计划。批准后启动 Phase 1（并行派发代码审查子代理）。

## Current Phase
Phase 4（修复，进行中）

## 审查范围

### 基线/头
- **Base:** `33b1d13`（main）
- **Head:** `e672e27`（HEAD，分支顶端）
- 共 10 个提交，31 个变更文件（+2634 / -209）

### 提交清单
| # | SHA | 主题 |
|---|-----|------|
| 1 | a517dc0 | feat(laya): laya 决策引擎集成（默认关闭）+ 调研文档 |
| 2 | 45d8094 | feat(ai): 集成 laya 情绪预筛引擎并修复关键阻塞问题 |
| 3 | 8006e0a | feat(sentiment): 暴露引擎来源(laya/llm)到 API 和 dashboard |
| 4 | 57d6d70 | feat(deploy): 生产容器启用 laya + 可配置 HF 端点 |
| 5 | 3efa23c | feat(ml): 添加交易门控系统和实测结果 |
| 6 | 0b6063d | fix(trade_gate): 鲁棒 NaN 处理 + 真实数据测试覆盖 |
| 7 | 52424f3 | feat(trade_gate): 将 TradeGate 接入引擎交易权限检查（默认关闭） |
| 8 | adaf850 | feat(strategy-extract): laya choice 用于策略名抽取（3.3） |
| 9 | e672e27 | feat(ai): 实施第三轮开发并清理死代码 |
| 10 | ce92f05 | docs(.planning): 400/401 错误和 MT5 连接问题诊断报告 |

### 按主题分组的变更文件（排除 .planning 文档）
| 主题 | 文件 |
|------|------|
| **Laya 运行时** | `backend/app/ai/laya_runtime.py`（新增 223 行）、`backend/tests/unit/test_laya_runtime.py`（新增 326 行） |
| **情绪预筛** | `backend/app/ai/news_sentiment.py`（+97）、`backend/app/config.py`（+59）、`frontend/components/ai/SentimentBadge.tsx`、`frontend/app/dashboard/page.tsx`、`frontend/store/botStore.ts` |
| **策略名抽取** | `backend/mcp_server/agent_config.py`（±53）、`backend/tests/unit/test_agent_config_strategy_extract.py`（新增） |
| **TradeGate** | `backend/app/ml/trade_gate.py`（新增 74 行）、`backend/app/bot/engine.py`（+29）、`backend/tests/unit/test_trade_gate.py`（新增 116 行） |
| **部署** | `backend/Dockerfile`（+18）、`backend/requirements.txt`（+7）、`backend/.env.example`（±2） |
| **死代码清理** | `backend/app/ai/quant_analyzer.py`（删除 155 行）、`backend/app/ai/client.py`（-21） |
| **脚本** | `backend/scripts/laya_synth_baseline.py`（新增 225 行）、`backend/scripts/_laya_api_verify.py`（新增 61 行） |
| **测试调整** | `backend/tests/unit/test_llm_lang.py`（±5）、`backend/tests/unit/test_config_cors_origins.py`（新增 61 行） |

### 既有对照基准
- `.planning/2026-09-21-laya-research/task_plan.md` —— 调研结论（三轮），实施轨道 3.0-3.9
- `.planning/2026-09-21-laya/task_plan.md` —— 针对 a517dc0 单提交的早期审查计划（已完成）

## Phases

### Phase 0: 计划制定
- [x] 收集分支范围、提交清单、变更文件统计
- [x] 读取 laya-research 调研计划与既有 laya 审查计划作为对照基准
- [x] 用户批准本计划 → 进入 Phase 1
- **Status:** complete

### Phase 1: 静态审查（并行派发子代理）
- [x] 派发 Laya 运行时审查代理（laya_runtime.py、news_sentiment.py、config.py、agent_config.py 策略抽取、相关测试）
- [x] 派发 TradeGate + 引擎集成审查代理（trade_gate.py、engine.py、相关测试）
- [x] 派发 部署 + 死代码清理 + 前端 审查代理（Dockerfile、requirements、client.py 删除、quant_analyzer.py 删除、SentimentBadge、botStore、dashboard、scripts）
- [x] 每个代理按 code-reviewer 模板输出 Strengths / Issues(Critical/Important/Minor) / Assessment
- **Status:** complete

### Phase 2: 交叉验证关键交互
- [x] 验证 laya 推理是否异步（to_thread 语义）—— `laya_runtime.py:87-91` to_thread + `:93-126` predict_choice 确认
- [x] 验证情绪预筛 Redis 缓存 + DB 审计一致性 + 降级路径—— `news_sentiment.py:109-134` 写 DB + `:135-143` 写 Redis，双路径确认
- [x] 验证 TradeGate NaN 处理、feature flag 默认 False、引擎接线正确性—— `config.py:322` False 确认；`engine.py:804` 接线确认
- [x] 验证生产启用 laya 依赖安装、HF 镜像、内存预算—— C1/C2/C3 铁证确认
- [x] 验证 quant_analyzer/client 死代码删除零调用方—— grep 全仓零命中确认
- **Status:** complete

### Phase 2: 交叉验证关键交互
- [ ] 验证 laya 推理是否阻塞事件循环（to_thread 语义）
- [ ] 验证情绪预筛的 Redis 缓存 + DB 审计一致性 + 降级路径
- [ ] 验证 TradeGate 的 NaN 处理、默认关闭（feature flag）、引擎接线正确性
- [ ] 验证生产启用 laya 的依赖安装、HF 镜像、内存预算（Railway 512MB-1GB）
- [ ] 验证 quant_analyzer/client 死代码删除是否真的零调用方
- **Status:** pending

### Phase 3: 结论汇总与分级
- [x] 汇总各代理发现，去重、按 Critical/Important/Minor 分级
- [x] 交叉验证代理结论（不盲信子代理）—— 6 项 Critical 全部实读/实测确认
- [x] 输出审查报告到 findings.md
- **Status:** complete

### Phase 4: 修复（仅 Critical/Important，经用户批准）
- [x] 用户批准修复清单（修全部 Critical + 落地 shadow 模式）
- [x] 修复 Critical 1（laya_enabled revert False + 注释 + 断言测试）
- [x] 修复 Critical 2（TradeGate 列名契约 tick_volume + 弃权第三态）
- [x] 修复 Critical 3（TradeGate fail-open 语义对齐）
- [x] 修复 Critical 4（laya confidence 不再削弱风险闸）
- [x] 修复 Critical 5（Dockerfile 权重路径对齐）
- [x] 修复 Critical 6（huggingface-cli → snapshot_download + pin 版本）
- [x] 落地 shadow 模式（trade_gate_shadow/enforce + gate_prob 落审计）
- [x] 回归测试（相关 36+63+14 全过；全量 1005 passed，8 failed 全部判定与本次改动无关：6×multi_agent 本机 .env provider 覆盖 + 2×预存 flaky）
- [x] **第二轮（验证代理对抗结论驱动）**：C1 真根因修复（裸 volume schema 失配 → 别名注入 + 训练侧排除元组）、pkl 入库、trend_following 白名单一致、NaN 检查前置、engine.py 注释矛盾、测试补盲区
- **Status:** complete

### Phase 5: 交付
- [x] 汇总审查结论 + 修复结果到 progress.md
- [x] 向用户报告
- **Status:** complete

## 审查重点（Checklist）
- **正确性**：Laya 推理是否正确接线、score/confidence 语义、策略抽取是否破坏原有行为
- **并发安全**：to_thread、Semaphore、单例、线程安全
- **降级路径**：laya 不可用/低置信时是否安全回退 Claude
- **安全性**：RSS 注入面、无白名单校验问题、TradeGate 是否可绕过硬闸门
- **配置一致性**：laya_* 配置字段、默认值、feature flag
- **测试质量**：测试验证真实行为还是 mock、覆盖边界、测试隔离
- **死代码**：quant_analyzer.py、client.py 删除是否影响其他调用
- **部署**：Docker 构建、依赖、HF 镜像、内存预算

## Decisions Made
| Decision | Rationale |
|----------|------------|
| 审查整个分支（33b1d13..e672e27）而非单提交 | 用户要求审查「当前分支提交的代码」，跨全部 10 个提交 |
| 按主题分 3 个并行审查代理 | 变更分 4 大主题，独立可并行，各代理专注减少误报 |
| 修复前必须用户批准 | 用户明确要求「计划需要我同意才能执行」 |
| 以 laya-research 调研结论为对照基准 | 判断实施是否符合调研轨道 3.0-3.9 的范围承诺 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Bash 分类器（deepseek-v4-pro）临时超时 | 1 | 等待后重试，成功初始化计划目录 |

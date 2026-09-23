# Progress Log

## Session: 2026-09-22

### Current Status
- **Phase:** 1-2 complete（可行性核实 + 最终方案定稿）；3-5 pending（待用户批准实施）
- **Started:** 2026-09-22

### Actions Taken
- 初始化规划目录 `.planning/2026-09-22-laya-llm/`
- 代码核实 LLM 调用面：通道 A（client.py）3 点 / 通道 B（run_agent_loop）8 点
- 写入 findings.md（调用点表、可行性判定、分层架构、关键证据、数据条件）
- 写入 task_plan.md（5 阶段最终改造方案）

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| LLM 调用面枚举 | 11 点（3+8） | 11 点确认 | ✅ |
| laya 可替换分级 | B=2 / D=9 | B=2 / D=9（与 09-21 盘点一致） | ✅ |

### Errors
| Error | Resolution |
|-------|------------|
| 无 | — |

### 2026-09-22（第二轮：QuantDinger 复核）
- clone https://github.com/OpenByteInc/QuantDinger.git → /private/tmp/QuantDinger
- 代码核实：JEV_QUESTIONS 6 问、evaluate() 仅开仓动作、EXCLUDED 策略跳过、平仓/止损绕过
- Decision Context V2（ai_decision_context.py）确认：确定性预计算 state → JEV 只做 choice
- 校验链确认：白名单 + probabilities 和为 1 + choice==argmax + confidence≥0.55
- 兜底链确认：JEV → LLM → fail-open（记录不阻断）
- findings.md / task_plan.md 更新为 v2 结论

### 2026-09-22（第三轮：四路评审 + 计划迭代）
- 四路评审并行完成：架构 / 安全风控 / 评估验收 / 外部证据
- 评审汇总写入 findings.md §7；task_plan.md 迭代为 v3
- 关键迭代：laya「主判」→「veto-only 收紧层」；补收敛器/阈值/验收门槛设计冻结；影子隔离与审计字段方案
- 交付用户审批（未执行任何业务代码）

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| 相关测试文件 | — | 43 passed, 1 skipped | ✅（评审复测） |
| 全量测试 | — | 876 passed, 23 failed, 1 skipped（失败与本次无关） | ⚠️ 基线 |

### 2026-09-22（第四轮：Phase 3 实施）
- **Phase 3.0 完成**：`backend/app/ai/laya_gate.py` 收敛器纯函数（6 问 → 三值 + ESCALATE，7 级优先级）+ `design-converger.md` 设计冻结；17 单测通过
- **Phase 3.1 完成**：`laya_runtime.py` 补 JEV 级五校验（键集/sum≈1/finite/argmax/白名单）、`predict_choices` 单次前向批处理、`predict` 超时、`warmup()` 预热；config 加 laya_gate_shadow/enforce/threshold/timeout
- **Phase 3.2 完成**：`manual_order_gate._review` 集成 laya 影子（与 LLM 并行、veto-only、落 `stored["laya"]`、故障留痕 `UNAVAILABLE`）；新增 `test_laya_gate_shadow.py` 4 用例
- 测试：laya 相关 4 文件 **70 passed, 1 skipped**；manual gate 原有 24 用例无回归

### 2026-09-22（第五轮：Phase 3.3 完成）
- **影子明细表**：`ManualShadowReview` 模型 + alembic 迁移 `b0c1d2e3f4a5`（专表聚合，order_audits.review 只加摘要）
- **落库**：`_persist_shadow_review`（best-effort）——audit_id/双路 verdict/agreement/dangerous_divergence/latency/state_snapshot 全量
- **报表**：`app/ai/laya_gate_report.py` 纯函数（混淆矩阵/一致率+Wilson/Kappa/致命分歧/误杀/兜底率/延迟/逐问对齐）+ `scripts/laya_gate_report.py` CLI（含验收门槛 PASS/FAIL）
- **灰度**：`laya_gate_rollout_pct` 配置预留 + enforce=False 即 kill switch
- 测试：laya 相关 5 文件 **89 passed, 1 skipped**；manual gate 原 24 用例无回归


### 2026-09-22（第六轮：Phase 4 批准 + 启动）
- 用户批准 Phase 4（engine 开仓侧观测），要求记录该事项
- 设计冻结：`phase4-observation.md`（观测点/分歧定义/时间盒 4 周/最小侵入）
- 实施范围：`laya_engine_observation.py` 纯函数 + engine 挂钩（best-effort 零副作用）+ 新表迁移 + 配置 + 报表 CLI + 单测


### 2026-09-22（第七轮：Phase 4 实施完成）
- `app/ai/laya_engine_observation.py`：市场摘要（纯 pandas 确定性预计算）/ 快照组装 / 分歧分类（gate+final 双口径）/ 零副作用观测器（account/chain 注入、laya 与确定性检查并行、best-effort 落库、故障留痕 UNAVAILABLE）
- `app/ai/laya_engine_report.py` + `scripts/laya_engine_report.py`：分歧描述性统计 CLI（无 PASS/FAIL 门槛，Phase 4 契约）
- `LayaEngineObservation` 专表 + 迁移 `c2d3e4f5a6b7`（head 链完整：c2d3e4f5a6b7 → b0c1d2e3f4a5 → a9b8c7d6e5f4 → z0a1b2c3d4e5）
- 配置 `laya_gate_engine_shadow=False`（默认关，观测期开启）
- engine：`_check_trade_permission` 拆 wrapper（观测生命周期，finally 必 finish）+ inner（原逻辑 + `_engine_obs` 注入 account/chain）
- 测试：`test_laya_engine_observation.py` **31 passed**；laya 相关 7 文件 **133 passed, 1 skipped**；engine/scheduler **21 passed** 无回归


### 2026-09-22（第八轮：观测开关打开）
- 用户批准「打开」观测 → 代码默认 `laya_gate_engine_shadow=True`（观测-only，零行为风险；C1 的 `laya_enabled` 代码默认仍 False，由 env 覆盖）
- `.env` 新增：`LAYA_ENABLED=true`、`LAYA_GATE_SHADOW=true`（手动通道 Phase 3 影子）、`LAYA_GATE_ENGINE_SHADOW=true`、`LAYA_HF_ENDPOINT=https://hf-mirror.com`
- 验证：settings 加载全部生效；laya 相关套件 133 passed/1 skipped 无回归
- 遗留（部署侧）：`alembic upgrade head` 未执行（无 DB 连接）；本地 venv 未装 laya/torch（Docker 重建后由 requirements.txt 安装，权重首次运行从 HF 下载）


### 2026-09-22（第九轮：部署说明 + 数据库建表完成）
- 写入 `DEPLOY_NOTE.md`（已完成项/部署侧待做/观测期行为/kill switch/报表命令）
- 线上库执行 `alembic upgrade head`（100.72.200.33:15432/mt5，原版本 a9b8c7d6e5f4）：
  应用 `b0c1d2e3f4a5`（manual_shadow_reviews）+ `c2d3e4f5a6b7`（laya_engine_observations）
- 验证：alembic 版本 = c2d3e4f5a6b7（head）；两表存在；laya_engine_observations 19 列齐全
- 运行环境唯一待办：重建/重启容器让 .env 与依赖生效（本地 venv 未装 laya/torch，运行环境是 Docker）


### 2026-09-22（第十轮：第二轮多路评审）
- OCR 委托模式（open-code-review v1.12.8）选定 13 文件 + 三路并行评审（架构 Rawls / 安全 Mendel / 测试 Bernoulli）+ 主持人复核
- 结果：2 High（H1 持仓快照恒空 getattr-on-dict、H2 engine wrapper 异常泄漏 H-3 路径）、8 Medium（M1 手工单 await laya 35s / M2 超时线程堆积 / M3 事件循环 import laya / M4 空测试类 / M5 classify 矩阵不全 / M6 _persist 未实测 / M7 min_confidence 硬编码 / M8 未判 laya_enabled）、8 Low（L1-L8）
- 评审文档：`reviews/review-r2-code-review.md`；findings.md §8
- **修复计划已写入 task_plan.md（Review Fix Phase R2），待用户批准后执行**


### 2026-09-22（第十一轮：R2 修复执行完成）
- 用户批准 R2 修复计划 → 全部完成：
  - H1 持仓 dict 压缩 / H2 wrapper 异常隔离 / M1 手工单 3s 预算+后台补写 / M2 推理锁+超时 fail-stop / M3 懒加载导入+启动 warmup / M4 超时/预热用例 / M5 classify 全矩阵+未知值 / M6 _persist ORM 实测 / M7 阈值接线 / M8 laya_enabled 门控 / L1-L6 / L8 边界
- 源码：laya_runtime / laya_engine_observation / laya_engine_report / laya_gate / engine / manual_order_gate / models / config / main.py
- 一致性：迁移索引与模型对齐（signal_label/created_at index=True）
- 测试：laya 相关 8 文件 **204 passed, 1 skipped**（较修复前 150 +54），无回归
- 新增报警/降级语义：predict 超时 fail-stop（runtime 置不可用）、warmup 超时不 fail-stop、启动期线程内预热

### 2026-09-22（第十一轮收尾：最终核验）
- 重跑 laya 相关 9 文件套件（含 scheduler_risk_gate）：**208 passed, 1 skipped**，原 8 文件口径 204 passed，无回归
- 确认 `app/main.py:60` 已导入 settings；`main.py:305-310` laya_enabled 时启动期线程内 warmup（timeout 取自 `laya_gate_warmup_timeout_s`）
- R2 全部完成；未提交 git；无新迁移（模型索引与已建 PG 表一致）

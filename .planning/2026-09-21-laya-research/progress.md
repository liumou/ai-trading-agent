# Progress: Laya 调研

## Session 1 — 2026-09-21

### 已完成
- [x] 克隆 laya 仓库（shallow, 到任务 tmp）
- [x] 通读 README / pyproject / BENCHMARKS（能力与诚实限制）
- [x] 精读源码：__init__ / agent.py / common.py / router.py / presets.py / lang.py
- [x] 确认项目后端依赖（无 torch/transformers）
- [x] 确认 Railway Dockerfile（python:3.12-slim, 无 GPU）
- [x] 确认 AI 客户端异步、调度器 async
- [x] 核实情绪分类（news_sentiment.py Claude JSON）、手动风控情绪规则（_emotion_flags 纯规则）、健康监测/熔断（确定性数值）
- [x] 写 findings.md（能力边界 + 适配性初判 + 集成点评估矩阵）
- [x] 写 task_plan.md 初稿（Phase 结构，尚未含最终决策）
- [x] 启动 2 个探索 agent（laya 能力复核 / 项目集成点）→ 进行中

### 下一步
- ✅ 探索 agent 1（项目集成点）已返回，全景并入 findings.md + task_plan
- ✅ 探索 agent 2（laya 源码复核）已返回，部署/并发/校准约束并入 findings.md + task_plan（Decisions Made 扩展到 11 项）
- ✅ 调研 Phase 1 完成，第一版计划已提交用户审批
- 🔄 **【第二轮调研，2026-09-21】用户提出：所有 LLM 是否都能用 laya 决策**
- ✅ 自读证据链（交叉验证）：prompts.py schema（情绪/优化/订单审查）；MCP agents（technical 调工具+长文、orchestrator 规则融合+执行、reflector 14 工具反思循环）；strategy_optimizer（连续数值）；quant_analyzer（嵌套分数）
- ✅ 中间结论：**不能全量替换**——laya 只替换"纯文本→离散分类"层；工具调用/数值/长文/多步智能体仍须 LLM
- ✅ 穷尽盘点 agent（abd53fb1565e4af6e）已返回：13 调用点 A=0/B=2/C=0/D=11，按调用量 B≈14%/D≈86%；识别死代码（quant_analyzer、complete_async）；洞察"确定性代码换 specialist"路径
- ✅ task_plan 第二轮迭代完成：定量结论 + 混合架构 + 3.7 阶段"确定性代码换 specialist" + Decisions Made 扩到 17 项
- ✅ findings.md 并入完整盘点报告
- **第二轮调研完成，迭代后的计划待用户审批**
- 🔄 **【第三轮调研，2026-09-21】用户提出新方向：让 laya 直接做交易决策引擎**——给足够样本，laya 判断"是否可交易 + 成功概率"。用户要求**用实际操作**调研真实可行性，据此迭代计划；未经同意禁止执行。
- 🔄 实操 spike：隔离 venv 装 laya(CPU) → 构造交易状态输入 → 实测加载/延迟/内存/零样本输出分布 → 评估微调可行性（数据源/GPU）
- **第三轮调研完成后提交审批**

### 第三轮进展（实操 + 数据）
- ✅ 隔离 venv + GitHub laya 0.3.4（pypi 是错包 0.3.3）+ CPU-only torch
- ✅ 网络实测：huggingface.co 模型端点被阻断 → hf-mirror.com 镜像 + UA 可用（`HF_ENDPOINT`）
- ✅ **english spike 实测**：9 状态零样本——方向有微弱判别，can_trade/胜率无判别力（noul 恒 0.47-0.59）；内存 ~1.7GB；延迟 134-262ms
- ✅ **multilingual spike 实测**：同样无判别力（can_trade 恒 0.85-0.96）；延迟 189ms
- ✅ 数据 agent：trades 真实样本 ~10-30 行（三源交叉），pre_trade_snapshot 特征偏薄+循环论证风险，「能否交易」缺负样本；ohlcv(14万)+backtest 合成是唯一路径；Kaggle T4 可行
- ⚠️ trades 精确 count(*) 被自动分类器拦截（只读+read_only 硬保证被误杀）——交给用户决定是否授权
- **第三轮结论成形：零样本不可行，必须微调；真实数据不足，需合成。待迭代计划提交审批**

### 错误
（无）

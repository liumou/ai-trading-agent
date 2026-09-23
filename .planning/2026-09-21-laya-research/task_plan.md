# Task Plan: Laya 决策引擎应用调研

## Goal
评估 laya 非自回归决策引擎能否应用到当前 ai-trading-agent 项目，产出**可执行的实施计划**并在用户批准后实施。

## 结论（调研汇总，两轮）

### 第一轮结论：可以用，但范围要收敛
laya 是「非自回归单次前向的结构化判定引擎」（choice 分类 / score 序数打分 / noul 二值概率），**不做文本生成**。项目最有价值应用：
1. **新闻/公告情绪分类**（`backend/app/ai/news_sentiment.py`）：现有 Claude JSON 返回 `{sentiment, confidence}` 三分类——与 laya `choice` 形态一致。可做 Claude 廉价预筛分层或纯本地情绪引擎。
2. **交易行为模式辅助信号**（`manual_order_gate.py::_emotion_flags`）：laya 给 LLM 审查提供概率特征，**不得替换硬风控闸门**。
3. **（可选）AI Agent 输入护栏**：`guard_questions()` 预设。

### 第二轮结论：**不能全量替换（定量：86% 调用量不可），但"分类层"可外置（14%）**（新调研）
> 用户问：所有 LLM 是否都能用 laya 做决策？

**结论：不能，且已有定量支撑。** 穷尽盘点（13 个调用点）分级：**A 完全可替换=0；B 大部分可替换=2（Sentiment、Manual gate）；C 部分/混合=0；D 不可替换=11**。**按每日调用量 B≈14% / D≈86%**。

D 占 86% 的四类阻塞：
1. **连续数值生成**（`strategy_optimizer` 7 参数直接进实盘、risk 手数/SL/TP）——laya 只给离散选项
2. **工具调用/多步推理**（8 个 agent 需 4-14 个 MCP 工具）
3. **自由文本生成**（chat_agent、chat_workflow、reflector 记忆条目）
4. **跨长上下文综合 + 强制审计留痕**（orchestrator 综合 4 报告、`log_decision` MANDATORY）

**关键洞察**：最有价值的中间路径**不是"用 laya 换 agent"，而是"用确定性代码换 specialist"**——`technical_analyst` 的输入输出本质是 `run_full_analysis` 的指标数学（`tools/learning.detect_regime` 已是纯规则表）。把 3 个 specialist + reflector 改为结构化代码输出后，orchestrator 的"有无可交易信号"快筛**才具备 laya 快筛（C 级）条件**，从而可能砍掉决策档 LLM 大部分调用量。

**可落地（B 级 2 个）**：#1 Sentiment（sentiment→choice、confidence→noul，score 离散化、key_factors 删/保留 LLM）、#3 Manual gate（verdict→choice、confidence→noul、reasoning 模板化）。**恰好也是仅有的两处提示词注入面**（RSS 标题、order comment）——laya 非生成式可彻底消除。

**附带发现**：`quant_analyzer.py` 与 `complete_async`（纯文本入口）为**死代码**（零调用方），可先删。

**全量替换=不可行的根本原因**：项目没有"纯分类"的 LLM 调用——每个输出都掺杂连续数值/长文/工具/审计字段，全量替换会破坏输出契约与审计链。正解是**混合架构**：laya 做高频分类预筛/影子打分，LLM 保留深度决策与论证。

### 第三轮结论：**laya 直接做交易决策引擎——实操可行但必须微调，且真实数据不足，需合成样本**（新调研）
> 用户提出：给 laya 足够样本，让 laya 判断"是否可交易 + 成功概率"。要求用实际操作调研可行性。

**实操实测（spike，隔离 venv）**：
- english 421M / multilingual 322M 均从 HF 镜像下载跑通；本机 device=mps，内存 **~1.7GB / GB 级**；延迟 134-262ms / 189ms。
- **零样本判别力（9 合成状态实测）**：方向有微弱判别（strong_bull→BUY、strong_bear→SELL）；**但"能否交易"(noul) 恒 0.47-0.59（english）与恒 0.85-0.96（multilingual）——无判别力**；"成功概率"(score) 不随风险/震荡状态有效变化。→ **零样本不可用，必须微调**（印证 README "base 不是零样本决策引擎"）。
- 网络约束：huggingface.co 模型端点被阻断，需 `HF_ENDPOINT=hf-mirror.com` 镜像（生产部署需预缓存/镜像）。

**数据可行性（agent 三源交叉）**：
- `pre_trade_snapshot`→`profit` 形态**成立**（laya 合法 JSON state + 可派生标签 win/loss/exit_reason）。
- **但生产 `trades` 真实样本仅 ~10-30 行**（文档 0 行 + 日志 11 ticket + 闸门限制三源交叉），**撑不起 RLCD 微调**（需 ~30k 样本）。
- `pre_trade_snapshot` 特征偏薄（无价格/趋势/动量）+ `effective_confidence` 循环论证风险。
- **「能否交易」子问题缺负样本**（被拒单不落 trades 表）。
- **唯一可行路径**：`ohlcv_data`(GOLD M15, 140,821 行) + `ml/features.py`(40+ 特征) + `backtest/engine.py` **合成同形态样本**（数千-上万条带入场快照的成交），量级才够 RLCD。Kaggle 免费 T4 可跑 2 epoch。
- **建议先做 LightGBM 基线 AUC**（同一批合成数据）——AUC 显著高于基率才值得继续，否则微调只是"用模型记 30 个数字"。

## 当前状态
调研 Phase 1 完成（三轮：①laya 能力+集成点 ②全量替换可行性定量 ③**实操 spike 交易决策引擎**），Phase 2 决策已成形（混合架构 + 交易决策需微调且需合成数据），**待用户批准后进入 Phase 3 实施**。

## Next Step
用户审批本计划。批准后按批准的范围进入 Phase 3（3.0 spike / 3.2 情绪分类层外置 / 3.8 合成数据+LightGBM 基线 等轨道）。

## Phases

### Phase 1: 调研（Requirements & Discovery）
- [x] 精读 laya 源码（agent/router/common/presets/lang）——能力与限制
- [x] 核实项目部署与并发约束（无 torch 依赖、CPU 部署、async AI 客户端）
- [x] 核实情绪分类/手动风控/健康监测/熔断现状
- [x] 写 findings.md（能力边界 + 集成点评估矩阵）
- [x] 启动 2 个探索 agent（laya 能力复核 / 项目集成点）→ 均返回，发现已并入
- **Status:** complete

### Phase 2: 决策与计划
- [x] 明确结论：**部分采用**，范围收敛（情绪预筛 + 辅助信号）
- [x] 限定最小价值场景（不把概率模型放进硬风控闸门）
- [x] 部署方案（依赖、镜像、内存、模型缓存、CPU 推理、to_thread）
- [x] 分阶段实施计划
- **Status:** complete

### Phase 3: 实施（需用户批准）
> 以下为**候选实施步骤**，批准后细化。探索 agent 确认了额外 2 个 A 档点（agent_config 策略抽取、hallucination_check claim 识别）。**实施默认 `laya_enabled: bool = False` 开关 + try-import 降级**（不装依赖也能启动），沿用 mcp_server 的 `_AGENT_AVAILABLE` 模式。**第二轮结论：laya 做"分类层外置"，不替换 LLM 决策本体**。
- [ ] **3.0 spike 验证**（可选但推荐）：在本地/CI 装 laya 依赖（~2GB），CPU 跑通 1 个情绪 choice 判定，测延迟/内存/导入时间 → **重点验证 Railway 512MB–1GB 内存预算能否容纳常驻 421M 模型**（agent 报告指出的容量风险）
- [ ] **3.1 依赖与模型层**：新增 `laya` 依赖（默认关闭 + try-import 降级）；建 `backend/app/ai/laya_runtime.py`——懒加载单例（`Router(preload=True)` 或仅 english/322M multilingual），启动时 warm-up，本地路径加载权重，`asyncio.to_thread` + `Semaphore(1~2)` 封装，线程安全
- [ ] **3.2 情绪分类层外置**（核心价值，混合架构）：`NewsSentimentAnalyzer.analyze()`（`news_sentiment.py:47`）先跑 laya `choice`（bullish/bearish/neutral）→ 输出 sentiment 三分类 + 概率；**score/key_factors/上下文加权仍由 Claude**（只把"三分类"这一格交给 laya，保留完整输出）；低置信才让 Claude 深析；Redis 缓存与降级；**修无白名单校验问题**
- [ ] **3.3 策略名抽取修复**（A 档）：`mcp_server/agent_config.py:79-95` 用 laya `choice`（8+1 策略类）替换 `if keyword in text` 子串匹配 → 消除顺序敏感、中文/否定误判，输出置信度
- [ ] **3.4 hallucination claim 识别**（A 档，可拆）：`app/ai/hallucination_check.py:59-142` 仅 claim 识别层用 laya；`rsi_val < 65` 等数值验证保留规则
- [ ] **3.5 手动风控影子打分**（可选-B 档）：`manual_order_gate._review`（`:169`）laya 作第一遍快筛/影子打分（只产出 verdict 概率参考），仅阈值带模糊样本升级给 Claude；**不替换主判官、不替换 `:574` block 硬规则、reasoning/risk_flags 审计链保留**
- [ ] **3.6 AI Agent 输入护栏**（可选）：runner/agent 输入前置 `guard_questions()` 检测（jailbreak/injection），命中则拒绝/告警
- [ ] **3.7 【中间路径·省 token 大头】确定性代码换 specialist**（独立于 laya，可并做）：把 3 个 specialist + reflector 的**指标读取/规则判定**层改为确定性代码输出（结构化 Signal/Bias/Verdict + confidence）——`technical_analyst` 的输入输出本质已是 `run_full_analysis` 指标数学、`detect_regime` 已是纯规则表。三个 impact：①砍掉 specialist 的 agent 循环 token 成本（占比最高的 4×/cycle）；②为后续 laya 作 orchestrator "有无可交易信号"快筛铺路（C 级）；③orchestrator 综合仍保留 LLM（长上下文+审计）。**此项可能是全计划中省成本最大的，风险是改变 specialist 输出契约→影响 orchestrator 文本解析**
- [ ] **3.8 【第三轮·laya 直接做交易决策引擎】先做合成数据 + LightGBM 基线**（独立轨道，与上面集成轨道无关）：①确认生产库只读访问权限（用户授权，`read_only` 硬保证）；②导出 GOLD `ohlcv_data`(140k) 到隔离环境；③跑 `ml/features.py build_features()` + `backtest/engine.py` 合成「入场快照→outcome」样本（数千-上万条）；④同一批数据训 **LightGBM 基线** 测 AUC/基率。**决策门**：AUC 显著高于基率 → 进入 3.9；否则**终止该轨道**（微调只是记数字，不值）
- [ ] **3.9 【第三轮·条件性】Kaggle 微调 laya（仅当 3.8 基线达标）**：合成样本 JSONL 导出 → Kaggle 2×T4 跑 RLCD 微调（`proper_reward`，2 epoch 断点续训）→ 推 HF 私有仓库 → 本地/部署加载微调模型实测（能否交易/成功概率判别力对比零样本）。**强约束**：微调模型只作**辅助信号/影子决策**，绝不直接下单；真钱实盘需人工批准 + 长影子验证
- **Status:** pending

### Phase 4: 测试与验证
- [ ] 单测：laya runtime mock、情绪预筛（高/低置信分支）、降级路径
- [ ] 手动风控辅助信号（若做）行为不变断言
- [ ] `ruff check` + 全量 pytest（--no-cov 快速回归）
- [ ] 部署冒烟（Railway CPU 内存是否达标）
- **Status:** pending

### Phase 5: 交付
- [ ] 汇总变更、效果（延迟/token 成本）、开关/配置
- **Status:** pending

## Decisions Made
| 决策 | 理由 |
|------|------|
| 部分采用 laya，范围=情绪预筛+辅助信号 | 高贴合（形态一致）、低成本（本地毫秒级）；避免概率模型入硬风控 |
| 不把 laya 放进 manual_order_gate 硬闸门 | 不变量：REJECTED 不可强制、block 直接拒；概率模型不可靠作拦截依据 |
| CPU 推理 + to_thread 隔离 | Railway 无 GPU；async 事件循环不能被同步阻塞 |
| 引入前先 spike 验证 | ~2GB 依赖 + 1.5GB 权重是大变更，先验证 CPU 延迟/内存可接受 |
| 情绪预筛保留 Claude 降级 | laya 不可用/低置信时回退 Claude，不影响现有功能 |
| **并发策略：`device="cpu"` 显式预载 + lifespan startup 单例 + `asyncio.to_thread`/线程池 + `Semaphore(1~2)`** | Agent.device 是可变共享状态，OOM 回落会搬模型（数据竞争）；Router 无锁。显式 CPU 预载完全避开回落路径；startup 加载避免 7-10s 冷载入请求路径 |
| **Router 用 preload + max_loaded≥驻留数** | 避免运行期 load/evict 竞态重复构建（两份 421M 内存尖峰） |
| **环境变量：`USE_TF=0` + `TOKENIZERS_PARALLELISM=false`** | 有 TF 会死锁 model 构建（laya.load() 永久挂起）；Py3.12 tokenizer fork 死锁 |
| **生产必须用 Router 不用裸 Agent** | english checkpoint 对非英文崩溃但 confidence 不降，路由必须在推理前 |
| **校准：先在自有数据重拟合温度（按 qtype×选项数桶）** | base 出厂过度自信（ECE 0.466/0.314）；拟合可降到 0.081/0.106，性价比最高 |
| **部署：Docker 构建时预热 laya.load() 使 HF cache 落盘入镜像** | 库无离线开关、目录需可写（_fix_tokenizer_config 写盘）；运行期走本地路径分支绕开下载与写盘 |
| **moderation_questions 预设不上生产** | 实测 toxic 0.530 接近随机 |
| **【第二轮】不尝试全量替换 LLM，laya 只做"分类层外置"** | 穷尽证据：13 个调用点 A=0/B=2/C=0/D=11，86% 调用量不可替换；全量替换会破坏输出契约与审计 |
| **【第二轮】混合架构：laya 分类预筛 + LLM 深度决策** | 情绪三分类/策略名/claim 类别/correlation status 等高置信用 laya，低置信/含长文数值场景保留 Claude |
| **【第二轮】laya 不碰 MCP 多 agent 管道**（orchestrator/analysts/reflector） | 工具调用+多步智能体+状态变更，超出 laya 单次前向能力 |
| **【第二轮】laya 不碰连续数值输出**（score/params/嵌套分数） | laya 只输出离散选项概率，不能产生连续数值 |
| **【第二轮】优先"确定性代码换 specialist"，laya 快筛作后续** | specialist 输入输出本质是指标数学（detect_regime 已纯规则）；先结构化代码化砍 4×/cycle 的 agent token，再让 laya 做 orchestrator"有无信号"快筛 |
| **【第二轮】删除死代码：quant_analyzer.py 与 complete_async** | 零调用方；先删省两个调用面（独立小任务，可在 3.0 前先做） |
| **【第三轮】零样本不可用，交易决策必须微调**（spike 实测：can_trade/胜率无判别力） | english noul 恒 0.47-0.59、multilingual 恒 0.85-0.96，均不随状态区分；方向虽有微弱判别但不足为凭 |
| **【第三轮】真实 trades 样本不足（~10-30 行），用 ohlcv+backtest 合成** | 三源交叉确认；RLCD 需 ~30k 样本，真实数据差 3 个数量级 |
| **【第三轮】先做 LightGBM 基线 AUC 再谈微调** | 避免"用模型记 30 个数字"；AUC 显著高于基率才值得 RLCD 微调 |
| **【第三轮】微调模型只作辅助信号/影子决策，绝不直接下单** | 交易决策是高风险；概率模型判定须影子验证 + 人工批准，与手动风控不变量一致 |
| **【第三轮】生产部署需 HF 镜像/预缓存权重** | 实测 huggingface.co 模型端点被阻断；`HF_ENDPOINT=hf-mirror.com` + 构建时预缓存 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| GateGuard 拦截 planning 文件写入 | 2 | 按 hook 要求陈述事实后重试通过 |
| GateGuard 拦截 git clone (rm -rf) | 1 | 陈述事实后重试通过 |
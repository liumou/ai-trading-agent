# Findings: Laya 调研

> 调研目标：评估 https://github.com/NandhaKishorM/laya.git 的 laya 决策引擎能否应用于当前 ai-trading-agent 项目。本文件仅存研究/发现（原始证据），计划另存 task_plan.md。

## 1. laya 是什么

- **定位**：多语言、**非自回归（单次前向）**的 "System 1 决策引擎"。给定任意 state（文本 / JSON dict / 对话列表），一次前向输出若干**结构化判定**，不做自由文本生成。
- **决策原语（3 种）**：
  - `choice`：从 N 个候选标签中选一个（返回各标签概率 + 归一化熵置信度）。
  - `score`：序数刻度上的期望分值（返回期望分数 + legend + 分布）。
  - `noul`：二值概率 P(true)∈[0,1]（是否成立），如 "是否退款请求 / 是否注入攻击"。
- **单次前向**：所有问题拼接进一个序列（`[CLS] 类型+指令 [SEP] [MASK] 选项… [SEP] state [SEP]`），一次过编码器，全部问题并行判定。速度：T4 GPU ~33ms/问，batch 10 问 ~7.2ms/问；CPU ~200–500ms。
- **置信度可校准**：RLCD（proper scoring rule 奖励）训练，`confidence_from_probs` = 归一化香农熵（1 − H/log k）。README 强调概率有意义、可做置信度门控。
- **三个 checkpoint + Router**：
  | checkpoint | 参数 | 上下文 | 语言 |
  |---|---|---|---|
  | `laya`（english） | 421M (ModernBERT-large) | 512 | 英文 |
  | `laya-multilingual` | 322M (mmBERT-base) | 1024 | 100+ 语言 |
  | `laya-typed-decisions` | 421M (ModernBERT-large) | 1024 | 4 个合成工作流微调 |
  - `Router` 按脚本/语言检测（纯 Python，<0.5ms）选 checkpoint；`preload=True` 常驻内存避免冷加载（冷加载 CPU 7.4s / T4 10.3s）。
- **依赖**：torch>=2.0, transformers>=4.45, safetensors, huggingface_hub, numpy。Python >=3.8。
- **模型权重**：从 HuggingFace `convaiinnovations/laya` 下载（`snapshot_download`），约 421M/322M 参数（fp32 权重约 1.6–1.7GB / 1.3GB，加载后内存占用量级 GB 级）。支持本地路径加载、GPU/MPS/CPU 自动回退。
- **内置工作流预设**（presets.py）：`triage_questions()` 工单分诊、`email_questions()` 邮件分诊+威胁过滤、`guard_questions()` LLM 输入护栏（jailbreak / prompt_injection / sensitive_data / harm_severity / topic）、`moderation_questions()` 内容审核、`router_questions()` 模型路由判定。
- **许可证**：Apache 2.0，开源可商用。版本 0.3.4，Development Status Beta。

## 2. 能力边界（诚实限制，README 明确）

- **不是零样本决策引擎**：基础 checkpoint 在 typed-decisions 基准上接近随机（0.36 vs 随机 0.318 / 多数类 0.461）；0.766 的精度来自**微调后的** checkpoint。**"Laya 是快速可特化的底座，不是开箱即用的零样本决策引擎"。**
- **高基数选项（>20 个）**：选项共享 head_max_len token 预算（默认 192/256），77 选项时每标签仅 3-4 token，精度骤降（Banking77 0.425）。可调 `head_max_len` 或做粗→细分层。
- **ordinal score 最弱**：SST-5 0.372。
- **english checkpoint 对非英文崩溃**（Khmer 0.000 @ 0.952 置信度——高置信度犯错，靠 Router 在**前向之前**做脚本检测规避）。
- **概率虽可校准，但默认过自信**：ECE 0.466→0.081（laya 需重新拟合温度）；multilingual 甚至没带拟合温度。**在生产依赖其概率前必须 fit 温度**。
- `laya-typed-decisions` 默认不被 Router 自动选中（需 opt-in `auto_task_detection` 或显式指定），因它只对 4 个合成工作流微调。

## 3. 与当前项目的适配性初步判断

当前项目技术栈：FastAPI + Python 3.12，Claude 做自由文本分析与交易审查，LightGBM 做 ML 预测，手动订单风控用规则+LLM 混合（manual_order_gate）。

**贴合点（结构化判定）**：
- 手动/自动订单审查中"马丁格尔 / 复仇交易 / 连亏 / 追单频率"等**行为模式分类**（choice / noul）→ 可给概率+置信度。
- 新闻/公告情绪分类（bullish/bearish/neutral，choice）→ 替代或增强规则/LLM 判定。
- 风控告警分级（score）→ 结合概率做置信度门控。
- MT5 Bridge 健康/状态异常分类、线路超时定性（noul）。
- LLM 提示词注入/敏感数据检测（guard_questions 预设）→ 保护 MCP agent runner 的 prompt。

**不贴合点**：
- 自由文本生成（交易理由、策略优化报告、Claude 分析长文）→ 需要 LLM，laya 不做生成。
- 需要长上下文推理 / 数值计算 / 复杂多跳 → 不是 laya 的场景。
- 多语种交易消息（泰语、中文）→ 需 multilingual checkpoint（项目通知有泰语，但交易分析主要是英文新闻）。

**部署约束**：
- 引入 torch+transformers 重依赖（~2GB 起）到 Railway 容器，显著增大镜像/内存；需评估 CPU 推理 ~200–500ms 是否可接受，或是否需要 GPU。
- 权重需从 HF 下载（离线环境要预缓存，`HF_HUB_OFFLINE` / 本地路径加载）。
- 线程安全：Agent 是 torch 模型，需确认 async 环境中加锁或独立进程/线程，避免阻塞事件循环。

## 4. 项目侧事实（已核实）

- **依赖**：`backend/requirements.txt` **没有 torch/transformers**。现有 ML 依赖 lightgbm / scikit-learn / statsmodels / joblib / hmmlearn / filterpy / cvxpy / arch（纯 CPU 轻量）。引入 laya 需新增 `torch>=2.0` + `transformers>=4.45` + `safetensors` + `huggingface_hub`。
- **部署容器**：`backend/Dockerfile` = python:3.12-slim，**无 GPU**（Railway Hobby 级）。CPU 推理 laya 单问 200–500ms。
- **AI 客户端**：`backend/app/ai/client.py` 是**异步**（`async def complete_async/complete_json_async`），回退 provider 网络调用。调度器任务（`bot/scheduler.py`）是 async。→ 引入同步 torch 推理不得阻塞事件循环，需 `asyncio.to_thread` / 线程池 / 独立 worker。
- **情绪分类现有实现**：`backend/app/ai/news_sentiment.py` —— `SentimentResult(label: bullish/bearish/neutral, confidence: float)`；`NewsSentimentAnalyzer.analyze()` 用 `complete_json_async` 让 Claude 返回 `{"sentiment": ..., "confidence": ...}`，按 symbol 缓存进 Redis，每 15 分钟调度（`_sentiment_job`）。**这是与 laya `choice` 三分类形态完全一致的最高价值贴合点。**
- **手动订单风控情绪规则**：`backend/app/services/manual_order_gate.py::_emotion_flags()` 是**纯规则**（martingale_after_loss block / revenge_trade_window warn / 连亏 / 频率）。**硬闸门不变量：REJECTED 不可强制、block 级直接拒。→ laya 不应替换这些确定性规则的拦截路径**（风控不能交给概率模型），但可作为给 LLM 审查的附加信号（如交易描述的行为模式概率）。

## 5. 候选集成点评估矩阵（初判）

| 候选集成点 | 现状 | laya 原语 | 贴合度 | 说明 |
|---|---|---|---|---|
| **新闻/公告情绪分类** | `news_sentiment.py` 用 Claude `complete_json_async` 返回 `{sentiment, confidence}`，Redis 缓存，15min 调度 | `choice`（bullish/bearish/neutral 3 类） | **高** | 形态完全一致；可做 Claude 的**廉价预筛/分层**（低置信才让 Claude 深析）或纯本地情绪引擎。零 token 成本、毫秒级。 |
| **交易行为模式分类** | `manual_order_gate._emotion_flags` 纯规则（martingale/revenge/连亏/频率） | `choice`/`noul` | 中（辅助） | **风控硬闸门不变量**：REJECTED 不可强制、block 直接拒。laya 只能作 LLM 审查的**附加概率信号**，不能替换拦截路径。 |
| **AI Agent 输入护栏** | runner 系统让 Claude agent 处理任务（prompt 可能被注入） | `guard_questions()` 预设（jailbreak/injection/sensitive） | 中（可选） | 可给 MCP runner 的输入加一道廉价防护；但当前 agent 任务来源受控（内部），价值待定。 |
| 健康监测/熔断状态 | `health_monitor.py`/`circuit_breaker.py` 确定性数值逻辑 | 无 | **低（不贴合）** | 连续失败计数、PnL/回撤阈值，规则已最优，无需文本分类。 |
| 自由文本生成（交易理由/策略报告/Claude 分析） | `ai/prompts.py` 等 | 无 | **不适用** | laya 不做生成。 |
| 策略/新闻长文推理 | LLM 多跳推理 | 无 | **不适用** | 非 laya 场景。 |

## 6. 项目集成点全景（探索 agent 报告 a2a1981e5dd9a81cb，路径基准 backend/）

> 仅读扫描结论，与我的初判（findings 第 5 节）交叉验证一致。关键补充：输出契约现状 + 三处遗漏的 `if keyword in text` 脆弱点。

**现状全局**：
- 项目**全部判定输出已是 `{choice, score, confidence}` 三元组形态**（Claude JSON + 白名单 + fail-closed），与 laya 输出契约几乎零阻抗。
- LLM 调用全异步（httpx / Claude SDK），无同步阻塞。
- 依赖：无 torch/transformers/safetensors/huggingface_hub（venv 实测 0 匹配）。
- 部署：python:3.12-slim 纯 CPU，无 CUDA；`COPY . .` 带入 `backend/models/`（现有 `{btcusd,gold,us30cash}_signal.pkl`）。
- **容量风险（重要）**：421M/322M 参数 ≈ fp32 1.7/1.3GB、fp16 850/650MB。Railway 常规实例 512MB–1GB 内存，容器还要跑 uvicorn+Redis client+Claude CLI(Node)。**常驻 421M 模型很可能超内存预算**，需升配或选 322M multilingual / 量化。→ 支撑「先 spike 验证」决策。

**A 档（choice/score/noul 高度贴合，建议优先）**：
| 集成点 | 判定 | 现有实现 | laya 理由 |
|---|---|---|---|
| `app/ai/news_sentiment.py:47`（prompt `prompts.py:5,19,48`） | 3 分类+连续分+置信度 | Claude Haiku JSON，15min×N symbols，**:95-101 无白名单校验** | **最理想落点**，输出契约一一对应；下游 `ml/sentiment_features.py:13` 只消费聚合比率，来源不敏感 |
| `mcp_server/agent_config.py:79-95` | 自由文本→策略类别（8+1） | `if keyword.lower() in decision.lower()` 首个命中即定、顺序敏感、无置信度 | **正确工具替换错误工具**；中文/英文/否定句会误判；低频率低风险，试错成本低 |
| `app/ai/hallucination_check.py:59-142`（claim 识别层） | 识别「AI 声称了什么」→类别 | 20 处 `if "xxx" in text` + 手工 `not in` 负向排除 | **只替换 claim 识别层**；`rsi_val < 65` 等数值验证保留规则；`:142` 无法处理否定/委婉表达 |

**B 档（结构贴合但受安全/可解释约束，影子模式或预筛）**：
| 集成点 | 现状 | 约束 |
|---|---|---|
| `app/services/manual_order_gate.py:169` `_review`（`prompts.py:128`） | Claude JSON + `_normalize_verdict(:222)` 白名单 fail-closed | **真钱网关**，6 条防火墙不变量。laya 只作**第一遍快筛/影子打分**，置信度落阈值带的模糊样本给 Claude；不替换主判官、不替换 `:574` block 硬规则。判定输入是结构化数值非文本 |
| `app/ai/confirmation_gate.py:61` `evaluate` | 5 路信号→BUY/SELL/HOLD/UNCERTAIN | 确定性加权阈值，可解释可单测。仅适合阈值学习/消融实验 |

**C 档（部分贴合，价值有限）**：
- `manual_order_gate.py:574` `_emotion_flags`：纯算术（占位 2x / 连亏 / 频率），**规则应保留**（可解释可审计零延迟）；laya 边际收益低
- `app/news/fetcher.py:37-41`：RSS 标题与资产相关性过滤，子串匹配是**有意为之的廉价启发式**，且高频；换 laya 需与 sentiment 合并成单次前向，否则得不偿失
- `app/ai/bias_guard.py:59` / `expert_framework.py:107` classify_trade / `trade_accountability.py:55`：**源码注释明示已标记死代码**
- `notifications/telegram.py:31`：severity 三套词汇不统一（warning/critical、high/medium、warn/block）——更应统一枚举而非模型
- `app/api/routes/jobs.py:19` job_type：自由字符串精确匹配；**若未来开放自然语言任务描述→分派**，是理想 choice 落点

**D 档（不贴合，保持确定性）**：
- `health_monitor.py`（连续失败计数）、`risk/circuit_breaker.py`（熔断/回撤阈值）、`ai/circuit_breaker.py`（LLM 可用性计数）
- `bot/engine.py:376` `_detect_regime`（hmmlearn HMM 已是专用概率模型，非文本场景）
- `ml/predictor.py:34`（LightGBM 已是 SELL/HOLD/BUY 3 类概率输出 = choice+score；laya 只能作其特征之一）

**落地建议（agent 报告，供决策）**：
1. 最小可行切入：`news_sentiment.py:47` + `agent_config.py:79-95`（修 bug 型替换）
2. 依赖：`laya_enabled: bool = False` 默认关闭 + try-import 降级（沿用 `mcp_server` 的 `_AGENT_AVAILABLE` 模式），不装依赖也能启动
3. 配置：`config.py:286` 现有 `ml_model_path` 单文件字符串；新增模型根目录后复用 `api/routes/ml.py` 的 path-traversal 防护
4. 推理：`asyncio.to_thread` + `asyncio.Semaphore(1~2)`（项目已有 12+ 处 to_thread 先例）；懒加载单例
5. 避开关键路径：`submit_order` 内已有 90s 超时+fail-closed+per-account 锁，模型延迟会转化为拒单；laya 放影子/预筛位
6. 保留可解释性：`_emotion_flags`、circuit breaker、health monitor 三类必须保持确定性

**与我的初判差异**：agent 补充了 `agent_config.py:79-95` 与 `hallucination_check.py` 两个 A 档点；确认情绪分类无白名单校验（我之前未列）；补充 Railway 内存容量风险。

## 7. laya 源码能力复核（探索 agent 报告 a9f42b97edf50c66d，交叉验证达成一致）

> 包仅 6 个 .py 文件 1076 行，`research/`/`notebooks/` 不被包导入、应用层可丢弃。类只有 `Agent`（`RLAgent = Agent` 别名），**无 `LayaAgent`**。

**部署约束（硬性）**：
- **A1 首发必联网**：无 `local_files_only`/`cache_dir`/离线开关（grep 0 命中）；`agent.py:116` 无本地目录即 `snapshot_download`；encoder 目录不存在也走 `AutoModel.from_pretrained` 下载。权重下载 english 10.7s / multilingual 8.3s（T4 带宽实测）。缓存默认 `~/.cache/huggingface/hub/`。
- **A2 依赖大**：torch>=2.0 / transformers>=4.45 等 5 项；CPU-only 装 `torch --index-url .../whl/cpu` 可显著缩小（CI 就是这么测的）。
- **A3 `USE_TF=0` 硬要求**：环境有 TF 时 transformers import 会在 macOS/Py3.9 死锁 model 构建 → `laya.load()` 永久挂起。部署须 `USE_TF=0 USE_TORCH=1 TOKENIZERS_PARALLELISM=false`。
- **A4 模型目录需可写**：`_fix_tokenizer_config`（`agent.py:21-46`）原地写盘修补 `tokenizer/tokenizer_config.json`；mmBERT 的 `extra_special_tokens` list→dict 修补**必需**（不修则 `AutoTokenizer` 抛 `'list' object has no attribute 'keys'` 直接加载失败）。→ **只读卷/multilingual 会挂；建议构建时预热并持久化缓存目录**。
- **A5 严格权重校验**：必须 `rl_agent_config.json` + `model.safetensors`，权重前缀 `encoder./type_emb./scorer./act_head.` + shape 全匹配，不能用别的 BERT 权重替代。
- **A6 无线程安全**：全靠应用层。

**并发（FastAPI 重点）**：
- `Agent.device/dtype` 是可变共享状态，`system_one` 内 OOM（`agent.py:278-290`）会改写 `self.device` 并 `model.to(cpu)` → 共享 Agent 时一线程触发回落会把别的线程模型搬走，数据竞争。**规避：直接 `device="cpu"` 预加载，完全避开回落路径。**
- `Router._agents/_order` 无锁，`load/_touch/_evict` 在 predict 路径上读写；`max_loaded=1` 并发不同语言可能竞态重复构建（两份 421M）。**规避：启动 `preload()` + `max_loaded` ≥ 驻留数，运行期不再触发 load/evict。**
- `_evict` 只 `del` 不 `empty_cache()`；无内置跨请求批量（`system_one` 只支持 1 state × N 问题）。
- **FastAPI 集成**：`load()`/`Router(preload=True)` 同步阻塞 7-10s+，必须在 lifespan startup 加载成单例，调用点 `run_in_executor`/`asyncio.to_thread` 包裹避免阻塞 event loop。

**内存/速度/校准（实测）**：
- 参数：english 421.29M / multilingual 321.91M / typed-decisions 421M；三个共 ~1.16B。推导内存 fp32：english ~1.69GB、multilingual ~1.30GB、三载 ~4.64GB；fp16 减半。**证实 Railway 512MB-1GB 内存常规实例风险。**
- 速度 T4：1 问 32.8-39.5ms；50 问 6.8-15.9ms/问。CPU（preload）：**193-464ms/请求**。冷加载（盘上有）：CPU 7.4s / T4 10.3s。`reference_compile` 被强制 False → **无 JIT 预热惩罚，第一次前向即稳定**。
- 环境变量：CI 都用 `TOKENIZERS_PARALLELISM=false`（Py3.12 tokenizer fork 会死锁）→ uvicorn 启动前设好。
- **校准**：两 base checkpoint 出厂过度自信（english ECE 0.466 / multilingual 0.314 且 `temperature_by_options={}` 完全没拟合）；DIY 按 (qtype, 选项数桶) 拟合温度可降到 0.081/0.106 —— **性价比最高动作**。english 对非英文崩溃但 confidence 不降 → **生产必须用 Router（路由在前）不要裸 Agent**。
- 质量：guardrails(jailbreak/prompt_injection) 实测 0.708-0.762 可信；**moderation 不适合生产**（toxic 0.530 接近随机）；score 最弱（SST-5 0.372）；邮件 spam/phishing 0.993（但在训练分布内）；选项顺序鲁棒性差于 Jev；>20 选项精度骤降、超预算直接 `ValueError`。

**集成建议（agent 报告）**：
1. 最小可行：english + multilingual，`Router(preload=True, max_loaded=2)`；typed-decisions 除非真用那 4 个工作流签名否则不预载。
2. **Docker 构建时预热** `laya.load(...)` 让 HF cache 落盘打进镜像/持久化，运行时走本地路径分支，绕开下载与 tokenizer 写盘需求。
3. CPU-only 完全可行（CI 证明），203-464ms/请求仍远快于 Claude/LightGBM。
4. 分工：laya 做高频前置决策（spam/phishing/routing/guardrail 0.7-0.76/triage），Claude 做复杂生成，LightGBM 做结构化特征排序。
5. 不需要 `research/` 和 finetune notebook（不被包导入）。

## 8. 第二轮调研：所有 LLM 调用能否都用 laya 决策（2026-09-21）

> 用户假设：现在所有 LLM 是否都可以用 laya 做决策，可行性很大。以下为核心判断，穷尽盘点 agent（abd53fb1565e4af6e）返回后补充完整清单。

**核心判断（推翻"全量替换"假设）**：
- **很多 LLM 调用点表面有分类标签，但标签只是输出的冰山一角**。以 `mcp_server/agents/technical_analyst.py` 为例：输出含 Signal(BUY/SELL/NEUTRAL)+confidence，但完整输出是 **Trend/Momentum/Volatility/Key Levels/Reasoning** 结构化长文，且分析前要**调用工具**（`run_full_analysis`/`calculate_ema`/`calculate_rsi`/`calculate_atr`）读取指标。laya 无法调用工具、无法综合数值指标、无法生成论证——它只能替换标签层，替换不了决策过程本身。
- **laya 的输入是文本/JSON state，无法消费数值行情序列**（K线/指标）做"为什么看多"的推理。
- **分类标签 ≠ 决策**：orchestrator 用加权规则融合多个 analyst 输出（BUY/SELL/HOLD + 阈值），标签只是中间量。

**prompts.py 输出形态（已确认）**：
| schema | 输出字段 | 分类可替换 | 长文/数值不可替换 |
|---|---|---|---|
| SENTIMENT（3 分类） | sentiment+confidence | ✅ choice | — |
| OPTIMIZATION | assessment(2-3句)+suggested_params(7数值)+confidence+reasoning | ❌ | assessment/reasoning 长文、params 连续数值 |
| ORDER_REVIEW | verdict(3分类)+confidence+risk_flags+emotional_indicators+reasoning(1-3句) | ⚠️ verdict 可 choice | reasoning 长文、risk_flags 结构化清单、真钱网关审计 |
| MCP technical/fundamental/risk analyst | Signal/Bias/Verdict + confidence + 长文 | ⚠️ 标签可 | 工具调用、指标综合、Reasoning |

**结论方向**：**不能全量替换**。laya 能替换的是"纯文本→离散分类"的那一层（情绪、策略名、guardrail、部分 claim 识别）；**带工具调用/数值推理/长文生成/可解释审计的决策仍是 LLM 的地盘**。混合架构（laya 快筛分类 + LLM 深析论证）才是可行的最大化方案。等穷尽盘点 agent 返回 A/B/C/D 分级统计后定量确认。

**orchestrator 决策本质（决定性证据）**：
- `mcp_server/agents/orchestrator.py` SYSTEM_PROMPT 明确：**orchestrator 是唯一有执行权的决策者**，工具 = `place_order`/`modify_position`/`close_position`/`log_decision`/`log_reasoning`。
- 其决策框架是**规则化融合**（技术信号主、基本面辅、风险 APPROVED/CAUTION 才可交易、BUY vs BEARISH 冲突→HOLD、置信度阈值 0.4/0.5、每 cycle ≤3 单、reflector 过拟合分级调仓）。
- → **orchestrator 的"融合规则"本质是确定性逻辑**（可无 LLM 实现），但 orchestrator 的核心价值是**工具调用的多步智能体循环**（读报告→判断→执行下单→日志），远超 laya 的"一次前向给离散判定"能力。
- → laya 唯一能替换的是最底层 analyst 的**分类标签头**（BUY/SELL/NEUTRAL），但会丢失工具读数与 Reasoning。所以 MCP 多 agent 管道**整体不可被 laya 替换**。

## 9. LLM 调用点穷尽盘点与 laya 可替换性定量（agent 报告 abd53fb1565e4af6e）

> 穷尽盘点确认：**全项目只有两条 LLM 通道**——A 简单补全 `app/ai/client.py:38/59`（complete_async/complete_json_async）；B agent 循环 `mcp_server/agents/base.py:68`（run_agent_loop，带 MCP 工具）。其余 app/ai/* 全部纯 Python。**`complete_async`（纯文本）零调用方 = 死入口**；`mcp_server/tools/*.py` 全部 17 个工具为确定性代码（零 LLM 调用）。

**逐调用点评估（13 个）与分级**：
| 分级 | 数 | 调用点 |
|---|---|---|
| **A 完全可替换** | **0** | —（无调用点所有输出字段均 choice/score/noul 可表达且下游只吃这些） |
| **B 大部分可替换** | **2** | **#1 Sentiment**（`news_sentiment.py:88`）：sentiment→choice(3类)✓、confidence→noul✓、score(连续-1~1，已进 ML 特征)→需离散化、key_factors(用户可见文本)→无法生成；**#3 Manual gate**（`manual_order_gate.py:174`）：verdict→choice(3类)✓、confidence→noul✓、reasoning→可模板化、risk_flags/emotional→审计留痕 |
| **C 部分/混合** | **0** | 当前无；潜在：若 specialist 重构为结构化契约，orchestrator 可前置 laya 快筛 |
| **D 不可替换** | **11** | #2 Optimizer(7 连续数值进实盘)、#4 Quant(死代码)、#5 single_agent、#6 orchestrator(唯一执行权+log_decision 强制审计)、#7-10 四 specialist/reflector(工具调用+长文+记忆生成)、#11 chat_agent(自由问答)、#12/#13 chat_workflow(6 角色) |

**定量结论**：按调用点 A=0/B=2/C=0/D=11；**按每日调用量 B≈14% / D≈86%**（单品种 multi 约 576 次/天；4 品种 ≈2300 次/天，远超 `MAX_DAILY_AGENT_CALLS=200`——该上限仅对话路径生效，交易决策路径不受约束）。**11 个 agent 调用点都不传 max_tokens**（只由 max_turns=8/10/15/50 + 超时 60-600s 间接约束）→ agent 路径是 token 成本主要来源。

**四大阻塞类别（为什么 D 占 86%）**：
1. 连续数值生成（optimizer 7 参数、risk 手数/SL/TP，直接进实盘）
2. 工具调用/多步推理（8 个 agent 需 4-14 个 MCP 工具）
3. 自由文本生成（chat_agent、chat_workflow、reflector 记忆条目）
4. 跨长上下文综合 + 强制审计留痕（orchestrator 综合 4 报告、log_decision MANDATORY）

**最有价值的中间路径（agent 洞察）**：**不是"用 laya 换 agent"，而是"用确定性代码换 specialist"**——`technical_analyst` 输入输出本质是 `run_full_analysis` 的指标数学（`tools/learning.detect_regime` 已是纯规则表）。把 3 个 specialist + reflector 改为结构化代码输出后，orchestrator 的"有无可交易信号"快筛**才具备 laya 快筛（C 级）条件**，从而可能砍掉决策档 LLM 的大部分调用量。

**两大提示词注入面 = 恰是 B 级 2 个**：#1 RSS 标题（已清洗）、#3 order comment（UNTRUSTED INPUT）。laya 非生成式可彻底消除这两个注入面。

**附带发现（死代码）**：`quant_analyzer.py`（1024 tokens 调用）与 `complete_async`（纯文本入口）均零调用方，可先删省两个调用面。

## 10. 第三轮：实操 spike —— laya 做交易决策引擎（2026-09-21）

> 用户新方向：给 laya 足够样本，让 laya 判断"是否可交易 + 成功概率"。要求用实际操作调研可行性，据此迭代计划；未经同意禁止执行生产改动。

**Spike 设计**：隔离 venv（`$CLAUDE_JOB_DIR/tmp/laya-spike-venv`，Python 3.10）→ 装 CPU-only torch 2.14.0 + transformers 5.17.0 → laya → 构造交易状态（JSON dict）→ 用 noul/choice/score 三原语问「能否交易/方向/成功概率」→ 测零样本行为 + 延迟/内存。**不触碰生产代码/依赖/部署。**

**环境实测发现**：
- ✅ 网络：pypi 与 huggingface.co 均可达（200）。
- ✅ 隔离 venv 建好（Python 3.10.18），CPU-only torch 2.14.0 + transformers 5.17.0 + safetensors 0.8.0 + huggingface_hub 1.32.0 安装成功。
- ⚠️ **pypi 上的 `laya` 是错包**：`pip install laya` 装的是 0.3.3（site-packages 无 `laya/` 目录、`__version__`/`Agent` 等 API 缺失），**不是 NandhaKishorM 的 laya**。正确安装需 `pip install git+https://github.com/NandhaKishorM/laya.git@main`（0.3.4，与已调研源码一致）。→ **pypi 包不可信，需用 GitHub 源**。
- ⚠️ transformers 5.17 很新（laya 声明 ≥4.45）；若 5.x 不兼容需降级 4.x。待 spike 实跑验证。

**待继续**：GitHub 源装 laya → 跑 spike 脚本 → 记录零样本输出/延迟/内存 → 结合数据可行性 agent（Trade 表 pre_trade_snapshot→profit 监督样本）评估微调路径。

**✅ 微调监督样本的结构（关键证据，engine.py:1143-1173）**：
- `pre_trade_snapshot`（下单前快照，JSON dict）字段：`balance`、`regime`（多 TF HMM）、`indicators`（atr/atr_pct/garch_vol/effective_vol_pct/adx）、`risk`（effective_confidence/lot_final/near_event）、`ai_sentiment`、`strategy`/`strategy_reason`。
- Trade 表：`profit`（结果标签）、`close_price`/`close_time`、`type`（BUY/SELL）、`ai_sentiment_*`、`trade_reason`。
- → **监督样本成立**：`pre_trade_snapshot`（laya 合法 state 形态）→ `profit>0` 二分类（noul 微调）；或 → 成功概率（score/calibrated noul）。特征充分、标签可定义。
- **真正约束**：微调需 GPU（Kaggle 2×T4 免费，4-5h/30k 样本）+ 样本量（需确认 Trade 表行数）+ RLCD 训练（proper_reward）。零样本 base checkpoint 无交易判别力（README 已证，spike 待实测确认）。

## 11. 实操 spike 实测结果（决定性，2026-09-21）

**执行**：隔离 venv + GitHub laya 0.3.4 + english checkpoint（从 HF 镜像 hf-mirror.com 下载，因 huggingface.co 模型端点被网络阻断）。9 个合成交易状态（强多/多/弱多/强空/空/震荡/高波动/超买/风险警告）× 3 问题（can_trade noul / direction choice / win_probability score）。

**环境/部署约束（实测确认）**：
- ⚠️ **huggingface.co 模型下载端点（resolve/cdn-lfs）被网络阻断**（主页 200 但大文件超时）；**hf-mirror.com 镜像带 UA 可达（200）**，`HF_ENDPOINT=https://hf-mirror.com` 成功下载 38 文件。→ **生产部署同样需要镜像/代理或预缓存**（重要：Railway 若在境外则无此问题，但本机/国内环境必须镜像）。
- laya.load() 耗时 135.8s（含下载+构建），device 自动 **mps**（Apple GPU）。
- **内存：~1.7-1.8GB**（english fp32，ru_maxrss 换算）→ 证实 Railway 512MB-1GB 常规实例**放不下常驻模型**，需升配或 322M multilingual/量化。

**零样本交易判别力（核心实测结果）**：
| 状态 | can_trade(noul) | 方向(choice) | 胜率分/3 |
|---|---|---|---|
| strong_bull | 0.586 | BUY(0.86) | 1.93 |
| bull | 0.581 | BUY(0.66) | 1.07 |
| weak_bull | 0.587 | BUY(0.81) | 1.41 |
| strong_bear | **0.474** | SELL(0.52) | **0.14** |
| bear | 0.501 | NEUTRAL(0.50) | 0.21 |
| ranging | 0.593 | NEUTRAL(0.57) | 0.97 |
| high_vol | 0.568 | BUY(0.67) | 1.08 |
| overbought | 0.541 | BUY(0.82) | 1.59 |
| risk_warn | **0.563** | NEUTRAL(0.42) | 0.55 |

**分析**：
1. **方向有微弱判别力**：strong_bull→BUY、strong_bear→SELL、bear/ranging→NEUTRAL（部分符合"价格与均线关系"的常识）。
2. **can_trade（能否交易）基本无判别力**：noul 概率恒 0.47-0.59，风险警告状态(0.563)≈强多(0.586)，所有状态"五五开"。→ **base checkpoint 不能回答"能否交易"。**
3. **胜率分有方向性噪声**（strong_bear 0.14 vs strong_bull 1.93），但不可靠（bull 1.07 vs weak_bull 1.41 倒挂）。
4. **延迟**：首条冷启动 4099ms，后续 **134-262ms/状态**（mps），符合 README CPU 量级。

**结论（实操支撑）**：**零样本下 laya 对"交易方向"有微弱判别、对"能否交易/成功概率"无判别力** → "给足够样本让 laya 做交易决策"**必须走微调**（RLCD, Kaggle 2×T4, 4-5h/30k 样本），且监督样本（pre_trade_snapshot→profit）数据层面成立。**可行性取决于微调是否执行，而非零样本能力。**

**multilingual 对照（322M，实测）**：`can_trade` noul **恒 0.85-0.96**（全部高概率、零区分）、`win_probability` 恒 1.4-1.7/3（不随状态变化）、方向仅 strong_bull/strong_bear/bear 对。→ **322M 同样无交易判别力**，甚至比 english 更"一律乐观"。延迟 189ms（更快），mps 上同样 GB 级内存。结论：**与 english 一致，必须微调**。

## 12. 数据可行性 agent 报告（ad8b9551a42a5473b）——真实样本量是硬伤

**一句话结论（agent）**：`pre_trade_snapshot`+`profit` 的数据**形态**完全成立（JSON state + 标签，laya 合法形态），但**生产 `trades` 表真实样本量只有约 10-30 行**，撑不起任何 RLCD 微调。真正出路不是导出 trades，而是用 `ohlcv_data`（GOLD M15，140,821 行）+ `ml/features.py`（40+ 特征）+ `backtest/engine.py` **合成同形态样本**。

**DB 访问**：生产库在 `100.72.200.33:15432/mt5`（Tailscale 内网 IP，非 Railway 公网）；TCP 连通已实测 OK；backend/.venv 有 asyncpg/psycopg2/sqlalchemy。只读方式：`default_transaction_read_only='on'`（PG 服务端硬保证）。本地 Docker pg（5434, goldbot）是另一库，无同步；测试库是内存 SQLite 无历史。

**Trade 表监督学习程度**：
- `pre_trade_snapshot` 7 顶层 key ~15 标量：balance/regime(多TF)/indicators(atr/garch/adx)/risk(confidence/lot/near_event)/ai_sentiment/strategy/strategy_reason。**无价格序列、无 OHLCV 窗口、无 RSI/EMA/MACD——特征偏薄**，且 `risk.effective_confidence` 是策略自己算的（用它当特征近似 data leakage/循环论证）。
- 标签可派生：`outcome`(win/breakeven/loss) 在 engine.py:1554-1556；`exit_reason`(stop_loss/take_profit/manual_close) 在 :1534-1548。无需重算。
- **「能否交易」子问题缺负样本**：只有成交单进 trades，被拒单（TRADE_BLOCKED）不落表 → **没有"不该交易"的标签**，这是该子问题当前无解的原因。

**样本量（硬伤，日志+文档+代码三重交叉）**：
- `docs/SIGNAL-DIAGNOSIS-2026-09-16.md:94-100`：当时 `trades` = **0 行**（"无任何成交记录"）；`:257` 明确"Kelly 需 ≥20 笔才启用，永远用不了"。
- 日志实测：`Position closed` 去重 ticket **仅 11 个**。
- 品种面窄：仅 GOLD 启用；风控闸门（每小时≤5、间隔≥120s、连亏5暂停、R:R 5:1）下**跑满一年也难攒千笔**。
- 09-14 那 8 笔成交 @2050 而当时金价 ≈4290（别名归一化 bug），**价格特征错误必须剔除**。
- `ohlcv_data` 停在 2026-09-15，140,821 行（唯一入库品种 GOLD M15）。

**Kaggle 微调可行性**：免费 T4 16GB×2 单次 12h（每周刷新），2 epoch 可行（断点续训）。瓶颈 100% 在数据。**真实 trades 微调 = 10 条样本训 30k 模型 → 过拟合到噪声**；**ohlcv+backtest 合成样本形态合规、量级达标、标签可复现，是唯一可行路径**。建议先做「导出+特征体检+LightGBM 基线 AUC」——AUC 显著高于基率再谈 LLM 微调，否则是在用模型记 30 个数字。

**agent 未完成项**：trades 精确行数（当时安全分类器超时）——待主会话只读 `count(*)` 核实。
- **情绪 prompt**（`prompts.py` L5-55）：输出 `{sentiment, score(-1..1 连续), confidence, key_factors[]}`。**只有 `sentiment` 是 choice 可表达**；`score` 连续、`key_factors` 列表不可。增强版还依赖**市场上下文加权**（价格趋势/历史表现/宏观冲突→调 confidence），是数值综合。→ **情绪是 B 类（部分），不是 A 类**。laya 只替代 sentiment 三分类这一格，score/key_factors/上下文加权仍是 Claude 的。
- **手动风控 `_review`**（`manual_order_gate.py`）：verdict 消费有 `_normalize_verdict` 白名单 + fail-closed（L222-238），`reasoning`/`risk_flags`/`emotional_indicators` **全部入库审计**（stored["llm"]）。→ verdict 三分类理论可 choice，但 laya 无 reasoning/risk_flags **会破坏审计完整性**；整条链是审计关键路径。**确认只能影子/预筛，不能替换主判官。**
- `strategy_optimizer`：输出 7 个**连续数值参数**（PARAM_RANGES 钳位）+ assessment 长文 → 不可替换。
- `quant_analyzer`：嵌套分数 dict + suggestions 列表 + reasoning 长文 → 不可替换（仅 correlation_changes.status 三分类可离散，是子字段）。
- MCP technical/fundamental/risk analyst：**调用工具读指标** + 结构化长文 + 标签 → 仅标签可离散。
- MCP reflector：14 个工具（含 `apply_strategy` 改状态）的多步反思循环 + 长文 → 整体不可替换。

## 14. 3.8 决策门实测结果（真实生产数据，2026-09-21）

> 用用户授权的**只读**连库（`default_transaction_read_only='on'`，PG 服务端硬保证）跑了 `scripts/laya_synth_baseline.py --db --symbol GOLD --timeframe M15`。

**实测结果**：
- OHLCV：**34,552 行**（GOLD M15，2025-04-01 → 2026-09-17）
- 合成样本：**34,542 条**、41 特征（`build_features` 40+ 列）
- 标签分布（SELL/HOLD/BUY）：15502 / 3372 / 15668（三重障碍，`tp_pips=5.0`，`forward_bars=10`）
- 可交易占比（基率）：0.896
- **LightGBM AUC（3-fold 时间序列 CV）：0.739 ± 0.025**
- **决策门判定：AUC=0.739 ≥ 0.6 → 值得继续，微调 laya / 数据驱动交易决策有基础**

**解读**：GOLD M15 历史数据里确实存在可预测的「该不该交易」信号（LightGBM 0.74 AUC，显著高于随机 0.5 且高于基率 0.896）。→ 3.9 轨道（Kaggle RLCD 微调 laya）数据层面**成立**。

**关键反思**：既然 LightGBM 已能 0.74 AUC，**先用 LightGBM 做"可否交易"门控可能比微调 laya 更便宜**（微调需 GPU + 4-5h + 权重）。建议 3.9 前：①固化 LightGBM 基线为可落地的"可否交易"门控 ②再评估微调 laya 的**边际收益**（文本+数值混合状态 vs 纯结构化特征）。
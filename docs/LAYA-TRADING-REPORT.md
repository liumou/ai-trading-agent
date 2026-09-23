# Laya 模型用于交易 —— 详细说明报告

> 日期：2026-09-22
> 分支：`feat/laya-research-and-integration`
> 状态：laya 决策引擎已集成（默认关闭），Trade Gate「可否交易」门控已落地（shadow 验证中）

---

## 1. 报告摘要

本报告说明 **laya 决策引擎**在本交易系统中的定位、能力边界、落地架构、实测数据与部署方式。

**一句话结论**：laya 是「非自回归单次前向的结构化判定引擎」（choice / score / noul 三原语），本项目用它做**高频分类预筛**与**结构化标签抽取**（新闻情绪三分类、策略名分类），**不替换** LLM 的深度决策、长文生成、工具调用与数值推理。配套的 Trade Gate「可否交易」门控（LightGBM 基线，AUC≈0.739）已验证「用数据驱动交易决策」在数据层面成立，为后续 laya 微调（3.9 轨道）提供基础。

当前 laya 默认 **关闭**（`laya_enabled=False`），缺依赖/缺权重/推理失败时自动降级回 LLM，系统照常运行。

---

## 2. Laya 是什么

### 2.1 定位

- 来源：https://github.com/NandhaKishorM/laya （Apache 2.0，可商用；版本 0.3.4，Beta）
- 多语言、**非自回归（单次前向）**的 "System 1 决策引擎"
- 给定任意 state（文本 / JSON dict / 对话列表），一次前向输出若干**结构化判定**，不做自由文本生成
- 单次前向：所有问题拼接进一个序列（`[CLS] 类型+指令 [SEP] [MASK] 选项… [SEP] state [SEP]`），一次过编码器，全部问题并行判定
- 速度实测：T4 GPU ~33ms/问，batch 10 问 ~7.2ms/问；CPU ~200–500ms

### 2.2 三种决策原语

| 原语 | 语义 | 返回值 | 本项目用途 |
|---|---|---|---|
| `choice` | 从 N 个候选标签中选一个 | 各标签概率 + 归一化熵置信度 | 情绪三分类、策略名分类 |
| `score` | 序数刻度上的期望分值 | 期望分数 + legend + 分布 | （评估过，未落地） |
| `noul` | 二值概率 P(true)∈[0,1] | 是否成立 | （评估过，未落地） |

### 2.3 模型 Checkpoint

| checkpoint | 参数 | 上下文 | 语言 |
|---|---|---|---|
| `laya`（english） | 421M (ModernBERT-large) | 512 | 英文 |
| `laya-multilingual` | 322M (mmBERT-base) | 1024 | 100+ 语言 |
| `laya-typed-decisions` | 421M (ModernBERT-large) | 1024 | 4 个合成工作流微调 |

- Router 按脚本/语言检测（纯 Python，<0.5ms）选 checkpoint
- 权重从 HuggingFace `convaiinnovations/laya` 下载，约 1.3–1.7GB fp32/checkpoint

### 2.4 校准特性（重要）

- 概率可校准，但**默认过自信**：english ECE 0.466 → DIY 拟合温度后 0.081
- `confidence` 语义（实测 laya 0.3.4）：原生的 `ans["confidence"]` 是**归一化熵置信度**（`1 - H(p)/log(k)`），不反映「模型对所选类别的把握」。本项目把 confidence **重定义为所选类别的最大类概率**（阈值判断的自然语义），原熵置信度保留为 `entropy_confidence` 参考字段。

---

## 3. 能力边界（诚实限制）

调研（`.planning/2026-09-21-laya-research/findings.md`）确认的关键边界：

1. **不是零样本决策引擎**：基础 checkpoint 在 typed-decisions 基准上接近随机（0.36 vs 随机 0.318 / 多数类 0.461）；0.766 的精度来自微调后的 checkpoint。它是「快速可特化的底座」。
2. **高基数选项（>20 个）精度骤降**：77 选项时每标签仅 3–4 token（Banking77 0.425）。本项目候选集 ≤7，安全。
3. **ordinal score 最弱**（SST-5 0.372）——本项目未用它做风控依据。
4. **english checkpoint 对非英文崩溃**（Khmer 0.000 @ 0.952 置信度——高置信度犯错）。本项目交易新闻为英文，且生产必须走 Router/指定 checkpoint。
5. **不能消费数值行情序列**：输入是文本/JSON state，无法对 K 线/指标做「为什么看多」的推理。
6. **无内置线程安全**：`Agent.device/dtype` 是可变共享状态，OOM 回落会搬模型致数据竞争——本项目显式 `device="cpu"` 预载避开回落路径，并用 `threading.Lock` 串行化首次加载。

### 3.1 全项目 LLM 调用点盘点（为什么不能全量替换）

穷尽盘点（13 个调用点）：**A 完全可替换 = 0；B 大部分可替换 = 2（情绪、手动门控 verdict）；D 不可替换 = 11**。按每日调用量：B≈14% / D≈86%。

四大阻塞类别：
1. 连续数值生成（optimizer 7 参数、risk 手数/SL/TP，直接进实盘）
2. 工具调用/多步推理（8 个 agent 需 4–14 个 MCP 工具）
3. 自由文本生成（chat_agent、reflector 记忆条目等）
4. 跨长上下文综合 + 强制审计留痕（orchestrator）

**结论**：laya 能替换的是「纯文本→离散分类」那一层；带工具调用/数值推理/长文生成/可解释审计的决策仍是 LLM 的地盘。混合架构（laya 快筛分类 + LLM 深析论证）是可行的最大化方案。

---

## 4. 落地架构（backend/app/ai/laya_runtime.py）

### 4.1 设计约束（来自调研 spike 实测）

- **懒加载单例**：首次用到才 `laya.load()`（冷加载 ~136s 含构建），绝不在 import 期加载。
- **线程安全**：`threading.Lock` 串行化首次加载；显式 `device="cpu"` 预载，彻底避开 OOM 回落路径（数据竞争源）。
- **不阻塞事件循环**：推理包 `asyncio.to_thread`（CPU 单次 ~200ms，绝不能进 async 关键路径）。
- **降级**：laya 依赖缺失 / 权重下载失败 → `available=False`，调用方回退 LLM，系统照常运行（try-import 模式，沿用 `mcp_server` 的 `_AGENT_AVAILABLE` 模式）。
- **镜像支持**：`HF_ENDPOINT` 环境变量（实测 huggingface.co 模型端点被阻断，需 hf-mirror.com）。

### 4.2 加载优先级

1. 环境变量 `HF_ENDPOINT` > `config.laya_hf_endpoint` > 不设置（用官方）——Railway 境外直连官方，国内显式设镜像。
2. `settings.laya_model_cache_dir` 本地缓存目录优先（支持构建时预下载到镜像的部署模式）：`<cache_dir>/<model_id basename>` 存在则直接用本地路径。
3. 环境：`TOKENIZERS_PARALLELISM=false`（Py3.12 tokenizer fork 死锁）、`USE_TF=0`（TF 存在时 transformers 会死锁模型构建）。

### 4.3 对外 API

- `LayaRuntime.predict(state, questions)` → 异步推理，包 `to_thread`
- `LayaRuntime.predict_choice(state, question_key, question)` → choice 解析 + 防御：
  - probabilities 为空 → None
  - **choice ≠ argmax → 视为低置信畸形结果返回 None**（M2 防御，绝不把错位 confidence 传给阈值判断）
  - confidence = 最大类概率；entropy_confidence = 原生熵置信度
- `laya_sentiment_choice(headlines, symbol)` → 情绪三分类预筛（bullish/bearish/neutral + 概率 + 置信度）
- `laya_strategy_choice(decision, confidence_threshold)` → 策略名分类（6 类 + ai_autonomous 兜底）
- `get_laya_runtime()` → 模块级单例

### 4.4 白名单校验

- `SENTIMENT_LABELS = {bullish, bearish, neutral}`：laya 返回候选外值（如 "positive"/"unknown"）一律回落 LLM（I2 修复，防 KeyError 吞掉整个分析）。
- `STRATEGY_LABELS = {trend_following, mean_reversion, breakout, momentum_rank, hold, ai_autonomous}`：
  - I4 修复：此前含 "momentum"，注册表里是 "momentum_rank"，一旦 strategy_switch 接上会 `ValueError`。
  - `trend_following` 保留：它是 keyword 兜底链路的既有契约名（`test_llm_lang` 断言），仅用于展示，不 resolve 到实现。

---

## 5. 三个实际接入点

### 5.1 新闻情绪三分类预筛（backend/app/ai/news_sentiment.py）

- 流程：`_clean_headlines`（RSS 标题清洗：去换行/指令注入分隔符、截断 200 字符/条、拼接最多 3000 字符）→ `laya_sentiment_choice` → 高置信（≥ `laya_confidence_threshold`，默认 0.85）直接采用，**省一次 LLM 调用（零 token、毫秒级）**；低置信/不可用回退 Claude 深析。
- laya 只给三分类；`score` 用 label 近似映射（bullish 0.5 / bearish -0.5 / neutral 0.0），避免凭空造连续值。
- 审计：预筛命中**也写 DB 审计行**（`raw_response` 显式标记 `engine: laya` + probabilities + confidence），避免 ML 情绪特征静默饿死；Redis 缓存同源。
- `SentimentResult.engine` 字段（`llm | laya`）经 API/前端展示；旧缓存缺字段时 dataclass 默认 "llm"（向后兼容）。

### 5.2 策略名抽取（backend/mcp_server/agent_config.py）

- 从 AI 决策文本抽取策略名：**优先 laya choice**（语义分类，抗中文/否定误判），回退原 `if keyword in text` 子串匹配（顺序敏感是已知局限）。
- I3 修复：默认对结果施加置信阈值（`laya_strategy_confidence_threshold`，默认 0.6）——否则 laya 最低置信的猜测会覆盖 keyword 兜底（关键词命中时是近确定性信号）。
- 结果仅用于展示（scheduler 的 `strategy_used`），不 resolve 到策略实现。

### 5.3 Trade Gate「可否交易」门控（backend/app/ml/trade_gate.py）

> 注：这是 laya 调研的第 3.8 步落地——先用 **LightGBM 基线**验证「该不该交易」信号是否存在，作为 laya 微调（3.9）的数据前提。它证明数据基础成立，本身是确定性 ML 模型而非 laya 权重。

- 输入：当前 OHLCV 状态（最后一根 bar，`df.iloc[-2]` 决策同源语义对齐）→ `build_features` 40+ 特征 → LightGBM 二分类 P(可交易)。
- 训练：`backend/scripts/laya_synth_baseline.py --db --symbol GOLD --timeframe M15`（只读连库，绝不写生产库）。
- 三种返回语义（C2 修复）：
  - `(True, p)` 通过
  - `(False, p)` 明确拒绝
  - `(None, 0.0)` **弃权**（数据不足/模型不可用/schema 问题）→ 调用方必须放行，绝不允许数据问题被转译成全量拒单。
- 双开关（I1 修复）：
  - `trade_gate_shadow=True`（默认）：记录判定与概率、**不否决**交易（影子验证）
  - `trade_gate_enforce=False`（默认）：判定不通过则拒绝交易（等影子数据积累后再开）
- 健壮性：
  - `joblib.load` 失败 → fail-open 禁用 gate（曾实测 pkl 依赖模块被卸载导致构造崩溃）
  - 加载期最小可信度校验：必须有 `predict_proba`、阈值 ∈ [0,1]
  - 旧模型 schema 对齐：历史 feature_columns 含裸 `volume` 时注入 `tick_volume → volume` 别名列（C1 修复）
  - 最后一根 bar NaN 比例 > 30% → 弃权（避免门控基于退化特征做硬否决）
  - 引擎中 gate 异常一律降级放行（`TradeGate check failed, allowing trade`）

---

## 6. 数据基础与实测结果（Phase 3.8）

用**只读**连库（`default_transaction_read_only='on'`，PG 服务端硬保证）跑 `laya_synth_baseline.py`：

| 指标 | 数值 |
|---|---|
| OHLCV | 34,552 行（GOLD M15，2025-04-01 → 2026-09-17） |
| 合成样本 | 34,542 条、41 特征（`build_features` 40+ 列） |
| 标签分布（SELL/HOLD/BUY） | 15502 / 3372 / 15668（三重障碍，`tp_pips=5.0`，`forward_bars=10`） |
| 可交易占比（基率） | 0.896 |
| **LightGBM AUC（3-fold TS）** | **0.739 ± 0.025** |
| 决策门判定 | AUC ≥ 0.6 → **值得继续**（微调 laya / 数据驱动交易决策有基础） |

### 6.1 标签方法（build_labels）

- 三重障碍标签：UP barrier 先触 → BUY(1)；DOWN barrier 先触 → SELL(-1)；窗口内未触 → HOLD(0)。
- barrier **对称**（双方 `tp_pips`）——标签是方向目标而非单笔 P&L；非对称会在漂移随机游走中注入 labeler 先验。
- 同一根 bar 内上下 barrier 都触 → HOLD（intrabar 顺序 OHLC 不可知，默认 BUY 会制造牛市偏差）。
- 末 `forward_bars` 行无法打标 → NaN。

### 6.2 已知局限（诚实标注）

- **结构性泄漏**：bar i 的标签引用 i+1..i+10 的未来价格，这些未来 bar 同时是后续训练样本，TimeSeriesSplit 无法消除 → **AUC 可能系统性虚高**（config 注释标注「待 purge-gap」）。
- 样本量硬伤：真实 `trades` 仅 ~11 个去重 ticket（日志实测），Kelly 需 ≥20 笔；真实交易微调 = 过拟合到噪声。**合成样本是唯一可行路径**。
- 价格特征：09-14 那 8 笔成交 @2050 而当时金价 ≈4290（别名归一化 bug）——真实成交价格特征错误，必须剔除。
- 「能否交易」子问题缺负样本：被拒单（TRADE_BLOCKED）不落 trades 表，没有「不该交易」的标签。

---

## 7. 配置与部署

### 7.1 配置项（backend/app/config.py）

| 配置 | 默认 | 说明 |
|---|---|---|
| `laya_enabled` | `False` | 总开关；默认关，缺依赖/权重时系统照常运行 |
| `laya_model` | `convaiinnovations/laya` | english 421M；多语言用 `convaiinnovations/laya-multilingual` |
| `laya_confidence_threshold` | `0.85` | 情绪预筛阈值（max-class 概率 ≥ 此值才采用） |
| `laya_strategy_confidence_threshold` | `0.6` | 策略名抽取阈值（低于回退 keyword） |
| `laya_model_cache_dir` | `""` | 权重缓存目录（默认 ~/.cache/huggingface） |
| `laya_hf_endpoint` | `""` | HF 下载端点（空 = 用 HF_ENDPOINT 或官方） |
| `trade_gate_shadow` | `True` | 门控影子验证（记录不否决） |
| `trade_gate_enforce` | `False` | 门控强制执行（否决不通过） |
| `trade_gate_model_path` | `models/trade_gate.pkl` | joblib 模型路径 |

### 7.2 部署（backend/Dockerfile）

- 先装 **CPU-only torch**（`--index-url .../whl/cpu`），防止 `pip install laya` 拉 CUDA 版 torch（~2GB+）；安装失败不阻断构建（运行时 try-import 降级）。
- 构建时 `--build-arg PRELOAD_LAYA=1` → `snapshot_download('convaiinnovations/laya', local_dir='/app/models/laya/laya')` 预缓存权重打进镜像，运行时走本地路径，绕开下载与 tokenizer 写盘需求。
- 目录布局必须与 runtime 查找逻辑对齐：`os.path.join(cache_dir, basename("convaiinnovations/laya")) = /app/models/laya/laya`（C5 修复）。
- `ENV LAYA_MODEL_CACHE_DIR=/app/models/laya`（默认）。
- 依赖 pin：`laya==0.3.4` + torch/transformers 版本固定，避免 laya 宽依赖锥（hub>=0.20 / transformers>=4.45 无上界）解析到不可复现版本。

### 7.3 环境变量（backend/.env.example）

```bash
LAYA_ENABLED=false
# LAYA_MODEL_CACHE_DIR=/app/models/laya
# 国内网络必须设镜像，否则 huggingface.co 模型端点被阻断
# HF_ENDPOINT=https://hf-mirror.com
# LAYA_CONFIDENCE_THRESHOLD=0.85
# LAYA_STRATEGY_CONFIDENCE_THRESHOLD=0.6
# TRADE_GATE_SHADOW=true
# TRADE_GATE_ENFORCE=false
# TRADE_GATE_MODEL_PATH=models/trade_gate.pkl
```

---

## 8. 可靠性设计汇总

| 风险 | 对策 |
|---|---|
| 依赖缺失/权重下载失败 | try-import + `available=False` → 调用方回退 LLM，系统照常运行 |
| 冷加载阻塞事件循环 | 懒加载 + `asyncio.to_thread` |
| OOM 回落数据竞争 | 显式 `device="cpu"` 预载 + `threading.Lock` |
| 畸形 label 外溢 | 白名单（SENTIMENT_LABELS / STRATEGY_LABELS）→ 回落 LLM/keyword |
| choice ≠ argmax 错位 | `predict_choice` 校验 → 低置信降级（M2） |
| laya 低置信覆盖 keyword 兜底 | 策略阈值 0.6（I3） |
| 门控数据 schema 问题阻断交易 | 弃权第三态 `(None, 0.0)` + fail-open |
| 门控模型损坏 | `joblib.load` 失败禁用 gate（不崩溃） |
| laya 行 confidence 刻度不同 | risk/manager 对 `engine=="laya"` 行跳过 AI 过滤（概率模型不作风控拦截依据） |
| 预筛命中无审计 | 写 DB 审计行（engine=laya + probabilities） |
| 国内 HF 阻断 | `HF_ENDPOINT` / `laya_hf_endpoint` 镜像 |

---

## 9. 测试与验证

### 9.1 单元测试（backend/tests/unit/test_laya_runtime.py，19 passed + 1 skipped）

- 默认关闭回归（`Settings.model_fields["laya_enabled"].default is False`，不实例化避免 .env 干扰）
- 启用 + 高置信预筛命中 → 跳过 LLM
- 启用 + 低置信 → 回退 LLM
- 启用 + laya 不可用 → 回退 LLM
- `predict_choice` 解析：confidence=max_prob（非熵）、空 probabilities、缺 answers key、**choice≠argmax 降级**、正常返回
- 情绪白名单：invalid label → None（回退）
- 策略阈值：低于/等于阈值、默认阈值、显式覆盖
- 真实 API（skip 标记）：需真实模型 + 权重缓存（隔离 venv）

> 实测（2026-09-22）：`pytest tests/unit/test_laya_runtime.py -q` → **19 passed, 1 skipped**；4 个核心测试文件 59 passed, 1 skipped（上一轮审查记录）。

### 9.2 真实模型验证（backend/scripts/_laya_api_verify.py）

- 隔离 venv（有 laya + 权重缓存），绕过项目 conftest
- 验证：`laya_sentiment_choice` 真实返回、label 在白名单、probabilities keys 完整、confidence == max(probabilities)

### 9.3 决策门脚本（backend/scripts/laya_synth_baseline.py）

```bash
cd backend
.venv/bin/python scripts/laya_synth_baseline.py --db --symbol GOLD --timeframe M15          # 评估基线
.venv/bin/python scripts/laya_synth_baseline.py --db --symbol GOLD --timeframe M15 --save  # AUC≥0.6 时保存门控模型
.venv/bin/python scripts/laya_synth_baseline.py --csv x.csv                                 # 本地 CSV
```

---

## 10. 结论与后续路径

### 10.1 结论

1. **laya 适合做「高频前置结构化判定」**：情绪三分类、策略名分类已接入，毫秒级、零 token，高置信才采用，低置信自动回退——风险可控。
2. **不能全量替换 LLM**：D 类调用点占 86%（连续数值、工具调用、长文、审计关键路径）。
3. **数据驱动交易决策有基础**：GOLD M15 历史存在可预测的「该不该交易」信号（LightGBM AUC 0.739），但必须先解决标签泄漏（purge-gap）再谈微调。
4. **Trade Gate 处于影子验证期**：只记录不否决；数据积累后再开 `trade_gate_enforce`。

### 10.2 建议的后续工作（按优先级）

1. **固化 LightGBM 基线为落地门控**（已完成），持续影子验证收集「拒绝/放行」对照样本。
2. **purge-gap 修复**：消除标签窗口与训练样本重叠导致的 AUC 虚高，重新评估真实判别力。
3. **3.9 轨道（可选）**：Kaggle 免费 T4（16GB×2，12h/次）RLCD 微调 laya（合成样本 JSONL 导出）——瓶颈 100% 在数据；若 purge-gap 后 AUC 仍显著高于基率再投入，否则冻结该方向。
4. **温度校准**：若把 laya 概率用于更严格的门控，需按 (qtype, 选项数桶) 拟合温度（english ECE 0.466 → 0.081）。
5. **多语言评估**：交易消息若含中文/泰语，切换 `laya-multilingual` 并复测（english checkpoint 对非英文高置信度犯错）。

---

## 附录 A：关键文件索引

| 文件 | 作用 |
|---|---|
| `backend/app/ai/laya_runtime.py` | Laya 推理运行时单例 + 情绪/策略 choice 封装 |
| `backend/app/ai/news_sentiment.py` | 新闻情绪分析（laya 预筛 → LLM 回退） |
| `backend/mcp_server/agent_config.py` | 策略名抽取（laya choice → keyword 回退） |
| `backend/app/ml/trade_gate.py` | Trade Gate「可否交易」门控（LightGBM 运行时） |
| `backend/scripts/laya_synth_baseline.py` | 合成样本 + LightGBM 基线 + 门控模型训练 |
| `backend/scripts/_laya_api_verify.py` | 真实 laya API 兼容验证 |
| `backend/app/ml/features.py` | 特征构建（build_features）与三重障碍标签（build_labels） |
| `backend/tests/unit/test_laya_runtime.py` | laya 集成测试（19 passed, 1 skipped） |
| `backend/app/config.py` | laya / trade gate 配置项 |
| `backend/Dockerfile` | CPU-only torch + 权重预缓存部署 |
| `.planning/2026-09-21-laya-research/findings.md` | 完整调研证据（能力边界、LLM 盘点、实测） |

## 附录 B：风险清单

| 风险 | 等级 | 说明 |
|---|---|---|
| 标签窗口泄漏（AUC 虚高） | 中 | 已记录，config 注释标注「待 purge-gap」 |
| 真实 trades 样本量不足（~11 笔） | 中 | 微调前必须用合成样本 |
| english checkpoint 非英文高置信犯错 | 中 | 交易新闻为英文，必要时切 multilingual |
| laya 概率默认过自信 | 低 | 已用 max-class 概率重定义 confidence + 阈值门控 |
| `trend_following` 不在 STRATEGIES 注册表 | 低 | 仅展示用，strategy_switch 接通前无害 |
| 镜像体积增大（CPU torch + 权重） | 低 | 安装失败不阻断构建，运行时降级 |

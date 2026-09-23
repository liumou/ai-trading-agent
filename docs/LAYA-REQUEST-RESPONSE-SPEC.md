# Laya 模型请求参数 / 期望结果 / 返回结果结构化说明

> 日期：2026-09-22
> 分支：`feat/laya-research-and-integration`
> 范围：laya 决策引擎在本项目中的**接口契约**——请求怎么构造、期望什么、返回什么结构。纯文档，不修改任何代码。
> 对应源码：`backend/app/ai/laya_runtime.py`、`backend/app/ai/laya_gate.py`、`backend/app/ai/laya_engine_observation.py`、`backend/app/ai/laya_gate_report.py`、`backend/app/ai/laya_engine_report.py`

---

## 0. 阅读导览

本系统对 laya 的使用分 **4 层**，每层有独立的请求/响应结构：

| 层 | 模块 | 作用 |
|---|---|---|
| ① Laya 原生 API | `laya` 库 `agent.system_one()` | 一次前向推理，输入 `state` + `questions`，输出 `answers` |
| ② 运行时包装层 | `LayaRuntime` | 懒加载单例 + 线程安全 + 白名单校验 + 置信度重定义，统一返回**解析后**结构 |
| ③ 业务层 | `laya_gate.py` / `laya_runtime.py` 顶层函数 | 构造特定业务问题的 state/questions，产出业务语义结果 |
| ④ 集成层 | `news_sentiment.py` / `manual_order_gate.py` / `engine.py` | 消费业务层结果，落库 / 记录 / 影子观测 |

各层返回结构层层收窄：原生 `answers`（含 `type`/`choice`/`probabilities`/`confidence`/`action`）→ 解析后 `{label, confidence, entropy_confidence, probabilities}` → 业务判定 `{decision, confidence, reasons, checks, answers, engine}`。

---

## 1. Laya 原生 API 契约（层 ①）

### 1.1 调用形式

```python
agent.system_one(state, questions)
```

- `state`：任意上下文，JSON dict / 文本 / 对话列表均可。本项目统一传 **dict**。
- `questions`：`{question_key: question}` 的 dict，**一次前向并行判定全部问题**（拼接进单序列）。
- 单次前向语义：所有问题拼进一个序列（`[CLS] 类型+指令 [SEP] [MASK] 选项… [SEP] state [SEP]`），一次过编码器，全部问题并行判定。
- 速度实测：T4 GPU ~33ms/问，batch 10 问 ~7.2ms/问；CPU ~200–500ms（本项目强制 `device="cpu"`）。

### 1.2 请求结构

#### `questions` 中单个 question（`choice` 原语）

```jsonc
{
  "type": "choice",
  "instructions": "What is the market sentiment of these headlines?",
  "criteria": {
    "bullish": "positive outlook, price expected to rise",
    "bearish": "negative outlook, price expected to fall",
    "neutral": "mixed or no clear direction"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | `str` | 是 | 原语类型，本项目只用 `"choice"`（另有 `score`/`noul`，未落地） |
| `instructions` | `str` | 是 | 给模型的任务指令（自然语言） |
| `criteria` | `dict[str, str]` | 是 | **候选标签 → 语义定义**。key 是候选标签，value 是对该标签的定义。候选集 ≤ 7 个（>20 个选项精度骤降，见调研） |

#### `state` 请求（业务层构造）

`state` 是模型做判定的上下文。本项目有三类 state 形态（详见 §3）：

- **情绪预筛**：`{"symbol": str, "headlines": str}`
- **策略抽取**：`{"decision": str}`
- **6 问交易判定**：`{"order": {...}, "account": {...}, "positions": [...], "recent_trades": [...], "rule_flags": [...], "market": {...}}`

### 1.3 返回结构（原生，真实实测示例）

laya 0.3.4 真实返回（本机 probe 实测，来自 `test_laya_runtime.py` 注释）：

```jsonc
{
  "model": "laya-rl-agent",
  "answers": {
    "sentiment": {
      "type": "choice",
      "choice": "bearish",
      "probabilities": {"bullish": 0.0927, "bearish": 0.8749, "neutral": 0.0323},
      "confidence": 0.5919,
      "action": {"act_probability": 1.0}
    }
  },
  "usage": {"input_tokens": 84, "output_tokens": 0}
}
```

| 顶层字段 | 类型 | 说明 |
|---|---|---|
| `model` | `str` | 模型标识 |
| `answers` | `dict[question_key, ans]` | **与请求 questions 的 key 一一对应** |
| `usage` | `dict` | token 用量（`input_tokens`/`output_tokens`） |

单个 `ans`（choice 原语）字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | `str` | 回显请求原语类型 `"choice"` |
| `choice` | `str` | 模型选定的**候选标签**（应等于请求 criteria 的某个 key） |
| `probabilities` | `dict[str, float]` | **每个候选标签**的概率，keys 应完整覆盖候选集，sum≈1 |
| `confidence` | `float` | **laya 原生归一化熵置信度**（`1 - H(p)/log(k)`）。⚠️ 不反映"对所选类别的把握"——见 §1.4 |
| `action` | `dict` | 元信息（`act_probability`）。本项目不消费 |

### 1.4 ⚠️ confidence 语义（关键陷阱）

- laya 原生 `ans["confidence"]` 是**归一化熵置信度**（实测某类概率已 0.87 时置信度仅 0.59），**不反映**"模型对所选类别的把握"。
- 本项目把 `confidence` **重定义为所选类别的最大类概率**（`max(probabilities.values())`），用于阈值判断；原熵置信度保留为 `entropy_confidence` 参考字段（层 ② 的 `_parse_choice` 负责此重定义）。
- 因此：**任何"置信度阈值"比较（如 `laya_confidence_threshold` / `laya_gate_confidence_threshold` / `laya_strategy_confidence_threshold`）都作用在 max-class 概率刻度上**，不是熵刻度。

---

## 2. 运行时包装层契约（层 ②，`LayaRuntime`）

### 2.1 可用性

- `available` 属性：`laya_enabled=False` 或加载失败后 → `False`。首次探测**不 import**（`import laya` 是重型栈），假定可用由首次 predict/warmup 实证。
- `predict()` 超时（`asyncio.wait_for`）触发 **fail-stop**：置 `_available=False` 并抛 `asyncio.TimeoutError`，防止推理线程/队列无限堆积。重启进程后由 warmup 重新加载。
- `_predict_sync()` 用 `threading.Lock` 串行化推理（`Agent.device/dtype` 是可变共享状态，防数据竞争）。

### 2.2 三个异步入口

| 方法 | 签名 | 返回 | 说明 |
|---|---|---|---|
| `predict` | `(state, questions, *, timeout=None)` | `dict` | 底层包装，返回原生 `{"answers": {...}}`。不可用抛 `RuntimeError` |
| `predict_choice` | `(state, question_key, question, *, allowed_labels=None, timeout=None)` | `Optional[dict]` | **单问 choice** + 安全解析。**业务代码唯一应调用的异步入口之一**。失败/畸形返回 `None` |
| `predict_choices` | `(state, questions, *, allowed_labels=None, timeout=None)` | `dict[question_key, Optional[dict]]` | **单次前向批处理多问**。单问畸形/失败只置该 key 为 `None`，不影响其他问 |

- `allowed_labels`：可选白名单（`set[str]`）。传了则 label 必须命中白名单且 `probabilities` keys 必须与白名单**完全相等**，否则判定畸形。
- `warmup(timeout=None)`：启动预热，冷加载 ~136s。返回是否成功；失败仅记日志（不崩溃），运行期首次 predict 再试。

### 2.3 `_parse_choice` 解析与校验链（JEV 级）

对单个原生 ans，按顺序校验，**任一步失败返回 `None`**（调用方降级）：

1. `choice` 字段存在（缺失 → None）
2. label ∈ `allowed_labels`（白名单校验）
3. `probabilities` 是**非空 dict**
4. `probabilities` 全部值可转 `float`
5. `probabilities` keys 集合 == `allowed_labels` 集合（传白名单时）
6. 所有概率 **finite 且 ∈ [0,1]**
7. `sum(probabilities) ≈ 1.0`（容差 1e-2）
8. `choice` 必须是 **argmax**（`choice_prob >= max_prob - 1e-9`，M2 防御概率错位）
9. 通过后返回解析结果（见下）

解析后返回结构：

```jsonc
{
  "label": "bearish",
  "confidence": 0.8749,             // max-class 概率（重定义）
  "entropy_confidence": 0.5919,     // laya 原生熵置信度（仅参考）
  "probabilities": {"bullish": 0.0927, "bearish": 0.8749, "neutral": 0.0323}
}
```

| 字段 | 类型 | 语义 |
|---|---|---|
| `label` | `str` | 选定的候选标签（已过白名单 + argmax 校验） |
| `confidence` | `float` | **最大类概率**（阈值判断语义） |
| `entropy_confidence` | `float` | laya 原生熵置信度，保留参考，非判定依据 |
| `probabilities` | `dict[str, float]` | 全部候选概率（已过完整性/finite/sum 校验） |

`predict_choices` 返回：`{question_key: parsed_dict_or_None}`。调用方（收敛器）对畸形问整体 ESCALATE。

---

## 3. 业务层请求构造（层 ③）

### 3.1 情绪三分类预筛：`laya_sentiment_choice(headlines, symbol)`

**请求 `state`**：

```jsonc
{
  "symbol": "GOLD",
  "headlines": "<headlines[:3000]>"   // 已清洗拼接的单段文本，截断防超长
}
```

**请求 question（key = `"sentiment"`）**：

```jsonc
{
  "type": "choice",
  "instructions": "What is the market sentiment of these headlines?",
  "criteria": {
    "bullish": "positive outlook, price expected to rise",
    "bearish": "negative outlook, price expected to fall",
    "neutral": "mixed or no clear direction"
  }
}
```

**白名单**：`SENTIMENT_LABELS = {"bullish", "bearish", "neutral"}`（候选外标签 → 回落 LLM）。

**期望结果**：返回 `None`（不可用/失败/畸形/候选外 label）或解析后的 `{label, confidence, entropy_confidence, probabilities}`，其中 `label ∈ SENTIMENT_LABELS`。

### 3.2 策略名抽取：`laya_strategy_choice(decision, confidence_threshold=None)`

**请求 `state`**：

```jsonc
{ "decision": "<AI 决策文本[:3000]>" }
```

**请求 question（key = `"strategy"`）**：候选 6 类，criteria 见源码 `laya_runtime.py:356-366`：

| 候选 label | criteria 语义 |
|---|---|
| `trend_following` | 顺势：EMA 交叉、趋势延续 |
| `mean_reversion` | 均值回归：低买高卖 |
| `breakout` | 突破区间/价位入场 |
| `momentum_rank` | 动量/RSI/相对强弱入场 |
| `hold` | 明确不交易、持仓观望 |
| `ai_autonomous` | 以上皆非，自适应决策 |

**白名单**：`STRATEGY_LABELS = {"trend_following", "mean_reversion", "breakout", "momentum_rank", "hold", "ai_autonomous"}`。

**期望结果**：返回 `None` 当——不可用 / 调用失败 / 返回畸形 / **低置信**（`confidence < laya_strategy_confidence_threshold`，默认 0.6）/ label 不在白名单。否则返回解析结构。

**I3 注意**：`trend_following` 是 keyword 兜底链路的既有契约名（保留）；`momentum_rank` 而非 `momentum`（避免 `Unknown strategy` 报错）。两处白名单（`STRATEGY_LABELS` / `_STRATEGY_KEYWORDS`）必须一致。

### 3.3 6 问交易判定：`laya_gate_review(snapshot, *, timeout=None)`

veto-only 收紧层。一次前向跑 **6 个 choice 问**（`LAYA_GATE_QUESTIONS`），再经收敛器产出 verdict。

#### 请求 state 的来源

`build_laya_state(snapshot)`：从 ManualOrderGate 快照**子集**组装（OHLCV/regime 特征留待 Phase 4）：

```jsonc
{
  "order":         snapshot.order,          // 原样
  "account":       snapshot.account,        // 原样
  "positions":     snapshot.positions[:5],  // 截取前 5
  "recent_trades": snapshot.recent_trades[:5],
  "rule_flags":    snapshot.rule_flags,     // 原样
  "market":        snapshot.market          // 原样
}
```

当 `laya_state_prose=True`（默认）时，改用 `render_laya_state_prose(snapshot)` 渲染为**自然语言证据短句**（2026-09-22 真实数据实测：JSON 数字形态判 sufficient 仅 3%，prose 形态 61%；ECE(entry,2h) 0.131→0.068）。总量控制在 512 token 内（位置/近期成交各最多 4 条，字段短句化）。渲染字段见源码 `laya_gate.py:248-324`。

#### 6 问定义与候选（`LAYA_GATE_QUESTIONS` + `LAYA_GATE_OPTIONS`）

| question_key | 白名单候选 | instructions 主旨 |
|---|---|---|
| `data_quality` | `sufficient` / `partial` / `insufficient` | 点状证据是否足够做手动单风控决策（**纯审计问**，不参与收敛判定） |
| `signal_alignment` | `aligned` / `mixed` / `conflict` / `insufficient` | 订单方向 vs 行情快照/近期成交；缺证据视为 insufficient 而非 conflict |
| `market_regime` | `favorable` / `neutral` / `adverse` / `insufficient` | 当前市场条件是否适合入场 |
| `risk_check` | `clear` / `caution` / `block` / `insufficient` | 仓位/敞口/日盈亏/近期亏损/止损与保护 |
| `execution_quality` | `clear` / `caution` / `block` / `insufficient` | 价格新鲜度/点差/订单类型/执行条件 |
| `entry_decision` | `pass` / `reject` | 最终下单前决定；仅具体矛盾/实质风险/不安全执行才 reject |

**强制收敛问**（`CONVERGENCE_QUESTIONS`）：`signal_alignment`, `market_regime`, `risk_check`, `execution_quality`, `entry_decision`（`data_quality` 仅审计，不参与收敛，但其 `insufficient` 会附加 reason）。

#### 期望结果（收敛判定）

`converge_laya_verdict(answers, min_confidence=...)` 是**纯函数**（无 I/O、无状态），返回 `LayaGateDecision`：

```python
@dataclass(frozen=True)
class LayaGateDecision:
    verdict: str        # APPROVED | CAUTION | REJECTED | ESCALATE
    confidence: Optional[float]  # 聚合置信度 = min(强制问 max-class 概率)；ESCALATE 时为 None
    reasons: list[str]
    checks: dict[str, dict]      # {q: {"label": str, "confidence": float}}
```

收敛规则优先级（自上而下第一条命中即返回）：

| # | 条件 | verdict |
|---|---|---|
| 1 | 结构畸形（缺问 / 非法 label / 非法 confidence） | `ESCALATE`（fail-closed 交 LLM） |
| 2 | 任一强制问 label == `insufficient` | `ESCALATE`（证据不足不做判定） |
| 3 | 任一强制问 `confidence < min_confidence` | `ESCALATE`（低置信） |
| 4 | `risk_check`=block 或 `execution_quality`=block 或 `entry_decision`=reject | `REJECTED`（确定性 block） |
| 5 | `signal_alignment`=conflict ∧ `market_regime`=adverse | `REJECTED`（方向合取） |
| 6 | 全 pass 且 `entry=pass` ∧ `risk=clear` ∧ `execution=clear` ∧ `signal=aligned` ∧ `regime=favorable` | `APPROVED` |
| 7 | 其余（mixed/neutral/caution 组合） | `CAUTION` |

语义约束（veto-only）：APPROVED **不代表跳过 LLM 深析**（调用方负责）；REJECTED 须 LLM/确定性规则佐证后终局。`min_confidence` 实参 = `settings.laya_gate_confidence_threshold`（默认 0.6，max-class 概率刻度）。

---

## 4. 集成层返回结构（层 ④）

### 4.1 `laya_gate_review` 返回值（ManualGate 影子 & engine 影子共用）

```jsonc
{
  "decision": "CAUTION",                       // APPROVED | CAUTION | REJECTED | ESCALATE
  "confidence": 0.62,                          // min(强制问 max-class 概率)；ESCALATE 为 null
  "reasons": ["mixed or cautionary signals"],  // 收敛原因列表
  "checks": {
    "signal_alignment":  {"label": "mixed",    "confidence": 0.71},
    "market_regime":     {"label": "neutral",  "confidence": 0.65},
    "risk_check":        {"label": "clear",    "confidence": 0.82},
    "execution_quality": {"label": "clear",    "confidence": 0.80},
    "entry_decision":    {"label": "pass",     "confidence": 0.62}
  },
  "answers": {
    "data_quality":       {"label": "sufficient", "confidence": 0.90, "entropy_confidence": 0.55, "probabilities": {"sufficient": 0.90, "partial": 0.07, "insufficient": 0.03}},
    "signal_alignment":   { /* 同上解析结构 */ },
    // ... 6 问各自：正常为解析结构，畸形/失败为 null
  },
  "engine": "laya",
  "emotional_indicators": "not_assessed_by_laya"
}
```

- 返回 `None`：laya 不可用（调用方跳过影子）。
- 畸形/低置信**收敛为 ESCALATE**（不抛错），由调用方记录。

### 4.2 故障留痕结构（UNAVAILABLE）

Laya 故障/超时时，集成层不回传分析，而是返回**明确的故障标记**（基础设施故障不伪装成分析结论）：

```jsonc
{ "engine": "laya", "decision": "UNAVAILABLE", "error": "<错误/超时原因>" }
```

engine 影子侧额外带 `"latency_ms"` 与 `"error": "timeout"` 等。

### 4.3 情绪预筛消费（`news_sentiment.py`）

`laya_sentiment_choice` 命中且 `confidence >= laya_confidence_threshold`（默认 0.85）时，`SentimentResult`：

```python
SentimentResult(
    label=prefilter["label"],     # bullish|bearish|neutral
    score={"bullish": 0.5, "bearish": -0.5, "neutral": 0.0}[label],  # label 近似映射
    confidence=prefilter["confidence"],   # max-class 概率
    key_factors=["laya prefilter (confidence>=threshold)"],
    source_count=len(news_items),
    analyzed_at=now,
    engine="laya",                # 与 LLM 行区分的来源标记
)
```

- DB 审计行 `raw_response` 含 `{"engine": "laya", "probabilities", "confidence"}`。
- Redis 缓存键 `sentiment:latest:{symbol}`，TTL 900s。
- **I1 修复**：预筛命中也写 DB 审计行，避免 ML 情绪特征静默饿死。
- **C4 修复**：`engine == "laya"` 的行**不参与**引擎 ConfirmationGate 投票、不计作独立 AI 数据源、不参与风险闸拦截（confidence 刻度不同，混用会静默改变通过率）。详见 `engine.py:500-550`、`risk/manager.py:326-332`。

### 4.4 策略抽取消费（未直接入库，用于展示）

`laya_strategy_choice` 的 label 用于 `strategy_used` **展示**（`scheduler.py`），不 resolve 到实现注册表。替代 `agent_config.py` 的 keyword 子串匹配（顺序敏感、中文/否定误判）。

### 4.5 ManualGate 影子（`manual_order_gate.py`）

- 与 LLM 并行跑，`asyncio.wait_for(laya_task, timeout=LAYA_SHADOW_AWAIT_BUDGET_S=3.0)`。**超时即放行订单**，影子数据由后台任务补写（绝不让 laya 故障拖慢真钱订单）。
- 落 `ManualShadowReview` 专表字段：`laya_verdict`, `laya_confidence`, `laya_reasons`, `laya_checks`, `laya_answers`, `laya_latency_ms`, `laya_error`, `agreement`（laya 三值 verdict == LLM 三值 verdict；ESCALATE/UNAVAILABLE → None），`dangerous_divergence`（laya=APPROVED ∧ LLM=REJECTED，唯一致命方向）。
- 快照来源 `_build_snapshot`（`manual_order_gate.py:669-695`）：

```jsonc
{
  "order":         {"review_id", "symbol", "type", "lot", "sl", "tp"},
  "account":       {"balance", "equity", "floating_profit", "realized_daily_pnl"},
  "positions":     [{"symbol", "type", "lot", "profit"}],
  "recent_trades": deals[:10],
  "rule_flags":    [{flag, severity, detail}],
  "market":        {"bid", "ask", "spread", "avg_spread", "sentiment"}
}
```

### 4.6 Engine 开仓影子观测（`laya_engine_observation.py`）

- 只观测不改行为，零副作用。观测器启动条件：`laya_gate_engine_shadow ∧ laya_enabled`。
- 观测对象是「laya vs TradeGate+确定性链」的**分歧率**（不是一致率，engine 路径无 LLM 参照）。
- `build_laya_engine_snapshot` 组装 state（与 ManualGate 快照同构，但 market 用确定性预计算摘要）：

```jsonc
{
  "order": {"signal", "signal_label", "symbol", "timeframe", "side"},   // side 由 signal 符号推导 BUY/SELL/FLAT
  "account": {"balance", "positions_count", "daily_pnl", "recent_win_rate?"},
  "positions": [{"symbol", "type", "volume", "profit"}],  // 压缩，最多前 5
  "recent_trades": [],     // engine 路径观测不触发额外 DB 查询 → 空列表
  "rule_flags": [],
  "market": {"last_close", "change_1_pct", "change_5_pct", "vol_14", "range_position_50", "price_vs_sma9", "price_vs_sma21", "symbol", "timeframe"}
}
```

- 分歧分类 `classify_divergence(chain_can_trade, laya_verdict, final_allowed)` 返回 `{"gate": ..., "final": ...}`，类别：

| 类别 | 语义 |
|---|---|
| `none` | 现有链路与 laya 方向一致 |
| `tighten` | **收紧分歧（重点）**：链路放行，laya 判 REJECTED/ESCALATE |
| `loosen` | **放松分歧**：链路不放行，laya 判 APPROVED |
| `chain_abstain` | TradeGate 弃权（None） |
| `laya_unavailable` | laya UNAVAILABLE / 未知 verdict（M5） |
| `laya_escalate` | 链路不放行 + laya ESCALATE |
| `caution_on_allow` / `caution_on_deny` | 链路放行/不放行 + laya CAUTION |
| `laya_absent` | 观测未启用（laya None） |

- 落 `LayaEngineObservation` 专表：`symbol`, `timeframe`, `signal_label`, `signal`, `balance`, `chain_can_trade`, `chain_prob`, `allowed`, `laya_verdict`, `laya_confidence`, `laya_reasons`, `laya_checks`, `laya_answers`, `divergence_gate`, `divergence_final`, `laya_latency_ms`, `state_snapshot`。

### 4.7 影子报表聚合（`laya_gate_report.py` / `laya_engine_report.py`）

`build_report(pairs, decisions, laya_latencies, llm_latencies, answers, llm_verdicts_for_qa)` 返回：

| 字段 | 说明 |
|---|---|
| `n` | 计入行数（两者都在三值内） |
| `raw_agreement` | 原始三分类一致率 |
| `agreement_wilson_lower` | 单侧 Wilson 95% 下限 |
| `kappa` | Cohen's Kappa（3 类，去随机一致） |
| `confusion_matrix` | 3×3（APPROVED/CAUTION/REJECTED） |
| `dangerous_divergence_count` | 致命分歧（laya APPROVED & LLM REJECTED） |
| `false_kill_count` | 误杀（laya REJECTED & LLM APPROVED） |
| `fallback_rate` | ESCALATE/UNAVAILABLE / 总数 |
| `laya_latency_ms` / `llm_latency_ms` | `{p50, p95}`（线性插值） |
| `per_question` | 逐问 choice 分布 + 与 LLM verdict 映射一致率 |

`build_engine_report(rows)`（Phase 4，无 PASS/FAIL 门槛，只出描述统计）：`n`, `laya_available`, `laya_unavailable`, `chain_abstain`, `tighten_gate`/`loosen_gate`（gate 口径）, `tighten_final`/`loosen_final`（final 口径）, `none_gate`, `caution_gate`, `laya_absent`, `tighten_cases`/`loosen_cases`（各 ≤100 条供人工复核）, `laya_latency_ms`, `signal_labels`。

---

## 5. 请求构造速查表（三业务入口汇总）

| 入口 | state | question key(s) | 候选集（白名单） | 阈值 | 失败降级 |
|---|---|---|---|---|---|
| `laya_sentiment_choice` | `{symbol, headlines[:3000]}` | `sentiment` | `bullish/bearish/neutral` | `laya_confidence_threshold`=0.85（在调用方 news_sentiment 应用） | → LLM 深析 |
| `laya_strategy_choice` | `{decision[:3000]}` | `strategy` | 6 类策略（§3.2） | `laya_strategy_confidence_threshold`=0.6（内部应用） | → keyword 匹配 |
| `laya_gate_review` | 6 问快照（§3.3） | 6 问 | `LAYA_GATE_OPTIONS`（§3.3 表） | `laya_gate_confidence_threshold`=0.6（收敛器应用） | ESCALATE → LLM |

统一降级语义：**返回 `None` / `UNAVAILABLE` / `ESCALATE` 都是"证据不足或模型不可用"的信号，调用方回退 LLM 或确定性逻辑，绝不把未校验的 label 当判定依据**。

---

## 6. 相关配置项对照（`config.py:303-346`）

| 配置 | 默认 | 作用 |
|---|---|---|
| `laya_enabled` | `False` | 总开关。False 时系统完全不受 laya 影响（try-import 降级） |
| `laya_model` | `convaiinnovations/laya` | HF repo id 或本地缓存路径 |
| `laya_confidence_threshold` | `0.85` | 情绪预筛置信阈值（max-class 概率刻度） |
| `laya_strategy_confidence_threshold` | `0.6` | 策略抽取置信阈值 |
| `laya_model_cache_dir` | `""` | 模型权重缓存目录（生产建议构建时预缓存） |
| `laya_hf_endpoint` | `""` | HF 下载端点（空=官方或环境变量 `HF_ENDPOINT`；国内设 hf-mirror） |
| `laya_gate_shadow` | `False` | ManualGate 影子：laya 与 LLM 并行跑、只记录不拦截 |
| `laya_gate_enforce` | `False` | enforce 阶段 laya 判定参与拦截（须一致率达标后手动开启） |
| `laya_gate_confidence_threshold` | `0.6` | 收敛器 min_confidence（max-class 概率刻度） |
| `laya_gate_rollout_pct` | `0` | 灰度放行比例（0-100），enforce 开启后按比例抽样生效 |
| `laya_gate_predict_timeout_s` | `30.0` | 6 问推理超时（冷加载在 warmup 处理，这里防 predict 卡死） |
| `laya_state_prose` | `True` | state 渲染形态：prose（自然语言短句）/ json |
| `laya_gate_warmup_timeout_s` | `600.0` | 启动 warmup 预算；超时只告警不 fail-stop |
| `laya_gate_engine_shadow` | `True` | engine 开仓侧观测开关（只观测不改行为，2026-09-22 批准打开） |

---

## 7. 校验与降级不变量（勿破坏）

1. **白名单是底线**：label 不在白名单 / probabilities keys 不完整 → 判定畸形 → 回落 LLM（I2：杜绝未校验 label 传给 score 映射引发 KeyError）。
2. **confidence 刻度统一**：一律 max-class 概率；`entropy_confidence` 仅保留参考，绝不参与阈值比较。
3. **fail-closed 优先**：畸形/低置信/证据不足 → ESCALATE（交 LLM）；laya 故障 → UNAVAILABLE 留痕，**基础设施故障不伪装成分析结论**。
4. **影子非干扰性（H-3）**：laya 故障/超时绝不影响 LLM 路径结果、绝阻塞真钱订单（ManualGate 3s 预算、engine 全异步零副作用）。
5. **Laya 行不混入 LLM 语义**：`engine="laya"` 行不参与 ConfirmationGate 投票 / 风险闸拦截 / AI 数据源计数（C4）。
6. **收敛器是纯函数**：`converge_laya_verdict` 无 I/O、无状态；新增/修改规则需同步更新单测（`test_laya_gate.py` 覆盖 7 级优先级）。

---

## 8. 调用链全景

```
【情绪预筛】news_sentiment.analyze
  → laya_sentiment_choice(headlines, symbol)
    → LayaRuntime.predict_choice(state={symbol,headlines}, "sentiment", question)
      → predict → _predict_sync → agent.system_one(state, {sentiment: question})
      ← answers.sentiment（原生 choice）
      ← _parse_choice → {label, confidence, entropy_confidence, probabilities}
  → confidence ≥ 0.85 ? 直接用 : 回落 LLM
  → SentimentResult(engine="laya") → DB + Redis(sentiment:latest:{symbol})

【策略抽取】scheduler/agent 决策文本
  → laya_strategy_choice(decision)
    → predict_choice(state={decision}, "strategy", question)
    → confidence ≥ 0.6 ? 用 : keyword 兜底 → strategy_used 展示

【ManualGate 影子】manual_order_gate._review
  → 并行 create_task(laya_gate_review(snapshot))
    → build_laya_state(snapshot) / render_laya_state_prose(snapshot)
    → LayaRuntime.predict_choices(state, LAYA_GATE_QUESTIONS, allowed_labels=LAYA_GATE_OPTIONS)
      → predict → system_one(state, 6 questions)
      ← answers（6 个原生 choice）→ 逐个 _parse_choice
    → converge_laya_verdict(answers, min_confidence)
      → LayaGateDecision → {decision, confidence, reasons, checks, answers, engine}
  → wait_for(3s) → 落 ManualShadowReview（agreement/dangerous_divergence）

【Engine 影子观测】engine._check_trade_permission wrapper
  → start_engine_observation(...) → EngineLayaObservation._run
    → build_laya_engine_snapshot(...)
    → laya_gate_review(snapshot, timeout=30s)
    → classify_divergence(chain_can_trade, laya_verdict, final_allowed)
  → 落 LayaEngineObservation（divergence_gate/final）

【报表】scripts / 页面
  → build_report(...)（ManualGate 影子，3×3 混淆矩阵 + 一致率 + Kappa + 危险分歧）
  → build_engine_report(rows)（engine 观测，收紧/放松分歧 + 案例清单）

---

## 9. 2026-09-22 追加：证据面对齐 JEV 与 512-token 预算（Phase 0/1 已落地）

> 对应审计与实验：`.planning/2026-09-22-laya-jev-param-audit/`（findings.md §6）。

### 9.1 请求 state 变更（向后兼容，全部可空）
- `render_laya_state_prose`（默认 prose 形态）新增 ManualGate 快照键兼容：
  `order.lot/sl/tp/type`、`account.equity/floating_profit/realized_daily_pnl/consecutive_losses`、
  `market.bid/ask/spread/avg_spread/sentiment`；`rule_flags` 支持 dict（取 detail/flag）。
- `build_market_summary(df, timeframe)` 新增：`ma5/ma10/ma20`、`rsi14`、`atr14`+`atr_pct`、
  `macd_state`+`macd_histogram`、`support`/`resistance`、`volume_ratio_20`（tick_volume/volume 兼容）、
  `latest_bar_time_utc`/`data_age_seconds`/`is_stale`（df index + timeframe 零 I/O 计算）。
- `build_laya_engine_snapshot` 新增：根级 `context_version: 2`；`recent_profits`
  → `account.recent_exit_pnl[:10]` + `account.consecutive_losses`（复用 engine 已预取的平仓盈亏，零额外 I/O）；
  持仓归一化 `entry_price`/`current_price`（兼容 MT5 `price_open`/`price_current`）。

### 9.2 512-token 预算护栏
- `_PROSE_BUDGET_CHARS = 900`（≈350-375 token；最坏 head room=413，留 ~40 token 余量）；
  超预算先丢 `Rule flags:`→`Recent trades:`→`Positions:` 行，再硬截断（保 order/account/market 核心证据）。
- 真实 laya tokenizer 逐问验证（`build_sequence` 512/192）：engine-max state=343 token（full=441）、
  manual-max state=364 token（full=462），全部不截断、选项完整。验证脚本 `/tmp/verify_laya_tokens.py`。

### 9.3 实验结论（n=240 真实回放，与 C1 同 idx 配对）
- 证据面增强未带来方向技能（entry_pass 各 horizon p≥0.10）；逐问判定被大幅打乱
  （market_regime 225/240 翻转、data_quality sufficient 147→0）→ 输入面改动解决不了未微调模型能力缺陷；
- 0.6 阈值下 C3/C4 仍 100% ESCALATE；C4（+H1）仅更保守（entry reject 219/240）；
- 结论：Phase 0/1 的价值是把证据正确、不截断地送进模型（为微调后复测提供基线）；
  解锁点仍是模型面（领域微调 / 托管 JEV / 放弃 6 问），见 `task_plan.md` Phase 3。
```

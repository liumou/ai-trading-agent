# Findings: Laya 集成代码审查（提交 a517dc0 / PR #1）

> 审查目标：对 `feat/laya-research-and-integration` 分支提交 a517dc0 做成体系代码审查。
> 本文件记录审查发现（原始证据），分级结论在审查完成后并入 task_plan / 最终报告。

## 一、审查范围确认

- 提交 a517dc0（相对 a517dc0~1），8 个文件：5 代码/测试 + 3 调研文档
- 审查重点：正确性、并发安全、降级路径、安全性、配置一致性、测试质量

## 二、调用链证据（已验证）

```
scheduler._sentiment_job (async)
  → engine.fetch_and_analyze_sentiment (async)
    → sentiment_analyzer.analyze(news, context, symbol) (async)
      → laya_sentiment_choice(_clean_headlines(news_items), symbol)  [news_sentiment.py:81]
        → rt._predict_sync(state, questions)  [laya_runtime.py:135]  ← 同步！
          → _load()  [laya_runtime.py:92]  ← 冷加载 ~136s
          → agent.system_one(...)  [推理 200-500ms]
```

## 三、主会话独立验证结论

### C1. 【Critical】同步阻塞事件循环（绕过异步 to_thread 封装）
- **位置**：`laya_runtime.py:135`
- **证据**：`laya_sentiment_choice()` 直接调用 `rt._predict_sync(state, questions)` 而非 `await rt.predict()`。类已提供异步 `predict()`（内部用 `asyncio.to_thread`），但调用方绕过它。
- **失败场景**：启用 `laya_enabled=True` 后，首次 `analyze()` 触发 `_load()`（冷加载 ~136s）在 async 事件循环线程内同步执行；后续每次预筛 200-500ms 也阻塞。期间所有其他请求（下单、行情、其他 symbol 情绪分析）全部卡住。
- **关键**：注释 `laya_runtime.py:134` 声称"to_thread 在 predict 内部"，但 `_predict_sync` 并没有 to_thread。这是注释与实际代码不一致。
- **补充**：`predict()` 异步方法（`laya_runtime.py:85`）**全库零调用方**（`grep .predict(` 无命中）。设计者写了正确的 to_thread 封装却没人用——`laya_sentiment_choice` 是唯一调用路径且绕过了它。修复方向明确：`laya_sentiment_choice` 改为 `await rt.predict(...)`。

### C2. 【Important】预筛命中不写 DB → ML 特征缺口
- **位置**：`news_sentiment.py:97-107`
- **证据**：预筛命中时只写 Redis（`sentiment:latest:{symbol}`），明确注释"不写 DB"（:97-98）。但 `ml/sentiment_features.py:26` 用 `select(NewsSentiment)` 从 DB 表聚合特征。
- **失败场景**：启用 laya 后，高置信预筛命中的新闻不落 `NewsSentiment` 表 → ML 特征 `sent_count`/`sent_momentum_3d` 缺失这些时段，特征分布偏移（`sent_score_mean` 只剩 LLM 低置信样本）。`sent_count` 也变少。
- **权衡**：注释说是"避免污染审计轨迹"。但 DB 表同时是 ML 特征源。需要决策：预筛是否也该写 DB（带来源标记）或至少保留一条聚合记录。

### C3. 【Important】测试覆盖缺口
- **位置**：`test_laya_runtime.py` 全部 4 用例
- **证据**：`laya_sentiment_choice` 本身在所有测试中都被 `patch("app.ai.laya_runtime.laya_sentiment_choice")` mock 掉。无任何测试直接调用真实的 `laya_sentiment_choice`/`_predict_sync` 路径。
- **失败场景**：C1 的同步阻塞 bug 恰好落在 `laya_sentiment_choice` 内部，但测试 mock 了它 → bug 漏网。真实 laya 调用路径（state 构造、questions 格式、返回解析）零覆盖。
- **量化证据**：`pytest --cov=app.ai.laya_runtime --cov=app.ai.news_sentiment` 实测：
  - `laya_runtime.py` **47%**（34/64 覆盖）。未覆盖：`:119-137`（laya_sentiment_choice 真实推理路径，全 mock）、`:54-83`（_load 加载/失败）、`:87-93`（predict/_predict_sync）、`:28`（import 降级）、`:47-50`（available 缓存）
  - `news_sentiment.py` **62%**（41/107 覆盖）。预筛分支（:81-107）已覆盖，但降级/失败路径（:105-106,134-135,173-174）与读取路径（:180-209）未覆盖
  - 项目标准：关键路径 ~89% 覆盖率 → laya_runtime 47% 明显偏低

### C4. 【Minor】available 缓存陷阱
- **位置**：`laya_runtime.py:42-50`
- **证据**：`self._available` 首次访问 `available` 时缓存为 True，之后不再重估。`_load()` 失败置 False 后永久禁用。
- **影响**：正常部署 `laya_enabled` 启动即定死，可接受。但若配置热重载/动态开关，会失效。

### C5. 【Minor】`_clean_headlines` 双重调用
- **位置**：`news_sentiment.py:81,111`
- **证据**：analyze() 为 prefilter 调一次 `_clean_headlines`，为 LLM prompt 再调一次。重复清洗。

### C6. 【Minor】score 近似映射偏差
- **位置**：`news_sentiment.py:88`
- **证据**：`{"bullish": 0.5, "bearish": -0.5, "neutral": 0.0}` 硬编码离散值，与 LLM 连续 score(-1~1) 分布不同。
- **影响**：下游 `sentiment_features.py` 的 `sent_score_mean` 因混合离散/连续值而分布偏移。默认关闭无影响。

### C7. 【Minor】laya 上下文截断可能超限
- **位置**：`laya_runtime.py:121` `headlines[:3000]`
- **证据**：3000 字符 ≈ 700-1000 token（英文），但 english checkpoint 上下文窗口 512 token。可能超限。
- **缓解**：异常被 `except` 吞掉 → 返回 None → 回退 LLM。功能安全，只是预筛命中率降低。

### C8. 【Minor】baseline 脚本 `fillna(0)` 制造虚假信号
- **位置**：`laya_synth_baseline.py:99` `X = fdf[cols].fillna(0)`
- **证据**：前 199 行滚动特征（ema_200 等）为 NaN，fillna(0) 人为制造"特征=0"样本，与真实价格特征悬殊。
- **建议**：改用 dropna（会损失前 199 行，约 0.14% 样本）。

### C9. 【Minor】`y3.unique()` 含 NaN 的集合比较
- **位置**：`laya_synth_baseline.py:107` `if set(y3.unique()) <= {-1, 0, 1}`
- **证据**：`y3` 最后 forward_bars 行为 NaN，`set` 含 NaN，`<= {-1,0,1}` 为 False → 打印分支被跳过。不崩但意图不清。

### C10. 【Minor】硬编码 HF 镜像为默认端点
- **位置**：`laya_runtime.py:63-64`
- **证据**：`os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"` **无条件写入**（仅当环境变量未设置时），即使 Railway 境外环境可直连 huggingface.co 也会强制走镜像。
- **影响**：镜像稳定性依赖第三方；若镜像临时不可用而官方可直连，模型下载反而失败 → 预筛降级。建议改为默认不设置（用官方），国内部署显式配 `HF_ENDPOINT`，或至少允许配置覆盖。

### C11. 【Important】预筛 label 无白名单校验 → KeyError 吞掉整个情绪分析（降级路径有洞）
- **位置**：`news_sentiment.py:88`
- **证据**：`score = {"bullish": 0.5, "bearish": -0.5, "neutral": 0.0}[prefilter["label"]]`。若 laya 返回 criteria 外的 label（模型输出畸形值如 "Bull"/"bull"/空串），此处 KeyError。
- **失败场景**：KeyError 不在 `laya_sentiment_choice` 的 try 块内（该 try 只包到 :141），`analyze()` 也无 try/except 包 prefilter 段 → 异常向上传播到 `engine.fetch_and_analyze_sentiment` 的 except（engine.py:1327）→ **整个情绪分析失败 log "Sentiment analysis error"**，而非降级回 LLM。
- **违背设计意图**：注释声称"低置信/不可用回退 Claude 深析"，但畸形 label 导致的是失败而非降级。预筛层的"失败"应当与"不可用"一样回退 LLM。

## 四、安全审查结论

- ✅ SQL 注入：`symbol` 参数化（`$1`），`limit` 为 argparse int，安全
- ✅ RSS 注入面：`_clean_headlines` 去除指令注入分隔符（`---`/` ``` `/`<|`/`|>`/`###`）并截断 200 字符，与既有 LLM prompt 同策略
- ✅ `laya_sentiment_choice` 异常吞没返回 None → 调用方降级，无未处理异常
- ⚠️ `.env` 解析（baseline 脚本）：遍历取第一个 DATABASE_URL 开头行，若 `.env` 同时有 `DATABASE_URL` 和 `DATABASE_URL_SYNC` 可能取到 SYNC 而误判；但 `DATABASE_URL_SYNC` 优先级更高，逻辑可接受

## 五、默认关闭零影响验证（通过）

模拟未安装 laya 环境（blocked import）+ 默认 `laya_enabled=False`：
- `available` → `False` ✅
- `laya_sentiment_choice` → `None`（调用方降级回 LLM）✅
- **不写 `HF_ENDPOINT` 环境变量** ✅（C10 镜像逻辑只在 `_load()` 启用后触发）
- **不 import laya 包** ✅
- 结论：默认关闭零影响承诺成立，不装依赖可正常启动。

## 六、真实 laya API 测试发现（Phase 4b，2026-09-21）

### 背景
用户要求真实测试 laya API（code-reviewer OQ：返回结构从未真实执行验证过）。在隔离 venv（/tmp/laya-api-venv，Python 3.10 + CPU torch 2.14 + laya 0.3.4）跑真实模型。

### 🔴 关键发现：confidence 语义错误（重要 Bug）
**真实返回结构**（probe 实测）：
```json
{
  "answers": {"sentiment": {
    "type": "choice",
    "choice": "bearish",
    "probabilities": {"bullish": 0.0927, "bearish": 0.8749, "neutral": 0.0323},
    "confidence": 0.5919,       // ← laya 原生：归一化熵置信度 1-H(p)/log(k)
    "action": {"act_probability": 1.0}
  }}
}
```
- **`confidence` 是熵置信度（0.59），不是最大类概率（0.87）**——`confidence_from_probs` = `1 - H(p)/log(k)`（源码 agent.py/common.py 确认）
- 我们预筛用 `prefilter["confidence"] >= 0.85` 判断 → **几乎永不命中**（即使某类 87% 把握，熵置信度才 0.59）
- **已修复**：`predict_choice` 把 `confidence` 重定义为所选类别的最大类概率，熵置信度保留为 `entropy_confidence`
- 真实验证：`max_prob=0.9068 > entropy=0.6688` → 修复后预筛能正确命中

### 其他真实数据
- 冷加载 CPU 46s（本机，非 136s）
- `system_one` 单状态推理 **1592ms**（CPU，84 input tokens）——比预想 200-500ms 慢，4 symbol 调度需注意
- 模型对看空标题（"Gold plunges/Fed hike/Safe-haven collapses"）正确判定 `bearish` 0.9068——语义符合预期

### 验证通过
- `_laya_api_verify.py`（独立脚本，不依赖 pytest/conftest）真实跑通：label 白名单、confidence=max_prob、probabilities keys 全部正确
- pytest `TestRealLayaAPI` 集成测试（importorskip）在无 laya 环境正确 skip
- pytest `TestPredictChoiceParsing` 用真实返回结构 mock 断言 confidence=0.8749（最大概率）——单元级验证

### 环境坑（记录供后续）
- pip 23 装 torch 的 build-isolation 找不到 flit_core → 升级 pip 26 + `--no-build-isolation`
- 系统 SOCKS 代理 → 需 `httpx[socks]`（socksio）
- 隔离 venv 3.10 无法 import 项目 db/models（`enum.StrEnum` 需 3.11+）→ 真实集成测试用独立脚本绕过

## 七、code-reviewer 子代理审查结果（ac11cba27fb43449c，已完成）

> 独立审查确认了主会话的 C1/I1/I2，并新增了重要发现（I3 列名错误、M10 标签泄漏等）。本会话验证：

### 已验证（主会话交叉确认）
- **I3 确认**：`OHLCVData` 模型列名是 `volume`（`models.py:110`），collector 插入 `volume`（`collector.py:93-94`），MT5 `tick_volume` 入库时已重命名（`collector.py:85`）。baseline 脚本 `laya_synth_baseline.py:66` 查询 `tick_volume` → **必然 `UndefinedColumnError`**，`--db` 模式无法运行。另确认缺 `timeframe` 过滤（`models.py:102` 有 timeframe 列）。
- C1（同步阻塞）— code-reviewer 高置信确认，与主会话一致
- I1（ML 特征饿死）— code-reviewer 确认，补充"预筛命中后直接 return，后续 LLM 永不补齐"细节
- I2（label KeyError）— code-reviewer MEDIUM 确认，补充 confidence TypeError 风险（`prefilter["confidence"] >= threshold`）

### code-reviewer 新增发现（主会话未识别）
| 编号 | 级别 | 问题 |
|------|------|------|
| M3 | Minor | `available` 属性恒真式（tautology），探测逻辑冗余 |
| M4 | Minor | `get_laya_runtime()` 无锁 → 并发可能构造多个 runtime（模型加载 ×2） |
| M5 | Minor | `HF_ENDPOINT` 无条件覆盖进程全局 env（不只是默认值问题） |
| M7 | Minor | 预筛丢失 context 上下文加权，与 LLM 路径语义不一致但共用缓存键 |
| M8 | Minor | 异常吞没把基础设施故障与模型失败混一类，`_load` 失败永久禁用不重试 |
| M10 | Minor | 标签窗口与训练样本重叠 → AUC 虚高（需 purge gap） |
| M11 | Minor | baseline 脚本 :135 截断 print 语句（未写完） |
| OQ | 低置信 | laya API 返回结构从未真实执行过（`result["answers"]["sentiment"]` 键名是调研推测）→ 上线前需 smoke test |

### 合并后最终清单（主会话 + code-reviewer 去重）
- **Critical (1)**: C1 同步阻塞事件循环（`laya_runtime.py:135`）
- **Important (4)**: I1 ML 特征饿死、I2 label/confidence 无校验、I3 `--db` 列名错误+timeframe 缺失
- **Minor (13)**: C4-C9 + C10/C11 + M3-M8 + M10-M11
- **正向观察**: 降级链正确、默认关闭零影响、`build_labels(sl_pips=None)` 正确用法、`default_transaction_read_only` 硬保证
- **code-reviewer 结论**: COMMENT 可合入（默认关闭），但 `LAYA_ENABLED=true` 前必须修 C1/I1/I2/I3

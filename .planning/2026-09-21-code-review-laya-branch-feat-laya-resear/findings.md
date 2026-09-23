# Findings: 审查 feat/laya-research-and-integration 分支

## 审查元信息
- **范围:** `33b1d13..e672e27`（10 提交，31 文件，+2634 / -209）
- **分支:** feat/laya-research-and-integration
- **日期:** 2026-09-21
- **方法:** 3 个并行审查代理（Laya 集成 / TradeGate+引擎 / 部署+死代码+前端+脚本）→ 交叉验证 → 分级汇总
- **对照基准:** `.planning/2026-09-21-laya-research/task_plan.md`（调研三轮结论）+ `.planning/2026-09-21-laya/task_plan.md`（早期单提交审查）

## 主题映射
| 主题 | 提交 | 代理 |
|------|------|------|
| Laya 运行时 + 情绪预筛 + 策略抽取 | a517dc0, 45d8094, 8006e0a, adaf850, e672e27 | A |
| TradeGate + 引擎接线 | 3efa23c, 0b6063d, 52424f3 | B |
| 部署 + 死代码 + 前端 + 脚本 | 57d6d70, ce92f05, e672e27 | C |

## 代理发现（待填充）

### 代理 A — Laya 集成（已返回）

**优点**
1. Confidence 语义文档化有价值（`laya_runtime.py:101-105` 区分 entropy confidence vs max-class probability；测试用真实 laya 0.3.4 输出 pin 住 0.8749/0.5919）
2. RSS 注入修复正确（`_clean_headlines()` 提取到模块级 `news_sentiment.py:22-35`，预筛与 LLM 路径共用同一清洗串）
3. 白名单防御分层（`laya_runtime.py:145` SENTIMENT_LABELS + `news_sentiment.py:90` 复检 + try 内标签校验）
4. 优雅降级真实（try-import、RuntimeError→None→LLM 回退、`_load` 失败永久 `_available=False` 不重试下载）
5. keyword 兜底修复潜在 bug（`_STRATEGY_KEYWORDS` 硬映射中英关键词→英文策略名，修了原 `replace(" ","_")` 返回中文的问题）
6. 测试隔离好（autouse fixture 重置单例；39 passed 1 skipped）

**Critical（必须修复）**
- **C1** `config.py:303-307`：`laya_enabled: bool = True`（注释写「默认 False」+ 计划要求 False + 测试声明 False）。Git 确认 a517dc0 引入时是 False，后被翻成 True。Dockerfile/requirements 都装 laya==0.3.4 → 正常部署下模型默认启用、无 opt-in；首个情绪任务（scheduler 每 15min 全 symbol）触发 `_load()`，非预载镜像（PRELOAD_LAYA=0 默认）在**请求期**从 HF **无超时**下载 ~1.7GB → 生产冷启动风险。**修复：revert 为 False + 显式部署步骤（LAYA_ENABLED=true + PRELOAD_LAYA=1 同时配）+ 断言测试**
- **C2** `news_sentiment.py:103`：`confidence=prefilter["confidence"]`（laya max-class prob ≥0.85）写进 LLM confidence 字段。下游两处阈值不匹配：`risk/manager.py:325-333` AI 过滤闸（laya 通过率远高于 Claude → **静默削弱风险闸**）；`engine.py:505/544-545` ConfirmationGate 投票；`ml/sentiment_features.py:47` 写三档 ±0.5/0.0 代理 vs LLM 连续 -1~1 → **分布偏移**。计划说「score/key_factors 仍由 Claude」但 score 被近似、confidence 被混同。**修复选项：(a) engine=="laya" 行在 `_get_ai_sentiment` 不作权威（仅 llm 生效过滤/确认）；(b) 重缩放进 LLM 约定；(c) 至少双记录 + 特征选择按 engine 门控**

**Important（应当修复）**
- **I1** 冷载无超时、无推理并发上限、占默认池 worker（计划要求 Semaphore 1~2，只有加载锁）；`predict()` 直发 `asyncio.to_thread` 无 semaphore、`_load()` 无 timeout → 1.7GB 下载/136s 构建可占多 worker。修复：`asyncio.wait_for` + Semaphore + lifespan 预载
- **I2** 策略抽取默认标签不一致：keyword 兜底无匹配返回 `"ai_autonomous"`（`agent_config.py:54`），laya `STRATEGY_LABELS` 含 `"hold"` → 语义倒置（无策略提及 vs 明确持仓）
- **I3** laya 策略结果无置信阈值（`agent_config.py:111-112` 非 None 即收，丢 confidence）→ laya 最低置信猜测可覆盖 keyword 近确定性信号。需与情绪路径同阈值门控
- **I4** 「8+1 策略」声明不符：`STRATEGY_LABELS` 仅 6 项、criteria 覆盖 5+ai_autonomous；`momentum` 不是真实策略（注册表是 `momentum_rank`）→ laya 可发出 resolve 不到 `STRATEGIES` 的标签，接上 strategy_switch 会 ValueError
- **I5** `_load()` `# pragma: no cover` 零测试（HF_ENDPOINT 优先级、缓存目录解析、失败→永久禁用、线程安全）；`TestRealLayaAPI` CI 跳过；`test_default_disabled` patch 而非断言默认 → C1 修复后仍通过。需补默认断言 + mock import 测 `_load`
- **I6** 死 `lang` 参数：`news_sentiment.analyze(lang=None)` 仅在 LLM 回退路径生效，预筛命中时被忽略（当前无调用方传它，潜在 footgun）

**Minor**
- `get_laya_runtime()` 非线程安全（`laya_runtime.py:137-141` 双重构造各载 ~1.7GB×2）
- `HF_ENDPOINT` 进程级变异（`:65-66`）影响其他 huggingface_hub 消费者
- `_available` 首次探测缓存 `settings.laya_enabled`，运行期翻转无效
- `news_sentiment.py:115` `item["title"]` 直接索引（pre-existing，KeyError 风险，位于新 DB 写循环内）
- 重复 import（SENTIMENT_LABELS）
- SENTIMENT_LABELS/STRATEGY_LABELS 在 laya_runtime.py 而非 constants.py（违反项目约定）

**评估：可以合并吗？否**——C1 使生产首次情绪任务无超时下载 1.7GB；C2 把概率模型经 AI 过滤闸重新塞回交易闸门（违反「概率模型不可靠作拦截依据」）。降级机制/白名单/置信语义文档做得好、39 测试过。修 C1 C2 再合并，I1-I4 同批。

### 代理 B — TradeGate + 引擎（已返回）

**优点**
1. 硬闸门不变量未被破坏（`engine.py:804` `_check_trade_permission` 内，只追加否决；preflight 仍执行；手动/MCP 通道不经过）
2. 无重复开仓风险（每 K 线一次，gate False → return）
3. fail-open 异常兜底正确（`engine.py:817-819`）
4. 懒加载 + `trade_gate_enabled: bool = False`（`config.py:322`）确认
5. 注释诚实（fillna(0) 退化风险、标签泄漏自述）
6. joblib 延迟导入、类型标注齐全

**Critical（必须修复）**
- **C1** `trade_gate.py:61`：`required = {"open","high","low","close","volume"}` 列名契约错误。生产链路无 `volume` 列（Bridge 返回 `tick_volume`，`market_data.py:65-80` 原样透传，无重命名）。`required.issubset(df.columns)` 恒 False → 恒 `(False, 0.0)` → `engine.py:814` 判定不可交易 → **`trade_gate_enabled=True` 拒绝每笔交易**（不是功能未生效，是启用即灾难）。feature 层 `features.py:88` 读 `tick_volume` 才是对的（实测：volume 列 → NaN，tick_volume → 0.396）
- **C2** `engine.py:801-803` 注释承诺「模型缺失/OHLCV 不可用 → 放行」，实际前两种走返回值 `(False,0.0)` → **阻断**（fail-closed），只有异常才 fail-open。schema 问题被转译成「永久全量否决」= 静默停止交易。且 `.fillna(0)`（`trade_gate.py:69`）在缺列时把特征填 0（训练分布外常数值）→ 门控静默失效 + 大概率拒单。需引入「弃权」第三态 `(None, 0.0)`

**Important（应当修复）**
- **I1** 影子验证机制缺失：计划要求「影子决策 + 长影子验证」，交付却是硬否决。无 `trade_gate_shadow` 开关、`gate_prob` 不落 `pre_trade_snapshot`/audit → 无法计算真实精度
- **I2** `engine.py:811` 重复拉取 OHLCV（`_generate_signal` 已拉过 `df`）；且决策用 bar N-1（`df.iloc[-2]["signal"]` `engine.py:753`）而 gate 评分 bar N（`features.iloc[-1]`）——语义错位 + 第二次网络往返（连接器超时 8s）
- **I3** NaN 鲁棒性测试未覆盖真实路径：测试夹具整数索引无 DatetimeIndex → 时间特征 6 列常数；全程未注入 NaN；`FakeModel` 忽略输入 X，`prob > 0.5` 断言是恒真式
- **I4** 阈值取自基率（`laya_synth_baseline.py:163`）锁死接受率；`TradeGate._load` 不校验 `predict_proba`、`threshold`、symbol 匹配 → 别的品种模型可被指向静默拦单
- **I5** `engine.py:815` 用 `BotEventType.TRADE_BLOCKED` 与硬闸门共用枚举，审计无法区分；且不发 Telegram（对比 `engine.py:896-901` risk_manager 阻断会发）→ 真钱模式静默拦单
- **I6** warm-up 门槛 `len > 60` 无效：实测 bars=61 时最后一行仍 2 个 NaN（`atr_percentile` 需 rolling(100)）

**Minor**
- `engine.py:811` 硬编码 200（`constants.py:19` 已有 `DEFAULT_OHLCV_BARS`）；`len>60` 应入常量
- 无模型热加载（模型文件后到不重试）
- `trade_gate_enabled` 仅环境变量，`/settings` 无法操作
- `_trade_gate` 实例级可提模块级单例
- 测试导入 `patch` 未用；两测试重复
- `trade_gate.py:74` 文件尾缺换行
- 文档漂移：docstring「41 特征」实际 47
- `laya_enabled: bool = True`（`config.py:310`）默认开 vs `trade_gate_enabled` 默认关，同为实验功能默认值相反

**评估：可以合并吗？否**——C1 使「启用即停单」，C2 使注释承诺 fail-open 实际 fail-closed，且「长影子验证」计划硬性要求无落地机制。默认关闭无害，但翻开开关即停单不报警。建议按 C1→C2→shadow 顺序修完再合并。

### 代理 C — 部署 + 死代码 + 前端 + 脚本（已返回）

**优点**
1. 死代码删除硬闸门通过：全仓 grep `quant_analyzer`/`QuantAnalyzer`/`QuantAnalysis`/`build_quant_context`/`analyze_quant_metrics`/`complete_async` 后端源码零命中；无动态 import、无路由注册、无模块级副作用（仅 `docs/SYMBOL-PARAMETERS-TECH.md:128` 行号引用失效）
2. Dockerfile CPU-only torch 策略正确（`--index-url .../whl/cpu` 避免拉 CUDA torch；`|| echo` 降级）
3. laya_runtime 工程质量高（懒加载+Lock、显式 cpu、to_thread、白名单、置信语义）
4. baseline 脚本 DB 侧防护扎实（`default_transaction_read_only='on'`、参数化查询、timeframe 过滤、LIMIT clamp、TimeSeriesSplit 不 shuffle、单类折跳过、主动披露泄漏）
5. 前端降级路径安全（engine 可选、条件渲染、colorMap 兜底、dataclass 默认 "llm"、to_dict 两路径都带字段）
6. test_config_cors_origins.py 质量高（9 用例，patch.object 防本机 .env 漂移，修 typo）

**Critical（必须修复）**
- **C1** `config.py:307` `laya_enabled=True` + `Dockerfile:25 PRELOAD_LAYA=0` 默认 off + Railway 不传 build-arg → 镜像 `/app/models/laya` 空目录 → 首个情绪任务请求期**无超时下载 ~1.7GB** + 加载后 ~1.7-1.8GB RSS（无 dtype 参数）→ **512MB-1GB 预算放不下 + OOM 风险 + 失败即永久降级**。修复：revert False + 显式两步部署（PRELOAD_LAYA=1 + LAYA_ENABLED=true + LAYA_MODEL_CACHE_DIR）+ `_load` 超时 + 内存 spike
- **C2** 权重目录布局不匹配：Dockerfile `--local-dir /app/models/laya`（文件直接落目录内）vs `laya_runtime.py:73` `join(cache_dir, basename(model_id))` = `/app/models/laya/laya` → `isdir()` 恒 False → **本地路径分支永不命中，PRELOAD_LAYA=1 也拿不到预载权重**，仍运行期下载。修复：对齐路径或改运行时逻辑
- **C3** `huggingface-cli` 被 huggingface-hub 1.x 废弃（exit 1）；laya 0.3.4 依赖锥（hub>=0.20 无上界）会解析到 1.14.x → **Dockerfile:29 构建必然失败**（且 `--local-dir-use-symlinks` 新 CLI 已删）。该分支默认 0 从未被执行验证过。requirements 未 pin hub/transformers 不可复现。修复：用 `hf download`/`snapshot_download` + 删 symlinks flag + pin 版本（注意国内镜像 laya 只到 0.3.3）
- **C4** baseline AUC 结构性泄漏：`TimeSeriesSplit` 训练末尾样本标签引用 i+1..i+10 价格，同时出现在验证集 → AUC 系统性虚高（脚本已披露但仅在 AUC<0.55 分支打印）。决策门 ≥0.6 据此判定「值得继续」并全量重训落盘 → 产物已进生产（trade_gate.py 头注释 AUC≈0.739、config trade_gate_model_path、engine.py:804 已接入）。修复：purge gap（切除 forward_bars）+ 标注「阈值待重测」

**Important（应当修复）**
- **I1** `.env.example` 未记录 LAYA_ENABLED/LAYA_MODEL_CACHE_DIR/HF_ENDPOINT/TOKENIZERS_PARALLELISM/USE_TF（本轮核心诉求「可操作开关」未达成）；USE_TF/TOKENIZERS_PARALLELISM 只在 `_load()` 内部 setdefault 未烘焙进 Dockerfile ENV
- **I2** 注释与实际默认值矛盾（`config.py:306` 注释「默认 False」vs L307 `True`；`requirements.txt:34` 也写「默认关闭」）三处打架
- **I3** `_load()` 无条件写进程级 `os.environ`（HF_ENDPOINT 影响同进程其他 hub 消费者）；建议 `snapshot_download(endpoint=...)` 参数级
- **I4** baseline 脚本 symbol 硬编码 GOLD（`--symbol BTCUSD` 合法参数不生效）；DB 列名 volume vs features 只认 tick_volume → volume 特征恒 0（跨 symbol 迁移静默失配）；`.env` 手工解析不处理 `export ` 前缀/行内注释
- **I5** `_load()` `# pragma: no cover` 零测试（承载全部部署逻辑）；TestRealLayaAPI CI 跳过；默认值测试是 patch 非断言
- **I6** `laya.load()` 无超时保护（无网络时阻塞调用线程，线程池打满情绪任务全排队）

**Minor**
- **M1** `SentimentBadge.tsx:52` `text-info` 在当前主题未定义（globals.css 无 `--info`）→ laya 标记渲染成与 llm 相同样式，区分效果没实现
- **M2** baseline `datetime.now().isoformat()` 本地时区 naive（应 utcnow）
- **M3** baseline 缺列直接 KeyError 无可操作错误信息
- **M4** `_laya_api_verify.py:36-37` 无 try/finally 还原，翻转 laya_enabled 易被复制进生产
- **M5** Dockerfile `npm install -g @anthropic-ai/claude-code@latest` 未 pin

**评估：可以合并吗？No**——死代码删除硬闸门通过、前端/CORS 质量高可单独落地；但部署方向坏：laya_enabled=True + PRELOAD_LAYA=0 会在生产请求期触发 1.7GB 下载 + 1.7GB 内存（无 dtype、无超时、失败即永久降级），叠加 C2 路径不匹配 + C3 CLI 废弃，「生产容器启用 laya」目标未成立。按 C1→C2→C3 修复 + 容器内存实测后再合。

## 交叉验证清单（Phase 2）

### 已确认（编排者实测/实读）
- [x] **C1(laya_enabled=True)**：代理 A+C 双报，`config.py:307` 实读确认 `True`，注释 `:306` 写「默认 False」；Git 确认 a517dc0 引入为 False 后被翻 True
- [x] **C1(TradeGate volume vs tick_volume)**：铁证。`mt5_bridge/main.py:372` 返回 `tick_volume`；`market_data.py:65-80` 原样透传不重命名；`trade_gate.py:61` 要求 `volume` → 恒 `(False, 0.0)` → 启用即全量拒单。且 `backend/models/trade_gate.pkl` **存在**（697KB）→ is_ready=True → 杀伤力实锤
- [x] **C2(Dockerfile 路径不匹配)**：`Dockerfile:29` `--local-dir /app/models/laya`（文件直接落目录内）vs `laya_runtime.py:73` `join(cache_dir, basename("convaiinnovations/laya"))` = `/app/models/laya/laya` → 本地分支永不命中
- [x] **C2(confidence 混入风控闸)**：`news_sentiment.py:103` 写 max-class prob；`risk/manager.py:325-333` 按 `confidence >= eff_threshold` 过滤（laya 通过率远高 → 静默削弱）；`sentiment_features.py:62-74` 混合三档近似 score 与连续 score → 分布偏移
- [x] **C3(huggingface-cli 废弃)**：Dockerfile:29 用 `huggingface-cli download` + `--local-dir-use-symlinks`，huggingface-hub 1.x 已废弃（exit 1）；PRELOAD_LAYA=0 默认该分支从未被执行验证
- [x] **trade_gate.pkl 在 .gitignore** → 不进仓库，需手动投放（部署坑）
- [x] **agent_config.py:106-114**：laya 策略结果无置信阈值直接采用（代理 A I3）；keyword 兜底默认 `ai_autonomous` vs laya `STRATEGY_LABELS` 含 `hold`（I2）
- [x] **engine.py:804-826**：`_check_trade_permission` 内接线确认；`get_ohlcv(...,200)` 重复拉取（I2）+ `len>60` 门槛（I6）

### 待补充
- [ ] quant_analyzer/client 死代码删除零调用方确认（代理 C 已 grep 全仓零命中，编排者复核）
- [ ] 前端 SentimentBadge `text-info` 类是否真的未定义（代理 C M1）

## Phase 4 第二轮修复（验证代理对抗结论，2026-09-22）

独立验证代理 a1ab60b58a2efe325 裁决「不能提交」，实测揭出第一轮修复未及之处：

### C1 真根因（第一轮仅治标，本轮根治）
**第一轮修复只改了列名契约**（`volume` → `tick_volume` 允许缺失），但实测真实 `models/trade_gate.pkl`：
```
[LightGBM] [Fatal] The number of features in data (40) is not the same as it was in training data (41).
dropped: ['volume']
```
**根因**：训练脚本 `laya_synth_baseline.py:117` 排除元组 `("open","high","low","close","tick_volume")` **漏排裸 `volume`**；而 `features.py:83 out = df.copy()` 把入参 df 的额外列透传 → 训练特征矩阵含裸 `volume`，模型 `feature_columns` 41 列含 `volume`。推理时 `build_features` 只产出仓库计算特征（`volume_sma_ratio` 等，无裸 `volume`）→ 41 vs 40 特征失配 → LightGBM 报错被 `except` 吞 → `(None, 0.0)` 弃权。**生产 `tick_volume` 列名下旧实现 100% 弃权**（shadow 模式掩盖，enforce 一开就是永不拦的哑开关）。

**此前的测试为何漏掉**：`_fake_joblib_data` 用 `FEATURE_COLUMNS`（不含裸 `volume`），mock 模型 40 列对齐 40 列，从未加载真实 pkl。

**本轮修复**：
1. `TradeGate._load` 检测 `requires_legacy_volume`（`volume in feature_columns`）
2. `TradeGate.predict` 在 `tick_volume` 存在且模型要裸 volume 时，注入 `df["volume"] = df["tick_volume"]`
3. 训练脚本排除元组加 `volume`（防未来重训再犯）
4. `.gitignore` 改 `backend/models/*` + `!backend/models/trade_gate.pkl` + `!*.pkl` 豁免（trade_gate.pkl 是引擎必需产物，必须入库；Railway git archive context 下缺失 → gate 空转）
5. **真实 pkl + tick_volume 回归测试**（`test_real_pkl_tick_volume_predicts`，pkl 缺失跳过）——实测通过，咬住 C1 本体

### trend_following 白名单决策反转（保留为既有契约）
第一轮把 `STRATEGY_LABELS` 的 `momentum`→`momentum_rank` 时顺手删了 `trend_following`，验证代理指出这会与 `_STRATEGY_KEYWORDS` 不一致（keyword 兜底仍能产出）。**第二轮先同步删 keyword** → 但全量暴露 `test_llm_lang.py` 既有断言回归（`"Trend Following..."` → `strategy_used == "trend_following"`）——**trend_following 是分支既有契约名**（HEAD 原始就含），`strategy_used` 仅用于展示不 resolve 到实现，删它会破坏既有行为。

**最终决策**：恢复 `trend_following` 到 STRATEGY_LABELS + laya criteria + `_STRATEGY_KEYWORDS`，两处白名单一致；只保留真 bug 修复 `momentum`→`momentum_rank`（该名确实不在注册表，切 `strategy_switch` 会 ValueError）。

**遗留既有问题（记档）**：`trend_following` 不在 `STRATEGIES` 实现注册表（9 个注册策略无它）。当前 `strategy_used` 只进 dashboard/WS 展示，无 resolve 风险；一旦 `strategy_switch` 接通，需映射层（`trend_following` → 具体策略如 ema_crossover）或显式拒绝。这是分支既有设计问题，非本次审查引入。

### 其他补齐
- **NaN 检查死代码**（`trade_gate.py`）：`isna().mean() > 0.3` 在 `fillna(0)` 之后恒 False → 提前到 fillna 前（既有测试用常数 df 恰好掩盖，改用真实 ohlcv_df drop volume）
- **engine.py:547 注释矛盾**：说「AI 数据源计数仍包含它」与实际（available_sources 排除 laya）矛盾 → 改准确
- **测试盲区补齐**：`TestLegacyVolumeSchema`（4 用例）+ `TestStrategyChoiceThreshold`（阈值边界 4 用例）

### 遗留 Minor（未修，记档）
- shadow 弃权/会拦记 `TRADE_BLOCKED` 事件 → 活动页 signal 分类有「shadow: ...」噪声（消息已带 shadow 前缀，需前端分类细化时一并处理）
- `HF_HUB_ENABLE_HF_TRANSFER=1` 空转（requirements 未装 hf_transfer，hub 打 warning 后回退普通传输，不影响构建）
- `momentum_rank` 需 `symbol` 参数，`strategy_switch` 切到它时未传会构造失败（当前未接通 strategy_switch 使用，潜伏）
- `PRELOAD_LAYA=0` 时首个推理请求内在请求线程下载 ~1.7GB（to_thread 隔离阻塞，但首次延迟）
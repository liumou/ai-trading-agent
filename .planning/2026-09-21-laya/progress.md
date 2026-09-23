# Progress Log

## Session: 2026-09-21 — Laya 集成代码审查

### 当前状态
- **阶段:** 1-2（逐文件审查 + 交叉验证进行中）
- **审查对象:** 提交 a517dc0（PR #1，feat/laya-research-and-integration）
- **计划:** 用户已批准审查计划

### 已执行动作
- [x] 读取全部变更文件（laya_runtime.py / news_sentiment.py diff / config.py / laya_synth_baseline.py / test_laya_runtime.py）
- [x] 分派 code-reviewer 子代理审查完整 diff（进行中）
- [x] 验证调用链：`scheduler._sentiment_job` → `engine.fetch_and_analyze_sentiment` → `sentiment_analyzer.analyze`（async）
- [x] 运行 `pytest tests/unit/test_laya_runtime.py` → 4 passed
- [x] `py_compile` 全部变更文件 → 通过
- [x] 验证 SQL 注入面：symbol 用 `$1` 参数化、limit 为 argparse int → 安全
- [x] 验证 ML 特征消费链：`sentiment_features.py:26` 从 `NewsSentiment` DB 表聚合
- [x] 确认 `laya_sentiment_choice` 唯一调用方 = `news_sentiment.py:81`

### 独立验证发现（主会话）
| # | 级别 | 问题 | 证据 |
|---|---|---|---|
| 1 | **Critical** | `laya_sentiment_choice` 直接调 `_predict_sync`（同步），冷加载 ~136s + 推理 200-500ms 全部阻塞事件循环 | `laya_runtime.py:135` 绕过异步 `predict()`；调用链 async |
| 2 | **Important** | 预筛命中不写 `NewsSentiment` DB 表 → ML 特征聚合缺数据 | `news_sentiment.py:97-98` 只写 Redis；`sentiment_features.py:26` 消费 DB |
| 3 | **Important** | 测试覆盖缺口：`laya_sentiment_choice` 本身无直接测试（被 mock）；无独立 sentiment 回归测试 | `test_laya_runtime.py` 4 用例全 mock `laya_sentiment_choice` |
| 4 | Minor | `_clean_headlines` 在 analyze() 中调用两次（prefilter + LLM prompt） | `news_sentiment.py:81,111` |
| 5 | Minor | score 近似映射硬编码 `{bullish:0.5, bearish:-0.5, neutral:0.0}`，与 LLM 连续 score 分布不一致 | `news_sentiment.py:88` |
| 6 | 验证 | `Connection._cancel was never awaited` 警告来自 fakeredis 非新代码 | 对比 conftest 确认 |

### 测试结果
| 测试 | 预期 | 实际 | 状态 |
|------|------|------|------|
| test_laya_runtime.py (4 用例) | 通过 | 4 passed | ✅ |
| test_scheduler_risk_gate.py + test_ml_barrier_validation.py + test_build_labels.py (34 用例) | 通过 | 34 passed | ✅ |
| 默认关闭零影响（模拟无 laya 环境） | 不 import、不写 env、返回 None | 通过 | ✅ |
| LLM 原路径（默认关闭 analyze） | bullish/0.6/0.9 | 通过 | ✅ |
| 覆盖率：laya_runtime.py / news_sentiment.py | — | 47% / 62% | ⚠️ 真实推理路径 0 覆盖 |

### 错误
| 错误 | 处理 |
|------|------|
| ruff 未装入 venv | 用 py_compile 替代基础检查；CI 会跑 ruff |
| test_news_sentiment.py 不存在 | 无独立回归，由 test_laya_runtime.py 覆盖 |

### 最终结论（Phase 3 完成）
- **Critical (1)**: C1 同步阻塞事件循环（`laya_runtime.py:135`）
- **Important (4)**: I1 ML 特征饿死、I2 label/confidence 无校验、I3 `--db` 列名错误+timeframe 缺失
- **Minor (13)**: 见 findings.md
- **结论**: COMMENT 可合入（默认关闭零影响），但 `LAYA_ENABLED=true` 前必须修 C1/I1/I2/I3
- **待用户批准**: Phase 4 修复

### Phase 4b 真实 API 测试（用户要求，2026-09-21 完成）
- **背景**：laya API 返回结构（`result["answers"]["sentiment"]`）从未真实执行验证过（code-reviewer OQ），用户要求测试
- [x] 建隔离 venv `/tmp/laya-api-venv`（Python 3.10.18，与 spike 一致）
- [x] 装 CPU-only torch 2.14.0（踩坑：旧 pip 23 的 build-isolation 找不到 flit_core → 升级 pip 26.2.1 + `--no-build-isolation`）
- [x] 装 laya 0.3.4（GitHub 源）+ transformers 5.17 + 项目依赖
- [x] 权重已在 HF 缓存（~4.7M 引子 + 快照，加载 46s）
- [x] **真实 API 验证通过**（`_laya_api_verify.py`）：label/confidence/probabilities 解析完全兼容
- [x] **发现并修复 confidence 语义 bug**：laya 原生 confidence 是熵置信度（0.59≠最大概率 0.87）→ `predict_choice` 改 confidence=最大类概率，熵置信度保留为 entropy_confidence
- [x] 固化真实结构到单测：`TestPredictChoiceParsing`（3 用例，断言 confidence=max_prob）+ `TestRealLayaAPI`（importorskip 集成测试）
- [x] 项目 venv：12 passed + 1 skipped（集成 skip 正确）
- **错误**：torch flit_core（旧 pip）→ 升级 pip + --no-build-isolation；SOCKS 代理需 httpx[socks]；隔离 3.10 无法 import db/models（StrEnum）→ 独立脚本绕过

### Phase 4 修复（用户已批准，2026-09-21 完成）
- ✅ **C1**：`laya_runtime.py` 新增 `predict_choice()`（内部走 `predict()` → to_thread），`laya_sentiment_choice` 改 `async def`；`news_sentiment.py:82` 改 `await`
- ✅ **I1**：`news_sentiment.py` 预筛命中写 DB 审计行（`raw_response` 标记 `engine=laya` + probabilities）
- ✅ **I2**：`SENTIMENT_LABELS` 白名单（laya_runtime + news_sentiment 双保险）+ confidence 类型检查
- ✅ **I3**：`laya_synth_baseline.py` 列名 `tick_volume`→`volume`、加 `timeframe` 过滤（CLI `--timeframe`）、limit clamp、SQL `$3` 参数化
- ✅ **额外**：修 baseline 单类别折 `roc_auc_score` 崩溃（跳过 + 提示）；C5 `_clean_headlines` 单次复用；M11 截断 print 补全
- ✅ **测试**：9 laya（+5 真实路径 Direct 测试 + I2 双保险测试）+ 67 全量回归通过
- ✅ **验证**：baseline `--csv` 全链路跑通（5000 行 → 41 特征 → AUC 决策门正确输出）
- ✅ **覆盖率**：laya_runtime 47%→51%、news_sentiment 62%→63%（真实 _load 路径需装 laya 才能测）
- ⚠️ **遗留**：laya API 返回结构（`result["answers"]["sentiment"]`）从未真实执行过——上线前需真实环境 smoke test

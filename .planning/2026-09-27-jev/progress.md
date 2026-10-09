# Progress Log

## Session: 2026-09-27/28 — 手动交易决策迁移到 JEV（System One）

### Current Status
- **Phase:** Phase 1 ✅ + Phase 2 ✅（Phase 3/4 可选未实施，等用户指示）
- **交付物：** `backend/app/services/systemone.py`（新）、config/main/order_preflight/manual_order_gate（改）、前端轮询 1 处、2 个新测试文件 + 旧回归 autouse 保护

### Actions Taken
- 2026-09-27 计划 v1→v3（两轮评审共 7 名评审员），用户批准 v3
- 2026-09-28 实施：
  - systemone.py：SystemOneDecision/LocalRuleEngine；四路并发数据获取（M15/H1/spec/history，wait_for 5s 封顶）；数据质量三档分诊；9 规则；5 检查映射；收敛器；confidence=完备度×余量；to_review_llm_shape + audit_block
  - config.py：manual_review_* 20 个配置项（Literal provider 枚举 + ge/le + model_validator block>warn）
  - main.py：gate 创建后启动 dump 全部生效阈值
  - order_preflight.py：PreflightContext 增加 account_daily_pnl（账户级日亏）
  - manual_order_gate.py：provider 链（local_jev→LLM 兜底，typesafe_jev 预留）；_llm_review 原样抽取（回滚纯度）；_apply_verdict 合并写审计（systemone 块 + review.llm 兼容形状）；REJECTED kind=systemone_rejected；降级计数 + CIRCUIT_BREAKER 聚合告警（阈值触发一次，恢复 info 日志）
  - frontend/app/trading/page.tsx：REVIEW_POLL_MAX 20→50（40s→100s > 90s LLM 兜底超时），修正过时注释
  - 测试：test_systemone.py（21 用例）+ test_gate_provider_chain.py（9 用例）；test_manual_order_gate.py 加 autouse provider=llm fixture（24 用例零改动保持绿）

### 实施期决策（偏离计划文本处，均有依据）
1. **全量历史由引擎自拉**（非 gate snapshot 复用）：snapshot.recent_trades 是 LLM prompt 输入，改它会破坏 provider=llm 回滚纯度
2. **全 TF 陈旧 → data_quality=insufficient → CAUTION**（非降级）：周末休市是已知市场状态非基础设施故障；只有「无数据/根数不足」才降级 LLM
3. **无趋势周期 → signal_alignment=aligned（放行）**：震荡市无方向矛盾；否则横盘市每单 CAUTION（正是评审警告的常态误报）
4. **margin_pct 为可选路径**：bridge /account 未暴露 leverage 字段，有则启用（risk_pct 为主指标不受影响）
5. **converge.upgrade 带规则名**（机器可读审计）
6. 删除计划里的「deal time 畸形→partial」用例：引擎从不解析 deal time（中位手数/陌生品种只用 symbol+lot），无可破坏路径

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| tests/unit/test_systemone.py | 21 pass | 21 pass | ✅ |
| tests/unit/test_gate_provider_chain.py | 9 pass | 9 pass | ✅ |
| tests/unit/test_manual_order_gate.py（autouse llm） | 全绿零改动 | 24 pass | ✅ |
| 全量 tests/ | 既有失败不增加 | 1081 pass；8 fail 全部既有（7×multi_agent 记忆在案 + 1×ml_barrier 隔离顺序问题，已用 git stash 在干净树复现） | ✅ |
| app.main 导入冒烟 | OK | OK | ✅ |

### Errors
| Error | Resolution |
|-------|------------|
| PreflightContext dataclass 字段顺序错误（带默认值字段插在中间） | 移到类尾 |
| submit_order 里 ctx 未绑定裸名 | 锁块内补 ctx = pf.ctx |
| 测试数据 linspace 漂移导致 ADX 仅 10-14（资格线 20 过滤掉趋势） | 改 per-bar 漂移口径并程序化搜索参数 |
| 测试数据趋势漂移使 entry 偏离 EMA20 → spike p100 | 健康单用例改平盘数据（规则行为正确） |
| 连续下单被 guardrails 120s 最小间隔硬拒 | 测试间清 guardrails:last_trade_time |
| 量化评审 agent 首派遇并发限额 | 重试成功 |

## Session: 2026-10-09 — Phase 3（TypeSafe JEV 真实 API）实施

### Actions Taken
- 用户提供 JEV key（oc_sk_…）+ model jev-1.13-free + base https://opencode.ai/zen/v1/systemone
- 新增 `backend/app/services/typesafe_jev.py`（协议镜像 + 严格校验 + 熔断 + market_evidence）
- config 增 manual_review_typesafe_* 8 项 + conf_floor；网关 provider 链泛化为可排序链（typesafe 可主审/中继）
- 网关 `_typesafe_provider()` 注入 MarketDataService（行情证据）；`_provider_timeout_s()` per-provider 预算
- main.py 启动 dump 增 JEV 状态（configured/未配置 + base/model/阈值，key 只报有无）
- .env 写入 key/base/model + MANUAL_REVIEW_PROVIDER=typesafe_jev（主审）
- 真实 API 冒烟 3 次（无证据/基础证据/一致状态）→ 一致状态得 CAUTION 1.1s，signal_alignment 0.88 证明证据被消费
- 测试：test_typesafe_jev.py 14 用例 + 链测试 3 用例；gate 链测试 autouse 清真实 key（防真打外部 API）

### 实施期决策
1. **两段置信分诊**（floor 0.30 / min 0.55）：free 模型置信保守（0.29~0.41），单一 min 阈值会让 JEV 恒降级；floor~min → CAUTION 人工确认是正确档位（不放松 firewall）
2. **state 必须带 market_evidence**：实测无证据时 exec 0.32 + alignment 无法判断；证据用与 local 同源的指标摘要（trend/adx/atr_pct/rsi/macd/momentum）
3. **JEV 自带证据拉取**（不共享 local 的 fetch）：provider=typesafe 主审时 local 不运行；provider=local 时 JEV 仅作降级中继，重复拉取代价可接受
4. **熔断只影响本 provider 可用性**：不改变决策链 fail-closed；连续 3 次失败冷却 300s
5. **默认 provider 设为 typesafe_jev**（.env，非代码默认）：对齐用户「把决策交给 JEV」的意图；local 为秒级第二链；env 一行即可回滚

### Test Results
| Test | Actual | Status |
|------|--------|--------|
| tests/unit/test_typesafe_jev.py | 14 pass | ✅ |
| tests/unit/test_gate_provider_chain.py | 12 pass（含 3 JEV 链用例） | ✅ |
| tests/unit 全量 | 956 pass / 7 fail（既有 multi_agent） | ✅ |
| 真实 JEV API 冒烟 | CAUTION conf 0.35 latency 1.1s | ✅ |

### Errors
| Error | Resolution |
|-------|------------|
| 字段名与方法名冲突（self._local_engine）导致 'NoneType' not callable | 字段改名 _local |
| 跨测试文件 import（test_typesafe_jev）失败 | 链测试内置本地应答构造器 |
| 测试真打外部 JEV API（.env 有真 key + local 失败路径） | autouse fixture 清 key |
| 低置信→降级 与 低置信→CAUTION 设计冲突（CAUTION 不可达） | 两段阈值：floor 以下降级，floor~min CAUTION |
| 主审用例断言 local 不调 get_ohlcv（JEV 也要拉证据） | 改用 local 引擎指纹 get_symbol_spec |

## Session: 2026-10-09（续）— 真实数据 JEV API 测试
- 用户要求：用真实数据（非 mock）测 JEV API → 产出详细报告
- 桥间歇 502 → 写 fetch_real_snapshot.py（6 次重试/项）采集真实快照；测试脚本 /tmp/jev_realdata_test.py（冻结快照 + RecordingProvider 保留原始 answers + local 对照）
- 6 次真实 JEV 调用（地板 0.30/0.15 两轮 × 3 场景）；报告 .planning/2026-09-27-jev/jev-realdata-report.md（375 行，含原始 answers/概率分布/诚实披露）
- 关键产出：地板 0.30 吞风险否决 → 定 0.15；JEV 对逆势/超杠杆单能到 REJECTED（local 只 CAUTION）
- 测试卫生：test_typesafe_jev._credentials 钉死 conf_floor=0.30（.env 改为 0.15 后单测不得漂移）

## Session: 2026-10-09（续）— 用户可见文案中文化
- 诉求：风控审查结果全是英文表达式，看不懂
- 改动：systemone.py（本地引擎）与 typesafe_jev.py（JEV）的 reasoning/risk_flags/evidence/规则详情全部中文化（检查名/选项/规则名/严重度/理由码）；converge 与 checks 的机器键保持英文（审计与测试不受影响）
- 连带修复：_assess_data_quality 的 too_short 分诊匹配英文字串（"bars <"/"no data"）→ 改中文（"无数据"/"最低"），否则全 TF 根数不足会误入 CAUTION 而非降级
- 测试：4 个文件 71 通过（断言同步改中文 token）；真实快照验证本地+JEV 两条链输出均为中文
- 后端已重启（pid 86392）生效；前端无需改动（reasoning 来自后端字段）

## Session: 2026-10-09（续）— JEV 技术文档
- 交付 docs/JEV-INTEGRATION.md：外部 API 请求/响应字段级说明（state + market_evidence + 5 问结构 + answers 校验 + 收敛映射）、手动单全流程 mermaid 图 + JEV 段时序图 + 每步职责表 + 超时预算、真实请求/响应示例（S3 原始 answers）、审计结构、配置表、排查表
- docs/MANUAL-REVIEW-JEV-GUIDE.md 增加交叉链接

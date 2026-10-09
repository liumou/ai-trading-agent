# Task Plan v3: 手动交易决策迁移到 JEV（System One 快判断层）

> v3 = v2 经第二轮全方位评审（落实核查/量化规则/工程事实核查/SRE安全，2026-09-28）迭代。评审证据见 findings.md 两个「评审结论」章节。
> v3 核心变化：①补第 9 项 mtf_conflict + 检查↔规则映射表（v2 收敛器引用 conflict 但无定义，实施即卡住）；②exposure_cap 改 risk_pct/margin_pct 口径（notional/equity 在杠杆品种下常态性误触发 + JPY 计价 150× 汇率错误，三方独立确认）；③mtf conflict 单独→CAUTION、双 TF 逆向才 REJECTED（单 TF 共识会误杀回调进场单）；④spike_chase 改滚动分位自校准（固定 ATR 倍数在强趋势常态误报）；⑤no_sl 确认为死规则；⑥freshness 用 broker 同源时钟；⑦降级告警链路；⑧工程红线（运行时读 settings、import 路径、一次性写审计）。

## Goal
把 ManualOrderGate 的异步 LLM 审查（90s 超时）替换为 System One 确定性规则决策层（5 检查 + 9 规则 + 收敛器，秒级封顶），LLM 降为兜底。风控强度**不松于**现 LLM 基线：REJECTED 收窄为「硬闸门 block + 规则 block + 双 TF 逆势升级」，其余风险一律 CAUTION 人在环。fail-closed 不变式全部保留。

## Next Step
Phase 3 ✅ 已随用户提供 JEV_API_KEY 实施并真实端到端验证（1.1s/判 CAUTION）。Phase 4（shadow 对比）仍为可选项。
核心交付（Phase 1+2+3)完成并通过回归。建议观察一段实际判决分布后决定是否校准阈值/F扩展。

## Current Phase
Phase 1 ✅ / Phase 2 ✅ / Phase 3 ✅ complete（2026-09-28 实施 + 10-09 真实 API 验证）

## Phases

### Phase 1: System One 规则引擎核心（新增 backend/app/services/systemone.py）

**数据获取与前置硬校验（不信任指标栈静默降级）**
- [ ] MarketDataService(connector).get_ohlcv（backend/app/mt5/market_data.py:65-78），传 ctx.symbol，M15/H1 两 TF 请求 count=120、`asyncio.gather` 并发（先例 order_preflight.py:174）+ `asyncio.wait_for` 5s 总封顶
- [ ] **数据质量硬校验**：全 TF 空 DF/超时/异常 = provider 失败 → 降级链；单 TF 空 = partial；`len(df) >= 60`（请求 120 留 buffer）；**freshness 用同源时钟**——最后 K 线时间 vs bridge tick 自带时间戳（同为 broker 时间，免疫时区/周末/DST），tick 不可得才回退 _utcnow() + `manual_review_freshness_mult`（×N 根 bar 周期，非绝对秒）；休市陈旧 = partial 非 insufficient；逐指标 `pd.notna`（周末持平 K 线 ADX=NaN→ranging 陷阱）
- [ ] **数据切分复用**：一次全量 get_history(days=14)（symbol=None，connector.py:271-277），本地切分——同品种子集给 size/revenge/spike 检查，全量给 unfamiliar_symbol；snapshot.recent_trades 形状不变（同品种最近 10 条）

**9 条确定性规则（阈值全进 config，manual_review_* 前缀；preflight/guardrails 已覆盖的只读结果）**

| # | 规则 | metric 公式 | warn | block | config（初始值） |
|---|------|------------|------|-------|------------------|
| 1 | spike_chase | z=\|entry−EMA20(M15)\|/ATR14 vs 近 200 根滚动分位；历史<100 根只 warn 不 block | z>90 分位 | z>99 分位 且连续≥4 根同向 K 线 | spike_pct_warn=0.90 / block=0.99 |
| 2 | exposure_cap | risk_pct=潜在亏损/equity，潜在亏损=\|entry−SL\|/tick_size×tick_value×lot（**tick_value 已是账户货币计价**，规避 JPY 汇率坑）；margin_pct=初始保证金/equity | risk>2% ∨ margin>25% | risk>5% ∨ margin>50%（crypto 减半） | risk_pct_warn=0.02/block=0.05；margin_pct_warn=0.25/block=0.50；tick_value 缺失→warn-only |
| 3 | size_near_limit | lot≥0.8×min(全局 MAX_LOT_PER_TRADE=1.0 @mcp_server/guardrails.py:29, per-symbol max_lot)；lot≥3×同品种近 10 笔中位 | 任一 | — | size_cap_frac=0.8 / size_med_mult=3.0 |
| 4 | loss_chase_combo | **账户级**日亏/balance≤−1% ∧（revenge 窗 ∨ loss_streak≥3 ∨ freq≥4/h） | 触发 | — | loss_warn_pct=0.01（需把 account_daily_pnl 加进 PreflightContext，order_preflight.py:208-219 已有计算） |
| 5 | rr_sanity | SL 距 < max(3×spread, 0.5×ATR14) ∨ RR<0.25 ∨ SL 距 > 10×ATR14(M15) | 任一 | — | rr_min=0.25 / sl_max_atr=10 |
| 6 | unfamiliar_symbol | 品种 ∉ 近 14 天全量成交 ∪ 持仓；历史空→跳过不打标 | 触发 | — | familiar_days=14 |
| 7 | sentiment_conflict | 逆向 ∧ \|score\|≥0.5（TTL 15min 内，过期=无数据） | 触发 | — | sent_conflict=0.5 |
| 8 | no_sl | **死规则**（SL=0 已被硬闸门拒 @mcp_server/guardrails.py:262-276）——仅审计展示，不接收敛器 | 展示 | — | — |
| 9 | mtf_conflict | get_mtf_consensus(M15,H1) 逆向 → conflict；TF 间不一致 → mixed；一致 → aligned | conflict→CAUTION | **仅当** M15+H1 双 TF 同向逆向且各 ADX≥25，或 conflict 叠加规则 1/4 的 warn | mtf_adx_reject=25 |

**5 检查映射表（规则 → 检查 choice 的推导，实施依据）**
- data_quality ← Phase 1 硬校验（partial/insufficient）
- signal_alignment ← 规则 9（aligned/mixed/conflict）+ 规则 7 附加证据（有 warn 时 mixed）
- market_regime ← detect_regime(atr_pct, adx_value)（regime.py:47，favorable/neutral/adverse）
- risk_check ← 规则 2∨3∨4（有 block→block，任一 warn→caution，无→clear）
- execution_quality ← 规则 1∨5（同上归并）

**收敛器（fail-closed，不松于 LLM 基线）**
- [ ] risk/execution block → REJECTED；规则 9 升级条件成立 → REJECTED；conflict/partial/insufficient/任一 warn → CAUTION；全 clear → APPROVED；弃用 QuantDinger fail-open 语义
- [x] confidence = 证据完备度 × 规则余量（构造分，非概率，不伪造 probabilities；仅 Phase 3 typesafe_jev 受 min_confidence 约束）
- [x] `to_review_llm_shape()`（verdict/confidence/risk_flags≤10×60/reasoning≤500 确定性摘要/emotional_indicators=[]）+ 过 `_normalize_verdict` 同构清洗（gate:222-239）

**实现红线（工程核查确认）**
- 直 `import app.strategy.indicators`（勿 import app.strategy 包——拉全 13 个策略）；strategy 层仅依赖 numpy/pandas/app.constants，无循环
- 引擎不自读 Redis（只用 snapshot 数据，不新增故障面）

**单元测试 tests/unit/test_systemone.py**
- [ ] DataFrame fixture 沿用 conftest.make_ohlcv_df(:113) 风格；确定性金样（同输入两次输出全等）
- [ ] 收敛矩阵：risk/execution block→REJECTED；双 TF 逆向 ADX≥25→REJECTED 边界（=25/24.99）；conflict 单独→CAUTION；warn 组合→CAUTION；全 clear→APPROVED；data_quality=insufficient→CAUTION（**无 0.55 置信边界用例——那是 QuantDinger 残留，min_confidence 仅约束 Phase 3**）
- [ ] 边界：全 TF 空→provider 失败、单 TF 空→partial、周末陈旧（broker 偏移 fixture）→partial、新品种<60 根、deal time 畸形→partial、z 分位历史<100 根只 warn、JPY 品种 risk_pct（tick_value 路径）数值用例
- **Status:** complete

### Phase 2: Gate 接线 + Provider 降级链

**配置**
- [x] `manual_review_provider: Literal["local_jev","typesafe_jev","llm"] = "local_jev"`（config.py 无 Literal 先例但 pydantic v2 支持；非法值启动报错不静默回退）；全部阈值 ge/le 约束 + `model_validator`（block>warn、min_bars∈[10,200]）；启动日志 dump 生效 provider + 全部阈值（防 env 手误静默用默认）
- [x] **provider 选择运行时读 settings**（gate:42 LLM_REVIEW_TIMEOUT_S 模块级常量教训——被测试 patch；该常量名保留，测试 :287 依赖）

**Gate 改造**
- [x] `ManualOrderGate(connector, redis, ai_client, systemone=None)` 可选注入（3 参测试构造不破坏）；_review 改 provider 链：local_jev → (可选 typesafe_jev) → 现有 LLM 路径（原代码不动作兜底，仍 fail-closed）
- [x] 分诊：全 TF 空/超时/引擎异常 → provider 失败 → 降级 LLM 兜底；partial → CAUTION；sentiment=None/recent_trades 空 → 正常缺失不影响；全链失败 → REJECTED + retryable=True + AI_AGENT_ERROR
- [x] 事件语义：规则拒绝 = 正常拒绝，kind=**"systemone_rejected"** + TRADE_BLOCKED + retryable=False，**绝不发 AI_AGENT_ERROR**；infra 失败保留 kind="llm_unavailable" + AI_AGENT_ERROR（现有测试 :274 等断言不破坏）；"LLM review crashed" 措辞 provider 中性化
- [x] **降级遥测**（SRE）：loguru `logger.bind(event="manual_review_degraded", review_id, provider, reason, latency_ms)`；进程内连续失败计数（镜像 ai/circuit_breaker.py:72-84）≥3 次 → 发 `CIRCUIT_BREAKER` BotEvent（枚举已存在 models.py:88）+ Telegram 聚合告警（复用 set_alert_callback 接线 main.py:340-344）；只告警不跳过审查；恢复发 info
- [x] 审计：`review.systemone = {provider, ts, latency_ms, checks:[{name,choice,confidence,evidence≤200字符}], converge, degraded}` **合并进同一个 stored dict 一次性 _update_audit**（gate:200-202 模式，两次独立写有竞态）；provider=llm 时不写 systemone 块（回滚纯度）；review.llm 兼容形状（测试 :469 断言）；`_audit_to_dict` 剔除 shadow_llm 明细只留 {verdict,confidence,agree}
- [x] PreflightContext 增加 account_daily_pnl 字段（order_preflight.py:208-219 已有账户级计算）
- [x] 前端 2 行：REVIEW_POLL_MAX 20→50（40s→100s > 90s LLM 兜底超时，page.tsx:72 注释"25s"一并修正）；发布顺序先前端后后端（纯放宽向后兼容）
- [x] **回滚**：env 切 llm + 重启 ≤1 分钟；验证清单 = 旧测试全绿 + 历史单重放对比 verdict + review JSON 无 systemone 块

**测试**
- [x] test_manual_order_gate.py + test_manual_trading_routes.py 加 autouse fixture `monkeypatch.setattr(settings, "manual_review_provider", "llm")`（先例 :48-50；settings 非 frozen）——零改动保持纯 LLM 回归基线
- [x] 新建 tests/unit/test_gate_provider_chain.py：local 正常时 `complete_json_async.side_effect=AssertionError`；local 异常→LLM 兜底；全链失败→fail-closed + AI_AGENT_ERROR 断言；review["llm"] 兼容形状；降级计数 ≥3 → CIRCUIT_BREAKER 事件；env 切换 fixture；分诊第 3 行（sentiment=None/recent_trades 空不影响 verdict）用例
- [x] 默认值断言测试（settings 默认 = local_jev）
- [x] 注意：systemone 测试勿复用 test_manual_order_gate.py 的 SYMBOL_PROFILES 快照 fixture（会清 contract_size）
- **Status:** complete

### Phase 3（可选）: TypeSafe JEV 真实 API Provider
- [x] httpx async 镜像 QuantDinger 协议（/systemone，严格校验：选项白名单、概率和≈1、choice=argmax，qd:437-477）；8s 超时只包 HTTP 段不包执行段；min_confidence=0.55 仅在此 provider 生效
- [x] 密钥 env/vault 双轨（main.py:350-360 Secret 先例），禁入审计/日志；出网是新增 egress 面（Tailscale 需放行 api.typesafe.ai:443），代理复用 telegram_proxy_url 模式加 jev_proxy_url；简单熔断（镜像 ai/circuit_breaker.py）避免每单白等 8s；失败沿链降级
- [x] 仅在用户提供 JEV_API_KEY 时启用
- **Status:** complete

### Phase 4（可选）: Shadow 对比 + 阈值校准
- [ ] 执行后异步补跑 LLM（fire-and-forget 复用 _tasks + done callback），写 review.shadow_llm（不参与决策、不发 WS/事件），失败计数防丢样；每次补跑 `logger.info(bind(shadow_agree=bool))`；一致率用 OrderAudit SQL（review->'shadow_llm' 非空）统计，不建表不建事件
- [ ] 校准重点：conflict 误杀率（回调进场单）、spike 分位触发率、size_med_mult；N≥50 单后评估；`_audit_to_dict` 已剔除明细，校准完成可停写
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 本地规则 System One 为默认 provider，真 JEV API 可选 | 零外部依赖、秒级封顶、确定性可审计 |
| 代码默认 local_jev（两轮裁决维持） | 单人自托管、痛点即慢；autouse fixture 保护旧测试 + 启动 dump + env 一键回滚 |
| mtf conflict 单独→CAUTION，双 TF 同向逆向 ADX≥25 才 REJECTED（**v3 改判，推翻第一轮风控评审建议**） | 量化+落实核查两名评审员独立证实单 TF 共识常态性误杀回调进场单；= LLM 基线（counter to momentum 本就是 CAUTION）；REJECTED 增量条件有论证 |
| exposure_cap 改 risk_pct（tick_value 计价）+ margin_pct 双指标（**v3 改判**） | notional/equity 在 1:100+ 杠杆下常态性误触发；USDJPY notional 为 JPY 计价差 150×；tick_value 已是账户货币计价 |
| spike_chase 改滚动分位自校准（**v3 改判**） | 固定 2.5×ATR 在强趋势常态误报、4×ATR 拦合法突破；分位自校准自适应品种波动 |
| no_sl 移出收敛器，仅审计展示 | 硬闸门已拒 SL=0，死规则 |
| 9 规则 + 5 检查映射表进计划 | 落实核查 P0：v2 收敛器引用 conflict 但无规则产出，实施即卡住 |
| 数据质量三档分诊：provider 失败→降级 LLM；partial→CAUTION；全链失败→REJECTED+AI_AGENT_ERROR | 「看不清」与「有风险」与「坏了」是三类不同性质 |
| freshness 用 broker 同源时钟（tick 时间戳） | MT5 服务器 UTC+2/+3，墙钟比较则周末/周一全军 CAUTION |
| kind="systemone_rejected" 新值 | 确定性规则≠AI 结论；前端零改动；现有 kind 断言不破坏 |
| LLM 保留为降级链末端；CAUTION 不做同步 LLM 复核 | 120s 确认窗装不下 90s LLM；confirm 窗在判决后才起（gate:209-217），无侵蚀；LLM 独有价值由 shadow 异步弥补 |
| 异步任务 + 轮询不变；先前端后后端发布 | 同步会把 place_order 拖进 HTTP 生命周期；REVIEW_POLL_MAX 放宽纯向后兼容 |
| systemone.py 放 services/；直 import indicators 模块 | 与 order_preflight 同类；避免拉全 strategy 包 |
| 降级只告警不熔断跳过 | fail-closed 语义零改动；告警走既有 CIRCUIT_BREAKER 事件 + Telegram 接线 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 量化规则评审 agent 首次派发遇 user concurrency limit exceeded | 重试一次成功 |

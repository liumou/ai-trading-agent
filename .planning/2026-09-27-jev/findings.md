# Findings — 手动交易决策迁移到 JEV

## QuantDinger 的 JEV 设计（参考仓库）

来源：README_CN.md、docs/trading/LIVE_TRADING_SAFETY_CN.md、backend_api_python/app/services/ai_decision_filter.py（已下载到 /tmp/qd_ai_decision_filter.py）

1. **JEV 定义**：「System One 模型：针对整理好的状态与类型化问题输出结构化判断，而不是生成自由文本」。即快直觉判断层（对比 LLM 的慢推理层 System Two）。
2. **协议**：POST `{base_url}/systemone`（默认 `https://api.typesafe.ai/v1`，model `jev-latest`，Bearer JEV_API_KEY），body = `{model, state, questions}`；响应 `answers: {问题名: {choice, probabilities, confidence}}`。
3. **5 道固定选择题**（选项白名单 + 概率和=1 + choice 必须是最大概率项，否则整体判畸形）：
   - data_quality: sufficient/partial/insufficient
   - signal_alignment: aligned/mixed/conflict/insufficient
   - market_regime: favorable/neutral/adverse/insufficient
   - risk_check: clear/caution/block/insufficient
   - execution_quality: clear/caution/block/insufficient
4. **确定性决策收敛器**（代码，非模型）：allowed = risk_check≠block AND execution_quality≠block AND NOT(signal=conflict AND regime=adverse 且两者置信度≥阈值)。min_confidence 默认 0.55。
5. **降级链**：JEV → LLM（同一份 state，严格 JSON pass/reject，10s 超时）→ 都不可用 fail-open 放行。**仅过滤开仓**；平仓/止损/止盈/紧急操作全部绕过。
6. **审计**：每次决策写 qd_ai_decisions 表（provider/model/decision/confidence/probabilities/checks/latency_ms/request_snapshot）。
7. 配置：JEV_API_KEY/JEV_BASE_URL/JEV_MODEL/JEV_TIMEOUT_SECONDS(8s)/JEV_MIN_CONFIDENCE(0.55)。

## 本地现状（ai-trading-agent，feature/jev 分支）

来源：backend/app/services/manual_order_gate.py（全文）、backend/app/ai/prompts.py:128-、backend/app/config.py:198

1. **手动单链路**：submit_order → OrderAudit(PENDING_REVIEW) → per-account 锁 {switching gate → preflight 硬闸门 → 情绪规则（block 级内联拒，不烧 token）} → **异步 LLM 审查** → APPROVED(重验+执行) / CAUTION(PENDING_CONFIRM 120s 窗口) / REJECTED。
2. **慢点**：`ai_client.complete_json_async(ORDER_REVIEW_SYSTEM_PROMPT, build_order_review_user_prompt(snapshot), max_tokens=300)`，超时 `settings.llm_review_timeout_s` **默认 90s**（config.py:199，env LLM_REVIEW_TIMEOUT_S）。LLM 端点在本机 :8317。
3. **fail-closed**：LLM 超时/畸形 → 拒单 + AI_AGENT_ERROR 事件（与 QuantDinger fail-open 相反，本地是真金白银手动通道，更保守）。
4. **现有 snapshot 数据**：order{symbol,type,lot,sl,tp}、account{balance,equity,floating_profit,realized_daily_pnl}、positions[]、recent_trades(1天)、rule_flags（no_stop_loss/revenge_trade_window/martingale_after_loss/loss_streak/near_frequency_limit）、market{bid,ask,spread,avg_spread,sentiment}。
5. **不变式**（gate 文件头）：硬闸门结果只能被收紧不能放松；REJECTED 不可覆写；confirm 绑定持久化行 + 重跑硬闸门；shadow/paper 拒真实单；执行前重验。
6. 现有 verdict 三元：APPROVED/CAUTION/REJECTED + confidence + risk_flags + emotional_indicators + reasoning。

## 本地可复用的行情/信号资产（JEV 五问的原料）

backend/app/strategy/ 下已有：indicators.py、regime.py、mtf_filter.py、quant_signals.py、momentum_rank.py、rsi_filter.py、ema_crossover.py、kalman.py 等；data/collector.py（历史行情采集）；ai/news_sentiment.py；ai/quant_analyzer.py。→ signal_alignment / market_regime 两问可以用现成指标计算，不必新建数据管道。

## 三方评审结论（2026-09-28，风控规则 / 架构集成 / 测试边界）

### P0（不改就不能实施）
1. **规则覆盖缺口 = 放松风控**：现硬闸门（order_preflight + guardrails.py:219-332）只覆盖 lot/并发/日亏/回撤/连亏/频率/点差/SL-TP 方向/volume grid/live 授权 + 情绪层亏损后加倍（gate:594-602）。全仓无 margin/notional 检查。LLM 提示词的 overleveraging、chasing a spike、counter to momentum/sentiment、no economic sense 均无规则可依 → 必须补确定性规则清单。
2. **指标栈对数据不足静默降级**：market_data.get_ohlcv 失败返空 DF（market_data.py:70-72）；get_trend len<22 返 0（mtf_filter.py:16）；_regime_from_df len<16 返 "normal"（regime.py:154）；detect_multi_tf_regime 异常返 "normal"（regime.py:179-180）；ADX NaN（周末持平 K 线，indicators.py:144-148）→ "ranging"。不重验会把「看不清」判成「没问题」→ 引擎侧必须显式校验 min_bars（60）、最后 K 线新鲜度、逐指标 notna。
3. **延迟封顶缺失**：connector._request 超时 8s×重试 2（connector.py:25-26,48-62）→ 单次 get_ohlcv 最坏 ~26s，3 TF 串行 ~78s，比 LLM 还慢 → asyncio.gather 并发 + wait_for ≈5s 总封顶，超时/异常 = provider 失败走降级链。
4. **旧测试会全面走样**：test_manual_order_gate.py:82-87 等 mock ai_client 的测试隐含「走 LLM」→ 旧文件加 autouse fixture 固定 provider=llm（零改动保持纯 LLM 回归基线），新建 test_systemone.py + test_gate_provider_chain.py + 默认值断言。
5. **事件语义**：规则 REJECTED 是正常拒绝（TRADE_BLOCKED，retryable=False），绝不发 AI_AGENT_ERROR；仅 infra 故障（引擎崩溃/超时/全链失败）发 AI_AGENT_ERROR（gate:694-697 历史修复）。

### P1
- 收敛比 QuantDinger 更保守：conflict（ADX≥MTF_ADX_TRENDING_THRESHOLD=20 的 MTF 反向共识）单独成立即 REJECTED，不等 regime=adverse 同时成立；显式弃用 QuantDinger 的 fail-open/error_allowed。
- 分诊表（三方一致）：拉取异常/全 TF 不可用 → provider 失败 → 降级 LLM 兜底；陈旧/部分缺/NaN/deal time 不可解析 → partial → CAUTION；sentiment=None/recent_trades 空 → 正常缺失不影响；全链失败 → REJECTED+retryable+AI_AGENT_ERROR。
- OHLC 用 MarketDataService.get_ohlcv（带校验 DF，market_data.py:65-80；main.py:313 有先例），传 ctx.symbol（connector 内部 to_broker_alias，connector.py:111）。
- 代码位置：backend/app/services/systemone.py（规则引擎与 order_preflight 同类）；构造 ManualOrderGate(..., systemone=None) 可选注入，现有 3 参测试构造不破坏；输出过 _normalize_verdict 同构清洗（gate:222-239）。
- kind 用新值 "systemone_rejected"（前端 kind 仅文本标签零改动，ReviewResultCard.tsx:74 白名单只有 llm_unavailable）；现有测试断言的 kind=llm_unavailable 保留。
- 配置：manual_review_provider 为 pydantic Literal（非法值启动报错，不静默回退）；manual_review_* 前缀对齐本仓库风格；回滚纯度 = provider=llm 时不写 review.systemone 块。
- 既有缺口注明：前端轮询 REVIEW_POLL_MAX=20×2s=40s（page.tsx:71-72）< llm_review_timeout_s=90，LLM 兜底路径下前端先放弃 → 顺手提轮询上限到 100s。
- _emotion_flags deal time 解析失败静默禁用马丁闸（gate:588-592）→ 计为 data_quality=partial。
- local 的 confidence = 证据完备度 × 规则余量评分；jev_min_confidence 仅约束 typesafe_jev。
- sentiment TTL 15min（news_sentiment.py:18），陈旧 → 视为无数据。

### P2 / 分歧裁决
- 规则清单（风控评审给出可计算定义）：spike_chase（|entry−EMA20|>2.5×ATR warn / >4×ATR block，顺向 RSI>70/<30）、exposure_cap（notional/equity>10% warn、>30% block，SYMBOL_PROFILES 缺 contract_size 先 warn-only）、size_near_limit（≥0.8×MAX_LOT 或 ≥2×近10笔中位）、loss_chase_combo（日亏≤−1% 且 revenge∨streak∨frequency）、rr_sanity（SL距<3×spread 或 RR<0.25 或 >10×ATR）、unfamiliar_symbol、sentiment_conflict（|score|≥0.5 逆向）、no_sl（收敛强制≥CAUTION）。阈值全部进 config。
- 同步 vs 异步：维持异步（前端 page.tsx:326-333 已兼容终态直返，但同步会把 place_order 拖进 HTTP 生命周期并重构锁边界，收益仅省一次 2s 轮询）。
- CAUTION 不做同步 LLM 复核（120s 确认窗装不下 90s LLM）；LLM 独有价值（新颖组合模式/comment 语义）由 Phase 4 shadow 异步对比弥补。
- Phase 3：密钥不入审计 JSON/日志（main.py:350-360 Secret 先例）；简单熔断镜像 ai/circuit_breaker.py，避免每单白等 8s。
- Phase 4：fire-and-forget 复用 _tasks；review.shadow_llm 不参与决策不发 WS；失败计数防丢样；N≥50 且一致率达标后再议改默认。
- collector.load_from_db 可作 OHLC 本地兜底（collector.py:100-139），需先核实表中 symbol 命名（别名 vs 规范名）→ v1 不接。
- 默认值裁决：单人自托管系统、用户痛点即慢 → 代码默认 local_jev + 启动日志打印生效 provider + env 一键回滚 llm（架构评审的两步灰度适合多租户，此处不采用；旧测试用 autouse fixture 保护）。

## 第二轮评审结论（2026-09-28，落实核查 / 量化规则 / 工程事实核查 / SRE安全，全方位）

### P0
1. **signal_alignment/market_regime 无产出规则（落实核查）**：8 条规则全落 risk/execution，收敛器引用的 conflict 无可计算定义 → 补第 9 项 mtf_conflict + 检查↔规则映射表。
2. **mtf_conflict 单 TF 共识 + REJECTED 系统性误杀回调单（量化）**：mtf_filter.py:78-81 在 2 TF 下 1 个逆向即共识；trend=price vs EMA21±0.05%（constants.py:26-27），强趋势回调时 M15 必翻 → BUY 回调单常态 REJECTED。裁决：conflict 单独→CAUTION（=LLM 基线 prompts.py:144-145）；REJECTED 仅当 M15+H1 双 TF 同向逆向且各 ADX≥25，或 conflict 叠加 spike_chase/loss_chase warn。
3. **exposure_cap 的 notional/equity 口径对杠杆品种必然失效（三方独立确认）**：GOLD 0.01 lot notional≈26% equity，每笔 GOLD 单都进确认窗；USDJPY contract_size=100000 → notional 是 JPY 计价，直接除 USD equity 差 ~150×（工程核查）。修正：risk_pct=潜在亏损/equity，潜在亏损=|entry−SL|/tick_size×tick_value×lot（tick_value 已是账户货币计价，tick_value 缺失→warn-only）；辅指标 margin_pct=初始保证金/equity（按资产类分级）。
4. **spike_chase 常态性误报（量化）**：强趋势中 |close−EMA20|/ATR 常态处于 1.5~3、RSI 可在 70-85 逗留数十根 → 2.5×ATR+顺向 RSI 把顺势单打成常态 warn，4×ATR block 拦合法突破单。修正：滚动分位自校准（近 200 根 z 分布，>90 分位 warn、>99 分位且连续≥4 根同向 K 线才 block；历史<100 根只 warn 不 block）。

### P1
5. no_sl 是死规则：SL=0 已被硬闸门拒（mcp_server/guardrails.py:262-276）→ 仅保留审计展示，不接收敛器。
6. loss_chase_combo 日亏口径错位：ctx.daily_pnl 是单品种日亏（order_preflight.py:201），账户级未进 ctx（:208-219 有计算）→ 把 account_daily_pnl 加进 PreflightContext。
7. size_near_limit 微手账户过敏：中位 0.01 时 0.02 lot 即 2× → 改 ≥3× 同品种中位 + 0.8×min(全局 1.0, per-symbol max_lot)；MAX_LOT_PER_TRADE 实际在 mcp_server/guardrails.py:29。
8. rr_sanity 误报低点差外汇短线：改 SL 距<max(3×spread, 0.5×ATR14) 双下限。
9. unfamiliar_symbol 数据口径不可达：_recent_deals 按品种过滤（gate:558，桥端严格过滤 mt5_bridge/main.py:840-843）→ 一次全量 get_history(days=14)（symbol=None 不过滤，connector.py:271-277 支持本地切分复用（同品种子集给 size/revenge 检查，全量给 unfamiliar）；历史空=数据不足不打标。
10. freshness 时钟基准：MT5 服务器时区 UTC+2/+3，与 _utcnow() 直接比较则每个 bar 都"陈旧" → 同源时钟（最后 K 线时间 vs bridge tick 自带时间戳，同为 broker 时间）；tick 不可得才回退 _utcnow() + ×N 根 bar 周期宽容度；测试补 broker-UTC 偏移 fixture。
11. 降级无告警：local 引擎每单异常 → 静默走 90s LLM 无感知 → loguru bind 结构化日志（event/review_id/provider/degraded/latency/verdict）+ 进程内连续失败计数 ≥3 次发 CIRCUIT_BREAKER BotEvent（枚举已存在 models.py:88）+ Telegram 聚合告警（复用 llm_circuit_breaker.set_alert_callback 接线 main.py:340-344）；只告警不跳过审查。
12. 空 DF 分诊闭环：get_ohlcv 失败返空 DF 而非抛异常（mt5/market_data.py:70-72）→ 全 TF 空=provider 失败→降级链；单 TF 空=partial→CAUTION。
13. data_quality=insufficient 收敛出口显式→CAUTION。
14. config 跨字段校验：pydantic ge/le + model_validator（block>warn、min_bars∈[10,200]≤count）+ 启动日志 dump 全部生效阈值。
15. 收敛矩阵测试有 QuantDinger 残留：conflict+adverse、0.55 置信边界对 local 无意义（confidence 是构造分非概率）→ 改为 conflict 单独→CAUTION、双 TF 逆向→REJECTED 边界、全 clear→APPROVED。
16. min_bars：请求 120 根、校验 ≥60（broker 少回 1 根不误触发）。
17. 审计瘦身：evidence 单条 ≤200 字符（产出端截断）；_audit_to_dict 剔除 shadow_llm 明细只留 {verdict,confidence,agree}；list_reviews 50 行全量 ~300KB 的问题随之缓解。

### 工程事实核查（v2 引用对码结果，行号漂移≤2）
- market_data 实为 backend/app/mt5/market_data.py（get_ohlcv 带 validate，:65-78）；to_broker_alias 定义在 symbol_resolver.py:10（connector.py:110-111 只是调用点）。
- PreflightContext 另有 base_lot 字段；contract_size 四品种全有（config.py:17/33/49/65 + DB 加载 symbol_config_service.py:37），"缺 contract_size"仅 DB NULL 时发生，保留为防御分支。
- conftest.py 有 make_ohlcv_df(:113)/mock_ai_client fixture；test_manual_order_gate.py 有 3 个 autouse 先例（:29/:39/:48，含 settings.llm_allow_live patch 先例 :48-50，settings 非 frozen 可 monkeypatch）；该文件 SYMBOL_PROFILES 快照 fixture 会清 contract_size——systemone 测试勿直接复用其 GOLD profile。
- gate:42 LLM_REVIEW_TIMEOUT_S 模块级常量被测试 :287 patch → **provider 选择必须运行时读 settings**（不可模块级缓存）；该常量名保留。
- systemone.py 必须直 import app.strategy.indicators（勿 import app.strategy 包——会拉全 13 个策略模块）；services→strategy 方向无循环（engine.py:95-96 有先例）。
- asyncio.gather 先例充足（order_preflight.py:174 本身、scheduler.py:364/459/591）。
- 前端 page.tsx:71-72 REVIEW_POLL_MS=2000/MAX=20，注释"25s 预算"已过时需顺手改；终态直返分支 :327-331 存在。
- test_manual_trading_routes.py 用 MagicMock gate 挂 app.state——路由层零改动成立。
- deal 字段 lot/profit/time/symbol 齐全（mt5_bridge/main.py:846-854）；sentiment TTL=900s（news_sentiment.py:18/:124-129）；guardrails:trade_results 三通道都写（engine.py:1460/circuit_breaker.py:329/position_close.py:122）。

### SRE/安全
- 注入面收窄是净收益（规则引擎纯数值运算 + ORM + _sanitize_comment）；TypeSafe JEV 出网是新增 egress 面（Tailscale 需放行 api.typesafe.ai:443，代理复用 telegram_proxy_url 模式加 jev_proxy_url）。
- 发布顺序：先前端（REVIEW_POLL_MAX 20→50 纯放宽）→ 后端重启；回滚验证清单加「历史单重放对比 verdict」。
- confirm 连续性确认无碍：confirm_expires_at 在判决后才写（gate:209-217），120s 窗完整；SRE 担忧被代码事实解除，仅需轮询上限修复（已计划）。
- 实现红线：review.systemone 必须合并进同一个 stored dict 一次性 _update_audit（两次独立写有丢字段竞态，gate:200-202 模式）。
- Redis 零新增键、Postgres 零迁移确认；pandas 计算勿扩大 count（事件循环阻塞量级可忽略）。

## Phase 3 实施与真实 API 验证（2026-10-09）

### 落地内容
- `backend/app/services/typesafe_jev.py`：TypesafeJevProvider（QuantDinger 协议镜像）
  - POST `{base_url}/systemone`（Bearer key），body={model, state, questions}；5 问严格校验（选项白名单/概率和≈1/choice=argmax/confidence∈[0,1]）
  - 收敛：risk/exec block → REJECTED；conflict∧adverse 双高置信 → REJECTED；caution 档 → CAUTION；置信 floor~min 之间 → CAUTION；全 clear 且 ≥min → APPROVED
  - **低置信两段分诊**（实测 free 模型置信普遍偏低，单一 min 阈值会让 JEV 永不生效）：`< conf_floor(0.30)` → 降级链；`floor~min_confidence(0.55)` → CAUTION；≥min → APPROVED
  - 熔断：连续失败 ≥3 → 冷却 300s 跳过本 provider（避免每单白等 8s）；恢复自动重试
  - state 携带 **context.market_evidence**（M15/H1 的 trend/adx/atr_pct/rsi/macd_hist/momentum_pct）——实测缺证据时 exec 置信仅 0.32，且 signal_alignment 无法判断
- 网关 provider 链泛化：`_systemone_providers()` 按 provider 配置排序 —— local_jev → typesafe → llm 或 typesafe → local → llm，首个有效判决者决定；per-provider 超时预算（typesafe = fetch 5s + JEV 8s）
- 配置：manual_review_typesafe_*（api_key/base_url/model/timeout/proxy/circuit 阈值冷却/conf_floor）；密钥只存 .env（gitignore 已覆盖），不进审计/日志/前端（有专门测试锁定）
- 令牌：`.env` 写入 MANUAL_REVIEW_TYPESAFE_API_KEY + BASE_URL(https://opencode.ai/zen/v1/systemone) + MODEL(jev-1.13-free) + MANUAL_REVIEW_PROVIDER=typesafe_jev（JEV 主审，local 秒级第二链，LLM 兜底）

### 真实 API 验证（opencode.ai/zen，1 次/场景）
| 场景 | 结果 |
|------|------|
| 无行情证据 | risk=0.80 / exec=0.32 → 低于 floor 降级（证明证据必需） |
| 基础证据（trend/adx/atr） | risk=0.41 / exec=0.39 → 降级 |
| 证据+数据一致 | **CAUTION / conf 0.35 / 1.1s**；signal_alignment=aligned(0.88)、market_regime=favorable(0.53)、data_quality=partial(0.55)、exec=caution(0.53) |
| 延迟对比 | JEV 约 1.1s vs LLM 兜底 90s（约 80×） |

**关键观察**：模型确实读懂了 market_evidence（signal_alignment 0.88 与证据趋势一致）；free 模型风险置信保守（0.29~0.41），因此 CAUTION 是正确落点（人工一键确认，firewall 不放松）。data_quality=partial 可能与 state 缺新闻/情绪证据有关 —— 后续可补 sentiment 进 evidence（snapshot.market.sentiment 已有，可提升 data_quality 置信）。

### 测试
- tests/unit/test_typesafe_jev.py（14 用例）：判决映射/两段置信分诊/畸形应答四类/网络故障+熔断/state 形状/密钥不泄漏
- tests/unit/test_gate_provider_chain.py 增 3 用例：typesafe 第二链（local 失败→JEV 判）、typesafe 失败→LLM、typesafe 主审（local 引擎指纹 get_symbol_spec 未被调）
- 测试隔离：gate 链测试 autouse 清空真实 JEV key（.env 有真 key，local 失败降级路径会真打外部 API）
- 全量：956 passed / 7 failed（全部既有 multi_agent 非回归）

## 真实数据端到端测试（2026-10-09，报告：.planning/2026-09-27-jev/jev-realdata-report.md）

### 测试形态
- **真 API + 真行情**：真实 JEV 端点（opencode.ai，jev-1.13-free）共 6 次真实调用（地板 0.30/0.15 两次对照 × 3 场景）；行情来自真实 MT5 Bridge（XM Global 336773771，GOLD_）——120 根 M15/120 根 H1/253 笔 14 天真实成交/真实账户 $14,554
- 桥在测试窗口内间歇 502/ReadTimeout（上游终端不稳），行情用 16:50:31 真实快照冻结保证三场景一致；只读、未下单
- 三场景：S1 顺势 ~1% 风险 / S2 逆势 / S3 2.0 手超大手数

### 结果（地板=0.15 生效配置）
| 场景 | local | JEV | 延迟 |
|------|-------|-----|------|
| S1 顺势 BUY 0.24 | APPROVED | **CAUTION** conf 0.24（risk=caution, regime=adverse） | 1.35s |
| S2 逆势 SELL 0.24 | CAUTION | **REJECTED** conf 0.20（conflict+adverse+risk=block） | 1.09s |
| S3 2.0 手 | CAUTION | **REJECTED** conf 0.36（risk=block） | 1.23s |

### 实证结论（对上游代码有直接影响）
1. free 模型置信带宽 0.17~0.36 且与风险不单调（最危险单 0.36 > 普通单 0.24）→ **决策信号在 choice 不在 confidence**
2. 地板 0.30 会把模型的风险否决吞掉（S3 的 block 仅 0.33 险过）→ **地板定 0.15**（已写入 .env MANUAL_REVIEW_TYPESAFE_CONF_FLOOR=0.15）；min_confidence 保持 0.55
3. JEV 增量价值实证：S2 逆势与 S3 超高杠杆 local 只到 CAUTION、JEV 能到 REJECTED（local 敞口规则按 SL 距离算 risk_pct 抓不到 27× 名义杠杆——margin_pct 仍等桥暴露 leverage）
4. S1 上 JEV 比 local 保守（真实 H1 RSI=73.5 超买 → regime adverse），读到 local 未建模的过热状态
5. data_quality 恒 partial(0.55)：state 缺新闻/情绪证据 → 后续把 snapshot.market.sentiment 并入 market_evidence
6. 阈值定论需 Phase 4 shadow 积累 N≥50 单；单时点单品种样本不足以定论

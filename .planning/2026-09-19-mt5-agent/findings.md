# Findings & Decisions — MT5 手动交易 + Agent 风控防火墙

## Requirements

- 手动下单(市价/挂单/撤单/改单),每次下单前 AI Agent 审查(合理性/风控/情绪化交易),可拦截。
- 决策:REJECTED 硬拦截不可强制;LLM 失败 fail-closed;完全沿用 AI 通道 rollout 门禁;CAUTION 需二次确认;独立 /trading 页。

## Research Findings

### MT5 Bridge / 执行链(2026-09-19 探索)
- Bridge `mt5_bridge/main.py`(598 行):`POST /order`(仅市价:365)、`PUT /position/{ticket}`(:426)、`DELETE /position/{ticket}`(:457)、`/history`(:566)等;**无任何挂单端点**——挂单/撤单/改挂单全需新增。
- **MT5 SDK 正确性(经 MQL5 官方文档验证)**:
  - 挂单成功 retcode 是 `TRADE_RETCODE_PLACED=10008`(市价 DONE=10009)——照抄 DONE 检查会把成功挂单判失败→用户重试→重复挂单。
  - 挂单 type_filling 硬编码 IOC 会被拒(10030 INVALID_FILL);需按 `symbol_info().filling_mode` 位掩码推导,回落 `ORDER_FILLING_RETURN`。
  - TRADE_ACTION_MODIFY 改挂单需先 `orders_get(ticket=)` 回填 type_time/expiration 整包回传,否则 10016/10022。
  - orders_get 字段与 positions 完全不同(ORDER_TYPE_BUY_LIMIT=2..5、price_open、state)。
  - `mt5_bridge/tests/conftest.py` mock 缺:orders_get、TRADE_ACTION_PENDING=5/MODIFY=7/REMOVE=8、ORDER_TYPE_*_LIMIT、TRADE_RETCODE_PLACED;且 ORDER_FILLING_IOC=2 是错值(真实=1,2 是 RETURN)。
  - 现有 `/order` type 处理(main.py:393)非 "BUY" 一律按 SELL;负 lot 被 clamp 成最小手数静默放行——新端点必须 pydantic Literal/校验拒绝。
- connector `_request` 对 Timeout/ConnectError/ValueError 一律重试 2 次 + executor.place_order 再重试 3 次 → **下单超时重试=双开仓风险**。
- executor 唯一消费者是 BotEngine;新挂单方法不加 executor 封装(死代码)。

### 现成风控资产(可复用)
- **闸门序列**在 `mcp_server/tools/broker.py::place_order`(:50-350):resolve_symbol→并发拉 account/positions/tick→rolling avg spread(Redis `guardrails:spread_history`)→`TradingGuardrails.validate_order`→volume grid 归一(`app/services/symbol_validation.py:normalize_lot_to_volume_grid`)→rollout(Redis `guardrails:rollout_mode` 为准)→`llm_allow_live`→`switching:in_progress`→comment 清洗 27 字符。每条注释都是真实事故教训——**必须抽共享模块复用,禁止复制**。
- `TradingGuardrails`(`mcp_server/guardrails.py`):validate_order(symbol, lot, order_type, current_positions, account_balance, daily_pnl, spread, avg_spread, entry_price, sl, tp)->GuardrailResult;MAX_LOT=1.0、并发 3/5、日亏 3%、频率 5/h、MICRO_MAX_LOT=0.01。
  - record_order_opened():小时计数+last_trade_time;record_trade_closed(is_win):`guardrails:trade_results:{date}` rpush。
  - check_rollout_mode_async(lot) 只返回"将被 cap"message,**cap 由调用方执行**;shadow/paper 对真实单 allowed=False。
- CircuitBreaker(`app/risk/circuit_breaker.py`):key 带账号前缀 `circuit:acc:{login}:`;record_trade_result/get_daily_pnl(per-symbol key)。

### AI/LLM 层
- `AIClient.complete_json_async`(`app/ai/client.py:59`)失败返回 None;**无 timeout 参数**,provider 层 `settings.llm_timeout` 默认 120s;Claude 路径完全无 timeout → 15s 预算只能 gate 层 `asyncio.wait_for` 强制。
- **同步 LLM 不可行**:前端 axios 默认 timeout=10000ms(lib/api.ts:5)→ 同步审查+下单必然双重下单窗口。改为 **202+轮询**(chat_runs/agent_chat 先例)。
- LLM 熔断只挂 OpenAI 路径(provider.py:173-175),ClaudeSDKProvider 不经过熔断器。
- 畸形 JSON→None(provider.py:278-290)→fail-closed 语义正确;verdict 需白名单校验。
- risk_analyst(`mcp_server/agents/risk_analyst.py`)已有"评估拟议交易"prompt 同构(自由文本非 JSON)。
- **BiasGuard 是死代码**(无调用点,内存态);trade_accountability 是事后分类器——情绪规则自建,数据源用 bridge `/history`(天然账号隔离;trades 表只记引擎成交,手动单盲区)。

### 数据模型
- OrderAudit(`app/db/models.py:257-274`)无 account_login、无 source;唯一写入方 engine._log_order_audit,**AI 通道不写**。决定:**扩 OrderAudit**(加 source/account_login/order_kind/order_price/review JSON),不建 order_reviews 新表;status 扩展生命周期值(字符串列,无需 ALTER TYPE)。
- Trade:ticket+account_login 复合唯一(H4);只记引擎成交。
- BotEventType 是 PG 原生 Enum;决定复用 TRADE_BLOCKED/TRADE_OPENED/AI_AGENT_ERROR + `[Manual]` 前缀,不加新值。
- **Alembic 当前 head = `z0a1b2c3d4e5`**(单线性链)。
- 单测不跑 alembic:conftest SQLite+create_all。

### 存量漏洞(评审确认)
- `routes/positions.py` DELETE 平仓:直连 executor,**零检查**→ Phase 0 修复为 close_position_gated。
- 引擎 `sync_positions`(engine.py:1209)跟踪**所有**持仓(含手动),消失时 `_handle_closed_trades` 记账 → 手动平仓路由**不得重复记账**(有引擎品种归引擎,无引擎品种 gate 记账)。
- broker `_switching_in_progress` Redis 异常 fail-open;手动通道必须 fail-closed。
- switching TTL 仅 120s;drain 仅 sleep(1.0)——gate 需 bridge 调用前一刻复查。
- spread_history key 不分 symbol→跨品种污染;resolve_symbol 失败 fail-open+SYMBOL_PROFILES miss 绕过 volume 归一→共享 preflight 改 fail-closed。
- broker.modify_position 找不到 ticket 静默放行(:373-394)→ 手动路由先做 ticket 归属校验。
- SL 拉宽 ≤×2 基数是当前 SL(几何漂移),SL=0 检查被跳过,TP 无校验→改以 entry 为基数+上限+次数预算。
- init_broker 自建独立 connector(≠app.state.connector)——gate 必须复用 app.state.connector、直接 TradingGuardrails(redis) 构造。

### 前端
- Next.js 16 App Router + Tailwind v4 + shadcn(base-nova) + zustand + next-intl。
- **无任何下单 UI**;lib/api.ts axios timeout 10s(per-call 可覆盖);WS channel:price_update/position_update/bot_event/sentiment_update/status_update。
- i18n 免注册(readdirSync 动态加载 messages/{zh,en}/*.json)。
- accounts 页是页面模板(用 alert,新页面用 lib/toast.ts);Dialog 参考 symbols 页。
- symbol-tabs 的 activeSymbol 全局单例——/trading 用本地 state。
- 持仓表 dashboard 内嵌,抽 PositionsTable 统一 WS 订阅。
- 通知中心通用渲染 event_type,新枚举不破坏(本方案不加新枚举)。

### 测试基建
- conftest:fakeredis redis_client、AsyncMock(spec=MT5BridgeConnector)、SQLite 内存 db_engine。
- guardrails 用 fakeredis 真跑(test_guardrails.py);test_account_switch.py 全依赖构造模式可套用。
- 既有非回归失败(改动前已验证):test_multi_agent 7、test_engine.py 4、test_ml_barrier_validation 1。

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| 硬闸门抽共享 `app/services/order_preflight.py` | broker.py 与 ManualOrderGate 共用;复制=安全关键代码双真相源;test_guardrails 等锚定回归 |
| LLM 审查 202+轮询(异步) | complete_json_async 无 timeout、provider 120s、axios 10s——同步必然双重下单窗口 |
| 扩 OrderAudit 不建新表 | 与 OrderAudit 职责重叠;AI 通道顺带补齐审计;status 单生命周期列防漂移 |
| confirm 绑定 review_id | 杜绝"确认 A 单执行 B 单"参数篡改;PENDING_CONFIRM TTL 120s→EXPIRED |
| 改挂单=全流水线重审 | 堵死"远价挂单过审→改价逼近市价"绕过 |
| SL 拉宽以 entry 为基数 | 现以当前 SL 为基数几何漂移(×2 五轮=32 倍);SL=0 也要校验;TP 方向校验 |
| connector 下单禁歧义重试 | 超时重试=双开仓;只有 ConnectError(未发出)可重试 |
| per-account asyncio.Lock | guardrails check-then-act 非原子;LLM 在锁外,执行前重验 |
| switching 门禁手动通道 fail-closed | 手动真金通道,Redis 异常→503 拒绝 |
| 情绪规则自建 | BiasGuard 死代码/trade_accountability 事后内存态;数据源 /history+guardrails 计数 |
| 手动单独立 MANUAL_MAGIC_NUMBER | 事后归因区分来源 |
| 迁移挂 head z0a1b2c3d4e5 | 单线性链,挂 b8c9d0e1f2a3 会双 head |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| get_persisted_rollout_mode Redis 无值回落 env 默认 shadow | 测试成功路径显式 set `guardrails:rollout_mode=live` |
| CircuitBreaker key 用 get_canonical_symbol(券商名),测试环境恒等返回原名 | 测试断言按同口径计算 key |
| 单跑通过的 test_backtest_consistency::test_gold_reads_profile 在全量套件失败 | 待查:疑似 SYMBOL_PROFILES 全局态测试污染,需 stash 基线确认 |

## Resources

- MQL5 Trade Server Return Codes: https://www.mql5.com/en/docs/constants/errorswarnings/tradereturncodes
- MQL5 ORDER_FILLING: https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties
- 评审报告全文:见会话记录(安全 C1-C4/H1-H6/M1-M4、架构 C1-C2/H1-H5/M1-M5、可行性 C1-C3/H1-H6/F1)

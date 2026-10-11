# 复盘功能计划评审记录（串行多路）

## 第一路：架构评审（2026-10-10）

### 结论
方案成立可推进，但必须先修正 3 项：
1. **CRITICAL-C1 手动单复盘链路缺失**：手动单（MT5 通道）不写 Trade 表、trade_id=None。需 trade_id nullable + (ticket, account_login) 自然键 + 手动单基础输入组装器 + 前端手动单行可点复盘。
2. **CRITICAL-C2 LLM 执行模型未定义 + 并发幂等缺失**：AIClient 无内置超时；需 DB 原子状态机（pending→running→completed/failed）+ asyncio.wait_for 超时 + 独立短连接 session + 启动 recover 悬挂 running。复刻 chat_runs.py claim() 范式。
3. **HIGH-H1 行情窗口数据源未闭环**：ohlcv_data 表无定时采集，老订单窗口大概率无数据。需数据可用性检查 + 从 Bridge 按需拉取回填 + 显式降级（绝不伪造）。

### MEDIUM（次要必改）
- M1: classification 与 reasoning_correct 可能矛盾 → LLM 只输出 reasoning_correct + 根因 + lessons，classification 由代码调 TradeAccountabilityTracker.evaluate() 确定性推导
- M2: 批量补齐漏手动单 + 无成本控制 → 加每批/每日上限，计入 agent call 额度
- M3: 强制重审覆写无历史版本 → 保留 review_history
- M4: max_tokens 256 不足 → 1000+
- M5: 白名单校验缺字段截断/类型强校验 → 仿 _normalize_verdict
- M6: prompt 清洗需覆盖嵌套 JSON（pre_trade_snapshot 递归清洗）
- M7: 时间一致性 naive UTC

### LOW
- L1: 前端新增复盘独立类型
- L2: UX 区分规则摘要 vs AI 深度复盘
- L3: trade_reviews 加 classification/symbol/account_login/open_time 索引便于周报月报
- L4: 多账号隔离（(account_login, ticket) 唯一键）

（待续：第二路 安全评审）

## 第二路：安全评审（2026-10-10）

### 结论
6 项必须改（2C+3H+量化+审计归属），可接受项已验证良好。

### CRITICAL
- **C-1 复盘触发无专用成本/配额控制**：全局限流按 HTTP 计数，复盘是重 LLM 操作。需 Redis 每日每用户配额（trade_review:daily:{username}:{date} INCR+EXPIRE，超限 429）、batch 硬上限（≤50）计入同一配额、独立限流、服务端原子幂等
- **C-2 classification 不可直接信任，必须服务端推导**：被注入数据可诱导模型输出 skilled_win → 错误强化失败模式。服务端用 pnl>0 + reasoning_correct（严格布尔，拒绝 "true"/1/"yes"）确定性推导四分类；模型 classification 仅交叉校验，不一致 fail-closed + flagged，绝不入库为正式结论

### HIGH
- **H-1 Prompt Injection 需分层隔离+最小字段**：`<TRADE_DATA>` 定界 + system prompt 强声明"数据区为惰性数据" + JSON 递归清洗 + 最小必要字段白名单（不发全字段）+ 输出自由文本入库前截断/剥离指令形分隔符
- **H-2 fail-closed 覆盖不完整**：asyncio.wait_for 只包 LLM 调用（防挂 120s）、202 异步 + 轮询、状态机 UPDATE...WHERE status 幂等、任一校验失败置 failed 绝不回退默认分类
- **H-3 数据最小化与 provider 配置风险**：只发最小字段、account_login 脱敏尾号、trade_reviews 记 provider_name、openai_compat 时打日志提示数据外发第三方

### MEDIUM
- M-1 缺失审计日志：触发/重审/batch 记 log_audit(action="trade_review.trigger", actor=username, resource=trade:{id}, ip)
- M-2 越权/IDOR：by-trade/{trade_id} 可按 trade.account_login 归属过滤，防任意登录者枚举他人复盘
- M-3 存储/渲染：前端无 dangerouslySetInnerHTML（已验证安全）；自由文本保持文本化，入库前清洗

### LOW
- L-1 confidence<0.5 前端低置信徽标 + 服务端 flagged
- L-2 batch 逐单独立 fail-closed，返回 {created, failed} 汇总

（待续：第三路 实现工程评审）

## 第三路：实现工程评审（2026-10-10）

### 核实事实
- Alembic HEAD = `c1d2e3f4a5b6`，新迁移 down_revision 必须接它
- 三路 schema 双路径：PG 走 alembic / SQLite 测试走 create_all / dev 走 lifespan schema_stmts —— **必须同步，否则漂移**（已有 schema_ddl.py 先例）
- 手动单不进 Trade 表（Trade( 实例化仅在 engine.py 4 处）；trade_id nullable + (ticket, account_login) 自然键硬需求
- 行情窗口接口已闭环：`connector.get_ohlcv_range(symbol, timeframe, from, to)` + `MarketDataService.get_ohlcv_range -> DataFrame` + bridge `/ohlcv/{symbol}/history`（上限 50000 bars）
- uvicorn 默认单 worker → 多 worker create_task 坑当前不成立；但代码仍写"DB 原子抢单 + 可恢复"防未来扩容

### BLOCKER（实现前必须定型）
- **B-1 唯一约束**：SQLAlchemy 无 PartialUniqueIndex；partial index 是 PG-only、SQLite 不支持（测试挂）。方案：Model 只声明普通索引 (ticket, account_login)；migration 用 postgresql_where 建两个 partial unique index；应用层去重 + IntegrityError→409；SQLite 测试跳过 DB 约束只依赖应用层逻辑
- **B-2 手动单幂等**：service 层 trigger() 先 SELECT 非 failed 幂等返回，并发靠 PG 唯一索引 + IntegrityError 兜底（非单靠 SELECT 预判）
- **B-3 行情回填不能破坏 ohlcv upsert 语义**：回填走 collector.HistoricalDataCollector.collect()（独立 session + naive UTC + ON CONFLICT），复盘服务只读提取绝不自行 INSERT；提取不到→显式降级不伪造
- **B-4 递归清洗**：_clean 只处理单行字符串；嵌套 JSON（pre_trade_snapshot）需抽公共 `app/ai/sanitize.py`（借鉴 chat_runs.py:33 的 depth≤12/list≤200/secret 打码）

### HIGH
- H-1 trade_reviews DDL 抽 TRADE_REVIEWS_DDL 进 schema_ddl.py，migration + lifespan 共用；review 用 JSON（可 with_variant JSONB）
- H-2 完整列清单：id/trade_id NULL/ticket/account_login/symbol/classification/status/review/review_history/error/provider_name/confidence/worker_token/lease_until/open_time/created_at/updated_at/attempt_count；**统一以 (ticket, account_login) 为物理自然键**，trade_id 仅冗余关联列
- H-3 worker 复刻 chat_runs lease/recover（recover 启动清理悬挂 + claim UPDATE...WHERE + run_claimed 心跳 + wait_for 包 LLM + 独立短连接）；lifespan 接线仿 chat worker
- H-4 时区：open_time 存前 parse_bridge_time_to_naive_utc() 归一（bridge 返回带 +00:00 ISO）
- H-5 触发入参二选一 {trade_id} 或 {ticket, account_login}；IDOR 按 account_login 过滤
- H-6 配额/超时进 config.py（trade_review_max_batch=50/daily_quota/timeout_s/lease_s），白名单/枚举进 constants.py

### MEDIUM
- M-1 金额 round(x,2)、价格按 SYMBOL_PROFILES digits
- M-2 字符串截断统一走 bounded_text（chat_runs.py:293）
- M-3 前端 key 改 `${t.source}-${t.ticket}`（手动单 id=None 会 React key 冲突）；抽独立组件 TradeReviewDialog；POST 202 + 2s 轮询
- M-4 翻译：独立 messages/{zh,en}/tradeReview.json（两侧 key 对齐）
- M-5 测试：AIClient 构造支持 provider 注入 mock；并发幂等复用 test_chat_runs.py:50-66 范式

### LOW
- L-1 updated_at 手动更新不引入 onupdate
- L-2 open_time 索引先建，报表等批量补齐后
- L-3 保留 attempt_count/provider_name 审计用，不建事件流表
- L-4 错误文案走 translate_detail i18n
- L-5 review JSON 只存结构化结论不存原始 prompt/全字段

（待续：第四路 产品需求评审）

## 第四路：产品需求评审（2026-10-10）

### 结论
方案解决"看懂每一单"，但用户要的是"不再犯"——MVP 补齐行为信号与四分类文案，第二步跨单模式统计让用户看见重复错误，长期教训库×开仓提醒才是终局。

### 必须做（MVP 硬核）
- **P1 单笔深度复盘核心链路**：持久化硬性要求——历史页显示"已复盘"状态 + 分类徽章，可回看（复盘成为可积累资产而非一次性弹窗）
- **P2 四分类中文文案 + 反直觉概念讲透**：每个分类配"徽章文案 + 一句话解释 + 行动指引 + 颜色"四件套（skilled_win 绿/判断正确盈利/继续保持；correct_process 蓝/判断正确亏损/别因单次亏损动摇；lucky_win 琥珀/判断错误盈利/别把运气当实力；real_mistake 红/判断错误亏损/必须改正），各配一个动词（保持/别慌/警惕/改进）
- **P3 手动单复盘**：用户亲手做的决策最需要问责；手动单行同样显示复盘按钮，trade_reason/OrderAudit 注释作输入
- **P4 行为信号进复盘判断**：持仓时长异常 + 纪律违规 flag（复仇/马丁/连亏，数据现成：discipline_gate + OrderAudit + post_trade_analysis.duration_hours）→ AI 明确回答"是否情绪化操作"
- **P5 配额控制 + 失败重试 + 超限友好提示**：失败显示"复盘暂不可用请重试"、超限显示"今日次数用完明天再来"

### 建议做（MVP 后第二批）
- P6 跨单模式认知：弹窗内"近 30 天第 N 次 real_mistake，根因 X 出现 M 次"（从 trade_reviews 聚合，无需额外 LLM）
- P7 批量复盘当前筛选（≤50 计入配额，逐单独立失败，返回成功/失败汇总）
- P8 "待复盘"入口提示（顶部软提示 + 未复盘按钮高亮 + 已复盘分类徽章）
- P9 复盘记录列表页（按分类筛选回看 + 分类分布 + 最常见错误 top5）

### 可选
- P10 用户心态备注；P11 自动复盘新单（开关默认关）；P12 复盘完成推送；P13 教训标星

### 不要做
- 一键复盘全部历史（成本失控 + 旧单数据缺失）
- 复盘写回 post_trade_analysis（会被覆写）
- 信任 LLM 直接输出 classification（服务端推导）
- 为复盘引入 MCP 多轮对话

### 长期预留
- trade_reviews 的 lessons/根因标签**字段化存储**（非纯文本），为未来"教训库×开仓前提醒"（preflight/manual_order_gate 命中历史教训给 warning）预留——本次表结构就要支持

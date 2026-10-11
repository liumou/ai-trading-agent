# Task Plan: 历史订单复盘功能

## Goal

在历史记录（`/history`）页面增加订单**AI 深度复盘**功能：对历史订单（**含 bot 自动单 + 手动单**）进行复盘分析——为什么亏损/盈利？是什么原因导致的？是做对了还是做错了？应吸取什么经验？复用并接线 `TradeAccountabilityTracker` 四分类问责框架（**服务端确定性推导**），通过**行为信号 + 根因标签 + 经验教训 + 改进建议**帮助用户防止再犯同样错误。复盘结果**持久化可回看**，为跨单模式统计与长期教训库预留字段化结构。

## Next Step

全部完成。commit `cf5c4be` 已落在 `feature/jev`（ahead 1，待推送）。剩余：可选推送 + 依赖 MT5 bridge 运行时的手动验证（bot/手动单触发 → 徽章 → 查看/重审 → 批量 → 汇总）。

## Current Phase

全部 Phase complete（1-6 完成）

## Phases

### Phase 1: 需求与现状分析 ✅
- [x] 定位历史订单数据结构（Trade 模型、MT5 合并通道、/api/history/trades 字段）
- [x] 定位现有 AI 分析服务（app/ai/ client/context_builder/prompts、analyze_recent_trades）
- [x] 识别复盘需要的行情上下文（入场/离场价格 vs 当时趋势、策略信号）
- [x] 识别复用组件（TradeAccountabilityTracker 四分类、AIClient、prompts 模板、_clean 防注入）
- [x] 明确复盘模式：逐单手动触发（首选）+ 批量补齐（可选）
- [x] 记录到 findings.md
- **Status:** complete

### Phase 2: 规划与结构 ✅
- [x] 确定复盘方案：逐单 AI 复盘（按需触发），复用四分类问责 + AIClient + 独立存储表
- [x] **四路评审**：架构 / 安全 / 实现工程 / 产品需求（记录于 reviews.md）
- [x] 吸收全部 CRITICAL/BLOCKER/必须做项进 Phase 3-6 细化
- [x] 写入 task_plan.md 细化 Phase 3-6，提交用户审批
- **Status:** complete

### Phase 3: 后端实现（含评审定稿项）

**Status:** complete（2026-10-11 全量回归 1180 通过；9 失败均环境配置既有，与本功能无关）

**3A. 数据模型 & 迁移（实现前必定型）**
- [x] 新表 `trade_reviews`（**完整列清单，评审 B-1/H-2**）：
  - `id` BIGINT PK；`trade_id` BIGINT NULL（bot 单关联，冗余列）；`ticket` BIGINT NOT NULL；`account_login` VARCHAR(32) default '0'；`symbol` VARCHAR(20)
  - `classification` VARCHAR(32) NULL；`status` VARCHAR(16) default 'pending'；`review` JSON NULL；`review_history` JSON NULL；`error` TEXT NULL；`provider_name` VARCHAR(32)；`confidence` FLOAT
  - `worker_token` VARCHAR(36) NULL；`lease_until` DATETIME NULL；`attempt_count` INT default 0
  - `open_time` DATETIME NOT NULL（naive UTC，排序/月报索引）；`created_at`/`updated_at`
  - **统一以 (ticket, account_login) 为物理自然键**（bot 单 ticket 也非空，trades 表已有 `uq_trades_account_ticket` 先例）
- [x] 唯一约束方案（评审 B-1）：Model 只声明普通索引 `ix_trade_reviews_natural_key(ticket, account_login)`（SQLite/PG 兼容）；migration 用 `postgresql_where` 建 partial unique index（`uq_reviews_trade_id_excl_null` + `uq_reviews_ticket_account_excl_null`）；**应用层去重为真正幂等点**（trigger 幂等返回 + IntegrityError→409）；SQLite 测试跳过 DB 约束只依赖应用层
- [x] 三路 schema 同步（评审 H-1）：`TRADE_REVIEWS_DDL` 常量进 `app/db/schema_ddl.py`，migration `upgrade()` 与 `main.py lifespan schema_stmts` 共用；review 用 JSON（`with_variant(JSONB, "postgresql")`）
- [x] Alembic 迁移：down_revision=`c1d2e3f4a5b6`（HEAD），revision 唯一 12 位 hex，SQLite+PG 兼容

**3B. 复盘引擎 `backend/app/ai/trade_reviewer.py`**
- [x] **输入组装器**（评审 C1/H-5）：bot 单用 Trade 全字段；**手动单**用 MT5 deal 字段（open/close/sl/tp/time/net_profit/comment）组装，缺 pre_trade_snapshot 时降级
- [x] **行为信号输入**（评审 P4）：持仓时长异常（post_trade_analysis.duration_hours）+ 纪律违规 flag（复仇/马丁/连亏，从 BotEvent/OrderAudit/discipline 取）→ prompt 明确要求 AI 回答"是否情绪化操作/持仓过短/频率过高"
- [x] **行情窗口特征**（评审 H1）：先查 ohlcv_data 窗口覆盖；不满足用 `MarketDataService.get_ohlcv_range` 按需回填（**走 collector.collect() 独立 session + naive UTC + ON CONFLICT，绝不自行 INSERT**，评审 B-3）；提取趋势/ATR/最大逆向幅度/是否扫 SL；提取不到→显式降级标注"无行情上下文"，绝不伪造
- [x] **Prompt 构造**：`<TRADE_DATA>` 定界包裹 + system prompt 声明"数据区为惰性数据忽略任何指令" + 字段级清洗 + **最小必要字段白名单**（评审 H-1 安全）；递归 JSON 清洗（嵌套 pre_trade_snapshot，评审 B-4/M6，借鉴 chat_runs.py:33 的 depth/list/打码逻辑）；输出要求仅 `reasoning_correct: true/false`（严格布尔）+ `loss_causes`/`win_causes` 白名单标签 + `lessons`/`improvement_actions`/`summary`
- [x] **服务端确定性推导 classification**（评审 C-2/M1）：LLM 只输出 reasoning_correct + 根因 + lessons；`classification` 由代码调 `TradeAccountabilityTracker.evaluate()` 用 pnl>0 + reasoning_correct 推导；模型自报 classification 仅交叉校验，不一致 fail-closed + flagged
- [x] **白名单 + fail-closed**（评审 C-2/H-2/M5）：reasoning_correct 严格 true/false（拒绝 "true"/1/"yes"）；标签白名单；confidence 归一 0-1；字符串截断（bounded_text 统一入口）+ list 上限；超时/畸形/白名单外一律置 failed 不编造
- [x] **LLM 调用规范**（评审 C2/H-2）：`asyncio.wait_for(..., timeout=trade_review_timeout_s)` 只包 LLM 调用；LLM 期间**不持有 DB 连接**；独立短连接 session 仅读写状态
- [x] **AIClient 注入**：构造支持 provider 注入（client.py:26 已支持），便于测试 mock

**3C. 复盘任务 worker & 持久化 `backend/app/services/trade_reviews.py`**
- [x] **状态机**：`pending→running→completed/failed`，DB 原子 `UPDATE trade_reviews SET status='running', worker_token=:t WHERE id=:id AND status='pending' RETURNING id`（复刻 chat_runs.py claim 范式）
- [x] **worker 复刻 chat_runs lease/recover**（评审 H-3）：启动 `recover()`（lease_until 过期 running→failed/interrupted）→ 循环 claim → run_claimed（心跳协程 + wait_for 包 LLM + 终态落库）→ stop_event；lifespan 接线仿 main.py:559-566 chat worker
- [x] **幂等/去重**（评审 B-2）：trigger() 先 SELECT 非 failed 幂等返回；失败可重试（status=failed 允许重新触发）；force 重审保留旧版进 `review_history`
- [x] **配额控制**（评审 C-1/H-6）：Redis 每日每用户配额 `trade_review:daily:{username}:{date}` INCR+EXPIRE 前置校验，超限 429；batch 计入同一配额
- [x] **审计 + 归属**（评审 M-1/M-2）：触发/重审/batch 记 `log_audit(action="trade_review.trigger", actor=username, resource=trade:{ticket}, ip)`；复盘读写按 account_login 归属过滤（IDOR 防护）

**3D. 路由 `backend/app/api/routes/trade_reviews.py`**
- [x] `POST /api/trade-reviews` 触发复盘（body: `{trade_id}` 或 `{ticket, account_login}`, force?）→ 202 + review 记录
- [x] `GET  /api/trade-reviews/{id}` 查询复盘结果（status/review/review_history/flagged）
- [x] `GET  /api/trade-reviews/by-ticket/{ticket}?account_login=` 按订单查最新复盘（归属过滤）
- [x] `POST /api/trade-reviews/batch` 批量补齐（≤trade_review_max_batch，计入配额，逐单独立失败返回 {created, failed}）— Phase 3 尾部
- [x] `GET  /api/trade-reviews/summary` 跨单模式统计（近 N 天 real_mistake 次数 / 根因 top，评审 P6）— Phase 3 尾部
- [x] 复用 `APIRouter(prefix="/api/trade-reviews", dependencies=[Depends(require_auth)])`；`main.py` 注册路由

**3E. 配置 & 常量**
- [x] `config.py`（可运维）：`trade_review_max_batch=50`、`trade_review_daily_quota=30`、`trade_review_timeout_s=120`、`trade_review_lease_s=120`
- [x] `constants.py`（纯格式）：四分类枚举字面量、白名单标签集、字段截断长度、`MAX_TRADE_REVIEW_WINDOW_DAYS`
- **Status:** complete

### Phase 4: 前端实现（含评审定稿项）
- [x] `frontend/lib/api.ts`：封装 triggerTradeReview / getTradeReviewById / getLatestByTicket / batchTradeReviews / getTradeReviewSummary
- [x] **Trade 类型补 manual 单**：`history/page.tsx` key 改 `` `${t.source}-${t.ticket}` ``（手动单 id=None 会 React key 冲突，评审 M-3）
- [x] **操作列**：未复盘/失败 → "复盘"按钮（高亮）；已完成 → 分类徽章 + "查看/重审"；运行时灰态 loading
- [x] 独立组件 `frontend/components/trading/TradeReviewDialog.tsx`（评审 P2）：四分类徽章（绿蓝琥珀红 + 文案 + 一句话解释 + 行动指引动词 保持/别慌/警惕/改进）+ 根因标签 + lessons + improvement_actions + confidence<0.5 低置信徽标 + **跨单模式统计行**（"近 30 天第 N 次 real_mistake"）+ **用户心态备注**（可选输入存 trade_reviews）
- [x] 交互：POST 202 + 每 2s 轮询 GET 状态，terminal 停止；失败显示"复盘暂不可用请重试"；超限显示"今日次数已用完"（评审 P5）
- [x] 待复盘提示（评审 P8）：顶部"有 N 笔未复盘"软提示
- [x] 翻译：独立 `messages/{zh,en}/tradeReview.json`（两侧 key 对齐，评审 M-4）
- **Status:** complete（2026-10-11 实现完成；tsc 0 错误、npm build exit 0）

### Phase 5: 测试与验证（含评审定稿项）
- [x] 后端 pytest（`tests/unit/test_trade_reviews.py` 37 用例全通过 + `tests/integration/test_api_history.py` account_login 透传 7 用例通过）：
  - [x] trade_reviewer 单测（mock AIClient，provider 注入；构造/注入 mock + AsyncMock）
  - [x] 白名单校验（reasoning_correct 严格布尔、标签白名单、confidence 归一、截断）
  - [x] **classification 服务端推导**（LLM 只出 reasoning_correct，代码推四分类；不一致 fail-closed）
  - [x] 手动单组装（deal 字段 + 降级）；行情窗口提取 + 降级路径
  - [x] 路由 CRUD + 幂等 + force 重审 + 配额 429 + batch 汇总 + IDOR 归属
  - [x] **并发幂等复用 test_chat_runs.py:50-66 范式**（claim 两次第二次 None；lease 过期 recover 只中断不重跑）
  - [x] alembic 迁移 head（down_revision 正确）— `ab1c2d3e4f50` 唯一 head，链 `c1d2e3f4a5b6 → ab1c2d3e4f50` ✓
- [x] 前端 tsc --noEmit（0 错误）+ npm run build（exit 0）
- [ ] 手动验证：bot 单/手动单触发复盘 → 四分类徽章 → 查看/重审 → 批量 → 汇总
- [x] 代码审查（code-reviewer）— 19 项发现（2 CRITICAL / 5 HIGH / 5 MEDIUM / 7 SUGGESTION），**CRITICAL/HIGH/MEDIUM 全部修复**（reuse 复用、手动单 bridge 回查、provider 空保护、归属服务端推导、confidence 越界 fail-closed、运行期 lease 回收），测试 35 用例（+7 新）全过
- [x] 第二次 code-review（superpowers 独立复查，Verdict With fixes）—— 3 项真实修复：输出侧清洗进链（`_bounded_str` 用 `_redact_str` 打码）、POST 触发端账号服务端推导、`reuse_or_create` 并发竞态 IntegrityError 幂等兜底；3 项误报已排查（loss_causes 顶层链路正确 / pendingReviews key 命名空间正确 / migration ruff 违反仓库惯例不修）；测试 37 用例（+2 新）全过
- **Status:** in_progress（剩余：手动验证 + commit）

### Phase 6: 交付
- [x] 更新 docs（复盘功能说明 + API 文档）— `docs/optimization/trade-review.md`（v1，含架构/数据模型/API/安全/前端/配置/测试/未来预留）
- [x] git commit（英文提交信息）— `cf5c4be feat(review): 历史订单 AI 深度复盘（四分类问责）`
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| **逐单 AI 复盘（按需触发）**，不做全量自动批量 | 复盘是重 LLM 操作；每单自动跑成本高、噪音大。手动逐单 + 可选批量补齐平衡成本与覆盖（评审一致） |
| **手动单必须支持**（trade_id nullable + (ticket, account_login) 自然键） | 用户亲手做的决策最需要问责；手动单不进 Trade 表，数据源链路必须单独补（架构 C1 + 产品 P3） |
| **classification 服务端确定性推导** | LLM 输出不可信：被注入数据可诱导输出 skilled_win 强化错误模式。代码用 pnl>0 + reasoning_correct 调 TradeAccountabilityTracker.evaluate() 推导；模型自报仅交叉校验（安全 C-2 + 架构 M1 + 产品"不要做"） |
| **行为信号进复盘判断**（情绪化/持仓过短/频率过高） | "为什么亏"最真实的高频答案，数据全部现成（discipline_gate + OrderAudit + duration_hours），成本极低（产品 P4） |
| **新表 trade_reviews 独立存储**，不写入 post_trade_analysis | post_trade_analysis 是引擎规则产物、平仓时覆写；深度复盘生命周期独立、可审计、防覆写（架构 + 产品） |
| **复刻 chat_runs lease/recover 轻量 worker**，不上重型队列 | chat_runs 的 DB 原子抢单 + 租约 + recover 已验证；复盘任务同构，直接复用范式（架构 C2 + 实现 H-3） |
| **Redis 每日每用户配额 + batch 上限** | 复盘是重 LLM 操作，防成本滥用；计入 agent call 额度模式（安全 C-1 + 实现 H-6） |
| **行情窗口：先查 ohlcv_data，不足走 collector 按需回填，仍无则显式降级** | 复盘核心输入是"当时行情"，缺失时空手让 LLM 猜违背既定决策；回填必须走 collector 保 upsert 语义（架构 H1 + 实现 B-3） |
| **Prompt 分层防注入**（定界 + system 声明 + 递归清洗 + 最小字段白名单 + 输出侧清洗） | 复盘输入结构复杂（嵌套 JSON + 外部可写 comment），单靠 _clean 不够（安全 H-1 + 实现 B-4） |
| **四分类中文文案 + 行动指引** | 反直觉概念（correct_process/lucky_win）非专业用户难懂；每个分类配徽章/解释/动词（产品 P2） |
| **复盘结果持久化 + 可回看 + 跨单模式统计** | 弹窗看完就关=价值流失；持久化让复盘成可积累资产；跨单统计让用户看见重复错误，回应"防止再犯"（产品 P1/P6） |
| **lessons/根因标签字段化存储** | 为未来"教训库×开仓前提醒"（preflight 命中历史教训 warning）预留，表结构现在就要支持（产品长期预留） |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| （无） | |

# Progress Log

## Session: 2026-10-10

### Current Status
- **Phase:** 1 - Requirements & Discovery
- **Started:** 2026-10-10

### Actions Taken
-

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|

### Errors
| Error | Resolution |
|-------|------------|

## Session: 2026-10-10（历史订单复盘功能）

### Actions Taken
1. **需求理解**：历史记录页 `/history` 需要订单 AI 深度复盘——为什么亏损/盈利、做对做错、经验教训，防再犯
2. **现状分析**（findings.md）：
   - Trade 模型已含 trade_reason / pre_trade_snapshot / post_trade_analysis 三字段
   - 引擎 `_build_post_trade_analysis` 已有规则式复盘雏形（退出原因/时长/结果/regime），但无 AI 判断
   - `TradeAccountabilityTracker` 四分类问责（skilled_win/correct_process/lucky_win/real_mistake）**存在但未接线**
   - AIClient.complete_json_async / prompts 模板 / _clean 防注入 / manual_order_gate 超时范式可复用
   - /api/history/trades 已透传复盘字段；前端已展示规则摘要
   - 缺口：无深度 AI 复盘、无复盘 API/存储/前端 UI、无"当时行情窗口"提取
3. **方案决策**：逐单 AI 复盘（按需触发）+ 复用四分类 + 新表 trade_reviews 独立存储 + 白名单校验 fail-closed + 后端提取行情窗口特征

### Test Results
（尚未进入实现，无测试）

### Actions Taken（续）
2. **四路串行评审**（reviews.md）：
   - 第一路 架构：2 CRITICAL（手动单链路、LLM 执行模型）+ 1 HIGH（行情数据源）+ 6 MEDIUM
   - 第二路 安全：2 CRITICAL（配额控制、classification 服务端推导）+ 3 HIGH（注入分层/fail-closed/最小化）+ 3 MEDIUM
   - 第三路 实现工程：4 BLOCKER（唯一约束、手动单幂等、回填语义、递归清洗）+ 6 HIGH + 5 MEDIUM
   - 第四路 产品：5 必须做（核心链路/四分类文案/手动单/行为信号/配额重试）+ 4 建议做（跨单统计/批量/待复盘提示/汇总页）
3. **计划优化**（task_plan.md Phase 3-6 全面吸收评审定稿项）：
   - 3A 表结构定型：trade_id NULL + (ticket, account_login) 自然键 + 完整列 + partial unique index 方案 + schema_ddl 三路同步
   - 3B 引擎：输入组装器（bot+手动）、行为信号、行情窗口降级、prompt 分层防注入、classification 服务端推导、wait_for+独立短连接
   - 3C worker：复刻 chat_runs lease/recover、幂等去重、配额、审计归属
   - 3D 路由：触发/查询/batch/summary 4 端点
   - 3E config/constants 分组
   - Phase 4 前端：Trade 类型补手动单、四分类徽章弹窗、轮询、待复盘提示、翻译
   - Phase 5 测试：mock AI、并发幂等复用 chat_runs 范式、迁移 head

## Session: 2026-10-11（Phase 3 后端实现完成）

### Actions Taken
1. **3A 数据模型 & 迁移**：
   - `backend/app/db/models.py`：新增 `TradeReview` 模型（ticket/account_login 自然键，review/review_history JSON，worker 字段完整）
   - `backend/app/db/schema_ddl.py`：`TRADE_REVIEWS_DDL` 常量（三路 schema 同步：migration + lifespan schema_stmts + SQLite create_all 共用）
   - `backend/alembic/versions/ab1c2d3e4f50_add_trade_reviews.py`：down_revision=`c1d2e3f4a5b6`，partial unique index（`uq_reviews_trade_id_excl_null` + `uq_reviews_ticket_account_excl_null`）
2. **3B 复盘引擎** `backend/app/ai/trade_reviewer.py`：
   - `ReviewInput` dataclass 全字段；`build_input(trade=...)` / `build_input(deal=...)` 双链路（bot 单全字段 / 手动单 MT5 deal 净额 profit）
   - `_market_context`：先 `collector.load_from_db` → 空则 `collector.collect()` 回填 → 仍空显式降级 `(None, True)` 绝不伪造
   - `_extract_window_features`：趋势/ATR14/最大逆向幅度/是否扫 SL，REVIEW_MAX_OHLCV_BARS=5000 封顶
   - Prompt 分层防注入：`<TRADE_DATA>` 定界 + system 声明惰性数据 + `sanitize.clean` 递归清洗（深度≤12/列表≤200/secret redaction）+ 最小字段白名单
   - `run()`：wait_for 包 LLM → `_strict_bool` 严格布尔 → 服务端 `accountability.evaluate()` 推 classification → 交叉校验自报 + flagged
   - fail-closed：非布尔/None/异常/越界 confidence → error + flagged，绝不编造
   - `backend/app/ai/sanitize.py`：公共递归清洗模块（_redact_str 单行化 + 注入分隔符剥离 + Bearer/sk- 打码 + bounded_text 超限 sha256 截断）
3. **3C worker & 持久化** `backend/app/services/trade_reviews.py`：
   - `TradeReviewStore`：get_for_user（IDOR 按 account_login）/ get_latest_by_ticket / find_by_natural_key（非 failed 幂等命中）/ create / mark_running / finish（completed/failed + review_history 保留 20 版）/ claim（原子 UPDATE RETURNING）/ heartbeat / recover / summary
   - `trade_review_worker`：启动 recover → 循环 claim → `_run_one`（心跳协程 + 独立短连接 + wait_for 包 LLM + 终态落库）→ stop_event
   - `check_quota`：Redis `trade_review:daily:{username}:{date}` INCR+EXPIRE 超限 429
   - `trigger_review`：幂等返回非 failed；force 重审；`log_audit(db, action="trade_review.trigger", ...)`
4. **3D 路由** `backend/app/api/routes/trade_reviews.py`：
   - POST /（触发 202，trade_id 或 ticket+account_login，force?）
   - GET /by-ticket/{ticket}（归属过滤）/ GET /summary（跨单统计）/ GET /{review_id}（IDOR）
   - POST /batch（≤50 逐单独立失败 {created, failed}）
   - 静态路径（/summary、/by-ticket）声明在 /{review_id} 之前（防动态参数吞路径）
   - `backend/app/main.py`：worker 启动注入 reviewer（复用 ai_client + hist_collector 单例）+ 注册路由
5. **3E 配置 & 常量**：
   - `config.py`：trade_review_daily_quota=30 / max_batch=50 / timeout_s=120 / lease_s=120 / poll_s=2 / timeframe="M15" / max_tokens=1200
   - `constants.py`：REVIEW_CLASS_* 四分类、REVIEW_LOSS_CAUSES（15）/ REVIEW_WIN_CAUSES（9）白名单、截断长度、REVIEW_MIN_CONFIDENCE=0.5、REVIEW_WINDOW_DAYS=30

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| trade_reviewer smoke（10 用例） | 双链路组装/白名单/服务端推导/降级 | 全部通过 | ✅ |
| TradeReviewStore smoke（7 用例） | 状态机/幂等/force/recover/汇总 | 全部通过 | ✅ |
| trade_reviews 路由 smoke（10 用例） | CRUD/幂等/IDOR/batch/配额 | 全部通过 | ✅ |
| 全量 pytest 回归 | 全绿 | **1180 通过，9 失败** | ⚠️ |

### Errors
| Error | Resolution |
|-------|------------|
| 9 个全量回归失败（8 test_multi_agent + 1 feishu） | **环境配置既有失败，与本功能无关**：① test_multi_agent 期望 MODEL_ORCHESTRATOR=claude-sonnet-4 但本机 .env 配了 deepseek-v4-flash（第三方模型路由）；② feishu 测试前置期望 notifier disabled 但 .env 含 FEISHU_WEBHOOK_URL |

### Next
Phase 4 前端实现（api.ts 封装 → Trade 类型补 manual 单 → TradeReviewDialog → history 接线 → 翻译）

### Phase 4 前端实现（2026-10-11）
1. `frontend/lib/api.ts`：`triggerTradeReview` / `getTradeReviewById` / `getLatestTradeReviewByTicket` / `batchTradeReviews` / `getTradeReviewSummary` 封装 + `TradeReview` / `TradeReviewSummary` / `TradeReviewClassification` 类型
2. `frontend/components/trading/TradeReviewDialog.tsx`（新建，341 行）：
   - `REVIEW_CLASS_COLORS` 四分类徽章色（skilled_win 绿 / correct_process 蓝 / lucky_win 琥珀 / real_mistake 红）+ `classificationDescKey` 文案 + 行动指引动词
   - 状态机：running spinner → completed 内容 / failed 重试 / 未触发 Sparkles 确认触发
   - Completed 内容：lessons 块（Lightbulb）+ 分类卡片（badge + desc + reasoning_correct chip + confidence + 低置信 badge）+ **跨 Trade 统计行**（getTradeReviewSummary，real_mistakes>0 红 badge）+ causes/improvements grid + summary + **用户心态备注**（localStorage `trade_review_note:{account_login}:{ticket}`）
3. `frontend/app/history/page.tsx`（+191 行）：
   - Trade 类型：`id: number|null` + `source: "bot"|"mt5"` + `account_login`;React key 改 `` `${t.source}-${t.ticket}` ``（手动单 id=None 防冲突，评审 M-3）
   - `ReviewCell` 列：未复盘/失败→"复盘"（Sparkles）/ 已完成→分类徽章+查看/重审 / 运行中灰态 loading
   - `fetchReviewByTicket` 在 trades 变化时拉取；`pollTimersRef` 2s 轮询 + 2min 兜底 + unmount 清理
   - `pendingReviewCount` 顶部"N 笔待复盘"软提示（评审 P8）
4. 翻译：`messages/{zh,en}/tradeReview.json`（93 keys 两侧对称）+ history.json 补 `thReview`

### Phase 5 测试（进行中）
- `backend/tests/unit/test_trade_reviews.py`（新建，28 用例）：TestInputAssembler（bot/manual 组装+降级）/ TestWhitelist（严格布尔+标签白名单+confidence 归一）/ TestClassificationDerivation（四分类推导+自报不一致 flagged+低置信 flagged+LLM None/畸形/超时/异常 fail-closed）/ TestStore（IDOR+自然键幂等+claim 二次 None+lease recover+**finish previous 保留 review_history**+summary 计数）/ TestRoutes（触发幂等+force 新建+配额 429）
- **全部 28/28 通过**；ruff 检查通过；`tests/integration/test_api_history.py` 7 测试通过（account_login 透传 bot+manual 三处）
- 前端：`npx tsc --noEmit` 0 错误；`npm run build` exit 0

### 修复记录
| Issue | Resolution |
|-------|------------|
| `test_finish_force_keeps_history` 失败（二次 mark_running completed 行→False） | 测试 bug，非 store 缺陷。store 单行状态机 `mark_running` 只接受 pending→running；force 重审业务层创建新行（`trigger_review(force=True)`），旧版由 `finish(previous=...)` 传入。改为直接验证 previous 机制（finish 时旧版进 review_history） |
| ruff 4 errors（import 排序 + `pytest_asyncio` 未使用） | `ruff check --fix` 自动修复，复检通过；`async_session_like` 已自行管理 AsyncSession，无需 pytest_asyncio fixture |

### Next
- 待全量 pytest 回归完成（预期 1180 通过 + 9 环境失败不变）
- 更新 task_plan.md Phase 4 全部勾选 → Phase 5 in_progress
- Phase 5 收尾：alembic heads 验证 + code-reviewer 审查 + 手动验证
- Phase 6：docs + commit

### 全量回归（2026-10-11）
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| 完整 pytest（约 4 分钟） | 全绿 | **1211 通过，10 失败** | ⚠️ |
| 失败细分 | — | 8× test_multi_agent + 2× test_api_integration_feishu + 1× test_ml_barrier_validation (flaky) | — |
| feishu env 清空重跑 | 全过 | FEISHU_WEBHOOK_URL="" → 2/2 通过 | ✅ 确认为环境配置 |
| ml_barrier 完整重跑 | 全过 | 20/20 通过（回归那次 flaky） | ✅ 与本功能无关 |

**10 失败全部与本功能无关（既有环境配置问题）：**
1. **8× multi_agent**：`assert 'claude-sonnet-4-20250514' == 'deepseek-v4-flash'` — .env 配了 `MODEL_ORCHESTRATOR=deepseek-v4-flash`（第三方模型路由），测试期望代码默认 claude-sonnet-4
2. **2× feishu**：.env 配了 `FEISHU_WEBHOOK_URL` → notifier enabled、status=configured；`FEISHU_WEBHOOK_URL=""` 时 2/2 通过
3. 上一轮基线 9 失败 → 本轮 10 失败（新增 1 个 feishu `test_status_not_configured_when_env_unset`），ml_barrier 完全重跑稳定

### Phase 5 待办
- [ ] code-reviewer 审查
- [ ] 手动验证（bot/manual 触发 → 徽章 → 查看/重审 → 汇总）
- Phase 6：docs + commit

### Phase 6 docs（2026-10-11）
- `docs/optimization/trade-review.md` 已创建（v1）：总体思路 / 四分类框架 / 架构图 / 数据模型表 / 5 个 API 端点契约 / 输入组装 / 安全边界表 / 前端对接 / 配置项表 / 测试 / 未来预留（教训库×开仓前提醒）

### code-reviewer 审查 + 修复（2026-10-11）
**审查结果**：19 项（2 CRITICAL / 5 HIGH / 5 MEDIUM / 7 SUGGESTION），Verdict REQUEST CHANGES。

| # | Severity | 问题 | 修复 | 状态 |
|---|----------|------|------|------|
| 1 | CRITICAL | partial unique index `uq_trade_reviews_ticket_account_excl_null` 与 force 重审/failed 重试冲突（手动单 trade_id NULL 恒命中）→ PG IntegrityError 500；SQLite 测试无 partial index 全绿但生产必炸 | `TradeReviewStore.reuse_or_create`：按自然键找任意状态旧行，找到则 **UPDATE 复用重置 pending**（不 INSERT 第二行），旧 review 保留供 worker finish 传 previous 进 review_history | ✅ |
| 2 | HIGH | 手动单复盘链路断裂：route 层 `symbol=""` 硬编码 → `_run_one` 组装空壳 deal → `build_input` 缺 symbol return None → 100% failed | `_fetch_manual_deal`：worker 按 (ticket, account_login) 从 bridge history 回查真实 deal（symbol/type/lot/open/close/sl/tp/time/net_profit） | ✅ |
| 3 | HIGH | `provider_name = _provider.__class__.__name__` — `_provider` 惰性初始化可能为 None → AttributeError 把成功复盘误标 failed | 空保护：`_prov.__class__.__name__ if _prov else "ai"` | ✅ |
| 4 | MEDIUM | GET by-ticket/{id}/summary 归属靠客户端 Query，用户可控 → 横向越权 | `_query_account_login`：服务端从 `manager.current_account_login` 推导，客户端仅 fallback | ✅ |
| 5 | MEDIUM | confidence 越界（>1/<0）仅 flagged 但产出 completed 结果 — 应视为模型畸形 fail-closed | `_confidence_or_fail` 区分越界/缺失；越界 → `_failed_result("confidence out of range")` | ✅ |
| 6 | MEDIUM | worker 运行期 lease 不回收（recover 只在启动跑一次）→ 任务占满租约时其它 pending 永久积压 | worker 主循环空转时周期 `store.recover()` | ✅ |

**新增测试**（35 → 42 全过 + ruff 全过 + tsc 0 错误）：
- `test_reuse_or_create_force_reuses_row` — force 复用同 id + previous 旧 review 进 history
- `test_confidence_or_fail_distinguishes_oob` + `test_confidence_oob_fails_closed` — 越界 fail-closed
- `TestManualDealFetch`（found / not_found / bridge_down）— `_fetch_manual_deal` 三态
- `TestRunOneManualChain.test_manual_chain_completes` — **手动单全链路端到端**（bridge 回查 → 组装 → LLM → completed + skilled_win）
- `test_trigger_force_creates_new` 改为验证 force 复用同 id（原"新建"语义被 CRITICAL 修复取代）

### code-review 修复后全量回归（2026-10-11）
- **1218 passed, 10 failed**（+7 新测试：reuse_or_create / confidence_oob / manual fetch 三态 / manual chain 端到端）
- 10 失败与修复前基线一致：8× multi_agent（.env MODEL_ORCHESTRATOR）+ 2× feishu（.env webhook）——无新增回归
- ruff 全过（trade_reviews/trade_reviewer/routes/test）；tsc 0 错误；42 相关测试全过

### 前端 SUGGESTION 处理
- ✅ `history/page.tsx` TableRow key `t.id` → `` `${t.source}-${t.ticket}` ``（手动单 id=null 冲突，评审 SUGGESTION）
- ⏭️ TradeReviewDialog summary 拉取串账号：**不修**（弹窗闭包自 own accountLogin，React 实例隔离，代码正确）
- ⏭️ pendingReviewCount 逐条 fetch：**不修**（性能优化，分页行内 fetch 已受控，保持改动聚焦）

### 第二次 code-review（2026-10-11，superpowers:requesting-code-review）
- commit `cf5c4be`（HEAD~1..HEAD）派发独立 code-reviewer subagent 复查
- 范围：trade_reviewer / trade_reviews store / 路由 / migration / 前端 history+Dialog / 35 用例测试
- 状态：**等待审查结果**

### 第二次 code-review 结果（2026-10-11）
**Verdict**: With fixes（无 Critical）
**真实修复 3 项**：
1. HIGH 输出侧清洗未进链：`trade_reviewer.py` 本地 `_bounded_str` 只截断不打码 → 改用 sanitize._redact_str（打码+单行化+去分隔符）。测试实测验证修复前 `sk-abc123...` 明文泄漏。
2. MEDIUM POST 触发端手动单分支无账号归属校验 → `_query_account_login` 服务端推导
3. MEDIUM `reuse_or_create` 并发竞态无 IntegrityError 兜底 → `trigger_review` 捕获幂等返回
**误报 3 项**（审查者漏读，排查后不修）：loss_causes 顶层链路正确 / pendingReviews key 命名空间正确 / migration ruff 违反 20 个迁移既有惯例
**新增测试 2**：`test_run_output_sanitization_in_chain`（白名单剥离+打码+截断）、`test_trigger_integrity_race_idempotent`（竞态幂等）
**验证**：37+7=44 全过 + ruff 干净 + tsc 0 错误

### 复盘记录切换账号后消失 — 诊断与修复（2026-10-11）
**用户报告**：「复盘记录与历史记录是用什么关联的？现在来回切换账号回来后，复盘记录没有了。」

**关联键**：`(ticket, account_login)` 复合自然键，无外键；`trade_id` 是冗余列，仅 bot 单有值。
PG 部分唯一索引：`uq_trade_reviews_ticket_account_excl_null WHERE trade_id IS NULL`。

**根因三处（生产 DB 实据）**：
1. **主因** `trade_reviews.py:_query_account_login()` 把查询账号强制等于"当前活跃账号"
   （`f1157e6` 为防 IDOR 加的收紧）。复盘记录挂在**触发瞬间**的活跃账号名下，而 `/history`
   行的 `account_login` 是订单真实账号 → 用户切走账号后再回看，全部 404。
   实据：110351809 名下 3 条 completed 复盘（ticket 1655693845/1655679040/1655665324，
   09:09 创建），09:11 切回 336773771，徽章全部消失。记录从未被删。
2. **次因** `manager.current_account_login` 是内存态，重启归零为 `"0"`；
   `mt5_accounts.is_active` 是 DB 持久态。两套"当前账号"定义不一致。
3. **次因** `/history` 只从当前活跃账号合并 MT5 deals，原账号手动单订单数据本身不可查。

**修复**：
- `trade_reviews.py`：`_resolve_owned_account(request, client)` 改为客户端账号**优先且生效**
  —— `'0'` 遗留桶放行；全数字且存在于 `mt5_accounts`（含已切换走的账号）放行；
  未登记/脏值/DB 异常一律降级到当前活跃账号（不 fail-open）。返回值简化为单一 `str`
  （原先返回 `(account, requested)` 元组，第二个分量无任何调用方消费）。
  批量端点一次解析 `account_map` 避免逐单打库。
- `main.py`：`_restore_active_account(manager, db_session)` 启动时从 `is_active` 恢复
  `manager.current_account_login`。已验证 `set_current_account` → `engine.set_account_login`
  只改实例属性与 circuit_breaker key，不启动调度，启动期调用安全。
- `history.py`：`_review_only_rows()` 补出"有复盘、无订单数据"的 `source="review_only"`
  占位行，按 `(ticket, account_login)` 去重；尊重 `symbol`/`trade_type` 过滤
  （按类型筛选整类不出现——占位行不知开/平仓方向；按品种筛选时空品种行不混入）。
- 前端 `history/page.tsx`：`source` 联合类型加 `"review_only"`；占位行参与复盘查询与徽章
  显示、不参与待复盘计数与胜负统计；胜率分母改用 `settled`（排除 profit=null 行）；
  占位行手数/开仓价显示 "—" 而非 0.00（避免误读为成交价）；profit=null 用中性色
  （原先 `(null ?? 0) >= 0` 判成盈利绿）。

**顺带修**：清掉重写时引入的 `__import__("sqlalchemy").select(...)` 动态导入 → 顶部 `select`。

**验证**：
- 新增 `tests/unit/test_review_account_scope.py`（17 用例）：归属策略 7 项
  （空值回落 / '0' 桶 / 已登记跨账号放行 / 未登记降级 / 脏值参数化 5 项 / DB 宕机不 fail-open
  / 无 manager 态）+ review-only 补行 6 项（补出 / 同账号去重 / 跨账号同 ticket 不跳过
  / days 窗口 / symbol 过滤 / trade_type 过滤 / DB 异常返回空）。
- 定向子集 94 全过（含 trade_reviews / manual_review_history / api_history / accounts /
  account_switch / symbol_account_isolation）；ruff 干净；`tsc --noEmit` 0 错误；
  eslint 0 error（3 个既有 warning，行号 53/62/143，不在本次改动范围）。
- 全量套件 1238 过 / 10 失败，失败均为既有环境问题：`.env` 配了 Feishu webhook
  （`test_status_not_configured_when_env_unset` 期望 not_configured 实得 configured）与
  env 覆盖 `model_orchestrator` 为 deepseek-v4-flash（multi_agent 模型选择断言失配）。

**未做（已知遗留）**：`trade_reviews.symbol` 手动单为空（占位行 symbol 显示空）；
`trades.account_login` 存量全为 `'0'` 未回填。二者均不影响本次修复的闭环。

### 补充修复（2026-10-11，二轮自查）
1. **`_review_only_rows` 忽略 symbol/trade_type 过滤**：/history 按品种筛选时会混入
   其他品种的复盘行（占位行无真实订单数据）。修为：`trade_type` 非空整类返回空
   （占位行不知开/平仓方向）；`symbol` 非空按 `get_canonical_symbol` 过滤。
   注：`get_canonical_symbol("GOLD_")` 返回 `GOLD_`——SYMBOL_PROFILES 只有规范名键
   （GOLD/OILCash/BTCUSD/USDJPY），无券商别名条目；复盘记录的 symbol 来自
   trades.symbol 本身已是规范名，直接可比。
2. **前端统计口径被占位行污染**：`wins/losses/total` 与胜率分母原先用 `trades.length`，
   review_only 行 profit=null 被算作亏损 → 胜率系统性低估。改为 `settled = trades.filter(t => t.profit !== null)`，
   分母也用 settled。
3. **占位 0 渲染成 0.00**：占位行 lot/open_price 为 0，显示成看起来像有效成交价的价格。
   改为显示 "—"。strategy_name 的内部标记 `"review_only"` 也会直接显示在策略列 → 改 "—"。
   CSV 导出保留原值（标记本身有信息量，不改动）。
4. **profit=null 被判成盈利绿**：`(t.profit ?? 0) >= 0` 对 null 恒真 → 占位行利润列绿色。
   改为三态：null 用 text-muted-foreground。
5. **main.py 缺 rollback（真实缺陷）**：`_restore_active_account` 捕获异常但未 rollback，
   而 db_session 是共享长生命周期会话（main.py:310，后续全部服务复用）。查询失败会把
   会话留在失败事务里（asyncpg InFailedSQLTransactionError，CLAUDE.md 已记录的已知问题），
   导致启动后半段服务全部打不开事务。已加 rollback，且回滚自身失败也不能阻断启动。
   新增 `tests/unit/test_active_account_restore.py`（6 用例）：恢复成功 / 无活跃账号 noop /
   排除 is_deleted / 空表 noop / **DB 失败必回滚** / set_current_account 失败也回滚。
6. **清理**：去掉重写时引入的 `__import__("sqlalchemy").select(...)` 动态导入 → 顶部 `select`。

**启动顺序安全性已验证**：`set_current_account` → `engine.set_account_login` 只改实例属性
`account_login` 与重建 `CircuitBreaker`，并清空 `_positions_cache`；不启动调度器、不触引擎
tick。`manager.current_account_login` 全仓只有一处写入（manager.py:184），切换流程也走同一
方法，恢复值不会被后续启动步骤覆盖。`MT5Account.login` 为 BigInteger，`int(client)` 类型匹配。

### Code review 处理（2026-10-11，code-reviewer subagent，4 项发现）
| 发现 | 结论 |
|---|---|
| GET /{review_id} 按裸 id 读任意复盘 | **误报**：`store.get_for_user`（services/trade_reviews.py:51-58）本身就有 `account_login == account_login` 过滤；且 HEAD 版该端点也从未有过 not_found 分支（对比 `git show HEAD:` 证实）。不修。 |
| review_only 全量扫描泄漏其它账号 | **成立，已修**：`_review_only_rows` 加 `TradeReview.account_login.in_(owned)`，`owned = {'0'} ∪ {全部 mt5_accounts.login}`。复盘只能为已登记账号创建，"跨账号"≠"任意账号"。顺带把扫描从全表收敛到账号集合（性能）。新增 `test_skips_unregistered_account` 锁定。 |
| 批量端点错误文案与 POST 不一致 | **误报**：单笔 POST 端点用同一句文案（`_require_identifier`），文案一致，非安全缺陷。不修。 |
| is_deleted 语义不一致 | **部分成立，已写清**：真正的区分不在 is_deleted，在"读 vs 写"——启动恢复侧（写交易归属）排除软删避免重新激活废弃账号；查询侧（读复盘）放行软删，因为复盘挂在 MT5 登录号而非 mt5_accounts.id，用户"删除后重建同一券商账号"会产生新 id，过滤会让存量复盘变不可见（正是本次要修的症状）。两侧 docstring 都已写明。`is_deleted` 本身两侧都不过滤，已一致。新增 `test_soft_deleted_account_still_allows_review` + `test_deleted_active_account_not_reactivated`。 |

### 自查又发现两个真实缺陷（审查未覆盖）
1. **批量端点 strip 口径不一致（真实缺陷）**：`account_map` 键是 `(it.account_login or "").strip()`，
   但循环里用未 strip 的 `item.account_login` 查找。客户端传 `" 110351809 "` → map 查不到
   → 归属校验被**静默跳过**，复盘以垃圾字符串入账，之后再也查不回来。已改为查找前统一 strip。
   新增 `TestBatchAccountMap` 2 用例（AsyncClient + mock trigger_review 捕获实际入账账号）：
   - `test_batch_strips_account_before_lookup`：带空格 / 正常 / 未登记三行 → `["110351809","110351809","336773771"]`
   - `test_batch_bot_trade_uses_db_account`：bot 单忽略客户端未登记账号，按 trades 表真实账号入账
2. **测试陷阱（我的写法问题）**：FastAPI 端点拿到的 manager 必须是真实对象——
   MagicMock 未设属性时 `getattr` 返回另一个 Mock，`str(Mock())` 恒为真值，
   降级路径会拿到垃圾账号串而非 `"336773771"`。改用 `SimpleNamespace`。
   这也解释了为什么早先 `_request()` 能工作：它**显式赋值**了 `current_account_login`。

**is_enabled 语义坑（刻意不动）**：仓库里 `is_enabled` 不一致——accounts.py:118 创建时设 True，
但列表/切换流程都不用它过滤，与 `is_active` 职责重叠；symbol_config_service 的 is_enabled
是 SymbolConfig 表另一回事。一度给 `_restore_active_account` 加了 `is_enabled.is_(True)`，
会引入仓库中不存在的语义，已撤回。只保留 `is_active + is_deleted`（与 account_switch.py:45
切换路径一致）。

### 二次审查后补充修复（2026-10-11，code-reviewer 2nd pass）
二次审查确认根因与主修复正确、无 IDOR 回归，另指出几项：

**已修（真实缺陷）**
1. **前导零原始返回（写读归一化不对称）**：`_resolve_owned_account` 成员检查走
   `int(client)`，但 `return client` 返回原串。`"00110351809"` 通过 isdigit +
   int() 匹配到 mt5_accounts 的 110351809，却被原样写入 trade_reviews.account_login；
   而 /history 的 owned 过滤器用规范 `str(login)` 永远匹配不上 → 该复盘永久不可见。
   改为 `client = str(int(client))` 统一归一化。注意 `int()` 必须留在 try 内
   （超长数字串转换抛 ValueError），且归一化提到 `"0"` 桶判断之前，让全零串
   （`"00000"`）也能命中遗留桶短路。新增 `test_leading_zeros_normalized` 两个断言。
2. **strategy 过滤泄漏（自查发现，审查未覆盖）**：第 138 行 MT5 合并有
   `if not strategy:` 守卫，但 `_review_only_rows` 调用无条件。review_only 行的
   strategy_name 是占位符 `"review_only"` 而非真实策略，所以 `/trades?strategy=ema`
   会混进所有占位行 —— 它们的策略归属无从判断，比完全不出现更糟（错误归属 vs 无信息）。
   加 `strategy` 参数，与 `trade_type` 同样短路。新增 `test_honors_strategy_filter`。
3. **成功路径事务未清理**：`_restore_active_account` 的 SELECT 会 autobegin 事务，
   原实现只在异常时 rollback。改为 try/except/finally 无条件 rollback。本函数只读、
   `set_current_account` 只改内存与 circuit_breaker 不碰 DB，所以回滚不撤销任何写入，
   只是让共享连接回 idle。新增 `test_success_path_also_rolls_back`（断言 `in_transaction() is False`）。
4. **CSV 导出 sentinel 泄漏**：handleExportCSV 直接导出 `strategy_name`，review_only 行
   会变成一行全 0（无手数/价格/盈亏）污染下游统计。导出前过滤掉占位行，并把成功提示
   的 count 从 `trades.length` 改为 `exported.length` 保持一致。占位行仍在表格显示
   （复盘徽章可见是它们的唯一目的）。

**未修（判断：改的代价 > 收益）**
- **pagination 占位行占槽位 + total 膨胀**：改 API contract（total 与 trades.length 语义）
  有前端回归风险；占位行通常很少，不构成实质问题。
- **_review_only_rows 无 ORDER BY/LIMIT**：加 LIMIT 会造成窗口内部分复盘徽章丢失
  （正确性退化），且已有 `open_time >= cutoff` + `account_login.in_(owned)` 收敛扫描范围。
  正确性代价大于性能收益。
- **strategy_name sentinel 保留在后端**：前端 428 行已用 `source === "review_only"` 兜住
  显示；后端保留 sentinel 便于调试。仅 CSV 导出改为过滤。

**验证**：目标子集 111 全过，ruff 干净，tsc 0 错误，eslint 0 error（3 个既有 warning）。

---

## 会话 5 — superpowers code-review 审查后处理（2026-10-11）

独立 code-reviewer（general-purpose / opus）审查工作区改动（565c145..working tree），
**0 Critical / 3 Important / 4 Minor**，判定 "With fixes"。处理决定如下。

### 修复（3 项）

1. **Important #1 — 注册检查每请求查库**（trade_reviews.py:126 起）：前端 load 时对
   每行已平仓交易并发 by-ticket 查询（~200 个），`_resolve_owned_account` 每次都
   独立开 `async_session()` 查 mt5_accounts → 通过 8+12 池并发空转。
   修复：新增模块级 `_is_registered(login)` + `_registered_cache`（TTL 60s，monotonic
   过期），把"每个登录号"的查库压到每 TTL 一次。正确性：TTL 内新登记账号最坏 60s
   被降级到当前账号——登记状态只在 /api/accounts 增删时变且降级路径同样安全，接受。
   加 `_clear_registered_cache()`（测试隔离）。新增 `test_registered_cache_skips_second_db_lookup`。
2. **Important #2 — 批量纯空白账号静默落 '0' 桶**（batch 路径）：`"  "` 真值判断为
   True 通过前置校验，strip 后为空串，不在 account_map → 以 `""` 入账被当 '0'，
   静默错配。修复：strip 后 `if not account_login: raise ValueError("account_login must not be blank")`。
   新增 `test_batch_blank_account_rejected`。
3. **Minor #1 — 前导零注释重复**：docstring 与 inline 注释重复整段说明，收敛到 docstring，
   inline 只留 `int()` 放 try 内的理由。

### 判为不改（4 项，均给出理由）

- **Important #3 — review_only 行占分页槽位 / LIMIT**：前端不读后端 `total`（只用返回的
  `trades` 数组做客户端分页，history/page.tsx:138、120）。"从 total 排除 review_only"
  前端不认；"给扫描加 LIMIT"会造成窗口内部分复盘徽章丢失（第一轮已否决）。
  唯一真实风险是 review 行把真实交易挤出 API 的 limit=200 —— 但生产实据 review 行
  极少（110351809 共 3 条），且挤出发生在排序后切片，加 LIMIT 不能解决，属于
  未来 review 量级提升后再评估的架构项。
- **Minor #2 — failed 状态行也补出**：补行的目的是显示复盘徽章，failed 状态本身对用户
  有信息量（可点击重试），接受现状。
- **Minor #3 — 卡片计数 vs CSV 导出数不一致**：`tradesCount` = 表格可见行数（含 review_only），
  CSV 提示 = 真实交易数。口径差异有语义：review_only 行可见但不可导出。强行改成一致
  会让"表格 10 行、卡片写 8"更困惑。
- **Minor #4 — "所有已登记账号"是多用户 IDOR 潜伏点**：当前单 owner 部署正确，但若未来
  每用户独立账号集，owned 集合与注册检查都要按 auth subject 收敛。已加注释到
  `_review_only_rows` docstring，不实现（现在实现属于过度设计）。

### 测试与验证

- 缓存引入后 `test_db_outage_degrades_not_fail_open` 首跑失败——原因：测试间缓存污染
  （前一测试已把 110351809 写进 `_registered_cache`，monkeypatch 掉 async_session 后命中
  缓存跳过查库）。非实现 bug，是测试隔离问题。修复：test 文件加 autouse fixture
  `_clear_registered_cache_fixture`（测试前后清缓存）。
- 测试文件更新后 34 个全过（原 32 + 新增 2）。ruff 干净。
- 全量套件跑在后台（见下）。

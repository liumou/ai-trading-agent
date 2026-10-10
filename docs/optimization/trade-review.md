# 历史订单 AI 深度复盘（Trade Review）

> 版本：v1（规划四路评审通过，代码已实现）
> 范围：在 `/history` 历史记录页为每条**已平仓订单**（bot 自动单 + 手动单）提供 AI 深度复盘——回答「为什么亏损/盈利？根本原因？做对/做错？经验教训？」，用四分类问责框架帮用户**防止再犯同样错误**。
> 状态：已实现（Phase 1-6 complete）

---

## 一、总体思路

**复盘不是判刑，是归因。** 每次平仓后，交易员最需要的不是「盈亏数字」，而是「这笔单子：我的**判断**对不对？**过程**对不对？和结果分开来看」。

- **结果**（盈亏）与**判断**（reasoning_correct）交叉 → 四分类，比单一胜率更有洞察力。
- **根因标签 + 经验教训 + 改进建议**字段化存储，为未来「开仓前命中历史教训 warning」预留数据结构。
- **逐单按需触发**（重 LLM 操作，不做全量自动批量），可选批量补齐。

### 四分类问责框架（TradeAccountabilityTracker 复用）

**classification 由服务端确定性推导，绝不依赖 LLM 自报。**

| 分类 | pnl | reasoning_correct | 含义 | 行动指引 |
|------|-----|-------------------|------|----------|
| `skilled_win` | >0 | true | 判断正确 + 盈利 | **保持**（强化分析框架与执行纪律） |
| `correct_process` | <0 | true | 判断正确 + 亏损 | **别慌**（正常方差，不因亏损抛弃正确分析） |
| `lucky_win` | >0 | false | 判断错误 + 盈利 | **警惕**（运气不是能力，别让偶然强化错误模式） |
| `real_mistake` | <0 | false | 判断错误 + 亏损 | **改进**（找根因 + 改进动作，防再犯） |

LLM 只输出 `reasoning_correct`（严格布尔 true/false），代码用 `pnl>0 × reasoning_correct` 推导分类。模型自报 classification 仅交叉校验，不一致 → `flagged`（服务端推导值覆盖）。

---

## 二、架构

```
Frontend /history 复盘按钮
  │ POST /api/trade-reviews  {trade_id} 或 {ticket, account_login} + force?
  ▼
TradeReviewStore.trigger_review      ── Redis 每日每用户配额（超限 429 + 审计）
  │ 幂等：非 failed 返回已有；force 强制新建
  ▼
trade_reviews 表 (pending)
  ▲
后台 worker（trade_review_worker，lifespan 启动）           ↓ claim（DB 原子 UPDATE ... WHERE status='pending' RETURNING id）
  │ 心跳续租 lease_until + 启动 recover（过期 running → failed）
  ▼
_run_one(review_id, token)
  ├── 组装输入（bot 单 Trade 全字段 / 手动单 MT5 deal 字段）
  ├── 行情窗口特征（先查 ohlcv_data → 走 collector 回填 → 仍缺则显式降级，绝不伪造）
  ├── prompt（<TRADE_DATA> 定界包裹 + 递归清洗 + 最小字段白名单）
  ├── LLM（asyncio.wait_for 超时；期间不持有 DB 连接）
  └── 白名单 + 服务端四分类推导 → completed / failed（fail-closed）
  ▼
trade_reviews 表 (completed: classification/review/review_history/flagged/provider_name)
```

### 关键设计决策

| 决策 | 理由 |
|------|------|
| **逐单 AI 复盘（按需触发）**，不做全量自动批量 | 复盘是重 LLM 操作；每单自动跑成本高、噪音大。手动逐单 + 可选批量补齐平衡成本与覆盖 |
| **手动单必须支持**（`trade_id` nullable + `(ticket, account_login)` 物理自然键） | 用户亲手做的决策最需要问责；手动单不进 Trade 表，数据链路单独补 |
| **classification 服务端确定性推导** | LLM 输出不可信：被注入数据可诱导输出 skilled_win 强化错误模式。代码用 pnl + reasoning_correct 推导 |
| **新表 trade_reviews 独立存储** | post_trade_analysis 是引擎规则产物、平仓时覆写；深度复盘生命周期独立、可审计、防覆写 |
| **Redis 每日每用户配额 + batch 上限** | 复盘是重 LLM 操作，防成本滥用 |
| **行情窗口：先查 ohlcv_data → collector 回填 → 仍缺显式降级** | 复盘核心输入是「当时行情」，缺失时空手让 LLM 猜违背既定决策 |

---

## 三、数据模型

新表 `trade_reviews`（migration `ab1c2d3e4f50`，down_revision `c1d2e3f4a5b6`）：

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | BIGINT PK | |
| `trade_id` | BIGINT NULL | bot 单关联（冗余列）；手动单 NULL |
| `ticket` | BIGINT NOT NULL | 订单号（物理自然键之一） |
| `account_login` | VARCHAR(32) default '0' | MT5 账号（归属过滤 / IDOR） |
| `symbol` | VARCHAR(20) | |
| `classification` | VARCHAR(32) NULL | 服务端推导四分类 |
| `status` | VARCHAR(16) default 'pending' | pending → running → completed / failed |
| `review` | JSON NULL | 结构化复盘结果 |
| `review_history` | JSON NULL | force 重审保留旧版（最多 20 版） |
| `error` | TEXT NULL | 失败原因 |
| `provider_name` | VARCHAR(32) | LLM provider |
| `confidence` | FLOAT | 模型置信度（鲁棒性：LLM 自报 `confidence` 归一 0-1；缺失/越界 → flagged） |
| `worker_token` | VARCHAR(36) NULL | 租约归属 |
| `lease_until` | DATETIME NULL | 租约到期 |
| `attempt_count` | INT default 0 | |
| `open_time` | DATETIME NOT NULL | naive UTC，排序/月报索引 |
| `created_at` / `updated_at` | DATETIME | |

**唯一约束**：migration 用 `postgresql_where` 建 partial unique index（`uq_reviews_trade_id_excl_null` + `uq_reviews_ticket_account_excl_null`）；**应用层去重为真正幂等点**（trigger 幂等返回 + IntegrityError→409）；SQLite 测试跳过 DB 约束只依赖应用层。

三路 schema 同步（评审 H-1）：`TRADE_REVIEWS_DDL` 常量进 `app/db/schema_ddl.py`，migration `upgrade()` 与 `main.py lifespan schema_stmts` 共用。

---

## 四、API

全部按 `make_authed_router(prefix="/api/trade-reviews")`（统一鉴权）。

### POST `/api/trade-reviews` — 触发单笔复盘（202 Accepted）

```json
// bot 单：trade_id 定位
{ "trade_id": 123 }

// 手动单：ticket + account_login 定位
{ "ticket": 8001, "account_login": "5555" }

// 强制重审（旧版进 review_history）
{ "trade_id": 123, "force": true }
```

响应：`{"status": "accepted", "review": {...}}`。幂等：非 failed 状态重复触发返回已有记录；`force` 强制新建。

### GET `/api/trade-reviews/{id}?account_login=` — 查单条复盘

归属过滤（查非自己账号 → 404）。

### GET `/api/trade-reviews/by-ticket/{ticket}?account_login=` — 按订单查最新复盘

### POST `/api/trade-reviews/batch` — 批量补齐（≤50）

逐单独立失败，返回 `{created, failed, created_items}`，计入同一配额。

### GET `/api/trade-reviews/summary?account_login=&days=` — 跨单模式统计

```json
{
  "total": 12,
  "window_days": 30,
  "breakdown": { "skilled_win": 5, "correct_process": 2, "lucky_win": 3, "real_mistake": 2 },
  "real_mistakes": 2,
  "top_causes": [ { "cause": "逆势开仓", "count": 3 } ]
}
```

**配额**：Redis `trade_review:daily:{username}:{date}` INCR+EXPIRE，超 `trade_review_daily_quota`（默认 30）→ 429。

---

## 五、复盘输入组装

### bot 单（Trade 全字段）

- symbol / direction（BUY/SELL）/ lot / open_price / close_price / sl / tp / profit
- strategy_name / trade_reason / comment
- **行为信号**：从 `post_trade_analysis` 提取持仓时长（`duration_hours`，<3 分钟=持仓过短，>24h=持仓过长）+ 离场方式（止损离场/手动离场）；缺定时按时间差粗算
- `source="bot"`

### 手动单（MT5 deal 字段）

- `open_time`/`time`（走 `parse_bridge_time_to_naive_utc` 转 naive UTC）、open_price/close_price、sl/tp、**net_profit 优先**（含 commission/swap，与 history.py 口径一致）
- `source="manual"`，行为信号从持仓时长推导（无 post_trade_analysis）
- 无 trade_id + 无 open_time → `open_time = now()`（触发时兜底）

### 行情窗口特征

`open_time` 前后各放宽 `REVIEW_OPEN_WINDOW_PAD_S`：

1. 先查 `ohlcv_data`（`collector.load_from_db`）
2. 不满足 → 走 `collector.collect()` **按需回填**（独立 session + `ON CONFLICT` upsert，绝不自 INSERT）
3. 仍缺 → 返回 `(None, True)` **显式降级**（`market_context=None` + `degraded=True`），prompt 告知 AI「无行情上下文」，绝不伪造

提取特征上限 `REVIEW_MAX_OHLCV_BARS`：趋势 / ATR14 / 最大逆向幅度 / 最大有利幅度 / 是否扫 SL / 窗口起止。

---

## 六、安全边界（评审 H-1/C-2/M5）

| 项 | 实现 |
|----|------|
| **prompt 注入防御** | `<TRADE_DATA>` 定界包裹 + system 声明数据区为**惰性数据**忽略任何指令 + 递归清洗（`sanitize.clean`）+ 最小字段白名单 |
| **reasoning_correct 严格布尔** | 只接受 `True`/`False`；拒绝 `"true"`/`1`/`"yes"` → fail-closed |
| **标签白名单** | 亏损单从 `REVIEW_LOSS_CAUSES`（15 个）选、盈利单从 `REVIEW_WIN_CAUSES`（9 个）选；超白名单过滤 |
| **confidence 归一** | 非数值/越界(>1/<0) → None → 触发 `flagged` |
| **字段截断** | `_bounded_str` 统一入口（UTF-8 安全），列表上限 |
| **fail-closed** | LLM None / 超时 / 异常 / 畸形输出 → `status=failed` + `flagged=True`，**绝不返回部分或伪造结论** |
| **LLM 调用** | `asyncio.wait_for(..., trade_review_timeout_s)` 只包 LLM 调用；期间**不持有 DB 连接**（独立短连接 session） |
| **IDOR 归属** | 所有读写按 `account_login` 过滤（`get_for_user` / `get_latest_by_ticket` / `summary`） |
| **worker 租约** | DB 原子 claim + `lease_until` 心跳;启动 `recover()` 只中断过期任务（→ failed），不重跑 |
| **审计** | 触发/重审/batch 记 `log_audit(action="trade_review.trigger", actor=username, ip)` |

---

## 七、前端

- `/history` 表格新增「复盘」列：未复盘/失败 → 「复盘」按钮（Sparkles 高亮）；已完成 → 分类徽章 + 「查看/重审」；运行中灰态 loading。
- **TradeReviewDialog** 弹窗：四分类徽章（绿/蓝/琥珀/红 + 一句话解释 + 行动指引动词）+ 根因标签 + lessons + improvement_actions + confidence<分析 + **跨单模式统计行**（「近 30 天第 N 次 real_mistake」）+ **用户心态备注**（localStorage 本地保存）。
- 交互：POST 202 → 每 2s 轮询 GET 状态，terminal 停止；失败显示「复盘暂不可用请重试」；超限显示「今日次数已用完」。
- 顶部「有 N 笔待复盘」软提示。
- 翻译：独立 `messages/{zh,en}/tradeReview.json`（93 keys 两侧对称对齐）。

---

## 八、配置项（`config.py`）

| 键 | 默认 | 说明 |
|----|------|------|
| `trade_review_daily_quota` | 30 | 每日每用户复盘配额（Redis 计数） |
| `trade_review_max_batch` | 50 | 批量补齐上限 |
| `trade_review_timeout_s` | 120 | 单次 LLM 调用超时（wait_for 包裹） |
| `trade_review_lease_s` | 120 | worker 租约时长 |
| `trade_review_poll_s` | 2 | worker 空转轮询间隔 |
| `trade_review_timeframe` | M15 | 复盘行情窗口 K 线周期 |
| `trade_review_max_tokens` | 1200 | LLM 输出 token 上限 |

**常量**（`constants.py`）：四分类枚举字面量、`REVIEW_LOSS_CAUSES`（15）/ `REVIEW_WIN_CAUSES`（9）白名单、截断长度、`REVIEW_MIN_CONFIDENCE=0.5`、`REVIEW_WINDOW_DAYS=30`。

---

## 九、测试

- `test_trade_reviews.py`（37 用例）：输入组装（bot/manual/降级）、白名单校验、服务端四分类推导（self-report 不一致 flagged、低置信 flagged、**confidence 越界 fail-closed**、LLM None/畸形/超时/异常 fail-closed）、Store 状态机（IDOR/自然键幂等/claim 二次 None/lease recover/`previous` 保留 review_history/`reuse_or_create` force 复用/summary 计数）、**`_fetch_manual_deal` 三态**（found/not_found/bridge_down）、**`_run_one` 手动单全链路端到端**（bridge 回查 → 组装 → LLM → completed + skilled_win）、路由（幂等/force 复用/配额 429/并发竞态幂等兜底）、**输出侧清洗进链**（白名单外剥离 + secret 打码 + 超长截断）。
- `test_api_history.py`：account_login 透传（bot + manual 三处）。
- 前端 tsc 0 错误 + `npm run build` exit 0。

### code-review 修复（2026-10-11，评审 REQUEST CHANGES 后）

| Severity | 修复 |
|----------|------|
| CRITICAL | partial unique index 与 force/重试冲突 → `reuse_or_create` UPDATE 复用（不插第二行） |
| HIGH | 手动单链路断裂 → worker 按 (ticket, account_login) 从 bridge history 回查真实 deal |
| HIGH | `provider_name` `_provider=None` AttributeError → 空保护 |
| MEDIUM | IDOR 归属客户端可控 → 服务端 `manager.current_account_login` 推导 |
| MEDIUM | confidence 越界仅 flagged → fail-closed |
| MEDIUM | worker 运行期 lease 不回收 → 主循环周期 recover |

### 第二次 code-review 修复（2026-10-11，superpowers 独立复查，Verdict With fixes）

| Severity | 问题 | 修复 |
|----------|------|------|
| HIGH | 输出侧清洗未真正进链：`trade_reviewer` 本地 `_bounded_str` 只做 `s[:limit]` 截断，不含 `sanitize._redact_str` 的打码/单行化/去注入分隔符——白名单内自由文本（summary/lessons）里的明文 `sk-` 密钥原样持久化 | 本地 `_bounded_str` 改用 `_redact_str(str(value))`（单行化 + Bearer/sk-/secret 打码 + 去分隔符）再截断；删冗余 `@staticmethod _bounded_str` 跳板 |
| MEDIUM | POST 触发端手动单分支无账号归属校验（客户端 `account_login` 直接可用，多账号用户可为他账号触发复盘消耗 LLM 配额） | 手动单分支 `account_login` 改走 `_query_account_login(request, ...)` 服务端推导（与 GET 端一致） |
| MEDIUM | `reuse_or_create` 并发竞态（双请求同时 SELECT 无人 → 双双 INSERT 自然键冲突）无 IntegrityError 兜底，文档承诺 409 不实 | `trigger_review` 捕获 IntegrityError → 幂等查已有返回（不 500） |
| Minor | 冗余 `@staticmethod _bounded_str` 跳板 | 删除 |

**误报排查**（审查者漏读，验证后不修）：
- `review.loss_causes` 入库空值：`run()` 返回**顶层** causes，`finish()` 读顶层——链路正确，根因标签正常落库
- `/history` `pendingReviews` key miss：`history/page.tsx:59` 用 `useTranslations("tradeReview")`，key 就在该 namespace——正常
- migration ruff 格式（UP035/I001）：全部 20 个迁移都 `from typing import Sequence, Union`，alembic 目录被 pyproject exclude——改一个会破坏仓库一致性，不修

**新增测试**（35 → 37）：`test_run_output_sanitization_in_chain`（白名单外标签剥离 + secret 打码 + 超长截断）、`test_trigger_integrity_race_idempotent`（并发竞态幂等兜底）。

### 实盘反馈修复（2026-10-11）

| 问题 | 根因 | 修复 |
|------|------|------|
| 复盘后 `lessons`/`improvement_actions`/`summary` 字段为空 | **system prompt 未要求 LLM 输出这些字段**——规则只列了 `reasoning_correct`/根因/`confidence`，规则 5「只输出 JSON」未定义 JSON 结构 → LLM 大概率不生成 → `raw.get(...)` 为 None → `_validate_strings(None)`=[] / `_bounded_str(None)`="" | prompt 加规则 5/6：明确 JSON 必须包含且只包含 `reasoning_correct`/`loss_causes` 或 `win_causes`/`lessons`/`improvement_actions`/`summary`/`confidence`，并要求 lessons/actions/summary 用简体中文且必须存在 |
| 复盘列「对·赢」「错·亏」码值不直观 | 四分类缩写徽章无解释 | Badge 加 `title` tooltip 显示完整分类文案（「做对了·盈利」等） |
| 复盘列显示原始 key（`tradeReview.classShortSkilled_win`） | 分类值 `skilled_win` 是 snake_case，key 拼接 `首字母大写+原样` 生成 `classShortSkilled_win`，翻译文件 key 是驼峰 `classShortSkilledWin` → next-intl 找不到 key 渲染原始字符串 | 新增 `classKeyPascal()`（snake_case → PascalCase）统一转换，history 页与弹窗徽章共用 |

> 已有复盘记录不会自动补全——重新触发（force 重审）后按新 prompt 重新生成。

> **回归基线**：1211 通过；10 失败均为既有环境配置问题（8× multi_agent 因 `.env` 配 `MODEL_ORCHESTRATOR=deepseek-v4-flash`、2× feishu 因 `.env` 配 webhook），与本功能无关（feishu env 清空后 2/2 通过；ml_barrier 完整重跑 20/20 稳定）。第二次修复后 44 相关测试全过 + ruff 干净。

---

## 十、未来预留

- **教训库 × 开仓前提醒**：`preflight_order()` 命中 `trade_reviews.review_history`（real_mistake）→ 开仓前 warning「近 N 天出现过该根因 N 次」。表结构已预留（review_history 字段化 + summary 跨单统计）。
- **跨单模式识别**：连续 real_mistake 同根因 → 提升为交易纪律红旗（联动 discipline gate 冷却）。
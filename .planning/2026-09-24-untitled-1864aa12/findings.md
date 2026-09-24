# Findings & Decisions — 行情提醒（飞书通知）

## Requirements

1. 指定品种：价格 **大于或小于** 设定报价（阈值）
2. 持续时间：价格持续满足条件 **超过 X 秒**
3. 发送次数：条件满足后**总共发送 N 次**（连续触发不重复刷屏，达到上限后停止/冷却）
4. 通知渠道：飞书自定义机器人 Webhook
   - URL 由用户提供：`https://open.feishu.cn/open-apis/bot/v2/hook/c275dcc2-a2f0-4e60-a625-9a5cc155c5c9`
5. 消息格式：**飞书卡片消息**，需要好看、明了

## Research Findings（代码库勘察）

### 行情数据流
- 现有 tick 更新循环：`backend/app/bot/scheduler.py` `_tick_job` → 每 **1 秒** 触发一次
  - `_fetch_tick(symbol, engine)` 调用 `engine.market_data.get_current_tick(symbol)` 获取实时 bid/ask
  - 存 Redis price cache + push WebSocket 事件 `price_update`
  - 已有现成的数据拉取链路，可复用：**行情提醒依赖此 tick 循环**（1 秒粒度满足"持续时间 X 秒"判定）
- `MarketDataService.get_current_tick(symbol)` 返回 `{ bid, ask, spread, time }` 字典（`backend/app/mt5/market_data.py`）
- 市场开闭市判断：`is_market_open(symbol)`（`backend/app/bot/scheduler.py:17`，用 `app.market.sessions`）

### 现有通知架构（仅 Telegram）
- `backend/app/notifications/telegram.py` — `TelegramNotifier` 类，`_send()` 用 httpx POST 发文本消息
- 配置：`settings.telegram_bot_token` / `telegram_chat_id` / `telegram_proxy_url`（`backend/app/config.py:278-280`）
- **飞书集成尚不存在** —— 需新建 `feishu.py` 通知模块
- `notifier` 在 `main.py` lifespan 初始化后注入 BotManager / BotEngine

### 定时任务模式
- `backend/app/bot/scheduler.py` — APScheduler `AsyncIOScheduler`
- `start()` 内注册任务：`add_job(self._tick_job, "interval", seconds=1, id="bot_tick", ...)`
- candle / sentiment / health 等均通过 `add_job` 注册
- `_track_task(coro)` 后台任务管理、`_log_gather_errors` 错误聚合——可复用模式
- **行情提醒的巡检任务**可在 scheduler 注册（如每 1-5 秒一次），遍历已配置的提醒规则

### API 路由模式
- 新路由必须用 `make_authed_router(prefix, tags)`（`backend/app/api/router_factory.py`）— 自动挂 `require_auth`，防止漏鉴权
- 在 `app/main.py` 中 `from app.api.routes import ...` + `app.include_router(...)`
- DB 会话：新代码必须用 `app.db.session.transaction()` 或 `async_session()`，**不要**用共享长连接 `BotEngine.db`

### 数据持久化模式
- SQLAlchemy 2.0 async + `DeclarativeBase`（`backend/app/db/models.py`）
- 需要新增表：`price_alerts`（行情提醒规则）— 含 symbol、方向、阈值、持续时间、最大发送次数、状态等
- Alembic 迁移：**实际 head = `a9b8c7d6e5f4`（manual trading firewall）**，其 `down_revision = "z0a1b2c3d4e5"`（add_account_isolation 是倒数第二个）。已用 `alembic heads` 验证。新迁移 `down_revision = "a9b8c7d6e5f4"`
- ⚠️ revision ID `a1b2c3d4e5f6` 已被 `a1b2c3d4e5f6_add_new_event_types.py` 占用（已 grep 验证），新迁移必须用全新 ID
- 注意坑：datetime 必须用 `datetime.utcnow()`（naive），asyncpg 拒绝 offset-aware

### 前端模式
- 页面在 `frontend/app/<page>/page.tsx`
- API 客户端集中在 `frontend/lib/api.ts`（axios，默认前缀 `/api`，带 auth 拦截器）
- 侧边栏导航：`frontend/components/layout/Sidebar.tsx`，`NavItem { href, labelKey, icon }`，按 group 组织
- 组件库：`frontend/components/ui/`（card, badge, button, switch, table, dialog 等 33 个原语）

### 飞书群自定义机器人
- Webhook endpoint：`POST https://open.feishu.cn/open-apis/bot/v2/hook/{token}`
- 支持 `msg_type: "interactive"` 内联卡片（含 `card` JSON 结构），不用先建卡片模板
- 卡片 JSON 结构：
  - `header`（卡片标题：title 含 icon + text）
  - `elements`：`div`（文本框，支持 lark_md 标签）、`hr`（分隔线）、`note`（备注）、`action`（按钮）等
  - `config`：`wide_screen_mode`、`update_multi`
- 认证：无需额外签名（群自定义机器人只要 hook URL；安全设置可配签名，此处未提供签名 secret）
- 卡片"好看、明了"→ 用 header 图标 + 主指标大字体 + 条件明细 div + 时间 note

## Technical Decisions

| 决策 | 理由 |
|------|------|
| 用 `msg_type="interactive"` 内联卡片而非 pre-create card_id | 一步到位，custom bot 支持；无需管理卡片模板生命周期 |
| 提醒巡检挂在现有 `bot_tick`（1s）循环上而非新建独立 task | 复用现有行情拉取，避免双倍连接/负载；判定粒度天然满足 |
| 提醒规则存 PostgreSQL（新表 `price_alerts`） | 迁移、历史管理、与 AI/手动/机器人共存统一；不够内存 Redis（重启丢失） |
| 规则 CRUD 走 REST API（`/api/price-alerts`）using `make_authed_router` | 与全站鉴权模式一致，防止漏鉴权 |
| 连续触发防刷屏用"达到 max_notifications 后置 inactive/冷却" | 满足"总共发送多少次"需求，避免夜间无限轰炸 |
| 持续时长判定：tick 循环内维护"首次突破时间"状态（Redis + 内存） | 幂等可重启；1s 粒度够用 |
| 飞书 webhook URL 存储于 `settings.feishu_webhook_url`（env）或 alert 表 | 不硬编码在代码里；与 telegram 的 token 配置模式一致 |

## 预审补充（自主发现 + critic 评审合并）

### 飞书卡片 payload 需实测验证
- 不要只凭记忆写卡片 JSON。Phase 3 第一步：**curl 发送最小 interactive 卡片**验证 webhook 可达 + 格式正确，再加完善样式。
- 已知 custom bot acceptance：`{"msg_type":"interactive","card":{...}}` 中 `card` 需含 `config.wide_screen_mode`、`header.title`、`elements`。

### webhook URL 是机密
- 含 token 的 URL 属于 secret：**只存 `settings.feishu_webhook_url`（env）**，禁止进代码/DB/前端回显完整值。
- UI 显示"已配置/未配置"布尔状态即可。

### "持续 N 秒"语义 = 严格连续
- 价格跌破再涨回 → 重新计时（严格连续满足）。记录为明确决策，避免歧义。

### 数据不可用时的健壮性
- Bridge 断连 / 市场休市 → `get_current_tick` 返回 None → 该规则本轮**跳过**（不重置 first_trigger，不清计数），标记数据不可用日志。避免误触发/误重置。

### 重入保护
- 提醒发送用 per-alert asyncio 锁或状态机（`sending` 标记），防止跨 tick 竞态双发。
- scheduler tick 本身 `max_instances=1 + coalesce` 已防并发，但发送是异步的。

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| 无 | — |

## Resources

- 飞书 webhook：`<FEISHU_WEBHOOK_URL>`（含 token 的完整 URL 由用户提供，存 env，**不进计划文件/代码/DB**）
- 飞书机器人卡片格式：https://open.feishu.cn/document/client-docs/card/... （custom bot 用 interactive）
- 参考代码：`backend/app/notifications/telegram.py`、`backend/app/bot/scheduler.py`、`backend/app/api/router_factory.py`

## 飞书卡片 JSON schema（实现模板，critic L1）

> 用 `msg_type="interactive"` 内联卡片，POST 到 webhook。以下为设计模板，实施时先用最小 payload curl 验证。

```json
{
  "msg_type": "interactive",
  "card": {
    "config": { "wide_screen_mode": true, "enable_forward": true },
    "header": {
      "title": { "tag": "plain_text", "content": "🚨 GOLD 行情提醒 — 突破 3350.00" },
      "template": "red"   // above=red，below=blue，按方向选择
    },
    "elements": [
      {
        "tag": "div",
        "text": {
          "tag": "lark_md",
          "content": "**当前价：<font color='red'>3352.50</font>**"
        }
      },
      { "tag": "hr" },
      {
        "tag": "div",
        "text": {
          "tag": "lark_md",
          "content": "📈 **触发条件**\n- 方向：价格 > 3350.00\n- 持续：已超过 60 秒\n- 已发送：1 / 5 次"
        }
      },
      {
        "tag": "note",
        "elements": [
          { "tag": "plain_text", "content": "触发时间：2026-09-24 16:30:00 (UTC+7) · 按买入价 bid 判定" }
        ]
      }
    ]
  }
}
```

- 注意：lark_md 的 `<font color='red'>` 颜色标签支持有限色值；粗体用 `**text**`
- price_decimals 来自品种配置（GOLD=2，USDJPY=3）
- 若飞书群配置了签名校验，需在 header 加 `timestamp`+`sign` 字段（待用户确认后实现）
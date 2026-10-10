# Findings — 飞书定时发送 K 线图片

## 需求原文
> 对于启用的品种，有行情数据时，每隔15分钟发送飞书 15分钟和1小时的k线图片，包括3条均线和移动平均线55日的。晚上12点以后就不发了。

## 已确认的代码事实

### 调度器（`backend/app/bot/scheduler.py`）
- `BotScheduler` 持有全部引擎：`_engines` / `_engines_snapshot()`（dict[symbol → BotEngine]）
- 现有注入模式：`set_price_alert_service()` 在 main.py lifespan 注入独立服务 → 注册独立 APScheduler job
- 行情提醒 job：`_price_alert_job()` 每 2 秒调 `service.check_all()`，失败仅记日志不影响主循环（`max_instances=1, coalesce=True`）
- 时间基准：UTC 为主（`datetime.utcnow()` / `datetime.now(UTC)`），前端 Asia/Shanghai 显示
- `TIMEFRAME_CRON` 已定义 M15/H1 的 cron 表达式

### K 线数据（`backend/app/mt5/market_data.py` + `backend/app/data/collector.py`）
- `MarketDataService.get_ohlcv(symbol, timeframe="M15", count=100)` → `pd.DataFrame`（time 索引 + open/high/low/close/volume），直接调 MT5 Bridge
- `HistoricalDataCollector.load_from_db(symbol, timeframe, from_date, to_date)` → 从 DB 读已采集的历史 K 线（用于长均线 MA55 需要更多数据时）
- 项目统一用 `to_broker_alias()` 做品种名映射，`SYMBOL_PROFILES` 提供 display_name / price_decimals
- 行情有效性：`scheduler.is_market_open(symbol)`（按资产类目判定）；价格 tick 写 Redis `price:cache:{symbol}`

### 飞书通知（`backend/app/notifications/feishu.py`）
- `FeishuNotifier`：webhook 配置优先 Vault（`FEISHU_WEBHOOK_URL`）→ env 回落；`reload_from()` 运行期刷新
- `_post(payload)` 发 `msg_type="interactive"` 卡片，成功返回 True / 失败返回 False（不抛出）
- 现有能力：文本卡片（price alert / test card）

### 依赖现状（关键缺口）
| 位置 | matplotlib | mplfinance | pillow | numpy |
|------|-----------|-----------|--------|-------|
| 本地 venv (.venv python3.12) | ✅ 3.11.2 | ❌ | ✅ 12.3.0 | ✅ 2.5.3 |
| backend/requirements.txt | ❌ | ❌ | ❌ | ❌（pandas 间接） |
| backend/Dockerfile | ❌ | ❌ | ❌ | ❌ |

**生产镜像没有绘图依赖**，`requirements.txt` 需新增绘图库（建议 `mplfinance`，依赖 matplotlib）。

## 关键技术风险（飞书发图）

**自定义机器人 webhook 无法直接发送图片**。已确认事实：
- webhook 自定义机器人仅支持 `text` / `post` / `interactive`（卡片）消息类型，**不支持 `msg_type=image`**
- 卡片 `img` 元素（`{"tag":"img","img_key":...}`）需要 **image_key**，且 **不支持直接填 URL**
- `image_key` 只能通过开放平台上传接口 `POST /open-apis/im/v1/images` 获取，需要 **tenant_access_token**（由 **app_id + app_secret** 换取）
- 自定义机器人 webhook 本身没有 app 凭据 → **必须在飞书开放平台创建一个应用**（仅用于上传图片，机器人可不入群），拿到 `app_id`/`app_secret`，授予图片上传权限（`im:resource`）

**可行的发图路径**（推荐）：
1. 用户创建飞书开放平台应用 → 获取 `app_id` + `app_secret`
2. 后端用 app 凭据换 `tenant_access_token`（缓存 2h）
3. 本地用 matplotlib/mplfinance 画 K 线图 → Pillow 转 PNG bytes
4. `POST /open-apis/im/v1/images` 上传 PNG → 得 `image_key`
5. 走现有 webhook 发 `interactive` 卡片，elements 内嵌 `img` 元素

### 备选路径（需评估）
- **A. 应用机器人直接发消息**：应用机器人可直接发 `msg_type=image`（`im/v1/messages`），需把应用机器人拉入目标群，webhook 变为应用 API（含 token 管理、签名、tenant_access_token 换取），与现有 `FeishuNotifier` webhook 架构差异大。
- **B. image_key 缓存复用**：同一 K 线图 image_key 全局有效、有 TTL，可上传一次缓存；但每 15 分钟每品种图都变 → 仍需每次上传。

## 用户已确认的决策（2026-10-10）
1. **飞书发图方式**：✅ 接受创建飞书开放平台应用（app_id + app_secret），仅用于上传图片，走现有 webhook 发卡片
2. **均线**：✅ MA5 / MA10 / MA20（3 条）+ 固定叠加 MA55
3. **夜窗语义**：✅ Asia/Shanghai 00:00–08:00 不发送，其余时段每 15 分钟发送
4. **实现方向**：✅ 每品种独立定时 job（每 15 分钟）遍历启用引擎；M15 图读 M15 K 线 + MA55，H1 图读 H1 K 线 + MA55；以「有行情数据」为准

## 时区与市场语义（复用项目约定）
- 日界 22:00 UTC 外汇日；后端时间落库统一 naive UTC；前端 Asia/Shanghai 显示
- "有行情数据时" = `is_market_open(symbol)` 为真 + `get_ohlcv` 返回非空 DataFrame

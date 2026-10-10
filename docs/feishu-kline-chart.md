# 飞书定时 K 线图发送

> 2026-10-10 实施。对启用的品种，有行情数据时每 15 分钟向飞书发送该品种的 **M15** 和 **H1** 两张 K 线图，图上叠加 **MA5/MA10/MA20 + MA55** 均线；Asia/Shanghai 时间 **00:00–08:00 不发送**。

## 功能概述

- **触发**：scheduler cron job `kline_chart_broadcast`，`minute="0,15,30,45"`（每 15 分钟）。
- **范围**：遍历当前启用引擎（`scheduler._engines_snapshot()`）；`is_market_open(symbol)` 为真才处理。
- **每品种输出**：M15 图 + H1 图，各一张飞书 `interactive` 卡片（内嵌 `img` 元素）。
- **夜窗**：Asia/Shanghai 00:00–08:00 跳过（`app.services.kline_sender.is_night_window`）。
- **失败隔离**：单品种失败仅记录日志，不影响其他品种（对齐 `_price_alert_job` 模式）。

## ⚠️ 品种 ID 与账号隔离（重要）

不同 MT5 账号（不同券商）对同一 canonical 品种的 **broker_alias 可能不同**。
例如本机券商 GOLD 的桥端符号是 `GOLD_`（DB `symbol_configs` 同时存过 `GOLD#`），
但切换到另一个账号后可能是 `XAUUSD` 或 `GOLD`。

KlineSender 取行情走 `engine.market_data.get_ohlcv()` → `MT5BridgeConnector`
→ `to_broker_alias()`。**别名只在 DB `symbol_configs` 按账号加载到
`SYMBOL_PROFILES` 后才存在**。因此：

- 每个调用 MT5 Bridge 的进程（backend 主进程、MCP 子进程、agent runner）
  启动时**必须执行 `load_profiles_into_memory()`**，否则 `to_broker_alias`
  退化原样返回 canonical，行情请求打到桥上报 `No OHLCV data`。
- **账号切换后** `account_switch` 会重载 profiles 为新账号的别名/品种，
  `to_broker_alias` 自动跟随。不要手工硬编码品种 ID。
- 排查 "K 线图没数据" 时先确认：当前账号的 `symbol_configs.broker_alias`
  是否指向券商真实符号（可用 Bridge `/symbols` 列表核对）。

回归测试锁住此契约：`tests/unit/test_market_data_alias.py`（桥前必须用别名）、
`tests/unit/test_symbol_resolver.py`（账号切换跟随新别名）、
`tests/unit/test_kline_sender.py`（KlineSender 透传引擎品种名不做二次映射）。

## 关键文件

| 文件 | 职责 |
|------|------|
| `backend/app/notifications/feishu_image.py` | `FeishuImageUploader`：app 凭据 → `tenant_access_token`（缓存 2h）→ 上传图片 → `image_key` |
| `backend/app/notifications/kline_chart.py` | `build_kline_png`：mplfinance 渲染蜡烛图 + 均线 → PNG bytes（图内英文标题） |
| `backend/app/services/kline_sender.py` | `KlineSender`：夜窗判定 + 遍历品种 + 取数据 + 画图 + 上传 + 发卡 |
| `backend/app/notifications/feishu.py` | `FeishuNotifier.send_kline_card`：卡片内嵌 img 元素 |
| `backend/app/bot/scheduler.py` | `set_kline_sender` + cron job 注册 |
| `backend/app/main.py` | lifespan 装配 `FeishuImageUploader` + `KlineSender` |

## 前置依赖（重要）

**飞书自定义机器人 webhook 不支持直接发送图片**。图片必须先经开放平台上传接口
`POST /open-apis/im/v1/images` 获取 `image_key`，再用卡片 `img` 元素发送。
获取 `image_key` 需要开放平台应用凭据（`app_id` + `app_secret`），换取
`tenant_access_token`。

因此使用本功能前，需要在 **飞书开放平台创建一个应用**（机器人无需入群，
仅用于上传图片）：

1. 进入 [飞书开放平台](https://open.feishu.cn/) → 创建企业自建应用
2. 获取 `App ID`（如 `cli_a9f...`）与 `App Secret`
3. 在「权限管理」中为应用开通图片上传相关权限（`im:resource`）
4. 发布应用（测试环境可自建应用，发布给本企业）

## 配置

| 环境变量 | 说明 |
|----------|------|
| `FEISHU_APP_ID` | 开放平台应用 App ID |
| `FEISHU_APP_SECRET` | 开放平台应用 App Secret |
| `FEISHU_WEBHOOK_URL` | 群自定义机器人 webhook（发卡片，原有配置） |

配置来源：`FEISHU_APP_ID` / `FEISHU_APP_SECRET` 读 Settings（env 或 Vault），
对齐 `FEISHU_WEBHOOK_URL` 的 Vault → env 回落模式。

> ⚠️ 凭据属机密：只存 env / Vault，禁止进代码、DB、前端。

## 图内语言

K 线图内标题/注脚使用**英文**（如 `Gold (XAUUSD) M15 K-line Chart`）。
生产 Docker（python:3.12-slim）无中文字体，中文标题会渲染成方框；英文规避
字体依赖。中文展示只在飞书卡片文本（由飞书渲染，不依赖本地字体）。

## 验证

```bash
# 本地生成样例 K 线图
cd backend && MPLBACKEND=Agg .venv/bin/python -c "
import pandas as pd, numpy as np
from app.notifications.kline_chart import build_kline_png
idx = pd.date_range('2026-01-01', periods=100, freq='15min')
df = pd.DataFrame({'open':1,'high':2,'low':0.5,'close':1.5,'volume':100}, index=idx)
png = build_kline_png('GOLD','M15',df)
open('/tmp/k.png','wb').write(png)
"

# 单元测试
cd backend && .venv/bin/python -m pytest tests/unit/test_kline_chart.py tests/unit/test_feishu_image.py tests/unit/test_kline_sender.py -q --no-cov
```

## 部署 checklist

- [ ] Railway env 配置 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（或 Vault）
- [ ] `FEISHU_WEBHOOK_URL` 已配置（原有）
- [ ] 飞书开放平台应用已开通 `im:resource` 权限并发布
- [ ] 启动后观察日志：`Kline chart broadcast job scheduled (every 15 min)`
- [ ] 首个 15 分钟周期验证群里收到 M15 + H1 两张图

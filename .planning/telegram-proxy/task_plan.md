# Task: Telegram 404 修复 + 可配置代理

## 问题 1：Telegram 测试返回 404
- 404 来自 Telegram API `getMe`/`sendMessage`：token 无效时 Telegram 返回 404 Not Found
- `backend/.env` 中 `TELEGRAM_BOT_TOKEN=your_bot_token` 是占位符（需用户填入真实 token，或经 vault secrets 注入）

## 问题 2：可配置代理
- 新增 `telegram_proxy_url` 配置项（默认空=直连）
- 当前代理：`http://127.0.0.1:7897`（用户消息写 17.0.0.1，判断为笔误）

## Steps
- [x] 定位所有 api.telegram.org 调用点：notifications/telegram.py, api/routes/integration.py, api/routes/secrets.py, mt5_bridge/watchdog.py
- [x] config.py 加 `telegram_proxy_url: str = ""`
- [x] 三处 httpx.AsyncClient 传 `proxy=settings.telegram_proxy_url or None`
- [x] 404 时返回明确提示"token 无效"
- [x] .env / .env.example 加 TELEGRAM_PROXY_URL
- [x] 验证 import + 语法

## Result
- 404 根因: .env 中 token 是占位符 your_bot_token → Telegram 对无效 token 返回 404
- 代理已实测连通（fake token 经代理返回 401 = Telegram 可达）

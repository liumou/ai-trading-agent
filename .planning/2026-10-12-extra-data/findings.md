# Findings — 飞书「Extra data」发送异常 + K线图未执行

## 现象
1. 日志每 2 秒刷 `ERROR app.notifications.feishu:_post - Feishu send exception: Extra data: line 1 column 5 (char 4)`，对应价格提醒发送失败，名额回滚 → 无限重试。
2. 用户反馈「定时飞书发图片没执行」：K 线发送 job 每 15 分钟触发，但卡片发不出去（`Kline sender [...]: card send failed`）。

## 直接根因
`FeishuNotifier._post` 旧实现：
```python
body = resp.json()  # 无保护解析
```
响应体非标准 JSON（如网关错误页 HTML）时 `json.loads` 抛 `Extra data`，被 `except Exception` 吞成一行无上下文 error（无 status_code / content-type / body）。

## 环境根因（实测确认）
本机 `.env` 配置的标准 webhook `https://open.feishu.cn/open-apis/bot/v2/hook/<uuid>` 实测：
| 请求方式 | 结果 |
|---------|------|
| httpx 默认 UA（`python-httpx/0.28.1`） | **502 HTML "Bad Gateway"** |
| httpx / curl 带 `User-Agent: curl/8.0` | **200 `{"code":0,"msg":"success"}`** |

DNS 解析 `open.feishu.cn` → `198.18.2.18`（Clash/Surge 类代理虚拟 IP 段）。本机走 Clash 代理，其 UA 分流规则把 `python-httpx` 识别为「非浏览器/爬虫」而 502 拦截。

**这是本地开发环境特有问题**：生产 Railway 无此代理，飞书发送一直正常（记忆：飞书定时K线图 2026-10-10 已实施并成功）。

## 修复（feishu.py `_post`）
1. **UA 修复**：`httpx.AsyncClient(headers={"User-Agent": "curl/8.0"})` → 绕过 Clash UA 拦截，真实发送成功。
2. **健壮日志**：错误分四段带上下文：
   - transport 异常：`err=<type>: <msg>`（host 脱敏）
   - 非 JSON 体：`status + content-type + body[:120]`（Extra data 根因可一眼定位）
   - JSON 但非 dict：防御性拒绝
   - 业务码非 0 / 非 200：`status + code + msg`
3. **`_mask_host`**：URL 含 token 属机密，日志只留 host。

## 关键结论
- **K 线发送 job 一直在执行**，只是卡片发送失败（与价格提醒同根因）。
- **运行中的后端是旧代码**（PID 81134 10:44 启动，代码 11:09 修改）→ **需重启后端生效**。
- 重启后 UA 生效，K 线图与价格提醒都应恢复。

## 第二层根因（2026-10-12 11:25 用户实测后暴露）
UA 修复后请求到达飞书，但报 **`code=19001 incoming webhook access token invalid`**。

**Vault 覆盖 env 的假 token**：main.py:427-444 配置优先级 Vault → env。集成页今天凌晨 03:21:30 保存过 `https://open.feishu.cn/open-apis/bot/v2/hook/abc123`（**`abc123` 占位 token**）到 Vault，程序启动即用该假 token → 19001。`.env` 的真 token 实测有效（code=0），但被 Vault 覆盖。

**修复**（用户授权"用 .env 有效 token 覆盖"）：
1. 用 VaultService 把 `.env` 有效 token 加密写回 Secret 表（token 前缀 `c275dc...`，与 .env 一致）。
2. mint 内部 token 调 `PUT /api/integration/config` → `notifier.reload_from()` 刷新运行实例（无需重启）。
3. 实测 `POST /api/price-alerts/test` → **200 `{"status":"sent"}`**，飞书真实收到。
4. reload 后日志：价格提醒 alert 2/3 正常触发且 `deactivate_if_saturated`（发送成功），无 ERROR。
   - 注：alert 2/3 因达 max_notifications 被自动停用（设计行为），用户需重新开启。

## 测试
`tests/unit/test_feishu_notifier.py` 9 用例覆盖 `_post` 全分支 + UA 锁定 + host 脱敏；feishu/price_alert/kline 相关 66 用例全过；ruff 全绿。集成测试 2 个前置失败为本机 .env 有 webhook 导致（`FEISHU_WEBHOOK_URL=""` 模拟无 env 即 4 passed）。

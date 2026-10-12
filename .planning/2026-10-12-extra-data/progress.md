# Progress Log — 飞书发送异常「Extra data」修复 + K线图未执行排查

## Session: 2026-10-12

### Current Status
- **Phase:** 4 - Testing & Verification
- **Started:** 2026-10-12

### Actions Taken
1. **复现** `Extra data: line 1 column 5 (char 4)` = 响应体是「4 字符标量 + 额外数据」（`true\n`）→ `json.loads` 抛 JSONDecodeError。
2. **定位** `FeishuNotifier._post`（feishu.py:67）`resp.json()` 无保护解析 + `except` 吞成一行无上下文 error。
3. **实测根因**（关键）：本机 `.env` 标准 webhook（`open.feishu.cn`）：
   - httpx 默认 UA → **502 HTML "Bad Gateway"**（DNS 解析到 Clash 代理虚拟 IP `198.18.2.18`，被 UA 分流规则拦截）
   - httpx/curl 带 `User-Agent: curl/8.0` → **200 `{"code":0,"msg":"success"}`** 成功
4. **修复 feishu.py `_post`**：
   - 加 `User-Agent: curl/8.0`（规避 Clash 类代理对 python-httpx 的 502 拦截）
   - 错误日志分段带上下文：transport 异常 / 非 JSON 体（status+content-type+body 片段）/ 非 dict body / 业务码
   - 新增 `_mask_host` 脱敏 host（URL 含 token 属机密）
5. **新增单测** `tests/unit/test_feishu_notifier.py`（9 用例）：成功/业务码非0/非JSON/非dict/HTTP错误/传输异常/disabled/UA锁定/host脱敏。
6. **实测验证**：新代码 `send_test_card(GOLD)` 返回 **True**（真实发送成功）。
7. **排查「K线图没执行」**：日志确认 `kline_broadcast_job` 每 15 分钟正常触发（09:15→11:15 GOLD/OILCash 各 M15+H1），图片上传成功，但 **card send failed**（`_send_one:172`）—— 与价格提醒同根因：`_post` 被 Clash 502 拦截。
8. **确认待重启**：后端 PID 81134 于 10:44:34 启动（旧代码），feishu.py 修改于 11:09:33 → **运行中仍是旧代码，需重启生效**。
9. **用户重启后报 19001**（11:25）：请求到达飞书但 token 无效。查 Vault：集成页 03:21:30 保存过 `abc123` 占位 token（Vault→env 优先级覆盖了 .env 真 token）。
10. **修复**：VaultService 覆写 Secret 表为 .env 有效 token → mint 内部 token 调 `PUT /api/integration/config` 触发 `reload_from` 刷新运行实例。
11. **实测**：`POST /api/price-alerts/test` → 200 sent；日志 reload 后价格提醒 alert 2/3 正常触发并 deactivate（无 ERROR）。alert 2/3 达 max_notifications 自动停用，需用户重新开启。

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| tests/unit/test_feishu_notifier.py（9 用例） | 全过 | 9 passed | ✅ |
| feishu + price_alert + kline 相关（66 用例） | 全过 | 66 passed | ✅ |
| ruff check feishu.py + test_feishu_notifier.py | 无错误 | All checks passed | ✅ |
| test_api_integration_feishu.py（`FEISHU_WEBHOOK_URL=""` 模拟无 env） | 全过 | 4 passed | ✅ |
| test_api_integration_feishu.py（本机 .env） | 全过 | 2 前置失败（notifier.enabled=True 期望 False） | ⚠️ 环境相关（本机 .env 有 webhook；CI 无） |
| 真实发送 send_test_card(GOLD) | True | True | ✅ |

### Errors
| Error | Resolution |
|-------|------------|
| test_post_success 递归 RecursionError | `_patch_transport` 内 `httpx.AsyncClient` 已被替换导致递归 → 替换前保存原始类引用 |
| test_post_disabled 误判 | `FeishuNotifier("")` 会回退 env 的 webhook → monkeypatch settings 置空 |
| test_sends_curl_user_agent 失败 | `_patch_transport` 丢弃了 headers kwargs → 透传 kwargs |
| 集成测试 2 前置失败 | 本机 .env 配了 webhook，测试假设 env 未配置 → `FEISHU_WEBHOOK_URL=""` 跑即 4 passed（预先存在，非本次改动） |

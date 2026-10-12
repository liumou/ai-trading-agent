# Task Plan: 飞书价格提醒发送异常「Extra data」修复

## Goal
消除飞书 webhook 发送时 `Extra data: line 1 column 5 (char 4)` 的无上下文 ERROR 刷屏，让失败日志**可诊断**（含 HTTP 状态码 / content-type / 响应体片段），并防御响应体非 JSON / 非 dict 场景。

## 根因（已确认，两层）
**直接根因**：`FeishuNotifier._post`（feishu.py:59-74）里 `body = resp.json()`（:67）无保护地解析飞书响应。实验复现：响应体是「4 字符标量 JSON（如 `true` / `null`）+ 额外数据」时 `json.loads` 抛 `Extra data: line 1 column 5 (char 4)`（JSONDecodeError）。该异常被 `except Exception` 吞成一行 `Feishu send exception: ...`，**既无 status_code、也无 content-type、更无响应体片段** —— 无法定位。

**环境根因（实测确认）**：本机 `.env` 配的标准 webhook（`open.feishu.cn/open-apis/bot/v2/hook/<uuid>`）实测：
- httpx 默认 UA（`python-httpx/x`）→ **502 HTML "Bad Gateway"**（body 为 HTML 错误页，`resp.json()` 抛 `Extra data`）
- curl / httpx 带 `User-Agent: curl/8.0` → **200 `{"code":0,"msg":"success"}`** 成功

DNS 解析 `open.feishu.cn` → `198.18.2.18`（Clash/Surge 类代理的虚拟 IP 段），本机走 Clash 代理，其 UA 分流规则把 `python-httpx` 识别为非浏览器而 502。**这是本地开发环境特有问题；生产 Railway 无此代理，飞书发送一直正常**（记忆：飞书定时K线图已实施并成功）。

副作用链：`_post` 返回 False → `price_alert_service._dispatch_send` 回滚名额（设计如此）→ 下轮每 2 秒重试 → 持续刷屏。

## Next Step
（已完成）UA 修复 + `_post` 健壮日志 + 清除 Vault 假 token（`abc123`，19001 根因）+ reload 运行实例；实测发送成功。

## 第二层根因（追加，2026-10-12 11:25）
UA 修复后请求到达飞书，但报 **`code=19001 token invalid`**：main.py Vault→env 优先级，集成页 03:21 保存过 `abc123` 占位 token 到 Vault，程序用假 token。
**修复**：Vault 覆写为 `.env` 有效 token + `PUT /api/integration/config` 触发 `reload_from` 刷新运行实例 → 实测 `POST /api/price-alerts/test` 200 sent，价格提醒 alert 2/3 正常触发（deactivate_if_saturated = 发送成功）。

## Current Phase
Phase 3: Implementation

## Phases

### Phase 1: Requirements & Discovery
- [x] 复现 `Extra data: line 1 column 5 (char 4)` 对应响应体形态（4 字符标量 + 额外数据）
- [x] 定位 `_post` 异常吞噬路径（feishu.py:67 无上下文 except）
- [x] 确认生产调用方：price_alert_service（价格提醒）+ kline_sender（K 线图）+ price_alerts 测试卡片
- [x] 确认 webhook 配置来源：Vault → env（`FEISHU_WEBHOOK_URL`），本机 env 未配置（raw_len=0）
- **Status:** complete

### Phase 2: Planning & Structure
- [x] 修复点：仅 `_post` 一处；保持绝不抛出契约 + 成功判定（HTTP 200 且 code==0）
- [x] 新增防御：响应体非 dict（如 `true` 解析成功后 `body.get` 会 AttributeError）
- [x] 日志上下文：host 脱敏（URL 含 token 属机密）、响应体截断
- **Status:** complete

### Phase 3: Implementation
- [x] `_post` 重构：transport 异常 / 非 JSON 体 / 非 dict body / 业务码 各带上下文日志
- [x] `User-Agent: curl/8.0`（规避 Clash 类代理对 python-httpx 的 502 拦截）
- [x] `_mask_host` 脱敏 host（URL 含 token 属机密）
- [x] 新增单测 9 用例：非 JSON / 非 dict / 传输异常 / HTTP 错误 / 业务码非0 / 成功 / disabled / UA 锁定 / host 脱敏
- **Status:** complete

### Phase 4: Testing & Verification
- [x] feishu/price_alert/kline/vault 相关 79 用例全过
- [x] ruff check feishu.py + test_feishu_notifier.py 全绿
- [x] 实测真实发送 send_test_card(GOLD) → True（修复生效）
- [x] 集成测试：`FEISHU_WEBHOOK_URL=""` 模拟无 env → 4 passed（本机 .env 有 webhook 导致的前置失败为环境问题）
- [x] 排查 K 线未执行：job 每 15 分钟触发但卡片发送失败（同根因 502）；待重启后端生效
- **Status:** complete

### Phase 5: Delivery
- [x] 更新 findings.md / progress.md / task_plan.md
- [x] 交付修复说明
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 只改 `_post`，不动 price_alert_service 重试语义 | 发送失败回滚名额是既有设计（防一次抖动耗尽名额）；重试频率属另一话题，不夹带 |
| 解析失败改记「带状态码+content-type+响应体片段」的 ERROR | 让运维一眼看出 webhook URL 配置/网关问题，而非一行无上下文 `Extra data` |
| 响应体非 dict 时防御性拒绝 | `true` 等标量解析成功后 `body.get("code")` 会 AttributeError |
| host 脱敏 + 响应体截断 | webhook URL 含 token 属机密，禁止进日志明文 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| 暂无 | | |

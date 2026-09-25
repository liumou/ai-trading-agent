# Findings & Decisions

## Requirements
- 用户配置"黄金高于 3270"提醒，未收到飞书消息，且无任何提示。需要找到根因并修复，使失败可诊断、可恢复。

## Research Findings

### 根因（已确认，有代码证据）
1. **主因：`FEISHU_WEBHOOK_URL` 未配置。**
   `backend/.env` 与 `.env.example` 中只有 `TELEGRAM_BOT_TOKEN/CHAT_ID/PROXY_URL`，无任何 FEISHU 项。
   `config.py:283` 默认 `feishu_webhook_url=""` → `feishu.py:38` `enabled=False` →
   `price_alert_service.check_all()` 入口 `return`，**无日志、无告警**，引擎每 2s 空转。
2. **次因（真 bug）：发送失败不回滚计数。**
   `_claim_notification` 先 `sent_count+1` 落库提交，再调飞书；失败不回滚，
   且 `if not claimed:` 分支（含 DB 异常返回的 `False`）也会调 `_deactivate_if_saturated`。
   默认 `max_notifications=1` → **一次网络抖动即永久停用规则**，而 UI 仍显示"启用中"。
3. **诊断黑洞：** 飞书状态在 `/api/integration`、`/api/notifications` 均无暴露；
   `price_alert_service.py` 全文件仅 1 条 `logger.info`，无触发/成功日志。
4. **测试盲区：** 8 个测试的 `FakeFeishu` 恒返回 `True`，发送失败路径零覆盖。

### 已排除的假设（勿重复排查）
| 假设 | 结论 |
|------|------|
| Alembic 迁移未进链 / 表不存在 | 否。`f0e1d2c3b4a5` 是唯一 head，链深 32，完整（d3e0458 那次 500 报错已修复） |
| Redis 缓存键不对齐 | 否。写 `price:cache:{sym}`（scheduler.py:374）↔ 读同前缀（service.py:25,190） |
| 缓存太陈旧 | 否。tick_job 每 1s 写，TTL 10s |
| tick 无 `bid` 字段 | 否。`mt5_bridge/main.py:286` |
| 品种名不一致 | 否。引擎键与规则都归一化为 `GOLD` |
| 前端默认值异常 | 否。`duration_seconds=60`，合理 |

### 顺带发现的既有缺陷（本次未修，超出范围）
- **`testAll` 永远报失败：** `integration/page.tsx:99` 用
  `every(r => r.status === "connected")`，而 `_test_tradingview` 只返回 `"configured"`。
  因此 "Test All" 按钮现在就已永远显示"Some services failed"。
  **这是选择把飞书状态放在 `/api/price-alerts/status` 而非 `/api/integration` 的直接原因**——
  塞进 integration 会把该缺陷从 1 个服务扩散到 2 个。
- **全量 `-x` 测试失败：** `test_backtest_consistency.py::TestRiskManagerForSymbol::test_gold_reads_profile`
  单独跑通过，全量跑失败，伴随 `Connection._cancel was never awaited` 泄漏警告
  （来自 accounts 测试）。经 stash 前后对照确认为**预先存在**，与本次改动无关。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 发送失败回滚，而非"先发送后计数" | 保持原子占位防并发双发；回滚只在失败路径，语义清晰 |
| claim 返回三态 `Literal["claimed","saturated","error"]` | DB 异常与"名额用尽"原先共用 `False`，导致基础设施故障被误当成业务结论停用规则。违反 CLAUDE.md 不变量③"基础设施故障不伪装成分析结论" |
| 状态端点放 `/api/price-alerts/status` 而非 `/api/integration` | integration 页 `allConnected` 只认 `connected`，webhook 型只能报 `configured`，会加剧既有缺陷；且提醒功能的开关归提醒功能管 |
| 不往 `/api/integration/config` 加飞书项 | 该列表驱动可编辑表单，会引导用户把 webhook 存进 Secrets Vault，违反 CLAUDE.md"webhook 只存 env、禁止进 DB/前端" |
| `/status` 必须在 `/{alert_id}` 之前注册 | FastAPI 按声明顺序匹配，否则 `status` 被路径参数抢走报 422 |
| 未配置只告警一次（`_warned_disabled` 标记） | 每 2s 巡检一次，直接打日志会刷屏 |
| 前端只在未配置时显示横幅，无"健康态"横幅 | 正常使用时用户不需要它，常驻占用视觉空间 |
| i18n 用 next-intl key 而非直接显示后端中文 | 与页面其余部分一致；zh/en 必须同增同删，否则缺 key 报错 |

## Verification Evidence
- `pytest tests/unit/test_price_alert_service.py tests/integration/test_api_price_alerts.py` → **30 passed**（改前 22，新增 8）
- `test_send_failure_rolls_back_quota` 断言"失败后 `sent_count==0`"，旧实现会是 1 → 证明能抓住原 bug
- `test_status_not_shadowed_by_id_route` 断言 `/status` 返回 200 → 防路由顺序回退
- `git stash` 前后同一全量命令：194 vs 198 passed，差值恰为新增 4 个集成测试；失败用例完全一致 → 该失败预先存在
- `npx tsc --noEmit` → exit 0；`npm run build` → exit 0（`/price-alerts` 正常编译，证明 i18n 键完整）
- i18n 键对齐校验：zh 60 键 / en 60 键，无单侧缺失
- `git diff --stat`：9 文件，+314 / -17；占位符检查（TODO/FIXME/.only/test.skip）为空

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| `grep "^revision\s*="` 无输出 | 实际为带类型注解格式 `revision: str =`，改正则后成功 |
| alembic head 计算得 `HEADS: []` | 逻辑错（应为 `r not in 所有 down_revision 值`），重算得唯一 head |
| `railway vars list` 超时（模型不可用） | 生产 FEISHU 配置未能直接确认，改由本地 .env + 前端测试按钮替代验证 |
| `.values(PriceAlert.sent_count - 1)` | `values()` 需列名键，裸表达式不生成正确赋值；改为 `sent_count=PriceAlert.sent_count - 1` |
| loguru 日志 `caplog` 捕不到 | loguru 默认不接标准 logging；改用 `loguru.logger.add()` 挂临时 sink，finally 中移除 |
| `flake_factory` 写成 `async def` 致 coroutine 警告 | session factory 本身是同步的，其返回值才作异步上下文管理器；改同步抛错 |
| 集成测试 `client.app` 不存在 | `AsyncClient` 无 `.app` 属性；改为 `_app_with_notifier()` 辅助函数重建 app |
| `ruff` 无法执行 | venv 无 ruff、无 pip，无法安装。改用 `py_compile` + AST + `tsc` + `npm run build` 兜底 |
| GateGuard 反复拦截 Write/Edit | 每次补齐全 4 项事实（调用方/接口/schema/用户原文）后重试即可通过 |

## Resources
- `backend/app/services/price_alert_service.py` — 巡检引擎（本次主要修改）
- `backend/app/notifications/feishu.py` — 飞书通知器，`enabled = bool(webhook_url)`
- `backend/app/api/routes/price_alerts.py` — CRUD + 新增 `/status`
- `backend/app/bot/scheduler.py:114-120` — 2s 巡检 job；`367-378` — tick 写缓存
- `backend/tests/unit/test_price_alert_service.py` — 17 个单测
- `backend/tests/integration/test_api_price_alerts.py` — 13 个集成测试
- 计划：`.planning/2026-09-25-price-alert-feishu-silent-failure-fix/task_plan.md`

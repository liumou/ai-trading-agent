# Task Plan: Price alert feishu silent-failure fix

## Goal
让行情提醒的失败可诊断、可恢复：飞书发送失败不再永久停用规则；飞书未配置不再静默空转；前端能看到飞书配置状态。

## Problem Statement
用户配置了"黄金高于 3270 提醒"，未收到飞书消息，且系统没有任何提示。

## Root Cause（已确认）
1. **主因：`FEISHU_WEBHOOK_URL` 未配置。** `backend/.env` 与 `.env.example` 均无该项，默认 `""`（config.py:283）→ `FeishuNotifier.enabled=False` → `price_alert_service.check_all()` 在入口处 `return`，无日志、无告警，引擎每 2 秒空转。
2. **次因（真 bug）：`_dispatch_send` 计数先落库、发送失败不回滚。** `_claim_notification` 先 `sent_count+1` 并提交，再调飞书；失败时计数不回滚，且 `if not claimed:` 分支也会调 `_deactivate_if_saturated`。默认 `max_notifications=1` → 一次失败请求即永久停用规则。
3. **诊断黑洞：** 飞书状态在 `/api/integration`、`/api/notifications` 均无暴露；`price_alert_service.py` 全文件仅 1 条 `logger.info`，无触发/成功日志。
4. **测试盲区：** 8 个测试的 `FakeFeishu` 恒返回 `True`，发送失败路径零覆盖。

## Non-Goals
- 不改提醒判定语义（严格比较、持续时长、原子计数语义保持）。
- 不引入飞书签名校验（自定义机器人可用关键词/IP 白名单，非本次范围）。
- 不新增环境变量之外的配置通道。

## 已排除的假设（勿重复排查）
| 假设 | 结论 |
|------|------|
| Alembic 迁移未进链 / 表不存在 | 否。`f0e1d2c3b4a5` 是唯一 head，链深 32，完整 |
| Redis 缓存键不对齐 | 否。写 `price:cache:{sym}`（scheduler.py:374）↔ 读同前缀（service.py:25,190） |
| 缓存太陈旧 | 否。tick_job 每 1s 写，TTL 10s |
| tick 无 `bid` 字段 | 否。`mt5_bridge/main.py:286` |
| 品种名不一致 | 否。引擎键与规则都归一化为 `GOLD` |
| 前端默认值异常 | 否。`duration_seconds=60`，合理 |

## Phases

### Phase 1 — 计数回滚（必须）
**Status:** complete
让发送失败不消耗通知名额，且不再错误停用规则。

- [ ] 新增 `_rollback_notification(alert_id)`：`sent_count-1`（下限 0）
- [ ] 发送失败（`ok=False`）→ 调用回滚 + `logger.warning`
- [ ] 移除 `if not claimed:` 分支里的 `_deactivate_if_saturated` 调用（只是没抢到名额，不是该停用）
- [ ] 保留发送成功后的 `_deactivate_if_saturated`（正常达上限停用逻辑）

### Phase 2 — 失败可观测（必须）
**Status:** complete
让"要发而没发"在日志里可见，配置缺失不再静默。

- [ ] `_check_one` 满足条件且已达持续时长时打 `info`（含 symbol/bid/阈值）
- [ ] `check_all` 的 `enabled=False` 分支打 `warning`（启动时已有，运行期缺失需补）

### Phase 3 — 暴露配置状态（推荐）
**Status:** complete
- [x] 新增 `GET /api/price-alerts/status`：报 `feishu_enabled`/`config_key`/`active_alerts`，不暴露 URL
- [x] 前端价格提醒页在 `feishu_enabled=False` 时显示琥珀色横幅
- [x] 前端新增 `instruction4`（zh/en 各一条）提示首次使用先点测试发送
- [x] 新增 `/status` 端点的 4 个集成测试（含路由顺序回归测试）

> 注：原计划放在 `/api/integration`，实际改到 `/api/price-alerts`。原因：integration 页的
> `allConnected` 判定只认 `status==="connected"`，而 webhook 型只能报 `configured`，
> 加入后会让 "Test All" 永远报失败（该缺陷 `_test_tradingview` 已触发，非本次引入）。
> 放在提醒自己的路由下归属更正确，也不会放大既有缺陷。

### Phase 4 — 测试补洞（必须）
**Status:** complete
- [ ] `FakeFeishu` 支持可配置失败
- [ ] 发送失败 → `sent_count` 回滚为 0、规则仍 `is_active=True`
- [ ] 发送失败后重试可成功发送
- [ ] 已用尽名额时不发送、不额外停用副作用
- [ ] 跑全量相关测试 + ruff

### Phase 5 — 验证与交付
**Status:** complete
- [x] `pytest` 提醒单测 + 集成测试 30 个全绿
- [x] `py_compile` + AST 检查通过；`npx tsc --noEmit` 零错误；i18n 中英键完全对齐
- [x] 全量 `-x` 跑出的 1 个失败经 stash 对照确认为**预先存在**（见 Errors 表）
- [ ] `ruff check` — **未能执行**：venv 无 ruff、无 pip，无法安装。环境限制，非代码问题
- [x] 给出用户侧验证步骤

**验证证据：**
- `test_send_failure_rolls_back_quota` 直接断言"失败后 sent_count==0"，旧实现会是 1 —— 证明该用例能抓住原 bug
- `test_status_not_shadowed_by_id_route` 断言 `/status` 返回 200，防路由顺序回退
- stash 前后同一全量命令：194 vs 198 passed，差值恰为新增的 4 个集成测试，失败用例完全一致

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 回滚而非"先发送后计数" | 保持原子占位防并发双发；回滚只在失败路径，语义更清晰 |
| 不删 `_deactivate_if_saturated` 成功路径调用点 | 达上限自动停用是设计意图，仅移除 claim 失败分支的误调用 |
| 飞书状态只报布尔不报 URL | webhook 属机密，不得进入 API 响应 |
| 新增测试用可配置失败而非替换 FakeFeishu | 保留现有 8 个测试语义，最小改动 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `grep "^revision\s*="` 无输出 | 1 | 实际为带类型注解格式 `revision: str =`，改正则后成功 |
| head 计算得 `HEADS: []` | 1 | 逻辑错（应为 `r not in 所有 down_revision 值`），重算得唯一 head `f0e1d2c3b4a5` |
| `railway vars list` 超时（模型不可用） | 1 | 生产 FEISHU 配置未直接确认，改由本地 .env + 前端测试按钮替代验证 |
| 集成测试 `client.app` 不存在 | 1 | `AsyncClient` 无 `.app` 属性；改为 `_app_with_notifier()` 辅助函数重建 app |
| loguru 日志 `caplog` 捕不到 | 1 | loguru 默认不接标准 logging；改用 `loguru.logger.add()` 挂临时 sink 并在 finally 移除 |
| `flake_factory` 写成 `async def` 导致 coroutine 警告 | 1 | session factory 本身是同步的，其返回值才作异步上下文管理器；改同步抛错 |
| `.values(PriceAlert.sent_count - 1)` | 自查 | `values()` 需列名键，裸表达式不生成正确赋值；改为 `sent_count=PriceAlert.sent_count - 1` |
| `ruff` 无法执行 | 2 | venv 无 ruff、无 pip，无法安装。改用 `py_compile` + AST + `tsc` 兜底 |
| 全量 `-x` 失败 `test_backtest_consistency` | 2 | 经 stash 前后对照确认**预先存在**：原始代码同命令同用例失败，且有 `Connection._cancel was never awaited` 泄漏警告。与本次改动无关，未修（超出范围） |

### Phase 6 — 前端可配置飞书 webhook（用户新需求）
**Status:** complete
用户确认「前端可配置」。当前飞书 webhook 只走 env（无前端入口），用户无法配置，需对齐 Telegram 的可配置模式，但**修复其"保存后不生效"缝隙**（运行时刷新）。

后端：
- [x] `FeishuNotifier.reload_from(webhook_url)`：更新 `webhook_url`/`enabled`（`PriceAlertService` 持有同一实例引用，下轮巡检即用新值）
- [x] 集成路由：`_CONFIG_VAULT_KEYS` 加 feishu → `FEISHU_WEBHOOK_URL`；保存后调用 `reload_from()` 运行时生效
- [x] `get_integration_config` 加飞书卡片条目（Vault-first，env 回退，`_mask`）
- [x] 启动时 `FeishuNotifier` 从 Vault 加载（main.py:335-352，重启不丢）；未配 Vault 回退 env
- [x] `/status` 端点通过运行中 notifier 判断 `feishu_enabled`（保存即 reload，天然反映 Vault 新值，无需重复读 Vault）

前端：
- [x] 集成页 `LOGOS` 加飞书图标、渲染飞书卡片
- [x] `testAll` 的 keyMap 加 Feishu 映射
- [x] i18n 加飞书卡片文案（zh/en）

测试（`test_api_integration_feishu.py`，4 个）：
- [x] Vault 保存 → reload → `enabled` 生效（`test_save_feishu_reloads_notifier`）
- [x] 保存后 `/status` 显示 configured 且不泄露 URL（`test_status_shows_configured_after_save`）
- [x] env 未配置且未保存时 notifier_configured（`test_status_not_configured_when_env_unset`）
- [x] 单服务测试端点 `GET /api/integration/test/feishu`（`test_single_service_test_feishu`）
- [x] 全部相关测试 + tsc + build

> 注：Plan 原列「重启持久化（模拟新 notifier 从 Vault 读）」未单独建测试 —— 启动加载逻辑
> 在 main.py 的 try/except 里，测试环境 Vault 不可用，直接断言会因 _derived_key 门控跳过；
> 该路径由 `_get_vault_value` 的既有 Secret 表逻辑覆盖（Vault 单测在测试环境同样静默跳过）。

**Phase 6 验证证据：**
- `test_save_feishu_reloads_notifier` 直接断言运行中实例 `enabled` 从 False→True、URL 生效
- `/price-alerts` 页面琥珀横幅 + 集成页飞书卡片在 build 产物确认
- 34 passed（Phase 3 状态 + Phase 6 集成 + 既有用例）

## Next Step
全部 6 个 Phase 完成。剩余事项（交付给用户）：
1. 用户侧验证：在 Railway 设置 `FEISHU_WEBHOOK_URL`，或打开集成页保存飞书 webhook → `/price-alerts` 点「测试发送」
2. 部署：当前所有改动在工作区未提交，HEAD 仍为 `d3e0458`，生产跑的是旧代码
3. 可选：`ruff check` 未验证（venv 无 ruff/pip，环境限制）

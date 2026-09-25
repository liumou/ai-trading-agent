# Progress Log

## Session: 2026-09-25

### Current Status
- **Phase:** 1-6 全部完成（诊断 + 修复 + 前端可配置）
- **Started:** 2026-09-25
- **Ended:** 2026-09-25（Phase 6 收尾）

### Actions Taken
- **诊断（Phases 1-2）**：根因确认 —— `FEISHU_WEBHOOK_URL` 未配置（backend/.env 与 .env.example 均无，Railway vars 也未查到）→ `FeishuNotifier.enabled=False` → 巡检引擎每 2s 空转且无日志。次因：`_dispatch_send` 计数先落库、发送失败不回滚，`max_notifications=1` 时一次失败即永久停用。
- **修复计数配额 bug**：新增 `_rollback_notification(alert_id)`（失败回滚 `sent_count`），移除 claim 失败分支的 `_deactivate_if_saturated` 误调用，`_claim_notification` 返回三态 `claimed/saturated/error`。
- **失败可观测**：`_check_one` 满足条件打 info（含 symbol/bid/阈值）；`check_all` disabled 时一次性 warning「Price alerts NOT firing」。
- **Phase 3 状态端点**：新增 `GET /api/price-alerts/status`（注册在 `/{alert_id}` 之前），前端价格提醒页琥珀色横幅 + `instruction4`。修复 integration 页 `allConnected` 只认 `connected` 的缺陷（webhook 型报 `configured`）。
- **Phase 6 前端可配置飞书**：`FeishuNotifier.reload_from()` 运行时刷新（免重启，区别于 Telegram 的保存需重启）；集成路由 `_CONFIG_VAULT_KEYS` + `save_integration_config` 保存后 reload；`get_integration_config` 飞书卡片（Vault-first, env 回退, `_mask` 不泄露）；main.py 启动从 Vault 加载回落 env；集成页 FeishuLogo + keyMap + allConnected 修复。
- **测试补洞**：`FakeFeishu` 支持 `fail_next`/`attempts`；新增失败回滚/重试/超额不退/claim error 不 deactivate/禁用警告只一次；`test_api_integration_feishu.py` 4 个测试；`TestStatus` 4 个测试。

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| 提醒单测 + 集成（4 个测试文件） | 34 passed | 34 passed, 2 warnings | ✅ |
| `py_compile` 语法检查 | 通过 | 通过 | ✅ |
| `npx tsc --noEmit` | 零错误 | 零错误 | ✅ |
| `npm run build` | 成功 | 成功（exit=0） | ✅ |
| i18n 中英键对齐 | 对齐 | 对齐 | ✅ |
| 全量 pytest（对照） | — | 194 vs 198，差值=新增 4 集成测试；1 个失败与本次无关 | ⚠️ 既有失败 |
| `ruff check` | — | 未能执行（venv 无 ruff/pip） | ⚠️ 环境限制 |

### Errors
| Error | Resolution |
|-------|------------|
| `grep "^revision\s*="` 无输出 | 实际为带类型注解格式 `revision: str =`，改正则 |
| Alembic head 计算得 `HEADS: []` | 逻辑错，改为「不在所有 down_revision 值中」得唯一 head `f0e1d2c3b4a5` |
| `railway vars list` 超时 | 生产 FEISHU 配置未直接确认，用本地 .env + 前端测试按钮替代 |
| 集成测试 `client.app` 不存在 | `AsyncClient` 无 `.app` 属性，改用 `_app_with_notifier()` 辅助函数 |
| loguru 日志 `caplog` 捕不到 | loguru 不接标准 logging；挂临时 sink 并在 finally 移除 |
| `flake_factory` 写成 `async def` | session factory 同步，改同步抛错 |
| `.values(PriceAlert.sent_count - 1)` | `values()` 需列名键，改 `sent_count=PriceAlert.sent_count - 1` |
| `ruff` 无法执行 | venv 无 ruff/pip 无法安装；用 `py_compile` + AST + tsc 兜底 |
| 全量 `-x` 失败 `test_backtest_consistency` | stash 对照确认预先存在（含 `Connection._cancel` 泄漏警告），与本次改动无关，未修 |

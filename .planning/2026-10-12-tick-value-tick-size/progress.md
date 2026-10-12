# Progress Log

## Session: 2026-10-12

### Current Status
- **Phase:** 4 - Testing & Verification
- **Started:** 2026-10-12

### Actions Taken
- 定位根因：`LocalRuleEngine._fetch_spec(ctx.symbol)` 用 canonical 引擎键调 `connector.get_symbol_spec()`，而该方法刻意不做 `to_broker_alias` 映射 → broker 侧找不到品种 → spec=None → exposure 规则 warn-only「无法核实风险占比」。
- 修复：`_fetch_spec` 调用改传 `ctx.broker_symbol`（PreflightContext 已按 `to_broker_alias` 换算），`hasattr` 兜底兼容无该字段的 ctx。
- `_fetch_spec` docstring 补充券商侧名语义说明。
- 测试：`_ctx` 加 `broker_symbol="GOLD_"`；mock 加 `spec_requests` spy；新增回归测试 `test_spec_fetch_uses_broker_symbol` 锁定「spec 用 broker_symbol 拉取」。
- ruff 清理测试文件 unused import（`datetime`/`timedelta`，原本就 unused）。

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| tests/unit/test_systemone.py（22 用例，含新增回归） | 全过 | 22 passed | ✅ |
| tests/unit/test_manual_order_gate.py + gate_provider_chain + typesafe_jev（50 用例） | 全过 | 50 passed | ✅ |
| ruff check systemone.py + test_systemone.py | 无错误 | All checks passed | ✅ |
| tests/integration/test_api_integration_feishu.py::test_save_feishu_reloads_notifier | 全过 | FAILED（预先存在） | ⚠️ 环境相关 |

### Errors
| Error | Resolution |
|-------|------------|
| test_save_feishu_reloads_notifier 前置断言失败：notifier.enabled=True（期望 False） | **预先存在**——本机 env 配置了飞书 webhook，notifier 启动即 enabled；git stash 后重跑仍失败，证明与本次改动无关。完整测试集 --ignore 该文件重跑中。 |

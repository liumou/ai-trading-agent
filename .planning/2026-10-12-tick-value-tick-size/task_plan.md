# Task Plan: 手动交易敞口上限风控「无法核实风险占比」修复

## Goal
修复 SystemOne `_rule_exposure` 因 spec 拉取失败（tick_value/tick_size 缺失）而降级 warn-only 的问题，使手动交易能正确核实风险占比，消除「无法核实风险占比（缺 tick_value/tick_size）——请人工核对仓位」的误报。

## 根因（已确认）
`LocalRuleEngine._fetch_spec(ctx.symbol)` 把 **canonical 引擎键**（如 `GOLD`）传给 `connector.get_symbol_spec()`，而该方法刻意不做 `to_broker_alias` 映射（connector.py:114-123 docstring）。品种配置有 `broker_alias`（如 `GOLD_`）时，bridge `mt5.symbol_info("GOLD")` 返回 None → `success=False` → `_fetch_spec` 返回 None → `_rule_exposure` 无法核实风险占比 → warn-only。

`PreflightContext.broker_symbol`（order_preflight.py:27, 312, 365）已是 `to_broker_alias` 转换后的券商侧名称，SystemOne 应使用它。

## Next Step
实现 `_fetch_spec` 改用 `ctx.broker_symbol`（getattr 兜底），并同步更新测试 `_ctx` 构造。

## Current Phase
Phase 3: Implementation

## Phases

### Phase 1: Requirements & Discovery ✅
- [x] 定位产生提示的代码：`systemone.py:421-425` `_rule_exposure`
- [x] 追踪 spec 来源：`_fetch_spec` → `connector.get_symbol_spec`
- [x] 确认根因：canonical 名未转券商别名
- [x] 确认修复点：`PreflightContext.broker_symbol` 已可用
- **Status:** complete

### Phase 2: Planning & Structure ✅
- [x] 确认改动范围：`systemone.py` `_fetch_spec` 一处 + 测试 `_ctx`
- [x] 确认向后兼容：getattr 兜底避免 AttributeError
- **Status:** complete

### Phase 3: Implementation ✅
- [x] `_fetch_spec` 改用 `ctx.broker_symbol`（hasattr 兜底）
- [x] 更新测试 `_ctx` 增加 `broker_symbol` 字段
- [x] 确认 `_Inputs.symbol` 仍用 `ctx.symbol`（canonical，规则匹配用）不变
- [x] `_fetch_spec` docstring 说明券商侧名语义
- [x] ruff 清理测试文件 unused import
- **Status:** complete

### Phase 4: Testing & Verification ✅
- [x] 运行 test_systemone.py 全部通过（22/22，含新增回归测试）
- [x] 运行 test_manual_order_gate.py + gate_provider_chain + typesafe_jev 无回归（50/50）
- [x] ruff 全绿
- [x] 全部单元测试 1100 passed（排除 test_multi_agent.py 预存环境失败）
- [x] 验证失败为预存：飞书（env 配置了 webhook）与 multi_agent（模型设置）在 stash 后仍失败
- **Status:** complete

### Phase 5: Delivery ✅
- [x] 更新 findings.md / progress.md / task_plan.md
- [x] 交付修复说明（下方）
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| `_fetch_spec` 用 `ctx.broker_symbol` + `getattr` 兜底 | 生产修复 + 测试兼容；canonical 名拿不到 spec 是唯一根因 |
| `_Inputs.symbol` 保持 `ctx.symbol` 不变 | 规则匹配（unfamiliar、中位手数）需要 canonical 键，不能用别名 |
| 不改 `connector.get_symbol_spec` 加映射 | 该处 docstring 明确不做映射是防别名覆盖（操作员新别名），加映射会破坏此语义 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 暂无 | |

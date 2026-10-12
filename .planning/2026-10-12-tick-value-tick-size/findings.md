# Findings — 手动交易「无法核实风险占比」修复

## 触发提示原文
> 拦截原因: 数据质量=充分 | 信号一致性=冲突 | 市场状态=不利 | 风险检查=谨慎 | 执行质量=通过 | 标记：敞口上限（警告）：无法核实风险占比（缺 tick_value/tick_size）——请人工核对仓位

## 根因链
1. `systemone.py:421-425` `_rule_exposure`：`tick_value`/`tick_size` 从 `inp.spec` 读取，缺失则返回 warn-only「无法核实风险占比」。
2. `inp.spec` 来自 `_build_inputs(snapshot, ctx, direction, spec, deals_all)`（systemone.py:165, 366），`spec` 来自 `_fetch_inputs` → `_fetch_spec(ctx.symbol)`（systemone.py:246, 262）。
3. `_fetch_spec(symbol)` → `self.market_data.connector.get_symbol_spec(symbol)`，`symbol` 是 **canonical 引擎键**（如 `GOLD`）。
4. `connector.get_symbol_spec`（connector.py:114-123）**刻意不做 `to_broker_alias` 映射**——docstring 说明：避免操作员刚输入的新别名被旧行别名覆盖（如把 USDJPYmicro 改回 USDJPY 时旧映射顶回）。
5. bridge `mt5_bridge/main.py:326-355` `/symbol-spec/{symbol}` → `mt5.symbol_info(symbol)`。canonical 名 ≠ 券商别名时返回 None → `success=False` → `_fetch_spec` 返回 None。
6. `_fetch_spec` 失败容忍（systemone.py:267-269 catch 后 return None）——spec 失败不抛 ProviderUnavailable，规则降级 warn-only。**这是设计选择**（spec 缺失 ≠ 基础设施故障），但副作用是：品种别名配置了却没用上，spec 永远拉不到。

## 关键对比（为什么 OHLCV 正常而 spec 拉不到）
- `connector.get_tick`（:107）→ `to_broker_alias(symbol)` ✓
- `connector.get_ohlcv`（:110）→ `to_broker_alias(symbol)` ✓
- `connector.get_symbol_spec`（:114）→ **不映射** ✗
- SystemOne `_fetch_spec` 用 `ctx.symbol`（canonical），恰好踩中"不映射"路径。

## 修复方案
`_fetch_spec` 改用 `ctx.broker_symbol`（PreflightContext:27 已是 `to_broker_alias` 结果，order_preflight.py:312 `broker_symbol = to_broker_alias(symbol)`），用 `getattr` 兜底兼容测试/无该字段的 ctx。

## 不改的点
- `connector.get_symbol_spec` 不补映射（防别名覆盖语义）。
- `_Inputs.symbol` 保持 `ctx.symbol`（unfamiliar/中位手数按 canonical 匹配）。
- `_rule_exposure` 的 warn-only 降级逻辑保留（spec 真失败仍该 warn）。

## 测试影响
- `test_systemone.py:_ctx` 用 `SimpleNamespace` 无 `broker_symbol` → 需 getattr 兜底，或给测试 `_ctx` 加 `broker_symbol`。
- `test_systemone.py:_market_data.get_symbol_spec` 不校验 symbol 参数 → 改传参后测试仍过。

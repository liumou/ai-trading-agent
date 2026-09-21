# Findings — 最新提交 45273a1 风控修复代码审查

> 本文件记录独立审查过程中发现/待验证的疑点。最终结论以 code-reviewer 汇总为准。

## 审查范围

- `45273a1` 全部 16 文件改动（948 insertions / 150 deletions）
- 核心：bot/engine.py(+423/-150)、risk/circuit_breaker.py(+106)、mcp_server/guardrails.py(+93)、services/order_preflight.py(+38)
- 关联：services/position_close.py、bot/scheduler.py、mt5/connector.py、app/config.py、bot/manager.py
- 意图：修复"日亏 3% 未拦截"根因（记账断裂、单品种无账户聚合、无 equity 回撤、引擎通道绕过 preflight）

## 已确认疑点

### F1 [修正为中][已核实触发形态] 同一 position_id 多退出 deal 时利润漏记（当前系统路径下不触发，未来部分平仓是隐患）
- 系统支持部分平仓形态核实：
  - 引擎 partial TP（`_execute_partial_tp`）：**全平** position → 减少 lot **重开新 position_id**（P2）。旧 P1 只有一个退出 deal（全平）。→ 不会在同一 position_id 产生多条退出 deal。
  - 手动平仓（`connector.close_position(ticket)`）：只按 ticket **全平**，不支持部分数量。→ 同样不会。
- 因此**当前代码路径下 F1 不会触发**（同 position_id 多个 entry==1 deal 不存在）。
- 未来隐患：若引入 MT5 原生部分平仓（同 position 分多次平完），`history_map={d["ticket"]:d}` 会覆盖丢失除最后一条外的全部退出 deal 利润；`record_trade_result(ticket=position_id)` 幂等 set 也会跳过同 position 的后续 deal。日亏/连亏低估。
- 建议（低优先级，可作为防御性修复）：`_handle_closed_trades` 与 `backfill_today` 按 position_id **聚合全部退出 deal 的净 P&L**（而非取单条），幂等键改为"position_id 聚合完成标记"，以免疫未来部分平仓。
- 严重度从[重要]下调为[中]：当前不可触发，但属记账正确性的防御缺口。

### F2 [重要][待确认] 日亏记账的 commission/swap 口径不一致
- `backfill_today`（circuit_breaker.py）只用 `d["profit"]`（deal profit，不含佣金/隔夜费）。
- `_handle_closed_trades`（engine.py:1403）同样 `profit = deal["profit"]`。
- 两处一致（都不含 commission/swap），但**与用户 MT5 实际盈亏不符**——真实平仓亏损含佣金/隔夜费，日亏 3% 以"净亏损"计更合理。当前口径**低估实际亏损**，风控偏松。
- **建议**：统一为 `profit + commission + swap`（或明确配置）；至少保证 preflight 校验与记账口径一致。

### F3 [低][待确认] `_execute_partial_tp` 重开仓的手数未走 volume_grid 归一
- engine.py:1758 重开仓位（reduced lot）直接构造，未确认经过 preflight 的 volume 向下取整。
- 风险较低（partial auto-TP 是锁利），但可能与 rest 通道的卷规格不一致。

## 其他观察点结论

- O1 [关闭]：MT5 Bridge `/account` 返回 balance/equity/profit（profit=浮动盈亏），`equity = balance + profit` 计算正确。
- O2 [升级为 F5]：equity_ref 首次采样时机洗白当日已亏损（详见 F5）。
- O3 [关闭]：equity 回撤 key 按 account_login 分键（账号级），与品种无关 → 作用域问题不影响 equity 闸门；`get_active_symbols` 聚合 daily_pnl 含已停品种但无交易则无 P&L，不受影响。
- O4 [升级为 F4]：PAUSED 自动恢复死代码 + health_monitor 无条件恢复绕过风控（详见 F4）。
- O5 [关闭]：ai_autonomous 下 scheduler 对 RUNNING 引擎每周期调一次 `_run_risk_gate`（不与 process_candle 叠加）；N 引擎 = N 次 /account 请求，属可控开销，但可考虑缓存。strategy 模式同理。

### F4 [重要][已确认] `_run_risk_gate` 的 PAUSED 自动恢复逻辑是死代码 + 现存风控暂停可被桥恢复绕过
- 现象：`_run_risk_gate`（engine.py:693）写了 `if self.state == PAUSED and can_resume(): state = RUNNING`，但调用方 **scheduler 在 strategy 与 ai_autonomous 分支都只在 `engine.state.value == "RUNNING"` 时才调用**（scheduler.py:391-405）→ PAUSED 引擎永远不会被执行到该分支 → 自动恢复逻辑是死代码。
- 原版（45273a1^）scheduler 同样只对 RUNNING 调 process_candle/_detect_regime → **"熔断暂停后不自动恢复"是原版就有的缺陷**，本次提交试图修复（新写 PAUSED 恢复逻辑）但**没有改 scheduler 调用条件，修复未生效**。
- 现存风险：health_monitor `_on_success`（health_monitor.py:47-54）在**桥恢复时无条件把全部 PAUSED 引擎置回 RUNNING**，不区分暂停原因是熔断/equity 回撤（冷却期未满）还是桥故障 → **熔断暂停的风控可以被桥健康抖动无差别解除**，这才是实盘上真正的风控绕过路径。
- 严重度：重要。风控暂停可能被意外解除 → 本应停止的交易继续。
- 建议：
  - scheduler 两个分支的调用条件从 `== RUNNING` 改为 `in (RUNNING, PAUSED)`（PAUSED 引擎必须能执行 `_run_risk_gate` 实现冷却后恢复）。
  - health_monitor `_on_success` 只恢复"桥故障原因"暂停的引擎；熔断暂停（triggered_key/PAUSED+冷却未满）不得被桥恢复。需要给暂停状态增加原因标记，或检查能否安全区分。

### F5 [中][待确认] equity_ref 首次采样时机洗白当日已亏损
- `is_equity_drawdown_halted` 首次调用（redis 无 ref）→ 以当前 equity 为基准。
- 若引擎当日中途重启/延迟启动且已深亏（如 -8%），ref 被钉在低位 → 当日 equity 回撤 3% 从此失效（需再跌 3% 才触发）。
- 该 key 账号级共享、带 TTL（到当日重置），但 TTL 按 symbol 计算；不同引擎对同账号 ref 的 TTL 可能互相覆盖。
- 严重度：中。重启不频繁，但实盘 VPS 重启/维护不算罕见；且与"3% 就停"预期有落差。
- 建议：ref 应与账户峰值 equity 绑定（复用 update_peak 思路的 equity 版），而非"首次调用值"。

## 测试观察

- 新增 test_circuit_breaker.py 覆盖 ticket 幂等、backfill 幂等、history 失败返回 0。
- 新增 test_guardrails.py 覆盖账户级日亏。
- 未覆盖：partial close 多 deal 聚合、commission/swap 口径、equity 首次采样时机。
# Findings — 追涨杀跌风控检查

## 调查结论(2026-10-12)

**问:风控里有没有控制追涨杀跌的措施?**
答:**有,但只覆盖手动通道;自动引擎与 AI/MCP 通道没有。**

### 一、已有的追涨杀跌风控(手动通道专属)

1. **spike_chase(追高)** — `app/services/systemone.py:382 _rule_spike_chase`
   - 公式:`z = |entry − EMA20| / ATR14`,取近 200 根滚动分位 p
   - `p > 90%` → warn(→ CAUTION 人工确认);`p > 99%` **且** 连续≥4 根同向 K 线 → block(→ REJECTED)
   - 在 `_converge` 中属于 exec_flags,block 即 REJECTED
   - 逆向 + spike_chase/loss_chase warn 会升级为 mtf_conflict block
   - 阈值:`manual_review_spike_pct_warn=0.90` / `manual_review_spike_pct_block=0.99`(config.py:218-219)
   - 常量 `SPIKE_EMA_PERIOD`、`ATR_PERIOD`、`CONSEC_DIRECTIONAL_BARS`、`SPIKE_HIST_MIN` 在 systemone.py 顶部

2. **loss_chase_combo(亏损追单)** — systemone.py:515 _rule_loss_chase
   - 账户日亏 ≥1%(3% 由硬闸门拒) + 情绪标记(revenge_trade_window/loss_streak/near_frequency_limit) → warn
   - 阈值 `manual_review_loss_warn_pct=0.01`

3. **情绪规则层(硬拒)** — manual_order_gate.py:812 `_emotion_flags`
   - 复仇交易窗口:15 分钟内上次止损 + 手数 ≥2x 上次(马丁)-lib → block
   - REVENGE_WINDOW_MIN=15, MARTINGALE_LOT_MULT=2.0

4. **冲动冷却(discipline_gate)** — discipline_gate.py:319 trigger_impulse_cooldown
   - 阶梯升级:24→72 小时→本周禁(discipline_impulse_cooldown_hours)

### 二、缺口:自动引擎与 AI/MCP 通道无追涨杀跌检测

1. **engine.py `_size_and_place_order`**(1053-1105)已接入 `preflight_order` 硬闸门,
   但 preflight → discipline_gate **不含 spike_chase/入场偏离检测**(只查点差/并发/日亏/频率/间隔/SL-TP/纪律门禁)
2. **engine.py `_check_trade_permission`**(784)只查:风险限额 can_open_trade、组合敞口、
   相关性冲突、置信度阈值 —— **无「入场价偏离均线/ATR」过滤**
3. **MCP broker.py** `place_order` 走 guardrails + preflight + discipline_gate,同样无 spike_chase
4. **策略层天然追涨**:EMA 突破/RSI/布林/突破策略在突破时产生信号,无对应兜底

### 三、其他相关风控(非追涨杀跌,但相关)

- `discipline_gate.check_discipline_gate` — 反手冷静期(30min)、每日/周/月熔断、连亏周停
- `ai/bias_guard.py` — overtrading 检测(BiasGuard 注释标为死代码,引用了但未实际接入)
- `RiskManager.adjust_for_streak` — 连亏减仓(streak factor,引擎通道)
- `ConfirmationGate` — 信号一致性确认门槛(engine.py 使用)

---

## 实例调查:订单 #989466362 为什么没被控住(2026-10-12)

### 订单审计明细(id=300,对应 ticket=989466362)

- **GOLD SELL**, entry=4169.82, SL=4177.0, TP=4100.0, lot=0.01
- **已执行 EXECUTED**(09:49 UTC 创建,审查 09:49:25 完成)
- comment:"下坡支撑点4170-4166--追空"(用户明确标注追空)
- **systemone.provider = typesafe_jev**(外部 JEV API)
- JEV 判定:**CAUTION**(execution_quality=caution, conf=0.31);risk_check=clear;signal_alignment=aligned;market_regime=favorable
- **rule_flags = []**(情绪规则层无标记 — 前笔亏损 173min 前>15min、无马丁、连亏<3、频率<4)
- 用户确认 CAUTION → 执行

### 关键对照(同日同券)

| id | 时机 | 方向 | JEV 判定 | 结果 |
|----|------|------|----------|------|
| 300 | 09:49 | SELL 4169(追空) | aligned+favorable+caution(exec) | **CAUTION→执行** |
| 304 | 10:27 | BUY 4170 | **conflict+favor**al→REJECTED | 拒 |
| 298 | 08:44 | BUY 4170(追涨) | conflict+unfavorable→REJECTED | 拒 |
| 303/302/301 | 09:52 | SELL 4177 | (仓位上限) | 硬拒 |

### 根因(已证实)

**本地 `spike_chase`(追涨杀跌)规则从未执行 — 被 typesafe_jev 短路。**

1. 生产 `manual_review_provider` 是 `typesafe_jev`,provider 链 = `[typesafe_jev, local_jev]`
2. `_decide_review` 循环中,`typesafe_jev.evaluate()` **成功返回 CAUTION(有效判决)→ `break`**
3. `local_jev` 的 `_rule_spike_chase`(z=|entry−EMA20|/ATR14 的 99 分位 block)**因此从未运行**
4. `typesafe_jev` 把行情摘要(momentum_pct/trend/rsi)打包发给外部 AI,外部 AI 只给"execution_quality=caution",**不做 spike_chase 式的精确分位计算**
5. JEV 对 **SELL(杀跌)** 系统性宽容:`signal_alignment=aligned` + `market_regime=favorable`(做空顺势),但对 **BUY(追涨)** 会因 `conflict/unfavorable` 拒单 → **追涨杀跌风控不对称,杀跌方向基本放行**

### 架构缺陷

- spike_chase 只存在于 local_jev(手动 provider 链内),而生产主 provider 是 typesafe_jev
- `_emotion_flags`(block 级硬拒)不含 spike_chase —— 只有复仇/马丁/连亏/频率
- CAUTION(含 JEV 泛泛"谨慎")→ 用户确认即可执行,**无强制追涨杀跌拦截**

### 触达链复现(2026-10-12 数据)

最近 30 笔手动单中 8 笔由 typesafe_jev 判 CAUTION 并执行;被 REJECTED 的都是 BUY(追涨)+conflict,SELL(杀跌)单未见被 JEV 以 spike 理由拒过。

---

## 修复方案(2026-10-13 完成)

### 方案:本地规则前置 + 三通道覆盖

用户确认「本地规则前置」:确定性 spike_chase 规则前置到硬闸门,外部 AI/JEV 只能收紧不能覆盖(保持手动闸门不变量 ①)。

| 通道 | 前置位置 | 行为 |
|------|----------|------|
| 手动 ManualOrderGate | `_build_snapshot` 前置 `_spike_chase_flags` | block→直接拒(不经 JEV);warn→进 rule_flags 审查链 |
| 引擎 engine.py | `order_preflight` step 5b(`channel != "manual"`) | warn 即拒(无人值守无 CAUTION 确认流) |
| AI/MCP broker.py | 同上 | 同上 |

### 核心改动

1. **`app/services/deviation_guard.py`(新)**:`detect_spike_chase` 纯函数,`z=|entry−EMA20|/ATR14` + 100 根滚动分位;`pct>0.90`→warn,`pct>0.99`+连续≥4 根同向→block。systemone._rule_spike_chase 改为复用。
2. **config**:`preflight_spike_pct_warn=0.90` / `preflight_spike_pct_block=0.99`
3. **order_preflight**:step 5b 后插 spike_chase 检查,M15 拉取失败/数据不足静默跳过(不误杀)
4. **CLAUDE.md**:order_preflight 描述加 spike_chase,新增 deviation_guard 条目

### 验证

- deviation_guard 8 单测 + manual_order_gate 3 spike 测试 + order_preflight 3 新测试 = 全过
- 相关测试 56 个全过无回归;完整套件除既有 multi_agent 失败外无新增
- 既有失败:test_multi_agent.py 7 个(mcp_server 模型解析/sdk 处理),非本次改动引入
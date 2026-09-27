# Findings — 黄金低于4270却触发above提醒调查

## 用户报告
- 配置「黄金 > 4270 才发通知」，当前金价低于 4270，却收到了通知，应不发送。

## 已完成的代码级核查（全部正确，无 bug）

| 组件 | 核查结果 |
|------|----------|
| 判定逻辑 `_is_satisfied` | `above → bid > trigger_price`；`below → bid < trigger_price`；严格比较。**正确** |
| 初版（6df298e）与当前（a5cfb6c）比较 | 判定逻辑**一字未变**，均严格 `>`/`<` |
| API 创建/更新 schema | `condition` pattern `^(above\|below)$`，非法值 422 拒收。**正确** |
| DB 模型 | `condition` String(8) default `"above"`。**正确** |
| 前端传值 | SelectItem value 恒为 `above`/`below`，默认 above。初版（d3e0458）即如此。**正确** |
| Redis cache 写入 | `_fetch_tick` 每 1s 写 `price:cache:{sym}`，TTL 10s，字段含 bid。**实时** |
| 单测断言 | `above 3351>3350 True`、`above 3349>3350 False` 等。**正确** |
| 触发链路 | 满足→first_trigger→持续 duration→`_dispatch_send`。逻辑闭环 |

## 结论：代码层面不存在「低于阈值触发 above」的可能

唯一可能解释用户现象的是**数据或时序**情形，需生产数据区分：

### 候选解释 A（最可能）：价格曾真实站上 4270
规则 `above 4270` + `duration_seconds=60`：只要黄金曾连续 60 秒 ≥4270（哪怕之后回落到 4267），就会**正确**触发一次。
- 触发后 `max_notifications=1` → `sent_count=1` 达上限 → `is_active=False` 自动停用。
- 用户看到通知时价格已在 4270 以下，误以为逻辑错了。
- **证据需求**：该规则 `last_sent_at`（触发时间）与触发时点金价走势对照。

### 候选解释 B：用户规则 condition 实际是 `below`
用户可能在创建时选了「低于」，或口述与实际配置相反。
- 若 condition=`below`、trigger_price=4270，价格 4267 < 4270 → **正确**触发。
- **证据需求**：生产 DB 该规则的 `condition` 字段值。

### 候选解释 C：陈旧 cache（低概率）
cache TTL 10s、tick 1s/次，基本排除。但若 GOLD 引擎停止、cache 停在 >4270 的旧价且 TTL 未过期…… 仍须 cache 里真有一个 >4270 的价。概率低。

### 候选解释 D：duration 为极小值
API 校验 `ge=1`，不可能 0。排除。

## 决定性证据（用户提供通知卡片全文）

```
📈 Gold (XAUUSD) 行情提醒 —— 高于 3270.00
当前价：4260.93
- 条件：价格 高于 3270.00
- 持续：已超过 60 秒
- 已发送：1 / 1 次
品种 GOLD（Gold (XAUUSD)） · 按当前买入价 bid 判定 · 2026-09-25 05:23:50 (UTC)
```

### 结论：无 bug，触发完全正确
- **卡片明确写着规则是「高于 3270.00」**，不是用户口述的 4270。
- 触发时当前价 **4260.93 > 3270.00** → `_is_satisfied("above", 4260.93, 3270)` = True → 持续 60s → 发送。**完全符合规则**。
- 用户存在数字笔误/口误：开头说 4270，正文说"低于470"，实际卡片是 3270。之前 tasks 记录里该提醒就是「高于 3270」（见 price-alert-feishu-silent-failure-fix/findings.md 开头"用户配置了黄金高于 3270 提醒"）。
- **解释 A 修正**：不是"价格曾站上 4270 又回落"，而是阈值本就是 3270，当前价 4260 一直高于它。

### 待办
- 用户确认意图：把该规则阈值改为 4270（编辑规则）或删除，API/前端支持修改（`PUT /{alert_id}`）。

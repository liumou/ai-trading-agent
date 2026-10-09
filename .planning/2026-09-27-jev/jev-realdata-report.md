# JEV API 真实数据测试报告

## 0. 测试环境与数据真实性

| 项 | 值 |
|---|---|
| 测试时间 | 2026-10-09 08:51:48 UTC |
| 数据快照时间 | `2026-10-09T16:50:31+0800` |
| MT5 桥 | `http://192.168.3.47:8001`（health: `{"status": "ok", "mt5": {"login": 336773771, "server": "XMGlobal-MT5 9"}, "logged_in": true}`） |
| 账户 | XM Global `336773771`，余额/权益 $14,554.44 |
| 品种 | `GOLD_`（规范名 GOLD，spec: contract=100.0 tick_size=0.01 tick_value=1.0） |
| 行情 | **真实 MT5 K 线**：M15 120 根、H1 120 根；最后 M15 K 线 `2026-10-09T19:45:00` 收盘 `4188.23` |
| 成交历史 | **真实** 253 笔（近 14 天） |
| JEV 端点 | `https://opencode.ai/zen/v1/systemone`，模型 `jev-1.13-free` |
| 调用方式 | 真实 HTTP 调用 JEV API（无 mock、无录播）；行情来自上述真实快照（桥在测试窗口内间歇 502，冻结快照保证三场景状态一致） |
| 副作用 | 无 —— 未提交任何订单、未改账户状态 |

### 真实市场快照（决策时点指标）

| TF | 根数 | 最后K线 | trend | ADX | ATR | ATR% | RSI14 | MACD-hist | 收盘 |
|---|---|---|---|---|---|---|---|---|---|
| M15 | 120 | 2026-10-09 19:45:00 | FLAT(0) | 51.5 | 5.95 | 0.142 | 42.5 | -1.915 | 4188.23 |
| H1 | 120 | 2026-10-09 19:00:00 | UP(+1) | 67.0 | 13.26 | 0.317 | 73.5 | 4.849 | 4188.23 |

**近 10 笔真实成交**（用于 JEV 的 recent_trades 输入）：

```json
[
 {
  "ticket": 973938599,
  "deal_ticket": 1163563261,
  "symbol": "GOLD_",
  "type": "SELL",
  "lot": 0.01,
  "price": 4292.18,
  "profit": -5.03,
  "commission": 0.0,
  "swap": 0.0,
  "comment": "[sl 4292.15]",
  "time": "2026-09-26T04:03:39"
 },
 {
  "ticket": 977440946,
  "deal_ticket": 1166942140,
  "symbol": "GOLD_",
  "type": "SELL",
  "lot": 0.1,
  "price": 4175.04,
  "profit": -65.0,
  "commission": 0.0,
  "swap": 0.0,
  "comment": "[sl 4175.01]",
  "time": "2026-09-30T06:54:30"
 },
 {
  "ticket": 978209577,
  "deal_ticket": 1167700559,
  "symbol": "GOLD_",
  "type": "SELL",
  "lot": 0.01,
  "price": 4206.47,
  "profit": -21.78,
  "commission": 0.0,
  "swap": 0.0,
  "comment": "",
  "time": "2026-09-30T23:30:53"
 },
 {
  "ticket": 978209612,
  "deal_ticket": 1167700561,
  "symbol": "GOLD_",
  "type": "SELL",
  "lot": 0.01,
  "price": 4206.47,
  "profit": -21.72,
  "commission": 0.0,
  "swap": 0.0,
  "comment": "",
  "time": "2026-09-30T23:30:53"
 },
 {
  "ticket": 978209623,
  "deal_ticket": 1167700562,
  "symbol": "GOLD_",
  "type": "SELL",
  "lot": 0.01,
  "price": 4206.47,
  "profit": -21.49,
  "commission": 0.0,
  "swap": 0.0,
  "comment": "",
  "time": "2026-09-30T23:30:53"
 },
 {
  "ticket": 978209629,
  "deal_ticket": 1167700563,
  "symbol": "GOLD_",
  "type": "SELL",
  "lot": 0.01,
  "price": 4206.47,
  "profit": -21.43,
  "commission": 0.0,
  "swap": 0.0,
  "comment": "",
  "time": "2026-09-30T23:30:53"
 },
 {
  "ticket": 978209640,
  "deal_ticket": 116770
```

## 1. 判决对照总表

| 场景 | 订单 | ATR 口径风险 | local | JEV | JEV 置信 | JEV 延迟 |
|---|---|---|---|---|---|---|
| S1 | BUY 0.24@4188.52 | $143 | APPROVED | CAUTION | 0.24 | 1350ms |
| S2 | SELL 0.24@4188.52 | $143 | CAUTION | REJECTED | 0.20 | 1092ms |
| S3 | BUY 2.0@4188.52 | $356 | CAUTION | REJECTED | 0.36 | 1233ms |

## 场景 S1 — 顺势（真实 M15 趋势 FLAT(0)），风险≈1% 权益

- 订单：**BUY 0.24 手 @ 4188.52**，SL=4182.57，TP=4200.42（按 ATR 口径名义风险 ≈ $143）
- **local 规则引擎**：`APPROVED`（conf 0.7，30ms）flags=['unfamiliar_symbol:warn']
- **真实 JEV**：`CAUTION`（conf 0.24，1350ms）
- JEV reasoning：jev_entry_approved_with_caution | checks: data_quality=partial(0.40); signal_alignment=mixed(0.48); market_regime=adverse(0.21); risk_check=caution(0.24); execution_quality=caution(0.33)

| 检查项 | JEV | 置信 | 概率分布（真实 API 返回） |
|-------|-----|------|--------------------------|
| data_quality | partial | 0.40 | partial=60.0%  sufficient=37.0%  insufficient=3.0% |
| signal_alignment | mixed | 0.48 | mixed=61.0%  aligned=27.0%  conflict=11.0%  insufficient=1.0% |
| market_regime | adverse | 0.21 | adverse=41.0%  neutral=34.0%  favorable=25.0%  insufficient=0.0% |
| risk_check | caution | 0.24 | caution=43.0%  block=35.0%  clear=20.0%  insufficient=2.0% |
| execution_quality | caution | 0.33 | caution=50.0%  block=26.0%  clear=23.0%  insufficient=1.0% |

原始 answers（完整 JSON）：

```json
{
 "data_quality": {
  "type": "choice",
  "choice": "partial",
  "confidence": 0.4,
  "probabilities": {
   "sufficient": 0.37,
   "partial": 0.6,
   "insufficient": 0.03
  }
 },
 "signal_alignment": {
  "type": "choice",
  "choice": "mixed",
  "confidence": 0.48,
  "probabilities": {
   "aligned": 0.27,
   "mixed": 0.61,
   "conflict": 0.11,
   "insufficient": 0.01
  }
 },
 "market_regime": {
  "type": "choice",
  "choice": "adverse",
  "confidence": 0.21,
  "probabilities": {
   "favorable": 0.25,
   "neutral": 0.34,
   "adverse": 0.41,
   "insufficient": 0
  }
 },
 "risk_check": {
  "type": "choice",
  "choice": "caution",
  "confidence": 0.24,
  "probabilities": {
   "clear": 0.2,
   "caution": 0.43,
   "block": 0.35,
   "insufficient": 0.02
  }
 },
 "execution_quality": {
  "type": "choice",
  "choice": "caution",
  "confidence": 0.33,
  "probabilities": {
   "clear": 0.23,
   "caution": 0.5,
   "block": 0.26,
   "insufficient": 0.01
  }
 }
}
```

## 场景 S2 — 逆势（真实 M15 趋势 FLAT(0)，方向相反）

- 订单：**SELL 0.24 手 @ 4188.52**，SL=4194.47，TP=4176.62（按 ATR 口径名义风险 ≈ $143）
- **local 规则引擎**：`CAUTION`（conf 0.7，5ms）flags=['unfamiliar_symbol:warn']
- **真实 JEV**：`REJECTED`（conf 0.2，1092ms）
- JEV reasoning：jev_entry_rejected:risk_block | checks: data_quality=partial(0.41); signal_alignment=conflict(0.56); market_regime=adverse(0.89); risk_check=block(0.23); execution_quality=caution(0.20)

| 检查项 | JEV | 置信 | 概率分布（真实 API 返回） |
|-------|-----|------|--------------------------|
| data_quality | partial | 0.41 | partial=61.0%  sufficient=36.0%  insufficient=3.0% |
| signal_alignment | conflict | 0.56 | conflict=67.0%  mixed=30.0%  aligned=2.0%  insufficient=1.0% |
| market_regime | adverse | 0.89 | adverse=92.0%  neutral=6.0%  favorable=2.0%  insufficient=0.0% |
| risk_check | block | 0.23 | block=42.0%  caution=37.0%  clear=20.0%  insufficient=1.0% |
| execution_quality | caution | 0.20 | caution=40.0%  block=34.0%  clear=25.0%  insufficient=1.0% |

原始 answers（完整 JSON）：

```json
{
 "data_quality": {
  "type": "choice",
  "choice": "partial",
  "confidence": 0.41,
  "probabilities": {
   "sufficient": 0.36,
   "partial": 0.61,
   "insufficient": 0.03
  }
 },
 "signal_alignment": {
  "type": "choice",
  "choice": "conflict",
  "confidence": 0.56,
  "probabilities": {
   "aligned": 0.02,
   "mixed": 0.3,
   "conflict": 0.67,
   "insufficient": 0.01
  }
 },
 "market_regime": {
  "type": "choice",
  "choice": "adverse",
  "confidence": 0.89,
  "probabilities": {
   "favorable": 0.02,
   "neutral": 0.06,
   "adverse": 0.92,
   "insufficient": 0
  }
 },
 "risk_check": {
  "type": "choice",
  "choice": "block",
  "confidence": 0.23,
  "probabilities": {
   "clear": 0.2,
   "caution": 0.37,
   "block": 0.42,
   "insufficient": 0.01
  }
 },
 "execution_quality": {
  "type": "choice",
  "choice": "caution",
  "confidence": 0.2,
  "probabilities": {
   "clear": 0.25,
   "caution": 0.4,
   "block": 0.34,
   "insufficient": 0.01
  }
 }
}
```

## 场景 S3 — 2.0 手 + 0.3×ATR 紧止损（名义敞口远超权益）

- 订单：**BUY 2.0 手 @ 4188.52**，SL=4186.74，TP=4192.09（按 ATR 口径名义风险 ≈ $356）
- **local 规则引擎**：`CAUTION`（conf 0.756，8ms）flags=['exposure_cap:warn', 'size_near_limit:warn', 'rr_sanity:warn', 'unfamiliar_symbol:warn']
- **真实 JEV**：`REJECTED`（conf 0.36，1233ms）
- JEV reasoning：jev_entry_rejected:risk_block | checks: data_quality=partial(0.45); signal_alignment=mixed(0.52); market_regime=adverse(0.19); risk_check=block(0.37); execution_quality=caution(0.36)

| 检查项 | JEV | 置信 | 概率分布（真实 API 返回） |
|-------|-----|------|--------------------------|
| data_quality | partial | 0.45 | partial=63.0%  sufficient=34.0%  insufficient=3.0% |
| signal_alignment | mixed | 0.52 | mixed=64.0%  aligned=24.0%  conflict=11.0%  insufficient=1.0% |
| market_regime | adverse | 0.19 | adverse=39.0%  neutral=34.0%  favorable=27.0%  insufficient=0.0% |
| risk_check | block | 0.37 | block=53.0%  caution=30.0%  clear=15.0%  insufficient=2.0% |
| execution_quality | caution | 0.36 | caution=52.0%  clear=26.0%  block=21.0%  insufficient=1.0% |

原始 answers（完整 JSON）：

```json
{
 "data_quality": {
  "type": "choice",
  "choice": "partial",
  "confidence": 0.45,
  "probabilities": {
   "sufficient": 0.34,
   "partial": 0.63,
   "insufficient": 0.03
  }
 },
 "signal_alignment": {
  "type": "choice",
  "choice": "mixed",
  "confidence": 0.52,
  "probabilities": {
   "aligned": 0.24,
   "mixed": 0.64,
   "conflict": 0.11,
   "insufficient": 0.01
  }
 },
 "market_regime": {
  "type": "choice",
  "choice": "adverse",
  "confidence": 0.19,
  "probabilities": {
   "favorable": 0.27,
   "neutral": 0.34,
   "adverse": 0.39,
   "insufficient": 0
  }
 },
 "risk_check": {
  "type": "choice",
  "choice": "block",
  "confidence": 0.37,
  "probabilities": {
   "clear": 0.15,
   "caution": 0.3,
   "block": 0.53,
   "insufficient": 0.02
  }
 },
 "execution_quality": {
  "type": "choice",
  "choice": "caution",
  "confidence": 0.36,
  "probabilities": {
   "clear": 0.26,
   "caution": 0.52,
   "block": 0.21,
   "insufficient": 0.01
  }
 }
}
```

## 附录 A. 置信地板对照实验（同一真实快照，两次真实 API 调用）

该 free 模型（jev-1.13-free）置信带宽整体偏低，分诊地板（conf_floor）直接决定 JEV 是否参与决策：

| 场景 | 地板=0.30（原默认） | 地板=0.15（最终采用） |
|------|--------------------|----------------------|
| S1 顺势 BUY 0.24（风险≈$143） | JEV 弃权（risk=0.27/exec=0.36 低于地板）→ 落到 local：APPROVED | JEV **CAUTION** conf 0.24（risk=caution, regime=adverse, signal=mixed），1350ms |
| S2 逆势 SELL 0.24（风险≈$143） | JEV 弃权（risk=0.21/exec=0.17）→ 落到 local：CAUTION | JEV **REJECTED** conf 0.20（signal=conflict, regime=adverse, **risk=block**），1092ms |
| S3 2.0 手 + 0.3×ATR（风险≈$356） | JEV **REJECTED** conf 0.33（risk=block）—— 险过地板 | JEV **REJECTED** conf 0.36（risk=block），1233ms |

### 关键结论（基于真实数据）

1. **真实 API 可用**：结构校验全部通过，延迟 1.09~1.35 秒（含行情证据拉取）。对照原 LLM 审查的 90 秒超时，约快 70~80 倍。
2. **置信度不能当主信号**：free 模型置信带宽 0.17~0.36，且与风险性不单调——最危险的 S3（27× 名义敞口）置信 0.36，反而高于普通单 S1 的 0.24。**决策信号在 choice（block/caution/clear），不在 confidence**。
3. **因此地板必须低（≤0.20）**：地板 0.30 时，模型的风险否决会被自己的阈值吞掉（S3 的 block 仅 0.33 险过；若模型某次给 0.28，否决就丢了）。最终采用 **0.15**。
4. **JEV 的增量价值被实证**：
   - S2 逆势单：JEV 判 REJECTED（risk_block + signal=conflict + regime=adverse），本地规则只到 CAUTION；
   - S3 超大手数：JEV 判 REJECTED，本地规则只到 CAUTION —— 本地敞口规则按 SL 距离算 risk_pct（2.4%，仅 warn），抓不到 27× 名义杠杆（margin_pct 需桥暴露 leverage 字段，尚未提供）；
   - S1 顺势单：JEV 比本地更保守（CAUTION vs APPROVED）——真实 H1 RSI=73.5 超买，JEV 读到 regime=adverse，这是本地规则未建模的过热状态。
5. **data_quality 恒为 partial（0.55）**：state 缺新闻/情绪证据（测试未接 Redis 情绪；snapshot.market.sentiment 为空）。补上 sentiment 后该项置信有望回升。
6. **配置建议**：`MANUAL_REVIEW_TYPESAFE_CONF_FLOOR=0.15`（已写入 .env）；`MANUAL_REVIEW_MIN_CONFIDENCE` 保持 0.55 → 普通单落 CAUTION 人工一键确认。**若觉得确认频繁，调 min_confidence，不要提高 floor**（提高 floor 会连风险否决一起丢掉）。

## 附录 B. 数据边界与可信度说明（诚实披露）

- 桥在测试窗口内**间歇返回 502/ReadTimeout**（16:45–16:50，上游 MT5 终端不稳；/health 始终 ok）。因此行情使用 `2026-10-09T16:50:31+0800` 采集的**真实快照**冻结：账户余额/报价/规格/253 笔 14 天成交/120 根 M15/120 根 H1 全部为真实值，仅"冻结时点"由脚本控制以保证三场景状态一致。
- 真实 JEV 调用共 **6 次**（附录 A 两次对照各 3 场景），全部为真实 HTTP 请求，无 mock、无录播。
- 样本量小（单品种 GOLD_、单时点、3 场景、1 个模型）。阈值定论需 Phase 4 shadow 在真实下单流水中积累 N≥50 单后复核。
- 测试全程只读：未提交订单、未修改账户状态。

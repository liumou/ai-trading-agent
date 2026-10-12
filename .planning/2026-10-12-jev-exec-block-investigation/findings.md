# Findings — JEV 执行阻断拦截调查

## 目标拦截记录：#296

- **订单**：GOLD BUY_LIMIT 0.01 手 @ 4170.0，SL=4165.0（5 点止损），TP=4210.0，注释"上一日支撑点4165"
- **时间**：2026-10-12 07:31:54 UTC（= 北京时间 15:31:54）
- **判决**：REJECTED，error_message = `JEV 判定拒绝：执行阻断｜检查：数据质量=部分可用（置信 0.43）；信号一致性=冲突（置信 0.95）；市场状态=不利（置信 0.92）；风险检查=通过（置信 0.60）；执行质量=阻断（置信 0.25）`
- **provider**：typesafe_jev（外部 JEV API，jev-1.13-free），延迟 1.5s
- **账户**：336773771（XM Global），GOLD 券商别名 GOLD_

## 拦截文案来源

`backend/app/services/typesafe_jev.py:_converge` 的 `reasoning` 格式（`_REASON_CN["jev_entry_rejected:execution_block"]` = "JEV 判定拒绝：执行阻断" + "｜检查：…"）。与 local_jev（systemone.py）文案格式不同（后者无 "JEV 判定拒绝" 前缀）——确认走 typesafe_jev。

## 判决逻辑链路（typesafe_jev.py:375-422 _converge）

```
confidence = min(risk_conf=0.60, exec_conf=0.25) = 0.25
if confidence < floor:  → 0.25 < 0.15? NO（.env 覆盖 floor=0.15）
if not allowed:  exec_choice=="block" → REJECTED（不检查 block 票自身置信）
```

**核心缺陷**：`_converge` 只对整体 min(risk,exec) 置信过 floor，**对"block"票自身没有任何置信门槛**。0.25 的低置信 execution block 被直接当作高置信否决。

## 根因一：floor=0.15 是刻意设计（非误配置）

`.planning/2026-09-27-jev/jev-realdata-report.md`（commit ad99b12 集成 JEV 时产物）明确：
> "**因此地板必须低（≤0.20）**：地板 0.30 时，模型的风险否决会被自己的阈值吞掉（S3 的 block 仅 0.33 险过；若模型某次给 0.28，否决就丢了）。最终采用 **0.15**。"
> "决策信号在 choice（block/caution/clear），不在 confidence"

即 **floor=0.15 是"宁可信其有"的保守设计**，让 JEV 的低置信否决票（risk/exec block）能生效。副作用：低置信 block（0.25）直接 REJECTED。

**复现验证**（`_jev_repro.py`，逐字复现）：
- floor=0.30（默认）→ ProviderUnavailable（降级链）→ local_jev 兜底，不会 REJECTED
- floor=0.15（.env）→ REJECTED conf=0.25，reason 与 #296 逐字一致

## 根因二：data_quality=partial (0.43) 是系统性已知盲点

- 真实数据报告结论 #5：**"data_quality 恒为 partial (0.55)：state 缺新闻/情绪证据…补上 sentiment 后该项置信有望回升"**
- #296 的 data_quality=partial (0.43) 与此一致 —— JEV 拿到的证据本就不完整（snapshot.market.sentiment 为空）
- findings.md:154：`data_quality 恒 partial(0.55)：state 缺新闻/情绪证据 → 后续把 snapshot.market.sentiment 并入 market_evidence`（**尚未实施**）

## 根因三：execution_quality=block 的直接诱因

- #296 是 **BUY_LIMIT 挂单**，`reference_price = ctx.entry_ref = 4170.0`（挂单价）
- 当日 GOLD 市价约 4200+（价格提醒 10:34/11:00 bid=4204-4205；Kline 15:30 正常渲染）
- JEV 的 execution_quality criteria 明确包含 **"reference-price deviation"**：挂单价 4170 vs 市价 ~4200 偏差约 30 点 → JEV 判执行阻断
- 且叠加：signal_alignment=conflict(0.95) + market_regime=adverse(0.92) 双高置信 —— JEV 认为市场方向与 BUY 相反

## 对比证据：#294（同天 04:58 UTC）CAUTION conf=0.37

- 同为 GOLD BUY 挂单，typesafe_jev 判 **CAUTION** 而非 REJECTED
- 说明 JEV 对类似抛单**通常给 CAUTION**（人在环确认），#296 的 REJECTED 是**偶然的低置信 block 票**
- 逐项对比（_jev_compare.py 实测）：
  | 检查 | #294（CAUTION） | #296（REJECTED） |
  |------|----------------|-----------------|
  | data_quality | partial 0.37 | partial 0.43 |
  | signal_alignment | mixed 0.76 | conflict 0.95 |
  | market_regime | neutral 0.45 | adverse 0.92 |
  | risk_check | clear 0.67 | clear 0.60 |
  | execution_quality | **caution 0.37** | **block 0.25** |
- 两者 exec 置信仅差 0.12，结果却是一个"确认后执行"、一个"硬拦截"——支撑 5.1 建议（exec block 加置信门槛后 #296 降 CAUTION 与 #294 一致）
- #294 最终实际结果 **FAILED**（Bridge code 10027 *AutoTrading disabled by client*）——证明 CAUTION 路径可正常走到 Bridge 执行，不是死端

## 设计权衡（诚实披露）

| 方向 | 后果 |
|------|------|
| floor=0.15（现配置） | JEV 否决票（含低置信 block）可生效 → 偶发误杀挂单（如 #296） |
| floor=0.30（默认） | 低置信 block 走降级链 → 但 JEV 的合法否决也可能被吞（S2/S3 测试场景） |
| 提高 block 置信门槛 | 需区分"risk block"（真风险）vs "exec block"（多为数据/挂单价偏差） |

## 测试盲区

`backend/tests/unit/test_typesafe_jev.py`：
- **无 `execution_quality=block → REJECTED` 测试用例**（只有 risk_block、signal_conflict+adverse）
- 测试固定 floor=0.30（:67），与实际 .env=0.15 不一致
- 无"低置信 block"场景测试

## 关联事件

- #295（07:31:40 UTC）：同账号 GOLD BUY 挂单被 guardrail 拒：`BUY TP 4120.0 <= entry 4170.0 — invalid`（TP 在 entry 下方，方向非法）
- #289（00:22 UTC）：GOLD SELL 市价单被 local_jev REJECTED（signal=conflict + regime=adverse + risk=caution）
- ohlcv_data 表 GOLD 自 2026-10-11 后无新数据（仅 BTCUSD 有）—— 历史采集暂停，但 JEV/local 走 Bridge 实时拉取，不受影响

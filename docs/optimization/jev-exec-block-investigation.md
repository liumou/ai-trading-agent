# 调查报告：手动下单被 JEV「执行阻断」拦截

> **结论先行**：这次拦截**不是 bug**，而是 JEV 决策系统在 `conf_floor=0.15`（刻意设计）下的**低置信执行阻断票被直接判定 REJECTED** 的结果。根因链条 = ① floor 刻意压低让否决票生效 → ② JEV 对 BUY_LIMIT 挂单（挂单价与市价偏差大）判"执行质量=阻断" → ③ **block 票自身置信（0.25）没有任何二次门槛**，直接 REJECTED。它不是数据质量故障（Bridge 在线、K线正常），但 **data_quality=partial（0.43）暴露了 JEV 证据盲点**（缺新闻/情绪证据，已知且未修复）。
>
> **一句话总结**：低置信执行阻断票（exec=0.25 block）在默认 `conf_floor=0.30` 下会走降级链让 local/LLM 兜底，但因 `.env` 将 floor 压到 0.15，这张票被直接当权威否决，订单被硬拦。

---

## 1. 拦截详情

| 项 | 值 |
|---|---|
| 审查记录 ID | **#296** |
| 订单 | **GOLD BUY_LIMIT 0.01 手 @ 4170.0**，SL=4165.0，TP=4210.0 |
| 注释 | "上一日支撑点4165" |
| 时间 | 2026-10-12 07:31:54 UTC（北京 15:31:54） |
| 账户 | 336773771（XM Global） |
| 审查 provider | **typesafe_jev**（外部 JEV API，`jev-1.13-free`），延迟 1.5s |
| 判决 | **REJECTED**（ref kind `systemone_rejected`，retryable=false） |
| 拦截文案 | `JEV 判定拒绝：执行阻断｜检查：数据质量=部分可用（置信 0.43）；信号一致性=冲突（置信 0.95）；市场状态=不利（置信 0.92）；风险检查=通过（置信 0.60）；执行质量=阻断（置信 0.25）` |

### 五道检查明细（JEV 原始 answers）

| 检查 | JEV 判定 | 置信 | 含义 |
|------|---------|------|------|
| data_quality | partial 部分可用 | 0.43 | 证据不完整（缺新闻/情绪）|
| signal_alignment | conflict 冲突 | 0.95 | 双周期趋势与 BUY 方向相悖 |
| market_regime | adverse 不利 | 0.92 | 市场状态不支持该方向 |
| risk_check | clear 通过 | 0.60 | 仓位/敞口无问题 |
| **execution_quality** | **block 阻断** | **0.25** | **挂单参考价与市价偏差** |

---

## 2. 判决逻辑（`backend/app/services/typesafe_jev.py:_converge`）

```python
confidence = round(min(risk_conf=0.60, exec_conf=0.25), 3)   # = 0.25
if confidence < floor:      # 0.25 < 0.15? → NO    ← .env 覆盖 floor=0.15
    raise ValueError(...)   # 走降级链（本应触发）
...
if not allowed:             # exec_choice == "block" → True
    if risk_choice == "block": ...
    elif exec_choice == "block":
        verdict, reason = "REJECTED", "jev_entry_rejected:execution_block"
```

**关键缺陷**：收敛器只对整体置信（min of risk/exec）过了 `conf_floor`，但**对触发 REJECTED 的 block 票本身没有任何置信门槛**。一张 0.25 置信的 execution block 不满但其心不虚的低置信票，被直接当作权威否决。

**逐字复现验证**（`backend/_jev_repro.py`，同一组 answers）：

| conf_floor | 结果 |
|---|---|
| 0.30（代码默认） | `ProviderUnavailable` → **降级链**（local_jev → LLM 兜底），不产生 REJECTED |
| 0.15（.env 实际值） | **`REJECTED confidence=0.25`**，reason 与 #296 逐字相同 |

---

## 3. 根因分析

### 根因一（主因）：`conf_floor=0.15` 是刻意设计，但无 block 置信二次门槛

`.planning/2026-09-27-jev/jev-realdata-report.md`（集成 JEV 的实测报告，commit `ad99b12`）明确记录了 floor 的取舍：

> "决策信号在 **choice**（block/caution/clear），不在 confidence。"
> "**因此地板必须低（≤0.20）**：地板 0.30 时，模型的风险否决会被自己的阈值吞掉（S3 的 block 仅 0.33 险过；若模型某次给 0.28，否决就丢了）。最终采用 **0.15**。"

这是「宁可信其有」的保守设计——**低置信的风险/执行否决票宁可误拦也不放过**。副作用正是本次事故：**低置信 block（0.25）直接 REJECTED**。
报告还明确警告：
> "若觉得确认频繁，调 min_confidence，**不要提高 floor**（提高 floor 会连风险否决一起丢掉）。"

### 根因二（诱因）：BUY_LIMIT 挂单价与市价偏差 → JEV 判 execution_block

- #296 是 **BUY_LIMIT @ 4170**，而当日 GOLD 市价在 **4200+**（价格提醒记录 10:34/11:00 bid=4204-4205；K线 15:30 正常渲染 100 bars）。
- JEV 的 `execution_quality` criteria 明确含 **"reference-price deviation"**（参考价偏离）；`_state_payload` 里 `reference_price = ctx.entry_ref = 4170`。
- 叠加 `signal_alignment=conflict(0.95)` + `market_regime=adverse(0.92)` 双高置信相悖方向 → JEV 认为该 BUY 挂单执行条件差，判 block。

### 根因三（背景）：data_quality=partial 是系统性已知盲点

- JEV 实测报告结论 #5（原样）："**data_quality 恒为 partial (0.55)：state 缺新闻/情绪证据…补上 sentiment 后该项置信有望回升**"。
- `findings.md:154` 记录后续行动项：将 `snapshot.market.sentiment` 并入 `market_evidence`——**尚未实施**。
- #296 的 data_quality=0.43 正是该盲点的又一实例。但它不是拦截主因：即使 data_quality=sufficient，exec block 依然会 REJECTED。

---

## 4. 证据链与对比

### 4.1 同天对照组 #294（12:58 UTC）→ CAUTION

同为 GOLD BUY 挂单，typesafe_jev 判 **CAUTION**（conf=0.37）而非 REJECTED。说明 JEV 对类似 BUY 挂单**通常给 CAUTION（人在环确认）**，#296 的 REJECTED 是**低置信 block 票的偶发路径**。

**逐项对比（同一天，同 provider，同方向挂单）**：

| 检查 | #294（CAUTION） | #296（REJECTED） |
|------|----------------|-----------------|
| data_quality | partial 0.37 | partial 0.43 |
| signal_alignment | mixed 0.76 | **conflict 0.95** |
| market_regime | neutral 0.45 | **adverse 0.92** |
| risk_check | clear 0.67 | clear 0.60 |
| **execution_quality** | **caution 0.37** | **block 0.25** |
| 最终 verdict | CAUTION | REJECTED |

两者 execution_quality 的置信只差 0.12（0.37 vs 0.25），结果却是一个"确认后执行"、一个"硬拦截"。这直接支撑 5.1 建议：若给 exec block 加置信门槛（0.35），#296 的 0.25 会降为 CAUTION，与 #294 的确认路径一致。且 #294 的最终实际结果是 `FAILED`（Bridge 报 *AutoTrading disabled by client, code 10027*）——**证明 CAUTION 路径可正常走到 Bridge 执行**，不是死端。

### 4.2 前置事件 #295（07:31:40 UTC）

同账号 GOLD BUY 挂单被硬闸门 guardrail 拒：`BUY TP 4120.0 <= entry 4170.0 — invalid`（TP 设在入场价下方，方向非法）。这是用户在 #296 之前一次合法触发的**参数错误**被硬闸门拦下。

### 4.3 基础设施状态（排除项）

- MT5 Bridge **在线**：15:30（#296 前一分钟）K线图正常渲染 GOLD M15/H1（各 100 bars）。
- ohlcv_data 表 GOLD 自 10-11 后无新数据 —— 仅影响历史采集，**JEV/local 走 Bridge 实时拉取，不受影响**。
- 无 AI_AGENT_ERROR / 熔断 / 降级事件 —— 整链只有 typesafe_jev 一个 provider 正常产出了 REJECTED。

---

## 5. 修复建议（按优先级）

### 5.1 【推荐】为 block 票增加置信二次门槛

```python
# typesafe_jev.py _converge，在 exec_choice=="block" 分支前：
BLOCK_MIN_CONF = 0.35  # 可配置：manual_review_typesafe_block_min_conf
if exec_choice == "block" and exec_conf < BLOCK_MIN_CONF:
    # 低置信执行阻断 → 不强行 REJECTED，降 CAUTION（人在环确认）
    verdict, reason = "CAUTION", "jev_entry_low_confidence"
```

**为什么推荐**：
- 保持 floor 哲学（否决票整体生效），但**低置信 block 不再硬杀**，落到 CAUTION 人工确认——这恰好对齐 firewall 的「人在环」设计（真实数报里 free 模型 block 置信集中在 0.2~0.4）。
- exec block 往往是数据/挂单偏差（可人工核实），而非 deterministic 风险（如超大敞口）。**risk block 可保持低门槛，exec block 建议提高**。
- 需要新增测试：`execution_quality=block(0.25) → CAUTION`（当前测试完全无 exec block 用例）。

### 5.2 补充 data_quality 证据

实施 `findings.md:154` 的行动项：把 `snapshot.market.sentiment`（Redis `sentiment:latest:{symbol}`）并入 `_market_evidence`，让 JEV 的 data_quality 不再恒为 partial。这能提升 JEV 判断质量，减少低置信 block 场景。

### 5.3 前端/UX 缓解（可选）

- REJECTED 展示已有 `rule_flags` + `reasoning` 明细，但 users 仍需手动重试。考虑让 `systemone_rejected`（非基础设施故障）显示"可修改参数后重提"的引导，与 `retryable=false` 区分开。

---

## 6. 附件：复现与验证

- 复现脚本：`backend/_jev_repro.py`（模拟同一组 JEV answers，对照 floor 0.30/0.15 两种结果）
- 对比脚本：`backend/_jev_compare.py`（读取 #294/#295/#296/#289 审查 JSON）
- 计划文件：`.planning/2026-10-12-jev-exec-block-investigation/`

---

*调查人：Claude Code · 2026-10-12 · 纯只读调查，未修改任何代码/配置*
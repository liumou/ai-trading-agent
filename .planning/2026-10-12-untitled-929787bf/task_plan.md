# 追涨杀跌风控检查与改造计划

- **计划 ID**: 2026-10-12-untitled-929787bf
- **日期**: 2026-10-12
- **状态**: in_progress

## Goal

检查风控中是否有追涨杀跌控制 → 已有(仅手动通道)→ 补齐自动引擎 + AI/MCP 通道的追涨杀跌风控。

## 调查结论(摘要)

| 通道 | spike_chase 追高 | loss_chase 亏损追单 | 情绪硬拒 | 冲动冷却 |
|------|------------------|---------------------|----------|----------|
| 手动 manual_order_gate | ⚠️ 存在但被短路(见下) | ⚠️ 同上 | ✅ block | ✅ |
| 自动 engine.py | ❌ | ❌ | ❌ | ✅(preflight 透传) |
| AI/MCP broker.py | ❌ | ❌ | ❌ | ✅(preflight 透传) |

**根因一(手动通道)**:spike_chase 内嵌在 `systemone.LocalRuleEngine`,但生产 `.env` 配置 `MANUAL_REVIEW_PROVIDER=typesafe_jev`
→ provider 链 = [typesafe_jev, local_jev] → typesafe_jev 成功返回有效判决(CAUTION/REJECTED)即 `break` 短路
→ **local_jev 的 spike_chase 在手动通道从未执行**。外部 JEV AI 只给"execution_quality=caution",不做 spike_chase 分位计算。
- 实例:订单 #989466362(id=300)SELL 追空,rule_flags=[],JEV CAUTION → 用户确认 → 执行,未被拦截。

**根因二(引擎/AI 通道)**:engine.py 与 broker.py 的下单走 `preflight_order`(不含 spike_chase 逻辑),`_check_trade_permission` 也无入场偏离过滤 → 自动通道完全无追涨杀跌控制。

## Phase 1 — 修复手动通道:local_jev 确定性规则不被 typesafe_jev 短路

目标:恢复本地 spike_chase/loss_chase 确定性规则对**每一笔手动单**的强制检查,不管外部 JEV 是否成功。

- [x] 1.1 抽出 `app/services/deviation_guard.py`(detect_spike_chase 纯函数),systemone._rule_spike_chase 改为复用
- [x] 1.2 手动闸门 `_build_snapshot` 前置执行 `_spike_chase_flags`:block 级直接拒(不经 JEV),warn 级进 rule_flags 审查链
- [x] 1.3 测试:deviation_guard 8 个单元测试 + manual_order_gate 3 个 spike 测试,101 测试全过无回归
- [x] 1.4 ruff:新文件全过;manual_order_gate 仅剩既有错误(非本次引入)
- [ ] 1.5 (可选)手动通道 loss_chase 同理前置(目前 warn 级已随 emotion_flags 进审查链,基本覆盖)

## Phase 2 — 复用 spike_chase 检测到统一硬闸门(覆盖引擎/AI 通道)

目标:把「入场偏离 EMA20/ATR14 滚动分位」检测抽出为公共函数,接入 `order_preflight`(所有通道真相源),使引擎 + AI/MCP 通道获得追涨拦截。

- [x] 2.1 `order_preflight` 增加 spike_chase 规则(复用 deviation_guard);引擎通道 warn 级也拒(无人值守)
- [x] 2.2 配置项:`preflight_spike_pct_warn` / `preflight_spike_pct_block`
- [x] 2.3 测试:3 个新测试(引擎拒单 / 手动跳过 / M15 不可用静默跳过),17 个 preflight 测试全过

## Phase 3 — 验证与收尾

- [x] 3.2 ruff lint:本次引入文件全过(manual_order_gate 既有 4+7 错误非本次引入)
- [x] 3.3 更新 CLAUDE.md 文档:order_preflight 加 spike_chase 说明 + deviation_guard 新条目
- [x] 3.1 跑全部相关测试:相关 56 个全过;完整套件 10 failed(全为既有 —— feishu×2 / ml_barrier×1 / multi_agent×7,均不在本次 diff),1297 passed 无回归

## Decisions

- [2026-10-12] 根因定位:生产 provider 配置 `typesafe_jev` 短路了 local_jev 的 spike_chase,导致手动通道追涨杀跌规则失效
- [2026-10-12] 修复方向:确定性 spike_chase 规则前置到硬闸门/情绪层,AI(JEV)只能补充不能覆盖
- [2026-10-12] 引擎/AI 通道:spike_chase 抽到 `order_preflight` 统一硬闸门覆盖

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| (暂无) | - | - |

## Next Step

全部 Phase 1-3 完成。追涨杀跌风控已覆盖三通道(手动前置 block/warn + 引擎/AI preflight warn 即拒)。待用户 review 后提交。

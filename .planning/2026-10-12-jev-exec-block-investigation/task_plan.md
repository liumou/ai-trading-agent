# Task Plan: JEV 执行阻断拦截调查与报告

## Goal
调查手动下单被 typesafe_jev（外部 JEV API）以「JEV 判定拒绝：执行阻断」拦截的完整链路，定位根因（数据质量 partial + 执行质量 block 低置信 0.25 却直接 REJECTED），生成可交付的调查报告文档。

## Next Step
~~验证 .env 中 `MANUAL_REVIEW_TYPESAFE_CONF_FLOOR=0.15` 是导致低置信 block 票通过地板的关键配置，形成修复建议。~~ → 已完成：调查报告 `docs/optimization/jev-exec-block-investigation.md` 已交付；复现脚本 + 对比脚本 + 修复建议验证脚本均已跑通，证据链闭合。

## Current Phase
全部完成（交付阶段）

## Phases

### Phase 1: 定位拦截链路与证据
- [x] 定位拦截文案来源：`typesafe_jev.py:_converge` 的 `reasoning` 格式（JEV 判定拒绝：执行阻断｜检查：…）
- [x] 找到 DB 真实记录 #296（GOLD BUY_LIMIT 0.01 @ 4170，SL 4165，TP 4210，2026-10-12 07:31:54 UTC）
- [x] 确认审查 provider 链：typesafe_jev → local_jev → llm（.env 设 `MANUAL_REVIEW_PROVIDER=typesafe_jev`）
- [x] 提取 review JSON：systemone.provider=typesafe_jev，checks 全通过 floor
- [x] 确认日志证据：15:31:54 chain start → 15:31:56 REJECTED conf=0.25，延迟 1.5s
- **Status:** complete

### Phase 2: 根因确认
- [x] 读全 `_converge`：block 票自身置信无保护（只有整体 min confidence 过 floor）
- [x] 确认 .env 覆盖 `MANUAL_REVIEW_TYPESAFE_CONF_FLOOR=0.15`（默认 0.30）
- [x] 计算：exec_conf=0.25 ≥ floor=0.15 → 通过地板；默认 0.30 则 0.25 < 0.30 → 降级链
- [x] 对比 #294（同天更早）：CAUTION conf=0.37 —— typesafe 低置信正常降 CAUTION
- [x] 确认数据采集链路：ohlcv_data 表 GOLD 自 10-11 后无新数据（但 JEV 走 Bridge 实时拉取，Kline 15:30 正常渲染）
- [x] 确认 JEV 收到 market_evidence 的具体内容 —— data_quality=partial 定因：state 缺新闻/情绪证据（jev-realdata-report.md 结论 #5 记录的已知盲点），#296 实测 0.43 与此一致
- [x] 复核 `_validate` 对 block 票置信的语义 —— 无二次门槛：block 票 0.25 直接 REJECTED；修复建议 5.1 已验证（exec block < 0.35 → CAUTION 人在环）
- **Status:** complete

### Phase 3: 测试盲区确认
- [x] 检查 test_typesafe_jev.py：无 `execution_quality=block → REJECTED` 测试用例
- [x] 确认测试对 floor 显式固定 0.30（`test_typesafe_jev.py:67`），与 .env 的 0.15 不一致
- [x] 运行测试确认当前行为：14 passed；修复建议脚本 `_jev_proposal_verify.py` 实测 REJECTED→CAUTION
- **Status:** complete

### Phase 4: 修复建议设计
- [x] 评估：恢复 floor=0.30 或为 block 票单独设置信门槛 —— 推荐后者（恢复 floor 会连 JEV 合法否决一起丢，违反 jev-realdata 设计）
- [x] 评估：exec/risk block 需要更高置信才触发 REJECTED —— exec block 建议 BLOCK_MIN_CONF≥0.35（多为挂单/数据偏差，可人工核实）；risk block 保持低门槛（deterministic 风险）
- [x] 评估：data_quality=partial 时是否应限制 block 生效 —— 记录为盲点，建议补 sentiment 证据（5.2），不单独作为 block 前置条件
- **Status:** complete

### Phase 5: 生成报告文档
- [x] 撰写调查报告（根因、证据链、修复建议、复现步骤）
- [x] 交付给用户
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 根因定位为「低置信 block 票通过过低的 conf_floor 直接 REJECTED」 | exec_conf=0.25 被 .env 覆盖的 floor=0.15 放过；默认 0.30 会触发降级链 |
| 拦截是 JEV 模型对 BUY_LIMIT 挂单的市场证据判断，非基础设施故障 | Kline 15:30 正常渲染，Bridge 在线 |
| 这是「审查逻辑盲区 + 配置放宽」组合 | 单一因素不必然导致此拦截 |
| floor=0.15 是刻意设计，不可简单恢复默认 | jev-realdata-report.md：地板高会吞掉 JEV 自身的合法否决票 |
| 推荐「block 票置信二次门槛」而非改 floor | #294/#296 对比：exec caution(0.37)→CAUTION、exec block(0.25)→REJECTED，加 0.35 门槛后 #296 与 #294 行为一致，人环介入 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 分类器反复临时不可用 | 改用只读分析、分多次查询 |
| psycopg2 不接受 +asyncpg 协议前缀 | 替换为 postgresql:// 前缀 |
| ohlcv_data 无 GOLD 近期数据 | 确认是采集链路暂停，不影响 JEV（走 Bridge 实时） |
| _jev_compare.py 遍历 #289（local_jev）时 `KeyError: 'confidence'` | local_jev 的 checks 无 confidence 键，改用 `.get()` |
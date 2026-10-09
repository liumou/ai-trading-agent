# Task Plan: 交易纪律风控系统增强 — 优化计划文档（两轮八路评审）

> 2026-10-09 由用户提出：「根据我的交易计划的文档，是否可以在系统里增加一些功能，如何解决频繁开仓、多空频繁切换、多次爆仓、总是想交易的交易行为，请根据现有系统的功能，生成优化的计划文档，计划需要我同意才能执行。」随后要求：多路评审该计划，一个一个 agent 串行执行（防 429），共两轮。期间报告历史订单时区问题。

## Goal
产出一份经两轮八路评审修订的《交易纪律风控增强》优化计划文档（v3）：四大行为问题映射到系统现有能力，含时区基础修复（F1-F6）、纪律时区口径（discipline_timezone）、运维开关、可测验收与里程碑。**本阶段仅交付计划文档，用户批准并冻结三项口径后才进入实施。**

## Next Step
将 v3 计划文档 + 两轮评审结论呈现给用户，请其批准 + 冻结三项二选一（纪律时区/日边界/保证金分母）。

## Current Phase
Phase 6 ✅（两轮八路评审完成 + v3 修订完成，等待用户批准）

## Phases

### Phase 1: 现状调研（已完成）
- [x] 提取用户交易计划 docx 全文；全仓库探索（防火墙/JEV/配置/链路/数据/前端）
- [x] 对照分析：四大行为问题 × 已有能力 × 缺口
- **Status:** complete

### Phase 2: 方案设计（已完成）
- [x] 设计原则：复用 preflight、集中纪律门禁、Redis 计数、优先级划分
- **Status:** complete

### Phase 3: 交付 v1（已完成）
- [x] 生成 v1 计划文档 + 规划文件
- **Status:** complete

### Phase 4: 第一轮评审 + v2 修订（已完成）
- [x] 评审 1 实现可行性 / 评审 2 风控逻辑 / 评审 3 架构安全 / 评审 4 测试验收（串行）
- [x] 修订 v2
- **Status:** complete

### Phase 5: 时区专项 + v2.1 修订（已完成）
- [x] 时区链路调查（Explore agent）；定位根因；修复方案 F1-F5
- [x] 修订 v2.1（3.0 节时区基础修复）
- **Status:** complete

### Phase 6: 第二轮评审 + v3 修订（已完成）
- [x] 评审 5 时区修复专项：F1 修法成立（MT5 time 是绝对 epoch）；F1 单独部署静默破坏后端；F2 收敛点 9 处不全；缺存量回填；F3 覆盖漏 5 处
- [x] 评审 6 修订落实复核：v2.1 解决第一轮 15/18 项；3.0 保留错误表述；改动清单缺 8 项
- [x] 评审 7 时区×纪律交互：纪律计数统一 discipline_timezone（默认上海）；streak 序列语义；until 绝对时刻；冷却与日界正交
- [x] 评审 8 可实施性终审：有条件 GO；部署顺序后端→前端→bridge；里程碑 M1-M5；需运维开关
- [x] 修订 v3：F1-F6 + 同窗部署 + 9 处收敛点 + 开关 + 里程碑 + 第九节口径表替换 + 附录
- **Status:** complete（等待用户批准 v3 + 冻结三项口径）

### Phase 7: 实施（未启动，需用户批准）
- [ ] 用户批准并冻结口径后另建实施计划（PLAN_ID），按 M1→M5 落地
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 本期交付物为计划文档（v3），不做代码变更 | 用户明确"计划需要我同意才能执行" |
| 两轮 8 个独立 general-purpose agent 串行执行 | 用户要求防 429 |
| 纪律时区 discipline_timezone 默认 Asia/Shanghai | 评审 7：约束的是北京的人，"UTC 周五"反直觉 |
| 存储/传输 UTC + 纪律边界本地时区双层 | 评审 7：互不干扰，F1-F3 与纪律计数解耦 |
| F1+F2+F3 同窗部署，顺序后端→前端→bridge | 评审 5/8：新 bridge+旧后端 TypeError 崩溃 |
| 存量历史数据一次性回填（F6） | 评审 5：用户核心投诉只修新数据无效 |
| 运维开关 discipline_gate_enabled | 评审 8：风控系统必须有一键回滚 |
| 回填主源 MT5 history | 评审 8：AI 通道不写 OrderAudit/Trade |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| init-session 中文名生成 slug 为 untitled-332db516 | 目录名无实际影响，标题手工写入 |
| 计划 v1 "966 用例"不可考 | 评审 4 实测，v2 改基线快照 diff；v3 以 --collect-only 实测为准 |
| v2.1 3.0 节"见第六节后附录"悬空 | v3 补文末附录（时区专项调查摘要） |

## Reference
- 交易计划文档：/Users/liumou/Downloads/黄金期货交易计划.docx（提取文本 /tmp/trading_plan.txt）
- 探索与评审结论：findings.md（本目录，含两轮 8 路评审结论）
- 交付物：docs/optimization/trading-discipline-enhancement.md（v3）
- 记忆：manual-trading-firewall、user-prefers-autonomous-chinese、backend-deployment-topology

# Task Plan: Agent 对话页重新设计

## Goal

重新设计 AI 交易 Agent 对话页面的布局与视觉，使其与 Wise 风格设计系统（dashboard/activity/insights 等页面）保持一致且美观，改善"感觉欠缺"的体验，同时不破坏现有会话管理、run 轮询与安全边界。

## Next Step

**第三轮评审完成（2026-09-18），结论 REVISE**。发现计划状态记录与实际代码系统性失真：P0/P1 已完成但未回填，P2 仅完成 1/5 核心项，P3 几乎未动，且 markdown 已超范围接入。评审见 `.omc/research/chat-redesign/plan-critic-round3.md`。

计划已迭代为 **V3**：新增 §2.4 收口阶段 **P2.5**（替代错位的原 P2 残余 + P3）。**下一步：P2.5 待用户批准后执行**，其中 M-6（markdown 保留 vs 回退）是唯一的范围决策点。

## Current Phase

Phase 3（实施）— P0/P1/P2 部分完成；Phase 3.5（第三轮评审）已完成；等待 P2.5 收口阶段批准

## Phases

### Phase 1: 探索与团队探讨
- [x] 探索现状：chat 页面代码、chat 组件、设计系统、API 类型
- [x] 记录探索发现到 findings.md
- [x] 启动 omc:team 探讨：designer（视觉方案）+ critic（约束评审）+ planner（实施结构）
- [x] 汇总 3 份探讨文档，整合为综合设计方案（plan.md）
- **Status:** complete

### Phase 2: 计划评审与迭代
- [x] 对综合方案（plan.md）进行第二轮批评评审（plan-critic-round2.md，结论 REVISE）
- [x] 迭代计划，解决评审问题（D3 定案方案 A、D4 拆分、3 个 MAJOR 遗漏补齐、P0-P3 阶段拆分、use-chat-session 不拆）
- [x] 提交用户审批（**门禁：未经批准不执行**）
- **Status:** complete

### Phase 3: 实施（已批准执行）
- [x] P0：CRITICAL 修复（page.tsx 两处 mode→"free" + create_session 验证测试）——后端 19 测试全绿，前端 tsc 通过
- [x] P1：组件抽取（message-bubble / conversation-thread / session-list / chat-toolbar / input-composer + RunPanel 保留）——tsc 通过，视觉保持现状
- [x] 时区统一：`frontend/lib/chat-utils.ts`（`parseChatDate` naive 串补 Z 视作 UTC，Asia/Bangkok 渲染，消 7h 偏差）
- [x] 气泡样式：wise-gradient 用户气泡 / bg-card 助手气泡 / 头像 / 时间戳 / animate-fade-in
- [x] 会话列表相对时间：手写 `Intl.DateTimeFormat`（**偏离原计划的 date-fns**，零新增依赖、效果正确——已更新计划而非改代码）
- [x] i18n 文案补齐：zh/en 各 103 key，双向差集为空
- [x] markdown 富文本接入 react-markdown（**超出原批准范围**，原 §1.8 定为「本次不做」——见 P2.5 的 M-6）
- [ ] P2.5 收口：RunPanel 折叠 / 空状态引导 / 100dvh / reduced-motion / table 样式 / memo / .gitignore
- **Status:** in_progress（P0/P1 完成，P2 部分完成，P3 未完成）

### Phase 3.5: 第三轮计划评审（2026-09-18）
- [x] 逐项对照代码核查计划自述（不以计划自述为准）
- [x] 输出评审 `.omc/research/chat-redesign/plan-critic-round3.md`（结论 REVISE）
- [x] 迭代计划为 V3：新增 §2.4 P2.5 收口阶段、修正 §1.8 markdown 状态
- [x] 回填 task_plan 状态失真、修正 2 项「计划偏离但实现合理」项
- **Status:** complete

### Phase 4: 验证
- [x] tsc 类型检查 + production build（`npm run build` 23/23 静态页通过）
- [ ] 移动端 / 暗色模式检查
- [ ] 回归：会话/轮询/断线恢复
- **Status:** in_progress

### Phase 5: 交付
- [ ] 代码审查
- [ ] 交付用户验收
- **Status:** pending

## Key Questions

1. 是否引入 markdown 渲染依赖？（待 critic 权衡新依赖 vs 无依赖方案）
2. 事件时间线/agent 过程的定位：常驻还是折叠？（默认折叠为"过程审计"）
3. 消息时间戳时区与格式？（倾向 Bangkok，同 activity 页 TH_TZ）
4. 移动端工具栏如何重排？（候选：SymbolTabs/横向滚动条）

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| 使用 omc:team 探讨方式做纯规划 | 用户明确要求"先生成计划文档，评审迭代，批准后才执行" |
| 聚焦前端 chat 页面，不改后端 | 后端 V2 数据已就绪，本任务为 P4 页面部分 |
| 只读安全边界保持 | 对话页为只读分析，不触碰交易执行 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| use-run-detail 文件扩展名 .tsx 读取失败 | 1 | 实际为 .ts，用正确路径重读 |

## Notes

- 本计划为纯规划阶段，不修改任何业务代码
- 3 个探讨 agent 输出到 `.omc/research/chat-redesign/`
- 历史参考：`.planning/2026-09-17-agent/chat-v2-plan.md`（P4 页面部分即本任务）
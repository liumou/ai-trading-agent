# Task Plan: Agent 对话式交易计划/报告功能

## Goal
新增一个与 AI 交易 Agent 的多轮对话功能：用户选择交易品种，通过聊天让 Agent 生成交易计划、市场报告或自由问答；只读分析，绝不执行交易。

## Next Step
已交付。剩余可选项：真实 PG 执行 `alembic upgrade head`、端到端联调（见测试报告第五节）。

## Current Phase
Phase 6 - 已交付

## Phases

### Phase 1: Requirements & Discovery
- [x] 确认现有代码中无任何 chat/conversation 功能
- [x] 确认可复用组件：run_agent_loop、MCP 工具（14 模块）、prompt_registry、guardrails、agent_prompts 路由范式
- [x] 确认安全边界：对话 Agent 不得挂执行类工具（place_order 等）
- [x] 记录到 findings.md
- **Status:** completed

### Phase 2: Planning & Structure
- [x] 确定方案（单 Agent 会话 + 三个预置意图 + 可选深度模式）
- [x] 确定后端/前端改动清单与 API 设计
- [x] 写入 task_plan.md，提交用户审批
- **Status:** completed（等待用户批准）

### Phase 3: Backend Implementation（未批准，不执行）
- [ ] `backend/mcp_server/agents/chat_agent.py` — 对话 Agent：系统提示词（中文交易顾问人设，禁止下单）、工具白名单（只读）、`run_chat_turn(history, user_msg, symbol)` 编排
- [ ] `backend/app/api/routes/agent_chat.py` — REST 路由：
  - `POST /api/agent-chat/sessions` 创建会话（symbol、timeframe、mode）
  - `GET  /api/agent-chat/sessions` / `GET /api/agent-chat/sessions/{id}` 会话列表/详情
  - `POST /api/agent-chat/sessions/{id}/messages` 发消息并返回 Agent 回复（同步，超时 120s）
  - `POST /api/agent-chat/sessions/{id}/preset` 快捷意图（trading_plan / report）
  - `DELETE /api/agent-chat/sessions/{id}` 删除会话
- [ ] 会话存储：DB 新表 `agent_chat_sessions` / `agent_chat_messages`（alembic 迁移），历史裁剪最近 20 轮入上下文
- [ ] `main.py` 注册路由
- **Status:** pending

### Phase 4: Frontend Implementation（未批准，不执行）
- [ ] `frontend/app/chat/page.tsx` — 聊天页：左侧会话列表，右侧消息流
- [ ] 品种选择器（复用 settings 的品种列表）、模式切换（交易计划/报告/自由问答）、"深度分析（多智能体）"按钮
- [ ] `frontend/lib/api.ts` 增加 agentChat API 封装
- [ ] 侧边栏 Sidebar 加入口
- [ ] i18n 中英文案
- **Status:** pending

### Phase 5: Testing & Verification（未批准，不执行）
- [ ] 后端 pytest：会话 CRUD、消息往返（mock run_agent_loop）、工具白名单不含执行工具的回归断言、历史裁剪
- [ ] 手动验证：创建会话 → 生成 GOLD 交易计划 → 追问 → 生成报告 → 删除会话
- [ ] 前端 build 通过
- **Status:** pending

### Phase 6: Delivery（未批准，不执行）
- [ ] 更新 docs（MULTI-AGENT-REPORT 或新 README 段落）
- [ ] git commit
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 方案 A：单 Agent 会话（Sonnet 档）而非每问跑一次 multi-agent | multi-agent 每轮 2-3 分钟、成本高；对话需要秒级响应。提供"深度分析"按钮按需触发 multi-agent |
| 对话 Agent 工具白名单硬编码只读工具 | 从机制上杜绝对话中下单（安全第一，比提示词约束更可靠） |
| 会话存 DB（非 Redis TTL） | 用户需要回看历史交易计划/报告，持久化符合审计习惯 |
| 历史裁剪最近 20 轮 | 控制上下文成本，避免长会话 token 爆炸 |
| 复用 prompt_registry 模式 | 支持用户在 /agent-prompts 自定义 chat 提示词（新 agent_id="chat_agent"） |
| 不提供任何下单工具 | 用户明确此功能是"计划/报告"，不是交易执行 |

## Errors Encountered
| Error | Resolution |
|-------|------------|


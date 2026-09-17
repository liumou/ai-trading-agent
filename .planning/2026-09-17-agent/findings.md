# Findings & Decisions

## Requirements
- 与 Agent 多轮对话，选择交易品种
- 生成交易计划 / 市场报告 / 自由问答
- 只读分析，绝不执行交易（用户未授权交易能力）

## Research Findings

### 现有可复用组件
- `backend/mcp_server/agents/base.py::run_agent_loop` — 统一 agent loop 入口（claude SDK / openai_compat 双通道）。跨请求对话历史需自行拼接进 user_message。
- `backend/mcp_server/agents/orchestrator.py::run_multi_agent` — 多智能体流水线（约 2-3 分钟/次），可作"深度分析"后端。
- MCP 工具 14 模块（`backend/mcp_server/tools/`）；**broker 模块绝不能给 chat agent**。
- `prompt_registry.py` — Redis 提示词热更新；AGENT_META 注册表，新 agent_id="chat_agent" 可无缝接入 /agent-prompts 页面。
- `agent_prompts.py` 路由范式（APIRouter + require_auth + Pydantic）；路由注册在 `main.py`。
- guardrails `validate_agent_call` — 每日 200 次上限，chat 接入计数。

### 现状缺口
- 无任何 chat/会话功能（搜索零命中）；DB 无会话/消息表，需 alembic 迁移。
- 前端无聊天页；Sidebar 在 `components/layout/Sidebar.tsx`；API 封装 `lib/api.ts`；i18n 中英 JSON。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 方案 A：单 Agent 会话 + 按需"深度分析"按钮 | 对话需秒级响应；multi-agent 每轮 2-3 分钟太贵 |
| 工具白名单硬编码只读 | 机制上杜绝对话中下单，比提示词约束可靠 |
| 会话/消息存 DB 新表 | 历史交易计划可回看、可审计 |
| 历史裁剪最近 20 轮入上下文 | 控制成本 |
| agent_id="chat_agent" 入 prompt_registry | 支持用户自定义提示词 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
- 计划文件：`.planning/2026-09-17-agent/task_plan.md`
- 架构参考：`docs/MULTI-AGENT-REPORT.md`


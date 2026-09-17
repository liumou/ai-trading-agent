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

## 2026-09-18 异常专项：分析报告全部子 Agent 超时（"Agent loop terminated / timeout"）

### 症状
用户报告「决策依据」报告四路全部未返回：
- 技术面报告：未返回（Agent loop terminated / timeout）— 无任何技术信号可用
- 基本面报告：未返回（同上）— 无情绪或基本面倾向可用
- 风险报告：未返回（同上）— 无 APPROVED / CAUTION 审批结论，lot/SL/TP 均未提供
- 反射器（Reflector）：未返回 — 无过拟合等级数据可供参考

### 定位（有代码/配置证据）
1. 该文案 `Agent loop terminated (timeout/max_turns)` 全仓库唯一出处：
   `backend/mcp_server/agents/openai_loop.py:345`。新的 V2 聊天 runtime
   （`chat_runtime.py` / `chat_sdk_runtime.py`）从不输出此串，只返回结构化
   `status`/`reason_code`。→ 报告的来源是**旧多智能体流水线**，不是 V2 聊天专家模式。
2. `backend/.env`：`LLM_PROVIDER=openai_compat`，`LLM_BASE_URL`=火山方舟 ARK，
   `LLM_MODEL=MODEL_ORCHESTRATOR=MODEL_SPECIALIST=deepseek-v4-flash`。
   → `run_agent_loop` 路由到 `openai_loop`。
3. `orchestrator.py::run_multi_agent`：reflector 先行（`reflector.reflect`），
   technical/fundamental/risk 三路 `asyncio.create_task` 并行，
   每路 `analyze()` 内部 `run_agent_loop` 带硬预算（specialist 60s/8 turns，
   orchestrator 120s/10 turns，见 orchestrator.py:163-171 与 MULTI-AGENT-REPORT.md）。
   预算耗尽 → `openai_loop` 返回兜底文案 + `error="agent loop failed (timeout or exception)"`，
   无 response 文本。
4. 上游调用：`app/bot/scheduler.py::_run_ai_agent`（candle 收盘 / 手动分析，
   agent_mode=multi 时）与 `app/runner/agent_entrypoint.py:96`。
5. 「决策依据 / 无任何技术信号可用 / 无过拟合等级数据可供参考」等中文不在前端代码里，
   是汇总层（LLM）把「子 Agent 返回空 + 兜底错误」转述成的报告——即失败被当成了
   「市场本身无信号」，而非「分析超时」。这会误导用户认为行情中性。

### 为什么 V2 还没解决它
- V2（P1→P4）实现了**新的只读聊天/专家报告**（`chat_workflow.py` 600s 总预算、
  结构化终态、部分结果保留、DB 事件留痕），并已接好前端 `mode="experts"`。
- 但 `progress.md` 明确：**V2 代码完成但未部署**（生产迁移/服务重启未执行）。
- 旧多智能体 `run_multi_agent`（含自动交易决策路径）**未迁移**，仍用 60/120s 硬超时，
  是本次异常的直发源。V2 计划曾声明「保留自动交易 loops 不变」。

### 结论
- 直接触发点：旧流水线对慢 LLM（deepseek-v4-flash@ARK）固定 60/120s 预算，
  与 `openai_loop` 仅在轮次边界检查总时长 → 子 Agent 反复超时、返回兜底空结果。
- 两条出路（可组合）：
  a) **部署 V2**（alembic 迁移 b8c9d0e1f2a3 + 重启后端），用新专家报告路径替换旧「决策依据」；
  b) **加固旧 run_multi_agent**：把 specialist/orchestrator 预算配置化并提高、
     让 `openai_loop` 超时时区分「分析超时」与「市场无信号」，汇总层不得把失败当中性。

## Resources
- 计划文件：`.planning/2026-09-17-agent/task_plan.md`
- 架构参考：`docs/MULTI-AGENT-REPORT.md`


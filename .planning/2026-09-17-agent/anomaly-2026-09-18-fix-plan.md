# 实施计划/清单：分析报告四路子 Agent 全超时（2026-09-18 异常）

> 状态：仅计划/清单，**尚未改任何代码**。根因见 `findings.md`「2026-09-18 异常专项」。
> 本文件是实施时的操作清单，供用户批准后逐项勾选执行。

## 0. 决策前提（已核实的事实）
- 异常来源 = 旧 `orchestrator.py::run_multi_agent`（openai_loop 60/120s 硬预算），
  文案唯一出处 `openai_loop.py:345`；新 V2 runtime 从不输出该串。
- 配置：`backend/.env` `LLM_PROVIDER=openai_compat`、ARK/deepseek-v4-flash。
- V2 代码已实现：`chat_workflow.py` / `chat_runtime.py` / `chat_sdk_runtime.py` /
  `chat_runs.py` / `app/api/routes/agent_chat.py` / 前端 `chat/page.tsx` + `api.ts`
  `mode="experts"`；`chat_worker` 已在 `main.py` lifespan 启动、路由已注册。
- 迁移链：alembic 单头 `b8c9d0e1f2a3`；新表 `agent_chat_sessions/messages/runs/events`
  已建（`models.py:466-520`）。预算配置键已存在（`config.py:190-196`）。

---

## 方案 A：部署 V2，切新专家报告路径（推荐，不碰自动交易循环）

### A1. 迁移数据库（只读前先备份）
- [ ] 备份：生产库 `pg_dump`（或对应托管方快照），记录迁移前 alembic 版本。
- [ ] `cd backend && alembic upgrade head`（预期落到 `b8c9d0e1f2a3`）。
- [ ] 验证：`alembic current` == `alembic heads` == `b8c9d0e1f2a3`（单头一致）。
- [ ] 验证四张表存在且结构正确（`\d agent_chat_runs`、`\d agent_chat_events`）。

### A2. 配置核对（`.env`）
- [ ] 确认/设置：`CHAT_TOTAL_TIMEOUT_S`（默认 600，范围 30–1800）、
      `CHAT_REQUEST_TIMEOUT_S`（默认 180）、`CHAT_TOOL_TIMEOUT_S`（60）、
      `CHAT_HEAVY_TOOL_TIMEOUT_S`（300）、`CHAT_MAX_TURNS`（15）、
      `CHAT_LEASE_S`（30）、`CHAT_WORKER_POLL_S`（2）。
- [ ] 确认 `LLM_MAX_RETRIES` 语义：`chat_budget()` 已用 `max(0, settings.llm_max_retries)`，
      避免 `or 2` 覆盖 0；按需显式设 0/1。
- [ ] 前端代理/网关对聊天接口的等待上限需 ≥ 聊天总预算（默认 600s），不全局延长普通 API。

### A3. 启动与验证
- [ ] 重启后端（`start-backend.sh` 或等效），确认 `chat_worker` 启动日志无异常。
- [ ] 健康检查 `/health` OK；无 alembic/导入报错。
- [ ] 冒烟：创建会话 → 提交 `mode="experts"` run（`POST /sessions/{id}/runs`）→
      轮询 `GET /runs/{id}` 观察 `agent_started/agent_completed` 增量事件与各专家报告。
- [ ] 验证慢场景：某专家因预算耗尽返回 `status!=completed` 时，run 终态为
      `incomplete/timed_out`（reason_code 区分），部分结果保留，**不会**伪装成成功报告。
- [ ] 前端：`mode="experts"` 报告区展示各 Agent 状态/耗时/部分结果，错误与有效报告视觉区分。

### A4. 验收（针对本次异常）
- [ ] 同一慢 LLM 场景下，「决策依据」报告能给出**结构化失败原因**（分析超时），
      而非把失败转述成「市场无信号」。
- [ ] 自动交易循环行为保持不变（scheduler `_run_ai_agent` 未改动、仍用旧 run_multi_agent）。

### A5. 回滚
- [ ] 若异常：停后端 → `alembic downgrade b8c9d0e1f2a3 之前版本` → 重启旧代码。
- [ ] 记录：迁移前 alembic 版本、备份文件路径、回滚验证命令。

---

## 方案 B：加固旧 `run_multi_agent`（止血；触碰自动交易决策路径，需谨慎）

### B1. 预算配置化（`orchestrator.py` / `agent_config.py`）
- [ ] 新增配置：`multi_agent_specialist_timeout_s`、`multi_agent_orchestrator_timeout_s`、
      `multi_agent_specialist_max_turns`、`multi_agent_orchestrator_max_turns`，
      带 pydantic 范围校验（对齐 config.py 既有 Field ge/le 风格）。
- [ ] `run_multi_agent`（orchestrator.py:163-171）改用配置值替换硬编码
      `timeout=120, max_turns=10`；specialist `analyze()` 内部预算同样配置化。
- [ ] 不改默认行为除非显式设更高值（避免自动交易被悄悄放宽）。默认可先保持现状，
      只把硬编码改为可配置。

### B2. 失败与「无信号」区分（openai_loop 与汇总层）
- [ ] `openai_loop.py`：超时/max_turns 分支保留结构化 `error`（已具备），
      并把「兜底文案」改为明确的失败语义（如 `Agent loop failed: total_timeout`），
      与「正常完成但无交易信号」区分开。
- [ ] 汇总层（`_build_synthesis_message` / 报告生成）：子 Agent 失败时，
      在「决策依据」中标注「分析超时/失败」，**不得**写成「市场无信号/中性」。
- [ ] 校验：`run_multi_agent` 返回的每路结果，调用方（scheduler/entrypoint）能识别失败并落
      `AI_AGENT_ERROR` 而非伪「分析结论」。

### B3. 回归测试（mock 慢 LLM，不连真实端点）
- [ ] 新增用例：慢模型在 60/120s 预算内未完成 → 返回结构化失败，无空兜底文案。
- [ ] 新增用例：子 Agent 失败时汇总层不输出「无信号/中性」。
- [ ] 新增用例：`max_retries=0` 不被 `or 2` 覆盖。
- [ ] 覆盖现有 `test_multi_agent.py` / `test_phase_e.py`（注意其既有环境失败与本次无关，
      用干净 main 对照）。

### B4. 影响评估与回滚
- [ ] 明确：B 方案改动的是**自动交易决策循环**，须先在非生产验证，再灰度。
- [ ] 记录变更前 orchestrator.py/agent_config.py diff，便于回滚。

---

## 方案 A+B 统一回归清单
- [ ] 慢 LLM 预算耗尽 → 结构化失败（非兜底空文案）。
- [ ] 失败与「无信号」语义分离。
- [ ] `max_retries=0` 语义正确。
- [ ] 前端错误/有效报告视觉区分。
- [ ] 自动交易循环与只读专家报告互不影响（隔离）。

---

## 决策与留痕
- [ ] 用户批准范围（A / B / A+B）后：更新 `task_plan.md` Phase 勾选，
      每步完成回填 `progress.md`，代码改动前 `git diff` 留底。
- [ ] 涉及部署时：迁移备份路径、alembic 前后版本、重启时间写入 progress.md。

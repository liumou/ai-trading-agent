# 测试报告 —— Agent 对话 V2（过程审计 + 超时治理 + 后台执行）

> 日期：2026-09-17 ｜ 代码基线：main + V2 改动 ｜ 环境：backend/.venv (Python 3.12)，全部 mock/隔离，不触真实 LLM/券商

## 一、测试总览（96 passed）

| 套件 | 用例 | 结果 |
|------|------|------|
| `test_chat_runtime.py`（只读双 provider 运行时） | 9 | ✅ 全过 |
| `test_chat_workflow.py`（单/专家工作流、失败降级、审计中止） | 5 | ✅ 全过 |
| `test_chat_deadline.py`（精确总预算硬截止） | 1 | ✅ 通过 |
| `test_chat_runs.py`（队列/租约/恢复/归档/脱敏） | 11 | ✅ 全过 |
| `test_agent_chat.py`（会话 CRUD、归档、结构化 error 不冒充成功） | 18 | ✅ 全过 |
| `test_chat_sdk_runtime.py`（SDK 适配器） | — | ✅ 通过 |
| 回归：`test_llm_lang.py` + `test_phase_e.py` | 52 | ✅ 全过 |
| 前端 `tsc --noEmit` | — | ✅ 0 error |
| Alembic 迁移链 | — | ✅ 单头 `b8c9d0e1f2a3`，已应用生产库 |

## 二、本次修复的核心问题

### 2.1 精确超时红绿验证（`test_chat_deadline.py`）
- **修复前**：模拟 provider 阻塞 30 秒、总预算 1 秒、外层测试保护 2 秒 → 超出 2 秒保护，抛 `TimeoutError`（不是明确状态）。
- **根因**：`run_chat_turn` 只把 timeout 传给底层循环，底层在循环边界检查时间，阻塞的模型/工具调用期间可超预算。
- **修复**：`chat_agent.run_chat_turn` 外层用 `asyncio.timeout(settings.chat_total_timeout_s)` 包裹整个循环，超时返回 `{status: "timed_out", reason_code: "total_timeout"}`，并覆盖阻塞 await。
- **修复后**：1 秒预算立即中断 30 秒阻塞，测试通过。已独立重跑确认。

### 2.2 失败不再冒充成功报告（`test_agent_chat.py::test_loop_termination_is_not_a_successful_reply`）
- **修复前**：接口只检查回答前缀 `Agent error:`，忽略结构化 `error` 字段 → `Agent loop terminated (timeout/max_turns)` 被当成功回答保存并返回 200。
- **修复后**：接口先检查 `result.error`，命中即 502，且不落库为 assistant 消息。红绿验证：先红（DID NOT RAISE）后绿。

### 2.3 队列租约序号缺陷（`test_chat_runs.py` 暴露）
- **缺陷**：`recover()` 写入中断事件但未递增 run 的 sequence 计数器 → 后续 `requeue` 产生 `(run_id, sequence)` 唯一约束冲突。
- **修复**：`recover()` 改用原子 `UPDATE ... RETURNING` 分配序号，与 claim/finish 一致。
- **覆盖**：claim 单次性、heartbeat 校验 token、finish 释放会话守卫、cancel 终态、恢复后不自动重跑、显式 requeue、归档在有活动任务时 409。

## 三、V2 交付范围（本次补齐）

### 后端
- `chat_agent.py`：外层硬截止（可配置总预算）
- `chat_runtime.py`：独立只读运行时（OpenAI 兼容 + Claude SDK 适配），事件流、公开文本保留、工具白名单、脱敏
- `chat_workflow.py`：`single` / `experts` 两模式；专家失败 → `incomplete`，绝不输出已批准计划；审计失败先停
- `chat_runs.py`：持久任务队列（DB，非 Redis 交易 Runner）+ worker（租约/心跳/恢复/取消）+ 归档 + 幂等 + 脱敏
- `agent_chat.py`：V2 端点（`POST /runs` 202、`GET /runs/{id}` 游标轮询、cancel、requeue、config）；DELETE 改为归档
- `config.py`：聊天专属预算（600s 总 / 180s 请求 / 60s 工具 / 300s 重工具 / 15 轮）
- `models.py` + 迁移 `b8c9d0e1f2a3`：`agent_chat_runs`、`agent_chat_events`、会话 `archived` / `active_run_id`
- `main.py`：lifespan 挂载 worker（非致命，表未建时安全排队）

### 前端
- `components/chat/`：`run-panel.tsx`（状态/原因/部分结果/Agent 报告/执行时间线）、`run-state.ts`、`use-run-detail.ts`（轮询+去重）
- `app/chat/page.tsx`：接入后台任务流，提交幂等键、轮询恢复、取消/重试、归档确认、专家模式
- `lib/api.ts` + 中英 i18n（各 93 键）

## 四、安全与边界
- 聊天与专家 Agent 工具白名单仅只读，机制上无法下单。
- 不在任何测试中连接真实模型、券商或生产迁移；生产迁移已显式执行并确认版本。
- 未隐藏/未声称捕获模型内部思维链，仅保存公开文本与执行轨迹。

## 五、剩余人工验收（未在自动化覆盖内）
1. 真实 LLM 端到端：创建会话 → 生成交易计划 → 追问 → 取消 → 重试。
2. 多 worker 并发/跨进程取消的行为（自动化为单进程租约语义）。
3. 前端生产构建 + 真实部署后菜单/页面验证。


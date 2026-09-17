# V2 进度（2026-09-17，已获批准）

- 实施中，未部署。业务基线17测试通过。
- 最小红绿验证1：exact loop termination 被路由当成功；新增回归先失败（DID NOT RAISE），检查result.error后18通过。
- 最小红绿验证2：模拟provider阻塞30秒、总预算1秒；原函数超过测试2秒保护触发TimeoutError；加入聊天专属配置和asyncio.timeout后19通过，独立重跑1通过。
- 提示词链路：修复chat_agent默认提示词未登记；实际函数为_load_defaults，导入与默认值校验成功；chat + deadline + language合跑39通过。
- 并行模块：独立双provider只读runtime、DB任务/事件worker/API、前端轮询轨迹；尚待完成与联调，不能宣称V2已交付。
- 后续验证不触发真实LLM/交易。生产迁移和服务重启未执行。


# Progress Log

## Session: 2026-09-18（A+B 实施）

### 方案 B（加固旧 run_multi_agent）——已实施并测试
- `backend/app/config.py`：新增 6 个多 Agent 预算配置
  （specialist/reflector/orchestrator 的 timeout 与 max_turns），默认对齐原硬编码值，
  带 pydantic 范围校验。
- `technical/fundamental/risk_analyst.py` 与 `reflector.py`：`analyze()/reflect()` 改读
  `settings.multi_agent_*` 预算（原硬编码 60/90s、8/10 turns）。
- `orchestrator.py`：
  - 新增 `_specialist_failed()` 识别 openai_loop 兜底失败（error 字段 / 空响应 /
    `Agent loop terminated` 等）。
  - `run_multi_agent` 收集后标记失败专家，报告区改为「分析未完成（超时或失败）…
    不是市场中性结论」，并计入 `errors`；reflector 失败同样处理。
  - orchestrator 循环预算改用 `settings.multi_agent_orchestrator_*`。
  - `_build_synthesis_message` 新增 `failed_specialists` 参数，注入
    `## IMPORTANT: analyst failures` 警告，禁止把失败当无信号。
- 测试 `test_multi_agent.py` 新增 5 例（失败识别、不当作中性、预算配置生效、
  synthesis 失败注记），全部通过。`test_phase_e`+`test_agent_config` 36 过。
  既有 7 例失败（TestModelSelection/TestBaseAgentLoop）为 .env 覆盖模型的既有环境问题，
  与本改动无关。

### 方案 A（部署 V2）——迁移已核实，待重启后端激活 B 改动
- 已核实 DB：4 张 chat 表（sessions/messages/runs/events）均存在，
  alembic 版本 = 单头 `b8c9d0e1f2a3`（start-backend.sh 启动时自动 `upgrade head`）。
- 运行中后端 PID 94706（uvicorn :8002，今日 00:00 启动，父进程为 zsh，无 supervisor）。
- 待办：重启后端以加载 B 改动（并确认 V2 chat worker 生效），随后 /health 验证。

### 待办（部署收尾）
- [ ] 重启后端（nohup 脱离会话）→ /health OK。
- [ ] 冒烟 `mode="experts"` run，确认增量事件与专家报告。
- [ ] 若重启失败，回滚：直接 `./start-backend.sh` 手动拉起并核对日志。

## Session: 2026-09-18（分析报告异常专项诊断）

### Current Status
- 完成对用户报告的「决策依据」四路子 Agent 全超时异常的根因定位（未改任何业务代码）。
- 证据链见 findings.md「2026-09-18 异常专项」。

### 根因结论
- 该异常来自**旧多智能体流水线** `orchestrator.py::run_multi_agent`
  （`openai_loop.py:345` 兜底文案为唯一出处），非新 V2 聊天 runtime。
- 触发点：`LLM_PROVIDER=openai_compat`（火山方舟 ARK / deepseek-v4-flash），
  specialist 60s/8 turns、orchestrator 120s/10 turns 硬预算在慢 LLM 下反复超时，
  返回「Agent loop terminated (timeout/max_turns)」+ 空 response；
  汇总层把「分析超时」误转述成「市场无信号」。
- V2（新专家报告、600s 预算、结构化终态、DB 留痕）代码已完成且前端 `mode="experts"`
  已接好，但**未部署**（无迁移/重启），故旧路径仍在生效。

### Actions Taken
- 复核 openai_loop / chat_runtime / chat_workflow / chat_sdk_runtime / orchestrator /
  scheduler / agent_entrypoint / chat_runs / config / 前端 chat+api.ts。
- 确认 `chat_worker` 已在 main.py lifespan 启动、`agent_chat` 路由已注册。
- 记录根因到 findings.md，更新本 progress。

### 待办（按范围，待用户批准后执行）
- 方案 A：部署 V2（alembic 迁移 b8c9d0e1f2a3 + 重启后端），切新专家报告路径。
- 方案 B：加固旧 run_multi_agent（预算配置化、失败与「无信号」区分、汇总不得当中性）。
- 无论 A/B：新增回归（慢 LLM 下 60/120s 预算被耗尽 → 结构化失败而非兜底空文案）。

## Session: 2026-09-17

### Current Status
- **Phase:** 6 - 已交付（代码完成 + 测试通过 + 测试报告生成）

### Actions Taken
- 调研现有代码：确认无对话功能，梳理可复用组件（run_agent_loop / MCP 工具 / prompt_registry / guardrails / 路由范式）
- 产出计划三件套，用户批准方案 A
- 后端：chat_agent.py（只读白名单 28 工具）、agent_chat.py 路由（5 端点）、DB 两表 + alembic 迁移 a7b8c9d0e1f2、prompt_registry 注册 chat_agent、main.py 注册路由
- 前端：/chat 聊天页、api.ts 封装、Sidebar 入口、中英 i18n（agentChat.json + nav 增键）
- 测试：test_agent_chat.py 17 用例全过；相邻回归 61 过；tsc 0 error；修复 4 处 TS 错误和测试 patch 目标错误
- 生成 docs/AGENT-CHAT-TEST-REPORT.md

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| test_agent_chat.py | 17 passed | 17 passed | ✅ |
| test_phase_e + test_multi_symbol_hardening 合跑 | 通过 | 61 passed | ✅ |
| test_llm_lang.py | 通过 | 20 passed | ✅ |
| tsc --noEmit | 0 error | 0 error | ✅ |
| alembic heads | 单头 a7b8c9d0e1f2 | 一致 | ✅ |

### Errors
| Error | Resolution |
|-------|------------|
| 测试 patch base.run_agent_loop 无效，真实调用 LLM | patch 使用方 chat_agent.run_agent_loop |
| 断言键名 allowed_tools/prompt 不存在 | 改 tool_names/user_message |
| 前端 4 处 TS 类型错误 | 逐一修复，tsc 复检通过 |
| reflector.py 误编辑 | 立即撤销，git diff 确认恢复 |
| test_multi_agent.py 5 例失败 | 干净 main 对照验证为既有环境问题（.env 真实 LLM 端点），与本次无关 |

# Progress Log — AI 分析超时问题

## Session: 2026-09-18 — 根因调查

### Actions Taken
- 恢复既有规划状态：发现 .planning/2026-09-17-agent（旧 multi_agent 超时计划，A+B 已批）与
  .planning/2026-09-18-analyze-recent-trades（解析 bug，已修复）
- 用户复报「AI 分析仍失败（超时）」，已修改数次未解决
- 定位真实根因（见 findings.md）：
  1. openai_loop.py:210 单请求 clamp 矛盾（LLM_TIMEOUT=300 被 specialist 180s 覆盖）
  2. V2 chat_workflow 串行专家 + 全局 deadline 挤压（reflector 90s < request 180s）
  3. chat_runtime reserve 15% 过早 summary
  4. .env 未设 CHAT_*，走默认值
- 初始化新计划目录：2026-09-18-ai

## Session: 2026-09-18（续）— 用户新增重试需求

### Actions Taken
- 用户询问：项目有无重试机制？能否加，间隔按次数增加？
- 盘点现有重试机制：
  - openai_loop.py:212：SDK 内部 max_retries，不可控
  - chat_runtime.py:173-184：自研重试但 APITimeoutError 不重试、退避 cap 2s、DEFAULT_BUDGET max_retries=0
  - llm_errors.py：is_connection_error() 把 timeout 归可重试但无调用方用它做重试
- 结论：现有机制不满足需求 → 计划新增方案 5「指数退避重试」：
  - 新增 app/ai/llm_retry.py 共享 helper（compute_retry_delay / is_retryable / 预算联动）
  - 两条路径统一接入（chat_runtime._openai_request + openai_loop）
  - APITimeoutError 纳入可重试
  - 退避间隔 base*2^attempt 封顶 max_s（15s→30s→60s→120s，用户反馈 2s 太短需放宽防平台拦截）
  - 平台限流（429）用更长间隔
  - 重试计入总预算，剩余不足即放弃
  - 新增配置 llm_retry_base_s=15 / llm_retry_max_s=120
- 更新 task_plan.md（Phase 3 增 B 组「指数退避重试机制」）+ findings.md（新增方案 5）

## Session: 2026-09-18（实施）— 用户批准后执行

### 代码修改
| 文件 | 改动 |
|------|------|
| `backend/app/ai/llm_retry.py`（新增） | 共享指数退避重试 helper：compute_retry_delay / is_retryable_error / has_retry_budget |
| `backend/app/config.py` | 新增 llm_retry_base_s=15 / llm_retry_max_s=120 |
| `backend/mcp_server/agents/chat_runtime.py` | _openai_request 改指数退避重试（APITimeoutError 可重试）、reserve_s 绝对兜底 |
| `backend/mcp_server/agents/chat_workflow.py` | chat_budget 加 reserve_s=30；专家编排并行化（reflector 与 tech/fund 并行） |
| `backend/mcp_server/agents/openai_loop.py` | 单请求超时改用全局 LLM_TIMEOUT（去 clamp）；弃用 SDK 重试，新增 _call_with_retry |
| `backend/.env` | 显式配置 CHAT_TOTAL_TIMEOUT_S=900 / CHAT_REQUEST_TIMEOUT_S=300 / CHAT_HEAVY_TOOL_TIMEOUT_S=600 / LLM_MAX_RETRIES=2 / LLM_RETRY_BASE_S=15 / LLM_RETRY_MAX_S=120 |
| `backend/tests/unit/test_llm_retry.py`（新增） | 14 个 helper 单测 |
| `backend/tests/unit/test_chat_runtime.py` | +5 重试集成测试 |
| `backend/tests/unit/test_openai_agent_loop.py` | +3 重试测试（TestRetryBackoff） |

### Test Results
| 测试集 | 结果 |
|--------|------|
| test_llm_retry.py | 14/14 ✅ |
| test_chat_runtime.py | 14/14 ✅（含 5 新重试） |
| test_openai_agent_loop.py | 14/14 ✅（含 3 新重试） |
| test_chat_workflow.py + test_chat_deadline.py | 6/6 ✅ |
| test_chat_runs / chat_sdk_runtime / agent_chat | 全绿 |
| test_multi_agent.py（排除环境失败类） | 15/15 ✅ |
| **全量 unit（排除 test_multi_agent）** | **691 passed** ✅ 无回归 |
| 配置加载 | .env 正确读入（15/120/2/900/300/600）|

### 已知环境失败（非本次引入）
- test_multi_agent.py 的 7 个失败（TestModelSelection 5 + TestBaseAgentLoop 2）
  是本地 `.env` 未跟踪文件覆盖模型默认值所致（MODEL_SPECIALIST=deepseek-v4-flash vs
  测试期望 claude-haiku-4-5-20251001）。与本次改动无关，之前 findings 已记录。

### Errors
| Error | Resolution |
|-------|------------|
| venv 无 ruff | py_compile 语法验证 + CI 覆盖 lint |

## Session: 2026-09-18（代码审查修复）— superpowers:requesting-code-review

### 审查发现与修复（commit 43fa2dc）
| 级别 | 问题 | 修复 |
|------|------|------|
| CRITICAL | C1：openai_loop 用 time.time()(wall-clock) 构造 deadline，llm_retry 用 time.monotonic() → 剩余预算恒 ~17.9 亿秒，重试预算闸门失效 | start_mono=time.monotonic()，统一 monotonic 基准 |
| IMPORTANT | I1：reserve_s=30 复制进小 allocation 专家（reflector 90s 占 33%）吃掉重试空间 | reserve 按 allocation 缩放（min(全局, alloc*fraction)） |
| IMPORTANT | I2：5xx 两条路径行为不一致（chat_runtime 有分支，openai_loop 没有） | 5xx 判定下沉到 llm_retry.is_retryable_error() 返回 server_error |
| IMPORTANT | I3：剩余预算 clamp 到 0.001s → synthesizer 拿 0.001s 预算整个分析被清空 | left<=0 时跳过该专家返回结构化失败 |
| IMPORTANT | I4：重试预算耗尽 reason_code 一律 total_timeout 掩盖真实原因 | retry_budget_exhausted:<tag> |
| MINOR | M2/M3/M7 | max(0,..) 统一 / reserve_s 显式 None 判断 / 删冗余 is_slow_timeout |
| MINOR | M5 测试缺口 | 新增 5xx 可重试、monotonic 契约、openai_loop 预算闸门防护 |

### Test Results（审查修复后）
| 测试集 | 结果 |
|--------|------|
| test_llm_retry / chat_runtime / openai_loop / chat_workflow | 53/53 ✅ |
| 全量 unit（排除 test_multi_agent 环境失败） | 697 passed ✅ |

# 测试报告 —— Agent 对话式交易计划/报告功能

> 日期：2026-09-17 ｜ 代码基线：main `bf2a10c` + 本次改动 ｜ 测试环境：macOS (darwin)，backend/.venv (Python 3.12)

## 一、测试总览

| 套件 | 结果 |
|------|------|
| `tests/unit/test_agent_chat.py`（本次新增，17 个用例） | **17 passed** |
| 相邻回归：`test_phase_e.py` + `test_multi_symbol_hardening.py`（44 个用例） | **61 passed**（与 chat 合跑） |
| `tests/unit/test_llm_lang.py`（提示词/语言链路回归） | **20 passed** |
| 前端 TypeScript 编译（`tsc --noEmit`） | **0 error** |
| Alembic 迁移链完整性（ScriptDirectory.get_heads） | **单头 `a7b8c9d0e1f2`** |

## 二、新增测试用例明细（test_agent_chat.py）

### 2.1 安全红线（TestChatAgentSafety）—— 4 passed
| 用例 | 断言 | 结果 |
|------|------|------|
| test_no_execution_tools | chat 工具白名单不含 `place_order` / `modify_position` / `close_position` | ✅ |
| test_no_write_tools | 不含任何写操作工具（log_decision、save_*、apply_strategy 等 8 项） | ✅ |
| test_whitelist_nonempty_and_str | 白名单非空且全为字符串 | ✅ |
| test_has_core_read_tools | 核心只读工具齐全（run_full_analysis / get_account / get_ohlcv / detect_regime） | ✅ |

**结论：对话 Agent 在机制上无法执行交易**——工具白名单硬编码只读，不依赖提示词约束。

### 2.2 消息构造（TestBuildUserMessage）—— 5 passed
| 用例 | 断言 | 结果 |
|------|------|------|
| test_plain_question | 当前问题与品种正确拼入 user message | ✅ |
| test_history_included | 历史对话完整拼接 | ✅ |
| test_history_cropped | 历史裁剪：仅保留最近 20 条，最旧的被剔除 | ✅ |
| test_preset_overrides_history | 快捷意图（交易计划）忽略历史、使用意图模板 | ✅ |
| test_preset_template_renders | 模板变量（symbol/timeframe）正确渲染 | ✅ |

### 2.3 run_chat_turn 分发（TestRunChatTurn）—— 2 passed
| 用例 | 断言 | 结果 |
|------|------|------|
| test_dispatch_with_history | 正确传递 system_prompt / user_message / tool_names / agent_id="chat_agent" | ✅ |
| test_requires_message_or_preset | 空消息且无意图 → ValueError | ✅ |

### 2.4 路由与持久化（TestChatRoutes，内存 SQLite + mock chat agent）—— 6 passed
| 用例 | 断言 | 结果 |
|------|------|------|
| test_create_and_get_session | 会话创建与详情读取 | ✅ |
| test_get_session_404 | 不存在的会话 → 404 | ✅ |
| test_send_message_roundtrip | 消息往返：用户+助手两条落库 | ✅ |
| test_preset_roundtrip | 快捷意图调用带 preset 参数并落库 | ✅ |
| test_agent_error_normalized_to_502 | LLM 失败（"Agent error:"）→ HTTP 502，不冒充正常回复 | ✅ |
| test_delete_session_cascades | 删除会话级联删除全部消息 | ✅ |

## 三、验证过程中发现并修复的问题

| # | 问题 | 修复 |
|---|------|------|
| 1 | 首轮测试 patch 目标错误（`mcp_server.agents.base.run_agent_loop`），导致测试**真实调用了一次 LLM**（产生 69.7s 真实请求） | 改为 patch `mcp_server.agents.chat_agent.run_agent_loop`（chat_agent 持有独立引用，必须 patch 使用方） |
| 2 | 断言键名与 base.run_agent_loop 实际参数不符（`allowed_tools`/`prompt`） | 修正为 `tool_names` / `user_message` |
| 3 | 前端 4 处 TS 类型错误（tool_calls 元素 unknown、sessionId 可空、Select onValueChange 可空） | 逐一修复，tsc 复检 0 error |
| 4 | 编辑过程中 reflector.py 曾被误改 | 立即撤销，`git diff` 确认恢复原状 |

## 四、与本次改动无关的既有问题（非阻塞）

- `tests/unit/test_multi_agent.py` 中 5 个用例在**干净 main 分支上同样失败**（已用 `git stash` 对照验证）：环境 `.env` 配置了 `openai_compat` 真实 LLM 端点，部分 ModelSelection/BaseAgentLoop 用例会打出真实网络调用（单次最长 69.7s）并因事件循环错配报错。**与本次改动无关**，建议后续修复（测试内强制 mock LLM provider）。

## 五、未覆盖项（人工验收建议）

1. `alembic upgrade head` 对真实 PostgreSQL 执行（本测试仅验证迁移链结构，SQLite 不兼容部分 PG 特性时需人工确认）
2. 端到端联调：启动 backend + frontend，创建会话 → 生成 GOLD 交易计划 → 追问 → 删除
3. 每日 200 次调用护栏的 429 行为（需 Redis 环境）

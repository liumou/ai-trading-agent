"""
OpenAI 兼容 Agent Loop — 自研工具循环，让交易决策路径支持任意 OpenAI 兼容模型。

与 sdk_agent_loop（Claude Agent SDK 内建 MCP 编排）平行的第二条通道，由
`base.py` 的 AGENT_LOOPS 注册表按 settings.llm_provider 分发。

安全设计（对齐已批准计划 v2.2 §2.1）：
  - 工具 schema 与执行统一走 FastMCP server 单一来源（杜绝名字/参数漂移）；
  - tool_names 白名单双向过滤（schema 层 + 执行层），复刻 SDK allowed_tools
    的「仅 orchestrator 有执行权」安全层；
  - 每循环交易工具执行数硬上限（llm_max_orders_per_loop，默认 1，最高 3）；
  - 非 Claude 路径默认限制 rollout=shadow/paper：micro/live 需显式
    llm_allow_live=true（AC-11，rollout 模式以 Redis 为准）；
  - guardrails 在 broker 工具函数内部，经 call_tool 执行天然继承，不可绕过；
  - 累计超时 + max_turns 双重终止条件；工具错误回喂模型而非崩溃（fail-closed）。

依赖 openai 库（requirements.txt: openai>=1.40,<2），函数内懒导入。
"""

import json
import time
from typing import Any

from loguru import logger

from app.config import settings

# 拥有真实资金影响的交易工具集合 —— 受每循环上限与 rollout 限制约束
TRADE_TOOL_NAMES = {"place_order", "modify_position", "close_position"}


async def _list_tool_schemas(server: Any) -> list[dict]:
    """从 FastMCP server 读取已注册工具的 schema（单一事实来源）。

    返回 OpenAI tools 参数格式：
        [{"type": "function", "function": {"name", "description", "parameters"}}]
    """
    tools = await server.list_tools()
    schema: list[dict] = []
    for t in tools:
        schema.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema or {"type": "object", "properties": {}},
                },
            }
        )
    return schema


def _normalize_tool_result(raw: Any) -> tuple[str, bool]:
    """把 FastMCP call_tool 的返回值序列化为回喂模型的字符串。

    不同 mcp 版本的返回形状：
      - list[TextContent | ...]（TextContent 有 .text，部分有 .isError）
      - tuple(list, dict structuredContent)（较新版本）
      - dict（个别封装层）
    返回 (序列化文本, is_error)。
    """
    structured: Any = None
    blocks: Any = raw
    if isinstance(raw, tuple) and len(raw) == 2:
        blocks, structured = raw

    # 优先 structuredContent（FastMCP dict 返回值会同时提供结构化结果）
    if structured is not None:
        try:
            return json.dumps(structured, ensure_ascii=False, default=str), False
        except Exception:
            pass

    parts: list[str] = []
    is_error = False
    for b in blocks or []:
        if getattr(b, "isError", False):
            is_error = True
        text = getattr(b, "text", None)
        if text is not None:
            parts.append(str(text))
    if parts:
        return "\n".join(parts), is_error
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False, default=str), False
    return "ok", False


async def _execute_tool(server: Any, name: str, args: dict) -> tuple[str, bool]:
    """通过 FastMCP server 执行工具（guardrails 在工具函数内部，天然继承）。

    工具不存在 / 参数错误 / 工具内部异常 → 返回错误文本给模型继续推理，
    不向外抛（fail-closed：不崩溃、不绕过白名单）。
    """
    try:
        raw = await server.call_tool(name, args)
        return _normalize_tool_result(raw)
    except Exception as e:
        # FastMCP 对未知工具/参数校验失败/工具函数异常统一抛 ErrorData 或 ToolError
        logger.warning(f"[openai_loop] tool {name} execution error: {e}")
        return f"Tool execution error: {e}", True


async def _rollout_allows_trade() -> tuple[bool, str]:
    """AC-11：非 Claude 路径默认限制 rollout=shadow/paper（Redis 为准）。

    micro/live 需要显式 llm_allow_live=true。返回 (允许, 当前模式/原因)。
    """
    from mcp_server.tools import broker as broker_mod

    guardrails = getattr(broker_mod, "_guardrails", None)
    if guardrails is None:
        # 未初始化 —— 交易工具自身会因 _require_init 失败，这里放行由工具层报错
        return True, "unknown"
    try:
        mode = await guardrails.get_persisted_rollout_mode()
    except Exception as e:
        logger.warning(f"[openai_loop] rollout mode read failed: {e} — fail-closed to deny trades")
        return False, f"rollout check failed: {e}"
    if mode in ("micro", "live") and not settings.llm_allow_live:
        return False, (
            f"rollout={mode}: 非 Claude provider 默认只允许 shadow/paper，"
            "需显式设置 LLM_ALLOW_LIVE=true 才放开（AC-11）"
        )
    return True, mode


async def openai_agent_loop(
    system_prompt: str,
    user_message: str | None = None,
    prompt: str | None = None,
    tool_names: list[str] | None = None,
    allowed_tools: list[str] | None = None,
    model: str | None = None,
    max_turns: int = 15,
    timeout: int = 120,
    agent_id: str = "unknown",
    **kwargs: Any,
) -> dict:
    """OpenAI 兼容工具循环。签名同时兼容 sdk_agent_loop 的参数命名
    （prompt/allowed_tools）与 run_agent_loop 的命名（user_message/tool_names），
    便于 base.py 统一分发。"""
    from app.ai.usage_logger import log_ai_usage

    user_message = user_message if user_message is not None else prompt
    tool_names = tool_names if tool_names is not None else allowed_tools
    model = model or settings.llm_model or settings.model_specialist

    start_time = time.time()
    text_parts: list[str] = []
    tool_calls_log: list[dict] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    turns = 0
    success = True
    response = ""

    # —— 每循环交易数上限（clamp 到 1..3）——
    max_orders = max(1, min(int(settings.llm_max_orders_per_loop or 1), 3))
    orders_attempted = 0

    try:
        # 懒导入：openai 未安装时返回错误结构（fail-closed），不影响 Claude 路径
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            logger.error(f"[openai_loop] openai 库未安装: {e}")
            return {
                "response": "Agent error: openai package not installed",
                "tool_calls": [],
                "turns": 0,
                "duration_s": round(time.time() - start_time, 1),
                "cost_usd": None,  # 与正常路径返回形状一致
                "error": str(e),
            }

        from mcp_server.server import get_server

        server = get_server()

        # schema 单一来源 + 白名单过滤（双向过滤之 schema 层）
        all_schemas = await _list_tool_schemas(server)
        if tool_names is not None:
            whitelist = set(tool_names)
            schemas = [s for s in all_schemas if s["function"]["name"] in whitelist]
        else:
            schemas = all_schemas
        tools_param = schemas if schemas else None

        client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "not-needed",
            # 每请求超时取 min(全局配置, 调用方预算)：specialist 传 60s 时
            # 不应被 settings 的 120s 覆盖（否则累计超时会 overshoot 一倍）
            timeout=min(settings.llm_timeout or timeout, timeout),
        )

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message or ""},
        ]

        while True:
            # 累计超时判定（不依赖单请求 timeout）
            if time.time() - start_time > timeout:
                logger.warning(f"[openai_loop] cumulative timeout after {timeout}s")
                success = False
                break
            if turns >= max_turns:
                # 达轮数上限但模型未给出最终答案 —— 非正常收敛，标记未完全成功（fail-closed）
                logger.info(f"[openai_loop] reached max_turns={max_turns}")
                success = False
                break

            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools_param,
                temperature=settings.llm_temperature,
            )
            turns += 1

            # usage 累计（各轮相加；保留 cached_tokens 细节 → _extract_tokens 识别）
            if getattr(resp, "usage", None):
                usage_total["prompt_tokens"] += getattr(resp.usage, "prompt_tokens", 0) or 0
                usage_total["completion_tokens"] += getattr(resp.usage, "completion_tokens", 0) or 0
                details = getattr(resp.usage, "prompt_tokens_details", None)
                cached = (getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
                if cached:
                    usage_total.setdefault("prompt_tokens_details", {})["cached_tokens"] = (
                        usage_total.get("prompt_tokens_details", {}).get("cached_tokens", 0) + cached
                    )

            choice = resp.choices[0] if resp.choices else None
            msg = choice.message if choice else None
            if msg is None:
                logger.warning(f"[openai_loop] empty choices from model (turn {turns})")
                break

            if not msg.tool_calls:
                response = msg.content or ""
                text_parts.append(response)
                break

            # —— 回喂 assistant 消息（必须原样带 tool_calls，OpenAI 协议硬性要求）——
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            # —— 逐个执行工具（tool_call_id 配对，顺序与 tool_calls 一致）——
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be a JSON object")
                except (json.JSONDecodeError, ValueError) as e:
                    # 参数幻觉：错误信息回喂而非崩溃
                    tool_result = f"Invalid tool arguments: {e}"
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
                    tool_calls_log.append({"tool": name, "input": {}, "error": str(e)})
                    continue

                # —— 记录区分「真实执行」与「被拦截」（H3：审计日志必须与事实一致）——
                # 白名单外工具 / 订单上限 / rollout 拦截的调用不进入 call_tool，
                # 记录带 blocked 标记且 executed=False，下游按 executed 统计真实下单。
                if tool_names is not None and name not in set(tool_names):
                    tool_calls_log.append({"tool": name, "input": args, "blocked": "whitelist", "executed": False})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"Tool not allowed for this agent: {name}",
                        }
                    )
                    continue

                # 交易工具：订单上限 + rollout 限制（AC-11）
                if name in TRADE_TOOL_NAMES:
                    if orders_attempted >= max_orders:
                        tool_calls_log.append({"tool": name, "input": args, "blocked": "limit", "executed": False})
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": (
                                    f"TRADE EXECUTION BLOCKED: per-loop trade limit "
                                    f"({max_orders}) reached. Provide your final answer now."
                                ),
                            }
                        )
                        logger.warning(
                            f"[openai_loop] trade limit hit: {orders_attempted}/{max_orders} — {name} blocked"
                        )
                        continue
                    allowed, reason_or_mode = await _rollout_allows_trade()
                    if not allowed:
                        tool_calls_log.append({"tool": name, "input": args, "blocked": "rollout", "executed": False})
                        messages.append(
                            {"role": "tool", "tool_call_id": tc.id, "content": f"TRADE BLOCKED: {reason_or_mode}"}
                        )
                        logger.warning(f"[openai_loop] rollout block: {reason_or_mode}")
                        continue
                    orders_attempted += 1  # 尝试即计数（成功/失败均计入），防模型反复试

                tool_calls_log.append({"tool": name, "input": args, "executed": True})
                logger.info(f"[Agent] Tool: {name}")
                tool_result, _is_error = await _execute_tool(server, name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

        if not response:
            response = "".join(text_parts) or (
                "No response" if success else "Agent loop terminated (timeout/max_turns)"
            )

    except Exception as e:
        logger.error(f"[openai_loop] agent error: {e}")
        success = False
        response = f"Agent error: {e}"

    duration = round(time.time() - start_time, 1)
    logger.info(f"[openai_loop] {turns} turns, {len(tool_calls_log)} tools, {duration}s")

    await log_ai_usage(
        agent_id=agent_id,
        model=model,
        usage=usage_total,
        cost_usd_sdk=None,
        duration_ms=int(duration * 1000),
        turns=turns,
        tool_calls_count=len(tool_calls_log),
        success=success,
    )

    result: dict = {
        "response": response,
        "tool_calls": tool_calls_log,
        "turns": turns,
        "duration_s": duration,
        "cost_usd": None,
    }
    if not success:
        result["error"] = "agent loop failed (timeout or exception)"
    return result

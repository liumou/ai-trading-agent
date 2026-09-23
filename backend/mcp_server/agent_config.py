"""
Agent configuration — entry points for AI trading agent.

Tools are served via MCP server (server.py), dispatched by Claude Agent SDK.
"""

import json
from pathlib import Path

from mcp_server.agents.base import run_agent_loop
from mcp_server.guardrails import AGENT_TIMEOUT, MAX_AGENT_TURNS

# ─── System Prompt ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT: str | None = None


def _load_system_prompt() -> str:
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        prompt_path = Path(__file__).parent / "system_prompt.md"
        _SYSTEM_PROMPT = prompt_path.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT


# ─── Agent Entry Points ─────────────────────────────────────────────────────

# 策略名抽取的关键词回退（3.3：laya choice 不可用时使用）。
# 关键词 → 英文策略名。原实现用 `keyword.lower().replace(" ", "_")` 对中文关键词
# 返回中文原文（如 "均值回归"），导致下游 strategy_switch 期望英文名时失配——修复。
_STRATEGY_KEYWORDS = [
    ("trend_following", "Trend Following"),
    ("trend_following", "趋势跟踪"),
    ("mean_reversion", "Mean Reversion"),
    ("mean_reversion", "均值回归"),
    ("breakout", "Breakout"),
    ("breakout", "突破"),
    # I4 对齐：注册表策略名是 momentum_rank（非 momentum），strategy_switch 才能 resolve。
    ("momentum_rank", "Momentum"),
    ("momentum_rank", "动量"),
    ("hold", "Hold"),
    ("hold", "持仓"),
]


def _keyword_strategy_fallback(decision: str) -> str:
    """从决策文本用关键词抽取策略名（laya 不可用时的回退）。

    顺序敏感是已知局限（原逻辑一致）；laya 可用时优先语义分类（3.3）。
    """
    lowered = decision.lower()
    for strategy, keyword in _STRATEGY_KEYWORDS:
        if keyword.lower() in lowered:
            return strategy
    return "ai_autonomous"


def _agent_error_from(result: dict) -> str | None:
    """从 agent loop 结果中提取失败信息；正常响应返回 None。

    两条通道（Claude SDK / openai_loop）失败时都会把 response 回退成
    "Agent error: <原因>"，并尽量在 error 字段单独带原始异常串。这里统一
    归一化：优先 error，否则解析 response 前缀——绝不让 "Agent error: ..."
    被当成一次真实分析决策（scheduler 据此落 AI_AGENT_ERROR 事件）。
    """
    error = result.get("error")
    if error and isinstance(error, str) and error.strip():
        return error
    response = result.get("response") or ""
    if isinstance(response, str) and response.startswith("Agent error:"):
        return response[len("Agent error:") :].strip()
    return None


async def run_agent(
    job_type: str,
    job_input: dict | None,
    model: str | None = None,
    lang: str | None = None,
) -> dict:
    """Run single-agent loop.

    Args:
        job_type: Job type
        job_input: Job parameters
        model: 显式模型（None 时按 agent 解析）
        lang: 输出语言（None 时用默认配置）
    """
    from mcp_server.agents.prompt_registry import get_active_prompt

    system_prompt = await get_active_prompt("single_agent", lang) or _load_system_prompt()
    user_message = _build_user_message(job_type, job_input)

    result = await run_agent_loop(
        system_prompt=system_prompt,
        user_message=user_message,
        model=model,
        max_turns=MAX_AGENT_TURNS,
        timeout=AGENT_TIMEOUT,
        agent_id="single_agent",
    )
    decision = result.get("response", "No decision")
    ai_error = _agent_error_from(result)
    if ai_error:
        decision = "HOLD (AI unavailable)"

    # Extract strategy name from decision text（3.3：优先 laya choice，回退关键词）
    strategy_used = "ai_autonomous"
    try:
        from app.ai.laya_runtime import laya_strategy_choice

        laya_result = await laya_strategy_choice(decision)
        strategy_used = laya_result["label"] if laya_result is not None else _keyword_strategy_fallback(decision)
    except Exception:
        strategy_used = _keyword_strategy_fallback(decision)

    return {
        "decision": decision,
        "strategy_used": strategy_used,
        "ai_error": ai_error,
        "turns": result.get("turns", 0),
        "tool_calls": result.get("tool_calls", []),
        "duration_s": result.get("duration_s", 0),
    }


async def run_multi_agent(
    job_type: str,
    job_input: dict | None,
    lang: str | None = None,
) -> dict:
    """Run multi-agent pipeline."""
    from mcp_server.agents.orchestrator import run_multi_agent as _run

    result = await _run(job_type, job_input, lang=lang)
    # orchestrator 自身的 LLM 失败同样归一化：response 为 "Agent error: ..." 时
    # decision 不再冒充分析结论。specialist 错误仍由 orchestrator 的 errors 字段承载。
    decision = result.get("decision", "HOLD")
    if isinstance(decision, str) and decision.startswith("Agent error:"):
        ai_error = result.get("error") or decision[len("Agent error:") :].strip()
        result["decision"] = "HOLD (AI unavailable)"
        result["ai_error"] = ai_error
    return result


def _build_user_message(job_type: str, job_input: dict | None) -> str:
    input_str = json.dumps(job_input, default=str) if job_input else "{}"
    if job_type == "candle_analysis":
        symbol = (job_input or {}).get("symbol")
        if not symbol:
            raise ValueError("candle_analysis job requires 'symbol' in job_input")
        timeframe = (job_input or {}).get("timeframe", "M15")
        return f"A new {timeframe} candle has closed for {symbol}. Analyze market conditions, detect regime, flag risks. Trading is handled by the strategy engine.\n\nJob input: {input_str}"
    elif job_type == "manual_analysis":
        symbol = (job_input or {}).get("symbol")
        if not symbol:
            raise ValueError("manual_analysis job requires 'symbol' in job_input")
        return f"Manual analysis requested for {symbol}. Provide thorough analysis with recommendation.\n\nJob input: {input_str}"
    elif job_type == "weekly_review":
        return f"Perform a weekly trading review.\n\nJob input: {input_str}"
    else:
        return f"Job type: {job_type}\nInput: {input_str}"

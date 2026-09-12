"""
AI usage logger — writes AIUsageLog rows after each SDK call.
Failures are swallowed: logging must never break the agent call.
"""

from typing import Any

from loguru import logger

from app.ai.pricing import calculate_cost
from app.db.models import AIUsageLog
from app.db.session import async_session


def _extract_tokens(usage: dict[str, Any] | None) -> tuple[int, int, int, int]:
    """从不同厂商的 usage dict 中归一化 token 计数。

    - Anthropic（SDK）：input_tokens / output_tokens / cache_read_input_tokens /
      cache_creation_input_tokens
    - OpenAI / DeepSeek：prompt_tokens / completion_tokens /
      prompt_tokens_details.cached_tokens（嵌套）
    两者同时出现时以 Anthropic 字段优先；未知厂商返回 0（不崩溃）。
    """
    if not usage:
        return 0, 0, 0, 0

    # input：Anthropic 字段优先，回退 OpenAI prompt_tokens
    input_tokens = usage.get("input_tokens")
    if input_tokens is None:
        input_tokens = usage.get("prompt_tokens", 0)

    # output：Anthropic 字段优先，回退 OpenAI completion_tokens
    output_tokens = usage.get("output_tokens")
    if output_tokens is None:
        output_tokens = usage.get("completion_tokens", 0)

    # cache_read：Anthropic 直读；OpenAI 在 prompt_tokens_details.cached_tokens
    cache_read = usage.get("cache_read_input_tokens")
    if cache_read is None:
        details = usage.get("prompt_tokens_details") or {}
        cache_read = details.get("cached_tokens", 0) if isinstance(details, dict) else 0

    cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)

    return int(input_tokens or 0), int(output_tokens or 0), int(cache_read or 0), cache_write


async def log_ai_usage(
    *,
    agent_id: str,
    model: str,
    usage: dict[str, Any] | None,
    cost_usd_sdk: float | None = None,
    duration_ms: int = 0,
    turns: int = 0,
    tool_calls_count: int = 0,
    success: bool = True,
) -> None:
    try:
        input_t, output_t, cache_r, cache_w = _extract_tokens(usage)
        cost_calc = calculate_cost(model, input_t, output_t, cache_r, cache_w)

        row = AIUsageLog(
            agent_id=agent_id,
            model=model,
            input_tokens=input_t,
            output_tokens=output_t,
            cache_read_tokens=cache_r,
            cache_write_tokens=cache_w,
            cost_usd_sdk=cost_usd_sdk,
            cost_usd_calc=cost_calc,
            duration_ms=duration_ms,
            turns=turns,
            tool_calls_count=tool_calls_count,
            success=success,
            raw_usage=usage,
        )
        async with async_session() as session:
            session.add(row)
            await session.commit()
    except Exception as e:
        logger.warning(f"log_ai_usage failed: {e}")

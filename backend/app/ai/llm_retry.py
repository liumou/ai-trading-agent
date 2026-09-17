"""LLM 指数退避重试 helper —— 两条 agent 路径（chat_runtime / openai_loop）共享。

统一「什么时候可重试、等多久、还允不允许重试」三类判定，避免 V2 聊天与旧自动交易
路径在重试行为上漂移。

设计（2026-09-18 用户批准）：
- 失败间隔按次数指数递增：base * 2^attempt，封顶 max_s。默认 base=15s → 15/30/60/120。
- 间隔放宽以规避平台限流（ARK 等对高频重试会返回 429）；平台明确限流（429）时
  直接用更长间隔（max_s 起步），避免重试反而加剧拦截。
- 重试计入调用方总预算：`has_retry_budget()` 检查剩余时长是否足够下一次退避，
  不足则放弃重试（fail-closed，避免把分析无限拉长）。
"""

from __future__ import annotations

import time


def compute_retry_delay(
    attempt: int,
    base_s: float,
    max_s: float,
    is_rate_limit: bool = False,
) -> float:
    """计算第 ``attempt`` 次失败后的等待间隔（秒）。

    指数退避：``base * 2^attempt``，封顶 ``max_s``。
    ``is_rate_limit``（平台明确 429）时直接返回 ``max_s`` —— 平台已经限流，
    普通退避起步的 15s 仍可能触发新的 429，取封顶更稳妥。
    """
    if is_rate_limit:
        return max_s
    return min(max_s, base_s * (2 ** max(0, attempt)))


def is_retryable_error(e: BaseException) -> tuple[bool, str]:
    """LLM 调用异常是否可重试，返回 (是否可重试, 稳定短标签)。

    复用 ``app.ai.llm_errors.classify_llm_error``：
    - timeout / connect / refused / dns：端点可达性问题，重试有意义。
    - rate_limit：平台限流，重试有意义但必须用更长间隔（调用方按
      ``is_rate_limit=True`` 走封顶退避）。
    其余（auth / 4xx 业务错误 / 其他）不可重试。
    """
    from app.ai.llm_errors import classify_llm_error

    tag = classify_llm_error(e)
    retryable = tag in ("timeout", "connect", "refused", "dns", "rate_limit")
    return retryable, tag


def remaining_retry_time(deadline: float, now: float | None = None) -> float:
    """距离 deadline 的剩余时长（秒）；已过则为 0。"""
    now = now if now is not None else time.monotonic()
    return max(0.0, deadline - now)


def has_retry_budget(
    deadline: float,
    next_delay_s: float,
    reserve_s: float = 0.0,
    now: float | None = None,
) -> bool:
    """剩余预算是否还够做下一次退避重试。

    重试前的最后一道闸：剩余时长必须 ≥ 下次退避间隔 + 保留时间，
    否则直接放弃重试（重试只会把分析拖到超时，不如返回结构化失败）。
    """
    left = remaining_retry_time(deadline, now)
    return left >= next_delay_s + reserve_s

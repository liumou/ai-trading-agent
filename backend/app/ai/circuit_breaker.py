"""LLM 熔断器 —— 端点在数分钟内不可达时避免每 15 分钟刷屏式重试。

触发条件：连续 `llm_circuit_threshold`（默认 3）次**连接类失败**
（见 app/ai/llm_errors.is_connection_error）。熔断开启后 `allow()` 返回
False，调用方直接跳过 LLM 请求（openai_loop 返回 HOLD 说明，provider 返回
None），冷却 `llm_circuit_cooldown_s`（默认 300s）后进入半开态放行一次探测。

进程内单例（uvicorn 多 worker 时各自独立；本服务单进程，够用）。
"""

from __future__ import annotations

import time

from loguru import logger


class LLMCircuitBreaker:
    def __init__(self) -> None:
        self._failures = 0
        self._opened_at: float | None = None
        self._recovered = True

    def _threshold(self) -> int:
        from app.config import settings

        return max(1, int(getattr(settings, "llm_circuit_threshold", 3) or 3))

    def _cooldown_s(self) -> int:
        from app.config import settings

        return max(1, int(getattr(settings, "llm_circuit_cooldown_s", 300) or 300))

    @property
    def open(self) -> bool:
        return self._opened_at is not None and not self._allow_probe()

    def _allow_probe(self) -> bool:
        """冷却期满 → 半开：重置计数，放行一次探测。"""
        if self._opened_at is None:
            return True
        if time.time() - self._opened_at >= self._cooldown_s():
            self._opened_at = None
            self._failures = 0
            return True
        return False

    def allow(self) -> bool:
        allowed = self._allow_probe()
        if not allowed and self._recovered:
            # 只打一次"熔断开启"日志，避免每 15 分钟重复刷屏
            logger.warning(
                f"[llm_circuit] OPEN — {self._failures} consecutive failures; "
                f"skipping LLM calls for {self._cooldown_s()}s"
            )
            self._recovered = False
        return allowed

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold() and self._opened_at is None:
            self._opened_at = time.time()
            logger.error(
                f"[llm_circuit] tripped at {self._failures} failures — cooldown "
                f"{self._cooldown_s()}s"
            )

    def record_success(self) -> None:
        if self._failures or self._opened_at is not None:
            logger.info("[llm_circuit] recovered (success)")
        self._failures = 0
        self._opened_at = None
        self._recovered = True


# 进程内共享单例
llm_circuit_breaker = LLMCircuitBreaker()
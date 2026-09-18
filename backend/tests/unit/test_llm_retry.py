"""LLM 指数退避重试 helper 测试（app/ai/llm_retry.py）。

覆盖：间隔按次数递增、429 用封顶间隔、可重试错误分类、预算联动判定。
"""

import time

import pytest

from app.ai.llm_retry import (
    compute_retry_delay,
    has_retry_budget,
    is_retryable_error,
    remaining_retry_time,
)


class TestComputeRetryDelay:
    def test_exponential_growth(self):
        """间隔按次数指数递增：15 → 30 → 60 → 120（封顶）。"""
        assert compute_retry_delay(0, 15, 120) == 15
        assert compute_retry_delay(1, 15, 120) == 30
        assert compute_retry_delay(2, 15, 120) == 60

    def test_capped_at_max(self):
        """封顶：超过 2^attempt 覆盖 max_s 后不再增长。"""
        assert compute_retry_delay(3, 15, 120) == 120
        assert compute_retry_delay(10, 15, 120) == 120

    def test_rate_limit_uses_max(self):
        """平台明确限流（429）直接用封顶间隔，不按普通失败递增。"""
        assert compute_retry_delay(0, 15, 120, is_rate_limit=True) == 120
        assert compute_retry_delay(5, 15, 120, is_rate_limit=True) == 120

    def test_custom_base(self):
        """自定义 base 生效。"""
        assert compute_retry_delay(0, 30, 300) == 30
        assert compute_retry_delay(1, 30, 300) == 60


class TestIsRetryableError:
    def test_timeout_retryable(self):
        e = Exception("Request timed out")
        retryable, tag = is_retryable_error(e)
        assert retryable is True
        assert tag == "timeout"

    def test_rate_limit_retryable(self):
        e = Exception("429 Too Many Requests")
        retryable, tag = is_retryable_error(e)
        assert retryable is True
        assert tag == "rate_limit"

    def test_connection_refused_retryable(self):
        e = ConnectionError("[Errno 61] Connection refused")
        retryable, tag = is_retryable_error(e)
        assert retryable is True
        assert tag == "refused"

    def test_auth_not_retryable(self):
        e = Exception("401 Unauthorized")
        retryable, tag = is_retryable_error(e)
        assert retryable is False
        assert tag == "auth"

    def test_value_error_not_retryable(self):
        e = ValueError("bad request")
        retryable, tag = is_retryable_error(e)
        assert retryable is False
        assert tag == "other"


class TestBudget:
    def test_remaining_retry_time(self):
        deadline = time.monotonic() + 50
        assert remaining_retry_time(deadline) == pytest.approx(50, abs=2)

    def test_remaining_past_deadline_is_zero(self):
        deadline = time.monotonic() - 1
        assert remaining_retry_time(deadline) == 0

    def test_has_retry_budget_true(self):
        deadline = time.monotonic() + 100
        assert has_retry_budget(deadline, next_delay_s=30, reserve_s=10) is True

    def test_has_retry_budget_false_when_short(self):
        deadline = time.monotonic() + 20
        assert has_retry_budget(deadline, next_delay_s=30, reserve_s=10) is False

    def test_has_retry_budget_false_at_deadline(self):
        deadline = time.monotonic()
        assert has_retry_budget(deadline, next_delay_s=15, reserve_s=0) is False


class TestServerErrorRetryable:
    """5xx 服务器错误应纳入可重试（两条路径统一经 helper 判定）。"""

    def test_api_status_500_retryable(self):
        from openai import APIStatusError
        import httpx

        req = httpx.Request("POST", "http://x")
        e = APIStatusError("boom", response=httpx.Response(500, request=req), body=None)
        retryable, tag = is_retryable_error(e)
        assert retryable is True
        assert tag == "server_error"

    def test_api_status_502_retryable(self):
        from openai import APIStatusError
        import httpx

        req = httpx.Request("POST", "http://x")
        e = APIStatusError("bad gateway", response=httpx.Response(502, request=req), body=None)
        retryable, _ = is_retryable_error(e)
        assert retryable is True

    def test_api_status_400_not_retryable(self):
        from openai import APIStatusError
        import httpx

        req = httpx.Request("POST", "http://x")
        e = APIStatusError("bad request", response=httpx.Response(400, request=req), body=None)
        retryable, tag = is_retryable_error(e)
        assert retryable is False
        assert tag != "server_error"


class TestMonotonicClockGuard:
    """预算闸门的时间基准契约（C1 防护）。

    关键：调用方（openai_loop / chat_runtime）必须用 time.monotonic() 构造 deadline，
    与 helper 内部的 now 基准一致。若误用 wall-clock（time.time()），remaining 会
    变成约 17.9 亿秒、闸门恒真、预算约束失效。helper 本身是纯函数无法拒绝 wall-clock，
    真正的防护在调用方测试（见 test_openai_agent_loop 的
    test_retry_budget_exhausted_stops_retrying）—— 这里锁定 helper 的单调基准语义。
    """

    def test_monotonic_deadline_correct(self):
        d = time.monotonic() + 60
        assert remaining_retry_time(d) == pytest.approx(60, abs=2)

    def test_has_retry_budget_uses_monotonic_base(self):
        """deadline 与内部 now 同基准（monotonic）时，剩余预算正确。"""
        import time as _t

        d = _t.monotonic() + 30
        assert has_retry_budget(d, next_delay_s=15, reserve_s=10) is True

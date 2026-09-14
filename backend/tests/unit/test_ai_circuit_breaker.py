"""LLM 熔断器测试（app/ai/circuit_breaker.py）——端点持续不可达时跳过 LLM 调用。"""

from unittest.mock import patch

from app.ai.circuit_breaker import LLMCircuitBreaker, llm_circuit_breaker


class TestLLMCircuitBreaker:
    def test_allow_when_idle(self):
        cb = LLMCircuitBreaker()
        assert cb.allow() is True

    def test_trips_after_threshold(self):
        cb = LLMCircuitBreaker()
        with patch.object(cb, "_threshold", return_value=3), patch.object(cb, "_cooldown_s", return_value=300):
            cb.record_failure()
            cb.record_failure()
            assert cb.allow() is True
            cb.record_failure()
            assert cb.allow() is False
            assert cb.open is True

    def test_open_blocks_until_cooldown_elapsed(self):
        cb = LLMCircuitBreaker()
        with (
            patch.object(cb, "_threshold", return_value=2),
            patch.object(cb, "_cooldown_s", return_value=300),
            patch("app.ai.circuit_breaker.time.time") as mock_time,
        ):
            mock_time.return_value = 1000.0
            cb.record_failure()
            cb.record_failure()
            assert cb.allow() is False

            # 冷却期内仍拦截
            mock_time.return_value = 1000.0 + 299
            assert cb.allow() is False

            # 冷却期满 → 半开，放行一次探测，计数重置
            mock_time.return_value = 1000.0 + 301
            assert cb.allow() is True

    def test_success_resets_failures(self):
        cb = LLMCircuitBreaker()
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failures == 0
        assert cb._opened_at is None
        assert cb.allow() is True

    def test_singleton_is_shared(self):
        assert isinstance(llm_circuit_breaker, LLMCircuitBreaker)
        # 幂等：成功记录不会反复日志
        llm_circuit_breaker.record_success()
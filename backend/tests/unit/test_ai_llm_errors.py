"""LLM 错误分类测试（app/ai/llm_errors.py）。"""

import httpx

from app.ai.llm_errors import classify_llm_error, format_llm_error, is_connection_error


def _openai_connection_error(httpx_exc: Exception) -> Exception:
    """模拟 openai SDK 的行为：APIConnectionError 默认文案 "Connection error."
    并把底层 httpx 异常挂在 __cause__ 链上。"""
    outer = Exception("Connection error.")
    outer.__cause__ = httpx_exc
    return outer


def test_refused_via_httpx_cause():
    e = _openai_connection_error(httpx.ConnectError("[Errno 61] Connection refused"))
    assert classify_llm_error(e) == "refused"
    assert is_connection_error(e) is True


def test_dns_via_httpx_cause():
    e = _openai_connection_error(httpx.ConnectError("[Errno 8] nodename nor servname provided"))
    assert classify_llm_error(e) == "dns"
    assert is_connection_error(e) is True


def test_timeout():
    e = _openai_connection_error(httpx.ConnectTimeout("timed out"))
    assert classify_llm_error(e) == "timeout"
    assert is_connection_error(e) is True


def test_bare_openai_connection_error():
    """无 cause 链的裸 APIConnectionError（openai 直接 raise）→ connect。"""

    class APIConnectionError(Exception):
        pass

    e = APIConnectionError("Connection error.")
    assert classify_llm_error(e) == "connect"
    assert is_connection_error(e) is True


def test_builtin_connection_error_refused():
    e = ConnectionError("[Errno 61] Connection refused")
    assert classify_llm_error(e) == "refused"
    assert is_connection_error(e) is True


def test_plain_exception_refused_message():
    """测试/胶水代码常用裸 Exception("connection refused") → 仍归 refused。"""
    e = Exception("connection refused")
    assert classify_llm_error(e) == "refused"
    assert is_connection_error(e) is True


def test_auth_and_ratelimit_are_not_connection_errors():
    e401 = Exception("401 Unauthorized")
    e429 = Exception("429 Too Many Requests")
    assert classify_llm_error(e401) == "auth"
    assert classify_llm_error(e429) == "rate_limit"
    assert is_connection_error(e401) is False
    assert is_connection_error(e429) is False


def test_other_error_is_not_connection():
    e = ValueError("bad request")
    assert classify_llm_error(e) == "other"
    assert is_connection_error(e) is False


def test_format_llm_error_includes_classification_and_base_url():
    e = _openai_connection_error(httpx.ConnectError("Connection refused"))
    text = format_llm_error(e, base_url="http://llm:8317/v1")
    assert "llm_error=refused" in text
    assert "base_url=http://llm:8317/v1" in text
    assert "ConnectError" in text
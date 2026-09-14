"""LLM 失败分类 —— 让 "Connection error." 这类含糊文案变得可诊断。

openai SDK 的 `APIConnectionError` 默认文案就是字面 "Connection error."，底层
httpx 原因（DNS 解析失败 / 连接拒绝 / 超时）藏在异常链里。这里把异常归类为
稳定短标签（dns / refused / connect / timeout / auth / rate_limit / other），
`classify_llm_error()` 供日志与通知展示，`is_connection_error()` 供重试/熔断
判定（只对端点可达性问题计入，避免把业务错误（如 400/500）误触熔断）。
"""

from __future__ import annotations


def classify_llm_error(e: BaseException) -> str:
    """把 LLM 调用异常归类为稳定短标签。"""
    name = type(e).__name__
    msg_lower = str(e).lower()
    if "Timeout" in name or "timed out" in msg_lower:
        return "timeout"

    # openai 库把 httpx 底层异常挂在 __cause__ / __context__ 链上
    cause = e.__cause__ or e.__context__
    cause_name = type(cause).__name__ if cause is not None else ""
    if cause is not None and cause_name in ("ConnectError", "ConnectTimeout", "ReadTimeout"):
        low = f"{cause}".lower()
        if "name or service not known" in low or "getaddrinfo failed" in low or "nodename nor servname" in low:
            return "dns"
        if "refused" in low:
            return "refused"
        if "timed out" in low or "timeout" in low:
            return "timeout"
        return "connect"

    if name == "APIConnectionError" or name == "ConnectionError":
        # 无 cause 链的裸连接错误（openai 直接 raise / 内置 ConnectionError）
        if "refused" in msg_lower:
            return "refused"
        if "timed out" in msg_lower or "timeout" in msg_lower:
            return "timeout"
        return "connect"

    if name == "APITimeoutError":
        return "timeout"
    if name in ("AuthenticationError", "PermissionDeniedError") or "401" in msg_lower or "403" in msg_lower:
        return "auth"
    if name == "RateLimitError" or "429" in msg_lower:
        return "rate_limit"
    # 兜底：基于消息文本的启发式（测试 mock 常用裸 Exception("connection refused")）
    if "refused" in msg_lower or "connection refused" in msg_lower:
        return "refused"
    if "name or service not known" in msg_lower or "getaddrinfo" in msg_lower or "nodename" in msg_lower:
        return "dns"
    return "other"


def is_connection_error(e: BaseException) -> bool:
    """端点可达性问题（重试/熔断只针对这类）。"""
    return classify_llm_error(e) in ("connect", "refused", "dns", "timeout")


def format_llm_error(e: BaseException, base_url: str = "") -> str:
    """结构化错误摘要：分类 + 异常类型 + 底层 cause + 端点（可读且可 grep）。

    供 openai_loop / provider 的 except 日志使用——替代裸 `str(e)`，让
    "Connection error." 变回 "llm_error=refused | exc=APIConnectionError |
    cause=ConnectError: [Errno 61] Connection refused | base_url=..."。
    """
    cause = e.__cause__ or e.__context__
    cause_repr = ""
    if cause is not None and cause is not e:
        cause_repr = f"{type(cause).__name__}: {str(cause)[:200]}"
    parts = [f"llm_error={classify_llm_error(e)}", f"exc={type(e).__name__}"]
    if cause_repr:
        parts.append(f"cause={cause_repr}")
    if base_url:
        parts.append(f"base_url={base_url}")
    parts.append(f"msg={str(e)[:200]}")
    return " | ".join(parts)
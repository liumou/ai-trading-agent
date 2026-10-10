"""公共递归清洗 —— 供复盘/聊天等 AI 输入输出两侧使用。

借鉴 chat_runs.py:33 sanitize 的递归策略（depth≤12 / list≤200 / 密钥打码），
抽出为独立模块，供历史订单复盘（trade_reviewer.py）与后续 AI 通道复用：
- 对 LLM 输入侧：嵌套 JSON（pre_trade_snapshot、MT5 deal 等）递归清洗，
  防 prompt injection（恶意注释内容夹带指令）与密钥泄漏。
- 对 LLM 输出侧：结构校验前的归一清洗（字符串截断、键名截断、secret 打码）。
"""
import hashlib
import json
import re

_SECRET_PATTERN = re.compile(r"(?i)(api.?key|authorization|password|secret|token|cookie)")
# 常见注入分隔符 —— 与 news_sentiment._clean 一致（新闻标题/注释等外部可控文本）。
INJECTION_DELIMITERS = ("---", "```", "<|", "|>", "###", "```", "<system", "<user", "ignore", "ignore previous")


def clean(value, limit: int = 24000, *, max_depth: int = 12, max_list: int = 200) -> dict:
    """递归清洗任意 JSON 值：密钥打码 + 深度/列表上限 + 整体字节截断。

    返回 dict（含 truncated 标记时 key 为 truncated/preview/sha256）。
    与 chat_runs.sanitize 语义一致，单独拆出以便 AI 输入输出复用。
    """

    def clean_inner(v, depth: int = 0):
        if depth > max_depth:
            return "[truncated:depth]"
        if isinstance(v, dict):
            return {
                str(k)[:100]: "[REDACTED]" if _SECRET_PATTERN.search(str(k)) else clean_inner(x, depth + 1)
                for k, x in list(v.items())[:max_list]
            }
        if isinstance(v, (list, tuple)):
            return [clean_inner(x, depth + 1) for x in v[:max_list]]
        if isinstance(v, str):
            return _redact_str(v)
        return v if v is None or isinstance(v, (int, float, bool)) else str(v)

    cleaned = clean_inner(value)
    raw = json.dumps(cleaned, ensure_ascii=False, default=str)
    if len(raw) > limit:
        return {
            "truncated": True,
            "preview": raw[:limit],
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "original_chars": len(raw),
        }
    return cleaned


def _redact_str(v: str) -> str:
    """单行化 + 去除指令分隔符 + Bearer/sk- 打码（外部可控文本防注入）。"""
    s = str(v).replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
    for bad in INJECTION_DELIMITERS:
        s = s.replace(bad, " ")
    s = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", s)
    s = re.sub(r"(?i)((?:api[_-]?key|password|secret|token)\s*[=:]\s*)[^\s,;\"}]+", r"\1[REDACTED]", s)
    return re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", s)


def bounded_text(value, limit: int = 24000) -> str:
    """截断长文本，超限追加 sha256 指纹（与 chat_runs.bounded_text 一致）。"""
    safe = clean(value, limit)
    if isinstance(safe, dict) and safe.get("truncated"):
        return safe["preview"] + "\n[truncated sha256=" + safe["sha256"] + "]"
    return str(safe)

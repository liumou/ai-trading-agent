"""
Claude model pricing (USD per 1M tokens) — used to compute equivalent API cost
for Max-subscription users whose SDK responses may omit total_cost_usd.
"""

PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.3,
        "cache_write": 3.75,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.0,
        "output": 5.0,
        "cache_read": 0.1,
        "cache_write": 1.25,
    },
}


def get_price_for_model(model: str) -> dict[str, float] | None:
    """查模型单价表：优先用户自定义（settings.custom_price_per_million），
    其次内置 Claude 表。未知模型返回 None（成本字段留空，不崩溃）。"""
    from app.config import settings

    custom = settings.custom_price_per_million or {}
    entry = custom.get(model)
    if isinstance(entry, dict):
        # 自定义表可能只给 input/output，缺失字段按 0 处理
        return {
            "input": float(entry.get("input", 0) or 0),
            "output": float(entry.get("output", 0) or 0),
            "cache_read": float(entry.get("cache_read", 0) or 0),
            "cache_write": float(entry.get("cache_write", 0) or 0),
        }
    return PRICING.get(model)


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int,
) -> float | None:
    p = get_price_for_model(model)
    if not p:
        return None
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read * p["cache_read"]
        + cache_write * p["cache_write"]
    ) / 1_000_000

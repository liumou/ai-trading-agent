"""LLM 输出语言解析与提示词语言指令注入。

大模型生成的自然语言文本（AI 决策分析、sentiment key_factors、优化
assessment/reasoning、量化 reasoning 等）应跟随「当前国际化配置」：
- 请求触发的生成：读请求头 Accept-Language（前端 locale，zh/en）；
- 后台定时任务（scheduler/runner，无请求上下文）：回退 settings.llm_response_lang。

注意：只本地化自然语言散文；枚举（BUY/SELL/HOLD、bullish/bearish/neutral）、
JSON key、技术术语（EMA/RSI/ATR/ADX/SL/TP）必须保持英文，否则会破坏前端的
枚举映射与后端 strategy_used 提取等依赖。
"""

from __future__ import annotations

DEFAULT_LLM_LANG = "zh"

LANG_NAMES = {
    "zh": "Simplified Chinese (中文)",
    "en": "English",
}

# 统一语言指令段，追加到各 system prompt 末尾。只约束散文语言，结构与术语不动。
LANGUAGE_INSTRUCTION_TEMPLATE = (
    "IMPORTANT — response language:\n"
    "- Write all natural-language prose (analysis, reasoning, assessment, key_factors, summaries) in {lang_name}.\n"
    "- Keep trading enums and technical terms in English exactly as-is: BUY, SELL, HOLD, NEUTRAL, "
    "bullish, bearish, neutral, EMA, RSI, ATR, ADX, SL, TP, and strategy-type keywords such as "
    "Trend Following / Mean Reversion / Breakout / Momentum.\n"
    "- Keep all JSON keys and enum values unchanged. Do NOT use emoji, icons, or unicode symbols."
)


def _default_lang() -> str:
    """settings.llm_response_lang，读取失败回退 DEFAULT_LLM_LANG。"""
    try:
        from app.config import settings

        return settings.llm_response_lang or DEFAULT_LLM_LANG
    except Exception:
        return DEFAULT_LLM_LANG


def resolve_llm_lang(request=None) -> str:
    """返回 LLM 输出语言。

    优先级：请求头 Accept-Language（zh/en）→ settings.llm_response_lang。
    ``request`` 为 FastAPI Request 或带 ``headers.get`` 的对象；None 表示后台任务。
    """
    if request is not None:
        headers = getattr(request, "headers", None)
        if headers is not None:
            accept = headers.get("accept-language")
            if accept:
                from app.i18n import pick_language

                return pick_language(accept, default=_default_lang())
    return _default_lang()


def language_instruction(lang: str | None = None) -> str:
    """渲染语言指令段；lang 为空或不在支持列表时用默认语言。"""
    if lang not in LANG_NAMES:
        lang = _default_lang()
    return LANGUAGE_INSTRUCTION_TEMPLATE.format(lang_name=LANG_NAMES[lang])


def append_language_instruction(prompt: str, lang: str | None = None) -> str:
    """把语言指令段追加到 system prompt 末尾（幂等，已包含则不重复追加）。"""
    if not prompt:
        return prompt
    block = language_instruction(lang)
    if block in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{block}"

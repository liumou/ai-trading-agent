"""
AI Client — 包装 LLM Provider（默认 Claude Agent SDK Max 订阅）。
AI 是可选层 — 所有调用失败返回 None。
"""

from loguru import logger

from app.ai.provider import get_provider
from app.config import settings

# 默认模型名（仅当 settings 未配置时作 fallback；实际模型调用时解析）
MODEL = "claude-haiku-4-5-20251001"

# Provider 懒加载单例（首次调用时按 settings 初始化）
_provider = None


def _resolve_model() -> str:
    """调用时解析实际模型：settings.llm_model > 默认常量。"""
    return settings.llm_model or MODEL


class AIClient:
    """AI client using a pluggable LLM Provider. No API key needed — Max subscription by default."""

    def __init__(self, provider=None):
        # 允许测试注入 mock provider；未注入时首次调用懒加载
        global _provider
        self._provider = provider or _provider

    def _get_provider(self):
        if self._provider is None:
            global _provider
            _provider = get_provider()
            self._provider = _provider
        return self._provider

    async def complete_json_async(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 256,
        agent_id: str = "sentiment",
    ) -> dict | None:
        try:
            provider = self._get_provider()
            return await provider.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=_resolve_model(),
                max_tokens=max_tokens,
                agent_id=agent_id,
                temperature=settings.llm_temperature,
            )
        except Exception as e:
            logger.error(f"AI JSON call failed: {e}")
            return None

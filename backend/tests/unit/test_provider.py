"""
单元测试 — app/ai/provider.py（Provider 抽象 + OpenAICompatProvider）。

全部 mock，不连网。覆盖：工厂分发、配置 fail-fast、complete/complete_json
解析与降级、_safe_json_loads 容错。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.provider import (
    ClaudeSDKProvider,
    OpenAICompatProvider,
    _safe_json_loads,
    get_provider,
)
from app.config import settings


def _mock_openai_response(content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    """构造一个 OpenAI ChatCompletion 形状的假响应对象。"""
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


class TestProviderFactory:
    def test_default_is_claude(self):
        with patch.object(settings, "llm_provider", "claude"):
            assert isinstance(get_provider(), ClaudeSDKProvider)

    def test_openai_compat_requires_base_url(self):
        """openai_compat 缺 base_url 必须 fail-fast（AC-10）。"""
        with patch.object(settings, "llm_provider", "openai_compat"), patch.object(settings, "llm_base_url", ""):
            with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
                get_provider()

    def test_openai_compat_with_base_url(self):
        with (
            patch.object(settings, "llm_provider", "openai_compat"),
            patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
            patch.object(settings, "llm_model", "qwen2.5:14b"),
        ):
            assert isinstance(get_provider(), OpenAICompatProvider)

    def test_openai_compat_requires_model_names(self):
        """openai_compat 模式下 llm_model 为空必须 fail-fast（AC-13）：
        否则简单补全路径会静默落到 Claude 默认模型名、对兼容端点必然失败。"""
        with (
            patch.object(settings, "llm_provider", "openai_compat"),
            patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
            patch.object(settings, "llm_model", ""),
        ):
            with pytest.raises(RuntimeError, match="LLM_MODEL"):
                get_provider()

    def test_openai_compat_requires_per_agent_models(self):
        """model_orchestrator/model_specialist 为空同样 fail-fast（AC-13）。"""
        with (
            patch.object(settings, "llm_provider", "openai_compat"),
            patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
            patch.object(settings, "llm_model", "deepseek-chat"),
            patch.object(settings, "model_orchestrator", ""),
            patch.object(settings, "model_specialist", "  "),
        ):
            with pytest.raises(RuntimeError, match="MODEL_ORCHESTRATOR.*MODEL_SPECIALIST"):
                get_provider()


class TestOpenAICompatProvider:
    @pytest.mark.asyncio
    async def test_complete_returns_text(self):
        provider = OpenAICompatProvider()
        fake = _mock_openai_response("hello world")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake)
        with (
            patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
            patch.object(settings, "llm_api_key", ""),
            patch("openai.AsyncOpenAI", return_value=mock_client),
        ):
            result = await provider.complete("sys", "user", "test-model", 128, "sentiment", 0.2)
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_complete_returns_none_on_error(self):
        """网络/API 异常 → 返回 None（不抛，AI 可选层语义）。"""
        provider = OpenAICompatProvider()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("connection refused"))
        with (
            patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
            patch("openai.AsyncOpenAI", return_value=mock_client),
        ):
            result = await provider.complete("sys", "user", "m", 128, "sentiment", 0.2)
        assert result is None

    @pytest.mark.asyncio
    async def test_complete_json_via_response_format(self):
        """官方支持 response_format 的厂商：一次调用即解析。"""
        provider = OpenAICompatProvider()
        fake = _mock_openai_response('{"label": "bullish", "score": 0.8}')
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake)
        with (
            patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
            patch("openai.AsyncOpenAI", return_value=mock_client),
        ):
            result = await provider.complete_json("sys", "user", "m", 128, "sentiment", 0.2)
        assert result == {"label": "bullish", "score": 0.8}

    @pytest.mark.asyncio
    async def test_complete_json_tolerates_code_fence(self):
        """模型输出 ```json 围栏时也能解析。"""
        provider = OpenAICompatProvider()
        fake = _mock_openai_response('```json\n{"label": "bearish"}\n```')
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake)
        with (
            patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
            patch("openai.AsyncOpenAI", return_value=mock_client),
        ):
            result = await provider.complete_json("sys", "user", "m", 128, "sentiment", 0.2)
        assert result == {"label": "bearish"}

    @pytest.mark.asyncio
    async def test_complete_json_falls_back_when_response_format_unsupported(self):
        """厂商不支持 response_format（如 Ollama）→ 降级 prompt 约束路径仍能解析。"""
        provider = OpenAICompatProvider()
        # 第一次（带 response_format）抛错，第二次（降级）返回可解析 JSON
        call_count = {"n": 0}

        async def _create(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("response_format not supported")
            return _mock_openai_response('{"ok": true}')

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_create)
        with (
            patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
            patch("openai.AsyncOpenAI", return_value=mock_client),
        ):
            result = await provider.complete_json("sys", "user", "m", 128, "sentiment", 0.2)
        assert result == {"ok": True}
        assert call_count["n"] >= 2

    @pytest.mark.asyncio
    async def test_complete_json_returns_none_on_unparseable(self):
        """始终返回非 JSON → 返回 None，不崩溃。"""
        provider = OpenAICompatProvider()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_openai_response("not json at all"))
        with (
            patch.object(settings, "llm_base_url", "http://localhost:11434/v1"),
            patch("openai.AsyncOpenAI", return_value=mock_client),
        ):
            result = await provider.complete_json("sys", "user", "m", 128, "sentiment", 0.2)
        assert result is None


class TestSafeJsonLoads:
    def test_plain_json(self):
        assert _safe_json_loads('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert _safe_json_loads('```json\n{"a": 1}\n```') == {"a": 1}

    def test_non_dict_returns_none(self):
        """JSON 数组/标量不是合法结果结构 → None。"""
        assert _safe_json_loads("[1, 2, 3]") is None

    def test_empty_returns_none(self):
        assert _safe_json_loads(None) is None
        assert _safe_json_loads("") is None

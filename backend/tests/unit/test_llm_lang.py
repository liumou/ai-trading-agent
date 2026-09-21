"""
LLM 输出语言解析与提示词语言指令注入的单元测试。

背景：LLM 生成的自然语言文本应跟随当前国际化配置（前端 locale / 后端默认）。
覆盖：
- pick_language 的 default 参数（错误翻译行为保持不变）
- resolve_llm_lang 的请求头优先 / 默认回退
- sentiment / optimization 提示词的语言指令注入（中英）
- prompt_registry.get_active_prompt 的运行时语言注入
- agent_config.run_agent 的 strategy_used 中英文关键词提取兼容
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.ai.language import append_language_instruction, language_instruction, resolve_llm_lang
from app.i18n import pick_language

ZH_BLOCK = "Simplified Chinese (中文)"
EN_BLOCK = "Write all natural-language prose"


class TestPickLanguageDefault:
    def test_default_applies_when_no_header(self):
        assert pick_language(None, default="zh") == "zh"

    def test_header_wins_over_default(self):
        assert pick_language("en-US,en;q=0.9", default="zh") == "en"

    def test_unknown_locale_falls_back_to_default(self):
        assert pick_language("fr-FR,fr;q=0.9", default="zh") == "zh"

    def test_zh_variant_matches(self):
        assert pick_language("zh-CN,zh;q=0.9,en;q=0.8", default="en") == "zh"

    def test_original_default_still_en(self):
        """零回归：错误文案翻译默认仍为 en。"""
        assert pick_language(None) == "en"


class TestResolveLlmLang:
    def test_no_request_uses_settings_default(self):
        with patch("app.ai.language._default_lang", return_value="zh"):
            assert resolve_llm_lang(None) == "zh"

    def test_request_header_takes_priority(self):
        class FakeHeaders:
            def __init__(self, value):
                self._v = value

            def get(self, key, default=None):
                return self._v

        class FakeRequest:
            headers = FakeHeaders("en-US")

        with patch("app.ai.language._default_lang", return_value="zh"):
            assert resolve_llm_lang(FakeRequest()) == "en"

    def test_request_without_header_uses_default(self):
        class FakeRequest:
            headers = {}

        with patch("app.ai.language._default_lang", return_value="zh"):
            assert resolve_llm_lang(FakeRequest()) == "zh"


class TestLanguageInstruction:
    def test_zh_block_renders(self):
        assert ZH_BLOCK in language_instruction("zh")

    def test_en_block_renders(self):
        assert EN_BLOCK in language_instruction("en")
        assert "in English" in language_instruction("en")

    def test_invalid_lang_falls_back_to_default(self):
        # 不在支持列表时不应抛异常，且回退到默认语言（zh）
        assert ZH_BLOCK in language_instruction("fr")

    def test_append_is_idempotent(self):
        prompt = "You are an analyst."
        once = append_language_instruction(prompt, "zh")
        twice = append_language_instruction(once, "zh")
        assert once == twice


class TestPromptInjection:
    def test_sentiment_prompt_zh(self):
        from app.ai.prompts import get_sentiment_prompt

        prompt = get_sentiment_prompt("GOLD", "zh")
        assert ZH_BLOCK in prompt
        assert "MUST be in English" not in prompt
        # 结构 key 不变（前端/解析依赖）
        assert '"key_factors"' in prompt
        assert '"sentiment"' in prompt

    def test_sentiment_prompt_en(self):
        from app.ai.prompts import get_sentiment_prompt

        prompt = get_sentiment_prompt("GOLD", "en")
        assert "in English" in prompt
        assert "Factor 1 in English" not in prompt

    def test_enhanced_sentiment_prompt_zh(self):
        from app.ai.prompts import get_enhanced_sentiment_prompt

        prompt = get_enhanced_sentiment_prompt("GOLD", "zh")
        assert ZH_BLOCK in prompt
        assert "context weighting rules" in prompt

    def test_optimization_prompt_zh(self):
        from app.ai.prompts import get_optimization_prompt

        prompt = get_optimization_prompt("zh")
        assert ZH_BLOCK in prompt
        assert '"assessment"' in prompt

    @pytest.mark.asyncio
    async def test_get_active_prompt_appends_lang_instruction(self):
        """运行时注入：即使默认提示词不含语言指令，get_active_prompt 也会追加。"""
        from mcp_server.agents import prompt_registry

        with (
            patch.object(prompt_registry, "_redis", None),
            patch.object(prompt_registry, "_inject_symbols", side_effect=lambda p: p),
        ):
            prompt = await prompt_registry.get_active_prompt("single_agent", "zh")
        assert ZH_BLOCK in prompt

    @pytest.mark.asyncio
    async def test_get_active_prompt_does_not_duplicate_instruction(self):
        from mcp_server.agents import prompt_registry

        with (
            patch.object(prompt_registry, "_redis", None),
            patch.object(prompt_registry, "_inject_symbols", side_effect=lambda p: p),
        ):
            prompt_zh = await prompt_registry.get_active_prompt("sentiment", "zh")
            prompt_en = await prompt_registry.get_active_prompt("sentiment", "en")
        # sentiment 默认已内嵌默认语言的指令段；显式 lang=en 时应追加英文指令
        assert ZH_BLOCK in prompt_zh
        assert "in English" in prompt_en


class TestStrategyUsedExtraction:
    @pytest.mark.asyncio
    async def test_chinese_decision_maps_to_strategy(self):
        """中文决策文本仍能提取 strategy_used（兼容改造核心）。"""
        from mcp_server.agent_config import run_agent
        from mcp_server.agents import prompt_registry

        mock_result = {
            "response": "市场处于趋势跟踪阶段，建议 BUY。",
            "tool_calls": [],
            "turns": 1,
            "duration_s": 2.0,
        }
        with (
            patch("mcp_server.agent_config.run_agent_loop", AsyncMock(return_value=mock_result)),
            patch("app.ai.laya_runtime.laya_strategy_choice", AsyncMock(return_value=None)),  # 3.3: 回退关键词
            patch.object(prompt_registry, "_redis", None),
            patch.object(prompt_registry, "_inject_symbols", side_effect=lambda p: p),
        ):
            result = await run_agent(job_type="candle_analysis", job_input={"symbol": "GOLD"})
        # 3.3 修复：策略名统一英文（不再返回中文原名），下游 strategy_switch 才能匹配。
        assert result["strategy_used"] == "trend_following"
        assert "BUY" in result["decision"]

    @pytest.mark.asyncio
    async def test_english_decision_maps_to_strategy(self):
        """零回归：英文关键词提取行为不变。"""
        from mcp_server.agent_config import run_agent
        from mcp_server.agents import prompt_registry

        mock_result = {
            "response": "Trend Following strategy is active, HOLD recommended.",
            "tool_calls": [],
            "turns": 1,
            "duration_s": 2.0,
        }
        with (
            patch("mcp_server.agent_config.run_agent_loop", AsyncMock(return_value=mock_result)),
            patch("app.ai.laya_runtime.laya_strategy_choice", AsyncMock(return_value=None)),  # 3.3: 回退关键词
            patch.object(prompt_registry, "_redis", None),
            patch.object(prompt_registry, "_inject_symbols", side_effect=lambda p: p),
        ):
            result = await run_agent(job_type="candle_analysis", job_input={"symbol": "GOLD"})
        assert result["strategy_used"] == "trend_following"

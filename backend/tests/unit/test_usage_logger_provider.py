"""
单元测试 — usage_logger 多厂商字段映射 + pricing 自定义成本。

覆盖：OpenAI/DeepSeek/Anthropic usage dict 归一化、未知模型成本 None、
自定义价格表命中。
"""

from app.ai.pricing import calculate_cost, get_price_for_model
from app.ai.usage_logger import _extract_tokens
from app.config import settings


class TestExtractTokensOpenAI:
    def test_openai_fields(self):
        """OpenAI：prompt_tokens/completion_tokens 归一化。"""
        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        assert _extract_tokens(usage) == (100, 50, 0, 0)

    def test_openai_with_cached(self):
        """OpenAI 嵌套 prompt_tokens_details.cached_tokens → cache_read。"""
        usage = {
            "prompt_tokens": 200,
            "completion_tokens": 80,
            "prompt_tokens_details": {"cached_tokens": 150},
        }
        assert _extract_tokens(usage) == (200, 80, 150, 0)

    def test_deepseek_cache_fields(self):
        """DeepSeek 也是 OpenAI 兼容字段，cached_tokens 同样识别。"""
        usage = {
            "prompt_tokens": 300,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 250},
        }
        assert _extract_tokens(usage) == (300, 40, 250, 0)


class TestExtractTokensAnthropic:
    def test_anthropic_fields(self):
        """Anthropic 原生字段保持优先。"""
        usage = {
            "input_tokens": 500,
            "output_tokens": 100,
            "cache_read_input_tokens": 400,
            "cache_creation_input_tokens": 50,
        }
        assert _extract_tokens(usage) == (500, 100, 400, 50)

    def test_anthropic_preferred_over_openai(self):
        """两种字段同时出现时以 Anthropic 优先。"""
        usage = {"input_tokens": 500, "prompt_tokens": 999}
        assert _extract_tokens(usage) == (500, 0, 0, 0)


class TestExtractTokensEdge:
    def test_empty(self):
        assert _extract_tokens(None) == (0, 0, 0, 0)
        assert _extract_tokens({}) == (0, 0, 0, 0)

    def test_non_int_values(self):
        """字段为 None/字符串也安全。"""
        usage = {"prompt_tokens": None, "completion_tokens": "42"}
        assert _extract_tokens(usage) == (0, 42, 0, 0)


class TestPricingCustom:
    def test_unknown_model_returns_none(self):
        """无自定义价格 + 非内置模型 → None（不崩溃）。"""
        assert get_price_for_model("deepseek-chat") is None
        assert calculate_cost("deepseek-chat", 1000, 100, 0, 0) is None

    def test_custom_price_hit(self, monkeypatch):
        """自定义价格表命中 → 正确计算成本。"""
        custom = {"gpt-4o": {"input": 2.5, "output": 10.0}}
        monkeypatch.setattr(settings, "custom_price_per_million", custom)
        # 1M input tokens * $2.5 + 500K output * $10 = 2.5 + 5.0 = 7.5
        assert get_price_for_model("gpt-4o") is not None
        assert calculate_cost("gpt-4o", 1_000_000, 500_000, 0, 0) == 7.5

    def test_builtin_claude_cost(self):
        """内置 Claude 表不受自定义影响。"""
        # claude-haiku-4-5: input $1 / 1M, output $5 / 1M
        assert calculate_cost("claude-haiku-4-5-20251001", 1_000_000, 100_000, 0, 0) == 1.5

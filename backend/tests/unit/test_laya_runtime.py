"""
Laya 决策引擎集成测试（Phase: laya integration）。

覆盖：
1. 默认关闭（laya_enabled=False）→ laya 不可用、sentiment 走 LLM 原路径（无回归）
2. 启用 + 高置信预筛命中 → 跳过 LLM，直接用 laya 结果
3. 启用 + 低置信 → 回退 LLM
4. 启用 + laya 不可用（依赖缺失/加载失败）→ 回退 LLM
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.laya_runtime import LayaRuntime, get_laya_runtime, laya_sentiment_choice
from app.ai.news_sentiment import NewsSentimentAnalyzer, SentimentResult


@pytest.fixture
def analyzer():
    """构造 analyzer，注入 mock AI / Redis。"""
    ai = MagicMock()
    ai.complete_json_async = AsyncMock(return_value={
        "sentiment": "bullish",
        "score": 0.6,
        "confidence": 0.9,
        "key_factors": ["Fed cut"],
    })
    redis = MagicMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    return NewsSentimentAnalyzer(ai_client=ai, db_session=MagicMock(), redis_client=redis)


class TestLayaRuntimeDefault:
    def test_default_disabled(self):
        """默认 laya_enabled=False → available=False，sentiment_choice 返回 None。"""
        with patch("app.ai.laya_runtime.settings") as mock_settings:
            mock_settings.laya_enabled = False
            rt = get_laya_runtime()
            assert rt.available is False
            assert laya_sentiment_choice("headline") is None


class TestSentimentPrefilter:
    async def test_high_confidence_skips_llm(self, analyzer):
        """启用 + 高置信命中 → 跳过 LLM，返回 laya 结果。"""
        with (
            patch("app.ai.laya_runtime.settings") as mock_rt,
            patch("app.ai.news_sentiment.settings") as mock_news,
            patch("app.ai.laya_runtime.laya_sentiment_choice") as mock_choice,
        ):
            mock_rt.laya_enabled = True
            mock_news.laya_enabled = True
            mock_news.laya_confidence_threshold = 0.85
            mock_choice.return_value = {
                "label": "bearish",
                "confidence": 0.95,
                "probabilities": {"bullish": 0.02, "bearish": 0.95, "neutral": 0.03},
            }
            result = await analyzer.analyze(
                [{"title": "Gold plunges on strong dollar", "source": "reuters", "published": "2026-09-21T00:00:00Z"}],
                symbol="GOLD",
            )
            # LLM 未被调用
            analyzer.ai.complete_json_async.assert_not_awaited()
            assert isinstance(result, SentimentResult)
            assert result.label == "bearish"
            assert result.confidence == 0.95
            assert result.score == -0.5  # label 近似映射
            # Redis 已缓存
            analyzer.redis.set.assert_awaited_once()

    async def test_low_confidence_falls_back_to_llm(self, analyzer):
        """启用 + 低置信 → 回退 LLM（完整分析）。"""
        with (
            patch("app.ai.laya_runtime.settings") as mock_rt,
            patch("app.ai.news_sentiment.settings") as mock_news,
            patch("app.ai.laya_runtime.laya_sentiment_choice") as mock_choice,
        ):
            mock_rt.laya_enabled = True
            mock_news.laya_enabled = True
            mock_news.laya_confidence_threshold = 0.85
            mock_choice.return_value = {
                "label": "bullish",
                "confidence": 0.5,  # 低于阈值
                "probabilities": {"bullish": 0.5, "bearish": 0.3, "neutral": 0.2},
            }
            result = await analyzer.analyze(
                [{"title": "Mixed signals in gold", "source": "bloomberg", "published": "2026-09-21T00:00:00Z"}],
                symbol="GOLD",
            )
            # LLM 被调用（回退）
            analyzer.ai.complete_json_async.assert_awaited_once()
            assert result.label == "bullish"  # 来自 mock LLM
            assert result.score == 0.6

    async def test_laya_unavailable_falls_back_to_llm(self, analyzer):
        """启用但 laya 不可用（依赖缺失）→ 回退 LLM。"""
        with (
            patch("app.ai.laya_runtime.settings") as mock_rt,
            patch("app.ai.news_sentiment.settings") as mock_news,
            patch("app.ai.laya_runtime.laya_sentiment_choice", return_value=None) as mock_choice,
        ):
            mock_rt.laya_enabled = True
            mock_news.laya_enabled = True
            mock_news.laya_confidence_threshold = 0.85
            result = await analyzer.analyze(
                [{"title": "Gold steady", "source": "cnbc", "published": "2026-09-21T00:00:00Z"}],
                symbol="GOLD",
            )
            analyzer.ai.complete_json_async.assert_awaited_once()
            assert result.label == "bullish"
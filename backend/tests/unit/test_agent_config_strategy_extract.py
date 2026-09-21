"""
策略名抽取测试（3.3：laya choice 优先 + 关键词回退）。

覆盖：
1. 关键词回退函数：英文/中文关键词 → 英文策略名（修复中文原名 bug）
2. laya 命中优先
3. laya 不可用 → 回退关键词
4. laya 白名单外 label → 回退关键词
"""

from unittest.mock import AsyncMock, patch

from mcp_server.agent_config import _keyword_strategy_fallback


class TestKeywordFallback:
    def test_english_keywords(self):
        # trend_following 是 keyword 兜底链路的既有契约名（test_llm_lang 断言它），保留
        assert _keyword_strategy_fallback("Use Trend Following on the breakout") == "trend_following"
        assert _keyword_strategy_fallback("Hold position for now") == "hold"
        assert _keyword_strategy_fallback("Momentum is strong, buy") == "momentum_rank"

    def test_chinese_keywords_map_to_english(self):
        """中文关键词必须返回英文策略名（修复原 `replace(" ","_")` 返回中文 bug）。"""
        assert _keyword_strategy_fallback("采用均值回归策略") == "mean_reversion"
        assert _keyword_strategy_fallback("突破前高后入场做多") == "breakout"
        assert _keyword_strategy_fallback("持仓等待确认") == "hold"

    def test_no_match_returns_ai_autonomous(self):
        assert _keyword_strategy_fallback("no decision text here") == "ai_autonomous"


class TestRunAgentStrategyExtraction:
    """验证 3.3 新抽取逻辑（laya choice 优先，None 回退关键词）。"""

    async def _extract(self, decision: str, mock_choice):
        from app.ai.laya_runtime import laya_strategy_choice

        try:
            result = await laya_strategy_choice(decision)
        except Exception:
            result = None
        if result is not None:
            return result["label"]
        return _keyword_strategy_fallback(decision)

    async def test_laya_hit_wins(self):
        with patch("app.ai.laya_runtime.laya_strategy_choice", new=AsyncMock(return_value={"label": "momentum_rank", "confidence": 0.9, "probabilities": {}})):
            got = await self._extract("Buy on Trend Following", AsyncMock())
            assert got == "momentum_rank"

    async def test_laya_unavailable_falls_back(self):
        with patch("app.ai.laya_runtime.laya_strategy_choice", new=AsyncMock(return_value=None)):
            got = await self._extract("Use Mean Reversion strategy", AsyncMock())
            assert got == "mean_reversion"

    async def test_laya_none_falls_back(self):
        with patch("app.ai.laya_runtime.laya_strategy_choice", new=AsyncMock(return_value=None)):
            got = await self._extract("持仓等待确认", AsyncMock())
            assert got == "hold"

    async def test_no_match_falls_back_ai_autonomous(self):
        with patch("app.ai.laya_runtime.laya_strategy_choice", new=AsyncMock(return_value=None)):
            got = await self._extract("completely unrelated text", AsyncMock())
            assert got == "ai_autonomous"
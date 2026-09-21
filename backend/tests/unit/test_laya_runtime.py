"""
Laya 决策引擎集成测试（Phase: laya integration）。

覆盖：
1. 默认关闭（laya_enabled=False）→ laya 不可用、sentiment 走 LLM 原路径（无回归）
2. 启用 + 高置信预筛命中 → 跳过 LLM，直接用 laya 结果
3. 启用 + 低置信 → 回退 LLM
4. 启用 + laya 不可用（依赖缺失/加载失败）→ 回退 LLM
5. laya_sentiment_choice 真实路径（label 白名单、异常降级）——C3 覆盖缺口修复
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.laya_runtime import SENTIMENT_LABELS, LayaRuntime, get_laya_runtime, laya_sentiment_choice, laya_strategy_choice
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


@pytest.fixture(autouse=True)
def _reset_laya_singleton():
    """隔离模块级单例：每个测试后重置 _runtime，避免跨用例污染。"""
    import app.ai.laya_runtime as laya_mod

    yield
    laya_mod._runtime = None


def _mock_runtime(available=True, predict_choice_result=None):
    """构造一个 available=True、predict_choice 可控的 mock runtime。"""
    rt = MagicMock(spec=LayaRuntime)
    rt.available = available
    rt.predict_choice = AsyncMock(return_value=predict_choice_result)
    return rt


class TestLayaRuntimeDefault:
    async def test_default_disabled(self):
        """默认 laya_enabled=False → available=False，sentiment_choice 返回 None。"""
        with patch("app.ai.laya_runtime.settings") as mock_settings:
            mock_settings.laya_enabled = False
            rt = get_laya_runtime()
            assert rt.available is False
            assert await laya_sentiment_choice("headline") is None

    def test_config_default_is_false(self):
        """C1 回归：真实 config 默认值必须是 False（曾误翻为 True，注释/计划/测试三方矛盾）。

        M3：断言 pydantic 字段默认值元数据，不实例化 Settings——避免触发 .env / 环境变量
        读取（开发机 .env 若设 LAYA_ENABLED=true 会让实例化后的值非 False，误伤本回归）。
        字段默认值 is False 直接反映 config.py 里的代码默认，是"曾翻 True 会复现"的最强断言。
        """
        from app.config import Settings

        assert Settings.model_fields["laya_enabled"].default is False


class TestLayaSentimentChoiceDirect:
    """直接测 laya_sentiment_choice 真实路径（不 mock 被测函数本身）。"""

    async def test_invalid_label_falls_back(self):
        """laya 返回白名单外 label → 回落 LLM（返回 None）。"""
        with (
            patch("app.ai.laya_runtime.settings") as mock_settings,
            patch("app.ai.laya_runtime.get_laya_runtime") as mock_rt_factory,
        ):
            mock_settings.laya_enabled = True
            rt = _mock_runtime(predict_choice_result={
                "label": "positive",  # 白名单外
                "confidence": 0.95,
                "probabilities": {"bullish": 0.1, "bearish": 0.1, "neutral": 0.1},
            })
            mock_rt_factory.return_value = rt
            result = await laya_sentiment_choice("headline")
            assert result is None  # 回退 LLM

    async def test_valid_label_passthrough(self):
        """laya 返回白名单内 label → 正常返回。"""
        with (
            patch("app.ai.laya_runtime.settings") as mock_settings,
            patch("app.ai.laya_runtime.get_laya_runtime") as mock_rt_factory,
        ):
            mock_settings.laya_enabled = True
            rt = _mock_runtime(predict_choice_result={
                "label": "bearish",
                "confidence": 0.9,
                "probabilities": {"bullish": 0.05, "bearish": 0.9, "neutral": 0.05},
            })
            mock_rt_factory.return_value = rt
            result = await laya_sentiment_choice("headline")
            assert result is not None
            assert result["label"] == "bearish"
            assert result["confidence"] == 0.9

    async def test_predict_choice_error_falls_back(self):
        """predict_choice 返回 None（如返回结构畸形/调用失败）→ 回落 LLM。"""
        with (
            patch("app.ai.laya_runtime.settings") as mock_settings,
            patch("app.ai.laya_runtime.get_laya_runtime") as mock_rt_factory,
        ):
            mock_settings.laya_enabled = True
            rt = _mock_runtime(predict_choice_result=None)
            mock_rt_factory.return_value = rt
            result = await laya_sentiment_choice("headline")
            assert result is None

    async def test_runtime_unavailable_falls_back(self):
        """runtime 不可用（available=False）→ 回落 LLM。"""
        with (
            patch("app.ai.laya_runtime.settings") as mock_settings,
            patch("app.ai.laya_runtime.get_laya_runtime") as mock_rt_factory,
        ):
            mock_settings.laya_enabled = True
            rt = _mock_runtime(available=False)
            mock_rt_factory.return_value = rt
            result = await laya_sentiment_choice("headline")
            assert result is None
            rt.predict_choice.assert_not_awaited()  # 不可用时不调推理


class TestPredictChoiceParsing:
    """用 laya 0.3.4 真实返回结构（probe 实测）验证 predict_choice 解析。

    真实返回（本机实测）：
      answers.sentiment = {
        "type": "choice",
        "choice": "bearish",
        "probabilities": {"bullish": 0.0927, "bearish": 0.8749, "neutral": 0.0323},
        "confidence": 0.5919,   # 熵置信度（laya 原生），不是最大概率！
        "action": {"act_probability": 1.0},
      }
    """

    async def test_confidence_is_max_prob_not_entropy(self):
        """confidence 应为最大类概率 0.8749，而非 laya 原生熵置信度 0.5919。"""
        rt = LayaRuntime()
        # 只 mock predict 依赖（predict_choice 内部调 self.predict，不检查 available）
        rt.predict = AsyncMock(return_value={
            "model": "laya-rl-agent",
            "answers": {
                "sentiment": {
                    "type": "choice",
                    "choice": "bearish",
                    "probabilities": {"bullish": 0.0927, "bearish": 0.8749, "neutral": 0.0323},
                    "confidence": 0.5919,
                    "action": {"act_probability": 1.0},
                }
            },
            "usage": {"input_tokens": 84, "output_tokens": 0},
        })
        result = await rt.predict_choice({"symbol": "GOLD"}, "sentiment", {"type": "choice", "criteria": {}})
        assert result is not None
        assert result["label"] == "bearish"
        assert result["confidence"] == 0.8749  # 最大类概率（阈值判断语义）
        assert result["entropy_confidence"] == 0.5919  # laya 原生熵置信度保留
        assert result["probabilities"] == {"bullish": 0.0927, "bearish": 0.8749, "neutral": 0.0323}

    async def test_empty_probabilities_falls_back(self):
        """probabilities 为空 → 返回 None（回落 LLM），避免 max() 崩溃。"""
        rt = LayaRuntime()
        rt.predict = AsyncMock(return_value={
            "answers": {
                "sentiment": {
                    "type": "choice",
                    "choice": "bearish",
                    "probabilities": {},
                    "confidence": 0.5,
                }
            }
        })
        result = await rt.predict_choice({"symbol": "GOLD"}, "sentiment", {"type": "choice"})
        assert result is None

    async def test_missing_answers_key_falls_back(self):
        """answers 里缺 question_key → 返回 None（回落 LLM）。"""
        rt = LayaRuntime()
        rt.predict = AsyncMock(return_value={"answers": {"other": {}}})
        result = await rt.predict_choice({"symbol": "GOLD"}, "sentiment", {"type": "choice"})
        assert result is None

    async def test_choice_not_argmax_falls_back(self):
        """M2：choice ≠ argmax（概率错位）→ 返回 None 降级，不把错位 confidence 传给阈值。

        防御 laya 异常输出：choice 声明的类不是最大概率类时，confidence（max-class prob）
        与 label 错位会静默失真阈值判断。此处模拟该畸形返回，应回落而非放行。
        """
        rt = LayaRuntime()
        rt.predict = AsyncMock(return_value={
            "answers": {
                "sentiment": {
                    "type": "choice",
                    "choice": "neutral",  # 声明 neutral，但最大概率是 bullish
                    "probabilities": {"bullish": 0.9, "bearish": 0.05, "neutral": 0.05},
                    "confidence": 0.5,
                }
            }
        })
        result = await rt.predict_choice({"symbol": "GOLD"}, "sentiment", {"type": "choice"})
        assert result is None

    async def test_choice_argmax_normal_returns(self):
        """M2 对照：choice = argmax（正常路径）→ 正常返回，不被防御误伤。"""
        rt = LayaRuntime()
        rt.predict = AsyncMock(return_value={
            "answers": {
                "sentiment": {
                    "type": "choice",
                    "choice": "bullish",
                    "probabilities": {"bullish": 0.9, "bearish": 0.05, "neutral": 0.05},
                    "confidence": 0.5,
                }
            }
        })
        result = await rt.predict_choice({"symbol": "GOLD"}, "sentiment", {"type": "choice"})
        assert result is not None
        assert result["label"] == "bullish"
        assert result["confidence"] == 0.9


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

    async def test_invalid_label_falls_back_to_llm(self, analyzer):
        """启用但 laya 返回白名单外 label（I2 防御）→ 回退 LLM。"""
        with (
            patch("app.ai.laya_runtime.settings") as mock_rt,
            patch("app.ai.news_sentiment.settings") as mock_news,
            patch("app.ai.laya_runtime.laya_sentiment_choice") as mock_choice,
        ):
            mock_rt.laya_enabled = True
            mock_news.laya_enabled = True
            mock_news.laya_confidence_threshold = 0.85
            # 模拟 laya_sentiment_choice 因白名单过滤返回 None → 回退 LLM
            mock_choice.return_value = None
            result = await analyzer.analyze(
                [{"title": "Gold steady", "source": "cnbc", "published": "2026-09-21T00:00:00Z"}],
                symbol="GOLD",
            )
            # 非法 label → 未进预筛分支 → LLM 被调用
            analyzer.ai.complete_json_async.assert_awaited_once()
            assert result.label == "bullish"

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


class TestStrategyChoiceThreshold:
    """I3：laya_strategy_choice 的置信阈值分支（低置信 → None → keyword 兜底）。"""

    async def _choice(self, confidence: float, threshold: float | None = None):
        with (
            patch("app.ai.laya_runtime.settings") as mock_settings,
            patch("app.ai.laya_runtime.get_laya_runtime") as mock_rt_factory,
        ):
            mock_settings.laya_enabled = True
            mock_settings.laya_strategy_confidence_threshold = 0.6
            rt = _mock_runtime(predict_choice_result={
                "label": "momentum_rank",
                "confidence": confidence,
                "probabilities": {"momentum_rank": confidence, "mean_reversion": 0.1},
            })
            mock_rt_factory.return_value = rt
            return await laya_strategy_choice("momentum looks strong", threshold)

    async def test_below_threshold_returns_none(self):
        """置信 0.59 < 阈值 0.6 → None（回退 keyword 兜底）。"""
        result = await self._choice(0.59)
        assert result is None

    async def test_at_threshold_returns_label(self):
        """置信恰等阈值 0.6 → 通过（>= 语义）。"""
        result = await self._choice(0.6)
        assert result is not None
        assert result["label"] == "momentum_rank"

    async def test_default_threshold_from_settings(self):
        """显式 threshold=None 时使用 settings.laya_strategy_confidence_threshold。"""
        result = await self._choice(0.8, None)
        assert result is not None

    async def test_explicit_threshold_overrides_default(self):
        """显式 threshold 覆盖 settings 默认。"""
        # 显式 0.95 → 0.8 低于它 → None；settings 默认 0.6 本会通过
        result = await self._choice(0.8, 0.95)
        assert result is None


# ---------------------------------------------------------------------------
# 真实 laya API 集成测试（Phase 4b）
# ---------------------------------------------------------------------------
# 用真实 laya 模型验证 laya_sentiment_choice 的返回结构（choice/confidence/概率）。
# 需要：1) laya 已安装 2) 模型权重已缓存（~1.7GB，HF 镜像预下载）。
# 未安装时该测试类整体 skip（importorskip 放在 setup_class 内，不阻塞同文件其他
# 单元测试收集）——CI 不装 laya 所以默认跳过，本机验证用 /tmp/laya-api-venv。
# ---------------------------------------------------------------------------
from app.ai.laya_runtime import SENTIMENT_LABELS  # noqa: E402


class TestRealLayaAPI:
    """真实模型推理（跳过条件：无 laya 包或无权重缓存）。"""

    @classmethod
    def setup_class(cls):
        import pytest  # noqa: F811

        laya = pytest.importorskip(
            "laya", reason="真实 laya API 集成测试需要 laya 包（未安装则跳过）"
        )
        import os

        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("USE_TF", "0")
        # 权重必须已缓存（避免测试内下载 1.7GB）
        from huggingface_hub import snapshot_download

        cls._cache_dir = snapshot_download(
            "convaiinnovations/laya", local_files_only=True
        )
        cls.rt = LayaRuntime()
        cls.rt._agent = laya.load(cls._cache_dir, device="cpu")

    async def test_laya_sentiment_choice_real(self):
        """真实 laya 返回 → 结构正确、confidence 为最大概率、label 在白名单内。"""
        import app.ai.laya_runtime as laya_mod

        with patch.object(laya_mod, "get_laya_runtime", return_value=self.rt):
            result = await laya_sentiment_choice(
                "1. Gold plunges on strong dollar\n2. Fed signals rate hike\n3. Safe-haven demand rises",
                symbol="GOLD",
            )
        assert result is not None
        assert result["label"] in SENTIMENT_LABELS
        assert 0.0 <= result["confidence"] <= 1.0
        # confidence 是最大类概率，应接近 probabilities 中最高值
        assert result["confidence"] == max(result["probabilities"].values())
        assert set(result["probabilities"].keys()) == SENTIMENT_LABELS

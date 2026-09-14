"""ML 障碍护栏（app/ml/barrier_validation.py）单元测试。

回归背景：品种页填 GOLD ml_tp_pips=1500 被 400 拒绝，报错只说
"Keep the barrier within roughly [0.15, 6.0]× mean bar range"，
运营者无法自行算出该填多少；同时 ML 页的训练入口完全没有护栏，
UI 修好仍可从 API 写入结构性坏配置。

本测试锁定：
1. 判定尺度是"屏障 ÷ 单根K线波幅"，不是绝对价格、也不是 tp:sl 比值；
2. 报错必须包含可直接填写的 pips 建议区间；
3. 有行情/无行情两条路径；
4. 与 trainer 推荐区间同源。
"""

import pytest

from app.ml.barrier_validation import (
    BARRIER_RATIO_RECOMMENDED,
    validate_ml_barriers,
)

# GOLD 实测值：M15 近 500 根 mean(high-low) ≈ 8.8784，pip_value = 1.0
GOLD_MEAN_RANGE = 8.8784
GOLD_PIP_VALUE = 1.0


class TestRejectBand:
    """[0.15, 6.0]× 之外必须拒绝 —— 结构性地产不出三类标签。"""

    def test_user_scenario_1500_is_rejected(self):
        """复现用户场景：1500 × 1.0 / 8.8784 ≈ 169× → 拒绝。"""
        ok, msg = validate_ml_barriers(1500.0, 500.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)
        assert ok is False
        assert "169" in msg
        assert "8.8784" in msg

    def test_reject_message_gives_fillable_pips_range(self):
        """报错必须给出可直接填写的 pips 区间，而不只是倍数带。"""
        ok, msg = validate_ml_barriers(1500.0, 10.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)
        assert ok is False
        # 拒绝带换算：0.15×8.8784≈1.33 到 6×8.8784≈53.3
        assert "1.33176" in msg
        assert "53.2704" in msg
        # 推荐区间换算：0.5×8.8784≈4.44 到 1.5×8.8784≈13.32
        assert "4.4392" in msg
        assert "13.3176" in msg

    def test_reject_message_points_to_live_params(self):
        """必须提示"该参数只影响训练标签，实盘请改 ATR 倍数"。"""
        ok, msg = validate_ml_barriers(1500.0, 10.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)
        assert ok is False
        assert "tp_atr_mult" in msg
        assert "sl_atr_mult" in msg
        assert "does NOT set live SL/TP" in msg

    def test_too_narrow_is_rejected(self):
        """过窄同样拒绝：0.1 × 1.0 = 0.1 价格单位，ratio ≈ 0.011× < 0.15。"""
        ok, msg = validate_ml_barriers(0.1, 10.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)
        assert ok is False
        assert "ml_tp_pips" in msg

    def test_sl_barrier_checked_independently(self):
        """ml_sl_pips 目前对标签惰性，但护栏仍独立校验它。"""
        ok, msg = validate_ml_barriers(10.0, 1500.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)
        assert ok is False
        assert "ml_sl_pips" in msg


class TestAcceptBand:
    """合法区间必须放行 —— 避免护栏把正常配置也挡住。"""

    def test_factory_default_gold_passes(self):
        """GOLD 出厂值 10/10 → ratio ≈ 1.13×，落在推荐区间内。"""
        ok, msg = validate_ml_barriers(10.0, 10.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)
        assert ok is True
        assert msg is None

    def test_mt5_point_equivalent_passes(self):
        """用户本意的 $15（MT5 口径 1500 点）→ 填 15 应通过。"""
        ok, msg = validate_ml_barriers(15.0, 5.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)
        assert ok is True
        assert msg is None

    def test_warn_band_still_allowed(self):
        """[0.3, 3.0] 之外的告警带：可疑但允许（ratio ≈ 2.8×）。"""
        ok, msg = validate_ml_barriers(25.0, 10.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)
        assert ok is True
        assert msg is None

    def test_boundary_values_are_inclusive(self):
        """拒绝带边界取等号应放行（判定是 < 与 >）。"""
        lo = 0.15 * GOLD_MEAN_RANGE / GOLD_PIP_VALUE
        hi = 6.0 * GOLD_MEAN_RANGE / GOLD_PIP_VALUE
        assert validate_ml_barriers(lo, 10.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)[0] is True
        assert validate_ml_barriers(hi, 10.0, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)[0] is True


class TestNoDataFallback:
    """行情尚未回填时退化为宽松量级校验，不阻塞品种配置。"""

    def test_none_mean_range_allows_normal_values(self):
        ok, msg = validate_ml_barriers(10.0, 10.0, GOLD_PIP_VALUE, None)
        assert ok is True
        assert msg is None

    def test_zero_mean_range_allows_normal_values(self):
        ok, _ = validate_ml_barriers(10.0, 10.0, GOLD_PIP_VALUE, 0.0)
        assert ok is True

    def test_implausible_zero_is_rejected(self):
        ok, msg = validate_ml_barriers(0.0, 10.0, GOLD_PIP_VALUE, None)
        assert ok is False
        assert "implausible" in msg

    def test_implausible_huge_is_rejected(self):
        """胖手指量级（> 1e6）即使无行情数据也要拦住。"""
        ok, msg = validate_ml_barriers(2_000_000.0, 10.0, GOLD_PIP_VALUE, None)
        assert ok is False
        assert "implausible" in msg


class TestPipValueScaling:
    """pip_value 参与换算 —— 不同品种的同一 pips 数值含义不同。"""

    def test_high_pip_value_scales_delta(self):
        """pip_value=100（USDJPY 出厂值）时，10 pips 变成 1000 价格单位。"""
        ok, msg = validate_ml_barriers(10.0, 10.0, 100.0, 8.8784)
        assert ok is False
        assert "1000" in msg

    def test_small_pip_value_allows_larger_pips(self):
        """pip_value=0.0001 时，同样 10 pips 只有 0.001 价格单位 → 过窄被拒。"""
        ok, _ = validate_ml_barriers(10.0, 10.0, 0.0001, 8.8784)
        assert ok is False


class TestRecommendedBandSingleSource:
    """推荐区间与 trainer 诊断同源，避免两处阈值互相矛盾。"""

    def test_recommended_band_values(self):
        assert BARRIER_RATIO_RECOMMENDED == (0.5, 1.5)

    def test_trainer_uses_shared_band(self):
        """trainer 的 _barrier_diagnosis 必须从共享模块取区间。"""
        import inspect

        from app.ml import trainer

        source = inspect.getsource(trainer)
        assert "BARRIER_RATIO_RECOMMENDED" in source


class TestI18nCoverage:
    """护栏报错必须有中文译文（此前中文用户只能看到英文）。"""

    @pytest.mark.parametrize("tp,sl", [(1500.0, 500.0), (0.1, 10.0)])
    def test_reject_message_is_translated(self, tp, sl):
        from app.i18n import translate_detail

        ok, msg = validate_ml_barriers(tp, sl, GOLD_PIP_VALUE, GOLD_MEAN_RANGE)
        assert ok is False
        translated = translate_detail(msg, "zh")
        assert translated != msg, "护栏报错缺少中文译文"
        assert "训练标签" in translated

    def test_implausible_message_is_translated(self):
        from app.i18n import translate_detail

        ok, msg = validate_ml_barriers(0.0, 10.0, GOLD_PIP_VALUE, None)
        assert ok is False
        translated = translate_detail(msg, "zh")
        assert translated != msg
        assert "不合理" in translated

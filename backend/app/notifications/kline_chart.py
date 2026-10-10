"""
K 线图生成 — 用 mplfinance 渲染 OHLCV 蜡烛图 + 多条均线，输出 PNG 字节。

用于飞书定时 K 线图卡片：每 15 分钟对启用且有行情的品种渲染 M15 / H1 两张图，
图上叠加 MA5 / MA10 / MA20 三条均线 + MA55。

- 无头渲染：模块内强制 ``MPLBACKEND=Agg``（在 import mplfinance 前设置），
  保证在无显示环境下（Docker / CI / VPS）可正常工作。
- 均线计算独立成 ``moving_average()``，便于单元测试直接校验数值。
- 失败返回 None，绝不抛出（对齐通知模块"失败不抛出"约定）。
"""

import io

# mplfinance 底层 matplotlib：必须在 matplotlib 导入前设置无头后端。
import os
from datetime import UTC, datetime

import pandas as pd
from loguru import logger

os.environ.setdefault("MPLBACKEND", "Agg")

# 默认均线周期（用户确认：MA5/MA10/MA20 + 固定 MA55）
DEFAULT_MA_PERIODS = (5, 10, 20, 55)

# 画布尺寸（飞书卡片推荐宽度约 800px）
CHART_FIGSIZE = (12, 6)
CHART_DPI = 100

# 图内文字统一用英文（生产 Docker slim 无中文字体，中文标题会变方框）。
# 中文展示只出现在飞书卡片文本（由飞书渲染，不依赖本地字体）。
# 字体固定 DejaVu Sans（matplotlib 内置），零外部依赖。


def moving_average(series: pd.Series, period: int) -> pd.Series:
    """计算简单移动平均线（MA）。与 mplfinance 的 mav 口径一致。

    返回与输入同索引的 Series，前 ``period-1`` 个值为 NaN（不足窗口）。
    """
    return series.rolling(window=period, min_periods=period).mean()


def build_kline_png(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    ma_periods: tuple[int, ...] = DEFAULT_MA_PERIODS,
    display_name: str | None = None,
) -> bytes | None:
    """渲染 K 线图并返回 PNG 字节。

    参数:
        symbol: 品种标识（用于标题/注脚，如 "GOLD"）
        timeframe: K 线周期（如 "M15" / "H1"，用于标题）
        df: OHLCV DataFrame（time 索引 + open/high/low/close 列），
            与 ``MarketDataService.get_ohlcv`` 返回格式一致
        ma_periods: 均线周期元组（默认 MA5/10/20/55）
        display_name: 品种展示名（SYMBOL_PROFILES.display_name），用于中文标题

    返回:
        PNG 字节；数据不足/异常返回 None（不抛出）。
    """
    if df is None or df.empty:
        logger.warning(f"Kline chart skipped [{symbol} {timeframe}]: empty data")
        return None
    # 生产数据（MarketDataService.get_ohlcv）用 tick_volume 列，而 mplfinance
    # 的 volume=True 硬性要求列名恰为 "volume"。统一归一化，兼容两种形态。
    if "tick_volume" in df.columns:
        df = df.rename(columns={"tick_volume": "volume"})
    required = {"open", "high", "low", "close"}
    if not required.issubset(df.columns):
        logger.warning(f"Kline chart skipped [{symbol} {timeframe}]: missing OHLC columns {required - set(df.columns)}")
        return None
    # 均线最大周期需要足够的样本；不足则仍画（均线尾部可能为空），
    # 但至少要有 2 根 K 线才能画蜡烛图。
    if len(df) < 2:
        logger.warning(f"Kline chart skipped [{symbol} {timeframe}]: only {len(df)} bars")
        return None

    try:
        import matplotlib
        import mplfinance as mpf

        matplotlib.use("Agg")  # 双重保险：显式指定无头后端

        # 用 make_mpf_style 固定字体（mplfinance 应用 base style 会重置
        # rcParams['font.sans-serif']，直接设 rcParams 不生效）。统一用
        # DejaVu Sans：图内全英文，无中文字体依赖。
        style = mpf.make_mpf_style(
            base_mpf_style="charles",
            rc={
                "font.family": "sans-serif",
                "font.sans-serif": ["DejaVu Sans"],
                "axes.unicode_minus": False,
            },
        )

        display = display_name or symbol
        title = f"{display} {timeframe} K-line Chart"
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        fig, axes = mpf.plot(
            df,
            type="candle",
            mav=tuple(ma_periods),
            volume=True,
            style=style,
            figsize=CHART_FIGSIZE,
            title=title,
            returnfig=True,
            tight_layout=True,
        )
        # 在图上追加时间注脚
        try:
            axes[0].text(
                0.01,
                0.01,
                f"{symbol} {timeframe} · {now_str}",
                transform=axes[0].transAxes,
                fontsize=8,
                color="gray",
            )
        except Exception:
            pass  # 注脚失败不影响主图

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=CHART_DPI, bbox_inches="tight")
        import matplotlib.pyplot as plt

        plt.close(fig)
        png_bytes = buf.getvalue()
        if not png_bytes:
            logger.warning(f"Kline chart render produced empty PNG [{symbol} {timeframe}]")
            return None
        logger.info(f"Kline chart rendered [{symbol} {timeframe}]: {len(png_bytes)} bytes, {len(df)} bars")
        return png_bytes
    except Exception as e:
        logger.error(f"Kline chart render failed [{symbol} {timeframe}]: {e}")
        return None

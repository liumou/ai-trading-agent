"""
Named constants extracted from across the codebase.
Centralizes magic numbers for readability, testability, and maintainability.
"""

# ─── MT5 / Order ───────────────────────────────────────────────────────────────

MT5_MAGIC_NUMBER = 234000
# 手动交易独立 magic：与策略/AI 单区分，事后对账（trades 同步、归因）必需。
MANUAL_MAGIC_NUMBER = 234100

# 手动通道 SL/TP 防漂移预算（评审 C-4：以 entry 为固定基数，禁止以当前 SL
# 为基数逐轮 ×2 的几何漂移 —— 5 轮即 32 倍距离）。
MANUAL_SL_MAX_ENTRY_DIST_MULT = 5.0  # SL 距 entry 上限 = 5 × 初始风险距离
MANUAL_SL_WIDEN_DAILY_LIMIT = 3  # 每日拉宽次数预算（Redis 计数）

# ─── Engine ────────────────────────────────────────────────────────────────────

DEFAULT_OHLCV_BARS = 200
H1_BARS = 50
DEFAULT_ATR_FALLBACK = 10.0
WARMUP_SECONDS = 7200  # 2 hours — reduce lot during initial warmup
WARMUP_MIN_LOT_PCT = 0.25  # minimum 25% lot during warmup

# Multi-timeframe EMA trend thresholds
MTF_EMA_ABOVE = 1.0005  # price must be > EMA * this to confirm uptrend
MTF_EMA_BELOW = 0.9995  # price must be < EMA * this to confirm downtrend

# Paper trading
PAPER_TICKET_START = 900000
PAPER_INITIAL_BALANCE = 10000.0

# ─── Risk Management ──────────────────────────────────────────────────────────

MIN_LOT = 0.01

# Volatility-based lot adjustment thresholds
HIGH_VOL_THRESHOLD = 0.5
LOW_VOL_THRESHOLD = 0.2
HIGH_VOL_LOT_FACTOR = 0.7  # reduce lot in high volatility
LOW_VOL_LOT_FACTOR = 1.2  # increase lot in low volatility

# Slippage & commission defaults
DEFAULT_SLIPPAGE_PIPS = 2.0
DEFAULT_COMMISSION_PCT = 0.002

# Kelly Criterion
KELLY_FRACTION = 0.25  # fractional Kelly for safety
KELLY_MIN_RISK = 0.005  # minimum 0.5% risk
KELLY_MAX_RISK_MULT = 2  # cap at 2x max_risk_per_trade
MIN_KELLY_TRADES = 20  # minimum closed trades before using Kelly

# Minimum win rate to apply Kelly sizing
KELLY_MIN_WIN_RATE = 0.35

# Consecutive loss streak adjustments
STREAK_3_FACTOR = 0.5  # halve lot after 3 consecutive losses
STREAK_2_FACTOR = 0.75  # 75% lot after 2 consecutive losses

# AI confidence adjustments
AI_WORST_HOUR_THRESHOLD_BOOST = 0.15
AI_MAX_THRESHOLD = 0.95

# ─── Trailing Stop & Position Management ──────────────────────────────────────

BREAKEVEN_ATR_MULT = 0.5  # move to breakeven after profit > 0.5x ATR
DEFAULT_ATR_PCT_FALLBACK = 0.3  # fallback ATR% when not recorded at entry

# Default trailing stop settings
DEFAULT_TRAILING_START_ATR = 1.0  # activate trailing after profit > 1x ATR
DEFAULT_TRAILING_STEP_ATR = 0.5  # trail SL at 0.5x ATR behind price

# Adaptive trailing: volatility adjustments
HIGH_VOL_TRAIL_FACTOR = 1.3  # widen trail in high vol
LOW_VOL_TRAIL_FACTOR = 0.7  # tighten trail in low vol

# Profit-lock ratchet
PROFIT_LOCK_ATR_MULT = 2.0  # tighten trail after profit > 2x ATR
TIGHT_TRAIL_STEP_ATR = 0.3  # tighter step once profit-locked

# Scaling in/out
PARTIAL_TP_CLOSE_PCT = 0.5  # close this fraction at partial TP
SCALE_IN_ATR_MULT = 0.5  # add-on entry after price moves this * ATR
SCALE_IN_LOT_FACTOR = 0.5  # add-on lot = original * this
MAX_SCALE_IN_COUNT = 1  # max number of add-on entries per position

# ─── Multi-Timeframe ─────────────────────────────────────────────────────────

H4_BARS = 50
D1_BARS = 30
MTF_ADX_TRENDING_THRESHOLD = 20  # only apply MTF filter when ADX > this

# ─── Strategy Ensemble ────────────────────────────────────────────────────────

ENSEMBLE_BUY_THRESHOLD = 0.6  # weighted sum > this → BUY
ENSEMBLE_SELL_THRESHOLD = -0.6  # weighted sum < this → SELL

# ─── ML Strategy ──────────────────────────────────────────────────────────────

ADX_RANGING_THRESHOLD = 20
ATR_PERCENTILE_LOW = 0.4
RANGING_CONFIDENCE_FACTOR = 0.7

# Dynamic confidence threshold boosts
ML_HIGH_VOL_THRESHOLD_BOOST = 0.10
ML_LOW_VOL_THRESHOLD_BOOST = 0.15

# ─── Circuit Breaker ─────────────────────────────────────────────────────────

DEFAULT_PORTFOLIO_MAX_LOSS = 0.10  # 10% portfolio-level daily loss limit
MIN_TTL_SECONDS = 60  # minimum Redis TTL for daily PnL keys

# Kelly sizing: recent trades window
KELLY_RECENT_TRADES = 50
# Streak detection: recent trades to check
STREAK_RECENT_TRADES = 5

# ─── Regime-Aware Risk ──────────────────────────────────────────────────────

REGIME_LOT_MULTIPLIERS = {
    "trending_high_vol": 0.7,
    "trending_low_vol": 1.0,
    "ranging": 0.5,
    "normal": 1.0,
}

# ─── Event Filter ───────────────────────────────────────────────────────────

EVENT_LOT_FACTOR = 0.5  # halve lot size near high-impact events
EVENT_BLOCK_HOURS = 2  # hours before event to reduce exposure

# ─── Notifications ──────────────────────────────────────────────────────────

LOSING_STREAK_ALERT_THRESHOLD = 3  # consecutive losses before Telegram alert
PREDICTION_FEEDBACK_HOURS = 4  # match predictions within N hours of trade close

# ─── Absolute Drawdown ──────────────────────────────────────────────────────

DEFAULT_MAX_DRAWDOWN_FROM_PEAK = 0.15  # 15% drawdown from peak → halt all trading

# ─── Adaptive Confidence Policy ─────────────────────────────────────────────

CONFIDENCE_DRAWDOWN_5_BOOST = 0.05  # stricter when drawdown > 5%
CONFIDENCE_DRAWDOWN_10_BOOST = 0.10  # much stricter when drawdown > 10%
CONFIDENCE_RANGING_BOOST = 0.05  # stricter in ranging (false signals common)
CONFIDENCE_TRENDING_HV_DISCOUNT = 0.05  # looser in clear high-vol trend
CONFIDENCE_LOW_WINRATE_BOOST = 0.10  # stricter when recent win rate < 40%
CONFIDENCE_LOW_WINRATE_THRESHOLD = 0.40
CONFIDENCE_RECENT_TRADES_WINDOW = 20

# ─── Backtest Formula Version ───────────────────────────────────────────────

# 回测口径（PnL = pips × lot × contract_size、读取品种 SL/TP 配置等）的版本号。
# 写进 AIOptimizationLog；版本不匹配的历史建议不得被应用到实盘，
# 防止"旧口径算出的最优参数在新口径下生效"。
BACKTEST_FORMULA_VERSION = "v2"

# ─── Chart Indicators ───────────────────────────────────────────────────────

# 手动交易页图表的指标周期（集中常量，前端图表 /ohlcv 请求按此计算）。
# 决策 8：本期固定参数，/ohlcv 接受可选 indicator_params 覆盖（向后兼容）。
INDICATOR_SMA_LENGTH = 55  # 移动均线（55 周期）
INDICATOR_EMA_FAST = 20
INDICATOR_EMA_SLOW = 50
INDICATOR_RSI_LENGTH = 14
INDICATOR_MACD_FAST = 12
INDICATOR_MACD_SLOW = 26
INDICATOR_MACD_SIGNAL = 9
INDICATOR_ICHIMOKU_TENKAN = 9
INDICATOR_ICHIMOKU_KIJUN = 26
INDICATOR_ICHIMOKU_SENKOU_B = 52
INDICATOR_ICHIMOKU_DISPLACEMENT = 26
# 图表展示的指标数值统一按固定 2 位小数舍入（RSI/MACD 是无量纲值，
# 按价格 price_decimals 舍入是语义错误）。
INDICATOR_DECIMALS = 2

# ─── Trade Review（历史订单 AI 深度复盘）───────────────────────────────────

# 四分类问责枚举（对齐 TradeAccountabilityTracker / 产品四象限）。服务端用
# pnl>0 × reasoning_correct 确定性推导，LLM 自报值仅交叉校验。
REVIEW_CLASS_SKILLED_WIN = "skilled_win"  # 判断正确 + 盈利 → 强化
REVIEW_CLASS_CORRECT_PROCESS = "correct_process"  # 判断正确 + 亏损 → 方差不调整
REVIEW_CLASS_LUCKY_WIN = "lucky_win"  # 判断错误 + 盈利 → 噪音不强化
REVIEW_CLASS_REAL_MISTAKE = "real_mistake"  # 判断错误 + 亏损 → 学习调整
REVIEW_CLASSIFICATIONS = (
    REVIEW_CLASS_SKILLED_WIN,
    REVIEW_CLASS_CORRECT_PROCESS,
    REVIEW_CLASS_LUCKY_WIN,
    REVIEW_CLASS_REAL_MISTAKE,
)

# LLM 输出的根因标签白名单（防自由文本 + 未来教训匹配需字段化）。
REVIEW_LOSS_CAUSES = (
    "逆势开仓",
    "止损过近",
    "止损过宽",
    "进场过早",
    "进场过晚",
    "追涨杀跌",
    "波动率异常",
    "重大消息冲击",
    "持仓过短",
    "持仓过长",
    "情绪化操作",
    "频率过高",
    "仓位过重",
    "未设止损",
    "数据缺失",
)
REVIEW_WIN_CAUSES = (
    "顺势交易",
    "止损保护",
    "耐心持仓",
    "分批止盈",
    "点位精准",
    "波动率配合",
    "情绪稳定",
    "纪律执行",
    "仓位合理",
)

# 复盘字段长度/上限（统一 bounded_text 截断入口）。
REVIEW_SUMMARY_LIMIT = 4000  # 自由文本 summary 总上限
REVIEW_ITEM_LIMIT = 500  # 单条 lessons/improvement_actions/根因描述上限
REVIEW_CAUSES_LIMIT = 6  # loss_causes/win_causes 单次最多条数
REVIEW_LESSONS_LIMIT = 5  # lessons 最多条数
REVIEW_ACTIONS_LIMIT = 5  # improvement_actions 最多条数
REVIEW_MIN_CONFIDENCE = 0.5  # 低于此置信前端显示低置信徽标 + 服务端 flagged
REVIEW_WINDOW_DAYS = 30  # 跨单模式统计窗口
REVIEW_OPEN_WINDOW_PAD_S = 300  # 行情窗口前后各放宽 5 分钟（对齐 OHLCV 采样）
REVIEW_MAX_OHLCV_BARS = 5000  # 行情窗口特征提取最大 K 线条数

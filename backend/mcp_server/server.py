"""
MCP Tool Server — registers all trading tools for the Claude Agent.

Uses FastMCP (mcp Python SDK) with stdio transport.
Designed to run inside the agent container/process.

Usage:
    from mcp_server.server import create_server
    server = create_server()
    server.run(transport="stdio")
"""

from mcp.server.fastmcp import FastMCP

from mcp_server.tools import (
    broker,
    history,
    indicators,
    journal,
    learning,
    market_data,
    memory,
    overfitting,
    portfolio,
    quant,
    risk,
    sentiment,
    session,
    strategy_gen,
    strategy_switch,
)

# 模块级单例 —— openai_loop 通过 get_server() 复用同一实例枚举/执行工具，
# 避免重复构建 41 个工具定义（也避免闭包函数无法 import 的问题）。
_mcp_server: FastMCP | None = None


def get_server() -> FastMCP:
    """懒加载 MCP server 单例。首次调用时创建并注册全部工具。"""
    global _mcp_server
    if _mcp_server is None:
        _mcp_server = create_server()
    return _mcp_server


def create_server() -> FastMCP:
    """Create and configure the MCP server with all trading tools."""
    mcp = FastMCP("trading-agent-tools")

    # ─── Market Data Tools (read-only) ───────────────────────────────────

    @mcp.tool()
    async def get_tick(symbol: str) -> dict:
        """Get current bid/ask tick for a trading symbol."""
        return await market_data.get_tick(symbol)

    @mcp.tool()
    async def get_ohlcv(symbol: str, timeframe: str = "M15", count: int = 100) -> dict:
        """Get OHLCV candlestick data for a symbol."""
        return await market_data.get_ohlcv(symbol, timeframe, count)

    @mcp.tool()
    async def get_spread(symbol: str) -> dict:
        """Get current spread for a symbol."""
        return await market_data.get_spread(symbol)

    # ─── Indicator Tools (read-only) ─────────────────────────────────────

    @mcp.tool()
    async def calculate_ema(symbol: str, period: int = 20, timeframe: str = "M15") -> dict:
        """Calculate Exponential Moving Average for a symbol."""
        return await indicators.calculate_ema(symbol, period, timeframe)

    @mcp.tool()
    async def calculate_rsi(symbol: str, period: int = 14, timeframe: str = "M15") -> dict:
        """Calculate Relative Strength Index (0-100) for a symbol."""
        return await indicators.calculate_rsi(symbol, period, timeframe)

    @mcp.tool()
    async def calculate_atr(symbol: str, period: int = 14, timeframe: str = "M15") -> dict:
        """Calculate Average True Range (volatility) for a symbol."""
        return await indicators.calculate_atr(symbol, period, timeframe)

    @mcp.tool()
    async def run_full_analysis(symbol: str, timeframe: str = "M15") -> dict:
        """Run comprehensive technical analysis: EMA, RSI, ATR, ADX, Bollinger, Stochastic.
        This is the primary tool for getting a complete market picture."""
        return await indicators.full_analysis(symbol, timeframe)

    # ─── Risk Tools (analytical) ─────────────────────────────────────────

    @mcp.tool()
    async def validate_trade(
        symbol: str, signal: int, current_positions: int, daily_pnl: float, balance: float
    ) -> dict:
        """Check if a trade is allowed under risk management rules."""
        return risk.validate_trade(symbol, signal, current_positions, daily_pnl, balance)

    @mcp.tool()
    async def calculate_lot_size(symbol: str, balance: float, sl_pips: float) -> dict:
        """Calculate optimal position size (lot) based on risk parameters."""
        return risk.calculate_lot(symbol, balance, sl_pips)

    @mcp.tool()
    async def calculate_sl_tp(symbol: str, entry_price: float, signal: int, atr: float) -> dict:
        """Calculate stop-loss and take-profit levels."""
        return risk.calculate_sl_tp(symbol, entry_price, signal, atr)

    # ─── Broker Tools ─────────────────────────────────────────────────

    @mcp.tool()
    async def place_order(
        symbol: str,
        order_type: str,
        lot: float,
        sl: float,
        tp: float,
        comment: str = "",
    ) -> dict:
        """Place a trade order. Passes through non-bypassable guardrails.

        Args:
            symbol: Trading symbol (e.g. "GOLDmicro", "USDJPY")
            order_type: "BUY" or "SELL"
            lot: Lot size (will be capped by rollout mode)
            sl: Stop-loss price
            tp: Take-profit price
            comment: Optional order comment
        """
        return await broker.place_order(symbol, order_type, lot, sl, tp, comment)

    @mcp.tool()
    async def modify_position(ticket: int, sl: float | None = None, tp: float | None = None) -> dict:
        """Modify stop-loss and/or take-profit of an existing position."""
        return await broker.modify_position(ticket, sl, tp)

    @mcp.tool()
    async def close_position(ticket: int) -> dict:
        """Close an open position by ticket number."""
        return await broker.close_position(ticket)

    @mcp.tool()
    async def get_positions(symbol: str | None = None) -> dict:
        """Get all open positions, optionally filtered by symbol."""
        return await broker.get_positions(symbol)

    # ─── Portfolio Tools (read-only) ─────────────────────────────────────

    @mcp.tool()
    async def get_account() -> dict:
        """Get account summary: balance, equity, margin, profit."""
        return await portfolio.get_account()

    @mcp.tool()
    async def get_exposure() -> dict:
        """Get portfolio exposure breakdown by symbol."""
        return await portfolio.get_exposure()

    @mcp.tool()
    async def check_correlation(symbol: str, signal: int, active_positions: dict) -> dict:
        """Check for correlation conflicts before opening a new position."""
        return portfolio.check_correlation(symbol, signal, active_positions)

    # ─── Sentiment Tools (read-only) ─────────────────────────────────────

    @mcp.tool()
    async def get_sentiment() -> dict:
        """Get latest AI sentiment analysis (bullish/bearish/neutral)."""
        return await sentiment.get_latest_sentiment()

    @mcp.tool()
    async def get_sentiment_history(days: int = 7) -> dict:
        """Get sentiment history for the past N days."""
        return await sentiment.get_sentiment_history(days)

    # ─── History Tools (read-only) ───────────────────────────────────────

    @mcp.tool()
    async def get_trade_history(days: int = 7, symbol: str | None = None) -> dict:
        """Get recent trade history."""
        return await history.get_trade_history(days, symbol)

    @mcp.tool()
    async def get_daily_pnl(symbol: str | None = None) -> dict:
        """Get daily P&L summary."""
        return await history.get_daily_pnl(symbol)

    @mcp.tool()
    async def get_performance(days: int = 30, symbol: str | None = None) -> dict:
        """Get performance statistics (win rate, Sharpe, drawdown)."""
        return await history.get_performance(days, symbol)

    # ─── Journal Tools (logging) ─────────────────────────────────────────

    @mcp.tool()
    async def log_decision(symbol: str, decision: str, reasoning: str, confidence: float | None = None) -> dict:
        """Log a trading decision with full reasoning. Every trade MUST be logged."""
        return await journal.log_decision(symbol, decision, reasoning, confidence)

    @mcp.tool()
    async def log_reasoning(thought_process: str) -> dict:
        """Log the agent's internal reasoning/thought process for review."""
        return await journal.log_reasoning(thought_process)

    # ─── Learning Tools (Phase E: self-reflection) ──────────────────────

    @mcp.tool()
    async def analyze_recent_trades(days: int = 7, symbol: str | None = None) -> dict:
        """Analyze recent trade outcomes to identify patterns, streaks, and strategy performance."""
        return await learning.analyze_recent_trades(days, symbol)

    @mcp.tool()
    async def detect_regime(symbol: str, timeframe: str = "M15") -> dict:
        """Detect current market regime (trending/ranging/volatile/transitional)."""
        return await learning.detect_regime(symbol, timeframe)

    @mcp.tool()
    async def get_optimization_history(limit: int = 5) -> dict:
        """Get recent AI optimization attempts and outcomes."""
        return await learning.get_optimization_history(limit)

    # ─── Session Memory Tools (Phase E) ─────────────────────────────────

    @mcp.tool()
    async def save_context(symbol: str, context: dict) -> dict:
        """Save session context for today's trading session (merged with existing)."""
        return await session.save_context(symbol, context)

    @mcp.tool()
    async def get_context(symbol: str) -> dict:
        """Retrieve today's session context for a symbol."""
        return await session.get_context(symbol)

    @mcp.tool()
    async def save_learning(learning_text: str, category: str = "general") -> dict:
        """Save a cross-session learning that persists for 7 days."""
        return await session.save_learning(learning_text, category)

    @mcp.tool()
    async def get_learnings(category: str | None = None) -> dict:
        """Retrieve cross-session learnings, optionally filtered by category."""
        return await session.get_learnings(category)

    # ─── Strategy Tools (Phase E: adaptive selection) ───────────────────

    @mcp.tool()
    async def get_strategy_profiles() -> dict:
        """Get all available strategy profiles with regime suitability."""
        return strategy_gen.get_strategy_profiles()

    @mcp.tool()
    async def recommend_strategy(regime: str, symbol: str) -> dict:
        """Recommend the best strategy for the current market regime."""
        return strategy_gen.recommend_strategy(regime, symbol)

    @mcp.tool()
    async def generate_strategy_config(
        base_strategy: str, param_overrides: dict | None = None, name: str | None = None
    ) -> dict:
        """Generate a custom strategy config from a template with validated parameters."""
        return strategy_gen.generate_strategy_config(base_strategy, param_overrides, name)

    @mcp.tool()
    async def generate_ensemble_config(weights: dict, name: str = "custom_ensemble") -> dict:
        """Generate a custom ensemble strategy with specified strategy weights."""
        return strategy_gen.generate_ensemble_config(weights, name)

    # ─── Quant Tools (quantitative analysis) ───────────────────────────

    @mcp.tool()
    async def get_var_analysis(symbol: str, timeframe: str = "M15", count: int = 200) -> dict:
        """Calculate Value-at-Risk — estimate worst-case daily loss at 95% confidence."""
        return await quant.get_var_analysis(symbol, timeframe, count)

    @mcp.tool()
    async def get_volatility_forecast(symbol: str, timeframe: str = "M15", count: int = 200) -> dict:
        """Get GARCH volatility forecast vs realized — detect if volatility is expanding or contracting."""
        return await quant.get_volatility_forecast(symbol, timeframe, count)

    @mcp.tool()
    async def get_quant_signals(symbol: str, timeframe: str = "M15", count: int = 200) -> dict:
        """Get quantitative signals: momentum (ROC), mean-reversion (z-score), volatility breakout (ATR ratio)."""
        return await quant.get_quant_signals(symbol, timeframe, count)

    # ─── Overfitting Detection Tools ──────────────────────────────────

    @mcp.tool()
    async def compute_overfitting_score(
        strategy: str,
        symbol: str,
        timeframe: str = "M15",
        source: str = "db",
        count: int = 5000,
    ) -> dict:
        """Compute composite overfitting score (0-100%) for a strategy.
        Combines walk-forward ratio, permutation test, param stability, and monte carlo ruin probability."""
        return await overfitting.compute_overfitting_score(strategy, symbol, timeframe, source, count)

    # ─── Strategy Switch Tools ───────────────────────────────────────

    @mcp.tool()
    async def apply_strategy(
        symbol: str,
        strategy_name: str,
        params: str | None = None,
        reasoning: str = "",
    ) -> dict:
        """Apply a strategy to the trading engine based on market regime analysis.
        Only works when enable_auto_strategy_switch is ON. Cooldown: 1h, max 3/day."""
        return await strategy_switch.apply_strategy(symbol, strategy_name, params, reasoning)

    @mcp.tool()
    async def get_switch_status(symbol: str) -> dict:
        """Get auto-strategy-switch status: enabled, current strategy, cooldown, daily count."""
        return await strategy_switch.get_switch_status(symbol)

    # ─── Persistent Memory Tools (Phase E reflector — 修复现存缺口) ─────────
    # reflector.py 的 TOOL_NAMES 引用了 get_memories/save_memory/validate_memory，
    # 但此前从未在 server.py 注册 —— 导致 SDK allowed_tools 与 OpenAI loop
    # 的白名单都包含不存在于 list_tools() 的名字（AC-12 漂移断言前提）。

    @mcp.tool()
    async def save_memory(
        summary: str,
        category: str = "pattern",
        symbol: str | None = None,
        evidence: dict | None = None,
    ) -> dict:
        """Save an insight to mid-term memory (30 days, promotable to permanent)."""
        return await memory.save_memory(summary, category, symbol, evidence)

    @mcp.tool()
    async def get_memories(
        symbol: str | None = None,
        category: str | None = None,
        tier: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Recall stored memories, sorted by confidence."""
        return await memory.get_memories(symbol, category, tier, limit)

    @mcp.tool()
    async def validate_memory(memory_id: int, hit: bool) -> dict:
        """Validate whether a stored memory's prediction matched reality."""
        return await memory.validate_memory(memory_id, hit)

    return mcp


if __name__ == "__main__":
    import os

    import redis.asyncio as redis_async

    from mcp_server.tools import init_mcp_tools

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = redis_async.from_url(redis_url)
    init_mcp_tools(redis_client)

    server = create_server()
    server.run(transport="stdio")

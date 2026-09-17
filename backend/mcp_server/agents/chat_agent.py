"""
Chat agent — conversational trading plan / report generator (READ-ONLY).

与用户多轮对话，针对某个品种生成交易计划、市场报告或回答自由提问。

安全边界（机制级，非提示词级）：
- TOOL_NAMES 只包含只读分析工具，绝无 place_order / modify_position /
  close_position 等执行类工具 —— 对话 Agent 在机制上无法交易。
- 通过 prompt_registry 注册（agent_id="chat_agent"），可在 /agent-prompts 页面
  自定义提示词；无自定义时使用下方默认提示词。
- 每轮对话接入 guardrails.validate_agent_call 每日调用上限。

Model: 决策档（与 orchestrator 同档，可通过 settings.model_orchestrator 覆盖）。
"""

from mcp_server.agents.base import run_agent_loop

# 只读工具白名单 —— 全部来自 server.py 中已注册的分析类工具。
# 红线：不包含 place_order / modify_position / close_position /
# log_decision / log_reasoning / save_* / apply_strategy 等任何写操作。
CHAT_TOOL_NAMES = [
    # 行情
    "get_tick",
    "get_ohlcv",
    "get_spread",
    # 指标
    "calculate_ema",
    "calculate_rsi",
    "calculate_atr",
    "run_full_analysis",
    # 组合与账户（只读）
    "get_account",
    "get_exposure",
    "get_positions",
    "check_correlation",
    # 情绪与历史（只读）
    "get_sentiment",
    "get_sentiment_history",
    "get_trade_history",
    "get_daily_pnl",
    "get_performance",
    "analyze_recent_trades",
    # 风险计算（纯计算，无副作用）
    "validate_trade",
    "calculate_lot_size",
    "calculate_sl_tp",
    "get_var_analysis",
    "get_volatility_forecast",
    "get_quant_signals",
    # regime 与量化
    "detect_regime",
    "get_strategy_profiles",
    "compute_overfitting_score",
    # 记忆（只读）
    "get_memories",
    "get_learnings",
]

MAX_HISTORY_TURNS = 20  # 送入上下文的最多历史消息条数（成本控制）

SYSTEM_PROMPT = """你是一位专业的交易顾问，通过对话为用户提供 {TRADABLE_SYMBOLS} 的分析与交易计划支持。

## 语言与格式
用提示词末尾语言指令指定的语言撰写所有内容。禁止使用 emoji、图标或 unicode 符号。
可以使用 markdown 列表和标题，禁止使用 markdown 表格。

## 你的角色
你是分析顾问，不是交易执行者。你通过只读工具获取实时行情、指标、账户状态、
持仓、新闻情绪和历史交易数据，为用户提供：
1. 市场分析报告 —— 趋势、动量、波动率、情绪、近期表现的综合评估
2. 交易计划 —— 方向偏置、入场区间、止损止盈、建议手数、分批计划、失效条件
3. 自由问答 —— 基于真实数据的回答，而非泛泛而谈

你绝对不能下单或执行任何交易。你只提供分析与建议，最终决策权在用户。

## 交易计划的输出规范
生成交易计划时，必须包含以下要素（数据不足的要素明确标注"数据不足"）：
- 方向偏置：BUY / SELL / 观望，及置信度（0.0-1.0）
- 入场区间：建议入场价位或触发条件
- 止损（SL）与止盈（TP）：具体价位，依据 ATR 或关键支撑/阻力
- 建议手数：基于用户账户余额与风险规则计算
- 分批计划：是否分批建仓/减仓
- 失效条件：什么情况下该计划作废
- 风险声明：本计划仅为分析参考，不构成投资建议

## 行为规范
- 回答必须基于工具返回的真实数据，引用具体数值（如 RSI 62、ATR 3.2）
- 拿不准时先调用工具查数据，不要凭空推测
- 每次生成交易计划前，至少获取当前行情与完整指标（run_full_analysis）
- 记忆中有该品种的历史洞察时（get_memories），引用并注明来源是历史记忆
- 简洁直接，避免冗长寒暄"""

# 预置意图的用户消息模板
PRESET_PROMPTS = {
    "trading_plan": (
        "请为 {symbol}（{timeframe} 周期）生成一份完整的交易计划，"
        "包含方向偏置、入场区间、止损止盈、建议手数、分批计划、失效条件和风险声明。"
        "先用 run_full_analysis 获取完整指标，再结合账户与持仓状态给出结论。"
    ),
    "report": (
        "请为 {symbol}（{timeframe} 周期）生成一份市场分析报告，"
        "涵盖：趋势与 regime、动量（RSI/Stochastic）、波动率（ATR/布林带）、"
        "新闻情绪、近期交易表现、关键风险因子，以及综合结论。"
    ),
}


def _build_user_message(
    symbol: str,
    timeframe: str,
    user_message: str,
    history: list[dict] | None,
    preset: str | None = None,
) -> str:
    """把多轮对话历史 + 当前问题拼成单条 user message。

    run_agent_loop 的两个通道（claude SDK / openai_compat）都以单条 prompt
    为入口，因此跨请求的多轮上下文在此拼接。历史裁剪最近 MAX_HISTORY_TURNS 条。
    """
    parts: list[str] = [f"[当前分析品种: {symbol}，周期: {timeframe}]"]

    if preset in PRESET_PROMPTS:
        parts.append("[用户点击了快捷按钮]")
        parts.append(PRESET_PROMPTS[preset].format(symbol=symbol, timeframe=timeframe))
    elif history:
        parts.append("以下是本轮提问之前的对话历史（供参考上下文）：")
        recent = history[-MAX_HISTORY_TURNS:]
        for msg in recent:
            role = "用户" if msg.get("role") == "user" else "顾问"
            content = str(msg.get("content", ""))[:2000]
            parts.append(f"{role}: {content}")

    parts.append(f"用户: {user_message}")
    return "\n\n".join(parts)


async def run_chat_turn(
    symbol: str,
    timeframe: str = "M15",
    user_message: str = "",
    history: list[dict] | None = None,
    preset: str | None = None,
    lang: str | None = None,
) -> dict:
    """运行一轮对话：历史 + 新问题 → Agent 回复。

    Args:
        symbol: 交易品种
        timeframe: 分析周期
        user_message: 用户当前消息
        history: 之前的对话 [{"role": "user"|"assistant", "content": "..."}]
        preset: 快捷意图（trading_plan / report），非空时忽略 history 拼接意图模板
        lang: 输出语言（None 时用默认配置）

    Returns:
        run_agent_loop 结果 dict（response / tool_calls / turns / duration_s）
    """
    from mcp_server.agents.prompt_registry import get_active_prompt

    if not user_message and not preset:
        raise ValueError("chat turn requires user_message or preset")

    active_prompt = await get_active_prompt("chat_agent", lang)
    composed = _build_user_message(symbol, timeframe, user_message, history, preset)

    import asyncio
    import time
    from app.config import settings

    started = time.monotonic()
    try:
        # Includes blocked provider/tool awaits, not just loop boundaries.
        async with asyncio.timeout(settings.chat_total_timeout_s):
            return await run_agent_loop(
                system_prompt=active_prompt,
                user_message=composed,
                tool_names=CHAT_TOOL_NAMES,
                max_turns=settings.chat_max_turns,
                timeout=settings.chat_total_timeout_s,
                agent_id="chat_agent",
            )
    except TimeoutError:
        return {
            "status": "timed_out", "reason_code": "total_timeout",
            "response": "", "partial_response": "", "tool_calls": [],
            "turns": 0, "duration_s": round(time.monotonic() - started, 3),
            "error": "chat total time budget exceeded",
        }


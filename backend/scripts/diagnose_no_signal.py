"""诊断"长时间没有交易信号"——三重锁自检 + 策略活性回放。

用法（在 backend/ 目录下执行）：

    .venv/bin/python scripts/diagnose_no_signal.py [SYMBOL]

只读脚本：**不写数据库、不下单、不改 Redis**。依次检查：

  1) 模式锁   —— Redis ``trading_mode`` / ``settings.trading_mode`` / ``agent_mode``
  2) 下单权限 —— ``rollout_mode`` / ``LLM_ALLOW_LIVE`` / ``LLM_PROVIDER``
                 （这三者决定 AI 路径能否调用 place_order）
  3) 链路     —— MT5 Bridge 健康、账户、实时 tick
  4) 策略活性 —— 在真实行情上跑候选策略，统计信号频率 + 看"刚收盘那一根"
  5) 历史     —— 最近 SIGNAL_DETECTED / TRADE_OPENED 事件时间、trades 表行数

最后打印 VERDICT：当前配置下"是否有人能下单"。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

# 允许直接 `python scripts/diagnose_no_signal.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger

logger.remove()  # 静默第三方库日志，只保留本脚本的 print 输出

from app.bot.engine import _get_h1_trend  # noqa: E402
from app.config import SYMBOL_PROFILES, settings  # noqa: E402
from app.db.models import BotEvent, Trade  # noqa: E402
from app.mt5.connector import MT5BridgeConnector  # noqa: E402
from app.mt5.market_data import MarketDataService  # noqa: E402
from app.mt5.symbol_resolver import to_broker_alias  # noqa: E402
from app.strategy import get_strategy  # noqa: E402

CANDIDATE_STRATEGIES = ("ema_crossover", "breakout", "mean_reversion", "rsi_filter", "ml_signal")


def _hr(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


async def check_mode() -> dict:
    """1) 模式锁：Redis 是运行时真相源，Redis 不可用时回落 settings。"""
    import redis.asyncio as redis

    _hr("1) 模式锁（trading_mode）")
    redis_mode: str | None = None
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)
        try:
            redis_mode = await r.get("trading_mode")
            rollout = await r.get("guardrails:rollout_mode")
            switch = await r.get("enable_auto_strategy_switch")
            peak = await r.get("circuit:peak_balance")
        finally:
            await r.aclose()
        print(
            f"Redis 可达；trading_mode={redis_mode!r} rollout={rollout!r} "
            f"auto_switch={switch!r} peak_balance={peak!r}"
        )
    except Exception as e:
        print(
            f"!! Redis 不可达（{e!r}）—— 调度器会回落到 settings.trading_mode="
            f"{settings.trading_mode!r}"
        )

    effective = redis_mode or settings.trading_mode
    print(f"\n生效模式: {effective!r}   (settings.trading_mode={settings.trading_mode!r})")
    if effective == "ai_autonomous":
        print("  → 策略引擎整条路径被关闭：scheduler 不会调用 engine.process_candle()")
        print("    （见 app/bot/scheduler.py:388、app/bot/engine.py:384）")
    else:
        print("  → 策略引擎正常参与（收线时评估策略信号）")
    return {"effective_mode": effective, "redis_mode": redis_mode}


async def check_execution_rights(mode_info: dict) -> dict:
    """2) 谁能下单：策略引擎 vs AI orchestrator。"""
    _hr("2) 下单权限（谁有 place_order 权限）")
    agent_mode = getattr(settings, "agent_mode", "single")
    provider = getattr(settings, "llm_provider", "claude")
    allow_live = bool(getattr(settings, "llm_allow_live", False))

    print(f"agent_mode={agent_mode!r}  llm_provider={provider!r}  llm_allow_live={allow_live}")

    strategy_can_trade = mode_info["effective_mode"] == "strategy"
    suffix = "" if strategy_can_trade else "（trading_mode != strategy）"
    print(f"\n策略引擎可下单: {'是' if strategy_can_trade else '否'}{suffix}")

    if agent_mode == "multi":
        ai_can_trade = True
        ai_note = "orchestrator 持有 place_order 权限"
    else:
        ai_can_trade = False
        ai_note = "单 Agent 是纯分析师：system_prompt 明令 MUST NOT call place_order"
    print(f"AI Agent 可下单: {'是' if ai_can_trade else '否'}（{ai_note}）")

    if ai_can_trade and provider != "claude" and not allow_live:
        ai_can_trade = False
        print(
            "!! 额外的 AC-11 拦截：非 Claude provider 在 rollout=micro/live 下需要 "
            "LLM_ALLOW_LIVE=true，否则 openai_loop 直接回 TRADE BLOCKED"
        )
        print("   （见 mcp_server/agents/openai_loop.py:_rollout_allows_trade）")

    if not strategy_can_trade and not ai_can_trade:
        print("\n>>> 死锁：策略路径与 AI 路径都无法下单 —— 这就是零成交的直接原因。")
    return {"strategy_can_trade": strategy_can_trade, "ai_can_trade": ai_can_trade}


async def check_link(symbol: str) -> dict:
    """3) 数据链路：Bridge 健康 / 账户 / tick。"""
    _hr(f"3) 数据链路（MT5 Bridge，{symbol} → {to_broker_alias(symbol)}）")
    connector = MT5BridgeConnector()
    md = MarketDataService(connector)
    info: dict = {}
    try:
        health = await connector.get_health()
        print(f"bridge health: {health.get('data', health)}")
        account = await connector.get_account()
        if account.get("success"):
            d = account["data"]
            print(
                f"account: balance={d.get('balance')} equity={d.get('equity')} "
                f"margin={d.get('margin')} profit={d.get('profit')}"
            )
            info["balance"] = d.get("balance")
        else:
            print(f"!! account 获取失败: {account.get('error')}")
        tick = await md.get_current_tick(symbol)
        print(f"tick: {tick}")
        info["tick_ok"] = tick is not None
        if not tick:
            print("!! 无 tick —— _generate_signal/_size_and_place_order 都会直接 return")
    finally:
        try:
            await connector.close()
        except Exception as e:  # httpx>=0.28 的 AsyncClient 没有 close()，只有 aclose()
            print(f"（提示）connector.close() 失败：{e!r} —— app/mt5/connector.py:40 用的是 close()")
    return info


def _default_strategy_name(asset_class: str, profile: dict) -> str:
    """复刻 engine._default_strategy_for_profile 的取值顺序。"""
    explicit = profile.get("strategy_default")
    if explicit:
        return explicit
    return {
        "forex": "ema_crossover",
        "metal": "ema_crossover",
        "energy": "breakout",
        "crypto": "breakout",
        "index": "mean_reversion",
    }.get(asset_class, "ema_crossover")


async def check_strategy_activity(symbol: str, bars: int = 300) -> None:
    """4) 策略活性：在真实行情上评估各策略的信号频率。"""
    _hr(f"4) 策略活性回放（{symbol} {settings.timeframe}，最近 {bars} 根）")
    connector = MT5BridgeConnector()
    md = MarketDataService(connector)
    try:
        df = await md.get_ohlcv(symbol, settings.timeframe, bars)
        if df is None or df.empty:
            print("!! 无法获取 OHLCV —— 引擎的 _generate_signal 会返回 None")
            return
        print(f"bars={len(df)}  last_close={df['close'].iloc[-1]}")

        h1 = await md.get_ohlcv(symbol, "H1", 60)
        h1_trend = _get_h1_trend(h1)
        label = "上行" if h1_trend == 1 else "下行" if h1_trend == -1 else "中性"
        print(f"H1 趋势（MTF 过滤）= {h1_trend} ({label})")

        profile = SYMBOL_PROFILES.get(symbol, {})
        asset_class = (profile.get("asset_class") or "").lower()
        default_name = _default_strategy_name(asset_class, profile)
        print(f"\n该品种默认策略 = {default_name}（asset_class={asset_class or 'unknown'}）\n")

        print(f"{'策略':<20}{'信号数':>7}{'占比':>9}{'折算/天':>10}{'倒数第2根':>12}{'最后一根':>10}")
        for name in CANDIDATE_STRATEGIES:
            try:
                strat = get_strategy(name, symbol=symbol)
                out = strat.calculate(df.copy())
                if "signal" not in out.columns:
                    continue
                col = out["signal"]
                non_zero = int((col != 0).sum())
                pct = non_zero / len(col) * 100
                per_day = non_zero / len(col) * 96  # M15: 96 根/天
                marker = " *默认" if name == default_name else ""
                print(
                    f"{name + marker:<20}{non_zero:>7}{pct:>8.1f}%{per_day:>10.1f}"
                    f"{int(col.iloc[-2]):>12}{int(col.iloc[-1]):>10}"
                )
            except Exception as e:
                print(f"{name:<20} 评估失败: {e!r}")

        print(
            "\n说明：引擎只读取倒数第 2 根（df.iloc[-2]，即刚收盘那一根）的 signal；"
            "\n      ±1 才会继续走 MTF/AI/风控/确认门；0 则本次收线无任何动作。"
        )
    finally:
        try:
            await connector.close()
        except Exception:
            pass


async def check_history(symbol: str) -> None:
    """5) 历史：最近信号/成交事件与 trades 表规模。"""
    _hr("5) 历史信号与成交")
    from sqlalchemy import desc, func, select

    from app.db.session import async_session

    try:
        async with async_session() as db:
            total_trades = await db.scalar(select(func.count()).select_from(Trade))
            print(f"trades 表行数 = {total_trades}")
            for etype in ("SIGNAL_DETECTED", "TRADE_OPENED", "TRADE_BLOCKED", "AI_AGENT_ERROR"):
                stmt = (
                    select(BotEvent)
                    .where(BotEvent.event_type == etype)
                    .order_by(desc(BotEvent.id))
                    .limit(1)
                )
                last = (await db.execute(stmt)).scalars().first()
                if last is None:
                    print(f"{etype:<16} 从未出现")
                else:
                    print(f"{etype:<16} 最近一次: {last.created_at} | {last.message[:90]}")
    except Exception as e:
        print(f"!! 数据库查询失败: {e!r}")


async def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else (settings.symbol or "GOLD")
    print(f"诊断品种: {symbol}  时间: {datetime.now().isoformat(timespec='seconds')}")

    # 任何会调用 MT5 Bridge 的进程都必须先把 DB 里的品种 profile 加载进内存，
    # 否则 to_broker_alias() 退化（GOLD 不会映射成券商名 GOLD_），行情请求打到
    # 桥上会得到 "No tick/OHLCV data"。见 services/symbol_config_service.py:81。
    _hr("0) 加载 DB 品种 profile（别名解析前置条件）")
    try:
        from app.services.symbol_config_service import load_profiles_into_memory

        count = await load_profiles_into_memory()
        print(f"已加载 {count} 条 profile 条目")
        print(f"GOLD 的 broker_alias = {to_broker_alias('GOLD')!r}")
    except Exception as e:
        print(f"!! profile 加载失败: {e!r}")

    mode_info = await check_mode()
    rights = await check_execution_rights(mode_info)
    await check_link(symbol)
    await check_strategy_activity(symbol)
    await check_history(symbol)

    _hr("VERDICT")
    if rights["strategy_can_trade"] or rights["ai_can_trade"]:
        print(
            "存在可下单路径。若仍长期无信号，请检查：熔断/PAUSED 状态、"
            "信号后的 MTF/AI/风控/确认门拦截（grep 'Trade blocked' 日志）。"
        )
    else:
        print("!! 无人可下单（死锁）。按 docs/SIGNAL-DIAGNOSIS-2026-09-16.md §7 修复：")
        print("   A) 恢复策略交易：PUT /api/bot/strategy {name: <真实策略>, symbol: <SYMBOL>}")
        print("   B) 或用 AI 自主交易：AGENT_MODE=multi + LLM_ALLOW_LIVE=true + rollout>=micro")


if __name__ == "__main__":
    asyncio.run(main())
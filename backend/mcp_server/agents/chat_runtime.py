"""Isolated read-only chat execution; never dispatches the automatic trading loops.

Audit callbacks are awaited before work continues. The caller owns durable storage,
redaction and execution_id/run_id enrichment. Only public text is collected.
Cancellation propagates to the worker; cancelling local IO cannot stop remote jobs.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any

from loguru import logger

READONLY_TOOLS = frozenset({
    "get_tick", "get_ohlcv", "get_spread", "calculate_ema", "calculate_rsi",
    "calculate_atr", "run_full_analysis", "get_account", "get_exposure",
    "get_positions", "check_correlation", "get_sentiment", "get_sentiment_history",
    "get_trade_history", "get_daily_pnl", "get_performance", "analyze_recent_trades",
    "validate_trade", "calculate_lot_size", "calculate_sl_tp", "get_var_analysis",
    "get_volatility_forecast", "get_quant_signals", "detect_regime",
    "get_strategy_profiles", "compute_overfitting_score", "get_memories", "get_learnings",
})
HEAVY_TOOLS = frozenset({"compute_overfitting_score", "get_volatility_forecast", "get_var_analysis"})
DEFAULT_BUDGET = dict(total_timeout_s=600, request_timeout_s=180, tool_timeout_s=60,
                      heavy_tool_timeout_s=300, max_turns=15, max_retries=0)
SUMMARY_PROMPT = (
    "Tool/time budget is exhausted. Give a final summary using only the public text "
    "and tool evidence already available. Do not call tools. Explicitly identify "
    "missing evidence and unfinished analysis; do not claim it was completed."
)


class RuntimeStop(Exception):
    def __init__(self, status: str, reason: str):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _server():
    from mcp_server.server import get_server
    return get_server()


def _openai_client():
    from openai import AsyncOpenAI
    from app.config import settings
    # Retry at our boundary, not inside an opaque transport loop.
    return AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key or "not-needed",
                       max_retries=0)


def _result_text(raw: Any) -> tuple[str, bool]:
    if isinstance(raw, tuple):
        blocks, structured = raw
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False, default=str), False
        raw = blocks
    if hasattr(raw, "content"):
        return "\n".join(getattr(b, "text", "") for b in raw.content), bool(getattr(raw, "isError", False))
    if isinstance(raw, list):
        return "\n".join(getattr(b, "text", "") for b in raw), any(getattr(b, "isError", False) for b in raw)
    return json.dumps(raw, ensure_ascii=False, default=str), bool(isinstance(raw, dict) and raw.get("isError"))


class _Run:
    def __init__(self, agent_id, model, provider, budget, emit):
        self.budget = {**DEFAULT_BUDGET, **budget}
        for key, value in self.budget.items():
            if key in DEFAULT_BUDGET:
                minimum = 0 if key == "max_retries" else 1 if key == "max_turns" else 0
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"Invalid budget: {key}")
                if value < minimum or (key.endswith("_s") and value == 0):
                    raise ValueError(f"Invalid budget: {key}")
                if key in {"max_turns", "max_retries"} and not isinstance(value, int):
                    raise ValueError(f"Invalid budget: {key}")
        self.started = time.monotonic()
        self.deadline = self.started + self.budget["total_timeout_s"]
        self.reserve = min(self.budget["request_timeout_s"], self.budget["total_timeout_s"] * .15)
        self.identity = dict(agent_id=agent_id, model=model, provider=provider)
        self.callback = emit
        self.parts, self.calls = [], []
        self.turns = 0
        self.usage = {}
        self.response = ""
        self.issue = None
        self.allowed = set()
        self.server = None

    def remaining(self):
        return max(0.0, self.deadline - time.monotonic())

    async def wait(self, factory, cap, reason):
        left = self.remaining()
        if not left:
            raise RuntimeStop("timed_out", "total_timeout")
        try:
            async with asyncio.timeout(min(left, cap)):
                result = await factory()
        except TimeoutError as exc:
            raise RuntimeStop("timed_out", "total_timeout" if self.remaining() <= 0 else reason) from exc
        if not self.remaining():
            raise RuntimeStop("timed_out", "total_timeout")
        return result

    async def emit(self, kind, **payload):
        try:
            await self.wait(lambda: self.callback(kind, {**self.identity, **payload}), self.remaining(), "audit_timeout")
        except RuntimeStop:
            raise
        except Exception as exc:
            raise RuntimeStop("failed", "audit_error") from exc

    async def text(self, text):
        if text:
            self.parts.append(text)
            await self.emit("assistant_text", text=text, turn=self.turns)

    def add_usage(self, usage):
        for key, value in (usage or {}).items():
            if isinstance(value, (int, float)):
                self.usage[key] = self.usage.get(key, 0) + value

    def result(self, status, reason):
        return dict(status=status, reason_code=reason, response=self.response,
                    partial_response="\n".join(self.parts), tool_calls=self.calls,
                    turns=self.turns, duration_s=round(time.monotonic() - self.started, 3), usage=self.usage)

    async def tool(self, name, args, call_id, enabled=True):
        started = time.monotonic()
        record = dict(tool=name, tool_call_id=call_id, input=args, executed=False,
                      started_s=started - self.started, status="running")
        self.calls.append(record)
        await self.emit("tool_started", **record)
        reason, output = None, ""
        try:
            if name not in self.allowed or name not in READONLY_TOOLS or not enabled:
                reason, output = "tool_not_allowed", "Denied by read-only chat policy."
            elif not isinstance(args, dict):
                reason, output = "invalid_tool_arguments", "Expected a JSON object."
            elif self.remaining() <= self.reserve:
                reason, output = "budget_reserve", "Skipped to reserve final summary time."
            else:
                cap = self.budget["heavy_tool_timeout_s" if name in HEAVY_TOOLS else "tool_timeout_s"]
                record["executed"] = True
                raw = await self.wait(lambda: self.server.call_tool(name, args),
                                      min(cap, self.remaining() - self.reserve), "tool_timeout")
                output, error = _result_text(raw)
                reason = "tool_error" if error else None
        except RuntimeStop as exc:
            reason, output = exc.reason, "Local wait stopped; remote work may still be running."
        except asyncio.CancelledError:
            record.update(status="cancelled", reason_code="cancelled", duration_s=time.monotonic() - started)
            raise
        except Exception as exc:
            reason, output = "tool_error", f"Tool failed ({type(exc).__name__})."
        record.update(output=output, status="failed" if reason else "completed", reason_code=reason,
                      finished_s=time.monotonic() - self.started, duration_s=time.monotonic() - started)
        self.issue = self.issue or reason
        await self.emit("tool_finished", **record)
        return output, bool(reason)


async def _openai_request(run, client, params):
    from openai import APIConnectionError, APIStatusError, APITimeoutError

    async def request():
        for attempt in range(run.budget["max_retries"] + 1):
            try:
                return await client.chat.completions.create(**params)
            except Exception as exc:
                retryable = isinstance(exc, APIConnectionError) or (
                    isinstance(exc, APIStatusError) and (exc.status_code == 429 or exc.status_code >= 500))
                if not retryable or attempt == run.budget["max_retries"]:
                    if isinstance(exc, APITimeoutError):
                        raise RuntimeStop("timed_out", "llm_request_timeout") from exc
                    raise
                await run.emit("provider_retry", attempt=attempt + 1)
                await asyncio.sleep(min(.25 * 2 ** attempt, 2))
    return await run.wait(request, run.budget["request_timeout_s"], "llm_request_timeout")


async def _run_openai(run, system_prompt, user_message, model):
    from app.config import settings
    schemas = await run.wait(run.server.list_tools, run.budget["tool_timeout_s"], "tool_timeout")
    tools = [{"type": "function", "function": {"name": t.name, "description": t.description or "",
              "parameters": t.inputSchema}} for t in schemas if t.name in run.allowed]
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}]
    client = _openai_client()
    try:
        while run.turns < run.budget["max_turns"]:
            summarizing = run.turns == run.budget["max_turns"]-1 or run.remaining() <= run.reserve
            if summarizing:
                messages.append({"role": "user", "content": SUMMARY_PROMPT})
            params = dict(model=model, messages=messages, temperature=settings.llm_temperature)
            if tools:
                params.update(tools=tools, tool_choice="none" if summarizing else "auto")
            await run.emit("model_started", turn=run.turns + 1, summary_only=summarizing)
            reply = await _openai_request(run, client, params)
            run.turns += 1
            usage = getattr(reply, "usage", None)
            run.add_usage(usage.model_dump() if hasattr(usage, "model_dump") else {})
            if not getattr(reply, "choices", None):
                raise RuntimeStop("failed", "empty_response")
            msg = reply.choices[0].message
            text = getattr(msg, "content", None) or ""
            await run.text(text)
            calls = getattr(msg, "tool_calls", None) or []
            await run.emit("model_finished", turn=run.turns, tool_count=len(calls))
            if not calls:
                if not text.strip():
                    raise RuntimeStop("failed", "empty_response")
                if getattr(reply.choices[0], "finish_reason", "stop") == "length":
                    return "incomplete", "output_limit"
                if run.issue:
                    return "incomplete", run.issue
                run.response = text
                return "completed", None
            messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in calls
            ]})
            for tc in calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (ValueError, TypeError):
                    args = None
                output, _ = await run.tool(tc.function.name, args, tc.id, enabled=not summarizing)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": output})
            if summarizing:
                return "incomplete", "max_turns" if run.turns >= run.budget["max_turns"] else "budget_reserve"
        return "incomplete", "max_turns"
    finally:
        try:
            async with asyncio.timeout(1):
                await client.close()
        except Exception:
            pass


async def run_chat_runtime(*, system_prompt, user_message, tool_names, agent_id, model, provider, budget, emit):
    """Bounded public API; partial public text and tool trace survive failures."""
    run = _Run(agent_id, model, provider, budget, emit)
    if not set(tool_names) <= READONLY_TOOLS:
        return run.result("failed", "tool_not_allowed")
    run.allowed = set(tool_names)
    status, reason = "failed", "provider_error"
    try:
        async with asyncio.timeout(run.budget["total_timeout_s"]):
            await run.emit("runtime_started", budget=run.budget)
            run.server = _server()
            if provider == "openai_compat":
                status, reason = await _run_openai(run, system_prompt, user_message, model)
            elif provider == "claude":
                from mcp_server.agents.chat_sdk_runtime import run_sdk
                status, reason = await run_sdk(run, system_prompt, user_message, model)
            else:
                status, reason = "failed", "unsupported_provider"
    except RuntimeStop as exc:
        status, reason = exc.status, exc.reason
    except TimeoutError:
        status, reason = "timed_out", "total_timeout"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Chat runtime failed: {}", type(exc).__name__)
        status, reason = "failed", "provider_error"
    if status != "completed":
        run.response = ""
    # Storage's agent_completed event records the terminal result even after deadline.
    return run.result(status, reason)




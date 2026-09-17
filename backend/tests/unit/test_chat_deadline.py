"""Regression: the chat deadline must interrupt a stalled provider, not wait a turn."""
import asyncio
from unittest.mock import patch

import pytest

from app.config import settings
from mcp_server.agents.chat_agent import run_chat_turn


@pytest.mark.asyncio
async def test_chat_total_budget_interrupts_stalled_loop(monkeypatch):
    monkeypatch.setattr(settings, "chat_total_timeout_s", 1)
    cancelled = asyncio.Event()

    async def stalled(**kwargs):
        try:
            await asyncio.sleep(30)
            return {"response": "should never complete"}
        finally:
            cancelled.set()

    with patch("mcp_server.agents.chat_agent.run_agent_loop", side_effect=stalled), patch(
        "mcp_server.agents.prompt_registry.get_active_prompt", return_value="read-only analyst"
    ):
        # Test guard is longer than task budget but much shorter than stalled IO.
        result = await asyncio.wait_for(run_chat_turn("GOLD", user_message="plan"), timeout=2)
    assert result["status"] == "timed_out"
    assert result["reason_code"] == "total_timeout"
    assert result["response"] == ""
    assert cancelled.is_set()

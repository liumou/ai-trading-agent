"""Isolated V2 workflow tests; no provider, database, Redis or broker connections."""
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.config import Settings
from mcp_server.agents.chat_workflow import ROLE_TOOLS, chat_budget, run_workflow
from mcp_server.agents.chat_agent import CHAT_TOOL_NAMES


def test_chat_budget_defaults_and_validation():
    conf = Settings(_env_file=None)
    assert conf.chat_total_timeout_s == 600
    assert conf.chat_max_turns == 15
    for field, value in [("chat_total_timeout_s", 0), ("chat_max_turns", 31), ("chat_request_timeout_s", -1)]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{field: value})


@pytest.mark.asyncio
async def test_experts_review_actual_candidate_and_emit_all_roles():
    calls, events = [], []

    async def runtime(**kw):
        calls.append(kw)
        await kw["emit"]("assistant_text", {"text": "公开摘要"})
        return {"status": "completed", "response": f"report-{kw['agent_id']}", "turns": 1}

    async def emit(kind, payload):
        events.append((kind, payload))

    with patch("mcp_server.agents.chat_runtime.run_chat_runtime", side_effect=runtime):
        result = await run_workflow({"symbol": "GOLD", "mode": "experts", "message": "plan", "budget": chat_budget()}, emit)
    assert result["status"] == "completed"
    assert result["turns"] == 6
    assert len(calls) == 6
    risk = next(c for c in calls if c["agent_id"] == "risk_analyst")
    assert "report-plan_drafter" in risk["user_message"]
    for c in calls:
        assert set(c["tool_names"]) <= set(CHAT_TOOL_NAMES)
        assert c["tool_names"] == ROLE_TOOLS[c["agent_id"]]
    assert len([e for e in events if e[0] == "agent_started"]) == 6
    assert len([e for e in events if e[0] == "agent_completed"]) == 6
    assert all(e[1].get("execution_id") for e in events)


@pytest.mark.asyncio
async def test_expert_failure_cannot_be_complete_plan():
    async def runtime(**kw):
        if kw["agent_id"] == "risk_analyst":
            return {"status": "timed_out", "reason_code": "total_timeout", "response": ""}
        return {"status": "completed", "response": "summary", "turns": 1}
    with patch("mcp_server.agents.chat_runtime.run_chat_runtime", side_effect=runtime):
        result = await run_workflow({"symbol": "GOLD", "mode": "experts"}, AsyncMock())
    assert result["status"] == "incomplete"
    assert result["reason_code"] == "specialist_incomplete"
    assert not result["response"]
    assert result["partial_response"]


@pytest.mark.asyncio
async def test_single_records_one_real_participant():
    with patch("mcp_server.agents.chat_runtime.run_chat_runtime", new=AsyncMock(return_value={"status": "completed", "response": "ok"})) as runtime, patch(
        "mcp_server.agents.prompt_registry.get_active_prompt", new=AsyncMock(return_value="readonly")
    ):
        result = await run_workflow({"symbol": "GOLD", "message": "hi"}, AsyncMock())
    assert result["status"] == "completed"
    assert runtime.await_count == 1
    assert runtime.call_args.kwargs["agent_id"] == "chat_agent"


@pytest.mark.asyncio
async def test_audit_failure_stops_before_model():
    with patch("mcp_server.agents.chat_runtime.run_chat_runtime", new=AsyncMock()) as runtime, patch(
        "mcp_server.agents.prompt_registry.get_active_prompt", new=AsyncMock(return_value="readonly")
    ):
        with pytest.raises(RuntimeError, match="DB unavailable"):
            await run_workflow({"symbol": "GOLD", "message": "hi"}, AsyncMock(side_effect=RuntimeError("DB unavailable")))
    runtime.assert_not_called()

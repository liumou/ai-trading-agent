"""Claude Agent SDK 0.1.50 read-only in-process chat adapter.

No builtins, project/user settings, external MCP servers, or permission bypass.
Request timeout bounds a whole SDK query (including its tool orchestration), since
this SDK exposes no per-HTTP-request/retry control. Queries are never retried here.
Only public TextBlock content is audited; ThinkingBlock/stream deltas are ignored.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultDeny,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
)

from mcp_server.agents.chat_runtime import READONLY_TOOLS, SUMMARY_PROMPT, RuntimeStop

SERVER_NAME = "chat-readonly"
PREFIX = f"mcp__{SERVER_NAME}__"


async def run_sdk(run, system_prompt, user_message, model):
    """Update _Run and return (status, reason), preserving public partial evidence."""
    if not run.allowed <= READONLY_TOOLS:
        return "failed", "tool_not_allowed"
    schemas = await run.wait(run.server.list_tools, run.budget["request_timeout_s"], "tool_schema_timeout")
    schemas = [s for s in schemas if s.name in run.allowed]
    if {s.name for s in schemas} != run.allowed:
        return "failed", "tool_unavailable"
    tickets = defaultdict(deque)
    seen = set()
    fatal = None
    phase_tools = False

    def key(name, args):
        return name, json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)

    async def deny_permission(name, args, context):
        # PreToolUse explicitly allows only our wrappers; everything else fails closed.
        return PermissionResultDeny(message="Only audited chat MCP wrappers are permitted.")

    async def pre_tool(data, tool_use_id, context):
        nonlocal fatal
        name = data.get("tool_name", "")
        call_id = data.get("tool_use_id") or tool_use_id
        allowed = (phase_tools and not fatal and name.startswith(PREFIX) and name[len(PREFIX):] in run.allowed
                   and call_id and call_id not in seen)
        if allowed:
            seen.add(call_id)
            tickets[key(name[len(PREFIX):], data.get("tool_input", {}))].append(call_id)
        else:
            try:
                await run.tool(name, data.get("tool_input", {}), call_id, enabled=False)
            except RuntimeStop as exc:
                fatal = exc
                raise
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                "permissionDecision": "allow" if allowed else "deny",
                "permissionDecisionReason": "Explicit read-only chat allowlist"}}

    def wrapper(schema):
        async def handler(args):
            nonlocal fatal
            queue = tickets[key(schema.name, args)]
            if fatal:
                raise fatal
            if not queue:
                # The MCP schema does not carry tool_use_id; no matching hook means no IO.
                run.issue = run.issue or "tool_not_allowed"
                return {"content": [{"type": "text", "text": "Missing read-only authorization ticket."}], "isError": True}
            try:
                output, error = await run.tool(schema.name, args, queue.popleft())
            except RuntimeStop as exc:
                fatal = exc
                raise
            return {"content": [{"type": "text", "text": output}], "isError": error}
        return SdkMcpTool(schema.name, schema.description or "", schema.inputSchema, handler)

    sdk_server = create_sdk_mcp_server(SERVER_NAME, tools=[wrapper(s) for s in schemas])

    async def session(prompt, turns, tools_enabled):
        nonlocal fatal, phase_tools
        phase_tools = tools_enabled
        tickets.clear()
        options = ClaudeAgentOptions(
            system_prompt=system_prompt, model=model, max_turns=turns,
            tools=[], allowed_tools=[PREFIX + s.name for s in schemas] if tools_enabled else [],
            mcp_servers={SERVER_NAME: sdk_server} if tools_enabled else {},
            permission_mode="default", can_use_tool=deny_permission,
            hooks={"PreToolUse": [HookMatcher(hooks=[pre_tool])]},
            setting_sources=[], plugins=[],
            extra_args={},
        )
        async def prompts():
            yield {"type": "user", "message": {"role": "user", "content": prompt}}
        last_text = ""
        result = None
        await run.emit("model_started", turn=run.turns + 1, tools_enabled=tools_enabled)
        async for message in query(prompt=prompts(), options=options):
            if fatal:
                raise fatal
            if isinstance(message, AssistantMessage):
                run.turns += 1
                has_tools = False
                text = []
                for block in message.content:
                    if isinstance(block, TextBlock):
                        await run.text(block.text)
                        text.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        has_tools = True
                last_text = "" if has_tools else "\n".join(text)
                if message.error:
                    raise RuntimeStop("failed", "provider_error")
            elif isinstance(message, ResultMessage):
                run.add_usage(message.usage)
                result = message
        if fatal:
            raise fatal
        await run.emit("model_finished", turn=run.turns, usage=dict(run.usage))
        if result is None:
            return "failed", "empty_response"
        if result.is_error:
            return ("incomplete", "max_turns") if result.subtype == "error_max_turns" else ("failed", "provider_error")
        final = last_text or result.result or ""
        if not final.strip():
            return "failed", "empty_response"
        if not last_text:
            await run.text(final)
        run.response = final
        return "completed", None

    turns = run.budget["max_turns"]
    tools_enabled = bool(schemas) and turns > 1
    if tools_enabled:
        status, reason = await run.wait(
            lambda: session(user_message, turns - 1, True),
            min(run.budget["request_timeout_s"], max(.001, run.remaining() - run.reserve)),
            "llm_request_timeout",
        )
        if status == "completed" and not run.issue:
            return status, reason
        if reason != "max_turns" and not run.issue:
            return status, reason
        run.issue = run.issue or reason
        if run.turns >= turns:
            return "incomplete", "max_turns"
    elif schemas:
        run.issue = "max_turns"
    evidence = json.dumps({"public_text": run.parts, "tool_results": run.calls}, ensure_ascii=False, default=str)
    prompt = user_message if not schemas else f"{user_message}\n\n{SUMMARY_PROMPT}\n\nEvidence:\n{evidence}"
    status, reason = await run.wait(lambda: session(prompt, max(1, turns - run.turns), False),
                                    run.budget["request_timeout_s"], "llm_request_timeout")
    if status != "completed":
        return status, reason
    if run.issue:
        return ("timed_out" if run.issue in {"tool_timeout", "total_timeout"} else "incomplete"), run.issue
    return "completed", None

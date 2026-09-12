"""
Base agent — provider-aware agent loop dispatcher.

All specialist agents and the orchestrator use run_agent_loop(), which routes
to the Claude Agent SDK loop (default) or the self-built OpenAI-compat loop
based on settings.llm_provider.
"""

from typing import Any

from app.config import settings
from mcp_server.sdk_client import sdk_agent_loop

# ─── Model Constants ─────────────────────────────────────────────────────────
# 保留作为 settings 未配置时的 fallback 默认值来源；实际模型在调用时解析
# （优先级：显式 model 参数 > settings.llm_model > 按 agent 的 settings
# model_orchestrator/model_specialist > 此处常量）。

MODEL_ORCHESTRATOR = "claude-sonnet-4-20250514"
MODEL_SPECIALIST = "claude-haiku-4-5-20251001"

# ─── Agent Loop 注册表（开闭原则）────────────────────────────────────────────
# 注册表登记 provider → 实现名；_resolve_agent_loop 在调用时按名字解析模块
# 全局（而非 import 时捕获函数引用），保证测试可以 patch
# `mcp_server.agents.base.sdk_agent_loop` 生效。

AGENT_LOOPS = {
    "claude": "sdk_agent_loop",
    "openai_compat": "openai_agent_loop",
}


def _resolve_agent_loop():
    """按 settings.llm_provider 查 AGENT_LOOPS 注册表解析 loop 实现。

    查表驱动（OCP：新增 provider 只需登记一行 + 提供实现）；返回前对
    sdk_agent_loop 走模块全局查找而非闭包捕获，保证测试可以 patch
    `mcp_server.agents.base.sdk_agent_loop` 生效。
    """
    impl_name = AGENT_LOOPS.get(settings.llm_provider, AGENT_LOOPS["claude"])
    if impl_name == "openai_agent_loop":
        from mcp_server.agents.openai_loop import openai_agent_loop

        return openai_agent_loop
    return sdk_agent_loop


# 决策级 agent（用 orchestrator 档模型）：multi-agent 的 orchestrator 与
# single-agent 模式的入口。两者旧行为一致（默认 sonnet），zero-regression
# 要求下必须同档；prompt_registry 的前端展示也按此映射。
_ORCHESTRATOR_GRADE_AGENTS = {"orchestrator", "single_agent"}


def _resolve_model(agent_id: str, model: str | None) -> str:
    """延迟解析模型：显式参数 > llm_model > 按 agent 的 per-agent 配置 > 常量。"""
    if model:
        return model
    if settings.llm_model:
        return settings.llm_model
    if agent_id in _ORCHESTRATOR_GRADE_AGENTS:
        return settings.model_orchestrator or MODEL_ORCHESTRATOR
    return settings.model_specialist or MODEL_SPECIALIST


# ─── Shared Agent Loop ──────────────────────────────────────────────────────


async def run_agent_loop(
    system_prompt: str,
    user_message: str,
    tools: list[dict[str, Any]] | None = None,
    tool_names: list[str] | None = None,
    model: str | None = None,
    max_turns: int = 15,
    timeout: int = 120,
    agent_id: str = "unknown",
    **kwargs,
) -> dict:
    """按配置的 LLM provider 分发到对应 agent loop。

    - 默认 claude → sdk_agent_loop（现有行为，零回归）
    - openai_compat → openai_agent_loop（自研工具循环）
    model 在调用时解析，支持不同 agent 用不同模型。
    """
    loop = _resolve_agent_loop()
    resolved_model = _resolve_model(agent_id, model)
    return await loop(
        prompt=user_message,
        system_prompt=system_prompt,
        model=resolved_model,
        allowed_tools=tool_names,
        max_turns=max_turns,
        timeout=timeout,
        agent_id=agent_id,
    )

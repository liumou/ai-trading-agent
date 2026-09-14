"""
LLM Provider 抽象 — 让分析与非交易决策路径支持任意 OpenAI 兼容模型。

两条实现：
  - ClaudeSDKProvider：默认，走 Claude Agent SDK（Max 订阅），零回归。
  - OpenAICompatProvider：OpenAI 兼容端点（OpenAI / DeepSeek / OpenRouter /
    Ollama / vLLM），仅覆盖「简单补全」路径；agent 工具循环在
    `mcp_server/agents/openai_loop.py` 单独实现（交易决策）。

配置入口：`app.config.settings`（LLM_PROVIDER / LLM_BASE_URL / LLM_API_KEY /
LLM_MODEL / LLM_TEMPERATURE / LLM_TIMEOUT）。默认 claude 保持现有行为。
"""

import json
import re
import time

from loguru import logger

from app.ai.circuit_breaker import llm_circuit_breaker
from app.ai.llm_errors import format_llm_error, is_connection_error
from app.config import settings

# 默认模型名（config 的 settings.model_specialist 已有默认值，这里仅作兜底）
_FALLBACK_MODEL = "claude-haiku-4-5-20251001"


def _make_openai_client():
    """构造 AsyncOpenAI 客户端（懒导入：openai 未安装时抛 ImportError 由调用方兜底）。

    api_key 对 Ollama/vLLM 可留空 —— openai 库要求非空占位值，用 "not-needed"。
    集中一处构造，避免三个调用点重复且各自漂移。
    """
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "not-needed",
        timeout=settings.llm_timeout,
        # 连接类失败显式短退避重试（次数可配）
        max_retries=settings.llm_max_retries or 2,
    )


async def _record_openai_usage(resp, model: str, agent_id: str, duration_ms: int) -> None:
    """把 OpenAI 兼容端点的 usage 写入 AIUsageLog（AC-6）。

    旧路径由 sdk_complete 内部记录；openai_compat 必须自己记，否则切到
    非 Claude 后简单补全路径（sentiment/quant/优化）在 /ai-usage 全部消失。
    记录失败绝不影响补全结果（fail-safe），与 log_ai_usage 语义一致。
    """
    try:
        from app.ai.usage_logger import log_ai_usage

        usage_obj = getattr(resp, "usage", None)
        usage: dict | None = None
        if usage_obj is not None:
            if hasattr(usage_obj, "model_dump"):  # openai pydantic 对象
                usage = usage_obj.model_dump()
            else:  # 测试替身 / 非 pydantic 形状
                usage = {
                    "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
                }
                details = getattr(usage_obj, "prompt_tokens_details", None)
                if details is not None:
                    cached = (
                        details.model_dump()
                        if hasattr(details, "model_dump")
                        else {"cached_tokens": getattr(details, "cached_tokens", 0)}
                    )
                    usage["prompt_tokens_details"] = cached
        await log_ai_usage(
            agent_id=agent_id,
            model=model,
            usage=usage,
            duration_ms=duration_ms,
        )
    except Exception as e:
        logger.warning(f"openai usage record failed ({agent_id}): {e}")


class LLMProvider:
    """LLM Provider 抽象基类。所有调用失败（网络/解析）返回 None，不抛异常。

    但「配置错误」（base_url 缺失等）属于 fail-fast 范畴，由 get_provider()
    在启动/首次调用时抛 RuntimeError，让上层（lifespan / 端点）及时暴露。
    """

    name: str = "base"

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int,
        agent_id: str,
        temperature: float,
    ) -> str | None:
        """请求纯文本补全，失败返回 None。"""
        raise NotImplementedError

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int,
        agent_id: str,
        temperature: float,
    ) -> dict | None:
        """请求并解析 JSON 结构化输出，失败返回 None。"""
        raise NotImplementedError


class ClaudeSDKProvider(LLMProvider):
    """走 Claude Agent SDK 的默认实现（Max 订阅，无 API key）。"""

    name = "claude"

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int,
        agent_id: str,
        temperature: float,
    ) -> str | None:
        # sdk_complete 内部捕获异常返回 None，符合「AI 可选层」语义。
        from mcp_server.sdk_client import sdk_complete

        return await sdk_complete(
            prompt=user_prompt,
            system_prompt=system_prompt,
            model=model or settings.llm_model or _FALLBACK_MODEL,
            max_turns=1,
            agent_id=agent_id,
        )

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int,
        agent_id: str,
        temperature: float,
    ) -> dict | None:
        text = await self.complete(system_prompt, user_prompt, model, max_tokens, agent_id, temperature)
        return _safe_json_loads(text)


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容端点（Chat Completions）实现。

    注意：api_key 对 Ollama/vLLM 可留空；openai 库需要一个非空占位值，
    这里用 "not-needed"。base_url 缺失属配置错误，在 get_provider() 处抛错。
    """

    name = "openai_compat"

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int,
        agent_id: str,
        temperature: float,
    ) -> str | None:
        if not llm_circuit_breaker.allow():
            logger.warning(f"OpenAI Compat complete skipped ({agent_id}): LLM circuit open")
            return None
        try:
            client = _make_openai_client()
            resolved = model or settings.llm_model or _FALLBACK_MODEL
            start = time.time()
            resp = await client.chat.completions.create(
                model=resolved,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature if temperature is not None else settings.llm_temperature,
            )
            llm_circuit_breaker.record_success()
            await _record_openai_usage(resp, resolved, agent_id, int((time.time() - start) * 1000))
            return resp.choices[0].message.content if resp.choices else None
        except Exception as e:
            logger.error(
                f"OpenAI Compat complete failed ({agent_id}): "
                f"{format_llm_error(e, settings.llm_base_url)}"
            )
            if is_connection_error(e):
                llm_circuit_breaker.record_failure()
            # 显式 opt-in 回退（llm_fallback_to_claude）：默认 False 时 fail-closed，
            # 绝不静默切换模型（交易决策路径的模型必须是运维明确配置的）。
            if settings.llm_fallback_to_claude:
                logger.warning(f"Falling back to Claude SDK for {agent_id} (llm_fallback_to_claude=true)")
                return await ClaudeSDKProvider().complete(
                    system_prompt, user_prompt, model, max_tokens, agent_id, temperature
                )
            return None

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int,
        agent_id: str,
        temperature: float,
    ) -> dict | None:
        # 优先 response_format=json_object（OpenAI/DeepSeek 官方支持）；
        # 不支持的厂商（如 Ollama）会在请求时抛异常，降级为 prompt 约束 + 解析重试。
        text = await self._complete_json_via_response_format(
            system_prompt, user_prompt, model, max_tokens, agent_id, temperature
        )
        if text:
            parsed = _safe_json_loads(text)
            if parsed is not None:
                return parsed

        # 降级：prompt 强制 JSON 输出 + 最多 2 次解析尝试
        prompt = f"{user_prompt}\n\nRespond with a single valid JSON object. No markdown, no prose."
        for attempt in range(2):
            text = await self.complete(system_prompt, prompt, model, max_tokens, agent_id, temperature)
            if not text:
                return None
            parsed = _safe_json_loads(text)
            if parsed is not None:
                return parsed
            if attempt == 0:
                logger.warning("JSON parse failed on first OpenAI attempt, retrying")
        return None

    async def _complete_json_via_response_format(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int,
        agent_id: str,
        temperature: float,
    ) -> str | None:
        try:
            client = _make_openai_client()
            resolved = model or settings.llm_model or _FALLBACK_MODEL
            start = time.time()
            resp = await client.chat.completions.create(
                model=resolved,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature if temperature is not None else settings.llm_temperature,
                response_format={"type": "json_object"},
            )
            llm_circuit_breaker.record_success()
            await _record_openai_usage(resp, resolved, agent_id, int((time.time() - start) * 1000))
            return resp.choices[0].message.content if resp.choices else None
        except Exception as e:
            if is_connection_error(e):
                logger.error(
                    f"OpenAI response_format call failed (connection): "
                    f"{format_llm_error(e, settings.llm_base_url)}"
                )
                llm_circuit_breaker.record_failure()
            else:
                logger.debug(f"response_format json_object not supported, falling back ({e})")
            return None


def _safe_json_loads(text: str | None) -> dict | None:
    """从模型文本中提取并解析 JSON：容忍 ```json 围栏与前导/尾随空白。"""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError as e:
        logger.error(f"AI JSON parse failed: {e}\nRaw: {text[:200]}")
        return None


def get_provider() -> LLMProvider:
    """按 settings.llm_provider 返回对应 Provider 实例。

    配置错误时抛 RuntimeError（fail-fast，AC-10/AC-13）：openai_compat 必须配置
    base_url，且三个模型字段（llm_model / model_orchestrator / model_specialist）
    非空 —— 否则简单补全路径会静默落到 Claude 默认模型名上、对 OpenAI 兼容端点
    必然失败。调用方应在启动时（lifespan）调用此函数做校验。
    """
    if settings.llm_provider == "openai_compat":
        if not settings.llm_base_url:
            raise RuntimeError(
                "LLM_PROVIDER=openai_compat 但未配置 LLM_BASE_URL — 请设置 OpenAI 兼容端点地址，"
                "或改回 LLM_PROVIDER=claude"
            )
        missing = [
            name
            for name, value in (
                ("LLM_MODEL", settings.llm_model),
                ("MODEL_ORCHESTRATOR", settings.model_orchestrator),
                ("MODEL_SPECIALIST", settings.model_specialist),
            )
            if not (value or "").strip()
        ]
        if missing:
            raise RuntimeError(
                f"LLM_PROVIDER=openai_compat 但模型配置为空: {', '.join(missing)} — "
                "openai_compat 模式下必须显式指定实际可用的模型名"
                "（内置 Claude 默认值对 OpenAI 兼容端点无效）"
            )
        return OpenAICompatProvider()
    return ClaudeSDKProvider()

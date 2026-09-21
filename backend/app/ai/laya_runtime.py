"""
Laya 决策引擎运行时封装（Phase: laya integration，默认关闭）。

laya 是「非自回归单次前向的结构化判定引擎」（choice / score / noul 三原语），
本项目用它做**高频分类预筛**（如新闻情绪三分类），不替换 LLM 的深度决策/长文生成。

设计约束（来自调研 spike 实测）：
- **懒加载单例**：首次用到才 `laya.load()`（冷加载 ~136s 含构建），绝不在 import 期加载。
- **线程安全**：`Agent.device/dtype` 是可变共享状态，OOM 回落会搬模型致数据竞争。
  因此显式 `device="cpu"` 预载，彻底避开回落路径；并用 `threading.Lock` 串行化首次加载。
- **不阻塞事件循环**：推理包 `asyncio.to_thread`（CPU 单次 ~200ms，绝不能进 async 关键路径）。
- **降级**：laya 依赖缺失 / 权重下载失败 → `available=False`，调用方回退 LLM，系统照常运行。
- **镜像支持**：`HF_ENDPOINT` 环境变量（实测 huggingface.co 模型端点被阻断，需 hf-mirror.com）。
"""

import asyncio
import os
import threading
from typing import Any, Dict, Optional

from loguru import logger

from app.config import settings

# try-import 降级：未安装 laya 依赖时系统照常运行（沿用 mcp_server 的 _AGENT_AVAILABLE 模式）
try:
    import laya as _laya  # type: ignore
    _LAYA_IMPORT_OK = True
except Exception:  # pragma: no cover - 依赖缺失时的降级路径
    _laya = None  # type: ignore
    _LAYA_IMPORT_OK = False


class LayaRuntime:
    """Laya 推理运行时单例。懒加载 + 线程安全 + 事件循环友好。"""

    def __init__(self) -> None:
        self._agent: Any = None
        self._lock = threading.Lock()
        self._available: Optional[bool] = None  # None=未探测

    @property
    def available(self) -> bool:
        """laya 是否可用（依赖已装 + 模型已加载或可加载）。"""
        if not settings.laya_enabled or not _LAYA_IMPORT_OK:
            return False
        if self._available is None:
            # 首次探测：不实际加载模型，只确认依赖在（模型懒加载到首次 predict）
            self._available = _LAYA_IMPORT_OK and settings.laya_enabled
        return bool(self._available)

    def _load(self) -> Any:
        """同步加载模型（只调用一次，受锁保护）。失败置 available=False。"""
        if self._agent is not None:
            return self._agent
        if not self.available:
            raise RuntimeError("laya disabled or dependency missing")
        with self._lock:
            if self._agent is not None:
                return self._agent
            try:
                # C10：HF_ENDPOINT 可配置，不再无条件写 hf-mirror。
                # 优先级：环境变量 HF_ENDPOINT > config.laya_hf_endpoint > 不设置（用官方）。
                # 这样 Railway 境外可直连官方，国内部署显式设 HF_ENDPOINT=hf-mirror.com。
                if not os.environ.get("HF_ENDPOINT") and settings.laya_hf_endpoint:
                    os.environ["HF_ENDPOINT"] = settings.laya_hf_endpoint
                os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
                os.environ.setdefault("USE_TF", "0")

                model_id = settings.laya_model
                if settings.laya_model_cache_dir:
                    # 本地缓存目录优先：支持"构建时预下载到镜像"的部署模式
                    local = os.path.join(settings.laya_model_cache_dir, os.path.basename(model_id))
                    if os.path.isdir(local):
                        model_id = local

                # 显式 device="cpu"：避开 OOM 回落路径（Agent.device 可变共享状态），
                # 并保证在无 GPU 的 Railway 上行为确定。CPU 单次 ~200-500ms 可接受。
                self._agent = _laya.load(model_id, device="cpu")
                logger.info(f"[laya] runtime loaded: model={settings.laya_model}")
            except Exception as e:  # pragma: no cover - 依赖/网络/权重故障降级
                logger.error(f"[laya] load failed, disabled: {e}")
                self._available = False
                raise RuntimeError(f"laya load failed: {e}") from e
            return self._agent

    async def predict(self, state: Any, questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """异步推理：包到线程池，避免阻塞事件循环。不可用时抛 RuntimeError 由调用方降级。"""
        if not self.available:
            raise RuntimeError("laya unavailable")
        return await asyncio.to_thread(self._predict_sync, state, questions)

    async def predict_choice(
        self, state: Any, question_key: str, question: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """异步 choice 推理并安全解析返回（label 白名单 + confidence 类型收窄）。

        返回 None 表示不可用/调用失败/返回畸形——调用方应回落 LLM。
        这是唯一应被业务代码调用的异步入口（内部走 to_thread）。

        confidence 语义（实测 laya 0.3.4）：laya 原生的 ``ans["confidence"]`` 是
        **归一化熵置信度**（``1 - H(p)/log(k)``，实测 0.59 即使某类概率已达 0.87），
        不反映"模型对所选类别的把握"。因此这里把 confidence 重定义为
        **所选类别的最大类概率**（阈值判断的自然语义），原熵置信度保留为 entropy_confidence。
        """
        try:
            result = await self.predict(state, {question_key: question})
            ans = result["answers"][question_key]
            label = str(ans["choice"])
            probabilities = ans.get("probabilities", {})
            if not isinstance(probabilities, dict) or not probabilities:
                logger.warning(f"[laya] {question_key} returned empty probabilities")
                return None
            # 最大类概率 = 模型对所选类别的把握（用于预筛阈值判断）
            max_prob = float(max(probabilities.values()))
            # laya 原生熵置信度（保留参考）
            entropy_conf = float(ans.get("confidence", 0.0))
        except Exception as e:
            logger.warning(f"[laya] {question_key} inference failed: {e}")
            return None
        return {
            "label": label,
            "confidence": max_prob,
            "entropy_confidence": entropy_conf,
            "probabilities": probabilities,
        }

    def _predict_sync(self, state: Any, questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        agent = self._load()
        return agent.system_one(state, questions)


# 模块级单例
_runtime: Optional[LayaRuntime] = None


def get_laya_runtime() -> LayaRuntime:
    global _runtime
    if _runtime is None:
        _runtime = LayaRuntime()
    return _runtime


# 情绪三分类候选集（白名单，laya 返回候选外值一律回落 LLM——见 I2 修复）
SENTIMENT_LABELS = {"bullish", "bearish", "neutral"}


async def laya_sentiment_choice(
    headlines: str, symbol: str = "GOLD"
) -> Optional[Dict[str, Any]]:
    """对新闻标题做情绪三分类预筛（choice: bullish/bearish/neutral + 概率 + 置信度）。

    异步：内部走 ``rt.predict``（``asyncio.to_thread``），不阻塞事件循环。
    返回 None 表示 laya 不可用/调用失败/返回畸形——调用方应回落 LLM。
    仅返回离散分类，不生成 score/key_factors（那些仍是 LLM 的地盘）。
    """
    rt = get_laya_runtime()
    if not rt.available:
        return None
    state = {
        "symbol": symbol,
        "headlines": headlines[:3000],  # 截断防超长
    }
    question = {
        "type": "choice",
        "instructions": "What is the market sentiment of these headlines?",
        "criteria": {
            "bullish": "positive outlook, price expected to rise",
            "bearish": "negative outlook, price expected to fall",
            "neutral": "mixed or no clear direction",
        },
    }
    result = await rt.predict_choice(state, "sentiment", question)
    if result is None:
        return None
    # I2：白名单校验——laya 可能返回候选外的畸形标签（如 "positive"/"unknown"），
    # 直接回落 LLM，绝不把未校验 label 传给 score 映射（否则 KeyError 吞掉整个分析）。
    if result["label"] not in SENTIMENT_LABELS:
        logger.warning(f"[laya] sentiment label outside whitelist, falling back to LLM: {result['label']}")
        return None
    return result


# 策略名候选（与 agent_config 抽取对齐：8 个策略 + ai_autonomous 兜底）
STRATEGY_LABELS = {
    "trend_following",
    "mean_reversion",
    "breakout",
    "momentum",
    "hold",
    "ai_autonomous",
}


async def laya_strategy_choice(decision: str) -> Optional[Dict[str, Any]]:
    """从 AI 决策文本抽取策略名（choice：8+1 策略类 + 概率 + 置信度）。

    替换 agent_config 里 `if keyword in text` 子串匹配（顺序敏感、中文/否定误判）。
    返回 None 表示 laya 不可用/调用失败/返回畸形——调用方应回退原关键词匹配。
    """
    rt = get_laya_runtime()
    if not rt.available:
        return None
    state = {"decision": decision[:3000]}
    question = {
        "type": "choice",
        "instructions": "Which trading strategy does this AI decision most clearly describe?",
        "criteria": {
            "trend_following": "riding established trends, EMA crossover, trend continuation",
            "mean_reversion": "buying dips / selling rallies, reverting to average",
            "breakout": "price breaking a range or level, breakout entries",
            "momentum": "momentum / RSI / velocity based entries",
            "hold": "no trade, hold position, wait, stay out",
            "ai_autonomous": "none of the above, autonomous/adaptive decision",
        },
    }
    result = await rt.predict_choice(state, "strategy", question)
    if result is None:
        return None
    if result["label"] not in STRATEGY_LABELS:
        logger.warning(f"[laya] strategy label outside whitelist, falling back to keyword match: {result['label']}")
        return None
    return result

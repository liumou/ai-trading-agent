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
                # 支持 HF 镜像（实测 huggingface.co 模型端点被阻断）
                if not os.environ.get("HF_ENDPOINT"):
                    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
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


def laya_sentiment_choice(
    headlines: str, symbol: str = "GOLD"
) -> Optional[Dict[str, Any]]:
    """对新闻标题做情绪三分类预筛（choice: bullish/bearish/neutral + 概率 + 置信度）。

    返回 None 表示 laya 不可用/调用失败——调用方应回退 LLM。
    仅返回离散分类，不生成 score/key_factors（那些仍是 LLM 的地盘）。
    """
    try:
        rt = get_laya_runtime()
        if not rt.available:
            return None
        state = {
            "symbol": symbol,
            "headlines": headlines[:3000],  # 截断防超长
        }
        questions = {
            "sentiment": {
                "type": "choice",
                "instructions": "What is the market sentiment of these headlines?",
                "criteria": {
                    "bullish": "positive outlook, price expected to rise",
                    "bearish": "negative outlook, price expected to fall",
                    "neutral": "mixed or no clear direction",
                },
            }
        }
        # 同步推理（to_thread 在 predict 内部），此处直接调同步版本以复用单例
        result = rt._predict_sync(state, questions)
        ans = result["answers"]["sentiment"]
        return {
            "label": ans["choice"],
            "confidence": ans["confidence"],
            "probabilities": ans["probabilities"],
        }
    except Exception as e:  # pragma: no cover - 降级路径
        logger.warning(f"[laya] sentiment prefilter failed: {e}")
        return None

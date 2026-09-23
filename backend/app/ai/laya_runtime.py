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
from typing import Any, Collection, Dict, Optional

from loguru import logger

from app.config import settings

class LayaRuntime:
    """Laya 推理运行时单例。懒加载 + 线程安全 + 事件循环友好。"""

    def __init__(self) -> None:
        self._agent: Any = None
        self._laya: Any = None  # 懒加载模块（M3：import 走线程内 _load）
        self._lock = threading.Lock()  # 加载锁（幂等、串行）
        self._predict_lock = threading.Lock()  # 推理锁（M2：共享 Agent 状态串行化）
        self._available: Optional[bool] = None  # None=未探测

    @property
    def available(self) -> bool:
        """laya 是否可用（已启用且未被故障禁用）。

        M3：首次探测不导入（import laya 是重型栈，走线程内 _load），
        依赖缺失/加载失败由 _load 置 available=False。
        """
        if not settings.laya_enabled:
            return False
        if self._available is None:
            self._available = True  # 假定可用，由首次 predict/warmup 实证
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

                # M3：import laya（torch/transformers 栈）在线程内执行（warmup/predict
                # 的 to_thread），绝不阻塞事件循环。
                if self._laya is None:
                    import laya as _laya_mod  # type: ignore  # noqa: PLC0415

                    self._laya = _laya_mod
                # 显式 device="cpu"：避开 OOM 回落路径（Agent.device 可变共享状态），
                # 并保证在无 GPU 的 Railway 上行为确定。CPU 单次 ~200-500ms 可接受。
                self._agent = self._laya.load(model_id, device="cpu")
                logger.info(f"[laya] runtime loaded: model={settings.laya_model}")
            except Exception as e:  # pragma: no cover - 依赖/网络/权重故障降级
                logger.error(f"[laya] load failed, disabled: {e}")
                self._available = False
                raise RuntimeError(f"laya load failed: {e}") from e
            return self._agent

    async def predict(
        self, state: Any, questions: Dict[str, Dict[str, Any]], *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """异步推理：包到线程池，避免阻塞事件循环。不可用时抛 RuntimeError 由调用方降级。

        timeout：秒（可选）。冷加载 ~136s 与异常卡死不得拖垮请求路径——
        传入 wait_for 让调用方能按预算失败并降级（评审 H3）。
        """
        if not self.available:
            raise RuntimeError("laya unavailable")
        coro = asyncio.to_thread(self._predict_sync, state, questions)
        try:
            if timeout is not None:
                coro = asyncio.wait_for(coro, timeout)
            return await coro
        except asyncio.TimeoutError:
            # M2 fail-stop：wait_for 只取消 await，底层线程会继续跑完；
            # 置不可用防止反复超时导致推理线程/队列无限堆积拖垮进程。
            logger.error(
                f"[laya] predict timed out after {timeout}s — marking runtime unavailable "
                "(fail-stop；重启进程后由 warmup 重新加载)"
            )
            self._available = False
            raise

    async def predict_choice(
        self,
        state: Any,
        question_key: str,
        question: Dict[str, Any],
        *,
        allowed_labels: Optional[Collection[str]] = None,
        timeout: Optional[float] = None,
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
            result = await self.predict(state, {question_key: question}, timeout=timeout)
            ans = result["answers"][question_key]
            return self._parse_choice(ans, question_key, allowed_labels=allowed_labels)
        except Exception as e:
            logger.warning(f"[laya] {question_key} inference failed: {e}")
            return None

    async def predict_choices(
        self,
        state: Any,
        questions: Dict[str, Dict[str, Any]],
        *,
        allowed_labels: Optional[Dict[str, Collection[str]]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """单次前向批处理多问 choice（评审 M1：6 问逐个 predict_choice 是 6 次前向 1.2-3s）。

        返回 {question_key: parsed_or_None}——单问畸形/失败只置 None，不影响其他问；
        调用方（收敛器）对畸形问整体 ESCALATE。
        """
        if not self.available:
            raise RuntimeError("laya unavailable")
        result = await self.predict(state, questions, timeout=timeout)
        answers = result.get("answers", {}) if isinstance(result, dict) else {}
        out: Dict[str, Optional[Dict[str, Any]]] = {}
        for key, question in questions.items():
            ans = answers.get(key) if isinstance(answers, dict) else None
            if not isinstance(ans, dict):
                logger.warning(f"[laya] {key} missing answer")
                out[key] = None
                continue
            labels = None
            if allowed_labels:
                labels = allowed_labels.get(key)
            out[key] = self._parse_choice(ans, key, allowed_labels=labels)
        return out

    async def warmup(self, timeout: Optional[float] = None) -> bool:
        """启动预热：冷加载（~136s）不得污染影子基线段（评审 H3/H4）。

        返回是否成功；失败仅记日志（不崩溃），运行期首次 predict 再试。
        """
        try:
            if not self.available:
                return False
            coro = asyncio.to_thread(self._load)
            if timeout is not None:
                coro = asyncio.wait_for(coro, timeout)
            await coro
            logger.info("[laya] warmup complete")
            return True
        except asyncio.TimeoutError:
            # 启动期慢下载：加载线程继续跑（有锁，幂等），不 fail-stop；
            # 运行期 predict 若仍超时则由 predict 的 fail-stop 兜底。
            logger.warning(f"[laya] warmup timed out after {timeout}s (load continues in thread; will retry at predict)")
            return False
        except Exception as e:  # pragma: no cover - 依赖/网络/权重故障降级
            logger.warning(f"[laya] warmup failed (will retry at predict): {e}")
            return False

    @staticmethod
    def _parse_choice(
        ans: Dict[str, Any],
        question_key: str,
        *,
        allowed_labels: Optional[Collection[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """JEV 级 choice 解析与校验（外部评审 H3：对齐 QuantDinger 五项）。

        校验链：label 白名单 → probabilities 键集完整 → sum≈1 → 数值 finite →
        choice==argmax → confidence 收窄。任一失败返回 None（调用方降级）。
        """
        import math

        label = ans.get("choice")
        if label is None:
            logger.warning(f"[laya] {question_key} missing choice")
            return None
        label = str(label)
        if allowed_labels is not None and label not in allowed_labels:
            logger.warning(f"[laya] {question_key} label {label!r} not in allowed set")
            return None

        probabilities = ans.get("probabilities", {})
        if not isinstance(probabilities, dict) or not probabilities:
            logger.warning(f"[laya] {question_key} returned empty probabilities")
            return None
        try:
            probs = {str(k): float(v) for k, v in probabilities.items()}
        except (TypeError, ValueError):
            logger.warning(f"[laya] {question_key} probabilities not numeric")
            return None
        # 键集完整（与 label 候选对齐时）
        if allowed_labels is not None and set(probs.keys()) != set(allowed_labels):
            logger.warning(
                f"[laya] {question_key} probabilities keys {sorted(probs)} != allowed {sorted(allowed_labels)}"
            )
            return None
        # finite + [0,1]
        if any(not math.isfinite(v) or not (0.0 <= v <= 1.0) for v in probs.values()):
            logger.warning(f"[laya] {question_key} probabilities out of range")
            return None
        # sum ≈ 1（容差 1e-2，QuantDinger 用 1e-3；宽松避免 fp 误差）
        if abs(sum(probs.values()) - 1.0) > 1e-2:
            logger.warning(
                f"[laya] {question_key} probabilities sum {sum(probs.values()):.4f} != 1"
            )
            return None

        max_prob = float(max(probs.values()))
        # choice 必须是 argmax（M2 防御）
        choice_prob = float(probs.get(label, 0.0))
        if choice_prob < max_prob - 1e-9:
            logger.warning(
                f"[laya] {question_key} choice {label!r} != argmax (prob {choice_prob:.3f} < {max_prob:.3f}), "
                f"treating as low-confidence"
            )
            return None
        # 原生熵置信度（保留参考，非判定依据）
        try:
            entropy_conf = float(ans.get("confidence", 0.0))
        except (TypeError, ValueError):
            entropy_conf = 0.0
        return {
            "label": label,
            "confidence": max_prob,
            "entropy_confidence": entropy_conf,
            "probabilities": probs,
        }

    def _predict_sync(self, state: Any, questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        agent = self._load()
        # M2：Agent.device/dtype 是可变共享状态（docstring 自认），推理必须串行化，
        # 防并发 system_one 数据竞争与 OOM 回落竞态。
        with self._predict_lock:
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


# 策略名候选（I4 修复：此前含 "momentum"，注册表里是 "momentum_rank"，一旦
# strategy_switch 接上会 raise ValueError("Unknown strategy: momentum") → 改 momentum_rank。
# 注意保留 trend_following：它是 keyword 兜底链路的既有契约名（AI 决策文本常说
# "Trend Following"，test_llm_lang 断言它），laya criteria 同样建模此类——虽然
# STRATEGIES 实现注册表里没有同名策略，但 strategy_used 仅用于展示（scheduler.py），
# 不 resolve 到实现。两处白名单（STRATEGY_LABELS / _STRATEGY_KEYWORDS）必须一致。）
# 注意：这是策略名的**子集**（覆盖 AI 决策文本常出现的 6 类 + ai_autonomous 兜底），
# 非全部注册策略——laya criteria 只建模这几类，其余归 ai_autonomous。
STRATEGY_LABELS = {
    "trend_following",
    "mean_reversion",
    "breakout",
    "momentum_rank",
    "hold",
    "ai_autonomous",
}


async def laya_strategy_choice(decision: str, confidence_threshold: float | None = None) -> Optional[Dict[str, Any]]:
    """从 AI 决策文本抽取策略名（choice：策略类 + 概率 + 置信度）。

    替换 agent_config 里 `if keyword in text` 子串匹配（顺序敏感、中文/否定误判）。
    返回 None 表示 laya 不可用/调用失败/返回畸形/低置信——调用方应回退原关键词匹配。

    I3 修复：默认对结果施加置信阈值（config.laya_strategy_confidence_threshold）。
    否则 laya 最低置信的猜测会覆盖 keyword 兜底（后者在关键词命中时是近确定性信号）。
    """
    rt = get_laya_runtime()
    if not rt.available:
        return None
    if confidence_threshold is None:
        confidence_threshold = settings.laya_strategy_confidence_threshold
    state = {"decision": decision[:3000]}
    question = {
        "type": "choice",
        "instructions": "Which trading strategy does this AI decision most clearly describe?",
        "criteria": {
            "trend_following": "riding established trends, EMA crossover, trend continuation",
            "mean_reversion": "buying dips / selling rallies, reverting to average",
            "breakout": "price breaking a range or level, breakout entries",
            "momentum_rank": "momentum / RSI / velocity / relative-strength based entries",
            "hold": "explicitly no trade, hold position, wait, stay out",
            "ai_autonomous": "none of the above, autonomous/adaptive decision",
        },
    }
    result = await rt.predict_choice(state, "strategy", question)
    if result is None:
        return None
    if result["label"] not in STRATEGY_LABELS:
        logger.warning(f"[laya] strategy label outside whitelist, falling back to keyword match: {result['label']}")
        return None
    if result["confidence"] < confidence_threshold:
        logger.debug(
            f"[laya] strategy label {result['label']} below threshold "
            f"({result['confidence']:.3f} < {confidence_threshold}) → keyword fallback"
        )
        return None
    return result

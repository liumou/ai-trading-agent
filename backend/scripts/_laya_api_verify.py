"""真实 laya API 验证脚本（Phase 4b，独立于 pytest/conftest）。

用真实 laya 模型验证 app.ai.laya_runtime 的解析与真实返回结构兼容。
隔离 venv 运行（有 laya + 权重缓存），绕过项目 conftest 依赖。

用法：
  cd backend && PYTHONPATH=. HF_ENDPOINT=https://hf-mirror.com \
    /tmp/laya-api-venv/bin/python scripts/_laya_api_verify.py
"""
import asyncio
import os
import sys

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("USE_TF", "0")

# 项目根（backend/）已在 cwd，确保能 import app.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    from app.ai.laya_runtime import LayaRuntime, laya_sentiment_choice
    from app.ai.laya_runtime import SENTIMENT_LABELS

    import laya
    from huggingface_hub import snapshot_download

    print("[verify] loading real laya model (cached weights)...")
    cache_dir = snapshot_download("convaiinnovations/laya", local_files_only=True)
    rt = LayaRuntime()
    rt._agent = laya.load(cache_dir, device="cpu")
    # 绕过 available 只读 property + 默认关闭：直接替换模块单例并启用
    import app.ai.laya_runtime as laya_mod

    laya_mod._runtime = rt
    laya_mod.settings.laya_enabled = True

    headlines = (
        "1. Gold plunges on strong dollar\n"
        "2. Fed signals aggressive rate hike\n"
        "3. Safe-haven demand collapses"
    )
    result = await laya_sentiment_choice(headlines, symbol="GOLD")

    print(f"\n[verify] result = {result}")
    assert result is not None, "laya_sentiment_choice 返回 None（真实模型不应失败）"
    assert result["label"] in SENTIMENT_LABELS, f"label 不在白名单: {result['label']}"
    assert set(result["probabilities"].keys()) == SENTIMENT_LABELS
    assert 0.0 <= result["confidence"] <= 1.0
    # confidence 是最大类概率
    assert result["confidence"] == max(result["probabilities"].values()), (
        f"confidence({result['confidence']}) != max_prob({max(result['probabilities'].values())})"
    )
    print(f"[verify] label={result['label']} max_prob={result['confidence']:.4f}")
    print(f"[verify] probabilities={result['probabilities']}")
    print("[verify] OK: 真实 laya API 与 app.ai.laya_runtime 解析完全兼容")


if __name__ == "__main__":
    asyncio.run(main())

FROM python:3.12-slim

# System deps + Node.js (required by claude-agent-sdk CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates libgomp1 && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
# 先装 CPU-only torch，防止 `pip install laya` 拉 CUDA 版 torch (~2GB+)。
# 集成了 laya 后镜像会显著增大（CPU torch ~170MB + 权重）。安装失败不阻断构建：
# laya 在运行时 try-import 降级，ws 照常启动（缺依赖时情绪仍走 LLM）。
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    || echo "[warn] CPU torch install failed; laya will be unavailable (try-import fallback)"
RUN pip install --no-cache-dir -r requirements.txt

# --- Laya 权重预缓存（可选，默认 off）--------------------------------------
# 构建时拉权重进镜像，避免每次冷启动从 HF 下载（~1.7GB）。用法：
#   docker build --build-arg PRELOAD_LAYA=1 .
# 运行时设 LAYA_MODEL_CACHE_DIR=/app/models/laya 指向缓存；默认走 HF_ENDPOINT 镜像。
ARG PRELOAD_LAYA=0
ARG HF_ENDPOINT=https://hf-mirror.com
RUN if [ "$PRELOAD_LAYA" = "1" ]; then \
        HF_ENDPOINT=${HF_ENDPOINT} HF_HUB_ENABLE_HF_TRANSFER=1 \
        huggingface-cli download convaiinnovations/laya --local-dir /app/models/laya --local-dir-use-symlinks False; \
    else \
        mkdir -p /app/models/laya && echo "[skip] PRELOAD_LAYA not set; laya weights download at runtime"; \
    fi

# Install Claude CLI globally (needed by claude-agent-sdk)
RUN npm install -g @anthropic-ai/claude-code@latest

# Copy application code
COPY . .

# Create non-root user (fixes: --dangerously-skip-permissions cannot be used with root)
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Claude CLI needs a writable home for config
ENV HOME=/home/appuser
ENV PYTHONPATH=/app

EXPOSE 8080

CMD ["sh", "-c", "echo '[migration] start' && timeout 120 alembic upgrade head 2>&1 && echo '[migration] ok' && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-keep-alive 30 --proxy-headers --forwarded-allow-ips=*"]

# 部署说明：laya 影子观测上线（2026-09-22）

状态：**开关已打开 + 数据库表已建**，只差运行环境生效（重建/重启容器）。

## 已完成
- [x] 代码默认 `laya_gate_engine_shadow=True`（backend/app/config.py）
- [x] `.env` 已加：`LAYA_ENABLED=true` / `LAYA_GATE_SHADOW=true` /
      `LAYA_GATE_ENGINE_SHADOW=true` / `LAYA_HF_ENDPOINT=https://hf-mirror.com`
- [x] 数据库迁移已执行并验证（线上库 100.72.200.33:15432/mt5）：
      `alembic upgrade head` 应用了 2 个迁移
      - `b0c1d2e3f4a5` → `manual_shadow_reviews`（Phase 3.3，手动通道影子）
      - `c2d3e4f5a6b7` → `laya_engine_observations`（Phase 4，engine 观测）
      当前版本 `c2d3e4f5a6b7`（head），两表结构与列已验证。

## 部署侧待做（运行环境的动作）
1. **重建/重启运行容器**（Docker 构建会按 `requirements.txt` 装 `laya==0.3.4`；
   若手动部署则确保运行环境 pip 装了 laya/torch/transformers）。
2. **模型权重首次下载**：第一次触发观测时 laya 从 HF 下载（约 1.7GB，冷加载 ~2 分钟）。
   国内网络走 `LAYA_HF_ENDPOINT=https://hf-mirror.com`（已在 .env）。
   生产可用 `LAYA_MODEL_CACHE_DIR` 指向构建期预缓存的权重目录，避免运行期下载。
3. **确认 .env 生效**：重启后日志应出现
   `[laya] runtime loaded: model=convaiinnovations/laya`（首次 warmup）。
   若出现 `[laya] load failed` 只影响观测，不影响交易（try-import 降级）。

## 观测期行为（无行为变更）
- 手动下单：laya 与 LLM 并行判断，只记录（manual_shadow_reviews）。
- 自动开仓：每次许可检查 laya 看同一份状态，只记录（laya_engine_observations）。
- 不拦单、不推送、不参与 enforce；laya 故障只留痕 UNAVAILABLE。

## Kill switch（任何异常立即止血）
- 把 `.env` 的 `LAYA_GATE_ENGINE_SHADOW`（自动）和/或 `LAYA_GATE_SHADOW`（手动）改回 `false`，重启即可。
- `laya_enabled=false` 也可整体关闭 laya 运行时。

## 观测期结束
- 满 4 周或 `laya_engine_observations` ≥150 条 → 跑报表：
  ```bash
  cd backend
  .venv/bin/python scripts/laya_engine_report.py --days 28
  .venv/bin/python scripts/laya_gate_report.py --days 28   # 手动通道一致率
  ```
- 分歧报告再决定：收敛为 veto-only 收紧层（需再审批）/ 终止 / 转 Phase 5 校准。

## 备注
- 本地测试 venv 未装 laya/torch（装它需 ~2GB+，且运行环境是 Docker）；本地跑测试不受影响。
- 数据库无连接时观测落库静默失败（best-effort 只记日志），不阻塞交易；本次已建表。

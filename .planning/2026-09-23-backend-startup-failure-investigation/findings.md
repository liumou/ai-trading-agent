# Findings — 后端启动失败诊断

## 根因（已 100% 确认）

**后端无法启动的唯一根因**：`.env` 文件（本地，未被 git 跟踪）中的 4 个 `LAYA_*` 环境变量在 `Settings` 模型中未定义，而 `pydantic-settings.BaseSettings` 默认 `extra="forbid"`，导致 `Settings()` 实例化时抛 `ValidationError` → `app.config` 导入失败 → `app.main` 导入失败 → 后端无法启动。

### 报错内容（复现）

```
pydantic_core._pydantic_core.ValidationError: 4 validation errors for Settings
laya_enabled           Extra inputs are not permitted [type=extra_forbidden]
laya_gate_shadow       Extra inputs are not permitted [type=extra_forbidden]
laya_gate_engine_shadow  Extra inputs are not permitted [type=extra_forbidden]
laya_hf_endpoint       Extra inputs are not permitted [type=extra_forbidden]
```

### 关键证据

| 证据 | 详情 |
|------|------|
| `.env` 中 LAYA 变量 | 第 90-94 行，4 个：`LAYA_ENABLED=true`、`LAYA_GATE_SHADOW=true`、`LAYA_GATE_ENGINE_SHADOW=true`、`LAYA_HF_ENDPOINT=https://hf-mirror.com`，注释标注「2026-09-22 用户批准打开：Phase 3/4 影子观测」 |
| `.env` mtime | 2026-09-22 06:59 |
| Settings 类 | `app/config.py:157` `class Settings(BaseSettings)`，第 424 行 `model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}` — **无 extra="ignore"**，默认 forbid |
| Settings 字段 | 无任何 `laya_*` 字段 |
| 代码库 LAYA 引用 | **零引用**。`app/ai/` 下无 `laya_runtime.py`；grep `app/ mcp_server/ tests/ scripts/` 全部无 LAYA |
| LAYA 文件残留 | 仅 `models/laya_ft`（1.6GB 模型）、`scripts/__pycache__/*.pyc`、`.omc` 记忆、历史 commit（`52922a0`）中的规划文档 |
| 当前代码库 | 无 `.planning/*laya*` 目录（已被清理），无 LAYA 源码 |

### LAYA 功能状态

LAYA 是一个"判定引擎"（可能是本地大模型判定引擎），曾在 2026-09-22 有过完整的开发计划（`.planning/2026-09-22-laya-*`），包含 `app/ai/laya_runtime.py`、`scripts/laya_synth_baseline.py`、`tests/unit/test_laya_runtime.py` 等文件，以及微调模型 `models/laya_ft`。**但这些源码当前都不存在** —— 功能已被移除/回滚，只剩 `.env` 配置残留，导致启动失败。

## 修复方案选项

### 方案 A：从 `.env` 移除 LAYA 配置（推荐）
- 从 `backend/.env` 删除第 90-94 行（注释 + 4 个变量）
- 优点：干净、符合"LAYA 功能已废弃"的现状
- 缺点：无

### 方案 B：在 `Settings` 中容忍 extra 变量
- 改 `model_config` 为 `extra="ignore"`
- 优点：未来新增未定义 env 变量不炸
- 缺点：掩盖配置错误（拼写错误的变量名静默忽略），违背项目"fail-closed"风格；`extra="ignore"` 会忽略所有未知变量，包括误配置

### 方案 C：在 `Settings` 中声明 `laya_*` 字段
- 在 `Settings` 类中显式添加 4 个 `laya_*: bool/str = False/""` 字段
- 优点：保留 LAYA 配置，未来可恢复功能
- 缺点：LAYA 源码不存在，声明无用字段是死代码；且若未来真启用 LAYA，还需要 `laya_runtime.py` 才能工作

**推荐方案 A**。理由：LAYA 源码已不存在，配置项属于"孤儿配置"；从 `.env` 移除是最直接、最小、最符合当前代码库状态的修复。同时不需要改动 `config.py`（保持 `extra="forbid"` 的 fail-closed 风格）。

### 额外检查
- 扫描确认 `.env` 中仅这 4 个未匹配变量，无其他潜在启动失败点。
- `.env` 未被 git 跟踪（`git ls-files backend/.env` 为空），改动只影响本地，不会污染仓库。
- 生产（Railway）环境变量由 `railway vars` 管理，与本任务无关；但建议用户检查 Railway 上是否有 `LAYA_*`（若有同样需要移除）。

## 修复执行与验证（2026-09-23）

### 已执行
- 删除 `backend/.env` 第 90-94 行（LAYA 注释 + 4 个变量）
- `.env` 中 LAYA 残留：0
- `import app.main` 成功（无 ValidationError）
- `from app.config import settings` 正常（llm_provider=openai_compat）
- `git diff` 仅 `.planning/.active_plan` 变更（规划指针），无意外跟踪文件改动

### 全量测试发现：9 个预先存在的失败（与本次改动无关）

跑 `pytest tests/ -q --no-cov` 有 9 个失败，但证据表明**全部是预先存在的测试隔离/环境问题**：

| 失败测试 | 失败原因 | 与 LAYA 改动的关系 |
|----------|---------|-------------------|
| `test_multi_agent.py::TestModelSelection`（4个） | 硬编码断言 `claude-haiku-4-5-20251001` 等，但 `.env` 配置 `MODEL_SPECIALIST=glm-5.3-flash` / `MODEL_ORCHESTRATOR=deepseek-v4-flash` | 无关。测试假设干净默认，读到本地 env 覆盖值 |
| `test_multi_agent.py::TestBaseAgentLoop`（2个） | 真实 LLM 调用（ARK API）超时/限流 | 无关。环境网络问题 |
| `test_backtest_consistency.py::test_gold_reads_profile` | `assert 5.0 == 2.0`：SYMBOL_PROFILES["GOLD"]["tp_atr_mult"] 被前置测试污染 | 无关。SYMBOL_PROFILES 是代码常量，不读 .env |
| `test_ml_barrier_validation.py::test_trainer_uses_shared_band` | `ImportError: cannot load module more than once per process` | 无关。pytest 模块加载隔离问题 |

**决定性证据**：
1. `test_multi_agent.py` 在**完全干净环境**（`env -i`，无 `.env`）下跑，仍 7 个失败 → 与 `.env` 无关
2. `SYMBOL_PROFILES` 是 `app/config.py` 代码硬编码，不读 `.env` → 移除 LAYA 不影响
3. 失败清单**不稳定**（第一次 -x 停 1 个、第二次 9 个、干净 env 7 个）→ 典型的全量测试顺序/共享状态污染

这些属于本项目**预先存在的测试卫生问题**（可能在近期多次新增功能后积累），与本次启动失败修复无关，不阻塞交付。

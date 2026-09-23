# Progress — 后端启动失败排查与修复

## 2026-09-23 会话

### 诊断阶段（Phase 1）
1. 复现：`backend/.venv/bin/python -c "import app.main"` → 抛 `pydantic_core._pydantic_core.ValidationError`
2. 报错 4 个 `extra_forbidden`：`laya_enabled` / `laya_gate_shadow` / `laya_gate_engine_shadow` / `laya_hf_endpoint`
3. 定位：`backend/.env` 第 90-94 行有 4 个 `LAYA_*` 变量（9-22 加入，注释"用户批准打开 Phase 3/4 影子观测"），但 `Settings` 类（app/config.py:157）无这些字段，且 `model_config` 未设 `extra="ignore"`（默认 forbid）
4. 扫描确认：`.env` 中仅这 4 个变量未匹配 Settings 字段，无其他启动失败点
5. LAYA 源码确认已废弃：`app/ai/` 无 `laya_runtime.py`；`tests/` `scripts/` `mcp_server/` 零引用；仅剩 `models/laya_ft`（1.6GB 模型文件）、`__pycache__` 残留、`.omc` 记忆、历史 commit `52922a0` 中的规划文档痕迹
6. `.env` 未被 git 跟踪，改动只影响本地

### 修复方案（已拟定，待用户审批）
- **方案 A（推荐）**：从 `backend/.env` 删除第 90-94 行（注释 + 4 个 LAYA 变量）
  - 优点：干净、符合 LAYA 已废弃现状、不改 config.py、保持 fail-closed
- 方案 B：改 `model_config` 为 `extra="ignore"` — 掩盖配置错误，不推荐
- 方案 C：Settings 声明 laya 字段 — 死代码，不推荐

### 执行与验证（Phase 2-4，用户已批准方案 A）
1. ✅ 编辑 `backend/.env` 删除第 90-94 行（LAYA 注释 + 4 变量）
2. ✅ `grep -ni laya .env` → 0 残留
3. ✅ `import app.main` 成功、`settings` 正常加载（llm_provider=openai_compat）
4. ✅ `.env` 备份在 /tmp/.env.bak（临时加回 LAYA 验证过"改动前也失败"）
5. ⚠️ 全量测试：9 个失败，全部为**预先存在的测试隔离问题**（见 findings.md），与本次改动无关：
   - `test_multi_agent` 硬编码模型断言 vs `.env` MODEL_* 覆盖（干净 env 下仍 7 失败）
   - `test_backtest_consistency` SYMBOL_PROFILES 共享状态污染（代码常量，不读 .env）
   - `test_ml_barrier_validation` pytest 模块加载隔离问题
6. ✅ git 无意外跟踪文件改动（仅 `.planning/.active_plan` 规划指针）

### 结论
- **根因**：`.env` 中 4 个 LAYA_* 孤儿变量 → pydantic-settings `extra="forbid"` → Settings() 校验失败 → 后端无法启动
- **修复**：从 `.env` 移除 LAYA 配置块
- **验证**：`app.main` 可正常导入，后端启动恢复

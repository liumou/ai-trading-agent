# Task Plan: 后端启动失败排查与修复

## Goal
修复后端无法启动的问题（根因：`.env` 中 4 个未定义的 `LAYA_*` 变量触发 pydantic-settings `extra="forbid"` 校验失败），恢复 `app.main` 正常导入，后端可正常启动。

## Next Step
已完成 —— 交付修复说明给用户

## Current Phase
Phase 4（交付）

## Phases

### Phase 1: 诊断根因 ✅
- [x] 复现启动失败（`.venv/bin/python -c "import app.main"` 抛 ValidationError）
- [x] 定位根因（`.env` 中 `LAYA_*` 4 个变量未在 Settings 定义，extra=forbid）
- [x] 扫描确认仅 4 个未匹配变量，无其他启动失败点
- [x] 确认 LAYA 源码已废弃（无 `laya_runtime.py`，无文档，仅模型残留）
- [x] 记录完整证据到 findings.md
- **Status:** complete

### Phase 2: 执行修复（从 `.env` 移除 LAYA 配置）✅
- [x] 备份 `.env` 到 /tmp/.env.bak
- [x] 编辑 `backend/.env`，删除第 90-94 行（LAYA 注释块 + 4 个变量）
- [x] `grep -ni "laya" .env` 验证 0 残留
- **Status:** complete

### Phase 3: 验证修复 ✅
- [x] `cd backend && .venv/bin/python -c "import app.main"` 成功导入（无 ValidationError）
- [x] `cd backend && .venv/bin/python -c "from app.config import settings"` 正常（llm_provider=openai_compat）
- [x] 确认 `.env` 恢复、git 无意外跟踪文件改动
- [x] 跑全量测试——发现 9 个预先存在的失败（见 findings.md），与本次改动无关
- **Status:** complete

### Phase 4: 交付 ✅
- [x] 更新 findings.md / task_plan.md 记录修复结果
- [x] 向用户交付修复说明（含 Railway 环境变量提醒）
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 方案 A：从 `.env` 移除 LAYA 配置（而非改 config.py extra="ignore"） | LAYA 源码已废弃，配置是孤儿配置；保持 `extra="forbid"` 的 fail-closed 风格不掩盖未来配置错误 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| pydantic ValidationError: 4 extra_forbidden（laya_*） | 从 `.env` 移除 LAYA 变量（方案 A） |
| 全量测试 9 个失败（test_multi_agent / test_backtest_consistency / test_ml_barrier_validation） | 确认与本次改动无关：`SYMBOL_PROFILES` 是代码常量不读 .env；干净 env 下 test_multi_agent 仍 7 失败；属预先存在的测试隔离问题 |

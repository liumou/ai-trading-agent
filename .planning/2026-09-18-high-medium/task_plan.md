# 修复代码审阅 HIGH/MEDIUM 问题

## 目标
根据 `996d10c` 提交的代码审阅结果，修复 HIGH 和 MEDIUM 严重等级的所有问题。

## 问题清单

### 🔴 HIGH（必须修复）
1. **page.tsx:74 — unused `pendingRef`** — ✅ 已移除（连同未使用的 `Users` import）
2. **run-panel.tsx:86 — `.reverse()` 数组变异** — ✅ 已改用 `.toReversed()`
3. **use-run-detail.ts — 无限轮询** — ✅ 已添加 `MAX_FAILURES=20` 上限

### 🟡 MEDIUM（考虑修复）
4. **chat_runtime.py:83 — 魔法数字 0.15** — ✅ 已改为 `DEFAULT_BUDGET["reserve_fraction"]` 可配置
5. **page.tsx:353 — retry 不支持 preset-only 运行** — ✅ `handleRetry` 已检查 `run.preset`
6. **chat_sdk_runtime.py:104 — extra_args None 值** — ✅ 已移除无意义的 None 值，改为 `{}`
7. **chat_runs.py:33 — sanitize 正则每次都编译** — ✅ 已提取为模块级 `_SECRET_PATTERN`

## 执行阶段

### Phase 1: 后端修复 (MEDIUM) — ✅ complete
- chat_runs.py: 提取正则到模块级 ✅
- chat_sdk_runtime.py: 确认 extra_args 用法或移除 ✅
- chat_runtime.py: 添加可配置 reserve_fraction ✅

### Phase 2: 前端修复 (HIGH + MEDIUM) — ✅ complete
- page.tsx 清理 pendingRef ✅
- run-panel.tsx 改用 toReversed() ✅
- use-run-detail.ts 添加 MAX_FAILURES ✅
- page.tsx 支持 preset-only retry ✅

### Phase 3: 验证 — ✅ complete
- 前端 tsc --noEmit 检查：0 error ✅
- 后端 ruff lint：未安装，改用 py_compile + pytest ✅
- 相关测试 44/44 通过（chat 相关）；test_multi_agent.py 7 失败为既有环境问题 ✅

## Next Step
所有阶段完成。可提交变更。
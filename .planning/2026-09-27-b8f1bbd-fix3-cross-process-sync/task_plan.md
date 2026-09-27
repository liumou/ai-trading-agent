# Cross-Process Sync — SYMBOL_PROFILES_DB_SYNCED (Phase 3, Problem 3)

> 从 `2026-09-27-code-review-b8f1bbd-fixes`（commit b8f1bbd 审查修复）拆分的独立任务。原相位 Phase 3 问题 3。

## Goal

解决 `config.SYMBOL_PROFILES_DB_SYNCED` 跨进程一致性问题：主进程（lifespan）与 MCP server stdio 子进程 / agent runner（Docker sandbox）各自持有独立的模块级 `SYMBOL_PROFILES_DB_SYNCED` 副本。主进程切号后更新主进程标记，子进程标记不变 → 子进程的兜底逻辑（如 reload_engines 若存在）仍可能启用静态兜底。

## Background（来自评审 findings.md）

- 根因：`SYMBOL_PROFILES_DB_SYNCED` 是 `global` 重新绑定的不可变 bool（config.py:134→143），`from X import Y` 值绑定 + global 可变状态。
- 相关修复：manager.py 已改为 `app_config.SYMBOL_PROFILES_DB_SYNCED` 模块属性访问（修复 1，已 closed）。
- 评审提出的候选方案（findings.md 问题 3）：
  - A. Redis 存 `symbol_profiles:synced:{account_login}` 标记，子进程启动时读取
  - B. 子进程启动时强制 `load_profiles_into_memory()` 自检（agent_entrypoint 已是此行为，MCP server stdio 需确认）
  - C. 接受现状（子进程不跑 reload_engines，只读 SYMBOL_PROFILES 内存映射）

## Phases

### Phase 1: 调研子进程实际行为 — `Status: complete`
调研证据（2026-09-27）：
- **MCP server stdio 子进程（app/mcp_server/）**：grep 零引用 `load_profiles_into_memory` / `load_profiles_from_db` / `apply_db_symbol_profiles` / `reload_engines` / `SYMBOL_PROFILES_DB_SYNCED` / `SYMBOL_PROFILES` / `to_broker_alias` —— **MCP server 完全不加载 DB profiles，不做兜底引擎决策，甚至不访问进程内 SYMBOL_PROFILES dict**。
- **agent runner（app/runner/agent_entrypoint.py:203-219）**：启动时调用 `load_profiles_into_memory()` 自检。但 agent 镜像刻意不打 app/db、不注入 DATABASE_URL（避免 runner 直连主库）→ import 必 ImportError → 退化告警（行 216-217）。别名映射闭合方案是 backend 经白名单 env 注入（manager._llm_runner_env），**不是**子进程自加载。
- **SYMBOL_PROFILES_DB_SYNCED 消费面**：仅主进程 BotManager（manager.py:73,320 `app_config.SYMBOL_PROFILES_DB_SYNCED`）。子进程从不读它。

### Phase 2: 方案选型与实施 — `Status: complete`
- **定稿方案 C（接受现状）+ 记录**：
  - 评审判断"若子进程只读内存映射、从不做兜底引擎决策，则问题 3 是理论风险"——**已实证成立**。
  - MCP server 不碰 SYMBOL_PROFILES_DB_SYNCED；agent runner 有独立自检（虽因镜像无 DB 退化，但那是设计意图：别名映射经 env 注入闭合）。
  - `SYMBOL_PROFILES_DB_SYNCED` 只被主进程消费，跨进程一致性当前**不是实际问题**。
- 无代码修改。不实施 Redis 标记（方案 A）或子进程自检（方案 B）——无消费方，属过度设计。

### Phase 3: 验证与回归 — `Status: complete`
- 结论：无代码变更，TestReloadEnginesFallback 不受影响（该测试只验证主进程内行为，Pass 状态已于修复 1 验证）。

## Errors Encountered

（无）

## Next Step

Task 完成（方案 C 定稿，无代码修改）。问题 3 → 记录为"理论风险，接受现状"。
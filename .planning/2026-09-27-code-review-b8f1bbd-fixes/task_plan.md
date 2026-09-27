# Code Review Fixes — commit b8f1bbd

## Goal

针对 commit `b8f1bbd`（实际为 MT5 账号隔离功能，944 行变更）的代码审查发现，落地可执行的修复项。OCR 工具因 LLM 后端 HTTP 404 不可用，已由人工完成专业审查。

本次范围：**仅输出计划文档，不执行修复**（用户明确选择）。

## Context

- 被审查提交：`b8f1bbd docs(findings): 添加黄金价格提醒调查分析文档`（commit message 与实际内容严重不符）
- 实际变更：symbol_configs 按 MT5 account_login 隔离（Alembic 迁移 + model + service + routes + scheduler + manager + main.py + connector 语义变更 + 前端切号广播）
- 测试：新增 `test_symbol_account_isolation.py`（322 行）
- 相关既有计划：`.planning/2026-09-26-mt5/`（本提交所属功能任务）
- **多路评审已执行（2026-09-27 Session 2）**：3 代理（critic 完整性 / architect 技术正确性 / verifier 验证策略）并行评审，关键结论已并入本计划。

## Findings Summary（详见 findings.md）

经验证的 8 个问题（5 HIGH + 3 LOW）+ 多路评审版修正：

| # | 严重度 | 问题 | 多路评审后处置 |
|---|--------|------|----------------|
| 1 | HIGH | commit message 误导（docs vs 实际 944 行核心代码） | 可 amend（main 未 push ahead 1），需用户决策 |
| 2 | HIGH | manager.py 行内 import——**非纯风格问题，是延迟绑定机制** | ✅ 正确修法：`import app.config as cfg` + `cfg.SYMBOL_PROFILES_DB_SYNCED`（原"顶部 from-import"方案被三方否决，会产生值绑定快照回归） |
| 3 | HIGH | config.py 用模块级全局 `SYMBOL_PROFILES_DB_SYNCED` 作可变状态，跨进程不同步 | 根因=值绑定+global 可变状态；Phase 3 架构权衡 |
| 4 | HIGH | 测试未覆盖 `list_configs(include_disabled=False)` 分支 | ✅ 可立即修（补 3 用例：过滤/跨账号/显式传参） |
| 5 | HIGH | Alembic 迁移与 main.py lifespan `schema_stmts` DDL 双写漂移风险 | Phase 3 架构权衡 |
| 6 | HIGH | broker-catalog `except Exception` 过宽，DB 挂掉时所有账号退化共用 `'0'` key 串目录 | Phase 3 架构权衡（收窄异常边界需限定范围） |
| 7 | ~~HIGH~~ | ~~AppShell useEffect 可能反复重订阅~~ → **前提证伪，无此问题** | **从 Phase 2 移除**，记入"已验证非问题"（subscribe 为 useCallback 稳定引用） |
| 8 | LOW | get_symbol_spec 契约变更未文档化 | 仓库无 CHANGELOG；落点改为 `docs/SYMBOL-PARAMETERS-TECH.md` 或新建 CHANGELOG（用户决策） |

## 多路评审已确认的技术事实（三方一致）

1. **`SYMBOL_PROFILES_DB_SYNCED` 是 `global` 重新绑定的不可变 bool**（config.py:134 False → apply 置 True）。`from X import Y` 是值绑定（快照），顶部 import 只绑定加载时 False → 之后 manager 永远走静态兜底 → **回归切号断链 bug** + `test_no_fallback_when_db_synced` 必红。
2. **当前行内 import 是刻意为之的延迟绑定**：函数内 import 每次调用取模块最新值，语义正确。上提须改为模块属性访问 `cfg.SYMBOL_PROFILES_DB_SYNCED`（引用绑定取值动态）。
3. **`subscribe` 为 `useCallback([],)` 稳定引用**（websocket.ts:189）、zustand action 稳定、同通道替换语义 —— AppShell 不存在重订阅问题。
4. **环境阻塞**：`ruff` 当前未安装；前端零自动化测试框架（无 vitest/jest）。
5. **`list_configs` 当前覆盖率 48%**，line 81（include_disabled=False 分支）未覆盖；目标是**函数级**覆盖而非整文件。

## 已验证非问题（含多路评审新增）

- 迁移 `down_revision` 链正确（`f0e1d2c3b4a5` 存在，`c1d2e3f4a5b6` 接续）
- lifespan 顺序正确（`load_profiles_into_memory` 在 `BotManager` 构造前）
- `_require_config` 经 `get_current_account_login` 自动限账号，update/delete/toggle/validate 均正确隔离
- `get_symbol_spec` 仅 2 处调用方（symbols.py:1001、symbol_validation.py:63），均传券商名，语义变更安全
- `agent_entrypoint` 的 `_loaded` 返回值语义虽偏移但行为更安全（空账号 → 回退静态默认），非 bug
- **[评审新增] AppShell useEffect 无重订阅问题**：subscribe 为 useCallback([],) 稳定引用（websocket.ts:189）、setSymbols/resetAccountScopedData 为 zustand 稳定 action、subscribe 自带同通道替换语义（websocket.ts:191-193）——原 HIGH 问题 7 关闭。

## Phases

### Phase 1: 计划文档输出（含多路评审）— `Status: complete`
- [x] 初始化 planning 目录 `2026-09-27-code-review-b8f1bbd-fixes`
- [x] 写入 task_plan.md / findings.md / progress.md
- [x] 8 个发现经验证（grep + Read 确认调用方、迁移链、lifespan 顺序）
- [x] 多路评审：3 代理并行评审，得到修复 1 值绑定陷阱 CRITICAL、修复 3 无效项、修复 8 落点不存在、环境阻塞等结论

### Phase 2: 待用户确认是否执行修复 — `Status: in_progress`
用户已于 2026-09-27 批准执行 Phase 2 修订后的 2 项修复（修复 1 正确修法 + 修复 3 个测试用例）。问题 1 amend 与 Phase 3 拆分仍待用户决策。按以下修订后的顺序执行（**注意：修复 1 已从"最低风险"改为"需谨慎处理"，风险分级经评审纠正**）：

1. ✅ **manager.py 延迟绑定修复**（问题 2，评审后正确修法）— `Status: complete`
   - ❌ 原方案（顶部 `from app.config import SYMBOL_PROFILES_DB_SYNCED`）**已废弃**——值绑定快照会回归切号断链 bug，且 `test_no_fallback_when_db_synced` 必红。
   - ✅ 已实施：`manager.py:13` 新增 `from app import config as app_config`（模块引用，运行时读最新绑定）；删除行内 import（原 71、318）；两处兜底判断改 `app_config.SYMBOL_PROFILES_DB_SYNCED`（行 73、320）。
   - 验证通过（2026-09-27）：
     - `import app.bot.manager` 冒烟 OK（无循环 import）
     - `TestReloadEnginesFallback` 两用例仍绿（`test_no_fallback_when_db_synced` 未回归，评审强调的行为不变证明）
     - 更宽回归集 53 passed
   - code-reviewer 复核：**APPROVE**（0 CRITICAL/0 HIGH/0 MEDIUM，2 LOW 已修——行 13 从 128→107 字符；确认快照失效路径已切断：`app_config.X` 实时读 vs from-import 快照对照实验）
   - 复核新发现（供参考）：生产启动路径 `load_profiles_into_memory()` 先于 `BotManager()`，原行内 import 启动场景读到正确值；快照 bug 只在二次构建路径出现 → 本修复属"修复潜在缺陷"，仍是正确防御。

2. ✅ **补 list_configs 测试**（问题 4）— `Status: complete`
   - 已实施：`tests/unit/test_symbol_account_isolation.py::TestScopedQueries` 新增 3 用例：
     1. `test_list_filters_disabled_when_requested` —— `include_disabled=False` 只返回启用品种
     2. `test_list_disabled_filter_scoped_to_account` —— 与账号过滤叠加，222 启用品种不串入
     3. `test_list_explicit_account_login_bypasses_active_resolution` —— 显式传参不解析活跃账号
   - 验证通过（2026-09-27）：
     - 完整 `test_symbol_account_isolation.py` 16 passed（原 13 + 新增 3）
     - 覆盖率：`list_configs` 函数（68-83 行）全覆盖，**line 81 已不在 Missing 列**（评审验收标准）
   - 待 code-reviewer 独立复核确认。

3. ~~AppShell useEffect 稳定性~~（**已移除**——subscribe 为稳定引用，无此问题，见"已验证非问题"）

### Phase 3: 架构权衡项 — `Status: split`（2026-09-27 用户指示拆分）

原 4 项已拆分为 3 个独立任务计划：

| 新计划 PLAN_ID | 承接问题 | 内容 |
|----------------|----------|------|
| `2026-09-27-b8f1bbd-fix3-cross-process-sync` | 问题 3 | 跨进程状态一致性 |
| `2026-09-27-b8f1bbd-fix5-6-schema-and-exception` | 问题 5/6 | DDL 双写 + broker-catalog 异常收窄 |
| `2026-09-27-b8f1bbd-fix8-contract-doc` | 问题 8 | get_symbol_spec 契约文档 |

各新计划已建独立目录并写入 task_plan.md（含评审 Background）。执行时用对应 PLAN_ID 定位。原计划不再包含 Phase 3 实施动作。

## 问题关闭矩阵（跟踪状态）

| # | 处置 | 关闭判据 |
|---|------|----------|
| 1 | ✅ **closed（已 amend）** | `git commit --amend` → `fe35cfa feat(symbols): 品种配置按 MT5 账号隔离（account_login）`（2026-09-27） |
| 2 | ✅ **closed（已修复+复核通过）** | 改模块属性访问 + TestReloadEnginesFallback 两用例绿 + 回归集 53 passed + code-reviewer APPROVE |
| 3 | ✅ **closed（方案 C 定稿）** | 子进程实证：MCP server 零引用 flag、agent runner 有独立自检 → 跨进程一致性是理论风险，接受现状（fix3 完成） |
| 4 | ✅ **closed（已修复+复核通过）** | list_configs 函数级覆盖，line 81 命中 + 16 passed + code-reviewer APPROVE |
| 5 | ✅ **closed（已修复）** | 提取 `app/db/schema_ddl.py` 共享 DDL，迁移与 main.py 单真相源；等价性验证通过（fix5-6 完成） |
| 6 | ✅ **closed（已修复）** | `except Exception` → `except SQLAlchemyError` + 退化不写缓存；补 test_db_degraded_bypasses_cache（fix5-6 完成） |
| 7 | **closed（无需修）** | subscribe 稳定引用已证伪 |
| 8 | ✅ **closed（已文档化）** | `docs/SYMBOL-PARAMETERS-TECH.md` §9.1 已记录 get_symbol_spec 契约变更（2026-09-27） |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| OCR review HTTP 404（LLM 后端不可用） | 1 | 回退人工审查，结论可靠 |
| GateGuard 拦截首个 Bash / Write | 1 | 按要求陈述事实后重试 |
| 修复 1 原方案值绑定回归 | 1 | 多路评审发现，改为模块属性访问 |

## Next Step

**全部问题已闭环**（2026-09-27）：
- 问题 1：amend 完成（fe35cfa）
- 问题 2/4：修复 + code-reviewer APPROVE（fb51676）
- 问题 3：方案 C 定稿（无代码修改）
- 问题 5/6：schema_ddl 共享 + 异常收窄（待 commit）
- 问题 7：证伪无需修
- 问题 8：契约文档已写入

**待办**：
1. commit 问题 5/6 改动（schema_ddl.py + 迁移 + main.py + symbols.py + 测试）到主分支
2. 可选：push 全部（fe35cfa → fb51676 + 新 commit）

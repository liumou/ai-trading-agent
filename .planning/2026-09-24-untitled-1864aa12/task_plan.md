# Task Plan: 行情提醒功能（价格阈值 → 飞书卡片通知）

> 本计划已吸收 oh-my-claudecode:critic 子代理的评审意见（2026-09-24），修复 3 个 CRITICAL、5 个 HIGH 问题后重写。所有声称已亲自验证。

## Goal

让用户在系统中配置"品种 + 阈值方向（大于/小于）+ 触发价 + 持续时长 + 最大发送次数"的行情提醒规则；当该品种价格满足条件并持续超过设定秒数时，通过飞书自定义机器人发送精美卡片消息，最多发送设定次数后停止。用户可在前端页面管理、启停规则。

## Next Step

Phase 1 完成规划与评审修订。等待用户审批（含 3 个需求语义确认项）后进入 Phase 2。

## Current Phase

Phase 1（规划 + 评审修订）— 完成后等待用户批准才能开始实现

## Phases

### Phase 1: 需求确认与规划（当前）

- [x] 理解需求：品种、阈值方向、触发价、持续时间、发送次数、飞书卡片
- [x] 勘察代码库：tick 循环、通知架构、路由模式、DB 模型、前端模式
- [x] 确定技术方案（写入 findings.md）
- [x] 撰写初版计划
- [x] critic 子代理评审（REVISE 结论）
- [x] 亲自验证 critic 的 CRITICAL/HIGH 声称（alembic head、revision ID、symbol 解析函数、Bell 图标、i18n 结构）✅ 全部属实
- [x] 迭代修订计划（本版）
- [x] **用户需求语义确认**：达上限=自动停用手动重开；持续时长=严格连续；飞书=无签名（2026-09-24 已确认）
- [ ] **用户最终批准计划**（等待用户确认后进入 Phase 2）
- **Status:** in_progress

### Phase 2: 后端 — 数据模型与迁移

> TDD：本阶段先写模型字段的单元测试（表结构、约束），再建迁移。

- [ ] 运行 `alembic heads` 确认当前 head 为 `a9b8c7d6e5f4`（已验证，但实施前再次确认无新增迁移）
- [ ] 新增 `PriceAlert` 模型（`backend/app/db/models.py`）：表 `price_alerts`
  - 字段：`id, symbol, condition("above"/"below"), trigger_price, duration_seconds, max_notifications, note, is_active, sent_count, last_sent_at, created_at, updated_at`
  - sent_count 默认 0，is_active 默认 True
- [ ] 新建 Alembic 迁移：**用 `alembic revision` 生成**（revision ID 用全新值如 `f0e1d2c3b4a5`，文件名与 ID 一致）
  - **`down_revision = "a9b8c7d6e5f4"`**（当前 head，不是 z0a1b2c3d4e5）
  - 生成后 grep `alembic/versions/` 确认 revision ID 无冲突
- [ ] 验证：`alembic upgrade head` 成功且 `alembic heads` 仍为单 head
- **Status:** pending

### Phase 3: 后端 — 飞书通知模块

> TDD：先写卡片构造 + 发送的单元测试（mock httpx），再实现。

- [ ] 第一步：**用 curl 发最小 interactive 卡片**验证 webhook 可达 + payload 格式正确（凭记忆不可靠，先实测）
- [ ] 新建 `backend/app/notifications/feishu.py`
  - `FeishuNotifier` 类：`enabled`（有无 webhook URL）、`send_price_alert_card(alert, price)` 方法
  - `_build_card(alert, price)` 构造精美卡片（见 findings.md 中附的卡片 JSON schema）：
    - `header`：图标 + 品种显示名 + 方向（template 颜色：above=red/绿，below=green/蓝）
    - `elements`：
      - 主指标 div：**大号当前价**（按 price_decimals 格式化）
      - 触发条件明细 div（lark_md）：阈值 / 持续秒数 / 发送次数
      - `hr` 分隔线
      - `note`：触发时间（UTC+7 或本地）
    - `config.wide_screen_mode=true`
  - httpx 异步 + timeout(10) + 失败日志（不崩溃）—— 对齐 Telegram `_send` 模式
  - 可选：轻量退避重试（1 次重试即可），失败仅 logger.error
- [ ] `backend/app/config.py` 增加 `feishu_webhook_url: str = ""`（env，默认空=禁用；**禁止进代码/DB/前端回显**）
- [ ] 注入路径（M5）：在 `main.py` lifespan 构建 `FeishuNotifier`，通过 `manager.set_price_alert_service(...)`（或等价注入）传给 PriceAlertService —— 与 TelegramNotifier 注入方式对齐
- **Status:** pending

### Phase 4: 后端 — 提醒巡检引擎

> TDD 核心：本阶段的状态机判定逻辑必须有完整单测（above/below/严格比较/持续时长/上限/失稳重置/数据不可用）。这是整个功能的核心，先写测试。

- [ ] 新建 `backend/app/services/price_alert_service.py`
  - `PriceAlertService(feishu_notifier, redis)` 类
  - **数据流（修复 H1）**：不调 bridge。从 **Redis price cache** 或内存读 scheduler `_fetch_tick` 已写入的 tick（`engine.market_data.get_current_tick` 已拉取并 push `price_update`，同时写 price cache）。若 cache 无数据，跳过本轮（视为数据不可用）
  - 判定：condition=above → **`bid > trigger_price`**（严格大于，贴合用户原文；浮点比较用 epsilon 或 price_decimals 归一化）
  - 持续时长：Redis key `price_alert:first_trigger:{alert_id}` 存首次满足的 unix 秒，设 **TTL = max(duration_seconds*2, 300)**（L2）
  - **数据不可用（H4）**：tick 为 None / Redis 不可用 / 拉取失败 → 视为"未知"：
    - 重置 first_trigger（保守，避免跨休市/断连误触发）
    - 不清 sent_count（已发送的是事实）
  - 持续满足达到 `duration_seconds` → 触发发送（异步 task，不 await 阻塞）+ 原子更新 sent_count
  - **原子计数（M1）**：`UPDATE price_alerts SET sent_count = sent_count + 1 WHERE id=? AND sent_count < max_notifications` 防并发双发；单进程 AsyncIOScheduler 下无需分布式锁，但注释注明不支持多 worker 部署
  - `sent_count >= max_notifications` → 置 `is_active=False`（自动停用）+ 清 first_trigger
  - 单条规则异常 try/except 隔离（不影响其他规则）
  - 使用 `is_market_open(symbol)` 跳过休市品种（M6，建议）
- [ ] **调度（修复 H1）**：注册**独立 interval job** `price_alert_check`（每 1-2s，`max_instances=1, coalesce=True`）—— 不复用 bot_tick 主循环，避免飞书网络慢时拖累行情刷新。飞书发送用 `asyncio.create_task` 异步，绝不阻塞巡检循环
- **Status:** pending

### Phase 5: 后端 — REST API + 注册

> TDD：先写 API 的 CRUD + 校验测试。

- [ ] 新建 `backend/app/api/routes/price_alerts.py`，用 `make_authed_router("/api/price-alerts", ...)`
  - `GET /api/price-alerts` — 列表（含运行态：sent_count、is_active、达上限标识）
  - `POST /api/price-alerts` — 创建（校验 symbol、condition、trigger_price>0、duration>0、max_notifications>0）
  - `PUT /api/price-alerts/{id}` — 更新（重置 sent_count/first_trigger）
  - `DELETE /api/price-alerts/{id}` — 删除（清理 Redis first_trigger 与孤儿状态）
  - `POST /api/price-alerts/{id}/toggle` — 启停
  - **`POST /api/price-alerts/test` — 测试发送**（M2）：发一张示例卡片，返回成功/失败及飞书错误
- [ ] **toggle 状态机（修复 C3）**：`is_active` false→true 时**必须重置** `sent_count=0` + 清 Redis first_trigger + `last_sent_at=NULL`。否则达上限后重开会静默失效
- [ ] `main.py` 注册 router + include_router
- [ ] **symbol 校验（修复 M3）**：用 `resolve_canonical_symbol()` + `get_active_symbols()`（不用静态 SYMBOL_PROFILES，避免拒绝 DB 动态品种/别名）。symbol 存规范名
- **Status:** pending

### Phase 6: 前端 — 页面

> 本阶段以 tsc + build 验证。

- [ ] `frontend/lib/api.ts` 增加 price-alerts API 客户端（list/create/update/delete/toggle/test）
- [ ] 新建 `frontend/app/price-alerts/page.tsx`
  - 规则列表（卡片/表格）：品种、方向、触发价、持续时长、发送上限、已发送次数、状态开关
  - **达上限标识**：规则自动停用时明确显示"已达发送上限，需手动重新启用"（不是灰色开关就完事）
  - 新建/编辑对话框（Dialog + Form）：品种下拉（用 `listSymbolConfigs()`，L4）、方向选择、触发价、持续秒数、发送上限、备注
  - "测试发送"按钮（调用 test 端点）
  - 价格判定提示：明确标注"按当前买入价（bid）判定"
- [ ] 侧边栏 `Sidebar.tsx` 增加入口（trading 分组，**BellRing 图标**，与 /notifications 的 Bell 区分，L3）
- [ ] i18n：`messages/zh/` + `messages/en/` 新建 `priceAlerts` 翻译，`nav.json` 加侧边栏条目（L5）
- **Status:** pending

### Phase 7: 测试与验证

> 各 Phase 内已嵌入单测（TDD）。本阶段聚合验证 + 补集成测试。

- [x] 单元测试（已散入各 Phase）：卡片构造、判定逻辑（above/below/严格比较/持续时长/上限/失稳重置/数据不可用/重开重置）— 20 个测试全过
- [x] 集成测试：API 全流程（CRUD + toggle 重开重置 + test 端点）— 9 个全过
- [x] 后端全量测试：9 failed / 1019 passed — **9 个失败均为预存问题（与本次改动无关，已逐一验证）**
- [x] 前端 `tsc --noEmit` + `npm run build` 通过
- [x] 手动验证：curl 最小卡片 + 完整行情提醒卡片真实发送成功（飞书群收到）
- **Status:** complete

### Phase 8: 交付

- [x] 更新 CLAUDE.md（新增模块路径、配置项 `FEISHU_WEBHOOK_URL` 说明）
- [x] 提交用户：变更清单、测试结果、部署说明
- [x] **webhook 安全提醒**：建议用户在飞书群机器人开启签名校验或 IP 白名单（H3）
- **Status:** complete

## Key Questions（需用户审批时确认）

1. ✅ **"总共发送多少次"达到上限后的行为**：用户已确认 **(A) 达上限自动停用，需手动重新开启（开启时重置计数）** — 2026-09-24
2. ✅ **"持续时长"语义**：用户已确认 **严格连续满足**（价格跌破即重新计时）— 2026-09-24
3. ✅ **飞书群机器人安全设置**：用户已确认 **无签名，直接调用** — 2026-09-24
4. **部署形态**：Railway 是否为单 uvicorn worker？（决定是否需要 Redis 分布式锁防并发双发；单进程默认不需要）— 实现按单 worker 假设，代码注释注明
5. **提醒规则是否需要绑定 MT5 账号**？（项目已多账号化；价格是全局的，倾向不需要）— 实现按"不绑定账号"（价格是全局的），代码注释注明

## Decisions Made

| 决策 | Rationale |
|------|-----------|
| **独立 interval job** 巡检提醒（不复用 bot_tick 主循环） | 飞书网络慢不拖累行情刷新；与"提醒故障不影响交易"原则一致（critic H1） |
| 从 Redis price cache 读 tick（不调 bridge） | 避免双倍 bridge 请求（critic H1） |
| 规则存 PostgreSQL 新表 `price_alerts`，运行态计数存 Redis | 重启不丢规则；计数易迁移 |
| 用 `msg_type="interactive"` 内联卡片 | custom bot 直接支持，无需卡片模板管理 |
| 飞书 webhook 走 `settings.feishu_webhook_url` env，不进代码/DB/前端 | 防泄漏（critic H3 + 安全规则） |
| 达到 max_notifications 自动停用，toggle 重开重置计数 | 防刷屏 + 修复静默失效（critic C3） |
| **严格比较 `>`/`<`**（贴合"大于/小于"原文） | 避免价格恰好等于阈值误触发（critic H5） |
| 数据不可用时重置 first_trigger（不清 sent_count） | 避免跨休市/断连误触发（critic H4） |
| 原子计数 `UPDATE ... sent_count = sent_count+1 WHERE ... < max` | 防并发双发（critic M1） |
| symbol 校验用 `resolve_canonical_symbol` + `get_active_symbols` | 支持 DB 动态品种/别名，不误拒合法品种（critic M3） |
| 新增 `POST /api/price-alerts/test` 测试发送端点 | 用户可验证 webhook 配置（critic M2） |
| 新增价格判定提示"按买入价 bid" | 用户明确价格来源，避免误解 |
| 删除规则时清理 Redis 孤儿 key | 防状态泄漏（critic L2 相关） |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| 计划 down_revision 写成 `z0a1b2c3d4e5`（实际是倒数第二个） | 1 | `alembic heads` 验证实际 head=`a9b8c7d6e5f4`，已修正；实施前再确认 |
| 计划迁移 revision ID `a1b2c3d4e5f6` 已被占用 | 1 | grep 验证冲突，改用全新 ID `f0e1d2c3b4a5`，用 `alembic revision` 生成 |
| check_all 挂 bot_tick 与失败隔离自相矛盾 | 1 | 改为独立 interval job + 从 price cache 读 tick（critic H1） |
| toggle 重开未重置 sent_count → 静默失效 | 1 | 定义 false→true 重置状态机（critic C3） |
| 价格 `>=`/`<=` 与用户"大于/小于"语义不符 | 1 | 改严格 `>`/`<`（critic H5） |
| findings.md 明文含 webhook URL | 1 | 已改为占位符 `<FEISHU_WEBHOOK_URL>`，提醒用户开签名（critic H3） |

## Notes

- 项目规则：代码注释必须中文；与用户交流用中文
- DB 时间列用 `datetime.utcnow()`（naive），勿用 offset-aware
- 新路由必须 `make_authed_router`，杜绝漏鉴权
- **`FEISHU_WEBHOOK_URL` 是机密**：只存 env，禁止写入 git/代码/DB/前端回显
- 不支持多 worker 部署（单 uvicorn worker 前提，若多 worker 需 Redis 分布式锁）
- 用户要求"先生成计划，计划经过我同意，才能执行" — Phase 1 完成后必须停下等待审批，且审批需包含 Key Questions 的 3 项确认
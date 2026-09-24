# Progress Log — 行情提醒（飞书通知）

## Session 1 — 2026-09-24（规划阶段）

- 初始化规划目录 `2026-09-24-untitled-1864aa12`
- 勘察代码库：
  - `backend/app/bot/scheduler.py`：`bot_tick` 1s 循环、`_fetch_tick` 复用行情拉取
  - `backend/app/mt5/market_data.py`：`get_current_tick` 返回 bid/ask
  - `backend/app/notifications/telegram.py`：通知模式（httpx 异步、enabled 开关、失败日志不崩溃）
  - `backend/app/api/router_factory.py`：`make_authed_router` 鉴权统一
  - `backend/app/db/models.py`：SQLAlchemy 2.0 模型模式、`SymbolConfig` 参考
  - `backend/alembic/versions/`：最新 head `z0a1b2c3d4e5`（add_account_isolation）
  - `frontend/lib/api.ts`、`frontend/components/layout/Sidebar.tsx`：前端模式
- 撰写 `findings.md`（技术发现 + 决策）与 `task_plan.md`（8 阶段计划）
- **状态**：初版计划完成

## Session 2 — 2026-09-24（评审 + 迭代修订）

- 用户要求评审并迭代优化计划（不许并行子代理）
- 发起 **oh-my-claudecode:critic** 子代理评审（单个），结论 **REVISE**
- critic 发现：3 CRITICAL + 5 HIGH + 4 MEDIUM + 5 LOW
- **亲自验证 critic 的关键声称**：
  - ✅ `alembic heads` = `a9b8c7d6e5f4`（不是 z0a1b2c3d4e5）→ 修正 down_revision
  - ✅ revision ID `a1b2c3d4e5f6` 已被占用 → 改用全新 ID
  - ✅ `resolve_canonical_symbol` / `get_active_symbols` 存在（symbol 校验方案）
  - ✅ Sidebar `Bell` 已被 /notifications 占用 → 用 BellRing
  - ✅ i18n = `messages/zh/` + `messages/en/`，nav.json 存在
- **计划修订内容**：
  - 修复 C1（迁移 head）+ C2（revision ID）
  - 修复 C3（toggle 重开重置 sent_count 状态机）
  - 修复 H1（独立 interval job + 从 price cache 读 tick，不阻塞 bot_tick）
  - 修复 H3（webhook URL 移出计划文件 → env 占位符）
  - 修复 H4（数据不可用重置 first_trigger）
  - 修复 H5（严格比较 >/<）
  - 修复 M1（原子计数）、M2（test 端点）、M3（symbol 校验）、M5（注入路径）
  - 补充飞书卡片 JSON schema 模板（L1）
  - 明确 5 个 Key Questions 需用户审批时确认
- **状态**：计划已修订，等待用户审批（含需求语义确认）

## Session 3 — 2026-09-24（执行 Phase 2：数据模型与迁移）

- 用户批准计划，开始执行
- Phase 2 完成：
  - 新增 `PriceAlert` 模型（`backend/app/db/models.py`，表 `price_alerts`，12 列）
  - 用 `alembic revision --rev-id f0e1d2c3b4a5` 生成迁移 `f0e1d2c3b4a5_add_price_alerts.py`，`down_revision = "a9b8c7d6e5f4"`（当前 head，正确）
  - 验证：`alembic heads` = `f0e1d2c3b4a5 (head)` 单 head；`history` 确认挂在正确位置；迁移列与模型列完全一致
  - 注：SQLite 无法验证完整迁移链（早期迁移用 PG-only 语法 + env.py `SET LOCAL`），生产用 PostgreSQL 无此问题；完整 DB 验证留待 CI/部署
- **状态**：Phase 2 完成，进入 Phase 3

## Session 4 — 2026-09-24（执行 Phase 3：飞书通知模块）

- curl 实测 webhook：最小 interactive 卡片发送成功（HTTP 200, code=0）
- 新建 `backend/app/notifications/feishu.py`：
  - `FeishuNotifier` 类：`enabled`、`_post`（httpx 异步 + 超时 + 失败日志不崩溃）、`_build_price_alert_card`（精美卡片）、`send_price_alert_card`、`send_test_card`
  - 卡片：header 图标+主色（above=red/below=green）+ 大号当前价 `<font color='red'>` + 条件明细 div + note 时间注脚
  - price_decimals 格式化（GOLD=2, USDJPY=3）
- `config.py` 增加 `feishu_webhook_url`（env，默认空=禁用）
- `main.py` import FeishuNotifier + lifespan 注入 `app.state.feishu_notifier`
- 验证：
  - 卡片构造单测（模拟 alert 对象）通过
  - **真实 webhook 发送完整行情提醒卡片成功**（飞书群已收到）
  - `main` 导入 OK；TestClient lifespan 启动 OK，`app.state.feishu_notifier` 注入成功
- **状态**：Phase 3 完成，进入 Phase 4

## 测试结果

- curl 最小卡片：HTTP 200, code=0, success
- `send_price_alert_card` 真实发送：True
- TestClient lifespan：feishu_notifier 注入成功（enabled=False 因本地无 env webhook）

## Session 5 — 2026-09-24（执行 Phase 4：提醒巡检引擎）

- 新建 `backend/app/services/price_alert_service.py`：
  - `PriceAlertService(feishu_notifier, redis, session_factory=None)` — session_factory 可注入（测试用 SQLite）
  - `check_all()`：加载活跃提醒 → 逐条判定（失败隔离）
  - `_check_one()`：读 price cache → 严格比较 → 维护 first_trigger 状态机 → 达时长触发
  - `_is_satisfied()`：严格 `>`/`<`（等于不满足）
  - `_dispatch_send()`：原子计数占位 + 发送 + 达上限停用（`_sending` 防重入）
  - Redis key：`price:cache:{symbol}`（TTL 10s，scheduler 写）、`price_alert:first_trigger:{id}`（TTL=max(duration*2,300)）
- `scheduler.py` 修改：
  - `_fetch_tick` 写 Redis price cache（`price:cache:{symbol}`，TTL 10s）
  - 注册 `price_alert_check` interval job（每 2 秒，max_instances=1）
  - `set_price_alert_service()` 注入 + `_price_alert_job()`
- `main.py` lifespan 构建 `PriceAlertService` 注入 scheduler + app.state
- **验证**：
  - TestClient lifespan：`price_alert_check` job 注册成功，service 注入成功
  - 单元测试 11 个全部通过（严格比较/时长状态机/达上限/原子计数/TTL）
  - 关键设计修正：服务用可注入 session_factory（非全局 async_session），使测试可用 SQLite
- **状态**：Phase 4 完成，进入 Phase 5

## Session 6 — 2026-09-24（执行 Phase 5：REST API）

- 新建 `backend/app/api/routes/price_alerts.py`：
  - 6 个端点：`GET/POST /api/price-alerts`、`PUT/DELETE /{id}`、`POST /{id}/toggle`、`POST /test`
  - `_validate_symbol` 用 `resolve_canonical_symbol` + `get_active_symbols`（支持 DB 动态品种）
  - toggle 重开重置 sent_count + 清理 Redis（修复静默失效）
  - 删除清理 Redis 孤儿 key（走 app.state.price_alert_service.cleanup_rule）
  - `/test` 走 app.state.feishu_notifier
- `main.py` 注册 `price_alerts_routes.router`
- **验证**：
  - 路由注册确认：6 个端点全部出现
  - API 集成测试 9 个全部通过（CRUD/toggle 重置/symbol 校验/参数校验）
  - py_compile 全部文件通过
- **状态**：Phase 5 完成，进入 Phase 6（前端）

## Session 7 — 2026-09-24（执行 Phase 6：前端页面）

- `frontend/lib/api.ts`：新增 `PriceAlert` interface + 6 个 API 函数（list/create/update/delete/toggle/test）
- 新建 `frontend/app/price-alerts/page.tsx`：
  - 规则列表 Table：品种/方向 Badge/触发价/持续时长/已发送/状态/操作
  - **达上限标识**：`statusSaturated` 琥珀色 Badge（"已达发送上限"）
  - 新增/编辑 Dialog：品种下拉（listSymbolConfigs）、方向 Select、价格/时长/次数 Input、备注
  - 每行：Switch 启停 + 测试发送按钮 + 编辑 + 删除
  - 侧边栏 BellRing 图标入口
- i18n：`messages/zh/priceAlerts.json` + `messages/en/priceAlerts.json`（40+ key），`nav.json` 加 priceAlerts 入口
- **验证**：`tsc --noEmit` 通过、`npm run build` 成功（/price-alerts 路由出现）
- **状态**：Phase 6 完成，进入 Phase 7（测试聚合）

## Session 8 — 2026-09-24（执行 Phase 7：测试与验证）

- 后端全量测试：**9 failed, 1019 passed**（我的新增测试 20 个全过）
- 9 个失败**全部为预存问题，与本次改动无关**（已验证失败测试不引用 price_alert/feishu）：
  - `test_multi_agent.py` 6 个：本地 LLM 模型配置（glm-5.3-flash/deepseek-v4-flash）与测试预期的 claude 模型名不符 — 环境问题
  - `test_ml_barrier_validation.py`：`ImportError: cannot load module more than once per process` — 预存
  - `test_backtest_consistency.py::test_gold_reads_profile`：SYMBOL_PROFILES 测试间污染（单独跑通过、全量跑失败）— 预存
- 前端：`tsc --noEmit` ✅、`npm run build` ✅（/price-alerts 路由出现）
- ruff：venv 无 pip/ruff（用 uv 管理），无法本地跑；CI 会处理
- 手动验证：curl 最小卡片 ✅、完整行情提醒卡片真实发送 ✅（飞书群收到）
- **状态**：Phase 7 完成（预存失败已确认与改动无关），准备 Phase 8 交付
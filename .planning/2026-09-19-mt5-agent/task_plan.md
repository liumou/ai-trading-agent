# Task Plan — MT5 手动交易 + Agent 风控防火墙(v2,经三路评审)

## Goal

前端手动对 MT5 下单(市价/挂单/撤单/改单),每次下单前由 AI Agent 审查(订单合理性/风控触发/情绪化交易检测),Agent 可拦截。防火墙不变量:**硬闸门先行,LLM 只能收紧不能放宽硬闸门结论;REJECTED 不可强制;LLM 失败 fail-closed。**

## 已确认决策

| 决策点 | 结论 |
|--------|------|
| REJECTED | 硬拦截,不可强制 |
| LLM 失败 | fail-closed(可重新请求审查) |
| rollout | 沿用 AI 通道(shadow/paper 拒绝真实单、micro cap 0.01、live 需 llm_allow_live) |
| UI | 独立 /trading 页 |
| CAUTION | review_id 绑定二次确认,TTL 120s |

## 评审记录

三路 critic(安全/风控、架构/一致性、可行性/工程)评审 v1,合并修复:硬闸门抽共享 preflight(禁复制 broker.py)、LLM 审查改 202+轮询(同步 LLM 会撞 axios 10s/provider 120s)、扩 OrderAudit 不建新表、confirm 绑定 review_id、改挂单全流水线重审、SL 漂移以 entry 为基数、下单禁超时重试(防双开仓)、per-account 锁、switching 手动通道 fail-closed、ticket 归属校验、情绪规则自建(BiasGuard 是死代码/trade_accountability 是事后内存态)、Bridge retcode 10008=PLACED、filling 模式推导、迁移挂 head z0a1b2c3d4e5。

## Phases

### Phase 0: 存量漏洞前置修复 (complete)
- [x] routes/positions.py DELETE 平仓收口(close_position_gated:switching fail-closed/ticket 归属/rollout 拦截/记账防双计/事件+WS)
- [x] connector _request 下单类禁歧义重试(retry_ambiguous=False);executor _NO_RETRY_ERRORS 加 timeout
- [x] 回归测试:新增 test_position_close.py 10/10;全量 861 passed,13 failed 经 stash 基线确认为既有问题(零回归)

### Phase 1: Bridge 挂单端点 (complete)
- [x] GET /orders、POST /order/pending、PUT /order/{ticket}、DELETE /order/{ticket};verify_api_key;拒绝响应带结构化 retcode(_retcode_reject,存量 /order、/position 端点同步补齐)
- [x] 挂单正确性:retcode PLACED(10008)/DONE(10009) 双成功码、filling_mode 位掩码推导(回落 RETURN)、价格关系校验(LIMIT/STOP×BUY/SELL×SL/TP 8 组)、pydantic Literal+gt=0 校验(422)、TRADE_ACTION_MODIFY 查单回填 type_time/expiration
- [x] conftest mock 扩展(orders_get/order_send/symbol_info 等 6 函数 + 全部常量,修正 IOC=1)
- [x] 测试:mt5_bridge/tests/test_pending_orders.py 28 个,36/36 全绿

### Phase 2: 共享 preflight + 数据模型 (complete)
- [x] app/services/order_preflight.py(preflight_order + _sanitize_comment);broker.py place_order 重构为调用它,strict_symbol=False 保持 AI 通道语义
- [x] 修复:symbol 解析 strict fail-closed、spread history key 分 symbol、strict 无 volume 配置拒绝(检查前置——normalize 对缺失配置原样放行)
- [x] connector 补 place_pending_order/get_orders/modify_order/cancel_order(下单禁歧义重试;executor 不加)
- [x] Alembic 迁移 a9b8c7d6e5f4(down_revision=z0a1b2c3d4e5,单 head 验证):OrderAudit 加 source/account_login/order_kind/order_price/review + 3 索引
- [x] MANUAL_MAGIC_NUMBER + SL 漂移预算常量
- [x] 测试:test_order_preflight.py 12 个;broker/guardrails/executor/accounts/switch 回归全绿

### Phase 3: ManualOrderGate (complete)
- [x] app/services/manual_order_gate.py:per-account 锁→preflight(strict)→情绪规则(马丁=block 直接拒;复仇/连亏/频率=warn 交 LLM;sentiment 进快照)→PENDING_REVIEW→异步 LLM(wait_for 25s 仅约束 LLM 调用,防执行段被取消;verdict 白名单)→APPROVED 执行/CAUTION 待确认/REJECTED 拦截
- [x] confirm 绑定 review_id+重跑硬闸门+TTL 120s→EXPIRED;改挂单全流水线;撤单仅 switching+归属;改 SL/TP entry 锚点漂移预算(Redis anchor×5+日预算 3,SL=0 设止损放行并记锚)
- [x] switching fail-closed;ticket 归属校验;[Manual] 事件(TRADE_BLOCKED/TRADE_OPENED/ORDER_FAILED/SETTINGS_CHANGED/AI_AGENT_ERROR)+WS bot_event 推送
- [x] 测试:test_manual_order_gate.py 20 个(发现并修正:AsyncMock 未配置子属性陷阱;零 SL 由 guardrails 硬拒)

### Phase 4: 路由 (complete)
- [x] routes/manual_trading.py:POST /orders(PENDING_REVIEW→202/REJECTED→200)、POST /orders/{id}/confirm、GET /reviews(账号隔离)、GET /reviews/{id}(404)、GET /orders(symbol 归一化)、DELETE /orders/{ticket}(400)、PUT /orders/{ticket}(全流水线改挂单,未指定字段取当前单值)、PUT /positions/{ticket}(拒绝=200 业务结果/错误=400)、POST /positions/{ticket}/close(复用 close_position_gated)
- [x] main.py:lifespan 挂 app.state.manual_order_gate(复用 connector/redis/ai_client)+ 路由注册;import 验证通过;迁移离线 SQL 验证
- [x] 测试:test_manual_trading_routes.py 17 个;Phase 3+4 合计 58 passed

### Phase 5: 前端 /trading (complete)
- [x] app/trading/page.tsx:市价/挂单 Tab+方向/类型选择、下单表单、审查结果卡(PENDING_REVIEW 轮询 2s×20、CAUTION 确认、REJECTED 拦截面板+重新提交)、挂单表(改价走全流水线/撤单)、持仓表(改SL/TP/平仓)、shadow/paper 横幅
- [x] components/trading/ReviewResultCard.tsx + PositionsTable.tsx(抽组件;dashboard 迁移列为后续项——无前端测试,控制回归风险)
- [x] lib/api.ts 手动交易接口+类型;messages/{zh,en}/trading.json;nav.json×2+Sidebar
- [x] tsc --noEmit 通过;npm run build 通过(/trading 已注册)

### Phase 6: 测试 (in_progress)

### Phase 6: 测试 (pending)
- [ ] Gate 判定矩阵/路由/Bridge/回归;全量 pytest(backend/.venv/bin/python,勿加 -p no:cov);tsc+build

### Phase 7: 文档 (pending)
- [ ] CLAUDE.md

## Next Step

**全部 Phase 0-7 完成。** 下一步(可选):部署验证(Railway 后端跑 alembic 迁移 a9b8c7d6e5f4 → VPS Bridge 更新 → Vercel 前端);提交 git(等待用户确认)。

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|

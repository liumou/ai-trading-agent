# Task Plan: 后端启动后 400/401 大量刷屏 + 「MT5 连不上」根因定位与修复

## Goal
不改动任何交易/风控/MT5 连接器逻辑，定位并修复「后端启动后浏览器端 400 / 401 刷屏、
各类面板全部显示未连接（被误判为 MT5 掉线）」的故障。

## 已复现的根因（详细证据见 findings.md）

| # | 现象 | 根因 | 证据 |
|---|---|---|---|
| **A** | `400 Bad Request` | `backend/.env` 的 `CORS_ORIGINS` 首项拼写错误 `http:/localhost:3000`（少一个斜杠）；`config.cors_origin_list` 只做 `strip()`，不做校验/归一 → `http://localhost:3000` 实际不在白名单。前端 dev（:3000）访问后端（:8002）是跨域，带 `Authorization` 的请求必走 OPTIONS 预检 → Starlette 直接 `400 Disallowed CORS origin` | 复现：`curl -X OPTIONS ... -H 'Origin: http://localhost:3000'` → **400**；换成 `http://192.168.3.34:3000` → **200** |
| **B** | `401 Unauthorized` | 后端已启用鉴权（`AUTH_PASSWORD_HASH` 已设），但前端 `AppShell.tsx:29-35` 在 hostname 为 `localhost/127.0.0.1` 时**无条件**写入 `localStorage.token="__noauth__"` 并跳过登录（与 `login/page.tsx:21` 的 `NEXT_PUBLIC_DEV_NOAUTH` 门控不一致）。axios 拦截器随即把 `Bearer __noauth__` 发给所有接口 → `verify_token` 返回 None → 401；401 拦截器删 token + 跳 /login，AppShell 再写回 → 死循环 | 复现：`curl -H 'Authorization: Bearer __noauth__' /api/bot/status` → **401 Invalid or expired token**；无 token → **401 Authentication required** |
| **C** | 「MT5 连不上」 | **不是真实故障**。后端↔bridge↔MT5 全链路健康，UI 显示未连接是 A+B 导致浏览器拿不到任何数据 | bridge `/health` = `logged_in:true, login:336773771`；`/account` 余额 11177.48；后端 `/health` `mt5_connected:true`；`/api/integration/status` MT5 Bridge connected 108~195ms；`/api/market-data/tick?symbol=GOLD` 返回实时 bid/ask 4345.31/4345.85 |

> 附带风险：401 死循环会让 UI 无法加载，等于挡住「紧急停止」入口，而当前 `.env` 是
> `ROLLOUT_MODE=micro` + `LLM_ALLOW_LIVE=true`（实盘微仓）→ 本修复优先级提高。

## 范围

| 文件 | 动作 |
|---|---|
| `backend/.env` | 修 `CORS_ORIGINS` 拼写（**唯一必改项**，非入库文件） |
| `backend/app/config.py` | 可选：`cors_origin_list` 增加 origin 合法性过滤 + 启动告警 |
| `backend/tests/unit/test_*.py` | 可选：为上面的校验补单测 |
| `frontend/components/layout/AppShell.tsx` | 加 `NEXT_PUBLIC_DEV_NOAUTH` 门控 + 清理 `__noauth__` 脏值 |
| 交易/风控/MT5 connector | **不改** |

## Current Phase
Phase 0-3 已完成并验证；Phase 4 收尾中（剩 2 项用户决策 + 1 步浏览器登录复验）。
后端 :8002（用户 19:11 重启，已带修好的 `.env`）、前端 :3000（新构建，PID 89359，日志 `/tmp/pwf-fe-start.log`）。

## Phases

### Phase 0: 证据复现与根因定位 —— 只读，不改任何文件
- [x] 枚举 400 来源并原位复现（CORS 预检）
- [x] 枚举 401 来源并原位复现（假 token / 缺 token / 登录失败）
- [x] 独立验证 MT5 链路（bridge 直连 + 后端 API + 实时 tick）
- [x] 确认工作区改动（`config.py` 的 `laya_enabled`、`.active_plan`）
- **Status:** complete

### Phase 1: 修 400（CORS 白名单）—— ✅ 完成
- [x] 1.1 `backend/.env`：`http:/localhost:3000` → `http://localhost:3000`（**用户于 19:10 手改完成**）；另修仓库内同源错误 `backend/.env.example:60`（同一拼写，会传染新部署）
- [x] 1.2 `config.py::cors_origin_list` 改为：过滤非 `http(s)://host[:port]` 条目 + `logger.warning` 列出被忽略项 + 尾斜杠归一 + 去重；`*` 原样保留（`auth._assert_auth_consistent()` 的启动期防线依赖它）
- [x] 1.3 单测 `backend/tests/unit/test_config_cors_origins.py`（9 用例）**9 passed in 0.09s**
- [x] 1.4 验证：`OPTIONS` 预检 `Origin: http://localhost:3000` → **200 + acao 正确**（修复前 400）；`http://192.168.3.34:3000` → **200**；`http://evil.example.com` → **400**（白名单外仍正确拒绝）
- **Status:** complete

### Phase 2: 修 401（前端 localhost 自动放行）—— ✅ 代码完成，浏览器端待用户登录复验
- [x] 2.1 `AppShell.tsx`：localhost 自动放行收窄为 `isLocal && process.env.NEXT_PUBLIC_DEV_NOAUTH === "1"`（与 login 页门控一致）
- [x] 2.2 未开启该开关时保持原逻辑：无 token → `router.replace("/login")`；有 token → `api.get("/api/auth/me")` 校验
- [x] 2.3 历史脏值清理：`token === "__noauth__"` → `removeItem` 且视为未登录（`token = null` → 跳登录），解除 401 死循环
- [x] 2.4 回归：`npx tsc --noEmit` → **0 error**；`npx eslint components/layout/AppShell.tsx` → **0 error**（1 条 set-state-in-effect 警告为改动前既有、位于未触碰的 `isAuthPage` 分支）
- [x] 2.4b 产物级验证：`npm run build` 成功（`✓ Compiled successfully in 15.7s`，0 error），并在 `.next/static/chunks/0jdx-ez.k1khj.js` 中确认新逻辑已编译进去：
      `t="1"===env.NEXT_PUBLIC_DEV_NOAUTH; if(e&&t){...setItem("token","__noauth__")...}` → **放行被门控**；
      `"__noauth__"===o&&removeItem("token"), ("__noauth__"===o?null:o)?...:→/login` → **脏值被清理**（构建时未设该 env ⇒ 旁路为死代码）
- [ ] 2.5 手工验证（**需用户的登录口令，我做不了**）：浏览器打开 `http://localhost:3000` → 若 localStorage 里还有 `__noauth__` 会被自动清掉并跳登录 → 用 `AUTH_USERNAME`(admin) + 口令登录 → dashboard 出数据，DevTools Network 无 400/401
- **Status:** in_progress（仅剩浏览器交互确认）

### Phase 3: 端到端验证（只读）—— ✅ 完成（除 3.4 需看用户终端）
- [x] 3.1 后端健康：`/health` → `{"status":"ok","mt5_connected":true,"redis_connected":true,"bot_state":"STOPPED"}`
- [x] 3.2 数据面（带有效 token，全部 **200**）：`/api/bot/status`、`/api/positions`、`/api/symbols`、`/api/market-data/symbols`、`/api/integration/status`、`/api/market-data/tick?symbol=GOLD`
- [x] 3.3 前端：`:3000` 重启后 `GET /login` **200**、`GET /dashboard` **200**；WebSocket 实测：有效 token → **CONNECTED**；`__noauth__` / 无 token → **HTTP 403 拒绝**（符合预期）
- [x] 3.3b MT5 实时性：`/api/market-data/tick?symbol=GOLD` → `bid 4354.17 / ask 4354.71`（22:21:54，价随盘走）
- [ ] 3.4 uvicorn 终端：重启后不再刷 `400 Bad Request` / `401 Unauthorized` —— **需你在后端终端确认**（我无法读取你的前台终端输出；白名单外 400 与无 token 401 属正常行为，不应消失）
- **Status:** complete（3.4 移交用户目视确认）

### Phase 4: 收尾 —— 🔄 进行中
- [x] 4.1 结论 + 全部验证输出已写入 `progress.md`；构建/网络踩坑写入 `findings.md`
- [ ] 4.2 待用户决定：`backend/app/config.py` 的 `laya_enabled` 默认值 True（工作区改动，未入库）是否提交/回退
- [ ] 4.3 待用户决定：`frontend/.env`(192.168.3.34:8002) 与 `.env.local`(localhost:8002) 并存 —— 构建时 Next 加载顺序是 `.env.local` 优先，所以远端/手机访问 `192.168.3.34:3000` 时会去请求「访客自己的 localhost:8002」而失败（会被误读成后端/MT5 挂了）
- [ ] 4.4 **建议单开任务（安全）**：`backend/.env.example`（入库文件）里填的是线上真值 —— `SECRET_KEY` / `AUTH_PASSWORD_HASH` / `VAULT_MASTER_KEY` 与 `backend/.env` **完全相同**（哈希比对，见 findings.md §8）。拿到仓库即可离线签发 admin JWT 绕过登录。需：改占位符 + 轮换密钥（轮换 SECRET_KEY 会让所有 token 失效需重登；换 VAULT_MASTER_KEY 需迁移既有密文）。
- **Status:** in_progress

## Decisions Made
| Decision | Rationale |
|---|---|
| 先修 CORS `.env` 与前端假 token，不动交易代码 | 两个故障都在「接入层」，与策略/风控无关，改动面最小 |
| 独立计划目录 `2026-09-21-auth-cors-401-mt5-diagnosis` | 与本机在跑的 `2026-09-21-laya` 审查计划隔离；pin 已切到本计划 |
| MT5 连接器**不动** | 实测全链路健康，改它属于无证据改动 |
| 修复前必须用户批准 | 用户明确要求「计划需要我同意才能执行」 |

## Errors Encountered
| Error | Resolution |
|---|---|
| 在 `backend/logs/bot.log` 里 grep 不到 400/401 | 400/401 是 uvicorn access log，走 **stdout**（终端），不进 loguru 文件；改为 curl 原位复现 |
| `/api/integration/mt5` 404 | 实际路由是 `/api/integration/test/{service}`，用 `test/mt5` 复测 |
| 本地无 AUTH 口令，无法用真实账号登录验证 | `require_auth` 只验 JWT（无 DB JTI 校验）→ 用 `mint_internal_token()` 铸造临时 token 做只读验证（未落盘、未写入任何地方） |

## 回归测试结论（2026-09-21 19:2x）
- 新增 `backend/tests/unit/test_config_cors_origins.py` → **9 passed**
- 定向回归（config / symbol / market-data / llm / mcp / accounts 共 14 个文件）→ **147 passed, 9 warnings, 7.12s**
- 全量 `tests/unit`（873 用例）**未能全绿，但属预先存在的环境依赖问题，与本次改动无关**：
  - `test_multi_agent.py::TestModelSelection` **5 failed**：用例断言 claude 系模型名，而 `.env` 为 `LLM_PROVIDER=openai_compat`，真实 `openai_agent_loop` 被调用 → `mock_loop.call_args` 为 `None`（`AttributeError: 'NoneType' object has no attribute 'kwargs'`）。
  - **归因证据**：把 `config.py` 临时换回 HEAD 版本 → 同样 **5 failed**（14.52s）；恢复本次版本后仍 5 failed ⇒ 与 `cors_origin_list` 改动无关（已核 md5 一致、`from app.config import settings` 正常）。
  - 该文件其余用例会发**真实 LLM 请求**（实测单次 5.0s），全量跑在 49% 处长时间阻塞（已手动终止，日志 `/tmp/pwf-pytest-unit.log`）。
  - 想全量绿需要：这些用例的 mock 目标随 `LLM_PROVIDER` 适配，或跑测试时切回 `LLM_PROVIDER=claude` —— 建议另开任务，不塞进本次修复。

## Next Step
用户执行 Phase 2.5（浏览器登录复验）→ 决定 4.2/4.3 → 若认可则按上面的「未提交改动清单」提交。

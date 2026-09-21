# Progress Log: 400 / 401 / 「MT5 连不上」

## 会话 2026-09-21 19:0x（诊断，只读）

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 | 盘点运行态：`ps`/`lsof` | 后端 :8002（PID 81860，18:57 起）、前端 :3000（PID 82326）；**8001 无监听**（bridge 在 192.168.3.47） |
| 2 | `git status` / `git diff` | 仅 `config.py`(laya_enabled) 与 `.planning/.active_plan` 未提交 |
| 3 | `curl /health` | 200，`mt5_connected:true, redis_connected:true, bot_state:STOPPED` |
| 4 | 无 token 打 `/api/*` | 全部 **401 Authentication required**（鉴权已开） |
| 5 | bridge 直连 + 后端行情 API | 全部正常（登录 336773771、余额 11177.48、实时 tick/K 线可取） |
| 6 | OPTIONS 预检 | `Origin: http://localhost:3000` → **400 Disallowed CORS origin**；`Origin: http://192.168.3.34:3000` → 200 |
| 7 | 假 token 复现 | `Bearer __noauth__` → **401 Invalid or expired token** |
| 8 | 定位假 token 注入点 | `frontend/components/layout/AppShell.tsx:31-32`（无开关）vs `login/page.tsx:21`（有开关） |
| 9 | 检查 uvicorn stdout 与 loguru 文件 | 400/401 只出现在 uvicorn access log（stdout），故 `bot.log` grep 不到 |
| 10 | 写计划三件套 | 本目录 task_plan.md / findings.md / progress.md |

### 结论（待用户批准修复）
1. **400 = CORS 白名单拼写错误** `http:/localhost:3000`（`backend/.env`），解析侧不校验 → 改为 `http://localhost:3000`（并建议加校验）。
2. **401 = 前端 localhost 无条件注入 `__noauth__` 假 token**（`AppShell.tsx:31-32`），鉴权开启后必然 401，并与 401 拦截器形成跳登录死循环 → 用 `NEXT_PUBLIC_DEV_NOAUTH` 门控 + 清理脏值。
3. **MT5 未掉线**：后端↔bridge↔MT5 全链路正常；「连不上」是上面两条把浏览器数据面打死后的表象。

### 未做（等批准）
- 未改任何应用代码、未改 `backend/.env`、未重启任何进程。
- 未清理浏览器 `localStorage`（属用户侧动作，修复后需用户在 DevTools 里清一次或自动清除）。

### 待用户决策
| # | 决策点 | 选项 |
|---|---|---|
| 1 | 是否执行 Phase 1（CORS 修 .env [+ 可选校验/单测]） | 执行 / 只改 .env / 暂不 |
| 2 | 是否执行 Phase 2（AppShell 门控 + 脏值清理） | 执行 / 只加门控 / 暂不 |
| 3 | `frontend/.env` 与 `.env.local` 的 `NEXT_PUBLIC_API_URL` 是否统一 | 统一为实际访问地址 / 保持现状 |
| 4 | `config.py` 的 `laya_enabled=True` 是否提交/回退 | 提交 / 回退为 False |

---

## 会话 2026-09-21 19:1x-19:3x（用户批准 Phase 1-3 后执行）

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 | 修 `backend/.env` CORS | 编辑时发现**用户已于 19:10 手改**（9 条全部合法），无需再改 |
| 2 | 修 `backend/.env.example` 同源拼写 | ✅ |
| 3 | `config.py::cors_origin_list` 加校验/归一/告警 | ✅ |
| 4 | 新增 `tests/unit/test_config_cors_origins.py` | ✅ 9 passed |
| 5 | `AppShell.tsx` 门控 + 清 `__noauth__` | ✅ |
| 6 | `tsc --noEmit` / `eslint` | ✅ 0 error |
| 7 | 前端生产构建 | 第 1 次被工具超时杀掉；第 2 次因 Google Fonts 失败；**第 3 次用 DoH 代理成功**（15.7s） |
| 8 | 重启前端（旧进程 82326 已 500） | ✅ 新进程 PID 89359，`/login` `/dashboard` 均 200 |
| 9 | 端到端验证 | ✅ 预检 200 / 数据面全 200 / WS 有效 token 连通、假 token 403 / MT5 实时价 |
| 10 | 计划文件更新 | ✅ task_plan / findings / progress |

### 服务运行方式变更（请注意）
| 服务 | 之前 | 现在 |
|---|---|---|
| 后端 :8002 | 你终端前台（PID 81860 → 你 19:11 自行重启为 85925） | 未改动你的终端；**当前由你启动的进程在跑** |
| 前端 :3000 | 你终端前台 PID 82326（旧构建，已 500） | **已终止并由我以后台方式重启**（PID 89359，日志 `/tmp/pwf-fe-start.log`）。想回到前台：在他们终端 `Ctrl+C`/忽略后重跑 `./start-frontend.sh`（构建产物已是最新，脚本会直接 start） |
| 临时字体代理 | 无 | 后台 `127.0.0.1:7899`（`/tmp/pwf_fontproxy.py`，日志 `/tmp/pwf-fontproxy.log`）。**只是本地构建辅助**，不用可杀：`kill $(pgrep -f pwf_fontproxy)` |

### 剩下要你做的 3 件事
1. **浏览器复验（Phase 2.5）**：打开 `http://localhost:3000`（若之前存过 `__noauth__` 会被自动清掉并跳登录）→ 用 `admin` + 你的口令登录 → dashboard 出数据、DevTools Network 无 400/401。若仍 401，请把 Network 里那条请求的 **状态码 + 请求头** 发我。
2. **看后端终端（Phase 3.4）**：确认不再成片刷 `400 Disallowed CORS origin`；正常的 401（无 token 访问）应仍存在，那是预期。
3. **两个决策（Phase 4.2/4.3）**：`config.py` 的 `laya_enabled=True` 是否提交；`frontend/.env` 与 `.env.local` 的 `NEXT_PUBLIC_API_URL` 是否统一（远端访问会指向访客本机）。

### 未提交改动清单（本次会话产生）
```
 M backend/.env.example                                  (CORS 拼写)
 M backend/app/config.py                                 (cors_origin_list 校验 + 之前就有的 laya_enabled=True)
 M frontend/components/layout/AppShell.tsx               (401 修复)
 ?? backend/tests/unit/test_config_cors_origins.py       (新增 9 用例)
 ?? .planning/2026-09-21-auth-cors-401-mt5-diagnosis/    (本计划三件套)
 M .planning/.active_plan                                (pin 指向本计划)
```

### 补充：回归测试（19:2x）
```
# 新增用例
9 passed in 0.09s

# 定向回归（config/symbol/market-data/llm/mcp/accounts 14 文件）
147 passed, 9 warnings in 7.12s

# 全量 tests/unit（873）—— 未全绿，预先存在的环境依赖
5 failed (test_multi_agent.py::TestModelSelection)   # 与 .env 的 LLM_PROVIDER=openai_compat 有关
归因：HEAD 版 config.py 同样 5 failed ⇒ 与本次改动无关
后续用例有真实 LLM 调用（慢）→ 全量跑在 49% 阻塞，已手动终止
```

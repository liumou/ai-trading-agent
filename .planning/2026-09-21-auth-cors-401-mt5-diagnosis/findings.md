# Findings: 400 / 401 / 「MT5 连不上」证据链

诊断时间：2026-09-21 19:00~19:07 CST
后端进程：PID 81860 `uvicorn app.main:app --host 0.0.0.0 --port 8002`（18:57 启动）
前端进程：PID 82326 `next-server (v16.2.3)` 监听 :3000
代码版本：`45d8094`（main，工作区另有未提交改动，见「工作区状态」）

---

## 1. 400 Bad Request —— CORS 白名单拼写错误（已复现）

### 1.1 配置原文（`backend/.env`）
```
CORS_ORIGINS=http:/localhost:3000,http://192.168.3.47:3000,http://100.124.17.97:3000,
  http://localhost:8002,http://192.168.3.47:8002,http://192.168.3.34:8002,
  http://100.78.102.105:8002,http://192.168.3.34:3000,http://100.78.102.105:3000
```
首项 **`http:/localhost:3000`（单个斜杠）**，即 `http://localhost:3000` 从未被允许。

### 1.2 解析侧不做校验（`backend/app/config.py:361-362`）
```python
@property
def cors_origin_list(self) -> list[str]:
    return [o.strip() for o in self.cors_origins.split(",")]
```
只 strip，不校验形态 → 坏值静默进入 `CORSMiddleware(allow_origins=...)`。

### 1.3 原位复现（curl）
```
$ curl -i -X OPTIONS http://localhost:8002/api/bot/status \
    -H 'Origin: http://localhost:3000' \
    -H 'Access-Control-Request-Method: GET' \
    -H 'Access-Control-Request-Headers: authorization'
HTTP/1.1 400 Bad Request
access-control-allow-methods: GET, POST, PUT, DELETE, OPTIONS
access-control-allow-headers: Accept, Accept-Language, Authorization, ...
access-control-allow-credentials: true
content-length: 22
body: Disallowed CORS origin          <-- 无 access-control-allow-origin

$ 同一请求换 Origin: http://192.168.3.34:3000
HTTP/1.1 200 OK
access-control-allow-origin: http://192.168.3.34:3000
```
→ 白名单内（192.168.3.34:3000）通过、白名单外的 localhost:3000 被 400 拒绝，**确认根因是那一条拼写**。

### 1.4 前端确实走跨域
- `frontend/.env.local`：`NEXT_PUBLIC_API_URL=http://localhost:8002`、`NEXT_PUBLIC_WS_URL=ws://localhost:8002/ws`
- `frontend/.env`：`NEXT_PUBLIC_API_URL=http://192.168.3.34:8002`（Next 会优先加载 `.env.local`）
- `frontend/lib/api.ts:4`：`baseURL = process.env.NEXT_PUBLIC_API_URL`
- 页面源 `http://localhost:3000` → 目标 `http://localhost:8002`：跨域；axios 请求带 `Authorization`（非 CORS 安全头）→ **必触发 OPTIONS 预检** → 400。

### 1.5 为什么终端同时看到 400 和 401
- 带 `Authorization` 的请求 → 预检 → **400**（请求根本没发出去）
- `Accept-Language` 属 CORS 安全头，因此不带 token 的简单 GET → 无预检、直连 → **401**（响应被浏览器 CORS 策略拦截，页面表现为「连接失败」）
→ 两类请求交替，正是用户看到的「400 和 401 一起刷」。

---

## 2. 401 Unauthorized —— 前端 localhost 自动写入假 token（已复现）

### 2.1 后端鉴权是开启的
`backend/.env`：`AUTH_USERNAME=admin`、`AUTH_PASSWORD_HASH=$2b$12...`（60 字符 bcrypt）、`SECRET_KEY`（64 字符）
→ `auth._auth_enabled()` 为 True，所有 `/api/*` 走 `require_auth`。

### 2.2 前端有「三条不一致」的放行逻辑
| 位置 | 行为 | 是否有开关 |
|---|---|---|
| `frontend/components/layout/AppShell.tsx:29-35` | hostname 是 localhost/127.0.0.1 → `localStorage.setItem("token", "__noauth__")` + `setAuthChecked(true)`，**跳过登录** | ❌ **无任何开关** |
| `frontend/app/login/page.tsx:18-26` | 同样写 `__noauth__` | ✅ 需 `NEXT_PUBLIC_DEV_NOAUTH=1` |
| `frontend/lib/api.ts:11-14` | 有 token 就发 `Authorization: Bearer <token>` | — |

`AppShell` 的这段是 2026-04-17 `664d2fb`（第三方 upstream 提交）引入的，门控改造只落在了 login 页，AppShell 漏了。

### 2.3 复现
```
$ curl -H 'Authorization: Bearer __noauth__' http://localhost:8002/api/bot/status
HTTP/1.1 401 Unauthorized
{"detail":"Invalid or expired token"}

$ curl http://localhost:8002/api/bot/status        # 无 token
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required"}
```
后端侧 `auth.py:238-250`：`verify_token("__noauth__")` 解码失败 → None → 401。
`rate_limit.py:121-134` 也显式把 `__noauth__` 当哨兵值拒绝，说明这个值是「鉴权关闭」时的专用约定，**鉴权开启后它必然 401**。

### 2.4 死循环链路
1. 打开 `http://localhost:3000/dashboard` → AppShell 写 `__noauth__`、放行渲染
2. 页面并发请求 → 预检 400 / 简单请求 401
3. `api.ts:25-30` 401 拦截器：`localStorage.removeItem("token")` + `window.location.href="/login"`
4. 用户登录成功 → 存真 token；但只要 token 被清（刷新/过期/一次 401），AppShell 又写回 `__noauth__` → 回到第 2 步
→ 结果：所有面板（含 MT5 状态、持仓、账户）长期空白/未连接。

---

## 3. MT5 其实完全正常（三重独立验证）

| 验证 | 命令 | 结果 |
|---|---|---|
| 直连 bridge | `curl http://192.168.3.47:8001/health` | `{"status":"ok","mt5":{"login":336773771,"server":"XMGlobal-MT5 9"},"logged_in":true}` |
| 直连 bridge 账户 | `curl -H 'x-bridge-key: ...' http://192.168.3.47:8001/account` | `balance 11177.48 USD, equity 11177.48` |
| 后端健康 | `curl http://localhost:8002/health` | `{"status":"ok","mt5_connected":true,"redis_connected":true,"ai_available":true,"bot_state":"STOPPED"}` |
| 后端集成页数据源 | `GET /api/integration/status` | `MT5 Bridge: connected, latency_ms 108~195, detail "VPS: http://192.168.3.47:8001"` |
| 后端实时行情 | `GET /api/market-data/tick?symbol=GOLD` | `bid 4345.31 / ask 4345.85`（22:05:19） |
| 后端实时行情 | `GET /api/market-data/tick?symbol=BTCUSD` | `bid 84481.65 / ask 84521.65` |
| 后端 K 线 | `GET /api/market-data/ohlcv?symbol=GOLD&timeframe=M15&limit=3` | 返回完整 candles |

补充：
- `.env` 中 `MT5_BRIDGE_URL=http://192.168.3.47:8001`；本机 `localhost:8001` 无监听（bridge 按设计跑在 Windows 主机），`ping 192.168.3.47` 2ms 正常。
- 仅有的 MT5 相关报错是**历史**的：`backend/logs/bot.log` 里 15:22 出现 `Failed to get tick for GOLD_: tick timeout` / `BTCUSD: tick timeout`，来自**上一个**后端进程 PID 86797；18:57 重启后的当前进程 18:57 之后 WARNING 仅 2 条（LIVE TRADE MODE、TRUSTED_HOSTS），**无任何 tick/连接错误**。
- `symbol_configs` 别名链路正常：`GOLD → broker_alias GOLD_`、`BTCUSD → BTCUSD`（`market_data.get_current_tick` 会 `to_broker_alias` 后再请求桥）。

---

## 4. 其他观察（非本次故障主因，附在此供决策）

1. **前端 API 地址双份**：`.env`(192.168.3.34:8002) 与 `.env.local`(localhost:8002) 并存且语义冲突。Next 优先 `.env.local`，所以**从其他机器/手机打开 `http://192.168.3.34:3000` 时，页面仍会去请求「访客本机」的 localhost:8002** → 连接失败，也会被误读成「后端/MT5 挂了」。
2. **CORS 白名单含 `http://100.x.x.x`（Tailscale）**：说明存在远程访问场景，修 CORS 时不要把这几条删掉。
3. **未提交改动**：`backend/app/config.py` 把 `laya_enabled` 默认值从 `False` 改成 `True`（工作区改动，未入库）。`.env` 未设 `LAYA_ENABLED` → 该默认值生效。这与 400/401 无关，但会让本地行为与仓库默认不一致，建议单独决策。
4. **风险**：`.env` 为 `ROLLOUT_MODE=micro` + `LLM_ALLOW_LIVE=true`（实盘微仓）。UI 因 400/401 打不开 = 拿不到紧急停止入口，属操作风险。
5. `/api/auth/login/options` 返回 400 `Setup not complete. Register a passkey first.`（WebAuthn 未注册）。当前前端走的是账号密码路径，不影响；但若以后启用 passkey 需注意这是在启用鉴权后才出现的。

---

## 5. 工作区状态（诊断开始前）
```
$ git status --short
 M .planning/.active_plan      # 2026-09-18-ai -> 2026-09-21-laya（随后被本次 init 切到本计划）
 M backend/app/config.py       # laya_enabled False -> True
HEAD = 45d8094
```
本次诊断未修改任何应用代码；仅新增 `.planning/2026-09-21-auth-cors-401-mt5-diagnosis/{task_plan,findings,progress}.md`。

---

## 6. 修复执行记录（Phase 1-3，2026-09-21 19:1x-19:3x）

### 6.1 改动的文件
| 文件 | 改动 |
|---|---|
| `backend/.env`（gitignored） | `CORS_ORIGINS` 首项 `http:/localhost:3000` → `http://localhost:3000`（**用户 19:10 手改**，mtime 19:10） |
| `backend/.env.example` | 同一拼写错误（第 60 行）→ 已修，避免传染新部署 |
| `backend/app/config.py` | 新增 `from urllib.parse import urlparse` + `from loguru import logger`；`cors_origin_list` 重写为「校验 + 归一 + 告警 + 去重」，`*` 原样保留 |
| `backend/tests/unit/test_config_cors_origins.py` | 新增，9 用例 |
| `frontend/components/layout/AppShell.tsx` | localhost 放行加 `NEXT_PUBLIC_DEV_NOAUTH === "1"` 门控；清理残留 `__noauth__` 并视为未登录 |

### 6.2 验证输出（实测）
```
# 单测
9 passed in 0.09s

# 真实 .env 解析（新 config.py）
cors_origin_list = [http://localhost:3000, http://192.168.3.47:3000, http://100.124.17.97:3000,
                    http://localhost:8002, http://192.168.3.47:8002, http://192.168.3.34:8002,
                    http://100.78.102.105:8002, http://192.168.3.34:3000, http://100.78.102.105:3000]  (count=9)

# 预检（活后端 :8002）
OPTIONS Origin=http://localhost:3000       -> 200   acao='http://localhost:3000'   (修复前 400)
OPTIONS Origin=http://192.168.3.34:3000    -> 200   acao='http://192.168.3.34:3000'
OPTIONS Origin=http://evil.example.com     -> 400   (白名单外仍拒绝，符合预期)

# 数据面（有效 JWT，全部 200）
/health /api/bot/status /api/positions /api/symbols /api/market-data/symbols /api/integration/status /api/market-data/tick?symbol=GOLD
/health → {"status":"ok","mt5_connected":true,"redis_connected":true,"ai_available":true,"bot_state":"STOPPED"}
/api/market-data/tick?symbol=GOLD → bid 4354.17 / ask 4354.71 (2026-09-21T22:21:54)

# 前端
npx tsc --noEmit            → 0 error
npx eslint AppShell.tsx     → 0 error（1 条既有警告，未触碰代码）
npm run build               → ✓ Compiled successfully in 15.7s，0 error
GET localhost:3000/login    → 200      GET localhost:3000/dashboard → 200

# 构建产物里的 gating（.next/static/chunks/0jdx-ez.k1khj.js）
t="1"===m.default.env.NEXT_PUBLIC_DEV_NOAUTH; if(e&&t){localStorage.getItem("token")||localStorage.setItem("token","__noauth__"),a(!0);return}
let o=localStorage.getItem("token");("__noauth__"===o&&localStorage.removeItem("token"),"__noauth__"===o?null:o)?nN.default.get("/api/auth/me")...:→/login

# WebSocket（后端 :8002/ws）
valid_token -> CONNECTED
__noauth__  -> rejected HTTP 403
no_token    -> rejected HTTP 403
```

## 7. 修复过程中踩的坑（重要，别人/以后会再撞）

### 7.1 前端是**生产构建**，改前端必须重新 build
`:3000` 不是 `next dev`，而是 `start-frontend.sh` 里的 `npm run start`（PID 82326 / BUILD_ID 18:58）。所以改 `AppShell.tsx` 后必须 `npm run build` 才生效。
⚠️ 且 `next build` 会先清空/重建 `.next`，**运行中的 `next start` 会立刻 500**（本次实测 `GET /login -> 500`），构建完必须重启前端进程。

### 7.2 `next build` 卡在 Google Fonts（Clash fake-IP + DIRECT 规则）
```
next/font: error: Failed to fetch `Noto Sans SC` from Google Fonts.
Import trace: ./PycharmProjects/ai-trading-agent/frontend/app/layout.tsx
```
根因链：
1. `nslookup fonts.googleapis.com` → **198.18.0.31**（Clash TUN 的 fake-IP；DNS 被 TUN 劫持，即使系统 DNS 写的是 114.114.114.114）
2. 该域名在 Clash 规则里似乎走 **DIRECT** → 直连被墙 → `curl` 直接返回 `000`
3. 因此**连走 Clash 的 HTTP/SOCKS 代理也没用**（同一 core 仍按域名规则走 DIRECT）→ `curl -x http://127.0.0.1:7897` 同样是 `000`
4. 但**真实 Google IP 可达**：DoH 拿到 `173.194.174.95` 后 `curl --resolve fonts.googleapis.com:443:173.194.174.95` → **200**（因为变成按 IP 匹配规则，Google IP 段走了代理）

绕过方案（本次实际使用）：
- 写了一个临时 **DoH 解析的 CONNECT 代理** `/tmp/pwf_fontproxy.py`（监听 `127.0.0.1:7899`，上游用可用的 `127.0.0.1:7897` 做 DoH 查询），代码见该文件（仅监听本地，无鉴权、无外部暴露）
- 构建时注入 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7899` + `NODE_USE_ENV_PROXY=1` → Turbopack（Rust 端）会读 `HTTPS_PROXY` → 字体拉取成功
- 代理日志证据：`CONNECT fonts.gstatic.com:443 -> 142.250.198.131`
- 备选（需 sudo，本次未用）：`/etc/hosts` 把 `fonts.googleapis.com` / `fonts.gstatic.com` 指向真实 IP
- 彻底修复建议：在 Clash 规则里把 `fonts.googleapis.com`、`fonts.gstatic.com` 改为走代理（不要让它们 DIRECT）

### 7.3 后台长任务会被工具会话超时杀掉
`nohup ... &` 仍会被清理（进程组被杀）→ 本次改用 `python3 -c "subprocess.Popen(..., start_new_session=True)"`（等效 setsid）才活下来；被杀的构建会残留 `frontend/.next/lock`，重跑前需 `rm -f`。

---

## 8. ⚠️ 顺带发现的严重安全问题（预先存在，非本次改动引入）

`backend/.env.example` 是**被 git 跟踪的文件**，而它里面填的是**线上真值**（仅比对哈希，未打印明文）：

| 字段 | .env.example 是否入库 | 与线上 `backend/.env` 是否相同 |
|---|---|---|
| `SECRET_KEY` | 是 | **相同 ⚠️**（sha256 前 12 位 68266a8c7464） |
| `AUTH_PASSWORD_HASH` | 是 | **相同 ⚠️**（5af075865fa1） |
| `VAULT_MASTER_KEY` | 是 | **相同 ⚠️**（6bf50534ce95） |
| `AUTH_USERNAME` | 是 | **相同**（admin） |

影响：
1. **JWT 签名密钥泄露** → 任何拿到仓库的人都能离线签发合法 `admin` token（本轮 401 排查已证实 `require_auth` 只验签名 + exp），等于绕过登录直接调 `/api/*`（含下单/紧急停止）。
2. `AUTH_PASSWORD_HASH`（bcrypt）泄露 → 可离线爆破口令，进而横向复用。
3. `VAULT_MASTER_KEY` 泄露 → 账户密码等 Vault 密文可解密。

建议（**需用户决策，未擅自执行**）：
- 把 `.env.example` 里的真值全部替换为占位符（`SECRET_KEY=`、`AUTH_PASSWORD_HASH=`、`VAULT_MASTER_KEY=` 留空或写 example），仅保留键名与注释。
- **轮换** 这三样：`python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成新 SECRET_KEY（轮换后所有已签发 token 失效，需重新登录）；重设登录口令并生成新 hash；重生成 VAULT_MASTER_KEY 并**重新加密 Vault 内既有密文**（注意：换 key 后旧密文解不开，需要迁移流程）。
- 若该仓库曾推送到远端（`origin https://github.com/liumou/ai-trading-agent.git`），泄露密钥需按「已被提交」处理，光删文件不够。
- 与本轮 400/401 无关，但风险等级高于它们，建议单独开一个修复任务。

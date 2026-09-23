# Findings & Decisions

## 任务：局域网与公网访问修复

### 现象
- 本机（Mac）访问 `localhost:3000` 正常
- 局域网其他设备访问 `http://<Mac-IP>:3000` 报错
- 需求：修复局域网访问 + 提供公网访问服务

### 环境拓扑（实测）
| 组件 | 监听地址 | 端口 |
|------|----------|------|
| Next.js dev (v16.2.3) | `*:3000` | 3000 |
| FastAPI 后端 (uvicorn) | `0.0.0.0:8002` | 8002 |
| Mac 当前局域网 IP | `192.168.3.34` | - |

注意：后端未跑在 8000（生产 Railway 默认 `8080`），本机开发统一用 8002。

### 根因链（已确认）
1. **`.env.local` 覆盖 `.env`**（Next.js 加载优先级 `.env.local` > `.env`）：
   - `frontend/.env`（正确但不生效）：`NEXT_PUBLIC_API_URL=http://192.168.3.34:8002`
   - `frontend/.env.local`（实际生效·错误）：`NEXT_PUBLIC_API_URL=http://localhost:8002`
   - → 局域网设备浏览器对**自身** `localhost:8002` 发请求 → API 全部失败 → 页面框架能显示但数据/操作全报错
2. **`next.config.ts` 的 `allowedDevOrigins` 是旧 IP**：
   - 配置为 `192.168.3.47` 与 `100.124.17.97`，当前 Mac IP 为 `192.168.3.34`
   - Next 16 dev 对非白名单 Origin 会拦截跨域（HMR/内联）→ 局域网访问报错的叠加因素
3. **IP 硬编码不可持久**：`.env` 手动写死 `192.168.3.34`，DHCP 重连即失效

### 已验证不成立的可能根因
- 前端只在 `127.0.0.1` 监听？→ No，`*:3000` 全接口监听 ✓
- 后端 8002 局域网不可达？→ 实测 `192.168.3.34:8002/api/*` 返回 401（证明可达，CORS 头也正确）✓
- macOS 应用防火墙拦截？→ 待补充检查
- 静态资源 404？→ `/_next/static/chunks/webpack.js` 404 是 dev 模式正常现象（按需编译），非根因

### 页面本身
返回 200，HTML 完整（含中文标题「交易机器人」），说明前端页面服务正常。问题聚焦在**浏览器端 JS 的 API 请求目标**。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 【已实施】API 地址前端运行时自动推导（`协议://window.location.hostname:BACKEND_PORT`，端口默认 8002） | 本机/局域网/Tailscale 通吃，生产包不再埋死 IP/localhost；`.env` 注释化，`NEXT_PUBLIC_API_URL` 仍可显式覆盖（公网反代） |
| 【已实施】`allowedDevOrigins` 更新为当前本机 IP + localhost + Tailscale IP | dev 模式跨站白名单（生产模式不读，非本次根因但顺带修正） |
| 【已定】公网方案 = **Tailscale**（用户选定） | 已有 Tailscale（本机 `100.78.102.105`）；私密、无公网 IP、任意 tailnet 设备经 `http://100.78.102.105:3000` 访问 |
| 【建议未落地】CORS 用 `allow_origin_regex` 泛化防止 IP 漂移 | 当前 `allow_credentials=True` 白名单模式；换 IP 需手动追加 `CORS_ORIGINS`。改动需重启后端，建议空闲时做 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| `NEXT_PUBLIC_API_URL` 在 `.env`(IP) 与 `.env.local`(localhost) 冲突 | 统一策略：前端自动推导同源后端地址，删除冲突 env |
| `allowedDevOrigins` IP 过期 | 需要覆盖任意来源或正则，见方案 |

## Resources
- Next.js 16 env 优先级：`.env.development.local` > `.env.local` > `.env.development` > `.env`
- Next.js `allowedDevOrigins` 说明（dev 模式跨域白名单）
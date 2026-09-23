# Task Plan: 局域网与公网访问修复

## Goal
修复局域网其他设备无法访问前端的问题，并提供可用的公网访问服务。

## Next Step
交付总结 + 可选 CORS 加固（换 IP 防复发，需重启后端）。

## Current Phase
Phase 5

## Phases

### Phase 1: Requirements & Discovery
- [x] 确认现象：本机正常、局域网设备报错
- [x] 定位根因：生产包嵌入 `localhost:8002`，局域网设备请求自身
- [x] 确认环境：后端 8002、前端 3000（生产模式 `npm run start`）
- **Status:** complete

### Phase 2: Planning & Structure
- [x] 确定修复方案（见 Decisions）
- [ ] 与用户确认公网方案
- **Status:** in_progress

### Phase 3: Implementation
- [x] 修改 `frontend/lib/api.ts`：运行时推导后端地址（window.location.hostname）
- [x] 修改 `frontend/lib/websocket.ts`：同上
- [x] 处理 `.env` / `.env.local` 冲突（改为注释说明，自动推导生效）
- [x] `next.config.ts` allowedDevOrigins 兼容局域网/Tailscale（dev 场景）
- [x] 重建生产包（npm run build）并重启前端
- **Status:** complete

### Phase 4: Testing & Verification
- [x] 本机验证 API 请求正确（tsc 通过、推导逻辑单元仿真正确）
- [x] 局域网设备访问验证（IP Origin → 后端 200 + CORS 头正确）
- [x] Tailscale 公网链路验证（前端/后端 via Tailscale IP 均 200，CORS 正确）
- [x] 客户端 chunk 确认推导逻辑已嵌入（无非浏览器依赖）
- **Status:** complete

### Phase 5: Delivery
- [ ] 文档更新（README / CLAUDE.md 网络访问说明）
- [ ] 交付使用说明
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 【确定】API 基址改为**运行时自动推导**：`http://<window.location.hostname>:<BACKEND_PORT>`，端口由 `NEXT_PUBLIC_BACKEND_PORT`（默认 8002）控制 | 生产包不再硬编码 localhost/IP；无论本机、局域网任意 IP、Tailscale IP、公网域名访问前端，API 自动指向同一主机的后端，彻底消灭 env IP/localhost 冲突 |
| 【确定】`allowedDevOrigins` 放行任意来源（dev 场景） | dev 场景任意 IP 可访问，避免 IP 过期 |
| 【待确认】公网方案：Tailscale（已有，私密稳定）/ cloudflared 隧道（临时公网链接）/ ngrok | 视用户偏好与使用场景 |
| 【确定】后端已监听 `0.0.0.0`，且 CORS 已含常见 IP——无需改后端 | 后端链路已验证可用 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| GateGuard 拦截规划文件写入 | 先陈述文件调用关系与用户指令再重试 |
| 误判为 dev 模式 | 实测进程命令为 `npm run start`（生产模式），改按生产包排查，找到嵌入值 |

## Implementation Detail (api.ts 修改设计)

```typescript
// 运行时推导后端地址：与前端同主机的 BACKEND_PORT
function resolveApiBaseUrl(): string {
  if (typeof window === "undefined") return "http://localhost:8002"; // SSR 安全兜底
  const host = window.location.hostname;
  const port = process.env.NEXT_PUBLIC_BACKEND_PORT || "8002";
  const protocol = window.location.protocol === "https:" ? "https" : "http";
  return `${protocol}://${host}:${port}`;
}

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || resolveApiBaseUrl(),
  timeout: 10000,
});
```

- `NEXT_PUBLIC_API_URL` 仍可显式覆盖（生产公网 CDN 场景需要）
- 未设置时自动推导，dev/局域网/Tailscale 通吃
- WebSocket 同法推导 `ws://<host>:8002/ws`（http→ws, https→wss）

## 公网方案对比（待用户选）
| 方案 | 特点 | 适用 |
|------|------|------|
| Tailscale Funnel/共享 | 已有 Tailscale，私密零配置，仅 tailnet 内设备可访问 | 个人/小团队，最稳 |
| cloudflared tunnel | 临时公网 URL，无需公网 IP，自带 TLS | 对外演示/临时分享 |
| 路由器端口转发+DDNS | 真正公网入口，需公网 IP 与路由器权限 | 长期自建 |

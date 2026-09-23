import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

// dev 模式下跨站请求防护白名单（仅 dev 生效，生产模式不读取）。
// 局域网/Tailscale 的来访主机动态变化，无法全部静态列举——
// 常见本地入口在此兜底即可；新的局域网/Tailscale IP 只需按需追加。
// 生产模式（npm run start / Railway）不受此配置影响。
const nextConfig: NextConfig = {
  allowedDevOrigins: [
    "http://localhost:3000",
    "http://192.168.3.34:3000",
    "http://100.78.102.105:3000",
  ],
};

export default withNextIntl(nextConfig);

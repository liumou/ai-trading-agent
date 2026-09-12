import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

const nextConfig: NextConfig = {
  allowedDevOrigins: ["http://192.168.3.47:3000", "http://100.124.17.97:3000"],
};

export default withNextIntl(nextConfig);

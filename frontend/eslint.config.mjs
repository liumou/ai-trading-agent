import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // React ESLint plugin flags setState inside useEffect as an error, but
      // that is a legitimate pattern for async auth flows, fetch results, etc.
      // Downgrade to warning so CI does not fail on idiomatic code.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // 本仓库历史上把 Python 虚拟环境建在 frontend/ 下（已在 .gitignore），
    // 其 site-packages 里带第三方 JS（matplotlib web backend）。CI 没有
    // .venv，所以不忽略会让本地 lint 多报 5 个来自 matplotlib 的
    // no-this-alias error，与 CI 结果不一致 —— 这里一并排除。
    ".venv/**",
    "venv/**",
  ]),
]);

export default eslintConfig;

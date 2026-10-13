# Findings & Decisions

## Requirements
- 用户要求将后端所有风控拦截消息（reason）从英文转为中文
- 用户通过 AskUserQuestion 明确选择「全通道后端消息中文化」范围
- 覆盖链路：discipline_gate → order_preflight → guardrails → manual_order_gate → 前端 ReviewResultCard

## Research Findings
- **前端无翻译层**：`frontend/components/trading/ReviewResultCard.tsx` line 67 直接渲染 `{review.reason}`，没有 next-intl 翻译兜底 —— 后端必须直接出中文
- **拦截消息链路**：discipline_gate.py → order_preflight.py → guardrails.py → manual_order_gate.py → 前端，四个文件共用
- **position_close.py 是遗漏文件**：手动平仓闸门（DELETE /api/positions）也有用户可见英文消息，需纳入
- **英文专有名词保留**：SL/TP/BUY/SELL/MICRO/SHADOW 等交易领域术语在中英文消息中保留（用户可读性更好）
- **`_ai_error_event` detail 进 DB 事件**：manual_order_gate 的 LLM 失败 detail 拼进 `AI_AGENT_ERROR` 事件消息，前端 activity 页可见

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 全通道后端消息中文化 | 前端直接渲染后端 reason，无翻译层 |
| 保留英文交易术语 | SL/TP/BUY/SELL 等领域术语中英混排更清晰 |
| discipline_month_key import 加 noqa F401 | 测试 monkeypatch.setattr(dg, ...) 依赖模块级绑定 |
| `_build_snapshot` 保留调用删赋值 | 函数有副作用（emotion/spike flags 计数），返回值未被使用 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Python `b"当日亏损"` 语法错误 | bytes 只能含 ASCII，改 `"当日亏损" in e.decode()` |
| ruff 误删 `discipline_month_key` import | 加回 import + `# noqa: F401`（测试 monkeypatch 目标） |
| ruff F841 snapshot 未使用 | 删除赋值保留调用（`_build_snapshot` 有副作用） |
| test_guardrails 6 处英文断言漏改 | 逐个更新为中文子串断言 |
| ruff 误删 `_discipline_local_weekday` 局部 import | 恢复局部 import —— 该函数 docstring 明确声明「patch 源模块生效」，模块级绑定读不到补丁（WEEKEND_CLOSE 测试隔离失效） |
| spike_chase 前缀残留英文 | `spike_chase block:`/`warn:` → 「追涨杀跌拦截/警告（spike_chase）」，特性名保留 |
| `_parse_flip` count 段无防护 | 迁移期脏数据 ValueError → fail-closed 误拒，加 try/except 归 0 |
| `next/font/google` 下载失败（104 报错堆栈） | 改用系统字体栈，移除构建期网络依赖 |
| `--font-heading` 自引用循环 | 修正为 `var(--font-sans)`（git 历史原定义即如此） |

## 代码审查（/code-review）发现
- **双轴并行子代理**：Standards 轴（文档化标准 + Fowler smell baseline）+ Spec 轴（`.planning/` 两份 spec）
- **Spec 轴阻断发现**：ruff --fix 静默删除了 `_discipline_local_weekday()` 内局部 import，改用模块级绑定 → monkeypatch 源模块失效。测试今天通过只因 2026-10-13 是周二（weekday<5）；真实周末固定到周三的测试会被 WEEKEND_CLOSE 误拦
- **Standards 轴风险发现**：`_parse_flip` 第 3 段 count 无 try/except（第 4 段 flip_today 有防护），迁移期回退路径遇脏数据 → fail-closed 误拒单
- **Standards 轴重复发现**：flip key 模板 3 处手写 → 提取 `_flip_key` helper
- **未修判断**：rollout 术语混排不一致（guardrails「影子模式（shadow）」vs manual_order_gate「live 或 paper」vs position_close 裸插值）；旧 key 迁移期仍全通道共享（已注释声明为权衡）

## 前端字体编译失败（Phase 7）发现
- **编译失败是误报**：tsc exit 0、build 成功、dev server Ready、eslint 0 errors —— 编译本身没失败；用户看到的满屏红色堆栈全部来自 `next/font/google` 构建期网络下载
- **`next/font/google` 构建期硬依赖 Google Fonts**：`Noto_Sans_SC`/`Noto_Sans_Thai` 需从 fonts.gstatic.com 下载 woff2，国内网络不可达 → 104 个 module-not-found 堆栈 + 字体静默缺失
- **Turbopack（Next.js 16）降级为 warning**：字体下载失败不打断构建（exit 0），构建产物 CSS 无 `@font-face` 确认字体缺失 —— 线上字体回退浏览器默认，需检查才能发现
- **`--font-heading` 自引用循环**（git 历史 commit dd8a44 引入）：`var(--font-heading)` 引用自身是无效声明，card.tsx/dialog.tsx 的 font-heading 类一直拿不到字体。git 历史显示原定义即 `var(--font-sans)`，修复为系统字体栈
- **修复方案**：改用系统字体栈（苹方/微软雅黑/系统 CJK），`--font-sans` 保留 Noto Sans 家族作为用户本机已有字体的后备；彻底移除构建期网络依赖，Vercel 生产不再下载字体

## Resources
- `backend/app/services/discipline_gate.py`
- `backend/app/services/order_preflight.py`
- `backend/app/services/manual_order_gate.py`
- `backend/app/services/position_close.py`
- `backend/mcp_server/guardrails.py`
- 测试：`backend/tests/unit/{test_guardrails,test_phase_f,test_position_close,test_manual_order_gate,test_review_fixes}.py`

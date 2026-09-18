# Progress Log

## Session: 2026-09-18

### Current Status
- **Phase:** P2.5 + V5（remark-gfm） — **已完成**
- **Started:** 2026-09-18

### Actions Taken

**计划评审（第三轮）**
- 逐文件核对计划自述 vs 实际代码，输出 `plan-critic-round3.md`（REVISE）
- 计划升级为 V3：新增 §2.4 P2.5 收口阶段、修正 §1.8 markdown 状态、回填 task_plan 失真
- 修正 2 项「实现合理但偏离计划」：`Intl.DateTimeFormat`（非 date-fns）、`Bot` 图标（非 Sparkles）——改计划而非改代码

**P2.5 收口实施**
- M-1：拆 `run-events.tsx`（时间线折叠、active/failed 自动展开）；agents 整体折叠为 `AgentsSection` + `AgentCard` 单卡展开；budget 改 `BudgetBadge` + Tooltip 六项明细
- M-2：新增 `ChatEmptyState`（EmptyState 图标 + 建议提问卡 `grid sm:grid-cols-3`），空态由 `ConversationThread` 的 `empty` slot 接入
- M-3：`RunPanel` 在 `!detail` 时 `return null`（原渲染 `newRunHint` 与空态矛盾）
- M-4：`globals.css` 加 `prefers-reduced-motion` 媒体查询（仅处理实际存在的动画类，`*` 通配只压时长不隐藏元素）
- M-5：`page.tsx` `h-[calc(100vh-4rem)]` → `100dvh`（iOS 软键盘遮挡）
- M-9：`.gitignore` 加 `.serena/`
- m-1：`MessageBubble` 加 `memo`（轮询期跳过引用未变的消息重渲）
- 自动滚动锚定：`ConversationThread` 加「回到底部」按钮，用户上滚时暂停自动滚；page 传 `scrollSignal`
- 建议卡点击填入输入框 + 聚焦（`focusSignal`，`InputComposer` 内部 `querySelector` 定位）
- `sendHint`（⏎ 发送）提示接入免责声明行

**V5：remark-gfm 引入（用户批准）**
- 安装 `remark-gfm@4.0.1`（18 个传递依赖，unified 生态 v11 兼容）
- `message-bubble.tsx` 接入 `remarkPlugins={[remarkGfm]}`，恢复 `th`/`td`/`del`/`input` 映射
- 表格：外层 `div` 横向滚动包裹 + `th`（bg-muted/70 + font-semibold）+ `td`（border-t + align-top）
- 任务列表：`- [ ]`/`- [x]` 的 disabled checkbox 替换为自定义 `<span>`（避免原生勾选框外观，保持只读语义）
- 删除线：`~~text~~` → `<del class="text-muted-foreground line-through">`
- **安全边界实测**：5 个 XSS 攻击向量（`<script>`/`<img onerror>`/`javascript:`/`data:`/`vbscript:`）**全部被阻断**——react-markdown 把危险协议净化为空 `href=""`，原始 HTML 转义为纯文本，无事件属性穿透。`remark-gfm` 是语法扩展（表格/任务列表/删除线），不解析原始 HTML，不破坏 XSS 防线。`rehype-raw` 仍未引入。

**i18n 真实缺陷修复（3 处，本轮新发现）**
1. en 文件 **13 个 key 全是中文**（英文环境直接显示中文）→ 逐一翻译；同时发现上一轮曾把原有 `cancelRun` 中文/英文改写，已恢复原文以最小化 diff
2. `formatSessionRelative` **硬编码中文**（「刚刚」「N 分钟前」）→ 改 `Intl.RelativeTimeFormat` + locale 参数，`session-list` 传 `useLocale()`
3. `t.raw()` **缺失 key 返回 fallback 字符串而非 undefined** → `ChatEmptyState` 的 `?? []` 守卫失效会导致 `.map()` 崩溃，改 `Array.isArray`

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| `npx tsc --noEmit` | 零类型错误 | 通过（多次全绿） | ✅ |
| `npm run build` | 编译通过 | 524 个错误**全部是 `fonts.gstatic.com` 下载 `.woff2` 失败**（环境网络不可达），零代码级错误 | ⚠️ 环境阻塞 |
| 代码级构建错误 | 无 | 非字体相关错误数 = 0 | ✅ |
| 后端 `test_agent_chat.py` | 全绿 | 19 passed in 0.75s | ✅ |
| i18n zh/en key 双向差集 | 空 | 空（各 101 key） | ✅ |
| en 文件残留中文 | 无 | 无（已修 13 处） | ✅ |
| `prefers-reduced-motion` | 存在 | `globals.css` 已加，仅覆盖实际使用的动画类 | ✅ |
| 移动端 `100dvh` | 已替换 | 已替换，`100vh` 零残留 | ✅ |
| remark-gfm 表格渲染 | 可用 | 端到端实测：`<table><thead><th>`+`<tbody><td>` 正确生成 | ✅ |
| remark-gfm 安全边界 | 未破坏 | 5 个 XSS 攻击向量全部被阻断 | ✅ |
| markdown 语法渲染能力 | 全可用 | **11/11 全可用**（V5 引入 remark-gfm 后）：✓ h2/h3 ul ol strong em code pre a blockquote hr p table 任务列表 删除线 | ✅ |

### Errors
| Error | Resolution |
|-------|------------|
| 会话创建 422（前端传 run 层 `single`，后端 `SessionCreateRequest` 只接受会话层枚举） | 前端两处改传 `"free"` + 补 `test_create_session_accepts_v1_mode` |
| naive/aware datetime 混用导致 7 小时偏差 | `chat-utils.ts` `parseChatDate` 统一 naive 串补 `Z` 视作 UTC |
| 计划状态记录与实际代码系统性失真 | 第三轮评审逐项核查，迭代为 V3/V4 |
| `color-mix()` 不被 Turbopack 当前 CSS 管线支持（构建失败） | 移除该用法，表格单元格样式改用项目内已有先例的 Tailwind 类 `bg-muted/70` |
| `.serena/` 无 gitignore 规则会误提交 IDE 索引 | 加入 `.gitignore` |
| **M-7 前提不成立**：`react-markdown@10.1.0` 默认不解析表格（GFM 扩展），表格渲染为纯文本管道符 | 补的 `th`/`td` 映射永不触发，属死代码，已回退；支持表格需引入 `remark-gfm`（新增依赖，与 §1.8 安全边界冲突），单独立项 |
| `t.raw()` 缺失 key 返回 fallback 字符串，`?? []` 守卫失效 | 改 `Array.isArray` 守卫 |
| en 文件 13 个 key 为中文、`formatSessionRelative` 硬编码中文 | 翻译 + 改 `Intl.RelativeTimeFormat` |

### Blocked / 待用户决策
- **`npm run build` 无法端到端完成**：本环境到 `fonts.gstatic.com`/`fonts.googleapis.com` 网络不可达（SSL_ERROR_SYSCALL），524 个错误全部是字体文件下载失败。代码侧已确认零错误（tsc 全绿、CSS 括号配平、`color-mix` 清零、非字体相关错误数 = 0）。联网环境重跑即可。
- `.planning/2026-09-18-auto-adopted-notification-fix/` 为其他会话遗留，不在本会话范围，未代删。

# Agent 对话页重新设计 — 综合方案 V2（已批准执行）+ V3 收口修订

> 状态：**2026-09-18 用户批准全部（P0→P3）**。用户决策：D1=引入 vitest；D2=markdown 本次一并实现。
> **V3 修订（2026-09-18 第三轮评审后）**：计划状态记录与实际代码系统性失真，评审见 `.omc/research/chat-redesign/plan-critic-round3.md`。原 P2/P3 编号已与实际执行错位，**新增 §2.4 收口阶段 P2.5**，原 P2/P3 标注真实完成度。
> 探讨参与：designer（视觉方案）/ critic（约束评审×3）/ planner（实施结构），文档见 `.omc/research/chat-redesign/`。
> 关联背景：`.planning/2026-09-17-agent/chat-v2-plan.md`（本任务是其 P4「页面」部分）。

## V3 实测状态总览（以代码为准，非计划自述）

| 阶段 | 计划自述 | 实际 | 判定 |
|------|---------|------|------|
| P0 CRITICAL 修复 | 待办 | 已完成（两处 `mode:"free"` + 后端测试） | ✅ 但任务记录未回填 |
| P1 组件抽取 | 待办 | 已完成（5 组件 + tsc/build 通过） | ✅ 但任务记录未回填 |
| P2 视觉 + 信息结构 | 待办 | **仅完成时区统一与气泡样式**；RunPanel 折叠、空状态引导、budget Tooltip 未做 | 🔶 部分 |
| P3 动效 / 移动端 | 待办 | **未完成**（无 `100dvh`、无 `prefers-reduced-motion`、无 `React.memo`） | ❌ |
| markdown（原 §1.8 定为不做） | 不做 | **已接入** react-markdown | ⚠️ 超范围，见 §1.8 |
| i18n | 待补 | zh/en 各 103 key，双向差集为空 | ✅ |

**P2.5 收口结果（V4 实测）**：✅ 已完成 M-1（RunPanel 拆 `run-events` 折叠 + budget Tooltip）、M-2（`ChatEmptyState` + 建议卡）、M-3（`!detail` return null）、M-4（`prefers-reduced-motion`）、M-5（`100dvh`）、M-6（§1.8 改写）、M-9（`.gitignore`）、m-1（`MessageBubble` memo）；⛔ **M-7 取消**（前提不成立，见 §1.8）；✅ 额外修复 i18n 真实缺陷 3 处（见下）。

**P2.5 额外发现并修复的 i18n 真实缺陷（V4）**：
1. **en 文件 13 个 key 全是中文**（`cancelRun`/`emptyHeading`/`emptyDesc`/`suggestTitle`/`suggestItems`/`expandTrace`/`collapseTrace`/`expandAgents`/`collapseAgents`/`sendHint`/`sessionUpdatedAgo` 等）——英文环境直接显示中文，已翻译修正。
2. **`formatSessionRelative` 硬编码中文**（「刚刚」「N 分钟前」）——改为 `Intl.RelativeTimeFormat` + locale 参数，`session-list` 传 `useLocale()`。
3. **`t.raw()` 缺失 key 时返回 fallback 字符串而非 `undefined`**——`ChatEmptyState` 原 `?? []` 守卫失效会致 `.map()` 崩溃，改为 `Array.isArray` 守卫。

---

## 0. 关键前置发现（必须先行解决）

### 🔴 CRITICAL — 新会话创建 422（会话 mode 语义冲突）

**现状**：前端在「新会话」与「首条消息自动建会话」时传 `mode`（`frontend/app/chat/page.tsx:144,182`），值为 run 层枚举 `"single"`；后端 `SessionCreateRequest.mode` 只接受 V1 会话层枚举 `^(free|trading_plan|report)$`（`backend/app/api/routes/agent_chat.py:24`）。

**后果**：清空 localStorage 的用户点「新会话」或「首条消息」→ 后端 **422**，前端 `showError(t("sendFailed"))`。仅已恢复旧 activeId 的用户能绕过。对话页核心主路径瘫痪。

**修复方向（已定案，第二轮评审确认）**：
- **方案 A（拍板采纳）**：`createChatSession` 两处（`page.tsx:144,182`）改传会话层合法值 `"free"`。最小改动、不碰后端、零测试破坏、不影响 V1。
  - 核实：`startChatRun` 仍传 run 层 `single/experts`（命中 `RunCreateRequest.mode` pattern），不同端点不同字段互不影响；`SessionListItem` 不渲染 session.mode，DB 存 `"free"` 对 UI 零影响。
  - **修复注释必须写明**：「session.mode 已废弃、不承载 UI 语义，真实模式在 run.mode」——避免后人误读两套枚举。
  - **补一条 `create_session` 端点验证测试**（当前后端对该端点零覆盖，是 422 潜伏至今的根因）：断言传 `single` 被拒、传 `free` 通过。
- 方案 B（后端 pattern 放行 `single/experts`）**已否决**：会把两套枚举永久混进同一字段，语义更乱，且该端点无测试兜底。

### 性能确认（critic 核查）

- `use-run-detail.ts` running 期每 2s 轮询且**无条件 `setDetail({...data})`** → 整页每 2s 重渲染，气泡/消息列表无 `React.memo`。折叠审计区为主【必要条件渲染，非 CSS hidden】；同时建议给 `MessageBubble` 加 `React.memo` 或让 `useRunDetail` 无变化时不 setState。
- `date-fns`、`react-markdown` 均已在依赖（零新增）。

---

## 1. 设计方向（综合 designer 方案，采纳 critic 范围约束）

### 设计语言一句话
> 把对话页从「debugger 视图」升级为「交易者视图」：主色 Wise Green 作信任锚点，助手消息从"一坨纯文本"升级为结构化卡片，审计信息（agent 过程 / 事件轨迹）折叠进「展开详情」。

### 1.1 信息架构（三组信息分层）
| 信息组 | 处置 | 依据 |
|--------|------|------|
| 对话内容 | **主视图常驻** | 用户心智主线 |
| 会话管理 | 桌面侧栏常驻 / 移动抽屉（现状合理，增强样式） | 不遮挡 |
| 运行审计（status/agents/events/budget） | **默认折叠**，「状态徽章 + 最终报告」常驻，详情抽屉展开 | 条件渲染降噪 + 降 DOM |

### 1.2 消息气泡（替换现状两色气泡）
- **用户消息**：右对齐，`wise-gradient` 绿色气泡（品牌一致），右下 `rounded-br-sm` 指向头像；`size-7 rounded-full bg-muted ring-border` 头像含 `User` 图标；上方 `caption` 时间戳。
- **助手消息**：左对齐，`bg-card border` 卡片式气泡（与用户填色气泡形成"卡片 vs 填色"对比），`rounded-bl-sm`；`size-7 rounded-full bg-primary/10` 头像含 `Sparkles` 图标。
- **时间戳**：Bangkok 时区（`Asia/Bangkok`，date-fns `formatInTimeZone`），当天 `HH:mm`，跨天 `dd/MM HH:mm`。
  - **⚠️ 时区语义统一（MAJOR-4 前置）**：三类 `created_at` 语义不同——后端消息 naive 无 `Z`（`agent_chat.py:146`）、事件带 `Z`（`chat_runs.py`）、前端乐观消息带 `Z`（`page.tsx:81,209`）。`formatInTimeZone` 对 naive 串解释不确定，混用会错 7 小时（Bangkok UTC+7）。**必须**先定统一转换函数（如 `parseDate(iso): Date`——naive 串补 `Z` 视作 UTC），再渲染时间戳。列入验收项。
- **experts 模式**：气泡左上角 `Badge variant="outline" animate-badge-in` 显示 agent 角色；不同角色可 badge 着不同 accent 色，气泡本体保持中性。

### 1.3 空状态 / 首次进入
- 用现成 `EmptyState` 组件替换一行文字（icon + heading + description）。
- 下方「建议提问卡」：`grid sm:grid-cols-3` 可点击提问项（点击填入输入框）+ 两个 preset 按钮（`wise-gradient glow-hover`）。
- 会话列表空态：小尺寸 EmptyState。

### 1.4 输入区
- 容器化：`rounded-2xl border bg-card p-2 ring-border focus-within:glow-green`，内部 Input 去自身边框，发送按钮 `wise-gradient glow-hover`。
- `active` 时可显示取消运行按钮（`Ban`）；快捷键提示 `caption`。

### 1.5 RunPanel → 交易者视图
- 状态分层：运行中=蓝 outline+Loader、完成=`wise-gradient` 徽章、失败=红 outline+Tooltip 显 reason、中断=琥珀。
- **断线（disconnected）单独分层**：与"中断/取消"区分——中断是 run 被取消，断线是轮询/网络失败（琥珀 `ConnectionStatus` 风格，文案用 `loadFailed`）。
- **Agent 贡献卡**（experts）：每 agent 一张可折叠卡，默认只显 `Badge + 状态 + model + summary` 一行，展开显示 `report`。single 模式隐藏。
- **事件时间线**：默认折叠为一行 `caption`「执行轨迹 (N 步) · Xs」，展开显示 `border-l-2 border-primary/30` 时间线；仅 `active`/`failed` 自动展开。
- **预算**：`Badge + Clock` + Tooltip 展开四项（现状是一行 10px 数字）。
- **`!detail` 时 RunPanel `return null`**（见 MAJOR-2 空态叠加：空态已给引导，newRunHint 与 EmptyState 矛盾）。

### 1.6 动效（全部复用现有 token，零新增）
- 新消息 `animate-fade-in`；助手等待态=三点绿色脉冲 typing 指示（替代 loader 文字）；agent 卡展开 `animate-scale-in`；页面 `page-enter`。
- 滚动锚定：`scrollIntoView smooth`，用户手动上滚时暂停自动滚。

### 1.7 移动端
- 工具栏 `md+` 用 `rounded-xl border bg-card p-2` 卡片包裹；`<md` 折叠为单行（symbol/timeframe 合一）+ 抽屉。
- 会话抽屉 `glass ring-border`；列表项选中左 bar `border-l-2 border-primary`；移动端删除按钮常显。
- 气泡宽度移动端 `max-w-[92%]`，头像 `size-6`。
- **键盘弹起适配**：根容器 `h-[calc(100vh-4rem)]` 改 `h-[calc(100dvh-4rem)]`（iOS 软键盘下 `100vh` 不收缩导致输入区被遮挡），或加 `viewport-fit`/`visualViewport` 监听（MAJOR-3）。

### 1.8 Markdown 富文本 —— ⚠️ 实际已实现（原定为「本阶段不做」）

- `react-markdown@10.1.0` **已在依赖**（frontend/package.json:28），dashboard 已在用。
- **V2 原定**：本阶段不做（行为变更 + 需 sanitize 策略），推迟到后续里程碑。
- **V3 实测**：`message-bubble.tsx` 已接入 `ReactMarkdown`，13 个自定义组件映射（h1-h3/ul/ol/table/code/p/strong/em/a），映射到 globals.css token（`heading-3`、`wise-gradient-text` 等）。
- **既有安全边界（V5 实测核实，须保留不得放宽）**：
  - 仅 assistant 消息启用；user 消息走 `whitespace-pre-wrap` 纯文本（`message-bubble.tsx`）✅
  - **无 `rehype-raw`** → AI 输出中的原始 HTML 不会被解析执行，XSS 主路径已阻断 ✅（V5 实测：5 个攻击向量全部被净化）
  - **`remark-gfm@4.0.1` 已引入**（V5，用户批准）→ 解析表格/任务列表/删除线/自动链接；**不解析原始 HTML**，不破坏 XSS 防线 ✅
  - 外链统一 `target="_blank" rel="noopener noreferrer"`；危险协议（`javascript:`/`data:`/`vbscript:`）被 react-markdown 净化为空 `href=""` ✅
- **表格 —— ✅ V5 已落地**（引入 `remark-gfm@4.0.1`，用户批准）。原 M-7 前提不成立问题已解决：表格、任务列表、删除线均可渲染。
- **安全边界实测（V5，引入 remark-gfm 后）**：5 个 XSS 攻击向量（`<script>`/`<img onerror>`/`javascript:`/`data:`/`vbscript:`）**全部被阻断**——react-markdown 把危险协议净化为空 `href=""`，原始 HTML 转义为纯文本，无事件属性、无 `<script>` 标签穿透。`remark-gfm` 是**语法扩展**（表格/任务列表/删除线/自动链接），不解析原始 HTML，不破坏 XSS 防线。`rehype-raw` 仍**未引入**（HTML 解析插件才会破坏边界）。
- **V5 渲染能力清单（11/11 全可用）**：✓ h2/h3 标题、ul、ol、strong、em、inline code、pre 代码块、a 链接、blockquote、hr、p、**table（th/td）**、任务列表（`- [ ]`/`- [x]`，自定义 span 替代 disabled checkbox）、删除线（`~~text~~`）。
- **designer 的三层结构化卡片（结论/依据/引导）**：依赖 markdown 深度解析，仍推迟到 P4（本阶段未做）。
- **会话预览 / 运行徽章**（列表项消息摘要、活跃 run 状态）：后端 `list_sessions` 均无此字段 → **当前不做**（画饼项，需后端加字段，同列 P4）。

---

## 2. 组件拆分与分阶段实施

### 2.1 目标组件清单（`components/chat/` 下拆）
| 文件 | 组件 | 职责 | props 草案 |
|------|------|------|-----------|
| `message-bubble.tsx` | `MessageBubble` | 单消息气泡（user/assistant），含头像/时间/角标 | `{ msg }` |
| `conversation-thread.tsx` | `ConversationThread` | 滚动容器 + 消息列表 + 空态 + waiting 指示 | `{ messages, empty, thinking, children }` |
| `session-list.tsx` | `SessionList` | 会话列表（桌面/移动复用），删除、相对时间（`updated_at`）；**不含运行徽章**（无数据） | `{ sessions, activeId, loading, onSelect, onDelete, onNew }` |
| `chat-toolbar.tsx` | `ChatToolbar` | symbol/timeframe/mode Select + preset + experts hint | `{ symbols, ...selectors, onPreset, submitting }` |
| `input-composer.tsx` | `InputComposer` | 输入框 + 发送 + Enter/IME + 取消运行 | `{ value, placeholder, submitting, onSend, onCancel }` |
| `run-events.tsx` | `RunEvents` | 事件流 + 折叠（自 run-panel 拆出） | `{ events }` |
| `run-panel.tsx` | `RunPanel`（改造） | 状态徽章 + 最终报告 + agent 贡献卡折叠 | props 契约不变 |
| `use-chat-session.ts` | `useChatSession`（**本轮不拆**） | 会话状态管理 hook——`submit` 耦合 activeId/runId/saveSelection/loadMessages，抽 hook 扰动幂等键/localStorage/事件累加时序，收益仅 page 瘦身 | — |

### 2.2 阶段路径（P0→P3，每阶段验收通过再推进）

**Phase 0 — CRITICAL 修复（独立提交，剥离视觉改动）**
- `page.tsx:144,182` 两处 `mode` 改传 `"free"` + 注释说明「session.mode 已废弃、不承载 UI 语义」。
- 新增一条 `create_session` 端点验证测试（后端）：传 `single` 被拒、传 `free` 通过。
- 验收：清 localStorage 点「新会话」/首条消息成功、不 422；`pytest` 相关测试通过。
- **独立 commit、独立评审**——1 行修复不混入视觉 diff。

**Phase 1 — 组件抽取（纯搬运，零视觉改动）**
- 抽 `message-bubble` / `conversation-thread` / `session-list` / `chat-toolbar` / `input-composer` 五个纯展示组件到 `components/chat/`，`page.tsx` 瘦身为编排者。
- Enter/IME 判断迁入 `input-composer`（优先手测中文/日文 IME）。
- 验收：tsc/build/lint 全过；新建/切换/发送/预设/删除/抽屉主路径零回归；**视觉与现状完全一致**（结构先稳定再动样式）。

**Phase 2 — 视觉升级 + 信息结构**
- 气泡/空状态（EmptyState+建议卡）/输入区容器化 / 类型化状态徽章 / 移动端抽屉玻璃态。
- RunPanel 改折叠：拆 `run-events.tsx`，agents/events 折叠（**条件渲染**），`!detail` 时 `return null`；budget 用 Tooltip。
- 会话列表增强：相对时间 `date-fns`（`updated_at`）；**不做运行徽章**（无数据）。
- 时区统一函数 `parseDate()` 先行落地。
- 验收：新建会话可用；空态无 newRunHint 叠加；审计子树默认不 mount；i18n key 双向一致。
- **Phase 2 之后 context 重新聚焦（可视情况）**。

**Phase 3 — 动效 / 移动端 / 打磨**
- `animate-fade-in`/typing 三点/`page-enter`；移动端工具栏抽屉化 + 键盘 `100dvh` 适配；滚动锚定。
- 验收：`prefers-reduced-motion` 尊重；360px 无横向溢出；`.dark` 对比度；轮询期无每 2s 全列表卡顿。

**Phase 4（后续独立里程碑，不在本次批准范围）— markdown 富文本 / 三层结构化卡片 / 会话预览 & 运行徽章（均待后端字段）**

### 2.3 风险矩阵与保护
| 风险 | 破坏点 | 等级 | 保护策略 |
|------|--------|------|----------|
| 轮询状态流 | `useRunDetail` 依赖 runId + onUpdate | 高 | `use-run-detail.ts`/`run-state.ts` **零改动**，page 为唯一状态持有者 |
| localStorage 恢复 | 初始化 effect 时序 | 高 | 保持 `run-state.ts` 与初始化 effect 不动；`use-chat-session` **本轮不拆**，随机抽取风险一并规避 |
| 移动端抽屉 | historyOpen 状态 | 中 | 状态留 page，SessionList 纯展示复用 |
| Enter/IME | 中文输入法提交 | 中 | 判断迁入 InputComposer，优先单测覆盖 |
| 幂等键 | requestId 去重 | 中 | submit/handleSend 逻辑全部留 page |
| 事件累加 | mergeEvents 去重 | 中 | run-events 只读 events，不参与累加 |

### 2.4 Phase 2.5 — 收口（V3 新增，替代原 P2 残余 + P3）

> 原 P2/P3 编号已与实际执行错位（P1/P2 部分内容已完成，P2 核心与 P3 几乎未动）。为避免继续失真，**新增单一收口阶段 P2.5**，不再沿用原 P2/P3 拆分。

**必做（阻断交付）**
1. **M-6 markdown 范围决策记录**：把 §1.8 从「本次不做」改为「已实现 + 边界清单」，明确无 `rehype-raw`/`remark-gfm` 为安全底线。✅ 已落地（§1.8 已改写）
2. **M-7 markdown table 补样式** — ⛔ **前提不成立，取消**（V4 实测）：`react-markdown@10.1.0` **默认不解析表格**（表格是 GFM 扩展，需 `remark-gfm`，而 §1.8 明令不得引入）。实测渲染结果为纯文本管道符，`th`/`td` 映射永不被调用。因此「补 `th`/`td`」是死代码，已回退；真正的前置条件是「引入 `remark-gfm`」，属新增依赖，需单独决策（同 §1.8 范围边界），**不列入 P2.5**。代码块 `overflow-x-auto` 仍已落地（`globals.css` `.prose-chat pre`）。
3. **M-9 `.serena/` 加入 `.gitignore`** ✅ 已落地（`.gitignore:23`）。另：其他会话遗留的 `.planning/2026-09-18-auto-adopted-notification-fix/` **不在本会话范围**，不代删。

**应该做（本次价值主体）**
4. **M-1 RunPanel 改「交易者视图」**：拆 `run-events.tsx`，agents/events **默认折叠**（条件渲染、非 CSS hidden），budget 改 Tooltip。**这是 findings.md 第 2、8 条差距的实质解决点**——不做则本次重设计停留在换配色。
5. **M-2 空状态升级**：`EmptyState`（icon+heading+desc）+ 建议提问卡 `grid sm:grid-cols-3` + preset 引导；`ConversationThread` 空态从一行文字换掉。
6. **M-3 `!detail` 时 RunPanel `return null`**：依赖第 5 项先落地（空态已给引导，`newRunHint` 与其叠加矛盾）。
7. **M-5 移动端 `100dvh`**：`page.tsx:214` `h-[calc(100vh-4rem)]` → `100dvh`（iOS 软键盘遮挡输入区）。

**可以做**
8. **M-4 `prefers-reduced-motion`**：`globals.css` 加媒体查询禁用 `animate-fade-in`/`pulse`/`scale-in`（无障碍合规，改动小）。
9. **m-1 轮询重渲染**：`MessageBubble` 加 `React.memo`，或 `useRunDetail` 无变化时不 `setState`——P3 验收项「轮询期无每 2s 全列表卡顿」目前无保障。

**不做（保持原结论）**
- 三层结构化卡片、会话预览/运行徽章（均依赖后端字段，P4 单独立项）。
- `use-chat-session` hook 抽取（原 §2.1 已定「本轮不拆」）。

**P2.5 验收**
- `npx tsc --noEmit` + `npm run build` 通过
- 审计子树（agents/events）默认不 mount
- 360px 无横向溢出；`.dark` 对比度达标
- `prefers-reduced-motion` 下无动画
- AI 报告含表格时不破图
- 后端 `pytest` 496 个零影响

---

## 3. i18n 与测试

- **i18n**：沿用 `agentChat` 命名空间，新增 key 同步 `messages/zh/agentChat.json` 与 `messages/en/agentChat.json`（现有 94 个 key 不动）。预计新增（命名统一版，采纳 planner 前缀风格）：
  - `emptyHeading`/`emptyDesc`/`suggestTitle`（建议卡标题 "试试这样问"）/`suggestItems`（建议数组）
  - `expandTrace`/`collapseTrace`（事件时间线）/`expandAgents`/`collapseAgents`（agent 卡）
  - `sessionUpdatedAgo`（"{time}前"）/`timeAgo`（相对时间格式化）/`cancelRun`/`sendHint`（"⏎ 发送"）
  - 事件时间线 "执行轨迹 (N 步) · Xs" 的计数/时长格式化 key

> **V4 实测落地（与上述「预计」不同，以此为准）**：最终 zh/en 各 **101 个 key**，双向差集为空。
> - 已落地：`emptyHeading`/`emptyDesc`/`suggestTitle`/`suggestItems`/`expandTrace`/`collapseTrace`/`expandAgents`/`collapseAgents`/`sendHint` + budget 六项（`budgetHeavyTool`/`budgetMaxRetries`/`secondsUnit`/`turnsUnit`/`timesUnit`）
> - **取消 `sessionUpdatedAgo`**：改用 `Intl.RelativeTimeFormat` 后不再需要 "{time}前" 模板（已删除，否则是死 key）；`timeAgo` 从未实现
> - **未落地**：事件时间线的计数/时长格式化 key（改为直接插值 `t("executionTrace")} ({n})`，无独立 key）
> - 删除 `emptyHint`（空态改用 `emptyHeading`+`emptyDesc`，原一行文字提示作废）
> - 修正 en 文件 13 个 key 误填中文的问题（原「不动」的假设不成立，见文件头 P2.5 记录）
- **测试**：
  - 门禁（每阶段必做）：`npx tsc --noEmit` + `npm run lint` + `npm run build`；后端回归 `pytest tests/ -v --no-cov`（496 个）零影响。
  - **vitest（决策点 D1）**：若引入（P1 起），覆盖对象应聚焦**本次实际改动**的高风险点（`input-composer` 的 Enter/IME）与统一时间函数 `parseDate()`；**不测**`run-state.ts` 纯函数（本轮零改动、已稳定，测之边际价值低）。critic 建议可暂不引入，先靠 tsc/build + 手测 IME 兜底。
- **校验**：
  - P0：清 localStorage 点「新会话」成功不 422。
  - i18n key 双向一致；360px 无横滚；`.dark` 对比度；时间戳无 7 小时偏差（`parseDate` 单测）；轮询期 DevTools 无每 2s 全列表重渲染卡顿。

---

## 4. 开放决策点（需用户批准确认）

> **已定案（团队自决，不再推给用户）**：
> - **D3 CRITICAL 修复**：已拍板方案 A（前端改传 `"free"`）+ 补 create_session 验证测试。第二轮评审确认方案 A 为明确最优（最小改动、零测试破坏、不影响 V1），方案 B 会把两套枚举永久混入同字段。
> - **D4 拆分**：相对时间**做**（`updated_at` 有数据）；运行徽章**不做**（`list_sessions` 无 active run 数据，是画饼，与"会话预览"同列 P4）。

| # | 决策 | 选项 | 我的建议 |
|---|------|------|---------|
| D1 | **是否引入 vitest + testing-library**（测 InputComposer 的 Enter/IME + parseDate） | A. 引入 · B. 不引入靠 tsc/build + 手测 IME 兜底 | **B**（第二轮评审下调：本轮零改动的 run-state 纯函数测之边际价值低，唯一改动点 InputComposer 可手测） |
| D2 | **markdown 富文本**是否纳入本次范围 | A. 本次不做（后续里程碑）· B. 本次一并实现 | A（critic 克制建议；三层结构化卡片随 markdown 一并推迟，本阶段明示取舍） |

> 若用户同意按此综合方案 V2 推进，则：
> 1. 本计划文档转为实施计划（更新 status）
> 2. 依次执行 Phase 0（CRITICAL 独立）→ 1（组件搬运）→ 2（视觉+信息结构）→ 3（动效/移动端），每阶段验收通过再推进
> 3. Phase 4（markdown / 三层卡片 / 会话预览 / 运行徽章，均待后端字段或独立立项）单独申请批准
> 4. 全程后端零改动（除 P0 补 1 条 create_session 测试）；前端零新增运行时依赖

---

## 附：探讨文档索引
- 视觉方案：`.omc/research/chat-redesign/designer-proposal.md`
- 第一轮批评评审：`.omc/research/chat-redesign/critic-review.md`
- 实施结构：`.omc/research/chat-redesign/planner-proposal.md`
- 第二轮批评评审：`.omc/research/chat-redesign/plan-critic-round2.md`
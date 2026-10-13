# Task Plan: 风控拦截消息全通道中文化

## Goal
将所有后端风控拦截消息（reason/error）从英文转为中文，覆盖手动、AI/MCP、引擎三通道的拦截链路：discipline_gate → order_preflight → guardrails → manual_order_gate → position_close。

## Next Step
已全部完成。待用户验收 + 前端确认渲染。

## Current Phase
Phase 5 (Delivery)

## Phases

### Phase 1: Requirements & Discovery
- [x] 盘点 4 个风控文件的英文拦截消息来源
- [x] 确认前端 ReviewResultCard.tsx 直接渲染 `{review.reason}`（无翻译层，需后端改中文）
- [x] 用户通过 AskUserQuestion 选择「全通道后端消息中文化（推荐）」
- **Status:** complete

### Phase 2: Planning & Structure
- [x] 确定范围：discipline_gate.py、order_preflight.py、guardrails.py、manual_order_gate.py
- [x] 补充发现 position_close.py 也是用户可见平仓闸门，纳入范围
- **Status:** complete

### Phase 3: Implementation
- [x] discipline_gate.py：全部 reason 中文化（熔断、冷却、次数、方向、休息日）+ Impulse cooldown 审计事件中文化
- [x] order_preflight.py：解析/闸门/手数/熔断消息中文化
- [x] guardrails.py：`_validate_order_core` 全部拦截 + rollout 模式 + `validate_agent_call` + ValueError 中文化
- [x] manual_order_gate.py：情绪规则、switching、AI 审查、rollout、撤单/改 SLTP 全部中文化
- [x] position_close.py：switching / 持仓归属 / rollout / 平仓失败消息中文化
- **Status:** complete

### Phase 4: Testing & Verification
- [x] 更新 test_guardrails.py / test_phase_f.py / test_position_close.py / test_manual_order_gate.py 断言
- [x] 修复 `b"当日亏损"` 非 ASCII bytes 字面量语法错误
- [x] ruff --fix 清理 import 排序 + F841（snapshot 未使用）
- [x] 修复 ruff 误删 `discipline_month_key` import（测试 monkeypatch 依赖）
- [x] 修复 test_mcp_broker_guard.py 断言（"below broker minimum" → "低于券商最小"）
- [x] 相关模块 169 passed；ruff all passed；全量 1302 passed / 10 failed（仅剩既有环境性失败）
- **Status:** complete

### Phase 5: Delivery
- [x] 向用户提交中文化完成说明
- **Status:** complete

### Phase 6: 代码审查（/code-review）+ 修复
- [x] 双轴审查（Standards + Spec 并行子代理）
- [x] 修复 Spec 轴阻断发现：`_discipline_local_weekday()` 局部 import 被 ruff 误删 → monkeypatch 回归（恢复局部 import）
- [x] 修复 Spec 轴遗漏：order_preflight.py spike_chase 前缀英文 → 「追涨杀跌拦截（spike_chase）」
- [x] 修复 Standards 轴风险：`_parse_flip` count 段（parts[2]）无 try/except → 脏数据致 fail-closed 误拒
- [x] 修复 Standards 轴重复：提取 `_flip_key(account, symbol, channel=None)` helper（消除 3 处手写 flip key）
- [x] 验证：ruff all passed；相关模块 184 passed；复现验证 monkeypatch 修复生效
- **Status:** complete

### Phase 7: 前端字体编译失败修复
- [x] 根因：`next/font/google` 构建期从 fonts.gstatic.com 下载 Noto Sans SC/Thai → 国内网络连不上 → 104 个红色报错堆栈 + 字体静默缺失
- [x] 用户选择「改用系统字体栈」方案
- [x] layout.tsx 移除 `next/font/google` 导入与字体常量
- [x] globals.css `--font-sans` 改纯系统字体栈（苹方/微软雅黑/系统 CJK）
- [x] 顺手修正 `--font-heading` 自引用循环 bug（git 历史原为 var(--font-sans)）
- [x] 验证：tsc exit 0；build 成功；字体错误 104 → 0；Vercel 生产不再依赖字体下载
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 全通道后端消息中文化 | 前端无翻译层，ReviewResultCard 直接渲染后端 reason |
| 保留英文专有名词（SL/TP/BUY/SELL/MICRO 等） | 交易领域术语，中英混排更清晰 |
| position_close.py 纳入范围 | 手动平仓闸门同样有用户可见英文消息 |
| `_flip_key` 提取 helper（审查修复） | 消除 3 处手写 flip key 重复（Duplicated Code） |
| `_parse_flip` count 段加防护（审查修复） | 脏数据致 ValueError → fail-closed 误拒，比放行更危险 |
| 前端改用系统字体栈（Phase 7） | next/font/google 依赖 Google Fonts 下载，国内网络永久连不上 → 构建报错 + 字体缺失 |
| `--font-heading` 修正为 var(--font-sans) | 自引用循环（var 引用自身）是无效声明，font-heading 类一直拿不到字体 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| Python `b"当日亏损"` 语法错误 | 改为 `"当日亏损" in e.decode()` |
| ruff 误删 `discipline_month_key` import | 加回 import + `# noqa: F401`（测试 monkeypatch 目标） |
| ruff F841 snapshot 未使用 | 删除赋值保留调用（`_build_snapshot` 有副作用） |
| ruff 误删 `_discipline_local_weekday` 局部 import（审查发现） | 恢复局部 import（monkeypatch 源模块语义） |
| spike_chase 前缀残留英文（审查发现） | 改为「追涨杀跌拦截/警告（spike_chase）」 |

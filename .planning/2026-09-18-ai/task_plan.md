# Task Plan: AI 分析超时问题最终解决方案

## Goal
彻底解决「AI 分析仍会失败（超时）」问题。已修改数次（996d10c 预算配置化、a65007b chat V2 修复、6cd9597 解析 bug），但用户复报仍超时。本次给出**最终方案**，并**新增可配置的指数退避重试机制**（间隔按次数递增）。计划经用户同意后执行。

## 新增需求（用户 2026-09-18）
> 现在的项目中，有没有重试机制？能否加上，中间间隔时间可以按次数增加。

**结论**：有重试机制但不满足需求——两个路径都只重试「连接失败」不重试「慢请求超时」，退避间隔被 cap（V2 ≤2s、SDK 内部不可控）。需要新增**可配置的指数退避重试**（间隔按次数递增），且重试要纳入总预算约束。

## Next Step
向用户提交根因分析与优化计划，等待批准后执行。

## Current Phase
Phase 3 实施完成 + 代码审查修复完成，Phase 4 验证中

## 根因（一句话）
慢 LLM（deepseek-v4-flash @ ARK，单请求 120–300s）超过了各级预算的**单请求 clamp 值**：
1. **旧路径 openai_loop.py:210** `timeout=min(settings.llm_timeout, timeout)` → specialist 传 180s → 单请求被 clamp 到 180s，模型响应 >180s 即 APITimeoutError。
2. **V2 专家模式串行 + 全局 deadline**：reflector 分 90s，但 request_timeout_s=180 > 90 → 一个 LLM 请求就超；且串行编排让前一专家拖久挤压后续所有专家。
3. **reserve 机制**（15% → 90s）提前进入 summary，分析不完整。
4. `.env` 未设置任何 `CHAT_*`，走默认值（request=180 与 LLM_TIMEOUT=300 不匹配）。

## Phases

### Phase 1: 根因分析 — ✅ complete
- [x] 确认 AGENT_MODE=multi → 自动交易走旧 run_multi_agent
- [x] 定位 openai_loop.py:210 单请求 clamp 矛盾（min(LLM_TIMEOUT, 子预算)）
- [x] 定位 V2 chat_workflow 串行专家 + 全局 deadline 挤压
- [x] 定位 chat_runtime reserve 机制过早 summary
- [x] 确认 .env 未设 CHAT_*，走默认值
- [x] 排查既往修改（996d10c/a65007b/6cd9597）为何未解决
- [x] **盘点现有重试机制**：两条路径都不重试「慢请求超时」，退避 cap 过小/不可控

### Phase 2: 方案设计 — ✅ complete（待批准）
见下方「候选方案」与「推荐组合」。

### Phase 3: 实施 — ✅ complete

**A. 慢模型适配（核心）**
- [x] `openai_loop.py`：单请求超时改用全局 `LLM_TIMEOUT`（`timeout=settings.llm_timeout or timeout`），去掉对子预算的 clamp
- [x] V2 预算合理化（chat_workflow.py + chat_runtime.py）：
  - 专家编排并行化：reflector 与 tech/fund 并行（原先串行，reflector 耗时挤压后续）
  - allocation 总和 >100% 刻意设计：每 agent 拿理想时间片，全局 deadline 兜底
  - reserve 改为绝对兜底 30s（`reserve_s` 优先于 `reserve_fraction`）

**B. 指数退避重试机制（用户新增需求）**
- [x] 新增共享 helper `backend/app/ai/llm_retry.py`：
  - `compute_retry_delay(attempt, is_rate_limit=False)`：`base * 2^attempt` 封顶 `max_s`
  - 间隔放宽：`base=15s` 起步（15s→30s→60s→120s 封顶），429 用封顶间隔
  - `is_retryable_error()`：复用 `llm_errors.classify_llm_error`，timeout/connect/refused/dns/rate_limit 可重试
  - `has_retry_budget()`：重试前检查剩余预算，不足则放弃
- [x] `chat_runtime.py::_openai_request`：改用共享 helper
  - `APITimeoutError` 纳入可重试（不再直接判超时失败）
  - 退避间隔指数递增（替换 cap 2s 的旧逻辑）
  - 重试间隔计入总预算（剩余 < 下次退避 + reserve → 放弃）
  - 429 用更长间隔
- [x] `openai_loop.py`：弃用 SDK 内部 `max_retries`，新增 `_call_with_retry` 自研重试循环
  - 慢请求/连接失败统一指数退避
  - 累计超时判定不变（轮次边界）

**C. 配置与 env**
- [x] config.py 新增：`llm_retry_base_s=15`、`llm_retry_max_s=120`；`llm_max_retries=2` 保留
- [x] `.env` 显式配置：`CHAT_TOTAL_TIMEOUT_S=900`、`CHAT_REQUEST_TIMEOUT_S=300`、`CHAT_HEAVY_TOOL_TIMEOUT_S=600`、`LLM_MAX_RETRIES=2`、`LLM_RETRY_BASE_S=15`、`LLM_RETRY_MAX_S=120`

**D. 回归测试**
- [x] 慢模型 mock：APITimeoutError 首次抛出 → 重试成功，断言退避间隔递增
- [x] 超时重试：mock 请求超时 → 重试 → 成功（chat_runtime + openai_loop 双路径）
- [x] 预算联动：剩余预算不足 → 放弃重试直接 total_timeout
- [x] 重试次数耗尽 → 结构化失败；max_retries=0 → 不重试
- [x] 现有 chat_runtime / chat_workflow / openai_loop / llm_retry 测试全绿

### Phase 4: 验证 — 🔄 in progress
- [x] pytest：新增重试测试（llm_retry 14 + chat_runtime 5 + openai_loop 3）全绿
- [x] 全量 unit 回归：691 passed（排除 test_multi_agent 22 个已知环境失败）
- [x] 配置加载验证：新配置从 .env 正确读取（15/120/2/900/300/600）
- [ ] 手动：chat 页 experts 模式跑 GOLD 报告，5 路 agent 全部完成（需部署后）
- [ ] 观察日志：重试事件（provider_retry）按 15s/30s/60s/… 递增（需部署后）

### Phase 5: 交付 — ✅ complete
- [x] 提交代码（f60c49f）+ 提交规划文档（5f67322）
- [x] .env 配置已更新（本地未跟踪文件，直接生效）
- [x] 代码审查（superpowers:requesting-code-review）+ 修复提交（43fa2dc）
- [ ] 部署后手动验证 chat experts + 自动交易（待用户部署）

### Phase 6: 代码审查修复 — ✅ complete（2026-09-18）
- [x] C1(CRITICAL)：openai_loop 时间基准不一致 → 重试预算闸门恒真。改 start_mono=monotonic
- [x] I1：reserve_s=30 无差别复制进小 allocation 专家 → 按 allocation 缩放
- [x] I2：5xx 两条路径不一致 → 下沉到 llm_retry.is_retryable_error()
- [x] I3：剩余预算 <=0 硬跑 0.001s → 跳过并返回结构化失败
- [x] I4：预算耗尽 reason_code 保留标签 → retry_budget_exhausted:<tag>
- [x] M2/M3/M7：max(0,..) 统一 / reserve_s 显式判断 / 删冗余
- [x] M5：补测试（5xx 可重试、monotonic 契约、openai_loop 预算闸门）
- 结果：697 passed 无回归（commit 43fa2dc）

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 本次只做分析和计划，不改代码 | 用户明确要求：计划经过我同意才能开始执行 |
| 重试机制采用共享 helper（llm_retry.py） | 两条路径统一逻辑，避免再出现「V2 有重试、旧路径没有」的漂移 |
| 重试间隔按次数指数递增（15s→30s→60s→120s 封顶） | 满足用户「中间间隔时间按次数增加」需求；放宽间隔规避平台限流（用户反馈 2s 太短可能被拦截） |
| 平台限流（429）用更长间隔 | 尊重平台节流，避免重试反而加剧 429 |
| APITimeoutError 纳入可重试 | 当前慢模型超时是最常见失败，必须重试而不是直接判失败 |
| 重试纳入总预算约束 | 防止重试把分析无限拉长，预算到了就放弃重试

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 无 | — |

# Findings — AI 分析超时问题专项（2026-09-18 用户复报）

> 用户在 analyze_recent_trades 解析 bug 修复后仍报告「AI 分析失败（超时）」，已修改数次未解决。
> 本文件记录新一轮根因调查的完整证据链。

## 关键结论（一句话）

真正的根因不是解析 bug，而是**慢 LLM（deepseek-v4-flash @ ARK）的单请求时长（120–300s）超过了各级预算的单请求 clamp 值**，叠加 **V2 专家模式串行编排 + 全局 deadline 的预算挤压**，导致分析在中途超时/不完整。

## 已核实的事实

### 环境
| 配置 | 值 | 影响 |
|------|-----|------|
| `LLM_PROVIDER` | `openai_compat` | 两条路径都走 openai 协议循环 |
| `LLM_MODEL` | `deepseek-v4-flash`（ARK） | 慢模型，单请求可达 120–300s |
| `LLM_TIMEOUT` | 300s | 全局单请求超时上限 |
| `AGENT_MODE` | **`multi`** | **自动交易走 run_multi_agent**（旧多智能体） |
| `MULTI_AGENT_SPECIALIST_TIMEOUT_S` | 180s | 分析师总预算（env 已调高） |
| `MULTI_AGENT_ORCHESTRATOR_TIMEOUT_S` | 240s | 编排总预算 |
| `MULTI_AGENT_REFLECTOR_TIMEOUT_S` | 150s | 反射器预算 |
| `CHAT_*` | **未设置** | V2 聊天专家用默认值：total=600 / request=180 / tool=60 / heavy=300 / turns=15 / reserve=15% |

### 路径 1：旧 run_multi_agent（自动交易，AGENT_MODE=multi）
- `openai_loop.py:210`：`timeout=min(settings.llm_timeout or timeout, timeout)`
  - specialist 传 `multi_agent_specialist_timeout_s=180` → **单请求 clamp 到 180s**，即使 LLM_TIMEOUT=300
  - **若模型真实响应 >180s → APITimeoutError → 该路直接失败**
- `openai_loop.py:221-230`：累计超时判定在轮次边界，max_turns=10
- 已加固（996d10c）：失败语义分离 ✓、失败标注 ✓、预算配置化 ✓
- **但没解决慢请求 vs clamp 矛盾**：`LLM_TIMEOUT=300` 形同虚设

### 路径 2：V2 聊天专家模式（chat 页 mode="experts"）
- `chat_workflow.py:99-108` 专家编排（**串行**，除 tech+fund 并行）：
  ```
  reflector 15% → tech+fund 25% 并行 → plan_drafter 15% → risk 20% → synth 25%
  ```
  总额 100%，但全局 deadline=600s，**任一专家拖久后续全被挤压**
- `chat_workflow.py:77`：`local_budget total = min(allocation, deadline-now)`
  - reflector 分 90s，但 `request_timeout_s=180` > 90 → **一个 LLM 请求就可能超 reflector 预算**
- `chat_runtime.py:146` + `_run_openai.py:197`：`remaining() <= reserve` 强制 summary，
  reserve=15% of total（90s）→ 剩余 ≤90s 时模型再调工具会被跳过
- **结果**：deepseek 慢响应（120–300s）在 180s request / 90s reflector 预算下必然超时，
  且 reserve 机制让最后一段强制总结 → 分析不完整或 total_timeout

### 之前修改的覆盖范围（为什么没解决）
| 提交 | 内容 | 未覆盖 |
|------|------|--------|
| 996d10c | B 方案：multi_agent 预算配置化 + 失败语义分离 | 慢请求 clamp 矛盾、V2 预算挤压 |
| a65007b | chat V2 HIGH/MEDIUM 修复（reserve_fraction 配置化等） | 预算本身不合理、串行挤压 |
| 6cd9597 | analyze_recent_trades 解析 bug | 完全无关的超时 |

## 影响范围
- 自动交易（AGENT_MODE=multi）：分析师/编排反复超时 → AI_AGENT_ERROR 事件 / 决策降级
- chat 专家模式：报告不完整 / timed_out / failed（用户直接可见）
- 单 agent 模式（AGENT_MODE=single）：相对好，但仍受 request clamp 影响

## 现有重试机制盘点（用户询问）

### 现状（不满足需求）
| 位置 | 机制 | 问题 |
|------|------|------|
| `openai_loop.py:212`（旧路径） | openai SDK `max_retries=settings.llm_max_retries or 2` | SDK 内部固定退避（≤2s），不可配置、不针对慢模型 |
| `chat_runtime.py:173-184`（V2 路径） | 自研重试：`retryable` 只认 `APIConnectionError` + 429/5xx | ①`APITimeoutError` **不重试**直接失败 ②退避 `min(.25*2^attempt, 2)` cap=2s ③`DEFAULT_BUDGET` 里 `max_retries=0` |
| `llm_errors.py:55-57` | `is_connection_error()` 把 timeout 归为可重试 | 但没有调用方用它做重试判定，只用于熔断 |

### 核心差距
- **慢请求超时（APITimeoutError）目前不触发重试，直接判失败** —— 这正是用户遇到的场景
- **退避间隔不可控/过小**：V2 cap 2s、旧路径 SDK 内部固定，都不满足「间隔按次数增加」
- `LLM_MAX_RETRIES` 未在 .env 设置，走默认 2（但 V2 的 DEFAULT_BUDGET 写死 0，行为不一致）

### 用户新需求 → 设计
> 加上重试机制，中间间隔时间按次数增加。

**指数退避重试设计**：
- 新增共享 helper `app/ai/llm_retry.py`：
  - `compute_retry_delay(attempt, is_rate_limit=False) = base * 2^attempt`，封顶 `max_s`
  - **间隔放宽规避平台限流**（用户反馈：2s 太短可能被 ARK 拦截）：
    `base=15s` 起步 → 15s→30s→60s→120s 封顶；平台限流（429）直接采用更长间隔
  - `is_retryable(e)`：复用 `classify_llm_error`，timeout/connect/refused/dns/rate_limit 全部可重试
  - 重试前检查剩余预算（`remaining_retry_time`），不足则放弃重试
- 两个路径统一接入（V2 的 `_openai_request` + 旧路径 `openai_loop`），避免再次漂移
- 重试间隔计入总预算，重试不放大超时上限
- 新增配置：`llm_retry_base_s=15`、`llm_retry_max_s=120`（config.py）
- **行为示例**：慢请求 180s 超时 → 等 15s 重试 → 又超时 → 等 30s → 成功（3 次尝试）
  若剩余预算 < 60s（下次间隔）→ 放弃重试，返回结构化超时

## 候选方案（需用户批准范围）

### 方案 1：慢模型适配（核心，最小改动）
- **让单请求超时 ≥ 各层总预算**，消除 clamp 矛盾：
  - `openai_loop.py`：`timeout = settings.llm_timeout`（不再用 `min(settings.llm_timeout, timeout)` clamp 到子预算）。
    子预算管的是「累计总时长」，单请求超时应该用全局 LLM_TIMEOUT 兜底。
  - `chat_workflow.py`：`local_budget` 的 `request_timeout_s` 设为 `min(LLM_TIMEOUT, allocation)`，
    而不是继承全局 180s 默认（当前反射器 90s < 180s 的缺陷）。
- 收益：慢模型每请求允许跑满 LLM_TIMEOUT，累计预算成为真正的「总时长上限」。

### 方案 2：V2 专家编排预算合理化（解决串行挤压）
- **取消固定百分比 allocation 作为硬预算**，改为「每 agent 一个最小时间片 + 全局 deadline 兜底」：
  - reflector: min 120s；tech/fund: min 150s；plan: min 90s；risk: min 120s；synth: min 120s
  - 全部加起来 > 600？→ 那就把 `CHAT_TOTAL_TIMEOUT_S` 默认提到 900s（专家模式本身是深度分析，可接受）
- 或者：**把串行改成更激进的并行**（reflector 与 tech/fund 并行，risk 与 plan 部分并行）
- 收益：每个专家真正拿到时间，不再因前一专家拖慢而饿死。

### 方案 3：reserve 机制合理化
- `reserve_fraction` 当前 15% 全局；当 total=600 时 reserve=90s。
- 改为「绝对兜底」（如固定 30s 用于最终总结），避免过早进入 summary 模式。
- 收益：模型有更多时间做真正的工具分析。

### 方案 4：预算默认值与环境显式化
- `.env` 显式设置 `CHAT_*`（当前完全默认）：
  - `CHAT_TOTAL_TIMEOUT_S=900`（专家模式深度分析）
  - `CHAT_REQUEST_TIMEOUT_S=300`（对齐 LLM_TIMEOUT）
  - `CHAT_HEAVY_TOOL_TIMEOUT_S=600`
- 收益：生产行为可预期、可审计。

### 方案 5（新增）：指数退避重试机制
- 见上方「用户新需求 → 设计」。共享 helper + 两条路径接入 + 配置化 + 预算联动。

### 推荐组合
**方案 1（核心）+ 方案 2 + 方案 4 + 方案 5（重试）**。前端/报告层展示已具备，暂不动。
改动集中在 `openai_loop.py` / `chat_workflow.py` / `chat_runtime.py` / `llm_retry.py`（新增）/ config.py / `.env`，
约 6 处小改动，风险可控。

## 验证方法
1. 慢模型 mock：模拟 LLM 请求 250s 完成，断言 specialist 不再因 180s clamp 超时，而是总预算 180s 耗尽才超时
2. 超时重试：mock APITimeoutError 首抛→重试成功，断言退避间隔按 15s→30s→60s 递增
3. 预算联动：mock 剩余预算 < 下次退避间隔 → 放弃重试直接失败
4. V2 专家模式：mock 各专家耗时，断言后续专家不因前一专家拖慢而分配为 0
5. 回归：chat_runtime / chat_workflow / openai_loop 现有测试全绿
6. 手动：chat 页 experts 模式跑 GOLD 报告，观察 5 路 agent 是否都完成 + 日志重试事件递增

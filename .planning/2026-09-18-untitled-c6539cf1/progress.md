# Progress Log

## Session: 2026-09-18

### Current Status
- **Phase:** 1 - Requirements & Discovery
- **Started:** 2026-09-18

### Actions Taken
-

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|

### Errors
| Error | Resolution |
|-------|------------|

## 2026-09-18 计划批准，开始实施

### 已批准计划
- 范围：修复 + 补护栏（comment + 预算 + 系统性护栏失效修复）
- 形态：先 AI 后混合
- 计划文件：`~/.claude/plans/moonlit-marinating-wozniak.md`

### 实施顺序（Phase 1 护栏优先）
- [ ] 1.5 SL/TP 校验（guardrails.py validate_order 扩展签名）
- [ ] 1.1 日亏熔断（engine.py 平仓路径 + broker.py close_position 写 Redis）
- [ ] 1.2 连亏熔断（拆分 record_trade）
- [ ] 1.3 spread 熔断（rolling avg spread）
- [ ] 1.4 rollout 统一 Redis 读取
- [ ] 1.6 护栏下沉 broker 层
- [ ] 2.1 comment 清洗
- [ ] 3.1 预算调高（.env）
- [ ] 4.1 .env 值覆盖统一

### Phase 1 + 2 完成（护栏修复 + comment 清洗）— ✅
- [x] 1.5 SL/TP 校验（guardrails.py validate_order 扩展签名 + 8 项方向/有效性校验）
- [x] 1.1 日亏熔断（engine.py _handle_closed_trades 调 record_trade_result）
- [x] 1.2 连亏熔断（拆分 record_order_opened / record_trade_closed，broker 开仓记频率、平仓记胜负）
- [x] 1.3 spread 熔断（broker.py rolling avg spread via Redis）
- [x] 1.4 rollout 统一 Redis 读取（broker.py place_order/modify_position/close_position 用 get_persisted_rollout_mode）
- [x] 1.6 护栏下沉（broker.py micro/live 需 LLM_ALLOW_LIVE=true，provider 无关）
- [x] 2.1 comment 清洗（MT5 27 字符上限，去非 ASCII，简化前缀为 "AI"）
- [x] 测试更新：test_mcp_broker_guard.py、test_phase_f.py、test_guardrails.py（新增 SL/TP 校验测试）

### 验证结果
- test_mcp_broker_guard.py: 5/5 ✅
- test_phase_f.py: 18/18 ✅
- test_guardrails.py: 26/26 + 新增 8 项 SL/TP ✅
- test_circuit_breaker.py: 16/16 ✅
- test_engine.py: 16/16 ✅
- test_multi_agent.py 7 失败 = 既有环境问题（未改动相关文件，findings 已记录）

### 待做
- [ ] 3.1 预算调高（.env MULTI_AGENT_*）
- [ ] 4.1 .env 值覆盖统一

### Phase 3 + 4 完成 — ✅
- [x] 3.1 预算调高：.env 添加 MULTI_AGENT_SPECIALIST_TIMEOUT_S=180、REFLECTOR=150、ORCHESTRATOR=240（验证 Settings 加载 = 180/150/240）
- [x] 4.1 值覆盖统一：Redis guardrails:rollout_mode 从 live 降级为 micro（安全关键）；.env 已统一 LLM_MAX_ORDERS_PER_LOOP=2
- [x] 2.2 核对 DB broker_alias：GOLD→GOLD_（正确别名，非 bug），仅 GOLD 启用
- [x] engine.py 平仓路径补充连亏记录（record_trade_closed）
- [x] 清理 broker.py 冗余 rollout_check 变量

### 最终测试结果
- 91/91 全部通过（guardrails/phase_f/broker_guard/circuit_breaker/engine）
- test_multi_agent.py 7 失败 = 既有环境问题（未触碰相关文件）

### 关键运行时发现
- Redis: trading_mode=ai_autonomous（AI 自主模式）、guardrails:rollout_mode 原为 live（已降 micro）
- circuit:daily_pnl:GOLD=None（日亏从未写入，证实熔断失效根因）
- 后端进程 PID 65233 仍在运行旧代码 → **需要重启才能生效**

### 已知限制
- SDK 通道（Claude）无 per-loop 订单上限（仅 openai_compat 有）。当前 provider 是 openai_compat，已有此保护；频率限制（每小时 5 笔 + 120s 间隔）+ guardrails 提供兜底。LOW-MEDIUM 风险，记录待后续下沉。

### 重要发现：llm_timeout=120 才是多智能体超时的真正瓶颈
- `config.py:185 llm_timeout=120`（单次 LLM 请求超时），`.env` 未覆盖
- `openai_loop.py:210`: `timeout=min(settings.llm_timeout or timeout, timeout)` → specialist 传 180 被 min 到 120
- deepseek-v4-flash 单次请求（含工具调用）需 2-3 分钟 → 每次 120s 超时 → 多次重试 → 实测 specialist 总耗时 543-575s
- 修复：`.env` 添加 `LLM_TIMEOUT=300`（已验证 Settings 加载=300）
- 此发现修正计划 Phase 3：不仅调 specialist 预算，还必须调 llm_timeout，否则 specialist 预算提高无效

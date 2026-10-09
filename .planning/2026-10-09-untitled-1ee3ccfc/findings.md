# Findings & Decisions

## Requirements
-

## Research Findings
-

## Technical Decisions
| Decision | Rationale |
|----------|-----------|

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
-

## 2026-10-09 真实数据验证发现（历史审查查询）

- 真实 `order_audits.review.systemone.converge` 是**收敛器完整输出 dict**（data_quality/signal_alignment/market_regime/risk_check/execution_quality/verdict/upgrade），不是字符串 —— `_verdict_of` 已兼容 dict（取 converge['verdict']）与字符串两种形状。llm 块始终存在且先命中，真实行无歧义。
- 30 天窗口 174 条 manual 审查：approved 11 / caution 17 / rejected 146。拒绝占绝对多数 —— 多为硬闸门内联拒绝（review 无 llm/systemone 块，verdict 走 status 兜底，provider=None）。这符合防火墙设计：大部分风险单在规则层就被拦下，不烧 LLM。
- 生产后端 8002 已启用鉴权（auth_password_hash 非空）；HTTP 端到端验证需登录凭证，服务层只读冒烟是可行替代。
- 全量 unit 中 test_multi_agent.py 6 failed（此前 7 failed）为既有非回归（asyncio loop 绑定），与本次改动文件零交集。

# Progress — 追涨杀跌风控检查

## Session Log

### 2026-10-12 T1 — 初查 + 实例分析
- 梳理风控三条通道(手动/引擎/AI),确认 spike_chase/loss_chase/情绪规则/冲动冷却现状
- 发现 spike_chase 仅存于 systemone.LocalRuleEngine,引擎与 AI 通道走 preflight(无追涨逻辑)
- **实例**:订单 #989466362(id=300)SELL 追空未拦截,已执行
- 定位根因:`.env` 配置 `MANUAL_REVIEW_PROVIDER=typesafe_jev` → provider 链短路 → local_jev 的 spike_chase 不执行
- 交叉验证:JEV 对 SELL(杀跌)系统性宽容(aligned+favorable),对 BUY(追涨)更严格(conflict 拒),追涨杀跌风控不对称
- 更新 task_plan 为三阶段修复方案

### 2026-10-12 T2 — Phase 1 手动通道修复(完成)
- 抽出 `app/services/deviation_guard.py`(detect_spike_chase 纯函数),systemone._rule_spike_chase 改为复用
- manual_order_gate `_build_snapshot` 前置执行 `_spike_chase_flags`:block 级直接拒(不经 JEV),warn 级进 rule_flags
- 用户确认方案「本地规则前置」:确定性规则前置到硬相位,外部 AI/JEV 只能收紧不能覆盖
- 8 个 deviation_guard 单测 + 3 个 manual_order_gate spike 测试,101 测试全过

### 2026-10-12 T3 — Phase 2 引擎/AI 通道(完成)
- `order_preflight` 增加 spike_chase 规则(仅 `channel != "manual"`,warn 即拒——无人值守)
- config 新增 `preflight_spike_pct_warn=0.90` / `preflight_spike_pct_block=0.99`
- 新增 `_preflight_spike_chase` 辅助:M15 拉取失败/数据不足静默跳过(不误杀)
- 3 个新测试:引擎拒单 / 手动跳过(不重复拉取) / M15 不可用跳过;17 个 preflight 测试全过
- ruff:本次引入文件全过(清理了 test_deviation_guard 的 4 个未使用 import)

### 2026-10-13 T4 — Phase 3 验证与收尾(完成)
- ruff:本次引入 4 个文件全过;manual_order_gate 及测试既有 4+7 错误非本次引入
- CLAUDE.md:order_preflight 描述加 spike_chase 自动通道说明,新增 deviation_guard 条目
- 完整测试套件重跑(带 -rf):10 failed / 1297 passed —— 10 个失败全为既有:
  - feishu×2(test_save_feishu_reloads_notifier / test_status_not_configured_when_env_unset)
  - ml_barrier×1(test_trainer_uses_shared_band)
  - multi_agent×7(mcp_server 模型解析/sdk 处理)
  - 均不在本次 diff(deviation_guard/order_preflight/config/systemone/manual_order_gate),确认无回归

### 待办
- [x] 与用户确认修复方案选项(已选「本地规则前置」)
- [x] Phase 3:全量测试 + CLAUDE.md 文档(完成)
- [ ] 用户 review 后提交(commit + PR)
# Task Plan: 增加发送品种行情图片的飞书开关（默认关闭）

## Goal
为 KlineSender（每15分钟发 M15+H1 K线图到飞书）增加一个独立开关 `feishu_send_market_image`，**默认关闭**；关闭时 KlineSender 不发送任何行情图片，开启后恢复正常发送。开关可在前端 /settings 页面运行时切换（免重启）。

## Next Step
全部 Phase 完成，等待用户验收。

## Current Phase
完成（Phase 2-5 全部 done）

## Phases

### Phase 1: 需求与探索（已完成）
- [x] 理解用户意图：增加"发送品种行情图片"飞书开关，默认关闭
- [x] 定位现有链路：`KlineSender` 已完整实现并接入 scheduler
- [x] 确认现状：无独立开关——只要 webhook+uploader 配置即自动发图
- [x] 确认复用模式：`enable_auto_strategy_switch` 的 Switch + Redis 动态更新先例
- **Status:** complete

### Phase 2: 后端实现
- [x] config.py：Settings 加 `feishu_send_market_image: bool = False`
- [x] kline_sender.py：`__init__` 加 `enabled_provider` 注入（默认读 settings），`send_all()` 开头检查开关，关闭直接 return
- [x] bot.py：`SettingsUpdate` 加字段，`update_settings` 写 Redis + 更新 settings 实例
- [x] manager.py：`get_status()` 返回 `feishu_send_market_image`
- **Status:** complete

### Phase 3: 前端实现
- [x] settings/page.tsx：加 state + Card + Switch（与 autoStrategySwitch 并列），调 `updateSettings`
- [x] lib/api.ts：`updateSettings` 类型加 `feishu_send_market_image?: boolean`
- [x] messages/zh/settings.json + messages/en/settings.json：加 5 个 i18n key
- **Status:** complete

### Phase 4: 测试更新
- [x] test_kline_sender.py：`_build_sender` 加 `enabled_provider` 参数（默认 lambda: True）
- [x] 新增测试：`test_disabled_flag_skips_all`（开关关闭时不发送）
- **Status:** complete

### Phase 5: 验证
- [x] 后端：ruff check（仅 manager.py 既有 I001，非本次引入）；pytest 40 passed
- [x] 前端：npx tsc --noEmit 0 errors
- **Status:** complete

## 关键发现（详见 findings.md）
1. `KlineSender`（backend/app/services/kline_sender.py）已完整实现：夜窗判定 + 遍历引擎 + `build_kline_png` + `upload_png_bytes` + `send_kline_card`，每15分钟由 scheduler `_kline_broadcast_job` 触发
2. 已接入 main.py:543-554，有完整测试（test_kline_sender.py / test_kline_chart.py / test_feishu_image.py）
3. **无独立开关**：`send_all()` 仅检查 `feishu_notifier.enabled`（webhook URL）和 `uploader.enabled`（app_id+secret），配置即发
4. 复用 `enable_auto_strategy_switch` 模式：config.py 字段 + bot.py SettingsUpdate + Redis 持久化 + settings 实例更新 + manager.get_status 暴露 + 前端 Switch
5. 前端 settings 页已有 autoStrategySwitch 完整先例（Switch + updateSettings + useTranslations），i18n 在 messages/zh + messages/en

## 技术要点
- **开关读取**：`KlineSender.__init__` 注入 `enabled_provider`（默认 `lambda: settings.feishu_send_market_image`），`send_all()` 开头 `if not self.enabled_provider(): return`——测试可控，不破坏现有构造签名
- **运行时切换**：`update_settings` 写 Redis `feishu_send_market_image` + 更新 `settings` 实例，KlineSender 下轮调度读到新值，免重启（对齐 enable_auto_strategy_switch）
- **默认关闭语义**：现有 test_kline_sender.py 会因默认 False 而跳过发送 → 必须更新测试（设置开关 True 或注入 provider），这是预期行为变更

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 开关放 /settings 页面，与 autoStrategySwitch 并列 | 复用成熟 Switch + updateSettings 模式，零新组件；不侵入 integration 的 Vault 配置 |
| KlineSender 用 `enabled_provider` 注入而非直接 import settings | 测试可控（注入 lambda: True），保持现有构造签名兼容 |
| 开关默认 `False` | 用户明确要求"默认关闭" |
| 动态更新写 Redis + settings 实例 | 对齐 enable_auto_strategy_switch 既有模式，运行时免重启生效 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 2 个 explore 后台任务失败（unknown provider glm-5.3-flash） | 环境问题，改用 codegraph + 直接 grep/read 完成探索 |

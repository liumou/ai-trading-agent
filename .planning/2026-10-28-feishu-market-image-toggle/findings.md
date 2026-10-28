# Findings & Decisions

## Requirements
- 增加"发送品种行情图片"的飞书开关，**默认关闭**（用户原始需求）
- 关闭时 KlineSender 不发送任何行情图片；开启后恢复正常发送
- 开关需前端可切换（运行时生效，免重启）

## Research Findings
- `KlineSender`（backend/app/services/kline_sender.py）已完整实现：夜窗判定 + 遍历引擎快照 + `build_kline_png`（kline_chart.py:44）+ `upload_png_bytes`（feishu_image.py:158）+ `send_kline_card`（feishu.py:285），每15分钟由 scheduler `_kline_broadcast_job`（scheduler.py:632）触发
- 已接入 main.py:543-554（构建 KlineSender + FeishuImageUploader + set_kline_sender），完整测试：test_kline_sender.py / test_kline_chart.py / test_feishu_image.py
- **现状无独立开关**：`send_all()` 仅检查 `feishu_notifier.enabled`（webhook URL 非空）和 `uploader.enabled`（app_id+secret 非空）——只要飞书配置了就每15分钟自动发图
- `enable_auto_strategy_switch` 是成熟可复用先例：config.py 字段（默认 False）+ bot.py SettingsUpdate + `update_settings` 写 Redis `enable_auto_strategy_switch` + 更新 settings 实例（bot.py:342-351）+ manager.get_status 暴露（manager.py:220）+ 前端 Switch
- 前端 settings 页已有完整 Switch 先例（autoStrategySwitch，page.tsx:185-216），i18n 在 messages/zh/settings.json + messages/en/settings.json
- `settings` 单例在 config.py:166（BaseSettings），可运行时更新实例属性（update_settings 已这么做）

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 开关放 /settings 页面，与 autoStrategySwitch 并列 | 复用成熟 Switch + updateSettings 模式，零新组件；不侵入 integration 的 Vault 配置 |
| KlineSender 用 `enabled_provider` 注入而非直接 import settings | 测试可控（注入 lambda: True），保持现有构造签名兼容 |
| 开关默认 `False` | 用户明确要求"默认关闭" |
| 动态更新写 Redis + settings 实例 | 对齐 enable_auto_strategy_switch 既有模式，运行时免重启生效 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| 2 个 explore 后台任务失败（unknown provider glm-5.3-flash） | 环境问题（模型配置），改用 codegraph + 直接 grep/read 完成探索 |

## Resources
- backend/app/services/kline_sender.py（主实现）
- backend/app/notifications/kline_chart.py（build_kline_png）
- backend/app/notifications/feishu_image.py（FeishuImageUploader）
- backend/app/notifications/feishu.py（send_kline_card / _build_kline_card）
- backend/app/api/routes/bot.py（SettingsUpdate / update_settings 先例）
- backend/app/bot/manager.py（get_status 先例）
- frontend/app/settings/page.tsx（autoStrategySwitch 先例）
- frontend/messages/zh/settings.json + frontend/messages/en/settings.json（i18n）
- backend/tests/unit/test_kline_sender.py（需更新）

# Task Plan: 修复孤立持仓自动认领 Telegram 通知未发送问题

## Goal
分析并解决 `Auto-adopted orphaned positions` 触发时 Telegram 通知未成功发送的问题，优化通知机制、规范 `TelegramNotifier` 调用并增加健壮性日志。

## Phase 1: 需求确认与现状排查
- [x] 分析 `_reconcile_once` 中的通知逻辑及 `TelegramNotifier._send` 实现
- [ ] 检查环境变量配置 (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) 是否在运行环境中缺失
- [ ] 评估 `_send` 私有方法直接调用的不规范性

## Phase 2: 代码重构与优化
- [ ] 在 `TelegramNotifier` 中增加公开的通知方法（如 `send_system_alert(text: str)` 或通用通知方法），替代直接调用 `_send`
- [ ] 优化 `engine.py` 中的通知调用，改为调用新的公开方法
- [ ] 增加详细的日志记录：当通知发送失败或未配置时输出清晰的警告/错误日志

## Phase 3: 测试与验证
- [ ] 编写或更新单元测试验证通知逻辑
- [ ] 运行后端测试集验证无回归

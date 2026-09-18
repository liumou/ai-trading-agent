# Findings & Discoveries

- **Notification Direct Call**: `BotEngine._reconcile_once` directly calls `self.notifier._send(...)` which is a private method.
- **Enabled State Check**: If `telegram_bot_token` or `telegram_chat_id` are not configured in settings, `TelegramNotifier.enabled` is `False`, causing `_send` to silently return without logging.

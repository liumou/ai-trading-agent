"""
飞书群自定义机器人通知 — 发送精美卡片消息（价格阈值提醒等）。

- Webhook 配置优先走 Secrets Vault（`FEISHU_WEBHOOK_URL`，AES 加密），未配置时回退 env。
  集成页可编辑保存；`reload_from()` 让运行期实例立即生效（不需重启）。
- 卡片用 `msg_type="interactive"` 内联格式（custom bot 直接支持，无需预建卡片模板）。
- 发送失败只记录日志，绝不抛出异常影响调用方（对齐 TelegramNotifier._send 模式）。
"""

import httpx
from loguru import logger

from app.config import SYMBOL_PROFILES, settings

# 方向 → 卡片头部主色。above=红色系（价格冲高），below=绿色系（价格回落）。
_TEMPLATE_BY_CONDITION = {"above": "red", "below": "green"}

# 方向的中文标签（卡片正文展示用）。
_CONDITION_LABEL = {"above": "高于", "below": "低于"}


def _symbol_display_name(symbol: str) -> str:
    """返回品种的展示名（优先 SYMBOL_PROFILES.display_name，回退原始名）。"""
    profile = SYMBOL_PROFILES.get(symbol) or {}
    return profile.get("display_name") or symbol


def _price_decimals(symbol: str) -> int:
    """返回品种价格小数位数（GOLD=2, USDJPY=3 等），用于卡片价格格式化。"""
    profile = SYMBOL_PROFILES.get(symbol) or {}
    return int(profile.get("price_decimals", 2))


class FeishuNotifier:
    """飞书群自定义机器人通知器。"""

    def __init__(self, webhook_url: str | None = None):
        """初始化通知器。

        ``webhook_url`` 传入时优先使用（集成页 Vault 配置），否则回退 env
        ``FEISHU_WEBHOOK_URL``。运行期可调 ``reload_from`` 让新配置即时生效。
        """
        self.webhook_url = (webhook_url or settings.feishu_webhook_url or "").strip()
        self.enabled = bool(self.webhook_url)

    def reload_from(self, webhook_url: str | None) -> None:
        """运行时用新 webhook 刷新实例状态（保存配置后调用，免重启）。

        调用方负责读取 Vault 中的最新值；此处只同步状态。空值视为清空禁用。
        """
        new_url = (webhook_url or "").strip()
        if new_url != self.webhook_url or bool(new_url) != self.enabled:
            self.webhook_url = new_url
            self.enabled = bool(new_url)
            logger.info(f"Feishu notifier reloaded (enabled={self.enabled})")

    async def _post(self, payload: dict) -> bool:
        """POST 到飞书 webhook。成功返回 True，失败记录日志返回 False（绝不抛出）。"""
        if not self.enabled:
            logger.debug("Feishu notifier disabled (no webhook url), skip send")
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.webhook_url, json=payload)
                body = resp.json()
                if resp.status_code == 200 and body.get("code") == 0:
                    return True
                logger.error(f"Feishu send failed: {resp.status_code} {body.get('msg')}")
                return False
        except Exception as e:
            logger.error(f"Feishu send exception: {e}")
            return False

    def _build_price_alert_card(self, alert, current_price: float) -> dict:
        """构造行情提醒卡片（精美版：头部图标+主色、大号当前价、条件明细、时间注脚）。

        参数 alert 为 PriceAlert ORM 对象（仅读取字段，不依赖 ORM 能力）。
        """
        symbol = alert.symbol
        display = _symbol_display_name(symbol)
        condition = alert.condition
        direction_label = _CONDITION_LABEL.get(condition, "处于")
        template = _TEMPLATE_BY_CONDITION.get(condition, "blue")
        decimals = _price_decimals(symbol)

        icon = "📈" if condition == "above" else "📉"
        price_str = f"{current_price:.{decimals}f}"
        trigger_str = f"{alert.trigger_price:.{decimals}f}"

        # 已发送次数（含本次）与剩余
        sent = alert.sent_count + 1
        max_send = alert.max_notifications

        header_content = f"{icon} {display} 行情提醒 —— {direction_label} {trigger_str}"
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**当前价：<font color='red'>{price_str}</font>**",
                },
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"🎯 **触发条件**\n"
                        f"- 条件：价格 {direction_label} {trigger_str}\n"
                        f"- 持续：已超过 {alert.duration_seconds} 秒\n"
                        f"- 已发送：{sent} / {max_send} 次"
                    ),
                },
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"品种 {symbol}（{display}）· 按当前买入价 bid 判定 · {_now_str()}",
                    }
                ],
            },
        ]

        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": header_content}, "template": template},
                "elements": elements,
            },
        }

    async def send_price_alert_card(self, alert, current_price: float) -> bool:
        """发送行情提醒卡片。发送成功返回 True，失败返回 False（不抛出）。"""
        payload = self._build_price_alert_card(alert, current_price)
        ok = await self._post(payload)
        return ok

    async def send_test_card(self, symbol: str = "GOLD", current_price: float = 0.0) -> bool:
        """发送测试卡片（验证 webhook 配置）。供 /api/price-alerts/test 端点调用。"""
        from datetime import datetime, timezone

        display = _symbol_display_name(symbol)
        template = "blue"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": "✅ 行情提醒 · 连通性测试"}, "template": template},
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**飞书通知已就绪**\n\n- 品种：{display}（{symbol}）\n- 当前价：{current_price if current_price else '—'}",
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [{"tag": "plain_text", "content": f"测试时间：{now} (UTC)"}],
                    },
                ],
            },
        }
        return await self._post(payload)


def _now_str() -> str:
    """当前 UTC 时间的可读字符串，用于卡片注脚。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S (UTC)")
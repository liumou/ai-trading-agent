"""
飞书群自定义机器人通知 — 发送精美卡片消息（价格阈值提醒等）。

- Webhook 配置优先走 Secrets Vault（`FEISHU_WEBHOOK_URL`，AES 加密），未配置时回退 env。
  集成页可编辑保存；`reload_from()` 让运行期实例立即生效（不需重启）。
- **格式兜底**：Vault/env 读到的值若不符合飞书 webhook 标准路径格式，回退 env 有效值
  （仍非法则禁用）。防止集成页误存坏值（如只存了 `xx/yyy` 的占位）后永久盖住 `.env`
  里的有效值 —— 那是曾导致「飞书持续 404 page not found」的根因（2026-10-13）。
- 卡片用 `msg_type="interactive"` 内联格式（custom bot 直接支持，无需预建卡片模板）。
- 发送失败只记录日志，绝不抛出异常影响调用方（对齐 TelegramNotifier._send 模式）。
"""

from datetime import UTC

import httpx
from loguru import logger

from app.config import SYMBOL_PROFILES, settings

# 方向 → 卡片头部主色。above=红色系（价格冲高），below=绿色系（价格回落）。
_TEMPLATE_BY_CONDITION = {"above": "red", "below": "green"}

# 方向的中文标签（卡片正文展示用）。
_CONDITION_LABEL = {"above": "高于", "below": "低于"}

# 飞书 webhook 请求 UA。实测本机 Clash 类代理对 python-httpx 默认 UA 返回
# 502 HTML，带 curl 风格 UA 则放行（2026-10-12 排障确认）。生产无代理不
# 受影响；统一用该 UA 兼作"非爬虫"信号，规避分流误判。
_WEBHOOK_USER_AGENT = "curl/8.0"


def _symbol_display_name(symbol: str) -> str:
    """返回品种的展示名（优先 SYMBOL_PROFILES.display_name，回退原始名）。"""
    profile = SYMBOL_PROFILES.get(symbol) or {}
    return profile.get("display_name") or symbol


def _mask_host(url: str) -> str:
    """脱敏 webhook URL：仅保留 host，丢掉路径（含 hook token，属机密）。

    日志里禁止出现完整 webhook URL —— token 泄露即可冒充机器人发消息。
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url.strip())
        return parsed.hostname or "<invalid url>"
    except Exception:
        return "<invalid url>"


def _price_decimals(symbol: str) -> int:
    """返回品种价格小数位数（GOLD=2, USDJPY=3 等），用于卡片价格格式化。"""
    profile = SYMBOL_PROFILES.get(symbol) or {}
    return int(profile.get("price_decimals", 2))


# 飞书自定义机器人 webhook 标准路径前缀（含标准端口）。
# token 段通常为形如 ``xxxx-xxxx-...`` 的 36 字符 UUID。路径必须以该前缀
# 开头且其后跟非空 token，才视为合法 webhook。
_STANDARD_PATH_PREFIX = "/open-apis/bot/v2/hook/"


def _is_valid_webhook_url(url: str) -> bool:
    """判断 webhook URL 是否为标准飞书自定义机器人格式（仅结构，不联网）。

    校验 host 是 open.feishu.cn 且路径以标准前缀开头并带非空 token 段。
    防止 Vault/env 里被误存坏值（如只存了 ``xx/yyy`` 的占位/测试值）——
    坏值会让飞书对不存在的路径返回 ``404 page not found``，而有效值
    会被永久盖住用不上（2026-10-13 根因）。
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    if parsed.scheme not in ("https", "http"):
        return False
    if parsed.hostname != "open.feishu.cn":
        return False
    if not parsed.path.startswith(_STANDARD_PATH_PREFIX):
        return False
    token = parsed.path[len(_STANDARD_PATH_PREFIX) :]
    return bool(token)


def _pick_webhook_url(*candidates: str | None) -> str:
    """从候选 webhook 中选第一个格式合法的；全不合法返回空串。

    ``candidates`` 优先级由调用方决定（通常 Vault → env）。选中的值即生效；
    无任何合法值时返回 ``""``，通知器禁用并打日志，避免带着坏 URL 空转。
    """
    for c in candidates:
        if c and _is_valid_webhook_url(c):
            return c.strip()
    if any(candidates):
        logger.warning(
            "Feishu webhook 配置格式非法（非标准飞书路径），已禁用通知。"
            "请到集成页重新保存正确的 webhook 或在 env 配置 FEISHU_WEBHOOK_URL。"
        )
    return ""


class FeishuNotifier:
    """飞书群自定义机器人通知器。"""

    def __init__(self, webhook_url: str | None = None):
        """初始化通知器。

        ``webhook_url`` 传入时优先使用（集成页 Vault 配置），否则回退 env
        ``FEISHU_WEBHOOK_URL``。**两处值都会做格式校验**：传入值非法时回退
        env 有效值（仍非法则禁用并告警）。运行期可调 ``reload_from`` 让新配置即时生效。
        """
        self.webhook_url = _pick_webhook_url(
            webhook_url, settings.feishu_webhook_url
        )
        self.enabled = bool(self.webhook_url)

    def reload_from(self, webhook_url: str | None) -> None:
        """运行时用新 webhook 刷新实例状态（保存配置后调用，免重启）。

        语义：
        - 传入空值 = 显式清空禁用（不回退旧值）。
        - 传入非空但格式非法的值 = 回退当前有效值（无有效旧值则禁用），
          避免用户在集成页误存坏值后把运行中可用的 webhook 也一起打断。
        """
        new_url = (webhook_url or "").strip()
        # 只有非空的新值才有资格回退到旧值；空值必须清除（否则没法禁用）
        candidates = [new_url] + ([self.webhook_url] if new_url else [])
        final_url = _pick_webhook_url(*candidates)
        if final_url != self.webhook_url or bool(final_url) != self.enabled:
            self.webhook_url = final_url
            self.enabled = bool(final_url)
            logger.info(f"Feishu notifier reloaded (enabled={self.enabled})")

    async def _post(self, payload: dict) -> bool:
        """POST 到飞书 webhook。成功返回 True，失败记录日志返回 False（绝不抛出）。

        失败日志必须带诊断上下文（HTTP 状态码 / content-type / 响应体片段），
        否则像 ``Extra data: line 1 column 5 (char 4)`` 这种「webhook 返回了
        非标准 JSON（如健康检查页的 ``true\\n``）」会吞成一行无上下文 error，
        运维无法判断是 URL 配错还是网关拦截。host 脱敏（URL 含 token 属机密）。
        """
        if not self.enabled:
            logger.debug("Feishu notifier disabled (no webhook url), skip send")
            return False
        try:
            async with httpx.AsyncClient(
                timeout=10, headers={"User-Agent": _WEBHOOK_USER_AGENT}
            ) as client:
                resp = await client.post(self.webhook_url, json=payload)
        except Exception as e:
            # 传输层异常（超时 / 连接被拒 / DNS）：记异常类型即可，无响应体可参考
            logger.error(f"Feishu send failed (transport): host={_mask_host(self.webhook_url)} err={type(e).__name__}: {e}")
            return False

        body_text = resp.text
        try:
            body = resp.json()
        except Exception as e:
            # 响应体非标准 JSON（如 ``true\\n`` / 网关错误页）：这是最常见的
            # "Extra data" 来源，必须带响应体片段才能定位
            logger.error(
                f"Feishu send failed (bad json body): status={resp.status_code} "
                f"content-type={resp.headers.get('content-type')} "
                f"body={body_text[:120]!r} err={type(e).__name__}: {e}"
            )
            return False
        if not isinstance(body, dict):
            # 标量/数组体（如 ``true``）解析成功但不能用 .get —— 防御性拒绝
            logger.error(
                f"Feishu send failed (unexpected body type): status={resp.status_code} "
                f"content-type={resp.headers.get('content-type')} "
                f"body={body_text[:120]!r}"
            )
            return False
        if resp.status_code == 200 and body.get("code") == 0:
            return True
        logger.error(f"Feishu send failed: status={resp.status_code} code={body.get('code')} msg={body.get('msg')}")
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

    def _build_kline_card(self, symbol: str, timeframe: str, image_key: str) -> dict:
        """构造 K 线图卡片（内嵌图片）。

        注意：飞书自定义机器人 webhook 的卡片 ``img`` 元素，``alt`` 字段必须是
        对象结构（``{"tag": "plain_text", "content": "..."}``），不是纯字符串。
        实测踩坑：字符串 alt 会返回 200621 "parse card json err"。
        """
        display = _symbol_display_name(symbol)
        header_content = f"📊 {display} · {timeframe} K线图"
        elements = [
            {
                "tag": "img",
                "img_key": image_key,
                "alt": {"tag": "plain_text", "content": f"{display} {timeframe} K线"},
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"品种 {symbol}（{display}）· {timeframe} 周期 · {_now_str()}",
                    }
                ],
            },
        ]
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": header_content}, "template": "blue"},
                "elements": elements,
            },
        }

    async def send_kline_card(self, symbol: str, timeframe: str, image_key: str) -> bool:
        """发送 K 线图卡片（图片已上传，此方法只发卡片）。成功返回 True，失败返回 False。"""
        if not image_key:
            logger.warning(f"Kline card skipped [{symbol} {timeframe}]: empty image_key")
            return False
        payload = self._build_kline_card(symbol, timeframe, image_key)
        ok = await self._post(payload)
        return ok

    async def send_test_card(self, symbol: str = "GOLD", current_price: float = 0.0) -> bool:
        """发送测试卡片（验证 webhook 配置）。供 /api/price-alerts/test 端点调用。"""
        from datetime import datetime

        display = _symbol_display_name(symbol)
        template = "blue"
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
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
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S (UTC)")
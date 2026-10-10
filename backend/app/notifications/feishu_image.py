"""
飞书开放平台图片上传 — 供 K 线图卡片内嵌 image_key 使用。

背景：飞书自定义机器人 webhook 不支持直接发送图片消息，卡片 ``img`` 元素
又必须填 ``image_key``（不支持直接放 URL）。``image_key`` 只能通过开放平台
上传接口 ``POST /open-apis/im/v1/images`` 获取，需要先用应用凭据换取
``tenant_access_token``。因此发送 K 线图卡片需要用户在飞书开放平台创建
一个应用（仅用于上传图片，机器人无需入群）。

配置来源对齐 FeishuNotifier（Vault → env 回落）：
- ``FEISHU_APP_ID``    —— 应用 App ID
- ``FEISHU_APP_SECRET`` —— 应用 App Secret

失败不抛出：token 失效自动重取、上传失败返回 None + 记录日志，绝不抛出
异常影响调用方（对齐 FeishuNotifier._post 模式）。
"""

import asyncio
from datetime import UTC, datetime

import httpx
from loguru import logger

# 开放平台 base URL（与 webhook 无关，是开放 API）
OPEN_BASE_URL = "https://open.feishu.cn"

# tenant_access_token 的有效期约 2 小时，提前 5 分钟刷新
_TOKEN_TTL_SECONDS = 2 * 3600 - 300


def _now_ts() -> int:
    """当前 unix 秒，用于 token 缓存判断。"""
    return int(datetime.now(UTC).timestamp())


class FeishuImageUploader:
    """飞书开放平台图片上传器：凭据 → tenant_access_token → 上传图片 → image_key。"""

    def __init__(self, app_id: str = "", app_secret: str = "", transport=None):
        """初始化上传器。``app_id`` / ``app_secret`` 传入时优先，否则读 env。

        ``transport`` 可选：测试注入 httpx.MockTransport；缺省为真实网络请求。
        """
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.enabled = bool(self.app_id and self.app_secret)
        # token 缓存（内存即可，单进程部署）
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        # 请求 transport（可注入 mock；None = 真实网络）
        self._transport = transport
        # token 刷新锁：KlineSender 并发遍历多品种时，缓存过期只会有一个协程
        # 真正发 token 请求，其余等待后直接复用新缓存（防止并发双发 + 失效复用）
        self._token_lock = asyncio.Lock()

    def reload_from(self, app_id: str | None, app_secret: str | None) -> None:
        """运行时用新凭据刷新实例状态（保存配置后调用，免重启）。

        凭据变化时清空 token 缓存，强制下次重取。
        """
        new_id = (app_id or "").strip()
        new_secret = (app_secret or "").strip()
        if new_id != self.app_id or new_secret != self.app_secret:
            self.app_id = new_id
            self.app_secret = new_secret
            self.enabled = bool(new_id and new_secret)
            self._token = None
            self._token_expires_at = 0.0
            logger.info(f"Feishu image uploader reloaded (enabled={self.enabled})")

    # ─── token 获取（带缓存，失效自动重取）───────────────────────────────────

    async def get_tenant_access_token(self) -> str | None:
        """获取 tenant_access_token（缓存 2h，提前 5 分钟刷新）。

        成功返回 token 字符串；失败返回 None（不抛出）。并发安全：缓存过期时
        仅一个协程发起 token 请求，其余等待后复用新缓存。
        """
        if self._token and _now_ts() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            # 双检查：等待锁期间可能已有协程刷新了缓存
            if self._token and _now_ts() < self._token_expires_at:
                return self._token
            if not self.enabled:
                logger.warning(
                    "Feishu image uploader disabled: FEISHU_APP_ID / FEISHU_APP_SECRET not set"
                )
                return None
            try:
                async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
                    resp = await client.post(
                        f"{OPEN_BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
                        json={"app_id": self.app_id, "app_secret": self.app_secret},
                    )
                    body = resp.json()
                    if resp.status_code == 200 and body.get("code") == 0:
                        token = body.get("tenant_access_token")
                        if not token:
                            logger.error("Feishu token response missing tenant_access_token")
                            return None
                        # 用响应里的 expire（秒），异常时回退保守默认
                        expire = int(body.get("expire", 7200))
                        self._token = token
                        self._token_expires_at = _now_ts() + max(expire - 300, 60)
                        return token
                    logger.error(f"Feishu token failed: {resp.status_code} {body.get('msg')}")
                    return None
            except Exception as e:
                logger.error(f"Feishu token exception: {e}")
                return None

    # ─── 图片上传 ────────────────────────────────────────────────────────────

    async def upload_image(self, image_bytes: bytes) -> str | None:
        """上传图片（multipart/form-data），返回 image_key。

        ``image_bytes`` 为图片二进制（PNG/JPG）。失败返回 None，不抛出。
        """
        if not image_bytes:
            logger.warning("Feishu upload_image: empty image bytes")
            return None
        token = await self.get_tenant_access_token()
        if not token:
            return None
        try:
            headers = {"Authorization": f"Bearer {token}"}
            # image_type=message 表示消息图片（含卡片内嵌 img）
            files = {
                "image_type": (None, "message"),
                "image": ("kline.png", image_bytes, "image/png"),
            }
            async with httpx.AsyncClient(timeout=20, transport=self._transport) as client:
                resp = await client.post(
                    f"{OPEN_BASE_URL}/open-apis/im/v1/images",
                    headers=headers,
                    files=files,
                )
                body = resp.json()
                if resp.status_code == 200 and body.get("code") == 0:
                    image_key = body.get("data", {}).get("image_key")
                    if not image_key:
                        logger.error("Feishu upload response missing image_key")
                        return None
                    return image_key
                # 权限/过期错误时强制刷新 token，下次可重试
                code = body.get("code")
                if code in (99991668, 99991661, 99991662, 10012):  # 常见 token/权限错误码
                    self._token = None
                logger.error(f"Feishu upload failed: {resp.status_code} code={code} {body.get('msg')}")
                return None
        except Exception as e:
            logger.error(f"Feishu upload exception: {e}")
            return None

    # ─── 便捷：一步上传 ──────────────────────────────────────────────────────

    async def upload_png_bytes(self, png_bytes: bytes) -> str | None:
        """上传 PNG 字节流，返回 image_key（等价 upload_image，语义更清晰）。"""
        return await self.upload_image(png_bytes)


# 模块级单例（供不依赖装配的场景直接复用；生产由 main.py lifespan 构建）
_uploader_singleton: FeishuImageUploader | None = None


def get_feishu_uploader() -> FeishuImageUploader:
    """返回全局单例上传器（惰性创建，配置缺失时 disabled）。"""
    global _uploader_singleton
    if _uploader_singleton is None:
        try:
            from app.config import settings

            _uploader_singleton = FeishuImageUploader(
                app_id=getattr(settings, "feishu_app_id", ""),
                app_secret=getattr(settings, "feishu_app_secret", ""),
            )
        except Exception as e:
            logger.warning(f"Feishu uploader singleton init failed: {e}")
            _uploader_singleton = FeishuImageUploader()
    return _uploader_singleton

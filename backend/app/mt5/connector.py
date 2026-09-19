"""
MT5 Bridge Connector — HTTP client that calls the MT5 Bridge on Windows VPS.
"""

import asyncio
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

from app.config import settings
from app.mt5.symbol_resolver import to_broker_alias


def _enc(symbol: str) -> str:
    """URL-encode symbol for path segment (handles `#`, spaces, etc.)."""
    return quote(symbol, safe="")


class MT5BridgeConnector:
    def __init__(self):
        self.base_url = settings.mt5_bridge_url
        self.headers = {"X-Bridge-Key": settings.mt5_bridge_api_key}
        self.timeout = 8.0
        self.max_retries = 2
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=self.timeout,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.close()
            self._client = None

    async def _request(self, method: str, path: str, *, retry_ambiguous: bool = True, **kwargs) -> dict[str, Any]:
        import time

        client = await self._get_client()
        start = time.monotonic()
        for attempt in range(self.max_retries + 1):
            try:
                response = await getattr(client, method)(path, **kwargs)
                response.raise_for_status()
                result = response.json()
                await self._record_timing(path, time.monotonic() - start)
                return result
            except (httpx.TimeoutException, httpx.ConnectError, ValueError) as e:
                # 歧义失败（超时/响应损坏）：请求可能已到达桥并被执行，重试
                # 会造成同一笔订单双开仓。下单类调用传 retry_ambiguous=False，
                # 只有明确未发出的 ConnectError 才允许重试。
                ambiguous = not isinstance(e, httpx.ConnectError)
                if attempt < self.max_retries and (retry_ambiguous or not ambiguous):
                    logger.warning(f"MT5 Bridge {method.upper()} {path} retry {attempt + 1}: {e}")
                    await asyncio.sleep(1)
                else:
                    logger.error(
                        f"MT5 Bridge {method.upper()} {path} failed after {attempt + 1} attempts: {e}"
                    )
                    await self._record_timing(path, time.monotonic() - start, error=True)
                    return {"success": False, "data": None, "error": str(e)}
            except httpx.HTTPStatusError as e:
                logger.error(f"MT5 Bridge {method.upper()} {path} HTTP error: {e.response.status_code}")
                await self._record_timing(path, time.monotonic() - start, error=True)
                return {"success": False, "data": None, "error": str(e)}

    async def _record_timing(self, path: str, duration: float, error: bool = False):
        """Record request timing to metrics (if available)."""
        try:
            from app.metrics import get_metrics

            metrics = get_metrics()
            if metrics:
                name = f"mt5_bridge{path.replace('/', '_')}"
                await metrics.record_timing(name, round(duration * 1000, 1))
                if error:
                    await metrics.increment_counter("mt5_bridge_errors")
        except Exception:
            pass

    async def _request_fast(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Single attempt with short timeout — for ephemeral data like ticks."""
        client = await self._get_client()
        try:
            response = await getattr(client, method)(path, timeout=2.0, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError, ValueError):
            return {"success": False, "data": None, "error": "tick timeout"}

    async def get_health(self) -> dict:
        return await self._request("get", "/health")

    # ─── 品种名 = 最后一英里：所有请求路径/参数在调用桥前统一转券商别名 ─────
    # 别名来源是 DB symbol_configs.broker_alias（如 GOLD → GOLD_）。此前每
    # 个消费方各自转换，MCP 行情工具漏做 → AI 分析拿到 "No OHLCV data" 而 DB
    # 明明有数据。转换在此收敛为单一强制点：to_broker_alias 幂等且对未知
    # 符号原样返回，已转换的调用方不会二次改写。
    async def get_tick(self, symbol: str) -> dict:
        return await self._request_fast("get", f"/tick/{_enc(to_broker_alias(symbol))}")

    async def get_ohlcv(self, symbol: str, timeframe: str = "M15", count: int = 100) -> dict:
        return await self._request(
            "get", f"/ohlcv/{_enc(to_broker_alias(symbol))}", params={"timeframe": timeframe, "count": count}
        )

    async def get_symbol_spec(self, symbol: str) -> dict:
        """Fetch broker-side symbol spec (digits, volume limits, contract size)."""
        return await self._request("get", f"/symbol-spec/{_enc(to_broker_alias(symbol))}")

    async def list_symbols(self) -> dict:
        """Fetch all broker-visible symbols with specs (used by catalog dropdown)."""
        return await self._request("get", "/symbols")

    async def get_account(self) -> dict:
        return await self._request("get", "/account")

    async def switch_account(self, login: int, password: str, server: str | None = None) -> dict:
        """同终端切换 MT5 账号（Phase 3 Bridge /account/switch 端点的后端调用）。

        返回 Bridge 响应（success=True 含新账号快照；False 含 error + current）。
        """
        body: dict = {"login": login, "password": password}
        if server:
            body["server"] = server
        return await self._request("post", "/account/switch", json=body)

    async def get_positions(self) -> dict:
        return await self._request("get", "/positions")

    async def place_order(
        self,
        symbol: str,
        order_type: str,
        lot: float,
        sl: float,
        tp: float,
        comment: str = "",
        magic: int | None = None,
    ) -> dict:
        from app.constants import MT5_MAGIC_NUMBER

        if magic is None:
            magic = MT5_MAGIC_NUMBER
        # retry_ambiguous=False：下单请求超时后订单可能已在桥端成交，重试会
        # 双开仓（手动/引擎两条通道共用此方法，都是真实资金路径）。
        return await self._request(
            "post",
            "/order",
            retry_ambiguous=False,
            json={
                "symbol": symbol,
                "type": order_type,
                "lot": lot,
                "sl": sl,
                "tp": tp,
                "comment": comment,
                "magic": magic,
            },
        )

    async def place_pending_order(
        self,
        symbol: str,
        order_type: str,
        lot: float,
        price: float,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "",
        magic: int | None = None,
        expiration: str | None = None,
    ) -> dict:
        """挂单（BUY_LIMIT/SELL_LIMIT/BUY_STOP/SELL_STOP），Phase 1 Bridge 端点。"""
        from app.constants import MANUAL_MAGIC_NUMBER

        if magic is None:
            magic = MANUAL_MAGIC_NUMBER
        # retry_ambiguous=False：同 place_order —— 挂单超时后可能已在桥端
        # 挂上，重试会重复挂单。
        body: dict = {
            "symbol": to_broker_alias(symbol),
            "type": order_type,
            "lot": lot,
            "price": price,
            "sl": sl,
            "tp": tp,
            "comment": comment,
            "magic": magic,
        }
        if expiration:
            body["expiration"] = expiration
        return await self._request("post", "/order/pending", retry_ambiguous=False, json=body)

    async def get_orders(self) -> dict:
        """当前账号挂单列表（桥端 orders_get 序列化）。"""
        return await self._request("get", "/orders")

    async def modify_order(self, ticket: int, price: float | None = None, sl: float | None = None,
                           tp: float | None = None, expiration: str | None = None) -> dict:
        """改挂单（价/SL/TP）。桥端先查单回填 type_time/expiration 整包回传。

        手动通道语义：任一参数变更都必须先重跑全流水线（审查针对的是
        变更后的订单），本方法只做执行。
        """
        body: dict = {}
        if price is not None:
            body["price"] = price
        if sl is not None:
            body["sl"] = sl
        if tp is not None:
            body["tp"] = tp
        if expiration is not None:
            body["expiration"] = expiration
        return await self._request("put", f"/order/{ticket}", json=body)

    async def cancel_order(self, ticket: int) -> dict:
        """撤单（TRADE_ACTION_REMOVE）。风险只减不增，无需审查。"""
        return await self._request("delete", f"/order/{ticket}", retry_ambiguous=False)

    async def modify_position(self, ticket: int, sl: float | None = None, tp: float | None = None) -> dict:
        body = {}
        if sl is not None:
            body["sl"] = sl
        if tp is not None:
            body["tp"] = tp
        return await self._request("put", f"/position/{ticket}", json=body)

    async def close_position(self, ticket: int) -> dict:
        return await self._request("delete", f"/position/{ticket}")

    async def close_all_positions(self, symbol: str | None = None) -> dict:
        params = {"symbol": symbol} if symbol else {}
        return await self._request("delete", "/positions", params=params)

    async def get_ohlcv_range(self, symbol: str, timeframe: str, from_date: str, to_date: str) -> dict:
        """Fetch historical OHLCV data by date range. Uses longer timeout for large datasets."""
        client = await self._get_client()
        try:
            response = await client.get(
                f"/ohlcv/{_enc(to_broker_alias(symbol))}/history",
                params={"timeframe": timeframe, "from_date": from_date, "to_date": to_date},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.error(f"MT5 Bridge historical OHLCV failed: {e}")
            return {"success": False, "data": None, "error": str(e)}
        except httpx.HTTPStatusError as e:
            logger.error(f"MT5 Bridge historical OHLCV HTTP error: {e.response.status_code}")
            return {"success": False, "data": None, "error": str(e)}
        except ValueError as e:
            logger.error(f"MT5 Bridge historical OHLCV invalid response: {e}")
            return {"success": False, "data": None, "error": str(e)}

    async def get_history(self, days: int = 1, symbol: str | None = None) -> dict:
        # 桥端以 deal.symbol（券商名）严格比对过滤，故规范名必须先转别名，
        # 否则成交历史会被整体过滤为空。
        params: dict = {"days": days}
        if symbol:
            params["symbol"] = to_broker_alias(symbol)
        return await self._request("get", "/history", params=params)

import asyncio
from decimal import Decimal
import os

import aiohttp


class XRocketError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(f"{code} (HTTP {status})" if status else code)


class XRocketClient:
    """Async client for xRocket Pay API (https://pay.ton-rocket.com)."""

    API_URL = "https://pay.ton-rocket.com"

    def __init__(self, token: str):
        if not token:
            raise RuntimeError("XROCKET_API_KEY is not configured")
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={
                    "Rocket-Pay-Key": self._token,
                    "Content-Type": "application/json",
                },
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, payload: dict | None = None):
        session = await self._get_session()
        url = f"{self.API_URL}/{path.lstrip('/')}"
        try:
            if method.upper() == "POST":
                req = session.post(url, json=payload or {})
            else:
                req = session.get(url, params=payload or {})
            async with req as response:
                try:
                    body = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                if response.status >= 400:
                    err_msg = str(body.get("message") or body.get("error") or f"HTTP_{response.status}")
                    raise XRocketError(err_msg, status=response.status)
        except XRocketError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise XRocketError("NETWORK_ERROR") from error

        if not body.get("success", False) and "data" not in body:
            raise XRocketError(str(body.get("message") or "UNKNOWN_API_ERROR"), status=response.status)
        return body.get("data", body)

    async def get_version(self) -> dict:
        return await self._request("GET", "/version")

    async def create_rub_invoice(
        self,
        rub_amount: Decimal,
        token_amount: int,
        payload: str,
        *,
        description: str | None = None,
    ) -> dict:
        amount = Decimal(rub_amount).quantize(Decimal("0.01"))
        desc = description or f"{token_amount:,} токенов Emerald AI".replace(",", " ")
        # xRocket tg-invoices endpoint supports creating multi-currency invoices with fiat equivalent or crypto
        # standard endpoint: /tg-invoices
        body = {
            "amount": float(amount),
            "currency": "RUB",
            "description": desc,
            "hiddenMessage": "Спасибо за оплату Emerald AI!",
            "payload": payload,
            "expiredIn": 3600,
        }
        res = await self._request("POST", "/tg-invoices", body)
        return res

    async def get_invoice(self, invoice_id: str | int) -> dict | None:
        try:
            res = await self._request("GET", f"/tg-invoices/{invoice_id}")
            return res
        except XRocketError as e:
            if e.status == 404:
                return None
            raise

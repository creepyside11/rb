import asyncio
from decimal import Decimal

import aiohttp


class CryptoPayError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(f"{code} (HTTP {status})" if status else code)


class CryptoPayClient:
    """Small async client matching the official Crypto Pay HTTP API."""

    API_URL = "https://pay.crypt.bot/api"

    def __init__(self, token: str):
        if not token:
            raise RuntimeError("CRYPTOBOT_TOKEN is not configured")
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"Crypto-Pay-API-Token": self._token},
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, payload: dict | None = None):
        session = await self._get_session()
        try:
            async with session.post(f"{self.API_URL}/{method}", json=payload or {}) as response:
                try:
                    body = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                error_code = str(body.get("error") or f"HTTP_{response.status}")
                if response.status >= 400:
                    raise CryptoPayError(error_code, status=response.status)
        except CryptoPayError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise CryptoPayError("NETWORK_ERROR") from error
        if not body.get("ok"):
            raise CryptoPayError(str(body.get("error") or "UNKNOWN_API_ERROR"), status=response.status)
        return body.get("result")

    async def get_me(self) -> dict:
        return await self._request("getMe")

    async def create_rub_invoice(self, rub_amount: Decimal, token_amount: int, payload: str) -> dict:
        amount = Decimal(rub_amount).quantize(Decimal("0.01"))
        return await self._request(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": format(amount, "f"),
                "description": f"{token_amount:,} токенов Emerald AI".replace(",", " "),
                "payload": payload,
                "expires_in": 3600,
                "allow_comments": False,
                "allow_anonymous": False,
            },
        )

    @staticmethod
    def _normalize_invoices(result) -> list[dict]:
        """Accept the documented array and the wrapped `items` response used by some API versions."""
        if isinstance(result, list):
            invoices = result
        elif isinstance(result, dict) and isinstance(result.get("items"), list):
            invoices = result["items"]
        elif isinstance(result, dict) and "invoice_id" in result:
            invoices = [result]
        else:
            raise CryptoPayError("INVALID_GET_INVOICES_RESPONSE")
        return [invoice for invoice in invoices if isinstance(invoice, dict)]

    async def get_invoices(self, invoice_ids: list[int]) -> list[dict]:
        if not invoice_ids:
            return []
        result = await self._request(
            "getInvoices",
            {"invoice_ids": ",".join(str(invoice_id) for invoice_id in invoice_ids), "count": len(invoice_ids)},
        )
        return self._normalize_invoices(result)

    async def get_invoice(self, invoice_id: int) -> dict | None:
        invoices = await self.get_invoices([invoice_id])
        return next((item for item in invoices if int(item.get("invoice_id", 0)) == invoice_id), None)


def invoice_payment_url(invoice: dict) -> str | None:
    return (
        invoice.get("bot_invoice_url")
        or invoice.get("mini_app_invoice_url")
        or invoice.get("web_app_invoice_url")
        or invoice.get("pay_url")
    )

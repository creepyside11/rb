import json
from decimal import Decimal
from typing import Any

import aiohttp


class PlategaError(RuntimeError):
    def __init__(self, code: str, message: str = "", status: int | None = None):
        self.code = code
        self.message = message or code
        self.status = status
        super().__init__(self.message)


class PlategaClient:
    """Small async client for the documented Platega payment-link API."""

    def __init__(
        self,
        merchant_id: str,
        api_key: str,
        return_url: str,
        failed_url: str,
        base_url: str = "https://app.platega.io",
    ):
        self.merchant_id = merchant_id.strip()
        self.api_key = api_key.strip()
        self.return_url = return_url.strip()
        self.failed_url = failed_url.strip()
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _amount_value(amount: Decimal) -> int | float:
        normalized = Decimal(amount).quantize(Decimal("0.01"))
        if normalized == normalized.to_integral_value():
            return int(normalized)
        return float(normalized)

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        session = await self._get_session()
        headers = {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.api_key,
            "Content-Type": "application/json",
        }
        try:
            async with session.request(
                method,
                f"{self.base_url}/{path.lstrip('/')}",
                headers=headers,
                **kwargs,
            ) as response:
                raw = await response.text()
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError as error:
                    raise PlategaError("INVALID_RESPONSE", raw[:300], response.status) from error
                if response.status >= 400:
                    code = str(data.get("code") or data.get("error") or f"HTTP_{response.status}")
                    message = str(data.get("message") or data.get("detail") or code)
                    raise PlategaError(code, message, response.status)
                if not isinstance(data, dict):
                    raise PlategaError("INVALID_RESPONSE", "Platega returned a non-object response", response.status)
                return data
        except (aiohttp.ClientError, TimeoutError) as error:
            raise PlategaError("NETWORK_ERROR", str(error)) from error

    async def create_payment_link(
        self,
        amount: Decimal,
        token_amount: int,
        payload: str,
        user_id: int,
        user_name: str | None = None,
    ) -> dict[str, Any]:
        # Intentionally do not pass paymentMethod: the hosted page must let the
        # payer choose among the methods enabled for this merchant.
        metadata = {"userId": str(user_id)}
        if user_name:
            metadata["userName"] = user_name[:80]
        body = {
            "paymentDetails": {
                "amount": self._amount_value(amount),
                "currency": "RUB",
            },
            "description": f"Пополнение Emerald AI: {token_amount} токенов",
            "return": self.return_url,
            "failedUrl": self.failed_url,
            "payload": payload,
            "metadata": metadata,
        }
        result = await self._request("POST", "/v2/transaction/process", json=body)
        transaction_id = result.get("transactionId")
        payment_url = result.get("url") or result.get("redirect")
        if not transaction_id or not payment_url:
            raise PlategaError("INVALID_RESPONSE", "Transaction ID or payment URL is missing")
        return result

    async def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        result = await self._request("GET", f"/transaction/{transaction_id}")
        if not result.get("id"):
            raise PlategaError("INVALID_RESPONSE", "Transaction ID is missing")
        return result

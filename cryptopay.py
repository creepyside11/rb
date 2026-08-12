import json
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CryptoPayError(RuntimeError):
    pass


class CryptoPayClient:
    API_URL = "https://pay.crypt.bot/api"

    def __init__(self, token: str):
        if not token:
            raise RuntimeError("CRYPTOBOT_TOKEN is not configured")
        self._token = token

    def _request(self, method: str, payload: dict | None = None):
        request = Request(
            f"{self.API_URL}/{method}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Crypto-Pay-API-Token": self._token,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8")).get("error", "HTTP error")
            except (ValueError, AttributeError):
                detail = "HTTP error"
            raise CryptoPayError(f"Crypto Pay rejected the request: {detail}") from error
        except (URLError, TimeoutError, ValueError) as error:
            raise CryptoPayError("Crypto Pay is temporarily unavailable") from error
        if not body.get("ok"):
            raise CryptoPayError(f"Crypto Pay API error: {body.get('error', 'unknown error')}")
        return body.get("result")

    def create_rub_invoice(self, rub_amount: int, payload: str) -> dict:
        return self._request(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "RUB",
                "accepted_assets": "USDT,TON,TRX,USDC",
                "amount": f"{Decimal(rub_amount):.2f}",
                "description": f"{rub_amount * 1_000_000:,} токенов Emerald AI".replace(",", " "),
                "payload": payload,
                "expires_in": 3600,
                "allow_comments": False,
                "allow_anonymous": False,
            },
        )

    def get_invoice(self, invoice_id: int) -> dict | None:
        invoices = self._request("getInvoices", {"invoice_ids": str(invoice_id), "count": 1}) or []
        return next((item for item in invoices if int(item.get("invoice_id", 0)) == invoice_id), None)


def invoice_payment_url(invoice: dict) -> str | None:
    return (
        invoice.get("bot_invoice_url")
        or invoice.get("mini_app_invoice_url")
        or invoice.get("web_app_invoice_url")
        or invoice.get("pay_url")
    )

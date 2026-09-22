import unittest
from decimal import Decimal
from unittest.mock import AsyncMock

from platega import PlategaClient, PlategaError


class PlategaClientTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = PlategaClient(
            "merchant-id",
            "api-key",
            "https://t.me/emeraldairobot",
            "https://t.me/emeraldairobot",
        )

    async def test_payment_link_uses_v2_checkout_without_forced_method(self):
        self.client._request = AsyncMock(return_value={
            "transactionId": "transaction-id",
            "status": "PENDING",
            "url": "https://pay.platega.io/?id=transaction-id",
            "expiresIn": "01:00:00",
        })

        result = await self.client.create_payment_link(
            Decimal("25.50"),
            25_500_000,
            "private-payload",
            42,
            "Test User",
        )

        self.assertEqual(result["transactionId"], "transaction-id")
        _, path = self.client._request.await_args.args
        body = self.client._request.await_args.kwargs["json"]
        self.assertEqual(path, "/v2/transaction/process")
        self.assertNotIn("paymentMethod", body)
        self.assertEqual(body["paymentDetails"], {"amount": 25.5, "currency": "RUB"})
        self.assertEqual(body["metadata"]["userId"], "42")

    async def test_missing_checkout_url_is_rejected(self):
        self.client._request = AsyncMock(return_value={"transactionId": "transaction-id"})

        with self.assertRaises(PlategaError) as context:
            await self.client.create_payment_link(
                Decimal("10.00"),
                10_000_000,
                "payload",
                1,
            )

        self.assertEqual(context.exception.code, "INVALID_RESPONSE")

    async def test_get_transaction_unwraps_data_and_handles_ids(self):
        # Top-level id
        self.client._request = AsyncMock(return_value={
            "id": "tx-123",
            "status": "CONFIRMED",
            "paymentDetails": {"amount": 10, "currency": "RUB"},
        })
        res1 = await self.client.get_transaction("tx-123")
        self.assertEqual(res1["id"], "tx-123")
        self.assertEqual(res1["status"], "CONFIRMED")

        # Nested in "data"
        self.client._request = AsyncMock(return_value={
            "data": {
                "transactionId": "tx-456",
                "status": "PENDING",
            }
        })
        res2 = await self.client.get_transaction("tx-456")
        self.assertEqual(res2["transactionId"], "tx-456")

        # Missing id raises error
        self.client._request = AsyncMock(return_value={"status": "CONFIRMED"})
        with self.assertRaises(PlategaError) as ctx:
            await self.client.get_transaction("missing-id")
        self.assertEqual(ctx.exception.code, "INVALID_RESPONSE")


if __name__ == "__main__":
    unittest.main()

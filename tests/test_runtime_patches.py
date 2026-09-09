import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from runtime_patches import apply, normalize_channel_username


class RuntimePatchTest(unittest.IsolatedAsyncioTestCase):
    def test_blank_channel_username_is_validation_error_not_index_error(self):
        for value in ("", "   ", "@", "  @  "):
            with self.assertRaises(ValueError):
                normalize_channel_username(value)

    def test_channel_username_is_normalized(self):
        self.assertEqual(normalize_channel_username("  @emeraldainews  "), "emeraldainews")

    async def test_no_payment_providers_returns_clear_message(self):
        original = AsyncMock()
        core = SimpleNamespace(
            offer_payment_methods=original,
            payment_keyboard=lambda *_: None,
            normalize_channel_username=None,
        )
        apply(core)
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())

        await core.offer_payment_methods(
            message,
            state,
            10_000_000,
            Decimal("10.00"),
            None,
            None,
        )

        state.clear.assert_awaited_once()
        original.assert_not_awaited()
        message.answer.assert_awaited_once()
        text = message.answer.await_args.args[0]
        self.assertIn("Оплата временно недоступна", text)

    async def test_available_provider_uses_original_flow(self):
        original = AsyncMock()
        core = SimpleNamespace(
            offer_payment_methods=original,
            payment_keyboard=lambda *_: None,
            normalize_channel_username=None,
        )
        apply(core)
        message = object()
        state = object()
        crypto = object()

        await core.offer_payment_methods(
            message,
            state,
            1_000_000,
            Decimal("1.00"),
            crypto,
            None,
        )

        original.assert_awaited_once_with(
            message,
            state,
            1_000_000,
            Decimal("1.00"),
            crypto,
            None,
        )

    def test_runtime_crypto_keyboard_has_clean_back_label(self):
        core = SimpleNamespace(
            offer_payment_methods=AsyncMock(),
            payment_keyboard=lambda *_: None,
            normalize_channel_username=None,
        )
        apply(core)
        keyboard = core.payment_keyboard("https://example.com", 7)
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("⬅️ Другой пакет", labels)
        self.assertFalse(any("�" in label for label in labels))


if __name__ == "__main__":
    unittest.main()

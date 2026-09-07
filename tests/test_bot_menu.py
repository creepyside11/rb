import unittest
from decimal import Decimal

from bot import (
    CHANNEL_URL,
    PRIVACY_POLICY_URL,
    SUPPORT_URL,
    USER_AGREEMENT_URL,
    admin_keyboard,
    balance_text,
    main_menu_keyboard,
    package_keyboard,
    payment_keyboard,
    payment_method_keyboard,
    platega_payment_keyboard,
    subscription_gate_keyboard,
)
from payments import SUBSCRIPTION_CHANNEL_USERNAME, SUBSCRIPTION_REWARD_TOKENS


class BotMenuTest(unittest.TestCase):
    def test_main_menu_contains_purchase_support_and_legal_links(self):
        buttons = [button for row in main_menu_keyboard().inline_keyboard for button in row]
        by_text = {button.text: button for button in buttons}

        self.assertEqual(by_text["💎 Купить токены"].callback_data, "show:packages")
        self.assertEqual(by_text["💎 Купить токены"].style, "primary")
        self.assertEqual(by_text["💰 Проверить баланс"].style, "success")
        self.assertEqual(by_text["🛟 Поддержка"].url, SUPPORT_URL)
        self.assertEqual(by_text["📄 Пользовательское соглашение"].url, USER_AGREEMENT_URL)
        self.assertEqual(by_text["🔒 Политика конфиденциальности"].url, PRIVACY_POLICY_URL)

    def test_balance_has_no_old_platega_announcement(self):
        text = balance_text(25_000_000)

        self.assertIn("25 000 000", text)
        self.assertNotIn("Скоро", text)

    def test_platega_button_uses_automatic_checkout_flow(self):
        buttons = [
            button
            for row in payment_method_keyboard(crypto_available=False, platega_available=True).inline_keyboard
            for button in row
        ]
        by_callback = {button.callback_data: button for button in buttons if button.callback_data}

        self.assertEqual(
            by_callback["method:platega"].text,
            "🏦 СБП Платега · автоматически",
        )
        self.assertNotIn("method:sbp", by_callback)
        self.assertEqual(by_callback["method:platega"].style, "success")

    def test_admin_keyboard_is_simple_and_consolidated(self):
        buttons = [
            button
            for row in admin_keyboard().inline_keyboard
            for button in row
        ]
        by_callback = {button.callback_data: button for button in buttons}

        self.assertEqual(by_callback["admin:stats"].text, "📊 Статистика")
        self.assertEqual(by_callback["admin:price"].text, "💵 Цена токенов")
        self.assertEqual(by_callback["admin:users"].text, "👥 Пользователи")
        self.assertEqual(by_callback["admin:payments"].text, "🧾 Платежи")
        self.assertEqual(by_callback["admin:broadcast"].text, "📣 Рассылка")
        self.assertEqual(by_callback["admin:stats"].style, "primary")
        self.assertEqual(by_callback["admin:payments"].style, "success")
        # Old split lists are gone.
        self.assertNotIn("admin:site_users", by_callback)
        self.assertNotIn("admin:bot_users", by_callback)

    def test_subscription_gate_promotes_channel_and_reward(self):
        buttons = [
            button
            for row in subscription_gate_keyboard().inline_keyboard
            for button in row
        ]
        by_callback = {button.callback_data: button for button in buttons if button.callback_data}

        self.assertEqual(
            by_callback["check:subscription"].text,
            "✅ Я подписался",
        )
        self.assertEqual(by_callback["check:subscription"].style, "success")
        url_buttons = [button for button in buttons if button.url]
        self.assertEqual(len(url_buttons), 1)
        self.assertEqual(url_buttons[0].url, CHANNEL_URL)
        self.assertIn(SUBSCRIPTION_CHANNEL_USERNAME, url_buttons[0].text)
        self.assertEqual(SUBSCRIPTION_REWARD_TOKENS, 400_000)

    def test_payment_actions_use_new_button_styles(self):
        crypto_buttons = [button for row in payment_keyboard("https://example.com", 42).inline_keyboard for button in row]
        platega_buttons = [button for row in platega_payment_keyboard("https://example.com").inline_keyboard for button in row]

        self.assertEqual(crypto_buttons[0].style, "success")
        self.assertEqual(crypto_buttons[1].style, "primary")
        self.assertEqual(platega_buttons[0].style, "success")

    def test_package_menu_uses_configured_price(self):
        buttons = [
            button
            for row in package_keyboard(Decimal("2.50")).inline_keyboard
            for button in row
        ]
        by_callback = {button.callback_data: button for button in buttons if button.callback_data}

        self.assertIn("2.5 ₽", by_callback["buy:1"].text)
        self.assertIn("25 ₽", by_callback["buy:10"].text)

    def test_package_menu_can_return_to_main_menu(self):
        callbacks = {
            button.callback_data
            for row in package_keyboard().inline_keyboard
            for button in row
            if button.callback_data
        }

        self.assertIn("show:menu", callbacks)


if __name__ == "__main__":
    unittest.main()

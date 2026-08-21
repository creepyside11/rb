import unittest

from bot import (
    PAYMENT_ANNOUNCEMENT,
    PRIVACY_POLICY_URL,
    SUPPORT_URL,
    USER_AGREEMENT_URL,
    balance_text,
    main_menu_keyboard,
    package_keyboard,
)


class BotMenuTest(unittest.TestCase):
    def test_main_menu_contains_purchase_support_and_legal_links(self):
        buttons = [button for row in main_menu_keyboard().inline_keyboard for button in row]
        by_text = {button.text: button for button in buttons}

        self.assertEqual(by_text["💎 Купить токены"].callback_data, "show:packages")
        self.assertEqual(by_text["🛟 Поддержка"].url, SUPPORT_URL)
        self.assertEqual(by_text["📄 Пользовательское соглашение"].url, USER_AGREEMENT_URL)
        self.assertEqual(by_text["🔒 Политика конфиденциальности"].url, PRIVACY_POLICY_URL)

    def test_balance_contains_payment_announcement(self):
        text = balance_text(25_000_000)

        self.assertIn("25 000 000", text)
        self.assertIn(PAYMENT_ANNOUNCEMENT, text)

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

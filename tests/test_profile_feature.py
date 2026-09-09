import unittest

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from profile_feature import (
    HIDE_PASSWORD_CALLBACK,
    PROFILE_CALLBACK,
    RESET_CALLBACK,
    RESET_CONFIRM_CALLBACK,
    add_profile_button,
    new_password_keyboard,
    profile_keyboard,
    profile_text,
    reset_confirmation_keyboard,
)


class ProfileFeatureTest(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "id": 42,
            "name": "Emerald TEST",
            "email": "em-test@emeraldai.sbs",
            "token_balance": 25_000_000,
        }

    def test_profile_button_is_added_once(self):
        base = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💎 Купить", callback_data="show:packages")
        ]])
        first = add_profile_button(base)
        second = add_profile_button(first)
        callbacks = [
            button.callback_data
            for row in second.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertEqual(callbacks.count(PROFILE_CALLBACK), 1)

    def test_profile_text_contains_copyable_identity_and_balance(self):
        value = profile_text(self.profile)
        self.assertIn("Emerald TEST", value)
        self.assertIn("em-test@emeraldai.sbs", value)
        self.assertIn("25 000 000", value)
        self.assertIn("хэш", value)

    def test_profile_keyboard_has_copy_and_reset_actions(self):
        keyboard = profile_keyboard(self.profile)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        by_callback = {button.callback_data: button for button in buttons if button.callback_data}
        copy_values = [button.copy_text.text for button in buttons if button.copy_text]
        self.assertIn("Emerald TEST", copy_values)
        self.assertIn("em-test@emeraldai.sbs", copy_values)
        self.assertEqual(by_callback[RESET_CALLBACK].style, "danger")

    def test_reset_confirmation_requires_explicit_confirmation(self):
        buttons = [button for row in reset_confirmation_keyboard().inline_keyboard for button in row]
        callbacks = {button.callback_data for button in buttons if button.callback_data}
        self.assertIn(RESET_CONFIRM_CALLBACK, callbacks)
        self.assertIn(PROFILE_CALLBACK, callbacks)

    def test_new_password_keyboard_copies_and_can_hide_password(self):
        keyboard = new_password_keyboard("secret-password")
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        copy_values = [button.copy_text.text for button in buttons if button.copy_text]
        callbacks = {button.callback_data for button in buttons if button.callback_data}
        self.assertEqual(copy_values, ["secret-password"])
        self.assertIn(HIDE_PASSWORD_CALLBACK, callbacks)


if __name__ == "__main__":
    unittest.main()

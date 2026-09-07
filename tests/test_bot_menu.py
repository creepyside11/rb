import unittest
from decimal import Decimal
from types import SimpleNamespace

from bot import (
    CHANNEL_URL,
    PRIVACY_POLICY_URL,
    SUPPORT_URL,
    USER_AGREEMENT_URL,
    admin_battle_pass_detail_keyboard,
    admin_battle_pass_keyboard,
    admin_keyboard,
    admin_task_detail_keyboard,
    admin_tasks_keyboard,
    balance_text,
    battle_pass_keyboard,
    battle_pass_text,
    documents_keyboard,
    free_token_task_keyboard,
    free_tokens_keyboard,
    main_menu_keyboard,
    package_keyboard,
    payment_keyboard,
    payment_method_keyboard,
    platega_payment_keyboard,
    subscription_gate_keyboard,
)
from payments import (
    SUBSCRIPTION_CHANNEL_USERNAME,
    SUBSCRIPTION_REWARD_TOKENS,
    normalize_channel_username,
    normalize_free_token_reward,
)


class BotMenuTest(unittest.TestCase):
    def test_main_menu_is_compact_with_free_tokens_section(self):
        buttons = [button for row in main_menu_keyboard().inline_keyboard for button in row]
        by_text = {button.text: button for button in buttons}

        self.assertEqual(by_text["💎 Купить"].callback_data, "show:packages")
        self.assertEqual(by_text["💎 Купить"].style, "primary")
        self.assertEqual(by_text["💰 Баланс"].callback_data, "show:balance")
        self.assertEqual(by_text["💰 Баланс"].style, "success")
        self.assertEqual(by_text["🎁 Бесплатные токены"].callback_data, "show:free_tokens")
        self.assertEqual(by_text["🛟 Поддержка"].url, SUPPORT_URL)
        self.assertEqual(by_text["📄 Документы"].callback_data, "show:documents")
        # Legal links moved out of the main menu.
        self.assertNotIn("📄 Пользовательское соглашение", by_text)
        self.assertNotIn("🔒 Политика конфиденциальности", by_text)

    def test_main_menu_has_three_compact_rows_and_battle_pass(self):
        keyboard = main_menu_keyboard()
        self.assertEqual([len(row) for row in keyboard.inline_keyboard], [2, 2, 2])
        by_callback = {
            button.callback_data: button
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertEqual(by_callback["show:battle_pass"].text, "🏆 Battle Pass")

    def test_battle_pass_keyboard_only_claims_unlocked_levels(self):
        levels = [
            SimpleNamespace(id=1, title="Старт", required_purchase_tokens=10_000_000, reward_tokens=500_000),
            SimpleNamespace(id=2, title="Профи", required_purchase_tokens=50_000_000, reward_tokens=2_000_000),
            SimpleNamespace(id=3, title="Легенда", required_purchase_tokens=100_000_000, reward_tokens=5_000_000),
        ]
        keyboard = battle_pass_keyboard(levels, 60_000_000, {1})
        by_callback = {
            button.callback_data: button
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        }

        self.assertNotIn("battle_pass:claim:1", by_callback)
        self.assertEqual(by_callback["battle_pass:claim:2"].text, "🎁 Забрать: Профи")
        self.assertNotIn("battle_pass:claim:3", by_callback)
        self.assertIn("show:packages", by_callback)
        text = battle_pass_text(levels, 60_000_000, {1})
        self.assertIn("■■■■■■□□□□", text)
        self.assertIn("40 000 000", text)
        self.assertIn("можно забрать", text)

    def test_admin_battle_pass_keyboards_support_full_management(self):
        levels = [
            SimpleNamespace(id=7, title="Старт", required_purchase_tokens=10_000_000, is_active=True),
            SimpleNamespace(id=8, title="Профи", required_purchase_tokens=50_000_000, is_active=False),
        ]
        list_buttons = [
            button
            for row in admin_battle_pass_keyboard(levels).inline_keyboard
            for button in row
        ]
        list_by_callback = {button.callback_data: button for button in list_buttons if button.callback_data}
        self.assertEqual(list_by_callback["admin:bp:view:7"].text, "✅ Старт · 10 000 000")
        self.assertEqual(list_by_callback["admin:bp:view:8"].text, "⏸ Профи · 50 000 000")
        self.assertIn("admin:bp:add", list_by_callback)

        detail_buttons = [
            button
            for row in admin_battle_pass_detail_keyboard(levels[0]).inline_keyboard
            for button in row
        ]
        detail_callbacks = {button.callback_data for button in detail_buttons if button.callback_data}
        self.assertIn("admin:bp:toggle:7", detail_callbacks)
        self.assertIn("admin:bp:edit:title:7", detail_callbacks)
        self.assertIn("admin:bp:edit:threshold:7", detail_callbacks)
        self.assertIn("admin:bp:edit:reward:7", detail_callbacks)
        self.assertIn("admin:bp:archive:7", detail_callbacks)

    def test_documents_keyboard_links_to_legal_pages(self):
        buttons = [button for row in documents_keyboard().inline_keyboard for button in row]
        by_text = {button.text: button for button in buttons}

        self.assertEqual(by_text["📄 Пользовательское соглашение"].url, USER_AGREEMENT_URL)
        self.assertEqual(by_text["🔒 Политика конфиденциальности"].url, PRIVACY_POLICY_URL)
        self.assertEqual(by_text["⬅️ Главное меню"].callback_data, "show:menu")

    def test_free_tokens_keyboard_lists_tasks_and_returns_to_menu(self):
        tasks = [
            SimpleNamespace(id=1, title="Новости", reward_tokens=400_000),
            SimpleNamespace(id=2, title="Партнёр", reward_tokens=1_000_000),
        ]
        buttons = [button for row in free_tokens_keyboard(tasks).inline_keyboard for button in row]
        by_callback = {button.callback_data: button for button in buttons if button.callback_data}

        self.assertEqual(by_callback["free:task:1"].text, "🎁 Новости · 400 000")
        self.assertEqual(by_callback["free:task:2"].text, "🎁 Партнёр · 1 000 000")
        self.assertEqual(by_callback["show:menu"].text, "⬅️ Главное меню")

    def test_free_token_task_keyboard_links_to_channel_and_checks(self):
        task = SimpleNamespace(id=7, channel_username="emeraldainews", reward_tokens=500_000)
        buttons = [button for row in free_token_task_keyboard(task).inline_keyboard for button in row]
        by_callback = {button.callback_data: button for button in buttons if button.callback_data}

        self.assertEqual(by_callback["free:check:7"].text, "✅ Проверить подписку")
        self.assertEqual(by_callback["free:check:7"].style, "success")
        url_buttons = [button for button in buttons if button.url]
        self.assertEqual(url_buttons[0].url, "https://t.me/emeraldainews")
        self.assertIn("@emeraldainews", url_buttons[0].text)

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

    def test_admin_keyboard_includes_free_tokens_section(self):
        buttons = [
            button
            for row in admin_keyboard().inline_keyboard
            for button in row
        ]
        by_callback = {button.callback_data: button for button in buttons}

        self.assertEqual(by_callback["admin:stats"].text, "📊 Статистика")
        self.assertEqual(by_callback["admin:price"].text, "💵 Цена токенов")
        self.assertEqual(by_callback["admin:tasks"].text, "🎁 Бесплатные токены")
        self.assertEqual(by_callback["admin:battle_pass"].text, "🏆 Battle Pass")
        self.assertEqual(by_callback["admin:users"].text, "👥 Пользователи")
        self.assertEqual(by_callback["admin:payments"].text, "🧾 Платежи")
        self.assertEqual(by_callback["admin:broadcast"].text, "📣 Рассылка")
        self.assertEqual(by_callback["admin:stats"].style, "primary")
        self.assertEqual(by_callback["admin:payments"].style, "success")
        self.assertNotIn("admin:site_users", by_callback)
        self.assertNotIn("admin:bot_users", by_callback)

    def test_admin_tasks_keyboard_lists_tasks_and_add_button(self):
        tasks = [
            SimpleNamespace(id=1, channel_username="emeraldainews", reward_tokens=400_000, is_active=True),
            SimpleNamespace(id=2, channel_username="partner", reward_tokens=1_000_000, is_active=False),
        ]
        buttons = [button for row in admin_tasks_keyboard(tasks).inline_keyboard for button in row]
        by_callback = {button.callback_data: button for button in buttons if button.callback_data}

        self.assertEqual(by_callback["admin:task:1"].text, "✅ @emeraldainews · 400 000")
        self.assertEqual(by_callback["admin:task:2"].text, "⏸ @partner · 1 000 000")
        self.assertEqual(by_callback["admin:task:add"].text, "➕ Добавить задание")
        self.assertEqual(by_callback["admin:task:add"].style, "success")
        self.assertEqual(by_callback["admin:home"].text, "⬅️ Админ-панель")

    def test_admin_task_detail_keyboard_toggles_and_deletes(self):
        active_task = SimpleNamespace(id=3, is_active=True)
        inactive_task = SimpleNamespace(id=4, is_active=False)
        active_buttons = [
            button
            for row in admin_task_detail_keyboard(active_task).inline_keyboard
            for button in row
        ]
        inactive_buttons = [
            button
            for row in admin_task_detail_keyboard(inactive_task).inline_keyboard
            for button in row
        ]
        active_by_callback = {b.callback_data: b for b in active_buttons if b.callback_data}
        inactive_by_callback = {b.callback_data: b for b in inactive_buttons if b.callback_data}

        self.assertEqual(active_by_callback["admin:task:toggle:3"].text, "⏸ Выключить")
        self.assertEqual(inactive_by_callback["admin:task:toggle:4"].text, "▶️ Включить")
        self.assertEqual(active_by_callback["admin:task:delete:3"].text, "🗑 Удалить")
        self.assertEqual(active_by_callback["admin:task:delete:3"].style, "danger")

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

    def test_normalize_channel_username_strips_at_and_validates(self):
        self.assertEqual(normalize_channel_username("@emeraldainews"), "emeraldainews")
        self.assertEqual(normalize_channel_username("emeraldainews"), "emeraldainews")
        with self.assertRaises(ValueError):
            normalize_channel_username("ab")
        with self.assertRaises(ValueError):
            normalize_channel_username("bad space")

    def test_normalize_free_token_reward_accepts_integers(self):
        self.assertEqual(normalize_free_token_reward("500000"), 500_000)
        self.assertEqual(normalize_free_token_reward("1 000 000"), 1_000_000)
        with self.assertRaises(ValueError):
            normalize_free_token_reward("0")
        with self.assertRaises(ValueError):
            normalize_free_token_reward("not a number")


if __name__ == "__main__":
    unittest.main()

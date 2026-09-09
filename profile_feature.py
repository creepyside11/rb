from __future__ import annotations

import asyncio
import html
import secrets
import sys
from datetime import datetime, timezone
from typing import Any

from aiogram import F
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import text
from werkzeug.security import generate_password_hash


PROFILE_CALLBACK = "show:profile"
RESET_CALLBACK = "profile:reset_password"
RESET_CONFIRM_CALLBACK = "profile:reset_password:confirm"
HIDE_PASSWORD_CALLBACK = "profile:hide_password"
_installed_modules: set[int] = set()
_legacy_bootstrap_enabled = False
_original_asyncio_run = asyncio.run


def add_profile_button(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = [list(row) for row in markup.inline_keyboard]
    if any(button.callback_data == PROFILE_CALLBACK for row in rows for button in row):
        return markup
    rows.append([
        InlineKeyboardButton(
            text="👤 Профиль",
            callback_data=PROFILE_CALLBACK,
            style="primary",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_text(profile: dict[str, Any]) -> str:
    return (
        "👤 <b>Профиль Emerald AI</b>\n\n"
        f"Ник: <code>{html.escape(str(profile['name']))}</code>\n"
        f"Email / логин: <code>{html.escape(str(profile['email']))}</code>\n"
        f"ID: <code>{int(profile['id'])}</code>\n"
        f"Баланс: <b>{format(int(profile['token_balance']), ',').replace(',', ' ')}</b> токенов\n\n"
        "🔐 Пароль хранится только как хэш и не может быть показан. "
        "Если вы его забыли, нажмите «Сбросить пароль»."
    )


def profile_keyboard(profile: dict[str, Any]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📋 Скопировать ник",
                copy_text=CopyTextButton(text=str(profile["name"])),
                style="primary",
            ),
            InlineKeyboardButton(
                text="📋 Скопировать email",
                copy_text=CopyTextButton(text=str(profile["email"])),
                style="primary",
            ),
        ],
        [InlineKeyboardButton(
            text="🔑 Сбросить пароль",
            callback_data=RESET_CALLBACK,
            style="danger",
        )],
        [InlineKeyboardButton(
            text="⬅️ Главное меню",
            callback_data="show:menu",
            style="primary",
        )],
    ])


def reset_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚠️ Да, сбросить пароль",
            callback_data=RESET_CONFIRM_CALLBACK,
            style="danger",
        )],
        [InlineKeyboardButton(
            text="⬅️ Назад в профиль",
            callback_data=PROFILE_CALLBACK,
            style="primary",
        )],
    ])


def new_password_keyboard(password: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 Скопировать новый пароль",
            copy_text=CopyTextButton(text=password),
            style="success",
        )],
        [InlineKeyboardButton(
            text="🗑 Скрыть пароль из чата",
            callback_data=HIDE_PASSWORD_CALLBACK,
            style="danger",
        )],
        [InlineKeyboardButton(
            text="👤 Вернуться в профиль",
            callback_data=PROFILE_CALLBACK,
            style="primary",
        )],
    ])


def _load_profile(bot_module, session_factory, telegram_user_id: int):
    with session_factory() as session:
        link = bot_module.get_bound_link(session, telegram_user_id)
        if link is None:
            return None
        user = session.get(bot_module.User, link.user_id)
        if user is None:
            return None
        return {
            "id": int(user.id),
            "name": user.name,
            "email": user.email,
            "token_balance": int(user.token_balance or 0),
        }


def _reset_password(bot_module, session_factory, telegram_user_id: int):
    password = secrets.token_urlsafe(14)
    password_hash = generate_password_hash(password)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        link = bot_module.get_bound_link(session, telegram_user_id)
        if link is None:
            return None
        user = session.get(bot_module.User, link.user_id)
        if user is None:
            return None
        session.execute(
            text("UPDATE users SET password_hash = :password_hash WHERE id = :user_id"),
            {"password_hash": password_hash, "user_id": user.id},
        )
        session.execute(
            text(
                "UPDATE auth_sessions SET revoked_at = :revoked_at "
                "WHERE user_id = :user_id AND revoked_at IS NULL"
            ),
            {"revoked_at": now, "user_id": user.id},
        )
        session.commit()
        return {
            "id": int(user.id),
            "name": user.name,
            "email": user.email,
            "token_balance": int(user.token_balance or 0),
            "password": password,
        }


def _private_message(callback: CallbackQuery) -> bool:
    return bool(callback.message and callback.message.chat.type == ChatType.PRIVATE)


def install(bot_module) -> None:
    module_id = id(bot_module)
    if module_id in _installed_modules:
        return
    _installed_modules.add(module_id)

    original_main_menu_keyboard = bot_module.main_menu_keyboard

    def main_menu_keyboard_with_profile() -> InlineKeyboardMarkup:
        return add_profile_button(original_main_menu_keyboard())

    bot_module.main_menu_keyboard = main_menu_keyboard_with_profile

    @bot_module.router.callback_query(F.data == PROFILE_CALLBACK)
    async def show_profile(callback: CallbackQuery, session_factory):
        if not _private_message(callback):
            await callback.answer("Профиль доступен только в личном чате с ботом.", show_alert=True)
            return
        await callback.answer()
        profile = _load_profile(bot_module, session_factory, callback.from_user.id)
        if profile is None:
            await callback.message.answer(
                "🔗 Сначала привяжите аккаунт Emerald AI через персональную ссылку из кабинета.",
                reply_markup=bot_module.main_menu_keyboard(),
            )
            return
        await callback.message.answer(
            profile_text(profile),
            reply_markup=profile_keyboard(profile),
        )

    @bot_module.router.callback_query(F.data == RESET_CALLBACK)
    async def confirm_password_reset(callback: CallbackQuery):
        if not _private_message(callback):
            await callback.answer("Сброс пароля доступен только в личном чате с ботом.", show_alert=True)
            return
        await callback.answer()
        await callback.message.answer(
            "⚠️ <b>Сбросить пароль?</b>\n\n"
            "Текущий пароль перестанет работать, а все активные веб-сессии аккаунта будут завершены. "
            "Новый пароль бот покажет после подтверждения.",
            reply_markup=reset_confirmation_keyboard(),
        )

    @bot_module.router.callback_query(F.data == RESET_CONFIRM_CALLBACK)
    async def reset_password(callback: CallbackQuery, session_factory):
        if not _private_message(callback):
            await callback.answer("Сброс пароля доступен только в личном чате с ботом.", show_alert=True)
            return
        await callback.answer()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        result = _reset_password(bot_module, session_factory, callback.from_user.id)
        if result is None:
            await callback.message.answer(
                "🔗 Привязанный аккаунт не найден.",
                reply_markup=bot_module.main_menu_keyboard(),
            )
            return
        password = result["password"]
        await callback.message.answer(
            "✅ <b>Пароль успешно сброшен</b>\n\n"
            f"Email / логин: <code>{html.escape(str(result['email']))}</code>\n"
            f"Новый пароль: <tg-spoiler><code>{html.escape(password)}</code></tg-spoiler>\n\n"
            "Сохраните новый пароль сейчас. В базе он уже хранится только как хэш, "
            "поэтому повторно показать этот пароль бот не сможет. Старые веб-сессии отозваны.",
            reply_markup=new_password_keyboard(password),
        )

    @bot_module.router.callback_query(F.data == HIDE_PASSWORD_CALLBACK)
    async def hide_password(callback: CallbackQuery):
        if not _private_message(callback):
            await callback.answer("Недоступно в этом чате.", show_alert=True)
            return
        await callback.answer("Пароль скрыт")
        try:
            await callback.message.delete()
        except Exception:
            await callback.message.edit_text(
                "🔒 Сообщение с новым паролем скрыто.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="👤 Вернуться в профиль",
                        callback_data=PROFILE_CALLBACK,
                        style="primary",
                    )
                ]]),
            )


def enable_legacy_bot_entrypoint() -> None:
    """Install the profile feature even when hosting still runs `python bot.py`."""
    global _legacy_bootstrap_enabled
    if _legacy_bootstrap_enabled:
        return
    _legacy_bootstrap_enabled = True

    def run_with_profile(main, *, debug=None, loop_factory=None):
        main_module = sys.modules.get("__main__")
        if (
            main_module is not None
            and hasattr(main_module, "router")
            and hasattr(main_module, "main_menu_keyboard")
            and hasattr(main_module, "get_bound_link")
            and hasattr(main_module, "User")
        ):
            install(main_module)
        return _original_asyncio_run(main, debug=debug, loop_factory=loop_factory)

    asyncio.run = run_with_profile

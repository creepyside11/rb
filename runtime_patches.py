from __future__ import annotations

import re
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import payments


def normalize_channel_username(value: str) -> str:
    """Validate channel usernames without crashing on blank/whitespace input."""
    cleaned = (value or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", cleaned):
        raise ValueError("Username должен быть 4–32 символа: A–Z, a–z, 0–9, _")
    return cleaned


def apply(core) -> None:
    # The original helper indexed split()[0] and raised IndexError for a string
    # containing only spaces. Patch both modules because create_free_token_task
    # resolves its own module global while handlers resolve bot_core's global.
    payments.normalize_channel_username = normalize_channel_username
    core.normalize_channel_username = normalize_channel_username

    original_offer_payment_methods = core.offer_payment_methods

    async def offer_payment_methods(
        message,
        state,
        token_amount: int,
        rub_amount: Decimal,
        crypto,
        platega,
    ):
        if crypto is None and platega is None:
            await state.clear()
            await message.answer(
                "⚠️ <b>Оплата временно недоступна</b>\n\n"
                "Сейчас ни один платёжный способ не подключён. "
                "Попробуйте позже или обратитесь в поддержку.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="⬅️ Главное меню",
                        callback_data="show:menu",
                        style="primary",
                    )
                ]]),
            )
            return
        await original_offer_payment_methods(
            message,
            state,
            token_amount,
            rub_amount,
            crypto,
            platega,
        )

    core.offer_payment_methods = offer_payment_methods

    # Repair the visible corrupted label in the runtime payment keyboard without
    # touching the large legacy core file.
    def payment_keyboard(payment_url: str, payment_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="💳 Оплатить в Crypto Bot",
                url=payment_url,
                style="success",
            )],
            [InlineKeyboardButton(
                text="✅ Проверить оплату",
                callback_data=f"check:{payment_id}",
                style="primary",
            )],
            [InlineKeyboardButton(
                text="⬅️ Другой пакет",
                callback_data="show:packages",
                style="primary",
            )],
        ])

    core.payment_keyboard = payment_keyboard

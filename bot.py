import asyncio
import html
import logging
import os
import re
import secrets
from decimal import Decimal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ErrorEvent, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from cryptopay import CryptoPayClient, CryptoPayError, invoice_payment_url
from database import Base, TokenPayment, User, database_from_environment
from payments import (
    MAX_TOKEN_AMOUNT,
    MIN_TOKEN_AMOUNT,
    PACKAGES,
    bind_purchase_link,
    credit_verified_payment,
    get_bound_link,
    save_pending_payment,
    tokens_to_rubles,
)


logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logger = logging.getLogger("emerald-payment-bot")
router = Router(name="emerald-payments")
LINK_PATTERN = re.compile(r"^buy_([A-Za-z0-9_-]{30,48})$")


class PurchaseState(StatesGroup):
    waiting_for_token_amount = State()


def format_tokens(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def format_rubles(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".")


def package_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"💎 {format_tokens(tokens)} токенов · {rub} ₽",
            callback_data=f"buy:{rub}",
        )]
        for rub, tokens in PACKAGES.items()
    ]
    rows.append([InlineKeyboardButton(text="✍️ Ввести своё количество", callback_data="buy:custom")])
    rows.append([InlineKeyboardButton(text="💰 Проверить баланс", callback_data="show:balance")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_keyboard(payment_url: str, payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить в Crypto Bot", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check:{payment_id}")],
        [InlineKeyboardButton(text="⬅️ Другой пакет", callback_data="show:packages")],
    ])


def crypto_error_message(error: CryptoPayError) -> str:
    code = error.code.upper()
    if error.status == 403 or "FORBIDDEN" in code or "ACCESS" in code or "IP" in code:
        hint = "Проверьте CRYPTOBOT_TOKEN и IP allowlist приложения в @CryptoBot."
    elif "TOKEN" in code or "UNAUTHORIZED" in code:
        hint = "CRYPTOBOT_TOKEN должен быть токеном Crypto Pay → My Apps, а не токеном @BotFather."
    elif "AMOUNT" in code:
        hint = "Crypto Pay отклонил сумму счёта. Попробуйте выбрать больше токенов."
    elif code == "NETWORK_ERROR":
        hint = "Crypto Pay временно не отвечает. Попробуйте ещё раз через минуту."
    else:
        hint = "Проверьте настройки приложения Crypto Pay."
    return f"❌ <b>Не удалось создать счёт</b>\n{hint}\n\nКод: <code>{html.escape(error.code)}</code>"


def read_balance(session_factory, telegram_user_id: int):
    with session_factory() as session:
        link = get_bound_link(session, telegram_user_id)
        user = session.get(User, link.user_id) if link else None
        return user.token_balance if user else None


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session_factory,
):
    await state.clear()
    argument = command.args or ""
    match = LINK_PATTERN.fullmatch(argument)
    with session_factory() as session:
        if match:
            result, link = bind_purchase_link(session, match.group(1), message.from_user.id)
        else:
            link = get_bound_link(session, message.from_user.id)
            result = "ok" if link else "missing"
        if result == "claimed":
            await message.answer("🔒 Эта ссылка уже привязана к другому Telegram‑аккаунту.")
            return
        if result in {"invalid", "missing"} or link is None:
            await message.answer(
                "🔗 Откройте личный кабинет Emerald AI и нажмите «Купить токены», "
                "чтобы получить персональную ссылку."
            )
            return
        user = session.get(User, link.user_id)
        balance = user.token_balance if user else 0
    await message.answer(
        "💚 <b>Пополнение Emerald AI</b>\n\n"
        "💎 Курс: <b>1 000 000 токенов = 1 ₽</b>\n"
        f"💰 Ваш баланс: <b>{format_tokens(balance)}</b> токенов\n\n"
        "Выберите пакет или введите своё количество:",
        reply_markup=package_keyboard(),
    )


@router.message(Command("balance"))
async def balance(message: Message, session_factory):
    amount = read_balance(session_factory, message.from_user.id)
    if amount is None:
        await message.answer("🔗 Сначала откройте бота по ссылке из кабинета Emerald AI.")
    else:
        await message.answer(f"💰 Ваш баланс: <b>{format_tokens(amount)}</b> токенов")


@router.callback_query(F.data == "show:balance")
async def show_balance(callback: CallbackQuery, session_factory):
    await callback.answer()
    amount = read_balance(session_factory, callback.from_user.id)
    if callback.message:
        text = "🔗 Сначала откройте покупку с сайта."
        if amount is not None:
            text = f"💰 Ваш баланс: <b>{format_tokens(amount)}</b> токенов"
        await callback.message.answer(text)


@router.callback_query(F.data == "show:packages")
async def show_packages(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    if callback.message:
        await callback.message.answer("💎 Выберите пакет:", reply_markup=package_keyboard())


@router.callback_query(F.data == "buy:custom")
async def request_custom_amount(callback: CallbackQuery, state: FSMContext, session_factory):
    await callback.answer()
    with session_factory() as session:
        link = get_bound_link(session, callback.from_user.id)
    if callback.message is None:
        return
    if link is None:
        await callback.message.answer("🔗 Сначала откройте покупку по персональной ссылке с сайта.")
        return
    await state.set_state(PurchaseState.waiting_for_token_amount)
    await callback.message.answer(
        "✍️ <b>Введите нужное количество токенов</b>\n\n"
        "Минимум: 1 000 000\n"
        "Например: <code>25000000</code>\n\n"
        "Для отмены отправьте /cancel"
    )


@router.message(Command("cancel"))
async def cancel_custom_amount(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ Ввод отменён.", reply_markup=package_keyboard())


async def issue_invoice(
    message: Message,
    telegram_user_id: int,
    token_amount: int,
    rub_amount: Decimal,
    session_factory,
    crypto: CryptoPayClient,
):
    with session_factory() as session:
        link = get_bound_link(session, telegram_user_id)
    if link is None:
        await message.answer("🔗 Ссылка не привязана. Откройте покупку с сайта.")
        return

    payload = f"em_{secrets.token_urlsafe(20)}"
    try:
        invoice = await crypto.create_rub_invoice(rub_amount, token_amount, payload)
        payment_url = invoice_payment_url(invoice)
        if not payment_url:
            raise CryptoPayError("INVOICE_URL_MISSING")
        with session_factory() as session:
            current_link = get_bound_link(session, telegram_user_id)
            if current_link is None:
                raise RuntimeError("Purchase link was revoked")
            payment = save_pending_payment(
                session,
                current_link,
                telegram_user_id,
                rub_amount,
                token_amount,
                payload,
                invoice,
            )
    except CryptoPayError as error:
        logger.warning("Crypto Pay createInvoice failed: %s", error)
        await message.answer(crypto_error_message(error))
        return
    except (RuntimeError, KeyError, ValueError, SQLAlchemyError):
        logger.exception("Could not store Crypto Pay invoice")
        await message.answer("❌ Счёт создан некорректно. Попробуйте ещё раз через минуту.")
        return

    await message.answer(
        f"🧾 <b>Счёт на {format_rubles(rub_amount)} ₽</b>\n"
        f"💎 Будет начислено: <b>{format_tokens(payment.token_amount)}</b> токенов\n\n"
        "После оплаты нажмите «Проверить оплату».",
        reply_markup=payment_keyboard(payment_url, payment.id),
    )


@router.callback_query(F.data.startswith("buy:"))
async def create_package_invoice(callback: CallbackQuery, state: FSMContext, session_factory, crypto: CryptoPayClient):
    if callback.data == "buy:custom":
        return
    try:
        rubles = int(callback.data.split(":", 1)[1])
        token_amount = PACKAGES[rubles]
    except (ValueError, IndexError, KeyError, AttributeError):
        await callback.answer("❌ Некорректный пакет", show_alert=True)
        return
    await callback.answer("⏳ Создаю счёт…")
    await state.clear()
    if callback.message:
        await issue_invoice(
            callback.message,
            callback.from_user.id,
            token_amount,
            Decimal(rubles),
            session_factory,
            crypto,
        )


@router.message(PurchaseState.waiting_for_token_amount)
async def create_custom_invoice(message: Message, state: FSMContext, session_factory, crypto: CryptoPayClient):
    raw_value = (message.text or "").strip()
    if not re.fullmatch(r"[0-9 _]+", raw_value):
        await message.answer("⚠️ Введите целое число, например: <code>25000000</code>")
        return
    token_amount = int(raw_value.replace(" ", "").replace("_", ""))
    if token_amount < MIN_TOKEN_AMOUNT:
        await message.answer(f"⚠️ Минимум — <b>{format_tokens(MIN_TOKEN_AMOUNT)}</b> токенов.")
        return
    if token_amount > MAX_TOKEN_AMOUNT:
        await message.answer(f"⚠️ Максимум — <b>{format_tokens(MAX_TOKEN_AMOUNT)}</b> токенов.")
        return
    rub_amount = tokens_to_rubles(token_amount)
    await state.clear()
    await message.answer(
        f"🧮 Рассчитано: <b>{format_tokens(token_amount)}</b> токенов = <b>{format_rubles(rub_amount)} ₽</b>"
    )
    await issue_invoice(message, message.from_user.id, token_amount, rub_amount, session_factory, crypto)


@router.callback_query(F.data.startswith("check:"))
async def check_payment(callback: CallbackQuery, session_factory, crypto: CryptoPayClient):
    try:
        payment_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("❌ Некорректный счёт", show_alert=True)
        return
    with session_factory() as session:
        payment = session.get(TokenPayment, payment_id)
        if payment is None or payment.telegram_user_id != callback.from_user.id:
            await callback.answer("❌ Счёт не найден", show_alert=True)
            return
        invoice_id = payment.invoice_id
    await callback.answer("🔍 Проверяю оплату…")
    if callback.message is None:
        return
    try:
        invoice = await crypto.get_invoice(invoice_id)
    except CryptoPayError as error:
        logger.warning("Crypto Pay getInvoices failed for %s: %s", invoice_id, error)
        await callback.message.answer("⚠️ Crypto Bot пока не отвечает. Попробуйте проверить ещё раз.")
        return
    if invoice is None:
        await callback.message.answer("❌ Счёт не найден в Crypto Bot.")
        return
    with session_factory() as session:
        result, payment = credit_verified_payment(session, payment_id, invoice)
        user = session.get(User, payment.user_id) if payment else None
        current_balance = user.token_balance if user else 0
    if result == "credited":
        await callback.message.answer(
            f"✅ <b>Оплата подтверждена!</b>\n"
            f"💎 Начислено: <b>{format_tokens(payment.token_amount)}</b> токенов\n"
            f"💰 Новый баланс: <b>{format_tokens(current_balance)}</b>"
        )
    elif result == "already":
        await callback.message.answer(
            f"✅ Этот счёт уже зачислен.\n💰 Баланс: <b>{format_tokens(current_balance)}</b>"
        )
    elif result == "unpaid":
        await callback.message.answer("⏳ Оплата пока не найдена. Оплатите счёт и проверьте ещё раз.")
    else:
        logger.warning("Invoice verification mismatch for payment %s", payment_id)
        await callback.message.answer("⚠️ Данные счёта не совпали. Баланс не изменён.")


async def handle_error(event: ErrorEvent):
    error = event.exception
    logger.error(
        "Unhandled Telegram update error: %s",
        error,
        exc_info=(type(error), error, error.__traceback__),
    )
    return True


async def main():
    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    crypto_token = os.getenv("CRYPTOBOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")
    if not crypto_token:
        raise RuntimeError("CRYPTOBOT_TOKEN is not configured")
    if bot_token == crypto_token:
        raise RuntimeError("CRYPTOBOT_TOKEN must be a Crypto Pay app token, not BOT_TOKEN")

    engine, session_factory = database_from_environment()
    await asyncio.to_thread(Base.metadata.create_all, engine)
    crypto = CryptoPayClient(crypto_token)
    try:
        crypto_app = await crypto.get_me()
    except CryptoPayError as error:
        await crypto.close()
        engine.dispose()
        raise RuntimeError(
            f"Crypto Pay authentication failed: {error}. Check CRYPTOBOT_TOKEN and IP allowlist."
        ) from error
    logger.info("Crypto Pay authenticated: app_id=%s name=%s", crypto_app.get("app_id"), crypto_app.get("name"))

    bot = Bot(bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher.errors.register(handle_error)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(
            bot,
            session_factory=session_factory,
            crypto=crypto,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        await crypto.close()
        await bot.session.close()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import html
import logging
import os
import re
import secrets
from decimal import Decimal
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
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
from platega import PlategaClient, PlategaError
from payments import (
    MAX_TOKEN_AMOUNT,
    MIN_TOKEN_AMOUNT,
    PACKAGES,
    DEFAULT_TOKEN_PRICE_PER_MILLION,
    admin_bot_user,
    admin_bot_users,
    admin_site_user,
    admin_site_users,
    admin_statistics,
    backfill_bound_bot_users,
    bind_purchase_link,
    broadcast_recipients,
    create_platega_payment,
    credit_verified_platega_payment,
    credit_verified_payment,
    get_bound_link,
    get_pending_payments,
    get_pending_platega_payments,
    recent_platega_payments,
    save_pending_payment,
    set_token_price,
    get_token_price,
    tokens_to_rubles,
    upsert_bot_user,
)


logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logger = logging.getLogger("emerald-payment-bot")
router = Router(name="emerald-payments")
LINK_PATTERN = re.compile(r"^buy_([A-Za-z0-9_-]{30,48})$")
DEFAULT_ADMIN_ID = 7973988177
PLATEGA_INVOICE_TTL_MINUTES = 60
SUPPORT_URL = "https://t.me/EmeraldAiSupport"
PRIVACY_POLICY_URL = "https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-21-72"
USER_AGREEMENT_URL = "https://telegra.ph/POLZOVATELSKOE-SOGLASHENIE-08-21-55"


class PurchaseState(StatesGroup):
    waiting_for_token_amount = State()
    choosing_payment_method = State()


class AdminState(StatesGroup):
    waiting_for_broadcast = State()
    confirming_broadcast = State()
    waiting_for_token_price = State()


def format_tokens(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def format_rubles(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".")


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Купить токены", callback_data="show:packages")],
        [InlineKeyboardButton(text="💰 Проверить баланс", callback_data="show:balance")],
        [InlineKeyboardButton(text="🛟 Поддержка", url=SUPPORT_URL)],
        [InlineKeyboardButton(text="📄 Пользовательское соглашение", url=USER_AGREEMENT_URL)],
        [InlineKeyboardButton(text="🔒 Политика конфиденциальности", url=PRIVACY_POLICY_URL)],
    ])


def balance_text(amount: int) -> str:
    return f"💰 Ваш баланс: <b>{format_tokens(amount)}</b> токенов"


def package_keyboard(
    price_per_million: Decimal = DEFAULT_TOKEN_PRICE_PER_MILLION,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=(
                f"💎 {format_tokens(tokens)} токенов · "
                f"{format_rubles(tokens_to_rubles(tokens, price_per_million))} ₽"
            ),
            callback_data=f"buy:{package_id}",
        )]
        for package_id, tokens in PACKAGES.items()
    ]
    rows.append([InlineKeyboardButton(text="✍️ Ввести своё количество", callback_data="buy:custom")])
    rows.append([InlineKeyboardButton(text="💰 Проверить баланс", callback_data="show:balance")])
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="show:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_method_keyboard(
    crypto_available: bool = True,
    platega_available: bool = True,
) -> InlineKeyboardMarkup:
    rows = []
    if crypto_available:
        rows.append([InlineKeyboardButton(text="💎 Crypto Bot · автоматически", callback_data="method:crypto")])
    if platega_available:
        rows.append([InlineKeyboardButton(text="🏦 СБП Платега · автоматически", callback_data="method:platega")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к пакетам", callback_data="show:packages")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
            InlineKeyboardButton(text="💵 Цена токенов", callback_data="admin:price"),
        ],
        [InlineKeyboardButton(text="🌐 Пользователи сайта", callback_data="admin:site_users")],
        [InlineKeyboardButton(text="🤖 Пользователи бота", callback_data="admin:bot_users")],
        [
            InlineKeyboardButton(text="🧾 Платежи СБП Платега", callback_data="admin:payments"),
            InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast"),
        ],
    ])


def is_admin(telegram_user_id: int, admin_id: int) -> bool:
    return telegram_user_id == admin_id


def admin_site_users_keyboard(rows) -> InlineKeyboardMarkup:
    buttons = []
    for user, _link, _bot_user in rows:
        name = " ".join((user.name or "Без имени").split())[:28]
        buttons.append([InlineKeyboardButton(
            text=f"🌐 {name} · #{user.id}",
            callback_data=f"admin:site_user:{user.id}",
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def bot_user_title(profile) -> str:
    if profile.username:
        return f"@{profile.username}"
    full_name = " ".join(filter(None, [profile.first_name, profile.last_name])).strip()
    return full_name or str(profile.telegram_user_id)


def admin_bot_users_keyboard(rows) -> InlineKeyboardMarkup:
    buttons = []
    for profile, _link, _site_user in rows:
        buttons.append([InlineKeyboardButton(
            text=f"🤖 {bot_user_title(profile)[:32]}",
            callback_data=f"admin:bot_user:{profile.telegram_user_id}",
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_user_detail_keyboard(back_callback: str, username: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if username and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        rows.append([InlineKeyboardButton(text=f"↗️ Открыть @{username}", url=f"https://t.me/{username}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def save_bot_profile(session_factory, profile) -> None:
    with session_factory() as session:
        upsert_bot_user(
            session,
            profile.id,
            profile.username,
            profile.first_name,
            profile.last_name,
        )


def initialize_bot_records(session_factory) -> None:
    with session_factory() as session:
        backfill_bound_bot_users(session)


async def refresh_missing_bot_profiles(bot: Bot, session_factory, profiles) -> None:
    semaphore = asyncio.Semaphore(5)

    async def refresh(profile) -> None:
        if profile.first_name is not None:
            return
        try:
            async with semaphore:
                telegram_profile = await bot.get_chat(profile.telegram_user_id)
            await asyncio.to_thread(save_bot_profile, session_factory, telegram_profile)
        except Exception:
            logger.info(
                "Could not refresh Telegram profile for user_id=%s",
                profile.telegram_user_id,
            )

    await asyncio.gather(*(refresh(profile) for profile in profiles))


class BotUserTrackingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        profile = data.get("event_from_user")
        session_factory = data.get("session_factory")
        if profile is not None and session_factory is not None:
            try:
                await asyncio.to_thread(save_bot_profile, session_factory, profile)
            except SQLAlchemyError:
                logger.exception("Could not save Telegram profile for user_id=%s", profile.id)
        return await handler(event, data)


def payment_keyboard(payment_url: str, payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить в Crypto Bot", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check:{payment_id}")],
        [InlineKeyboardButton(text="⬅️ Другой пакет", callback_data="show:packages")],
    ])


def platega_payment_keyboard(payment_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏦 Оплатить через СБП Платега", url=payment_url)],
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


def platega_error_message(error: PlategaError) -> str:
    if error.status in {401, 403}:
        hint = "Проверьте PLATEGA_MERCHANT_ID и PLATEGA_API_KEY."
    elif error.code == "NETWORK_ERROR":
        hint = "Platega временно не отвечает. Попробуйте ещё раз через минуту."
    else:
        hint = "Платёжная система отклонила создание счёта. Попробуйте позже."
    return f"❌ <b>Не удалось создать счёт</b>\n{hint}\n\nКод: <code>{html.escape(error.code)}</code>"


def read_balance(session_factory, telegram_user_id: int):
    with session_factory() as session:
        link = get_bound_link(session, telegram_user_id)
        user = session.get(User, link.user_id) if link else None
        return user.token_balance if user else None


def read_token_price(session_factory) -> Decimal:
    with session_factory() as session:
        return get_token_price(session)


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
            await message.answer(
                "🔒 Эта ссылка уже привязана к другому Telegram‑аккаунту.",
                reply_markup=main_menu_keyboard(),
            )
            return
        if result in {"invalid", "missing"} or link is None:
            await message.answer(
                "🔗 Откройте личный кабинет Emerald AI и нажмите «Купить токены», "
                "чтобы получить персональную ссылку.\n\n"
                "Вы также можете обратиться в поддержку или ознакомиться с документами:",
                reply_markup=main_menu_keyboard(),
            )
            return
        user = session.get(User, link.user_id)
        balance = user.token_balance if user else 0
        token_price = get_token_price(session)
    await message.answer(
        "💚 <b>Emerald AI</b>\n\n"
        f"💎 Курс: <b>1 000 000 токенов = {format_rubles(token_price)} ₽</b>\n"
        f"{balance_text(balance)}\n\n"
        "Выберите действие:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("balance"))
async def balance(message: Message, session_factory):
    amount = read_balance(session_factory, message.from_user.id)
    if amount is None:
        await message.answer(
            "🔗 Сначала откройте бота по ссылке из кабинета Emerald AI.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await message.answer(balance_text(amount), reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "show:balance")
async def show_balance(callback: CallbackQuery, session_factory):
    await callback.answer()
    amount = read_balance(session_factory, callback.from_user.id)
    if callback.message:
        text = "🔗 Сначала откройте покупку с сайта."
        if amount is not None:
            text = balance_text(amount)
        await callback.message.answer(text, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "show:menu")
async def show_main_menu(callback: CallbackQuery, state: FSMContext, session_factory):
    await callback.answer()
    await state.clear()
    amount = read_balance(session_factory, callback.from_user.id)
    if callback.message:
        text = "💚 <b>Emerald AI</b>\n\nВыберите действие:"
        if amount is not None:
            text = f"💚 <b>Emerald AI</b>\n\n{balance_text(amount)}\n\nВыберите действие:"
        await callback.message.answer(text, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "show:packages")
async def show_packages(callback: CallbackQuery, state: FSMContext, session_factory):
    await state.clear()
    with session_factory() as session:
        link = get_bound_link(session, callback.from_user.id)
        token_price = get_token_price(session)
    if link is None:
        await callback.answer(
            "Сначала откройте покупку по персональной ссылке из кабинета Emerald AI.",
            show_alert=True,
        )
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"💎 Выберите пакет:\n\nКурс: <b>1 000 000 = {format_rubles(token_price)} ₽</b>",
            reply_markup=package_keyboard(token_price),
        )


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
async def cancel_custom_amount(
    message: Message,
    state: FSMContext,
    session_factory,
    admin_id: int,
):
    current_state = await state.get_state()
    await state.clear()
    if is_admin(message.from_user.id, admin_id) and current_state and current_state.startswith("AdminState:"):
        await message.answer("↩️ Действие отменено.", reply_markup=admin_keyboard())
        return
    token_price = read_token_price(session_factory)
    await message.answer("↩️ Ввод отменён.", reply_markup=package_keyboard(token_price))


async def issue_invoice(
    message: Message,
    telegram_user_id: int,
    token_amount: int,
    rub_amount: Decimal,
    session_factory,
    crypto: CryptoPayClient | None,
):
    if crypto is None:
        await message.answer("⚠️ Crypto Bot сейчас недоступен. Выберите СБП Платега.")
        return
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


async def issue_platega_invoice(
    message: Message,
    telegram_user_id: int,
    token_amount: int,
    rub_amount: Decimal,
    session_factory,
    platega: PlategaClient | None,
):
    if platega is None:
        await message.answer("⚠️ СБП Платега сейчас недоступна. Выберите другой способ.")
        return
    with session_factory() as session:
        link = get_bound_link(session, telegram_user_id)
        user = session.get(User, link.user_id) if link else None
    if link is None:
        await message.answer("🔗 Ссылка не привязана. Откройте покупку с сайта.")
        return

    payload = f"em_platega_{secrets.token_urlsafe(18)}"
    try:
        transaction = await platega.create_payment_link(
            rub_amount,
            token_amount,
            payload,
            link.user_id,
            user.name if user else None,
        )
        payment_url = str(transaction.get("url") or transaction.get("redirect") or "")
        with session_factory() as session:
            current_link = get_bound_link(session, telegram_user_id)
            if current_link is None:
                raise RuntimeError("Purchase link was revoked")
            payment = create_platega_payment(
                session,
                current_link,
                telegram_user_id,
                rub_amount,
                token_amount,
                payload,
                transaction,
                PLATEGA_INVOICE_TTL_MINUTES,
            )
    except PlategaError as error:
        logger.warning("Platega create payment failed: %s", error)
        await message.answer(platega_error_message(error))
        return
    except (RuntimeError, KeyError, ValueError, SQLAlchemyError):
        logger.exception("Could not store Platega transaction")
        await message.answer("❌ Счёт создан некорректно. Попробуйте ещё раз через минуту.")
        return

    provider_ttl = str(transaction.get("expiresIn") or "")
    if provider_ttl and provider_ttl not in {"01:00:00", "60:00"}:
        logger.warning(
            "Platega transaction %s returned expiresIn=%s; configure a 60-minute lifetime in the merchant account",
            payment.transaction_id,
            provider_ttl,
        )
    await message.answer(
        f"🏦 <b>Счёт СБП Платега на {format_rubles(rub_amount)} ₽</b>\n"
        f"💎 Будет начислено: <b>{format_tokens(payment.token_amount)}</b> токенов\n"
        f"⏱ Срок действия: <b>{PLATEGA_INVOICE_TTL_MINUTES} минут</b>\n\n"
        "На странице оплаты выберите удобный способ. После успешной оплаты "
        "токены зачислятся автоматически — отправлять чек и нажимать кнопку проверки не нужно.",
        reply_markup=platega_payment_keyboard(payment_url),
    )


async def offer_payment_methods(
    message: Message,
    state: FSMContext,
    token_amount: int,
    rub_amount: Decimal,
    crypto: CryptoPayClient | None,
    platega: PlategaClient | None,
):
    await state.set_state(PurchaseState.choosing_payment_method)
    await state.update_data(token_amount=token_amount, rub_amount=str(rub_amount))
    await message.answer(
        f"💎 <b>{format_tokens(token_amount)}</b> токенов · <b>{format_rubles(rub_amount)} ₽</b>\n\n"
        "Выберите способ оплаты:",
        reply_markup=payment_method_keyboard(crypto is not None, platega is not None),
    )


@router.callback_query(F.data.startswith("buy:"))
async def create_package_invoice(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory,
    crypto: CryptoPayClient | None,
    platega: PlategaClient | None,
):
    if callback.data == "buy:custom":
        return
    try:
        package_id = int(callback.data.split(":", 1)[1])
        token_amount = PACKAGES[package_id]
    except (ValueError, IndexError, KeyError, AttributeError):
        await callback.answer("❌ Некорректный пакет", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        token_price = read_token_price(session_factory)
        await offer_payment_methods(
            callback.message,
            state,
            token_amount,
            tokens_to_rubles(token_amount, token_price),
            crypto,
            platega,
        )


@router.message(PurchaseState.waiting_for_token_amount)
async def create_custom_invoice(
    message: Message,
    state: FSMContext,
    session_factory,
    crypto: CryptoPayClient | None,
    platega: PlategaClient | None,
):
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
    token_price = read_token_price(session_factory)
    rub_amount = tokens_to_rubles(token_amount, token_price)
    await message.answer(
        f"🧮 Рассчитано: <b>{format_tokens(token_amount)}</b> токенов = <b>{format_rubles(rub_amount)} ₽</b>"
    )
    await offer_payment_methods(message, state, token_amount, rub_amount, crypto, platega)


@router.callback_query(PurchaseState.choosing_payment_method, F.data.startswith("method:"))
async def choose_payment_method(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory,
    crypto: CryptoPayClient | None,
    platega: PlategaClient | None,
):
    data = await state.get_data()
    try:
        token_amount = int(data["token_amount"])
        rub_amount = Decimal(data["rub_amount"])
    except (KeyError, ValueError):
        await state.clear()
        await callback.answer("Сумма устарела. Выберите пакет заново.", show_alert=True)
        return
    if callback.message is None:
        return

    method = callback.data.split(":", 1)[1]
    if method == "crypto":
        await callback.answer("⏳ Создаю счёт…")
        await state.clear()
        await issue_invoice(
            callback.message,
            callback.from_user.id,
            token_amount,
            rub_amount,
            session_factory,
            crypto,
        )
        return
    # Keep the old callback as a compatibility alias for payment-choice messages
    # that Telegram users may still have open. Both values create only a Platega
    # checkout; the removed manual SBP flow cannot be reached.
    if method not in {"platega", "sbp"}:
        await callback.answer("Неизвестный способ оплаты", show_alert=True)
        return
    await callback.answer("⏳ Создаю счёт…")
    await state.clear()
    await issue_platega_invoice(
        callback.message,
        callback.from_user.id,
        token_amount,
        rub_amount,
        session_factory,
        platega,
    )


@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext, admin_id: int):
    if not is_admin(message.from_user.id, admin_id):
        return
    await state.clear()
    await message.answer(
        "🛡 <b>Админ-панель Emerald AI</b>\n\n"
        "Управление платежами, пользователями и рассылками.",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery, state: FSMContext, admin_id: int):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🛡 <b>Админ-панель Emerald AI</b>",
            reply_markup=admin_keyboard(),
        )


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery, session_factory, admin_id: int):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    with session_factory() as session:
        stats = admin_statistics(session)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📊 <b>Статистика</b>\n\n"
            f"👥 Пользователей сайта: <b>{format_tokens(stats['users'])}</b>\n"
            f"🤖 Пользователей бота: <b>{format_tokens(stats['bot_users'])}</b>\n"
            f"🔗 Привязано к Telegram: <b>{format_tokens(stats['linked'])}</b>\n"
            f"💎 Crypto Bot: <b>{stats['crypto_paid']}</b> оплат · "
            f"<b>{format_rubles(stats['crypto_rub'])} ₽</b>\n"
            f"🏦 СБП Платега: <b>{stats['platega_paid']}</b> оплат · "
            f"<b>{format_rubles(stats['platega_rub'])} ₽</b>\n"
            f"⏳ Счетов СБП Платега в ожидании: <b>{stats['pending_platega']}</b>",
            reply_markup=admin_keyboard(),
        )


@router.callback_query(F.data.in_({"admin:site_users", "admin:users"}))
async def admin_site_user_list(callback: CallbackQuery, session_factory, admin_id: int):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    with session_factory() as session:
        users = admin_site_users(session)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🌐 <b>Пользователи сайта</b>\n\nНажмите на пользователя, чтобы открыть карточку.",
            reply_markup=admin_site_users_keyboard(users),
        )


@router.callback_query(F.data.startswith("admin:site_user:"))
async def admin_site_user_details(
    callback: CallbackQuery,
    session_factory,
    bot: Bot,
    admin_id: int,
):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        user_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    with session_factory() as session:
        row = admin_site_user(session, user_id)
    if row is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    user, link, profile = row
    if profile is not None and profile.first_name is None:
        await refresh_missing_bot_profiles(bot, session_factory, [profile])
        with session_factory() as session:
            row = admin_site_user(session, user_id)
        user, link, profile = row
    telegram_id = link.telegram_user_id if link else None
    username = profile.username if profile else None
    telegram_name = bot_user_title(profile) if profile else "профиль ещё не сохранён"
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🌐 <b>Пользователь сайта</b>\n\n"
            f"ID: <code>{user.id}</code>\n"
            f"Имя: <b>{html.escape(user.name)}</b>\n"
            f"Email: <code>{html.escape(user.email)}</code>\n"
            f"Баланс: <b>{format_tokens(user.token_balance)}</b> токенов\n"
            f"Telegram ID: {f'<code>{telegram_id}</code>' if telegram_id else 'не привязан'}\n"
            f"Telegram: <b>{html.escape(telegram_name)}</b>",
            reply_markup=admin_user_detail_keyboard("admin:site_users", username),
        )


@router.callback_query(F.data == "admin:bot_users")
async def admin_bot_user_list(
    callback: CallbackQuery,
    session_factory,
    bot: Bot,
    admin_id: int,
):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    with session_factory() as session:
        users = admin_bot_users(session)
    await refresh_missing_bot_profiles(bot, session_factory, [row[0] for row in users])
    with session_factory() as session:
        users = admin_bot_users(session)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🤖 <b>Пользователи бота</b>\n\nНажмите на пользователя, чтобы открыть карточку.",
            reply_markup=admin_bot_users_keyboard(users),
        )


@router.callback_query(F.data.startswith("admin:bot_user:"))
async def admin_bot_user_details(
    callback: CallbackQuery,
    session_factory,
    bot: Bot,
    admin_id: int,
):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        telegram_user_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    with session_factory() as session:
        row = admin_bot_user(session, telegram_user_id)
    if row is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    profile, _link, site_user = row
    if profile.first_name is None:
        await refresh_missing_bot_profiles(bot, session_factory, [profile])
        with session_factory() as session:
            row = admin_bot_user(session, telegram_user_id)
        profile, _link, site_user = row
    username = f"@{profile.username}" if profile.username else "не указан"
    full_name = " ".join(filter(None, [profile.first_name, profile.last_name])) or "не указано"
    site_account = (
        f"<b>{html.escape(site_user.name)}</b> · <code>#{site_user.id}</code>"
        if site_user else "не привязан"
    )
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🤖 <b>Пользователь бота</b>\n\n"
            f"Telegram ID: <code>{profile.telegram_user_id}</code>\n"
            f"Username: <b>{html.escape(username)}</b>\n"
            f"Имя: <b>{html.escape(full_name)}</b>\n"
            f"Аккаунт сайта: {site_account}\n"
            f"Последняя активность: <code>{profile.last_seen_at:%Y-%m-%d %H:%M UTC}</code>",
            reply_markup=admin_user_detail_keyboard("admin:bot_users", profile.username),
        )


@router.callback_query(F.data == "admin:price")
async def admin_price_start(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory,
    admin_id: int,
):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    token_price = read_token_price(session_factory)
    await state.set_state(AdminState.waiting_for_token_price)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "💵 <b>Цена за 1 000 000 токенов</b>\n\n"
            f"Текущая цена: <b>{format_rubles(token_price)} ₽</b>\n"
            "Отправьте новую цену в рублях, например: <code>1.50</code>\n"
            "Минимум — 0,01 ₽. Для отмены отправьте /cancel."
        )


@router.message(AdminState.waiting_for_token_price)
async def admin_price_save(
    message: Message,
    state: FSMContext,
    session_factory,
    admin_id: int,
):
    if not is_admin(message.from_user.id, admin_id):
        await state.clear()
        return
    try:
        with session_factory() as session:
            token_price = set_token_price(session, message.text or "")
    except ValueError as error:
        await message.answer(f"⚠️ {html.escape(str(error))}. Введите цену ещё раз.")
        return
    await state.clear()
    await message.answer(
        "✅ <b>Цена обновлена</b>\n\n"
        f"1 000 000 токенов = <b>{format_rubles(token_price)} ₽</b>\n"
        "Новая цена уже применяется ко всем пакетам и произвольным покупкам.",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin:payments")
async def admin_payment_list(callback: CallbackQuery, session_factory, admin_id: int):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    with session_factory() as session:
        payments = recent_platega_payments(session)
    status_names = {
        "pending": "ожидает оплату",
        "confirmed": "оплачен",
        "canceled": "отменён",
        "chargebacked": "возврат",
        "expired": "истёк",
    }
    lines = ["🧾 <b>Последние платежи СБП Платега</b>", ""]
    for payment in payments:
        lines.append(
            f"• <code>#{payment.id}</code> · {format_rubles(Decimal(payment.rub_amount))} ₽ · "
            f"{status_names.get(payment.status, payment.status)}\n"
            f"  TG <code>{payment.telegram_user_id}</code> · {format_tokens(payment.token_amount)} токенов\n"
            f"  Платега <code>{html.escape(payment.transaction_id)}</code>"
        )
    if not payments:
        lines.append("Платежей пока нет.")
    await callback.answer()
    if callback.message:
        await callback.message.answer("\n".join(lines), reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext, admin_id: int):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminState.waiting_for_broadcast)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📣 <b>Новая рассылка</b>\n\n"
            "Отправьте одно сообщение. Можно использовать текст, фото, видео или файл.\n"
            "Для отмены отправьте /cancel."
        )


@router.message(AdminState.waiting_for_broadcast)
async def admin_broadcast_preview(message: Message, state: FSMContext, admin_id: int):
    if not is_admin(message.from_user.id, admin_id):
        await state.clear()
        return
    await state.set_state(AdminState.confirming_broadcast)
    await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id)
    await message.answer(
        "👀 Сообщение принято. Запустить рассылку всем привязанным пользователям?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚀 Отправить", callback_data="broadcast:confirm"),
            InlineKeyboardButton(text="✖️ Отмена", callback_data="broadcast:cancel"),
        ]]),
    )


@router.callback_query(AdminState.confirming_broadcast, F.data == "broadcast:cancel")
async def admin_broadcast_cancel(callback: CallbackQuery, state: FSMContext, admin_id: int):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer("Рассылка отменена")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(AdminState.confirming_broadcast, F.data == "broadcast:confirm")
async def admin_broadcast_send(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory,
    bot: Bot,
    admin_id: int,
):
    if not is_admin(callback.from_user.id, admin_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    if not source_chat_id or not source_message_id:
        await state.clear()
        await callback.answer("Сообщение устарело", show_alert=True)
        return
    with session_factory() as session:
        recipients = broadcast_recipients(session)
    await callback.answer("Рассылка запущена")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)

    delivered = 0
    failed = 0
    for telegram_id in recipients:
        if telegram_id == admin_id:
            continue
        try:
            await bot.copy_message(telegram_id, source_chat_id, source_message_id)
            delivered += 1
        except Exception:
            failed += 1
            logger.info("Broadcast delivery failed for telegram_user_id=%s", telegram_id)
        await asyncio.sleep(0.04)
    await state.clear()
    if callback.message:
        await callback.message.answer(
            "✅ <b>Рассылка завершена</b>\n\n"
            f"Доставлено: <b>{delivered}</b>\n"
            f"Не доставлено: <b>{failed}</b>",
            reply_markup=admin_keyboard(),
        )


@router.callback_query(F.data.startswith("check:"))
async def check_payment(callback: CallbackQuery, session_factory, crypto: CryptoPayClient | None):
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
    if crypto is None:
        await callback.message.answer("⚠️ Crypto Bot сейчас недоступен. Попробуйте позже или выберите СБП Платега.")
        return
    try:
        invoice = await crypto.get_invoice(invoice_id)
    except CryptoPayError as error:
        logger.warning("Crypto Pay getInvoices failed for %s: %s", invoice_id, error)
        await callback.message.answer(
            f"⚠️ Crypto Bot пока не ответил. Автопроверка продолжает работать.\n"
            f"Код: <code>{html.escape(error.code)}</code>"
        )
        return
    except Exception:
        logger.exception("Unexpected Crypto Pay check failure for invoice %s", invoice_id)
        await callback.message.answer("⚠️ Не удалось проверить сейчас. Автопроверка повторит попытку через 5 секунд.")
        return
    if invoice is None:
        await callback.message.answer("❌ Счёт не найден в Crypto Bot.")
        return
    try:
        with session_factory() as session:
            result, payment = credit_verified_payment(session, payment_id, invoice)
            user = session.get(User, payment.user_id) if payment else None
            current_balance = user.token_balance if user else 0
    except SQLAlchemyError:
        logger.exception("Database failure while crediting payment %s", payment_id)
        await callback.message.answer("⚠️ База временно недоступна. Автопроверка повторит начисление.")
        return
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


def load_pending_payments(session_factory):
    with session_factory() as session:
        return get_pending_payments(session)


def credit_automatic_payment(session_factory, payment_id: int, invoice: dict):
    with session_factory() as session:
        result, payment = credit_verified_payment(session, payment_id, invoice)
        user = session.get(User, payment.user_id) if payment else None
        balance = user.token_balance if user else 0
        return result, payment, balance


async def reconcile_pending_payments(bot: Bot, session_factory, crypto: CryptoPayClient) -> None:
    """Verify pending invoices in batches and credit them without a button click."""
    pending = await asyncio.to_thread(load_pending_payments, session_factory)
    if not pending:
        return
    invoice_by_id = {
        int(invoice.get("invoice_id", 0)): invoice
        for invoice in await crypto.get_invoices([payment.invoice_id for payment in pending])
    }
    for pending_payment in pending:
        invoice = invoice_by_id.get(pending_payment.invoice_id)
        if invoice is None or invoice.get("status") != "paid":
            continue
        result, payment, balance = await asyncio.to_thread(
            credit_automatic_payment,
            session_factory,
            pending_payment.id,
            invoice,
        )
        if result != "credited" or payment is None:
            continue
        logger.info("Automatically credited payment_id=%s invoice_id=%s", payment.id, payment.invoice_id)
        try:
            await bot.send_message(
                payment.telegram_user_id,
                "✅ <b>Оплата подтверждена автоматически!</b>\n"
                f"💎 Начислено: <b>{format_tokens(payment.token_amount)}</b> токенов\n"
                f"💰 Новый баланс: <b>{format_tokens(balance)}</b>",
            )
        except Exception:
            # The balance is already committed; a Telegram delivery failure must not roll it back.
            logger.exception("Could not notify Telegram user for payment %s", payment.id)


def load_pending_platega_payments(session_factory):
    with session_factory() as session:
        return get_pending_platega_payments(session)


def credit_automatic_platega_payment(session_factory, payment_id: int, transaction: dict):
    with session_factory() as session:
        result, payment = credit_verified_platega_payment(session, payment_id, transaction)
        user = session.get(User, payment.user_id) if payment else None
        balance = user.token_balance if user else 0
        return result, payment, balance


async def reconcile_pending_platega_payments(
    bot: Bot,
    session_factory,
    platega: PlategaClient,
) -> None:
    pending = await asyncio.to_thread(load_pending_platega_payments, session_factory)
    for pending_payment in pending:
        try:
            transaction = await platega.get_transaction(pending_payment.transaction_id)
        except PlategaError as error:
            logger.warning(
                "Platega status check failed for %s: %s",
                pending_payment.transaction_id,
                error,
            )
            continue
        result, payment, balance = await asyncio.to_thread(
            credit_automatic_platega_payment,
            session_factory,
            pending_payment.id,
            transaction,
        )
        if result != "credited" or payment is None:
            continue
        logger.info(
            "Automatically credited Platega payment_id=%s transaction_id=%s",
            payment.id,
            payment.transaction_id,
        )
        try:
            await bot.send_message(
                payment.telegram_user_id,
                "✅ <b>Оплата подтверждена автоматически!</b>\n"
                f"💎 Начислено: <b>{format_tokens(payment.token_amount)}</b> токенов\n"
                f"💰 Новый баланс: <b>{format_tokens(balance)}</b>",
            )
        except Exception:
            logger.exception("Could not notify Telegram user for Platega payment %s", payment.id)


async def payment_reconciliation_loop(
    bot: Bot,
    session_factory,
    crypto: CryptoPayClient | None,
    platega: PlategaClient | None,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        if crypto is not None:
            try:
                await reconcile_pending_payments(bot, session_factory, crypto)
            except CryptoPayError as error:
                logger.warning("Automatic Crypto Pay check failed: %s", error)
            except SQLAlchemyError:
                logger.exception("Automatic Crypto Pay database check failed")
            except Exception:
                logger.exception("Unexpected automatic Crypto Pay check failure")
        if platega is not None:
            try:
                await reconcile_pending_platega_payments(bot, session_factory, platega)
            except SQLAlchemyError:
                logger.exception("Automatic Platega database check failed")
            except Exception:
                logger.exception("Unexpected automatic Platega check failure")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


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
    platega_merchant_id = os.getenv("PLATEGA_MERCHANT_ID", "").strip()
    platega_api_key = os.getenv("PLATEGA_API_KEY", "").strip()
    platega_return_url = os.getenv("PLATEGA_RETURN_URL", "https://t.me/emeraldairobot").strip()
    platega_failed_url = os.getenv("PLATEGA_FAILED_URL", "https://t.me/emeraldairobot").strip()
    try:
        admin_id = int(os.getenv("ADMIN_ID", str(DEFAULT_ADMIN_ID)).strip())
    except ValueError as error:
        raise RuntimeError("ADMIN_ID must be a Telegram numeric user ID") from error
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")
    if crypto_token and bot_token == crypto_token:
        raise RuntimeError("CRYPTOBOT_TOKEN must be a Crypto Pay app token, not BOT_TOKEN")
    if bool(platega_merchant_id) != bool(platega_api_key):
        raise RuntimeError("PLATEGA_MERCHANT_ID and PLATEGA_API_KEY must be configured together")

    engine, session_factory = database_from_environment()
    await asyncio.to_thread(Base.metadata.create_all, engine)
    await asyncio.to_thread(initialize_bot_records, session_factory)
    crypto = CryptoPayClient(crypto_token) if crypto_token else None
    platega = (
        PlategaClient(
            platega_merchant_id,
            platega_api_key,
            platega_return_url,
            platega_failed_url,
        )
        if platega_merchant_id
        else None
    )
    if crypto is not None:
        try:
            crypto_app = await crypto.get_me()
        except CryptoPayError as error:
            await crypto.close()
            engine.dispose()
            raise RuntimeError(
                f"Crypto Pay authentication failed: {error}. Check CRYPTOBOT_TOKEN and IP allowlist."
            ) from error
        logger.info("Crypto Pay authenticated: app_id=%s name=%s", crypto_app.get("app_id"), crypto_app.get("name"))
    else:
        logger.warning("CRYPTOBOT_TOKEN is not configured; Crypto Bot payments are unavailable")
    if platega is None:
        logger.warning("Platega credentials are not configured; Platega payments are unavailable")

    bot = Bot(bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(BotUserTrackingMiddleware())
    dispatcher.include_router(router)
    dispatcher.errors.register(handle_error)
    stop_reconciliation = asyncio.Event()
    reconciliation_task = None
    if crypto is not None or platega is not None:
        reconciliation_task = asyncio.create_task(
            payment_reconciliation_loop(bot, session_factory, crypto, platega, stop_reconciliation),
            name="payment-reconciliation",
        )
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(
            bot,
            session_factory=session_factory,
            crypto=crypto,
            platega=platega,
            admin_id=admin_id,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        stop_reconciliation.set()
        if reconciliation_task is not None:
            await reconciliation_task
        if crypto is not None:
            await crypto.close()
        if platega is not None:
            await platega.close()
        await bot.session.close()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

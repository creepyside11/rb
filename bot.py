import asyncio
import logging
import os
import re
import secrets

from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from cryptopay import CryptoPayClient, CryptoPayError, invoice_payment_url
from database import Base, User, database_from_environment
from payments import (
    PACKAGES,
    bind_purchase_link,
    credit_verified_payment,
    get_bound_link,
    save_pending_payment,
)


logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logger = logging.getLogger("emerald-payment-bot")
LINK_PATTERN = re.compile(r"^buy_([A-Za-z0-9_-]{30,48})$")


def format_tokens(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def package_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"{format_tokens(tokens)} · {rub} ₽", callback_data=f"buy:{rub}")
        for rub, tokens in PACKAGES.items()
    ]
    return InlineKeyboardMarkup([[button] for button in buttons])


def get_services(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["Session"], context.application.bot_data["crypto"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_message is None:
        return
    Session, _ = get_services(context)
    argument = context.args[0] if context.args else ""
    match = LINK_PATTERN.fullmatch(argument)
    with Session() as session:
        if match:
            result, link = bind_purchase_link(session, match.group(1), update.effective_user.id)
        else:
            link = get_bound_link(session, update.effective_user.id)
            result = "ok" if link else "missing"
        if result == "claimed":
            await update.effective_message.reply_text("Эта ссылка уже привязана к другому Telegram-аккаунту.")
            return
        if result in {"invalid", "missing"} or link is None:
            await update.effective_message.reply_text(
                "Откройте личный кабинет Emerald AI и нажмите «Купить токены», чтобы получить персональную ссылку."
            )
            return
        user = session.get(User, link.user_id)
        balance = user.token_balance if user else 0
    await update.effective_message.reply_text(
        "Пополнение Emerald AI\n\n"
        "Курс: 1 000 000 токенов = 1 ₽\n"
        f"Ваш баланс: {format_tokens(balance)} токенов\n\n"
        "Выберите пакет:",
        reply_markup=package_keyboard(),
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_message is None:
        return
    Session, _ = get_services(context)
    with Session() as session:
        link = get_bound_link(session, update.effective_user.id)
        user = session.get(User, link.user_id) if link else None
        amount = user.token_balance if user else None
    if amount is None:
        await update.effective_message.reply_text("Сначала откройте бота по ссылке из кабинета Emerald AI.")
    else:
        await update.effective_message.reply_text(f"Баланс: {format_tokens(amount)} токенов")


async def create_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.from_user is None:
        return
    try:
        rub_amount = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        await query.answer("Некорректный пакет", show_alert=True)
        return
    if rub_amount not in PACKAGES:
        await query.answer("Такого пакета нет", show_alert=True)
        return

    Session, crypto = get_services(context)
    with Session() as session:
        link = get_bound_link(session, query.from_user.id)
    if link is None:
        await query.answer("Ссылка не привязана. Откройте покупку с сайта.", show_alert=True)
        return
    await query.answer("Создаю счёт…")
    payload = f"em_{secrets.token_urlsafe(20)}"
    try:
        invoice = await asyncio.to_thread(crypto.create_rub_invoice, rub_amount, payload)
        payment_url = invoice_payment_url(invoice)
        if not payment_url:
            raise CryptoPayError("Crypto Pay did not return a payment URL")
        with Session() as session:
            current_link = get_bound_link(session, query.from_user.id)
            if current_link is None:
                raise RuntimeError("Purchase link was revoked")
            payment = save_pending_payment(
                session, current_link, query.from_user.id, rub_amount, payload, invoice
            )
    except (CryptoPayError, RuntimeError, KeyError, ValueError, SQLAlchemyError):
        logger.exception("Could not create invoice")
        await query.message.reply_text("Не удалось создать счёт. Попробуйте ещё раз через минуту.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Оплатить в Crypto Bot", url=payment_url)],
        [InlineKeyboardButton("Проверить оплату", callback_data=f"check:{payment.id}")],
    ])
    await query.message.reply_text(
        f"Счёт на {rub_amount} ₽\nПосле оплаты будет начислено {format_tokens(payment.token_amount)} токенов.",
        reply_markup=keyboard,
    )


async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.from_user is None:
        return
    try:
        payment_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        await query.answer("Некорректный счёт", show_alert=True)
        return
    Session, crypto = get_services(context)
    from database import TokenPayment

    with Session() as session:
        payment = session.get(TokenPayment, payment_id)
        if payment is None or payment.telegram_user_id != query.from_user.id:
            await query.answer("Счёт не найден", show_alert=True)
            return
        invoice_id = payment.invoice_id
    await query.answer("Проверяю оплату…")
    try:
        invoice = await asyncio.to_thread(crypto.get_invoice, invoice_id)
    except CryptoPayError:
        logger.exception("Could not check invoice %s", invoice_id)
        await query.message.reply_text("Crypto Bot пока не отвечает. Попробуйте проверить ещё раз.")
        return
    if invoice is None:
        await query.message.reply_text("Счёт не найден в Crypto Bot.")
        return
    with Session() as session:
        result, payment = credit_verified_payment(session, payment_id, invoice)
        user = session.get(User, payment.user_id) if payment else None
        current_balance = user.token_balance if user else 0
    if result == "credited":
        await query.message.reply_text(
            f"Оплата подтверждена. Начислено {format_tokens(payment.token_amount)} токенов.\n"
            f"Новый баланс: {format_tokens(current_balance)}"
        )
    elif result == "already":
        await query.message.reply_text(f"Этот счёт уже зачислен. Баланс: {format_tokens(current_balance)}")
    elif result == "unpaid":
        await query.message.reply_text("Оплата пока не найдена. Оплатите счёт и проверьте ещё раз.")
    else:
        logger.warning("Invoice verification mismatch for payment %s", payment_id)
        await query.message.reply_text("Данные счёта не совпали. Баланс не изменён; обратитесь в поддержку.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled Telegram update error", exc_info=context.error)


def main():
    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    crypto_token = os.getenv("CRYPTOBOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")
    engine, Session = database_from_environment()
    Base.metadata.create_all(engine)
    crypto = CryptoPayClient(crypto_token)

    application = Application.builder().token(bot_token).build()
    application.bot_data.update({"Session": Session, "crypto": crypto})
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CallbackQueryHandler(create_invoice, pattern=r"^buy:\d+$"))
    application.add_handler(CallbackQueryHandler(check_payment, pattern=r"^check:\d+$"))
    application.add_error_handler(error_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()

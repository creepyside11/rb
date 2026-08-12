from decimal import Decimal, InvalidOperation, ROUND_UP

from sqlalchemy import select

from database import PurchaseLink, TokenPayment, User, utcnow


PACKAGES = {
    1: 1_000_000,
    10: 10_000_000,
    50: 50_000_000,
    100: 100_000_000,
    500: 500_000_000,
}
TOKENS_PER_RUBLE = 1_000_000
MIN_TOKEN_AMOUNT = 1_000_000
MAX_TOKEN_AMOUNT = 1_000_000_000_000


def tokens_to_rubles(token_amount: int) -> Decimal:
    """Round the price up to one kopeck so an invoice never undercharges."""
    return (Decimal(token_amount) / TOKENS_PER_RUBLE).quantize(Decimal("0.01"), rounding=ROUND_UP)


def bind_purchase_link(session, token: str, telegram_user_id: int):
    link = session.execute(
        select(PurchaseLink).where(PurchaseLink.token == token, PurchaseLink.is_active.is_(True)).with_for_update()
    ).scalar_one_or_none()
    if link is None:
        return "invalid", None
    if link.telegram_user_id is not None and link.telegram_user_id != telegram_user_id:
        return "claimed", None
    link.telegram_user_id = telegram_user_id
    link.last_opened_at = utcnow()
    session.commit()
    return "ok", link


def get_bound_link(session, telegram_user_id: int):
    return session.execute(
        select(PurchaseLink).where(
            PurchaseLink.telegram_user_id == telegram_user_id,
            PurchaseLink.is_active.is_(True),
        )
    ).scalar_one_or_none()


def save_pending_payment(
    session,
    link: PurchaseLink,
    telegram_user_id: int,
    rub_amount: Decimal,
    token_amount: int,
    payload: str,
    invoice: dict,
):
    payment = TokenPayment(
        user_id=link.user_id,
        purchase_link_id=link.id,
        telegram_user_id=telegram_user_id,
        invoice_id=int(invoice["invoice_id"]),
        payload=payload,
        rub_amount=Decimal(rub_amount),
        token_amount=token_amount,
        status="pending",
    )
    session.add(payment)
    session.commit()
    return payment


def _same_decimal(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError):
        return False


def credit_verified_payment(session, payment_id: int, invoice: dict):
    payment = session.execute(
        select(TokenPayment).where(TokenPayment.id == payment_id).with_for_update()
    ).scalar_one_or_none()
    if payment is None:
        return "invalid", None
    if payment.status == "paid":
        return "already", payment

    valid = (
        str(invoice.get("invoice_id")) == str(payment.invoice_id)
        and invoice.get("payload") == payment.payload
        and invoice.get("status") == "paid"
        and (not invoice.get("currency_type") or invoice.get("currency_type") == "fiat")
        and (not invoice.get("fiat") or invoice.get("fiat") == "RUB")
        and _same_decimal(invoice.get("amount"), payment.rub_amount)
    )
    if not valid:
        return "unpaid" if invoice.get("status") != "paid" else "invalid", payment

    user = session.execute(select(User).where(User.id == payment.user_id).with_for_update()).scalar_one_or_none()
    if user is None:
        return "invalid", payment
    user.token_balance += payment.token_amount
    payment.status = "paid"
    payment.paid_asset = str(invoice.get("paid_asset") or "")[:16] or None
    payment.paid_amount = str(invoice.get("paid_amount") or "")[:64] or None
    payment.paid_at = utcnow()
    session.commit()
    return "credited", payment

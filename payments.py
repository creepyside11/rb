from decimal import Decimal, InvalidOperation, ROUND_UP

from sqlalchemy import func, select

from database import ManualPayment, PurchaseLink, TokenPayment, User, utcnow


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


def get_pending_payments(session, limit: int = 100):
    return list(session.execute(
        select(TokenPayment)
        .where(TokenPayment.status == "pending")
        .order_by(TokenPayment.created_at.desc())
        .limit(limit)
    ).scalars())


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


def create_manual_payment(
    session,
    link: PurchaseLink,
    telegram_user_id: int,
    rub_amount: Decimal,
    token_amount: int,
):
    payment = ManualPayment(
        user_id=link.user_id,
        purchase_link_id=link.id,
        telegram_user_id=telegram_user_id,
        rub_amount=Decimal(rub_amount),
        token_amount=token_amount,
        status="awaiting_receipt",
    )
    session.add(payment)
    session.commit()
    return payment


def submit_manual_receipt(session, payment_id: int, telegram_user_id: int, file_id: str, receipt_type: str):
    payment = session.execute(
        select(ManualPayment).where(ManualPayment.id == payment_id).with_for_update()
    ).scalar_one_or_none()
    if payment is None or payment.telegram_user_id != telegram_user_id:
        return "invalid", None
    if payment.status != "awaiting_receipt":
        return "already", payment
    payment.receipt_file_id = file_id
    payment.receipt_type = receipt_type
    payment.status = "pending_review"
    payment.submitted_at = utcnow()
    session.commit()
    return "submitted", payment


def attach_admin_message(session, payment_id: int, admin_message_id: int):
    payment = session.get(ManualPayment, payment_id)
    if payment is None:
        return None
    payment.admin_message_id = admin_message_id
    session.commit()
    return payment


def review_manual_payment(session, payment_id: int, admin_id: int, approve: bool):
    """Review once under row locks; approved payments credit the balance exactly once."""
    payment = session.execute(
        select(ManualPayment).where(ManualPayment.id == payment_id).with_for_update()
    ).scalar_one_or_none()
    if payment is None:
        return "invalid", None, None
    if payment.status == "approved":
        user = session.get(User, payment.user_id)
        return "already_approved", payment, user.token_balance if user else None
    if payment.status == "rejected":
        return "already_rejected", payment, None
    if payment.status != "pending_review":
        return "not_ready", payment, None

    payment.reviewed_by = admin_id
    payment.reviewed_at = utcnow()
    if not approve:
        payment.status = "rejected"
        session.commit()
        return "rejected", payment, None

    user = session.execute(select(User).where(User.id == payment.user_id).with_for_update()).scalar_one_or_none()
    if user is None:
        return "invalid", payment, None
    user.token_balance += payment.token_amount
    payment.status = "approved"
    session.commit()
    return "approved", payment, user.token_balance


def admin_statistics(session):
    users = session.scalar(select(func.count(User.id))) or 0
    linked = session.scalar(
        select(func.count(PurchaseLink.id)).where(PurchaseLink.telegram_user_id.is_not(None))
    ) or 0
    crypto_paid = session.scalar(
        select(func.count(TokenPayment.id)).where(TokenPayment.status == "paid")
    ) or 0
    crypto_rub = session.scalar(
        select(func.coalesce(func.sum(TokenPayment.rub_amount), 0)).where(TokenPayment.status == "paid")
    ) or 0
    sbp_paid = session.scalar(
        select(func.count(ManualPayment.id)).where(ManualPayment.status == "approved")
    ) or 0
    sbp_rub = session.scalar(
        select(func.coalesce(func.sum(ManualPayment.rub_amount), 0)).where(ManualPayment.status == "approved")
    ) or 0
    pending_sbp = session.scalar(
        select(func.count(ManualPayment.id)).where(ManualPayment.status == "pending_review")
    ) or 0
    return {
        "users": int(users),
        "linked": int(linked),
        "crypto_paid": int(crypto_paid),
        "crypto_rub": Decimal(crypto_rub),
        "sbp_paid": int(sbp_paid),
        "sbp_rub": Decimal(sbp_rub),
        "pending_sbp": int(pending_sbp),
    }


def admin_users(session, limit: int = 30):
    return list(session.execute(
        select(User, PurchaseLink.telegram_user_id)
        .outerjoin(PurchaseLink, PurchaseLink.user_id == User.id)
        .order_by(User.id.desc())
        .limit(limit)
    ).all())


def broadcast_recipients(session):
    return list(session.scalars(
        select(PurchaseLink.telegram_user_id)
        .where(PurchaseLink.telegram_user_id.is_not(None), PurchaseLink.is_active.is_(True))
        .distinct()
    ))


def recent_manual_payments(session, limit: int = 20):
    return list(session.scalars(
        select(ManualPayment).order_by(ManualPayment.created_at.desc()).limit(limit)
    ))

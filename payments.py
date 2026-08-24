from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_UP

from sqlalchemy import func, select, update

from database import (
    BotSetting,
    BotUser,
    PlategaPayment,
    PurchaseLink,
    Referral,
    TokenPayment,
    User,
    utcnow,
)


PACKAGES = {
    1: 1_000_000,
    10: 10_000_000,
    50: 50_000_000,
    100: 100_000_000,
    500: 500_000_000,
}
TOKENS_PER_MILLION = 1_000_000
TOKEN_PRICE_SETTING = "token_price_per_million"
DEFAULT_TOKEN_PRICE_PER_MILLION = Decimal("1.00")
MIN_TOKEN_PRICE_PER_MILLION = Decimal("0.01")
MAX_TOKEN_PRICE_PER_MILLION = Decimal("1000000.00")
MIN_TOKEN_AMOUNT = 1_000_000
MAX_TOKEN_AMOUNT = 1_000_000_000_000
REFERRAL_MIN_TOPUP_TOKENS = 10_000_000
REFERRAL_REWARD_TOKENS = 2_000_000


def normalize_token_price(value) -> Decimal:
    try:
        price = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError) as error:
        raise ValueError("Цена должна быть числом") from error
    if not price.is_finite() or not MIN_TOKEN_PRICE_PER_MILLION <= price <= MAX_TOKEN_PRICE_PER_MILLION:
        raise ValueError("Цена должна быть от 0,01 до 1 000 000 ₽")
    return price.quantize(Decimal("0.01"))


def get_token_price(session) -> Decimal:
    setting = session.get(BotSetting, TOKEN_PRICE_SETTING)
    if setting is None:
        return DEFAULT_TOKEN_PRICE_PER_MILLION
    try:
        return normalize_token_price(setting.value)
    except ValueError:
        return DEFAULT_TOKEN_PRICE_PER_MILLION


def set_token_price(session, value) -> Decimal:
    price = normalize_token_price(value)
    setting = session.get(BotSetting, TOKEN_PRICE_SETTING)
    if setting is None:
        setting = BotSetting(key=TOKEN_PRICE_SETTING, value=format(price, "f"))
        session.add(setting)
    else:
        setting.value = format(price, "f")
        setting.updated_at = utcnow()
    session.commit()
    return price


def tokens_to_rubles(
    token_amount: int,
    price_per_million: Decimal = DEFAULT_TOKEN_PRICE_PER_MILLION,
) -> Decimal:
    """Round the price up to one kopeck so an invoice never undercharges."""
    price = normalize_token_price(price_per_million)
    return (
        Decimal(token_amount) / TOKENS_PER_MILLION * price
    ).quantize(Decimal("0.01"), rounding=ROUND_UP)


def upsert_bot_user(
    session,
    telegram_user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
):
    profile = session.get(BotUser, telegram_user_id)
    now = utcnow()
    if profile is None:
        profile = BotUser(telegram_user_id=telegram_user_id, first_seen_at=now)
        session.add(profile)
    profile.username = (username or "").lstrip("@")[:64] or None
    profile.first_name = (first_name or "")[:128] or None
    profile.last_name = (last_name or "")[:128] or None
    profile.last_seen_at = now
    session.commit()
    return profile


def backfill_bound_bot_users(session) -> int:
    telegram_ids = session.scalars(
        select(PurchaseLink.telegram_user_id)
        .where(PurchaseLink.telegram_user_id.is_not(None))
        .distinct()
    ).all()
    existing_ids = set(session.scalars(
        select(BotUser.telegram_user_id)
        .where(BotUser.telegram_user_id.in_(telegram_ids))
    )) if telegram_ids else set()
    now = utcnow()
    missing_ids = [telegram_id for telegram_id in telegram_ids if telegram_id not in existing_ids]
    session.add_all([
        BotUser(
            telegram_user_id=telegram_id,
            first_seen_at=now,
            last_seen_at=now,
        )
        for telegram_id in missing_ids
    ])
    if missing_ids:
        session.commit()
    return len(missing_ids)


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


def apply_first_topup_referral_reward(
    session,
    referred_user_id: int,
    topup_tokens: int,
    payment_source: str,
    payment_id: int,
):
    """Resolve a pending referral once, inside the payment transaction."""
    referral = session.execute(
        select(Referral)
        .where(Referral.referred_user_id == referred_user_id)
        .with_for_update()
    ).scalar_one_or_none()
    if referral is None or referral.status != "pending":
        return False

    referral.first_topup_tokens = int(topup_tokens)
    referral.payment_source = payment_source[:16]
    referral.payment_id = payment_id
    if topup_tokens < REFERRAL_MIN_TOPUP_TOKENS:
        referral.status = "ineligible_minimum"
        return False

    referrer = session.execute(
        select(User).where(User.id == referral.referrer_id).with_for_update()
    ).scalar_one_or_none()
    if referrer is None or referrer.id == referred_user_id:
        referral.status = "blocked_self"
        return False

    reward = int(referral.reward_tokens or REFERRAL_REWARD_TOKENS)
    referrer.token_balance += reward
    referral.status = "rewarded"
    referral.qualified_at = utcnow()
    referral.rewarded_at = referral.qualified_at
    return True


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
    apply_first_topup_referral_reward(
        session, user.id, payment.token_amount, "crypto", payment.id
    )
    session.commit()
    return "credited", payment


def get_pending_platega_payments(session, limit: int = 100):
    now = utcnow()
    session.execute(
        update(PlategaPayment)
        .where(PlategaPayment.status == "pending", PlategaPayment.expires_at <= now)
        .values(status="expired", last_checked_at=now)
    )
    session.commit()
    return list(session.scalars(
        select(PlategaPayment)
        .where(PlategaPayment.status == "pending", PlategaPayment.expires_at > now)
        .order_by(PlategaPayment.created_at.asc())
        .limit(limit)
    ))


def create_platega_payment(
    session,
    link: PurchaseLink,
    telegram_user_id: int,
    rub_amount: Decimal,
    token_amount: int,
    payload: str,
    transaction: dict,
    ttl_minutes: int = 60,
):
    payment = PlategaPayment(
        user_id=link.user_id,
        purchase_link_id=link.id,
        telegram_user_id=telegram_user_id,
        transaction_id=str(transaction["transactionId"]),
        payload=payload,
        rub_amount=Decimal(rub_amount),
        token_amount=token_amount,
        status="pending",
        provider_expires_in=str(transaction.get("expiresIn") or "")[:24] or None,
        expires_at=utcnow() + timedelta(minutes=ttl_minutes),
    )
    session.add(payment)
    session.commit()
    return payment


def credit_verified_platega_payment(session, payment_id: int, transaction: dict):
    payment = session.execute(
        select(PlategaPayment).where(PlategaPayment.id == payment_id).with_for_update()
    ).scalar_one_or_none()
    if payment is None:
        return "invalid", None
    if payment.status == "confirmed":
        return "already", payment

    transaction_id = transaction.get("id") or transaction.get("transactionId")
    status = str(transaction.get("status") or "").upper()
    details = transaction.get("paymentDetails") or {}
    valid_identity = (
        str(transaction_id) == payment.transaction_id
        and transaction.get("payload") == payment.payload
    )
    payment.last_checked_at = utcnow()
    payment.payment_method = str(transaction.get("paymentMethod") or "")[:32] or None
    if not valid_identity:
        session.rollback()
        return "invalid", payment
    if status != "CONFIRMED":
        if status in {"CANCELED", "CHARGEBACKED"}:
            payment.status = status.lower()
            session.commit()
            return payment.status, payment
        session.commit()
        return "pending", payment

    valid_amount = (
        str(details.get("currency") or "").upper() == "RUB"
        and _same_decimal(details.get("amount"), payment.rub_amount)
    )
    if not valid_amount:
        session.rollback()
        return "invalid", payment

    user = session.execute(
        select(User).where(User.id == payment.user_id).with_for_update()
    ).scalar_one_or_none()
    if user is None:
        session.rollback()
        return "invalid", payment
    user.token_balance += payment.token_amount
    payment.status = "confirmed"
    payment.paid_at = utcnow()
    apply_first_topup_referral_reward(
        session, user.id, payment.token_amount, "platega", payment.id
    )
    session.commit()
    return "credited", payment


def admin_statistics(session):
    users = session.scalar(select(func.count(User.id))) or 0
    bot_users = session.scalar(select(func.count(BotUser.telegram_user_id))) or 0
    linked = session.scalar(
        select(func.count(PurchaseLink.id)).where(PurchaseLink.telegram_user_id.is_not(None))
    ) or 0
    crypto_paid = session.scalar(
        select(func.count(TokenPayment.id)).where(TokenPayment.status == "paid")
    ) or 0
    crypto_rub = session.scalar(
        select(func.coalesce(func.sum(TokenPayment.rub_amount), 0)).where(TokenPayment.status == "paid")
    ) or 0
    platega_paid = session.scalar(
        select(func.count(PlategaPayment.id)).where(PlategaPayment.status == "confirmed")
    ) or 0
    platega_rub = session.scalar(
        select(func.coalesce(func.sum(PlategaPayment.rub_amount), 0))
        .where(PlategaPayment.status == "confirmed")
    ) or 0
    pending_platega = session.scalar(
        select(func.count(PlategaPayment.id)).where(PlategaPayment.status == "pending")
    ) or 0
    return {
        "users": int(users),
        "bot_users": int(bot_users),
        "linked": int(linked),
        "crypto_paid": int(crypto_paid),
        "crypto_rub": Decimal(crypto_rub),
        "platega_paid": int(platega_paid),
        "platega_rub": Decimal(platega_rub),
        "pending_platega": int(pending_platega),
    }


def admin_site_users(session, limit: int = 30):
    return list(session.execute(
        select(User, PurchaseLink, BotUser)
        .outerjoin(PurchaseLink, PurchaseLink.user_id == User.id)
        .outerjoin(BotUser, BotUser.telegram_user_id == PurchaseLink.telegram_user_id)
        .order_by(User.id.desc())
        .limit(limit)
    ).all())


def admin_site_user(session, user_id: int):
    return session.execute(
        select(User, PurchaseLink, BotUser)
        .outerjoin(PurchaseLink, PurchaseLink.user_id == User.id)
        .outerjoin(BotUser, BotUser.telegram_user_id == PurchaseLink.telegram_user_id)
        .where(User.id == user_id)
    ).one_or_none()


def admin_bot_users(session, limit: int = 30):
    return list(session.execute(
        select(BotUser, PurchaseLink, User)
        .outerjoin(PurchaseLink, PurchaseLink.telegram_user_id == BotUser.telegram_user_id)
        .outerjoin(User, User.id == PurchaseLink.user_id)
        .order_by(BotUser.last_seen_at.desc())
        .limit(limit)
    ).all())


def admin_bot_user(session, telegram_user_id: int):
    return session.execute(
        select(BotUser, PurchaseLink, User)
        .outerjoin(PurchaseLink, PurchaseLink.telegram_user_id == BotUser.telegram_user_id)
        .outerjoin(User, User.id == PurchaseLink.user_id)
        .where(BotUser.telegram_user_id == telegram_user_id)
    ).one_or_none()


def broadcast_recipients(session):
    return list(session.scalars(
        select(PurchaseLink.telegram_user_id)
        .where(PurchaseLink.telegram_user_id.is_not(None), PurchaseLink.is_active.is_(True))
        .distinct()
    ))


def recent_platega_payments(session, limit: int = 20):
    return list(session.scalars(
        select(PlategaPayment).order_by(PlategaPayment.created_at.desc()).limit(limit)
    ))

import os
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """Minimal mapping of the users table owned by the Emerald web app."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    token_balance: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ApiKey(Base):
    """Shared API-key table mapping used to create the limits table safely."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="Основной ключ")
    prefix: Mapped[str] = mapped_column(String(18), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    encrypted_key: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKeyLimit(Base):
    __tablename__ = "api_key_limits"

    api_key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True
    )
    daily_request_limit: Mapped[int | None] = mapped_column(Integer)
    monthly_token_limit: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PurchaseLink(Base):
    __tablename__ = "purchase_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    token: Mapped[str] = mapped_column(String(48), nullable=False, unique=True, index=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BotUser(Base):
    """Telegram profiles seen by the bot, independent from website accounts."""

    __tablename__ = "telegram_bot_users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class BotSetting(Base):
    """Persistent bot settings editable from the Telegram admin panel."""

    __tablename__ = "telegram_bot_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class TokenPayment(Base):
    __tablename__ = "token_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    purchase_link_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    invoice_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    payload: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    rub_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    token_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    paid_asset: Mapped[str | None] = mapped_column(String(16))
    paid_amount: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlategaPayment(Base):
    """Automatically reconciled Platega payment-link transaction."""

    __tablename__ = "platega_token_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    purchase_link_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    payload: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    rub_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    token_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    payment_method: Mapped[str | None] = mapped_column(String(32))
    provider_expires_in: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferralCode(Base):
    __tablename__ = "referral_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    referred_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    registration_ip: Mapped[str | None] = mapped_column(String(64))
    registration_device_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    reward_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=2_000_000)
    first_topup_tokens: Mapped[int | None] = mapped_column(BigInteger)
    payment_source: Mapped[str | None] = mapped_column(String(16))
    payment_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def build_database(database_url: str):
    engine = create_engine(
        normalize_database_url(database_url),
        pool_pre_ping=True,
        pool_recycle=300,
    )
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def database_from_environment():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return build_database(database_url)

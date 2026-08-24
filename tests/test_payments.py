import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, PlategaPayment, PurchaseLink, Referral, TokenPayment, User, utcnow
from cryptopay import CryptoPayClient, CryptoPayError
from payments import (
    admin_statistics,
    bind_purchase_link,
    create_platega_payment,
    credit_verified_platega_payment,
    credit_verified_payment,
    get_pending_platega_payments,
    tokens_to_rubles,
)


class PaymentTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as session:
            session.add(User(id=1, name="Test", email="test@example.com", token_balance=5_000_000))
            session.add(PurchaseLink(id=1, user_id=1, token="x" * 32, is_active=True))
            session.commit()

    def test_link_binds_to_only_one_telegram_account(self):
        with self.Session() as session:
            result, _ = bind_purchase_link(session, "x" * 32, 101)
        with self.Session() as session:
            stolen, _ = bind_purchase_link(session, "x" * 32, 202)
        self.assertEqual(result, "ok")
        self.assertEqual(stolen, "claimed")

    def test_custom_token_amount_is_converted_at_one_million_per_ruble(self):
        self.assertEqual(tokens_to_rubles(25_000_000), Decimal("25.00"))
        self.assertEqual(tokens_to_rubles(1_000_001), Decimal("1.01"))

    def test_get_invoices_accepts_documented_and_wrapped_responses(self):
        invoice = {"invoice_id": 77, "status": "paid"}
        self.assertEqual(CryptoPayClient._normalize_invoices([invoice]), [invoice])
        self.assertEqual(CryptoPayClient._normalize_invoices({"items": [invoice]}), [invoice])
        self.assertEqual(CryptoPayClient._normalize_invoices(invoice), [invoice])
        with self.assertRaises(CryptoPayError):
            CryptoPayClient._normalize_invoices({"unexpected": []})

    def test_paid_invoice_is_credited_exactly_once(self):
        with self.Session() as session:
            session.add(TokenPayment(
                id=1,
                user_id=1,
                purchase_link_id=1,
                telegram_user_id=101,
                invoice_id=77,
                payload="em-secret",
                rub_amount=Decimal("10.00"),
                token_amount=10_000_000,
                status="pending",
            ))
            session.commit()
        invoice = {
            "invoice_id": 77,
            "payload": "em-secret",
            "status": "paid",
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": "10.00",
            "paid_asset": "USDT",
            "paid_amount": "0.12",
        }
        with self.Session() as session:
            first, _ = credit_verified_payment(session, 1, invoice)
        with self.Session() as session:
            second, _ = credit_verified_payment(session, 1, invoice)
            balance = session.get(User, 1).token_balance
        self.assertEqual(first, "credited")
        self.assertEqual(second, "already")
        self.assertEqual(balance, 15_000_000)

    def test_wrong_payload_never_credits_balance(self):
        with self.Session() as session:
            session.add(TokenPayment(
                id=2,
                user_id=1,
                purchase_link_id=1,
                telegram_user_id=101,
                invoice_id=88,
                payload="expected",
                rub_amount=Decimal("1.00"),
                token_amount=1_000_000,
                status="pending",
            ))
            session.commit()
        with self.Session() as session:
            result, _ = credit_verified_payment(session, 2, {
                "invoice_id": 88,
                "payload": "forged",
                "status": "paid",
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": "1.00",
            })
            balance = session.get(User, 1).token_balance
        self.assertEqual(result, "invalid")
        self.assertEqual(balance, 5_000_000)

    def test_first_qualifying_crypto_topup_rewards_referrer_once(self):
        with self.Session() as session:
            session.add(User(id=2, name="Referrer", email="ref@example.com", token_balance=5_000_000))
            session.add(Referral(
                referrer_id=2,
                referred_user_id=1,
                status="pending",
                reward_tokens=2_000_000,
            ))
            session.add(TokenPayment(
                id=9,
                user_id=1,
                purchase_link_id=1,
                telegram_user_id=101,
                invoice_id=99,
                payload="ref-payment",
                rub_amount=Decimal("10.00"),
                token_amount=10_000_000,
                status="pending",
            ))
            session.commit()
        invoice = {
            "invoice_id": 99,
            "payload": "ref-payment",
            "status": "paid",
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": "10.00",
        }
        with self.Session() as session:
            first, _ = credit_verified_payment(session, 9, invoice)
            second, _ = credit_verified_payment(session, 9, invoice)
            referral = session.query(Referral).one()
            referrer_balance = session.get(User, 2).token_balance
        self.assertEqual(first, "credited")
        self.assertEqual(second, "already")
        self.assertEqual(referrer_balance, 7_000_000)
        self.assertEqual(referral.status, "rewarded")
        self.assertEqual(referral.first_topup_tokens, 10_000_000)
        self.assertEqual(referral.payment_source, "crypto")

    def test_small_first_topup_never_unlocks_reward_later(self):
        with self.Session() as session:
            session.add(User(id=2, name="Referrer", email="ref@example.com", token_balance=5_000_000))
            session.add(Referral(
                referrer_id=2,
                referred_user_id=1,
                status="pending",
                reward_tokens=2_000_000,
            ))
            for payment_id, amount in ((10, 5), (11, 20)):
                session.add(TokenPayment(
                    id=payment_id,
                    user_id=1,
                    purchase_link_id=1,
                    telegram_user_id=101,
                    invoice_id=100 + payment_id,
                    payload=f"small-{payment_id}",
                    rub_amount=Decimal(f"{amount}.00"),
                    token_amount=amount * 1_000_000,
                    status="pending",
                ))
            session.commit()
        with self.Session() as session:
            for payment_id, amount in ((10, 5), (11, 20)):
                credit_verified_payment(session, payment_id, {
                    "invoice_id": 100 + payment_id,
                    "payload": f"small-{payment_id}",
                    "status": "paid",
                    "currency_type": "fiat",
                    "fiat": "RUB",
                    "amount": f"{amount}.00",
                })
            referral = session.query(Referral).one()
            referrer_balance = session.get(User, 2).token_balance
        self.assertEqual(referral.status, "ineligible_minimum")
        self.assertEqual(referral.first_topup_tokens, 5_000_000)
        self.assertEqual(referrer_balance, 5_000_000)

    def test_platega_payment_is_credited_exactly_once(self):
        with self.Session() as session:
            session.add(User(id=2, name="Referrer", email="ref@example.com", token_balance=5_000_000))
            session.add(Referral(
                referrer_id=2,
                referred_user_id=1,
                status="pending",
                reward_tokens=2_000_000,
            ))
            link = session.get(PurchaseLink, 1)
            payment = create_platega_payment(
                session,
                link,
                101,
                Decimal("25.00"),
                25_000_000,
                "platega-payload",
                {
                    "transactionId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "expiresIn": "01:00:00",
                },
            )
            payment_id = payment.id
        transaction = {
            "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "payload": "platega-payload",
            "status": "CONFIRMED",
            "paymentMethod": "SBPQR",
            "paymentDetails": {"amount": 25, "currency": "RUB"},
        }
        with self.Session() as session:
            credited, _ = credit_verified_platega_payment(session, payment_id, transaction)
        with self.Session() as session:
            repeated, _ = credit_verified_platega_payment(session, payment_id, transaction)
            stored = session.get(PlategaPayment, payment_id)
            balance = session.get(User, 1).token_balance
            referral = session.query(Referral).one()
            referrer_balance = session.get(User, 2).token_balance
        self.assertEqual(credited, "credited")
        self.assertEqual(repeated, "already")
        self.assertEqual(balance, 30_000_000)
        self.assertEqual(stored.status, "confirmed")
        self.assertEqual(stored.payment_method, "SBPQR")
        self.assertEqual(referral.status, "rewarded")
        self.assertEqual(referral.payment_source, "platega")
        self.assertEqual(referrer_balance, 7_000_000)

    def test_unconfirmed_platega_payment_never_credits_balance(self):
        with self.Session() as session:
            link = session.get(PurchaseLink, 1)
            payment = create_platega_payment(
                session,
                link,
                101,
                Decimal("10.00"),
                10_000_000,
                "cancel-payload",
                {"transactionId": "cancel-transaction", "expiresIn": "01:00:00"},
            )
            payment_id = payment.id
        with self.Session() as session:
            result, _ = credit_verified_platega_payment(session, payment_id, {
                "id": "cancel-transaction",
                "payload": "cancel-payload",
                "status": "CANCELED",
                "paymentDetails": {"amount": 10, "currency": "RUB"},
            })
            user_balance = session.get(User, 1).token_balance
            stored = session.get(PlategaPayment, payment_id)
        self.assertEqual(result, "canceled")
        self.assertEqual(stored.status, "canceled")
        self.assertEqual(user_balance, 5_000_000)

    def test_platega_invoice_expires_locally_after_60_minutes(self):
        with self.Session() as session:
            link = session.get(PurchaseLink, 1)
            payment = create_platega_payment(
                session,
                link,
                101,
                Decimal("10.00"),
                10_000_000,
                "expiry-payload",
                {"transactionId": "expiry-transaction", "expiresIn": "01:00:00"},
            )
            lifetime_seconds = (payment.expires_at - payment.created_at).total_seconds()
            payment.expires_at = utcnow()
            session.commit()
        with self.Session() as session:
            pending = get_pending_platega_payments(session)
            stored = session.get(PlategaPayment, payment.id)
        self.assertGreaterEqual(lifetime_seconds, 3599)
        self.assertLessEqual(lifetime_seconds, 3601)
        self.assertEqual(pending, [])
        self.assertEqual(stored.status, "expired")

    def test_admin_statistics_include_platega(self):
        with self.Session() as session:
            link = session.get(PurchaseLink, 1)
            link.telegram_user_id = 101
            payment = create_platega_payment(
                session,
                link,
                101,
                Decimal("5.00"),
                5_000_000,
                "stats-payload",
                {"transactionId": "stats-transaction", "expiresIn": "01:00:00"},
            )
            credit_verified_platega_payment(session, payment.id, {
                "id": "stats-transaction",
                "payload": "stats-payload",
                "status": "CONFIRMED",
                "paymentDetails": {"amount": 5, "currency": "RUB"},
            })
        with self.Session() as session:
            stats = admin_statistics(session)
        self.assertEqual(stats["users"], 1)
        self.assertEqual(stats["linked"], 1)
        self.assertEqual(stats["platega_paid"], 1)
        self.assertEqual(stats["platega_rub"], Decimal("5.00"))


if __name__ == "__main__":
    unittest.main()

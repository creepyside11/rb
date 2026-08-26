import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, BotUser, PlategaPayment, PurchaseLink, Referral, TokenPayment, User, utcnow
from cryptopay import CryptoPayClient, CryptoPayError
from payments import (
    admin_statistics,
    admin_bot_user,
    admin_bot_users,
    admin_site_user,
    admin_site_users,
    backfill_bound_bot_users,
    bind_purchase_link,
    create_platega_payment,
    credit_verified_platega_payment,
    credit_verified_payment,
    get_pending_platega_payments,
    get_expired_platega_payments,
    mark_expired_platega_payment_checked,
    get_token_price,
    set_token_price,
    tokens_to_rubles,
    upsert_bot_user,
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

    def test_admin_price_is_persistent_and_used_for_all_amounts(self):
        with self.Session() as session:
            self.assertEqual(get_token_price(session), Decimal("1.00"))
            stored = set_token_price(session, "2,50")
        with self.Session() as session:
            loaded = get_token_price(session)

        self.assertEqual(stored, Decimal("2.50"))
        self.assertEqual(loaded, Decimal("2.50"))
        self.assertEqual(tokens_to_rubles(10_000_000, loaded), Decimal("25.00"))

    def test_invalid_admin_price_is_rejected(self):
        with self.Session() as session:
            with self.assertRaises(ValueError):
                set_token_price(session, "0")
            with self.assertRaises(ValueError):
                set_token_price(session, "not-a-number")

    def test_bot_profiles_and_site_users_are_listed_separately(self):
        with self.Session() as session:
            link = session.get(PurchaseLink, 1)
            link.telegram_user_id = 101
            upsert_bot_user(session, 101, "first_username", "First", "User")
            updated = upsert_bot_user(session, 101, "new_username", "New", "Name")
            session.add(BotUser(
                telegram_user_id=202,
                username=None,
                first_name="Bot only",
                last_seen_at=utcnow(),
            ))
            session.commit()

            site_rows = admin_site_users(session)
            bot_rows = admin_bot_users(session)
            site_row = admin_site_user(session, 1)
            bot_row = admin_bot_user(session, 101)

        self.assertEqual(updated.username, "new_username")
        self.assertEqual(len(site_rows), 1)
        self.assertEqual(len(bot_rows), 2)
        self.assertEqual(site_row[0].email, "test@example.com")
        self.assertEqual(site_row[2].username, "new_username")
        self.assertEqual(bot_row[0].telegram_user_id, 101)
        self.assertEqual(bot_row[2].id, 1)

    def test_existing_bound_telegram_users_are_backfilled(self):
        with self.Session() as session:
            session.get(PurchaseLink, 1).telegram_user_id = 303
            session.commit()
            created = backfill_bound_bot_users(session)
            repeated = backfill_bound_bot_users(session)
            profile = session.get(BotUser, 303)

        self.assertEqual(created, 1)
        self.assertEqual(repeated, 0)
        self.assertIsNotNone(profile)

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

    def test_expired_platega_invoice_can_be_recovered_exactly_once(self):
        with self.Session() as session:
            link = session.get(PurchaseLink, 1)
            payment = create_platega_payment(
                session,
                link,
                101,
                Decimal("10.00"),
                10_000_000,
                "old-paid-payload",
                {"transactionId": "old-paid-transaction", "expiresIn": "01:00:00"},
            )
            payment.expires_at = utcnow()
            session.commit()
        with self.Session() as session:
            self.assertEqual(get_pending_platega_payments(session), [])
            expired = get_expired_platega_payments(session)
            self.assertEqual([item.id for item in expired], [payment.id])
            credited, _ = credit_verified_platega_payment(session, payment.id, {
                "id": "old-paid-transaction",
                "payload": "old-paid-payload",
                "status": "CONFIRMED",
                "paymentDetails": {"amount": 10, "currency": "RUB"},
            })
            repeated, _ = credit_verified_platega_payment(session, payment.id, {
                "id": "old-paid-transaction",
                "payload": "old-paid-payload",
                "status": "CONFIRMED",
                "paymentDetails": {"amount": 10, "currency": "RUB"},
            })
            balance = session.get(User, 1).token_balance
        self.assertEqual(credited, "credited")
        self.assertEqual(repeated, "already")
        self.assertEqual(balance, 15_000_000)

    def test_unpaid_expired_platega_invoice_leaves_recovery_queue(self):
        with self.Session() as session:
            link = session.get(PurchaseLink, 1)
            payment = create_platega_payment(
                session,
                link,
                101,
                Decimal("10.00"),
                10_000_000,
                "old-unpaid-payload",
                {"transactionId": "old-unpaid-transaction", "expiresIn": "01:00:00"},
            )
            payment.expires_at = utcnow()
            session.commit()
            get_pending_platega_payments(session)
            self.assertTrue(mark_expired_platega_payment_checked(session, payment.id))
            self.assertEqual(get_expired_platega_payments(session), [])
            stored = session.get(PlategaPayment, payment.id)
        self.assertEqual(stored.status, "expired_checked")

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

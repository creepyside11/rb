import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, PurchaseLink, TokenPayment, User
from payments import bind_purchase_link, credit_verified_payment


class PaymentTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as session:
            session.add(User(id=1, token_balance=5_000_000))
            session.add(PurchaseLink(id=1, user_id=1, token="x" * 32, is_active=True))
            session.commit()

    def test_link_binds_to_only_one_telegram_account(self):
        with self.Session() as session:
            result, _ = bind_purchase_link(session, "x" * 32, 101)
        with self.Session() as session:
            stolen, _ = bind_purchase_link(session, "x" * 32, 202)
        self.assertEqual(result, "ok")
        self.assertEqual(stolen, "claimed")

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


if __name__ == "__main__":
    unittest.main()

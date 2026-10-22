import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.auth import get_current_user
from database.database import Base, get_db
from database.models import Payment, TripQuote, User
from payments.router import router as payments_router


class PaymentSecurityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        with self.session_factory() as db:
            self.user = User(name="Traveler One", email="one@example.test", password_hash="x")
            self.other_user = User(name="Traveler Two", email="two@example.test", password_hash="x")
            db.add_all([self.user, self.other_user])
            db.commit()
            db.refresh(self.user)
            db.refresh(self.other_user)

        self.current_user = self.user
        self.app = FastAPI()
        self.app.include_router(payments_router)

        def override_db():
            with self.session_factory() as db:
                yield db

        def override_user():
            return self.current_user

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = override_user

    def tearDown(self):
        self.engine.dispose()

    def _create_quote(self, client):
        response = client.post(
            "/api/payments/create-quote",
            json={
                "flight_option": "Flight option",
                "hotel_option": "Hotel option",
                "flight_price": 12000,
                "hotel_price": 8000,
                "nights": 4,
                "travelers": 2,
                "destination": "Singapore",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["quote_id"]

    def _create_payment(self, quote_id, order_id, user_id=None, status="CREATED"):
        with self.session_factory() as db:
            payment = Payment(
                user_id=user_id or self.user.id,
                quote_id=quote_id,
                paypal_order_id=order_id,
                amount=20000,
                currency="INR",
                status=status,
                description="Test payment",
            )
            db.add(payment)
            db.commit()
            db.refresh(payment)
            return payment.id

    def test_payment_endpoints_require_authentication(self):
        self.app.dependency_overrides.pop(get_current_user)
        with TestClient(self.app) as client:
            response = client.post("/api/payments/create-order", json={"quote_id": 1})
        self.assertGreaterEqual(response.status_code, 400)

    def test_create_order_uses_persisted_quote_amount(self):
        with TestClient(self.app) as client:
            quote_id = self._create_quote(client)
            with patch(
                "payments.router.paypal_client.create_order",
                new_callable=AsyncMock,
                return_value={"id": "ORDER-TRUSTED"},
            ) as create_order:
                response = client.post(
                    "/api/payments/create-order",
                    json={"quote_id": quote_id, "amount": 0.01, "currency": "USD"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["amount_inr"], 20000)
        create_order.assert_awaited_once()
        self.assertEqual(create_order.await_args.kwargs["amount_inr"], 20000)

    def test_expired_and_other_users_quotes_cannot_create_orders(self):
        with TestClient(self.app) as client:
            expired_quote_id = self._create_quote(client)
            with self.session_factory() as db:
                quote = db.query(TripQuote).filter_by(id=expired_quote_id).one()
                quote.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
                db.commit()

            with patch("payments.router.paypal_client.create_order", new_callable=AsyncMock) as create_order:
                expired_response = client.post(
                    "/api/payments/create-order",
                    json={"quote_id": expired_quote_id},
                )
                self.assertEqual(expired_response.status_code, 400)

                other_user_quote_id = self._create_quote(client)
                self.current_user = self.other_user
                ownership_response = client.post(
                    "/api/payments/create-order",
                    json={"quote_id": other_user_quote_id},
                )
                self.assertEqual(ownership_response.status_code, 403)
                create_order.assert_not_awaited()

    def test_other_users_cannot_capture_payment(self):
        with TestClient(self.app) as client:
            quote_id = self._create_quote(client)
            self._create_payment(quote_id, "ORDER-OWNED")
            self.current_user = self.other_user
            with patch("payments.router.paypal_client.capture_order", new_callable=AsyncMock) as capture:
                response = client.post(
                    "/api/payments/capture-order",
                    json={"order_id": "ORDER-OWNED"},
                )

        self.assertEqual(response.status_code, 403)
        capture.assert_not_awaited()

    def test_capture_requires_completed_provider_status(self):
        with TestClient(self.app) as client:
            quote_id = self._create_quote(client)
            self._create_payment(quote_id, "ORDER-PENDING")
            with patch(
                "payments.router.paypal_client.capture_order",
                new_callable=AsyncMock,
                return_value={"status": "PENDING"},
            ):
                response = client.post(
                    "/api/payments/capture-order",
                    json={"order_id": "ORDER-PENDING"},
                )

        self.assertEqual(response.status_code, 400)
        with self.session_factory() as db:
            payment = db.query(Payment).filter_by(paypal_order_id="ORDER-PENDING").one()
            self.assertEqual(payment.status, "FAILED")

    def test_completed_capture_is_idempotent(self):
        completed_response = {
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {"id": "CAPTURE-1", "amount": {"value": "240.00"}}
                        ]
                    }
                }
            ],
        }
        with TestClient(self.app) as client:
            quote_id = self._create_quote(client)
            self._create_payment(quote_id, "ORDER-COMPLETED")
            with patch(
                "payments.router.paypal_client.capture_order",
                new_callable=AsyncMock,
                return_value=completed_response,
            ) as capture:
                first = client.post(
                    "/api/payments/capture-order",
                    json={"order_id": "ORDER-COMPLETED"},
                )
                repeated = client.post(
                    "/api/payments/capture-order",
                    json={"order_id": "ORDER-COMPLETED"},
                )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["status"], "already_captured")
        capture.assert_awaited_once_with("ORDER-COMPLETED")


if __name__ == "__main__":
    unittest.main()

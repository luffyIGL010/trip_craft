import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.auth import get_password_hash, router as auth_router
from database.database import Base, get_db
from database.models import User


TEST_SECRET = "test-jwt-signing-key-for-login-tests"


class AuthLoginTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.app = FastAPI()
        self.app.include_router(auth_router)

        def override_db():
            with self.session_factory() as db:
                yield db

        self.app.dependency_overrides[get_db] = override_db
        self.client_context = patch.dict(os.environ, {"JWT_SECRET_KEY": TEST_SECRET})
        self.client_context.start()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.client_context.stop()
        self.engine.dispose()

    def test_registered_account_can_login_after_client_reopens_with_email_case_change(self):
        registration = self.client.post(
            "/auth/register",
            json={
                "name": "Login Test",
                "email": "Login.Test@example.com",
                "password": "correct horse battery staple",
            },
        )
        self.assertEqual(registration.status_code, 200)

        self.client.close()
        self.client = TestClient(self.app)
        login = self.client.post(
            "/auth/login",
            json={
                "email": " login.test@EXAMPLE.com ",
                "password": "correct horse battery staple",
            },
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn("access_token", login.json())

    def test_wrong_password_is_still_rejected(self):
        with self.session_factory() as db:
            db.add(
                User(
                    name="Login Test",
                    email="login.test@example.com",
                    password_hash=get_password_hash("correct horse battery staple"),
                )
            )
            db.commit()

        response = self.client.post(
            "/auth/login",
            json={
                "email": "login.test@example.com",
                "password": "incorrect password",
            },
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()

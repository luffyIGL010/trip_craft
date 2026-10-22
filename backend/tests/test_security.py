import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.auth import create_access_token, require_jwt_secret_key
from database.database import Base, get_db
from database.models import User
from websocket import router as websocket_router


TEST_SECRET = "test-jwt-signing-key-for-security-tests"


class AuthenticationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        with self.session_factory() as db:
            user = User(
                name="Security Test",
                email="security@example.test",
                password_hash="unused",
            )
            db.add(user)
            db.commit()

        self.app = FastAPI()
        self.app.include_router(websocket_router)

        def override_db():
            with self.session_factory() as db:
                yield db

        self.app.dependency_overrides[get_db] = override_db

    def tearDown(self):
        self.engine.dispose()

    def test_jwt_secret_is_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "JWT_SECRET_KEY must be configured"):
                require_jwt_secret_key()
            with self.assertRaisesRegex(RuntimeError, "JWT_SECRET_KEY must be configured"):
                create_access_token({"sub": "security@example.test"})

    def test_query_parameter_does_not_authenticate_websocket(self):
        with patch.dict(os.environ, {"JWT_SECRET_KEY": TEST_SECRET}):
            token = create_access_token({"sub": "security@example.test"})
            with TestClient(self.app) as client:
                with client.websocket_connect(
                    f"/ws?token={token}",
                    subprotocols=["travelagent"],
                ) as websocket:
                    self.assertEqual(
                        websocket.receive_json(),
                        {"error": "Missing authentication token"},
                    )
                    with self.assertRaises(WebSocketDisconnect) as disconnect:
                        websocket.receive_json()
                    self.assertEqual(disconnect.exception.code, 1008)

    def test_invalid_and_expired_websocket_tokens_are_rejected(self):
        with patch.dict(os.environ, {"JWT_SECRET_KEY": TEST_SECRET}):
            expired_token = create_access_token(
                {"sub": "security@example.test"},
                expires_delta=timedelta(seconds=-10),
            )
            for token in ("not-a-valid-token", expired_token):
                with self.subTest(token=token == expired_token):
                    with TestClient(self.app) as client:
                        with client.websocket_connect(
                            "/ws",
                            subprotocols=["travelagent", f"travelagent.auth.{token}"],
                        ) as websocket:
                            self.assertEqual(
                                websocket.receive_json(),
                                {"error": "Invalid or expired authentication token"},
                            )
                            with self.assertRaises(WebSocketDisconnect) as disconnect:
                                websocket.receive_json()
                            self.assertEqual(disconnect.exception.code, 1008)


if __name__ == "__main__":
    unittest.main()

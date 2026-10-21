import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.master_agent import MasterAgent
from auth.auth import get_current_user
from database.database import Base, get_db
from database.models import TravelPreference, User
from preferences.router import router as preferences_router
from preferences.service import (
    explicit_categories_in_request,
    extract_explicit_preferences,
    upsert_preference,
)
from schemas.hotel import HotelOption, HotelResult
from schemas.transport import TransportResult


class PreferenceApiTests(unittest.TestCase):
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
        app = FastAPI()
        app.include_router(preferences_router)

        def override_db():
            with self.session_factory() as db:
                yield db

        def override_user():
            return self.current_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def test_preference_create_update_delete_and_user_isolation(self):
        created = self.client.post(
            "/api/preferences",
            json={"category": "accommodation", "value": "budget"},
        )
        self.assertEqual(created.status_code, 201)
        preference_id = created.json()["id"]

        listed = self.client.get("/api/preferences")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["preferences"][0]["value"], "budget")

        updated = self.client.put(
            f"/api/preferences/{preference_id}",
            json={"category": "accommodation", "value": "luxury"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["value"], "luxury")

        self.current_user = self.other_user
        self.assertEqual(
            self.client.get("/api/preferences").json()["preferences"],
            [],
        )
        self.assertEqual(
            self.client.delete(f"/api/preferences/{preference_id}").status_code,
            404,
        )

        self.current_user = self.user
        self.assertEqual(
            self.client.delete(f"/api/preferences/{preference_id}").status_code,
            204,
        )

    def test_budget_validation_and_profile_clear(self):
        invalid = self.client.post(
            "/api/preferences",
            json={"category": "budget", "min_amount": 500, "max_amount": 100},
        )
        self.assertEqual(invalid.status_code, 422)

        created = self.client.post(
            "/api/preferences",
            json={
                "category": "budget",
                "min_amount": 100,
                "max_amount": 500,
                "currency": "inr",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["currency"], "INR")
        self.assertEqual(self.client.delete("/api/preferences").status_code, 204)
        self.assertEqual(self.client.get("/api/preferences").json()["preferences"], [])

    def test_private_endpoint_requires_authentication(self):
        self.client.app.dependency_overrides.pop(get_current_user)
        response = self.client.get("/api/preferences")
        self.assertGreaterEqual(response.status_code, 400)

    def test_preferences_persist_across_database_engine_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "preferences.db"
            url = f"sqlite:///{database_path}"
            first_engine = create_engine(url)
            Base.metadata.create_all(bind=first_engine)
            first_session = sessionmaker(bind=first_engine)
            with first_session() as db:
                user = User(name="Persistent", email="persist@example.test", password_hash="x")
                db.add(user)
                db.commit()
                db.refresh(user)
                upsert_preference(
                    db,
                    user.id,
                    category="interest",
                    value="history",
                )
                db.commit()
                user_id = user.id
            first_engine.dispose()

            restarted_engine = create_engine(url)
            restarted_session = sessionmaker(bind=restarted_engine)
            with restarted_session() as db:
                saved = db.query(TravelPreference).filter_by(user_id=user_id).one()
                self.assertEqual(saved.value, "history")
            restarted_engine.dispose()

    def test_only_recurring_explicit_preferences_are_extracted(self):
        extracted = extract_explicit_preferences(
            "I usually prefer budget hotels and historical places."
        )
        self.assertEqual(
            extracted,
            [
                {"category": "accommodation", "value": "budget"},
                {"category": "interest", "value": "history"},
            ],
        )
        self.assertEqual(
            extract_explicit_preferences("Find a budget hotel for this trip."),
            [],
        )

    def test_trip_dates_do_not_override_saved_budget_preference(self):
        categories = explicit_categories_in_request(
            "Plan a trip to Jaipur and find budget hotels from 2027-05-01 to 2027-05-04"
        )
        self.assertIn("accommodation", categories)
        self.assertNotIn("budget", categories)
        self.assertIn("budget", explicit_categories_in_request("My budget is INR 50,000"))


class MasterAgentPreferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_accommodation_preference_sorts_real_results(self):
        agent = MasterAgent()
        agent.client = None
        agent.travel_agent.execute = AsyncMock(
            return_value=TransportResult(status="success")
        )
        hotels = HotelResult(
            status="success",
            options=[
                HotelOption(name="Lower rated", total_price=300, star_rating=3),
                HotelOption(name="Higher rated", total_price=100, star_rating=5),
            ],
        )
        agent.hotel_agent.execute = AsyncMock(return_value=hotels)

        response = await agent.process_request(
            "Plan a trip to Jaipur from Delhi from 2027-05-01 to 2027-05-04 for 2 adults",
            saved_preferences=[
                {"category": "accommodation", "value": "luxury"},
                {"category": "interest", "value": "history"},
            ],
        )

        self.assertEqual(
            response["hotel_results"]["options"][0]["name"],
            "Higher rated",
        )
        self.assertTrue(
            any("interest: history" in item for item in response["plan"]["preferences"])
        )

    async def test_current_accommodation_request_overrides_saved_preference(self):
        agent = MasterAgent()
        agent.client = None
        agent.hotel_agent.execute = AsyncMock(
            return_value=HotelResult(
                status="success",
                options=[
                    HotelOption(name="Lower priced", total_price=100),
                    HotelOption(name="Higher priced", total_price=300),
                ],
            )
        )

        response = await agent.process_request(
            "Plan a trip to Jaipur from 2027-05-01 to 2027-05-04 and find a budget hotel",
            saved_preferences=[
                {"category": "accommodation", "value": "luxury"},
            ],
        )

        self.assertEqual(
            response["hotel_results"]["options"][0]["name"],
            "Lower priced",
        )
        self.assertFalse(
            any("accommodation: luxury" in item for item in response["plan"]["preferences"])
        )

    async def test_empty_memory_preserves_existing_hotel_order(self):
        agent = MasterAgent()
        agent.client = None
        hotels = HotelResult(
            status="success",
            options=[
                HotelOption(name="First result", total_price=300),
                HotelOption(name="Second result", total_price=100),
            ],
        )
        agent.hotel_agent.execute = AsyncMock(return_value=hotels)

        response = await agent.process_request(
            "Find hotels in Jaipur from 2027-05-01 to 2027-05-04",
            saved_preferences=[],
        )

        self.assertEqual(
            [item["name"] for item in response["hotel_results"]["options"]],
            ["First result", "Second result"],
        )


if __name__ == "__main__":
    unittest.main()

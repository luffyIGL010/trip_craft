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
from database.database import Base, ensure_travel_preference_budget_basis, get_db
from database.models import TravelPreference, User
from preferences.router import router as preferences_router
from preferences.service import (
    explicit_categories_in_request,
    extract_explicit_preferences,
    upsert_preference,
)
from agents.travel_agent import TravelAgent
from agents.schemas import Budget, Dates, MasterAgentResponse
from schemas.hotel import HotelOption, HotelResult
from schemas.transport import FlightLeg, TransportOption, TransportResult


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

        interest = self.client.post(
            "/api/preferences",
            json={"category": "interest", "value": "history"},
        )
        self.assertEqual(interest.status_code, 201)
        interest_id = interest.json()["id"]
        interest_update = self.client.put(
            f"/api/preferences/{interest_id}",
            json={"category": "interest", "value": "culture"},
        )
        self.assertEqual(interest_update.status_code, 200)
        self.assertEqual(interest_update.json()["value"], "culture")

        self.current_user = self.other_user
        self.assertEqual(
            self.client.get("/api/preferences").json()["preferences"],
            [],
        )
        self.assertEqual(
            self.client.delete(f"/api/preferences/{preference_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.put(
                f"/api/preferences/{preference_id}",
                json={"category": "accommodation", "value": "luxury"},
            ).status_code,
            404,
        )

        self.current_user = self.user
        self.assertEqual(
            self.client.delete(f"/api/preferences/{preference_id}").status_code,
            204,
        )
        self.assertEqual(
            self.client.delete(f"/api/preferences/{interest_id}").status_code,
            204,
        )
        self.assertEqual(
            self.client.get("/api/preferences").json()["preferences"],
            [],
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
                "budget_basis": "total_trip",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["currency"], "INR")
        self.assertEqual(created.json()["budget_basis"], "total_trip")
        interest = self.client.post(
            "/api/preferences",
            json={"category": "interest", "value": "history"},
        )
        self.assertEqual(interest.status_code, 201)
        self.assertEqual(len(self.client.get("/api/preferences").json()["preferences"]), 2)
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
                upsert_preference(
                    db,
                    user.id,
                    category="budget",
                    value="Usual budget",
                    min_amount=120000,
                    max_amount=600000,
                    currency="INR",
                    budget_basis="total_trip",
                )
                db.commit()
                user_id = user.id
            first_engine.dispose()

            restarted_engine = create_engine(url)
            restarted_session = sessionmaker(bind=restarted_engine)
            with restarted_session() as db:
                saved = db.query(TravelPreference).filter_by(
                    user_id=user_id, category="interest"
                ).one()
                self.assertEqual(saved.value, "history")
                saved_budget = db.query(TravelPreference).filter_by(
                    user_id=user_id, category="budget"
                ).one()
                self.assertEqual(saved_budget.budget_basis, "total_trip")
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

    def test_existing_preference_table_gets_unspecified_basis_additively(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'legacy.db'}")
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE travel_preferences ("
                    "id INTEGER PRIMARY KEY, user_id INTEGER, category VARCHAR, "
                    "value VARCHAR, min_amount FLOAT, max_amount FLOAT, currency VARCHAR)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO travel_preferences "
                    "(id, user_id, category, value, min_amount, max_amount, currency) "
                    "VALUES (1, 7, 'budget', '', 120000, 600000, 'INR')"
                )

            ensure_travel_preference_budget_basis(engine)
            with engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT min_amount, max_amount, currency, budget_basis "
                    "FROM travel_preferences WHERE id = 1"
                ).one()
                self.assertEqual(tuple(row), (120000, 600000, "INR", "unspecified"))
            engine.dispose()


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

    async def test_total_trip_budget_compares_against_combined_all_traveler_estimate(self):
        agent = MasterAgent()
        agent.client = None
        agent.travel_agent.execute = AsyncMock(return_value=TransportResult(
            status="success",
            travelers=2,
            trip_type="round_trip",
            options=[
                TransportOption(
                    provider="Air",
                    departure="out",
                    arrival="in",
                    duration="2h",
                    stops=0,
                    price=300000,
                    currency="INR",
                    price_label="Provider itinerary price for 2 travelers (round trip)",
                    trip_type="round_trip",
                    outbound_leg=FlightLeg(
                        provider="Air",
                        flight_number="AB 123",
                        departure_airport="DEL",
                        departure_time="2027-03-15T08:00",
                        arrival_airport="DXB",
                        arrival_time="2027-03-15T10:00",
                    ),
                )
            ],
        ))
        agent.hotel_agent.execute = AsyncMock(return_value=HotelResult(
            status="success",
            nights=5,
            options=[
                HotelOption(
                    name="Within budget stay",
                    city="Dubai",
                    total_price=100000,
                    currency="INR",
                ),
                HotelOption(
                    name="Above budget stay",
                    city="Dubai",
                    total_price=350000,
                    currency="INR",
                ),
            ],
        ))

        result = await agent.process_request(
            "Plan a trip to Dubai from Delhi from March 15, 2027 to March 20, 2027 for 2 adults",
            saved_preferences=[
                {
                    "category": "budget",
                    "min_amount": 120000,
                    "max_amount": 600000,
                    "currency": "INR",
                    "budget_basis": "total_trip",
                }
            ],
        )

        estimate = result["combined_trip_cost"]["estimates"][0]
        self.assertEqual(estimate["combined_total"], 400000)
        self.assertEqual(estimate["flight_scope"], "Provider itinerary price for 2 travelers (round trip)")
        self.assertEqual(estimate["saved_budget_comparison"]["status"], "within")
        self.assertEqual(
            result["combined_trip_cost"]["estimates"][1]["saved_budget_comparison"]["status"],
            "above",
        )
        self.assertTrue(
            any(
                "total trip, all travelers" in preference
                for preference in result["plan"]["preferences"]
            )
        )
        self.assertTrue(
            any("INR 120,000–INR 600,000" in preference
                for preference in result["plan"]["preferences"])
        )

    async def test_current_budget_overrides_saved_budget_preference(self):
        agent = MasterAgent()
        agent.client = None
        agent.travel_agent.execute = AsyncMock(
            return_value=TransportResult(status="needs_information")
        )
        agent.hotel_agent.execute = AsyncMock(
            return_value=HotelResult(status="needs_information")
        )
        result = await agent.process_request(
            "Plan a 5 day trip to Dubai from Delhi for 2 adults with a budget of 50000",
            saved_preferences=[
                {
                    "category": "budget",
                    "min_amount": 120000,
                    "max_amount": 600000,
                    "currency": "INR",
                    "budget_basis": "total_trip",
                }
            ],
        )
        preferences = result["plan"]["preferences"]
        self.assertTrue(
            any("Current trip request overrides saved budget" in item for item in preferences)
        )
        self.assertFalse(any("Saved travel preference — usual budget" in item for item in preferences))

    async def test_duplicate_flight_and_hotel_options_create_one_combination(self):
        agent = MasterAgent()
        agent.client = None
        flight = TransportOption(
            provider="Air",
            departure="out",
            arrival="in",
            duration="2h",
            stops=0,
            price=10000,
            currency="INR",
            trip_type="round_trip",
            outbound_leg=FlightLeg(
                provider="Air",
                flight_number="AB 123",
                departure_airport="DEL",
                departure_time="2027-03-15T08:00",
                arrival_airport="DXB",
                arrival_time="2027-03-15T10:00",
            ),
        )
        hotel = HotelOption(name="Hotel One", city="Dubai", total_price=25000, currency="INR")
        agent.travel_agent.execute = AsyncMock(return_value=TransportResult(
            status="success",
            options=[flight, flight.model_copy(update={"price": 11000})],
        ))
        agent.hotel_agent.execute = AsyncMock(return_value=HotelResult(
            status="success",
            nights=5,
            options=[hotel, hotel.model_copy(update={"total_price": 26000})],
        ))

        result = await agent.process_request(
            "Plan a trip to Dubai from Delhi from March 15, 2027 to March 20, 2027 for 2 adults"
        )

        estimates = result["combined_trip_cost"]["estimates"]
        self.assertEqual(len(estimates), 1)
        self.assertEqual(estimates[0]["combined_total"], 35000)


class FlightFareMappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip_query_uses_provider_itinerary_price_and_hides_return_fare(self):
        agent = TravelAgent()
        search = AsyncMock(return_value={
            "is_mock": False,
            "trip_type": "round_trip",
            "data": {
                "best_flights": [
                    {
                        "flights": [{
                            "airline": "Air",
                            "flight_number": "AB 123",
                            "departure_airport": {"id": "DEL", "time": "2027-03-15T08:00"},
                            "arrival_airport": {"id": "DXB", "time": "2027-03-15T10:00"},
                        }],
                        "total_duration": 120,
                        "price": 400000,
                    },
                    {
                        "flights": [{
                            "airline": "Air",
                            "flight_number": "CD 234",
                            "departure_airport": {"id": "DEL", "time": "2027-03-15T12:00"},
                            "arrival_airport": {"id": "DXB", "time": "2027-03-15T14:00"},
                        }],
                        "total_duration": 120,
                    },
                ]
            },
            "return_data": {
                "best_flights": [{
                    "flights": [{
                        "airline": "Air",
                        "flight_number": "AB 124",
                        "departure_airport": {"id": "DXB", "time": "2027-03-20T12:00"},
                        "arrival_airport": {"id": "DEL", "time": "2027-03-20T14:00"},
                    }],
                    "total_duration": 120,
                    "price": 200000,
                }]
            },
        })
        agent.api_client.search_flights = search
        plan = MasterAgentResponse(
            request_summary="Dubai trip",
            origin="Delhi",
            destination="Dubai",
            dates=Dates(start="2027-03-15", end="2027-03-20"),
            travelers=2,
            budget=Budget(),
        )

        result = await agent.execute(plan)

        search.assert_awaited_once_with(
            origin="DEL",
            destination="DXB",
            outbound_date="2027-03-15",
            return_date="2027-03-20",
            travelers=2,
        )
        self.assertEqual(result.options[0].price, 400000)
        self.assertIsNone(result.options[1].price)
        self.assertEqual(
            result.price_label,
            "Provider itinerary price for 2 travelers (round trip)",
        )
        self.assertEqual(len(result.return_options), 1)
        self.assertIsNone(result.return_options[0].price)


if __name__ == "__main__":
    unittest.main()

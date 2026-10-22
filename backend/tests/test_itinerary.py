import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.auth import create_access_token
from database.database import Base, get_db
from database.models import User
from agents.itinerary_agent import ItineraryAgent
from agents.master_agent import MasterAgent
from agents.schemas import Budget, Dates, MasterAgentResponse
from schemas.hotel import HotelOption, HotelResult
from schemas.itinerary import ItineraryResult
from schemas.transport import FlightLeg, TransportOption, TransportResult


def make_completion(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


def make_days(start_date="2027-05-01"):
    return [
        {
            "date": start_date,
            "title": "Arrival and local orientation",
            "activities": [
                {
                    "start_time": "13:00",
                    "end_time": "14:00",
                    "title": "Lunch near the hotel",
                    "category": "meal",
                    "location": "Near the hotel",
                    "description": "Choose a nearby restaurant after arrival.",
                },
                {
                    "start_time": "15:00",
                    "end_time": "17:00",
                    "title": "Explore the waterfront",
                    "category": "activity",
                    "location": "Waterfront",
                    "description": "A flexible orientation walk; verify local conditions.",
                },
            ],
        },
        {
            "date": "2027-05-02",
            "title": "Culture and a relaxed afternoon",
            "activities": [
                {
                    "start_time": "09:00",
                    "end_time": "11:00",
                    "title": "Visit a local museum",
                    "category": "activity",
                    "location": "City center",
                    "description": "Check current opening hours before visiting.",
                },
                {
                    "start_time": "12:00",
                    "end_time": "13:00",
                    "title": "Lunch",
                    "category": "meal",
                    "location": "City center",
                    "description": "Allow time for a meal break.",
                },
            ],
        },
        {
            "date": "2027-05-03",
            "title": "Departure day",
            "activities": [
                {
                    "start_time": "09:00",
                    "end_time": "10:00",
                    "title": "Breakfast and pack",
                    "category": "meal",
                    "location": "Hotel",
                    "description": "Leave time for checkout and transfer arrangements.",
                },
                {
                    "start_time": "11:00",
                    "end_time": "15:30",
                    "title": "Free time near the hotel",
                    "category": "free_time",
                    "location": "Near the hotel",
                    "description": "Keep this flexible and confirm your transfer plan.",
                },
            ],
        },
    ]


def make_plan(**updates):
    values = {
        "request_summary": "Three-day Goa trip",
        "destination": "Goa",
        "origin": "Delhi",
        "dates": Dates(start="2027-05-01", end="2027-05-03"),
        "duration_days": 3,
        "travelers": 2,
        "budget": Budget(amount=50000, currency="INR"),
        "preferences": ["beaches", "relaxed pace"],
        "tasks": ["create_itinerary"],
    }
    values.update(updates)
    return MasterAgentResponse(**values)


def configured_itinerary_agent(payload):
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=Mock(return_value=make_completion(json.dumps(payload))),
            )
        )
    )
    return ItineraryAgent(client=client), client


class ItineraryAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_one_day_per_trip_date_and_uses_trip_context(self):
        agent, client = configured_itinerary_agent({
            "summary": "A relaxed three-day plan.",
            "days": make_days(),
        })
        transport = TransportResult(
            status="success",
            trip_type="round_trip",
            is_mock=False,
            options=[
                TransportOption(
                    option_id="flight-1",
                    provider="Air",
                    departure="2027-05-01 08:00",
                    arrival="2027-05-01 12:00",
                    duration="4h",
                    stops=0,
                    outbound_leg=FlightLeg(
                        provider="Air",
                        flight_number="AB 123",
                        departure_airport="DEL",
                        departure_time="2027-05-01T08:00",
                        arrival_airport="GOI",
                        arrival_time="2027-05-01T12:30",
                    ),
                    return_leg=FlightLeg(
                        provider="Air",
                        flight_number="AB 124",
                        departure_airport="GOI",
                        departure_time="2027-05-03T17:30",
                        arrival_airport="DEL",
                        arrival_time="2027-05-03T20:00",
                    ),
                )
            ],
            return_options=[
                TransportOption(
                    option_id="return-1",
                    paired_outbound_option_id="flight-1",
                    provider="Air",
                    departure="2027-05-03 17:30",
                    arrival="2027-05-03 20:00",
                    duration="2h30m",
                    stops=0,
                    outbound_leg=FlightLeg(
                        provider="Air",
                        flight_number="AB 124",
                        departure_airport="GOI",
                        departure_time="2027-05-03T17:30",
                        arrival_airport="DEL",
                        arrival_time="2027-05-03T20:00",
                    ),
                )
            ],
        )
        hotels = HotelResult(
            status="success",
            options=[
                HotelOption(
                    option_id="hotel-1",
                    name="Coast Stay",
                    address="Central Goa",
                    total_price=12000,
                    currency="INR",
                )
            ],
        )

        result = await agent.execute(
            make_plan(),
            transport_results=transport,
            hotel_results=hotels,
            weather_results={
                "status": "success",
                "source": "Open-Meteo",
                "timezone": "Asia/Kolkata",
                "days": [{
                    "date": "2027-05-01",
                    "forecast_available": True,
                    "condition": "Moderate rain",
                    "temperature_2m_max": 31,
                    "precipitation_probability_max": 70,
                    "warnings": ["Rain is forecast."],
                    "hourly": [{
                        "time": "2027-05-01T12:00",
                        "temperature_2m": 29,
                        "condition": "Moderate rain",
                        "precipitation_probability": 70,
                    }],
                }],
            },
            saved_preferences=[
                {
                    "category": "dietary",
                    "value": "vegetarian",
                },
                {
                    "category": "budget",
                    "min_amount": 30000,
                    "max_amount": 60000,
                    "currency": "INR",
                    "budget_basis": "total_trip",
                },
            ],
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.duration_days, 3)
        self.assertEqual(
            [day.date for day in result.days],
            ["2027-05-01", "2027-05-02", "2027-05-03"],
        )
        self.assertIn("AI-generated suggestions", result.notice)
        self.assertIn("not a booked or selected flight", result.notice)
        request_context = json.loads(
            client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        )
        self.assertEqual(request_context["travelers"], 2)
        self.assertEqual(request_context["budget"]["amount"], 50000)
        self.assertEqual(request_context["flight_timing"]["arrival"]["time"], "12:30")
        self.assertEqual(
            request_context["flight_timing"]["return_departure"]["time"],
            "17:30",
        )
        self.assertEqual(
            request_context["flight_options"][0]["paired_return_options"][0]["option_id"],
            "return-1",
        )
        self.assertEqual(request_context["hotel_options"][0]["name"], "Coast Stay")
        self.assertEqual(
            request_context["budget"]["saved_budget_preferences"][0]["budget_basis"],
            "total_trip",
        )
        self.assertEqual(request_context["weather"]["source"], "Open-Meteo")
        self.assertEqual(
            request_context["weather"]["days"][0]["condition"],
            "Moderate rain",
        )
        self.assertIn("forecasts are time-sensitive", result.notice)

    async def test_rejects_activity_before_destination_arrival(self):
        invalid_days = make_days()
        invalid_days[0]["activities"][0]["start_time"] = "11:00"
        invalid_days[0]["activities"][0]["end_time"] = "12:00"
        agent, _ = configured_itinerary_agent({
            "summary": "Activity before arrival",
            "days": invalid_days,
        })
        transport = TransportResult(
            status="success",
            is_mock=False,
            options=[
                TransportOption(
                    option_id="flight-1",
                    provider="Air",
                    departure="out",
                    arrival="in",
                    duration="4h",
                    stops=0,
                    outbound_leg=FlightLeg(
                        provider="Air",
                        arrival_time="2027-05-01T12:30",
                    ),
                    return_leg=FlightLeg(
                        provider="Air",
                        departure_time="2027-05-03T17:30",
                    ),
                )
            ],
        )

        result = await agent.execute(make_plan(), transport_results=transport)

        self.assertEqual(result.status, "error")
        self.assertIn("invalid schedule", result.message)

    async def test_rejects_activity_after_return_departure(self):
        invalid_days = make_days()
        invalid_days[2]["activities"][1]["end_time"] = "18:00"
        agent, _ = configured_itinerary_agent({
            "summary": "Activity after departure",
            "days": invalid_days,
        })
        transport = TransportResult(
            status="success",
            is_mock=False,
            options=[
                TransportOption(
                    option_id="flight-1",
                    provider="Air",
                    departure="out",
                    arrival="in",
                    duration="4h",
                    stops=0,
                    outbound_leg=FlightLeg(
                        provider="Air",
                        arrival_time="2027-05-01T12:30",
                    ),
                    return_leg=FlightLeg(
                        provider="Air",
                        departure_time="2027-05-03T17:30",
                    ),
                )
            ],
        )

        result = await agent.execute(make_plan(), transport_results=transport)

        self.assertEqual(result.status, "error")
        self.assertIn("invalid schedule", result.message)

    async def test_rejects_missing_days_duplicate_activities_and_overlaps(self):
        missing_day_agent, _ = configured_itinerary_agent({
            "summary": "Missing a day",
            "days": make_days()[:2],
        })
        duplicate_days = make_days()
        duplicate_days[1]["activities"][0]["title"] = "Explore the waterfront"
        duplicate_agent, _ = configured_itinerary_agent({
            "summary": "Duplicate activity",
            "days": duplicate_days,
        })
        overlapping_days = make_days()
        overlapping_days[0]["activities"][1]["start_time"] = "13:30"
        overlapping_days[0]["activities"][1]["end_time"] = "15:30"
        overlap_agent, _ = configured_itinerary_agent({
            "summary": "Overlapping activities",
            "days": overlapping_days,
        })

        missing_day = await missing_day_agent.execute(make_plan())
        duplicate = await duplicate_agent.execute(make_plan())
        overlap = await overlap_agent.execute(make_plan())

        for result in (missing_day, duplicate, overlap):
            self.assertEqual(result.status, "error")
            self.assertIn("invalid schedule", result.message)

    async def test_returns_useful_errors_for_missing_invalid_dates_and_duration(self):
        agent, client = configured_itinerary_agent({
            "summary": "Never called",
            "days": [],
        })
        missing = await agent.execute(make_plan(destination=None))
        bad_date = await agent.execute(make_plan(dates=Dates(start="2027-02-30")))
        reversed_dates = await agent.execute(
            make_plan(dates=Dates(start="2027-05-03", end="2027-05-01"))
        )
        missing_end = await agent.execute(
            make_plan(dates=Dates(start="2027-05-01"), duration_days=None)
        )

        self.assertEqual(missing.status, "needs_information")
        self.assertIn("destination", missing.missing_information)
        self.assertEqual(bad_date.status, "error")
        self.assertIn("on or after", reversed_dates.message)
        self.assertEqual(missing_end.status, "needs_information")
        self.assertIn("end_date_or_duration", missing_end.missing_information)
        client.chat.completions.create.assert_not_called()

    async def test_derives_end_date_from_duration_and_rejects_unsupported_duration(self):
        days = make_days()[:2]
        days[1]["date"] = "2027-05-02"
        agent, _ = configured_itinerary_agent({
            "summary": "Two-day trip",
            "days": days,
        })

        result = await agent.execute(
            make_plan(dates=Dates(start="2027-05-01"), duration_days=2)
        )
        too_long = await agent.execute(
            make_plan(
                dates=Dates(start="2027-05-01", end="2027-07-01"),
                duration_days=62,
            )
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.duration_days, 2)
        self.assertEqual(too_long.status, "error")
        self.assertIn("limited to 30 days", too_long.message)

    async def test_rejects_duration_conflicting_with_trip_dates(self):
        agent, client = configured_itinerary_agent({
            "summary": "Should not be called",
            "days": make_days(),
        })

        result = await agent.execute(make_plan(duration_days=4))

        self.assertEqual(result.status, "error")
        self.assertIn("does not match", result.message)
        client.chat.completions.create.assert_not_called()

    async def test_reports_malformed_model_response_and_provider_failure(self):
        malformed_agent, _ = configured_itinerary_agent({"days": "not a list"})
        failure_agent, _ = configured_itinerary_agent({"days": []})
        failure_agent.client.chat.completions.create.side_effect = RuntimeError(
            "provider request failed"
        )
        invalid_json_agent, _ = configured_itinerary_agent({})
        invalid_json_agent.client.chat.completions.create.return_value = (
            make_completion("not json")
        )

        malformed = await malformed_agent.execute(make_plan())
        provider_failure = await failure_agent.execute(make_plan())
        invalid_json = await invalid_json_agent.execute(make_plan())

        self.assertEqual(malformed.status, "error")
        self.assertEqual(provider_failure.status, "error")
        self.assertIn("could not be generated", provider_failure.message)
        self.assertEqual(invalid_json.status, "error")

    async def test_mock_flight_times_are_not_used_as_itinerary_constraints(self):
        agent, client = configured_itinerary_agent({
            "summary": "A relaxed three-day plan.",
            "days": make_days(),
        })
        transport = TransportResult(
            status="success",
            is_mock=True,
            options=[
                TransportOption(
                    option_id="mock-flight",
                    provider="Mock",
                    departure="2027-05-01 08:00",
                    arrival="2027-05-01 12:00",
                    duration="4h",
                    stops=0,
                    outbound_leg=FlightLeg(
                        provider="Mock",
                        arrival_time="2027-05-01T12:30",
                    ),
                )
            ],
        )

        result = await agent.execute(make_plan(), transport_results=transport)

        request_context = json.loads(
            client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(request_context["flight_timing"]["status"], "mock")
        self.assertIsNone(request_context["flight_timing"]["arrival"])
        self.assertIn("mock data", result.notice)

    async def test_fixture_hotel_prices_are_not_passed_as_live_budget_data(self):
        agent, client = configured_itinerary_agent({
            "summary": "A relaxed three-day plan.",
            "days": make_days(),
        })
        hotels = HotelResult(
            status="success",
            is_sandbox=True,
            options=[
                HotelOption(
                    option_id="fixture-hotel",
                    name="Sample Stay",
                    address="Goa",
                    nightly_price=1000,
                    total_price=2000,
                    currency="INR",
                    is_fixture_price=True,
                    is_sandbox=True,
                )
            ],
        )

        result = await agent.execute(make_plan(), hotel_results=hotels)

        context = json.loads(
            client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        )
        self.assertEqual(result.status, "success")
        self.assertTrue(context["hotel_options"][0]["is_fixture_price"])
        self.assertTrue(context["hotel_options"][0]["is_sandbox"])
        self.assertNotIn("total_price", context["hotel_options"][0])
        self.assertNotIn("nightly_price", context["hotel_options"][0])

    async def test_requires_model_configuration_instead_of_fabricating_itinerary(self):
        result = await ItineraryAgent().execute(make_plan())

        self.assertEqual(result.status, "error")
        self.assertIn("not configured", result.message)


class MasterAgentItineraryRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_mock_plan_routes_itinerary_without_unrequested_search_tasks(self):
        agent = MasterAgent()

        plan = agent._mock_response(
            "Create a day-by-day itinerary for Goa from Delhi "
            "from May 1, 2027 to May 3, 2027 for 2 adults"
        )

        self.assertEqual(plan.tasks, ["create_itinerary"])
        self.assertEqual(plan.duration_days, 3)

    async def test_routes_day_by_day_request_and_passes_provider_results(self):
        agent = MasterAgent()
        agent.client = None
        plan = make_plan(tasks=["find_transport", "find_hotel"])
        agent._mock_response = Mock(return_value=plan)
        transport = TransportResult(status="success")
        hotels = HotelResult(status="success")
        itinerary = ItineraryResult(
            status="success",
            destination="Goa",
            duration_days=3,
            days=[],
        )
        agent.travel_agent.execute = AsyncMock(return_value=transport)
        agent.hotel_agent.execute = AsyncMock(return_value=hotels)
        weather = {
            "status": "unavailable",
            "destination": "Goa",
            "message": "Requested dates are outside the available forecast.",
            "days": [],
        }
        agent.weather_service.get_trip_weather = AsyncMock(return_value=weather)
        agent.itinerary_agent.execute = AsyncMock(return_value=itinerary)
        status_callback = AsyncMock()

        result = await agent.process_request(
            "Create a day-by-day itinerary for Goa from Delhi",
            status_callback=status_callback,
            saved_preferences=[{"category": "dietary", "value": "vegetarian"}],
        )

        agent.itinerary_agent.execute.assert_awaited_once_with(
            plan,
            transport_results=transport.model_dump(),
            hotel_results=hotels.model_dump(),
            saved_preferences=[{"category": "dietary", "value": "vegetarian"}],
            weather_results=weather,
        )
        agent.weather_service.get_trip_weather.assert_awaited_once_with(
            destination="Goa",
            start_date="2027-05-01",
            end_date="2027-05-03",
            duration_days=3,
        )
        self.assertIn("create_itinerary", result["plan"]["tasks"])
        self.assertEqual(result["itinerary_results"]["status"], "success")
        self.assertIn(
            unittest.mock.call("itinerary_agent", "generating"),
            status_callback.await_args_list,
        )

    def test_authenticated_websocket_returns_complete_multi_day_itinerary(self):
        from websocket import router as websocket_router

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as db:
            user = User(
                name="Itinerary Traveler",
                email="itinerary@example.test",
                password_hash="unused",
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        app = FastAPI()
        app.include_router(websocket_router)

        def override_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        master = MasterAgent()
        master.client = None
        master.weather_service.get_trip_weather = AsyncMock(return_value={
            "status": "unavailable",
            "destination": "Goa",
            "location": None,
            "timezone": None,
            "current": None,
            "days": [],
            "message": "Dates are beyond the available forecast range.",
            "source": "Open-Meteo",
        })
        master._mock_response = Mock(return_value=make_plan(tasks=[]))
        generated = {
            "summary": "A relaxed three-day plan.",
            "days": make_days(),
        }
        master.itinerary_agent = configured_itinerary_agent(generated)[0]
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-jwt-signing-key-for-itinerary"}):
            token = create_access_token({"sub": user.email})

        try:
            with patch("websocket.master_agent", master):
                with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-jwt-signing-key-for-itinerary"}):
                    with TestClient(app) as client:
                        with client.websocket_connect(
                            "/ws",
                            subprotocols=["travelagent", f"travelagent.auth.{token}"],
                        ) as websocket:
                            websocket.send_json({
                                "type": "message",
                                "message": "Create a day-by-day itinerary for Goa",
                            })
                            responses = []
                            while True:
                                response = websocket.receive_json()
                                responses.append(response)
                                if response.get("type") == "plan":
                                    break

            plan_response = responses[-1]["data"]
            self.assertIsInstance(plan_response["trip_id"], int)
            self.assertIn("replanning", plan_response)
            self.assertEqual(plan_response["weather_results"]["status"], "unavailable")
            itinerary = plan_response["itinerary_results"]
            self.assertEqual(itinerary["status"], "success")
            self.assertEqual(itinerary["duration_days"], 3)
            self.assertEqual(
                [day["date"] for day in itinerary["days"]],
                ["2027-05-01", "2027-05-02", "2027-05-03"],
            )
            self.assertTrue(any(
                response.get("type") == "agent_status"
                and response.get("agent") == "itinerary_agent"
                for response in responses
            ))
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

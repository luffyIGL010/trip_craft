import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
        self.assertEqual(request_context["hotel_options"][0]["name"], "Coast Stay")
        self.assertEqual(
            request_context["budget"]["saved_budget_preferences"][0]["budget_basis"],
            "total_trip",
        )

    async def test_rejects_activity_before_arrival_and_after_return_departure(self):
        invalid_days = make_days()
        invalid_days[0]["activities"][0]["start_time"] = "11:00"
        invalid_days[0]["activities"][0]["end_time"] = "12:00"
        invalid_days[2]["activities"][1]["end_time"] = "18:00"
        agent, _ = configured_itinerary_agent({
            "summary": "Invalid schedule",
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
        invalid_days = make_days()
        invalid_days[1]["activities"][0]["title"] = "Explore the waterfront"
        invalid_days[1]["activities"][1]["start_time"] = "10:30"
        invalid_days[1]["activities"][1]["end_time"] = "11:30"
        agent, _ = configured_itinerary_agent({
            "summary": "Invalid schedule",
            "days": invalid_days[:2],
        })

        result = await agent.execute(make_plan())

        self.assertEqual(result.status, "error")
        self.assertIn("invalid schedule", result.message)

    async def test_returns_useful_errors_for_missing_invalid_dates_and_duration(self):
        agent, client = configured_itinerary_agent({
            "summary": "Never called",
            "days": [],
        })
        missing = await agent.execute(make_plan(destination=None))
        bad_date = await agent.execute(make_plan(dates=Dates(start="2027-02-30")))
        missing_end = await agent.execute(
            make_plan(dates=Dates(start="2027-05-01"), duration_days=None)
        )

        self.assertEqual(missing.status, "needs_information")
        self.assertIn("destination", missing.missing_information)
        self.assertEqual(bad_date.status, "error")
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

    async def test_requires_model_configuration_instead_of_fabricating_itinerary(self):
        result = await ItineraryAgent().execute(make_plan())

        self.assertEqual(result.status, "error")
        self.assertIn("not configured", result.message)


class MasterAgentItineraryRoutingTests(unittest.IsolatedAsyncioTestCase):
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
        )
        self.assertIn("create_itinerary", result["plan"]["tasks"])
        self.assertEqual(result["itinerary_results"]["status"], "success")
        self.assertIn(
            unittest.mock.call("itinerary_agent", "generating"),
            status_callback.await_args_list,
        )


if __name__ == "__main__":
    unittest.main()

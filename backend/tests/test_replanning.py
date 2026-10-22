import json
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.auth import create_access_token
from agents.master_agent import MasterAgent
from agents.schemas import Budget, Dates, MasterAgentResponse
from database.database import Base, get_db
from database.models import TripPlan, User
from schemas.itinerary import ItineraryResult
from services.replanning import (
    ReplanningService,
    max_replan_attempts,
    request_key,
)


def _estimate(total, confidence="verified", flight="Flight A", hotel="Hotel A"):
    return {
        "status": "available",
        "combined_total": total,
        "price_confidence": confidence,
        "currency": "INR",
        "flight_option": flight,
        "hotel_option": hotel,
        "price_warning": "Scope unverified" if confidence != "verified" else None,
    }


def _state(total=15000, dates=None, activities=None):
    dates = dates or {"start": "2027-05-01", "end": "2027-05-03"}
    itinerary = {
        "status": "success",
        "summary": "Existing itinerary",
        "days": [
            {
                "date": "2027-05-01",
                "activities": activities or [
                    {
                        "start_time": "10:00",
                        "end_time": "11:00",
                        "title": "Museum",
                    }
                ],
            }
        ],
    }
    return {
        "plan": {
            "request_summary": "Singapore trip",
            "destination": "Singapore",
            "origin": "Delhi",
            "dates": dates,
            "travelers": 2,
            "tasks": ["find_transport", "find_hotel", "create_itinerary"],
            "budget": {"amount": None, "currency": "INR"},
        },
        "transport_results": {
            "status": "success",
            "is_mock": False,
            "options": [
                {
                    "outbound_leg": {
                        "arrival_time": "2027-05-01T09:00:00+08:00",
                    }
                }
            ],
        },
        "hotel_results": {
            "status": "success",
            "check_in": dates["start"],
            "check_out": dates["end"],
        },
        "itinerary_results": itinerary,
        "combined_trip_cost": {
            "estimates": [_estimate(total)],
        },
    }


class ReplanningAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.budget = [{
            "category": "budget",
            "budget_basis": "total_trip",
            "currency": "INR",
            "max_amount": 20000,
        }]

    def test_budget_conflict_and_cheaper_verified_alternative(self):
        original = _state(total=30000)
        candidate = _state(total=18000)
        candidate["combined_trip_cost"]["estimates"].append(
            _estimate(18000, flight="Flight B", hotel="Hotel B")
        )

        conflicts = ReplanningService.detect_conflicts(original, self.budget)
        proposal = ReplanningService.prepare_proposal(
            original, candidate, "Replan this trip to reduce cost", self.budget
        )

        self.assertEqual(conflicts[0]["code"], "budget_exceeded")
        self.assertEqual(proposal["status"], "awaiting_approval")
        self.assertEqual(proposal["alternatives"][0]["combined_total"], 18000)
        self.assertEqual(proposal["changes"][0]["field"], "flight_hotel_recommendation")

    def test_no_suitable_alternatives_returns_unresolved_conflict(self):
        original = _state(total=30000)
        candidate = _state(total=32000)
        proposal = ReplanningService.prepare_proposal(
            original, candidate, "Replan this trip to reduce cost", self.budget
        )

        self.assertEqual(proposal["status"], "unresolved")
        self.assertEqual(proposal["alternatives"], [])
        self.assertEqual(proposal["unresolved_conflicts"][0]["code"], "budget_exceeded")

    def test_activity_overlap_and_flight_arrival_conflicts_are_detected(self):
        state = _state(activities=[
            {"start_time": "08:00", "end_time": "09:30", "title": "Breakfast"},
            {"start_time": "09:00", "end_time": "10:00", "title": "Museum"},
        ])

        conflicts = ReplanningService.detect_conflicts(state)

        self.assertEqual(
            {item["code"] for item in conflicts},
            {"activity_overlap", "activity_before_arrival"},
        )

    def test_return_departure_conflict_is_detected_only_for_paired_provider_data(self):
        state = _state(activities=[
            {"start_time": "16:00", "end_time": "18:00", "title": "Late activity"},
        ])
        state["transport_results"]["options"][0]["option_id"] = "outbound-1"
        state["transport_results"]["return_options"] = [{
            "paired_outbound_option_id": "outbound-1",
            "outbound_leg": {"departure_time": "2027-05-01T17:00:00+08:00"},
        }]

        conflicts = ReplanningService.detect_conflicts(state)

        self.assertIn("activity_after_departure", {item["code"] for item in conflicts})

    def test_missing_failed_search_and_hotel_date_mismatch_are_reported(self):
        state = _state()
        state["hotel_results"] = {
            "status": "error",
            "message": "Provider unavailable",
        }
        state["transport_results"] = None

        conflicts = ReplanningService.detect_conflicts(state)

        self.assertIn("flight_search_unavailable", {item["code"] for item in conflicts})
        self.assertIn("hotel_search_unavailable", {item["code"] for item in conflicts})
        self.assertTrue(any("Provider unavailable" in item["message"] for item in conflicts))

        state["transport_results"] = {"status": "success", "is_mock": True}
        state["hotel_results"] = {
            "status": "success",
            "check_in": "2027-05-02",
            "check_out": "2027-05-04",
        }
        conflicts = ReplanningService.detect_conflicts(state)
        self.assertIn(
            "hotel_checkin_mismatch",
            {item["code"] for item in conflicts},
        )

    def test_unverified_fares_are_not_used_for_budget_comparison(self):
        state = _state(total=100000)
        state["combined_trip_cost"]["estimates"][0]["price_confidence"] = "provisional"
        conflicts = ReplanningService.detect_conflicts(state, self.budget)
        self.assertNotIn("budget_exceeded", {item["code"] for item in conflicts})

    def test_attempt_limit_is_configurable_and_bounded(self):
        with patch.dict(os.environ, {"TRIP_REPLAN_MAX_ATTEMPTS": "2"}):
            self.assertEqual(max_replan_attempts(), 2)
        with patch.dict(os.environ, {"TRIP_REPLAN_MAX_ATTEMPTS": "11"}):
            with self.assertRaisesRegex(ValueError, "between 1 and 10"):
                max_replan_attempts()

    def test_dates_and_itinerary_updates_remain_proposals_until_decision(self):
        original = _state()
        candidate = _state(dates={"start": "2027-06-01", "end": "2027-06-03"})
        candidate["itinerary_results"]["summary"] = "Updated itinerary"
        analysis = ReplanningService.prepare_proposal(
            original, candidate, "Change the dates"
        )

        self.assertEqual(analysis["status"], "awaiting_approval")
        self.assertIn(
            "travel_dates",
            {item["field"] for item in analysis["changes"]},
        )
        self.assertIn(
            "itinerary",
            {item["field"] for item in analysis["changes"]},
        )
        self.assertIn("No flight or hotel is booked", analysis["booking_notice"])


class ReplanningPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        with self.session_factory() as db:
            self.owner = User(name="Owner", email="owner@example.test", password_hash="x")
            self.other = User(name="Other", email="other@example.test", password_hash="x")
            db.add_all([self.owner, self.other])
            db.commit()
            db.refresh(self.owner)
            db.refresh(self.other)

    def tearDown(self):
        self.engine.dispose()

    def test_approval_persists_proposed_plan_and_rejection_keeps_original(self):
        with self.session_factory() as db:
            original = _state()
            candidate = _state(dates={"start": "2027-06-01", "end": "2027-06-03"})
            analysis = ReplanningService.prepare_proposal(original, candidate, "Change dates")
            approved_trip = ReplanningService.save_initial_plan(db, self.owner.id, original)
            approved = ReplanningService.save_proposal(
                db,
                approved_trip,
                self.owner.id,
                "approved-key",
                original,
                candidate,
                analysis,
                {},
            )
            rejected_trip = ReplanningService.save_initial_plan(db, self.owner.id, original)
            rejected = ReplanningService.save_proposal(
                db,
                rejected_trip,
                self.owner.id,
                "rejected-key",
                original,
                candidate,
                analysis,
                {},
            )
            self.assertEqual(
                ReplanningService.decide(db, self.owner.id, approved.id, "approved")["status"],
                "approved",
            )
            self.assertEqual(
                ReplanningService.decide(db, self.owner.id, rejected.id, "rejected")["status"],
                "rejected",
            )
            db.refresh(approved_trip)
            self.assertEqual(
                json.loads(approved_trip.state_json)["plan"]["dates"]["start"],
                "2027-06-01",
            )
            db.refresh(rejected_trip)
            self.assertEqual(
                json.loads(rejected_trip.state_json)["plan"]["dates"]["start"],
                "2027-05-01",
            )
            self.assertEqual(
                ReplanningService.decide(db, self.owner.id, approved.id, "approved")["status"],
                "approved",
            )

    def test_proposal_reads_and_updates_are_owner_scoped(self):
        with self.session_factory() as db:
            original = _state()
            trip = ReplanningService.save_initial_plan(db, self.owner.id, original)
            candidate = _state(dates={"start": "2027-06-01", "end": "2027-06-03"})
            proposal = ReplanningService.save_proposal(
                db,
                trip,
                self.owner.id,
                "owner-key",
                original,
                candidate,
                {},
                {},
            )
            with self.assertRaisesRegex(LookupError, "not found"):
                ReplanningService.decide(db, self.other.id, proposal.id, "approved")
            db.refresh(trip)
            self.assertEqual(json.loads(trip.state_json)["plan"]["dates"]["start"], "2027-05-01")

    def test_request_key_is_stable_for_retries(self):
        first = request_key(7, 12, "Replan this trip")
        second = request_key(7, 12, "  REPLAN   THIS TRIP ")
        self.assertEqual(first, second)


class ReplanningOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_identical_search_inputs_reuse_successful_and_failed_results(self):
        for search_status in ("success", "error"):
            with self.subTest(search_status=search_status):
                agent = MasterAgent()
                previous = _state()
                previous["plan"]["tasks"] = [
                    "find_transport",
                    "find_hotel",
                ]
                previous["transport_results"] = {
                    "status": search_status,
                    "origin": "Delhi",
                    "destination": "Singapore",
                    "outbound_date": "2027-05-01",
                    "return_date": "2027-05-03",
                    "travelers": 2,
                    "trip_type": "round_trip",
                    "options": [],
                    "return_options": [],
                    "is_mock": False,
                    "message": "Existing result",
                }
                previous["hotel_results"] = {
                    "status": search_status,
                    "destination": "Singapore",
                    "check_in": "2027-05-01",
                    "check_out": "2027-05-03",
                    "adults": 2,
                    "options": [],
                    "message": "Existing hotel result",
                }
                agent._mock_response = Mock(return_value=MasterAgentResponse(
                    request_summary="Replan request",
                    destination="Singapore",
                    origin="Delhi",
                    dates=Dates(start="2027-05-01", end="2027-05-03"),
                    travelers=2,
                    budget=Budget(),
                    tasks=[],
                ))
                agent.travel_agent.execute = AsyncMock()
                agent.hotel_agent.execute = AsyncMock()
                agent.itinerary_agent.execute = AsyncMock(
                    return_value=ItineraryResult(status="error")
                )

                result = await agent.process_request(
                    "Replan my Singapore trip",
                    current_trip=previous,
                )

                agent.travel_agent.execute.assert_not_awaited()
                agent.hotel_agent.execute.assert_not_awaited()
                self.assertEqual(result["transport_results"]["status"], search_status)
                self.assertEqual(result["hotel_results"]["status"], search_status)

    def test_mock_parser_tracks_only_explicit_budget_basis(self):
        agent = MasterAgent()
        per_person = agent._mock_response(
            "Plan my trip with INR 50,000 per person"
        )
        unclear = agent._mock_response("Plan my trip with INR 50,000 budget")
        self.assertEqual(per_person.budget.basis, "per_person")
        self.assertIsNone(unclear.budget.basis)


class ReplanningWebSocketTests(unittest.TestCase):
    def test_websocket_keeps_plan_response_and_approval_flow(self):
        from websocket import router as websocket_router

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as db:
            user = User(name="WebSocket User", email="ws@example.test", password_hash="x")
            db.add(user)
            db.commit()
            db.refresh(user)

        app = FastAPI()
        app.include_router(websocket_router)

        def override_db():
            with session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        original = _state()
        candidate = _state(dates={"start": "2027-06-01", "end": "2027-06-03"})
        agent = AsyncMock()
        agent.process_request = AsyncMock(side_effect=[original, candidate])
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-replanning-key"}):
            token = create_access_token({"sub": user.email})

        try:
            with patch("websocket.master_agent", agent):
                with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-replanning-key"}):
                    with TestClient(app) as client:
                        with client.websocket_connect(
                            "/ws",
                            subprotocols=["travelagent", f"travelagent.auth.{token}"],
                        ) as websocket:
                            websocket.send_json({"type": "message", "message": "Plan Singapore"})
                            initial = websocket.receive_json()["data"]
                            trip_id = initial["trip_id"]
                            self.assertEqual(initial["plan"]["destination"], "Singapore")

                            websocket.send_json({
                                "type": "message",
                                "trip_id": trip_id,
                                "message": "Change my dates",
                            })
                            proposed = websocket.receive_json()["data"]
                            proposal_id = proposed["replanning"]["proposal_id"]
                            self.assertEqual(proposed["replanning"]["status"], "awaiting_approval")

                            websocket.send_json({
                                "type": "replan_decision",
                                "proposal_id": proposal_id,
                                "decision": "approve",
                            })
                            decision = websocket.receive_json()
                            self.assertEqual(decision["type"], "replan_decision")
                            self.assertEqual(decision["data"]["status"], "approved")

            with session_factory() as db:
                trip = db.query(TripPlan).filter_by(id=trip_id, user_id=user.id).one()
                self.assertEqual(
                    json.loads(trip.state_json)["plan"]["dates"]["start"],
                    "2027-06-01",
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

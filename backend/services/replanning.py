import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import TripPlan, TripPlanChange


REPLAN_REQUEST_PATTERN = re.compile(
    r"\b(replan|change|update|adjust|modify|reschedule|move|replace|"
    r"make (?:it|the trip) cheaper|find (?:a )?cheaper)\b",
    re.IGNORECASE,
)


def max_replan_attempts() -> int:
    value = os.getenv("TRIP_REPLAN_MAX_ATTEMPTS", "3")
    try:
        limit = int(value)
    except ValueError as exc:
        raise ValueError("TRIP_REPLAN_MAX_ATTEMPTS must be a positive integer") from exc
    if limit < 1 or limit > 10:
        raise ValueError("TRIP_REPLAN_MAX_ATTEMPTS must be between 1 and 10")
    return limit


def request_key(user_id: int, trip_id: int, message: str) -> str:
    normalized = re.sub(r"\s+", " ", message.strip()).casefold()
    value = f"{user_id}:{trip_id}:{normalized}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _field(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _parse_time(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        return None
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


class ReplanningService:
    """Detects supported conflicts and produces approval-gated plan candidates."""

    @staticmethod
    def is_replan_request(message: str) -> bool:
        return bool(REPLAN_REQUEST_PATTERN.search(message or ""))

    @classmethod
    def detect_conflicts(
        cls,
        state: dict,
        saved_preferences: Optional[list[dict]] = None,
    ) -> list[dict]:
        conflicts = []
        plan = state.get("plan") or {}
        dates = plan.get("dates") or {}
        transport = state.get("transport_results")
        hotels = state.get("hotel_results")
        itinerary = state.get("itinerary_results")
        tasks = set(plan.get("tasks") or [])

        if "find_transport" in tasks and (
            not transport or transport.get("status") != "success"
        ):
            conflicts.append({
                "code": "flight_search_unavailable",
                "message": (
                    transport.get("message")
                    if isinstance(transport, dict) and transport.get("message")
                    else "Flight search results are missing or unsuccessful."
                ),
            })
        if {"find_hotel", "find_hotels"} & tasks and (
            not hotels or hotels.get("status") != "success"
        ):
            conflicts.append({
                "code": "hotel_search_unavailable",
                "message": (
                    hotels.get("message")
                    if isinstance(hotels, dict) and hotels.get("message")
                    else "Hotel search results are missing or unsuccessful."
                ),
            })

        if hotels and hotels.get("status") == "success":
            if dates.get("start") and hotels.get("check_in") and dates["start"] != hotels["check_in"]:
                conflicts.append({
                    "code": "hotel_checkin_mismatch",
                    "message": "Hotel check-in does not match the trip start date.",
                })
            if dates.get("end") and hotels.get("check_out") and dates["end"] != hotels["check_out"]:
                conflicts.append({
                    "code": "hotel_checkout_mismatch",
                    "message": "Hotel check-out does not match the trip end date.",
                })

        if itinerary and itinerary.get("status") == "success":
            for day in itinerary.get("days") or []:
                activities = day.get("activities") or []
                valid_times = [
                    (_parse_time(activity.get("start_time")),
                     _parse_time(activity.get("end_time")), activity)
                    for activity in activities
                ]
                valid_times = [
                    item for item in valid_times
                    if item[0] is not None and item[1] is not None
                ]
                valid_times.sort(key=lambda item: item[0])
                for previous, current in zip(valid_times, valid_times[1:]):
                    if current[0] < previous[1]:
                        conflicts.append({
                            "code": "activity_overlap",
                            "date": day.get("date"),
                            "message": (
                                f"Activities '{previous[2].get('title', 'activity')}' and "
                                f"'{current[2].get('title', 'activity')}' overlap."
                            ),
                        })
                        break

            cls._detect_flight_timing_conflict(itinerary, transport, conflicts)

        cls._detect_budget_conflict(state, saved_preferences or [], conflicts)
        return conflicts

    @staticmethod
    def _detect_flight_timing_conflict(itinerary, transport, conflicts):
        if not transport or transport.get("status") != "success" or transport.get("is_mock"):
            return
        options = transport.get("options") or []
        if not options:
            return
        outbound_leg = _field(options[0], "outbound_leg")
        arrival_raw = _field(outbound_leg, "arrival_time")
        try:
            arrival = datetime.fromisoformat(str(arrival_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            arrival = None
        if arrival is None:
            return

        arrival_minutes = arrival.hour * 60 + arrival.minute
        arrival_date = arrival.date().isoformat()
        for day in itinerary.get("days") or []:
            if day.get("date") != arrival_date:
                continue
            for activity in day.get("activities") or []:
                start = _parse_time(activity.get("start_time"))
                if start is not None and start < arrival_minutes:
                    conflicts.append({
                        "code": "activity_before_arrival",
                        "date": arrival_date,
                        "message": (
                            f"'{activity.get('title', 'An activity')}' starts before the "
                            "available flight's arrival time."
                        ),
                    })
                    return

        outbound_id = _field(options[0], "option_id")
        return_leg = _field(options[0], "return_leg")
        if return_leg is None:
            paired_return = next(
                (
                    option for option in transport.get("return_options", []) or []
                    if _field(option, "paired_outbound_option_id") == outbound_id
                ),
                None,
            )
            return_leg = _field(paired_return, "outbound_leg")
        departure_raw = _field(return_leg, "departure_time")
        try:
            departure = datetime.fromisoformat(str(departure_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            departure = None
        if departure is None:
            return
        departure_minutes = departure.hour * 60 + departure.minute
        departure_date = departure.date().isoformat()
        for day in itinerary.get("days") or []:
            if day.get("date") != departure_date:
                continue
            for activity in day.get("activities") or []:
                end = _parse_time(activity.get("end_time"))
                if end is not None and end > departure_minutes:
                    conflicts.append({
                        "code": "activity_after_departure",
                        "date": departure_date,
                        "message": (
                            f"'{activity.get('title', 'An activity')}' ends after the "
                            "available return flight's departure time."
                        ),
                    })
                    return

    @staticmethod
    def _detect_budget_conflict(state, saved_preferences, conflicts):
        preference = ReplanningService._budget_for_state(state, saved_preferences)
        if not preference:
            return
        estimates = (state.get("combined_trip_cost") or {}).get("estimates") or []
        verified_totals = [
            estimate.get("combined_total")
            for estimate in estimates
            if estimate.get("status") == "available"
            and estimate.get("price_confidence") == "verified"
            and isinstance(estimate.get("combined_total"), (int, float))
        ]
        if not verified_totals:
            return
        travelers = max(1, int((state.get("plan") or {}).get("travelers") or 1))
        maximum = float(preference["max_amount"])
        basis = preference["budget_basis"]
        comparable = [
            amount / travelers if basis == "per_person" else amount
            for amount in verified_totals
        ]
        if min(comparable) > maximum:
            conflicts.append({
                "code": "budget_exceeded",
                "message": (
                    f"The lowest verified flight-and-hotel estimate exceeds the saved "
                    f"{basis.replace('_', ' ')} budget of INR {maximum:,.0f}."
                ),
            })

    @classmethod
    def prepare_proposal(
        cls,
        original: dict,
        candidate: dict,
        message: str,
        saved_preferences: Optional[list[dict]] = None,
    ) -> dict:
        conflicts = cls.detect_conflicts(original, saved_preferences)
        candidate_conflicts = cls.detect_conflicts(candidate, saved_preferences)
        changes = cls._describe_changes(original, candidate, message)
        alternatives = cls._budget_alternatives(candidate, saved_preferences or [])
        if (
            any(item["code"] == "budget_exceeded" for item in conflicts)
            and alternatives
        ):
            recommended = alternatives[0]
            old_total = cls._lowest_verified_total(original)
            new_total = recommended.get("combined_total")
            changes.append({
                "field": "flight_hotel_recommendation",
                "original": "No verified available combination within the saved budget.",
                "proposed": {
                    "flight_option": recommended.get("flight_option"),
                    "hotel_option": recommended.get("hotel_option"),
                    "combined_total": new_total,
                    "currency": recommended.get("currency"),
                    "confidence": recommended.get("confidence"),
                },
                "reason": "This available search combination fits the saved budget.",
                "price_difference": (
                    new_total - old_total
                    if old_total is not None and new_total is not None
                    else None
                ),
                "currency": "INR",
                "notice": "Recommendation only; neither option is selected or booked.",
            })
        unresolved = candidate_conflicts

        if conflicts and not changes:
            return {
                "status": "unresolved",
                "conflicts": conflicts,
                "changes": [],
                "alternatives": alternatives,
                "unresolved_conflicts": unresolved or conflicts,
                "message": "No validated change was found; the existing trip snapshot was left unchanged.",
            }
        if not changes:
            return {
                "status": "no_change",
                "conflicts": conflicts,
                "changes": [],
                "alternatives": alternatives,
                "unresolved_conflicts": unresolved,
                "message": "No material change was found in the candidate plan.",
            }
        return {
            "status": "awaiting_approval",
            "conflicts": conflicts,
            "changes": changes,
            "alternatives": alternatives,
            "unresolved_conflicts": unresolved,
            "message": "A candidate plan is ready. It will not replace the current plan unless you approve it.",
            "booking_notice": (
                "Approval updates this saved planning snapshot only. No flight or hotel "
                "is booked, rebooked, or paid for."
            ),
        }

    @staticmethod
    def _budget_alternatives(candidate: dict, saved_preferences: list[dict]) -> list[dict]:
        budget = ReplanningService._budget_for_state(candidate, saved_preferences)
        estimates = (candidate.get("combined_trip_cost") or {}).get("estimates") or []
        usable = [
            item for item in estimates
            if item.get("status") == "available"
            and isinstance(item.get("combined_total"), (int, float))
        ]
        usable.sort(key=lambda item: item["combined_total"])
        if not budget:
            return [
                {
                    "flight_option": item.get("flight_option"),
                    "hotel_option": item.get("hotel_option"),
                    "combined_total": item.get("combined_total"),
                    "currency": item.get("currency"),
                    "confidence": item.get("price_confidence", "provisional"),
                    "warning": item.get("price_warning"),
                }
                for item in usable[:3]
            ]
        travelers = max(1, int((candidate.get("plan") or {}).get("travelers") or 1))
        maximum = float(budget["max_amount"])
        factor = travelers if budget["budget_basis"] == "per_person" else 1
        return [
            {
                "flight_option": item.get("flight_option"),
                "hotel_option": item.get("hotel_option"),
                "combined_total": item.get("combined_total"),
                "currency": item.get("currency"),
                "confidence": item.get("price_confidence", "provisional"),
                "warning": item.get("price_warning"),
            }
            for item in usable
            if item.get("price_confidence") == "verified"
            and item["combined_total"] / factor <= maximum
        ][:3]

    @staticmethod
    def _budget_for_state(state: dict, saved_preferences: list[dict]) -> Optional[dict]:
        explicit = (state.get("plan") or {}).get("budget") or {}
        if (
            explicit.get("amount") is not None
            and explicit.get("basis") in {"total_trip", "per_person"}
            and (explicit.get("currency") or "").upper() == "INR"
        ):
            return {
                "budget_basis": explicit["basis"],
                "currency": explicit["currency"],
                "max_amount": explicit["amount"],
            }
        return next(
            (
                item for item in saved_preferences
                if item.get("category") == "budget"
                and item.get("budget_basis") in {"total_trip", "per_person"}
                and (item.get("currency") or "").upper() == "INR"
                and item.get("max_amount") is not None
            ),
            None,
        )

    @staticmethod
    def _describe_changes(original: dict, candidate: dict, message: str) -> list[dict]:
        changes = []
        old_plan = original.get("plan") or {}
        new_plan = candidate.get("plan") or {}
        old_dates = old_plan.get("dates") or {}
        new_dates = new_plan.get("dates") or {}
        if old_dates != new_dates:
            changes.append({
                "field": "travel_dates",
                "original": old_dates,
                "proposed": new_dates,
                "reason": "The candidate search and itinerary use the newly requested dates.",
                "price_difference": None,
            })

        old_itinerary = original.get("itinerary_results") or {}
        new_itinerary = candidate.get("itinerary_results") or {}
        if (
            new_itinerary.get("status") == "success"
            and old_itinerary != new_itinerary
        ):
            changes.append({
                "field": "itinerary",
                "original": old_itinerary.get("summary") or "Current suggested itinerary",
                "proposed": new_itinerary.get("summary") or "Updated suggested itinerary",
                "reason": "Regenerated with the current trip details and available search results.",
                "price_difference": None,
            })

        return changes

    @staticmethod
    def _lowest_verified_total(state: dict) -> Optional[float]:
        estimates = (state.get("combined_trip_cost") or {}).get("estimates") or []
        totals = [
            item.get("combined_total")
            for item in estimates
            if item.get("status") == "available"
            and item.get("price_confidence") == "verified"
            and isinstance(item.get("combined_total"), (int, float))
        ]
        return min(totals) if totals else None

    @staticmethod
    def save_initial_plan(db: Session, user_id: int, state: dict) -> TripPlan:
        trip = TripPlan(user_id=user_id, state_json=json.dumps(state))
        db.add(trip)
        db.commit()
        db.refresh(trip)
        return trip

    @staticmethod
    def reserve_attempt(
        db: Session,
        trip: TripPlan,
        user_id: int,
        key: str,
        original: dict,
    ) -> TripPlanChange:
        processing_response = {
            "trip_id": trip.id,
            "replanning": {
                "status": "processing",
                "message": "A re-planning attempt is in progress.",
            },
        }
        trip.replan_attempts += 1
        proposal = TripPlanChange(
            trip_id=trip.id,
            user_id=user_id,
            request_key=key,
            status="processing",
            original_json=json.dumps(original),
            proposed_json=json.dumps(original),
            response_json=json.dumps(processing_response),
            changes_json=json.dumps({"status": "processing"}),
        )
        db.add(proposal)
        db.commit()
        db.refresh(proposal)
        return proposal

    @staticmethod
    def complete_attempt(
        db: Session,
        proposal: TripPlanChange,
        candidate: dict,
        analysis: dict,
        response: dict,
        status: str,
    ) -> None:
        proposal.proposed_json = json.dumps(candidate)
        proposal.changes_json = json.dumps(analysis)
        proposal.response_json = json.dumps(response)
        proposal.status = status
        db.commit()

    @staticmethod
    def save_proposal(
        db: Session,
        trip: TripPlan,
        user_id: int,
        key: str,
        original: dict,
        candidate: dict,
        analysis: dict,
        response: dict,
        status: str = "pending",
    ) -> TripPlanChange:
        proposal = TripPlanChange(
            trip_id=trip.id,
            user_id=user_id,
            request_key=key,
            original_json=json.dumps(original),
            proposed_json=json.dumps(candidate),
            response_json=json.dumps(response),
            changes_json=json.dumps(analysis),
            status=status,
        )
        db.add(proposal)
        db.commit()
        db.refresh(proposal)
        return proposal

    @staticmethod
    def decide(
        db: Session,
        user_id: int,
        proposal_id: int,
        decision: str,
    ) -> dict:
        proposal = db.query(TripPlanChange).filter(
            TripPlanChange.id == proposal_id,
            TripPlanChange.user_id == user_id,
        ).first()
        if proposal is None:
            raise LookupError("Re-planning proposal not found.")
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decision must be approved or rejected.")
        if proposal.status != "pending":
            if proposal.status != decision:
                raise ValueError(f"This proposal was already {proposal.status}.")
            return {
                "proposal_id": proposal.id,
                "trip_id": proposal.trip_id,
                "status": proposal.status,
                "message": f"Proposal was already {proposal.status}.",
            }

        trip = db.query(TripPlan).filter(
            TripPlan.id == proposal.trip_id,
            TripPlan.user_id == user_id,
        ).first()
        if trip is None:
            raise LookupError("Trip plan not found.")
        if decision == "approved":
            trip.state_json = proposal.proposed_json
        proposal.status = decision
        db.commit()
        return {
            "proposal_id": proposal.id,
            "trip_id": trip.id,
            "status": decision,
            "message": (
                "The proposed planning snapshot was updated. No booking was made."
                if decision == "approved"
                else "The proposal was rejected; the existing trip snapshot is unchanged."
            ),
        }

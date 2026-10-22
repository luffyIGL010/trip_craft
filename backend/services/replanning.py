import hashlib
import json
import math
import os
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import update

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


def _valid_inr_amount(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


class ReplanAttemptLimitReached(Exception):
    pass


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
        return cls.analyze(state, saved_preferences)["conflicts"]

    @classmethod
    def analyze(
        cls,
        state: dict,
        saved_preferences: Optional[list[dict]] = None,
    ) -> dict:
        conflicts = []
        checks = []
        unavailable = []
        plan = state.get("plan") or {}
        dates = plan.get("dates") or {}
        transport = state.get("transport_results")
        hotels = state.get("hotel_results")
        itinerary = state.get("itinerary_results")
        tasks = set(plan.get("tasks") or [])

        if "find_transport" in tasks:
            if transport and transport.get("status") == "success":
                checks.append({"name": "flight_search", "status": "completed"})
                if not transport.get("options"):
                    unavailable.append({
                        "code": "flight_options_missing",
                        "message": "Flight search returned no options to validate.",
                    })
                elif transport.get("is_mock"):
                    unavailable.append({
                        "code": "flight_data_unverified",
                        "message": "Flight timing and price checks cannot use mock search data.",
                    })
            else:
                message = (
                    transport.get("message")
                    if isinstance(transport, dict) and transport.get("message")
                    else "Flight search results are missing or unsuccessful."
                )
                unavailable.append({
                    "code": "flight_search_unavailable",
                    "message": message,
                })
        else:
            checks.append({"name": "flight_search", "status": "not_requested"})

        hotel_tasks = {"find_hotel", "find_hotels"} & tasks
        if hotel_tasks:
            if hotels and hotels.get("status") == "success":
                checks.append({"name": "hotel_search", "status": "completed"})
                if not hotels.get("options"):
                    unavailable.append({
                        "code": "hotel_options_missing",
                        "message": "Hotel search returned no options to validate.",
                    })
            else:
                message = (
                    hotels.get("message")
                    if isinstance(hotels, dict) and hotels.get("message")
                    else "Hotel search results are missing or unsuccessful."
                )
                unavailable.append({
                    "code": "hotel_search_unavailable",
                    "message": message,
                })
        else:
            checks.append({"name": "hotel_search", "status": "not_requested"})

        if hotels and hotels.get("status") == "success":
            if dates.get("start") and hotels.get("check_in"):
                checks.append({"name": "hotel_check_in_date", "status": "completed"})
                if dates["start"] != hotels["check_in"]:
                    conflicts.append({
                        "code": "hotel_checkin_mismatch",
                        "message": "Hotel check-in does not match the trip start date.",
                    })
            else:
                unavailable.append({
                    "code": "hotel_checkin_unavailable",
                    "message": "Trip start date or hotel check-in date is unavailable.",
                })
            if dates.get("end") and hotels.get("check_out"):
                checks.append({"name": "hotel_check_out_date", "status": "completed"})
                if dates["end"] != hotels["check_out"]:
                    conflicts.append({
                        "code": "hotel_checkout_mismatch",
                        "message": "Hotel check-out does not match the trip end date.",
                    })
            else:
                unavailable.append({
                    "code": "hotel_checkout_unavailable",
                    "message": "Trip end date or hotel check-out date is unavailable.",
                })

        if itinerary and itinerary.get("status") == "success":
            days = itinerary.get("days")
            if isinstance(days, list) and days:
                malformed_schedule = False
                overlap_found = False
                for day in days:
                    activities = day.get("activities") or []
                    valid_times = []
                    for activity in activities:
                        start = _parse_time(activity.get("start_time"))
                        end = _parse_time(activity.get("end_time"))
                        if start is None or end is None or end <= start:
                            malformed_schedule = True
                            continue
                        valid_times.append((start, end, activity))
                    valid_times.sort(key=lambda item: item[0])
                    for previous, current in zip(valid_times, valid_times[1:]):
                        if current[0] < previous[1]:
                            overlap_found = True
                            conflicts.append({
                                "code": "activity_overlap",
                                "date": day.get("date"),
                                "message": (
                                    f"Activities '{previous[2].get('title', 'activity')}' and "
                                    f"'{current[2].get('title', 'activity')}' overlap."
                                ),
                            })
                            break
                if malformed_schedule:
                    unavailable.append({
                        "code": "activity_schedule_incomplete",
                        "message": "One or more activity time ranges are missing or invalid.",
                    })
                checks.append({
                    "name": "activity_overlaps",
                    "status": "conflict" if overlap_found else "completed",
                })
                timing_before = len(conflicts)
                if transport and transport.get("status") == "success" and not transport.get("is_mock"):
                    cls._detect_flight_timing_conflict(itinerary, transport, conflicts)
                    if len(conflicts) > timing_before:
                        checks.append({"name": "flight_activity_timing", "status": "conflict"})
                    elif cls._has_verified_flight_timing(transport):
                        checks.append({"name": "flight_activity_timing", "status": "completed"})
                    else:
                        checks.append({"name": "flight_activity_timing", "status": "unable_to_validate"})
                        unavailable.append({
                            "code": "flight_timing_unavailable",
                            "message": "Provider results do not include usable arrival and return-departure times.",
                        })
                else:
                    checks.append({"name": "flight_activity_timing", "status": "unable_to_validate"})
                    if "find_transport" in tasks:
                        unavailable.append({
                            "code": "flight_timing_unavailable",
                            "message": "Real flight times are unavailable for itinerary timing validation.",
                        })
            else:
                unavailable.append({
                    "code": "itinerary_days_missing",
                    "message": "The itinerary has no day entries to check.",
                })
        elif "create_itinerary" in tasks:
            unavailable.append({
                "code": "itinerary_unavailable",
                "message": (
                    itinerary.get("message")
                    if isinstance(itinerary, dict) and itinerary.get("message")
                    else "The itinerary is missing or could not be generated."
                ),
            })
        else:
            checks.append({"name": "activity_overlaps", "status": "not_requested"})

        budget = cls._explicit_budget_for_state(state)
        if budget:
            estimates = (state.get("combined_trip_cost") or {}).get("estimates") or []
            verified_totals = [
                estimate.get("combined_total")
                for estimate in estimates
                if estimate.get("status") == "available"
                and estimate.get("price_confidence") == "verified"
                and (estimate.get("currency") or "").upper() == budget["currency"]
                and _valid_inr_amount(estimate.get("combined_total"))
            ]
            if verified_totals:
                checks.append({"name": "current_trip_budget", "status": "completed"})
                travelers = max(1, int((plan or {}).get("travelers") or 1))
                maximum = float(budget["max_amount"])
                comparable = [
                    amount / travelers
                    if budget["basis"] == "per_person"
                    else amount
                    for amount in verified_totals
                ]
                if min(comparable) > maximum:
                    conflicts.append({
                        "code": "budget_exceeded",
                        "message": (
                            f"The lowest verified flight-and-hotel estimate exceeds the "
                            f"explicit current-trip budget of {budget['currency']} {maximum:,.0f}."
                        ),
                        "supporting_data": {
                            "lowest_estimate": min(verified_totals),
                            "currency": budget["currency"],
                            "budget": maximum,
                            "basis": budget["basis"],
                        },
                    })
            else:
                checks.append({"name": "current_trip_budget", "status": "unable_to_validate"})
                unavailable.append({
                    "code": "budget_estimate_unavailable",
                    "message": "A verified INR flight-and-hotel total is unavailable for comparison with the current-trip budget.",
                })
        else:
            explicit_budget = plan.get("budget") or {}
            if (
                explicit_budget.get("amount") is not None
                and explicit_budget.get("basis") in {"total_trip", "per_person"}
                and (explicit_budget.get("currency") or "").upper() != "INR"
            ):
                checks.append({"name": "current_trip_budget", "status": "unable_to_validate"})
                unavailable.append({
                    "code": "budget_currency_unsupported",
                    "message": "The current-trip budget currency cannot be compared without a supported conversion.",
                })
            elif explicit_budget.get("amount") is not None:
                checks.append({"name": "current_trip_budget", "status": "unable_to_validate"})
                unavailable.append({
                    "code": "budget_basis_unverified",
                    "message": "The current-trip budget amount has no explicit total-trip or per-person basis.",
                })
            else:
                checks.append({"name": "current_trip_budget", "status": "not_specified"})

        saved_budget = cls._saved_budget_guideline(saved_preferences or [])
        if saved_budget and not cls._explicit_budget_for_state(state):
            checks.append({"name": "usual_budget_guideline", "status": "guideline_only"})

        status = (
            "conflict_detected"
            if conflicts
            else "unable_to_validate"
            if unavailable
            else "no_conflict"
        )
        return {
            "status": status,
            "conflicts": conflicts,
            "checks_completed": checks,
            "unable_to_validate": unavailable,
        }

    @staticmethod
    def _has_verified_flight_timing(transport: dict) -> bool:
        options = transport.get("options") or []
        if not options or transport.get("is_mock"):
            return False
        outbound = _field(options[0], "outbound_leg")
        arrival = _field(outbound, "arrival_time")
        return_leg = _field(options[0], "return_leg")
        if return_leg is None:
            outbound_id = _field(options[0], "option_id")
            paired = next(
                (
                    option for option in transport.get("return_options", []) or []
                    if _field(option, "paired_outbound_option_id") == outbound_id
                ),
                None,
            )
            return_leg = _field(paired, "outbound_leg")
        departure = _field(return_leg, "departure_time")
        round_trip = bool(transport.get("return_date")) or transport.get("trip_type") == "round_trip"
        return bool(arrival and (departure if round_trip else True))

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
        if arrival is not None:
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
                        break

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

    @staticmethod
    def _detect_budget_conflict(state, saved_preferences, conflicts):
        preference = ReplanningService._explicit_budget_for_state(state)
        if not preference:
            return
        estimates = (state.get("combined_trip_cost") or {}).get("estimates") or []
        verified_totals = [
            estimate.get("combined_total")
            for estimate in estimates
            if estimate.get("status") == "available"
            and estimate.get("price_confidence") == "verified"
            and (estimate.get("currency") or "").upper() == "INR"
            and _valid_inr_amount(estimate.get("combined_total"))
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
                    f"The lowest verified flight-and-hotel estimate exceeds the explicit "
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
        original_analysis = cls.analyze(original, saved_preferences)
        candidate_analysis = cls.analyze(candidate, saved_preferences)
        conflicts = original_analysis["conflicts"]
        candidate_conflicts = candidate_analysis["conflicts"]
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
                "original": "No verified available combination within the explicit current-trip budget.",
                "proposed": {
                    "flight_option": recommended.get("flight_option"),
                    "hotel_option": recommended.get("hotel_option"),
                    "combined_total": new_total,
                    "currency": recommended.get("currency"),
                    "confidence": recommended.get("confidence"),
                },
                "reason": "This verified search combination fits the explicit current-trip budget.",
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
                "checks_completed": original_analysis["checks_completed"],
                "unable_to_validate": original_analysis["unable_to_validate"],
                "changes": [],
                "alternatives": alternatives,
                "unresolved_conflicts": unresolved or conflicts,
                "message": "No validated change was found; the existing trip snapshot was left unchanged.",
            }
        if not changes:
            return {
                "status": "no_change",
                "conflicts": conflicts,
                "checks_completed": original_analysis["checks_completed"],
                "unable_to_validate": original_analysis["unable_to_validate"],
                "changes": [],
                "alternatives": alternatives,
                "unresolved_conflicts": unresolved,
                "message": (
                    "No material change was found in the candidate plan."
                    if original_analysis["status"] == "no_conflict"
                    else "No material change was found; some conflicts could not be validated."
                ),
                "validation_status": original_analysis["status"],
            }
        return {
            "status": "awaiting_approval",
            "conflicts": conflicts,
            "checks_completed": original_analysis["checks_completed"],
            "unable_to_validate": original_analysis["unable_to_validate"],
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
            and (item.get("currency") or "").upper() == "INR"
            and _valid_inr_amount(item.get("combined_total"))
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
        basis = budget.get("basis") or budget.get("budget_basis")
        factor = travelers if basis == "per_person" else 1
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
        explicit = ReplanningService._explicit_budget_for_state(state)
        if explicit:
            return explicit
        return ReplanningService._saved_budget_guideline(saved_preferences)

    @staticmethod
    def _explicit_budget_for_state(state: dict) -> Optional[dict]:
        explicit = (state.get("plan") or {}).get("budget") or {}
        if (
            explicit.get("amount") is not None
            and explicit.get("basis") in {"total_trip", "per_person"}
            and (explicit.get("currency") or "").upper() == "INR"
        ):
            return {
                "basis": explicit["basis"],
                "currency": explicit["currency"],
                "max_amount": explicit["amount"],
            }
        return None

    @staticmethod
    def _saved_budget_guideline(saved_preferences: list[dict]) -> Optional[dict]:
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
            and (item.get("currency") or "").upper() == "INR"
            and _valid_inr_amount(item.get("combined_total"))
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
        limit: int,
    ) -> TripPlanChange:
        processing_response = {
            "trip_id": trip.id,
            "replanning": {
                "status": "processing",
                "message": "A re-planning attempt is in progress.",
            },
        }
        result = db.execute(
            update(TripPlan)
            .where(
                TripPlan.id == trip.id,
                TripPlan.user_id == user_id,
                TripPlan.replan_attempts < limit,
            )
            .values(replan_attempts=TripPlan.replan_attempts + 1)
        )
        if result.rowcount != 1:
            db.rollback()
            raise ReplanAttemptLimitReached("Re-planning attempt limit reached.")
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

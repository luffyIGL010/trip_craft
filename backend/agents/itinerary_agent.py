import json
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from agents.schemas import MasterAgentResponse
from schemas.itinerary import ItineraryContent, ItineraryResult

logger = logging.getLogger(__name__)

MAX_ITINERARY_DAYS = 30

ITINERARY_SYSTEM_PROMPT = """You are a travel itinerary assistant. Return only a JSON object with:
{"summary": string, "days": [{"date": "YYYY-MM-DD", "title": string, "activities": [{"start_time": "HH:MM", "end_time": "HH:MM", "title": string, "category": "activity|meal|free_time|transfer|rest", "location": string|null, "description": string}]}]}

Create exactly one day for each supplied trip date, in order. Use destination-local 24-hour times. Group nearby suggestions together, avoid repeating attractions, and keep each day practical with breaks, meals, or free time where appropriate. Respect the supplied interests, dietary/accessibility preferences, trip pace, traveler count, and budget without inventing prices.

All activities are AI-generated suggestions, not confirmed reservations or live recommendations. Do not claim current opening hours, ticket prices, availability, transit times, or travel durations. Do not invent flight or hotel bookings. Treat hotel options as candidates, not selected accommodations.

When flight timing constraints are supplied, schedule no activity before the destination arrival time on its date or after the return departure time on its date. These times are local times and should be treated as hard boundaries. If flight timing is unavailable or marked as mock data, do not infer arrival or departure times from it. Do not add facts that are absent from the supplied trip context."""


def _field(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _parse_provider_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None


def _leg_context(leg: Any) -> dict:
    if leg is None:
        return {}
    return {
        key: _field(leg, key)
        for key in (
            "flight_number",
            "departure_airport",
            "departure_time",
            "arrival_airport",
            "arrival_time",
            "duration",
        )
        if _field(leg, key) is not None
    }


class ItineraryAgent:
    def __init__(self, client=None):
        self.client = client

    async def execute(
        self,
        plan: MasterAgentResponse,
        transport_results: Any = None,
        hotel_results: Any = None,
        saved_preferences: Optional[list[dict]] = None,
    ) -> ItineraryResult:
        missing = []
        if not plan.destination or not plan.destination.strip():
            missing.append("destination")
        if not plan.dates or not plan.dates.start:
            missing.append("start_date")

        if missing:
            return ItineraryResult(
                status="needs_information",
                destination=plan.destination,
                origin=plan.origin,
                travelers=plan.travelers,
                missing_information=missing,
                message="Provide a destination and trip start date to create an itinerary.",
            )

        try:
            start_date = date.fromisoformat(plan.dates.start)
        except (TypeError, ValueError):
            return ItineraryResult(
                status="error",
                destination=plan.destination,
                message="The trip start date must be a valid YYYY-MM-DD date.",
            )

        if plan.dates.end:
            try:
                end_date = date.fromisoformat(plan.dates.end)
            except ValueError:
                return ItineraryResult(
                    status="error",
                    destination=plan.destination,
                    message="The trip end date must be a valid YYYY-MM-DD date.",
                )
        elif plan.duration_days is not None and plan.duration_days > 0:
            end_date = start_date + timedelta(days=plan.duration_days - 1)
        elif plan.duration_days is not None:
            return ItineraryResult(
                status="error",
                destination=plan.destination,
                message="Trip duration must be a positive number of days.",
            )
        else:
            return ItineraryResult(
                status="needs_information",
                destination=plan.destination,
                origin=plan.origin,
                travelers=plan.travelers,
                missing_information=["end_date_or_duration"],
                message="Provide a trip end date or a positive trip duration.",
            )

        if end_date < start_date:
            return ItineraryResult(
                status="error",
                destination=plan.destination,
                message="The trip end date must be on or after the start date.",
            )

        duration_days = (end_date - start_date).days + 1
        if plan.duration_days is not None and plan.duration_days != duration_days:
            return ItineraryResult(
                status="error",
                destination=plan.destination,
                duration_days=duration_days,
                message="The trip duration does not match the supplied start and end dates.",
            )
        if duration_days > MAX_ITINERARY_DAYS:
            return ItineraryResult(
                status="error",
                destination=plan.destination,
                duration_days=duration_days,
                message=f"Itineraries are limited to {MAX_ITINERARY_DAYS} days.",
            )

        if not self.client:
            return ItineraryResult(
                status="error",
                destination=plan.destination,
                duration_days=duration_days,
                message="AI itinerary generation is unavailable because the language model is not configured.",
            )

        context, timing_constraints = self._build_context(
            plan,
            start_date,
            end_date,
            transport_results,
            hotel_results,
            saved_preferences or [],
        )

        try:
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": ITINERARY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(context, ensure_ascii=True),
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=min(6000, 1000 + duration_days * 160),
            )
        except Exception:
            logger.exception("AI itinerary generation failed.")
            return ItineraryResult(
                status="error",
                destination=plan.destination,
                duration_days=duration_days,
                message="The itinerary could not be generated right now. Please try again.",
            )

        try:
            raw_content = response.choices[0].message.content
            content = ItineraryContent.model_validate_json(raw_content)
            self._validate_content(
                content,
                start_date,
                end_date,
                timing_constraints,
            )
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Itinerary generator returned invalid content: %s", exc)
            return ItineraryResult(
                status="error",
                destination=plan.destination,
                duration_days=duration_days,
                message="The itinerary generator returned an invalid schedule. Please try again.",
            )

        notices = [
            "AI-generated suggestions only. Verify opening hours, availability, prices, and local conditions before travel."
        ]
        if context["flight_timing"]["assumption"]:
            notices.append(context["flight_timing"]["assumption"])
        if context["flight_timing"]["status"] == "mock":
            notices.append(
                "Flight data was mock data and was not used to constrain the itinerary."
            )
        return ItineraryResult(
            status="success",
            destination=plan.destination,
            origin=plan.origin,
            travelers=plan.travelers,
            duration_days=duration_days,
            summary=content.summary,
            days=content.days,
            notice=" ".join(notices),
        )

    def _build_context(
        self,
        plan: MasterAgentResponse,
        start_date: date,
        end_date: date,
        transport_results: Any,
        hotel_results: Any,
        saved_preferences: list[dict],
    ) -> tuple[dict, dict]:
        transport_status = _field(transport_results, "status")
        is_mock_flights = bool(_field(transport_results, "is_mock", False))
        flight_options = []
        arrival_candidates = []
        departure_candidates = []
        reference_option_id = None

        if transport_status == "success" and not is_mock_flights:
            for option in (_field(transport_results, "options", []) or [])[:5]:
                option_id = _field(option, "option_id")
                outbound = _leg_context(_field(option, "outbound_leg"))
                returning = _leg_context(_field(option, "return_leg"))
                if not outbound:
                    outbound = {
                        key: _field(option, key)
                        for key in ("departure", "arrival")
                        if _field(option, key) is not None
                    }
                flight_options.append({
                    "option_id": option_id,
                    "outbound": outbound,
                    "return": returning or None,
                })
                arrival = _parse_provider_datetime(outbound.get("arrival_time"))
                if arrival:
                    arrival_candidates.append((option_id, arrival))
                departure = _parse_provider_datetime(returning.get("departure_time"))
                if departure:
                    departure_candidates.append((option_id, departure))

            return_options = _field(transport_results, "return_options", []) or []
            for option in return_options[:10]:
                paired_id = _field(option, "paired_outbound_option_id")
                if paired_id and any(
                    flight.get("option_id") == paired_id for flight in flight_options
                ):
                    reference_option_id = reference_option_id or paired_id
                    return_leg = _leg_context(_field(option, "outbound_leg"))
                    departure = _parse_provider_datetime(return_leg.get("departure_time"))
                    if departure:
                        departure_candidates.append((paired_id, departure))

        if not reference_option_id and flight_options:
            reference_option_id = flight_options[0].get("option_id")

        arrival = next(
            (
                value
                for option_id, value in arrival_candidates
                if option_id == reference_option_id
            ),
            None,
        )
        departure = next(
            (
                value
                for option_id, value in departure_candidates
                if option_id == reference_option_id
            ),
            None,
        )

        flight_timing = {
            "status": (
                "unavailable"
                if transport_status != "success"
                else "mock"
                if is_mock_flights
                else "provider"
            ),
            "reference_option_id": reference_option_id,
            "arrival": (
                {"date": arrival.date().isoformat(), "time": arrival.strftime("%H:%M")}
                if arrival
                else None
            ),
            "return_departure": (
                {"date": departure.date().isoformat(), "time": departure.strftime("%H:%M")}
                if departure
                else None
            ),
            "assumption": (
                "Flight timing uses a search result as a planning reference, not a booked or selected flight."
                if arrival or departure
                else None
            ),
        }
        constraints = {
            "arrival": (
                (arrival.date(), arrival.time())
                if arrival
                else None
            ),
            "return_departure": (
                (departure.date(), departure.time())
                if departure
                else None
            ),
        }
        hotels = []
        if _field(hotel_results, "status") == "success":
            for hotel in (_field(hotel_results, "options", []) or [])[:5]:
                hotels.append({
                    key: _field(hotel, key)
                    for key in (
                        "option_id",
                        "name",
                        "property_type",
                        "address",
                        "city",
                        "nightly_price",
                        "total_price",
                        "currency",
                    )
                    if _field(hotel, key) is not None
                })

        context = {
            "destination": plan.destination,
            "origin": plan.origin,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "duration_days": (end_date - start_date).days + 1,
            "trip_dates": [
                (start_date + timedelta(days=offset)).isoformat()
                for offset in range((end_date - start_date).days + 1)
            ],
            "travelers": plan.travelers,
            "interests_and_preferences": plan.preferences,
            "budget": {
                "amount": plan.budget.amount if plan.budget else None,
                "currency": plan.budget.currency if plan.budget else None,
                "saved_budget_preferences": [
                    {
                        key: preference.get(key)
                        for key in (
                            "min_amount",
                            "max_amount",
                            "currency",
                            "budget_basis",
                        )
                    }
                    for preference in saved_preferences
                    if preference.get("category") == "budget"
                ],
            },
            "flight_options": flight_options,
            "flight_timing": flight_timing,
            "hotel_options": hotels,
        }
        return context, constraints

    @staticmethod
    def _validate_content(
        content: ItineraryContent,
        start_date: date,
        end_date: date,
        timing_constraints: dict,
    ) -> None:
        expected_dates = [
            (start_date + timedelta(days=offset)).isoformat()
            for offset in range((end_date - start_date).days + 1)
        ]
        actual_dates = [day.date for day in content.days]
        if actual_dates != expected_dates:
            raise ValueError("Itinerary must contain every trip date exactly once and in order.")

        seen_activities = set()
        for day in content.days:
            itinerary_date = date.fromisoformat(day.date)
            previous_end = None
            for activity in day.activities:
                activity_start = datetime.combine(
                    itinerary_date, time.fromisoformat(activity.start_time)
                )
                activity_end = datetime.combine(
                    itinerary_date, time.fromisoformat(activity.end_time)
                )
                if activity_end <= activity_start:
                    raise ValueError("Activity end time must follow its start time.")
                if previous_end and activity_start < previous_end:
                    raise ValueError("Activities must be chronological and must not overlap.")
                previous_end = activity_end

                if activity.category == "activity":
                    normalized_title = re.sub(
                        r"\s+", " ", activity.title
                    ).strip().casefold()
                    if normalized_title in seen_activities:
                        raise ValueError("The same activity must not be repeated.")
                    seen_activities.add(normalized_title)

                arrival = timing_constraints.get("arrival")
                if arrival and itinerary_date == arrival[0] and activity_start.time() < arrival[1]:
                    raise ValueError("An activity is scheduled before the destination arrival.")

                departure = timing_constraints.get("return_departure")
                if (
                    departure
                    and itinerary_date == departure[0]
                    and activity_end.time() > departure[1]
                ):
                    raise ValueError("An activity is scheduled after the return departure.")

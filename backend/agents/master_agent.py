import os
import json
import logging
import re
from datetime import datetime
from .schemas import MasterAgentResponse, Dates, Budget
from .travel_agent import TravelAgent
from .hotel_agent import HotelAgent
from .itinerary_agent import ItineraryAgent
from preferences.service import (
    accommodation_preference_from_request,
    explicit_categories_in_request,
    preference_label,
)

logger = logging.getLogger(__name__)


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _normalized_identity(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _flight_identity(option, fallback_index):
    option_id = _normalized_identity(_field(option, "option_id"))
    if option_id:
        return ("provider-id", option_id)
    outbound = _field(option, "outbound_leg")
    returning = _field(option, "return_leg")
    components = [
        _field(option, "provider"),
        _field(option, "trip_type"),
        _field(outbound, "flight_number"),
        _field(outbound, "departure_airport"),
        _field(outbound, "departure_time"),
        _field(outbound, "arrival_airport"),
        _field(outbound, "arrival_time"),
        _field(returning, "flight_number"),
        _field(returning, "departure_airport"),
        _field(returning, "departure_time"),
        _field(returning, "arrival_airport"),
        _field(returning, "arrival_time"),
    ]
    normalized = tuple(_normalized_identity(part) for part in components)
    return (
        normalized
        if any(normalized[2:])
        else ("unidentified-flight", fallback_index)
    )


def _hotel_identity(option, fallback_index):
    option_id = _normalized_identity(_field(option, "option_id"))
    if option_id:
        return ("provider-id", option_id)
    url = _normalized_identity(_field(option, "url"))
    if url:
        return ("url", url)
    normalized = tuple(
        _normalized_identity(_field(option, name))
        for name in ("name", "property_type", "platform", "address", "city", "region")
    )
    distinctive_name = normalized[0] not in {
        "",
        "hotel",
        "accommodation",
        "unnamed property",
    }
    return (
        normalized
        if distinctive_name
        else ("unidentified-hotel", fallback_index)
    )


# The system prompt instructs the LLM to act as an orchestrator only
SYSTEM_PROMPT = """You are a Master Travel Agent Orchestrator. Your ONLY job is to analyze a user's travel request and return a structured JSON plan.

You must extract the following from the user's message:
- request_summary: A brief one-line summary of the user's request.
- destination: The travel destination (city name or uppercase 3-letter IATA airport code, or null if not provided).
- origin: Where they are traveling from (uppercase 3-letter IATA airport code, city name, or state/region name, or null if not provided).
- dates: An object with "start" and "end" date strings (or null if not provided). Guess dates format as YYYY-MM-DD for the near future if specified (e.g., November 10 to November 17, 2026 -> start: "2026-11-10", end: "2026-11-17"; 27 Dec to 31 Dec -> start: "2026-12-27", end: "2026-12-31").
- duration_days: Number of calendar days for the trip, calculated as inclusive calendar days: difference between end and start dates plus 1 (e.g. 2026-12-27 to 2026-12-31 is 5 calendar days; or null if dates not provided).
- travelers: Number of travelers/adults (or null if not provided).
- budget: An object with "amount" (number or null) and "currency" (default "INR").
- preferences: A list of interests/preferences mentioned (e.g. ["adventure", "beaches"]).
- tasks: A list of planning tasks required. Choose from: "find_transport", "find_hotel", "find_hotels", "find_activities", "create_itinerary", "check_budget", "check_weather". Include "find_hotel" when the user requests hotels or accommodation. Include "find_transport" when the user requests flights or transport. Include both when planning a full trip.
- Include "create_itinerary" when the user requests an itinerary, a day-by-day plan, or a daily schedule.
- missing_information: A list of important missing details the user did not provide (e.g. "origin", "travel dates or duration", "number of travelers"). Note: origin is only needed if finding transport; travel dates (check-in and check-out) are needed for hotels. Budget is optional and should NOT be added to missing_information merely because it is unspecified.

Rules:
1. Do NOT invent information the user did not provide. Use null for unknown fields.
2. If critical info is missing, add it to the missing_information list. Do NOT treat budget as required.
3. Always include relevant tasks from the allowed task list.
4. Return ONLY valid JSON matching the schema. No extra text or markdown."""

class MasterAgent:
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        self.client = None

        if self.api_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=self.api_key)
                logger.info("Groq LLM client initialized successfully.")
            except ImportError:
                logger.warning("groq package not installed. Running in mock mode. Run: pip install groq")
            except Exception as e:
                logger.warning(f"Failed to initialize Groq client: {e}. Running in mock mode.")
        else:
            logger.warning("No GROQ_API_KEY found in environment. MasterAgent running in mock mode.")
            
        self.travel_agent = TravelAgent()
        self.hotel_agent = HotelAgent()
        self.itinerary_agent = ItineraryAgent(client=self.client)

    async def process_request(
        self,
        user_message: str,
        status_callback=None,
        saved_preferences: list[dict] | None = None,
    ) -> dict:
        """Main entry point: analyze a user's travel request and return a structured plan with delegated results."""
        logger.info("Master Agent received a travel request.")

        if not user_message or not user_message.strip():
            raise ValueError("Empty message. Please describe your travel plans.")

        if status_callback:
            await status_callback("master_agent", "processing")

        if self.client:
            plan = self._call_llm(user_message)
        else:
            plan = self._mock_response(user_message)

        wants_itinerary = (
            "create_itinerary" in plan.tasks
            or re.search(
                r"\bitinerary\b|\bday[- ]by[- ]day\b|\bdaily schedule\b|\bplan each day\b",
                user_message,
                re.IGNORECASE,
            )
            is not None
        )
        if wants_itinerary and "create_itinerary" not in plan.tasks:
            plan.tasks.append("create_itinerary")

        current_categories = explicit_categories_in_request(user_message)
        if plan.budget.amount is not None:
            current_categories.add("budget")
        applicable_preferences = [
            preference
            for preference in (saved_preferences or [])
            if preference.get("category") not in current_categories
        ]
        for preference in applicable_preferences:
            plan.preferences.append(
                f"Saved travel preference — {preference_label(preference)}"
            )
        overridden_categories = {
            preference.get("category")
            for preference in (saved_preferences or [])
            if preference.get("category") in current_categories
        }
        if overridden_categories:
            plan.preferences.append(
                "Current trip request overrides saved "
                + ", ".join(sorted(overridden_categories))
                + " preference(s)."
            )
            
        response_data = {
            "plan": plan.model_dump(),
            "transport_results": None,
            "hotel_results": None,
            "itinerary_results": None,
        }
        
        # Phase 5: Delegate to Travel Agent if "find_transport" is a task
        if "find_transport" in plan.tasks:
            if status_callback:
                await status_callback("travel_agent", "searching_transport")
                
            transport_result = await self.travel_agent.execute(plan)
            response_data["transport_results"] = transport_result.model_dump()
            
            if status_callback:
                await status_callback("travel_agent", "completed")

        # Phase 6: Delegate to Hotel Agent if "find_hotel" or "find_hotels" is a task
        if "find_hotel" in plan.tasks or "find_hotels" in plan.tasks or any("hotel" in t for t in plan.tasks):
            if status_callback:
                await status_callback("hotel_agent", "preparing")
                await status_callback("hotel_agent", "searching_hotels")

            hotel_result = await self.hotel_agent.execute(plan)
            accommodation_style = accommodation_preference_from_request(user_message)
            if accommodation_style is None:
                accommodation_style = next(
                    (
                        preference.get("value", "").lower()
                        for preference in applicable_preferences
                        if preference.get("category") == "accommodation"
                    ),
                    None,
                )
            if accommodation_style and hotel_result.status == "success":
                if accommodation_style == "luxury":
                    rated_options = [
                        option for option in hotel_result.options
                        if not option.is_fixture_price and option.star_rating is not None
                    ]
                    if rated_options:
                        hotel_result.options.sort(
                            key=lambda option: (
                                option.star_rating is None or option.is_fixture_price,
                                -(option.star_rating or 0),
                                -(option.guest_rating or 0),
                            )
                        )
                else:
                    priced_options = [
                        option for option in hotel_result.options
                        if not option.is_fixture_price
                        and (option.total_price or option.nightly_price) is not None
                    ]
                    if priced_options:
                        prices = [
                            option.total_price or option.nightly_price
                            for option in priced_options
                        ]
                        midpoint = sorted(prices)[len(prices) // 2]

                        def hotel_preference_key(option):
                            price = option.total_price or option.nightly_price
                            if price is None or option.is_fixture_price:
                                return (1, 0)
                            if accommodation_style == "budget":
                                return (0, price)
                            return (0, abs(price - midpoint))

                        hotel_result.options.sort(key=hotel_preference_key)
            response_data["hotel_results"] = hotel_result.model_dump()

            if status_callback:
                await status_callback("hotel_agent", "completed")

        if wants_itinerary:
            if status_callback:
                await status_callback("itinerary_agent", "generating")
            itinerary_result = await self.itinerary_agent.execute(
                plan,
                transport_results=response_data["transport_results"],
                hotel_results=response_data["hotel_results"],
                saved_preferences=applicable_preferences,
            )
            response_data["itinerary_results"] = itinerary_result.model_dump()
            if status_callback:
                await status_callback("itinerary_agent", itinerary_result.status)

        # Calculate combined estimated trip cost for flight + hotel pairs
        combined_estimates = []
        tr = response_data.get("transport_results")
        hr = response_data.get("hotel_results")

        if tr and hr and tr.get("status") == "success" and hr.get("status") == "success":
            flight_opts = tr.get("options") or []
            hotel_opts = hr.get("options") or []
            travelers = plan.travelers or 1
            nights = hr.get("nights") or 0
            unique_flights = []
            seen_flights = set()
            for index, option in enumerate(flight_opts):
                identity = _flight_identity(option, index)
                if identity not in seen_flights:
                    seen_flights.add(identity)
                    unique_flights.append(option)

            unique_hotels = []
            seen_hotels = set()
            for index, option in enumerate(hotel_opts):
                identity = _hotel_identity(option, index)
                if identity not in seen_hotels:
                    seen_hotels.add(identity)
                    unique_hotels.append(option)

            saved_budget = next(
                (
                    preference
                    for preference in applicable_preferences
                    if preference.get("category") == "budget"
                ),
                None,
            )
            comparison_budget = None
            if saved_budget and saved_budget.get("budget_basis") == "total_trip":
                if (saved_budget.get("currency") or "").upper() == "INR":
                    comparison_budget = {
                        "min_amount": saved_budget.get("min_amount"),
                        "max_amount": saved_budget.get("max_amount"),
                        "currency": "INR",
                        "basis": "total_trip",
                    }
                else:
                    comparison_budget = {"status": "not_compared_currency_mismatch"}
            elif saved_budget and saved_budget.get("budget_basis") == "per_person":
                if (saved_budget.get("currency") or "").upper() == "INR":
                    comparison_budget = {
                        "min_amount": saved_budget.get("min_amount"),
                        "max_amount": saved_budget.get("max_amount"),
                        "currency": "INR",
                        "basis": "per_person",
                    }
                else:
                    comparison_budget = {"status": "not_compared_currency_mismatch"}
            elif saved_budget:
                comparison_budget = {
                    "status": "not_compared_basis_unspecified"
                    if saved_budget.get("budget_basis", "unspecified") == "unspecified"
                    else "not_compared_per_person"
                }

            # Generate useful comparisons for the best flight options and hotel options
            seen_combinations = set()
            for f_idx, fl in enumerate(unique_flights[:3]):
                f_price = fl.get("price")
                f_provider = fl.get("provider") or "Flight"
                f_type = fl.get("trip_type") or "flight"

                for h_idx, ht in enumerate(unique_hotels[:3]):
                    combination_id = (
                        _flight_identity(fl, f_idx),
                        _hotel_identity(ht, h_idx),
                    )
                    if combination_id in seen_combinations:
                        continue
                    seen_combinations.add(combination_id)

                    h_total = ht.get("total_price")
                    h_nightly = ht.get("nightly_price")
                    h_name = ht.get("name") or "Hotel"
                    same_currency = (
                        (fl.get("currency") or "INR").upper()
                        == (ht.get("currency") or "").upper()
                        == "INR"
                    )
                    priced_scope_matches_trip = (
                        f_type == tr.get("trip_type")
                    )
                    priced_scope_is_verified = (
                        f_type in {"round_trip", "one_way"}
                        and fl.get("price_scope") == "party_total"
                    )

                    if (
                        same_currency
                        and priced_scope_matches_trip
                        and priced_scope_is_verified
                        and f_price is not None
                        and f_price > 0
                        and h_total is not None
                        and h_total > 0
                    ):
                        comb_total = float(f_price + h_total)
                        estimate = {
                            "flight_option": f"Option {f_idx + 1} ({f_provider})",
                            "flight_option_id": repr(combination_id[0]),
                            "flight_price": float(f_price),
                            "flight_scope": fl.get("price_label")
                            or "Provider price; trip scope unspecified",
                            "hotel_option": f"Option {h_idx + 1} ({h_name})",
                            "hotel_option_id": repr(combination_id[1]),
                            "hotel_total": float(h_total),
                            "hotel_nightly": float(h_nightly) if h_nightly else None,
                            "hotel_scope": f"Total for {nights} night{'s' if nights != 1 else ''}",
                            "combined_total": comb_total,
                            "currency": "INR",
                            "formatted_total": f"₹{comb_total:,.0f}",
                            "inclusions": (
                                f"{fl.get('price_label') or 'Provider flight itinerary price'} + "
                                f"accommodation total for {nights} night"
                                f"{'s' if nights != 1 else ''}"
                            ),
                            "exclusions": "Excludes local transfers, meals, attraction entry fees, visa, and personal expenses.",
                            "disclaimer": "Search estimate only. Fares and room rates are subject to real-time availability and provider confirmation; prices are not guaranteed until booked.",
                            "status": "available"
                        }
                        if comparison_budget and "status" not in comparison_budget:
                            comparison_amount = (
                                comb_total / travelers
                                if comparison_budget["basis"] == "per_person"
                                else comb_total
                            )
                            minimum = comparison_budget["min_amount"]
                            maximum = comparison_budget["max_amount"]
                            if maximum is not None and comparison_amount > maximum:
                                budget_status = "above"
                            elif minimum is not None and comparison_amount < minimum:
                                budget_status = "below"
                            else:
                                budget_status = "within"
                            estimate["saved_budget_comparison"] = {
                                **comparison_budget,
                                "status": budget_status,
                                "travelers": travelers,
                                "comparison_amount": comparison_amount,
                                "comparison_label": (
                                    f"INR {comparison_amount:,.0f} per traveler "
                                    f"({travelers} travelers)"
                                    if comparison_budget["basis"] == "per_person"
                                    else f"INR {comparison_amount:,.0f} for the full trip"
                                ),
                            }
                        elif comparison_budget:
                            estimate["saved_budget_comparison"] = comparison_budget
                        combined_estimates.append(estimate)
                    else:
                        combined_estimates.append({
                            "flight_option": f"Option {f_idx + 1} ({f_provider})",
                            "flight_option_id": repr(combination_id[0]),
                            "hotel_option": f"Option {h_idx + 1} ({h_name})",
                            "hotel_option_id": repr(combination_id[1]),
                            "status": "unavailable",
                            "message": (
                                "Combined cost unavailable (currency mismatch, "
                                "unverified provider trip or traveler price scope, or reliable price "
                                "unavailable for one or both options)"
                            )
                        })

        response_data["combined_trip_cost"] = {
            "status": "available" if any(c.get("status") == "available" for c in combined_estimates) else "unavailable",
            "estimates": combined_estimates,
            "travelers": plan.travelers or 1,
            "hotel_nights": hr.get("nights") if hr else None,
            "trip_duration_days": plan.duration_days
        }

        return response_data

    def _call_llm(self, user_message: str) -> MasterAgentResponse:
        """Call the Groq LLM API to parse the user's request into a structured plan."""
        try:
            logger.info("Calling Groq LLM API...")

            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=1024
            )

            raw_text = response.choices[0].message.content
            logger.info("LLM response received. Parsing JSON...")

            data = json.loads(raw_text)

            # Safely build the response using Pydantic, handling explicit nulls
            dates_data = data.get("dates") or {}
            budget_data = data.get("budget") or {}

            # Calculate duration_days as calendar days (e.g. Dec 27 to Dec 31 = 5 calendar days)
            duration_days = data.get("duration_days")
            if dates_data.get("start") and dates_data.get("end"):
                try:
                    d_s = datetime.strptime(dates_data["start"], "%Y-%m-%d").date()
                    d_e = datetime.strptime(dates_data["end"], "%Y-%m-%d").date()
                    if d_e >= d_s:
                        duration_days = (d_e - d_s).days + 1
                except Exception:
                    pass

            # Budget is optional - filter out "budget" from missing_information if present
            raw_missing = data.get("missing_information") or []
            filtered_missing = [m for m in raw_missing if m.lower() != "budget"]

            plan = MasterAgentResponse(
                request_summary=data.get("request_summary", ""),
                destination=data.get("destination"),
                origin=data.get("origin"),
                dates=Dates(
                    start=dates_data.get("start"),
                    end=dates_data.get("end")
                ),
                duration_days=duration_days,
                travelers=data.get("travelers"),
                budget=Budget(
                    amount=budget_data.get("amount"),
                    currency=budget_data.get("currency", "INR")
                ),
                preferences=data.get("preferences") or [],
                tasks=data.get("tasks") or [],
                missing_information=filtered_missing
            )

            logger.info("Master Agent plan generated successfully via LLM.")
            return plan

        except Exception as e:
            logger.warning(f"LLM API call failed ({e}). Falling back to robust mock response.")
            return self._mock_response(user_message)

    def _mock_response(self, user_message: str) -> MasterAgentResponse:
        """Fallback mock response when no LLM API key is configured or LLM is unreachable."""
        logger.info("Generating mock response (fallback parser)")
        msg = user_message.lower()

        # 1. Extract destination and origin using regex patterns
        destination = None
        origin = None

        m_to_from = re.search(r'\bto\s+([A-Za-z\s]+?)\s+from\s+([A-Za-z\s,]+?)(?:\s*,?\s*from\s+|\s*,?\s*for\s+|\s*,?\s*under\s+|\s*,?\s*on\s+|\s*,?\s*with\s+|\s*$)', user_message, re.I)
        if m_to_from:
            destination = m_to_from.group(1).strip(' ,').title()
            raw_orig = m_to_from.group(2).strip(' ,').title()
            # If origin has comma like 'Dehradun, Uttarakhand', check first part for specific city/airport
            parts = [p.strip() for p in raw_orig.split(',') if p.strip()]
            origin = parts[0] if parts else raw_orig
        else:
            m_from_to = re.search(r'\bfrom\s+([A-Za-z\s,]+?)\s+to\s+([A-Za-z\s]+?)(?:\s*,?\s*from\s+|\s*,?\s*for\s+|\s*,?\s*under\s+|\s*,?\s*on\s+|\s*,?\s*with\s+|\s*$)', user_message, re.I)
            if m_from_to:
                raw_orig = m_from_to.group(1).strip(' ,').title()
                parts = [p.strip() for p in raw_orig.split(',') if p.strip()]
                origin = parts[0] if parts else raw_orig
                destination = m_from_to.group(2).strip(' ,').title()

        # Keyword destination fallbacks if not extracted
        if not destination:
            for place in ["goa", "paris", "manali", "jaipur", "mumbai", "london", "tokyo", "hnd", "dehradun"]:
                if place in msg:
                    destination = "HND" if place in ["tokyo", "hnd"] else place.title()
                    break
        if not destination:
            destination = "Unknown Destination"

        # Keyword origin fallbacks if not extracted
        if not origin:
            if "dehradun" in msg:
                origin = "Dehradun"
            elif "delhi" in msg:
                origin = "Delhi"
            elif "uttarakhand" in msg:
                origin = "Uttarakhand"
            elif "jfk" in msg or "new york" in msg:
                origin = "JFK"

        # 2. Date extraction - preserving explicitly supplied years
        start_date = None
        end_date = None

        # Check ISO format YYYY-MM-DD
        iso_dates = re.findall(r'\b(\d{4}-\d{2}-\d{2})\b', msg)
        if len(iso_dates) >= 2:
            start_date, end_date = iso_dates[0], iso_dates[1]
        else:
            months = {
                'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
                'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
                'aug': 8, 'august': 8, 'sep': 9, 'september': 9, 'oct': 10, 'october': 10,
                'nov': 11, 'november': 11, 'dec': 12, 'december': 12
            }
            month_rx = '|'.join(months.keys())

            # Pattern A: Month DD, YYYY to Month DD, YYYY
            # (e.g. 'January 10, 2027 to January 14, 2027' or 'December 27, 2026, to December 31, 2026')
            pa = rf'({month_rx})\s*(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{4}}))?\s*,?\s*(?:to|-)\s*({month_rx})\s*(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{4}}))?'
            ma = re.search(pa, msg, re.I)
            if ma:
                m1, d1, y1, m2, d2, y2 = ma.groups()
                year1 = int(y1) if y1 else (int(y2) if y2 else datetime.now().year)
                year2 = int(y2) if y2 else year1
                start_date = f"{year1:04d}-{months[m1.lower()]:02d}-{int(d1):02d}"
                end_date = f"{year2:04d}-{months[m2.lower()]:02d}-{int(d2):02d}"
            else:
                # Pattern B: DD Month YYYY to DD Month YYYY (e.g. '27 Dec to 31 Dec' or '10 January 2027 to 14 January 2027')
                pb = rf'(\d{{1,2}})(?:st|nd|rd|th)?\s*({month_rx})(?:\s*,?\s*(\d{{4}}))?\s*,?\s*(?:to|-)\s*(\d{{1,2}})(?:st|nd|rd|th)?\s*({month_rx})(?:\s*,?\s*(\d{{4}}))?'
                mb = re.search(pb, msg, re.I)
                if mb:
                    d1, m1, y1, d2, m2, y2 = mb.groups()
                    year1 = int(y1) if y1 else (int(y2) if y2 else datetime.now().year)
                    year2 = int(y2) if y2 else year1
                    start_date = f"{year1:04d}-{months[m1.lower()]:02d}-{int(d1):02d}"
                    end_date = f"{year2:04d}-{months[m2.lower()]:02d}-{int(d2):02d}"

        # Calculate duration in calendar days (e.g. Dec 27 to Dec 31 = 5 calendar days; Jan 10 to Jan 14 = 5 calendar days)
        duration = None
        if start_date and end_date:
            try:
                d_s = datetime.strptime(start_date, "%Y-%m-%d").date()
                d_e = datetime.strptime(end_date, "%Y-%m-%d").date()
                if d_e >= d_s:
                    duration = (d_e - d_s).days + 1
            except Exception:
                duration = 5
        else:
            for i in range(1, 31):
                if f"{i} day" in msg:
                    duration = i
                    break

        # 3. Travelers
        travelers = None
        for i in range(1, 11):
            if f"{i} people" in msg or f"{i} person" in msg or f"{i} travelers" in msg or f"{i} adults" in msg:
                travelers = i
                break
        if not travelers:
            if "two people" in msg or "2 people" in msg:
                travelers = 2
            elif "four people" in msg or "4 people" in msg:
                travelers = 4

        # 4. Budget
        budget_amount = None
        if "50000" in msg or "50,000" in msg:
            budget_amount = 50000.0

        # 5. Determine tasks
        is_hotel_intent = "hotel" in msg or "hotels" in msg or "stay" in msg or "accommodation" in msg
        is_flight_intent = "flight" in msg or "flights" in msg or "fly" in msg or "transport" in msg
        is_itinerary_intent = bool(re.search(
            r"\bitinerary\b|\bday[- ]by[- ]day\b|\bdaily schedule\b|\bplan each day\b",
            msg,
        ))
        is_general_trip = "plan a trip" in msg or "trip to" in msg or "plan a 5 day" in msg or "plan a 7" in msg or "plan a" in msg

        tasks = []
        if is_flight_intent or (is_general_trip and not is_hotel_intent):
            tasks.append("find_transport")
        if is_hotel_intent or (is_general_trip and not is_flight_intent):
            tasks.append("find_hotel")
        if is_flight_intent and is_hotel_intent:
            tasks = ["find_transport", "find_hotel"]
        if is_itinerary_intent:
            tasks.append("create_itinerary")
        if not tasks:
            tasks = ["find_transport", "find_hotel"]

        # 6. Determine missing information (budget is optional and never required)
        missing = []
        if "find_transport" in tasks and not origin:
            missing.append("origin")
        if not start_date:
            missing.append("check_in_date" if is_hotel_intent and not is_flight_intent else "travel dates or duration")
        if is_hotel_intent and not end_date:
            missing.append("check_out_date")
        if not travelers:
            missing.append("number of travelers")

        return MasterAgentResponse(
            request_summary=f"User wants to travel to {destination}" + (f" from {origin}" if origin else ""),
            destination=destination,
            origin=origin,
            dates=Dates(start=start_date, end=end_date),
            duration_days=duration,
            travelers=travelers,
            budget=Budget(amount=budget_amount),
            preferences=[],
            tasks=tasks,
            missing_information=missing
        )

import logging
from typing import Optional, List, Dict
from datetime import datetime

from schemas.hotel import HotelResult, HotelOption
from services.stayingapi_client import StayingAPIClient
from agents.schemas import MasterAgentResponse

logger = logging.getLogger(__name__)

# Common IATA airport codes mapped to city names for better hotel location search
AIRPORT_TO_CITY: Dict[str, str] = {
    "HND": "Tokyo",
    "NRT": "Tokyo",
    "JFK": "New York",
    "EWR": "New York",
    "LGA": "New York",
    "LHR": "London",
    "LGW": "London",
    "CDG": "Paris",
    "ORY": "Paris",
    "DEL": "Delhi",
    "BOM": "Mumbai",
    "GOI": "Goa",
    "GOX": "Goa",
    "DXB": "Dubai",
    "SIN": "Singapore",
    "BKK": "Bangkok",
    "SYD": "Sydney",
}

class HotelAgent:
    def __init__(self):
        self.api_client = StayingAPIClient()

    async def execute(self, plan: MasterAgentResponse) -> HotelResult:
        logger.info("Hotel Agent is analyzing hotel requirements.")

        # 1. Validate required information
        missing: List[str] = []
        if not plan.destination:
            missing.append("destination")

        check_in = plan.dates.start if plan.dates else None
        check_out = plan.dates.end if plan.dates else None

        if not check_in:
            missing.append("check_in_date")
        if not check_out:
            missing.append("check_out_date")

        if missing:
            logger.info(f"Hotel Agent missing info: {missing}")
            return HotelResult(
                status="needs_information",
                missing_information=missing,
                message="Please provide your destination and travel dates (check-in and check-out) to search for hotels."
            )

        # 2. Validate dates
        try:
            d_in = datetime.strptime(check_in, "%Y-%m-%d").date()
            d_out = datetime.strptime(check_out, "%Y-%m-%d").date()
            if d_out <= d_in:
                return HotelResult(
                    status="error",
                    message="Check-out date must be after check-in date."
                )
        except ValueError:
            logger.warning(f"Invalid date format: check_in={check_in}, check_out={check_out}")
            # If dates are strings like 'November 10', keep them or report error
            pass

        # 3. Resolve location name
        location = plan.destination.strip()
        if location.upper() in AIRPORT_TO_CITY:
            location = AIRPORT_TO_CITY[location.upper()]

        adults = plan.travelers or 2
        children = 0

        # Optional currency from budget if provided
        currency = plan.budget.currency if (plan.budget and plan.budget.currency) else None

        # 4. Call StayingAPI
        try:
            logger.info(f"Hotel Agent calling StayingAPI: location={location}, checkIn={check_in}, checkOut={check_out}, adults={adults}")
            api_response = await self.api_client.search_hotels(
                location=location,
                check_in=check_in,
                check_out=check_out,
                adults=adults,
                children=children,
                limit=6,
                currency=currency
            )

            raw_properties = api_response.get("data", [])
            is_sandbox = api_response.get("is_sandbox", False)
            options: List[HotelOption] = []

            for prop in raw_properties:
                loc = prop.get("location") or {}
                address_parts = []
                if loc.get("address"):
                    address_parts.append(loc["address"])
                else:
                    if loc.get("city"):
                        address_parts.append(loc["city"])
                    if loc.get("region"):
                        address_parts.append(loc["region"])
                    if loc.get("country"):
                        address_parts.append(loc["country"])
                address_str = ", ".join(address_parts) if address_parts else None

                price_info = prop.get("price") or {}
                nightly_price = price_info.get("nightlyPrice")
                total_price = price_info.get("totalPrice")
                prop_currency = price_info.get("currency") or "EUR"
                nights = price_info.get("nights")

                # URL from property or price info
                prop_url = prop.get("url") or price_info.get("url")

                options.append(HotelOption(
                    name=prop.get("name") or "Unnamed Property",
                    platform=prop.get("platform"),
                    property_type=prop.get("propertyType", "hotel"),
                    address=address_str,
                    city=loc.get("city"),
                    country=loc.get("country"),
                    latitude=loc.get("lat"),
                    longitude=loc.get("lng"),
                    star_rating=prop.get("starRating"),
                    guest_rating=prop.get("guestRating"),
                    rating_scale=prop.get("ratingScale"),
                    review_count=prop.get("reviewCount"),
                    nightly_price=float(nightly_price) if nightly_price is not None else None,
                    total_price=float(total_price) if total_price is not None else None,
                    currency=prop_currency,
                    nights=nights,
                    amenities=prop.get("amenities") or [],
                    url=prop_url,
                    images=prop.get("images") or [],
                    is_sandbox=is_sandbox
                ))

            notice = None
            if is_sandbox:
                notice = "Sandbox test data: Deterministic fixtures from StayingAPI Sandbox, not live hotel availability."

            if not options:
                return HotelResult(
                    status="success",
                    destination=location,
                    check_in=check_in,
                    check_out=check_out,
                    adults=adults,
                    children=children,
                    total_found=0,
                    options=[],
                    is_sandbox=is_sandbox,
                    notice=notice,
                    message=f"No accommodation options found in {location} for the selected dates."
                )

            return HotelResult(
                status="success",
                destination=location,
                check_in=check_in,
                check_out=check_out,
                adults=adults,
                children=children,
                total_found=len(options),
                options=options,
                is_sandbox=is_sandbox,
                notice=notice
            )

        except Exception as e:
            logger.error(f"Hotel Agent failed: {e}")
            return HotelResult(
                status="error",
                destination=location,
                check_in=check_in,
                check_out=check_out,
                message=str(e),
                is_sandbox=self.api_client.is_sandbox_key
            )

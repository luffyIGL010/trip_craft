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

# Country code and name mappings for reliable structured matching
COUNTRY_MAP: Dict[str, str] = {
    "HR": "Croatia", "CROATIA": "HR",
    "JP": "Japan", "JAPAN": "JP",
    "IN": "India", "INDIA": "IN",
    "US": "United States", "USA": "US", "UNITED STATES": "US",
    "GB": "United Kingdom", "UK": "GB", "UNITED KINGDOM": "GB",
    "FR": "France", "FRANCE": "FR",
    "DE": "Germany", "GERMANY": "DE",
    "IT": "Italy", "ITALY": "IT",
    "ES": "Spain", "SPAIN": "ES",
    "AE": "United Arab Emirates", "UAE": "AE", "DUBAI": "AE",
    "SG": "Singapore", "SINGAPORE": "SG",
    "TH": "Thailand", "THAILAND": "TH",
    "AU": "Australia", "AUSTRALIA": "AU"
}

def is_location_match(loc: dict, destination: str) -> bool:
    """
    Validate every returned hotel's actual location against the requested destination
    using reliable structured location fields (city, region, country, address).
    Do NOT rely on hotel names alone.
    """
    if not destination or not loc:
        return False
    
    dest_clean = destination.strip().lower()
    dest_resolved = AIRPORT_TO_CITY.get(destination.strip().upper(), "").lower()
    
    city = (loc.get("city") or "").strip().lower()
    region = (loc.get("region") or "").strip().lower()
    country = (loc.get("country") or "").strip().lower()
    address = (loc.get("address") or "").strip().lower()

    search_terms = {dest_clean}
    if dest_resolved:
        search_terms.add(dest_resolved)

    for term in list(search_terms):
        term_upper = term.upper()
        if term_upper in COUNTRY_MAP:
            search_terms.add(COUNTRY_MAP[term_upper].lower())

    for term in search_terms:
        if city and (term in city or city in term):
            return True
        if region and (term in region or region in term):
            return True
        if address and term in address:
            return True
        if country and (term == country or (country.upper() in COUNTRY_MAP and term == COUNTRY_MAP[country.upper()].lower())):
            return True

    return False

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

        # 2. Validate dates & calculate stay nights
        stay_nights: Optional[int] = None
        try:
            d_in = datetime.strptime(check_in, "%Y-%m-%d").date()
            d_out = datetime.strptime(check_out, "%Y-%m-%d").date()
            if d_out <= d_in:
                return HotelResult(
                    status="error",
                    destination=plan.destination,
                    check_in=check_in,
                    check_out=check_out,
                    message="Check-out date must be after check-in date."
                )
            stay_nights = (d_out - d_in).days
        except ValueError:
            logger.warning(f"Invalid date format: check_in={check_in}, check_out={check_out}")
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
            
            logger.debug(f"StayingAPI returned {len(raw_properties)} properties (is_sandbox={is_sandbox})")

            # 5. Filter properties by location validation
            matching_properties = []
            for prop in raw_properties:
                loc = prop.get("location") or {}
                if is_location_match(loc, location):
                    matching_properties.append(prop)
                else:
                    logger.info(
                        f"Filtered out mismatched hotel '{prop.get('name')}' at {loc} for destination '{location}'"
                    )

            notice = None
            if is_sandbox:
                notice = "Sandbox test data: Deterministic fixtures from StayingAPI Sandbox, not live hotel availability."

            # If no properties matched the destination
            if not matching_properties:
                msg = f"No accommodation options found in {location} for the selected dates."
                if is_sandbox and raw_properties:
                    msg = "The Sandbox returned sample properties that do not match your destination. No matching hotel results are available in this test response."

                return HotelResult(
                    status="success",
                    destination=location,
                    check_in=check_in,
                    check_out=check_out,
                    nights=stay_nights,
                    adults=adults,
                    children=children,
                    total_found=0,
                    options=[],
                    is_sandbox=is_sandbox,
                    notice=notice,
                    message=msg
                )

            # 6. Normalize matching properties
            options: List[HotelOption] = []
            for prop in matching_properties:
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
                fixture_total = price_info.get("totalPrice")
                prop_currency = price_info.get("currency") or "EUR"
                fixture_nights = price_info.get("nights")

                # Stay duration and total price calculation
                is_fixture_price = False
                total_price = None
                price_label = None

                if fixture_nights and stay_nights and fixture_nights != stay_nights:
                    is_fixture_price = True
                    total_price = None  # Do NOT present 7-night total as confirmed total for requested stay
                    if fixture_total is not None:
                        price_label = f"Sample fixture total: {prop_currency} {float(fixture_total):,.0f} for {fixture_nights} nights (fixture data, unverified for {stay_nights}-night stay)"
                elif is_sandbox:
                    is_fixture_price = True
                    total_price = None
                    if fixture_total is not None:
                        price_label = f"Sample fixture: {prop_currency} {float(fixture_total):,.0f} (test fixture data)"
                else:
                    is_fixture_price = False
                    total_price = float(fixture_total) if fixture_total is not None else None
                    if total_price is not None:
                        price_label = f"{prop_currency} {total_price:,.0f} total"

                prop_url = prop.get("url") or price_info.get("url")

                options.append(HotelOption(
                    name=prop.get("name") or "Unnamed Property",
                    platform=prop.get("platform"),
                    property_type=prop.get("propertyType", "hotel"),
                    address=address_str,
                    city=loc.get("city"),
                    region=loc.get("region"),
                    country=loc.get("country"),
                    latitude=loc.get("lat"),
                    longitude=loc.get("lng"),
                    star_rating=prop.get("starRating"),
                    guest_rating=prop.get("guestRating"),
                    rating_scale=prop.get("ratingScale"),
                    review_count=prop.get("reviewCount"),
                    nightly_price=float(nightly_price) if nightly_price is not None else None,
                    total_price=total_price,
                    currency=prop_currency,
                    nights=stay_nights,
                    fixture_nights=fixture_nights,
                    is_fixture_price=is_fixture_price,
                    price_label=price_label,
                    amenities=prop.get("amenities") or [],
                    url=prop_url,
                    images=prop.get("images") or [],
                    is_sandbox=is_sandbox
                ))

            return HotelResult(
                status="success",
                destination=location,
                check_in=check_in,
                check_out=check_out,
                nights=stay_nights,
                adults=adults,
                children=children,
                total_found=len(options),
                options=options,
                is_sandbox=is_sandbox,
                notice=notice
            )

        except Exception as e:
            logger.error(f"Hotel Agent failed: {e}", exc_info=True)
            return HotelResult(
                status="error",
                destination=location,
                check_in=check_in,
                check_out=check_out,
                nights=stay_nights,
                message=str(e),
                is_sandbox=self.api_client.is_sandbox_key
            )

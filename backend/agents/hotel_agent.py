import logging
import re
from typing import Optional, List, Dict, Any
from datetime import datetime

from schemas.hotel import HotelResult, HotelOption
from services.hotel_api import HotelAPIClient
from agents.schemas import MasterAgentResponse

logger = logging.getLogger(__name__)

# Common IATA airport codes mapped to city names for hotel location search
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
    "DED": "Dehradun",
    "BLR": "Bangalore",
    "MAA": "Chennai",
    "CCU": "Kolkata",
    "HYD": "Hyderabad",
    "AMD": "Ahmedabad",
    "PNQ": "Pune",
    "JAI": "Jaipur"
}

def extract_price_value(price_obj: Any) -> Optional[float]:
    """
    Safely extract numeric price value from dict, int, float, or string.
    Prevents AttributeError: 'str' object has no attribute 'get'.
    """
    if price_obj is None:
        return None
    if isinstance(price_obj, (int, float)):
        return float(price_obj)
    if isinstance(price_obj, dict):
        val = price_obj.get("extracted_lowest") or price_obj.get("extracted_price")
        if val is not None and isinstance(val, (int, float)):
            return float(val)
        raw_str = price_obj.get("lowest") or price_obj.get("price") or ""
        nums = re.findall(r"[\d,]+", str(raw_str))
        if nums:
            cleaned = nums[0].replace(",", "")
            try:
                return float(cleaned)
            except ValueError:
                pass
        return None
    if isinstance(price_obj, str):
        nums = re.findall(r"[\d,]+", price_obj)
        if nums:
            cleaned = nums[0].replace(",", "")
            try:
                return float(cleaned)
            except ValueError:
                pass
    return None

class HotelAgent:
    def __init__(self):
        self.api_client = HotelAPIClient()

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
        currency = (plan.budget.currency if plan.budget and plan.budget.currency else "INR") or "INR"

        # 4. Call SerpApi Google Hotels
        try:
            logger.info(
                f"Hotel Agent searching SerpApi Google Hotels: location={location}, "
                f"check_in={check_in}, check_out={check_out}, adults={adults}, currency={currency}"
            )
            api_response = await self.api_client.search_hotels(
                location=location,
                check_in=check_in,
                check_out=check_out,
                adults=adults,
                currency=currency,
                limit=10
            )

            if "error" in api_response and not api_response.get("properties"):
                err_msg = api_response["error"]
                logger.error(f"Hotel API error: {err_msg}")
                return HotelResult(
                    status="error",
                    destination=location,
                    check_in=check_in,
                    check_out=check_out,
                    nights=stay_nights,
                    adults=adults,
                    options=[],
                    message=f"Hotel search could not be completed: {err_msg}"
                )

            raw_properties = api_response.get("properties") or []
            logger.info(f"SerpApi Google Hotels returned {len(raw_properties)} properties for {location}.")

            if not raw_properties:
                return HotelResult(
                    status="success",
                    destination=location,
                    check_in=check_in,
                    check_out=check_out,
                    nights=stay_nights,
                    adults=adults,
                    total_found=0,
                    options=[],
                    message=f"No matching accommodation options were found in {location} for the selected dates."
                )

            options: List[HotelOption] = []
            for prop in raw_properties:
                if not isinstance(prop, dict):
                    continue

                name = prop.get("name") or "Hotel"
                prop_type = prop.get("type") or "hotel"
                link = prop.get("link")

                # GPS Coordinates
                gps = prop.get("gps_coordinates") if isinstance(prop.get("gps_coordinates"), dict) else {}
                latitude = float(gps.get("latitude")) if gps and gps.get("latitude") is not None else None
                longitude = float(gps.get("longitude")) if gps and gps.get("longitude") is not None else None

                # Ratings & Reviews
                overall_rating = prop.get("overall_rating")
                rating_val = float(overall_rating) if overall_rating is not None else None
                reviews_count = prop.get("reviews")
                if isinstance(reviews_count, str):
                    try:
                        reviews_count = int(reviews_count.replace(",", ""))
                    except ValueError:
                        reviews_count = None
                elif not isinstance(reviews_count, int):
                    reviews_count = None

                # Amenities
                raw_amenities = prop.get("amenities") or []
                amenities = [str(a) for a in raw_amenities if a] if isinstance(raw_amenities, list) else []

                # Images
                raw_images = prop.get("images") or []
                images = []
                if isinstance(raw_images, list):
                    for img in raw_images:
                        if isinstance(img, dict) and img.get("thumbnail"):
                            images.append(img["thumbnail"])
                        elif isinstance(img, str):
                            images.append(img)

                # Check-in/out times or nearby context as address info if specific address not given
                check_in_time = prop.get("check_in_time")
                check_out_time = prop.get("check_out_time")
                address_parts = [location]
                if check_in_time:
                    address_parts.append(f"Check-in: {check_in_time}")
                if check_out_time:
                    address_parts.append(f"Check-out: {check_out_time}")
                address_str = " | ".join(address_parts)

                # Platform / Source
                platform = "Google Hotels"
                prices_list = prop.get("prices")
                if isinstance(prices_list, list) and len(prices_list) > 0 and isinstance(prices_list[0], dict):
                    source_name = prices_list[0].get("source")
                    if source_name:
                        platform = source_name

                # Prices: safe extraction
                rate_per_night_raw = prop.get("rate_per_night")
                nightly_price = extract_price_value(rate_per_night_raw)

                total_rate_raw = prop.get("total_rate")
                api_total = extract_price_value(total_rate_raw)

                # Total stay price verification
                # If API provided a total_rate, use it. If not, and nightly_price and stay_nights are available, calculate base total.
                total_price = api_total
                price_note = None

                if nightly_price and stay_nights:
                    base_calc = nightly_price * stay_nights
                    if api_total is not None:
                        diff = api_total - base_calc
                        if abs(diff) > 2.0:
                            # Difference is due to taxes, service fees, or seasonal pricing included in the total
                            price_note = f"Includes taxes/fees (diff: ₹{diff:,.0f} from base ₹{base_calc:,.0f})"
                    else:
                        total_price = base_calc

                # Price Label consistent with currency
                price_label = None
                if total_price and stay_nights:
                    note_str = f" • {price_note}" if price_note else ""
                    price_label = f"₹{total_price:,.0f} total for {stay_nights} nights{note_str}"
                elif nightly_price:
                    price_label = f"₹{nightly_price:,.0f} /night"
                else:
                    price_label = "Price on request"

                options.append(HotelOption(
                    name=name,
                    platform=platform,
                    property_type=prop_type,
                    address=address_str,
                    city=location,
                    latitude=latitude,
                    longitude=longitude,
                    guest_rating=rating_val,
                    rating_scale=5.0,
                    review_count=reviews_count,
                    nightly_price=nightly_price,
                    total_price=total_price,
                    currency=currency,
                    nights=stay_nights,
                    is_fixture_price=False,
                    price_label=price_label,
                    amenities=amenities,
                    url=link,
                    images=images,
                    is_sandbox=False
                ))

            # Select the best 5-8 useful options based on available API data (price, rating, reviews)
            # Prioritize options that have prices or good ratings/reviews while keeping price-on-request valid
            def sort_key(opt: HotelOption):
                has_price = 1 if (opt.nightly_price is not None or opt.total_price is not None) else 0
                has_rating = 1 if opt.guest_rating is not None else 0
                rating = opt.guest_rating or 0.0
                reviews = opt.review_count or 0
                return (has_price, has_rating, rating, reviews)

            options.sort(key=sort_key, reverse=True)
            # Limit to top 6 (within the requested 5-8 range)
            selected_options = options[:6]

            return HotelResult(
                status="success",
                destination=location,
                check_in=check_in,
                check_out=check_out,
                nights=stay_nights,
                adults=adults,
                total_found=len(selected_options),
                options=selected_options,
                is_sandbox=False,
                notice=None
            )

        except Exception as e:
            logger.error(f"Hotel Agent failed: {e}", exc_info=True)
            return HotelResult(
                status="error",
                destination=location,
                check_in=check_in,
                check_out=check_out,
                nights=stay_nights,
                adults=adults,
                message="Hotel search is temporarily unavailable. Please try again later.",
                is_sandbox=False
            )

import logging
import math
from typing import Optional
from schemas.transport import TransportResult, TransportOption, FlightLeg
from services.transport_api import TransportAPIClient
from agents.schemas import MasterAgentResponse

logger = logging.getLogger(__name__)

def extract_airline(flight_data: dict) -> str:
    """Extract airline/provider name from raw API response without inventing names."""
    # 1. Top-level airline field if provided
    top_airline = flight_data.get("airline")
    if top_airline and isinstance(top_airline, str) and top_airline.strip():
        return top_airline.strip()

    # 2. Extract from flight segments
    flights = flight_data.get("flights") or []
    airlines = []
    for fl in flights:
        al = fl.get("airline")
        if al and isinstance(al, str) and al.strip() and al.strip() not in airlines:
            airlines.append(al.strip())

    if airlines:
        return ", ".join(airlines)

    # 3. If no airline name, use flight number if available
    flight_nums = [fl.get("flight_number") for fl in flights if fl.get("flight_number")]
    if flight_nums:
        return f"Flight {', '.join(flight_nums)}"

    # 4. Graceful fallback when provider name is missing
    return "Provider Not Specified"

def build_leg(flight_data: dict) -> FlightLeg:
    """Construct a FlightLeg schema object from raw flight data."""
    flights = flight_data.get("flights") or []
    provider = extract_airline(flight_data)
    if not flights:
        return FlightLeg(
            provider=provider,
            departure_time="TBD",
            arrival_time="TBD",
            duration="Unknown",
            stops=0
        )
    first_fl = flights[0]
    last_fl = flights[-1]
    dep_apt = first_fl.get("departure_airport", {}).get("name") or first_fl.get("departure_airport", {}).get("id")
    dep_time = first_fl.get("departure_airport", {}).get("time", "TBD")
    arr_apt = last_fl.get("arrival_airport", {}).get("name") or last_fl.get("arrival_airport", {}).get("id")
    arr_time = last_fl.get("arrival_airport", {}).get("time", "TBD")
    duration_mins = flight_data.get("total_duration")
    duration_str = f"{duration_mins // 60}h {duration_mins % 60}m" if duration_mins else "Unknown"
    flight_nums = [fl.get("flight_number") for fl in flights if fl.get("flight_number")]
    airplane = first_fl.get("airplane")

    return FlightLeg(
        provider=provider,
        flight_number=", ".join(flight_nums) if flight_nums else None,
        departure_airport=dep_apt,
        departure_time=dep_time,
        arrival_airport=arr_apt,
        arrival_time=arr_time,
        duration=duration_str,
        stops=max(0, len(flights) - 1),
        airplane=airplane
    )

# Recognized ambiguous states and regions that are not specific commercial airports
AMBIGUOUS_REGIONS: dict = {
    "UTTARAKHAND": "Uttarakhand is an Indian state with multiple regional airports (such as Dehradun - DED or Pantnagar - PGH).",
    "HIMACHAL PRADESH": "Himachal Pradesh is a state with multiple airports (such as Kullu - KUU, Kangra - DHM, or Shimla - SLV).",
    "RAJASTHAN": "Rajasthan is a state with multiple airports (such as Jaipur - JAI, Udaipur - UDR, or Jodhpur - JDH).",
    "KERALA": "Kerala is a state with multiple airports (such as Kochi - COK, Thiruvananthapuram - TRV, or Kozhikode - CCJ).",
    "UTTAR PRADESH": "Uttar Pradesh is a state with multiple airports (such as Lucknow - LKO or Varanasi - VNS).",
    "MADHYA PRADESH": "Madhya Pradesh is a state with multiple airports (such as Bhopal - BHO or Indore - IDR).",
    "MAHARASHTRA": "Maharashtra is a state with multiple airports (such as Mumbai - BOM or Pune - PNQ).",
    "GUJARAT": "Gujarat is a state with multiple airports (such as Ahmedabad - AMD or Surat - STV).",
    "PUNJAB": "Punjab is a state with multiple airports (such as Amritsar - ATQ or Chandigarh - IXC).",
    "TAMIL NADU": "Tamil Nadu is a state with multiple airports (such as Chennai - MAA or Coimbatore - CJB).",
    "KARNATAKA": "Karnataka is a state with multiple airports (such as Bengaluru - BLR or Mangaluru - IXE).",
    "WEST BENGAL": "West Bengal is a state with multiple airports (such as Kolkata - CCU or Bagdogra - IXB).",
    "CALIFORNIA": "California is a state with multiple airports (such as LAX, SFO, or SAN).",
    "TEXAS": "Texas is a state with multiple airports (such as DFW, IAH, or AUS).",
    "FLORIDA": "Florida is a state with multiple airports (such as MIA, MCO, or FLL).",
}

CITY_TO_IATA: dict = {
    "TOKYO": "HND", "NEW YORK": "JFK", "DELHI": "DEL", "MUMBAI": "BOM",
    "GOA": "GOI", "LONDON": "LHR", "PARIS": "CDG", "DUBAI": "DXB",
    "SINGAPORE": "SIN", "BANGKOK": "BKK", "SYDNEY": "SYD", "SAN FRANCISCO": "SFO",
    "LOS ANGELES": "LAX", "CHICAGO": "ORD", "DEHRADUN": "DED", "PANTNAGAR": "PGH",
    "JAIPUR": "JAI", "BANGALORE": "BLR", "BENGALURU": "BLR", "CHENNAI": "MAA",
    "KOLKATA": "CCU", "HYDERABAD": "HYD", "AHMEDABAD": "AMD", "PUNE": "PNQ",
    "KOCHI": "COK", "COCHIN": "COK", "CHANDIGARH": "IXC", "LUCKNOW": "LKO",
    "VARANASI": "VNS", "AMRITSAR": "ATQ", "SRINAGAR": "SXR"
}

def is_valid_iata(code: str) -> bool:
    """Check if code is a 3-letter alphabetic IATA airport code."""
    return bool(code and len(code) == 3 and code.isalpha())

class TravelAgent:
    def __init__(self):
        self.api_client = TransportAPIClient()

    async def execute(self, plan: MasterAgentResponse) -> TransportResult:
        logger.info("Travel Agent is analyzing the transport requirements.")
        
        # 1. Validate required information
        missing = []
        if not plan.origin:
            missing.append("origin")
        if not plan.destination:
            missing.append("destination")
            
        # SerpApi requires a departure date. If not provided, we must ask the user.
        if not plan.dates or not plan.dates.start:
            missing.append("departure_date")
            
        if missing:
            logger.info(f"Travel Agent missing info: {missing}")
            return TransportResult(
                status="needs_information",
                missing_information=missing,
                message="I need more details before I can search for transport."
            )

        # Determine outbound and return dates
        outbound_date = plan.dates.start
        return_date = None
        if plan.dates and plan.dates.end and plan.dates.end != plan.dates.start:
            return_date = plan.dates.end

        travelers = plan.travelers or 1
        is_round_trip = bool(return_date)

        origin_raw = plan.origin.strip()
        dest_raw = plan.destination.strip()

        # If origin contains a comma e.g. "Dehradun, Uttarakhand", check if first part is a specific city
        origin_resolved_name = origin_raw
        if "," in origin_raw:
            parts = [p.strip() for p in origin_raw.split(",") if p.strip()]
            for p in parts:
                if p.upper() in CITY_TO_IATA:
                    origin_resolved_name = p
                    break

        origin_upper = origin_resolved_name.upper()
        dest_upper = dest_raw.upper()

        # 2. Ambiguous origin check (e.g., Uttarakhand is a state, not an airport)
        if origin_upper in AMBIGUOUS_REGIONS and origin_upper != "GOA":
            detail = AMBIGUOUS_REGIONS[origin_upper]
            logger.info(f"Ambiguous origin detected: '{origin_raw}' is a state/region. {detail}")
            return TransportResult(
                status="needs_information",
                origin=origin_raw,
                destination=dest_raw,
                outbound_date=outbound_date,
                return_date=return_date,
                travelers=travelers,
                missing_information=["departure_city_or_airport"],
                message=f"'{origin_raw}' is a state rather than a specific airport. {detail} Please specify your departure city or airport (e.g., Dehradun - DED) before searching for flights."
            )

        # 3. Resolve city names to 3-letter IATA codes if applicable
        origin_code = CITY_TO_IATA.get(origin_upper, origin_upper)
        dest_code = CITY_TO_IATA.get(dest_upper, dest_upper)

        # Validate departure airport code format
        if not is_valid_iata(origin_code):
            logger.info(f"Origin '{origin_raw}' could not be resolved to a valid airport code.")
            return TransportResult(
                status="needs_information",
                origin=origin_raw,
                destination=dest_raw,
                outbound_date=outbound_date,
                return_date=return_date,
                travelers=travelers,
                missing_information=["departure_city_or_airport"],
                message=f"Could not identify a departure airport for '{origin_raw}'. Please specify your departure city or 3-letter airport code (e.g., DEL for Delhi, BOM for Mumbai)."
            )

        # Validate destination airport code format
        if not is_valid_iata(dest_code):
            logger.info(f"Destination '{dest_raw}' could not be resolved to a valid airport code.")
            return TransportResult(
                status="needs_information",
                origin=origin_raw,
                destination=dest_raw,
                outbound_date=outbound_date,
                return_date=return_date,
                travelers=travelers,
                missing_information=["destination_city_or_airport"],
                message=f"Could not identify a destination airport for '{dest_raw}'. Please specify your destination city or 3-letter airport code (e.g., BOM for Mumbai)."
            )

        # 4. Call Transport API with both dates if round-trip
        try:
            logger.info(
                f"Travel Agent searching flights: {origin_code} -> {dest_code} "
                f"(outbound={outbound_date}, return={return_date}, travelers={travelers})"
            )
            
            api_response = await self.api_client.search_flights(
                origin=origin_code,
                destination=dest_code,
                outbound_date=outbound_date,
                return_date=return_date,
                travelers=travelers
            )
            
            price_source = "Mock" if api_response.get("is_mock", False) else "Provider"

            # 4. Parse and normalize outbound flights
            options = []
            raw_flights = api_response.get("data", {}).get("best_flights", [])
            if not raw_flights:
                raw_flights = api_response.get("data", {}).get("other_flights", [])

            for f in raw_flights[:5]: # Limit to top 5 options
                airline = extract_airline(f)
                provider_type = str(f.get("type") or "").strip().casefold()
                if provider_type in {"round trip", "round-trip", "roundtrip"}:
                    option_trip_type = "round_trip"
                elif provider_type in {"one way", "one-way", "oneway"}:
                    option_trip_type = "one_way"
                else:
                    option_trip_type = None
                if option_trip_type:
                    option_price_label = (
                        f"{price_source} itinerary price for search requested with "
                        f"{travelers} traveler{'s' if travelers != 1 else ''} "
                        f"({option_trip_type.replace('_', ' ')})"
                    )
                else:
                    option_price_label = (
                        f"{price_source} price for search requested with "
                        f"{travelers} traveler{'s' if travelers != 1 else ''}; "
                        "trip scope unspecified"
                    )
                try:
                    price = float(f["price"])
                    if not math.isfinite(price) or price <= 0:
                        price = None
                except (KeyError, TypeError, ValueError):
                    price = None
                duration_mins = f.get("total_duration", 0)
                duration_str = f"{duration_mins // 60}h {duration_mins % 60}m" if duration_mins else "Unknown"
                
                flights_list = f.get("flights", [])
                departure_time = flights_list[0].get("departure_airport", {}).get("time", "TBD") if flights_list else "TBD"
                arrival_time = flights_list[-1].get("arrival_airport", {}).get("time", "TBD") if flights_list else "TBD"
                
                outbound_leg = build_leg(f)

                options.append(TransportOption(
                    option_id=(
                        str(f.get("departure_token") or f.get("flight_id") or f.get("id"))
                        if f.get("departure_token") or f.get("flight_id") or f.get("id")
                        else None
                    ),
                    provider=airline,
                    departure=departure_time,
                    arrival=arrival_time,
                    duration=duration_str,
                    stops=max(0, len(flights_list) - 1),
                    price=price,
                    currency="INR",
                    price_type="total",
                    price_label=option_price_label,
                    trip_type=option_trip_type,
                    outbound_leg=outbound_leg,
                    return_leg=None
                ))

            # 5. Parse return flight options if round-trip and available
            return_options = []
            raw_return_data = api_response.get("return_data")
            if raw_return_data:
                ret_flights = raw_return_data.get("best_flights", [])
                if not ret_flights and raw_return_data.get("data"):
                    ret_flights = raw_return_data["data"].get("best_flights", [])
                if not ret_flights:
                    ret_flights = raw_return_data.get("other_flights", [])

                for rf in ret_flights[:5]:
                    ret_provider = extract_airline(rf)
                    ret_dur_mins = rf.get("total_duration", 0)
                    ret_dur_str = f"{ret_dur_mins // 60}h {ret_dur_mins % 60}m" if ret_dur_mins else "Unknown"
                    ret_fl_list = rf.get("flights", [])
                    ret_dep = ret_fl_list[0].get("departure_airport", {}).get("time", "TBD") if ret_fl_list else "TBD"
                    ret_arr = ret_fl_list[-1].get("arrival_airport", {}).get("time", "TBD") if ret_fl_list else "TBD"
                    ret_leg = build_leg(rf)

                    return_options.append(TransportOption(
                        provider=ret_provider,
                        departure=ret_dep,
                        arrival=ret_arr,
                        duration=ret_dur_str,
                        stops=max(0, len(ret_fl_list) - 1),
                        price=None,
                        currency="INR",
                        price_type="unavailable",
                        price_label=None,
                        trip_type="round_trip",
                        outbound_leg=ret_leg,
                        return_leg=None
                    ))

            if not options:
                return TransportResult(
                    status="error",
                    message="No transport options found for these dates and locations.",
                    is_mock=api_response.get("is_mock", False)
                )

            return TransportResult(
                status="success",
                transport_type="flight",
                trip_type="round_trip" if is_round_trip else "one_way",
                origin=plan.origin,
                destination=plan.destination,
                outbound_date=outbound_date,
                return_date=return_date,
                travelers=travelers,
                price_type="total",
                price_label=(
                    f"{price_source} prices from a search requested with "
                    f"{travelers} traveler{'s' if travelers != 1 else ''}; "
                    "trip scope is reported per option when available"
                ),
                options=options,
                return_options=return_options,
                is_mock=api_response.get("is_mock", False)
            )

        except Exception as e:
            logger.error(f"Travel Agent failed to get transport: {e}")
            return TransportResult(
                status="error",
                message=str(e)
            )

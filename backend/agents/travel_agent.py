import logging
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

        # Resolve city names to 3-letter IATA codes if applicable
        city_to_iata = {
            "TOKYO": "HND", "NEW YORK": "JFK", "DELHI": "DEL", "MUMBAI": "BOM",
            "GOA": "GOI", "LONDON": "LHR", "PARIS": "CDG", "DUBAI": "DXB",
            "SINGAPORE": "SIN", "BANGKOK": "BKK", "SYDNEY": "SYD", "SAN FRANCISCO": "SFO",
            "LOS ANGELES": "LAX", "CHICAGO": "ORD"
        }
        origin_code = city_to_iata.get(plan.origin.strip().upper(), plan.origin.strip())
        dest_code = city_to_iata.get(plan.destination.strip().upper(), plan.destination.strip())

        # 2. Call Transport API with both dates if round-trip
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
            
            # 3. Accurate price labeling (Total price for all passengers as verified from API)
            if travelers > 1:
                price_label = f"Total for {travelers} travelers"
            else:
                price_label = "Total price (1 traveler)"

            if is_round_trip:
                price_label += " (Round trip)"
            else:
                price_label += " (One way)"

            # 4. Parse and normalize outbound flights
            options = []
            raw_flights = api_response.get("data", {}).get("best_flights", [])
            if not raw_flights:
                raw_flights = api_response.get("data", {}).get("other_flights", [])

            for f in raw_flights[:5]: # Limit to top 5 options
                airline = extract_airline(f)
                price = f.get("price", 0)
                duration_mins = f.get("total_duration", 0)
                duration_str = f"{duration_mins // 60}h {duration_mins % 60}m" if duration_mins else "Unknown"
                
                flights_list = f.get("flights", [])
                departure_time = flights_list[0].get("departure_airport", {}).get("time", "TBD") if flights_list else "TBD"
                arrival_time = flights_list[-1].get("arrival_airport", {}).get("time", "TBD") if flights_list else "TBD"
                
                outbound_leg = build_leg(f)

                options.append(TransportOption(
                    provider=airline,
                    departure=departure_time,
                    arrival=arrival_time,
                    duration=duration_str,
                    stops=max(0, len(flights_list) - 1),
                    price=float(price) if price else 0.0,
                    currency="INR",
                    price_type="total",
                    price_label=price_label,
                    trip_type="round_trip" if is_round_trip else "one_way",
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
                        price=float(rf.get("price", 0)) if rf.get("price") else 0.0,
                        currency="INR",
                        price_type="total",
                        price_label=price_label,
                        trip_type="round_trip",
                        outbound_leg=ret_leg,
                        return_leg=None
                    ))

                # Pair top return leg with top outbound option if available
                if options and return_options:
                    options[0].return_leg = return_options[0].outbound_leg

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
                price_label=price_label,
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

import logging
from schemas.transport import TransportResult, TransportOption
from services.transport_api import TransportAPIClient
from agents.schemas import MasterAgentResponse

logger = logging.getLogger(__name__)

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

        # 2. Call Transport API
        try:
            logger.info(f"Travel Agent searching flights: {plan.origin} -> {plan.destination} on {plan.dates.start}")
            
            api_response = await self.api_client.search_flights(plan.origin, plan.destination, plan.dates.start)
            
            # 3. Parse and normalize
            options = []
            raw_flights = api_response.get("data", {}).get("best_flights", [])
            
            if not raw_flights:
                # Some API returns put them in "flights" instead of "best_flights"
                raw_flights = api_response.get("data", {}).get("other_flights", [])

            for f in raw_flights[:5]: # Limit to top 5 options
                airline = f.get("airline", "Unknown Provider")
                price = f.get("price", 0)
                
                # Parse duration
                duration_mins = f.get("total_duration", 0)
                duration_str = f"{duration_mins // 60}h {duration_mins % 60}m" if duration_mins else "Unknown"
                
                # Departure and arrival time parsing is nested, we extract the first and last flight's times if available
                flights_list = f.get("flights", [])
                departure_time = flights_list[0].get("departure_airport", {}).get("time", "TBD") if flights_list else "TBD"
                arrival_time = flights_list[-1].get("arrival_airport", {}).get("time", "TBD") if flights_list else "TBD"
                
                options.append(TransportOption(
                    provider=airline,
                    departure=departure_time,
                    arrival=arrival_time,
                    duration=duration_str,
                    stops=len(flights_list) - 1 if flights_list else 0,
                    price=float(price) if price else 0.0,
                    currency="INR" # Can be dynamic based on API
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
                origin=plan.origin,
                destination=plan.destination,
                options=options,
                is_mock=api_response.get("is_mock", False)
            )

        except Exception as e:
            logger.error(f"Travel Agent failed to get transport: {e}")
            return TransportResult(
                status="error",
                message=str(e)
            )

import os
import httpx
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class TransportAPIClient:
    def __init__(self):
        self.api_key = os.getenv("TRANSPORT_API_KEY")
        # SerpApi Google Flights API URL
        self.base_url = "https://serpapi.com/search.json"

    async def search_flights(
        self, 
        origin: str, 
        destination: str, 
        outbound_date: str, 
        return_date: str = None, 
        travelers: int = 1
    ) -> Dict[str, Any]:
        if not self.api_key or self.api_key.strip() == "" or self.api_key == "your_api_key_here":
            logger.warning("No valid TRANSPORT_API_KEY found, running in mock mode.")
            return self._mock_response(origin, destination, outbound_date, return_date, travelers)

        params = {
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": outbound_date,
            "currency": "INR",
            "hl": "en",
            "api_key": self.api_key
        }

        # Send both outbound and return dates for round-trip requests
        if return_date:
            params["return_date"] = return_date
            params["type"] = "1"  # Round trip
        else:
            params["type"] = "2"  # One way

        if travelers and travelers > 0:
            params["adults"] = str(travelers)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.base_url, params=params, timeout=20.0)
                response.raise_for_status()
                outbound_data = response.json()

                # If round-trip, query return flights for the selected departure token if available
                return_data = None
                if return_date:
                    best_flights = outbound_data.get("best_flights", [])
                    dep_token = None
                    if best_flights:
                        dep_token = best_flights[0].get("departure_token")
                    elif outbound_data.get("other_flights"):
                        dep_token = outbound_data["other_flights"][0].get("departure_token")

                    if dep_token:
                        try:
                            return_params = {
                                "engine": "google_flights",
                                "departure_id": origin,
                                "arrival_id": destination,
                                "outbound_date": outbound_date,
                                "return_date": return_date,
                                "departure_token": dep_token,
                                "currency": "INR",
                                "hl": "en",
                                "api_key": self.api_key
                            }
                            if travelers and travelers > 0:
                                return_params["adults"] = str(travelers)

                            ret_res = await client.get(self.base_url, params=return_params, timeout=15.0)
                            if ret_res.status_code == 200:
                                return_data = ret_res.json()
                        except Exception as ex:
                            logger.warning(f"Could not fetch return flight details: {ex}")

                return {
                    "is_mock": False,
                    "data": outbound_data,
                    "return_data": return_data,
                    "trip_type": "round_trip" if return_date else "one_way",
                    "outbound_date": outbound_date,
                    "return_date": return_date,
                    "travelers": travelers or 1
                }
        except httpx.HTTPStatusError as e:
            logger.error(f"Transport API HTTP Error: {e.response.status_code} - {e.response.text}")
            raise Exception("Transport search is temporarily unavailable (API Error).")
        except Exception as e:
            logger.error(f"Transport API Error: {e}")
            raise Exception("Transport search is temporarily unavailable.")

    def _mock_response(self, origin, destination, outbound_date, return_date=None, travelers=1):
        # Deterministic mock response for testing and fallback
        travelers = travelers or 1
        is_round_trip = bool(return_date)
        multiplier = 2 if is_round_trip else 1
        base_price = 5500.0 * travelers * multiplier

        logger.info(f"Generating mock transport data for {origin} -> {destination} (round_trip={is_round_trip})")
        mock_data = {
            "best_flights": [
                {
                    "flights": [{
                        "airline": "Air India",
                        "flight_number": "AI 101",
                        "airplane": "Boeing 787",
                        "departure_airport": {"id": origin, "name": f"{origin} Airport", "time": f"{outbound_date} 08:00"},
                        "arrival_airport": {"id": destination, "name": f"{destination} Airport", "time": f"{outbound_date} 11:30"},
                        "duration": 210
                    }],
                    "total_duration": 210,
                    "price": base_price,
                    "type": "Round trip" if is_round_trip else "One way"
                },
                {
                    "flights": [{
                        "airline": "IndiGo",
                        "flight_number": "6E 202",
                        "airplane": "Airbus A320",
                        "departure_airport": {"id": origin, "name": f"{origin} Airport", "time": f"{outbound_date} 14:15"},
                        "arrival_airport": {"id": destination, "name": f"{destination} Airport", "time": f"{outbound_date} 18:00"},
                        "duration": 225
                    }],
                    "total_duration": 225,
                    "price": base_price * 0.9,
                    "type": "Round trip" if is_round_trip else "One way"
                }
            ]
        }

        mock_return_data = None
        if is_round_trip:
            mock_return_data = {
                "best_flights": [
                    {
                        "flights": [{
                            "airline": "Air India",
                            "flight_number": "AI 102",
                            "airplane": "Boeing 787",
                            "departure_airport": {"id": destination, "name": f"{destination} Airport", "time": f"{return_date} 18:30"},
                            "arrival_airport": {"id": origin, "name": f"{origin} Airport", "time": f"{return_date} 22:00"},
                            "duration": 210
                        }],
                        "total_duration": 210,
                        "price": base_price,
                        "type": "Round trip"
                    }
                ]
            }

        return {
            "is_mock": True,
            "data": mock_data,
            "return_data": mock_return_data,
            "trip_type": "round_trip" if is_round_trip else "one_way",
            "outbound_date": outbound_date,
            "return_date": return_date,
            "travelers": travelers
        }

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

    async def search_flights(self, origin: str, destination: str, date: str) -> Dict[str, Any]:
        if not self.api_key or self.api_key.strip() == "" or self.api_key == "your_api_key_here":
            logger.warning("No valid TRANSPORT_API_KEY found, running in mock mode.")
            return self._mock_response(origin, destination, date)

        params = {
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": date,
            "currency": "INR",
            "type": "2",
            "api_key": self.api_key
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.base_url, params=params, timeout=15.0)
                response.raise_for_status()
                return {"is_mock": False, "data": response.json()}
        except httpx.HTTPStatusError as e:
            logger.error(f"Transport API HTTP Error: {e.response.status_code} - {e.response.text}")
            raise Exception("Transport search is temporarily unavailable (API Error).")
        except Exception as e:
            logger.error(f"Transport API Error: {e}")
            raise Exception("Transport search is temporarily unavailable.")

    def _mock_response(self, origin, destination, date):
        # A clear mock response when no API key is provided
        logger.info(f"Generating mock transport data for {origin} -> {destination}")
        return {
            "is_mock": True,
            "data": {
                "best_flights": [
                    {
                        "airline": "Mock Airlines (Test Data)",
                        "flights": [{"departure_airport": {"id": origin}, "arrival_airport": {"id": destination}}],
                        "total_duration": 135,
                        "price": 5500
                    },
                    {
                        "airline": "MockJet (Test Data)",
                        "flights": [{"departure_airport": {"id": origin}, "arrival_airport": {"id": destination}}],
                        "total_duration": 150,
                        "price": 4200
                    }
                ]
            }
        }

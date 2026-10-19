import os
import httpx
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class HotelAPIClient:
    """Client for searching hotels using SerpApi Google Hotels engine."""

    def __init__(self):
        # Uses the unified SerpApi key in TRANSPORT_API_KEY (or SERPAPI_API_KEY fallback)
        self.api_key = os.getenv("TRANSPORT_API_KEY") or os.getenv("SERPAPI_API_KEY")
        self.base_url = "https://serpapi.com/search.json"

    async def search_hotels(
        self,
        location: str,
        check_in: str,
        check_out: str,
        adults: int = 2,
        currency: str = "INR",
        limit: int = 10
    ) -> Dict[str, Any]:
        """Search hotels via SerpApi Google Hotels API."""
        if not self.api_key or not self.api_key.strip():
            logger.warning("No valid SerpApi key found for hotel search. Missing TRANSPORT_API_KEY.")
            return {
                "error": "Hotel search is not configured. Missing API key.",
                "properties": []
            }

        params = {
            "engine": "google_hotels",
            "q": location,
            "check_in_date": check_in,
            "check_out_date": check_out,
            "adults": str(adults),
            "currency": currency,
            "gl": "in",
            "hl": "en",
            "api_key": self.api_key.strip()
        }

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.get(self.base_url, params=params)
                
                if response.status_code == 401 or response.status_code == 403:
                    logger.error(f"SerpApi Hotels auth/permission error: {response.status_code}")
                    return {
                        "error": "Hotel search authorization failed or quota exhausted.",
                        "properties": []
                    }

                if response.status_code >= 400:
                    logger.error(f"SerpApi Hotels HTTP {response.status_code}: {response.text[:200]}")
                    return {
                        "error": f"Hotel search service returned HTTP {response.status_code}",
                        "properties": []
                    }

                data = response.json()
                if "error" in data:
                    logger.error(f"SerpApi Hotels error in response: {data['error']}")
                    return {
                        "error": data["error"],
                        "properties": []
                    }

                return data

        except httpx.TimeoutException:
            logger.error("SerpApi Google Hotels request timed out.")
            return {
                "error": "Hotel search request timed out.",
                "properties": []
            }
        except Exception as e:
            logger.error(f"SerpApi Google Hotels exception: {e}", exc_info=True)
            return {
                "error": "Failed to connect to hotel search provider.",
                "properties": []
            }

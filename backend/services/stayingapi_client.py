import os
import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)

class StayingAPIClient:
    def __init__(self):
        self.api_key = os.getenv("STAYING_API_KEY")
        self.base_url = "https://api.stayingapi.com/v1"
        self.is_sandbox_key = bool(self.api_key and self.api_key.startswith("stay_test_"))

    async def search_hotels(
        self,
        location: str,
        check_in: Optional[str] = None,
        check_out: Optional[str] = None,
        adults: int = 2,
        children: int = 0,
        rooms: int = 1,
        platforms: Optional[str] = None,
        limit: int = 10,
        currency: Optional[str] = None,
        property_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Search hotels using StayingAPI /v1/search endpoint."""
        if not self.api_key or not self.api_key.strip():
            logger.error("STAYING_API_KEY is not configured in backend environment.")
            raise Exception("Hotel search is not configured. Missing STAYING_API_KEY.")

        headers = {
            "Authorization": f"Bearer {self.api_key.strip()}",
            "Accept": "application/json"
        }

        params: Dict[str, Any] = {
            "location": location
        }

        if check_in:
            params["checkIn"] = check_in
        if check_out:
            params["checkOut"] = check_out
        if adults and adults > 0:
            params["adults"] = adults
        if children and children > 0:
            params["children"] = children
        if rooms and rooms > 1:
            params["rooms"] = rooms
        if platforms:
            params["platforms"] = platforms
        if limit and limit > 0:
            params["limit"] = min(limit, 40)
        if currency:
            params["currency"] = currency
        if property_type:
            params["propertyType"] = property_type

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.get(
                    f"{self.base_url}/search",
                    headers=headers,
                    params=params
                )

                if response.status_code == 401:
                    logger.error("StayingAPI authentication failed (401 Unauthorized).")
                    raise Exception("StayingAPI authentication failed: Invalid or expired API key.")

                if response.status_code == 429:
                    logger.warning("StayingAPI rate limit reached (429).")
                    raise Exception("Hotel search rate limit reached. Please try again shortly.")

                if response.status_code >= 400:
                    err_msg = "Unknown API error"
                    try:
                        err_json = response.json()
                        err_msg = err_json.get("error", {}).get("message") or response.text
                    except Exception:
                        err_msg = response.text
                    logger.error(f"StayingAPI HTTP {response.status_code}: {err_msg}")
                    raise Exception(f"StayingAPI request error: {err_msg}")

                data = response.json()
                meta = data.get("meta", {})
                is_sandbox = bool(
                    self.is_sandbox_key or 
                    meta.get("sandbox") is True or 
                    meta.get("environment") == "test"
                )

                return {
                    "data": data.get("data", []),
                    "meta": meta,
                    "is_sandbox": is_sandbox
                }

        except httpx.TimeoutException:
            logger.error("StayingAPI request timed out.")
            raise Exception("Hotel search timed out. Please try again.")
        except httpx.RequestError as exc:
            logger.error(f"StayingAPI connection error: {exc}")
            raise Exception("Unable to connect to StayingAPI service.")

"""
PayPal HTTP client for sandbox integration.

Handles OAuth2 token acquisition, order creation, and order capture.
Credentials are loaded from environment variables — secrets are never logged.
"""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

# Fixed sandbox INR→USD conversion rate (1 INR ≈ 0.012 USD)
_INR_TO_USD_RATE = 0.012


class PayPalClient:
    """Async client for the PayPal v2 Orders API (sandbox-ready)."""

    def __init__(self) -> None:
        self._client_id: str = os.getenv("PAYPAL_CLIENT_ID", "")
        self._client_secret: str = os.getenv("PAYPAL_CLIENT_SECRET", "")
        self._base_url: str = os.getenv(
            "PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com"
        ).rstrip("/")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Return True only when all three required env vars are set."""
        return bool(self._client_id and self._client_secret and self._base_url)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ValueError(
                "PayPal credentials are not fully configured. "
                "Ensure PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, and "
                "PAYPAL_BASE_URL are set in your .env file."
            )

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    async def get_access_token(self) -> str:
        """Fetch a short-lived OAuth2 access token from PayPal."""
        self._require_configured()
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/v1/oauth2/token",
                auth=(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
                timeout=15.0,
            )
            response.raise_for_status()
            return response.json()["access_token"]

    async def create_order(
        self,
        amount_inr: float,
        description: str,
        idempotency_key: str,
    ) -> dict:
        """
        Create a PayPal order for the given INR amount.

        PayPal sandbox requires USD, so we convert at the fixed sandbox rate
        (1 INR = 0.012 USD).  The actual INR amount is stored server-side in
        the TripQuote record and is never derived from the frontend.

        Returns the full PayPal API response dict (includes ``id`` and
        ``status`` fields).
        """
        self._require_configured()
        usd_amount = round(amount_inr * _INR_TO_USD_RATE, 2)
        access_token = await self.get_access_token()

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "description": description,
                    "amount": {
                        "currency_code": "USD",
                        "value": str(usd_amount),
                    },
                }
            ],
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/v2/checkout/orders",
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "PayPal-Request-Id": idempotency_key,
                },
                timeout=15.0,
            )
            response.raise_for_status()
            return response.json()

    async def capture_order(self, order_id: str) -> dict:
        """
        Capture a previously approved PayPal order.

        The buyer must have approved the order (via the PayPal JS SDK on the
        frontend) before this call is made.  Returns the full capture response.
        """
        self._require_configured()
        access_token = await self.get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/v2/checkout/orders/{order_id}/capture",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
            response.raise_for_status()
            return response.json()

    async def get_order(self, order_id: str) -> dict:
        """Retrieve the current state of a PayPal order."""
        self._require_configured()
        access_token = await self.get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self._base_url}/v2/checkout/orders/{order_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            response.raise_for_status()
            return response.json()


# Module-level singleton — import this in the router
paypal_client = PayPalClient()

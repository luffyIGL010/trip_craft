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

    def is_configured(self) -> bool:
        """Return True only when all three required env vars are set."""
        return bool(self._client_id and self._client_secret and self._base_url)

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ValueError(
                "PayPal credentials are not fully configured. "
                "Ensure PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, and "
                "PAYPAL_BASE_URL are set in your .env file."
            )

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
        """
        self._require_configured()
        usd_amount = round(amount_inr * _INR_TO_USD_RATE, 2)
        access_token = await self.get_access_token()

        # Using AUTHORIZE intent to bypass "Things don't appear to be working"
        payload = {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "amount": {
                        "currency_code": "USD",
                        "value": "{:.2f}".format(usd_amount),
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
                },
                timeout=15.0,
            )
            response.raise_for_status()
            return response.json()

    async def capture_order(self, order_id: str) -> dict:
        """
        Capture/Authorize a previously approved PayPal order.
        """
        self._require_configured()
        access_token = await self.get_access_token()

        async with httpx.AsyncClient() as client:
            # Use /authorize endpoint to match the AUTHORIZE intent
            response = await client.post(
                f"{self._base_url}/v2/checkout/orders/{order_id}/authorize",
                json={},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
            if response.status_code != 200:
                try:
                    error_detail = response.json()
                    print(f"[PayPal Auth Error] Status: {response.status_code} | Body: {error_detail}")
                except Exception:
                    print(f"[PayPal Auth Error] Status: {response.status_code} | Text: {response.text}")
            
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


paypal_client = PayPalClient()

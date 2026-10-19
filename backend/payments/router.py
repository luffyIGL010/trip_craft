"""
Payment endpoints for the TravelAgent backend.

All prices are fetched server-side from TripQuote records — the frontend
is never trusted for monetary amounts.

Prefix : /api/payments
Tag    : payments
"""
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, validator
from sqlalchemy.orm import Session

from auth.auth import get_current_user
from database.database import get_db
from database.models import User, TripQuote, Payment
from payments.paypal_client import paypal_client

router = APIRouter(prefix="/api/payments", tags=["payments"])

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateQuoteRequest(BaseModel):
    flight_option: str
    hotel_option: str
    flight_price: float
    hotel_price: float
    nights: int
    travelers: int
    destination: str

    @validator("flight_price", "hotel_price")
    def prices_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Price must be greater than 0")
        return v

    @validator("nights", "travelers")
    def counts_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Value must be greater than 0")
        return v

    @validator("flight_option", "hotel_option", "destination")
    def strings_must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty")
        return v.strip()


class CreateOrderRequest(BaseModel):
    quote_id: int


class CaptureOrderRequest(BaseModel):
    order_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Return a timezone-aware UTC datetime (works on all Python versions)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/create-quote")
async def create_quote(
    body: CreateQuoteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Store a server-side trusted quote so later payment steps can read the
    canonical price without trusting the frontend.
    """
    now = _utcnow()
    # Calculate server-side — never from frontend
    total_amount = body.flight_price + body.hotel_price

    quote = TripQuote(
        user_id=current_user.id,
        flight_option=body.flight_option,
        hotel_option=body.hotel_option,
        flight_price=body.flight_price,
        hotel_price=body.hotel_price,
        total_amount=total_amount,
        currency="INR",
        nights=body.nights,
        travelers=body.travelers,
        destination=body.destination,
        is_valid=True,
        expires_at=now + timedelta(hours=1),
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)

    return {
        "quote_id": quote.id,
        "total_amount_inr": quote.total_amount,
        "currency": "INR",
        "flight_option": quote.flight_option,
        "hotel_option": quote.hotel_option,
        "destination": quote.destination,
        "nights": quote.nights,
        "travelers": quote.travelers,
        "expires_at": quote.expires_at.isoformat(),
    }


@router.post("/create-order")
async def create_order(
    body: CreateOrderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a PayPal order for an existing, unexpired, valid TripQuote.

    Idempotent: if a non-failed Payment already exists for this quote,
    the existing PayPal order id is returned.
    """
    # --- Validate quote ---------------------------------------------------
    quote: Optional[TripQuote] = db.query(TripQuote).filter(
        TripQuote.id == body.quote_id
    ).first()

    if quote is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    if quote.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Quote does not belong to you")
    if not quote.is_valid:
        raise HTTPException(status_code=400, detail="Quote is no longer valid")

    now = _utcnow()
    expires_at = quote.expires_at
    # Make expires_at timezone-aware if stored as naive
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=400, detail="Quote has expired")

    # --- Idempotency check ------------------------------------------------
    existing_payment: Optional[Payment] = db.query(Payment).filter(
        Payment.quote_id == quote.id,
        Payment.status != "FAILED",
    ).first()

    if existing_payment and existing_payment.status == "CREATED":
        usd_amount = round(quote.total_amount * 0.012, 2)
        return {
            "order_id": existing_payment.paypal_order_id,
            "amount_inr": quote.total_amount,
            "amount_usd": usd_amount,
            "currency_charged": "USD",
            "description": existing_payment.description,
            "disclaimer": "Search estimate only. Final price confirmed at booking.",
        }

    # --- Create PayPal order ----------------------------------------------
    description = (
        f"TravelAgent: {quote.destination} | "
        f"{quote.flight_option} | {quote.hotel_option} | "
        f"{quote.nights} nights, {quote.travelers} traveler(s)"
    )
    idempotency_key = f"quote-{quote.id}-user-{current_user.id}"

    try:
        pp_response = await paypal_client.create_order(
            amount_inr=quote.total_amount,
            description=description,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"PayPal order creation failed: {exc}",
        )

    paypal_order_id: str = pp_response["id"]
    usd_amount = round(quote.total_amount * 0.012, 2)

    # --- Persist Payment record -------------------------------------------
    payment = Payment(
        user_id=current_user.id,
        quote_id=quote.id,
        paypal_order_id=paypal_order_id,
        amount=quote.total_amount,
        currency="INR",
        status="CREATED",
        description=description,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    return {
        "order_id": paypal_order_id,
        "amount_inr": quote.total_amount,
        "amount_usd": usd_amount,
        "currency_charged": "USD",
        "description": description,
        "disclaimer": "Search estimate only. Final price confirmed at booking.",
    }


@router.post("/capture-order")
async def capture_order(
    body: CaptureOrderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Capture a PayPal order after the buyer has approved it on the frontend.
    Updates the Payment record and returns the capture details.
    """
    payment: Optional[Payment] = db.query(Payment).filter(
        Payment.paypal_order_id == body.order_id
    ).first()

    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Payment does not belong to you")

    # Idempotent: already captured
    if payment.status == "COMPLETED":
        return {
            "status": "already_captured",
            "capture_id": payment.paypal_capture_id,
            "paypal_order_id": payment.paypal_order_id,
            "payment_db_id": payment.id,
        }

    # --- Call PayPal capture API ------------------------------------------
    try:
        pp_response = await paypal_client.capture_order(body.order_id)
    except Exception as exc:
        payment.status = "FAILED"
        payment.updated_at = _utcnow()
        db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"PayPal capture failed: {exc}",
        )

    if pp_response.get("status") != "COMPLETED":
        payment.status = "FAILED"
        payment.updated_at = _utcnow()
        db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"PayPal capture returned unexpected status: {pp_response.get('status')}",
        )

    # Extract capture details
    try:
        capture = pp_response["purchase_units"][0]["payments"]["captures"][0]
        capture_id: str = capture["id"]
        amount_charged_usd: str = capture["amount"]["value"]
    except (KeyError, IndexError) as exc:
        payment.status = "FAILED"
        payment.updated_at = _utcnow()
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"Unexpected PayPal capture response structure: {exc}",
        )

    # --- Update Payment record -------------------------------------------
    payment.status = "COMPLETED"
    payment.paypal_capture_id = capture_id
    payment.updated_at = _utcnow()
    db.commit()
    db.refresh(payment)

    return {
        "status": "success",
        "capture_id": capture_id,
        "paypal_order_id": body.order_id,
        "amount_charged_usd": amount_charged_usd,
        "payment_db_id": payment.id,
    }


@router.get("/config/client-id")
async def get_client_id(
    current_user: User = Depends(get_current_user),
):
    """
    Return the PayPal public client-id to the authenticated frontend.
    This is safe to expose — it is only the public key, not the secret.
    """
    client_id = os.getenv("PAYPAL_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="PayPal client ID is not configured on the server",
        )
    return {"client_id": client_id}


@router.get("/{payment_id}")
async def get_payment(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve a Payment record by its internal DB id (owner-only)."""
    payment: Optional[Payment] = db.query(Payment).filter(
        Payment.id == payment_id
    ).first()

    if payment is None or payment.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Payment not found")

    return {
        "payment_id": payment.id,
        "quote_id": payment.quote_id,
        "paypal_order_id": payment.paypal_order_id,
        "paypal_capture_id": payment.paypal_capture_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "description": payment.description,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
        "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
    }

from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, ForeignKey, text
from sqlalchemy.sql import func
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TripQuote(Base):
    """Server-side trusted quote store — prices come from our API, not the frontend."""
    __tablename__ = "trip_quotes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    flight_option = Column(String, nullable=False)   # e.g. 'Singapore Airlines, SQ511'
    hotel_option = Column(String, nullable=False)    # hotel name
    flight_price = Column(Float, nullable=False)     # INR, from API
    hotel_price = Column(Float, nullable=False)      # INR, total stay, from API
    total_amount = Column(Float, nullable=False)     # flight_price + hotel_price
    currency = Column(String, default="INR")
    nights = Column(Integer, nullable=False)
    travelers = Column(Integer, nullable=False)
    destination = Column(String, nullable=False)
    is_valid = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)  # created_at + 1 hour


class Payment(Base):
    """PayPal order record — one row per PayPal order created."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quote_id = Column(Integer, ForeignKey("trip_quotes.id"), nullable=False)
    paypal_order_id = Column(String, unique=True, index=True, nullable=False)
    paypal_capture_id = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    # Lifecycle: CREATED -> APPROVED -> COMPLETED | FAILED | CANCELLED
    status = Column(String, nullable=False)
    description = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class TravelPreference(Base):
    """A user-controlled, persistent travel preference."""
    __tablename__ = "travel_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String, nullable=False, index=True)
    value = Column(String, nullable=False, default="")
    min_amount = Column(Float, nullable=True)
    max_amount = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    budget_basis = Column(
        String,
        nullable=False,
        default="unspecified",
        server_default=text("'unspecified'"),
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

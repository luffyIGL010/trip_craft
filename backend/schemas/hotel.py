from pydantic import BaseModel
from typing import List, Optional

class HotelOption(BaseModel):
    name: str
    platform: Optional[str] = None  # "google", "booking", "airbnb", "vrbo"
    property_type: Optional[str] = "hotel"
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    star_rating: Optional[float] = None
    guest_rating: Optional[float] = None
    rating_scale: Optional[float] = None  # e.g. 5 or 10
    review_count: Optional[int] = None
    nightly_price: Optional[float] = None
    total_price: Optional[float] = None
    currency: Optional[str] = "EUR"
    nights: Optional[int] = None
    amenities: List[str] = []
    url: Optional[str] = None
    images: List[str] = []
    is_sandbox: bool = False

class HotelResult(BaseModel):
    status: str  # "success", "needs_information", "error"
    message: Optional[str] = None
    missing_information: Optional[List[str]] = None
    destination: Optional[str] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    adults: Optional[int] = 2
    children: Optional[int] = 0
    total_found: int = 0
    options: List[HotelOption] = []
    is_sandbox: bool = False
    notice: Optional[str] = None

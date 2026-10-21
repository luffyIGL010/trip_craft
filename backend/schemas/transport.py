from pydantic import BaseModel
from typing import List, Optional

class FlightLeg(BaseModel):
    provider: str
    flight_number: Optional[str] = None
    departure_airport: Optional[str] = None
    departure_time: Optional[str] = None
    arrival_airport: Optional[str] = None
    arrival_time: Optional[str] = None
    duration: Optional[str] = None
    stops: int = 0
    airplane: Optional[str] = None

class TransportOption(BaseModel):
    provider: str
    departure: str
    arrival: str
    duration: str
    stops: int
    price: Optional[float] = None
    currency: str = "INR"
    price_type: str = "total"
    price_label: Optional[str] = None
    trip_type: Optional[str] = "one_way"  # "one_way" or "round_trip"
    outbound_leg: Optional[FlightLeg] = None
    return_leg: Optional[FlightLeg] = None

class TransportResult(BaseModel):
    status: str  # "success", "needs_information", "error"
    message: Optional[str] = None
    missing_information: Optional[List[str]] = None
    transport_type: str = "flight"
    trip_type: Optional[str] = "one_way"  # "one_way" or "round_trip"
    origin: Optional[str] = None
    destination: Optional[str] = None
    outbound_date: Optional[str] = None
    return_date: Optional[str] = None
    travelers: Optional[int] = 1
    price_type: str = "total"
    price_label: Optional[str] = None
    options: List[TransportOption] = []  # Outbound or bundled flight options
    return_options: List[TransportOption] = []  # Return flight options if available
    is_mock: bool = False

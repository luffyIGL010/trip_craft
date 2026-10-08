from pydantic import BaseModel
from typing import List, Optional

class TransportOption(BaseModel):
    provider: str
    departure: str
    arrival: str
    duration: str
    stops: int
    price: float
    currency: str

class TransportResult(BaseModel):
    status: str  # "success", "needs_information", "error"
    message: Optional[str] = None
    missing_information: Optional[List[str]] = None
    transport_type: str = "flight"
    origin: Optional[str] = None
    destination: Optional[str] = None
    options: List[TransportOption] = []
    is_mock: bool = False

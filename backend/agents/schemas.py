from pydantic import BaseModel
from typing import Optional, List

class Dates(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None

class Budget(BaseModel):
    amount: Optional[float] = None
    currency: str = "INR"

class MasterAgentResponse(BaseModel):
    request_summary: str
    destination: Optional[str] = None
    origin: Optional[str] = None
    dates: Dates = Dates()
    duration_days: Optional[int] = None
    travelers: Optional[int] = None
    budget: Budget = Budget()
    preferences: List[str] = []
    tasks: List[str] = []
    missing_information: List[str] = []

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ItineraryActivity(BaseModel):
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    title: str
    category: Literal["activity", "meal", "free_time", "transfer", "rest"]
    location: Optional[str] = None
    description: str


class ItineraryDay(BaseModel):
    date: str
    title: str
    activities: List[ItineraryActivity]


class ItineraryContent(BaseModel):
    summary: str
    days: List[ItineraryDay]


class ItineraryResult(BaseModel):
    status: Literal["success", "needs_information", "error"]
    destination: Optional[str] = None
    origin: Optional[str] = None
    travelers: Optional[int] = None
    duration_days: Optional[int] = None
    summary: Optional[str] = None
    days: List[ItineraryDay] = []
    missing_information: List[str] = []
    message: Optional[str] = None
    notice: Optional[str] = None

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, root_validator, validator
from sqlalchemy.orm import Session

from auth.auth import get_current_user
from database.database import get_db
from database.models import TravelPreference, User
from preferences.service import (
    MULTI_VALUE_CATEGORIES,
    find_preference,
    list_preferences,
    upsert_preference,
)

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

PreferenceCategory = Literal[
    "destination",
    "budget",
    "accommodation",
    "interest",
    "transport",
    "pace",
    "dietary",
    "accessibility",
]
BudgetBasis = Literal["total_trip", "per_person", "unspecified"]


class PreferenceInput(BaseModel):
    category: PreferenceCategory
    value: str = Field(default="", max_length=300)
    min_amount: Optional[float] = Field(default=None, ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    budget_basis: BudgetBasis = "unspecified"

    @validator("value")
    def trim_value(cls, value: str) -> str:
        return value.strip()

    @validator("currency")
    def normalize_currency(cls, value: Optional[str]) -> Optional[str]:
        if value and not value.isalpha():
            raise ValueError("Currency must be a three-letter code")
        return value.upper() if value else value

    @root_validator(skip_on_failure=True)
    def validate_preference(cls, values):
        category = values.get("category")
        value = values.get("value") or ""
        minimum = values.get("min_amount")
        maximum = values.get("max_amount")

        if category == "budget":
            if values.get("budget_basis") == "unspecified":
                raise ValueError(
                    "Choose whether this is a total-trip or per-traveler budget"
                )
            if minimum is None and maximum is None:
                raise ValueError("A minimum or maximum budget amount is required")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError("Minimum budget cannot exceed maximum budget")
        elif not value:
            raise ValueError("Preference value must not be empty")

        if category == "accommodation" and value.lower() not in {
            "budget",
            "mid-range",
            "luxury",
        }:
            raise ValueError("Accommodation must be budget, mid-range, or luxury")
        if category == "pace" and value.lower() not in {
            "relaxed",
            "balanced",
            "packed",
        }:
            raise ValueError("Trip pace must be relaxed, balanced, or packed")
        return values


def serialize_preference(preference: TravelPreference) -> dict:
    return {
        "id": preference.id,
        "category": preference.category,
        "value": preference.value,
        "min_amount": preference.min_amount,
        "max_amount": preference.max_amount,
        "currency": preference.currency,
        "budget_basis": preference.budget_basis,
    }


@router.get("")
def get_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"preferences": [
        serialize_preference(preference)
        for preference in list_preferences(db, current_user.id)
    ]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_preference(
    body: PreferenceInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    preference = upsert_preference(
        db,
        current_user.id,
        category=body.category,
        value=body.value or "Usual budget",
        min_amount=body.min_amount,
        max_amount=body.max_amount,
        currency=body.currency,
        budget_basis=body.budget_basis,
    )
    db.commit()
    db.refresh(preference)
    return serialize_preference(preference)


@router.put("/{preference_id}")
def update_preference(
    preference_id: int,
    body: PreferenceInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    preference = find_preference(db, preference_id, current_user.id)
    if preference is None:
        raise HTTPException(status_code=404, detail="Preference not found")

    duplicate = (
        db.query(TravelPreference)
        .filter(
            TravelPreference.user_id == current_user.id,
            TravelPreference.category == body.category,
            TravelPreference.id != preference_id,
        )
        .all()
    )
    if body.category in MULTI_VALUE_CATEGORIES and any(
        item.value.casefold() == (body.value or "").casefold()
        for item in duplicate
    ):
        raise HTTPException(
            status_code=409,
            detail="This preference is already saved",
        )

    other_single = next(
        (item for item in duplicate if item.category == body.category),
        None,
    )
    if body.category not in MULTI_VALUE_CATEGORIES and other_single:
        db.delete(other_single)

    preference.category = body.category
    preference.value = body.value or "Usual budget"
    preference.min_amount = body.min_amount
    preference.max_amount = body.max_amount
    preference.currency = body.currency
    preference.budget_basis = body.budget_basis
    db.commit()
    db.refresh(preference)
    return serialize_preference(preference)


@router.delete("/{preference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference(
    preference_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    preference = find_preference(db, preference_id, current_user.id)
    if preference is None:
        raise HTTPException(status_code=404, detail="Preference not found")
    db.delete(preference)
    db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(TravelPreference).filter(
        TravelPreference.user_id == current_user.id
    ).delete(synchronize_session=False)
    db.commit()

import re
from typing import Optional

from sqlalchemy.orm import Session

from database.models import TravelPreference

MULTI_VALUE_CATEGORIES = {
    "destination",
    "interest",
    "dietary",
    "accessibility",
}

_PERSISTENT_STATEMENT = re.compile(
    r"\b(?:i|we)\s+(?:(?:usually|generally|always|typically|tend to)\s+)"
    r"(?:prefer|like|love|enjoy)\s+([^.!?;\n]+)"
    r"|\bmy\s+(?:usual\s+)?travel preferences?\s+(?:are|include)\s+"
    r"([^.!?;\n]+)",
    re.IGNORECASE,
)

_INTERESTS = {
    "history": r"\bhistor(?:y|ical|ic)\b",
    "nature": r"\bnature\b|\bwildlife\b",
    "food": r"\bfood\b|\bculinary\b",
    "shopping": r"\bshopping\b",
    "adventure": r"\badventure\b",
    "culture": r"\bculture\b|\bcultural\b",
}

_CATEGORY_KEYWORDS = {
    "accommodation": r"\b(?:budget|mid[- ]range|luxury)\s+(?:hotels?|accommodation|stays?)\b",
    "pace": r"\b(?:relaxed|balanced|packed)\b",
    "interest": r"\b(?:history|historical|historic|nature|wildlife|food|culinary|shopping|adventure|culture|cultural)\b",
    "transport": r"\b(?:train|rail|flight|plane|bus|car)\b",
    "dietary": r"\b(?:vegetarian|vegan|halal|kosher|gluten[- ]free)\b",
    "accessibility": r"\b(?:wheelchair|step[- ]free|accessible)\b",
}

def accommodation_preference_from_request(message: str) -> Optional[str]:
    match = re.search(
        r"\b(budget|mid[- ]range|luxury)\s+(?:hotels?|accommodation|stays?)\b",
        message,
        re.IGNORECASE,
    )
    return match.group(1).replace(" ", "-").lower() if match else None


def preference_label(preference: TravelPreference | dict) -> str:
    if isinstance(preference, dict):
        category = preference.get("category")
        value = preference.get("value", "")
        minimum = preference.get("min_amount")
        maximum = preference.get("max_amount")
        currency = preference.get("currency")
        budget_basis = preference.get("budget_basis", "unspecified")
    else:
        category = preference.category
        value = preference.value
        minimum = preference.min_amount
        maximum = preference.max_amount
        currency = preference.currency
        budget_basis = preference.budget_basis

    if category == "budget":
        lower = f"{currency or ''} {minimum:,.0f}".strip() if minimum is not None else None
        upper = f"{currency or ''} {maximum:,.0f}".strip() if maximum is not None else None
        if lower and upper:
            value = f"{lower}–{upper}"
        else:
            value = lower or upper or value
        basis_label = {
            "total_trip": "total budget for the entire trip",
            "per_person": "budget per traveler",
        }.get(budget_basis, "basis needs selection")
        return f"usual budget range ({basis_label}): {value}"
    return f"{category}: {value}"


def list_preferences(db: Session, user_id: int) -> list[TravelPreference]:
    return (
        db.query(TravelPreference)
        .filter(TravelPreference.user_id == user_id)
        .order_by(TravelPreference.category, TravelPreference.id)
        .all()
    )


def find_preference(
    db: Session, preference_id: int, user_id: int
) -> Optional[TravelPreference]:
    return (
        db.query(TravelPreference)
        .filter(
            TravelPreference.id == preference_id,
            TravelPreference.user_id == user_id,
        )
        .first()
    )


def upsert_preference(
    db: Session,
    user_id: int,
    *,
    category: str,
    value: str,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    currency: Optional[str] = None,
    budget_basis: str = "unspecified",
    replace_single_value: bool = True,
) -> TravelPreference:
    existing = None
    if category not in MULTI_VALUE_CATEGORIES:
        existing = (
            db.query(TravelPreference)
            .filter(
                TravelPreference.user_id == user_id,
                TravelPreference.category == category,
            )
            .first()
        )
    else:
        existing = (
            db.query(TravelPreference)
            .filter(
                TravelPreference.user_id == user_id,
                TravelPreference.category == category,
            )
            .all()
        )
        existing = next(
            (
                preference
                for preference in existing
                if preference.value.casefold() == value.casefold()
            ),
            None,
        )

    if existing is None:
        existing = TravelPreference(user_id=user_id, category=category)
        db.add(existing)
    elif not replace_single_value and category not in MULTI_VALUE_CATEGORIES:
        return existing

    existing.value = value
    existing.min_amount = min_amount
    existing.max_amount = max_amount
    existing.currency = currency
    existing.budget_basis = budget_basis
    return existing


def extract_explicit_preferences(message: str) -> list[dict]:
    """Extract only preferences phrased as recurring or general travel choices."""
    extracted = []
    seen = set()

    for match in _PERSISTENT_STATEMENT.finditer(message):
        statement = (match.group(1) or match.group(2) or "").strip().lower()
        accommodation = re.search(
            r"\b(budget|mid[- ]range|luxury)\s+(?:hotels?|accommodation|stays?)\b",
            statement,
        )
        if accommodation:
            value = accommodation.group(1).replace(" ", "-")
            candidate = ("accommodation", value)
            if candidate not in seen:
                extracted.append({"category": candidate[0], "value": candidate[1]})
                seen.add(candidate)

        pace = re.search(r"\b(relaxed|balanced|packed)\b", statement)
        if pace:
            candidate = ("pace", pace.group(1))
            if candidate not in seen:
                extracted.append({"category": candidate[0], "value": candidate[1]})
                seen.add(candidate)

        for interest, pattern in _INTERESTS.items():
            if re.search(pattern, statement):
                candidate = ("interest", interest)
                if candidate not in seen:
                    extracted.append({"category": candidate[0], "value": candidate[1]})
                    seen.add(candidate)

        for category, pattern in _CATEGORY_KEYWORDS.items():
            if category in {"accommodation", "pace", "interest"}:
                continue
            found = re.search(pattern, statement)
            if found:
                value = found.group(0).strip()
                candidate = (category, value.casefold())
                if candidate not in seen:
                    extracted.append({"category": category, "value": value})
                    seen.add(candidate)

    return extracted


def explicit_categories_in_request(message: str) -> set[str]:
    """Identify preference categories specified for the current trip."""
    categories = {
        category
        for category, pattern in _CATEGORY_KEYWORDS.items()
        if re.search(pattern, message, re.IGNORECASE)
    }
    if re.search(
        r"\b(?:budget|spend|cost|price|under|below|up to)\s*"
        r"(?:(?:of|is|around|about)\s*)?(?:₹|INR\s*|USD\s*|EUR\s*|GBP\s*)?"
        r"\d",
        message,
        re.IGNORECASE,
    ) or re.search(
        r"\b\d[\d,]*(?:\.\d+)?\s*(?:INR|USD|EUR|GBP)\b",
        message,
        re.IGNORECASE,
    ):
        categories.add("budget")
    return categories

import logging
import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_VARIABLES = (
    "temperature_2m,relative_humidity_2m,precipitation,rain,weather_code,"
    "wind_speed_10m,wind_direction_10m,cloud_cover"
)
HOURLY_VARIABLES = (
    "temperature_2m,relative_humidity_2m,weather_code,cloud_cover,"
    "precipitation_probability,precipitation,rain,snowfall"
)
DAILY_VARIABLES = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max,precipitation_sum,wind_speed_10m_max,"
    "sunrise,sunset"
)
HOURLY_SAMPLE_HOURS = {8, 12, 16, 20}

WMO_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _normalized(value: str) -> str:
    return re.sub(r"[\W_]+", " ", value.casefold()).strip()


def _valid_coordinates(candidate: dict) -> bool:
    latitude = _finite_number(candidate.get("latitude"))
    longitude = _finite_number(candidate.get("longitude"))
    return (
        latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def _weather_description(code: Any) -> Optional[str]:
    if isinstance(code, bool) or not isinstance(code, (int, float)) or not math.isfinite(code):
        return None
    if not float(code).is_integer():
        return None
    normalized_code = int(code)
    return WMO_DESCRIPTIONS.get(normalized_code, f"Weather code {normalized_code}")


class OpenMeteoWeatherService:
    """Resolves a destination and returns only weather data supplied by Open-Meteo."""

    def __init__(
        self,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = 15.0,
    ):
        self.transport = transport
        self.timeout = timeout

    async def get_trip_weather(
        self,
        destination: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str] = None,
        duration_days: Optional[int] = None,
    ) -> dict:
        if not isinstance(destination, str) or not destination.strip():
            return self._unavailable(destination, "A destination is required to look up weather.")
        try:
            start = date.fromisoformat(start_date) if start_date else None
        except (TypeError, ValueError):
            return self._unavailable(
                destination,
                "The trip start date must be a valid YYYY-MM-DD date.",
            )
        if start is None:
            return self._unavailable(destination, "A trip start date is required for date-specific weather.")

        if end_date:
            try:
                end = date.fromisoformat(end_date)
            except (TypeError, ValueError):
                return self._unavailable(destination, "The trip end date must be a valid YYYY-MM-DD date.")
        elif isinstance(duration_days, int) and not isinstance(duration_days, bool) and duration_days > 0:
            end = start + timedelta(days=duration_days - 1)
        else:
            return self._unavailable(
                destination,
                "A trip end date or positive trip duration is required for date-specific weather.",
            )
        if end < start:
            return self._unavailable(destination, "The trip end date must be on or after the start date.")

        trip_dates = [
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)
        ]

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                location, lookup_status, lookup_message = await self._resolve_location(
                    client, destination.strip()
                )
                if location is None:
                    return self._unavailable(destination, lookup_message, lookup_status)

                response = await client.get(
                    FORECAST_URL,
                    params={
                        "latitude": location["latitude"],
                        "longitude": location["longitude"],
                        "current": CURRENT_VARIABLES,
                        "hourly": HOURLY_VARIABLES,
                        "daily": DAILY_VARIABLES,
                        "forecast_days": 16,
                        "timezone": "auto",
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            logger.warning("Open-Meteo request timed out for destination weather.")
            return self._unavailable(
                destination,
                "The weather service timed out. Trip planning can continue without weather data.",
                "error",
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("Open-Meteo returned HTTP %s.", exc.response.status_code)
            return self._unavailable(
                destination,
                "The weather service could not provide a forecast right now.",
                "error",
            )
        except httpx.RequestError:
            logger.warning("Open-Meteo could not be reached.")
            return self._unavailable(
                destination,
                "The weather service could not be reached. Trip planning can continue without weather data.",
                "error",
            )
        except (ValueError, TypeError):
            logger.warning("Open-Meteo returned invalid JSON.")
            return self._unavailable(
                destination,
                "The weather service returned an invalid response.",
                "error",
            )

        try:
            return self._map_forecast(destination, location, trip_dates, data)
        except (KeyError, TypeError, ValueError, IndexError):
            logger.warning("Open-Meteo returned an invalid forecast structure.")
            return self._unavailable(
                destination,
                "The weather service returned an incomplete forecast response.",
                "error",
            )

    async def _resolve_location(self, client: httpx.AsyncClient, destination: str):
        response = await client.get(
            GEOCODING_URL,
            params={
                "name": destination,
                "count": 10,
                "language": "en",
                "format": "json",
            },
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid geocoding response")
        results = data.get("results")
        if results is None:
            return None, "unavailable", "The destination could not be resolved to a weather location."
        if not isinstance(results, list):
            raise ValueError("Invalid geocoding results")
        candidates = [
            item for item in results
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and _valid_coordinates(item)
        ]
        if not candidates:
            return None, "unavailable", "No matching destination was found for weather lookup."

        parts = [part.strip() for part in destination.split(",") if part.strip()]
        base_name = _normalized(parts[0])
        exact = [
            item for item in candidates
            if _normalized(item["name"]) == base_name
        ]
        if not exact:
            exact = [
                item for item in candidates
                if base_name in _normalized(item["name"])
                or base_name == _normalized(item.get("admin1", ""))
            ]
        if not exact:
            return None, "unavailable", "No matching destination was found for weather lookup."

        qualified = []
        if len(parts) > 1:
            qualifiers = [_normalized(part) for part in parts[1:]]
            for candidate in exact:
                searchable = _normalized(" ".join(
                    str(candidate.get(key, ""))
                    for key in ("admin1", "admin2", "country", "country_code")
                ))
                if all(qualifier in searchable for qualifier in qualifiers):
                    qualified.append(candidate)
            if not qualified:
                return None, "unavailable", "The destination qualifiers did not match a known location."
            exact = qualified

        location_groups = {
            (
                str(item.get("country_code") or item.get("country") or "").casefold(),
                _normalized(str(item.get("admin1") or "")),
            )
            for item in exact
        }
        if len(location_groups) > 1 and len(parts) == 1:
            return None, "ambiguous", (
                "This destination name matches multiple locations. "
                "Add a region or country to identify the intended destination."
            )

        exact.sort(
            key=lambda item: (
                _finite_number(item.get("population")) or 0,
                -abs(_finite_number(item.get("latitude")) or 0),
            ),
            reverse=True,
        )
        selected = exact[0]
        return {
            "name": selected["name"],
            "admin1": selected.get("admin1"),
            "country": selected.get("country"),
            "country_code": selected.get("country_code"),
            "latitude": _finite_number(selected.get("latitude")),
            "longitude": _finite_number(selected.get("longitude")),
        }, "success", None

    @staticmethod
    def _map_forecast(destination: str, location: dict, trip_dates: list[str], data: Any) -> dict:
        if not isinstance(data, dict):
            raise ValueError("Invalid weather response")
        current = data.get("current")
        hourly = data.get("hourly")
        daily = data.get("daily")
        if not isinstance(current, dict) or not isinstance(hourly, dict) or not isinstance(daily, dict):
            raise ValueError("Missing weather sections")

        hourly_times = hourly.get("time")
        daily_times = daily.get("time")
        if not isinstance(hourly_times, list) or not isinstance(daily_times, list):
            raise ValueError("Missing forecast timestamps")
        if any(not isinstance(value, str) for value in hourly_times + daily_times):
            raise ValueError("Invalid forecast timestamps")

        daily_indexes = {day: index for index, day in enumerate(daily_times)}
        hourly_by_day: dict[str, list[dict]] = {}
        hourly_fields = (
            "temperature_2m",
            "relative_humidity_2m",
            "weather_code",
            "cloud_cover",
            "precipitation_probability",
            "precipitation",
            "rain",
            "snowfall",
        )
        for index, timestamp in enumerate(hourly_times):
            try:
                parsed = datetime.fromisoformat(timestamp)
            except ValueError:
                continue
            day = parsed.date().isoformat()
            if day not in trip_dates or parsed.hour not in HOURLY_SAMPLE_HOURS:
                continue
            sample = {"time": timestamp}
            for field in hourly_fields:
                values = hourly.get(field)
                value = values[index] if isinstance(values, list) and index < len(values) else None
                sample[field] = _finite_number(value)
            sample["condition"] = _weather_description(sample["weather_code"])
            hourly_by_day.setdefault(day, []).append(sample)

        daily_fields = (
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_probability_max",
            "precipitation_sum",
            "wind_speed_10m_max",
            "sunrise",
            "sunset",
        )
        forecast_days = []
        for trip_day in trip_dates:
            index = daily_indexes.get(trip_day)
            has_forecast = index is not None
            entry = {
                "date": trip_day,
                "forecast_available": has_forecast,
                "hourly": hourly_by_day.get(trip_day, []),
            }
            if has_forecast:
                for field in daily_fields:
                    values = daily.get(field)
                    value = values[index] if isinstance(values, list) and index < len(values) else None
                    if field in {"sunrise", "sunset"}:
                        entry[field] = value if isinstance(value, str) else None
                    else:
                        entry[field] = _finite_number(value)
                entry["condition"] = _weather_description(entry.get("weather_code"))
                entry["warnings"] = OpenMeteoWeatherService._planning_warnings(entry)
            else:
                entry["warnings"] = []
            forecast_days.append(entry)

        current_values = {}
        for field in (
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "rain",
            "weather_code",
            "wind_speed_10m",
            "wind_direction_10m",
            "cloud_cover",
        ):
            current_values[field] = _finite_number(current.get(field))
        current_values["time"] = current.get("time") if isinstance(current.get("time"), str) else None
        current_values["condition"] = _weather_description(current_values["weather_code"])

        any_forecast = any(item["forecast_available"] for item in forecast_days)
        status = "success" if any_forecast else "unavailable"
        message = None
        if not any_forecast:
            message = (
                "No requested trip dates are within the forecast dates returned by Open-Meteo. "
                "No weather values have been extrapolated."
            )
        elif not all(item["forecast_available"] for item in forecast_days):
            status = "partial"
            message = (
                "Only some trip dates are within the forecast dates returned by Open-Meteo. "
                "Other dates are marked unavailable; no weather values have been extrapolated."
            )

        return {
            "status": status,
            "destination": destination,
            "location": location,
            "timezone": data.get("timezone"),
            "current": current_values,
            "current_units": data.get("current_units") or {},
            "daily_units": data.get("daily_units") or {},
            "hourly_units": data.get("hourly_units") or {},
            "days": forecast_days,
            "forecast_days_requested": 16,
            "forecast_days_returned": len(daily_times),
            "advisory_notice": (
                "Forecast-based planning advisories use simple thresholds and are not official weather alerts."
            ),
            "message": message,
            "source": "Open-Meteo",
        }

    @staticmethod
    def _planning_warnings(day: dict) -> list[str]:
        warnings = []
        rain_probability = day.get("precipitation_probability_max")
        rain_total = day.get("precipitation_sum")
        wind_speed = day.get("wind_speed_10m_max")
        maximum = day.get("temperature_2m_max")
        minimum = day.get("temperature_2m_min")
        if (
            (rain_probability is not None and rain_probability >= 50)
            or (rain_total is not None and rain_total > 0)
        ):
            warnings.append("Rain is forecast; consider flexible indoor alternatives and rain protection.")
        if wind_speed is not None and wind_speed >= 40:
            warnings.append("Strong winds are forecast; check conditions before outdoor activities.")
        if maximum is not None and maximum >= 35:
            warnings.append("High temperatures are forecast; plan shade, hydration, and breaks.")
        if minimum is not None and minimum <= 5:
            warnings.append("Low temperatures are forecast; bring suitable warm layers.")
        return warnings

    @staticmethod
    def _unavailable(
        destination: Optional[str],
        message: str,
        status: str = "unavailable",
    ) -> dict:
        return {
            "status": status,
            "destination": destination,
            "location": None,
            "timezone": None,
            "current": None,
            "days": [],
            "message": message,
            "source": "Open-Meteo",
        }

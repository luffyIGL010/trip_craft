import unittest
from datetime import date, timedelta
from urllib.parse import parse_qs

import httpx

from services.weather_api import (
    FORECAST_URL,
    GEOCODING_URL,
    OpenMeteoWeatherService,
)


def forecast_payload():
    today = date.today()
    dates = [(today + timedelta(days=offset)).isoformat() for offset in range(16)]
    hourly_times = [
        f"{day}T{hour:02d}:00"
        for day in dates
        for hour in (8, 12, 16, 20)
    ]
    hourly_count = len(hourly_times)
    daily_count = len(dates)
    return {
        "timezone": "Asia/Kolkata",
        "current": {
            "time": f"{today.isoformat()}T12:00",
            "temperature_2m": 30.0,
            "relative_humidity_2m": 65,
            "precipitation": 1.2,
            "rain": 1.0,
            "weather_code": 61,
            "wind_speed_10m": 12.0,
            "wind_direction_10m": 180,
            "cloud_cover": 80,
        },
        "current_units": {
            "temperature_2m": "°C",
            "relative_humidity_2m": "%",
            "precipitation": "mm",
            "rain": "mm",
            "wind_speed_10m": "km/h",
        },
        "hourly": {
            "time": hourly_times,
            "temperature_2m": [28.0] * hourly_count,
            "relative_humidity_2m": [70] * hourly_count,
            "weather_code": [61] * hourly_count,
            "cloud_cover": [70] * hourly_count,
            "precipitation_probability": [65] * hourly_count,
            "precipitation": [0.5] * hourly_count,
            "rain": [0.5] * hourly_count,
            "snowfall": [0.0] * hourly_count,
        },
        "hourly_units": {
            "temperature_2m": "°C",
            "precipitation_probability": "%",
        },
        "daily": {
            "time": dates,
            "weather_code": [61] * daily_count,
            "temperature_2m_max": [31.0] * daily_count,
            "temperature_2m_min": [24.0] * daily_count,
            "precipitation_probability_max": [65] * daily_count,
            "precipitation_sum": [4.0] * daily_count,
            "wind_speed_10m_max": [18.0] * daily_count,
            "sunrise": [f"{day}T06:00" for day in dates],
            "sunset": [f"{day}T18:00" for day in dates],
        },
        "daily_units": {
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "precipitation_probability_max": "%",
            "precipitation_sum": "mm",
            "wind_speed_10m_max": "km/h",
        },
    }


class OpenMeteoWeatherServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []

    def make_service(self, geocoding_results=None, forecast=None, failure=None):
        def handler(request):
            self.calls.append(request)
            if failure == "timeout":
                raise httpx.ReadTimeout("simulated timeout", request=request)
            if failure == "network":
                raise httpx.ConnectError("simulated network failure", request=request)
            if request.url.host == "geocoding-api.open-meteo.com":
                if failure == "geocoding_http":
                    return httpx.Response(503, request=request)
                payload = geocoding_results
                if payload is None:
                    name = parse_qs(request.url.query.decode()).get("name", [""])[0]
                    locations = {
                        "Mumbai": {
                            "name": "Mumbai",
                            "latitude": 19.0728,
                            "longitude": 72.8826,
                            "country": "India",
                            "country_code": "IN",
                        },
                        "Bengaluru": {
                            "name": "Bengaluru",
                            "latitude": 12.9719,
                            "longitude": 77.5937,
                            "country": "India",
                            "country_code": "IN",
                        },
                    }
                    payload = {"results": [locations[name]] if name in locations else []}
                return httpx.Response(200, json=payload, request=request)
            if request.url.host == "api.open-meteo.com":
                if failure == "forecast_http":
                    return httpx.Response(503, request=request)
                if failure == "malformed_forecast":
                    return httpx.Response(200, text="not-json", request=request)
                return httpx.Response(200, json=forecast or forecast_payload(), request=request)
            raise AssertionError(f"Unexpected Open-Meteo URL: {request.url}")

        service = OpenMeteoWeatherService(transport=httpx.MockTransport(handler))
        service.calls = self.calls
        return service

    async def test_uses_destination_geocoding_and_returns_live_forecast_fields(self):
        service = self.make_service()
        start = date.today().isoformat()
        result = await service.get_trip_weather("Mumbai", start, start)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source"], "Open-Meteo")
        self.assertEqual(result["location"]["latitude"], 19.0728)
        self.assertEqual(result["location"]["longitude"], 72.8826)
        self.assertEqual(result["current"]["temperature_2m"], 30.0)
        self.assertEqual(result["current"]["condition"], "Slight rain")
        self.assertEqual(result["days"][0]["temperature_2m_max"], 31.0)
        self.assertEqual(result["days"][0]["hourly"][0]["time"], f"{start}T08:00")
        self.assertTrue(result["days"][0]["warnings"])

        forecast_request = next(call for call in self.calls if FORECAST_URL in str(call.url))
        query = dict(forecast_request.url.params)
        self.assertEqual(query["latitude"], "19.0728")
        self.assertEqual(query["longitude"], "72.8826")
        self.assertEqual(query["forecast_days"], "16")
        self.assertEqual(query["timezone"], "auto")
        self.assertIn("cloud_cover", query["current"])
        self.assertIn("snowfall", query["hourly"])
        self.assertIn("sunrise", query["daily"])

    async def test_different_destination_resolves_to_different_coordinates(self):
        start = date.today().isoformat()
        mumbai = await self.make_service().get_trip_weather("Mumbai", start, start)
        bengaluru = await self.make_service().get_trip_weather("Bengaluru", start, start)

        self.assertEqual(mumbai["status"], "success")
        self.assertEqual(bengaluru["status"], "success")
        self.assertNotEqual(
            (mumbai["location"]["latitude"], mumbai["location"]["longitude"]),
            (bengaluru["location"]["latitude"], bengaluru["location"]["longitude"]),
        )

    async def test_missing_invalid_and_ambiguous_destinations_do_not_call_forecast(self):
        missing = await self.make_service().get_trip_weather(None, "2026-10-22", "2026-10-22")
        invalid = await self.make_service().get_trip_weather(
            "Nowhere", "2026-10-22", "2026-10-22"
        )
        ambiguous_service = self.make_service(geocoding_results={"results": [
            {
                "name": "Springfield",
                "latitude": 39.8,
                "longitude": -89.6,
                "country": "United States",
                "country_code": "US",
            },
            {
                "name": "Springfield",
                "latitude": -27.7,
                "longitude": 152.9,
                "country": "Australia",
                "country_code": "AU",
            },
        ]})
        ambiguous = await ambiguous_service.get_trip_weather(
            "Springfield", "2026-10-22", "2026-10-22"
        )

        self.assertEqual(missing["status"], "unavailable")
        self.assertIn("destination is required", missing["message"])
        self.assertEqual(invalid["status"], "unavailable")
        self.assertIn("No matching destination", invalid["message"])
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertIn("multiple locations", ambiguous["message"])
        self.assertFalse(any(FORECAST_URL in str(call.url) for call in self.calls))

    async def test_invalid_coordinates_and_qualified_location_handling(self):
        invalid_coordinates = self.make_service(geocoding_results={"results": [
            {
                "name": "Mumbai",
                "latitude": 190,
                "longitude": 72,
                "country": "India",
                "country_code": "IN",
            }
        ]})
        result = await invalid_coordinates.get_trip_weather(
            "Mumbai", "2026-10-22", "2026-10-22"
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["location"])

        qualified = self.make_service(geocoding_results={"results": [
            {
                "name": "Paris",
                "latitude": 48.8,
                "longitude": 2.3,
                "country": "France",
                "country_code": "FR",
            },
            {
                "name": "Paris",
                "latitude": 33.6,
                "longitude": -95.5,
                "country": "United States",
                "country_code": "US",
            },
        ]})
        paris = await qualified.get_trip_weather(
            "Paris, France", date.today().isoformat(), date.today().isoformat()
        )
        self.assertEqual(paris["status"], "success")
        self.assertEqual(paris["location"]["country"], "France")

    async def test_unavailable_future_dates_are_explicit_and_not_extrapolated(self):
        service = self.make_service()
        future = date.today() + timedelta(days=200)
        result = await service.get_trip_weather(
            "Mumbai", future.isoformat(), (future + timedelta(days=1)).isoformat()
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertIn("No requested trip dates", result["message"])
        self.assertEqual(
            [day["forecast_available"] for day in result["days"]],
            [False, False],
        )
        self.assertNotIn("temperature_2m_max", result["days"][0])

    async def test_date_with_no_returned_weather_values_is_not_marked_forecastable(self):
        payload = forecast_payload()
        for field, values in payload["daily"].items():
            if field != "time":
                payload["daily"][field] = [None] * len(values)
        for field, values in payload["hourly"].items():
            if field != "time":
                payload["hourly"][field] = [None] * len(values)
        service = self.make_service(forecast=payload)

        result = await service.get_trip_weather(
            "Mumbai", date.today().isoformat(), date.today().isoformat()
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["days"][0]["forecast_available"])
        self.assertIsNone(result["days"][0]["temperature_2m_max"])

    async def test_partial_forecast_marks_unavailable_dates(self):
        service = self.make_service()
        start = date.today() + timedelta(days=15)
        result = await service.get_trip_weather(
            "Mumbai", start.isoformat(), (start + timedelta(days=2)).isoformat()
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            [day["forecast_available"] for day in result["days"]],
            [True, False, False],
        )

    async def test_http_timeout_network_and_malformed_response_fail_gracefully(self):
        for failure in ("timeout", "network", "geocoding_http", "forecast_http", "malformed_forecast"):
            with self.subTest(failure=failure):
                service = self.make_service(failure=failure)
                result = await service.get_trip_weather(
                    "Mumbai", date.today().isoformat(), date.today().isoformat()
                )
                self.assertEqual(result["status"], "error")
                self.assertIsNone(result["location"])
                self.assertTrue(result["message"])

    async def test_invalid_trip_dates_do_not_make_network_requests(self):
        service = self.make_service()
        invalid = await service.get_trip_weather("Mumbai", "2026-02-30", "2026-03-01")
        reversed_dates = await service.get_trip_weather("Mumbai", "2026-03-02", "2026-03-01")
        missing_end = await service.get_trip_weather("Mumbai", "2026-03-01")

        self.assertEqual(invalid["status"], "unavailable")
        self.assertIn("valid YYYY-MM-DD", invalid["message"])
        self.assertEqual(reversed_dates["status"], "unavailable")
        self.assertIn("on or after", reversed_dates["message"])
        self.assertEqual(missing_end["status"], "unavailable")
        self.assertIn("end date or positive trip duration", missing_end["message"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()

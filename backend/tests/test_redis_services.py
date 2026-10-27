import asyncio
import hashlib
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from services.rate_limiter import (
    AuthRateLimitMiddleware,
    RedisRateLimiter,
    rate_limit_policy,
)
from services.redis_manager import RedisCache, RedisConnectionManager
from services.weather_api import OpenMeteoWeatherService


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.now = 0
        self.fail_reads = False
        self.fail_eval = False
        self.closed = False
        self.ping_count = 0

    async def get(self, key):
        if self.fail_reads:
            raise RedisError("simulated read failure")
        entry = self.values.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= self.now:
            del self.values[key]
            return None
        return value

    async def set(self, key, value, ex):
        self.values[key] = (value, self.now + ex)

    async def eval(self, _script, _key_count, key, window_seconds):
        if self.fail_eval:
            raise RedisError("simulated rate-limit failure")
        current = self.values.get(key)
        if current is None or current[1] <= self.now:
            count = 1
            expires_at = self.now + int(window_seconds)
        else:
            count = int(current[0]) + 1
            expires_at = current[1]
        self.values[key] = (str(count), expires_at)
        return count, expires_at - self.now

    async def ping(self):
        self.ping_count += 1
        return True

    async def aclose(self):
        self.closed = True


class FakeManager:
    def __init__(self, client):
        self.client = client
        self.configured = True

    async def get_client(self):
        return self.client


class RedisCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_uses_distinct_keys_and_refreshes_expired_values(self):
        redis = FakeRedis()
        cache = RedisCache(FakeManager(redis))
        loads = []

        async def load():
            loads.append(len(loads) + 1)
            return {"result": loads[-1]}

        first = await cache.get_or_set(
            "flight",
            {"origin": "DEL", "date": "2026-11-01"},
            load,
            ttl_seconds=5,
        )
        repeated = await cache.get_or_set(
            "flight",
            {"date": "2026-11-01", "origin": "DEL"},
            load,
            ttl_seconds=5,
        )
        different_search = await cache.get_or_set(
            "flight",
            {"origin": "BOM", "date": "2026-11-01"},
            load,
            ttl_seconds=5,
        )

        self.assertEqual(first, {"result": 1})
        self.assertEqual(repeated, first)
        self.assertEqual(different_search, {"result": 2})
        self.assertEqual(len(loads), 2)

        redis.now += 6
        refreshed = await cache.get_or_set(
            "flight",
            {"origin": "DEL", "date": "2026-11-01"},
            load,
            ttl_seconds=5,
        )
        self.assertEqual(refreshed, {"result": 3})
        self.assertEqual(len(loads), 3)

    async def test_cache_skips_errors_and_falls_back_when_redis_fails(self):
        redis = FakeRedis()
        cache = RedisCache(FakeManager(redis))
        attempts = 0

        async def load_error_then_success():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return {"error": "provider unavailable"}
            return {"result": "fresh"}

        should_cache = lambda value: "error" not in value
        await cache.get_or_set(
            "weather",
            {"destination": "mumbai"},
            load_error_then_success,
            should_cache=should_cache,
        )
        fresh = await cache.get_or_set(
            "weather",
            {"destination": "mumbai"},
            load_error_then_success,
            should_cache=should_cache,
        )
        self.assertEqual(fresh, {"result": "fresh"})
        self.assertEqual(attempts, 2)

        redis.fail_reads = True
        fallback = await cache.get_or_set(
            "weather",
            {"destination": "bengaluru"},
            load_error_then_success,
        )
        self.assertEqual(fallback, {"result": "fresh"})


class RedisManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_health_and_shutdown_reuse_one_client(self):
        fake_redis = FakeRedis()
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/0"}):
            manager = RedisConnectionManager()
            with patch(
                "services.redis_manager.Redis.from_url",
                return_value=fake_redis,
            ) as from_url:
                self.assertTrue(await manager.connect())
                self.assertEqual(await manager.health_status(), "connected")
                self.assertEqual(from_url.call_count, 1)
                await manager.close()

        self.assertTrue(fake_redis.closed)
        self.assertIsNone(manager.client)

    async def test_weather_geocoding_is_cached_but_destination_isolation_is_kept(self):
        calls = []

        def handler(request):
            calls.append(request)
            name = request.url.params["name"].strip().casefold()
            locations = {
                "mumbai": {
                    "name": "Mumbai",
                    "latitude": 19.0728,
                    "longitude": 72.8826,
                    "country": "India",
                    "country_code": "IN",
                },
                "bengaluru": {
                    "name": "Bengaluru",
                    "latitude": 12.9719,
                    "longitude": 77.5937,
                    "country": "India",
                    "country_code": "IN",
                },
            }
            return httpx.Response(
                200,
                json={"results": [locations[name]]},
                request=request,
            )

        cache = RedisCache(FakeManager(FakeRedis()))
        service = OpenMeteoWeatherService(
            transport=httpx.MockTransport(handler),
            cache=cache,
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            first = await service._resolve_location(client, " Mumbai ")
            same_location = await service._resolve_location(client, "mumbai")
            other_location = await service._resolve_location(client, "Bengaluru")

        self.assertEqual(first, same_location)
        self.assertNotEqual(first[0]["latitude"], other_location[0]["latitude"])
        self.assertEqual(len(calls), 2)


class RedisRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_is_per_identity_and_atomic_window_expires(self):
        redis = FakeRedis()
        limiter = RedisRateLimiter(FakeManager(redis))

        first = await limiter.check("itinerary", "user:1", 1, 10)
        blocked = await limiter.check("itinerary", "user:1", 1, 10)
        another_user = await limiter.check("itinerary", "user:2", 1, 10)
        self.assertTrue(first.allowed)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.remaining, 0)
        self.assertTrue(another_user.allowed)

        redis.now += 10
        after_window = await limiter.check("itinerary", "user:1", 1, 10)
        self.assertTrue(after_window.allowed)

    async def test_auth_middleware_returns_429_and_does_not_trust_forwarded_identity(self):
        fake_redis = FakeRedis()
        manager = FakeManager(fake_redis)
        app = FastAPI()
        app.add_middleware(AuthRateLimitMiddleware, manager=manager)

        @app.post("/auth/login")
        async def login():
            return {"status": "ok"}

        with patch.dict(
            os.environ,
            {
                "REDIS_RATE_LIMIT_ENABLED": "true",
                "REDIS_LOGIN_RATE_LIMIT_REQUESTS": "1",
                "REDIS_LOGIN_RATE_LIMIT_WINDOW_SECONDS": "60",
            },
        ):
            with TestClient(app) as client:
                allowed = client.post(
                    "/auth/login",
                    headers={"X-Forwarded-For": "203.0.113.99"},
                )
                limited = client.post("/auth/login")

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)
        self.assertEqual(limited.headers["X-RateLimit-Limit"], "1")
        self.assertTrue(
            any(":auth_login:" in key for key in fake_redis.values)
        )
        spoofed_hash = hashlib.sha256(b"203.0.113.99").hexdigest()
        self.assertFalse(any(spoofed_hash in key for key in fake_redis.values))

    async def test_auth_middleware_fails_closed_when_configured_redis_is_unavailable(self):
        app = FastAPI()
        app.add_middleware(AuthRateLimitMiddleware, manager=FakeManager(None))

        @app.post("/auth/register")
        async def register():
            return {"status": "ok"}

        with patch.dict(os.environ, {"REDIS_RATE_LIMIT_ENABLED": "true"}):
            with TestClient(app) as client:
                response = client.post("/auth/register")

        self.assertEqual(response.status_code, 503)


class RateLimitPolicyTests(unittest.TestCase):
    def test_endpoint_policies_have_distinct_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(rate_limit_policy("auth_login"), (10, 60))
            self.assertEqual(rate_limit_policy("auth_register"), (5, 3600))
            self.assertEqual(rate_limit_policy("itinerary"), (6, 60))


if __name__ == "__main__":
    unittest.main()

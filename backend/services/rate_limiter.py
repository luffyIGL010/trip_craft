import hashlib
import logging
import os
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from redis.exceptions import RedisError

from services.redis_manager import RedisConnectionManager, positive_int_setting

logger = logging.getLogger(__name__)

_INCREMENT_WINDOW_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {count, redis.call('TTL', KEYS[1])}
"""


class RateLimitUnavailable(RuntimeError):
    """Raised when a configured Redis rate limit cannot be enforced."""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


def rate_limiting_enabled() -> bool:
    value = os.getenv("REDIS_RATE_LIMIT_ENABLED", "true").strip().casefold()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ValueError("REDIS_RATE_LIMIT_ENABLED must be a boolean")


def rate_limit_policy(scope: str) -> tuple[int, int]:
    default_requests = positive_int_setting("REDIS_RATE_LIMIT_REQUESTS", 60)
    default_window = positive_int_setting("REDIS_RATE_LIMIT_WINDOW_SECONDS", 60)
    if scope == "auth_login":
        return (
            positive_int_setting("REDIS_LOGIN_RATE_LIMIT_REQUESTS", 10),
            positive_int_setting("REDIS_LOGIN_RATE_LIMIT_WINDOW_SECONDS", 60),
        )
    if scope == "auth_register":
        return (
            positive_int_setting("REDIS_REGISTER_RATE_LIMIT_REQUESTS", 5),
            positive_int_setting("REDIS_REGISTER_RATE_LIMIT_WINDOW_SECONDS", 3600),
        )
    if scope == "itinerary":
        return (
            min(positive_int_setting("REDIS_AI_RATE_LIMIT_REQUESTS", 6), default_requests),
            positive_int_setting(
                "REDIS_AI_RATE_LIMIT_WINDOW_SECONDS",
                default_window,
            ),
        )
    raise ValueError(f"Unknown rate limit scope: {scope}")


def validate_rate_limit_configuration() -> None:
    if not rate_limiting_enabled():
        return
    positive_int_setting("REDIS_RATE_LIMIT_REQUESTS", 60)
    positive_int_setting("REDIS_RATE_LIMIT_WINDOW_SECONDS", 60)
    for scope in ("auth_login", "auth_register", "itinerary"):
        rate_limit_policy(scope)


class RedisRateLimiter:
    def __init__(self, manager: RedisConnectionManager) -> None:
        self.manager = manager

    async def check(
        self,
        scope: str,
        identity: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitResult:
        client = await self.manager.get_client()
        if client is None:
            raise RateLimitUnavailable("Redis rate limiting is unavailable")

        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        key = f"travel-craft:rate:{scope}:{identity_hash}"
        try:
            count, ttl = await client.eval(
                _INCREMENT_WINDOW_SCRIPT,
                1,
                key,
                window_seconds,
            )
        except (RedisError, TimeoutError) as exc:
            logger.warning("Redis rate-limit operation failed.")
            raise RateLimitUnavailable(
                "Redis rate limiting is unavailable"
            ) from exc

        count = int(count)
        retry_after = max(int(ttl), 1)
        return RateLimitResult(
            allowed=count <= limit,
            limit=limit,
            remaining=max(limit - count, 0),
            retry_after_seconds=retry_after,
        )


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, manager: RedisConnectionManager) -> None:
        super().__init__(app)
        self.manager = manager
        self.limiter = RedisRateLimiter(manager)

    async def dispatch(self, request: Request, call_next):
        routes = {
            "/auth/login": "auth_login",
            "/auth/register": "auth_register",
        }
        scope = routes.get(request.url.path)
        if (
            request.method != "POST"
            or scope is None
            or not rate_limiting_enabled()
            or not self.manager.configured
        ):
            return await call_next(request)

        client = request.client
        if client is None or not client.host:
            return JSONResponse(
                {"detail": "Authentication is temporarily unavailable."},
                status_code=503,
            )

        limit, window_seconds = rate_limit_policy(scope)
        try:
            result = await self.limiter.check(
                scope,
                client.host,
                limit,
                window_seconds,
            )
        except RateLimitUnavailable:
            return JSONResponse(
                {"detail": "Authentication is temporarily unavailable."},
                status_code=503,
            )

        headers = {
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": str(result.remaining),
        }
        if not result.allowed:
            headers["Retry-After"] = str(result.retry_after_seconds)
            return JSONResponse(
                {"detail": "Too many requests. Please try again later."},
                status_code=429,
                headers=headers,
            )

        response = await call_next(request)
        for name, value in headers.items():
            response.headers[name] = value
        return response

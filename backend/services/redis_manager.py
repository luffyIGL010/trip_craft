import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


def positive_int_setting(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def validate_cache_configuration() -> None:
    positive_int_setting("REDIS_CACHE_DEFAULT_TTL", 300)
    positive_int_setting("REDIS_GEOCODING_CACHE_TTL", 86400)


class RedisConnectionManager:
    def __init__(self) -> None:
        self.url = os.getenv("REDIS_URL")
        self.connect_timeout = float(os.getenv("REDIS_CONNECT_TIMEOUT_SECONDS", "2"))
        self.socket_timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "2"))
        if self.connect_timeout <= 0 or self.socket_timeout <= 0:
            raise ValueError("Redis connection and socket timeouts must be positive")
        self.client: Redis | None = None
        self._connect_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.url and self.url.strip())

    async def connect(self) -> bool:
        if not self.configured:
            return False
        if self.client is not None:
            return True

        async with self._connect_lock:
            if self.client is not None:
                return True

            client: Redis | None = None
            try:
                client = Redis.from_url(
                    self.url,
                    decode_responses=True,
                    socket_connect_timeout=self.connect_timeout,
                    socket_timeout=self.socket_timeout,
                    health_check_interval=30,
                )
                await client.ping()
            except (RedisError, TimeoutError, ValueError):
                if client is not None:
                    await self._close_client(client)
                logger.warning(
                    "Redis is unavailable; cache requests will use the provider "
                    "and Redis-protected requests will fail closed."
                )
                return False

            self.client = client
            return True

    async def get_client(self) -> Redis | None:
        if self.client is None:
            await self.connect()
        return self.client

    async def health_status(self) -> str:
        if not self.configured:
            return "not_configured"
        client = await self.get_client()
        if client is None:
            return "unavailable"
        try:
            await client.ping()
        except (RedisError, TimeoutError):
            return "unavailable"
        return "connected"

    async def close(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            await self._close_client(client)

    @staticmethod
    async def _close_client(client: Redis) -> None:
        try:
            await client.aclose()
        except (RedisError, TimeoutError):
            logger.warning("Redis connection pool did not close cleanly.")


class RedisCache:
    def __init__(self, manager: RedisConnectionManager) -> None:
        self.manager = manager

    @staticmethod
    def key_for(namespace: str, parameters: dict[str, Any]) -> str:
        normalized = json.dumps(
            parameters,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"travel-craft:cache:{namespace}:{digest}"

    async def get_or_set(
        self,
        namespace: str,
        parameters: dict[str, Any],
        loader: Callable[[], Awaitable[Any]],
        ttl_seconds: int | None = None,
        should_cache: Callable[[Any], bool] | None = None,
        validate_cached: Callable[[Any], bool] | None = None,
    ) -> Any:
        ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else positive_int_setting("REDIS_CACHE_DEFAULT_TTL", 300)
        )
        if ttl <= 0:
            raise ValueError("Cache TTL must be a positive integer")

        client = await self.manager.get_client()
        if client is None:
            return await loader()

        key = self.key_for(namespace, parameters)
        try:
            cached = await client.get(key)
        except (RedisError, TimeoutError):
            logger.warning("Redis cache read failed; fetching fresh provider data.")
        else:
            if cached is not None:
                try:
                    value = json.loads(cached)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Ignoring an invalid Redis cache entry.")
                else:
                    if validate_cached is None or validate_cached(value):
                        return value
                    logger.warning("Ignoring a Redis cache entry with an invalid shape.")

        value = await loader()
        cacheable = value is not None and (
            should_cache is None or should_cache(value)
        )
        if not cacheable:
            return value

        try:
            serialized = json.dumps(
                value,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        except (TypeError, ValueError):
            logger.warning("Skipping a Redis cache value that is not JSON serializable.")
            return value

        try:
            await client.set(key, serialized, ex=ttl)
        except (RedisError, TimeoutError):
            logger.warning("Redis cache write failed; returning fresh provider data.")
        return value


redis_manager = RedisConnectionManager()
redis_cache = RedisCache(redis_manager)

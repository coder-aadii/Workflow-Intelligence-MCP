"""Redis-backed and in-memory cache adapters.

Provides:
- ``CacheAdapter``: abstract interface for caching with TTL support.
- ``RedisCacheAdapter``: real Redis cache via ``redis.asyncio`` (used when a
  Redis server is reachable).
- ``InMemoryCacheAdapter``: offline mock backed by a dict with TTL
  tracking. Used by default in tests and when Redis is unavailable.

Usage patterns from Phase 6:
- Cache computed Process Discovery transition graphs (TTL: 1 hour).
- Track active exception triage sessions to prevent concurrent duplicate
  escalations.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import cast

import redis.asyncio as redis_async


class CacheAdapterError(Exception):
    """Raised when cache operations fail."""


class CacheAdapter(ABC):
    """Abstract interface for a key/value cache with TTL support."""

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """Return the cached value for ``key``, or None if absent/expired."""

    @abstractmethod
    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store ``value`` under ``key`` with the given TTL in seconds."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove ``key`` from the cache if present."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True when ``key`` is present and not expired."""


class RedisCacheAdapter(CacheAdapter):
    """Real Redis cache adapter using ``redis.asyncio``.

    Connects to a Redis server and performs get/set/delete/exists with TTL.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
    ) -> None:
        self._redis_url = redis_url
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout
        self._client: redis_async.Redis | None = None

    async def _get_client(self) -> redis_async.Redis:
        """Return a lazily-created async Redis client."""
        if self._client is None:
            self._client = redis_async.from_url(
                self._redis_url,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_connect_timeout,
                decode_responses=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the Redis client connection if open."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001 - best effort close
                pass
            self._client = None

    async def get(self, key: str) -> str | None:
        """Return the cached value for ``key``, or None if absent/expired."""
        client = await self._get_client()
        try:
            result = await client.get(key)
        except Exception as exc:  # noqa: BLE001 - wrap Redis errors
            raise CacheAdapterError(f"Redis GET failed for {key!r}: {exc}") from exc
        # ``decode_responses=True`` guarantees a ``str``; cast for the type
        # checker since the redis stubs may not reflect that precisely.
        return None if result is None else cast(str, result)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store ``value`` under ``key`` with the given TTL in seconds."""
        client = await self._get_client()
        try:
            await client.set(key, value, ex=ttl_seconds)
        except Exception as exc:  # noqa: BLE001 - wrap Redis errors
            raise CacheAdapterError(f"Redis SET failed for {key!r}: {exc}") from exc

    async def delete(self, key: str) -> None:
        """Remove ``key`` from the cache if present."""
        client = await self._get_client()
        try:
            await client.delete(key)
        except Exception as exc:  # noqa: BLE001 - wrap Redis errors
            raise CacheAdapterError(f"Redis DELETE failed for {key!r}: {exc}") from exc

    async def exists(self, key: str) -> bool:
        """Return True when ``key`` is present and not expired."""
        client = await self._get_client()
        try:
            return await client.exists(key) == 1
        except Exception as exc:  # noqa: BLE001 - wrap Redis errors
            raise CacheAdapterError(f"Redis EXISTS failed for {key!r}: {exc}") from exc


class InMemoryCacheAdapter(CacheAdapter):
    """Offline in-memory cache adapter with TTL tracking.

    Safe for unit and integration tests; makes no network calls. Entries
    expire based on wall-clock time once their TTL elapses.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}

    def _is_expired(self, expiry: float) -> bool:
        return time.monotonic() > expiry

    async def get(self, key: str) -> str | None:
        """Return the cached value for ``key``, or None if absent/expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if self._is_expired(expiry):
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store ``value`` under ``key`` with the given TTL in seconds."""
        expiry = time.monotonic() + ttl_seconds
        self._store[key] = (value, expiry)

    async def delete(self, key: str) -> None:
        """Remove ``key`` from the cache if present."""
        self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        """Return True when ``key`` is present and not expired."""
        entry = self._store.get(key)
        if entry is None:
            return False
        if self._is_expired(entry[1]):
            del self._store[key]
            return False
        return True

    def clear(self) -> None:
        """Remove all entries (test helper)."""
        self._store.clear()

    def size(self) -> int:
        """Return the number of non-expired entries (test helper)."""
        now = time.monotonic()
        expired = [k for k, (_, e) in self._store.items() if now > e]
        for k in expired:
            del self._store[k]
        return len(self._store)

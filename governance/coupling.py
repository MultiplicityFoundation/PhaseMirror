"""
Phase Mirror — Redis Coupling Bus
ADR-MVP-003: Replaces PIRTM spectral radius calculations with Redis Streams + rate limiting.

Responsibilities:
  - publish(agent_id, topic, payload): write to a Redis Stream
  - consume(topic, consumer_group): read from a stream (blocking)
  - check_rate(agent_id, topic): sliding-window rate limiter
    Returns (allowed: bool, current_count: int).
    If allowed=False, the caller MUST raise ConstitutionViolation("L0-coupling", ...)
    to surface resonance cascade prevention as a constitutional event.

Configuration (environment variables):
  REDIS_URL                    default: redis://localhost:6379/0
  COUPLING_RATE_WINDOW_SECONDS default: 60
  COUPLING_RATE_MAX_COUNT      default: 30
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
RATE_WINDOW_SECONDS = int(os.environ.get("COUPLING_RATE_WINDOW_SECONDS", "60"))
RATE_MAX_COUNT = int(os.environ.get("COUPLING_RATE_MAX_COUNT", "30"))

# Stream key prefix
STREAM_PREFIX = "pm:stream:"
RATE_KEY_PREFIX = "pm:rate:"


# ---------------------------------------------------------------------------
# RedisCoupling
# ---------------------------------------------------------------------------

class RedisCoupling:
    """
    Thin wrapper around redis-py providing Phase Mirror's coupling primitives.

    This is the MVP replacement for pirtm/ spectral network coupling (ADR-AGI-009).
    The mathematical invariant preserved: no agent-to-agent message loop may
    exceed COUPLING_RATE_MAX_COUNT messages within COUPLING_RATE_WINDOW_SECONDS.
    This is the operational definition of rho < 1.0 (spectral radius < 1) for
    the governance MVP — without eigenvalue computation.
    """

    def __init__(self, redis_url: str = DEFAULT_REDIS_URL) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)
        logger.info("RedisCoupling initialised at %s", redis_url)

    # --- Liveness ---

    def ping(self) -> bool:
        """Returns True if Redis is reachable."""
        try:
            return self._client.ping()
        except redis.RedisError:
            return False

    def close(self) -> None:
        self._client.close()

    # --- Pub/Sub (Redis Streams) ---

    def publish(
        self,
        agent_id: str,
        topic: str,
        payload: dict[str, Any],
        maxlen: int = 1000,
    ) -> str:
        """
        Writes a message to the Redis Stream for `topic`.
        Returns the Redis stream entry ID.
        `maxlen` caps the stream length to prevent unbounded growth.
        """
        stream_key = f"{STREAM_PREFIX}{topic}"
        entry = {
            "agent_id": agent_id,
            "payload": json.dumps(payload),
            "ts": str(time.time()),
        }
        entry_id = self._client.xadd(stream_key, entry, maxlen=maxlen, approximate=True)
        logger.debug("Published to %s: %s", stream_key, entry_id)
        return entry_id

    def consume(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
        count: int = 10,
        block_ms: int = 2000,
    ) -> list[dict]:
        """
        Reads messages from a Redis Stream consumer group.
        Creates the group and stream if they don't exist.
        Returns a list of decoded message dicts.
        """
        stream_key = f"{STREAM_PREFIX}{topic}"

        # Ensure group exists
        try:
            self._client.xgroup_create(stream_key, consumer_group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        raw = self._client.xreadgroup(
            groupname=consumer_group,
            consumername=consumer_name,
            streams={stream_key: ">"},
            count=count,
            block=block_ms,
        )

        messages = []
        if raw:
            for _stream, entries in raw:
                for entry_id, fields in entries:
                    messages.append({
                        "id": entry_id,
                        "agent_id": fields.get("agent_id"),
                        "payload": json.loads(fields.get("payload", "{}")),
                        "ts": fields.get("ts"),
                    })
        return messages

    def ack(self, topic: str, consumer_group: str, entry_id: str) -> None:
        """Acknowledges a consumed message (prevents redelivery)."""
        stream_key = f"{STREAM_PREFIX}{topic}"
        self._client.xack(stream_key, consumer_group, entry_id)

    # --- Rate limiting (sliding window) ---

    def check_rate(
        self,
        agent_id: str,
        topic: str,
        window_seconds: int = RATE_WINDOW_SECONDS,
        max_count: int = RATE_MAX_COUNT,
    ) -> tuple[bool, int]:
        """
        Sliding-window rate limiter using a Redis sorted set.

        Each call registers the current timestamp as a score.
        Entries older than `window_seconds` are pruned before counting.

        Returns:
            (True, count)   — request is allowed
            (False, count)  — request exceeds limit; caller must raise ConstitutionViolation

        This is the MVP equivalent of enforcing spectral radius rho < 1.0
        (ADR-AGI-009): no feedback loop may sustain more than `max_count`
        messages per `window_seconds` without human-observable throttling.
        """
        rate_key = f"{RATE_KEY_PREFIX}{agent_id}:{topic}"
        now = time.time()
        window_start = now - window_seconds

        pipe = self._client.pipeline()
        # Remove expired entries
        pipe.zremrangebyscore(rate_key, "-inf", window_start)
        # Add current request
        pipe.zadd(rate_key, {str(now): now})
        # Count entries in window
        pipe.zcard(rate_key)
        # Set TTL so the key self-expires if the agent goes quiet
        pipe.expire(rate_key, window_seconds * 2)
        results = pipe.execute()

        current_count: int = results[2]
        allowed = current_count <= max_count

        if not allowed:
            logger.warning(
                "Rate limit exceeded: agent=%s topic=%s count=%d limit=%d window=%ds",
                agent_id, topic, current_count, max_count, window_seconds,
            )
        return allowed, current_count

    def get_rate_counts(self, agent_id: str, topic: str) -> dict:
        """Diagnostic: returns current sliding-window count for /health endpoint."""
        rate_key = f"{RATE_KEY_PREFIX}{agent_id}:{topic}"
        now = time.time()
        window_start = now - RATE_WINDOW_SECONDS
        self._client.zremrangebyscore(rate_key, "-inf", window_start)
        count = self._client.zcard(rate_key)
        return {
            "agent_id": agent_id,
            "topic": topic,
            "current_count": count,
            "limit": RATE_MAX_COUNT,
            "window_seconds": RATE_WINDOW_SECONDS,
        }

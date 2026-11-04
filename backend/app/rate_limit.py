"""Redis-backed rate limiting for /analyze.

Audit note: the `redis` package (requirements.txt) and a running `redis`
service (docker-compose.yml, `REDIS_URL` already wired into every backend
container's environment) both existed in this repository *before* this
change, with zero application code ever calling either of them -- a real
"infrastructure exists but nothing uses it" gap found while auditing this
project for Day 2 work. This module is the first real use of both.

Why /analyze specifically: it's the most expensive endpoint in the system
-- one call uploads a file to S3/MinIO, writes a Postgres row, and
publishes a Kafka event that fans out to two separate worker processes
(OCR, then forensics). A single scripted/misbehaving client hammering this
endpoint doesn't just slow down the API -- it can flood the whole
distributed pipeline behind it. Nothing else in this API (health, status
polling, auth) does comparable downstream work, so nothing else is
rate-limited here.

Algorithm: fixed-window counter using atomic MULTI/EXEC with INCR and
EXPIRE NX. Redis 7+ is required for the NX expiration option.

Fails OPEN (allows the request) if Redis itself is unreachable. This is a
security trade-off, not an oversight: CertiFake is a certificate-analysis
upload API, not a payments or auth-attempt endpoint, and a Redis outage
temporarily removing anti-abuse throttling is judged less harmful than a
Redis outage taking down certificate analysis entirely. Documented here so
it is visible in review, not discovered by accident during an incident.
"""
import redis

from app.config import settings

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
    return _client


def check_rate_limit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds).

    retry_after_seconds is 0 when allowed is True, and is the Redis key's
    remaining TTL (at least 1) when allowed is False.
    """
    try:
        client = _get_client()
        redis_key = f"ratelimit:{key}"
        # Atomic MULTI/EXEC prevents interruption between increment and expiry.
        # NX preserves the original window and repairs keys with missing TTLs.
        with client.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds, nx=True)
            current, _ = pipe.execute()
        if current > limit:
            ttl = client.ttl(redis_key)
            return False, max(ttl, 1)
        return True, 0
    except redis.RedisError:
        return True, 0

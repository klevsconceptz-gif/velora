"""Fixed-window rate limiting backed by SQLite.

Applied to signup, login, verification resends, checkout, messaging, reports and
admin mutations. Counters are keyed by a salted hash of the identity, so raw IP
addresses and email addresses are never stored in the counter table.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .db import now_iso
from .security import hmac_hex

CLEANUP_AFTER_SECONDS = 60 * 60 * 24


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    count: int
    remaining: int
    retry_after: int
    window_seconds: int


class RateLimiter:
    def __init__(self, db, secret: bytes):
        self.db = db
        self.secret = secret

    def _bucket(self, action: str, identifier: str) -> str:
        return hmac_hex(self.secret, f"{action}:{identifier}")[:40]

    def check(self, action: str, identifier: str, *, limit: int, window_seconds: int,
              cost: int = 1, now: float | None = None, conn=None) -> RateLimitResult:
        """Consume ``cost`` allowance and report whether the action may proceed.

        The increment and the window sum happen in one write transaction so
        concurrent requests cannot slip past the limit.
        """
        moment = time.time() if now is None else now
        # A zero or negative window would divide by zero; treat it as one second.
        window_seconds = max(1, int(window_seconds))
        window_start = int(moment // window_seconds) * window_seconds
        window_end = window_start + window_seconds
        bucket = self._bucket(action, identifier or "anonymous")

        with self.db.transaction(conn) as conn:
            conn.execute(
                "DELETE FROM rate_limits WHERE bucket = ? AND window_start < ?",
                (bucket, window_start - CLEANUP_AFTER_SECONDS),
            )
            conn.execute(
                """
                INSERT INTO rate_limits (bucket, window_start, hit_count)
                VALUES (?, ?, ?)
                ON CONFLICT(bucket, window_start)
                DO UPDATE SET hit_count = hit_count + excluded.hit_count
                """,
                (bucket, window_start, max(1, cost)),
            )
            row = conn.execute(
                "SELECT COALESCE(SUM(hit_count), 0) AS total FROM rate_limits WHERE bucket = ? AND window_start >= ?",
                (bucket, window_start),
            ).fetchone()
            total = int(row["total"] if row else 0)
            if window_start > 0:
                conn.execute(
                    "DELETE FROM rate_limits WHERE bucket = ? AND window_start < ?",
                    (bucket, window_start),
                )

        allowed = total <= limit
        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            count=total,
            remaining=max(0, limit - total),
            retry_after=0 if allowed else max(1, int(window_end - moment) + 1),
            window_seconds=window_seconds,
        )

    def peek(self, action: str, identifier: str, *, window_seconds: int, now: float | None = None) -> int:
        moment = time.time() if now is None else now
        window_seconds = max(1, int(window_seconds))
        window_start = int(moment // window_seconds) * window_seconds
        bucket = self._bucket(action, identifier or "anonymous")
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(hit_count), 0) AS total FROM rate_limits WHERE bucket = ? AND window_start >= ?",
                (bucket, window_start),
            ).fetchone()
        return int(row["total"] if row else 0)

    def reset(self, action: str, identifier: str) -> None:
        bucket = self._bucket(action, identifier or "anonymous")
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM rate_limits WHERE bucket = ?", (bucket,))

    def prune(self, *, before: str | None = None) -> int:
        cutoff = before or now_iso()
        del cutoff  # kept for signature symmetry with other maintenance helpers
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM rate_limits WHERE window_start < ?",
                (int(time.time()) - CLEANUP_AFTER_SECONDS,),
            )
            return cursor.rowcount or 0

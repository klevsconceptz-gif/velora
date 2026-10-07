"""Shared service context: database, rate limiter, email, media store, clock."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .. import media as media_module
from ..config import Config, get_config
from ..db import Database
from ..emaillib import EmailService
from ..ratelimit import RateLimiter


@dataclass
class Context:
    config: Config
    db: Database
    limiter: RateLimiter
    email: EmailService
    media: media_module.MediaStore
    started_at: float

    def now(self) -> float:
        return time.time()

    def gate(self, action: str, identifier: str | None, conn=None) -> None:
        """Apply the configured rate limit for an action, or raise 429.

        Pass ``conn`` when the caller already holds a write transaction so the
        counter increment joins it instead of opening a second writer.

        Imported lazily to avoid a circular import with :mod:`server.http`.
        """
        from ..http import rate_limited

        rule = self.config.rate_limit(action)
        result = self.limiter.check(action, identifier or "anonymous", limit=rule.limit,
                                    window_seconds=rule.window_seconds, conn=conn)
        if not result.allowed:
            raise rate_limited(
                "Too many attempts from here. Please wait a moment and try again.",
                result.retry_after,
            )

    def gate_or_flag(self, action: str, identifier: str | None, conn=None) -> bool:
        rule = self.config.rate_limit(action)
        result = self.limiter.check(action, identifier or "anonymous", limit=rule.limit,
                                    window_seconds=rule.window_seconds, conn=conn)
        return result.allowed

    def client_hash(self, identifier: str | None) -> str:
        from ..security import fingerprint

        return fingerprint(self.config.secret_key, "client", identifier or "unknown")


def build_context(config: Config | None = None) -> Context:
    config = config or get_config()
    db = Database(config.db_path)
    return Context(
        config=config,
        db=db,
        limiter=RateLimiter(db, config.secret_key),
        email=EmailService(config),
        media=media_module.MediaStore(config),
        started_at=time.time(),
    )

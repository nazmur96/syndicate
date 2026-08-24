"""Shared platform types.

Fail-soft contract: adapters raise; the orchestrator catches per platform and
converts to a ``PlatformResult`` so one platform failing never blocks the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone


class PlatformError(Exception):
    """Any adapter failure. Caught per platform by the orchestrator."""


class CredentialError(PlatformError):
    """Missing, rejected, or expired credentials."""


class TokenExpired(CredentialError):
    """The access token is past its lifetime and needs a manual re-auth."""


class RateLimited(PlatformError):
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class WriteResult:
    remote_id: str | None = None
    url: str | None = None
    state: str | None = None


@dataclass
class PlatformResult:
    """Per-platform outcome, reported in the tracking output."""

    platform: str
    #: created | updated | published | skipped | failed
    action: str
    remote_id: str | None = None
    url: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.action != "failed"


def parse_retry_after(value: str | None) -> int | None:
    """Retry-After is either delta-seconds or an RFC 7231 HTTP-date.

    dev.to returns both forms in practice, so parse both. See RFC 9110 s10.2.3.
    """
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, int((when - datetime.now(timezone.utc)).total_seconds()))

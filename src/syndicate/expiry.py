"""LinkedIn access-token expiry arithmetic.

A member access token is an opaque string: it carries no readable expiry, and
LinkedIn offers no introspection endpoint for it. So the warning is driven by
two independent signals:

  * a *recorded issue date* -- the ``LINKEDIN_TOKEN_ISSUED_AT`` repo variable,
    set by hand whenever the OAuth flow is re-run -- plus the documented
    60-day lifetime; and
  * a *live probe* against the API, which can only ever tell us that the token
    is already dead, never how long it has left.

The date does the forecasting; the probe is the tiebreaker. A probe that says
"expired" always wins, because a token can also be revoked early.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from .platforms.linkedin import TOKEN_LIFETIME_DAYS

#: Repo *variable* (not a secret): the date the current token was issued.
TOKEN_ISSUED_AT = "LINKEDIN_TOKEN_ISSUED_AT"

#: Warn this many days before the token lapses -- long enough to schedule the
#: manual re-auth, short enough not to be background noise.
DEFAULT_WARN_DAYS = 14

OK = "ok"
WARNING = "warning"
EXPIRED = "expired"
UNKNOWN = "unknown"
NOT_CONFIGURED = "not_configured"

#: Probe verdicts, as produced by the CLI's live check.
PROBE_VALID = "valid"
PROBE_EXPIRED = "expired"
PROBE_INCONCLUSIVE = "inconclusive"

_SET_IT_HINT = (
    f"Set the {TOKEN_ISSUED_AT} repository variable to the date you last ran the "
    f"LinkedIn OAuth flow (YYYY-MM-DD) so expiry can be forecast -- see docs/SETUP.md."
)


@dataclass(frozen=True)
class TokenStatus:
    status: str
    message: str
    days_remaining: int | None = None
    issued_at: str | None = None
    expires_at: str | None = None
    probe: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def parse_issued_at(value: str) -> date:
    """Parse the recorded issue date. Accepts a date or a full timestamp."""
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(
            f"{TOKEN_ISSUED_AT} must be an ISO date such as 2026-08-25 (got {value!r})."
        ) from exc


def token_status(
    issued_at: str | None,
    probe: str | None,
    warn_days: int = DEFAULT_WARN_DAYS,
    today: date | None = None,
) -> TokenStatus:
    """Combine the recorded issue date and the live probe into one verdict.

    ``probe`` is ``None`` when no LinkedIn credentials are configured at all,
    which is a normal state (dev.to-only repos) and not a failure.
    """
    today = today or date.today()

    if probe is None:
        return TokenStatus(
            status=NOT_CONFIGURED,
            message="LinkedIn is not configured in this repository; nothing to warn about.",
            probe=None,
        )

    if probe == PROBE_EXPIRED:
        return TokenStatus(
            status=EXPIRED,
            message=(
                "LinkedIn rejected the token: it has expired or been revoked. "
                "Re-run the OAuth flow -- see docs/SETUP.md."
            ),
            issued_at=issued_at,
            probe=probe,
        )

    if not issued_at:
        return TokenStatus(
            status=UNKNOWN,
            message=f"The token answers, but its age is unknown. {_SET_IT_HINT}",
            probe=probe,
        )

    issued = parse_issued_at(issued_at)
    if issued > today:
        raise ValueError(
            f"{TOKEN_ISSUED_AT} is in the future ({issued.isoformat()}); "
            f"it must be the date the token was actually issued."
        )

    expires = issued + timedelta(days=TOKEN_LIFETIME_DAYS)
    days_remaining = (expires - today).days

    if days_remaining <= 0:
        status, message = EXPIRED, (
            f"The LinkedIn token lapsed on {expires.isoformat()} "
            f"({-days_remaining} days ago). Re-run the OAuth flow -- see docs/SETUP.md."
        )
    elif days_remaining <= warn_days:
        status, message = WARNING, (
            f"The LinkedIn token expires on {expires.isoformat()}, in {days_remaining} "
            f"days. It cannot be refreshed programmatically: re-run the OAuth flow and "
            f"update the LINKEDIN_ACCESS_TOKEN secret -- see docs/SETUP.md."
        )
    else:
        status, message = OK, (
            f"The LinkedIn token expires on {expires.isoformat()}, "
            f"in {days_remaining} days."
        )

    return TokenStatus(
        status=status,
        message=message,
        days_remaining=days_remaining,
        issued_at=issued.isoformat(),
        expires_at=expires.isoformat(),
        probe=probe,
    )

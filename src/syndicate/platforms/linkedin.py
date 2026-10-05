"""LinkedIn adapter -- own-profile posting via ``w_member_social``.

API reference:
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api

Verified against those docs:
  * ``POST https://api.linkedin.com/rest/posts`` -- the versioned Posts API,
    which replaces the deprecated ``/ugcPosts`` endpoint.
  * Every request requires ``LinkedIn-Version: {YYYYMM}`` and
    ``X-Restli-Protocol-Version: 2.0.0``, plus ``Authorization: Bearer``.
  * Text-post body: author, commentary, visibility, distribution
    (feedDistribution / targetEntities / thirdPartyDistributionChannels),
    lifecycleState, isReshareDisabledByAuthor.
  * Success is ``201`` and the post URN is returned in the ``x-restli-id``
    response header -- not in the body.
  * ``lifecycleState`` accepts only ``PUBLISHED`` on creation. DRAFT exists only
    as a response state, so there is no "create a LinkedIn draft" call: the
    human gate has to live entirely on our side of the wire.
  * ``w_member_social`` grants posting on behalf of the authenticated member.
  * Documented errors include 401 EMPTY_ACCESS_TOKEN, 403 ACCESS_DENIED,
    422 UNPROCESSABLE_ENTITY, 429 TOO_MANY_REQUESTS.
  * A published post is viewable at
    ``https://www.linkedin.com/feed/update/<urn>/``. The docs give the example
    as ``urn:li:ugcPost:<id>``, but a text post created through this adapter
    came back as ``urn:li:share:<id>`` and its feed URL resolved. So the URN
    *type* varies: build the URL from whatever ``x-restli-id`` returns and do
    not hardcode either form.

Verified live (2026-08-25, one text post to a real member account):
  * 201 + ``x-restli-id`` carrying the post URN, as documented.
  * The payload below is accepted as-is for a plain text post.
  * ``verify_author`` against ``/v2/userinfo`` matches the member URN when the
    token carries ``openid``/``profile``.

Unverified (flagged rather than guessed):
  * The 3000-character commentary limit is not stated in the Posts API
    reference; it is the documented limit for LinkedIn share commentary
    elsewhere and is applied here as a *local* guard so we fail before the
    network rather than eating a FIELD_LENGTH_TOO_LONG. The one live post was
    29 characters, so this limit remains untested.
  * Member access tokens are widely documented as lasting 60 days and not
    programmatically refreshable without approved partner access. This adapter
    therefore detects expiry and hands off to the runbook; it never tries to
    refresh. (An issued token did report ``expires_in=5183999``, i.e. 60 days.)
  * Every error path. No 401, 403, 422, or 429 has been seen from the live API;
    those branches are covered by fake-transport tests only.

Learned the hard way (2026-10-05):
  * ``commentary`` is LinkedIn "little text", not plain text. An unescaped
    reserved character silently truncates the post there: a 1447-character
    summary went out as its first 245 characters, cut before a "(". Every
    reserved character is backslash-escaped, and hashtags are sent as
    ``{hashtag|\\#|tag}`` templates so they stay clickable.
"""

from __future__ import annotations

import re

from ..http import RequestsTransport, Response, Transport
from .base import (
    CredentialError,
    PlatformError,
    RateLimited,
    TokenExpired,
    WriteResult,
    parse_retry_after,
)

#: LinkedIn commentary limit -- see module docstring (unverified in the Posts
#: API reference itself).
MAX_COMMENTARY_CHARS = 3000

#: Reserved in little text; any of these unescaped can end the post early.
_LITTLE_TEXT_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")
_HASHTAG = re.compile(r"(?<!\S)#(\w+)")


def to_little_text(text: str) -> str:
    """Escape plain text for ``commentary``, keeping ``#tag`` as a hashtag."""
    out, pos = [], 0
    for match in _HASHTAG.finditer(text):
        out.append(_LITTLE_TEXT_RESERVED.sub(r"\\\1", text[pos:match.start()]))
        out.append("{hashtag|\\#|" + match.group(1) + "}")
        pos = match.end()
    out.append(_LITTLE_TEXT_RESERVED.sub(r"\\\1", text[pos:]))
    return "".join(out)


#: Member access tokens last 60 days and cannot be refreshed without approved
#: partner access; re-auth is manual. See docs/SETUP.md.
TOKEN_LIFETIME_DAYS = 60

_REAUTH_HINT = (
    f"LinkedIn member tokens expire every {TOKEN_LIFETIME_DAYS} days and cannot be "
    f"refreshed programmatically. Re-run the OAuth flow and update the "
    f"LINKEDIN_ACCESS_TOKEN secret -- see docs/SETUP.md."
)


class LinkedInClient:
    BASE_URL = "https://api.linkedin.com"
    POSTS_PATH = "/rest/posts"
    USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
    #: LinkedIn cuts a version monthly and supports each for at least a year.
    DEFAULT_API_VERSION = "202608"

    def __init__(
        self,
        access_token: str,
        person_urn: str,
        transport: Transport | None = None,
        api_version: str | None = None,
    ):
        if not person_urn.startswith("urn:li:person:"):
            raise ValueError(
                f"LINKEDIN_PERSON_URN must be a member URN of the form "
                f"urn:li:person:<id> (got {person_urn!r}). Organization URNs need "
                f"w_organization_social, which this adapter does not implement."
            )
        self._token = access_token
        self._author = person_urn
        self._transport = transport or RequestsTransport()
        self._version = api_version or self.DEFAULT_API_VERSION

    # -- plumbing ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": self._version,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _detail(resp: Response) -> str:
        if isinstance(resp.body, dict):
            for key in ("message", "error_description", "error"):
                if resp.body.get(key):
                    return str(resp.body[key])
        return (resp.text or "").strip()[:200] or f"HTTP {resp.status_code}"

    def _raise_for_status(self, resp: Response) -> None:
        if 200 <= resp.status_code < 300:
            return
        detail = self._detail(resp)
        if resp.status_code == 401:
            if "expire" in detail.lower():
                raise TokenExpired(f"LinkedIn token expired: {detail}. {_REAUTH_HINT}")
            raise CredentialError(
                f"LinkedIn rejected the token (401): {detail}. "
                f"Check LINKEDIN_ACCESS_TOKEN. {_REAUTH_HINT}"
            )
        if resp.status_code == 403:
            raise PlatformError(
                f"LinkedIn denied the request (403): {detail}. The token must carry "
                f"the w_member_social scope and the author URN must be the "
                f"authenticated member."
            )
        if resp.status_code == 429:
            retry = parse_retry_after(resp.header("Retry-After"))
            raise RateLimited(f"LinkedIn rate limit hit: {detail}", retry_after=retry)
        raise PlatformError(f"LinkedIn POST {self.POSTS_PATH} failed ({resp.status_code}): {detail}")

    # -- operations --------------------------------------------------------

    def verify_author(self) -> bool | None:
        """Check the author URN is the authenticated member.

        Returns True on a match, raises on a mismatch, and returns ``None`` when
        the check is inconclusive -- a ``w_member_social``-only token cannot read
        ``/v2/userinfo`` (that needs ``openid``/``profile``), and being unable to
        check is not the same as failing the check.
        """
        resp = self._transport.request("GET", self.USERINFO_URL, headers=self._headers())
        if resp.status_code in (401, 403):
            return None
        self._raise_for_status(resp)
        subject = (resp.body or {}).get("sub") if isinstance(resp.body, dict) else None
        if not subject:
            return None
        if f"urn:li:person:{subject}" != self._author:
            raise CredentialError(
                f"LINKEDIN_PERSON_URN does not match the authenticated member "
                f"(token belongs to urn:li:person:{subject}). LinkedIn requires the "
                f"author URN to be the authenticated member."
            )
        return True

    def create_post(self, commentary: str) -> WriteResult:
        """Create a published text post on the member's own feed.

        There is no draft state on creation, so callers must apply the human
        gate before reaching this method.
        """
        if len(commentary) > MAX_COMMENTARY_CHARS:
            raise PlatformError(
                f"LinkedIn commentary is {len(commentary)} characters; the limit is "
                f"{MAX_COMMENTARY_CHARS}. Shorten crosspost.summary."
            )

        payload = {
            "author": self._author,
            "commentary": to_little_text(commentary),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        resp = self._transport.request(
            "POST", f"{self.BASE_URL}{self.POSTS_PATH}", headers=self._headers(), json=payload
        )
        self._raise_for_status(resp)

        urn = resp.header("x-restli-id")
        return WriteResult(
            remote_id=urn,
            url=f"https://www.linkedin.com/feed/update/{urn}/" if urn else None,
            state="published",
        )

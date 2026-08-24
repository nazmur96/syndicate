"""dev.to (Forem) adapter.

API reference: https://developers.forem.com/api/v1 (Forem API v1)

Verified against those docs:
  * Base URL is ``https://dev.to/api``.
  * Auth is a custom ``api-key`` header -- *not* ``Authorization: Bearer``.
  * ``accept: application/vnd.forem.api-v1+json`` selects the v1 response shape.
  * ``POST /articles`` creates an article. The body is wrapped in an ``article``
    object with title, body_markdown, published, series, main_image,
    canonical_url, description, tags, organization_id. ``published`` defaults to
    false; we set it explicitly. Documented responses: 201, 401, 422.
  * ``PUT /articles/{id}`` updates an article -- the docs expose PUT, not PATCH,
    with the same ``article`` wrapper. Documented responses: 200, 401, 404, 422.
  * Tags: "up to 4 tags".

Not stated in the official reference (flagged as unverified):
  * The exact rate limit for article writes. Community reports put it around 10
    requests / 30s, and 429 responses carry ``Retry-After`` in either
    delta-seconds or HTTP-date form. We parse both and surface the value rather
    than guessing a limit.
  * Tag slugification. dev.to silently lowercases tags and drops
    non-alphanumerics ("machine-learning" -> "machinelearning"). We apply the
    same transform locally so the manifest hash matches what dev.to stores, and
    report which tags were rewritten.
"""

from __future__ import annotations

import re

from ..http import RequestsTransport, Response, Transport
from .base import CredentialError, PlatformError, RateLimited, WriteResult, parse_retry_after

MAX_TAGS = 4
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def sanitise_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Return ``(tags_as_devto_will_store_them, tags_that_were_rewritten)``."""
    kept: list[str] = []
    rewritten: list[str] = []
    for tag in tags:
        slug = _NON_ALNUM.sub("", tag.strip().lower())
        if slug != tag.strip():
            rewritten.append(tag)
        if slug and slug not in kept:
            kept.append(slug)
    return kept[:MAX_TAGS], rewritten


class DevToClient:
    BASE_URL = "https://dev.to/api"
    #: Creating a draft does return a URL, but on a throwaway "-temp-slug-N"
    #: path that changes on publish, so tracking output always names the
    #: dashboard as the stable place to review drafts.
    DASHBOARD_URL = "https://dev.to/dashboard"

    def __init__(self, api_key: str, transport: Transport | None = None):
        self._api_key = api_key
        self._transport = transport or RequestsTransport()

    # -- plumbing ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "api-key": self._api_key,
            "accept": "application/vnd.forem.api-v1+json",
            "content-type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict | None = None) -> Response:
        resp = self._transport.request(
            method, f"{self.BASE_URL}{path}", headers=self._headers(), json=payload
        )
        self._raise_for_status(method, path, resp)
        return resp

    @staticmethod
    def _error_detail(resp: Response) -> str:
        if isinstance(resp.body, dict):
            for key in ("error", "message", "errors"):
                if resp.body.get(key):
                    return str(resp.body[key])
        return (resp.text or "").strip()[:200] or f"HTTP {resp.status_code}"

    def _raise_for_status(self, method: str, path: str, resp: Response) -> None:
        if 200 <= resp.status_code < 300:
            return
        detail = self._error_detail(resp)
        if resp.status_code == 401:
            raise CredentialError(
                "dev.to rejected the credentials (401). Check DEVTO_API_KEY is a "
                f"current key from https://dev.to/settings/extensions -- {detail}"
            )
        if resp.status_code == 429:
            retry = parse_retry_after(resp.header("Retry-After"))
            raise RateLimited(
                f"dev.to rate limit hit on {method} {path}"
                + (f"; retry after {retry}s" if retry is not None else ""),
                retry_after=retry,
            )
        raise PlatformError(f"dev.to {method} {path} failed ({resp.status_code}): {detail}")

    @staticmethod
    def _to_result(resp: Response) -> WriteResult:
        body = resp.body if isinstance(resp.body, dict) else {}
        article_id = body.get("id")
        return WriteResult(
            remote_id=str(article_id) if article_id is not None else None,
            url=body.get("url"),
            state="published" if body.get("published") else "draft",
        )

    # -- operations --------------------------------------------------------

    def create_draft(
        self,
        *,
        title: str,
        body_markdown: str,
        canonical_url: str,
        tags: list[str] | None = None,
        series: str | None = None,
        description: str | None = None,
    ) -> WriteResult:
        """POST /articles with ``published: false`` -- the human gate."""
        article: dict = {
            "title": title,
            "body_markdown": body_markdown,
            "published": False,
            "canonical_url": canonical_url,
        }
        if tags:
            article["tags"] = tags
        if series:
            article["series"] = series
        if description:
            article["description"] = description
        return self._to_result(self._request("POST", "/articles", {"article": article}))

    def update_draft(
        self,
        article_id: str,
        *,
        title: str,
        body_markdown: str,
        canonical_url: str,
        tags: list[str] | None = None,
        series: str | None = None,
        description: str | None = None,
    ) -> WriteResult:
        article: dict = {
            "title": title,
            "body_markdown": body_markdown,
            "canonical_url": canonical_url,
        }
        if tags:
            article["tags"] = tags
        if series:
            article["series"] = series
        if description:
            article["description"] = description
        return self._to_result(
            self._request("PUT", f"/articles/{article_id}", {"article": article})
        )

    def publish(self, article_id: str) -> WriteResult:
        """PUT /articles/{id} with ``published: true``."""
        return self._to_result(
            self._request("PUT", f"/articles/{article_id}", {"article": {"published": True}})
        )

    def unpublished(self) -> list[dict]:
        """GET /articles/me/unpublished -- the author's drafts.

        Verified against the live API: ``GET /articles/{id}`` is the *public*
        endpoint and returns 404 for an unpublished article, so this listing is
        the only way to read a draft back.
        """
        resp = self._request("GET", "/articles/me/unpublished")
        return resp.body if isinstance(resp.body, list) else []

    def me(self) -> dict:
        """GET /users/me -- read-only credential check."""
        resp = self._request("GET", "/users/me")
        return resp.body if isinstance(resp.body, dict) else {}

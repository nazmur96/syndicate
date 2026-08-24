"""A thin transport seam.

Adapters talk to this interface, not to ``requests``, so every adapter is
testable against a fake transport with no network and no credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Response:
    status_code: int
    headers: dict[str, str]
    body: Any | None
    text: str = ""

    def header(self, name: str) -> str | None:
        """Case-insensitive header lookup (HTTP headers are case-insensitive)."""
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        timeout: float | None = None,
    ) -> Response: ...


class RequestsTransport:
    """Default transport, backed by ``requests``."""

    def __init__(self, timeout: float = 30.0):
        import requests

        self._session = requests.Session()
        self._timeout = timeout

    def request(self, method, url, *, headers=None, json=None, timeout=None) -> Response:
        resp = self._session.request(
            method, url, headers=headers, json=json, timeout=timeout or self._timeout
        )
        try:
            body = resp.json()
        except ValueError:
            body = None
        return Response(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            body=body,
            text=resp.text,
        )

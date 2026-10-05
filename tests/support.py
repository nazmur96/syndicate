"""A recording fake transport, so adapters are tested without network."""
from __future__ import annotations

from dataclasses import dataclass, field

from syndicate.http import Response


@dataclass
class FakeTransport:
    responses: list[Response] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def request(
        self, method, url, *, headers=None, json=None, data=None, timeout=None
    ) -> Response:
        self.calls.append(
            {"method": method, "url": url, "headers": headers or {}, "json": json, "data": data}
        )
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)


def ok(status=200, body=None, headers=None):
    return Response(status_code=status, headers=headers or {}, body=body or {}, text="")

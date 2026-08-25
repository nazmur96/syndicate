"""The transport seam's failure translation.

A dropped connection is a platform failure like any other: the orchestrator's
fail-soft contract only holds if a network error arrives as a PlatformError
rather than escaping as a raw requests exception and aborting the whole run.
"""
import pytest
import requests

from syndicate.http import RequestsTransport
from syndicate.platforms.base import PlatformError, TransportError


class Boom:
    def __init__(self, exc):
        self._exc = exc

    def request(self, *args, **kwargs):
        raise self._exc


@pytest.mark.parametrize(
    "exc",
    [
        requests.ConnectionError("name or service not known"),
        requests.Timeout("timed out"),
    ],
)
def test_network_failures_arrive_as_platform_errors(exc):
    transport = RequestsTransport()
    transport._session = Boom(exc)
    with pytest.raises(TransportError) as caught:
        transport.request("GET", "https://example.invalid/x")
    assert isinstance(caught.value, PlatformError)
    assert "https://example.invalid/x" in str(caught.value)


def test_the_failure_message_names_the_method_and_url():
    transport = RequestsTransport()
    transport._session = Boom(requests.Timeout("timed out"))
    with pytest.raises(TransportError) as caught:
        transport.request("POST", "https://api.example.com/posts")
    assert "POST" in str(caught.value)
    assert "timed out" in str(caught.value)

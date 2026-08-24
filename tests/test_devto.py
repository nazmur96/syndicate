import pytest

from support import FakeTransport, ok
from syndicate.http import Response
from syndicate.platforms.base import PlatformError, RateLimited
from syndicate.platforms.devto import DevToClient, sanitise_tags


# --- tag handling ---------------------------------------------------------

def test_tags_are_lowercased_and_stripped_of_non_alphanumerics():
    # dev.to silently slugifies tags; we do it up front so the manifest hash
    # matches what dev.to actually stores, and report what got rewritten.
    kept, rewritten = sanitise_tags(["Machine-Learning", "C++", "python3"])
    assert kept == ["machinelearning", "c", "python3"]
    assert rewritten == ["Machine-Learning", "C++"]


def test_tags_that_slugify_to_nothing_are_dropped():
    kept, rewritten = sanitise_tags(["!!!", "python"])
    assert kept == ["python"]
    assert rewritten == ["!!!"]


def test_tag_list_is_capped_at_four():
    kept, _ = sanitise_tags(["a", "b", "c", "d", "e"])
    assert kept == ["a", "b", "c", "d"]


# --- draft creation -------------------------------------------------------

def make_client(transport):
    return DevToClient(api_key="k", transport=transport)


def test_create_draft_posts_unpublished_article_with_wrapper():
    t = FakeTransport([ok(201, {"id": 42, "url": "https://dev.to/me/x", "published": False})])
    client = make_client(t)

    result = client.create_draft(
        title="T", body_markdown="B", canonical_url="https://c/", tags=["py"],
        series="S", description="D",
    )

    call = t.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://dev.to/api/articles"
    assert call["json"]["article"]["published"] is False
    assert call["json"]["article"]["title"] == "T"
    assert call["json"]["article"]["body_markdown"] == "B"
    assert call["json"]["article"]["canonical_url"] == "https://c/"
    assert call["json"]["article"]["series"] == "S"
    assert call["json"]["article"]["tags"] == ["py"]
    assert result.remote_id == "42"


def test_auth_uses_the_api_key_header_not_bearer():
    t = FakeTransport([ok(201, {"id": 1})])
    make_client(t).create_draft(title="T", body_markdown="B", canonical_url="https://c/")
    headers = t.calls[0]["headers"]
    assert headers["api-key"] == "k"
    assert "Authorization" not in headers
    assert headers["accept"] == "application/vnd.forem.api-v1+json"


def test_series_is_omitted_when_absent():
    t = FakeTransport([ok(201, {"id": 1})])
    make_client(t).create_draft(title="T", body_markdown="B", canonical_url="https://c/")
    assert "series" not in t.calls[0]["json"]["article"]


# --- publishing -----------------------------------------------------------

def test_publish_puts_published_true_to_the_article_id():
    t = FakeTransport([ok(200, {"id": 42, "url": "https://dev.to/me/x", "published": True})])
    result = make_client(t).publish("42")

    call = t.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == "https://dev.to/api/articles/42"
    assert call["json"] == {"article": {"published": True}}
    assert result.url == "https://dev.to/me/x"


# --- errors ---------------------------------------------------------------

def test_401_is_a_clear_credential_error():
    t = FakeTransport([ok(401, {"error": "unauthorized"})])
    with pytest.raises(PlatformError) as exc:
        make_client(t).create_draft(title="T", body_markdown="B", canonical_url="https://c/")
    assert "DEVTO_API_KEY" in str(exc.value)


def test_422_surfaces_the_api_error_message():
    t = FakeTransport([ok(422, {"error": "Title can't be blank"})])
    with pytest.raises(PlatformError) as exc:
        make_client(t).create_draft(title="", body_markdown="B", canonical_url="https://c/")
    assert "Title can't be blank" in str(exc.value)


def test_429_reports_retry_after_seconds():
    t = FakeTransport([ok(429, {}, {"Retry-After": "30"})])
    with pytest.raises(RateLimited) as exc:
        make_client(t).create_draft(title="T", body_markdown="B", canonical_url="https://c/")
    assert exc.value.retry_after == 30


def test_429_handles_http_date_retry_after():
    # dev.to returns Retry-After as either an integer or an RFC 7231 date.
    t = FakeTransport([ok(429, {}, {"Retry-After": "Fri, 31 Jul 2026 07:00:00 GMT"})])
    with pytest.raises(RateLimited) as exc:
        make_client(t).create_draft(title="T", body_markdown="B", canonical_url="https://c/")
    assert isinstance(exc.value.retry_after, int)
    assert exc.value.retry_after >= 0


def test_dashboard_url_is_exposed_for_drafts():
    # Draft URLs are not directly viewable, so tracking output links the dashboard.
    assert DevToClient.DASHBOARD_URL == "https://dev.to/dashboard"


def test_non_json_error_body_still_raises_platform_error():
    t = FakeTransport([Response(status_code=500, headers={}, body=None, text="<html>oops")])
    with pytest.raises(PlatformError):
        make_client(t).create_draft(title="T", body_markdown="B", canonical_url="https://c/")


def test_unpublished_lists_drafts_from_the_authenticated_endpoint():
    # GET /articles/{id} is the *public* endpoint and 404s on a draft; the
    # authenticated listing is the only way to read one back.
    t = FakeTransport([ok(200, [{"id": 1, "published": False}])])
    drafts = make_client(t).unpublished()
    assert t.calls[0]["url"] == "https://dev.to/api/articles/me/unpublished"
    assert drafts[0]["id"] == 1

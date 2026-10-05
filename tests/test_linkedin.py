import pytest

from support import FakeTransport, ok
from syndicate.platforms.base import CredentialError, PlatformError, TokenExpired
from syndicate.platforms.linkedin import LinkedInClient

URN = "urn:li:person:AbC123"


def make_client(transport, urn=URN, version="202608"):
    return LinkedInClient(
        access_token="tok", person_urn=urn, transport=transport, api_version=version
    )


def created(post_urn="urn:li:share:999"):
    return ok(201, {}, {"x-restli-id": post_urn})


# --- request shape --------------------------------------------------------

def test_post_hits_the_versioned_rest_posts_endpoint_not_ugcposts():
    t = FakeTransport([created()])
    make_client(t).create_post("Hello world")
    assert t.calls[0]["method"] == "POST"
    assert t.calls[0]["url"] == "https://api.linkedin.com/rest/posts"
    assert "ugcPosts" not in t.calls[0]["url"]


def test_required_headers_are_sent():
    t = FakeTransport([created()])
    make_client(t).create_post("Hello")
    h = t.calls[0]["headers"]
    assert h["Authorization"] == "Bearer tok"
    assert h["X-Restli-Protocol-Version"] == "2.0.0"
    assert h["LinkedIn-Version"] == "202608"
    assert h["Content-Type"] == "application/json"


def test_body_matches_the_documented_text_post_shape():
    t = FakeTransport([created()])
    make_client(t).create_post("Hello world")
    body = t.calls[0]["json"]
    assert body["author"] == URN
    assert body["commentary"] == "Hello world"
    assert body["visibility"] == "PUBLIC"
    assert body["lifecycleState"] == "PUBLISHED"
    assert body["isReshareDisabledByAuthor"] is False
    assert body["distribution"] == {
        "feedDistribution": "MAIN_FEED",
        "targetEntities": [],
        "thirdPartyDistributionChannels": [],
    }


def test_post_id_is_read_from_the_x_restli_id_header():
    t = FakeTransport([created("urn:li:share:6844785523593134080")])
    result = make_client(t).create_post("Hello")
    assert result.remote_id == "urn:li:share:6844785523593134080"
    assert result.state == "published"


def test_post_url_is_derived_from_the_returned_urn():
    t = FakeTransport([created("urn:li:share:123")])
    result = make_client(t).create_post("Hello")
    assert result.url == "https://www.linkedin.com/feed/update/urn:li:share:123/"


def test_api_version_defaults_to_a_yyyymm_string():
    assert len(LinkedInClient.DEFAULT_API_VERSION) == 6
    assert LinkedInClient.DEFAULT_API_VERSION.isdigit()


# --- author URN guard -----------------------------------------------------

def test_non_person_urn_is_rejected_before_any_request():
    t = FakeTransport([])
    with pytest.raises(ValueError) as exc:
        make_client(t, urn="urn:li:organization:5515715")
    assert "urn:li:person:" in str(exc.value)
    assert t.calls == []


def test_verify_author_accepts_a_matching_member():
    t = FakeTransport([ok(200, {"sub": "AbC123"})])
    assert make_client(t).verify_author() is True
    assert t.calls[0]["url"] == "https://api.linkedin.com/v2/userinfo"


def test_verify_author_rejects_a_mismatched_member():
    t = FakeTransport([ok(200, {"sub": "SomeoneElse"})])
    with pytest.raises(CredentialError) as exc:
        make_client(t).verify_author()
    assert "LINKEDIN_PERSON_URN" in str(exc.value)


def test_verify_author_is_inconclusive_without_profile_scope():
    # w_member_social alone cannot read /v2/userinfo; that is not a failure.
    t = FakeTransport([ok(403, {"message": "Not enough permissions"})])
    assert make_client(t).verify_author() is None


# --- token expiry ---------------------------------------------------------

def test_expired_token_raises_token_expired_with_reauth_guidance():
    t = FakeTransport([ok(401, {"message": "The token used in the request has expired"})])
    with pytest.raises(TokenExpired) as exc:
        make_client(t).create_post("Hello")
    msg = str(exc.value)
    assert "60" in msg and "docs/SETUP.md" in msg


def test_empty_token_is_a_credential_error():
    t = FakeTransport([ok(401, {"serviceErrorCode": 65600, "message": "EMPTY_ACCESS_TOKEN"})])
    with pytest.raises(CredentialError):
        make_client(t).create_post("Hello")


def test_403_access_denied_names_the_required_scope():
    t = FakeTransport([ok(403, {"message": "ACCESS_DENIED"})])
    with pytest.raises(PlatformError) as exc:
        make_client(t).create_post("Hello")
    assert "w_member_social" in str(exc.value)


def test_422_surfaces_the_api_message():
    t = FakeTransport([ok(422, {"message": "FIELD_LENGTH_TOO_LONG"})])
    with pytest.raises(PlatformError) as exc:
        make_client(t).create_post("x")
    assert "FIELD_LENGTH_TOO_LONG" in str(exc.value)


def test_commentary_over_the_character_limit_is_rejected_locally():
    t = FakeTransport([])
    with pytest.raises(PlatformError) as exc:
        make_client(t).create_post("x" * 3001)
    assert "3000" in str(exc.value)
    assert t.calls == []


# --- little text escaping -------------------------------------------------
#
# commentary is LinkedIn "little text", not plain text. An unescaped reserved
# character silently truncates the post at that point: a live post was cut to
# "...External Secrets Operator " because the next character was "(".

def test_reserved_characters_are_backslash_escaped():
    t = FakeTransport([created()])
    make_client(t).create_post(r"ESO (v1) [a] {b} <c> @d |e ~f _g *h \i")
    assert t.calls[0]["json"]["commentary"] == (
        r"ESO \(v1\) \[a\] \{b\} \<c\> \@d \|e \~f \_g \*h \\i"
    )


def test_hashtags_become_hashtag_templates():
    t = FakeTransport([created()])
    make_client(t).create_post("#Kubernetes #AWS")
    assert t.calls[0]["json"]["commentary"] == (
        r"{hashtag|\#|Kubernetes} {hashtag|\#|AWS}"
    )


def test_a_hash_that_is_not_a_hashtag_is_escaped():
    t = FakeTransport([created()])
    make_client(t).create_post("issue #  and C#")
    assert t.calls[0]["json"]["commentary"] == r"issue \#  and C\#"


def test_urls_and_newlines_pass_through():
    t = FakeTransport([created()])
    text = "Para one.\n\nhttps://github.com/a/b/blob/main/c.md"
    make_client(t).create_post(text)
    assert t.calls[0]["json"]["commentary"] == text


def test_character_limit_counts_what_the_reader_sees_not_the_escapes():
    t = FakeTransport([created()])
    make_client(t).create_post("(" * 3000)  # 6000 escaped, 3000 visible
    assert len(t.calls[0]["json"]["commentary"]) == 6000


# --- image posts ----------------------------------------------------------

def init_upload(image_urn="urn:li:image:C4E", upload_url="https://upload.example/abc"):
    return ok(200, {"value": {"uploadUrl": upload_url, "image": image_urn}})


def test_image_post_initialises_uploads_then_attaches_the_image_urn():
    t = FakeTransport([init_upload(), ok(201), created()])
    make_client(t).create_post("Hello", image=(b"\x89PNG", "A diagram"))

    init, put, post = t.calls
    assert init["method"] == "POST"
    assert init["url"] == "https://api.linkedin.com/rest/images?action=initializeUpload"
    assert init["json"] == {"initializeUploadRequest": {"owner": URN}}

    assert put["method"] == "PUT"
    assert put["url"] == "https://upload.example/abc"
    assert put["data"] == b"\x89PNG"
    assert put["headers"]["Authorization"] == "Bearer tok"

    assert post["json"]["content"] == {
        "media": {"id": "urn:li:image:C4E", "altText": "A diagram"}
    }


def test_text_post_has_no_content_block():
    t = FakeTransport([created()])
    make_client(t).create_post("Hello")
    assert "content" not in t.calls[0]["json"]


def test_failed_upload_does_not_create_the_post():
    t = FakeTransport([init_upload(), ok(500)])
    with pytest.raises(PlatformError, match="upload"):
        make_client(t).create_post("Hello", image=(b"x", "alt"))
    assert len(t.calls) == 2


def test_initialise_response_without_an_upload_url_is_an_error():
    t = FakeTransport([ok(200, {"value": {}})])
    with pytest.raises(PlatformError, match="uploadUrl"):
        make_client(t).create_post("Hello", image=(b"x", "alt"))

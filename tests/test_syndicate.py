"""Orchestrator: the human gate, idempotency, and fail-soft behaviour."""
import pytest

from support import FakeTransport, ok
from syndicate.canonical import CanonicalConfig
from syndicate.core import Syndicator
from syndicate.manifest import Manifest
from syndicate.platforms.base import PlatformError

BODY = "Problem para.\n\nFinding para.\n\nTrade-off para.\n\nExtra para."


def write_doc(tmp_path, name="notes/post.md", devto="full", linkedin="summary", body=BODY):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "title: A Post", "crosspost:"]
    if devto:
        lines.append(f"  devto: {devto}")
    if linkedin:
        lines.append(f"  linkedin: {linkedin}")
    lines += ["---", "", body, ""]
    p.write_text("\n".join(lines))
    return p


class StubDevTo:
    DASHBOARD_URL = "https://dev.to/dashboard"

    def __init__(self, fail=None):
        self.created, self.published, self.updated = [], [], []
        self.fail = fail

    def create_draft(self, **kw):
        if self.fail:
            raise self.fail
        self.created.append(kw)
        from syndicate.platforms.base import WriteResult
        return WriteResult(remote_id="42", url=None, state="draft")

    def update_draft(self, article_id, **kw):
        self.updated.append((article_id, kw))
        from syndicate.platforms.base import WriteResult
        return WriteResult(remote_id=article_id, state="draft")

    def publish(self, article_id):
        if self.fail:
            raise self.fail
        self.published.append(article_id)
        from syndicate.platforms.base import WriteResult
        return WriteResult(remote_id=article_id, url="https://dev.to/me/a", state="published")


class StubLinkedIn:
    def __init__(self, fail=None):
        self.posts = []
        self.images = []
        self.fail = fail

    def verify_author(self):
        return True

    def create_post(self, commentary, image=None):
        if self.fail:
            raise self.fail
        self.posts.append(commentary)
        self.images.append(image)
        from syndicate.platforms.base import WriteResult
        return WriteResult(remote_id="urn:li:share:1",
                           url="https://www.linkedin.com/feed/update/urn:li:share:1/",
                           state="published")


def make(tmp_path, devto=None, linkedin=None):
    return Syndicator(
        repo_root=tmp_path,
        manifest=Manifest.load(tmp_path / "manifest.json"),
        canonical_config=CanonicalConfig(repository="acme/notes"),
        devto=devto,
        linkedin=linkedin,
    )


# --- the human gate -------------------------------------------------------

def test_draft_mode_creates_a_devto_draft_and_never_touches_linkedin(tmp_path):
    devto, linkedin = StubDevTo(), StubLinkedIn()
    results = make(tmp_path, devto, linkedin).run([write_doc(tmp_path)], mode="draft")

    assert len(devto.created) == 1
    assert devto.published == []
    assert linkedin.posts == [], "a push must never reach LinkedIn"
    assert {r.platform for r in results} == {"devto", "linkedin"}
    li = next(r for r in results if r.platform == "linkedin")
    assert li.action == "skipped"
    assert "publish" in li.message.lower()


def test_draft_mode_reports_the_dashboard_because_drafts_are_not_viewable(tmp_path):
    devto = StubDevTo()
    results = make(tmp_path, devto, StubLinkedIn()).run([write_doc(tmp_path)], mode="draft")
    dev = next(r for r in results if r.platform == "devto")
    assert dev.action == "created"
    assert dev.url == "https://dev.to/dashboard"


def test_publish_mode_publishes_devto_and_posts_linkedin(tmp_path):
    devto, linkedin = StubDevTo(), StubLinkedIn()
    syn = make(tmp_path, devto, linkedin)
    doc = write_doc(tmp_path)
    syn.run([doc], mode="draft")
    results = syn.run([doc], mode="publish")

    assert devto.published == ["42"]
    assert len(linkedin.posts) == 1
    assert "originally published at" in linkedin.posts[0]


def test_publish_creates_the_draft_first_if_one_does_not_exist(tmp_path):
    devto = StubDevTo()
    make(tmp_path, devto, StubLinkedIn()).run([write_doc(tmp_path)], mode="publish")
    assert len(devto.created) == 1
    assert devto.published == ["42"]


# --- idempotency ----------------------------------------------------------

def test_second_draft_run_with_unchanged_content_is_a_no_op(tmp_path):
    devto = StubDevTo()
    syn = make(tmp_path, devto, StubLinkedIn())
    doc = write_doc(tmp_path)
    syn.run([doc], mode="draft")
    results = syn.run([doc], mode="draft")

    assert len(devto.created) == 1, "must not double-post"
    assert devto.updated == []
    assert next(r for r in results if r.platform == "devto").action == "skipped"


def test_changed_content_updates_the_existing_draft_rather_than_creating_one(tmp_path):
    devto = StubDevTo()
    syn = make(tmp_path, devto, StubLinkedIn())
    doc = write_doc(tmp_path)
    syn.run([doc], mode="draft")
    write_doc(tmp_path, body=BODY + "\n\nNew paragraph.")
    results = syn.run([doc], mode="draft")

    assert len(devto.created) == 1
    assert devto.updated and devto.updated[0][0] == "42"
    assert next(r for r in results if r.platform == "devto").action == "updated"


def test_linkedin_is_never_posted_twice_for_the_same_content(tmp_path):
    linkedin = StubLinkedIn()
    syn = make(tmp_path, StubDevTo(), linkedin)
    doc = write_doc(tmp_path)
    syn.run([doc], mode="publish")
    syn.run([doc], mode="publish")
    assert len(linkedin.posts) == 1


def test_manifest_is_persisted_after_a_run(tmp_path):
    syn = make(tmp_path, StubDevTo(), StubLinkedIn())
    syn.run([write_doc(tmp_path)], mode="draft")
    assert (tmp_path / "manifest.json").exists()
    reloaded = Manifest.load(tmp_path / "manifest.json")
    assert reloaded.get("notes/post.md", "devto").remote_id == "42"


# --- opt-in ---------------------------------------------------------------

def test_document_without_a_crosspost_block_is_skipped_silently(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("---\ntitle: Plain\n---\n\nBody.\n")
    devto = StubDevTo()
    results = make(tmp_path, devto, StubLinkedIn()).run([p], mode="draft")
    assert results == []
    assert devto.created == []


def test_only_enabled_platforms_are_attempted(tmp_path):
    devto, linkedin = StubDevTo(), StubLinkedIn()
    doc = write_doc(tmp_path, linkedin=None)
    results = make(tmp_path, devto, linkedin).run([doc], mode="publish")
    assert linkedin.posts == []
    assert {r.platform for r in results} == {"devto"}


# --- fail soft ------------------------------------------------------------

def test_devto_failure_does_not_block_linkedin(tmp_path):
    devto = StubDevTo(fail=PlatformError("dev.to exploded"))
    linkedin = StubLinkedIn()
    results = make(tmp_path, devto, linkedin).run([write_doc(tmp_path)], mode="publish")

    assert len(linkedin.posts) == 1, "LinkedIn must still run"
    dev = next(r for r in results if r.platform == "devto")
    assert dev.action == "failed" and "exploded" in dev.message


def test_linkedin_failure_does_not_block_devto(tmp_path):
    devto = StubDevTo()
    linkedin = StubLinkedIn(fail=PlatformError("linkedin exploded"))
    results = make(tmp_path, devto, linkedin).run([write_doc(tmp_path)], mode="publish")

    assert devto.published == ["42"]
    li = next(r for r in results if r.platform == "linkedin")
    assert li.action == "failed"


def test_a_failed_platform_is_not_recorded_in_the_manifest(tmp_path):
    devto = StubDevTo(fail=PlatformError("boom"))
    syn = make(tmp_path, devto, StubLinkedIn())
    syn.run([write_doc(tmp_path)], mode="draft")
    assert Manifest.load(tmp_path / "manifest.json").get("notes/post.md", "devto") is None


def test_an_unconfigured_platform_is_reported_as_skipped_not_failed(tmp_path):
    results = make(tmp_path, StubDevTo(), linkedin=None).run(
        [write_doc(tmp_path)], mode="publish"
    )
    li = next(r for r in results if r.platform == "linkedin")
    assert li.action == "skipped"
    assert "LINKEDIN" in li.message


# --- validation is a hard failure ----------------------------------------

def test_invalid_crosspost_block_aborts_the_run(tmp_path):
    from syndicate.document import CrosspostInvalid
    p = tmp_path / "bad.md"
    p.write_text("---\ntitle: X\ncrosspost:\n  devto: sideways\n---\n\nBody.\n")
    with pytest.raises(CrosspostInvalid):
        make(tmp_path, StubDevTo(), StubLinkedIn()).run([p], mode="draft")


def test_draft_result_always_names_the_dashboard_even_when_a_temp_url_exists(tmp_path):
    # dev.to does return a temp-slug URL for a draft, but it is not a stable
    # place to review it -- the tracking output must still name the dashboard.
    class TempUrlDevTo(StubDevTo):
        def create_draft(self, **kw):
            from syndicate.platforms.base import WriteResult
            self.created.append(kw)
            return WriteResult(remote_id="42", url="https://dev.to/me/x-temp-slug-1", state="draft")

    results = make(tmp_path, TempUrlDevTo(), StubLinkedIn()).run(
        [write_doc(tmp_path)], mode="draft"
    )
    dev = next(r for r in results if r.platform == "devto")
    assert dev.url == "https://dev.to/me/x-temp-slug-1"
    assert "https://dev.to/dashboard" in dev.message


# --- LinkedIn image -------------------------------------------------------

def write_image_doc(tmp_path, image_exists=True):
    p = write_doc(tmp_path)
    src = p.read_text().replace(
        "  linkedin: summary\n",
        "  linkedin: summary\n  linkedin_image:\n    path: img/flow.png\n    alt: The flow\n",
    )
    p.write_text(src)
    if image_exists:
        (p.parent / "img").mkdir()
        (p.parent / "img" / "flow.png").write_bytes(b"\x89PNG-bytes")
    return p


def test_linkedin_image_is_read_relative_to_the_document(tmp_path):
    linkedin = StubLinkedIn()
    make(tmp_path, StubDevTo(), linkedin).run([write_image_doc(tmp_path)], mode="publish")
    assert linkedin.images == [(b"\x89PNG-bytes", "The flow")]


def test_missing_linkedin_image_fails_linkedin_without_posting(tmp_path):
    devto, linkedin = StubDevTo(), StubLinkedIn()
    results = make(tmp_path, devto, linkedin).run(
        [write_image_doc(tmp_path, image_exists=False)], mode="publish"
    )
    li = next(r for r in results if r.platform == "linkedin")
    assert li.action == "failed"
    assert "img/flow.png" in li.message
    assert linkedin.posts == []
    assert next(r for r in results if r.platform == "devto").action != "failed"

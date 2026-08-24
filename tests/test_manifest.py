import json

import pytest

from syndicate.manifest import Manifest, PlatformRecord, content_hash


def test_content_hash_is_stable_for_identical_payloads():
    assert content_hash("hello", ["a", "b"], "S") == content_hash("hello", ["a", "b"], "S")


def test_content_hash_changes_when_body_changes():
    assert content_hash("hello", [], None) != content_hash("hello!", [], None)


def test_content_hash_changes_when_tags_change():
    assert content_hash("hello", ["a"], None) != content_hash("hello", ["a", "b"], None)


def test_content_hash_changes_when_series_changes():
    assert content_hash("hello", [], None) != content_hash("hello", [], "S")


def test_empty_manifest_reports_nothing_posted(tmp_path):
    m = Manifest.load(tmp_path / "manifest.json")
    assert m.get("notes/a.md", "devto") is None


def test_record_round_trips_through_disk(tmp_path):
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.record(
        "notes/a.md",
        "devto",
        PlatformRecord(remote_id="12345", url="https://dev.to/x/a", state="draft",
                       content_hash="abc", posted_at="2026-08-24T00:00:00Z"),
    )
    m.save()

    reloaded = Manifest.load(path)
    rec = reloaded.get("notes/a.md", "devto")
    assert rec.remote_id == "12345"
    assert rec.state == "draft"
    assert rec.content_hash == "abc"


def test_manifest_file_is_deterministic_and_human_readable(tmp_path):
    path = tmp_path / "manifest.json"
    m = Manifest.load(path)
    m.record("b.md", "devto", PlatformRecord(remote_id="2", content_hash="h2"))
    m.record("a.md", "devto", PlatformRecord(remote_id="1", content_hash="h1"))
    m.save()
    data = json.loads(path.read_text())
    assert list(data["documents"]) == ["a.md", "b.md"], "keys must be sorted"
    assert path.read_text().endswith("\n")


def test_needs_write_is_true_when_never_posted(tmp_path):
    m = Manifest.load(tmp_path / "m.json")
    assert m.needs_write("a.md", "devto", "hash1") is True


def test_needs_write_is_false_for_identical_hash(tmp_path):
    m = Manifest.load(tmp_path / "m.json")
    m.record("a.md", "devto", PlatformRecord(remote_id="1", content_hash="hash1"))
    assert m.needs_write("a.md", "devto", "hash1") is False


def test_needs_write_is_true_when_hash_changed(tmp_path):
    m = Manifest.load(tmp_path / "m.json")
    m.record("a.md", "devto", PlatformRecord(remote_id="1", content_hash="hash1"))
    assert m.needs_write("a.md", "devto", "hash2") is True


def test_platforms_are_tracked_independently(tmp_path):
    m = Manifest.load(tmp_path / "m.json")
    m.record("a.md", "devto", PlatformRecord(remote_id="1", content_hash="h"))
    assert m.needs_write("a.md", "devto", "h") is False
    assert m.needs_write("a.md", "linkedin", "h") is True


def test_corrupt_manifest_is_a_hard_error_not_a_silent_reset(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{not json")
    with pytest.raises(ValueError):
        Manifest.load(path)

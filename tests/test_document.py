import pytest

from syndicate.document import (
    CrosspostInvalid,
    load_document,
    parse_document,
)


def test_document_without_frontmatter_is_skipped():
    assert parse_document("# Just a heading\n\nBody text.", "doc.md") is None


def test_document_without_crosspost_block_is_skipped():
    src = "---\ntitle: Hello\n---\n\nBody text.\n"
    assert parse_document(src, "doc.md") is None


def test_valid_crosspost_block_is_parsed():
    src = (
        "---\n"
        "title: Cache Invalidation\n"
        "description: A note\n"
        "crosspost:\n"
        "  devto: full\n"
        "  linkedin: summary\n"
        "  series: Hard Things\n"
        "  tags: [python, caching]\n"
        "---\n"
        "\n"
        "The body.\n"
    )
    doc = parse_document(src, "notes/cache.md")

    assert doc.title == "Cache Invalidation"
    assert doc.description == "A note"
    assert doc.body == "The body."
    assert doc.source_path == "notes/cache.md"
    assert doc.crosspost.devto == "full"
    assert doc.crosspost.linkedin == "summary"
    assert doc.crosspost.series == "Hard Things"
    assert doc.crosspost.tags == ["python", "caching"]


def test_crosspost_defaults_to_false_for_omitted_platforms():
    src = "---\ntitle: T\ncrosspost:\n  devto: summary\n---\nBody\n"
    doc = parse_document(src, "d.md")
    assert doc.crosspost.devto == "summary"
    assert doc.crosspost.linkedin is False
    assert doc.crosspost.series is None
    assert doc.crosspost.tags == []


def test_both_platforms_disabled_is_skipped():
    src = "---\ntitle: T\ncrosspost:\n  devto: false\n  linkedin: false\n---\nBody\n"
    assert parse_document(src, "d.md") is None


def test_invalid_devto_mode_hard_fails():
    src = "---\ntitle: T\ncrosspost:\n  devto: partial\n---\nBody\n"
    with pytest.raises(CrosspostInvalid) as exc:
        parse_document(src, "d.md")
    assert "devto" in str(exc.value)


def test_linkedin_full_is_rejected():
    src = "---\ntitle: T\ncrosspost:\n  linkedin: full\n---\nBody\n"
    with pytest.raises(CrosspostInvalid) as exc:
        parse_document(src, "d.md")
    assert "linkedin" in str(exc.value)


def test_more_than_four_tags_hard_fails():
    src = "---\ntitle: T\ncrosspost:\n  devto: full\n  tags: [a, b, c, d, e]\n---\nBody\n"
    with pytest.raises(CrosspostInvalid) as exc:
        parse_document(src, "d.md")
    assert "tags" in str(exc.value)


def test_unknown_crosspost_key_hard_fails():
    src = "---\ntitle: T\ncrosspost:\n  devto: full\n  medium: full\n---\nBody\n"
    with pytest.raises(CrosspostInvalid):
        parse_document(src, "d.md")


def test_missing_title_hard_fails_when_crosspost_enabled():
    src = "---\ncrosspost:\n  devto: full\n---\nBody\n"
    with pytest.raises(CrosspostInvalid) as exc:
        parse_document(src, "d.md")
    assert "title" in str(exc.value)


def test_load_document_reads_from_disk(tmp_path):
    p = tmp_path / "post.md"
    p.write_text("---\ntitle: T\ncrosspost:\n  devto: full\n---\nBody\n")
    doc = load_document(p, repo_root=tmp_path)
    assert doc.source_path == "post.md"
    assert doc.title == "T"


def test_linkedin_image_is_parsed():
    src = (
        "---\ntitle: T\ncrosspost:\n  linkedin: summary\n"
        "  linkedin_image:\n    path: img/flow.png\n    alt: A flow diagram\n---\n\nBody.\n"
    )
    image = parse_document(src, "posts/p.md").crosspost.linkedin_image
    assert image.path == "img/flow.png"
    assert image.alt == "A flow diagram"


def test_linkedin_image_without_alt_text_hard_fails():
    src = (
        "---\ntitle: T\ncrosspost:\n  linkedin: summary\n"
        "  linkedin_image:\n    path: img/flow.png\n---\n\nBody.\n"
    )
    with pytest.raises(CrosspostInvalid, match="alt"):
        parse_document(src, "posts/p.md")

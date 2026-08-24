import pytest

from syndicate.canonical import CanonicalConfig, canonical_url


def cfg(**kw):
    base = dict(repository="acme/notes", pages_base_url=None)
    base.update(kw)
    return CanonicalConfig(**base)


def test_default_base_url_is_project_pages_site():
    assert canonical_url("notes/cache.md", cfg()) == "https://acme.github.io/notes/notes/cache/"


def test_user_site_repo_has_no_path_prefix():
    c = cfg(repository="acme/acme.github.io")
    assert canonical_url("posts/a.md", c) == "https://acme.github.io/posts/a/"


def test_explicit_pages_base_url_wins():
    c = cfg(pages_base_url="https://notes.acme.dev")
    assert canonical_url("notes/cache.md", c) == "https://notes.acme.dev/notes/cache/"


def test_trailing_slash_in_base_is_normalised():
    c = cfg(pages_base_url="https://notes.acme.dev/")
    assert canonical_url("a.md", c) == "https://notes.acme.dev/a/"


def test_index_and_readme_map_to_directory_root():
    c = cfg(pages_base_url="https://notes.acme.dev")
    assert canonical_url("notes/index.md", c) == "https://notes.acme.dev/notes/"
    assert canonical_url("notes/README.md", c) == "https://notes.acme.dev/notes/"
    assert canonical_url("README.md", c) == "https://notes.acme.dev/"


def test_frontmatter_override_wins_over_everything():
    c = cfg(pages_base_url="https://notes.acme.dev")
    assert canonical_url("a.md", c, override="https://elsewhere.dev/a") == "https://elsewhere.dev/a"


def test_missing_repository_and_base_url_is_an_error():
    with pytest.raises(ValueError):
        canonical_url("a.md", CanonicalConfig(repository=None, pages_base_url=None))


def test_relative_override_is_rejected():
    with pytest.raises(ValueError):
        canonical_url("a.md", cfg(), override="/a/")

"""Canonical URL resolution.

Canonical URLs always point at the *source* repo's GitHub Pages site, never at
dev.to or LinkedIn, so search engines and readers are sent back to the origin.
"""

from __future__ import annotations

from dataclasses import dataclass

_INDEX_NAMES = {"index", "readme"}


@dataclass(frozen=True)
class CanonicalConfig:
    #: "owner/repo", normally from $GITHUB_REPOSITORY.
    repository: str | None = None
    #: Explicit override, e.g. a custom Pages domain. Wins over `repository`.
    pages_base_url: str | None = None


def _base_url(config: CanonicalConfig) -> str:
    if config.pages_base_url:
        return config.pages_base_url.rstrip("/")
    if not config.repository or "/" not in config.repository:
        raise ValueError(
            "cannot resolve canonical URL: set pages_base_url or repository (owner/repo)"
        )
    owner, repo = config.repository.split("/", 1)
    # GitHub Pages: a repo named `<owner>.github.io` is served at the domain
    # root; every other repo is served under /<repo>/.
    if repo.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io"
    return f"https://{owner.lower()}.github.io/{repo}"


def _slug_path(source_path: str) -> str:
    parts = source_path.strip("/").split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    dirs = parts[:-1]
    if stem.lower() in _INDEX_NAMES:
        return "/".join(dirs)
    return "/".join([*dirs, stem])


def canonical_url(
    source_path: str,
    config: CanonicalConfig,
    override: str | None = None,
) -> str:
    """Absolute canonical URL for a document, with a trailing slash."""
    if override:
        if not override.startswith(("http://", "https://")):
            raise ValueError(f"canonical_url override must be absolute, got {override!r}")
        return override
    base = _base_url(config)
    path = _slug_path(source_path)
    return f"{base}/{path}/" if path else f"{base}/"

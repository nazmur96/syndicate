"""Frontmatter parsing and hard validation of the ``crosspost`` opt-in block.

Opt-in rule: a document with no ``crosspost`` block (or with every platform set
to ``false``) is skipped entirely -- ``parse_document`` returns ``None``. A
document that *has* the block but gets it wrong is a hard failure, never a skip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

#: JSON Schema for the ``crosspost`` frontmatter block.
#:
#: ``linkedin`` deliberately has no ``full`` mode: LinkedIn posts are always the
#: short summary form, never the whole article body.
CROSSPOST_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "crosspost",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "devto": {"enum": ["full", "summary", False]},
        "linkedin": {"enum": ["summary", False]},
        "series": {"type": "string", "minLength": 1},
        # dev.to accepts a maximum of 4 tags per article.
        # https://developers.forem.com/api/v1 -> POST /articles ("up to 4 tags")
        "tags": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "minLength": 1},
        },
        # Authored summary used for `summary` mode and for LinkedIn. Free-form
        # prose; paragraphs are separated by blank lines.
        "summary": {"type": "string", "minLength": 1},
        # Max 2 hashtags on summary/LinkedIn output.
        "hashtags": {
            "type": "array",
            "maxItems": 2,
            "items": {"type": "string", "minLength": 1},
        },
        "canonical_url": {"type": "string", "format": "uri"},
        # One image attached to the LinkedIn post. `path` is relative to the
        # document's directory; alt text is required, not optional politeness.
        "linkedin_image": {
            "type": "object",
            "additionalProperties": False,
            "required": ["path", "alt"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "alt": {"type": "string", "minLength": 1},
            },
        },
    },
}

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


class CrosspostInvalid(Exception):
    """Raised when a document opts in but its crosspost block is invalid."""


@dataclass(frozen=True)
class LinkedInImage:
    path: str
    alt: str


@dataclass(frozen=True)
class CrosspostSpec:
    devto: str | bool = False
    linkedin: str | bool = False
    series: str | None = None
    tags: list[str] = field(default_factory=list)
    summary: str | None = None
    hashtags: list[str] = field(default_factory=list)
    canonical_url: str | None = None
    linkedin_image: LinkedInImage | None = None

    @property
    def enabled_platforms(self) -> list[str]:
        return [p for p in ("devto", "linkedin") if getattr(self, p)]


@dataclass(frozen=True)
class Document:
    source_path: str
    title: str
    description: str | None
    body: str
    crosspost: CrosspostSpec
    frontmatter: dict


def split_frontmatter(source: str) -> tuple[dict | None, str]:
    """Return ``(frontmatter_dict_or_None, body)``."""
    match = _FRONTMATTER_RE.match(source)
    if not match:
        return None, source.strip()
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise CrosspostInvalid(f"frontmatter is not valid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise CrosspostInvalid("frontmatter must be a YAML mapping")
    return data, source[match.end():].strip()


def _validate(raw: dict, source_path: str) -> CrosspostSpec:
    errors = sorted(
        Draft202012Validator(CROSSPOST_SCHEMA).iter_errors(raw),
        key=lambda e: list(e.path),
    )
    if errors:
        details = "; ".join(
            f"crosspost.{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in errors
        )
        raise CrosspostInvalid(f"{source_path}: invalid crosspost block -- {details}")
    return CrosspostSpec(
        devto=raw.get("devto", False),
        linkedin=raw.get("linkedin", False),
        series=raw.get("series"),
        tags=list(raw.get("tags", [])),
        summary=raw.get("summary"),
        hashtags=list(raw.get("hashtags", [])),
        canonical_url=raw.get("canonical_url"),
        linkedin_image=(
            LinkedInImage(**raw["linkedin_image"]) if "linkedin_image" in raw else None
        ),
    )


def parse_document(source: str, source_path: str) -> Document | None:
    """Parse markdown source. Returns ``None`` if the document is not opted in."""
    frontmatter, body = split_frontmatter(source)
    if not frontmatter or "crosspost" not in frontmatter:
        return None

    raw = frontmatter["crosspost"]
    if not isinstance(raw, dict):
        raise CrosspostInvalid(f"{source_path}: crosspost must be a mapping")

    spec = _validate(raw, source_path)
    if not spec.enabled_platforms:
        return None

    title = frontmatter.get("title")
    if not title or not str(title).strip():
        raise CrosspostInvalid(
            f"{source_path}: frontmatter `title` is required when crosspost is enabled"
        )

    return Document(
        source_path=source_path,
        title=str(title).strip(),
        description=frontmatter.get("description"),
        body=body,
        crosspost=spec,
        frontmatter=frontmatter,
    )


def load_document(path: str | Path, repo_root: str | Path) -> Document | None:
    path = Path(path)
    rel = path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    return parse_document(path.read_text(encoding="utf-8"), rel)

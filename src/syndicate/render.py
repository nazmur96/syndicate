"""Rendering markdown into per-platform payloads.

Two shapes:

* ``render_full``  -- the whole body, for dev.to ``devto: full``. Relative links
  become absolute against the canonical URL, and mermaid fences are replaced
  with a pointer back to the canonical page (dev.to does not render mermaid).
* ``render_summary`` -- 3-5 short plain-text paragraphs plus the canonical link
  and at most two hashtags. Used for ``devto: summary`` and for every LinkedIn
  post (LinkedIn ``commentary`` is plain text, not markdown).

Both outputs end with "originally published at <canonical>".
"""

from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit, urlunsplit

from .canonical import slug_path
from .document import Document

MIN_SUMMARY_PARAGRAPHS = 3
MAX_SUMMARY_PARAGRAPHS = 5
MAX_HASHTAGS = 2
#: Soft cap per summary paragraph. LinkedIn's own commentary limit is 3000
#: characters; short paragraphs read better in-feed well before that.
MAX_PARAGRAPH_CHARS = 400

FOOTER_TEMPLATE = "originally published at {canonical}"

_FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[ \t]*([^\n`]*)$")
_LINK_RE = re.compile(r"(!?\[[^\]]*\])\(\s*<?([^)\s>]+)>?((?:\s+\"[^\"]*\")?)\s*\)")


class RenderError(Exception):
    """Raised when a document cannot be rendered for a platform."""


# --------------------------------------------------------------------------
# fence-aware segmentation
# --------------------------------------------------------------------------

def _segments(body: str) -> list[tuple[str, str, str]]:
    """Split ``body`` into ``(kind, info, text)`` where kind is 'text'|'fence'."""
    out: list[tuple[str, str, str]] = []
    buf: list[str] = []
    fence: tuple[str, str] | None = None  # (marker, info)
    fence_buf: list[str] = []

    for line in body.split("\n"):
        match = _FENCE_RE.match(line)
        if fence is None:
            if match:
                if buf:
                    out.append(("text", "", "\n".join(buf)))
                    buf = []
                fence = (match.group(2), match.group(3).strip())
                fence_buf = [line]
            else:
                buf.append(line)
        else:
            fence_buf.append(line)
            marker = fence[0]
            if match and match.group(2)[0] == marker[0] and len(match.group(2)) >= len(marker):
                out.append(("fence", fence[1], "\n".join(fence_buf)))
                fence, fence_buf = None, []
    if fence is not None:  # unterminated fence: keep verbatim
        out.append(("fence", fence[1], "\n".join(fence_buf)))
    if buf:
        out.append(("text", "", "\n".join(buf)))
    return out


# --------------------------------------------------------------------------
# full rendering
# --------------------------------------------------------------------------

_MARKDOWN_SUFFIXES = (".md", ".markdown")


def _site_root(site_base: str) -> str:
    parts = urlsplit(site_base)
    return urlunsplit((parts.scheme, parts.netloc, "", "", "")) + "/"


def _absolutise(target: str, source_path: str, site_base: str) -> str:
    """Resolve a markdown link target to an absolute URL on the Pages site.

    Relative targets are resolved against the document's *repo* path, not
    against its canonical URL: `./img/a.png` in `notes/cache.md` means the repo
    file `notes/img/a.png`, even though the page is served at `/notes/cache/`.
    """
    # Anchors, absolute URLs, protocol-relative and non-http schemes (mailto:,
    # tel:) are left exactly as the author wrote them.
    if target.startswith(("#", "//")) or re.match(r"^[a-zA-Z][\w+.-]*:", target):
        return target

    path, sep, tail = target.partition("#")
    path, qsep, query = path.partition("?")
    suffix = f"{qsep}{query}{sep}{tail}"

    if path.startswith("/"):
        # Root-relative links address the domain root, as they do in a browser.
        return _site_root(site_base) + path.lstrip("/") + suffix
    if not path:
        return target

    repo_path = posixpath.normpath(
        posixpath.join(posixpath.dirname(source_path), path)
    ).lstrip("/")

    base = site_base.rstrip("/")
    if repo_path.lower().endswith(_MARKDOWN_SUFFIXES):
        # Pages serves the rendered page, not the .md source.
        slug = slug_path(repo_path)
        return (f"{base}/{slug}/" if slug else f"{base}/") + suffix
    return f"{base}/{repo_path}{suffix}"


def rewrite_links(text: str, source_path: str, site_base: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}({_absolutise(m.group(2), source_path, site_base)}{m.group(3)})"

    return _LINK_RE.sub(repl, text)


def render_full(doc: Document, canonical: str, site_base: str) -> str:
    """Whole body, links absolutised, mermaid swapped for a canonical link."""
    parts: list[str] = []
    for kind, info, text in _segments(doc.body):
        if kind == "fence":
            if info.lower().startswith("mermaid"):
                parts.append(
                    f"> The diagram here is a mermaid chart, which dev.to does not "
                    f"render. [View the diagram on the original post]({canonical})."
                )
            else:
                parts.append(text)
        else:
            parts.append(rewrite_links(text, doc.source_path, site_base))

    body = "\n".join(parts).strip()
    footer = FOOTER_TEMPLATE.format(canonical=canonical)
    return f"{body}\n\n---\n\n*{footer}*\n"


# --------------------------------------------------------------------------
# summary rendering
# --------------------------------------------------------------------------

_STRIP_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),        # images -> alt text
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),          # links -> link text
    (re.compile(r"`([^`]*)`"), r"\1"),                      # inline code
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),                # bold
    (re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"), r"\1"),     # italic
    (re.compile(r"__([^_]+)__"), r"\1"),                    # bold (underscore)
    (re.compile(r"^\s{0,3}>\s?", re.MULTILINE), ""),        # blockquote markers
    (re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE), ""),    # bullets
]


def to_plain_text(text: str) -> str:
    for pattern, repl in _STRIP_RULES:
        text = pattern.sub(repl, text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _body_paragraphs(doc: Document) -> list[str]:
    """Leading prose paragraphs of the body, skipping headings and fences."""
    picked: list[str] = []
    for kind, _info, text in _segments(doc.body):
        if kind == "fence":
            continue
        for para in _paragraphs(text):
            if para.lstrip().startswith("#"):
                continue
            if re.fullmatch(r"[-*_ ]{3,}", para.strip()):
                continue
            picked.append(para)
            if len(picked) == MIN_SUMMARY_PARAGRAPHS:
                return picked
    return picked


def _truncate(text: str, limit: int = MAX_PARAGRAPH_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" .,;:") + "…"


def render_summary(doc: Document, canonical: str, site_base: str) -> str:
    """3-5 plain-text paragraphs, <=2 hashtags, canonical link last.

    ``site_base`` is accepted so both renderers share one signature; summary
    output is plain text with every inline link flattened to its text, so the
    canonical URL in the footer is the only link it contains.
    """
    spec = doc.crosspost
    if spec.summary:
        paragraphs = _paragraphs(spec.summary)
        if not MIN_SUMMARY_PARAGRAPHS <= len(paragraphs) <= MAX_SUMMARY_PARAGRAPHS:
            raise RenderError(
                f"{doc.source_path}: crosspost.summary must be "
                f"{MIN_SUMMARY_PARAGRAPHS}-{MAX_SUMMARY_PARAGRAPHS} paragraphs "
                f"(problem, finding, trade-off); got {len(paragraphs)}"
            )
    else:
        paragraphs = _body_paragraphs(doc)
        if len(paragraphs) < MIN_SUMMARY_PARAGRAPHS:
            raise RenderError(
                f"{doc.source_path}: body has only {len(paragraphs)} prose "
                f"paragraph(s); add a crosspost.summary block with "
                f"{MIN_SUMMARY_PARAGRAPHS}-{MAX_SUMMARY_PARAGRAPHS} paragraphs"
            )

    lines = [_truncate(to_plain_text(p).replace("\n", " ")) for p in paragraphs]

    if len(spec.hashtags) > MAX_HASHTAGS:  # defence in depth; schema caps at 2
        raise RenderError(f"{doc.source_path}: at most {MAX_HASHTAGS} hashtags allowed")
    if spec.hashtags:
        lines.append(" ".join(f"#{t.lstrip('#')}" for t in spec.hashtags))

    lines.append(FOOTER_TEMPLATE.format(canonical=canonical))
    return "\n\n".join(lines)

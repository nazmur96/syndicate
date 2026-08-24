import pytest

from syndicate.document import parse_document
from syndicate.render import RenderError, render_full, render_summary

CANONICAL = "https://acme.github.io/notes/notes/cache/"
SITE = "https://acme.github.io/notes"


def doc(body, **cross):
    fm = ["---", "title: Cache Invalidation", "crosspost:"]
    fm += [f"  devto: full"]
    for k, v in cross.items():
        if isinstance(v, list):
            fm.append(f"  {k}: [{', '.join(v)}]")
        elif "\n" in str(v):
            fm.append(f"  {k}: |")
            fm += [f"    {line}" for line in str(v).split("\n")]
        else:
            fm.append(f"  {k}: {v}")
    fm.append("---")
    return parse_document("\n".join(fm) + "\n\n" + body + "\n", "notes/cache.md")


# --- full rendering -------------------------------------------------------

def test_full_render_keeps_body_and_appends_canonical_footer():
    out = render_full(doc("Some **bold** prose."), CANONICAL, SITE)
    assert out.startswith("Some **bold** prose.")
    assert out.rstrip().endswith(f"*originally published at {CANONICAL}*")


def test_relative_markdown_links_resolve_to_the_target_docs_canonical_url():
    # ../design/adr.md from notes/cache.md is repo path design/adr.md, which is
    # served by Pages at /design/adr/ -- not as a raw .md file.
    out = render_full(doc("See [the design](../design/adr.md) and [anchor](#why)."), CANONICAL, SITE)
    assert "(https://acme.github.io/notes/design/adr/)" in out
    assert "(#why)" in out, "in-page anchors must stay relative"


def test_relative_assets_resolve_against_the_docs_directory_not_its_pretty_url():
    # ./img/flow.png from notes/cache.md is repo path notes/img/flow.png.
    out = render_full(doc("![diagram](./img/flow.png)"), CANONICAL, SITE)
    assert "(https://acme.github.io/notes/notes/img/flow.png)" in out


def test_absolute_links_are_left_alone():
    out = render_full(doc("[x](https://example.com/a)"), CANONICAL, SITE)
    assert "(https://example.com/a)" in out


def test_root_relative_links_resolve_against_site_root():
    out = render_full(doc("[x](/about/)"), CANONICAL, SITE)
    assert "(https://acme.github.io/about/)" in out


def test_mermaid_blocks_are_replaced_with_a_link():
    body = "Intro\n\n```mermaid\ngraph TD;\nA-->B;\n```\n\nOutro"
    out = render_full(doc(body), CANONICAL, SITE)
    assert "```mermaid" not in out
    assert "graph TD" not in out
    assert CANONICAL in out
    assert "diagram" in out.lower()
    assert "Intro" in out and "Outro" in out


def test_non_mermaid_code_blocks_survive():
    body = "```python\nprint('hi')\n```"
    out = render_full(doc(body), CANONICAL, SITE)
    assert "```python" in out and "print('hi')" in out


def test_links_inside_code_blocks_are_not_rewritten():
    body = "```md\n[x](./rel.md)\n```"
    out = render_full(doc(body), CANONICAL, SITE)
    assert "[x](./rel.md)" in out


# --- summary rendering ----------------------------------------------------

AUTHORED = "The cache went stale.\n\nTTLs were lying to us.\n\nWe traded freshness for load."


def test_summary_uses_authored_summary_paragraphs():
    out = render_summary(doc("Long body.", summary=AUTHORED), CANONICAL, SITE)
    assert "The cache went stale." in out
    assert "TTLs were lying to us." in out
    assert "Long body." not in out


def test_summary_ends_with_originally_published_line():
    out = render_summary(doc("Body.", summary=AUTHORED), CANONICAL, SITE)
    assert out.rstrip().endswith(f"originally published at {CANONICAL}")


def test_summary_includes_at_most_two_hashtags():
    out = render_summary(
        doc("Body.", summary=AUTHORED, hashtags=["python", "caching"]), CANONICAL, SITE
    )
    assert "#python #caching" in out
    assert out.count("#") == 2


def test_summary_is_plain_text_markdown_is_stripped():
    authored = "A **bold** claim.\n\nSee [the docs](https://x.dev).\n\nAnd `code` too."
    out = render_summary(doc("Body.", summary=authored), CANONICAL, SITE)
    assert "**" not in out and "`" not in out
    assert "[the docs]" not in out
    assert "the docs" in out


def test_summary_falls_back_to_leading_body_paragraphs():
    body = "# Heading\n\nFirst para.\n\nSecond para.\n\nThird para.\n\nFourth para."
    out = render_summary(doc(body), CANONICAL, SITE)
    assert "First para." in out and "Third para." in out
    assert "Fourth para." not in out
    assert "# Heading" not in out


def test_summary_fallback_skips_code_and_mermaid():
    body = "```mermaid\ngraph TD;\n```\n\nReal opener.\n\nSecond.\n\nThird."
    out = render_summary(doc(body), CANONICAL, SITE)
    assert "graph TD" not in out
    assert out.lstrip().startswith("Real opener.")


def test_authored_summary_with_too_few_paragraphs_hard_fails():
    with pytest.raises(RenderError) as exc:
        render_summary(doc("Body.", summary="Only one paragraph."), CANONICAL, SITE)
    assert "3" in str(exc.value)


def test_authored_summary_with_too_many_paragraphs_hard_fails():
    six = "\n\n".join(f"Para {i}." for i in range(6))
    with pytest.raises(RenderError):
        render_summary(doc("Body.", summary=six), CANONICAL, SITE)


def test_body_too_short_to_summarise_hard_fails():
    with pytest.raises(RenderError) as exc:
        render_summary(doc("Just one line."), CANONICAL, SITE)
    assert "summary" in str(exc.value).lower()

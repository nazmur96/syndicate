# syndicate

Cross-post markdown from a GitHub repo to dev.to and LinkedIn, with a human
gate in the middle and no double-posting.

Built to be reused: a consuming repo adds a ~15-line caller workflow and a
`crosspost:` block in one document's frontmatter. Everything else lives here.

```
push to main  ──▶  dev.to DRAFT          (automatic, nothing public)
                        │
                   you read it
                        │
manual dispatch  ──▶  dev.to published + LinkedIn post
```

## Why it is shaped like this

**Publishing is not a push.** Drafting is cheap and reversible, so it happens
automatically. Publishing is neither — a LinkedIn post cannot be edited or
withdrawn through the API — so it is a deliberate, separate gesture with an
explicit document path. Publish mode refuses to run without one.

**The manifest is committed, not cached.** `.syndicate/manifest.json` records
what went where and the hash of what was sent. It is committed to the consuming
repo because a cache that can be evicted is a cache that eventually double-posts.

**Platforms fail independently.** An expired LinkedIn token does not stop the
dev.to draft. Each platform is attempted on its own and reported on its own.

**Canonical URLs point home.** Every cross-post carries a canonical URL back to
the repo's GitHub Pages site, so the copies do not compete with the original.

## Install

Two files in the consuming repo, and account-level secrets set once:

```yaml
# .github/workflows/syndicate.yml
name: Syndicate
on:
  push:
    branches: [main]
    paths: ['**/*.md']
  workflow_dispatch:
    inputs:
      mode:
        type: choice
        options: [draft, publish]
        default: draft
      paths:
        description: 'Required to publish: the document to publish'
permissions:
  contents: write
jobs:
  syndicate:
    uses: OWNER/syndicate/.github/workflows/syndicate.yml@v1
    with:
      mode: ${{ inputs.mode || 'draft' }}
      paths: ${{ inputs.paths || '' }}
    secrets: inherit
```

Full walkthrough — dev.to key, the LinkedIn OAuth flow, where the secrets go:
**[docs/SETUP.md](docs/SETUP.md)**.

Also copy [`templates/linkedin-token-check.yml`](templates/linkedin-token-check.yml)
if the repo posts to LinkedIn, and paste
[`docs/CLAUDE.md.snippet.md`](docs/CLAUDE.md.snippet.md) into the repo's
`CLAUDE.md`.

## Opting a document in

```yaml
---
title: The Cache That Lied
description: A stale-read incident, and what the TTL was actually measuring.
crosspost:
  devto: full          # full | summary | false
  linkedin: summary    # summary | false
  series: Incident Notes
  tags: [caching, postgres, debugging]   # max 4, dev.to only
  hashtags: [caching, postgres]          # max 2
  summary: |
    Three to five short paragraphs for someone scrolling a feed.
    This is what LinkedIn posts verbatim.
---
```

No `crosspost:` block, no syndication. A block that is *present but wrong* fails
the run rather than being skipped — an opt-in that silently does nothing is the
worst of both.

`full` sends the whole body, with relative links rewritten to absolute and
mermaid fences replaced by a pointer back to the canonical page (dev.to does not
render mermaid). `summary` sends the authored `summary`. LinkedIn is always the
summary form; there is no full mode, because there should not be.

See [`examples/example-post.md`](examples/example-post.md).

## CLI

```bash
syndicate draft   docs/post.md      # create/update the dev.to draft
syndicate publish docs/post.md      # publish dev.to, post to LinkedIn
syndicate check                     # credentials, read-only
syndicate token-status              # LinkedIn 60-day expiry forecast
syndicate draft docs/post.md --dry-run   # render only, write nothing
```

`--dry-run` prints exactly what each platform would receive and touches nothing.
Exit codes: `0` fine, `1` a platform failed, `2` an authoring error. (For
`token-status`: `0` fine, `1` expiring, `2` expired.)

## What is verified, and what is not

Worth being explicit about, because the two adapters are not equally trusted:

| | status |
|---|---|
| **dev.to** | **Live-verified.** Draft creation, update, and publish were exercised against the real API. |
| **LinkedIn** | **Mock-tested only.** Written against the documented Posts API and covered by tests against a fake transport. It has never touched a live LinkedIn account. |

The LinkedIn adapter's module docstring marks which of its behaviours come from
the API reference and which are inference. Treat the first live post as a test.

Deliberately out of scope: Medium and X.

## Known constraints

- **LinkedIn tokens expire every 60 days** and cannot be refreshed
  programmatically. The weekly check opens an issue before that happens; the
  runbook is in [docs/SETUP.md](docs/SETUP.md#linkedin-re-auth-runbook).
- **LinkedIn is post-once.** The API has no draft state on creation and no edit
  path here, so a posted summary is never rewritten, even if the source changes.
- **Personal profiles only.** Organisation posting needs
  `w_organization_social`, which is not implemented.
- **dev.to tag slugification is undocumented**, so tags are sanitised locally to
  keep the manifest hash honest; the run reports any tag dev.to rewrote.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/python -m pytest
```

No network in the test suite — every adapter is exercised through a fake
transport.

## Versioning

Consumers pin `@v1`, a **moving tag** on the latest v1-compatible commit. Fixes
reach every repo without a bump anywhere. Pin a commit SHA instead if you would
rather review each change.

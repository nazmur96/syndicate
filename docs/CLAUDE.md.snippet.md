# CLAUDE.md snippet for consuming repos

Paste the block below into the `CLAUDE.md` of any repo that uses this tool.
It is written for an agent working *in that repo*, not in this one.

---

```markdown
## Syndication (dev.to + LinkedIn)

Documents in this repo can be cross-posted to dev.to and LinkedIn by
`.github/workflows/syndicate.yml`. Canonical URLs always point back at this
repo's GitHub Pages site, never at dev.to.

### Opting a document in

Syndication is opt-in per document, via a `crosspost:` block in the
frontmatter. No block means the document is skipped entirely.

    ---
    title: The Cache That Lied
    description: A stale-read incident, and what the TTL was measuring.
    crosspost:
      devto: full          # full | summary | false
      linkedin: summary    # summary | false  (there is no full mode)
      series: Incident Notes
      tags: [caching, postgres, debugging]   # max 4, dev.to only
      hashtags: [caching, postgres]          # max 2, summary/LinkedIn only
      summary: |
        Three to five short paragraphs, blank-line separated, written for
        someone scrolling a feed. This is what LinkedIn posts verbatim.
      canonical_url: https://...             # optional override
    ---

An *invalid* `crosspost:` block fails the whole run — it is an authoring bug,
not something to skip past. Absent is fine; wrong is not.

### The human gate — do not step around it

- A push to `main` creates or updates a **dev.to draft**. Nothing goes public,
  and LinkedIn is never contacted.
- Publishing is a separate, manual gesture: **Actions → Syndicate → Run
  workflow**, mode `publish`, with the document's path in `paths`.

**Never run publish mode on the user's behalf without being asked for that
specific document.** A LinkedIn post cannot be edited or withdrawn through the
API, and the manifest deliberately refuses to re-post: if the summary is wrong
when it goes out, it stays wrong.

### `.syndicate/manifest.json`

Committed, and machine-owned. It records what was posted where, and the content
hash that decides whether a re-run is a no-op. Do not hand-edit it and do not
resolve merge conflicts in it by guessing — a wrong hash means either a silent
skip or a duplicate post. If it conflicts, take the version with the
`remote_id`s in it and re-run in draft mode.

### Checking things locally

    syndicate draft path/to/doc.md --dry-run   # renders, writes nothing
    syndicate check                            # credentials, read-only
    syndicate token-status                     # LinkedIn 60-day expiry

`--dry-run` is the right way to see what a change does to the rendered output.

### LinkedIn tokens expire every 60 days

They cannot be refreshed programmatically. A weekly workflow opens an issue
titled *"LinkedIn access token needs re-authorising"* before that happens; the
runbook is in the syndicate repo at `docs/SETUP.md`. While the token is
expired, dev.to keeps working — the two platforms fail independently.
```

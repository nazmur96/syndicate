"""The orchestrator: human gate, idempotency, fail-soft.

Two modes:

``draft``   -- what a push runs. Creates or updates a dev.to *draft* and stops.
               LinkedIn is never contacted.
``publish`` -- what a manual trigger (workflow_dispatch or a publish label)
               runs. Publishes the dev.to draft and posts the LinkedIn summary.

Every platform write is gated on the manifest content hash, and every platform
is attempted independently: one failing never blocks the other.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .canonical import CanonicalConfig, canonical_url
from .config import DEVTO_API_KEY, LINKEDIN_ACCESS_TOKEN
from .document import Document, load_document
from .manifest import Manifest, PlatformRecord, content_hash
from .platforms.base import PlatformError, PlatformResult
from .platforms.devto import sanitise_tags
from .render import render_full, render_summary

MODE_DRAFT = "draft"
MODE_PUBLISH = "publish"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Syndicator:
    def __init__(
        self,
        repo_root: str | Path,
        manifest: Manifest,
        canonical_config: CanonicalConfig,
        devto=None,
        linkedin=None,
        pages_base_url: str | None = None,
    ):
        self.repo_root = Path(repo_root)
        self.manifest = manifest
        self.canonical_config = canonical_config
        self.devto = devto
        self.linkedin = linkedin
        self._site_base = pages_base_url

    # -- helpers -----------------------------------------------------------

    def _site_base_url(self) -> str:
        if self._site_base:
            return self._site_base.rstrip("/")
        # Same resolution as canonical, minus the document path.
        return canonical_url("index.md", self.canonical_config).rstrip("/")

    def _canonical_for(self, doc: Document) -> str:
        return canonical_url(
            doc.source_path, self.canonical_config, override=doc.crosspost.canonical_url
        )

    # -- run ---------------------------------------------------------------

    def run(self, paths, mode: str = MODE_DRAFT) -> list[PlatformResult]:
        if mode not in (MODE_DRAFT, MODE_PUBLISH):
            raise ValueError(f"unknown mode {mode!r}; expected draft or publish")

        results: list[PlatformResult] = []
        touched = False
        for path in paths:
            # A CrosspostInvalid here is deliberately NOT caught: an invalid
            # opt-in block is an authoring bug and must fail the whole run.
            doc = load_document(path, self.repo_root)
            if doc is None:
                continue  # not opted in -- skipped entirely
            doc_results = self._run_document(doc, mode)
            results.extend(doc_results)
            touched = touched or any(r.action not in ("skipped", "failed") for r in doc_results)

        if touched:
            self.manifest.save()
        return results

    def _run_document(self, doc: Document, mode: str) -> list[PlatformResult]:
        results: list[PlatformResult] = []
        canonical = self._canonical_for(doc)
        site_base = self._site_base_url()

        if doc.crosspost.devto:
            results.append(self._do(self._devto_step, "devto", doc, canonical, site_base, mode))
        if doc.crosspost.linkedin:
            results.append(
                self._do(self._linkedin_step, "linkedin", doc, canonical, site_base, mode)
            )
        return results

    @staticmethod
    def _do(step, platform, doc, canonical, site_base, mode) -> PlatformResult:
        """Fail-soft wrapper: convert any platform error into a failed result."""
        try:
            return step(doc, canonical, site_base, mode)
        except PlatformError as exc:
            return PlatformResult(platform=platform, action="failed", message=str(exc))

    # -- dev.to ------------------------------------------------------------

    def _devto_step(self, doc, canonical, site_base, mode) -> PlatformResult:
        if self.devto is None:
            return PlatformResult(
                platform="devto",
                action="skipped",
                message=f"{DEVTO_API_KEY} is not configured; dev.to skipped.",
            )

        body = (
            render_full(doc, canonical, site_base)
            if doc.crosspost.devto == "full"
            else render_summary(doc, canonical, site_base)
        )
        tags, rewritten = sanitise_tags(doc.crosspost.tags)
        digest = content_hash(body, tags, doc.crosspost.series)
        note = (
            f" (dev.to slugified these tags: {', '.join(rewritten)})" if rewritten else ""
        )

        record = self.manifest.get(doc.source_path, "devto")
        needs_write = self.manifest.needs_write(doc.source_path, "devto", digest)

        payload = dict(
            title=doc.title,
            body_markdown=body,
            canonical_url=canonical,
            tags=tags,
            series=doc.crosspost.series,
            description=doc.description,
        )

        if record is None:
            result = self.devto.create_draft(**payload)
            action = "created"
        elif needs_write:
            result = self.devto.update_draft(record.remote_id, **payload)
            action = "updated"
        else:
            result = None
            action = "skipped"

        article_id = result.remote_id if result else record.remote_id
        state = record.state if record else "draft"

        if mode == MODE_PUBLISH and state != "published":
            published = self.devto.publish(article_id)
            state = "published"
            url = published.url
            action = "published"
        else:
            url = (result.url if result else record.url) or self.devto.DASHBOARD_URL

        if action == "skipped":
            return PlatformResult(
                platform="devto",
                action="skipped",
                remote_id=article_id,
                url=url,
                message="unchanged since the last run",
            )

        self.manifest.record(
            doc.source_path,
            "devto",
            PlatformRecord(
                remote_id=article_id,
                url=url,
                state=state,
                content_hash=digest,
                posted_at=_now(),
            ),
        )
        return PlatformResult(
            platform="devto",
            action=action,
            remote_id=article_id,
            url=url,
            message=(
                f"dev.to draft {article_id} -- review at {self.devto.DASHBOARD_URL}{note}"
                if state != "published"
                else f"published{note}"
            ),
        )

    # -- LinkedIn ----------------------------------------------------------

    def _linkedin_image(self, doc: Document) -> tuple[bytes, str] | None:
        """Read the optional post image, relative to the document's directory."""
        spec = doc.crosspost.linkedin_image
        if spec is None:
            return None
        path = self.repo_root / Path(doc.source_path).parent / spec.path
        try:
            return path.read_bytes(), spec.alt
        except OSError as exc:
            raise PlatformError(
                f"{doc.source_path}: linkedin_image {spec.path} cannot be read: "
                f"{exc.strerror}"
            ) from exc

    def _linkedin_step(self, doc, canonical, site_base, mode) -> PlatformResult:
        if mode != MODE_PUBLISH:
            return PlatformResult(
                platform="linkedin",
                action="skipped",
                message="held behind the human gate; run the publish trigger to post",
            )
        if self.linkedin is None:
            return PlatformResult(
                platform="linkedin",
                action="skipped",
                message=f"{LINKEDIN_ACCESS_TOKEN} is not configured; LinkedIn skipped.",
            )

        commentary = render_summary(doc, canonical, site_base)
        digest = content_hash(commentary, [], None)
        if not self.manifest.needs_write(doc.source_path, "linkedin", digest):
            record = self.manifest.get(doc.source_path, "linkedin")
            return PlatformResult(
                platform="linkedin",
                action="skipped",
                remote_id=record.remote_id,
                url=record.url,
                message="already posted; LinkedIn posts are never rewritten",
            )

        # A LinkedIn post cannot be edited into existence twice: once the URN is
        # recorded we never re-post, even if the summary text changes.
        if self.manifest.get(doc.source_path, "linkedin") is not None:
            return PlatformResult(
                platform="linkedin",
                action="skipped",
                message="summary changed after posting; LinkedIn is post-once, not updated",
            )

        image = self._linkedin_image(doc)
        self.linkedin.verify_author()
        result = self.linkedin.create_post(commentary, image=image)
        self.manifest.record(
            doc.source_path,
            "linkedin",
            PlatformRecord(
                remote_id=result.remote_id,
                url=result.url,
                state="published",
                content_hash=digest,
                posted_at=_now(),
            ),
        )
        return PlatformResult(
            platform="linkedin",
            action="published",
            remote_id=result.remote_id,
            url=result.url,
            message="posted to LinkedIn",
        )

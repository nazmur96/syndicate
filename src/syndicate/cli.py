"""Command line entry point.

    syndicate draft   <paths...>   # what a push runs: dev.to draft, then stop
    syndicate publish <paths...>   # what a manual trigger runs
    syndicate check                # read-only credential check
    syndicate token-status         # how long the LinkedIn token has left

Exit codes: 0 all good, 1 at least one platform failed (fail-soft: every other
platform still ran), 2 an authoring error (invalid crosspost block).

``token-status`` reads those codes differently -- 0 fine, 1 expiring soon,
2 already expired -- because it reports on one thing, not a batch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .canonical import CanonicalConfig
from .config import (
    DEVTO_API_KEY,
    LINKEDIN_ACCESS_TOKEN,
    LINKEDIN_PERSON_URN,
    Secrets,
)
from .core import MODE_DRAFT, MODE_PUBLISH, Syndicator
from .document import CrosspostInvalid, load_document
from .expiry import (
    DEFAULT_WARN_DAYS,
    EXPIRED,
    PROBE_EXPIRED,
    PROBE_INCONCLUSIVE,
    PROBE_VALID,
    TOKEN_ISSUED_AT,
    WARNING,
    token_status,
)
from .manifest import DEFAULT_MANIFEST_PATH, Manifest
from .platforms.base import CredentialError, PlatformError, TokenExpired
from .platforms.devto import DevToClient
from .platforms.linkedin import LinkedInClient
from .render import RenderError, render_full, render_summary

_ICON = {
    "created": "+",
    "updated": "~",
    "published": "*",
    "skipped": "-",
    "failed": "!",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syndicate", description=__doc__)
    parser.add_argument("mode", choices=[MODE_DRAFT, MODE_PUBLISH, "check", "token-status"])
    parser.add_argument("paths", nargs="*", help="markdown files to consider")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="owner/repo; defaults to $GITHUB_REPOSITORY",
    )
    parser.add_argument(
        "--pages-base-url",
        default=os.environ.get("SYNDICATE_PAGES_BASE_URL"),
        help="override the canonical base URL (custom Pages domain)",
    )
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--linkedin-api-version", default=None)
    parser.add_argument("--dry-run", action="store_true", help="render only, write nothing")
    parser.add_argument(
        "--warn-days",
        type=int,
        default=DEFAULT_WARN_DAYS,
        help="token-status: warn this many days before the LinkedIn token lapses",
    )
    parser.add_argument("--today", default=None, help="token-status: pin today's date (testing)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def _build_clients(secrets: Secrets, args):
    devto = None
    linkedin = None
    if secrets.has(DEVTO_API_KEY):
        devto = DevToClient(secrets.require(DEVTO_API_KEY))
    if secrets.has(LINKEDIN_ACCESS_TOKEN) and secrets.has(LINKEDIN_PERSON_URN):
        linkedin = LinkedInClient(
            secrets.require(LINKEDIN_ACCESS_TOKEN),
            secrets.require(LINKEDIN_PERSON_URN),
            api_version=args.linkedin_api_version,
        )
    return devto, linkedin


def _check(secrets: Secrets, args) -> int:
    """Read-only credential check. Never writes anything anywhere."""
    failed = False
    if not secrets.has(DEVTO_API_KEY):
        print(f"  {DEVTO_API_KEY}: not set")
    else:
        try:
            me = DevToClient(secrets.require(DEVTO_API_KEY)).me()
            print(f"  {DEVTO_API_KEY}: ok (dev.to user @{me.get('username')})")
        except PlatformError as exc:
            print(f"  {DEVTO_API_KEY}: FAILED -- {secrets.redact(str(exc))}")
            failed = True

    if not (secrets.has(LINKEDIN_ACCESS_TOKEN) and secrets.has(LINKEDIN_PERSON_URN)):
        print(f"  {LINKEDIN_ACCESS_TOKEN}/{LINKEDIN_PERSON_URN}: not set")
    else:
        try:
            client = LinkedInClient(
                secrets.require(LINKEDIN_ACCESS_TOKEN),
                secrets.require(LINKEDIN_PERSON_URN),
                api_version=args.linkedin_api_version,
            )
            verdict = client.verify_author()
            print(
                f"  {LINKEDIN_ACCESS_TOKEN}: "
                + ("ok (author URN matches)" if verdict else "present (author URN unverifiable "
                   "without the openid/profile scope)")
            )
        except (PlatformError, ValueError) as exc:
            print(f"  {LINKEDIN_ACCESS_TOKEN}: FAILED -- {secrets.redact(str(exc))}")
            failed = True
    return 1 if failed else 0


def _linkedin_probe(secrets: Secrets, args) -> str | None:
    """Ask LinkedIn whether the token still works. Never writes anything.

    Returns ``None`` when LinkedIn is not configured at all. A 401/403 on
    ``/v2/userinfo`` is *inconclusive*, not a failure: a ``w_member_social``-only
    token cannot read that endpoint even while perfectly healthy.
    """
    if not (secrets.has(LINKEDIN_ACCESS_TOKEN) and secrets.has(LINKEDIN_PERSON_URN)):
        return None
    client = LinkedInClient(
        secrets.require(LINKEDIN_ACCESS_TOKEN),
        secrets.require(LINKEDIN_PERSON_URN),
        api_version=args.linkedin_api_version,
    )
    try:
        return PROBE_VALID if client.verify_author() else PROBE_INCONCLUSIVE
    except TokenExpired:
        return PROBE_EXPIRED
    except (CredentialError, PlatformError, ValueError):
        return PROBE_INCONCLUSIVE


def _token_status(secrets: Secrets, args) -> int:
    from datetime import date

    probe = _linkedin_probe(secrets, args)
    try:
        status = token_status(
            issued_at=secrets.get(TOKEN_ISSUED_AT),
            probe=probe,
            warn_days=args.warn_days,
            today=date.fromisoformat(args.today) if args.today else None,
        )
    except ValueError as exc:
        print(f"error: {secrets.redact(str(exc))}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(status.as_dict(), indent=2))
    else:
        print(f"  linkedin token: {status.status} -- {secrets.redact(status.message)}")
    return {WARNING: 1, EXPIRED: 2}.get(status.status, 0)


def _dry_run(args, canonical_config, paths) -> int:
    from .canonical import canonical_url

    print("dry-run: rendering only, nothing will be written\n")
    for path in paths:
        doc = load_document(path, args.repo_root)
        if doc is None:
            print(f"- {path}: no crosspost block, skipped")
            continue
        canonical = canonical_url(
            doc.source_path, canonical_config, override=doc.crosspost.canonical_url
        )
        site_base = canonical_url("index.md", canonical_config).rstrip("/")
        print(f"=== {doc.source_path} -> {canonical}")
        if doc.crosspost.devto:
            body = (
                render_full(doc, canonical, site_base)
                if doc.crosspost.devto == "full"
                else render_summary(doc, canonical, site_base)
            )
            print(f"\n--- dev.to ({doc.crosspost.devto}) ---\n{body}")
        if doc.crosspost.linkedin:
            print(f"\n--- linkedin ---\n{render_summary(doc, canonical, site_base)}")
        print()
    return 0


def _report(mode: str, results, as_json: bool) -> None:
    if as_json:
        print(json.dumps(
            {
                "mode": mode,
                "results": [
                    {
                        "platform": r.platform,
                        "action": r.action,
                        "remote_id": r.remote_id,
                        "url": r.url,
                        "message": r.message,
                    }
                    for r in results
                ],
            },
            indent=2,
        ))
        return

    if not results:
        print("nothing to syndicate (no document opted in)")
        return
    for r in results:
        line = f"  {_ICON.get(r.action, '?')} {r.platform}: {r.action}"
        if r.url:
            line += f"  {r.url}"
        if r.message:
            line += f"\n      {r.message}"
        print(line)


def _write_step_summary(mode: str, results) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = [f"### Syndication ({mode})", "", "| platform | action | link |", "|---|---|---|"]
    for r in results:
        link = f"[open]({r.url})" if r.url else ""
        lines.append(f"| {r.platform} | {r.action} | {link} |")
        if r.message:
            lines.append(f"| | | {r.message} |")
    with open(target, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    secrets = Secrets.load(args.dotenv)

    if args.mode == "check":
        return _check(secrets, args)
    if args.mode == "token-status":
        return _token_status(secrets, args)

    canonical_config = CanonicalConfig(
        repository=args.repository, pages_base_url=args.pages_base_url
    )
    paths = [Path(p) for p in args.paths]

    try:
        if args.dry_run:
            return _dry_run(args, canonical_config, paths)

        manifest_path = args.manifest or Path(args.repo_root) / DEFAULT_MANIFEST_PATH
        devto, linkedin = _build_clients(secrets, args)
        syndicator = Syndicator(
            repo_root=args.repo_root,
            manifest=Manifest.load(manifest_path),
            canonical_config=canonical_config,
            devto=devto,
            linkedin=linkedin,
            pages_base_url=args.pages_base_url,
        )
        results = syndicator.run(paths, mode=args.mode)
    except (CrosspostInvalid, RenderError, ValueError) as exc:
        print(f"error: {secrets.redact(str(exc))}", file=sys.stderr)
        return 2

    _report(args.mode, results, args.json)
    _write_step_summary(args.mode, results)
    return 1 if any(r.action == "failed" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

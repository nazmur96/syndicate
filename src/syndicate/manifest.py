"""The committed syndication manifest -- the idempotency record.

One JSON file, committed to the consuming repo, mapping
``document path -> platform -> what we posted``. Every write to a platform is
gated on it: if the content hash matches what the manifest already records, the
write is skipped. That is what stops a re-run (or a force-push, or a second
workflow trigger) from double-posting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

MANIFEST_VERSION = 1
DEFAULT_MANIFEST_PATH = ".syndicate/manifest.json"


def content_hash(rendered_body: str, tags: list[str], series: str | None) -> str:
    """Hash of everything we would send to a platform.

    Hashes the *rendered* payload, not the source file, so a change that does
    not alter the output (a frontmatter field we do not send, say) does not
    trigger a pointless update -- and a renderer change that does alter the
    output correctly does.
    """
    payload = json.dumps(
        {"body": rendered_body, "tags": list(tags), "series": series},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class PlatformRecord:
    #: dev.to article id, or LinkedIn post URN.
    remote_id: str | None = None
    url: str | None = None
    #: "draft" or "published". LinkedIn only ever reaches "published".
    state: str | None = None
    content_hash: str | None = None
    posted_at: str | None = None


class Manifest:
    def __init__(self, path: Path, documents: dict[str, dict[str, PlatformRecord]]):
        self.path = Path(path)
        self.documents = documents

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        path = Path(path)
        if not path.exists():
            return cls(path, {})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{path}: manifest is not valid JSON ({exc}). Refusing to continue: "
                f"treating it as empty would re-post everything."
            ) from exc
        documents = {
            doc: {
                platform: PlatformRecord(**record)
                for platform, record in platforms.items()
            }
            for doc, platforms in data.get("documents", {}).items()
        }
        return cls(path, documents)

    def get(self, source_path: str, platform: str) -> PlatformRecord | None:
        return self.documents.get(source_path, {}).get(platform)

    def record(self, source_path: str, platform: str, record: PlatformRecord) -> None:
        self.documents.setdefault(source_path, {})[platform] = record

    def needs_write(self, source_path: str, platform: str, new_hash: str) -> bool:
        existing = self.get(source_path, platform)
        if existing is None:
            return True
        return existing.content_hash != new_hash

    def save(self) -> None:
        payload = {
            "version": MANIFEST_VERSION,
            "documents": {
                doc: {
                    platform: {k: v for k, v in asdict(rec).items() if v is not None}
                    for platform, rec in sorted(platforms.items())
                }
                for doc, platforms in sorted(self.documents.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

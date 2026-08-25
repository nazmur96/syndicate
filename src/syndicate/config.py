"""Credential loading.

Precedence: real environment first (that is how GitHub Actions injects
organisation or repository secrets), then a gitignored local ``.env`` for
testing. Values are never written to disk, never logged, and ``repr`` is
redacted so a stray traceback cannot leak them.
"""

from __future__ import annotations

import os
from pathlib import Path

DEVTO_API_KEY = "DEVTO_API_KEY"
LINKEDIN_ACCESS_TOKEN = "LINKEDIN_ACCESS_TOKEN"
LINKEDIN_PERSON_URN = "LINKEDIN_PERSON_URN"

#: Not secret, but read the same way.
KNOWN_SECRETS = (DEVTO_API_KEY, LINKEDIN_ACCESS_TOKEN)


class MissingCredential(Exception):
    """Raised when a required credential is absent or blank."""


def load_dotenv(path: str | Path) -> dict[str, str]:
    """Minimal ``.env`` reader. Never mutates ``os.environ``."""
    path = Path(path)
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


class Secrets:
    def __init__(self, env: dict[str, str], dotenv: dict[str, str]):
        self._env = env
        self._dotenv = dotenv

    @classmethod
    def load(cls, dotenv_path: str | Path = ".env") -> Secrets:
        return cls(dict(os.environ), load_dotenv(dotenv_path))

    def get(self, name: str) -> str | None:
        for source in (self._env, self._dotenv):
            value = source.get(name)
            if value is not None and value.strip():
                return value.strip()
        return None

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def require(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise MissingCredential(
                f"{name} is not set. Locally: add it to .env (gitignored). "
                f"In CI: add it as a GitHub secret named {name}."
            )
        return value

    def redact(self, text: str) -> str:
        """Mask any known secret value appearing in ``text``."""
        for name in KNOWN_SECRETS:
            value = self.get(name)
            if value and len(value) >= 6:
                text = text.replace(value, f"***{name}***")
        return text

    def __repr__(self) -> str:  # never leak values
        present = sorted(n for n in (*KNOWN_SECRETS, LINKEDIN_PERSON_URN) if self.has(n))
        return f"Secrets(present={present})"

    __str__ = __repr__

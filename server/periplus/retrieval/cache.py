"""On-disk cache of fetched pages, keyed by normalised URL.

Worth more than any token optimisation in this project. Tuning a prompt means running the
same brief dozens of times against the same forty pages; without this, every iteration
re-fetches the lot — slower, ruder to the sites involved, and dependent on them still
answering. With it, only the first run touches the network.

Deliberately a directory of JSON files rather than a database. The cache is inspectable
with an editor, survives schema churn, and deleting a bad entry is ``rm``.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from periplus.retrieval.urls import normalise_url, url_key

CACHE_VERSION = 1


@dataclass(frozen=True, slots=True)
class CachedPage:
    url: str
    html: str
    status: int
    content_type: str
    stored_at: datetime

    @property
    def age(self) -> timedelta:
        return datetime.now(UTC) - self.stored_at


class PageCache(Protocol):
    def get(self, url: str) -> CachedPage | None: ...

    def put(self, url: str, html: str, *, status: int, content_type: str) -> None: ...


class NullCache:
    """No storage. Used when a caller explicitly wants live pages every time."""

    def get(self, url: str) -> CachedPage | None:
        return None

    def put(self, url: str, html: str, *, status: int, content_type: str) -> None:
        return None


class DiskCache:
    """One JSON file per normalised URL under ``root``."""

    def __init__(self, root: Path | str, *, ttl: timedelta | None = None) -> None:
        self.root = Path(root)
        self.ttl = ttl
        self.hits = 0
        self.misses = 0

    def path_for(self, url: str) -> Path:
        key = url_key(url)
        # Two levels of fan-out: a shared cache across many runs would otherwise put tens
        # of thousands of files in one directory.
        return self.root / key[:2] / f"{key}.json"

    def get(self, url: str) -> CachedPage | None:
        path = self.path_for(url)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.misses += 1
            return None

        if payload.get("version") != CACHE_VERSION:
            self.misses += 1
            return None

        try:
            stored_at = datetime.fromisoformat(payload["stored_at"])
        except (KeyError, ValueError):
            self.misses += 1
            return None

        page = CachedPage(
            url=payload.get("url", url),
            html=payload.get("html", ""),
            status=int(payload.get("status", 200)),
            content_type=payload.get("content_type", ""),
            stored_at=stored_at,
        )
        if self.ttl is not None and page.age > self.ttl:
            self.misses += 1
            return None

        self.hits += 1
        return page

    def put(self, url: str, html: str, *, status: int, content_type: str) -> None:
        path = self.path_for(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "url": normalise_url(url),
            "html": html,
            "status": status,
            "content_type": content_type,
            "stored_at": datetime.now(UTC).isoformat(),
        }
        # Write-then-rename: an interrupted run leaves no half-written entry that would
        # read back as a valid but truncated page.
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False)
            temp = Path(handle.name)
        temp.replace(path)

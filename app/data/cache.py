"""A very small TTL cache for upstream payloads.

Deliberately dependency-free: an in-process dict plus an optional on-disk copy
so a warm container survives a worker restart. Good enough for a screening tool;
swap for Redis if this ever needs to scale horizontally.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.logging_config import get_logger

log = get_logger(__name__)


class TTLCache:
    """Time-boxed key/value cache with optional JSON persistence."""

    def __init__(self, ttl_seconds: int, directory: Path | None = None) -> None:
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.directory = Path(directory) if directory else None
        self._memory: dict[str, tuple[float, Any]] = {}
        if self.directory:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # pragma: no cover - read-only filesystems
                log.warning("cache directory unavailable (%s); memory-only", exc)
                self.directory = None

    def _path(self, key: str) -> Path | None:
        if not self.directory:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self.directory / f"{safe}.json"

    def get(self, key: str) -> tuple[Any, float] | None:
        """Return ``(value, age_seconds)`` when a fresh entry exists."""
        entry = self._memory.get(key)
        if entry is None:
            entry = self._read_disk(key)
        if entry is None:
            return None
        stored_at, value = entry
        age = time.time() - stored_at
        if self.ttl_seconds and age > self.ttl_seconds:
            self.invalidate(key)
            return None
        return value, age

    def set(self, key: str, value: Any) -> None:
        stored_at = time.time()
        self._memory[key] = (stored_at, value)
        path = self._path(key)
        if path is None:
            return
        try:
            path.write_text(json.dumps({"stored_at": stored_at, "value": value}),
                            encoding="utf-8")
        except (OSError, TypeError) as exc:
            log.warning("could not persist cache key %s: %s", key, exc)

    def invalidate(self, key: str) -> None:
        self._memory.pop(key, None)
        path = self._path(key)
        if path and path.exists():
            try:
                path.unlink()
            except OSError:  # pragma: no cover
                pass

    def _read_disk(self, key: str) -> tuple[float, Any] | None:
        path = self._path(key)
        if not path or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return float(payload["stored_at"]), payload["value"]
        except (OSError, ValueError, KeyError) as exc:
            log.warning("discarding unreadable cache entry %s: %s", key, exc)
            self.invalidate(key)
            return None

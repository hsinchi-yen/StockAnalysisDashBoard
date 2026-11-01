from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class CacheStore:
    base_dir: Path

    def __post_init__(self) -> None:
        resolved = Path(self.base_dir).expanduser().resolve()
        object.__setattr__(self, "base_dir", resolved)
        resolved.mkdir(parents=True, exist_ok=True)

    def _key_to_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.base_dir / f"{digest}.json"

    def get(self, key: str) -> Optional[dict[str, Any]]:
        path = self._key_to_path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        path = self._key_to_path(key)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def purge_expired(self, ttl_seconds: float) -> int:
        """Delete entries not written within ttl_seconds; returns how many went."""
        cutoff = time.time() - ttl_seconds
        removed = 0
        for path in self.base_dir.glob("*.json"):
            try:
                if path.stat().st_mtime <= cutoff:
                    path.unlink()
                    removed += 1
            except OSError:  # concurrently replaced or removed
                continue
        return removed

    def flush_to(self, target_dir: Path) -> int:
        """Mirror the cache into target_dir, e.g. from tmpfs to persistent storage.

        Only changed files are written, which is the point on eMMC: most of the
        cache is unchanged between flushes.
        """
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        copied = self._copy_changed(self.base_dir, target)
        current = {path.name for path in self.base_dir.glob("*.json")}
        for path in target.glob("*.json"):
            if path.name not in current:
                path.unlink(missing_ok=True)
        return copied

    def restore_from(self, source_dir: Path) -> int:
        """Copy a previously flushed cache back in; a missing source is a no-op."""
        source = Path(source_dir)
        return self._copy_changed(source, self.base_dir) if source.is_dir() else 0

    @staticmethod
    def _copy_changed(source: Path, target: Path) -> int:
        copied = 0
        for path in source.glob("*.json"):
            destination = target / path.name
            if destination.exists() and destination.stat().st_mtime >= path.stat().st_mtime:
                continue
            shutil.copy2(path, destination)
            copied += 1
        return copied


def build_cache_key(prefix: str, **parts: Any) -> str:
    normalized = {k: str(v) for k, v in sorted(parts.items(), key=lambda kv: kv[0])}
    return prefix + "|" + "|".join(f"{k}={v}" for k, v in normalized.items())

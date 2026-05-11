from __future__ import annotations

import hashlib
import json
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


def build_cache_key(prefix: str, **parts: Any) -> str:
    normalized = {k: str(v) for k, v in sorted(parts.items(), key=lambda kv: kv[0])}
    return prefix + "|" + "|".join(f"{k}={v}" for k, v in normalized.items())

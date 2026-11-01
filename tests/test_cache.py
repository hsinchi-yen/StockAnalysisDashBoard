from __future__ import annotations

import os
import time

from cache import CacheStore


def age(store: CacheStore, key: str, days: float) -> None:
    path = store._key_to_path(key)
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def names(directory) -> set[str]:
    return {p.name for p in directory.glob("*.json")}


def test_purge_removes_only_expired_entries(tmp_path) -> None:
    store = CacheStore(base_dir=tmp_path)
    store.set("old", {"v": 1})
    store.set("new", {"v": 2})
    age(store, "old", 8)
    assert store.purge_expired(7 * 86400) == 1
    assert store.get("old") is None
    assert store.get("new") == {"v": 2}


def test_zero_ttl_clears_everything(tmp_path) -> None:
    store = CacheStore(base_dir=tmp_path)
    store.set("a", {"v": 1})
    store.set("b", {"v": 2})
    age(store, "a", 0.001)
    age(store, "b", 0.001)
    assert store.purge_expired(0) == 2
    assert names(tmp_path) == set()


def test_flush_mirrors_the_cache(tmp_path) -> None:
    store = CacheStore(base_dir=tmp_path / "ram")
    store.set("a", {"v": 1})
    store.set("b", {"v": 2})
    persist = tmp_path / "disk"
    assert store.flush_to(persist) == 2
    assert names(persist) == names(store.base_dir)


def test_flush_only_writes_changed_files(tmp_path) -> None:
    """Rewriting unchanged files is exactly the eMMC wear this exists to avoid."""
    store = CacheStore(base_dir=tmp_path / "ram")
    store.set("a", {"v": 1})
    persist = tmp_path / "disk"
    store.flush_to(persist)
    assert store.flush_to(persist) == 0


def test_flush_removes_entries_gone_from_the_cache(tmp_path) -> None:
    store = CacheStore(base_dir=tmp_path / "ram")
    store.set("a", {"v": 1})
    persist = tmp_path / "disk"
    store.flush_to(persist)
    age(store, "a", 8)
    store.purge_expired(7 * 86400)
    store.flush_to(persist)
    assert names(persist) == set()


def test_restore_round_trip(tmp_path) -> None:
    persist = tmp_path / "disk"
    original = CacheStore(base_dir=tmp_path / "ram1")
    original.set("key", {"v": 1})
    original.flush_to(persist)

    rebooted = CacheStore(base_dir=tmp_path / "ram2")
    assert rebooted.restore_from(persist) == 1
    assert rebooted.get("key") == {"v": 1}


def test_restore_from_missing_directory_is_a_no_op(tmp_path) -> None:
    store = CacheStore(base_dir=tmp_path / "ram")
    assert store.restore_from(tmp_path / "never-existed") == 0

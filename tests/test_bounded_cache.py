"""Bounded parse caches: eviction, recency, and the parser wiring."""

from __future__ import annotations

import threading

import pytest
from groket import parser as parser_mod
from groket.bounded_cache import MIN_MAXSIZE, BoundedCache, resolve_maxsize
from groket.constants import (
    SYSTEM_PROMPT_CACHE_MAXSIZE,
    TIMELINE_CACHE_MAX_ENV,
    TIMELINE_CACHE_MAXSIZE,
)
from groket.session import control_views as cv_mod


class TestResolveMaxsize:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("GROKET_TEST_CAP", raising=False)
        assert resolve_maxsize(64, "GROKET_TEST_CAP") == 64

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("GROKET_TEST_CAP", "7")
        assert resolve_maxsize(64, "GROKET_TEST_CAP") == 7

    @pytest.mark.parametrize("raw", ["", "   ", "nonsense", "0", "-5"])
    def test_unusable_env_falls_back(self, monkeypatch, raw):
        monkeypatch.setenv("GROKET_TEST_CAP", raw)
        assert resolve_maxsize(64, "GROKET_TEST_CAP") == 64

    def test_floor_applies_to_default_and_override(self, monkeypatch):
        monkeypatch.setenv("GROKET_TEST_CAP", "1")
        assert resolve_maxsize(64, "GROKET_TEST_CAP") == MIN_MAXSIZE
        assert resolve_maxsize(1) == MIN_MAXSIZE


class TestBoundedCache:
    def test_evicts_coldest_past_maxsize(self):
        c: BoundedCache[int] = BoundedCache(3)
        for i, k in enumerate("abcd"):
            c[k] = i
        assert set(c) == {"b", "c", "d"}
        assert len(c) == 3
        assert c.evictions == 1

    def test_read_refreshes_recency(self):
        c: BoundedCache[int] = BoundedCache(3)
        c["a"], c["b"], c["c"] = 1, 2, 3
        assert c.get("a") == 1  # "a" is now hottest
        c["d"] = 4
        assert "a" in c
        assert "b" not in c

    def test_rewrite_refreshes_recency(self):
        c: BoundedCache[int] = BoundedCache(3)
        c["a"], c["b"], c["c"] = 1, 2, 3
        c["a"] = 99
        c["d"] = 4
        assert c["a"] == 99
        assert "b" not in c

    def test_iteration_does_not_reorder(self):
        c: BoundedCache[int] = BoundedCache(3)
        c["a"], c["b"], c["c"] = 1, 2, 3
        list(c)  # walking the cache must not count as use
        c["d"] = 4
        assert "a" not in c

    def test_miss_returns_default(self):
        c: BoundedCache[int] = BoundedCache(3)
        assert c.get("nope") is None
        assert c.get("nope", 5) == 5
        with pytest.raises(KeyError):
            _ = c["nope"]

    def test_clear_and_delete(self):
        c: BoundedCache[int] = BoundedCache(3)
        c["a"], c["b"] = 1, 2
        del c["a"]
        assert "a" not in c
        c.clear()
        assert len(c) == 0

    def test_maxsize_floor(self):
        assert BoundedCache(0).maxsize == MIN_MAXSIZE

    def test_concurrent_writes_stay_within_cap(self):
        c: BoundedCache[int] = BoundedCache(16)
        errors: list[BaseException] = []

        def worker(base: int) -> None:
            try:
                for i in range(200):
                    c[f"k{base}-{i}"] = i
                    c.get(f"k{base}-{i}")
            except BaseException as exc:  # noqa: BLE001 - surface to assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(c) == 16


class TestParserCachesAreBounded:
    """The caches that grew unbounded in a long-lived owner stay capped."""

    def test_module_caches_are_bounded(self):
        for cache in (
            parser_mod._timeline_cache,
            parser_mod._runtime_markers_cache,
            parser_mod._list_runtime_cache,
            parser_mod._system_prompt_cache,
            cv_mod._overview_cache,
            cv_mod._turn_view_cache,
        ):
            assert isinstance(cache, BoundedCache)

    def test_timeline_cache_honours_env_cap(self, monkeypatch):
        monkeypatch.setenv(TIMELINE_CACHE_MAX_ENV, "5")
        assert resolve_maxsize(TIMELINE_CACHE_MAXSIZE, TIMELINE_CACHE_MAX_ENV) == 5

    def test_parsing_many_sessions_keeps_timeline_cache_capped(self, tmp_path):
        """Parsing more sessions than the cap must not retain them all."""
        cap = parser_mod._timeline_cache.maxsize
        parser_mod._timeline_cache.clear()
        parser_mod._system_prompt_cache.clear()

        for n in range(cap + 8):
            sd = tmp_path / f"session-{n:03d}"
            sd.mkdir()
            (sd / "updates.jsonl").write_text(
                '{"type":"assistant","content":"hi"}\n', encoding="utf-8"
            )
            parser_mod.parse_timeline(sd)

        assert len(parser_mod._timeline_cache) == cap
        assert parser_mod._timeline_cache.evictions >= 8
        assert len(parser_mod._system_prompt_cache) <= SYSTEM_PROMPT_CACHE_MAXSIZE

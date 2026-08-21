"""Process-wide per-session inflight locks."""

from __future__ import annotations

from pathlib import Path

from groket.session_inflight import (
    KIND_REFRESH,
    clear,
    end,
    inflight_count,
    is_inflight,
    request_rerun,
    session_dir_key,
    try_begin,
)


def setup_function() -> None:
    clear()


def teardown_function() -> None:
    clear()


def test_try_begin_rejects_duplicate_per_kind(tmp_path: Path) -> None:
    sd = tmp_path / "019f-sess"
    sd.mkdir()
    assert try_begin(KIND_REFRESH, sd) is True
    assert try_begin(KIND_REFRESH, sd) is False
    assert is_inflight(KIND_REFRESH, sd) is True
    assert inflight_count(KIND_REFRESH) == 1
    assert try_begin("other", sd) is True
    assert inflight_count("other") == 1
    assert end(KIND_REFRESH, sd) is False
    assert try_begin(KIND_REFRESH, sd) is True
    end(KIND_REFRESH, sd)
    end("other", sd)


def test_key_normalizes_resolve(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    assert session_dir_key(sd) == session_dir_key(sd / ".")
    assert try_begin(KIND_REFRESH, sd) is True
    assert try_begin(KIND_REFRESH, sd / ".") is False
    end(KIND_REFRESH, sd)


def test_request_rerun_coalesces_on_end(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    assert try_begin(KIND_REFRESH, sd) is True
    request_rerun(KIND_REFRESH, sd)
    request_rerun(KIND_REFRESH, sd)
    assert end(KIND_REFRESH, sd) is True
    assert end(KIND_REFRESH, sd) is False
    assert is_inflight(KIND_REFRESH, sd) is False


def test_request_rerun_noop_when_idle(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    request_rerun(KIND_REFRESH, sd)
    assert end(KIND_REFRESH, sd) is False
    assert try_begin(KIND_REFRESH, sd) is True
    end(KIND_REFRESH, sd)


def test_clear_kind_leaves_other(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    try_begin(KIND_REFRESH, a)
    try_begin("other", b)
    clear(KIND_REFRESH)
    assert is_inflight(KIND_REFRESH, a) is False
    assert is_inflight("other", b) is True
    clear()
    assert inflight_count("other") == 0

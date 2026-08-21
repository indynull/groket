"""Tests for the live-refresh pool."""

from __future__ import annotations

import time

from groket.job_pools import (
    ActivityLog,
    JobPool,
    configure_job_pools,
    get_activity_log,
    get_live_refresh_pool,
)


def test_activity_log_and_spinner() -> None:
    log = ActivityLog(maxlen=3)
    log.log("analysis", "a")
    log.log("refresh", "b")
    assert len(log.snapshot()) == 2
    assert log.spinner_frame()
    before = log.seq
    log.clear()
    assert log.snapshot() == []
    assert log.seq > before


def test_pool_serial_submit() -> None:
    log = ActivityLog()
    pool = JobPool("analysis", 1, log)
    out: list[int] = []

    def work(n: int) -> None:
        out.append(n)
        time.sleep(0.02)

    f1 = pool.submit("one", lambda: work(1))
    f2 = pool.submit("two", lambda: work(2))
    f1.result(timeout=2)
    f2.result(timeout=2)
    assert out == [1, 2]
    snaps = log.snapshot()
    assert any("start: one" in e.message for e in snaps)
    pool.shutdown(wait=True)


def test_configure_pools() -> None:
    configure_job_pools(live_refresh_workers=1)
    assert get_live_refresh_pool().max_workers == 1
    assert get_activity_log().seq >= 0

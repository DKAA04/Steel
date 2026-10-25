"""A time limit for every test, so a runaway test fails instead of hanging.

Each phase of each test (setup, call, teardown) gets STEEL_TEST_TIMEOUT
seconds (default 60; a number from 1 to 3600, anything else stops the run
with a usage error). It is enforced with SIGALRM in the main thread: when the
time is up, the test fails with a PhaseTimeout that says so. A Jarvis test
once looped forever, grew by about 60 MiB a second and froze the computer
(2026-10-25); this guard is the backstop, bounded fakes are the first line.

Limits: the alarm interrupts Python code and blocking system calls, not a long
computation inside one C call; a test that installs its own SIGALRM handler
(tests/test_voice_core.py, tests/test_speak_core.py) replaces this one until
its own cleanup restores it.
"""

from __future__ import annotations

import math
import os
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

TIMEOUT_ENV = "STEEL_TEST_TIMEOUT"
DEFAULT_TIMEOUT = 60.0
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 3600.0


class PhaseTimeout(BaseException):
    """A test phase ran longer than its time limit.

    A BaseException, so that code under test with ``except Exception`` (for
    example the wake listener's engine-error handler) cannot swallow it."""


def parse_timeout(raw: str | None) -> float:
    """The limit from the environment value; ValueError if it is not usable."""
    if raw is None or not raw.strip():
        return DEFAULT_TIMEOUT
    try:
        value = float(raw.strip())
    except ValueError:
        raise ValueError(f"{TIMEOUT_ENV}={raw!r} is not a number") from None
    if not math.isfinite(value) or not MIN_TIMEOUT <= value <= MAX_TIMEOUT:
        raise ValueError(
            f"{TIMEOUT_ENV}={raw!r} must be from {MIN_TIMEOUT:g} to {MAX_TIMEOUT:g} s"
        )
    return value


@contextmanager
def deadline(seconds: float, what: str) -> Iterator[None]:
    """Raise PhaseTimeout in this (main) thread after *seconds*.

    An alarm that was already set is put back afterwards, minus the time
    spent here, so limits can be nested.
    """
    if threading.current_thread() is not threading.main_thread():
        yield  # signals reach only the main thread; nothing to arm here
        return

    def expired(_signum: int, _frame: object) -> None:
        raise PhaseTimeout(
            f"{what} ran longer than {seconds:g} s and was stopped by the "
            f"per-test time limit ({TIMEOUT_ENV}, tests/conftest.py)"
        )

    started = time.monotonic()
    old_handler = signal.signal(signal.SIGALRM, expired)
    old_delay, _interval = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_delay > 0:
            left = old_delay - (time.monotonic() - started)
            signal.setitimer(signal.ITIMER_REAL, max(left, 0.001))


def _limit(config: pytest.Config) -> float:
    return float(config.stash.get(_LIMIT_KEY, DEFAULT_TIMEOUT))


_LIMIT_KEY = pytest.StashKey[float]()


def pytest_configure(config: pytest.Config) -> None:
    try:
        config.stash[_LIMIT_KEY] = parse_timeout(os.environ.get(TIMEOUT_ENV))
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Iterator[None]:
    with deadline(_limit(item.config), f"setup of {item.nodeid}"):
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    with deadline(_limit(item.config), item.nodeid):
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Iterator[None]:
    with deadline(_limit(item.config), f"teardown of {item.nodeid}"):
        yield

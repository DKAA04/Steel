"""The per-test time limit in tests/conftest.py stops a runaway test.

Every slow loop here is itself bounded (a few seconds of real time), so a
broken guard makes these tests fail, never hang.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import conftest
from conftest import PhaseTimeout, deadline, parse_timeout

HERE = Path(__file__).resolve().parent


def spin(seconds: float) -> None:
    """Busy for at most *seconds* of real time, like a loop that never ends."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pass


class DeadlineTest(unittest.TestCase):
    def test_a_spinning_loop_is_stopped(self) -> None:
        started = time.monotonic()
        with self.assertRaises(PhaseTimeout) as caught, deadline(0.2, "the slow fake"):
            spin(5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn("the slow fake", str(caught.exception))
        self.assertIn("STEEL_TEST_TIMEOUT", str(caught.exception))

    def test_a_blocking_sleep_is_stopped(self) -> None:
        started = time.monotonic()
        with self.assertRaises(PhaseTimeout), deadline(0.2, "sleep"):
            time.sleep(5)
        self.assertLess(time.monotonic() - started, 2)

    def test_except_exception_cannot_swallow_it(self) -> None:
        swallowed = False
        with self.assertRaises(PhaseTimeout), deadline(0.2, "swallow"):
            try:
                spin(5)
            except Exception:  # noqa: BLE001 - what code under test may do
                swallowed = True
        self.assertFalse(swallowed)

    def test_a_fast_block_is_not_disturbed_and_the_handler_is_put_back(
        self,
    ) -> None:
        before = signal.getsignal(signal.SIGALRM)
        with deadline(5, "fast"):
            pass
        self.assertIs(signal.getsignal(signal.SIGALRM), before)

    def test_the_outer_limit_is_put_back_after_an_inner_one(self) -> None:
        # Runs inside the per-test limit of conftest, which must come back.
        before = signal.getitimer(signal.ITIMER_REAL)[0]
        self.assertGreater(before, 0)  # the test itself is guarded
        with deadline(5, "inner"):
            pass
        after = signal.getitimer(signal.ITIMER_REAL)[0]
        self.assertGreater(after, 0)
        self.assertLessEqual(after, before)


class ParseTimeoutTest(unittest.TestCase):
    def test_default_and_valid_values(self) -> None:
        self.assertEqual(parse_timeout(None), conftest.DEFAULT_TIMEOUT)
        self.assertEqual(parse_timeout(" "), conftest.DEFAULT_TIMEOUT)
        self.assertEqual(parse_timeout("1"), 1.0)
        self.assertEqual(parse_timeout(" 90.5 "), 90.5)
        self.assertEqual(parse_timeout("3600"), 3600.0)

    def test_unusable_values_are_refused(self) -> None:
        for raw in ("abc", "0", "0.5", "-5", "3601", "nan", "inf", "1e9"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_timeout(raw)


class WholeRunTest(unittest.TestCase):
    """A real pytest run with this conftest stops a slow test and moves on."""

    def run_pytest(self, test_code: str, timeout_value: str) -> tuple[int, str, float]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        shutil.copyfile(HERE / "conftest.py", root / "conftest.py")
        (root / "test_slow.py").write_text(test_code, encoding="utf-8")
        env = {**os.environ, "STEEL_TEST_TIMEOUT": timeout_value}
        started = time.monotonic()
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                "--rootdir",
                str(root),
                str(root),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
        return done.returncode, done.stdout + done.stderr, time.monotonic() - started

    def test_a_deliberately_slow_test_fails_with_a_clear_message(self) -> None:
        code = (
            "import time\n"
            "def test_slow():\n"
            "    end = time.monotonic() + 30\n"
            "    while time.monotonic() < end:\n"
            "        pass\n"
            "def test_fast():\n"
            "    assert True\n"
        )
        status, out, elapsed = self.run_pytest(code, "1")
        self.assertEqual(status, 1, out)
        self.assertIn("PhaseTimeout", out)
        self.assertIn("STEEL_TEST_TIMEOUT", out)
        self.assertIn("1 failed, 1 passed", out)
        self.assertLess(elapsed, 25)

    def test_an_invalid_setting_stops_the_run_before_any_test(self) -> None:
        status, out, _ = self.run_pytest("def test_x():\n    pass\n", "soon")
        self.assertEqual(status, 4, out)  # pytest's usage error
        self.assertIn("STEEL_TEST_TIMEOUT='soon' is not a number", out)


if __name__ == "__main__":
    unittest.main()

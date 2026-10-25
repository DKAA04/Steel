"""steel doctor's memory-pressure warning, read from fake /proc/meminfo text.

Nothing here reads the real /proc/meminfo: the path is always a temporary file.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_steel as base

from steel import meminfo
from steel.cli import main
from steel.meminfo import memory_lines, read_meminfo

GIB_KB = 1024 * 1024


def fake(available_kb: int, swap_total_kb: int, swap_free_kb: int) -> str:
    return (
        "MemTotal:       15157336 kB\n"
        "MemFree:          612000 kB\n"
        f"MemAvailable:   {available_kb} kB\n"
        "Buffers:           12000 kB\n"
        f"SwapTotal:      {swap_total_kb} kB\n"
        f"SwapFree:       {swap_free_kb} kB\n"
    )


class MemoryLinesTest(unittest.TestCase):
    def lines(self, text: str) -> list[str]:
        return memory_lines(read_meminfo(text))

    def test_plenty_of_memory_gives_one_line_and_no_warning(self) -> None:
        lines = self.lines(fake(8 * GIB_KB, 8 * GIB_KB, 7 * GIB_KB))
        self.assertEqual(len(lines), 1)
        self.assertIn("8.0 GiB available", lines[0])
        self.assertIn("swap 12% in use", lines[0])
        self.assertNotIn("WARNING", "\n".join(lines))

    def test_swap_more_than_half_full_warns_in_plain_words(self) -> None:
        lines = self.lines(fake(5 * GIB_KB, 9 * GIB_KB, 3 * GIB_KB))  # 67 % used
        text = "\n".join(lines)
        self.assertIn("memory WARNING", text)
        self.assertIn("swap is more than half full", text)
        self.assertIn("vision and voice will be slow", text)

    def test_exactly_half_full_swap_does_not_warn(self) -> None:
        lines = self.lines(fake(5 * GIB_KB, 8 * GIB_KB, 4 * GIB_KB))
        self.assertNotIn("WARNING", "\n".join(lines))

    def test_less_than_2_gib_available_warns(self) -> None:
        lines = self.lines(fake(GIB_KB, 8 * GIB_KB, 8 * GIB_KB))
        text = "\n".join(lines)
        self.assertIn("less than 2 GiB of memory is available", text)
        self.assertIn("vision and voice will be slow", text)
        self.assertNotIn("half full", text)

    def test_both_problems_are_both_named(self) -> None:
        text = "\n".join(self.lines(fake(GIB_KB, 8 * GIB_KB, GIB_KB)))
        self.assertIn("less than 2 GiB", text)
        self.assertIn("more than half full", text)

    def test_no_swap_at_all_is_not_a_warning(self) -> None:
        lines = self.lines(fake(6 * GIB_KB, 0, 0))
        self.assertIn("no swap", lines[0])
        self.assertNotIn("WARNING", "\n".join(lines))

    def test_unreadable_or_strange_content_says_so_and_does_not_warn(self) -> None:
        for text in ("", "garbage\n", "MemAvailable: lots kB\n", "SwapTotal: 5 kB\n"):
            lines = self.lines(text)
            self.assertEqual(lines, ["memory: cannot be read from /proc/meminfo"], text)


class DoctorMemoryTest(base.HomeTestCase):
    def doctor(self, content: str | None) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "meminfo"
            if content is not None:
                path.write_text(content)
            before = os.stat(path).st_mtime_ns if content is not None else None
            out = io.StringIO()
            with (
                mock.patch.object(meminfo, "MEMINFO", path),
                contextlib.redirect_stdout(out),
            ):
                code = main(["doctor"])
            if before is not None:
                self.assertEqual(os.stat(path).st_mtime_ns, before)  # read only
        return code, out.getvalue()

    def test_doctor_prints_the_warning_but_its_status_is_unchanged(self) -> None:
        calm_code, calm = self.doctor(fake(8 * GIB_KB, 8 * GIB_KB, 8 * GIB_KB))
        busy_code, busy = self.doctor(fake(GIB_KB, 9 * GIB_KB, GIB_KB))
        self.assertIn("memory: 8.0 GiB available", calm)
        self.assertNotIn("memory WARNING", calm)
        self.assertIn("memory WARNING", busy)
        self.assertIn("vision and voice will be slow", busy)
        # A warning, not a failure: the other checks alone decide the status.
        self.assertEqual(busy_code, calm_code)

    def test_a_missing_meminfo_file_is_reported_not_fatal(self) -> None:
        _, text = self.doctor(None)
        self.assertIn("memory: cannot be read from /proc/meminfo", text)


if __name__ == "__main__":
    unittest.main()

"""Audit log rotation: size cap, .1 -> .2 -> .3 shifting, mode 600, fail closed.

A tiny size limit is passed to the constructor so no test writes megabytes.
Everything runs in temporary directories; the real ~/.openjarvis is never used.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest import mock

from test_policy import PolicyCase

from steel.security import PolicyAuditLog, SteelPolicy

# One logged line is about 130 bytes, so this holds two or three of them.
TINY = 300


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


class RotationCase(PolicyCase):
    def setUp(self) -> None:
        super().setUp()
        self.audit = PolicyAuditLog(self.log_path, max_bytes=TINY)
        self.policy = SteelPolicy(audit=self.audit)
        self.policy.grant("room", "memory:read:notes")

    def numbered(self, n: int) -> Path:
        return Path(f"{self.log_path}.{n}")

    def decide(self, times: int) -> None:
        for _ in range(times):
            self.assertTrue(self.policy.check("room", "memory:read:notes", "t"))

    def lines(self, path: Path) -> list[dict[str, str]]:
        return [json.loads(x) for x in path.read_text().splitlines()]


class RotationTest(RotationCase):
    def test_nothing_rotates_below_the_limit(self) -> None:
        self.decide(2)
        self.assertEqual(len(self.lines(self.log_path)), 2)
        self.assertFalse(self.numbered(1).exists())

    def test_a_log_over_the_limit_is_renamed_to_dot_one_and_a_new_one_starts(
        self,
    ) -> None:
        self.decide(2)
        first_two = self.log_path.read_text()
        self.decide(1)
        self.assertEqual(self.numbered(1).read_text(), first_two)
        self.assertEqual(len(self.lines(self.log_path)), 1)

    def test_files_shift_and_the_oldest_is_deleted(self) -> None:
        marker = []
        for round_ in range(6):
            self.decide(3)
            marker.append(self.log_path.read_text())
        # Only the current file and three rotated ones exist, never a .4.
        self.assertTrue(self.numbered(3).exists())
        self.assertFalse(self.numbered(4).exists())
        # .1 is newer than .2, which is newer than .3.
        stamps = [self.lines(self.numbered(n))[0]["ts"] for n in (3, 2, 1)]
        self.assertEqual(stamps, sorted(stamps))
        total = sum(
            len(self.lines(p))
            for p in (self.log_path, *(self.numbered(n) for n in (1, 2, 3)))
        )
        self.assertLess(total, 18)  # 18 decisions made; the oldest were dropped

    def test_no_decision_is_lost_while_rotating_within_the_kept_files(self) -> None:
        self.decide(7)
        total = sum(
            len(self.lines(p))
            for p in (self.log_path, *(self.numbered(n) for n in (1, 2, 3)))
            if p.exists()
        )
        self.assertEqual(total, 7)

    def test_every_file_is_mode_600(self) -> None:
        self.decide(12)
        for path in (self.log_path, *(self.numbered(n) for n in (1, 2, 3))):
            self.assertEqual(mode(path), 0o600, path)

    def test_a_wide_open_old_log_is_private_after_rotation(self) -> None:
        self.log_path.write_text("x" * TINY)
        self.log_path.chmod(0o644)
        self.decide(1)
        self.assertEqual(mode(self.numbered(1)), 0o600)
        self.assertEqual(mode(self.log_path), 0o600)

    def test_new_files_are_private_even_with_a_permissive_umask(self) -> None:
        old = os.umask(0)
        try:
            self.decide(6)
        finally:
            os.umask(old)
        for path in (self.log_path, self.numbered(1)):
            self.assertEqual(mode(path), 0o600, path)

    def test_the_default_limit_is_five_megabytes(self) -> None:
        self.assertEqual(PolicyAuditLog(self.log_path).max_bytes, 5 * 1024 * 1024)

    def test_the_limit_must_be_positive(self) -> None:
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                PolicyAuditLog(self.log_path, max_bytes=bad)


class RotationFailsClosedTest(RotationCase):
    def test_if_rotation_fails_the_decision_is_a_deny(self) -> None:
        self.decide(2)
        with mock.patch("os.rename", side_effect=OSError("disk says no")):
            self.assertIs(self.policy.check("room", "memory:read:notes", "t"), False)
        # Nothing was appended to the full log either.
        self.assertEqual(len(self.lines(self.log_path)), 2)
        # Once rotation works again, the next decision goes through.
        self.assertTrue(self.policy.check("room", "memory:read:notes", "t"))

    def test_a_failure_halfway_through_the_shift_is_a_deny(self) -> None:
        # A full log with two rotated files already: the shift needs 3 renames.
        for n in (1, 2):
            self.numbered(n).write_text(f"old {n}\n")
        self.log_path.write_text("x" * TINY)
        real_rename = os.rename
        calls: list[int] = []

        def flaky(src: object, dst: object) -> None:
            calls.append(1)
            if len(calls) == 2:
                raise OSError("second rename fails")
            real_rename(src, dst)  # type: ignore[arg-type]

        with mock.patch("os.rename", side_effect=flaky):
            self.assertIs(self.policy.check("room", "memory:read:notes", "t"), False)
        self.assertEqual(len(calls), 2)

    def test_a_symlinked_active_log_is_neither_rotated_nor_followed(self) -> None:
        target = self.root / "elsewhere.txt"
        target.write_text("x" * (TINY + 10))
        self.log_path.symlink_to(target)
        self.assertIs(self.policy.check("room", "memory:read:notes", "t"), False)
        self.assertEqual(target.read_text(), "x" * (TINY + 10))
        self.assertFalse(self.numbered(1).exists())

    def test_a_symlink_planted_at_a_rotated_name_is_replaced_not_written_through(
        self,
    ) -> None:
        target = self.root / "victim.txt"
        target.write_text("untouched")
        self.decide(2)
        self.numbered(1).symlink_to(target)
        self.decide(1)  # rotates: the symlink at .1 is replaced by the real file
        self.assertEqual(target.read_text(), "untouched")
        self.assertFalse(self.numbered(1).is_symlink())
        self.assertEqual(len(self.lines(self.numbered(1))), 2)

    def test_a_directory_at_the_active_path_is_a_deny(self) -> None:
        self.log_path.mkdir()
        self.assertIs(self.policy.check("room", "memory:read:notes", "t"), False)


class RotationFilesTest(RotationCase):
    def test_rotated_files_lists_what_exists(self) -> None:
        self.assertEqual(self.audit.rotated_files(), [])
        self.decide(12)
        self.assertEqual(
            self.audit.rotated_files(),
            [self.numbered(1), self.numbered(2), self.numbered(3)],
        )

    def test_remove_all_deletes_the_log_and_every_rotated_file(self) -> None:
        self.decide(12)
        removed = self.audit.remove_all()
        self.assertEqual(
            sorted(removed),
            sorted(
                [
                    self.log_path.name,
                    *(self.numbered(n).name for n in (1, 2, 3)),
                ]
            ),
        )
        for path in (self.log_path, *(self.numbered(n) for n in (1, 2, 3))):
            self.assertFalse(os.path.lexists(path), path)

    def test_remove_all_removes_a_symlink_without_touching_its_target(self) -> None:
        target = self.root / "keep.txt"
        target.write_text("keep me")
        self.numbered(2).symlink_to(target)
        self.audit.remove_all()
        self.assertFalse(os.path.lexists(self.numbered(2)))
        self.assertEqual(target.read_text(), "keep me")

"""Download the pinned wake-word model files (docs/JARVIS.md section 1.1).

    python scripts/wake_setup.py            # fetch what is missing or wrong
    python scripts/wake_setup.py --dry-run  # say what would happen, fetch nothing

The only part of the Jarvis loop that uses the network, and only when you run
it. Three files from the openWakeWord GitHub release v0.5.1, each written as
NAME.part, checked against its pinned size and SHA-256, and only then renamed
into ~/.openjarvis/steel_models/wake/ (directory 700, files 600). A file that
does not match is deleted. The models are CC BY-NC-SA 4.0: personal,
non-commercial use (docs/JARVIS.md section 15).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from steel.home import steel_home
from steel.jarvis.models import (
    BASE_URL,
    LICENCE,
    WAKE_FILES,
    WakeFile,
    file_ok,
    wake_dir,
)

TIMEOUT = 60.0
BLOCK = 1 << 20


class SetupError(RuntimeError):
    pass


def _open_url(url: str) -> BinaryIO:
    if not url.startswith(BASE_URL):
        raise SetupError("refusing a URL that is not the pinned release")
    response = urllib.request.urlopen(url, timeout=TIMEOUT)
    # GitHub redirects release assets to its own download host; https only.
    if not response.geturl().startswith("https://"):
        raise SetupError("the download was redirected away from https")
    return response  # type: ignore[no-any-return]


def fetch_file(
    pinned: WakeFile,
    directory: Path,
    *,
    opener: Callable[[str], BinaryIO] = _open_url,
) -> bool:
    """Download one file and keep it only if it matches. True if downloaded."""
    target = directory / pinned.name
    if file_ok(target, pinned):
        return False
    part = directory / f"{pinned.name}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as out, opener(pinned.url) as response:
            while block := response.read(BLOCK):
                size += len(block)
                if size > pinned.size:
                    raise SetupError(f"{pinned.name}: larger than pinned")
                digest.update(block)
                out.write(block)
        if size != pinned.size or digest.hexdigest() != pinned.sha256:
            raise SetupError(f"{pinned.name}: size or SHA-256 does not match the pin")
        os.replace(part, target)
    finally:
        if part.exists():
            part.unlink()
    return True


def main(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    opener: Callable[[str], BinaryIO] = _open_url,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    directory = wake_dir(home or steel_home())
    print(f"wake-word models ({LICENCE}) in {directory}")
    if not args.dry_run:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    try:
        for pinned in WAKE_FILES:
            if file_ok(directory / pinned.name, pinned):
                print(f"  {pinned.name}: present, SHA-256 OK")
            elif args.dry_run:
                print(f"  {pinned.name}: would fetch {pinned.url}")
            else:
                fetch_file(pinned, directory, opener=opener)
                os.chmod(directory / pinned.name, 0o600)
                print(f"  {pinned.name}: fetched, {pinned.size:,} bytes, SHA-256 OK")
    except (SetupError, OSError) as exc:
        print(f"wake_setup: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

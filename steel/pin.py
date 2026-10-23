"""Refuse to run against any OpenJarvis build other than the pinned one.

SteelFactStore (steel/facts.py) uses private members of OpenJarvis'
LocalFactStore. Those can change without notice, so a different OpenJarvis
version must fail loudly instead of silently corrupting memory.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

# The commit STEEL was written and tested against.
OPENJARVIS_COMMIT = "108d257188cbad85f2e50209d9a61669ea4dec33"
# The version string the installed package reports for that commit.
OPENJARVIS_VERSION = "1.0.5.dev119+g108d257"


class PinError(RuntimeError):
    """Raised when the installed OpenJarvis is not the pinned build."""


def check_openjarvis_pin(installed: str | None = None) -> str:
    """Return the installed version, or raise PinError if it does not match."""
    if installed is None:
        try:
            # Read the version from package metadata (no heavy import needed).
            installed = version("openjarvis")
        except PackageNotFoundError as exc:
            raise PinError("OpenJarvis is not installed in this environment.") from exc
    if installed != OPENJARVIS_VERSION:
        raise PinError(
            f"OpenJarvis {installed} is installed, but STEEL is pinned to "
            f"{OPENJARVIS_VERSION} (commit {OPENJARVIS_COMMIT}). "
            "Review steel/facts.py against the new LocalFactStore before "
            "updating the pin."
        )
    return installed

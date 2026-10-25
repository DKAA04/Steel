"""The guard: the window outside the terminal, and the watchdog (docs/JARVIS.md
sections 3b and 3c). Runs as its own process:

    python -I -m steel.jarvis.guard LOOP_PID WATCHDOG_SECONDS

It reads one command per line on stdin from the loop: ``SHOW <state>`` (answer
``SHOWN`` once the window is on screen, or ``FAILED``), ``STATE <state>``,
``HIDE`` and ``BEAT``. It never sees audio or the owner's words.

It does not trust the loop: every 100 ms it looks in /proc for microphone
processes (arecord) whose parent is the loop, and

* kills any that runs while the window is not shown;
* hides the window only when none is left and the loop asked for it;
* while listening (the last state starts with LISTENING), kills them and then
  the loop if no BEAT came for WATCHDOG_SECONDS: a stuck loop must not keep
  the microphone;
* when its input ends (the loop has gone), kills every one it has seen.

It runs in its own session, so Ctrl-C or Ctrl-Z in the terminal do not reach it.
"""

from __future__ import annotations

import os
import select
import sys
import time
from collections.abc import Callable
from typing import Protocol

from steel.jarvis.procs import ProcInfo, kill_exact, mic_children, proc_info

TICK_SECONDS = 0.1
SHOW_TIMEOUT = 5.0  # the loop's wait for SHOWN


class Window(Protocol):
    def show(self, text: str) -> bool:
        """Put the window on screen; True only once it is really mapped."""

    def set_text(self, text: str) -> None: ...

    def hide(self) -> None: ...


class Guard:
    def __init__(
        self,
        loop_pid: int,
        window: Window,
        *,
        watchdog_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        children: Callable[[int], list[ProcInfo]] = mic_children,
        kill: Callable[[ProcInfo], bool] = kill_exact,
    ) -> None:
        self.loop_pid = loop_pid
        self.window = window
        self.watchdog_seconds = watchdog_seconds
        self.clock = clock
        self.children = children
        self.kill = kill
        self.shown = False
        self.hide_requested = False
        self.state = ""
        self.last_beat = clock()
        self.seen: dict[int, ProcInfo] = {}
        self.finished = False

    def command(self, line: str) -> str | None:
        """One line from the loop; the answer to send back, if any."""
        word, _, rest = line.strip().partition(" ")
        if word == "SHOW":
            self.state = rest
            self.hide_requested = False
            self.last_beat = self.clock()
            ok = self.window.show(rest)
            self.shown = self.shown or ok
            return "SHOWN" if ok else "FAILED"
        if word == "STATE":
            self.state = rest
            self.last_beat = self.clock()
            if self.shown:
                self.window.set_text(rest)
        elif word == "HIDE":
            self.hide_requested = True
        elif word == "BEAT":
            self.last_beat = self.clock()
        return None

    def tick(self) -> list[str]:
        """Check the microphone processes; what happened, as words for a log."""
        events: list[str] = []
        mics = self.children(self.loop_pid)
        for mic in mics:
            self.seen[mic.pid] = mic
        if mics and not self.shown:
            for mic in mics:
                if self.kill(mic):
                    events.append("killed a microphone that ran without the window")
        elif (
            mics
            and self.state.startswith("LISTENING")
            and self.clock() - self.last_beat > self.watchdog_seconds
        ):
            for mic in mics:
                if self.kill(mic):
                    events.append("watchdog: killed the microphone")
            loop = proc_info(self.loop_pid)
            if loop is not None and self.kill(loop):
                events.append("watchdog: killed the stuck loop")
            self.end()
        elif not mics and self.shown and self.hide_requested:
            self.window.hide()
            self.shown = False
            self.hide_requested = False
            events.append("hidden")
        return events

    def end(self) -> None:
        """The loop is gone (or stuck): no microphone it started may remain."""
        for mic in self.seen.values():
            self.kill(mic)  # only if that very process still runs
        self.window.hide()
        self.shown = False
        self.finished = True


class TkWindow:
    """A small, borderless, always-on-top red badge in the top-right corner."""

    def __init__(self) -> None:
        import tkinter as tk

        self._tk = tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)  # no frame: no minimise or close button
        self.root.attributes("-topmost", True)
        self.label = tk.Label(
            self.root,
            text="",
            bg="#b00020",
            fg="white",
            font=("sans", 12, "bold"),
            padx=12,
            pady=5,
        )
        self.label.pack()

    def _place(self) -> None:
        self.root.update_idletasks()
        width = self.root.winfo_screenwidth()
        x = width - self.root.winfo_reqwidth() - 20
        self.root.geometry(f"+{max(x, 0)}+20")

    def show(self, text: str) -> bool:
        self.label.configure(text=f"● STEEL MICROPHONE ON: {text}")
        self._place()
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        deadline = time.monotonic() + SHOW_TIMEOUT - 1
        while time.monotonic() < deadline:
            self.root.update()
            if self.root.winfo_ismapped() and self.root.winfo_viewable():
                return True
            time.sleep(0.02)
        return False

    def set_text(self, text: str) -> None:
        self.label.configure(text=f"● STEEL MICROPHONE ON: {text}")
        self._place()

    def hide(self) -> None:
        self.root.withdraw()
        self.root.update()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    loop_pid, watchdog = int(argv[0]), float(argv[1])
    window: Window
    try:
        window = TkWindow()
    except Exception:  # noqa: BLE001 - no display, no Tk: the loop gets FAILED
        window = _NoWindow()
    guard = Guard(loop_pid, window, watchdog_seconds=watchdog)
    fd = sys.stdin.fileno()
    pending = b""
    while not guard.finished:
        ready, _, _ = select.select([fd], [], [], TICK_SECONDS)
        if ready:
            data = os.read(fd, 4096)
            if not data:  # the loop has gone
                guard.end()
                break
            pending += data
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                answer = guard.command(line.decode("ascii", "replace"))
                if answer is not None:
                    sys.stdout.write(answer + "\n")
                    sys.stdout.flush()
        guard.tick()
        if isinstance(window, TkWindow):
            window.root.update()
    return 0


class _NoWindow:
    def show(self, text: str) -> bool:
        return False

    def set_text(self, text: str) -> None:
        pass

    def hide(self) -> None:
        pass


if __name__ == "__main__":
    raise SystemExit(main())

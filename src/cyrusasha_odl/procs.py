"""
Child-process execution with live log capture.

Two small building blocks are used by the installer, the job queue and the
hybrid-server manager:

* :class:`LogBuffer` - a thread-safe, append-only list of log lines with
  *absolute* indices. The web UI asks for "everything after line N", which
  keeps polling cheap even for very long logs. Lines are mirrored to a file.
* :class:`ManagedProcess` - starts a command without a console window, reads
  stdout+stderr line by line on a background thread, cleans each line, lets a
  callback inspect/transform/drop it, and can stop the complete process tree.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from typing import Callable, Dict, List, Optional

from .platform_utils import clean_line, decode_output, kill_tree, popen_kwargs

log = logging.getLogger(__name__)

#: Callback signature used by :class:`ManagedProcess`: receives a cleaned line
#: and returns the text to store, or ``None`` to drop the line.
LineHook = Callable[[str], Optional[str]]


class LogBuffer:
    """Thread-safe, append-only line store with absolute indexing.

    Only the newest ``max_lines`` lines are kept in memory; the complete log is
    mirrored to ``file_path`` (when given). Indices never shift: if old lines
    are dropped, :meth:`read` reports how many were skipped.
    """

    def __init__(self, file_path: Optional[str] = None, max_lines: int = 20000, append: bool = False) -> None:
        """Create a buffer.

        Args:
            file_path: Optional file that receives every appended line.
            max_lines: Maximum number of lines kept in memory.
            append: Append to an existing file instead of truncating it.
        """
        self._lines: List[str] = []
        self._base = 0
        self._max = max_lines
        self._lock = threading.Lock()
        self._fh = None
        self.file_path = file_path
        if file_path:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            self._fh = open(file_path, "a" if append else "w", encoding="utf-8", newline="\n")

    @classmethod
    def from_file(cls, file_path: str, max_lines: int = 20000) -> "LogBuffer":
        """Load an existing log file into a read-only buffer.

        Used for jobs that finished in an earlier session of the GUI.

        Args:
            file_path: The log file to read (missing files give an empty buffer).
            max_lines: Maximum number of lines kept in memory (newest win).

        Returns:
            A buffer that is not attached to any file.
        """
        buffer = cls(None, max_lines=max_lines)
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    buffer._lines.append(line.rstrip("\n"))
        except OSError:
            pass
        if len(buffer._lines) > max_lines:
            drop = len(buffer._lines) - max_lines
            del buffer._lines[:drop]
            buffer._base = drop
        return buffer

    def append(self, line: str) -> None:
        """Append one line (and mirror it to the file).

        Args:
            line: Text without trailing newline.
        """
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > self._max:
                drop = len(self._lines) - self._max
                del self._lines[:drop]
                self._base += drop
            if self._fh:
                try:
                    self._fh.write(line + "\n")
                    self._fh.flush()
                except (OSError, ValueError):
                    pass

    @property
    def total(self) -> int:
        """Number of lines appended so far (including dropped ones)."""
        with self._lock:
            return self._base + len(self._lines)

    def read(self, since: int = 0, limit: int = 3000) -> Dict[str, object]:
        """Return the lines that were appended after line ``since``.

        Args:
            since: Absolute index of the first wanted line.
            limit: Maximum number of lines to return.

        Returns:
            ``{"lines": [...], "next": int, "total": int, "skipped": int}`` where
            ``next`` is the index to pass on the following call.
        """
        with self._lock:
            since = max(0, int(since))
            start = max(since, self._base)
            index = start - self._base
            chunk = self._lines[index:index + max(1, int(limit))]
            return {
                "lines": list(chunk),
                "next": start + len(chunk),
                "total": self._base + len(self._lines),
                "skipped": start - since,
            }

    def tail(self, count: int = 30) -> List[str]:
        """Return the newest ``count`` lines.

        Args:
            count: Number of lines.

        Returns:
            A list (oldest first).
        """
        with self._lock:
            return list(self._lines[-count:])

    def close(self) -> None:
        """Close the mirror file. Further appends stay in memory only."""
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


class ManagedProcess:
    """A child process whose merged output is streamed into a :class:`LogBuffer`."""

    def __init__(
        self,
        cmd: List[str],
        *,
        log_buffer: LogBuffer,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        on_line: Optional[LineHook] = None,
    ) -> None:
        """Prepare (but do not start) a process.

        Args:
            cmd: Command and arguments; no shell is used, so arguments are
                never interpreted (safe against injection).
            log_buffer: Buffer that receives the output lines.
            cwd: Working directory.
            env: Environment for the child.
            on_line: Optional hook that can inspect, rewrite or drop lines.
        """
        self.cmd = cmd
        self.log = log_buffer
        self.cwd = cwd
        self.env = env
        self.on_line = on_line
        self.proc: Optional[subprocess.Popen] = None
        self.returncode: Optional[int] = None
        self.canceled = False
        self._done = threading.Event()
        self._reader: Optional[threading.Thread] = None

    @property
    def pid(self) -> Optional[int]:
        """Process id of the direct child (``None`` before :meth:`start`)."""
        return self.proc.pid if self.proc else None

    @property
    def running(self) -> bool:
        """``True`` while the child is alive or its output is still being read."""
        return self.proc is not None and not self._done.is_set()

    def start(self) -> None:
        """Start the child and the background reader thread.

        Raises:
            OSError: If the executable cannot be started.
        """
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            **popen_kwargs(),
        )
        self._reader = threading.Thread(target=self._pump, name=f"proc-{self.proc.pid}", daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        """Reader thread: split the byte stream into lines and store them.

        Reads raw chunks (instead of ``readline``) so output that only uses
        ``\\r`` for progress updates can still be processed, and very long lines
        cannot exhaust memory.
        """
        assert self.proc is not None and self.proc.stdout is not None
        stream = self.proc.stdout
        pending = b""
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                pending += chunk
                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    raw, pending = pending[:newline], pending[newline + 1:]
                    self._emit(raw)
                if len(pending) > 262144:  # absurdly long line: flush it
                    self._emit(pending)
                    pending = b""
            if pending:
                self._emit(pending)
        except (OSError, ValueError) as exc:
            log.debug("reader stopped: %s", exc)
        finally:
            try:
                self.returncode = self.proc.wait()
            finally:
                self._done.set()

    def _emit(self, raw: bytes) -> None:
        """Decode, clean, filter and store one raw line.

        Args:
            raw: Bytes of a single line without the newline character.
        """
        text = clean_line(decode_output(raw))
        if not text:
            return
        if self.on_line is not None:
            try:
                text = self.on_line(text)
            except Exception:  # a buggy hook must never kill the reader
                log.exception("line hook failed")
            if text is None:
                return
        self.log.append(text)

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        """Wait until the process has exited and its output was consumed.

        Args:
            timeout: Seconds to wait (``None`` = forever).

        Returns:
            The exit code, or ``None`` if the timeout expired first.
        """
        if self._done.wait(timeout):
            return self.returncode
        return None

    def cancel(self) -> None:
        """Stop the process and all of its children (idempotent)."""
        self.canceled = True
        if self.proc is not None and self.proc.poll() is None:
            try:
                kill_tree(self.proc)
            except OSError as exc:
                log.warning("could not stop process %s: %s", self.pid, exc)


def display_command(cmd: List[str]) -> str:
    """Format a command list for display, quoting arguments where needed.

    Args:
        cmd: Command and arguments.

    Returns:
        A copy-pasteable command line for the current platform.
    """
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    import shlex

    return " ".join(shlex.quote(part) for part in cmd)

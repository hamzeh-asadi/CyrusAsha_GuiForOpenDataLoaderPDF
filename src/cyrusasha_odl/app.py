"""
Application object - creates and wires all managers.

``App`` is the single composition root: the HTTP layer (``server.py``) only
talks to an ``App`` instance, and the managers talk to each other through it.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Optional, Any, Dict

from . import APP_ID, APP_NAME, __version__
from .config import Paths, PresetStore, Settings
from .errors import ApiError
from .storage import StorageInfo, check_target
from .env import EnvInspector
from .hybrid import HybridManager
from .jobs import JobManager
from .procs import LogBuffer
from .rules import RuleStore
from .setup_tasks import SetupManager


class _BufferHandler(logging.Handler):
    """Logging handler that mirrors application log records into a :class:`LogBuffer`."""

    def __init__(self, buffer: LogBuffer) -> None:
        """Create the handler.

        Args:
            buffer: Target buffer (shown on the Help page of the UI).
        """
        super().__init__()
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        """Format the record and append it to the buffer."""
        try:
            self.buffer.append(self.format(record))
        except Exception:  # logging must never raise
            pass


def configure_logging(paths: Paths, verbose: bool = False) -> LogBuffer:
    """Send log records to the console, ``data/logs/app.log`` and an in-memory buffer.

    Args:
        paths: Application paths.
        verbose: Log debug messages too.

    Returns:
        The buffer used by the UI's log viewer.
    """
    buffer = LogBuffer(os.path.join(paths.logs_dir, "app.log"), max_lines=5000, append=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    memory = _BufferHandler(buffer)
    memory.setFormatter(formatter)
    root.addHandler(memory)
    if sys.stderr is not None:  # pythonw.exe has no console streams
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        root.addHandler(console)
    return buffer


class App:
    """Holds all managers and implements the cross-cutting operations."""

    def __init__(self, data_dir: str = "", verbose: bool = False, install_dir: Optional[str] = None,
                 models_dir: Optional[str] = None) -> None:
        """Create the data folder, load settings and start the managers.

        Args:
            data_dir: Custom data folder (default: ``<app>/data``).
            verbose: Enable debug logging.
            install_dir: New install folder (``--install-dir``; ``""`` = default,
                ``None`` = keep the setting). Files are not moved.
            models_dir: New models folder (``--models-dir``), like ``install_dir``.
        """
        self.paths = Paths(data_dir or None)
        self.paths.ensure()
        self.app_log = configure_logging(self.paths, verbose)
        self.log = logging.getLogger("cyrusasha_odl")
        self.settings = Settings(self.paths.settings_file)
        self.paths.apply_locations(self.settings.get("install_dir"), self.settings.get("models_dir"))
        for kind, key, value in (("install", "install_dir", install_dir), ("models", "models_dir", models_dir)):
            if value is None:
                continue
            check = check_target(self.paths, kind, value)
            if not check["ok"]:
                raise ApiError("location_" + str(check["error"]), check["path"])
            self.settings.update({key: check["setting"]})
            self.paths.apply_locations(self.settings.get("install_dir"), self.settings.get("models_dir"))
        self.presets = PresetStore(self.paths.presets_file)
        self.rules = RuleStore(self.paths.rules_dir)
        self.env = EnvInspector(self.paths, self.settings)
        self._rev = 0
        self._rev_lock = threading.Lock()
        self.started = time.time()
        self.last_seen = time.time()  # last /api/state poll (used by the idle watchdog)
        self.url = ""
        self.hybrid = HybridManager(self.paths, self.settings, self.env, self.touch)
        self.storage = StorageInfo(self.paths, self.touch)
        self.setup = SetupManager(self.paths, self.settings, self.env, self, self.touch, self.storage)
        self.jobs = JobManager(self.paths, self.settings, self.env, self.hybrid, self.setup, self.rules, self.touch)
        self.shutdown_event = threading.Event()
        self.log.info("%s started (Python %s, data: %s, install: %s, models: %s)", APP_NAME,
                      ".".join(map(str, sys.version_info[:3])), self.paths.data_dir, self.paths.install_dir,
                      self.paths.models_dir)
        self.env.warm_up()

    # ------------------------------------------------------------- revision
    def touch(self) -> None:
        """Increase the state revision (the UI re-renders when it changes)."""
        with self._rev_lock:
            self._rev += 1

    @property
    def revision(self) -> int:
        """Current state revision number."""
        with self._rev_lock:
            return self._rev

    # ---------------------------------------------------- hooks for setup
    def jobs_active(self) -> bool:
        """``True`` while conversions are queued or running (used by the installer)."""
        return self.jobs.active()

    def prepare_venv_change(self) -> None:
        """Stop the hybrid server before the installer changes the venv."""
        if self.hybrid.state in ("starting", "ready", "stopping"):
            self.hybrid.log.append("Stopping the server because the installation is being changed ...")
            self.hybrid.stop()

    # -------------------------------------------------------------- state
    def state(self) -> Dict[str, Any]:
        """Compact snapshot polled by the UI (cheap: no external processes)."""
        jobs = self.jobs.list()
        return {
            "app": APP_ID,
            "version": __version__,
            "rev": self.revision,
            "env": self.env.brief(),
            "setup": self.setup.status(),
            "hybrid": self.hybrid.status(),
            "jobs": jobs[:300],
            "jobs_total": len(jobs),
            "job_counts": self.jobs.counts(),
            "settings": {"language": self.settings.get("language"), "theme": self.settings.get("theme")},
            "time": time.time(),
        }

    def idle_for(self) -> float:
        """Seconds since the UI last polled the state, or 0 while work is running.

        Used by ``--exit-when-idle``: the application may exit when every UI
        window has been closed and no job or installer task is active.
        """
        if self.jobs.active() or self.setup.busy():
            return 0.0
        return time.time() - self.last_seen

    def shutdown(self) -> None:
        """Cancel jobs, stop the hybrid server and signal the HTTP server to exit."""
        self.log.info("shutting down")
        self.jobs.shutdown()
        self.setup.cancel()
        self.hybrid.shutdown()
        self.shutdown_event.set()
        self.app_log.close()  # later records stay in memory only

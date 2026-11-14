"""
Manager for the local hybrid (AI) backend server.

Hybrid mode sends complex pages (tables, scans, formulas, charts) to a local
docling-based server started with ``opendataloader-pdf-hybrid``. This module
starts/stops that server, captures its log, polls ``/health`` to know when the
models are loaded, and lets conversion jobs wait until the server is ready
(starting it automatically when needed).

States reported to the UI:

``stopped``   not running
``starting``  process alive, models still loading (first start downloads them)
``ready``     ``/health`` answers - conversions can use the server
``stopping``  being shut down
``crashed``   the process exited unexpectedly (see the log)
``external``  another server already answers on the configured port
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from .config import Paths, Settings, sanitize_hybrid_config
from .env import EnvInspector
from .errors import ApiError
from .platform_utils import port_in_use
from .procs import LogBuffer, ManagedProcess, display_command

log = logging.getLogger(__name__)

#: Opener that never uses a proxy - health checks always target this computer.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check_health(url: str, timeout: float = 2.0) -> bool:
    """Return ``True`` if ``<url>/health`` answers with HTTP 200.

    Args:
        url: Base URL of a hybrid server, e.g. ``http://127.0.0.1:5002``.
        timeout: Socket timeout in seconds.
    """
    try:
        with _LOCAL_OPENER.open(url.rstrip("/") + "/health", timeout=timeout) as response:
            if response.status != 200:
                return False
            try:
                return json.loads(response.read(2048) or b"{}").get("status", "ok") == "ok"
            except ValueError:
                return True
    except Exception:
        return False


def build_server_args(cfg: Dict[str, Any]) -> List[str]:
    """Translate a sanitized configuration into ``opendataloader-pdf-hybrid`` arguments.

    Args:
        cfg: Output of :func:`cyrusasha_odl.config.sanitize_hybrid_config`.

    Returns:
        The argument list (without the program itself).
    """
    args = ["--host", cfg["host"], "--port", str(cfg["port"]), "--log-level", cfg["log_level"]]
    if cfg["ocr_mode"] == "force":
        args.append("--force-ocr")
    elif cfg["ocr_mode"] == "off":
        args.append("--no-ocr")
    if cfg["ocr_mode"] != "off":
        if cfg["ocr_engine"] and cfg["ocr_engine"] != "easyocr":
            args += ["--ocr-engine", cfg["ocr_engine"]]
        if cfg["ocr_lang"]:
            args += ["--ocr-lang", cfg["ocr_lang"].replace(" ", "")]
        if cfg["psm"] and cfg["ocr_engine"] in ("tesseract", "tesserocr"):
            args += ["--psm", str(cfg["psm"])]
    if cfg["enrich_formula"]:
        args.append("--enrich-formula")
    if cfg["enrich_picture_description"]:
        args.append("--enrich-picture-description")
        if cfg["picture_description_prompt"]:
            args += ["--picture-description-prompt", cfg["picture_description_prompt"]]
        if cfg["picture_area_threshold"]:
            args += ["--picture-area-threshold", str(cfg["picture_area_threshold"])]
    if cfg["heading_hierarchy"]:
        args.append("--heading-hierarchy")
    if cfg["device"] != "auto":
        args += ["--device", cfg["device"]]
    if cfg["max_file_size"]:
        args += ["--max-file-size", str(cfg["max_file_size"])]
    return args


class HybridManager:
    """Starts, stops and monitors the managed hybrid server."""

    def __init__(self, paths: Paths, settings: Settings, env: EnvInspector, on_change: Callable[[], None]) -> None:
        """Create the manager.

        Args:
            paths: Application paths (log location).
            settings: Settings with the stored server configuration.
            env: Environment inspector (venv interpreter, child environment).
            on_change: Callback notifying the UI about state changes.
        """
        self.paths = paths
        self.settings = settings
        self.env = env
        self.on_change = on_change
        self._lock = threading.RLock()
        self.state = "stopped"
        self.proc: Optional[ManagedProcess] = None
        self.config: Optional[Dict[str, Any]] = None
        self.started: Optional[float] = None
        self.ready_at: Optional[float] = None
        self.exit_code: Optional[int] = None
        self.command = ""
        self._stopping = False
        self.log = LogBuffer(os.path.join(paths.logs_dir, "hybrid-server.log"))

    # ---------------------------------------------------------------- helpers
    def url(self, cfg: Optional[Dict[str, Any]] = None) -> str:
        """Return the URL clients should use for the managed server.

        A server bound to ``0.0.0.0`` is reached through ``127.0.0.1``.

        Args:
            cfg: Configuration (defaults to the running or stored one).
        """
        cfg = cfg or self.config or sanitize_hybrid_config(self.settings.get("hybrid_server"))
        host = cfg["host"]
        if host in ("0.0.0.0", "::", "[::]", ""):
            host = "127.0.0.1"
        return f"http://{host}:{cfg['port']}"

    def build_command(self, cfg: Dict[str, Any]) -> List[str]:
        """Full command line used to start the server with ``cfg``."""
        return [self.env.venv_python(), "-X", "utf8", "-m", "opendataloader_pdf.hybrid_server",
                *build_server_args(cfg)]

    def preview(self, raw: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Return the command for a (not yet saved) configuration.

        Args:
            raw: Configuration from the UI.

        Returns:
            ``{"command": "...", "url": "..."}``.
        """
        cfg = sanitize_hybrid_config(raw if raw is not None else self.settings.get("hybrid_server"))
        args = build_server_args(cfg)
        return {"command": display_command(["opendataloader-pdf-hybrid", *args]), "url": self.url(cfg)}

    def status(self) -> Dict[str, Any]:
        """Snapshot for the UI."""
        with self._lock:
            return {
                "state": self.state,
                "url": self.url(),
                "pid": self.proc.pid if self.proc else None,
                "started": self.started,
                "ready_at": self.ready_at,
                "exit_code": self.exit_code,
                "config": self.config or sanitize_hybrid_config(self.settings.get("hybrid_server")),
                "running_config": self.config,
                "auto_start": bool(self.settings.get("hybrid_auto_start")),
                "external_url": self.settings.get("hybrid_external_url"),
                "command": self.command,
                "log_total": self.log.total,
            }

    def read_log(self, since: int = 0) -> Dict[str, Any]:
        """Return server log lines after index ``since``."""
        return self.log.read(since)

    # -------------------------------------------------------------- control
    def start(self, raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Start the server (optionally saving a new configuration first).

        Args:
            raw: Configuration from the UI; ``None`` uses the stored one.

        Returns:
            The new status.

        Raises:
            ApiError: ``hybrid_running``, ``hybrid_not_installed``, ``port_in_use``.
        """
        with self._lock:
            if self.proc is not None and self.proc.running:
                raise ApiError("hybrid_running", status=409)
            if raw is not None:
                self.settings.update({"hybrid_server": sanitize_hybrid_config(raw)})
            cfg = sanitize_hybrid_config(self.settings.get("hybrid_server"))
            if not self.env.package().get("hybrid"):
                self.env.invalidate("package")
                if not self.env.package().get("hybrid"):
                    raise ApiError("hybrid_not_installed")
            url = self.url(cfg)
            if port_in_use(cfg["port"]):
                if check_health(url):
                    self.state = "external"
                    self.config = cfg
                    self.log.append(f"A hybrid server is already answering at {url} - using it.")
                    self.on_change()
                    return self.status()
                raise ApiError("port_in_use", str(cfg["port"]))
            cmd = self.build_command(cfg)
            self.command = display_command(["opendataloader-pdf-hybrid", *build_server_args(cfg)])
            self.log.append("")
            self.log.append(f"$ {display_command(cmd)}")
            self.log.append("Loading models - the first start downloads them and can take several minutes ...")
            self.log.append(f"Models folder: {self.paths.models_dir}")
            proc = ManagedProcess(cmd, log_buffer=self.log, env=self.env.child_env(java=False, network=True),
                                  cwd=self.paths.data_dir)
            try:
                proc.start()
            except OSError as exc:
                raise ApiError("start_failed", str(exc)) from exc
            self.proc = proc
            self.config = cfg
            self.state = "starting"
            self.started = time.time()
            self.ready_at = None
            self.exit_code = None
            self._stopping = False
            threading.Thread(target=self._monitor, args=(proc, url), name="hybrid-monitor", daemon=True).start()
        self.on_change()
        return self.status()

    def stop(self) -> Dict[str, Any]:
        """Stop the managed server (no-op if it is not running).

        Returns:
            The new status.
        """
        with self._lock:
            proc = self.proc
            if self.state == "external":
                self.state = "stopped"
                self.config = None
            if proc is None:
                self.on_change()
                return self.status()
            self._stopping = True
            self.state = "stopping"
        self.on_change()
        proc.cancel()
        proc.wait(15)
        with self._lock:
            if self.proc is proc:
                self.proc = None
                self.state = "stopped"
                self.exit_code = proc.returncode
        self.log.append("Server stopped.")
        self.on_change()
        return self.status()

    def restart(self, raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Stop and start again (e.g. after changing the configuration)."""
        self.stop()
        return self.start(raw)

    def _monitor(self, proc: ManagedProcess, url: str) -> None:
        """Thread body: watch the process and poll ``/health``."""
        last_check = 0.0
        failures = 0
        while True:
            if proc.wait(1.0) is not None:
                with self._lock:
                    if self.proc is proc:
                        self.proc = None
                        self.exit_code = proc.returncode
                        self.state = "stopped" if self._stopping else "crashed"
                        if not self._stopping:
                            self.log.append(f"The hybrid server exited with code {proc.returncode}.")
                self.on_change()
                return
            now = time.time()
            if self.state == "starting":
                if check_health(url):
                    with self._lock:
                        if self.proc is proc:
                            self.state = "ready"
                            self.ready_at = now
                    self.log.append(f"Server is ready at {url} ({now - (self.started or now):.0f}s).")
                    self.on_change()
            elif self.state == "ready" and now - last_check > 15:
                last_check = now
                failures = 0 if check_health(url, timeout=5) else failures + 1
                if failures >= 3:
                    self.log.append("Warning: the server stopped answering /health.")
                    failures = 0

    def ensure_ready(self, timeout: float, cancel_event: threading.Event, report: Callable[[str], None]) -> bool:
        """Make sure the managed server is ready, starting it if allowed.

        Used by conversion jobs before they call the CLI with ``--hybrid``.

        Args:
            timeout: Maximum waiting time in seconds.
            cancel_event: Set when the job is canceled.
            report: Function that writes progress messages into the job log.

        Returns:
            ``True`` if the server is ready.
        """
        if self.state == "ready":
            return True
        if self.state == "external":
            return check_health(self.url())
        if self.state in ("stopped", "crashed"):
            if not self.settings.get("hybrid_auto_start"):
                report("The hybrid server is not running and auto-start is disabled.")
                return False
            report("Starting the hybrid server automatically ...")
            try:
                self.start()
            except ApiError as exc:
                report(f"Could not start the hybrid server: {exc.code} {exc.detail}".rstrip())
                return False
            if self.state == "external":
                return True
        began = time.time()
        last_report = began
        while time.time() - began < timeout:
            if cancel_event.is_set():
                return False
            state = self.state
            if state == "ready":
                report(f"Hybrid server ready after {time.time() - began:.0f}s.")
                return True
            if state in ("stopped", "crashed"):
                report("The hybrid server exited - see the 'Hybrid server' page for its log.")
                return False
            if time.time() - last_report >= 15:
                last_report = time.time()
                report(f"Waiting for the hybrid server (loading models) ... {time.time() - began:.0f}s")
            time.sleep(0.5)
        report("Timed out while waiting for the hybrid server.")
        return False

    def shutdown(self) -> None:
        """Stop the server when the application exits and close the log file."""
        try:
            self.stop()
        except Exception:
            log.exception("could not stop the hybrid server")
        finally:
            self.log.close()

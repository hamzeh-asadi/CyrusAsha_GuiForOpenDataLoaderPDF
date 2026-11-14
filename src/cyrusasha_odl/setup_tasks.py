"""
Installer and maintenance tasks.

A *task* is a sequence of *steps* (for example ``java -> venv -> pip ->
package -> verify``). Tasks run one at a time on a background thread; their
progress (step states, download progress) and log are polled by the web UI.

Everything is installed inside the *install folder* (the data folder unless
the user chose another one, see :mod:`.storage`):

* ``venv`` - a private Python virtual environment containing
  ``opendataloader-pdf`` (and optionally its ``[hybrid]`` extra). The system
  Python installation is never modified.
* ``java`` - an optional *portable* Eclipse Temurin JRE downloaded from the
  Adoptium API (SHA-256 verified). No administrator rights are needed.
* ``cache`` - pip's download cache and temporary files.

System-wide installs (Java or Tesseract via ``winget``) are offered as well.
The ``relocate`` action moves the install or models folder to a folder the
user chooses; ``clear-cache`` and ``clear-models`` free disk space.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shlex
import shutil
import tarfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import APP_NAME, USER_AGENT
from .config import Paths, Settings
from .env import PACKAGE, EnvInspector, version_key
from .errors import ApiError
from .platform_utils import (
    IS_MAC,
    IS_WINDOWS,
    build_base_env,
    find_java_in_dir,
    probe_java,
    probe_python,
    remove_tree,
    tesseract_dir,
)
from .procs import LogBuffer, ManagedProcess, display_command
from .storage import MoveCanceled, MoveError, check_target, dir_size, free_space, move_folder, same_volume

log = logging.getLogger(__name__)

#: Steps executed by each action. ``hybrid`` inside ``full`` is optional.
ACTIONS: Dict[str, List[str]] = {
    "full": ["java", "venv", "pip", "package", "hybrid", "verify"],
    "java-portable": ["java_portable"],
    "java-winget": ["java_winget"],
    "java-remove": ["java_remove"],
    "venv": ["venv", "pip"],
    "install": ["venv", "pip", "package"],
    "update": ["package"],
    "hybrid": ["venv", "hybrid"],
    "rapidocr": ["rapidocr"],
    "tesseract": ["tesseract"],
    "uninstall": ["uninstall"],
    "reset": ["reset"],
    "verify": ["verify"],
    "relocate": ["relocate"],
    "clear-cache": ["clear_cache"],
    "clear-models": ["clear_models"],
}

#: Steps that modify the virtual environment or the portable Java runtime.
#: They are refused while jobs are active, and queued jobs wait while they run.
VENV_STEPS = {"venv", "pip", "package", "hybrid", "rapidocr", "uninstall", "reset",
              "java", "java_portable", "java_remove", "relocate", "clear_models", "location"}

#: English step titles written into the installer log.
STEP_TITLES = {
    "java": "Check / install Java",
    "java_portable": "Download portable Java (Eclipse Temurin JRE)",
    "java_winget": "Install Java with winget",
    "java_remove": "Remove the portable Java runtime",
    "venv": "Create the Python virtual environment",
    "pip": "Upgrade pip",
    "package": "Install opendataloader-pdf",
    "hybrid": "Install the hybrid (AI) extra",
    "rapidocr": "Install RapidOCR",
    "tesseract": "Install Tesseract OCR with winget",
    "uninstall": "Uninstall opendataloader-pdf",
    "reset": "Delete the virtual environment",
    "verify": "Self-test (convert a sample PDF)",
    "relocate": "Change the install or models folder",
    "location": "Use the chosen install and models folders",
    "clear_cache": "Delete the pip download cache",
    "clear_models": "Delete the downloaded models",
}

_PROGRESS_RE = re.compile(r"^Progress (\d+) of (\d+)$")
_DOWNLOADING_RE = re.compile(r"^\s*Downloading (\S+)")
_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.\-+!]*$")


class StepFailed(Exception):
    """Raised by a step to abort the task with a translatable error code."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Create the error.

        Args:
            code: Error code (translated by the UI as ``err.<code>``).
            detail: Technical detail.
        """
        super().__init__(code)
        self.code = code
        self.detail = detail


class StepSkipped(Exception):
    """Raised by a step that has nothing to do (for example: already installed)."""

    def __init__(self, detail: str = "") -> None:
        """Create the signal.

        Args:
            detail: Short explanation shown next to the step.
        """
        super().__init__(detail)
        self.detail = detail


class TaskCanceled(Exception):
    """Raised when the user cancels a running task."""


def make_test_pdf(path: str, text: str = "Hello OpenDataLoader") -> None:
    """Write a tiny but valid one-page PDF used by the self-test.

    The file is assembled by hand (objects, cross-reference table, trailer),
    so no PDF library is required.

    Args:
        path: Target file.
        text: Text printed on the page (ASCII).
    """
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as fh:
        fh.write(out)


def adoptium_platform() -> Tuple[str, str, str]:
    """Map the current platform to Adoptium API names.

    Returns:
        ``(os, architecture, archive_extension)``, e.g. ``("windows", "x64", "zip")``.
    """
    machine = platform.machine().lower()
    arch = {
        "amd64": "x64", "x86_64": "x64", "arm64": "aarch64", "aarch64": "aarch64",
        "x86": "x32", "i386": "x32", "i686": "x32",
    }.get(machine, "x64")
    if IS_WINDOWS:
        return "windows", arch, "zip"
    if IS_MAC:
        return "mac", arch, "tar.gz"
    return "linux", arch, "tar.gz"


def extract_archive(archive: str, destination: str) -> None:
    """Extract a ``.zip`` or ``.tar.gz`` archive safely.

    Members that would be written outside ``destination`` ("zip slip") abort
    the extraction.

    Args:
        archive: Archive file.
        destination: Existing target folder.

    Raises:
        StepFailed: ``bad_archive`` for unsafe or unreadable archives.
    """
    root = os.path.realpath(destination)

    def check(name: str) -> None:
        """Reject member names that escape the destination folder."""
        target = os.path.realpath(os.path.join(destination, name))
        if os.path.commonpath([target, root]) != root:
            raise StepFailed("bad_archive", name)

    try:
        if archive.lower().endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for member in zf.infolist():
                    check(member.filename)
                zf.extractall(destination)
            if not IS_WINDOWS:  # zip archives do not keep the executable bit
                for current, _dirs, files in os.walk(destination):
                    if os.path.basename(current) == "bin":
                        for name in files:
                            os.chmod(os.path.join(current, name), 0o755)
        else:
            with tarfile.open(archive, "r:*") as tf:
                for member in tf.getmembers():
                    check(member.name)
                try:
                    tf.extractall(destination, filter="data")
                except TypeError:  # Python without extraction filters
                    tf.extractall(destination)  # noqa: S202 - members checked above
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        raise StepFailed("bad_archive", str(exc)) from exc


class SetupTask:
    """State of one installer task (shared with the UI through :meth:`to_dict`)."""

    def __init__(self, action: str, params: Dict[str, Any], steps: List[str], log_path: str) -> None:
        """Create the task.

        Args:
            action: Action name (key of :data:`ACTIONS`).
            params: Parameters from the UI (python path, version, ...).
            steps: Step keys to execute.
            log_path: File that receives the task log.
        """
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.action = action
        self.params = params
        self.state = "running"
        self.steps: List[Dict[str, Any]] = [{"key": k, "state": "pending", "detail": ""} for k in steps]
        self.started = time.time()
        self.finished: Optional[float] = None
        self.error: Optional[str] = None
        self.error_detail = ""
        self.download: Optional[Dict[str, Any]] = None
        self.log = LogBuffer(log_path)
        self.cancel_event = threading.Event()
        self.proc: Optional[ManagedProcess] = None
        self.last_download_name = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable snapshot for the UI."""
        return {
            "id": self.id,
            "action": self.action,
            "state": self.state,
            "steps": [dict(s) for s in self.steps],
            "started": self.started,
            "finished": self.finished,
            "error": self.error,
            "error_detail": self.error_detail,
            "download": dict(self.download) if self.download else None,
            "log_total": self.log.total,
            "params": {k: v for k, v in self.params.items() if k in ("hybrid", "version", "java_version", "python")},
        }


class SetupManager:
    """Runs installer tasks one at a time."""

    def __init__(self, paths: Paths, settings: Settings, env: EnvInspector, hooks: Any,
                 on_change: Callable[[], None], storage: Any = None) -> None:
        """Create the manager.

        Args:
            paths: Application paths.
            settings: Persistent settings (pip index, proxy, Java options).
            env: Environment inspector (invalidated after every task).
            hooks: Object with ``jobs_active()`` and ``prepare_venv_change()``;
                the application uses it to stop the hybrid server and to
                refuse changes while conversions are running.
            on_change: Callback notifying the UI that something changed.
            storage: Optional :class:`.storage.StorageInfo` whose measured
                sizes are reset after every task.
        """
        self.paths = paths
        self.settings = settings
        self.env = env
        self.hooks = hooks
        self.on_change = on_change
        self.storage = storage
        self.current: Optional[SetupTask] = None
        self.last_verify: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- public
    def busy(self) -> bool:
        """``True`` while a task is running."""
        task = self.current
        return bool(task and task.state == "running")

    def modifying_venv(self) -> bool:
        """``True`` while a running task contains venv-changing steps."""
        task = self.current
        return bool(task and task.state == "running" and any(s["key"] in VENV_STEPS for s in task.steps))

    def start(self, action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Start an installer action on a background thread.

        Args:
            action: Key of :data:`ACTIONS`.
            params: Optional parameters (``hybrid``, ``python``, ``version``,
                ``java_version``, ``recreate``, ``extra_args``; for ``full`` and
                ``install`` also ``install_dir``/``models_dir`` - folders to use
                for this installation, ``""`` = default).

        Returns:
            The task snapshot.

        Raises:
            ApiError: ``setup_busy``, ``busy_jobs`` or ``bad_action``.
        """
        params = dict(params or {})
        steps = self.steps_for(action, params)
        if action == "venv":
            params["recreate"] = True
        with self._lock:
            if self.busy():
                raise ApiError("setup_busy", status=409)
            if any(s in VENV_STEPS for s in steps) and self.hooks.jobs_active():
                raise ApiError("busy_jobs", status=409)
            log_path = os.path.join(self.paths.logs_dir, f"setup-{time.strftime('%Y%m%d-%H%M%S')}-{action}.log")
            task = SetupTask(action, params, steps, log_path)
            self.current = task
        threading.Thread(target=self._execute, args=(task,), name=f"setup-{action}", daemon=True).start()
        self.on_change()
        return task.to_dict()

    @staticmethod
    def steps_for(action: str, params: Dict[str, Any]) -> List[str]:
        """Return the steps an action runs with the given parameters.

        Args:
            action: Key of :data:`ACTIONS`.
            params: Action parameters (``hybrid``, ``install_dir``, ``models_dir`` ...).

        Returns:
            Step keys in execution order.

        Raises:
            ApiError: ``bad_action``.
        """
        if action not in ACTIONS:
            raise ApiError("bad_action", action)
        steps = list(ACTIONS[action])
        if action == "full" and not params.get("hybrid"):
            steps.remove("hybrid")
        if action in ("full", "install") and (params.get("install_dir") is not None
                                              or params.get("models_dir") is not None):
            steps.insert(0, "location")  # folders chosen in the install dialog
        return steps

    def cancel(self) -> bool:
        """Request cancellation of the running task.

        Returns:
            ``True`` if a task was running.
        """
        task = self.current
        if not task or task.state != "running":
            return False
        task.cancel_event.set()
        if task.proc:
            task.proc.cancel()
        return True

    def status(self) -> Optional[Dict[str, Any]]:
        """Snapshot of the current (or last) task, ``None`` if there was none."""
        task = self.current
        data = task.to_dict() if task else None
        if data is not None:
            data["last_verify"] = self.last_verify
        return data

    def read_log(self, since: int = 0) -> Dict[str, Any]:
        """Return log lines of the current task after index ``since``."""
        task = self.current
        if not task:
            return {"lines": [], "next": 0, "total": 0, "skipped": 0}
        return task.log.read(since)

    # ------------------------------------------------------------- execution
    def _execute(self, task: SetupTask) -> None:
        """Thread body: run all steps of ``task`` in order."""
        task.log.append(f"{APP_NAME} - setup action '{task.action}'")
        try:
            for step in task.steps:
                if task.cancel_event.is_set():
                    raise TaskCanceled()
                step["state"] = "running"
                step["started"] = time.time()
                self.on_change()
                task.log.append("")
                task.log.append(f"==> {STEP_TITLES.get(step['key'], step['key'])}")
                try:
                    detail = getattr(self, "_step_" + step["key"])(task)
                    step["state"] = "done"
                    step["detail"] = detail or ""
                except StepSkipped as skipped:
                    step["state"] = "skipped"
                    step["detail"] = skipped.detail
                    task.log.append(f"Skipped: {skipped.detail}" if skipped.detail else "Skipped")
                step["finished"] = time.time()
                self.on_change()
            task.state = "success"
        except TaskCanceled:
            task.state = "canceled"
            self._mark_remaining(task, "canceled")
        except StepFailed as failure:
            task.state = "failed"
            task.error = failure.code
            task.error_detail = failure.detail
            task.log.append(f"ERROR: {failure.code} {failure.detail}".rstrip())
            self._mark_remaining(task, "failed")
        except ApiError as failure:
            task.state = "failed"
            task.error = failure.code
            task.error_detail = failure.detail
            task.log.append(f"ERROR: {failure.code} {failure.detail}".rstrip())
            self._mark_remaining(task, "failed")
        except Exception as exc:  # unexpected problems are reported, not swallowed
            log.exception("setup task failed")
            task.state = "failed"
            task.error = "internal"
            task.error_detail = str(exc)
            task.log.append(f"ERROR: {exc}")
            self._mark_remaining(task, "failed")
        finally:
            task.finished = time.time()
            task.download = None
            task.log.append("")
            task.log.append(f"==> {task.state.upper()} ({task.finished - task.started:.1f}s)")
            task.log.close()
            self.env.invalidate()
            self.env.warm_up()
            if self.storage is not None:
                self.storage.invalidate()
            self.on_change()

    @staticmethod
    def _mark_remaining(task: SetupTask, state: str) -> None:
        """Mark the running step as ``state`` and pending steps as canceled."""
        for step in task.steps:
            if step["state"] == "running":
                step["state"] = state
            elif step["state"] == "pending":
                step["state"] = "canceled"

    def _run(self, task: SetupTask, cmd: List[str], env: Optional[Dict[str, str]] = None,
             cwd: Optional[str] = None, hook: Optional[Callable[[str], Optional[str]]] = None) -> int:
        """Run a command for ``task`` and stream its output into the task log.

        Args:
            task: The owning task (its cancel flag is honoured).
            cmd: Command and arguments.
            env: Environment for the child.
            cwd: Working directory.
            hook: Optional line hook (see :class:`ManagedProcess`).

        Returns:
            The exit code.

        Raises:
            StepFailed: ``start_failed`` if the program cannot be started.
            TaskCanceled: If the task was canceled meanwhile.
        """
        task.log.append("$ " + display_command(cmd))
        proc = ManagedProcess(cmd, log_buffer=task.log, env=env, cwd=cwd, on_line=hook)
        try:
            proc.start()
        except OSError as exc:
            raise StepFailed("start_failed", f"{cmd[0]}: {exc}") from exc
        task.proc = proc
        try:
            while proc.wait(0.3) is None:
                if task.cancel_event.is_set():
                    proc.cancel()
        finally:
            task.proc = None
        if task.cancel_event.is_set():
            raise TaskCanceled()
        task.log.append(f"(exit code {proc.returncode})")
        return int(proc.returncode or 0)

    def _pip_args(self, raw_progress: bool) -> List[str]:
        """Common pip options derived from the network settings.

        Args:
            raw_progress: Use ``--progress-bar raw`` (parsable progress lines).
        """
        args = ["--disable-pip-version-check", "--no-input"]
        if self.settings.get("pip_index_url"):
            args += ["--index-url", self.settings.get("pip_index_url")]
        if self.settings.get("pip_extra_index_url"):
            args += ["--extra-index-url", self.settings.get("pip_extra_index_url")]
        for host in re.split(r"[\s,;]+", self.settings.get("pip_trusted_host") or ""):
            if host:
                args += ["--trusted-host", host]
        if self.settings.get("proxy"):
            args += ["--proxy", self.settings.get("proxy")]
        args += ["--progress-bar", "raw" if raw_progress else "off"]
        return args

    def _pip_hook(self, task: SetupTask) -> Callable[[str], Optional[str]]:
        """Create a line hook that turns pip's raw progress lines into a progress bar."""
        def hook(line: str) -> Optional[str]:
            """Consume ``Progress X of Y`` lines and remember download names."""
            match = _PROGRESS_RE.match(line)
            if match:
                task.download = {"name": task.last_download_name, "done": int(match.group(1)),
                                 "total": int(match.group(2))}
                return None
            match = _DOWNLOADING_RE.match(line)
            if match and not match.group(1).endswith(".metadata"):
                task.last_download_name = os.path.basename(match.group(1))
            if line.startswith(("Installing collected packages", "Successfully installed")):
                task.download = None
            return line
        return hook

    @staticmethod
    def _java_hook(line: str) -> Optional[str]:
        """Drop the JVM's "Picked up JAVA_TOOL_OPTIONS" notice."""
        return None if line.startswith("Picked up ") else line

    def _require_venv(self) -> str:
        """Return the venv interpreter or fail with ``venv_missing``."""
        if not self.env.venv_exists():
            raise StepFailed("venv_missing")
        return self.env.venv_python()

    def _pip(self, task: SetupTask, *packages: str, upgrade: bool = True, extra: Optional[List[str]] = None) -> int:
        """Run ``pip install`` inside the venv.

        Args:
            task: Owning task.
            packages: Requirement specifiers.
            upgrade: Add ``--upgrade``.
            extra: Additional pip arguments.

        Returns:
            pip's exit code.
        """
        python = self._require_venv()
        cmd = [python, "-m", "pip", "install"]
        if upgrade:
            cmd.append("--upgrade")
        cmd += list(packages)
        cmd += self._pip_args(self.env.pip_supports_raw_progress())
        cmd += list(extra or [])
        return self._run(task, cmd, env=self.env.child_env(java=False, network=True, install=True),
                         hook=self._pip_hook(task))

    # ------------------------------------------------------------------ steps
    def _step_java(self, task: SetupTask) -> str:
        """Use an existing Java 11+ or download a portable one."""
        info = self.env.java(refresh=True)
        if info["ok"]:
            selected = info["selected"]
            raise StepSkipped(f"Java {selected['version']} ({selected['source']})")
        return self._step_java_portable(task)

    def _step_java_portable(self, task: SetupTask) -> str:
        """Download, verify and extract an Eclipse Temurin JRE into the ``java`` folder."""
        version = int(task.params.get("java_version") or self.settings.get("java_portable_version") or 21)
        os_name, arch, ext = adoptium_platform()
        checksum: Optional[str] = None
        custom_url = self.settings.get("java_download_url")
        if custom_url:
            url = custom_url
            name = os.path.basename(urllib.parse.urlsplit(url).path) or f"jre.{ext}"
            task.log.append(f"Using the configured download URL: {url}")
        else:
            api = (f"https://api.adoptium.net/v3/assets/latest/{version}/hotspot"
                   f"?architecture={arch}&image_type=jre&os={os_name}&vendor=eclipse")
            task.log.append(f"Asking Adoptium for the latest Temurin {version} JRE ({os_name}/{arch}) ...")
            data = self._http_json(api)
            if not isinstance(data, list) or not data:
                raise StepFailed("java_not_available", f"Temurin {version} {os_name}/{arch}")
            package = data[0]["binary"]["package"]
            url, checksum, name = package["link"], package.get("checksum"), package["name"]
            task.log.append(f"Release {data[0].get('release_name', '')}: {name} ({package.get('size', 0) / 1e6:.1f} MB)")

        os.makedirs(self.paths.install_tmp_dir, exist_ok=True)
        os.makedirs(self.paths.java_dir, exist_ok=True)
        archive = os.path.join(self.paths.install_tmp_dir, name)
        self._download(task, url, archive, checksum)
        staging = os.path.join(self.paths.java_dir, "_extract")
        remove_tree(staging)
        os.makedirs(staging, exist_ok=True)
        task.log.append("Extracting ...")
        extract_archive(archive, staging)
        try:
            os.remove(archive)
        except OSError:
            pass

        entries = os.listdir(staging)
        top = entries[0] if len(entries) == 1 and os.path.isdir(os.path.join(staging, entries[0])) else None
        target_name = top or f"jre-{version}"
        target = os.path.join(self.paths.java_dir, target_name)
        remove_tree(target)
        shutil.move(os.path.join(staging, top) if top else staging, target)
        remove_tree(staging)

        java = find_java_in_dir(target)
        info = probe_java(java) if java else None
        if not info or not info["ok"]:
            raise StepFailed("java_extract_failed", target)
        for other in os.listdir(self.paths.java_dir):
            if other != target_name and not other.startswith("_"):
                task.log.append(f"Removing older portable runtime {other}")
                remove_tree(os.path.join(self.paths.java_dir, other))
        if self.settings.get("java_mode") in ("system", "custom"):
            self.settings.update({"java_mode": "auto"})
        self.env.invalidate("java")
        task.log.append(f"Portable Java ready: {info['path']} ({info['version']})")
        return f"Java {info['version']}"

    def _step_java_winget(self, task: SetupTask) -> str:
        """Install Eclipse Temurin system-wide with winget (Windows)."""
        if not IS_WINDOWS:
            raise StepFailed("windows_only")
        if not shutil.which("winget"):
            raise StepFailed("no_winget")
        version = int(task.params.get("java_version") or self.settings.get("java_portable_version") or 21)
        package_id = f"EclipseAdoptium.Temurin.{version}.JRE"
        code = self._run(task, ["winget", "install", "--id", package_id, "-e", "--silent",
                                "--accept-package-agreements", "--accept-source-agreements",
                                "--disable-interactivity"], env=build_base_env())
        self.env.invalidate("java")
        info = self.env.java(refresh=True)
        found = [c for c in info["candidates"]
                 if c.get("ok") and c["source"] not in ("portable", "custom") and c.get("major") == version]
        if not found:
            raise StepFailed("winget_failed", f"exit code {code}")
        if self.settings.get("java_mode") == "portable":
            self.settings.update({"java_mode": "auto"})
        return f"Java {found[0]['version']}"

    def _step_java_remove(self, task: SetupTask) -> str:
        """Delete every portable Java runtime from the ``java`` folder."""
        removed = 0
        names = os.listdir(self.paths.java_dir) if os.path.isdir(self.paths.java_dir) else []
        for name in names:
            task.log.append(f"Deleting {os.path.join(self.paths.java_dir, name)}")
            remove_tree(os.path.join(self.paths.java_dir, name))
            removed += 1
        if self.settings.get("java_mode") == "portable":
            self.settings.update({"java_mode": "auto"})
        self.env.invalidate("java")
        if not removed:
            raise StepSkipped("nothing to remove")
        return f"{removed} removed"

    def _step_venv(self, task: SetupTask) -> str:
        """Create (or recreate) the ``venv`` folder with a suitable interpreter."""
        venv_dir = self.paths.venv_dir
        if self.env.venv_exists() and not task.params.get("recreate"):
            info = probe_python(self.env.venv_python())
            if info:
                raise StepSkipped(f"Python {info['version']}")
        self.hooks.prepare_venv_change()
        python = task.params.get("python") or self.env.pick_python()
        if not python:
            raise StepFailed("no_python")
        info = probe_python(python)
        if not info:
            raise StepFailed("python_broken", python)
        if not info["supported"]:
            raise StepFailed("python_too_old", str(info["version"]))
        task.log.append(f"Using Python {info['version']} ({info['bits']}-bit): {python}")
        if os.path.exists(venv_dir):
            task.log.append("Removing the old virtual environment ...")
            remove_tree(venv_dir)
        os.makedirs(os.path.dirname(venv_dir), exist_ok=True)
        task.log.append(f"Install folder: {self.paths.install_dir}")
        code = self._run(task, [python, "-m", "venv", venv_dir], env=build_base_env())
        if code != 0 or not self.env.venv_exists():
            raise StepFailed("venv_failed", f"exit code {code}")
        self.settings.update({"python_path": python})
        self.env.invalidate("package", "cli_options")
        return f"Python {info['version']}"

    def _step_pip(self, task: SetupTask) -> str:
        """Upgrade pip inside the venv (a failure is only a warning)."""
        python = self._require_venv()
        code = self._run(task, [python, "-m", "pip", "install", "--upgrade", "pip", *self._pip_args(False)],
                         env=self.env.child_env(java=False, network=True, install=True), hook=self._pip_hook(task))
        self.env.invalidate("package")
        if code != 0:
            task.log.append("Warning: pip could not be upgraded - continuing with the bundled version.")
            return "warning"
        return ""

    def _step_package(self, task: SetupTask) -> str:
        """Install or upgrade ``opendataloader-pdf`` (optionally a fixed version)."""
        self._require_venv()
        self.hooks.prepare_venv_change()
        version = str(task.params.get("version") or "").strip()
        if version and not _VERSION_RE.match(version):
            raise StepFailed("bad_version", version)
        spec = f"{PACKAGE}=={version}" if version else PACKAGE
        code = self._pip(task, spec, upgrade=not version)
        self.env.invalidate("package", "cli_options")
        package = self.env.package(refresh=True)
        if code != 0 or not package["installed"]:
            raise StepFailed("pip_failed", PACKAGE)
        return f"v{package['version']}"

    def _step_hybrid(self, task: SetupTask) -> str:
        """Install the ``[hybrid]`` extra (docling, FastAPI, uvicorn, EasyOCR...)."""
        python = self._require_venv()
        self.hooks.prepare_venv_change()
        installed = self.env.package(refresh=True).get("version")
        spec = f"{PACKAGE}[hybrid]=={installed}" if installed else f"{PACKAGE}[hybrid]"
        try:
            extra = shlex.split(str(task.params.get("extra_args") or ""))
        except ValueError as exc:
            raise StepFailed("bad_extra_args", str(exc)) from exc
        if not task.params.get("force"):
            self._hybrid_preflight(task, python, spec, extra)
        task.log.append("Note: this downloads several hundred MB (docling, PyTorch, OCR models). Please wait ...")
        code = self._pip(task, spec, extra=extra)
        self.env.invalidate("package", "cli_options")
        package = self.env.package(refresh=True)
        if code != 0 or not package["hybrid"]:
            raise StepFailed("pip_failed", "hybrid")
        return f"docling {package['packages'].get('docling')}"

    def _hybrid_preflight(self, task: SetupTask, python: str, spec: str, extra: List[str]) -> None:
        """Resolve the hybrid extra with ``pip --dry-run`` before downloading gigabytes.

        Very new Python versions (e.g. 3.15) often lack prebuilt Windows wheels
        for some native dependencies; pip would then try to compile them, which
        fails without a C/C++ compiler - after a long download. The dry run only
        fetches package metadata and reveals this up front.

        Args:
            task: Owning task.
            python: Interpreter of the venv.
            spec: Requirement to resolve.
            extra: Additional pip arguments from the UI.

        Raises:
            StepFailed: ``hybrid_preflight`` if pip cannot resolve/prepare the
                packages (the UI offers "install anyway" and "use another Python").
        """
        if version_key((self.env.package().get("packages") or {}).get("pip")) < (22, 2):
            task.log.append("Pre-check skipped (pip is too old for --dry-run --report).")
            return
        os.makedirs(self.paths.install_tmp_dir, exist_ok=True)
        report = os.path.join(self.paths.install_tmp_dir, f"pip-report-{task.id}.json")
        task.log.append("Pre-check: resolving the hybrid dependencies (metadata only, no large downloads) ...")
        cmd = [python, "-m", "pip", "install", "--dry-run", "--report", report, spec, *self._pip_args(False), *extra]
        code = self._run(task, cmd, env=self.env.child_env(java=False, network=True, install=True))
        if code != 0:
            errors = [ln for ln in task.log.tail(40) if ln.startswith("ERROR") or "error:" in ln.lower()]
            python_version = (probe_python(python) or {}).get("version", "?")
            raise StepFailed("hybrid_preflight", f"Python {python_version}: " + (" | ".join(errors[-3:]) or f"exit code {code}"))
        try:
            with open(report, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        finally:
            try:
                os.remove(report)
            except OSError:
                pass
        source_builds = []
        for item in data.get("install", []):
            url = str((item.get("download_info") or {}).get("url") or "")
            meta = item.get("metadata") or {}
            if url and not url.split("?")[0].lower().endswith(".whl"):
                source_builds.append(f"{meta.get('name', '?')} {meta.get('version', '')}".strip())
        total = len(data.get("install", []))
        if source_builds:
            task.log.append(f"Pre-check: {total} package(s) to install; these have no prebuilt wheel and will be "
                            f"built from source: {', '.join(source_builds)}")
            task.log.append("Pure-Python packages build fine. Native ones need 'Microsoft C++ Build Tools'; if the "
                            "installation fails, recreate the environment with Python 3.13 or 3.14.")
        else:
            task.log.append(f"Pre-check passed: all {total} package(s) are available as prebuilt wheels.")

    def _step_rapidocr(self, task: SetupTask) -> str:
        """Install the RapidOCR engine (``rapidocr`` + ``onnxruntime``)."""
        self.hooks.prepare_venv_change()
        code = self._pip(task, "rapidocr", "onnxruntime")
        self.env.invalidate("package")
        if code != 0:
            raise StepFailed("pip_failed", "rapidocr")
        return ""

    def _step_tesseract(self, task: SetupTask) -> str:
        """Install Tesseract OCR (UB-Mannheim build) with winget."""
        if not IS_WINDOWS:
            raise StepFailed("windows_only")
        if not shutil.which("winget"):
            raise StepFailed("no_winget")
        code = self._run(task, ["winget", "install", "--id", "UB-Mannheim.TesseractOCR", "-e", "--silent",
                                "--accept-package-agreements", "--accept-source-agreements",
                                "--disable-interactivity"], env=build_base_env())
        folder = tesseract_dir()
        if not folder:
            raise StepFailed("winget_failed", f"exit code {code}")
        return folder

    def _step_uninstall(self, task: SetupTask) -> str:
        """Remove ``opendataloader-pdf`` from the venv (dependencies stay)."""
        python = self._require_venv()
        self.hooks.prepare_venv_change()
        code = self._run(task, [python, "-m", "pip", "uninstall", "-y", PACKAGE],
                         env=self.env.child_env(java=False))
        self.env.invalidate("package", "cli_options")
        if code != 0:
            raise StepFailed("pip_failed", "uninstall")
        return ""

    def _step_reset(self, task: SetupTask) -> str:
        """Delete the complete virtual environment."""
        self.hooks.prepare_venv_change()
        task.log.append(f"Deleting {self.paths.venv_dir} ...")
        remove_tree(self.paths.venv_dir)
        if os.path.exists(self.paths.venv_dir):
            raise StepFailed("delete_failed", self.paths.venv_dir)
        self.env.invalidate()
        return ""

    def _step_verify(self, task: SetupTask) -> str:
        """Convert a generated one-page PDF and check the produced files."""
        python = self._require_venv()
        java = self.env.java(refresh=True)
        if not java["ok"]:
            raise StepFailed(java.get("error") or "java_missing")
        package = self.env.package(refresh=True)
        if not package["installed"]:
            raise StepFailed("package_missing")
        workdir = os.path.join(self.paths.tmp_dir, f"selftest-{int(time.time())}")
        os.makedirs(workdir, exist_ok=True)
        pdf = os.path.join(workdir, "selftest.pdf")
        out = os.path.join(workdir, "out")
        make_test_pdf(pdf)
        started = time.time()
        code = self._run(task, [python, "-X", "utf8", "-m", "opendataloader_pdf", pdf, "-o", out,
                                "-f", "json,markdown"],
                         env=self.env.child_env(java=True), cwd=workdir, hook=self._java_hook)
        seconds = time.time() - started
        markdown = os.path.join(out, "selftest.md")
        ok = code == 0 and os.path.isfile(markdown) and os.path.isfile(os.path.join(out, "selftest.json"))
        if ok:
            with open(markdown, "r", encoding="utf-8", errors="replace") as fh:
                ok = "Hello OpenDataLoader" in fh.read()
        remove_tree(workdir)
        self.last_verify = {"ok": ok, "time": time.time(), "seconds": round(seconds, 2),
                            "java": java["selected"]["version"], "version": package["version"]}
        if not ok:
            raise StepFailed("selftest_failed", f"exit code {code}")
        task.log.append(f"Self-test passed in {seconds:.1f}s (Java {java['selected']['version']}, "
                        f"opendataloader-pdf {package['version']}).")
        return f"OK ({seconds:.1f}s)"

    # ------------------------------------------------------------- storage
    def _step_location(self, task: SetupTask) -> str:
        """Switch to the install/models folders chosen in the install dialog (nothing is moved).

        The install folder is applied first, so a default models folder is
        checked below the new install folder.
        """
        changed = []
        for kind, key in (("install", "install_dir"), ("models", "models_dir")):
            value = task.params.get(key)
            if value is None:
                continue
            check = check_target(self.paths, kind, str(value))
            if not check["ok"]:
                raise StepFailed("location_" + str(check["error"]), check["path"])
            if check["same"]:
                continue
            self.settings.update({key: check["setting"]})
            self.paths.apply_locations(self.settings.get("install_dir"), self.settings.get("models_dir"))
            task.log.append(f"{'Install' if kind == 'install' else 'Models'} folder: {check['path']}")
            changed.append(check["path"])
        self.env.invalidate()
        if not changed:
            raise StepSkipped(self.paths.install_dir)
        return " · ".join(changed)

    def _step_relocate(self, task: SetupTask) -> str:
        """Move the install or models folder to a new place (or switch to it).

        Parameters (``task.params``): ``kind`` (``"install"``/``"models"``),
        ``path`` (``""`` = default location), ``mode`` (``"move"`` keeps
        everything installed, ``"fresh"`` only switches the folder) and
        ``delete_old`` (``fresh`` only: delete the old files).

        Folders are renamed when they stay on the same drive and copied with
        progress otherwise. Nothing is overwritten; a failure or cancellation
        moves already moved folders back. The setting changes only after
        everything was moved.
        """
        kind = str(task.params.get("kind") or "")
        if kind not in ("install", "models"):
            raise StepFailed("bad_action", kind)
        mode = "fresh" if task.params.get("mode") == "fresh" else "move"
        check = check_target(self.paths, kind, str(task.params.get("path") or ""))
        if not check["ok"]:
            raise StepFailed("location_" + str(check["error"]), check["path"])
        if check["same"]:
            raise StepSkipped("the folder did not change")
        self.hooks.prepare_venv_change()
        key = "install_dir" if kind == "install" else "models_dir"
        settings = {"install_dir": self.settings.get("install_dir"), "models_dir": self.settings.get("models_dir")}
        settings[key] = check["setting"]
        new = self.paths.locations(settings["install_dir"], settings["models_dir"])
        if kind == "install":
            items = [(name, getattr(self.paths, f"{name}_dir"), new[name]) for name in ("venv", "java", "cache")]
            if self.paths.models_default:
                items.append(("models", self.paths.models_dir, new["models"]))
        else:
            items = [("models", self.paths.models_dir, new["models"])]
        present = [item for item in items if os.path.exists(item[1])]
        task.log.append(f"Current folder: {self.paths.install_dir if kind == 'install' else self.paths.models_dir}")
        task.log.append(f"New folder:     {check['path']}  ({'move the files' if mode == 'move' else 'start fresh'})")
        if mode == "move" and present:
            self._move_items(task, present, check["path"])
        self.settings.update({key: check["setting"]})
        self.paths.apply_locations(self.settings.get("install_dir"), self.settings.get("models_dir"))
        if mode == "fresh" and task.params.get("delete_old"):
            for name, source, _target in present:
                task.log.append(f"Deleting the old {name} folder: {source}")
                remove_tree(source)
        self.env.invalidate()
        if kind == "install" and mode == "move" and self.env.venv_exists() and not probe_python(self.env.venv_python()):
            task.log.append("Warning: the moved Python environment does not start; recreate it on the Setup page.")
            return f"{check['path']} (recreate the environment)"
        return check["path"]

    def _move_items(self, task: SetupTask, items: List[Tuple[str, str, str]], target_root: str) -> None:
        """Move several folders with combined progress; undo everything on failure.

        Args:
            task: Owning task (progress in ``task.download``, cancel flag).
            items: ``(name, source, target)`` of folders that exist.
            target_root: The new install or models folder (for the space check).

        Raises:
            StepFailed: ``location_target_exists``, ``location_no_space`` or ``move_failed``.
            TaskCanceled: If the user canceled (moved folders are moved back).
        """
        for _name, _source, target in items:
            if os.path.isdir(target) and os.listdir(target):
                raise StepFailed("location_target_exists", target)
        sizes = {name: dir_size(source, task.cancel_event)[0] for name, source, _target in items}
        total = sum(sizes.values())
        copied = sum(sizes[name] for name, source, target in items if not same_volume(source, target))
        free = free_space(target_root)
        if copied and free is not None and free < copied * 1.05 + 50 * 1024 * 1024:
            raise StepFailed("location_no_space", f"{copied / 1e9:.2f} GB needed, {free / 1e9:.2f} GB free")
        force_copy = bool(task.params.get("force_copy"))
        moved: List[Tuple[str, str, str]] = []
        done = 0

        def progress(name: str, value: int) -> None:
            """Publish the copy progress for the task panel."""
            task.download = {"name": name, "done": value, "total": total, "kind": "copy"}

        try:
            for name, source, target in items:
                task.log.append(f"Moving {name} ({sizes[name] / 1e6:.0f} MB): {source} -> {target}")
                done = move_folder(source, target, progress, task.cancel_event, done, force_copy)
                moved.append((name, source, target))
        except (MoveCanceled, MoveError, OSError) as exc:
            for name, source, target in reversed(moved):
                task.log.append(f"Moving {name} back to {source} ...")
                try:
                    move_folder(target, source, lambda _n, _v: None)
                except (MoveError, OSError) as undo:
                    task.log.append(f"Warning: could not move {name} back: {undo}")
            if isinstance(exc, MoveCanceled):
                raise TaskCanceled() from exc
            code = "location_" + exc.code if isinstance(exc, MoveError) else "move_failed"
            raise StepFailed(code, getattr(exc, "detail", "") or str(exc)) from exc
        finally:
            task.download = None
        task.log.append(f"Moved {len(moved)} folder(s), {total / 1e6:.0f} MB.")

    def _step_clear_cache(self, task: SetupTask) -> str:
        """Delete pip's download cache and temporary files in the install folder."""
        size = dir_size(self.paths.cache_dir)[0]
        if not size:
            raise StepSkipped("the cache is empty")
        task.log.append(f"Deleting {self.paths.cache_dir} ({size / 1e6:.0f} MB) ...")
        remove_tree(self.paths.cache_dir)
        return f"{size / 1e6:.0f} MB"

    def _step_clear_models(self, task: SetupTask) -> str:
        """Delete every model downloaded by the hybrid server (they download again when needed)."""
        size = dir_size(self.paths.models_dir)[0]
        if not size:
            raise StepSkipped("no models downloaded")
        self.hooks.prepare_venv_change()
        task.log.append(f"Deleting {self.paths.models_dir} ({size / 1e6:.0f} MB) ...")
        remove_tree(self.paths.models_dir)
        if os.path.exists(self.paths.models_dir) and os.listdir(self.paths.models_dir):
            raise StepFailed("delete_failed", self.paths.models_dir)
        return f"{size / 1e6:.0f} MB"

    # -------------------------------------------------------------- network
    def _http_json(self, url: str) -> Any:
        """GET a JSON document honouring the proxy settings.

        Raises:
            StepFailed: ``network`` with the error text.
        """
        import json

        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self.env.url_opener().open(request, timeout=30) as response:
                return json.load(response)
        except Exception as exc:
            raise StepFailed("network", f"{url}: {exc}") from exc

    def _download(self, task: SetupTask, url: str, target: str, sha256: Optional[str] = None) -> None:
        """Download ``url`` to ``target`` with progress, cancellation and checksum.

        Args:
            task: Owning task (progress is published in ``task.download``).
            url: Source URL (redirects are followed).
            target: Destination file.
            sha256: Expected SHA-256 hex digest (optional).

        Raises:
            StepFailed: ``network`` or ``checksum_mismatch``.
            TaskCanceled: If the user cancels.
        """
        partial = target + ".part"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        name = os.path.basename(target)
        digest = hashlib.sha256()
        done = 0
        task.log.append(f"Downloading {url}")
        try:
            with self.env.url_opener().open(request, timeout=60) as response, open(partial, "wb") as fh:
                total = int(response.headers.get("Content-Length") or 0)
                task.download = {"name": name, "done": 0, "total": total}
                next_report = 0.1
                while True:
                    if task.cancel_event.is_set():
                        raise TaskCanceled()
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    task.download = {"name": name, "done": done, "total": total}
                    if total and done / total >= next_report:
                        task.log.append(f"  {done / total:4.0%}  ({done / 1e6:.1f} / {total / 1e6:.1f} MB)")
                        next_report += 0.1
        except TaskCanceled:
            self._silent_remove(partial)
            raise
        except Exception as exc:
            self._silent_remove(partial)
            raise StepFailed("network", f"{url}: {exc}") from exc
        finally:
            task.download = None
        if sha256 and digest.hexdigest().lower() != sha256.lower():
            self._silent_remove(partial)
            raise StepFailed("checksum_mismatch", name)
        if sha256:
            task.log.append("SHA-256 checksum verified.")
        os.replace(partial, target)

    @staticmethod
    def _silent_remove(path: str) -> None:
        """Delete a file, ignoring errors."""
        try:
            os.remove(path)
        except OSError:
            pass

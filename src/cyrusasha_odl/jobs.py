"""
Conversion jobs.

A *job* converts one or more PDF files/folders with one set of options. Each
job runs the ``opendataloader-pdf`` CLI of the private venv
(``python -m opendataloader_pdf ...``). Because every CLI call starts a new
JVM, all inputs of a job are passed to **one** call; only extremely long input
lists are split into several batches to stay below the Windows command-line
limit.

While the CLI runs, its log is parsed to report progress ("file 3 of 10"),
per-file results, warnings and errors. Afterwards the outputs of PDFs with
Persian, Arabic or Hebrew text are repaired (see :mod:`.rtl`: opendataloader-pdf
returns right-to-left text in reversed visual order). Every job keeps its own folder::

    data/jobs/<id>/job.json   metadata (state, inputs, options, results)
    data/jobs/<id>/log.txt    complete log
    data/jobs/<id>/output/    default output folder

The module also stores files that were dragged into the browser (uploads).
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import Paths, Settings, read_json, write_json_atomic
from .env import EnvInspector
from .errors import ApiError
from .hybrid import HybridManager, check_health
from .platform_utils import IS_WINDOWS, open_in_file_manager, remove_tree
from .procs import LogBuffer, ManagedProcess, display_command
from . import rtl

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Option specification                                                        #
# --------------------------------------------------------------------------- #
#: Options of the opendataloader-pdf CLI that the GUI exposes, grouped for the
#: options panel. ``type`` controls validation and the input widget:
#: ``bool``, ``choice``, ``multi`` (comma-separated list), ``int``, ``number``,
#: ``text``, ``password``, ``path``, ``url`` and ``rules`` (sanitize-rule file).
OPTION_SPEC: List[Dict[str, Any]] = [
    {"name": "format", "group": "output", "type": "multi", "default": ["json"],
     "choices": ["json", "markdown", "html", "text", "pdf", "tagged-pdf"]},
    {"name": "markdown-with-html", "group": "output", "type": "bool"},
    {"name": "pages", "group": "output", "type": "text", "pattern": r"^[0-9,\-\s]+$", "placeholder": "1,3,5-7"},
    {"name": "password", "group": "output", "type": "password"},
    {"name": "rtl-fix", "group": "text", "type": "choice", "choices": list(rtl.RTL_MODES), "default": "auto",
     "gui": True},
    {"name": "keep-line-breaks", "group": "text", "type": "bool"},
    {"name": "include-header-footer", "group": "text", "type": "bool"},
    {"name": "detect-strikethrough", "group": "text", "type": "bool"},
    {"name": "include-links", "group": "text", "type": "bool"},
    {"name": "replace-invalid-chars", "group": "text", "type": "text", "placeholder": "' '", "max": 10},
    {"name": "space-ratio", "group": "text", "type": "number", "min": 0, "max": 10, "placeholder": "0.17"},
    {"name": "table-method", "group": "layout", "type": "choice", "choices": ["default", "cluster"], "default": "default"},
    {"name": "reading-order", "group": "layout", "type": "choice", "choices": ["xycut", "off"], "default": "xycut"},
    {"name": "use-struct-tree", "group": "layout", "type": "bool"},
    {"name": "image-output", "group": "images", "type": "choice", "choices": ["external", "embedded", "off"],
     "default": "external"},
    {"name": "image-format", "group": "images", "type": "choice", "choices": ["png", "jpeg"], "default": "png"},
    {"name": "image-resolution", "group": "images", "type": "number", "min": 1, "max": 2400, "placeholder": "144"},
    {"name": "image-dir", "group": "images", "type": "path"},
    {"name": "markdown-page-separator", "group": "separators", "type": "text", "placeholder": "--- %page-number% ---"},
    {"name": "text-page-separator", "group": "separators", "type": "text", "placeholder": "--- %page-number% ---"},
    {"name": "html-page-separator", "group": "separators", "type": "text", "placeholder": "<hr/>"},
    {"name": "content-safety-off", "group": "safety", "type": "multi",
     "choices": ["all", "hidden-text", "off-page", "tiny", "hidden-ocg", "background"]},
    {"name": "filter-hidden-text", "group": "safety", "type": "choice", "choices": ["off", "on"], "default": "off"},
    {"name": "sanitize", "group": "safety", "type": "bool"},
    {"name": "sanitize-rules", "group": "safety", "type": "rules"},
    {"name": "hybrid", "group": "hybrid", "type": "choice", "choices": ["off", "docling-fast"], "default": "off"},
    {"name": "hybrid-mode", "group": "hybrid", "type": "choice", "choices": ["auto", "full"], "default": "auto"},
    {"name": "hybrid-url", "group": "hybrid", "type": "url", "placeholder": "http://127.0.0.1:5002"},
    {"name": "hybrid-timeout", "group": "hybrid", "type": "int", "min": 0, "max": 86400000, "placeholder": "0"},
    {"name": "hybrid-chunk-size", "group": "hybrid", "type": "int", "min": 1, "max": 100000, "placeholder": "50"},
    {"name": "hybrid-fallback", "group": "hybrid", "type": "bool"},
    {"name": "hybrid-availability-timeout", "group": "hybrid", "type": "int", "min": 0, "max": 600000,
     "placeholder": "3000"},
    {"name": "hybrid-triage-log", "group": "hybrid", "type": "bool"},
    {"name": "threads", "group": "performance", "type": "int", "min": 1, "max": 256, "placeholder": "1"},
]

#: CLI options the GUI controls itself and never accepts from the UI.
RESERVED_OPTIONS = {"output-dir", "quiet", "to-stdout"}

#: Options handled by the GUI itself (``"gui": True``); they are stored with the
#: job but never passed to the CLI.
GUI_OPTIONS = {o["name"] for o in OPTION_SPEC if o.get("gui")}

#: Options that only affect Markdown/HTML/text/image files; the line pass of the
#: RTL repair (JSON only) drops them.
LINE_PASS_DROP = ("format", "markdown-with-html", "image-output", "image-format", "image-dir", "image-resolution",
                  "markdown-page-separator", "text-page-separator", "html-page-separator", "hybrid-triage-log")

#: Order of the option groups in the UI.
OPTION_GROUPS = ["output", "text", "layout", "images", "separators", "safety", "hybrid", "performance", "other"]

#: Seconds a job waits for the managed hybrid server (first start downloads models).
HYBRID_WAIT_SECONDS = 1800

#: Maximum length of the argument data of one CLI call (Windows limit is 32767).
MAX_ARGS_CHARS = 24000

_SPEC_BY_NAME = {o["name"]: o for o in OPTION_SPEC}
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
_BATCH_RE = re.compile(r"^[A-Za-z0-9\-]{1,40}$")
_LOG_RE = re.compile(r"^(?:\d{2}:\d{2}:\d{2} )?(SEVERE|WARNING|INFO|CONFIG|FINE|FINER|FINEST): (.*)$")
_JUL_HEADER_RE = re.compile(r"^[A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2}:\d{2} [AP]M \S+ \S+$")
_PASSWORD_ERR_RE = re.compile(r"^Error: '(.+)' is password-protected")

#: Job states that count as "active".
ACTIVE_STATES = ("queued", "waiting", "running")


def count_pdfs(path: str, limit: int = 100000) -> Tuple[int, int]:
    """Count PDF files (recursively for folders) and their total size.

    Args:
        path: A file or folder.
        limit: Stop counting after this many files (protects against huge trees).

    Returns:
        ``(number_of_pdfs, total_bytes)``.
    """
    if os.path.isfile(path):
        try:
            return (1 if path.lower().endswith(".pdf") else 0), os.path.getsize(path)
        except OSError:
            return 0, 0
    count = size = 0
    for current, _dirs, files in os.walk(path):
        for name in files:
            if name.lower().endswith(".pdf"):
                count += 1
                try:
                    size += os.path.getsize(os.path.join(current, name))
                except OSError:
                    pass
                if count >= limit:
                    return count, size
    return count, size


def inspect_path(path: str, source: str = "local") -> Dict[str, Any]:
    """Describe an input path for the input list.

    Args:
        path: Absolute path typed, picked or uploaded by the user.
        source: ``"local"`` or ``"upload"``.

    Returns:
        ``{"path", "label", "kind", "pdfs", "size", "source", "exists", "error"}``.
    """
    path = os.path.normpath(os.path.expandvars(os.path.expanduser(str(path).strip().strip('"'))))
    info: Dict[str, Any] = {"path": path, "label": os.path.basename(path.rstrip("\\/")) or path,
                            "kind": None, "pdfs": 0, "size": 0, "source": source, "exists": False, "error": None}
    if not os.path.isabs(path):
        info["error"] = "not_absolute"
        return info
    if os.path.isdir(path):
        info["kind"] = "folder"
    elif os.path.isfile(path):
        info["kind"] = "file"
        if not path.lower().endswith(".pdf"):
            info["error"] = "not_pdf"
    else:
        info["error"] = "not_found"
        return info
    info["exists"] = True
    info["pdfs"], info["size"] = count_pdfs(path)
    if info["kind"] == "folder" and info["pdfs"] == 0:
        info["error"] = "no_pdfs"
    return info


def _norm(path: str) -> str:
    """Normalise a path for comparisons (case-insensitive on Windows)."""
    return os.path.normcase(os.path.realpath(path))


def is_within(path: str, root: str) -> bool:
    """Return ``True`` if ``path`` is ``root`` or inside it (symlinks resolved)."""
    try:
        path_n, root_n = _norm(path), _norm(root)
        return os.path.commonpath([path_n, root_n]) == root_n
    except ValueError:  # different drives on Windows
        return False


class Job:
    """One conversion job (mutable state shared with the UI)."""

    def __init__(self, job_id: str, folder: str) -> None:
        """Create an empty job object.

        Args:
            job_id: Unique id (``YYYYMMDD-HHMMSS-xxxx``).
            folder: ``data/jobs/<id>``.
        """
        self.id = job_id
        self.folder = folder
        self.name = ""
        self.preset = ""
        self.state = "queued"
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.inputs: List[Dict[str, Any]] = []
        self.options: Dict[str, Any] = {}
        self.output_dir = ""
        self.output_mode = "job"
        self.command = ""
        self.exit_code: Optional[int] = None
        self.error: Optional[str] = None
        self.error_detail = ""
        self.files_total = 0
        self.files_started = 0
        self.current_file = ""
        self.current_pages: Optional[int] = None
        self.pages_total = 0
        self.batches = 1
        self.batch = 0
        self.warnings = 0
        self.errors: List[str] = []
        self.file_results: List[Dict[str, Any]] = []
        self.created_files: List[str] = []
        self.hybrid_url = ""
        self.outputs_count = 0
        self.outputs_size = 0
        #: Current step of a running job: ``""`` (conversion) or ``"rtl"`` (RTL repair).
        self.phase = ""
        #: Summary of the RTL repair: ``{"files", "elements", "tables", "languages"}``.
        self.rtl: Dict[str, Any] = {}
        self.secret_password: Optional[str] = None
        self.proc: Optional[ManagedProcess] = None
        self.cancel_event = threading.Event()
        self.log: Optional[LogBuffer] = None
        self._last_key = ""
        self._repeat = 0

    # ------------------------------------------------------------ properties
    @property
    def log_path(self) -> str:
        """Path of the job log file."""
        return os.path.join(self.folder, "log.txt")

    @property
    def meta_path(self) -> str:
        """Path of ``job.json``."""
        return os.path.join(self.folder, "job.json")

    @property
    def active(self) -> bool:
        """``True`` while queued, waiting or running."""
        return self.state in ACTIVE_STATES

    @property
    def uses_hybrid(self) -> bool:
        """``True`` if the job uses the hybrid backend."""
        return self.options.get("hybrid", "off") not in ("", "off", None)

    def get_log(self) -> LogBuffer:
        """Return the log buffer, loading it from disk for old jobs."""
        if self.log is None:
            self.log = LogBuffer.from_file(self.log_path)
        return self.log

    # ------------------------------------------------------------ serialise
    def to_dict(self, full: bool = False) -> Dict[str, Any]:
        """Return a JSON-serialisable snapshot.

        Args:
            full: Include large fields (per-file results, all errors, created files).
        """
        data: Dict[str, Any] = {
            "id": self.id, "name": self.name, "preset": self.preset, "state": self.state,
            "created": self.created, "started": self.started, "finished": self.finished,
            "inputs": self.inputs, "output_dir": self.output_dir, "output_mode": self.output_mode,
            "exit_code": self.exit_code, "error": self.error, "error_detail": self.error_detail,
            "files_total": self.files_total, "files_started": self.files_started,
            "current_file": self.current_file, "current_pages": self.current_pages,
            "pages_total": self.pages_total, "batches": self.batches, "batch": self.batch,
            "warnings": self.warnings, "errors_count": len(self.errors),
            "outputs_count": self.outputs_count, "outputs_size": self.outputs_size,
            "hybrid": self.uses_hybrid, "formats": self.options.get("format") or ["json"],
            "options": self.public_options(), "phase": self.phase, "rtl": self.rtl,
        }
        if full:
            data.update({
                "command": self.command, "errors": self.errors[-200:],
                "file_results": self.file_results, "hybrid_url": self.hybrid_url,
                "log_total": self.get_log().total,
            })
        return data

    def public_options(self) -> Dict[str, Any]:
        """Options with the password masked (safe to store and display)."""
        options = dict(self.options)
        if options.get("password"):
            options["password"] = "********"
        return options

    def save(self) -> None:
        """Persist ``job.json`` (errors are logged, not raised)."""
        data = self.to_dict(full=False)
        data.update({"command": self.command, "errors": self.errors[-200:], "file_results": self.file_results,
                     "created_files": self.created_files[-5000:], "hybrid_url": self.hybrid_url})
        try:
            write_json_atomic(self.meta_path, data)
        except OSError as exc:
            log.warning("could not save job %s: %s", self.id, exc)

    @classmethod
    def load(cls, folder: str) -> Optional["Job"]:
        """Load a job from ``<folder>/job.json`` (``None`` if unreadable)."""
        data = read_json(os.path.join(folder, "job.json"), None)
        if not isinstance(data, dict) or not data.get("id"):
            return None
        job = cls(str(data["id"]), folder)
        for key in ("name", "preset", "state", "created", "started", "finished", "inputs", "output_dir",
                    "output_mode", "exit_code", "error", "error_detail", "files_total", "files_started",
                    "pages_total", "batches", "warnings", "outputs_count", "outputs_size", "command",
                    "errors", "file_results", "created_files", "hybrid_url", "rtl"):
            if key in data and data[key] is not None:
                setattr(job, key, data[key])
        job.options = data.get("options") or {}
        if job.state in ACTIVE_STATES:  # the application was closed while it ran
            job.state = "failed" if job.state == "running" else "canceled"
            job.error = "interrupted"
            job.finished = job.finished or time.time()
            job.save()
        return job


class JobManager:
    """Creates, schedules, runs and stores conversion jobs."""

    def __init__(self, paths: Paths, settings: Settings, env: EnvInspector, hybrid: HybridManager,
                 setup: Any, rules: Any, on_change: Callable[[], None]) -> None:
        """Create the manager and load the job history.

        Args:
            paths: Application paths.
            settings: Persistent settings.
            env: Environment inspector.
            hybrid: Hybrid-server manager (auto start for hybrid jobs).
            setup: Setup manager (jobs wait while the venv is being changed).
            rules: Rule store (resolves ``rules:<name>`` references).
            on_change: Callback notifying the UI about changes.
        """
        self.paths = paths
        self.settings = settings
        self.env = env
        self.hybrid = hybrid
        self.setup = setup
        self.rules = rules
        self.on_change = on_change
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._running = 0
        self._stopped = False
        self._load_history()
        threading.Thread(target=self._scheduler, name="job-scheduler", daemon=True).start()

    # --------------------------------------------------------------- history
    def _load_history(self) -> None:
        """Load the newest 1000 jobs from ``data/jobs``."""
        try:
            names = sorted((n for n in os.listdir(self.paths.jobs_dir) if _JOB_ID_RE.match(n)), reverse=True)
        except OSError:
            names = []
        for name in names[:1000]:
            job = Job.load(os.path.join(self.paths.jobs_dir, name))
            if job:
                self._jobs[job.id] = job

    def list(self) -> List[Dict[str, Any]]:
        """All jobs, newest first (compact snapshots)."""
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)
            return [j.to_dict() for j in jobs]

    def get(self, job_id: str) -> Job:
        """Return a job by id.

        Raises:
            ApiError: ``job_not_found``.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise ApiError("job_not_found", job_id, 404)
        return job

    def active(self) -> bool:
        """``True`` if any job is queued, waiting or running."""
        with self._lock:
            return any(j.active for j in self._jobs.values())

    def counts(self) -> Dict[str, int]:
        """Number of jobs per state (for the header badges)."""
        result: Dict[str, int] = {}
        with self._lock:
            for job in self._jobs.values():
                result[job.state] = result.get(job.state, 0) + 1
        return result

    # --------------------------------------------------------------- options
    def option_metadata(self) -> Dict[str, Any]:
        """Return the option specification merged with the installed CLI metadata.

        Options that exist in the installed version but are unknown to this
        GUI are returned as ``extra`` (generic widgets); options of the GUI
        that the installed version lacks are listed in ``unsupported``.
        """
        installed = self.env.cli_options()
        names = {o["name"] for o in installed}
        extra = []
        for option in installed:
            if option["name"] in _SPEC_BY_NAME or option["name"] in RESERVED_OPTIONS:
                continue
            extra.append({"name": option["name"], "group": "other",
                          "type": "bool" if option.get("type") == "boolean" else "text",
                          "description": option.get("description", ""), "default": option.get("default")})
        descriptions = {o["name"]: o.get("description", "") for o in installed}
        spec = [dict(o, description=descriptions.get(o["name"], "")) for o in OPTION_SPEC]
        return {
            "groups": OPTION_GROUPS,
            "spec": spec,
            "extra": extra,
            "unsupported": sorted(o["name"] for o in OPTION_SPEC
                                  if names and o["name"] not in names and o["name"] not in GUI_OPTIONS),
            "version": self.env.package().get("version"),
        }

    def build_option_args(self, options: Dict[str, Any], mask: bool = False) -> Tuple[List[str], Dict[str, Any]]:
        """Validate UI options and turn them into CLI arguments.

        Long options are passed as ``--name=value`` so that values beginning
        with ``-`` can never be mistaken for another option. GUI options
        (:data:`GUI_OPTIONS`) are validated and kept but produce no argument.

        Args:
            options: ``{cli_option_name: value}`` from the UI.
            mask: Replace the password with ``********`` (for display).

        Returns:
            ``(arguments, cleaned_options)``.

        Raises:
            ApiError: ``bad_option`` (detail = option name) or ``option_unsupported``.
        """
        if not isinstance(options, dict):
            raise ApiError("bad_option", "options")
        installed = {o["name"]: o for o in self.env.cli_options()}
        args: List[str] = []
        cleaned: Dict[str, Any] = {}
        for name, value in options.items():
            if name in RESERVED_OPTIONS or value is None or value == "" or value is False or value == []:
                continue
            spec = _SPEC_BY_NAME.get(name)
            if spec is None:
                if name not in installed:
                    raise ApiError("option_unsupported", name)
                spec = {"name": name, "type": "bool" if installed[name].get("type") == "boolean" else "text"}
            elif installed and name not in installed and name not in GUI_OPTIONS:
                raise ApiError("option_unsupported", name)
            value = self._validate_option(spec, value)
            if value is None:
                continue
            cleaned[name] = value
            if name in GUI_OPTIONS:  # handled by the GUI, not a CLI argument
                continue
            kind = spec["type"]
            if kind == "bool":
                args.append(f"--{name}")
            elif kind == "multi":
                if name == "format":
                    args.append("--format=" + ",".join(value))
                else:
                    args.append(f"--{name}=" + ",".join(value))
            elif kind == "password":
                args.append(f"--{name}=" + ("********" if mask else str(value)))
            else:
                args.append(f"--{name}={value}")
        return args, cleaned

    def _validate_option(self, spec: Dict[str, Any], value: Any) -> Any:
        """Validate one option value according to its specification.

        Args:
            spec: Entry of :data:`OPTION_SPEC` (or a generic one).
            value: Raw value from the UI.

        Returns:
            The normalised value, or ``None`` when it equals the default.

        Raises:
            ApiError: ``bad_option`` with the option name.
        """
        name, kind = spec["name"], spec["type"]

        def fail() -> None:
            """Raise the validation error for this option."""
            raise ApiError("bad_option", name)

        if kind == "bool":
            return True if value in (True, "true", "1", 1, "on") else None
        if kind == "choice":
            if value not in spec["choices"]:
                fail()
            return None if value == spec.get("default") else value
        if kind == "multi":
            items = value if isinstance(value, list) else str(value).split(",")
            items = [str(i).strip() for i in items if str(i).strip()]
            if any(i not in spec["choices"] for i in items):
                fail()
            items = [c for c in spec["choices"] if c in items]  # canonical order, no duplicates
            if "all" in items:
                items = ["all"]
            return items or None
        if kind in ("int", "number"):
            try:
                number = int(str(value).strip()) if kind == "int" else float(str(value).strip())
            except ValueError:
                fail()
                return None
            if number != number or ("min" in spec and number < spec["min"]) or ("max" in spec and number > spec["max"]):
                fail()
            if kind == "number" and float(number).is_integer():
                number = float(number)
            return number
        text = str(value)
        if _CTRL_RE.search(text) or len(text) > spec.get("max", 1000):
            fail()
        if kind == "text" and spec.get("pattern") and not re.match(spec["pattern"], text):
            fail()
        if kind == "url":
            text = text.strip().rstrip("/")
            if not _URL_RE.match(text):
                fail()
        if kind == "path":
            text = os.path.normpath(text.strip().strip('"'))
            if not os.path.isabs(text):
                fail()
        if kind == "rules":
            text = text.strip()
            if text.startswith("rules:"):
                text = self.rules.resolve(text[len("rules:"):])
            text = os.path.normpath(text.strip('"'))
            if not os.path.isfile(text):
                fail()
        return text

    # -------------------------------------------------------------- creation
    def _new_id(self) -> str:
        """Generate a unique, sortable job id."""
        while True:
            job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            if job_id not in self._jobs and not os.path.exists(os.path.join(self.paths.jobs_dir, job_id)):
                return job_id

    def _resolve_output(self, job_id: str, payload: Dict[str, Any]) -> Tuple[str, str]:
        """Determine the output folder of a new job.

        Args:
            job_id: Id of the job.
            payload: Request data (``output_mode``, ``output_dir``).

        Returns:
            ``(mode, absolute_folder)``.

        Raises:
            ApiError: ``bad_output_dir``.
        """
        mode = payload.get("output_mode") or "job"
        folder = str(payload.get("output_dir") or "").strip().strip('"')
        if mode == "custom" or (mode == "default" and self.settings.get("default_output_dir")):
            folder = folder or self.settings.get("default_output_dir")
            folder = os.path.normpath(os.path.expandvars(os.path.expanduser(folder)))
            if not folder or not os.path.isabs(folder) or _CTRL_RE.search(folder):
                raise ApiError("bad_output_dir", folder)
            return "custom", folder
        return "job", os.path.join(self.paths.jobs_dir, job_id, "output")

    def preview(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return the command line a job with ``payload`` would run (nothing is started)."""
        args, _cleaned = self.build_option_args(payload.get("options") or {}, mask=True)
        inputs = [str(i.get("path") if isinstance(i, dict) else i) for i in payload.get("inputs") or []]
        out = str(payload.get("output_dir") or "") if payload.get("output_mode") == "custom" else "<job>/output"
        hybrid_url = ""
        options = payload.get("options") or {}
        if options.get("hybrid", "off") != "off" and not options.get("hybrid-url"):
            hybrid_url = self.settings.get("hybrid_external_url") or self.hybrid.url()
            args.append(f"--hybrid-url={hybrid_url}")
        cmd = ["opendataloader-pdf", *(inputs or ["<input.pdf>"]), f"--output-dir={out or '<folder>'}", *args]
        return {"command": display_command(cmd), "hybrid_url": hybrid_url}

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a job request and add the job to the queue.

        Args:
            payload: ``{"inputs": [paths or {"path", "source"}], "options": {...},
                "output_mode": "job"|"custom", "output_dir": str, "name": str, "preset": str}``.

        Returns:
            The job snapshot.

        Raises:
            ApiError: ``no_inputs``, ``input_missing``, ``bad_option``, ...
        """
        raw_inputs = payload.get("inputs") or []
        if not isinstance(raw_inputs, list) or not raw_inputs:
            raise ApiError("no_inputs")
        inputs: List[Dict[str, Any]] = []
        seen = set()
        for item in raw_inputs:
            path = item.get("path") if isinstance(item, dict) else item
            source = (item.get("source") if isinstance(item, dict) else None) or "local"
            info = inspect_path(str(path or ""), "upload" if source == "upload" else "local")
            if not info["exists"]:
                raise ApiError("input_missing", info["path"])
            if info["error"] == "not_pdf":
                raise ApiError("input_not_pdf", info["path"])
            key = _norm(info["path"])
            if key not in seen:
                seen.add(key)
                inputs.append(info)
        options = dict(payload.get("options") or {})
        _args, cleaned = self.build_option_args(options)
        with self._lock:
            job_id = self._new_id()
            folder = os.path.join(self.paths.jobs_dir, job_id)
            os.makedirs(folder, exist_ok=True)
            job = Job(job_id, folder)
            job.name = str(payload.get("name") or "").strip()[:120]
            job.preset = str(payload.get("preset") or "")[:60]
            job.inputs = inputs
            job.secret_password = cleaned.get("password")
            job.options = cleaned
            job.output_mode, job.output_dir = self._resolve_output(job_id, payload)
            job.files_total = sum(int(i["pdfs"]) for i in inputs)
            job.log = LogBuffer(job.log_path)
            job.save()
            self._jobs[job_id] = job
            self._wake.notify_all()
        self.on_change()
        return job.to_dict()

    def rerun(self, job_id: str) -> Dict[str, Any]:
        """Queue a new job with the same inputs, options and output settings.

        Raises:
            ApiError: ``password_required`` if the original password is no longer known.
        """
        old = self.get(job_id)
        options = dict(old.options)
        if options.get("password"):
            if not old.secret_password:
                raise ApiError("password_required")
            options["password"] = old.secret_password
        payload = {"inputs": [{"path": i["path"], "source": i.get("source", "local")} for i in old.inputs],
                   "options": options, "name": old.name, "preset": old.preset,
                   "output_mode": "custom" if old.output_mode == "custom" else "job",
                   "output_dir": old.output_dir if old.output_mode == "custom" else ""}
        return self.create(payload)

    # --------------------------------------------------------------- control
    def cancel(self, job_id: str) -> Dict[str, Any]:
        """Cancel a queued or running job (finished jobs are left untouched)."""
        job = self.get(job_id)
        with self._lock:
            if job.state == "queued":
                job.state = "canceled"
                job.finished = time.time()
                job.save()
            elif job.active:
                job.cancel_event.set()
                if job.proc:
                    job.proc.cancel()
            self._wake.notify_all()
        self.on_change()
        return job.to_dict()

    def delete(self, job_id: str) -> None:
        """Delete a finished job, its log and - for the default folder - its output.

        Files in a custom output folder are never deleted.

        Raises:
            ApiError: ``job_active`` for queued/running jobs.
        """
        job = self.get(job_id)
        if job.active:
            raise ApiError("job_active", job_id, 409)
        with self._lock:
            self._jobs.pop(job_id, None)
        if job.log:
            job.log.close()
        remove_tree(job.folder)
        self.on_change()

    def clear(self, states: List[str]) -> int:
        """Delete all finished jobs whose state is in ``states``.

        Returns:
            The number of deleted jobs.
        """
        allowed = {"success", "partial", "failed", "canceled"}
        targets = [j.id for j in list(self._jobs.values()) if j.state in allowed and j.state in set(states or allowed)]
        for job_id in targets:
            try:
                self.delete(job_id)
            except ApiError:
                pass
        return len(targets)

    def shutdown(self) -> None:
        """Cancel everything (called when the application exits)."""
        with self._lock:
            self._stopped = True
            jobs = [j for j in self._jobs.values() if j.active]
            self._wake.notify_all()
        for job in jobs:
            try:
                self.cancel(job.id)
            except ApiError:
                pass

    # ------------------------------------------------------------- scheduler
    def _scheduler(self) -> None:
        """Thread body: start queued jobs while capacity is available."""
        while True:
            with self._lock:
                if self._stopped:
                    return
                limit = max(1, int(self.settings.get("max_parallel_jobs") or 1))
                queued = sorted((j for j in self._jobs.values() if j.state == "queued"), key=lambda j: j.created)
                if queued and self._running < limit and not self.setup.modifying_venv():
                    job = queued[0]
                    job.state = "running"
                    job.started = time.time()
                    self._running += 1
                    threading.Thread(target=self._execute, args=(job,), name=f"job-{job.id}", daemon=True).start()
                    continue
                self._wake.wait(1.0)

    def kick(self) -> None:
        """Wake the scheduler (e.g. after a settings change)."""
        with self._lock:
            self._wake.notify_all()

    # ------------------------------------------------------------- execution
    def _report(self, job: Job, message: str) -> None:
        """Write a GUI message (prefixed with ``[GUI]``) into the job log."""
        job.get_log().append(f"[GUI] {message}")

    def _execute(self, job: Job) -> None:
        """Thread body: run one job from start to finish."""
        self.on_change()
        try:
            self._run_job(job)
        except ApiError as exc:
            job.state, job.error, job.error_detail = "failed", exc.code, exc.detail
            self._report(job, f"ERROR: {exc.code} {exc.detail}".rstrip())
        except Exception as exc:
            log.exception("job %s crashed", job.id)
            job.state, job.error, job.error_detail = "failed", "internal", str(exc)
            self._report(job, f"ERROR: {exc}")
        finally:
            job.finished = time.time()
            job.proc = None
            job.current_file = ""
            self._scan_outputs(job)
            elapsed = job.finished - (job.started or job.finished)
            self._report(job, f"Finished: {job.state} in {elapsed:.1f}s - {job.outputs_count} output file(s).")
            job.save()
            job.get_log().close()
            with self._lock:
                self._running -= 1
                self._wake.notify_all()
            self._after_finish(job)
            self.on_change()

    def _run_job(self, job: Job) -> None:
        """Prepare the environment and run all CLI batches of ``job``."""
        logbuf = job.get_log()
        self._report(job, f"Job {job.id}: {job.files_total} PDF file(s) in {len(job.inputs)} input(s).")
        python = self.env.venv_python()
        if not self.env.venv_exists() or not self.env.package().get("installed"):
            self.env.invalidate("package")
            if not self.env.package().get("installed"):
                raise ApiError("package_missing")
        java = self.env.java()
        if not java.get("ok"):
            java = self.env.java(refresh=True)
            if not java.get("ok"):
                raise ApiError(java.get("error") or "java_missing")
        self._report(job, f"Java {java['selected']['version']} ({java['selected']['path']})")

        options = dict(job.options)
        if job.secret_password:
            options["password"] = job.secret_password
        elif options.get("password"):
            raise ApiError("password_required")
        args, _ = self.build_option_args(options)

        if job.uses_hybrid:
            url = options.get("hybrid-url") or self.settings.get("hybrid_external_url")
            if url:
                job.hybrid_url = url
                if not check_health(url, timeout=5):
                    self._report(job, f"Warning: no answer from the hybrid server at {url}.")
            else:
                job.state = "waiting"
                self.on_change()
                ready = self.hybrid.ensure_ready(HYBRID_WAIT_SECONDS, job.cancel_event,
                                                 lambda m: self._report(job, m))
                if job.cancel_event.is_set():
                    job.state = "canceled"
                    return
                job.state = "running"
                self.on_change()
                job.hybrid_url = self.hybrid.url()
                if not ready and not options.get("hybrid-fallback"):
                    raise ApiError("hybrid_unavailable", job.hybrid_url)
                args.append(f"--hybrid-url={job.hybrid_url}")

        os.makedirs(job.output_dir, exist_ok=True)
        input_paths = [i["path"] for i in job.inputs]
        out_arg = f"--output-dir={job.output_dir}"
        batches = self._split_batches(input_paths, args + [out_arg])
        job.batches = len(batches)
        base = [python, "-X", "utf8", "-m", "opendataloader_pdf"]
        # Same arguments with the password masked - used for display and the log.
        shown = self.build_option_args(options, mask=True)[0]
        if job.hybrid_url and not options.get("hybrid-url"):
            shown.append(f"--hybrid-url={job.hybrid_url}")
        job.command = display_command(["opendataloader-pdf", *input_paths, out_arg, *shown])
        failures = 0
        last_code = 0
        for index, batch in enumerate(batches, 1):
            if job.cancel_event.is_set():
                break
            job.batch = index
            if len(batches) > 1:
                self._report(job, f"Batch {index}/{len(batches)} ({len(batch)} input(s))")
            cmd = base + batch + [out_arg] + args
            logbuf.append("$ " + display_command(base + batch + [out_arg] + shown))
            proc = ManagedProcess(cmd, log_buffer=logbuf, env=self.env.child_env(java=True),
                                  cwd=job.output_dir, on_line=lambda line: self._parse_line(job, line))
            try:
                proc.start()
            except OSError as exc:
                raise ApiError("start_failed", str(exc)) from exc
            job.proc = proc
            if job.cancel_event.is_set():
                proc.cancel()
            code = proc.wait()
            self._flush_repeat(job)
            self._close_file(job)
            last_code = int(code or 0)
            if last_code != 0:
                failures += 1
            self.on_change()
        job.exit_code = last_code if not failures else (last_code or 1)
        if job.cancel_event.is_set():
            final = "canceled"
        elif failures == 0:
            final = "success"
        else:
            final = "partial" if job.created_files else "failed"
            job.error = job.error or "cli_failed"
        if final in ("success", "partial"):
            # The job stays "running" until its outputs are repaired.
            self._repair_rtl(job, options)
            if job.cancel_event.is_set():
                final = "canceled"
        job.state = final

    def _split_batches(self, inputs: List[str], fixed_args: List[str]) -> List[List[str]]:
        """Split the inputs so that each CLI call stays below the size limit.

        On Windows, the opendataloader-pdf wrapper passes non-ASCII arguments as
        JSON in an environment variable (``\\uXXXX`` escapes make them longer),
        so the JSON length is used for the estimate.

        Args:
            inputs: Absolute input paths.
            fixed_args: Arguments repeated in every call.

        Returns:
            A list of input lists (at least one).
        """
        def size(items: List[str]) -> int:
            """Estimated length of the argument data for ``items``."""
            return len(json.dumps(items + fixed_args)) + 200

        batches: List[List[str]] = [[]]
        for path in inputs:
            if batches[-1] and size(batches[-1] + [path]) > MAX_ARGS_CHARS:
                batches.append([])
            batches[-1].append(path)
        return batches

    # ------------------------------------------------------------ RTL repair
    def _repair_rtl(self, job: Job, options: Dict[str, Any]) -> None:
        """Repair Persian/Arabic/Hebrew text in the outputs of a finished conversion.

        opendataloader-pdf returns right-to-left text in visual (reversed)
        order with presentation forms. For every PDF whose outputs contain RTL
        letters, a JSON with the line structure is produced (see
        :meth:`_line_pass`) and :class:`.rtl.OutputRepair` rewrites the JSON,
        Markdown, HTML and text outputs in place. Problems are logged; they
        never fail the job.

        Args:
            job: The job (state ``success`` or ``partial``).
            options: Options of the job, including the password.
        """
        mode = options.get("rtl-fix") or "auto"
        if mode == "off" or job.cancel_event.is_set():
            return
        # PDFs with the same name in different folders write the same output
        # files; only the PDF that wrote a file last may repair it.
        owner: Dict[str, int] = {}
        for index, result in enumerate(job.file_results):
            for path in result.get("files") or []:
                owner[_norm(path)] = index
        candidates = []
        for index, result in enumerate(job.file_results):
            files = [p for p in result.get("files") or [] if owner.get(_norm(p)) == index
                     and os.path.splitext(p)[1].lower() in rtl.TEXT_EXTENSIONS and os.path.isfile(p)]
            if files and result.get("source") and any(rtl.file_has_rtl(p) for p in files):
                candidates.append((result, files))
        if not candidates:
            return
        job.phase = "rtl"
        self.on_change()
        self._report(job, f"Right-to-left text found in {len(candidates)} file(s): repairing the outputs "
                          f"(opendataloader-pdf returns RTL text in reversed visual order).")
        keep = bool(options.get("keep-line-breaks"))
        line_files: Dict[str, str] = {}
        missing: List[str] = []
        for result, files in candidates:
            own_json = next((p for p in files if p.lower().endswith(".json")), None)
            if keep and own_json:
                line_files[result["source"]] = own_json  # already written with --keep-line-breaks
            else:
                missing.append(result["source"])
        tmp_root = os.path.join(self.paths.tmp_dir, f"rtl-{job.id}-{secrets.token_hex(2)}")
        separators = [str(options.get(k) or "") for k in
                      ("markdown-page-separator", "text-page-separator", "html-page-separator")]
        totals: Dict[str, Any] = {"files": 0, "elements": 0, "tables": 0, "approximate": 0, "languages": []}
        try:
            if missing:
                line_files.update(self._line_pass(job, options, missing, tmp_root))
            for result, files in candidates:
                if job.cancel_event.is_set():
                    self._report(job, "RTL repair canceled.")
                    return
                source_json = line_files.get(result["source"])
                if not source_json:
                    self._report(job, f"RTL repair skipped for {result['name']}: no line data.")
                    continue
                try:
                    repair = rtl.OutputRepair.from_file(source_json, keep_breaks=keep, mode=mode,
                                                        separators=separators)
                    changed = [p for p in files if repair.repair_file(p)]
                except (OSError, ValueError) as exc:
                    log.warning("RTL repair of %s failed: %s", result["name"], exc, exc_info=True)
                    self._report(job, f"RTL repair failed for {result['name']}: {exc}")
                    continue
                stats = repair.stats
                result["rtl"] = {"language": stats["language"], "elements": stats["elements"],
                                 "tables": stats["tables"], "files": len(changed)}
                if changed:
                    totals["files"] += 1
                    for key in ("elements", "tables", "approximate"):
                        totals[key] += int(stats[key])
                    if stats["language"] not in totals["languages"]:
                        totals["languages"].append(stats["language"])
                self._report(job, f"RTL repair of {result['name']} ({stats['language']}): {stats['elements']} "
                                  f"text element(s), {stats['tables']} table(s), {len(changed)} file(s) rewritten.")
        finally:
            remove_tree(tmp_root)
            job.phase = ""
            job.rtl = totals if totals["files"] else {}
            self.on_change()

    def _line_pass(self, job: Job, options: Dict[str, Any], sources: List[str], tmp_root: str) -> Dict[str, str]:
        """Run the CLI again to learn where every line of the RTL text ends.

        The second run uses the same options (so it finds the same elements)
        but writes only JSON, with ``--keep-line-breaks`` and without images,
        into a temporary folder. PDFs with the same file name go into separate
        calls because their outputs would overwrite each other.

        Args:
            job: The job.
            options: Options of the job (including the password).
            sources: Absolute paths of the PDFs to analyse.
            tmp_root: Temporary folder (deleted by the caller).

        Returns:
            ``{source_pdf: line_json_path}`` for every PDF that succeeded.
        """
        opts = {k: v for k, v in options.items() if k not in LINE_PASS_DROP and k not in GUI_OPTIONS}
        opts.update({"format": ["json"], "keep-line-breaks": True, "image-output": "off"})
        args, _ = self.build_option_args(opts)
        if job.hybrid_url and not opts.get("hybrid-url"):
            args.append(f"--hybrid-url={job.hybrid_url}")
        groups: List[List[str]] = []
        for source in sources:
            stem = os.path.splitext(os.path.basename(source))[0].lower()
            for group in groups:
                if stem not in {os.path.splitext(os.path.basename(s))[0].lower() for s in group}:
                    group.append(source)
                    break
            else:
                groups.append([source])
        base = [self.env.venv_python(), "-X", "utf8", "-m", "opendataloader_pdf"]
        found: Dict[str, str] = {}
        current = {"source": "", "out": tmp_root}

        def on_line(line: str) -> Optional[str]:
            """Map created JSON files to their PDF; keep only errors in the job log."""
            match = _LOG_RE.match(line)
            level, message = (match.group(1), match.group(2)) if match else ("", line)
            if message.startswith("File name: "):
                current["source"] = message[len("File name: "):].strip()
            elif message.startswith("Created ") and message.rstrip().lower().endswith(".json"):
                path = message[len("Created "):].strip()
                if current["source"]:
                    key = os.path.normcase(os.path.normpath(current["source"]))
                    found[key] = path if os.path.isabs(path) else os.path.join(current["out"], path)
            if level == "SEVERE" or line.startswith("Error"):  # warnings were shown by the first run
                return "[RTL] " + line
            return None

        self._report(job, f"RTL repair: reading the line structure of {len(sources)} file(s)...")
        for index, group in enumerate(groups):
            out = os.path.join(tmp_root, str(index))
            os.makedirs(out, exist_ok=True)
            current["out"] = out
            out_arg = f"--output-dir={out}"
            for batch in self._split_batches(group, args + [out_arg]):
                if job.cancel_event.is_set():
                    return found
                proc = ManagedProcess(base + batch + [out_arg] + args, log_buffer=job.get_log(),
                                      env=self.env.child_env(java=True), cwd=out, on_line=on_line)
                try:
                    proc.start()
                except OSError as exc:
                    self._report(job, f"RTL repair: could not start the CLI: {exc}")
                    return found
                job.proc = proc
                if job.cancel_event.is_set():
                    proc.cancel()
                proc.wait()
                job.proc = None
        result: Dict[str, str] = {}
        for source in sources:
            path = found.get(os.path.normcase(os.path.normpath(source)))
            if path and os.path.isfile(path):
                result[source] = path
        return result

    # ----------------------------------------------------------- log parsing
    def _parse_line(self, job: Job, line: str) -> Optional[str]:
        """Inspect one CLI log line: update progress, collapse repeats, filter noise.

        Args:
            job: The running job.
            line: Cleaned output line.

        Returns:
            The line to store, or ``None`` to drop it.
        """
        if line.startswith(("Picked up ", "NOTE: Picked up")) or _JUL_HEADER_RE.match(line):
            return None
        match = _LOG_RE.match(line)
        level, message = (match.group(1), match.group(2)) if match else ("", line)
        key = f"{level}:{message}"
        if key == job._last_key:
            job._repeat += 1
            return None
        self._flush_repeat(job)
        job._last_key = key
        if level == "WARNING":
            job.warnings += 1
        if message.startswith("File name: "):
            self._close_file(job)
            job.files_started += 1
            source = message[len("File name: "):].strip()
            job.current_file = os.path.basename(source)
            job.current_pages = None
            job.file_results.append({"name": job.current_file, "pages": None, "outputs": 0, "ok": None,
                                     "source": source, "files": []})
            self.on_change()
        elif message.startswith("Number of pages: "):
            try:
                job.current_pages = int(message.split(":", 1)[1].strip())
                job.pages_total += job.current_pages
                if job.file_results:
                    job.file_results[-1]["pages"] = job.current_pages
            except ValueError:
                pass
        elif message.startswith("Created "):
            path = message[len("Created "):].strip()
            if not os.path.isabs(path):
                path = os.path.normpath(os.path.join(job.output_dir, path))
            job.created_files.append(path)
            if job.file_results:
                job.file_results[-1]["outputs"] += 1
                job.file_results[-1].setdefault("files", []).append(path)
        elif level == "SEVERE" or line.startswith("Error:") or (
                level == "WARNING" and "not found" in message):
            job.errors.append(line)
            password = _PASSWORD_ERR_RE.match(line)
            if job.file_results and (password or level == "SEVERE"):
                job.file_results[-1]["ok"] = False
                job.file_results[-1]["error"] = "password" if password else "error"
            if "Could not connect to hybrid backend" in line:
                job.error = "hybrid_unavailable"
            elif "'java' command not found" in line:
                job.error = "java_missing"
        return line

    @staticmethod
    def _flush_repeat(job: Job) -> None:
        """Write a "repeated N times" note for collapsed identical lines."""
        if job._repeat:
            job.get_log().append(f"    ... (repeated {job._repeat} more time(s))")
            job._repeat = 0

    @staticmethod
    def _close_file(job: Job) -> None:
        """Finalize the result of the file that was processed last."""
        if job.file_results and job.file_results[-1]["ok"] is None:
            job.file_results[-1]["ok"] = job.file_results[-1]["outputs"] > 0
        job._last_key = ""

    # ---------------------------------------------------------------- finish
    def _after_finish(self, job: Job) -> None:
        """Optional actions after a job: open the folder, delete uploads."""
        if job.state in ("success", "partial") and self.settings.get("open_folder_on_finish"):
            try:
                open_in_file_manager(job.output_dir)
            except OSError:
                pass
        if job.state == "success" and self.settings.get("delete_uploads_after_job"):
            self.cleanup_uploads(only=[i["path"] for i in job.inputs if i.get("source") == "upload"])

    def _scan_outputs(self, job: Job) -> None:
        """Count the files and bytes produced by ``job``."""
        files = self.output_files(job, limit=20000)
        job.outputs_count = len(files)
        job.outputs_size = sum(f["size"] for f in files)

    # ---------------------------------------------------------------- outputs
    def output_files(self, job: Job, limit: int = 5000) -> List[Dict[str, Any]]:
        """List the files of a job's output folder.

        For the default job folder every file is listed. For a custom folder
        (which may contain unrelated files) only files created by this job and
        the files inside the image folders created alongside them are listed.

        Args:
            job: The job.
            limit: Maximum number of entries.

        Returns:
            ``[{"path": relative, "size", "mtime", "ext"}]`` sorted by path.
        """
        root = job.output_dir
        if not root or not os.path.isdir(root):
            return []
        result: List[Dict[str, Any]] = []

        def add(abs_path: str) -> None:
            """Append a file entry relative to the output folder."""
            try:
                stat = os.stat(abs_path)
            except OSError:
                return
            rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
            result.append({"path": rel, "size": stat.st_size, "mtime": stat.st_mtime,
                           "ext": os.path.splitext(abs_path)[1].lower().lstrip(".")})

        if job.output_mode == "job":
            for current, _dirs, files in os.walk(root):
                for name in files:
                    add(os.path.join(current, name))
                    if len(result) >= limit:
                        break
        else:
            seen = set()
            for path in job.created_files:
                if path in seen or not is_within(path, root) or not os.path.isfile(path):
                    continue
                seen.add(path)
                add(path)
                stem = os.path.splitext(os.path.basename(path))[0]
                for suffix in ("_annotated", "_tagged"):
                    stem = stem[: -len(suffix)] if stem.endswith(suffix) else stem
                images = os.path.join(os.path.dirname(path), f"{stem}_images")
                if os.path.isdir(images) and images not in seen:
                    seen.add(images)
                    for current, _dirs, files in os.walk(images):
                        for name in files:
                            add(os.path.join(current, name))
                if len(result) >= limit:
                    break
        result.sort(key=lambda f: f["path"].lower())
        return result[:limit]

    def resolve_output(self, job_id: str, rel_path: str) -> str:
        """Map a relative output path of a job to an absolute file path.

        Path traversal (``..``, absolute paths, symlinks leaving the folder) is
        rejected; for custom folders only files listed by :meth:`output_files`
        can be served.

        Raises:
            ApiError: ``not_found`` / ``forbidden``.
        """
        job = self.get(job_id)
        root = job.output_dir
        rel = rel_path.replace("\\", "/").lstrip("/")
        if not rel or any(part in ("..", "") for part in rel.split("/")) or ":" in rel:
            raise ApiError("forbidden", rel_path, 403)
        path = os.path.normpath(os.path.join(root, *rel.split("/")))
        if not is_within(path, root):
            raise ApiError("forbidden", rel_path, 403)
        if job.output_mode != "job" and rel not in {f["path"] for f in self.output_files(job)}:
            raise ApiError("forbidden", rel_path, 403)
        if not os.path.isfile(path):
            raise ApiError("not_found", rel_path, 404)
        return path

    def make_zip(self, job_id: str) -> str:
        """Create a ZIP archive of all output files of a job.

        Returns:
            Path of the temporary ZIP file (the caller deletes it after sending).
        """
        job = self.get(job_id)
        files = self.output_files(job, limit=100000)
        if not files:
            raise ApiError("no_outputs", job_id, 404)
        target = os.path.join(self.paths.tmp_dir, f"{job.id}-{secrets.token_hex(3)}.zip")
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for entry in files:
                zf.write(os.path.join(job.output_dir, *entry["path"].split("/")), entry["path"])
        return target

    # ----------------------------------------------------------------- uploads
    def save_upload(self, batch: str, rel_path: str, stream: Any, length: int) -> Dict[str, Any]:
        """Store a file uploaded from the browser (drag & drop).

        Args:
            batch: Client-generated batch id (groups files of one drop).
            rel_path: File name, optionally with a folder path for dropped folders.
            stream: Readable binary stream (the request body).
            length: Number of bytes to read.

        Returns:
            ``{"path": absolute_file_path, "size": bytes, "root": top_level_path}``.

        Raises:
            ApiError: ``bad_upload``.
        """
        if not _BATCH_RE.match(batch or ""):
            raise ApiError("bad_upload", "batch")
        parts = [p for p in str(rel_path or "").replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts or any(p == ".." or _CTRL_RE.search(p) or ":" in p for p in parts):
            raise ApiError("bad_upload", rel_path)
        if not parts[-1].lower().endswith(".pdf"):
            raise ApiError("input_not_pdf", parts[-1])
        if IS_WINDOWS:  # characters that are illegal in Windows file names
            parts = [re.sub(r'[<>"|?*]', "_", p).rstrip(" .") or "_" for p in parts]
        folder = os.path.join(self.paths.uploads_dir, batch, *parts[:-1])
        os.makedirs(folder, exist_ok=True)
        stem, ext = os.path.splitext(parts[-1])
        target = os.path.join(folder, parts[-1])
        counter = 2
        while os.path.exists(target):
            target = os.path.join(folder, f"{stem} ({counter}){ext}")
            counter += 1
        partial = target + ".part"
        remaining = length
        try:
            with open(partial, "wb") as fh:
                while remaining > 0:
                    chunk = stream.read(min(1048576, remaining))
                    if not chunk:
                        raise ApiError("bad_upload", "connection closed")
                    fh.write(chunk)
                    remaining -= len(chunk)
            os.replace(partial, target)
        except BaseException:
            try:
                os.remove(partial)
            except OSError:
                pass
            raise
        root = os.path.join(self.paths.uploads_dir, batch, parts[0]) if len(parts) > 1 else target
        return {"path": target, "size": length, "root": root}

    def cleanup_uploads(self, only: Optional[List[str]] = None) -> int:
        """Delete uploaded files that no active job needs.

        Args:
            only: Restrict the cleanup to these paths (``None`` = every upload).

        Returns:
            The number of removed top-level entries.
        """
        with self._lock:
            in_use = [i["path"] for j in self._jobs.values() if j.active for i in j.inputs]
        removed = 0
        candidates = only if only is not None else [
            os.path.join(self.paths.uploads_dir, n) for n in os.listdir(self.paths.uploads_dir)]
        for path in candidates:
            if not is_within(path, self.paths.uploads_dir) or _norm(path) == _norm(self.paths.uploads_dir):
                continue
            if any(is_within(p, path) or is_within(path, p) for p in in_use):
                continue
            if os.path.isdir(path):
                remove_tree(path)
            elif os.path.isfile(path):
                os.remove(path)
            else:
                continue
            removed += 1
            # Remove batch folders that became empty.
            parent = os.path.dirname(path)
            while _norm(parent) != _norm(self.paths.uploads_dir) and is_within(parent, self.paths.uploads_dir):
                try:
                    os.rmdir(parent)  # only succeeds for empty folders
                except OSError:
                    break
                parent = os.path.dirname(parent)
        return removed

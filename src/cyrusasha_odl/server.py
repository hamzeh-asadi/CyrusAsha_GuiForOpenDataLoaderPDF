"""
HTTP server: serves the web UI (``web/``) and a small JSON API.

Built on :mod:`http.server` (standard library). Design notes:

* **Local only by default** - the server binds to ``127.0.0.1``. While bound to
  a loopback address the ``Host`` header must be ``localhost``/``127.0.0.1``
  (blocks DNS-rebinding attacks).
* **CSRF protection** - every state-changing request (POST/PUT/DELETE) must
  carry the custom header ``X-CyrusAsha-ODL: 1``. Browsers never add custom headers
  to cross-site requests without a CORS pre-flight, which this server rejects.
* **Uploads** are sent as raw ``PUT`` bodies (no multipart parsing needed) and
  streamed straight to disk, so large PDFs never sit in memory.
* Generated HTML/SVG files are served with a sandboxing Content-Security-Policy
  so that a malicious PDF can never run scripts in the UI's origin.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple

from . import APP_ID, APP_NAME, CSRF_HEADER, USER_AGENT, __version__
from .app import App
from .config import LOCATION_SETTINGS
from .errors import ApiError
from .hybrid import check_health
from .jobs import inspect_path
from .platform_utils import (
    IS_WINDOWS,
    create_desktop_shortcut,
    native_pick,
    open_in_file_manager,
    reveal_file,
)
from .rules import test_rules, validate_rules
from .storage import check_target

log = logging.getLogger(__name__)

#: Registered API routes: ``(method, compiled_pattern, handler)``.
ROUTES: List[Tuple[str, Pattern[str], Callable[..., Any]]] = []

#: Maximum size of a JSON request body (rule files may be up to 1 MiB).
MAX_JSON_BODY = 4 * 1024 * 1024

#: Maximum size of one uploaded file (16 GiB - effectively unlimited locally).
MAX_UPLOAD = 16 * 1024 ** 3

_TEXT_TYPES = {
    ".html": "text/html", ".htm": "text/html", ".css": "text/css", ".js": "text/javascript",
    ".json": "application/json", ".md": "text/markdown", ".txt": "text/plain", ".svg": "image/svg+xml",
    ".log": "text/plain", ".csv": "text/csv",
}
_BINARY_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".ico": "image/x-icon", ".pdf": "application/pdf", ".zip": "application/zip",
    ".woff2": "font/woff2",
}
#: Policy for generated HTML/SVG: no scripts, no forms, unique origin.
_SANDBOX_CSP = ("sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
                "img-src * data: blob:; style-src * 'unsafe-inline'; font-src * data:; media-src * data: blob:")
#: Output files that may be opened with their default application.
_SAFE_OPEN_EXT = {".pdf", ".md", ".json", ".html", ".htm", ".txt", ".png", ".jpg", ".jpeg"}


def route(method: str, pattern: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that registers an API handler.

    Args:
        method: HTTP method (``GET``, ``POST``, ``PUT``, ``DELETE``).
        pattern: Regular expression for the path; named groups become keyword
            arguments of the handler.

    Returns:
        The decorator.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        """Store the handler in :data:`ROUTES` and return it unchanged."""
        ROUTES.append((method, re.compile("^" + pattern + "$"), func))
        return func
    return decorator


def content_type(path: str) -> str:
    """Return the Content-Type header value for a file name (UTF-8 for text)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _TEXT_TYPES:
        return _TEXT_TYPES[ext] + "; charset=utf-8"
    if ext in _BINARY_TYPES:
        return _BINARY_TYPES[ext]
    guessed = mimetypes.guess_type(path)[0]
    return guessed or "application/octet-stream"


def content_disposition(kind: str, filename: str) -> str:
    """Build a Content-Disposition header that supports non-ASCII (e.g. Persian) names.

    Args:
        kind: ``"inline"`` or ``"attachment"``.
        filename: File name to suggest.
    """
    fallback = re.sub(r'[^A-Za-z0-9._ -]', "_", filename) or "file"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{urllib.parse.quote(filename)}"


class GuiServer(ThreadingHTTPServer):
    """Threading HTTP server that knows the :class:`App` instance."""

    daemon_threads = True
    # On Windows SO_REUSEADDR would let a second instance bind the same port.
    allow_reuse_address = not IS_WINDOWS

    def __init__(self, address: Tuple[str, int], app: App, host_check: bool) -> None:
        """Bind the socket.

        Args:
            address: ``(host, port)``.
            app: The application object.
            host_check: Enforce loopback ``Host`` headers (DNS-rebinding protection).
        """
        self.app = app
        self.host_check = host_check
        super().__init__(address, Handler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Log unexpected errors; ignore clients that simply disconnected."""
        import sys

        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError)):
            log.debug("client %s disconnected: %s", client_address, error)
            return
        log.exception("error while handling a request from %s", client_address)


class Handler(BaseHTTPRequestHandler):
    """Request handler: dispatches API routes and serves static files."""

    server: GuiServer
    server_version = USER_AGENT
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ plumbing
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature from base class
        """Route the default access log to the debug level (keeps the console quiet)."""
        log.debug("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        """Handle GET requests."""
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        """Handle POST requests."""
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        """Handle PUT requests (uploads, saving rule files)."""
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        """Handle DELETE requests."""
        self._dispatch("DELETE")

    def _host_allowed(self) -> bool:
        """Check the ``Host`` (and ``Origin``) header when bound to loopback."""
        if not self.server.host_check:
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        if name not in ("localhost", "127.0.0.1", "[::1]"):
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            origin_host = urllib.parse.urlsplit(origin).netloc.lower()
            if origin_host != host:
                return False
        return True

    def _dispatch(self, method: str) -> None:
        """Validate the request, find the matching route and run it."""
        parsed = urllib.parse.urlsplit(self.path)
        self.path_only = urllib.parse.unquote(parsed.path)
        self.query: Dict[str, str] = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if not self._host_allowed():
                raise ApiError("forbidden_host", self.headers.get("Host", ""), 403)
            if method != "GET" and self.headers.get(CSRF_HEADER) != "1":
                raise ApiError("csrf", f"missing {CSRF_HEADER} header", 403)
            for route_method, pattern, func in ROUTES:
                if route_method != method:
                    continue
                match = pattern.match(self.path_only)
                if match:
                    result = func(self, self.server.app, **match.groupdict())
                    if result is not None:
                        self.send_json(result)
                    return
            if method == "GET" and not self.path_only.startswith("/api/"):
                self.serve_static(self.path_only)
                return
            raise ApiError("not_found", self.path_only, 404)
        except ApiError as exc:
            self.send_json({"error": exc.code, "detail": exc.detail}, exc.status)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the browser went away - nothing to report
        except Exception as exc:
            log.exception("request %s %s failed", method, self.path_only)
            try:
                self.send_json({"error": "internal", "detail": str(exc)}, 500)
            except OSError:
                pass

    # ---------------------------------------------------------- responses
    def send_json(self, data: Any, status: int = 200) -> None:
        """Send ``data`` as a UTF-8 JSON response.

        Args:
            data: JSON-serialisable value.
            status: HTTP status code.
        """
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: str, *, download: bool = False, sandbox: bool = False,
                  cache: bool = False, name: Optional[str] = None) -> None:
        """Stream a file to the browser.

        Args:
            path: Absolute file path (already validated by the caller).
            download: Force a download (``Content-Disposition: attachment``).
            sandbox: Add the sandboxing CSP (generated HTML/SVG).
            cache: Allow the browser to cache the response briefly.
            name: File name suggested to the browser (default: basename).
        """
        try:
            size = os.path.getsize(path)
            fh = open(path, "rb")
        except OSError as exc:
            raise ApiError("not_found", os.path.basename(path), 404) from exc
        with fh:
            self.send_response(200)
            self.send_header("Content-Type", content_type(path))
            self.send_header("Content-Length", str(size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "max-age=60" if cache else "no-cache")
            if download or name:
                self.send_header("Content-Disposition",
                                 content_disposition("attachment" if download else "inline",
                                                     name or os.path.basename(path)))
            if sandbox:
                self.send_header("Content-Security-Policy", _SANDBOX_CSP)
            self.end_headers()
            shutil.copyfileobj(fh, self.wfile, 262144)

    def serve_static(self, url_path: str) -> None:
        """Serve a file of the web UI (``web/``), defaulting to ``index.html``."""
        web_dir = self.server.app.paths.web_dir
        rel = url_path.lstrip("/") or "index.html"
        if rel == "favicon.ico":
            rel = "brand/favicon.png"
        full = os.path.normpath(os.path.join(web_dir, *rel.split("/")))
        if os.path.commonpath([full, web_dir]) != web_dir or not os.path.isfile(full):
            raise ApiError("not_found", url_path, 404)
        self.send_file(full)

    # -------------------------------------------------------------- input
    def json_body(self) -> Any:
        """Read and parse a JSON request body (empty body -> ``{}``).

        Raises:
            ApiError: ``bad_json`` / ``too_large``.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_JSON_BODY:
            raise ApiError("too_large", str(length), 413)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError("bad_json", str(exc)) from exc


# =========================================================================== #
# API routes                                                                  #
# =========================================================================== #
def _flag(handler: Handler, name: str) -> bool:
    """Return ``True`` if the query parameter ``name`` is ``1``/``true``."""
    return handler.query.get(name, "").lower() in ("1", "true", "yes")


def _since(handler: Handler) -> int:
    """Return the ``since`` query parameter as an integer (default 0)."""
    try:
        return int(handler.query.get("since", "0"))
    except ValueError:
        return 0


# ------------------------------------------------------------------- general
@route("GET", r"/api/ping")
def api_ping(h: Handler, app: App) -> Dict[str, Any]:
    """Identify this application (used for single-instance detection)."""
    return {"app": APP_ID, "version": __version__, "pid": os.getpid()}


@route("GET", r"/api/info")
def api_info(h: Handler, app: App) -> Dict[str, Any]:
    """Static information for the UI: version, folders and platform."""
    return {"app": APP_ID, "name": APP_NAME, "version": __version__, "paths": app.paths.to_dict(),
            "windows": IS_WINDOWS}


@route("GET", r"/api/state")
def api_state(h: Handler, app: App) -> Dict[str, Any]:
    """Compact application state polled by the UI (also proves that a UI window is open)."""
    app.last_seen = time.time()
    return app.state()


@route("GET", r"/api/env")
def api_env(h: Handler, app: App) -> Dict[str, Any]:
    """Full environment report (``?refresh=1`` re-detects everything)."""
    return app.env.summary(refresh=_flag(h, "refresh"))


@route("GET", r"/api/versions")
def api_versions(h: Handler, app: App) -> Dict[str, Any]:
    """Release versions of opendataloader-pdf on PyPI (newest first)."""
    pypi = app.env.pypi(refresh=_flag(h, "refresh"))
    return {"latest": pypi.get("latest"), "versions": pypi.get("versions", []), "error": pypi.get("error")}


@route("GET", r"/api/pythons")
def api_pythons(h: Handler, app: App) -> List[Dict[str, Any]]:
    """Python interpreters that can host the virtual environment."""
    return app.env.pythons(refresh=_flag(h, "refresh"))


@route("GET", r"/api/settings")
def api_settings(h: Handler, app: App) -> Dict[str, Any]:
    """All settings."""
    return app.settings.all()


@route("POST", r"/api/settings")
def api_settings_update(h: Handler, app: App) -> Dict[str, Any]:
    """Validate and save changed settings."""
    patch = h.json_body()
    if isinstance(patch, dict):
        for key in LOCATION_SETTINGS:
            if key in patch:  # files may have to move: use /api/storage/change
                raise ApiError("bad_setting", key)
    result = app.settings.update(patch)
    if any(k in patch for k in ("java_mode", "java_custom_path")):
        app.env.invalidate("java")
        app.env.warm_up()
    if "proxy" in patch:
        app.env.invalidate("pypi")
    if "max_parallel_jobs" in patch:
        app.jobs.kick()
    app.touch()
    return result


@route("GET", r"/api/logs/app")
def api_app_log(h: Handler, app: App) -> Dict[str, Any]:
    """Lines of the application log after ``?since=N``."""
    return app.app_log.read(_since(h))


@route("POST", r"/api/open")
def api_open(h: Handler, app: App) -> Dict[str, Any]:
    """Open one of the application folders in the file manager.

    Body: ``{"target": "data"|"logs"|"venv"|"java"|"uploads"|"rules"|"jobs"}``.
    """
    target = str(h.json_body().get("target", ""))
    folders = app.paths.to_dict()
    if target not in folders:
        raise ApiError("bad_target", target)
    path = folders[target]
    os.makedirs(path, exist_ok=True)
    open_in_file_manager(path)
    return {"ok": True, "path": path}


@route("POST", r"/api/pick")
def api_pick(h: Handler, app: App) -> Dict[str, Any]:
    """Show a native file/folder dialog on this computer.

    Body: ``{"kind": "files"|"folder"|"json"|"exe", "title": str, "initial": str}``.
    """
    body = h.json_body()
    kind = body.get("kind", "files")
    if kind not in ("files", "folder", "json", "exe"):
        raise ApiError("bad_kind", str(kind))
    result = native_pick(kind, str(body.get("title") or ""), str(body.get("initial") or ""))
    if result.get("error"):
        raise ApiError("picker_failed", str(result.get("detail") or result["error"]), 500)
    return result


@route("POST", r"/api/inspect")
def api_inspect(h: Handler, app: App) -> List[Dict[str, Any]]:
    """Describe typed or picked paths (exists? file/folder? number of PDFs?)."""
    paths = h.json_body().get("paths") or []
    if not isinstance(paths, list):
        raise ApiError("bad_json", "paths")
    return [inspect_path(str(p)) for p in paths[:500]]


@route("POST", r"/api/shortcut")
def api_shortcut(h: Handler, app: App) -> Dict[str, Any]:
    """Create a desktop shortcut that starts the GUI without a console window."""
    target = os.path.join(app.paths.app_dir, "windows", "start-silent.bat")
    icon = os.path.join(app.paths.app_dir, "windows", "icon.ico")
    try:
        path = create_desktop_shortcut(target, app.paths.app_dir, icon, APP_NAME)
    except RuntimeError as exc:
        raise ApiError("shortcut_failed", str(exc)) from exc
    return {"ok": True, "path": path}


@route("POST", r"/api/shutdown")
def api_shutdown(h: Handler, app: App) -> Dict[str, Any]:
    """Stop jobs and the hybrid server, then exit the application."""
    def later() -> None:
        """Shut down after the response has been sent."""
        time.sleep(0.3)
        app.shutdown()
        threading.Thread(target=h.server.shutdown, daemon=True).start()

    threading.Thread(target=later, daemon=True).start()
    return {"ok": True}


# ------------------------------------------------------------------- storage
@route("GET", r"/api/storage")
def api_storage(h: Handler, app: App) -> Dict[str, Any]:
    """Install and models folders: paths, warnings, free space and sizes (``?refresh=1``)."""
    return app.storage.report(refresh=_flag(h, "refresh"))


@route("POST", r"/api/storage/check")
def api_storage_check(h: Handler, app: App) -> Dict[str, Any]:
    """Check a folder the user wants to use. Body: ``{"kind": "install"|"models", "path": str}``."""
    body = h.json_body()
    kind = body.get("kind")
    if kind not in ("install", "models"):
        raise ApiError("bad_kind", str(kind))
    return check_target(app.paths, kind, str(body.get("path") or ""))


@route("POST", r"/api/storage/change")
def api_storage_change(h: Handler, app: App) -> Dict[str, Any]:
    """Move or switch the install/models folder (runs as an installer task).

    Body: ``{"kind": "install"|"models", "path": str ("" = default),
    "mode": "move"|"fresh", "delete_old": bool}``.
    """
    body = h.json_body()
    kind = body.get("kind")
    if kind not in ("install", "models"):
        raise ApiError("bad_kind", str(kind))
    mode = body.get("mode") or "move"
    if mode not in ("move", "fresh"):
        raise ApiError("bad_mode", str(mode))
    check = check_target(app.paths, kind, str(body.get("path") or ""))
    if not check["ok"]:
        raise ApiError("location_" + str(check["error"]), check["path"])
    if check["same"]:
        raise ApiError("location_same", check["path"])
    params = {"kind": kind, "path": check["setting"], "mode": mode,
              "delete_old": bool(body.get("delete_old")) and mode == "fresh"}
    return app.setup.start("relocate", params)


# --------------------------------------------------------------------- setup
@route("POST", r"/api/setup/start")
def api_setup_start(h: Handler, app: App) -> Dict[str, Any]:
    """Start an installer action. Body: ``{"action": str, "params": {...}}``."""
    body = h.json_body()
    return app.setup.start(str(body.get("action", "")), body.get("params") or {})


@route("POST", r"/api/setup/cancel")
def api_setup_cancel(h: Handler, app: App) -> Dict[str, Any]:
    """Cancel the running installer action."""
    return {"ok": app.setup.cancel()}


@route("GET", r"/api/setup/status")
def api_setup_status(h: Handler, app: App) -> Dict[str, Any]:
    """State of the current/last installer action."""
    return {"task": app.setup.status()}


@route("GET", r"/api/setup/log")
def api_setup_log(h: Handler, app: App) -> Dict[str, Any]:
    """Installer log lines after ``?since=N``."""
    return app.setup.read_log(_since(h))


# ------------------------------------------------------------------- options
@route("GET", r"/api/options")
def api_options(h: Handler, app: App) -> Dict[str, Any]:
    """CLI option specification (merged with the installed version's metadata)."""
    if _flag(h, "refresh"):
        app.env.invalidate("cli_options")
    return app.jobs.option_metadata()


@route("GET", r"/api/presets")
def api_presets(h: Handler, app: App) -> List[Dict[str, Any]]:
    """Built-in and user presets."""
    return app.presets.all()


@route("POST", r"/api/presets")
def api_presets_save(h: Handler, app: App) -> Dict[str, Any]:
    """Save a user preset. Body: ``{"name": str, "options": {...}, "server": {...}}``."""
    body = h.json_body()
    options = dict(body.get("options") or {})
    options.pop("password", None)  # never store passwords in presets
    app.jobs.build_option_args(options)  # validates
    return app.presets.save(str(body.get("name") or ""), options, body.get("server"))


@route("DELETE", r"/api/presets/(?P<preset_id>[A-Za-z0-9\-]+)")
def api_presets_delete(h: Handler, app: App, preset_id: str) -> Dict[str, Any]:
    """Delete a user preset."""
    app.presets.delete(preset_id)
    return {"ok": True}


# ------------------------------------------------------------------- uploads
@route("PUT", r"/api/upload")
def api_upload(h: Handler, app: App) -> Dict[str, Any]:
    """Receive one dragged file as raw body (``?batch=<id>&path=<relative path>``)."""
    length = int(h.headers.get("Content-Length") or -1)
    if length < 0:
        raise ApiError("bad_upload", "Content-Length required", 411)
    if length > MAX_UPLOAD:
        raise ApiError("too_large", str(length), 413)
    return app.jobs.save_upload(h.query.get("batch", ""), h.query.get("path", ""), h.rfile, length)


@route("POST", r"/api/uploads/clear")
def api_uploads_clear(h: Handler, app: App) -> Dict[str, Any]:
    """Delete uploaded files that no queued/running job needs."""
    return {"removed": app.jobs.cleanup_uploads()}


# ---------------------------------------------------------------------- jobs
@route("GET", r"/api/jobs")
def api_jobs(h: Handler, app: App) -> List[Dict[str, Any]]:
    """All jobs (newest first)."""
    return app.jobs.list()


@route("POST", r"/api/jobs")
def api_jobs_create(h: Handler, app: App) -> Dict[str, Any]:
    """Create and queue a conversion job."""
    return app.jobs.create(h.json_body())


@route("POST", r"/api/jobs/preview")
def api_jobs_preview(h: Handler, app: App) -> Dict[str, Any]:
    """Return the CLI command a job would run (validates the options)."""
    return app.jobs.preview(h.json_body())


@route("POST", r"/api/jobs/clear")
def api_jobs_clear(h: Handler, app: App) -> Dict[str, Any]:
    """Delete finished jobs. Body: ``{"states": ["success", ...]}``."""
    return {"removed": app.jobs.clear(h.json_body().get("states") or [])}


@route("GET", r"/api/jobs/(?P<job_id>[0-9a-f\-]+)")
def api_job(h: Handler, app: App, job_id: str) -> Dict[str, Any]:
    """Details of one job."""
    return app.jobs.get(job_id).to_dict(full=True)


@route("GET", r"/api/jobs/(?P<job_id>[0-9a-f\-]+)/log")
def api_job_log(h: Handler, app: App, job_id: str) -> Dict[str, Any]:
    """Job log lines after ``?since=N``."""
    return app.jobs.get(job_id).get_log().read(_since(h))


@route("GET", r"/api/jobs/(?P<job_id>[0-9a-f\-]+)/files")
def api_job_files(h: Handler, app: App, job_id: str) -> Dict[str, Any]:
    """Output files of a job."""
    job = app.jobs.get(job_id)
    return {"root": job.output_dir, "files": app.jobs.output_files(job)}


@route("POST", r"/api/jobs/(?P<job_id>[0-9a-f\-]+)/cancel")
def api_job_cancel(h: Handler, app: App, job_id: str) -> Dict[str, Any]:
    """Cancel a queued or running job."""
    return app.jobs.cancel(job_id)


@route("POST", r"/api/jobs/(?P<job_id>[0-9a-f\-]+)/rerun")
def api_job_rerun(h: Handler, app: App, job_id: str) -> Dict[str, Any]:
    """Queue a copy of a job."""
    return app.jobs.rerun(job_id)


@route("DELETE", r"/api/jobs/(?P<job_id>[0-9a-f\-]+)")
def api_job_delete(h: Handler, app: App, job_id: str) -> Dict[str, Any]:
    """Delete a finished job (and its default output folder)."""
    app.jobs.delete(job_id)
    return {"ok": True}


@route("POST", r"/api/jobs/(?P<job_id>[0-9a-f\-]+)/open")
def api_job_open(h: Handler, app: App, job_id: str) -> Dict[str, Any]:
    """Open the output folder, reveal an output file or open it with its default app.

    Body: ``{"path": relative_path (optional), "mode": "folder"|"reveal"|"file"}``.
    """
    body = h.json_body()
    job = app.jobs.get(job_id)
    rel = str(body.get("path") or "")
    mode = body.get("mode", "folder")
    if not rel:
        if not os.path.isdir(job.output_dir):
            raise ApiError("not_found", job.output_dir, 404)
        open_in_file_manager(job.output_dir)
        return {"ok": True}
    path = app.jobs.resolve_output(job_id, rel)
    if mode == "file":
        if os.path.splitext(path)[1].lower() not in _SAFE_OPEN_EXT:
            raise ApiError("forbidden", rel, 403)
        open_in_file_manager(path)
    else:
        reveal_file(path)
    return {"ok": True}


@route("GET", r"/api/jobs/(?P<job_id>[0-9a-f\-]+)/zip")
def api_job_zip(h: Handler, app: App, job_id: str) -> None:
    """Download all output files of a job as one ZIP archive."""
    job = app.jobs.get(job_id)
    archive = app.jobs.make_zip(job_id)
    try:
        label = re.sub(r'[\\/:*?"<>|]+', "_", job.name or job.id)
        h.send_file(archive, download=True, name=f"{label}.zip")
    finally:
        try:
            os.remove(archive)
        except OSError:
            pass


@route("GET", r"/files/(?P<job_id>[0-9a-f\-]+)/(?P<rel>.+)")
def files_route(h: Handler, app: App, job_id: str, rel: str) -> None:
    """Serve an output file; relative links inside HTML/Markdown keep working."""
    path = app.jobs.resolve_output(job_id, rel)
    ext = os.path.splitext(path)[1].lower()
    h.send_file(path, download=_flag(h, "download"), sandbox=ext in (".html", ".htm", ".svg"),
                name=os.path.basename(path) if ext == ".pdf" else None)


# -------------------------------------------------------------------- hybrid
@route("GET", r"/api/hybrid")
def api_hybrid(h: Handler, app: App) -> Dict[str, Any]:
    """Status of the managed hybrid server."""
    return app.hybrid.status()


@route("POST", r"/api/hybrid/start")
def api_hybrid_start(h: Handler, app: App) -> Dict[str, Any]:
    """Start the hybrid server. Body: ``{"config": {...}}`` (optional, saved first)."""
    return app.hybrid.start(h.json_body().get("config"))


@route("POST", r"/api/hybrid/stop")
def api_hybrid_stop(h: Handler, app: App) -> Dict[str, Any]:
    """Stop the hybrid server."""
    return app.hybrid.stop()


@route("POST", r"/api/hybrid/restart")
def api_hybrid_restart(h: Handler, app: App) -> Dict[str, Any]:
    """Restart the hybrid server (optionally with a new configuration)."""
    return app.hybrid.restart(h.json_body().get("config"))


@route("POST", r"/api/hybrid/preview")
def api_hybrid_preview(h: Handler, app: App) -> Dict[str, Any]:
    """Command line for a hybrid configuration (validates it)."""
    return app.hybrid.preview(h.json_body().get("config"))


@route("POST", r"/api/hybrid/check")
def api_hybrid_check(h: Handler, app: App) -> Dict[str, Any]:
    """Check whether a hybrid server answers at ``{"url": ...}``."""
    url = str(h.json_body().get("url") or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s]+$", url):
        raise ApiError("bad_url", url)
    return {"ok": check_health(url, timeout=4), "url": url}


@route("GET", r"/api/hybrid/log")
def api_hybrid_log(h: Handler, app: App) -> Dict[str, Any]:
    """Hybrid server log lines after ``?since=N``."""
    return app.hybrid.read_log(_since(h))


# --------------------------------------------------------------------- rules
@route("GET", r"/api/rules")
def api_rules(h: Handler, app: App) -> List[Dict[str, Any]]:
    """Stored sanitization rule files."""
    return app.rules.list()


@route("POST", r"/api/rules/test")
def api_rules_test(h: Handler, app: App) -> Dict[str, Any]:
    """Validate rules and apply them to a sample text. Body: ``{"data": {...}, "text": str}``."""
    body = h.json_body()
    return test_rules(validate_rules(body.get("data")), str(body.get("text") or ""))


@route("GET", r"/api/rules/(?P<name>[A-Za-z0-9_\-]+)")
def api_rules_get(h: Handler, app: App, name: str) -> Dict[str, Any]:
    """Content of one rule file."""
    return app.rules.get(name)


@route("PUT", r"/api/rules/(?P<name>[A-Za-z0-9_\-]+)")
def api_rules_save(h: Handler, app: App, name: str) -> Dict[str, Any]:
    """Validate and save a rule file. Body: ``{"rules": [...]}``."""
    return app.rules.save(name, h.json_body())


@route("DELETE", r"/api/rules/(?P<name>[A-Za-z0-9_\-]+)")
def api_rules_delete(h: Handler, app: App, name: str) -> Dict[str, Any]:
    """Delete a rule file."""
    app.rules.delete(name)
    return {"ok": True}


@route("GET", r"/api/rules/(?P<name>[A-Za-z0-9_\-]+)/download")
def api_rules_download(h: Handler, app: App, name: str) -> None:
    """Download a rule file."""
    h.send_file(app.rules.resolve(name), download=True)


# =========================================================================== #
def make_server(app: App, host: str, port: int, host_check: Optional[bool] = None) -> GuiServer:
    """Create (bind) the HTTP server.

    Args:
        app: Application object.
        host: Interface to bind (``127.0.0.1`` by default).
        port: TCP port.
        host_check: Override the automatic DNS-rebinding protection (enabled
            for loopback addresses).

    Returns:
        A bound :class:`GuiServer` (call ``serve_forever()``).
    """
    if host_check is None:
        host_check = host in ("127.0.0.1", "localhost", "::1")
    return GuiServer((host, port), app, host_check)

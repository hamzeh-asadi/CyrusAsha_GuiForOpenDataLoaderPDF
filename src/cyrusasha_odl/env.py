"""
Environment inspector.

Answers questions such as "Which Java will be used?", "Is opendataloader-pdf
installed in our venv, and which version?", "Is a newer version on PyPI?" and
builds the environment variables for every child process.

Detection involves starting external programs (``java -version``, the venv
interpreter, ...), which takes a moment, so all results are cached and only
refreshed on request or after the installer changed something.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from . import USER_AGENT
from .config import Paths, Settings
from .platform_utils import (
    IS_WINDOWS,
    append_path,
    build_base_env,
    discover_pythons,
    find_java_in_dir,
    java_exe_name,
    os_description,
    prepend_path,
    probe_java,
    probe_python,
    run_capture,
    system_java_candidates,
    tesseract_dir,
    venv_python,
)
from .storage import install_env, model_env

log = logging.getLogger(__name__)

#: Name of the package on PyPI.
PACKAGE = "opendataloader-pdf"

#: Format of java.util.logging records: one line per record ("16:06:12 INFO: ...").
JAVA_LOG_FORMAT = "%1$tH:%1$tM:%1$tS %4$s: %5$s%6$s%n"

_PKG_PROBE = r"""
import json, sys
import importlib.metadata as md
names = ["opendataloader-pdf", "docling", "docling-parse", "torch", "fastapi", "uvicorn",
         "easyocr", "rapidocr", "onnxruntime", "tesserocr", "ocrmac", "pip"]
out = {}
for n in names:
    try:
        out[n] = md.version(n)
    except Exception:
        out[n] = None
out["_python"] = "%d.%d.%d" % sys.version_info[:3]
print(json.dumps(out))
"""

_CLI_PROBE = (
    "import json\n"
    "from opendataloader_pdf.cli_options_generated import CLI_OPTIONS\n"
    "print(json.dumps(CLI_OPTIONS))\n"
)


def version_key(version: Optional[str]) -> tuple:
    """Turn ``"2.5.21"`` into a comparable tuple ``(2, 5, 21)``.

    Args:
        version: Version string (``None`` sorts first).

    Returns:
        A tuple of integers.
    """
    if not version:
        return ()
    return tuple(int(part) for part in re.findall(r"\d+", version)[:4])


def is_final_release(version: str) -> bool:
    """Return ``True`` for plain releases (no alpha/beta/rc/dev/post tags)."""
    return bool(re.fullmatch(r"\d+(\.\d+)*", version))


def java_tool_options(max_memory_mb: int = 0) -> str:
    """Build the ``JAVA_TOOL_OPTIONS`` value used for every conversion.

    * English locale -> stable, parseable log messages (INFO/WARNING/SEVERE).
    * UTF-8 for files and console streams -> Persian file names in logs.
    * One-line log format -> compact log view and simple progress parsing.
    * Optional maximum heap size for very large documents.

    Args:
        max_memory_mb: ``-Xmx`` value in MB (0 = JVM default).

    Returns:
        The option string (quoted where the JVM requires it).
    """
    options = [
        "-Duser.language=en",
        "-Duser.country=US",
        "-Dfile.encoding=UTF-8",
        "-Dstdout.encoding=UTF-8",
        "-Dstderr.encoding=UTF-8",
        "-Dsun.stdout.encoding=UTF-8",
        "-Dsun.stderr.encoding=UTF-8",
        f'"-Djava.util.logging.SimpleFormatter.format={JAVA_LOG_FORMAT}"',
    ]
    if max_memory_mb and max_memory_mb > 0:
        options.append(f"-Xmx{int(max_memory_mb)}m")
    return " ".join(options)


class EnvInspector:
    """Collects, caches and reports information about the runtime environment."""

    def __init__(self, paths: Paths, settings: Settings) -> None:
        """Create the inspector.

        Args:
            paths: Application paths.
            settings: Persistent settings (Java mode, proxy, ...).
        """
        self.paths = paths
        self.settings = settings
        self._lock = threading.RLock()
        self._cache: Dict[str, tuple] = {}
        self._warming = False

    # ------------------------------------------------------------------ cache
    def _cached(self, key: str, ttl: float, producer: Callable[[], Any], refresh: bool = False) -> Any:
        """Return a cached value or compute (and store) a fresh one.

        Args:
            key: Cache key.
            ttl: Time to live in seconds.
            producer: Zero-argument function computing the value.
            refresh: Ignore the cache.

        Returns:
            The (possibly cached) value.
        """
        with self._lock:
            hit = self._cache.get(key)
        if hit and not refresh and time.time() - hit[0] < ttl:
            return hit[1]
        value = producer()
        with self._lock:
            self._cache[key] = (time.time(), value)
        return value

    def _peek(self, key: str) -> Any:
        """Return a cached value without computing it (``None`` if missing)."""
        with self._lock:
            hit = self._cache.get(key)
        return hit[1] if hit else None

    def invalidate(self, *keys: str) -> None:
        """Forget cached values.

        Args:
            keys: Keys to forget; no keys = forget everything.
        """
        with self._lock:
            if not keys:
                self._cache.clear()
            for key in keys:
                self._cache.pop(key, None)

    def warm_up(self) -> None:
        """Fill the most important caches on a background thread."""
        if self._warming:
            return
        self._warming = True

        def work() -> None:
            """Background thread body."""
            try:
                self.java()
                self.package()
                self.pypi()
            except Exception:  # never crash the thread
                log.exception("environment warm-up failed")
            finally:
                self._warming = False

        threading.Thread(target=work, name="env-warmup", daemon=True).start()

    # ----------------------------------------------------------------- python
    def venv_python(self) -> str:
        """Path of the interpreter inside ``data/venv``."""
        return venv_python(self.paths.venv_dir)

    def venv_exists(self) -> bool:
        """``True`` if the virtual environment has been created."""
        return os.path.isfile(self.venv_python())

    def pythons(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """List Python interpreters that can be used to build the venv.

        Args:
            refresh: Re-scan instead of using the cache.
        """
        return self._cached(
            "pythons", 600, lambda: discover_pythons(exclude_dirs=[self.paths.data_dir, self.paths.install_dir]),
            refresh
        )

    def pick_python(self) -> Optional[str]:
        """Choose the interpreter for a new venv.

        The configured interpreter wins; otherwise the newest supported
        (>= 3.11, 64-bit preferred) interpreter is used.

        Returns:
            Path to the interpreter or ``None`` if none qualifies.
        """
        configured = self.settings.get("python_path")
        if configured and os.path.isfile(configured):
            info = probe_python(configured)
            if info and info["supported"]:
                return configured
        supported = [p for p in self.pythons() if p["supported"]]
        supported.sort(key=lambda p: (p["bits"] == 64, p["source"] == "gui", list(p["version_tuple"])), reverse=True)
        return supported[0]["path"] if supported else None

    # ------------------------------------------------------------------- java
    def java(self, refresh: bool = False) -> Dict[str, Any]:
        """Return the Java runtime that conversions will use.

        Args:
            refresh: Re-detect instead of using the cache.

        Returns:
            ``{"ok", "mode", "selected", "candidates", "error"}``.
        """
        return self._cached("java", 300, self._detect_java, refresh)

    def _detect_java(self) -> Dict[str, Any]:
        """Probe custom, portable and system Java installations."""
        mode = self.settings.get("java_mode")
        candidates: List[Dict[str, Any]] = []

        custom = self.settings.get("java_custom_path")
        if custom:
            path = custom
            if os.path.isdir(path):
                path = find_java_in_dir(path, 3) or os.path.join(path, "bin", java_exe_name())
            info = probe_java(path) if os.path.isfile(path) else None
            if info:
                info["source"] = "custom"
                candidates.append(info)
            else:
                candidates.append({"path": path, "ok": False, "source": "custom", "error": "not_found"})

        if os.path.isdir(self.paths.java_dir):
            for name in sorted(os.listdir(self.paths.java_dir), reverse=True):
                folder = os.path.join(self.paths.java_dir, name)
                if not os.path.isdir(folder) or name.startswith("_"):
                    continue
                exe = find_java_in_dir(folder)
                info = probe_java(exe) if exe else None
                if info:
                    info["source"] = "portable"
                    candidates.append(info)

        for path, source in system_java_candidates(build_base_env()):
            info = probe_java(path)
            if info:
                info["source"] = source
                candidates.append(info)

        def pick(sources: Optional[tuple] = None, exclude: tuple = ()) -> Optional[Dict[str, Any]]:
            """Return the newest usable candidate from the given sources."""
            usable = [
                c for c in candidates
                if c.get("ok") and (sources is None or c["source"] in sources) and c["source"] not in exclude
            ]
            usable.sort(key=lambda c: c.get("major", 0), reverse=True)
            return usable[0] if usable else None

        if mode == "custom":
            selected = pick(("custom",))
        elif mode == "portable":
            selected = pick(("portable",))
        elif mode == "system":
            selected = pick(exclude=("portable", "custom"))
        else:
            selected = pick(("portable",)) or pick(exclude=("custom",))

        error = None
        if not selected:
            too_old = [c for c in candidates if c.get("major") and not c.get("ok")]
            error = "java_too_old" if too_old else "java_missing"
        return {"ok": selected is not None, "mode": mode, "selected": selected,
                "candidates": candidates, "error": error}

    # ---------------------------------------------------------------- package
    def package(self, refresh: bool = False) -> Dict[str, Any]:
        """Return versions of opendataloader-pdf and related packages in the venv.

        Args:
            refresh: Re-check instead of using the cache.

        Returns:
            ``{"installed", "version", "hybrid", "python", "packages": {...}}``.
        """
        return self._cached("package", 600, self._detect_package, refresh)

    def _detect_package(self) -> Dict[str, Any]:
        """Run a short script inside the venv to read package metadata."""
        empty = {"installed": False, "version": None, "hybrid": False, "python": None, "packages": {}}
        if not self.venv_exists():
            return dict(empty, venv=False)
        code, out = run_capture([self.venv_python(), "-c", _PKG_PROBE], timeout=60, env=build_base_env())
        data: Dict[str, Any] = {}
        for line in reversed(out.strip().splitlines()):
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
        if code != 0 or not data:
            return dict(empty, venv=True, error=out.strip()[-400:])
        version = data.get(PACKAGE)
        hybrid = bool(data.get("docling") and data.get("fastapi") and data.get("uvicorn"))
        python = data.pop("_python", None)
        return {"installed": bool(version), "version": version, "hybrid": hybrid,
                "python": python, "packages": data, "venv": True}

    def pip_supports_raw_progress(self) -> bool:
        """``True`` if the venv's pip understands ``--progress-bar raw`` (pip >= 24.1)."""
        pip_version = (self.package().get("packages") or {}).get("pip")
        return version_key(pip_version) >= (24, 1)

    def cli_options(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """Return the CLI option metadata shipped with the installed package.

        The package contains ``cli_options_generated.CLI_OPTIONS``; reading it
        lets the UI show options that were added in newer versions, without
        updating the GUI.

        Args:
            refresh: Re-read instead of using the cache.
        """
        def produce() -> List[Dict[str, Any]]:
            """Read the metadata from the venv."""
            if not self.venv_exists():
                return []
            code, out = run_capture([self.venv_python(), "-c", _CLI_PROBE], timeout=60, env=build_base_env())
            for line in reversed(out.strip().splitlines()):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, list):
                    return [o for o in data if isinstance(o, dict) and o.get("name")]
            return []

        return self._cached("cli_options", 3600, produce, refresh)

    # ------------------------------------------------------------------ pypi
    def url_opener(self) -> urllib.request.OpenerDirector:
        """Return a urllib opener that honours the proxy setting.

        Without a configured proxy the system proxy (environment / Windows
        Internet settings) is used, exactly like a browser would.
        """
        proxy = self.settings.get("proxy")
        if proxy:
            return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        return urllib.request.build_opener()

    def pypi(self, refresh: bool = False) -> Dict[str, Any]:
        """Return the latest version and the release list from PyPI.

        Args:
            refresh: Ask PyPI again instead of using the 30-minute cache.

        Returns:
            ``{"latest", "versions", "error"}`` (never raises).
        """
        def produce() -> Dict[str, Any]:
            """Query the PyPI JSON API."""
            try:
                request = urllib.request.Request(
                    f"https://pypi.org/pypi/{PACKAGE}/json",
                    headers={"User-Agent": USER_AGENT},
                )
                with self.url_opener().open(request, timeout=12) as response:
                    data = json.load(response)
                versions = [v for v, files in data.get("releases", {}).items() if files and is_final_release(v)]
                versions.sort(key=version_key, reverse=True)
                return {"latest": data.get("info", {}).get("version"), "versions": versions[:80], "error": None}
            except Exception as exc:  # offline, blocked, proxy problems...
                return {"latest": None, "versions": [], "error": str(exc)[:300]}

        return self._cached("pypi", 1800, produce, refresh)

    # ------------------------------------------------------- child processes
    def child_env(self, *, java: bool = True, network: bool = False, install: bool = False) -> Dict[str, str]:
        """Build the environment for a child process.

        Model downloads (Hugging Face, EasyOCR, docling, ModelScope, torch)
        always go to the models folder chosen by the user (see :mod:`.storage`).

        Args:
            java: Put the selected Java first on PATH, set ``JAVA_HOME`` and the
                logging/encoding ``JAVA_TOOL_OPTIONS``.
            network: Export the configured proxy (pip, model downloads).
            install: Keep pip's download cache and temporary files in the
                install folder (``PIP_CACHE_DIR``, ``TMP``/``TEMP``).

        Returns:
            A new environment dictionary.
        """
        env = build_base_env()
        env.update(model_env(self.paths.models_dir))
        if install:
            env.update(install_env(self.paths))
        if java:
            selected = self.java().get("selected")
            if selected:
                prepend_path(env, os.path.dirname(selected["path"]))
                env["JAVA_HOME"] = selected["home"]
            existing = env.get("JAVA_TOOL_OPTIONS", "").strip()
            extra = java_tool_options(int(self.settings.get("jvm_max_memory_mb") or 0))
            env["JAVA_TOOL_OPTIONS"] = f"{existing} {extra}".strip()
        folder = tesseract_dir()
        if folder:
            append_path(env, folder)
        no_proxy = [p for p in env.get("NO_PROXY", env.get("no_proxy", "")).split(",") if p.strip()]
        for host in ("localhost", "127.0.0.1", "::1"):
            if host not in no_proxy:
                no_proxy.append(host)
        env["NO_PROXY"] = ",".join(no_proxy)
        if network:
            proxy = self.settings.get("proxy")
            if proxy:
                env["HTTP_PROXY"] = proxy
                env["HTTPS_PROXY"] = proxy
                if not IS_WINDOWS:  # Windows variables are case-insensitive
                    env["http_proxy"] = proxy
                    env["https_proxy"] = proxy
                    env["no_proxy"] = env["NO_PROXY"]
        return env

    # -------------------------------------------------------------- summaries
    def _node_version(self) -> Optional[str]:
        """Return the Node.js version (informational only)."""
        def produce() -> Optional[str]:
            """Run ``node --version``."""
            if not shutil.which("node"):
                return None
            code, out = run_capture(["node", "--version"], timeout=10)
            return out.strip() if code == 0 else None

        return self._cached("node", 3600, produce)

    def summary(self, refresh: bool = False) -> Dict[str, Any]:
        """Return the complete environment report shown on the Setup page.

        Args:
            refresh: Re-detect everything.
        """
        java = self.java(refresh)
        package = self.package(refresh)
        pypi = self.pypi(refresh)
        latest = pypi.get("latest")
        installed = package.get("version")
        return {
            "os": os_description(),
            "gui_python": {"path": sys.executable, "version": ".".join(map(str, sys.version_info[:3]))},
            "paths": self.paths.to_dict(),
            "venv": {"exists": self.venv_exists(), "path": self.paths.venv_dir,
                     "python": package.get("python"), "interpreter": self.venv_python()},
            "package": dict(package, latest=latest,
                            update_available=bool(installed and latest and version_key(latest) > version_key(installed))),
            "pypi_error": pypi.get("error"),
            "java": java,
            "winget": bool(IS_WINDOWS and shutil.which("winget")),
            "tesseract": tesseract_dir(),
            "node": self._node_version(),
            "windows": IS_WINDOWS,
        }

    def brief(self) -> Dict[str, Any]:
        """Return a cheap status summary for the header (never blocks).

        Missing cache entries trigger a background refresh; until then the
        result contains ``"loading": True``.
        """
        java = self._peek("java")
        package = self._peek("package")
        pypi = self._peek("pypi") or {}
        if java is None or package is None:
            self.warm_up()
            return {"loading": True}
        latest = pypi.get("latest")
        installed = package.get("version")
        selected = java.get("selected") or {}
        return {
            "loading": False,
            "java_ok": bool(java.get("ok")),
            "java_version": selected.get("version"),
            "java_source": selected.get("source"),
            "venv": self.venv_exists(),
            "installed": bool(package.get("installed")),
            "version": installed,
            "hybrid": bool(package.get("hybrid")),
            "latest": latest,
            "update_available": bool(installed and latest and version_key(latest) > version_key(installed)),
        }

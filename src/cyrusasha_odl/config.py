"""
Paths, persistent settings and conversion presets.

Everything the application creates lives below one *data* folder (``data/`` in
the project root by default), so the whole installation is portable and can be
removed by deleting a single folder::

    data/
      settings.json      user settings (this module)
      presets.json       user-defined conversion presets
      venv/              Python virtual environment with opendataloader-pdf  ┐
      java/              portable Java runtime(s) downloaded by the installer │ install folder
      cache/             pip download cache and pip's temporary files       ┘
      models/            models downloaded by the hybrid server               - models folder
      jobs/<id>/         job.json, log.txt and (by default) output/
      uploads/<batch>/   PDFs that were dragged into the browser
      rules/             sanitization rule files (JSON)
      logs/              application, installer and hybrid-server logs
      tmp/               temporary downloads and self-test files

The *install folder* (``venv``, ``java``, ``cache``) and the *models folder*
can be moved elsewhere by the user (settings ``install_dir`` and
``models_dir``, see :mod:`.storage`); everything else stays in ``data/``.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from . import ENV_PREFIX
from .errors import ApiError

#: Project root (``src/cyrusasha_odl/config.py`` -> three levels up).
APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Folder with the static web UI (HTML, CSS, JavaScript, brand images).
WEB_DIR = os.path.join(APP_DIR, "src", "gui", "public")


def write_json_atomic(path: str, data: Any) -> None:
    """Write JSON to ``path`` atomically (temporary file + rename).

    A crash or power loss while saving can therefore never leave a truncated
    settings or job file behind.

    Args:
        path: Target file.
        data: JSON-serialisable object.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: file briefly locked by a virus scanner
            time.sleep(0.1 * (attempt + 1))
    os.replace(tmp, path)


def read_json(path: str, default: Any) -> Any:
    """Read a JSON file, returning ``default`` when it is missing or broken.

    Args:
        path: File to read.
        default: Value returned on any error.

    Returns:
        The parsed JSON value or ``default``.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


class Paths:
    """All folders and files used by the application."""

    def __init__(self, data_dir: Optional[str] = None) -> None:
        """Compute the paths.

        Args:
            data_dir: Data folder. Defaults to ``$CYRUSASHA_ODL_DATA`` or ``<project>/data``.
        """
        self.app_dir = APP_DIR
        self.web_dir = WEB_DIR
        self.data_dir = os.path.abspath(
            data_dir or os.environ.get(ENV_PREFIX + "DATA") or os.path.join(APP_DIR, "data")
        )
        self.apply_locations("", "")
        self.jobs_dir = os.path.join(self.data_dir, "jobs")
        self.uploads_dir = os.path.join(self.data_dir, "uploads")
        self.rules_dir = os.path.join(self.data_dir, "rules")
        self.logs_dir = os.path.join(self.data_dir, "logs")
        self.tmp_dir = os.path.join(self.data_dir, "tmp")
        self.settings_file = os.path.join(self.data_dir, "settings.json")
        self.presets_file = os.path.join(self.data_dir, "presets.json")

    def locations(self, install_dir: str = "", models_dir: str = "") -> Dict[str, Any]:
        """Compute the install and models folders for the given settings (nothing is changed).

        Args:
            install_dir: Setting ``install_dir`` (``""`` = the data folder).
            models_dir: Setting ``models_dir`` (``""`` = ``<install folder>/models``).

        Returns:
            ``{"install", "models", "venv", "java", "cache", "install_default", "models_default"}``.
        """
        install = os.path.abspath(install_dir) if install_dir else self.data_dir
        models = os.path.abspath(models_dir) if models_dir else os.path.join(install, "models")
        return {"install": install, "models": models, "venv": os.path.join(install, "venv"),
                "java": os.path.join(install, "java"), "cache": os.path.join(install, "cache"),
                "install_default": not install_dir, "models_default": not models_dir}

    def apply_locations(self, install_dir: str, models_dir: str) -> None:
        """Switch to new install/models folders (after loading or changing the settings).

        Args:
            install_dir: Setting ``install_dir``.
            models_dir: Setting ``models_dir``.
        """
        loc = self.locations(install_dir, models_dir)
        self.install_dir = loc["install"]
        self.models_dir = loc["models"]
        self.install_default = loc["install_default"]
        self.models_default = loc["models_default"]
        self.venv_dir = loc["venv"]
        self.java_dir = loc["java"]
        self.cache_dir = loc["cache"]
        self.pip_cache_dir = os.path.join(self.cache_dir, "pip")
        self.install_tmp_dir = os.path.join(self.cache_dir, "tmp")

    def ensure(self) -> None:
        """Create every folder of the data folder that does not exist yet.

        The install and models folders are created when something is
        installed into them - they may be on a drive that is not connected.
        """
        for folder in (self.data_dir, self.jobs_dir, self.uploads_dir, self.rules_dir, self.logs_dir, self.tmp_dir):
            os.makedirs(folder, exist_ok=True)

    def to_dict(self) -> Dict[str, str]:
        """Return the most relevant paths for display in the UI (and as targets of ``/api/open``)."""
        return {
            "app": self.app_dir,
            "data": self.data_dir,
            "install": self.install_dir,
            "models": self.models_dir,
            "venv": self.venv_dir,
            "java": self.java_dir,
            "cache": self.cache_dir,
            "jobs": self.jobs_dir,
            "uploads": self.uploads_dir,
            "rules": self.rules_dir,
            "logs": self.logs_dir,
        }


# --------------------------------------------------------------------------- #
# Hybrid server configuration                                                 #
# --------------------------------------------------------------------------- #
#: Default options of the managed ``opendataloader-pdf-hybrid`` server.
HYBRID_DEFAULTS: Dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 5002,
    "ocr_mode": "default",          # default | force | off
    "ocr_engine": "easyocr",
    "ocr_lang": "",
    "psm": "",
    "enrich_formula": False,
    "enrich_picture_description": False,
    "picture_description_prompt": "",
    "picture_area_threshold": 0.0,
    "heading_hierarchy": False,
    "device": "auto",               # auto | cpu | cuda | mps | xpu
    "max_file_size": 0,
    "log_level": "info",            # debug | info | warning | error
}

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")
_ENGINE_RE = re.compile(r"^[a-z0-9_\-]{1,40}$")
_LANG_RE = re.compile(r"^[A-Za-z0-9_,\- ]{0,200}$")


def _as_bool(value: Any) -> bool:
    """Interpret JSON/strings such as ``"true"``/``"1"`` as booleans."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def sanitize_hybrid_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and normalise a hybrid-server configuration.

    Unknown keys are dropped and invalid values raise :class:`ApiError`, so a
    configuration that passes this function can safely be turned into command
    line arguments.

    Args:
        raw: Configuration coming from the UI or from ``settings.json``.

    Returns:
        A complete configuration (missing keys get their defaults).

    Raises:
        ApiError: ``bad_hybrid_config`` with the offending key as detail.
    """
    cfg = dict(HYBRID_DEFAULTS)
    raw = raw or {}

    def fail(key: str) -> None:
        """Raise the validation error for ``key``."""
        raise ApiError("bad_hybrid_config", key)

    if "host" in raw:
        host = str(raw["host"]).strip() or "127.0.0.1"
        if not _HOST_RE.match(host):
            fail("host")
        cfg["host"] = host
    if "port" in raw:
        try:
            port = int(raw["port"])
        except (TypeError, ValueError):
            port = -1
        if not 1 <= port <= 65535:
            fail("port")
        cfg["port"] = port
    if "ocr_mode" in raw:
        if raw["ocr_mode"] not in ("default", "force", "off"):
            fail("ocr_mode")
        cfg["ocr_mode"] = raw["ocr_mode"]
    if "ocr_engine" in raw:
        engine = str(raw["ocr_engine"]).strip() or "easyocr"
        if not _ENGINE_RE.match(engine):
            fail("ocr_engine")
        cfg["ocr_engine"] = engine
    if "ocr_lang" in raw:
        lang = str(raw["ocr_lang"]).strip()
        if not _LANG_RE.match(lang):
            fail("ocr_lang")
        cfg["ocr_lang"] = lang
    if "psm" in raw:
        psm = str(raw["psm"]).strip()
        if psm and (not psm.isdigit() or not 0 <= int(psm) <= 13):
            fail("psm")
        cfg["psm"] = psm
    for key in ("enrich_formula", "enrich_picture_description", "heading_hierarchy"):
        if key in raw:
            cfg[key] = _as_bool(raw[key])
    if "picture_description_prompt" in raw:
        prompt = str(raw["picture_description_prompt"] or "").strip()
        if len(prompt) > 2000:
            fail("picture_description_prompt")
        cfg["picture_description_prompt"] = prompt
    if "picture_area_threshold" in raw:
        try:
            threshold = float(raw["picture_area_threshold"] or 0)
        except (TypeError, ValueError):
            threshold = -1.0
        if not 0.0 <= threshold <= 1.0:
            fail("picture_area_threshold")
        cfg["picture_area_threshold"] = threshold
    if "device" in raw:
        if raw["device"] not in ("auto", "cpu", "cuda", "mps", "xpu"):
            fail("device")
        cfg["device"] = raw["device"]
    if "max_file_size" in raw:
        try:
            size = int(raw["max_file_size"] or 0)
        except (TypeError, ValueError):
            size = -1
        if size < 0:
            fail("max_file_size")
        cfg["max_file_size"] = size
    if "log_level" in raw:
        if raw["log_level"] not in ("debug", "info", "warning", "error"):
            fail("log_level")
        cfg["log_level"] = raw["log_level"]
    return cfg


# --------------------------------------------------------------------------- #
# Settings                                                                    #
# --------------------------------------------------------------------------- #
DEFAULT_SETTINGS: Dict[str, Any] = {
    "language": "fa",                 # fa | en
    "theme": "auto",                  # auto | light | dark
    "ui_open_mode": "app",            # app (Edge/Chrome window) | browser
    "python_path": "",                # interpreter used to create the venv ("" = automatic)
    "java_mode": "auto",              # auto | portable | system | custom
    "java_custom_path": "",           # java(.exe) or a JAVA_HOME folder
    "java_portable_version": 21,      # 17 | 21 | 25 (Eclipse Temurin LTS)
    "java_download_url": "",          # optional mirror for the portable JRE archive
    "jvm_max_memory_mb": 0,           # 0 = JVM default heap size
    "max_parallel_jobs": 1,
    "default_output_dir": "",         # "" = data/jobs/<id>/output
    "open_folder_on_finish": False,
    "notify_on_finish": True,
    "delete_uploads_after_job": False,
    "pip_index_url": "",
    "pip_extra_index_url": "",
    "pip_trusted_host": "",
    "proxy": "",
    "hybrid_auto_start": True,
    "hybrid_external_url": "",
    "hybrid_server": dict(HYBRID_DEFAULTS),
    "install_dir": "",                # venv + java + pip cache ("" = the data folder)
    "models_dir": "",                 # downloaded AI models ("" = <install folder>/models)
}

#: Settings that are only changed through the storage API (files may have to move).
LOCATION_SETTINGS = ("install_dir", "models_dir")

_URL_RE = re.compile(r"^(https?|socks5h?)://[^\s]+$", re.IGNORECASE)


def _validate_setting(key: str, value: Any) -> Any:
    """Validate one setting and return its normalised value.

    Args:
        key: Setting name (must exist in :data:`DEFAULT_SETTINGS`).
        value: New value coming from the UI.

    Returns:
        The normalised value.

    Raises:
        ApiError: ``bad_setting`` with the key as detail.
    """
    def fail() -> None:
        """Raise the validation error for the current key."""
        raise ApiError("bad_setting", key)

    choices = {
        "language": ("fa", "en"),
        "theme": ("auto", "light", "dark"),
        "ui_open_mode": ("app", "browser"),
        "java_mode": ("auto", "portable", "system", "custom"),
    }
    if key in choices:
        if value not in choices[key]:
            fail()
        return value
    if key == "java_portable_version":
        try:
            version = int(value)
        except (TypeError, ValueError):
            version = 0
        if version not in (17, 21, 25):
            fail()
        return version
    if key in ("jvm_max_memory_mb", "max_parallel_jobs"):
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = -1
        limits = {"jvm_max_memory_mb": (0, 262144), "max_parallel_jobs": (1, 8)}[key]
        if not limits[0] <= number <= limits[1]:
            fail()
        return number
    if key in ("open_folder_on_finish", "notify_on_finish", "delete_uploads_after_job", "hybrid_auto_start"):
        return _as_bool(value)
    if key == "hybrid_server":
        if not isinstance(value, dict):
            fail()
        return sanitize_hybrid_config(value)
    if key in LOCATION_SETTINGS:
        text = str(value or "").strip()
        if text and (not os.path.isabs(text) or len(text) > 240 or any(ord(ch) < 32 for ch in text)):
            fail()
        return os.path.normpath(text) if text else ""
    if key in ("pip_index_url", "pip_extra_index_url", "proxy", "hybrid_external_url", "java_download_url"):
        text = str(value or "").strip()
        if text and not _URL_RE.match(text):
            fail()
        return text.rstrip("/") if key == "hybrid_external_url" else text
    if key in ("python_path", "java_custom_path", "default_output_dir", "pip_trusted_host"):
        text = str(value or "").strip().strip('"')
        if len(text) > 1024:
            fail()
        return text
    fail()
    return None  # unreachable, keeps type checkers happy


class Settings:
    """Thread-safe access to ``settings.json``."""

    def __init__(self, path: str) -> None:
        """Load the settings file (missing keys receive defaults).

        Args:
            path: Location of ``settings.json``.
        """
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_SETTINGS)
        stored = read_json(path, {})
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key not in DEFAULT_SETTINGS:
                    continue
                try:
                    self._data[key] = _validate_setting(key, value)
                except ApiError:
                    pass  # keep the default for broken values

    def all(self) -> Dict[str, Any]:
        """Return a deep copy of all settings."""
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, key: str) -> Any:
        """Return a deep copy of one setting.

        Args:
            key: Setting name.
        """
        with self._lock:
            return copy.deepcopy(self._data.get(key, DEFAULT_SETTINGS.get(key)))

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and apply several settings at once, then save.

        Args:
            patch: ``{key: value}`` pairs. Unknown keys are rejected.

        Returns:
            All settings after the update.

        Raises:
            ApiError: If a key is unknown or a value is invalid (nothing is
                changed in that case).
        """
        if not isinstance(patch, dict):
            raise ApiError("bad_setting", "payload")
        validated: Dict[str, Any] = {}
        for key, value in patch.items():
            if key not in DEFAULT_SETTINGS:
                raise ApiError("bad_setting", key)
            if key == "hybrid_server" and isinstance(value, dict):
                merged = dict(self.get("hybrid_server"))
                merged.update(value)
                value = merged
            validated[key] = _validate_setting(key, value)
        with self._lock:
            self._data.update(validated)
            write_json_atomic(self.path, self._data)
            return copy.deepcopy(self._data)


# --------------------------------------------------------------------------- #
# Presets                                                                     #
# --------------------------------------------------------------------------- #
#: Ready-made option sets. ``server`` lists hybrid-server options the preset
#: expects; the UI offers to apply them when they differ from the current ones.
BUILTIN_PRESETS: List[Dict[str, Any]] = [
    {"id": "rag", "name": {"fa": "RAG - مارک‌داون + JSON", "en": "RAG - Markdown + JSON"},
     "options": {"format": ["markdown", "json"]}},
    {"id": "accessibility", "name": {"fa": "دسترس‌پذیری - Tagged PDF", "en": "Accessibility - Tagged PDF"},
     "options": {"format": ["tagged-pdf"]}},
    {"id": "debug", "name": {"fa": "عیب‌یابی - PDF حاشیه‌نویسی‌شده", "en": "Debug - annotated PDF"},
     "options": {"format": ["json", "pdf"]}},
    {"id": "text", "name": {"fa": "متن ساده", "en": "Plain text"},
     "options": {"format": ["text"], "keep-line-breaks": True}},
    {"id": "web", "name": {"fa": "HTML برای وب", "en": "HTML for the web"},
     "options": {"format": ["html"], "image-output": "embedded", "include-links": True}},
    {"id": "safe", "name": {"fa": "حذف داده‌های حساس", "en": "Sanitize sensitive data"},
     "options": {"format": ["markdown", "json"], "sanitize": True}},
    {"id": "tables", "name": {"fa": "جدول‌های پیچیده (هیبرید)", "en": "Complex tables (hybrid)"},
     "options": {"format": ["markdown", "json"], "hybrid": "docling-fast"}},
    {"id": "scanned", "name": {"fa": "اسکن‌شده / OCR (هیبرید)", "en": "Scanned / OCR (hybrid)"},
     "options": {"format": ["markdown", "json"], "hybrid": "docling-fast"},
     "server": {"ocr_mode": "force"}},
    {"id": "formula", "name": {"fa": "فرمول‌های ریاضی (هیبرید)", "en": "Math formulas (hybrid)"},
     "options": {"format": ["markdown", "json"], "hybrid": "docling-fast", "hybrid-mode": "full"},
     "server": {"enrich_formula": True}},
    {"id": "charts", "name": {"fa": "توضیح تصاویر و نمودارها (هیبرید)", "en": "Chart & image descriptions (hybrid)"},
     "options": {"format": ["markdown", "json"], "hybrid": "docling-fast", "hybrid-mode": "full"},
     "server": {"enrich_picture_description": True}},
]

_PRESET_NAME_RE = re.compile(r"^[^\x00-\x1f]{1,60}$")


class PresetStore:
    """Built-in presets plus user presets stored in ``presets.json``."""

    def __init__(self, path: str) -> None:
        """Create the store.

        Args:
            path: Location of ``presets.json``.
        """
        self.path = path
        self._lock = threading.Lock()

    def _user(self) -> List[Dict[str, Any]]:
        """Read the user presets from disk."""
        data = read_json(self.path, [])
        return [p for p in data if isinstance(p, dict) and "id" in p] if isinstance(data, list) else []

    def all(self) -> List[Dict[str, Any]]:
        """Return built-in presets followed by user presets."""
        builtin = [dict(p, builtin=True) for p in BUILTIN_PRESETS]
        return builtin + [dict(p, builtin=False) for p in self._user()]

    def save(self, name: str, options: Dict[str, Any], server: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create or replace a user preset.

        Args:
            name: Display name (also used to derive the id).
            options: Conversion options of the preset.
            server: Optional hybrid-server options the preset expects.

        Returns:
            The stored preset.

        Raises:
            ApiError: ``bad_preset`` for an invalid name or options.
        """
        name = str(name or "").strip()
        if not _PRESET_NAME_RE.match(name) or not isinstance(options, dict):
            raise ApiError("bad_preset", name)
        preset_id = "user-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if preset_id == "user-":
            preset_id = f"user-{int(time.time())}"
        preset = {"id": preset_id, "name": {"fa": name, "en": name}, "options": options}
        if server:
            preset["server"] = server
        with self._lock:
            presets = [p for p in self._user() if p.get("id") != preset_id]
            presets.append(preset)
            write_json_atomic(self.path, presets)
        return preset

    def delete(self, preset_id: str) -> None:
        """Delete a user preset (built-in presets cannot be deleted).

        Args:
            preset_id: Id of the preset.
        """
        with self._lock:
            presets = [p for p in self._user() if p.get("id") != preset_id]
            write_json_atomic(self.path, presets)

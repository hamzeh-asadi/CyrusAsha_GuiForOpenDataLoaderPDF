"""
Install and model folders: locations, sizes, free space, checks and moving.

The heavy parts of an installation do not have to live in the data folder:

* the **install folder** holds ``venv/`` (the Python environment with
  opendataloader-pdf - several GB with the hybrid extra), ``java/`` (the
  portable JRE) and ``cache/`` (pip's download cache and temporary files, so
  large wheels such as PyTorch never fill drive ``C:``);
* the **models folder** holds everything the hybrid server downloads at run
  time: Hugging Face models (docling layout, table, formula and picture
  models), EasyOCR, docling, ModelScope (RapidOCR) and torch caches.

Both default to the data folder (``data/`` and ``data/models``). The user can
choose other folders on the Setup page - for example a short path on a drive
with more free space. The installer moves the files with progress and
cancellation (``SetupManager._step_relocate``); this module provides the
building blocks and the information shown in the UI.

Only the Python standard library is used.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import APP_DIR, Paths
from .platform_utils import IS_WINDOWS, long_paths_enabled, remove_tree

#: Environment variables that send model downloads into the models folder,
#: with the sub-folder each of them uses. Docling fetches its models through
#: the Hugging Face hub (``HF_HOME``); EasyOCR reads ``EASYOCR_MODULE_PATH``;
#: RapidOCR (v3) downloads through ModelScope; ``XDG_CACHE_HOME`` catches
#: libraries that follow the XDG convention.
MODEL_ENV: Dict[str, str] = {
    "HF_HOME": "huggingface",
    "EASYOCR_MODULE_PATH": "easyocr",
    "DOCLING_CACHE_DIR": "docling",
    "MODELSCOPE_CACHE": "modelscope",
    "TORCH_HOME": "torch",
    "XDG_CACHE_HOME": "cache",
}

#: Longest folder paths that keep every file below the Windows limit of 260
#: characters when long paths are disabled: the deepest files of the hybrid
#: packages are about 140 characters below ``venv\Lib\site-packages`` and the
#: deepest Hugging Face model files about 175 characters below the models folder.
MAX_SAFE_LENGTH = {"install": 100, "models": 80}

#: Bytes copied between two progress reports / cancellation checks.
CHUNK = 4 * 1024 * 1024

#: Reasons reported by :func:`check_target` and :class:`MoveError`. The API and
#: the installer prefix them with ``location_`` (``err.location_<reason>`` in the UI).
LOCATION_ERRORS = ("bad_path", "not_absolute", "inside_app", "nested", "is_file", "not_writable", "same",
                   "target_exists", "no_space", "delete_failed")


class MoveError(Exception):
    """A folder could not be moved (``code`` names the reason for the UI)."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Create the error.

        Args:
            code: Machine-readable reason (``target_exists``, ``no_space`` ...).
            detail: Path or message for the log.
        """
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class MoveCanceled(Exception):
    """The user canceled a move; the partial copy was removed."""


ProgressCallback = Callable[[str, int], None]


def model_env(models_dir: str) -> Dict[str, str]:
    """Return the environment variables that point model downloads into ``models_dir``.

    Args:
        models_dir: The models folder.
    """
    return {name: os.path.join(models_dir, sub) for name, sub in MODEL_ENV.items()}


def install_env(paths: Paths) -> Dict[str, str]:
    """Return the variables that keep pip's cache and temporary files in the install folder.

    pip unpacks wheels in the temporary folder; for PyTorch that is more than
    a gigabyte, so ``TMP``/``TEMP`` are redirected as well.

    Args:
        paths: Application paths.
    """
    os.makedirs(paths.pip_cache_dir, exist_ok=True)
    os.makedirs(paths.install_tmp_dir, exist_ok=True)
    return {"PIP_CACHE_DIR": paths.pip_cache_dir, "TMP": paths.install_tmp_dir,
            "TEMP": paths.install_tmp_dir, "TMPDIR": paths.install_tmp_dir}


# --------------------------------------------------------------------------- #
# Sizes and free space                                                        #
# --------------------------------------------------------------------------- #
def dir_size(path: str, cancel: Optional[threading.Event] = None) -> Tuple[int, int]:
    """Measure a folder without following symbolic links.

    Args:
        path: Folder (or file).
        cancel: Optional event that stops the scan early.

    Returns:
        ``(bytes, files)``; ``(0, 0)`` if the path does not exist.
    """
    if os.path.isfile(path) and not os.path.islink(path):
        try:
            return os.path.getsize(path), 1
        except OSError:
            return 0, 0
    total = files = 0
    stack = [path]
    while stack:
        if cancel is not None and cancel.is_set():
            break
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, files


def nearest_existing(path: str) -> Optional[str]:
    """Return ``path`` or its closest existing parent folder (``None`` if none exists).

    Args:
        path: Absolute path.
    """
    current = os.path.abspath(path)
    while current and not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return current or None


def free_space(path: str) -> Optional[int]:
    """Free bytes on the drive of ``path`` (the folder does not need to exist yet).

    Args:
        path: Absolute path.
    """
    existing = nearest_existing(path)
    if not existing:
        return None
    try:
        return shutil.disk_usage(existing).free
    except OSError:
        return None


def same_volume(source: str, target: str) -> bool:
    """``True`` if ``target`` (or its nearest existing parent) is on the drive of ``source``.

    Args:
        source: Existing path.
        target: Destination path (may not exist yet).
    """
    existing = nearest_existing(target)
    if not existing or not os.path.exists(source):
        return False
    if IS_WINDOWS:
        return os.path.splitdrive(os.path.abspath(source))[0].lower() == \
            os.path.splitdrive(os.path.abspath(existing))[0].lower()
    try:
        return os.stat(source).st_dev == os.stat(existing).st_dev
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Checks                                                                      #
# --------------------------------------------------------------------------- #
def _norm(path: str) -> str:
    """Normalise a path for comparisons (case-insensitive on Windows)."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def is_inside(path: str, root: str) -> bool:
    """``True`` if ``path`` equals ``root`` or lies below it.

    Args:
        path: Path to test.
        root: Folder.
    """
    path_n, root_n = _norm(path), _norm(root)
    if path_n == root_n:
        return True
    return path_n.startswith(root_n.rstrip("\\/") + os.sep)


def path_warnings(path: str, kind: str) -> List[str]:
    """Return warnings for a folder that will hold an install or models folder.

    * ``long_path`` - Windows without long-path support and a folder path that
      is long enough to push deep package or model files over 260 characters;
    * ``non_ascii`` - characters outside ASCII (some native libraries, e.g.
      older docling-parse or OpenCV, cannot open such paths on Windows);
    * ``network`` - a network share (slow, and Python environments on shares
      are unreliable).

    Args:
        path: Absolute folder path.
        kind: ``"install"`` or ``"models"``.
    """
    warnings: List[str] = []
    if IS_WINDOWS and long_paths_enabled() is not True and len(path) > MAX_SAFE_LENGTH[kind]:
        warnings.append("long_path")
    if any(ord(ch) > 127 for ch in path):
        warnings.append("non_ascii")
    if path.startswith(("\\\\", "//")):
        warnings.append("network")
    return warnings


def check_target(paths: Paths, kind: str, raw: str) -> Dict[str, Any]:
    """Validate a folder the user wants to use as install or models folder.

    Args:
        paths: Current application paths.
        kind: ``"install"`` or ``"models"``.
        raw: Folder typed or picked by the user (``""`` = default location).

    Returns:
        ``{"ok", "path", "setting", "default", "error", "warnings", "free",
        "exists", "empty", "conflicts", "same"}`` - ``setting`` is the value to
        store (``""`` for the default location).
    """
    text = unicodedata.normalize("NFC", str(raw or "").strip().strip('"'))
    if text:
        text = os.path.normpath(os.path.expandvars(os.path.expanduser(text)))
    current = paths.install_dir if kind == "install" else paths.models_dir
    if kind == "install":
        default_path = paths.data_dir
    else:
        default_path = os.path.join(paths.install_dir, "models")
    target = text or default_path
    result: Dict[str, Any] = {"ok": False, "path": target, "setting": "", "default": False, "error": None,
                              "warnings": [], "free": None, "exists": False, "empty": True, "conflicts": [],
                              "same": False}
    if any(ord(ch) < 32 for ch in target) or len(target) > 240:
        result["error"] = "bad_path"
        return result
    if not os.path.isabs(target):
        result["error"] = "not_absolute"
        return result
    target = os.path.abspath(target)
    result["path"] = target
    result["default"] = _norm(target) == _norm(default_path)
    result["setting"] = "" if result["default"] else target
    result["same"] = _norm(target) == _norm(current)
    for forbidden in (os.path.join(APP_DIR, "src"), os.path.join(APP_DIR, "windows"), os.path.join(APP_DIR, "docs")):
        if is_inside(target, forbidden):
            result["error"] = "inside_app"
            return result
    # A folder may not be moved into itself (or into a folder that moves with it).
    if not result["same"]:
        if kind == "install":
            moving = [paths.venv_dir, paths.java_dir, paths.cache_dir] + ([paths.models_dir] if paths.models_default else [])
            if any(is_inside(target, folder) for folder in moving) or \
                    (not paths.models_default and is_inside(target, paths.models_dir)):
                result["error"] = "nested"
                return result
        else:
            if is_inside(target, paths.models_dir) or is_inside(paths.models_dir, target) or \
                    _norm(target) == _norm(paths.install_dir) or \
                    any(is_inside(target, folder) for folder in (paths.venv_dir, paths.java_dir, paths.cache_dir)):
                result["error"] = "nested"
                return result
    if os.path.isfile(target):
        result["error"] = "is_file"
        return result
    result["exists"] = os.path.isdir(target)
    if result["exists"]:
        try:
            entries = os.listdir(target)
        except OSError:
            entries = []
        result["empty"] = not entries
        names = {"venv", "java", "cache", "models"} if kind == "install" else set(MODEL_ENV.values())
        result["conflicts"] = sorted(e for e in entries if e in names)
    if not result["same"]:
        problem = _writable_problem(target)
        if problem:
            result["error"] = problem
            return result
    result["warnings"] = path_warnings(target, kind)
    result["free"] = free_space(target)
    result["ok"] = True
    return result


def _writable_problem(folder: str) -> Optional[str]:
    """Check that ``folder`` can be created and written - without creating anything.

    A test file is written into ``folder`` if it exists, otherwise into its
    nearest existing parent (whoever may write there may create the folder).
    Nothing else is created, so checking paths while the user types leaves
    no empty folders behind.

    Args:
        folder: Folder to test.

    Returns:
        ``None`` if writable, otherwise ``"not_writable"``.
    """
    existing = nearest_existing(folder)
    if existing is None or not os.path.isdir(existing):
        return "not_writable"
    probe = os.path.join(existing, f".cyrusasha-write-test-{os.getpid()}-{threading.get_ident()}")
    try:
        with open(probe, "w", encoding="ascii") as fh:
            fh.write("ok")
    except OSError:
        return "not_writable"
    try:
        os.remove(probe)
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------- #
# Moving                                                                      #
# --------------------------------------------------------------------------- #
def _copy_file(source: str, target: str, progress: ProgressCallback, done: int,
               cancel: Optional[threading.Event]) -> int:
    """Copy one file in chunks, reporting progress and honouring cancellation.

    Args:
        source: Source file.
        target: Destination file (overwritten).
        progress: ``progress(name, bytes_done_overall)``.
        done: Bytes copied before this file.
        cancel: Cancellation event.

    Returns:
        The new overall number of copied bytes.

    Raises:
        MoveCanceled: When ``cancel`` is set.
    """
    name = os.path.basename(source)
    with open(source, "rb") as src, open(target, "wb") as dst:
        while True:
            if cancel is not None and cancel.is_set():
                raise MoveCanceled()
            chunk = src.read(CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            done += len(chunk)
            progress(name, done)
    try:
        shutil.copystat(source, target)
    except OSError:
        pass
    return done


def copy_tree(source: str, target: str, progress: ProgressCallback, cancel: Optional[threading.Event] = None,
              done: int = 0) -> int:
    """Copy a folder tree with progress; symbolic links are recreated as links.

    Hugging Face caches use relative links on Linux/macOS and a venv's
    interpreter is a link there, so links are kept (and copied as files if
    the system refuses to create them).

    Args:
        source: Existing folder.
        target: Destination folder (created).
        progress: ``progress(name, bytes_done_overall)``.
        cancel: Cancellation event.
        done: Bytes already copied (for combined progress of several folders).

    Returns:
        Overall copied bytes.

    Raises:
        MoveCanceled: When ``cancel`` is set.
        OSError: On read/write errors.
    """
    os.makedirs(target, exist_ok=True)
    for current, dirs, files in os.walk(source, followlinks=False):
        relative = os.path.relpath(current, source)
        destination = target if relative == "." else os.path.join(target, relative)
        os.makedirs(destination, exist_ok=True)
        for name in list(dirs):
            path = os.path.join(current, name)
            if os.path.islink(path):  # a link to a folder: recreate it, do not descend
                dirs.remove(name)
                _copy_link(path, os.path.join(destination, name))
        for name in files:
            if cancel is not None and cancel.is_set():
                raise MoveCanceled()
            path = os.path.join(current, name)
            if os.path.islink(path):
                _copy_link(path, os.path.join(destination, name))
                continue
            done = _copy_file(path, os.path.join(destination, name), progress, done, cancel)
    return done


def _copy_link(source: str, target: str) -> None:
    """Recreate a symbolic link (or copy what it points to if links are not allowed).

    Args:
        source: Existing link.
        target: Path of the new link.
    """
    try:
        os.symlink(os.readlink(source), target, target_is_directory=os.path.isdir(source))
    except OSError:
        if os.path.isdir(source):
            shutil.copytree(source, target, symlinks=False, dirs_exist_ok=True)
        elif os.path.exists(source):
            shutil.copy2(source, target)


def move_folder(source: str, target: str, progress: ProgressCallback, cancel: Optional[threading.Event] = None,
                done: int = 0, force_copy: bool = False) -> int:
    """Move a folder to a new place.

    On the same drive the folder is renamed (instant). Otherwise it is copied
    with progress and the source is deleted afterwards; a canceled or failed
    copy is removed and the source stays untouched.

    Args:
        source: Existing folder (nothing happens if it does not exist).
        target: New path of the folder; must not exist or be empty.
        progress: ``progress(name, bytes_done_overall)``.
        cancel: Cancellation event.
        done: Bytes already copied (combined progress).
        force_copy: Always copy (used by the tests).

    Returns:
        Overall copied bytes.

    Raises:
        MoveError: ``target_exists`` or ``delete_failed``.
        MoveCanceled: When ``cancel`` is set during a copy.
    """
    if not os.path.exists(source):
        return done
    if os.path.exists(target):
        if os.path.isdir(target) and not os.listdir(target):
            os.rmdir(target)
        else:
            raise MoveError("target_exists", target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if not force_copy and same_volume(source, target):
        try:
            os.rename(source, target)
            return done
        except OSError:
            pass  # e.g. a file is in use or a different mount point: copy instead
    try:
        done = copy_tree(source, target, progress, cancel, done)
    except BaseException:
        remove_tree(target)
        raise
    remove_tree(source)
    if os.path.exists(source):
        raise MoveError("delete_failed", source)
    return done


class StorageInfo:
    """Sizes and free space of the install and models folders (computed in the background)."""

    def __init__(self, paths: Paths, on_change: Callable[[], None]) -> None:
        """Create the collector.

        Args:
            paths: Application paths.
            on_change: Called when new sizes are available.
        """
        self.paths = paths
        self.on_change = on_change
        self._sizes: Optional[Dict[str, Any]] = None
        self._time = 0.0
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        """Forget the measured sizes (after installs, moves and deletions)."""
        with self._lock:
            self._sizes = None

    def _measure(self) -> None:
        """Thread body: measure every part of both folders."""
        paths = self.paths
        parts: Dict[str, Dict[str, int]] = {"install": {}, "models": {}}
        for name in ("venv", "java", "cache"):
            parts["install"][name] = dir_size(getattr(paths, f"{name}_dir"))[0]
        for sub in sorted(set(MODEL_ENV.values())):
            parts["models"][sub] = dir_size(os.path.join(paths.models_dir, sub))[0]
        total_models = dir_size(paths.models_dir)[0]
        parts["models"]["other"] = max(0, total_models - sum(parts["models"].values()))
        with self._lock:
            self._sizes = {"parts": parts, "install": sum(parts["install"].values()), "models": total_models,
                           "for": (paths.install_dir, paths.models_dir)}
            self._time = time.time()
            self._thread = None
        self.on_change()

    def report(self, refresh: bool = False) -> Dict[str, Any]:
        """Return locations, warnings, free space and (when available) sizes.

        Sizes are measured on a background thread; until they are ready the
        report contains ``"pending": True`` and the UI asks again.

        Args:
            refresh: Measure again even if recent sizes exist.
        """
        paths = self.paths
        with self._lock:
            stale = (self._sizes is None or refresh or time.time() - self._time > 300
                     or self._sizes.get("for") != (paths.install_dir, paths.models_dir))
            if stale and self._thread is None:
                self._thread = threading.Thread(target=self._measure, name="storage-size", daemon=True)
                self._thread.start()
            sizes = None if stale else self._sizes
            pending = self._thread is not None
        install_warnings = path_warnings(paths.install_dir, "install")
        if nearest_existing(paths.install_dir) is None:  # e.g. an external drive that is not connected
            install_warnings.insert(0, "drive_missing")
        models_warnings = path_warnings(paths.models_dir, "models")
        if nearest_existing(paths.models_dir) is None:
            models_warnings.insert(0, "drive_missing")
        return {
            "data": paths.data_dir,
            "install": {"path": paths.install_dir, "default": paths.install_default,
                        "exists": os.path.isdir(paths.install_dir), "free": free_space(paths.install_dir),
                        "warnings": install_warnings,
                        "size": sizes["install"] if sizes else None,
                        "parts": sizes["parts"]["install"] if sizes else None,
                        "venv": paths.venv_dir, "java": paths.java_dir, "cache": paths.cache_dir},
            "models": {"path": paths.models_dir, "default": paths.models_default,
                       "exists": os.path.isdir(paths.models_dir), "free": free_space(paths.models_dir),
                       "warnings": models_warnings,
                       "size": sizes["models"] if sizes else None,
                       "parts": sizes["parts"]["models"] if sizes else None},
            "long_paths": long_paths_enabled(),
            "windows": IS_WINDOWS,
            "pending": pending,
        }

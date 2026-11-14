"""
Operating-system helpers for CyrusAsha_GuiForOpenDataLoaderPDF.

Everything that depends on the platform lives in this module so the rest of the
code base stays portable:

* spawning hidden child processes and killing complete process trees,
* robust decoding of child-process output (UTF-8, ANSI and OEM code pages),
* discovering Python interpreters and Java runtimes,
* reading the *current* PATH from the Windows registry,
* opening folders in Explorer / Finder, native file dialogs (via tkinter),
* launching the UI in a chromeless "app" window (Microsoft Edge / Chrome).

Only the Python standard library is used.
"""

from __future__ import annotations

import glob
import json
import locale
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from typing import Dict, List, Optional, Tuple

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Windows process-creation flags. They are defined here (instead of being read
# from ``subprocess``) so this module can be imported on every platform.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008

# Regular expressions used to clean child-process output.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# --------------------------------------------------------------------------- #
# Child processes                                                             #
# --------------------------------------------------------------------------- #
def popen_kwargs() -> Dict[str, object]:
    """Return extra keyword arguments for :class:`subprocess.Popen`.

    On Windows the child gets no console window (important when the GUI runs
    under ``pythonw``) and its own process group, so stopping it never affects
    the GUI itself. On POSIX the child becomes the leader of a new session,
    which allows :func:`kill_tree` to signal the whole group at once.

    Returns:
        A dictionary that can be splatted into ``subprocess.Popen(...)``.
    """
    if IS_WINDOWS:
        return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def decode_output(data: bytes) -> str:
    """Decode raw bytes written by a child process into text.

    UTF-8 is tried first because every child we start is asked to write UTF-8.
    Tools that ignore that request (for example ``winget`` or an old JVM) write
    in the Windows ANSI or OEM code page, so those are tried next, followed by
    the locale encoding. The function never raises.

    Args:
        data: Bytes read from the child's stdout/stderr pipe.

    Returns:
        The decoded text (undecodable bytes are replaced).
    """
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    candidates: List[str] = ["mbcs", "oem"] if IS_WINDOWS else []
    candidates.append(locale.getpreferredencoding(False) or "latin-1")
    for encoding in candidates:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def clean_line(text: str) -> str:
    """Normalise one line of console output for display in the web UI.

    * Carriage-return "progress" output (``50%\\r60%\\r70%``) keeps only the
      last non-empty segment, i.e. the final state of the progress line.
    * ANSI colour/cursor escape sequences and other control characters are
      removed; tabs are kept.

    Args:
        text: A single line (without the trailing newline).

    Returns:
        The cleaned line, possibly empty.
    """
    if "\r" in text:
        segments = [segment for segment in text.split("\r") if segment.strip()]
        text = segments[-1] if segments else ""
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    return text.rstrip()


def run_capture(
    cmd: List[str],
    timeout: float = 30,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> Tuple[int, str]:
    """Run a short command to completion and return its exit code and output.

    The function never raises: a missing executable or a timeout is reported
    as exit code ``-1`` with the error text as output, so callers can treat
    "not installed" and "failed" uniformly.

    Args:
        cmd: Command and arguments (no shell is involved).
        timeout: Maximum run time in seconds.
        env: Optional environment for the child.
        cwd: Optional working directory.

    Returns:
        A ``(exit_code, combined_stdout_and_stderr)`` tuple.
    """
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=env,
            cwd=cwd,
            **popen_kwargs(),
        )
        return proc.returncode, decode_output(proc.stdout)
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s"
    except (OSError, ValueError) as exc:
        return -1, str(exc)


def kill_tree(proc: subprocess.Popen, timeout: float = 8.0) -> None:
    """Terminate a child process together with all of its descendants.

    The opendataloader-pdf wrapper (Python) starts a JVM as its own child, so
    killing only the direct child would leave Java running. On Windows the
    built-in ``taskkill /T /F`` removes the whole tree; on POSIX the process
    group created by :func:`popen_kwargs` receives SIGTERM and, if it does not
    exit in time, SIGKILL.

    Args:
        proc: The :class:`subprocess.Popen` object of the direct child.
        timeout: Seconds to wait for a graceful exit before forcing it.
    """
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
        pgid = None
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass


# --------------------------------------------------------------------------- #
# Environment                                                                 #
# --------------------------------------------------------------------------- #
def windows_registry_env() -> Dict[str, str]:
    """Read the current machine + user ``Path`` and ``JAVA_HOME`` from the registry.

    A process inherits its environment when it starts, therefore software that
    was installed afterwards (for example Java via winget) is invisible in
    ``os.environ``. Reading the registry lets the GUI find it without restart.

    Returns:
        A dictionary with the keys ``Path`` and/or ``JAVA_HOME`` (Windows only;
        an empty dictionary on other platforms or on errors).
    """
    if not IS_WINDOWS:
        return {}
    import winreg  # Windows-only module, imported lazily.

    result: Dict[str, str] = {}
    paths: List[str] = []
    locations = [
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ]
    for root, subkey in locations:
        try:
            with winreg.OpenKey(root, subkey) as key:
                for name in ("Path", "JAVA_HOME"):
                    try:
                        value, _kind = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    value = winreg.ExpandEnvironmentStrings(str(value))
                    if name == "Path":
                        paths.append(value)
                    else:
                        result["JAVA_HOME"] = value  # HKCU is read last and wins.
        except OSError:
            continue
    if paths:
        result["Path"] = os.pathsep.join(paths)
    return result


def build_base_env() -> Dict[str, str]:
    """Return a copy of ``os.environ`` prepared for child processes.

    * On Windows, PATH entries that exist in the registry but are missing from
      the inherited PATH are appended (see :func:`windows_registry_env`).
    * Python children are told to use UTF-8 for their standard streams.

    Returns:
        A new environment dictionary (``os.environ`` itself is not modified).
    """
    env = dict(os.environ)
    if IS_WINDOWS:
        registry = windows_registry_env()
        current = env.get("PATH", "")
        known = {p.strip().lower().rstrip("\\") for p in current.split(os.pathsep) if p.strip()}
        extra = [
            p
            for p in registry.get("Path", "").split(os.pathsep)
            if p.strip() and p.strip().lower().rstrip("\\") not in known
        ]
        if extra:
            env["PATH"] = current + os.pathsep + os.pathsep.join(extra)
        if not env.get("JAVA_HOME") and registry.get("JAVA_HOME"):
            env["JAVA_HOME"] = registry["JAVA_HOME"]
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def prepend_path(env: Dict[str, str], directory: str) -> None:
    """Put ``directory`` at the front of ``env['PATH']`` (in place).

    Args:
        env: Environment dictionary to modify.
        directory: Folder to add.
    """
    current = env.get("PATH", "")
    env["PATH"] = directory + (os.pathsep + current if current else "")


def append_path(env: Dict[str, str], directory: str) -> None:
    """Add ``directory`` to the end of ``env['PATH']`` if it is not there yet.

    Args:
        env: Environment dictionary to modify.
        directory: Folder to add.
    """
    current = env.get("PATH", "")
    parts = [p.lower().rstrip("\\/") for p in current.split(os.pathsep)]
    if directory.lower().rstrip("\\/") not in parts:
        env["PATH"] = (current + os.pathsep if current else "") + directory


def os_description() -> str:
    """Return a short human readable description of the operating system.

    Returns:
        For example ``"Windows 11 (10.0.22631) AMD64"``.
    """
    if IS_WINDOWS:
        release, version = platform.release(), platform.version()
        try:
            build = int(version.split(".")[-1])
            if release == "10" and build >= 22000:
                release = "11"
        except ValueError:
            pass
        return f"Windows {release} ({version}) {platform.machine()}"
    if IS_MAC:
        return f"macOS {platform.mac_ver()[0]} {platform.machine()}"
    return f"{platform.system()} {platform.release()} {platform.machine()}"


# --------------------------------------------------------------------------- #
# Python interpreters                                                         #
# --------------------------------------------------------------------------- #
_PY_PROBE = (
    "import json,sys,platform,struct;"
    "print(json.dumps({'v':list(sys.version_info[:3]),'bits':struct.calcsize('P')*8,"
    "'impl':platform.python_implementation(),'exe':sys.executable}))"
)

#: Minimum Python version required by the ``opendataloader-pdf`` package.
MIN_PYTHON = (3, 11)


def venv_python(venv_dir: str) -> str:
    """Return the path of the interpreter inside a virtual environment.

    Args:
        venv_dir: Root folder of the virtual environment.

    Returns:
        ``<venv>\\Scripts\\python.exe`` on Windows, ``<venv>/bin/python`` elsewhere.
    """
    if IS_WINDOWS:
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def console_python() -> str:
    """Return a console interpreter for helper scripts.

    When the GUI was started with ``pythonw.exe`` the sibling ``python.exe`` is
    preferred, because some tools behave better with a real console binary.

    Returns:
        Path to a Python executable.
    """
    exe = sys.executable or "python"
    if IS_WINDOWS and os.path.basename(exe).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(candidate):
            return candidate
    return exe


def probe_python(path: str) -> Optional[Dict[str, object]]:
    """Ask an interpreter for its version, bitness and implementation.

    Args:
        path: Path to a Python executable.

    Returns:
        A dictionary (``path``, ``version``, ``version_tuple``, ``bits``,
        ``impl``, ``supported``) or ``None`` if the interpreter does not run
        (for example the Microsoft Store alias stub).
    """
    code, out = run_capture([path, "-c", _PY_PROBE], timeout=20)
    if code != 0:
        return None
    for line in reversed(out.strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        numbers = [int(x) for x in data.get("v", [0, 0, 0])]
        return {
            "path": os.path.abspath(path),
            "version": ".".join(str(n) for n in numbers),
            "version_tuple": numbers,
            "bits": int(data.get("bits", 64)),
            "impl": data.get("impl", ""),
            "supported": tuple(numbers[:2]) >= MIN_PYTHON,
        }
    return None


def discover_pythons(exclude_dirs: Optional[List[str]] = None) -> List[Dict[str, object]]:
    """Find Python interpreters installed on this computer.

    Sources (in order): the interpreter running the GUI, the Windows ``py``
    launcher (``py -0p``, also understood by the new Python install manager),
    well-known installation folders and the PATH.

    Args:
        exclude_dirs: Folders whose interpreters must be ignored (our own venv).

    Returns:
        A list of :func:`probe_python` dictionaries with an extra ``source``
        key, sorted so that supported and newer versions come first.
    """
    exclude = [os.path.normcase(os.path.abspath(d)) for d in (exclude_dirs or [])]
    candidates: List[Tuple[str, str]] = []

    def add(path: Optional[str], source: str) -> None:
        """Register a candidate path if it exists and is not excluded."""
        if not path or not os.path.isfile(path):
            return
        norm = os.path.normcase(os.path.abspath(path))
        if any(norm.startswith(d + os.sep) for d in exclude):
            return
        candidates.append((os.path.abspath(path), source))

    add(console_python(), "gui")
    if IS_WINDOWS:
        code, out = run_capture(["py", "-0p"], timeout=20)
        if code == 0:
            for line in out.splitlines():
                match = re.search(r"([A-Za-z]:\\.*?\.exe)\s*$", line.strip())
                if match:
                    add(match.group(1), "py launcher")
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        patterns = [
            os.path.join(local, "Programs", "Python", "Python3*", "python.exe"),
            os.path.join(local, "Python", "pythoncore-3*", "python.exe"),
            os.path.join(program_files, "Python3*", "python.exe"),
            r"C:\Python3*\python.exe",
        ]
        for pattern in patterns:
            for path in glob.glob(pattern):
                add(path, "folder")
        on_path = shutil.which("python")
        if on_path and "windowsapps" not in on_path.lower():
            add(on_path, "PATH")
    else:
        for name in ("python3", "python3.15", "python3.14", "python3.13", "python3.12", "python3.11", "python"):
            add(shutil.which(name), "PATH")

    seen = set()
    result: List[Dict[str, object]] = []
    for path, source in candidates:
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            continue
        seen.add(key)
        info = probe_python(path)
        if info:
            info["source"] = source
            result.append(info)
    result.sort(key=lambda i: (bool(i["supported"]), list(i["version_tuple"])), reverse=True)
    return result


# --------------------------------------------------------------------------- #
# Java                                                                        #
# --------------------------------------------------------------------------- #
_JAVA_VERSION_RE = re.compile(r'version\s+"([^"]+)"')

#: Minimum Java major version required by opendataloader-pdf.
MIN_JAVA = 11


def java_exe_name() -> str:
    """Return the platform specific file name of the Java launcher."""
    return "java.exe" if IS_WINDOWS else "java"


def parse_java_major(version: str) -> int:
    """Extract the major version number from a Java version string.

    Args:
        version: For example ``"1.8.0_402"``, ``"11"`` or ``"21.0.5"``.

    Returns:
        The major version (8, 11, 21, ...) or 0 if it cannot be parsed.
    """
    parts = re.split(r"[._+-]", version.strip())
    try:
        major = int(parts[0])
        if major == 1 and len(parts) > 1:
            major = int(parts[1])
        return major
    except (ValueError, IndexError):
        return 0


def probe_java(java: str) -> Optional[Dict[str, object]]:
    """Run ``java -version`` and parse the result.

    Args:
        java: Path to a ``java`` / ``java.exe`` launcher.

    Returns:
        ``{"path", "home", "version", "major", "runtime", "ok"}`` or ``None``
        if the launcher cannot be executed.
    """
    env = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
        env.pop(name, None)  # Avoid "Picked up ..." noise in the probe.
    code, out = run_capture([java, "-version"], timeout=25, env=env)
    match = _JAVA_VERSION_RE.search(out)
    if code != 0 or not match:
        return None
    version = match.group(1)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith("Picked up")]
    runtime = lines[1] if len(lines) > 1 else ""
    major = parse_java_major(version)
    return {
        "path": os.path.abspath(java),
        "home": os.path.dirname(os.path.dirname(os.path.abspath(java))),
        "version": version,
        "major": major,
        "runtime": runtime,
        "ok": major >= MIN_JAVA,
    }


def find_java_in_dir(root: str, max_depth: int = 4) -> Optional[str]:
    """Search ``root`` for ``bin/java(.exe)``.

    Handles the usual JDK layouts including macOS bundles
    (``<root>/Contents/Home/bin/java``).

    Args:
        root: Folder to search.
        max_depth: Maximum folder depth below ``root``.

    Returns:
        The path of the Java launcher or ``None``.
    """
    exe = java_exe_name()
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for current, dirs, files in os.walk(root):
        depth = current.count(os.sep) - base_depth
        if depth >= max_depth:
            dirs[:] = []
        if os.path.basename(current) == "bin" and exe in files:
            return os.path.join(current, exe)
    return None


def system_java_candidates(env: Dict[str, str]) -> List[Tuple[str, str]]:
    """List Java launchers installed system-wide.

    Args:
        env: Environment used to resolve ``JAVA_HOME`` and ``PATH``.

    Returns:
        ``(path, source)`` tuples, duplicates removed.
    """
    exe = java_exe_name()
    found: List[Tuple[str, str]] = []
    java_home = env.get("JAVA_HOME")
    if java_home:
        found.append((os.path.join(java_home, "bin", exe), "JAVA_HOME"))
    on_path = shutil.which("java", path=env.get("PATH"))
    if on_path:
        found.append((on_path, "PATH"))
    patterns: List[str] = []
    if IS_WINDOWS:
        bases = {
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramW6432", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        }
        vendors = [
            "Eclipse Adoptium", "Java", "Microsoft", "Zulu", "Amazon Corretto",
            "BellSoft", "Semeru", "OpenJDK", "AdoptOpenJDK", "RedHat",
        ]
        for base in bases:
            for vendor in vendors:
                patterns.append(os.path.join(base, vendor, "*", "bin", exe))
    elif IS_MAC:
        patterns.append("/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java")
    else:
        patterns.append("/usr/lib/jvm/*/bin/java")
    for pattern in patterns:
        for path in sorted(glob.glob(pattern), reverse=True):
            found.append((path, "folder"))
    unique: List[Tuple[str, str]] = []
    seen = set()
    for path, source in found:
        if not os.path.isfile(path):
            continue
        key = os.path.normcase(os.path.realpath(path))
        if key not in seen:
            seen.add(key)
            unique.append((path, source))
    return unique


def tesseract_dir() -> Optional[str]:
    """Return the folder of a Tesseract OCR installation, if any.

    The UB-Mannheim installer (used by ``winget``) does not add Tesseract to
    PATH, so the default installation folders are checked as well.

    Returns:
        The folder containing ``tesseract(.exe)`` or ``None``.
    """
    on_path = shutil.which("tesseract")
    if on_path:
        return os.path.dirname(on_path)
    if IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            folder = os.path.join(base, "Tesseract-OCR")
            if os.path.isfile(os.path.join(folder, "tesseract.exe")):
                return folder
    return None


# --------------------------------------------------------------------------- #
# Files, folders and dialogs                                                  #
# --------------------------------------------------------------------------- #
def open_in_file_manager(path: str) -> None:
    """Open a folder in Explorer / Finder / the desktop file manager.

    Args:
        path: An existing folder.
    """
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal_file(path: str) -> None:
    """Show a file selected in its folder (Explorer ``/select``).

    Args:
        path: An existing file.
    """
    if IS_WINDOWS:
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    elif IS_MAC:
        subprocess.Popen(["open", "-R", path])
    else:
        open_in_file_manager(os.path.dirname(path))


_PICKER_SCRIPT = r"""
import json, sys
kind, title, initial = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception as exc:
    print(json.dumps({"error": "no_tk", "detail": str(exc)}))
    sys.exit(0)
root = tk.Tk()
root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
root.update()
opts = {"title": title, "parent": root}
if initial:
    opts["initialdir"] = initial
if kind == "files":
    paths = list(filedialog.askopenfilenames(filetypes=[("PDF", "*.pdf"), ("*", "*.*")], **opts))
elif kind == "folder":
    res = filedialog.askdirectory(mustexist=True, **opts)
    paths = [res] if res else []
elif kind == "json":
    res = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("*", "*.*")], **opts)
    paths = [res] if res else []
elif kind == "exe":
    res = filedialog.askopenfilename(**opts)
    paths = [res] if res else []
else:
    paths = []
root.destroy()
print(json.dumps({"paths": paths}))
"""


def native_pick(kind: str, title: str = "", initial_dir: str = "") -> Dict[str, object]:
    """Show a native open-file / choose-folder dialog on the local desktop.

    The dialog runs in a separate Python process (tkinter must own the main
    thread of its process), so it never blocks or destabilises the server.
    Because files picked this way are referenced by their real path, nothing
    has to be uploaded or copied - ideal for large PDFs and whole folders.

    Args:
        kind: ``"files"`` (multiple PDFs), ``"folder"``, ``"json"`` or ``"exe"``.
        title: Dialog title.
        initial_dir: Folder shown first (optional).

    Returns:
        ``{"paths": [...]}`` or ``{"error": "no_tk" | "failed", "detail": str}``.
    """
    code, out = run_capture(
        [console_python(), "-c", _PICKER_SCRIPT, kind, title or "", initial_dir or ""],
        timeout=3600,
    )
    for line in reversed(out.strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if "paths" in data:
            data["paths"] = [os.path.normpath(p) for p in data["paths"] if p]
        return data
    return {"error": "failed", "detail": out.strip()[-500:] or f"exit code {code}"}


def long_paths_enabled() -> Optional[bool]:
    """Tell whether Windows allows paths longer than 260 characters.

    Without ``LongPathsEnabled`` (registry, off by default on many systems)
    deep files of large Python packages or Hugging Face models cannot be
    written below a long folder path.

    Returns:
        ``True``/``False`` on Windows, ``None`` on other systems or when the
        registry cannot be read.
    """
    if not IS_WINDOWS:
        return None
    try:
        import winreg  # Windows-only module, imported lazily.

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            value, _kind = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(int(value))
    except FileNotFoundError:
        return False
    except (ImportError, OSError, ValueError):
        return None


def remove_tree(path: str) -> None:
    """Delete a folder recursively, also removing read-only files (Windows).

    Args:
        path: Folder to delete. Missing folders are ignored.
    """
    if not os.path.exists(path):
        return

    def _retry(func, target, *_args) -> None:
        """Clear the read-only flag and retry the failed removal."""
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:  # pragma: no cover - older Pythons
        shutil.rmtree(path, onerror=_retry)


# --------------------------------------------------------------------------- #
# Network helpers                                                             #
# --------------------------------------------------------------------------- #
def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check whether something already listens on ``host:port``.

    Args:
        port: TCP port.
        host: Address to connect to.

    Returns:
        ``True`` if a connection could be established.
    """
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Launching the UI                                                            #
# --------------------------------------------------------------------------- #
def find_chromium_browser() -> Optional[str]:
    """Locate Microsoft Edge or Google Chrome for the chromeless app window.

    Edge ships with every Windows 10/11 installation, so the GUI normally
    opens in a desktop-like window without tabs or an address bar.

    Returns:
        Path to the browser executable or ``None``.
    """
    if IS_WINDOWS:
        names = [
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
            r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ]
        for name in names:
            path = os.path.expandvars(name)
            if os.path.isfile(path):
                return path
        try:
            import winreg

            for exe in ("msedge.exe", "chrome.exe"):
                for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                    try:
                        key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
                        with winreg.OpenKey(root, key_path) as key:
                            value, _ = winreg.QueryValueEx(key, "")
                            if value and os.path.isfile(value):
                                return value
                    except OSError:
                        continue
        except ImportError:
            pass
        return None
    if IS_MAC:
        for path in (
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ):
            if os.path.isfile(path):
                return path
        return None
    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    return None


def open_ui(url: str, mode: str = "app") -> str:
    """Open the web UI.

    Args:
        url: Address of the local server.
        mode: ``"app"`` (chromeless Edge/Chrome window, falls back to the
            default browser), ``"browser"`` or ``"none"``.

    Returns:
        The mode that was actually used (``"app"``, ``"browser"`` or ``"none"``).
    """
    if mode == "none":
        return "none"
    if mode == "app":
        browser = find_chromium_browser()
        if browser:
            try:
                kwargs: Dict[str, object] = {}
                if IS_WINDOWS:
                    kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                else:
                    kwargs["start_new_session"] = True
                subprocess.Popen(
                    [browser, f"--app={url}", "--window-size=1380,900"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    **kwargs,
                )
                return "app"
            except OSError:
                pass
    webbrowser.open(url)
    return "browser"


def create_desktop_shortcut(target: str, working_dir: str, icon: str, name: str,
                            description: str = "CyrusAsha control center for OpenDataLoader PDF") -> str:
    """Create a ``.lnk`` shortcut on the Windows desktop.

    PowerShell and the ``WScript.Shell`` COM object are used, both of which are
    part of every Windows installation, so no extra package is required.

    Args:
        target: File the shortcut starts (``start-silent.bat``).
        working_dir: Working directory of the shortcut.
        icon: ``.ico`` file used as the shortcut icon.
        name: Shortcut name without extension.
        description: Tooltip text of the shortcut.

    Returns:
        The path of the created shortcut.

    Raises:
        RuntimeError: When not on Windows or when PowerShell fails.
    """
    if not IS_WINDOWS:
        raise RuntimeError("Desktop shortcuts are only supported on Windows")

    def quote(value: str) -> str:
        """Quote a value as a PowerShell single-quoted string."""
        return "'" + value.replace("'", "''") + "'"

    script = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        f"$p=Join-Path $d {quote(name + '.lnk')};"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
        f"$s.TargetPath={quote(target)};"
        f"$s.WorkingDirectory={quote(working_dir)};"
        f"$s.IconLocation={quote(icon)};"
        f"$s.Description={quote(description)};"
        "$s.WindowStyle=7;"
        "$s.Save();Write-Output $p"
    )
    code, out = run_capture(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=60
    )
    if code != 0:
        raise RuntimeError(out.strip() or f"powershell exit code {code}")
    return out.strip().splitlines()[-1] if out.strip() else ""


def sleep_until(predicate, timeout: float, interval: float = 0.2) -> bool:
    """Poll ``predicate`` until it returns true or ``timeout`` seconds pass.

    Args:
        predicate: Zero-argument callable.
        timeout: Maximum waiting time in seconds.
        interval: Poll interval in seconds.

    Returns:
        ``True`` if the predicate became true in time.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())

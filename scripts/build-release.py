#!/usr/bin/env python3
"""
CyrusAsha_GuiForOpenDataLoaderPDF - build the release package.

Creates ``release/CyrusAsha_GuiForOpenDataLoaderPDF-<version>.zip`` with a single
top-level folder ``CyrusAsha_GuiForOpenDataLoaderPDF-<version>/``. Runtime data
(``data/``), previous releases, caches and the test suite's temporary files are
excluded. Attach the ZIP to a GitHub Release instead of committing it.

Only the Python standard library is used::

    python scripts/build-release.py
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Folders that are never packaged (at any depth): VCS/editor folders and tool caches.
EXCLUDED_DIRS = {"__pycache__", ".git", ".github", ".freebuff", ".idea", ".vscode", "node_modules", ".mypy_cache",
                 ".pytest_cache", ".ruff_cache", ".tox", ".nox", ".venv", "htmlcov"}

#: Top-level folders that are never packaged: the runtime data folder and the
#: release output. Only the top level - ``src/cyrusasha_odl/data`` is needed.
EXCLUDED_ROOT_DIRS = {"data", "release"}


def is_excluded_dir(relative: str) -> bool:
    """Return ``True`` if a folder (path relative to the project root) must not be packaged.

    Args:
        relative: Folder path relative to the project root (``"."`` for the root).
    """
    parts = [p for p in relative.split(os.sep) if p not in ("", ".")]
    return bool(parts) and (parts[0] in EXCLUDED_ROOT_DIRS or any(p in EXCLUDED_DIRS for p in parts))

#: File name patterns that are never packaged.
EXCLUDED_FILES = re.compile(r"(\.pyc|\.log|\.DS_Store|Thumbs\.db|\.coverage|\.rtl-tmp|\.part)$")


def read_version() -> str:
    """Return ``__version__`` from ``src/cyrusasha_odl/__init__.py``."""
    with open(os.path.join(ROOT, "src", "cyrusasha_odl", "__init__.py"), "r", encoding="utf-8") as fh:
        match = re.search(r'__version__ = "([^"]+)"', fh.read())
    if not match:
        raise SystemExit("version not found")
    return match.group(1)


def check_batch_files() -> None:
    """Abort if a Windows launcher lost its CRLF line endings or contains non-ASCII bytes."""
    for current, _dirs, files in os.walk(ROOT):
        if is_excluded_dir(os.path.relpath(current, ROOT)):
            continue
        for name in files:
            if name.lower().endswith((".bat", ".cmd")):
                path = os.path.join(current, name)
                with open(path, "rb") as fh:
                    data = fh.read()
                if data.count(b"\n") != data.count(b"\r\n"):
                    raise SystemExit(f"{path}: launcher must use CRLF line endings")
                data.decode("ascii")


def collect_files() -> list:
    """Return all files to package as ``(absolute_path, path_inside_zip)``."""
    result = []
    for current, dirs, files in os.walk(ROOT):
        relative = os.path.relpath(current, ROOT)
        dirs[:] = sorted(d for d in dirs if not is_excluded_dir(os.path.join(relative, d)))
        for name in sorted(files):
            if EXCLUDED_FILES.search(name):
                continue
            full = os.path.join(current, name)
            result.append((full, os.path.relpath(full, ROOT).replace(os.sep, "/")))
    return result


def main() -> int:
    """Build the ZIP and print its SHA-256 checksum.

    Returns:
        Process exit code.
    """
    version = read_version()
    check_batch_files()
    name = f"CyrusAsha_GuiForOpenDataLoaderPDF-{version}"
    os.makedirs(os.path.join(ROOT, "release"), exist_ok=True)
    target = os.path.join(ROOT, "release", f"{name}.zip")
    files = collect_files()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for full, rel in files:
            zf.write(full, f"{name}/{rel}")
    digest = hashlib.sha256(open(target, "rb").read()).hexdigest()
    with open(target + ".sha256", "w", encoding="ascii", newline="\n") as fh:
        fh.write(f"{digest}  {os.path.basename(target)}\n")
    print(f"{os.path.relpath(target, ROOT)}: {len(files)} files, {os.path.getsize(target) // 1024} KB")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

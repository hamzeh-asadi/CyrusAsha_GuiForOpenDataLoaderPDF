"""
CyrusAsha_GuiForOpenDataLoaderPDF - launcher and repository convention tests.

Windows launchers must stay ASCII with CRLF line endings (cmd.exe misreads
labels in LF files; PowerShell 5.1 reads BOM-less UTF-8 as ANSI), every path
they reference must exist, and the brand assets must be complete.

Run from the project root::

    python -m unittest discover -s test -v
"""

from __future__ import annotations

import os
import re
import struct
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BATCH_FILES = ["Start.bat", "windows/start-gui.bat", "windows/start-silent.bat",
               "windows/install.bat", "windows/diagnose.bat"]


def read_bytes(rel: str) -> bytes:
    """Read a project file as bytes.

    Args:
        rel: Path relative to the project root (``/`` separated).
    """
    with open(os.path.join(ROOT, *rel.split("/")), "rb") as fh:
        return fh.read()


class LauncherTest(unittest.TestCase):
    """Conventions for the Windows launchers."""

    def test_batch_files_are_ascii_with_crlf(self) -> None:
        """Every .bat file is ASCII and uses CRLF exclusively."""
        for rel in BATCH_FILES:
            data = read_bytes(rel)
            data.decode("ascii")  # raises on non-ASCII bytes
            self.assertGreater(data.count(b"\r\n"), 0, rel)
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), f"{rel} has bare LF line endings")

    def test_powershell_script_is_ascii(self) -> None:
        """install-shortcut.ps1 is ASCII-only."""
        read_bytes("windows/install-shortcut.ps1").decode("ascii")

    def test_launchers_reference_existing_files(self) -> None:
        """Paths used by the launchers exist (relative to the project root)."""
        expected = {
            "Start.bat": ["windows/start-gui.bat"],
            "windows/start-gui.bat": ["src/main.py"],
            "windows/start-silent.bat": ["src/main.py", "windows/start-gui.bat"],
            "windows/install.bat": ["src/main.py", "windows/install-shortcut.ps1", "Start.bat"],
            "windows/diagnose.bat": ["src/main.py"],
        }
        for rel, targets in expected.items():
            text = read_bytes(rel).decode("ascii").replace("\\", "/")
            for target in targets:
                self.assertTrue(os.path.isfile(os.path.join(ROOT, target)), f"{target} missing")
                self.assertIn(os.path.basename(target), text, f"{rel} does not reference {target}")

    def test_launchers_carry_the_product_name(self) -> None:
        """Window titles use the CyrusAsha product name and version."""
        for rel in ["windows/start-gui.bat", "windows/install.bat", "windows/diagnose.bat"]:
            text = read_bytes(rel).decode("ascii")
            self.assertRegex(text, r"title CyrusAsha_GuiForOpenDataLoaderPDF-\d+\.\d+\.\d+", rel)


class BrandAssetsTest(unittest.TestCase):
    """The logo and every derived asset exist."""

    def test_brand_files_exist(self) -> None:
        """Source logo, header emblem, favicon and full logo are present PNG files."""
        for rel in ["assets/CyrusAsha_GuiForOpenDataLoaderPDF-logo.png", "src/gui/public/brand/CyrusAsha_GuiForOpenDataLoaderPDF.png",
                    "src/gui/public/brand/favicon.png", "src/gui/public/brand/logo-full.png"]:
            self.assertTrue(read_bytes(rel).startswith(b"\x89PNG\r\n\x1a\n"), rel)

    def test_icon_has_ten_sizes(self) -> None:
        """windows/icon.ico contains the ten sizes 16-256 px."""
        data = read_bytes("windows/icon.ico")
        _reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((kind, count), (1, 10))
        sizes = sorted((data[6 + 16 * i] or 256) for i in range(count))
        self.assertEqual(sizes, [16, 20, 24, 32, 40, 48, 64, 96, 128, 256])


class RepositoryTest(unittest.TestCase):
    """Repository metadata used when publishing on GitHub."""

    def test_gitattributes_keep_crlf_for_batch_files(self) -> None:
        """.gitattributes normalizes to LF but keeps CRLF for .bat/.cmd."""
        text = read_bytes(".gitattributes").decode("utf-8")
        self.assertIn("* text=auto eol=lf", text)
        self.assertRegex(text, r"\*\.bat\s+text eol=crlf")

    def test_gitignore_excludes_runtime_data(self) -> None:
        """Runtime data and release packages are not committed - but the package data folder is."""
        lines = read_bytes(".gitignore").decode("utf-8").splitlines()
        for entry in ("/data/", "/release/", "__pycache__/"):
            self.assertIn(entry, lines)
        # A bare "data/" would also ignore src/cyrusasha_odl/data/ (the RTL word list).
        self.assertNotIn("data/", lines)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "src", "cyrusasha_odl", "data", "rtl-words.txt.gz")))

    def test_readme_is_bilingual(self) -> None:
        """README.md contains both Persian and English text and the logo."""
        text = read_bytes("README.md").decode("utf-8")
        self.assertIn("assets/CyrusAsha_GuiForOpenDataLoaderPDF-logo.png", text)
        self.assertTrue(re.search(r"[\u0600-\u06FF]{20,}", text.replace(" ", "")), "no Persian text")
        self.assertIn("## ", text)

    def test_version_is_consistent(self) -> None:
        """The version in the package matches the README title and the launchers."""
        init = read_bytes("src/cyrusasha_odl/__init__.py").decode("utf-8")
        version = re.search(r'__version__ = "([^"]+)"', init).group(1)
        self.assertIn(f"CyrusAsha_GuiForOpenDataLoaderPDF-{version}", read_bytes("README.md").decode("utf-8"))
        self.assertIn(f"CyrusAsha_GuiForOpenDataLoaderPDF-{version}", read_bytes("windows/start-gui.bat").decode("ascii"))


if __name__ == "__main__":
    unittest.main()

"""
CyrusAsha_GuiForOpenDataLoaderPDF - GUI smoke test (optional, uses Playwright).

Opens every page in Persian and English and fails on JavaScript errors.
The test is skipped when Playwright is not installed. To enable it::

    pip install playwright
    python -m playwright install chromium
    python -m unittest test.test_gui -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional dependency
    sync_playwright = None

PAGES = ["convert", "jobs", "hybrid", "setup", "rules", "settings", "help"]


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class GuiSmokeTest(unittest.TestCase):
    """Render all pages in both languages."""

    @classmethod
    def setUpClass(cls) -> None:
        """Start the server with a temporary data folder and launch Chromium."""
        from cyrusasha_odl.app import App
        from cyrusasha_odl.server import make_server

        cls.tmp = tempfile.mkdtemp(prefix="cyrusasha-odl-gui-")
        cls.app = App(cls.tmp)
        cls.server = make_server(cls.app, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls) -> None:
        """Close the browser, stop the server, delete the data folder."""
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pages_render_without_errors(self) -> None:
        """No console errors on any page; the layout direction follows the language."""
        page = self.browser.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(self.base)
        for lang, direction in (("fa", "rtl"), ("en", "ltr")):
            page.evaluate(f"localStorage.setItem('odl.lang', '{lang}')")
            for name in PAGES:
                page.goto(self.base + "#" + name)
                page.reload()
                page.wait_for_timeout(700)
                self.assertEqual(page.get_attribute("html", "dir"), direction)
                self.assertIn("CyrusAsha_GuiForOpenDataLoaderPDF", page.inner_text("#topbar"))
        self.assertEqual(errors, [])
        page.close()

    def test_language_toggle(self) -> None:
        """The فارسی | English toggle switches direction instantly."""
        page = self.browser.new_page()
        page.goto(self.base + "#help")
        page.click(".seg button[lang=en]")
        self.assertEqual(page.get_attribute("html", "dir"), "ltr")
        page.click(".seg button[lang=fa]")
        self.assertEqual(page.get_attribute("html", "dir"), "rtl")
        self.assertTrue(page.is_visible(".site-footer a.footer-link"))
        page.close()


if __name__ == "__main__":
    unittest.main()

"""
CyrusAsha_GuiForOpenDataLoaderPDF - backend tests (standard library ``unittest`` only).

Run from the project root::

    python -m unittest discover -s test -v

No network access, Java or opendataloader-pdf installation is needed: the
tests cover validation, path safety, argument building, log helpers, the
installer helpers and an HTTP smoke test of the API with a temporary data folder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cyrusasha_odl import APP_ID, APP_NAME, CSRF_HEADER  # noqa: E402
from cyrusasha_odl.config import Settings, sanitize_hybrid_config  # noqa: E402
from cyrusasha_odl.errors import ApiError  # noqa: E402
from cyrusasha_odl.hybrid import build_server_args  # noqa: E402
from cyrusasha_odl.jobs import MAX_ARGS_CHARS, JobManager, count_pdfs, inspect_path, is_within  # noqa: E402
from cyrusasha_odl.platform_utils import clean_line, decode_output, parse_java_major  # noqa: E402
from cyrusasha_odl.rules import RuleStore, load_default_rules, validate_rules  # noqa: E402
from cyrusasha_odl.rules import test_rules as apply_rules  # noqa: E402  (renamed: not a test)
from cyrusasha_odl.setup_tasks import StepFailed, extract_archive, make_test_pdf  # noqa: E402


class TempDirTestCase(unittest.TestCase):
    """Base class that provides a temporary folder per test."""

    def setUp(self) -> None:
        """Create the temporary folder."""
        self.tmp = tempfile.mkdtemp(prefix="cyrusasha-odl-test-")

    def tearDown(self) -> None:
        """Delete the temporary folder."""
        shutil.rmtree(self.tmp, ignore_errors=True)


class PlatformHelpersTest(unittest.TestCase):
    """Parsing helpers used for Java detection and log display."""

    def test_java_major_versions(self) -> None:
        """Old (1.8) and new (11, 21, 25) version strings are parsed."""
        cases = {"1.8.0_402": 8, "11": 11, "11.0.22": 11, "17.0.10": 17, "21.0.5": 21, "25": 25, "21-ea": 21}
        for text, major in cases.items():
            self.assertEqual(parse_java_major(text), major, text)

    def test_clean_line_keeps_last_progress_state(self) -> None:
        """Carriage-return progress output keeps only the final state; ANSI codes are removed."""
        self.assertEqual(clean_line("\x1b[32m 10%\r 50%\r 100%"), " 100%")
        self.assertEqual(clean_line("a\tb\x07"), "a\tb")

    def test_decode_output_utf8(self) -> None:
        """Persian UTF-8 output is decoded correctly."""
        self.assertEqual(decode_output("سلام دنیا".encode("utf-8")), "سلام دنیا")

    def test_py_launcher_output_format(self) -> None:
        """The regular expression used for ``py -0p`` handles paths with spaces."""
        sample = (" -V:3.15 *        C:\\Users\\Ali\\AppData\\Local\\Python\\pythoncore-3.15-64\\python.exe\n"
                  " -V:3.12-arm64    D:\\Py\\Python 312\\python.exe")
        found = [m.group(1) for line in sample.splitlines()
                 for m in [re.search(r"([A-Za-z]:\\.*?\.exe)\s*$", line.strip())] if m]
        self.assertEqual(found[1], "D:\\Py\\Python 312\\python.exe")


class SettingsTest(TempDirTestCase):
    """Validation and persistence of settings and hybrid configuration."""

    def test_settings_roundtrip(self) -> None:
        """Valid settings are stored and nested hybrid options are merged."""
        path = os.path.join(self.tmp, "settings.json")
        Settings(path).update({"language": "en", "max_parallel_jobs": 2, "hybrid_server": {"port": 5003}})
        again = Settings(path)
        self.assertEqual(again.get("language"), "en")
        self.assertEqual(again.get("hybrid_server")["port"], 5003)
        self.assertEqual(again.get("hybrid_server")["ocr_engine"], "easyocr")

    def test_settings_rejects_invalid_values(self) -> None:
        """Unknown keys and invalid values raise ``bad_setting``."""
        settings = Settings(os.path.join(self.tmp, "settings.json"))
        for patch in ({"language": "de"}, {"max_parallel_jobs": 99}, {"proxy": "not a url"}, {"unknown": 1}):
            with self.assertRaises(ApiError, msg=str(patch)):
                settings.update(patch)

    def test_hybrid_config_validation(self) -> None:
        """Dangerous or out-of-range hybrid options are rejected."""
        for bad in ({"port": 0}, {"host": "a b"}, {"ocr_lang": "fa;rm -rf"}, {"psm": "99"}, {"picture_area_threshold": 2}):
            with self.assertRaises(ApiError, msg=str(bad)):
                sanitize_hybrid_config(bad)

    def test_hybrid_server_args(self) -> None:
        """``--no-ocr`` drops all OCR flags; enrichment options are passed through."""
        args = build_server_args(sanitize_hybrid_config({"ocr_mode": "off", "ocr_engine": "tesseract", "ocr_lang": "fas,eng"}))
        self.assertIn("--no-ocr", args)
        self.assertNotIn("--ocr-lang", args)
        args = build_server_args(sanitize_hybrid_config({
            "ocr_mode": "force", "ocr_engine": "tesseract", "ocr_lang": "fas, eng", "psm": "6",
            "enrich_picture_description": True, "picture_description_prompt": "Describe"}))
        self.assertIn("--force-ocr", args)
        self.assertEqual(args[args.index("--ocr-lang") + 1], "fas,eng")
        self.assertEqual(args[args.index("--picture-description-prompt") + 1], "Describe")


class RulesTest(TempDirTestCase):
    """Sanitization rule validation and storage."""

    def test_default_rules_are_valid(self) -> None:
        """The bundled upstream rule set validates and masks an IPv6 address."""
        rules = validate_rules(load_default_rules())
        self.assertGreaterEqual(len(rules), 10)
        result = apply_rules(rules, "IPv6 2001:db8:1234:5678:90ab:cdef:0:1 mail a@b.io")["result"]
        self.assertIn("email@example.com", result)
        self.assertNotIn("2001:db8", result)

    def test_invalid_rules(self) -> None:
        """Bad ids, duplicates and invalid patterns are rejected."""
        bad_sets = [
            {"rules": [{"id": "BAD ID", "pattern": "x", "replacement": "y"}]},
            {"rules": [{"id": "a", "pattern": "x", "replacement": ""}, {"id": "a", "pattern": "y", "replacement": ""}]},
            {"rules": [{"id": "a", "pattern": "(", "replacement": ""}]},
            {"nope": []},
        ]
        for data in bad_sets:
            with self.assertRaises(ApiError):
                validate_rules(data)

    def test_rule_store_blocks_path_traversal(self) -> None:
        """Rule names cannot escape the rules folder."""
        store = RuleStore(os.path.join(self.tmp, "rules"))
        with self.assertRaises(ApiError):
            store.save("../evil", {"rules": []})
        saved = store.save("ir-phone", {"rules": [{"id": "ir-phone", "pattern": r"09\d{9}", "replacement": "09XXXXXXXXX"}]})
        self.assertEqual(saved["count"], 1)
        self.assertIn("ir-phone", [r["name"] for r in store.list()])


class JobsHelpersTest(TempDirTestCase):
    """Input inspection, path safety, batching and option validation."""

    def make_manager(self) -> JobManager:
        """Create a JobManager without starting threads (enough for pure helpers)."""
        manager = JobManager.__new__(JobManager)

        class FakeEnv:
            """Environment stub: no installed package metadata."""

            @staticmethod
            def cli_options():
                """Return no metadata (accept every known option)."""
                return []

        class FakeRules:
            """Rule store stub."""

            @staticmethod
            def resolve(name):
                """Resolve nothing."""
                raise ApiError("not_found", name, 404)

        manager.env = FakeEnv()
        manager.rules = FakeRules()
        return manager

    def test_inspect_and_count(self) -> None:
        """PDFs in nested folders are counted; non-PDFs and missing paths are reported."""
        folder = os.path.join(self.tmp, "پوشه", "sub")
        os.makedirs(folder)
        make_test_pdf(os.path.join(folder, "سند.pdf"))
        with open(os.path.join(folder, "notes.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        self.assertEqual(count_pdfs(os.path.join(self.tmp, "پوشه"))[0], 1)
        self.assertEqual(inspect_path(os.path.join(folder, "notes.txt"))["error"], "not_pdf")
        self.assertEqual(inspect_path(os.path.join(self.tmp, "missing.pdf"))["error"], "not_found")
        self.assertEqual(inspect_path("relative/path.pdf")["error"], "not_absolute")

    def test_is_within(self) -> None:
        """Path containment check used for safe file serving."""
        self.assertTrue(is_within(os.path.join(self.tmp, "a", "b"), self.tmp))
        self.assertFalse(is_within(os.path.dirname(self.tmp), self.tmp))

    def test_option_args(self) -> None:
        """Options become ``--name=value``; defaults are omitted; invalid values fail."""
        manager = self.make_manager()
        args, cleaned = manager.build_option_args({
            "format": ["markdown", "json", "markdown"], "keep-line-breaks": True, "table-method": "default",
            "pages": "1,3,5-7", "password": "-secret", "hybrid": "off"})
        self.assertIn("--format=json,markdown", args)
        self.assertIn("--keep-line-breaks", args)
        self.assertIn("--password=-secret", args)
        self.assertNotIn("table-method", cleaned)
        masked, _ = manager.build_option_args({"password": "secret"}, mask=True)
        self.assertEqual(masked, ["--password=********"])
        for bad in ({"format": ["docx"]}, {"pages": "abc"}, {"threads": "0"}, {"unknown-option": True}):
            with self.assertRaises(ApiError, msg=str(bad)):
                manager.build_option_args(bad)

    def test_batches_stay_below_windows_limit(self) -> None:
        """Long lists of Persian paths are split below the command-line limit."""
        manager = self.make_manager()
        inputs = [f"C:\\Users\\علی\\Documents\\گزارش‌های سالانه\\پرونده-{i:04d}.pdf" for i in range(800)]
        batches = manager._split_batches(inputs, ["--format=json"])
        self.assertEqual(sum(len(b) for b in batches), 800)
        for batch in batches:
            self.assertLessEqual(len(json.dumps(batch + ["--format=json"])) + 200, MAX_ARGS_CHARS)


class InstallerHelpersTest(TempDirTestCase):
    """Archive extraction safety and the generated self-test PDF."""

    def test_zip_slip_is_blocked(self) -> None:
        """Archives with members outside the target folder are rejected."""
        archive = os.path.join(self.tmp, "evil.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../../outside.txt", "x")
        os.makedirs(os.path.join(self.tmp, "dest"))
        with self.assertRaises(StepFailed) as ctx:
            extract_archive(archive, os.path.join(self.tmp, "dest"))
        self.assertEqual(ctx.exception.code, "bad_archive")

    def test_self_test_pdf(self) -> None:
        """The hand-written PDF has a header, a cross-reference table and an EOF marker."""
        path = os.path.join(self.tmp, "t.pdf")
        make_test_pdf(path)
        with open(path, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertIn(b"xref", data)
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))


class ApiSmokeTest(unittest.TestCase):
    """Start the real HTTP server with a temporary data folder and call the API."""

    @classmethod
    def setUpClass(cls) -> None:
        """Start the server on a free port in a background thread."""
        from cyrusasha_odl.app import App
        from cyrusasha_odl.server import make_server

        cls.tmp = tempfile.mkdtemp(prefix="cyrusasha-odl-api-")
        cls.app = App(cls.tmp)
        cls.server = make_server(cls.app, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        """Stop the server and delete the data folder."""
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method: str, path: str, body=None, headers=None):
        """Send a request and return ``(status, parsed_json_or_text)``."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as res:
                raw = res.read().decode("utf-8")
                status = res.status
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8")
            status = err.code
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw

    def test_ping_and_info(self) -> None:
        """The instance identifies itself with the CyrusAsha product name."""
        status, data = self.call("GET", "/api/ping")
        self.assertEqual((status, data["app"]), (200, APP_ID))
        status, info = self.call("GET", "/api/info")
        self.assertEqual(info["name"], APP_NAME)

    def test_csrf_header_required(self) -> None:
        """State-changing requests without the custom header are refused."""
        status, data = self.call("POST", "/api/settings", {"language": "en"})
        self.assertEqual((status, data["error"]), (403, "csrf"))
        status, data = self.call("POST", "/api/settings", {"language": "en"}, {CSRF_HEADER: "1"})
        self.assertEqual((status, data["language"]), (200, "en"))

    def test_host_check(self) -> None:
        """Foreign Host headers are rejected (DNS-rebinding protection)."""
        status, data = self.call("GET", "/api/ping", headers={"Host": "evil.example"})
        self.assertEqual((status, data["error"]), (403, "forbidden_host"))

    def test_static_ui_and_brand(self) -> None:
        """index.html, the favicon and the brand logo are served."""
        status, html = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("CyrusAsha_GuiForOpenDataLoaderPDF", html)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path in ("/favicon.ico", "/brand/CyrusAsha_GuiForOpenDataLoaderPDF.png", "/brand/logo-full.png"):
            with opener.open(self.base + path, timeout=10) as res:
                self.assertEqual(res.headers["Content-Type"], "image/png")

    def test_unknown_job_and_traversal(self) -> None:
        """Unknown jobs give 404; static paths cannot escape the UI folder."""
        status, data = self.call("GET", "/api/jobs/20260101-000000-abcd")
        self.assertEqual((status, data["error"]), (404, "job_not_found"))
        status, _ = self.call("GET", "/../../src/cyrusasha_odl/config.py")
        self.assertEqual(status, 404)

    def test_job_validation(self) -> None:
        """Job creation validates inputs and options before queueing."""
        status, data = self.call("POST", "/api/jobs", {"inputs": []}, {CSRF_HEADER: "1"})
        self.assertEqual((status, data["error"]), (400, "no_inputs"))
        status, data = self.call("POST", "/api/jobs/preview", {"inputs": [], "options": {"format": ["docx"]}}, {CSRF_HEADER: "1"})
        self.assertEqual((status, data["error"]), (400, "bad_option"))


if __name__ == "__main__":
    unittest.main()

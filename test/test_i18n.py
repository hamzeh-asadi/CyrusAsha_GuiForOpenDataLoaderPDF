"""
CyrusAsha_GuiForOpenDataLoaderPDF - translation coverage tests.

Checks that every translation key used by the web UI exists in BOTH languages
(Persian and English), that every CLI option of the options panel has a label,
and that every error code raised by the backend has a message.

Run from the project root::

    python -m unittest discover -s test -v
"""

from __future__ import annotations

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_DIR = os.path.join(ROOT, "src", "gui", "public", "js")
BACKEND_DIR = os.path.join(ROOT, "src", "cyrusasha_odl")
sys.path.insert(0, os.path.join(ROOT, "src"))

from cyrusasha_odl.jobs import OPTION_SPEC  # noqa: E402
from cyrusasha_odl.storage import LOCATION_ERRORS  # noqa: E402

#: Keys built at runtime from a prefix plus one of these values.
DYNAMIC_KEYS = {
    "state.": ["queued", "waiting", "running", "success", "partial", "failed", "canceled"],
    "hstate.": ["stopped", "starting", "ready", "stopping", "crashed", "external"],
    "nav.": ["convert", "jobs", "hybrid", "setup", "rules", "settings", "help"],
    "group.": ["output", "text", "layout", "images", "separators", "safety", "hybrid", "performance", "other"],
    "jobs.filter.": ["all", "active", "done", "failed"],
    "jobtab.": ["outputs", "files", "log", "details"],
    "preview.": ["view", "raw", "summary"],
    "theme.": ["auto", "light", "dark"],
    "path.": ["app", "data", "venv", "java", "jobs", "uploads", "rules", "logs"],
    "action.": ["full", "java-portable", "java-winget", "java-remove", "venv", "install", "update", "hybrid",
                "rapidocr", "tesseract", "uninstall", "reset", "verify"],
    "step.": ["java", "java_portable", "java_winget", "java_remove", "venv", "pip", "package", "hybrid",
              "rapidocr", "tesseract", "uninstall", "reset", "verify"],
    "src.": ["custom", "portable", "JAVA_HOME", "PATH", "folder"],
    "jmode.": ["auto", "portable", "system", "custom"],
    "val.ocr_": ["default", "force", "off"],
    "val.device.": ["auto", "cpu", "cuda", "mps", "xpu"],
    "notify.job_": ["success", "partial", "failed", "canceled"],
    "input.err.": ["not_absolute", "not_found", "not_pdf", "no_pdfs"],
    "help.fmt.": ["json", "markdown", "html", "text", "pdf", "tagged-pdf"],
    "json.": ["file_name", "number_of_pages", "author", "title", "creation_date", "modification_date"],
    "hybrid.req": ["1", "2", "3", "4"],
    "help.quick": ["1", "2", "3", "4", "5"],
}


def load_dictionaries() -> dict:
    """Parse ``i18n.js`` and return ``{"en": set(keys), "fa": set(keys)}``.

    The file defines ``I18N = { en: {...}, fa: {...} }``; keys are single-quoted
    strings followed by a colon.
    """
    with open(os.path.join(JS_DIR, "i18n.js"), "r", encoding="utf-8") as fh:
        source = fh.read()
    en_start = source.index("  en: {")
    fa_start = source.index("  fa: {")
    end = source.index("function getLang()")
    key_re = re.compile(r"'([A-Za-z0-9_.\- ]+)':\s*'")
    return {
        "en": set(key_re.findall(source[en_start:fa_start])),
        "fa": set(key_re.findall(source[fa_start:end])),
    }


def used_keys() -> set:
    """Collect every literal ``t('key')`` call plus the dynamic keys and option labels."""
    keys = set()
    for name in os.listdir(JS_DIR):
        if not name.endswith(".js") or name == "i18n.js":
            continue
        with open(os.path.join(JS_DIR, name), "r", encoding="utf-8") as fh:
            for match in re.finditer(r"\bt\(\s*'([^']+)'\s*(\+)?", fh.read()):
                key, concatenated = match.group(1), match.group(2)
                if not concatenated:  # 'prefix' + value -> covered by DYNAMIC_KEYS
                    keys.add(key)
    for prefix, values in DYNAMIC_KEYS.items():
        keys.update(prefix + v for v in values)
    for i in range(1, 7):
        keys.update({f"help.faq{i}.q", f"help.faq{i}.a"})
    keys.update("opt." + spec["name"] for spec in OPTION_SPEC)
    return keys


def backend_error_codes() -> set:
    """Return every error code passed to ``ApiError(...)``/``StepFailed(...)`` in the backend."""
    codes = set()
    for name in os.listdir(BACKEND_DIR):
        if name.endswith(".py"):
            with open(os.path.join(BACKEND_DIR, name), "r", encoding="utf-8") as fh:
                codes.update(re.findall(r'(?:ApiError|StepFailed)\("([a-z_]+)"', fh.read()))
    # Codes built at run time: "location_" + a reason of storage.check_target()/MoveError.
    codes.discard("location_")
    codes.update("location_" + reason for reason in LOCATION_ERRORS)
    return codes | {"interrupted", "cli_failed", "hybrid_unavailable", "internal"}


class TranslationCoverageTest(unittest.TestCase):
    """Both languages must be complete."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the dictionaries once."""
        cls.dicts = load_dictionaries()

    def test_dictionaries_are_not_empty(self) -> None:
        """Sanity check of the parser."""
        self.assertGreater(len(self.dicts["en"]), 500)
        self.assertGreater(len(self.dicts["fa"]), 500)

    def test_used_keys_exist_in_both_languages(self) -> None:
        """Every key used by the UI exists in English and Persian."""
        keys = used_keys()
        for lang in ("en", "fa"):
            missing = sorted(keys - self.dicts[lang])
            self.assertEqual(missing, [], f"missing {lang} keys: {missing}")

    def test_error_codes_have_messages(self) -> None:
        """Every backend error code has an ``err.<code>`` message in both languages."""
        for lang in ("en", "fa"):
            missing = sorted(c for c in backend_error_codes() if "err." + c not in self.dicts[lang])
            self.assertEqual(missing, [], f"missing {lang} error messages: {missing}")

    def test_dynamic_keys_exist(self) -> None:
        """Keys built at run time: installer steps/actions, storage warnings and size parts."""
        from cyrusasha_odl import setup_tasks, storage

        keys = {"step." + k for k in setup_tasks.STEP_TITLES} | {"action." + k for k in setup_tasks.ACTIONS}
        keys |= {"storage.warn." + w for w in ("long_path", "non_ascii", "network", "drive_missing")}
        keys |= {"storage.part." + p for p in set(storage.MODEL_ENV.values()) | {"venv", "java", "cache", "other"}}
        from cyrusasha_odl.config import Paths

        keys |= {"path." + k for k in Paths(os.path.join(ROOT, "data")).to_dict()}
        for lang in ("en", "fa"):
            missing = sorted(keys - self.dicts[lang])
            self.assertEqual(missing, [], f"missing {lang} keys: {missing}")

    def test_languages_have_the_same_keys(self) -> None:
        """Persian may only lack keys whose English text is language neutral (e.g. PNG/JPEG)."""
        allowed = {"val.image-format.png", "val.image-format.jpeg"}
        self.assertEqual(sorted(self.dicts["en"] - self.dicts["fa"] - allowed), [])
        self.assertEqual(sorted(self.dicts["fa"] - self.dicts["en"]), [])


if __name__ == "__main__":
    unittest.main()

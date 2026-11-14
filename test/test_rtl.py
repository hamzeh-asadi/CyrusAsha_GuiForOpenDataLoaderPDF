"""
CyrusAsha_GuiForOpenDataLoaderPDF - tests of the right-to-left repair (``cyrusasha_odl.rtl``).

Run from the project root::

    python -m unittest test.test_rtl -v

The fixtures in ``test/fixtures/rtl`` are real outputs of opendataloader-pdf
2.5.21 for ``fa-sample.pdf`` (a Persian test document rendered by Chromium
with the Vazirmatn font): ``chromium/`` as produced, ``plain/`` from the same
document with a font whose letters carry no presentation forms (the way Word
or InDesign PDFs look). ``line.json`` is the ``--keep-line-breaks`` JSON the
repair uses, ``expected.json`` the text that was typed.

The optional end-to-end test runs the real CLI when the environment variable
``ODL_TEST_PYTHON`` points to a Python with opendataloader-pdf installed
(for example ``data/venv/Scripts/python.exe``) and Java is available.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cyrusasha_odl import rtl  # noqa: E402

FIXTURES = os.path.join(ROOT, "test", "fixtures", "rtl")
ZWNJ = "\u200c"


def load_expected() -> dict:
    """Return the ground truth of ``fa-sample.pdf``."""
    with open(os.path.join(FIXTURES, "expected.json"), encoding="utf-8") as fh:
        return json.load(fh)


def norm(text: str, zwnj: bool = True) -> str:
    """Collapse white space (and optionally drop ZWNJs) for comparisons.

    Args:
        text: Text to normalise.
        zwnj: Keep zero-width non-joiners.
    """
    text = re.sub(r"\s+", " ", text).strip()
    return text if zwnj else text.replace(ZWNJ, "")


class RepairedCopy:
    """Copy one fixture folder to a temporary folder and repair its outputs."""

    def __init__(self, name: str, **kwargs) -> None:
        """Copy ``test/fixtures/rtl/<name>`` and run :class:`rtl.OutputRepair` on it.

        Args:
            name: ``"chromium"`` or ``"plain"``.
            **kwargs: Extra arguments of :class:`rtl.OutputRepair`.
        """
        self.tmp = tempfile.mkdtemp(prefix="cyrusasha-rtl-test-")
        for entry in os.listdir(os.path.join(FIXTURES, name)):
            shutil.copy(os.path.join(FIXTURES, name, entry), self.tmp)
        self.repair = rtl.OutputRepair.from_file(os.path.join(self.tmp, "line.json"), **kwargs)
        self.changed = {ext: self.repair.repair_file(os.path.join(self.tmp, "out." + ext))
                        for ext in ("json", "md", "html", "txt")}

    def read(self, ext: str) -> str:
        """Return the repaired output with extension ``ext``."""
        with open(os.path.join(self.tmp, "out." + ext), encoding="utf-8", newline="") as fh:
            return fh.read()

    def contents(self) -> list:
        """Return the ``content`` strings of the repaired JSON output."""
        return [n["content"] for n in rtl.iter_text_nodes(json.loads(self.read("json")))]

    def close(self) -> None:
        """Delete the temporary folder."""
        shutil.rmtree(self.tmp, ignore_errors=True)


class BidiTest(unittest.TestCase):
    """Visual-to-logical reordering of single lines (strings taken from real outputs)."""

    def fix(self, line: str, rtl_paragraph: bool = True, persian: bool = True) -> str:
        """Reorder and normalise one line like the repair does."""
        return rtl.OutputRepair.fix_line(line, rtl_paragraph, True, persian)

    def test_heading(self) -> None:
        """Reversed letters, presentation forms and the lam-alef ligature are repaired."""
        self.assertEqual(self.fix("ﺎﺳآشورﻮﮐ ﺖﮐﺮﺷ ﮥﻧﻻﺎﺳ شراﺰﮔ"), "گزارش سالانهٔ شرکت کوروشآسا")

    def test_numbers_keep_their_order(self) -> None:
        """Digit groups, separators and phone numbers come back in reading order."""
        self.assertEqual(self.fix("ﺪﯿﺳر لﺎﯾر درﺎﯿﻠﯿﻣ ۱۲٬۵۰۰ ﻪﺑ"), "به ۱۲٬۵۰۰ میلیارد ریال رسید")
        self.assertEqual(self.fix("ﺪﯾﺮیﮕﺑ سﺎﻤﺗ ۸۸۸۸۷۷۷۷-۰۲۱ ﻦﻔﻠت"), "تلفن ۰۲۱-۸۸۸۸۷۷۷۷ تماس بگیرید")

    def test_brackets_are_unmirrored(self) -> None:
        """Mirrored brackets and guillemets of RTL runs are swapped back."""
        self.assertEqual(self.fix("(نﺎﻣﻮﺗ نﻮیﻠﯿﻣ) شوﺮﻓ"), "فروش (میلیون تومان)")
        self.assertEqual(self.fix("«ﻪﻠﺻﺎﻓﻢﯿﻧ» تﺎﻤﻠﮐ"), "کلمات «نیم‌فاصله»")

    def test_last_line_without_letters(self) -> None:
        """A line of digits and punctuation inside an RTL paragraph is reordered too."""
        self.assertEqual(self.fix(".(۱۶"), "۱۶).")

    def test_ltr_paragraph_with_rtl_word(self) -> None:
        """In a left-to-right paragraph only the RTL word is reversed."""
        self.assertEqual(self.fix("The Persian word مﻼﺳ means hello", rtl_paragraph=False),
                         "The Persian word سلام means hello")

    def test_mirror_flag(self) -> None:
        """Producers that map mirrored glyphs to the original character need no un-mirroring."""
        self.assertEqual(rtl.visual_to_logical("ﺲﯾﻮﻧ )IRB( ﻪﺑ", True, mirror=False), "ﺑﻪ (IRB) ﻧﻮﯾﺲ")
        self.assertEqual(rtl.bracket_errors("(IRB)"), 0)
        self.assertEqual(rtl.bracket_errors(")IRB("), 2)

    def test_text_without_rtl_is_untouched(self) -> None:
        """Pure left-to-right text is returned unchanged."""
        self.assertEqual(rtl.visual_to_logical("Hello (world) 1-2", True), "Hello (world) 1-2")
        self.assertEqual(rtl.normalize_text("plain text"), "plain text")


class NormalisationTest(unittest.TestCase):
    """Letters, ZWNJs, marks and Persian/Arabic differences."""

    def test_zwnj_restored_from_letter_shapes(self) -> None:
        """A final form followed by an initial form proves a lost ZWNJ."""
        self.assertEqual(rtl.normalize_text("ﻣﯽﮐﻨﺪ"), "می\u200cکند")
        self.assertEqual(rtl.normalize_text("ﺧﺎﻧﻪﻫﺎ"), "خانه\u200cها")

    def test_no_zwnj_without_evidence(self) -> None:
        """Plain letters carry no shape information, so nothing is inserted."""
        self.assertEqual(rtl.normalize_text("فایل"), "فایل")

    def test_persian_letters(self) -> None:
        """Arabic yeh/kaf become Persian only in Persian mode."""
        self.assertEqual(rtl.normalize_text("علي كتاب", persian=True), "علی کتاب")
        self.assertEqual(rtl.normalize_text("علي كتاب", persian=False), "علي كتاب")

    def test_hamza_mark_moves_behind_heh(self) -> None:
        """A hamza above that precedes a final heh (or the whole word) is put back on the heh."""
        self.assertEqual(rtl.normalize_text("نسخٔه", persian=True), "نسخه\u0654")
        self.assertEqual(rtl.normalize_text("\u0654خانه", persian=True), "خانه\u0654")

    def test_lone_mark_joins_previous_word(self) -> None:
        """An ezafe kasra the PDF placed between two words goes back to the first one."""
        self.assertEqual(rtl.normalize_text("با نام \u0650 رسمی", persian=True), "با نام\u0650 رسمی")
        self.assertEqual(rtl.normalize_text("تِهْران است", persian=True), "تِهْران است")

    def test_tatweel_removed(self) -> None:
        """Justification tatweels between letters disappear."""
        self.assertEqual(rtl.normalize_text("پیوسـت‌ها"), "پیوست‌ها")


class LigatureTest(unittest.TestCase):
    """Ligatures that a PDF maps to two letters in reading order (Word, InDesign)."""

    def check(self, observed: str, expected: str, lam_alef: bool = True) -> None:
        """Assert the repair of one Persian word."""
        self.assertEqual(rtl.fix_ligatures(observed, "fa", lam_alef), expected, observed)

    def test_lam_alef(self) -> None:
        """Reversed lam-alef glyphs are restored by word knowledge."""
        self.check("اطالعات", "اطلاعات")
        self.check("الزم", "لازم")
        self.check("تحصیالت", "تحصیلات")
        self.check("قبالً", "قبلاً")

    def test_other_ligatures(self) -> None:
        """Font ligatures such as نم, تم, نج, تح are restored too."""
        self.check("منایید", "نمایید")
        self.check("متام", "تمام")
        self.check("اجنام", "انجام")
        self.check("حتویل", "تحویل")

    def test_affixes(self) -> None:
        """Words behind prefixes and suffixes are found."""
        self.check("منیدانید", "نمیدانید")
        self.check("اطالعاتی", "اطلاعاتی")

    def test_real_words_are_kept(self) -> None:
        """Known words are never changed, even if a swap gives another word."""
        for word in ("سالم", "محله", "وکالت", "عالی", "ارسال", "مطالب", "من", "متن"):
            self.check(word, word)

    def test_lam_alef_disabled(self) -> None:
        """Documents with lam-alef presentation forms never swap ال."""
        self.check("اطالعات", "اطالعات", lam_alef=False)


class DetectionTest(unittest.TestCase):
    """Order, direction and language detection."""

    def test_order_evidence(self) -> None:
        """Visual text scores positive, logical text negative."""
        self.assertGreater(rtl.order_evidence("ﺪﻨﺳ ﻦیا ﺖﺳا هﺪﺷ ﻪﯿﻬﺗ زا ﯽﺳرﺎﻓ"), 0)
        self.assertLess(rtl.order_evidence("این سند از فارسی تهیه شده است"), 0)
        self.assertGreater(rtl.order_evidence("تسا هدش هیهت یسراف زا دنس نیا"), 0)

    def test_paragraph_direction(self) -> None:
        """Mixed lines in an RTL document stay RTL; English stays LTR."""
        self.assertTrue(rtl.paragraph_is_rtl(["HTML و JSON و Markdown ﯽﺟوﺮﺧ"], True, True))
        self.assertFalse(rtl.paragraph_is_rtl(["The Persian word مﻼﺳ means hello"], True, True))
        self.assertFalse(rtl.paragraph_is_rtl(["Only English"], True, True))

    def test_profile_language(self) -> None:
        """Persian, Arabic and Hebrew documents are told apart."""
        self.assertEqual(rtl.DocProfile([(1, "پیشگفتار این گزارش")]).language, "fa")
        self.assertEqual(rtl.DocProfile([(1, "اللغة العربية جميلة والكتابة")]).language, "ar")
        self.assertEqual(rtl.DocProfile([(1, "שלום עולם")]).language, "he")

    def test_file_has_rtl(self) -> None:
        """Output files with enough RTL letters are detected."""
        self.assertTrue(rtl.file_has_rtl(os.path.join(FIXTURES, "chromium", "out.md")))
        self.assertFalse(rtl.file_has_rtl(os.path.join(ROOT, "LICENSE")))


class HelpersTest(unittest.TestCase):
    """Line joining and the JSON writer."""

    def test_join_lines_like_opendataloader(self) -> None:
        """A hyphen at a line end is removed and the lines glued; otherwise one space."""
        self.assertEqual(rtl.join_lines(["multi-", "object", "network"]), "multiobject network")
        self.assertEqual(rtl.join_lines(["a", "b"]), "a b")
        self.assertEqual(rtl.join_lines([]), "")

    def test_dump_json_layout(self) -> None:
        """The writer reproduces the layout of opendataloader-pdf byte for byte."""
        with open(os.path.join(FIXTURES, "chromium", "out.json"), encoding="utf-8") as fh:
            original = fh.read()
        rebuilt = rtl.dump_json(json.loads(original)) + ("\n" if original.endswith("\n") else "")
        self.assertEqual(rebuilt, original)


class OutputRepairTest(unittest.TestCase):
    """Repair of real opendataloader-pdf outputs (fixtures)."""

    @classmethod
    def setUpClass(cls) -> None:
        """Repair both fixture folders once."""
        cls.expected = load_expected()
        cls.chromium = RepairedCopy("chromium")
        cls.plain = RepairedCopy("plain")

    @classmethod
    def tearDownClass(cls) -> None:
        """Delete the temporary copies."""
        cls.chromium.close()
        cls.plain.close()

    def test_all_outputs_changed(self) -> None:
        """JSON, Markdown, HTML and text outputs are rewritten."""
        self.assertEqual(self.chromium.changed, {"json": True, "md": True, "html": True, "txt": True})

    def test_chromium_text_matches_ground_truth(self) -> None:
        """Every element of the Chromium PDF equals the typed text (two ZWNJs are not in the PDF)."""
        contents = [norm(c, zwnj=False) for c in self.chromium.contents()]
        for text in self.expected["elements"]:
            self.assertIn(norm(text, zwnj=False), contents)
        exact = sum(1 for text in self.expected["elements"] if norm(text) in map(norm, self.chromium.contents()))
        self.assertGreaterEqual(exact, 7)

    def test_markdown_and_text_outputs(self) -> None:
        """Markdown and text contain the repaired paragraphs."""
        paragraph = norm(self.expected["elements"][2])
        self.assertIn(paragraph, norm(self.chromium.read("md")))
        self.assertIn(paragraph, norm(self.chromium.read("txt")))
        self.assertNotIn("ﺖﮐﺮﺷ", self.chromium.read("md"))  # no presentation forms left

    def test_tables_start_on_the_right(self) -> None:
        """RTL tables list their columns from right to left in every format."""
        self.assertIn("|فصل|فروش (میلیون تومان)|رشد|", self.chromium.read("md"))
        self.assertIn("فصل\tفروش (میلیون تومان)\tرشد", self.chromium.read("txt"))
        html = self.chromium.read("html")
        table_html = html[html.index("<table"):html.index("</table>")]
        self.assertIn('dir="rtl"', table_html[:table_html.index(">")])
        self.assertLess(table_html.index("فصل"), table_html.index("رشد"))
        table = next(rtl._iter_tables(json.loads(self.chromium.read("json"))))
        first_row = table["rows"][0]["cells"]
        self.assertEqual([c["column number"] for c in first_row], [1, 2, 3])
        self.assertEqual(first_row[0]["kids"][0]["content"], "فصل")
        self.assertEqual(self.chromium.repair.stats["tables"], 1)

    def test_html_language_and_direction(self) -> None:
        """The HTML document is marked Persian and right-to-left."""
        html = self.chromium.read("html")
        self.assertIn('<html lang="fa" dir="rtl">', html)
        self.assertIn('<p dir="auto">', html)
        self.assertIn("<title>fa-test.pdf</title>", html)  # file names are never touched

    def test_plain_letter_pdf(self) -> None:
        """PDFs without presentation forms (Word, InDesign) are repaired as well."""
        contents = [norm(c, zwnj=False) for c in self.plain.contents()]
        hits = sum(1 for text in self.expected["elements"] if norm(text, zwnj=False) in contents)
        self.assertGreaterEqual(hits, 7)
        joined = " ".join(contents)
        for word in ("محصولات", "اطلاعات", "سالانهٔ", "للاختبار"):
            self.assertIn(word, joined)

    def test_english_document_untouched(self) -> None:
        """A document without RTL text is not changed at all."""
        doc = {"kids": [{"type": "paragraph", "page number": 1, "content": "Hello\nworld (1)"}]}
        repair = rtl.OutputRepair(doc)
        markdown = "# Title\n\nHello world (1)\n\n|a|b|\n|---|---|\n|1|2|\n"
        self.assertEqual(repair.repair_markup(markdown, "md"), markdown)
        html = '<html lang="und">\n<body>\n<p>Hello world (1)</p>\n</body>\n</html>'
        self.assertEqual(repair.repair_markup(html, "html"), html)
        text = json.dumps({"kids": [{"type": "paragraph", "content": "Hello world (1)"}]})
        self.assertEqual(json.loads(repair.repair_json(text)), json.loads(text))

    def test_keep_line_breaks_mode(self) -> None:
        """With --keep-line-breaks the lines of the line JSON itself are repaired one by one."""
        tmp = tempfile.mkdtemp(prefix="cyrusasha-rtl-test-")
        try:
            path = os.path.join(tmp, "out.json")
            shutil.copy(os.path.join(FIXTURES, "chromium", "line.json"), path)
            repair = rtl.OutputRepair.from_file(path, keep_breaks=True)
            self.assertTrue(repair.repair_file(path))
            with open(path, encoding="utf-8") as fh:
                contents = [n["content"] for n in rtl.iter_text_nodes(json.load(fh))]
            joined = [norm(c) for c in contents]
            self.assertIn(norm(self.expected["elements"][2]), joined)
            self.assertTrue(any("\n" in c for c in contents))  # line breaks are kept
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_page_separators_are_kept(self) -> None:
        """User-defined page separators are never reordered."""
        doc = {"kids": [{"type": "paragraph", "page number": 1, "content": "ﻢﻼﺳ"}]}
        repair = rtl.OutputRepair(doc, separators=["--- صفحه %page-number% ---"])
        self.assertEqual(repair.repair_markup("--- صفحه 1 ---\n\nﻢﻼﺳ\n", "md"), "--- صفحه 1 ---\n\nسلام\n")


class JobIntegrationTest(unittest.TestCase):
    """The repair step of the job manager (without running the CLI)."""

    def test_option_is_gui_only(self) -> None:
        """``rtl-fix`` is validated and stored but never passed to the CLI."""
        from cyrusasha_odl.errors import ApiError
        from cyrusasha_odl.jobs import GUI_OPTIONS, JobManager

        manager = JobManager.__new__(JobManager)

        class FakeEnv:
            """Environment stub with the metadata of an installed CLI."""

            @staticmethod
            def cli_options():
                """Return a minimal option list (``rtl-fix`` is not a CLI option)."""
                return [{"name": "format", "type": "string"}, {"name": "keep-line-breaks", "type": "boolean"}]

        manager.env = FakeEnv()
        self.assertIn("rtl-fix", GUI_OPTIONS)
        args, cleaned = manager.build_option_args({"rtl-fix": "off", "format": ["json"]})
        self.assertEqual(args, ["--format=json"])
        self.assertEqual(cleaned["rtl-fix"], "off")
        self.assertNotIn("rtl-fix", manager.build_option_args({"rtl-fix": "auto"})[1])  # default
        with self.assertRaises(ApiError):
            manager.build_option_args({"rtl-fix": "sometimes"})

    def test_repair_step_of_a_job(self) -> None:
        """A finished job with RTL output (keep-line-breaks + JSON) is repaired in place."""
        from cyrusasha_odl.jobs import Job, JobManager

        tmp = tempfile.mkdtemp(prefix="cyrusasha-rtl-job-")
        try:
            output = os.path.join(tmp, "out.json")
            shutil.copy(os.path.join(FIXTURES, "chromium", "line.json"), output)
            job = Job("20260101-000000-abcd", tmp)
            job.file_results = [{"name": "fa-sample.pdf", "source": os.path.join(FIXTURES, "fa-sample.pdf"),
                                 "files": [output], "outputs": 1, "ok": True}]
            manager = JobManager.__new__(JobManager)
            manager.on_change = lambda: None

            class FakePaths:
                """Paths stub."""

                tmp_dir = tmp

            manager.paths = FakePaths()
            manager._repair_rtl(job, {"keep-line-breaks": True, "format": ["json"]})
            self.assertEqual(job.rtl.get("files"), 1)
            self.assertEqual(job.rtl.get("languages"), ["fa"])
            self.assertEqual(job.phase, "")
            self.assertEqual(job.file_results[0]["rtl"]["language"], "fa")
            with open(output, encoding="utf-8") as fh:
                self.assertIn("گزارش سالانهٔ شرکت", fh.read())
            self.assertIn("RTL repair of fa-sample.pdf", "\n".join(job.get_log().tail(10)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_repair_disabled(self) -> None:
        """``rtl-fix: off`` leaves the outputs alone."""
        from cyrusasha_odl.jobs import Job, JobManager

        tmp = tempfile.mkdtemp(prefix="cyrusasha-rtl-job-")
        try:
            output = os.path.join(tmp, "out.json")
            shutil.copy(os.path.join(FIXTURES, "chromium", "line.json"), output)
            with open(output, encoding="utf-8") as fh:
                before = fh.read()
            job = Job("20260101-000000-abce", tmp)
            job.file_results = [{"name": "x.pdf", "source": "x.pdf", "files": [output], "outputs": 1, "ok": True}]
            manager = JobManager.__new__(JobManager)
            manager._repair_rtl(job, {"rtl-fix": "off", "keep-line-breaks": True})
            with open(output, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), before)
            self.assertEqual(job.rtl, {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(os.environ.get("ODL_TEST_PYTHON"), "set ODL_TEST_PYTHON to a Python with opendataloader-pdf")
class EndToEndTest(unittest.TestCase):
    """Convert ``fa-sample.pdf`` with the real CLI and repair the result."""

    def test_real_cli(self) -> None:
        """First run + line run + repair give the typed Persian text."""
        python = os.environ["ODL_TEST_PYTHON"]
        tmp = tempfile.mkdtemp(prefix="cyrusasha-rtl-e2e-")
        try:
            pdf = os.path.join(FIXTURES, "fa-sample.pdf")
            out, lines = os.path.join(tmp, "out"), os.path.join(tmp, "lines")
            base = [python, "-X", "utf8", "-m", "opendataloader_pdf", pdf]
            subprocess.run(base + [f"--output-dir={out}", "--format=json,markdown,html,text"], check=True,
                           capture_output=True, timeout=300)
            subprocess.run(base + [f"--output-dir={lines}", "--format=json", "--keep-line-breaks",
                                   "--image-output=off"], check=True, capture_output=True, timeout=300)
            repair = rtl.OutputRepair.from_file(os.path.join(lines, "fa-sample.json"))
            for ext in ("json", "md", "html", "txt"):
                self.assertTrue(repair.repair_file(os.path.join(out, "fa-sample." + ext)), ext)
            with open(os.path.join(out, "fa-sample.md"), encoding="utf-8") as fh:
                markdown = norm(fh.read())
            for text in load_expected()["elements"][2:5]:
                self.assertIn(norm(text), markdown)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

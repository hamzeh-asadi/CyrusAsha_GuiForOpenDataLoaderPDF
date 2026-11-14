"""
Repair of right-to-left text (Persian, Arabic, Hebrew) in opendataloader-pdf outputs.

Why this module exists
----------------------
opendataloader-pdf builds every text line by sorting the text pieces of a PDF
page from left to right. For right-to-left scripts this yields the *visual*
order, so the outputs contain:

* reversed lines - ``"ﺪﻨﮐﯽم"`` instead of ``"می‌کند"``, the words of a
  sentence in reverse order and mirrored brackets;
* Arabic *presentation forms* (the contextual letter shapes the font drew,
  e.g. ``ﺳ ﺴ ﺲ`` instead of ``س``), which break searching and AI pipelines;
* lost zero-width non-joiners (``نیم‌فاصله``);
* table columns listed from left to right although an RTL table starts on
  the right.

The upstream project documents this as a known limitation ("does not perform
RTL shaping or visual reordering").

How the repair works
--------------------
1. The job runs normally. Afterwards the outputs of every PDF are checked for
   right-to-left letters (:func:`file_has_rtl`).
2. For each PDF with RTL text the CLI runs a second, cheap time with
   ``--keep-line-breaks --format json --image-output off`` (see
   ``JobManager._line_pass``). This JSON reveals where every line of every
   element ends - the information that is lost once lines are joined. Both
   runs produce the same elements in the same order (only the line separators
   differ), so every element of the real outputs can be matched with its lines.
3. Each line is converted from visual to logical order with the Unicode
   Bidirectional Algorithm (:func:`visual_to_logical`), brackets are
   un-mirrored, presentation forms become normal letters, zero-width
   non-joiners are restored where the letter shapes prove them and - for
   Persian - Arabic ``ي``/``ك`` become Persian ``ی``/``ک``
   (:func:`normalize_text`). Ligatures that the PDF maps to two letters
   (``لا``, ``نم`` ...) end up swapped after reversing; they are restored with
   a word list (:func:`fix_ligatures`). Whether a text really is in visual order is
   detected from the letter shapes and from frequent words
   (:func:`order_evidence`), so text that is already correct (for example
   from the hybrid AI backend) is left alone.
4. The repaired lines are joined exactly like opendataloader-pdf joins them
   and written back into the JSON, Markdown, HTML and text outputs
   (:class:`OutputRepair`). Tables whose content is mainly right-to-left get
   their columns in right-to-left order, HTML gets ``lang``/``dir`` attributes.

Only the Python standard library (:mod:`unicodedata`) is used.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import unicodedata
from functools import lru_cache
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #
#: Values of the GUI option ``rtl-fix``: detect automatically, always treat RTL
#: text as visual order, or never touch the outputs.
RTL_MODES = ("auto", "always", "off")

#: Output files that contain text and can be repaired.
TEXT_EXTENSIONS = (".json", ".md", ".html", ".txt")

#: A document needs at least this many right-to-left letters to be repaired.
MIN_RTL_LETTERS = 10

#: Characters that opendataloader-pdf removes at a line end when it joins lines
#: (``TextChunkUtils.HYPHENATION_SIGNS``).
HYPHENATION_SIGNS = ("-", "\u2014", "\u00ad")

ZWNJ = "\u200c"
TATWEEL = "\u0640"
#: Placeholder for protected markup (bidi class ON, never part of real text).
OBJECT_MARK = "\ufffc"

#: Characters with right-to-left letters (Hebrew, Arabic, Persian, Urdu,
#: presentation forms). Digits and punctuation are deliberately excluded.
_RTL_CHAR_RE = re.compile(
    "[\u05d0-\u05ea\u05f0-\u05f2\u0620-\u064a\u066e-\u06d3\u06d5\u06ee\u06ef\u06fa-\u06ff"
    "\u0750-\u077f\u08a0-\u08c9\ufb1d-\ufb4f\ufb50-\ufbb1\ufbd3-\ufd3d\ufd50-\ufdfb\ufe70-\ufefc]")

#: Pairs of characters that the bidi algorithm draws mirrored in RTL text.
_MIRROR_PAIRS = "()[]{}<>«»‹›⁅⁆⁽⁾₍₎〈〉《》「」『』【】〔〕〖〗〘〙〚〛⟨⟩⟪⟫≤≥≦≧≪≫"
MIRROR: Dict[str, str] = {}
for _i in range(0, len(_MIRROR_PAIRS), 2):
    MIRROR[_MIRROR_PAIRS[_i]] = _MIRROR_PAIRS[_i + 1]
    MIRROR[_MIRROR_PAIRS[_i + 1]] = _MIRROR_PAIRS[_i]

#: Frequent short words in logical order. Seeing them proves logical order,
#: seeing them reversed proves visual order. Words whose reversal is another
#: frequent word (``اما``, ``لا``/``ال``, ``מה``/``הם`` ...) are left out.
_FREQUENT_WORDS = """
از به در که این را با است برای های آن تا شده می کند شود بود خود نیز باید دارد اگر
في من على إلى أن التي الذي عن هذا كان قد ذلك هو هي لم أو ثم حتى عند بعد
של את על הוא היא עם זה כי גם או אם כל יש אני בין
""".split()

#: Hebrew letters that only appear at the end of a word.
_HEBREW_FINALS = "ךםןףץ"

#: Arabic letters that only appear at the end of a word.
_ARABIC_FINALS = "ةى"

#: Letters that only Persian uses (plus Persian digits) - for language detection.
_PERSIAN_ONLY = set("پچژگیک۰۱۲۳۴۵۶۷۸۹") | {chr(c) for c in range(0xFB56, 0xFB5A)} | \
    {chr(c) for c in range(0xFB7A, 0xFB7E)} | {chr(c) for c in range(0xFB8A, 0xFB96)} | \
    {chr(c) for c in range(0xFBFC, 0xFC00)}

#: Letters that point to Arabic rather than Persian (teh marbuta, hamza on alef, Arabic-Indic digits).
_ARABIC_ONLY = set("ةأإ٠١٢٣٤٥٦٧٨٩") | {"\ufe93", "\ufe94", "\ufe83", "\ufe84", "\ufe87", "\ufe88"}

#: Arabic letters that Persian writes differently (``ۀ`` is written as ``ه`` + hamza above).
_PERSIAN_LETTERS = str.maketrans({"\u064a": "\u06cc", "\u0649": "\u06cc", "\u0643": "\u06a9",
                                  "\u06c0": "\u0647\u0654"})

#: Folding used to compare words with :data:`_FREQUENT_WORDS`.
_FOLD = str.maketrans({"\u064a": "\u06cc", "\u0649": "\u06cc", "\u0643": "\u06a9", "\u0623": "\u0627",
                       "\u0625": "\u0627", "\u0622": "\u0627", "\u0629": "\u0647", ZWNJ: None})

_STRONG = ("L", "R", "AL")
_NEUTRALS = ("B", "S", "WS", "ON", "BN")
_EXPLICIT = ("LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI")

#: Ranked word lists of :func:`fix_ligatures` (built by ``scripts/build-rtl-lexicon.py``).
WORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "rtl-words.txt.gz")

_ALEFS = "\u0627\u0622\u0623\u0625"
_LAM = "\u0644"
#: Second letters of the optional two-letter ligatures of many Arabic fonts
#: (``نم``, ``تم``, ``لح``, ``نج`` ...); the first letter is any joining letter.
_LIGATURE_SECONDS = "\u0645\u062c\u0686\u062d\u062e"
#: Word-final ligatures such as ``تن``, ``بی`` (first letter, second letter).
_FINAL_FIRSTS = "\u0628\u067e\u062a\u062b\u0646\u06cc\u064a\u0626"
_FINAL_SECONDS = "\u0646\u06cc\u064a"
#: Arabic-script words including marks, tatweel and ZWNJ.
_ARABIC_WORD = re.compile("[\u0621-\u063a\u0640-\u065f\u0670-\u06d3\u06d5\u200c]+")
_NOT_LETTER = re.compile("[\u064b-\u065f\u0670\u0640\u200c]")
#: Prefixes and suffixes tried when a word is unknown.
_AFFIXES = {
    "fa": (("", "می", "نمی", "بی", "ب", "ن", "با"),
           ("", "ها", "های", "هایی", "هایش", "هایشان", "ی", "یی", "ای", "ات", "اش", "ش", "م", "ت", "مان", "تان",
            "شان", "تر", "ترین", "ان", "یان", "گان", "ند", "ید", "یم", "ست", "است")),
    "ar": (("", "ال", "وال", "بال", "فال", "كال", "لل", "و", "ف", "ب", "ك", "ل"),
           ("", "ة", "ه", "ها", "هم", "هما", "هن", "ك", "كم", "نا", "ي", "ات", "ون", "ين", "ان")),
}

_MD_ROW = re.compile(r"^\|.*\|\s*$")
_MD_SEP = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_MD_PREFIX = re.compile(r"^(\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)?)")
_MD_TOKENS = re.compile(r"!\[[^\]]*\]\(<[^>]*>\)|!\[[^\]]*\]\([^)]*\)|\[[^\]]*\]\(<[^>]*>\)|\[[^\]]*\]\([^)]*\)"
                        r"|<[^>]+>|&#?[A-Za-z0-9]+;|\\.")
_HTML_TOKENS = re.compile(r"&#?[A-Za-z0-9]+;")
_HTML_SPLIT = re.compile(r"(<[^>]+>)")
_HTML_TABLE = re.compile(r"(<table\b[^>]*>)(.*?)</table>", re.S | re.I)
_HTML_ROW = re.compile(r"(<tr\b[^>]*>)(.*?)(</tr>)", re.S | re.I)
_HTML_CELL = re.compile(r"<(t[dh])\b[^>]*>.*?</\1>", re.S | re.I)
_HTML_BLOCK = re.compile(r"<(p|h[1-6]|li|td|th|caption|figcaption)\b([^>]*)>", re.I)
_HTML_ROOT = re.compile(r"<html\b([^>]*)>", re.I)
_WORD = re.compile(r"\S+")
#: Presentation forms (contextual letter shapes) - only text with them needs the ZWNJ analysis.
_PRESENTATION_RE = re.compile("[\ufb50-\ufdff\ufe70-\ufefc]")
#: A combining mark at the start of a word (see :func:`_fix_leading_marks`).
_LEADING_MARK_RE = re.compile("(?:^|\\s)[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
#: Marks standing alone after an Arabic-script word: ``"نام ِ رسمی"``.
_LONE_MARK_RE = re.compile("(?<=[\u0620-\u06d5\u200c])\\s+([\u064b-\u065f\u0670]+)(?=\\s|$)")
#: Characters counted by :func:`bracket_errors`.
_BRACKETS = {"(": (0, 1), ")": (0, -1), "[": (1, 1), "]": (1, -1), "{": (2, 1), "}": (2, -1),
             "\u00ab": (3, 1), "\u00bb": (3, -1)}
_BRACKET_RE = re.compile(r"[()\[\]{}\u00ab\u00bb]")
_SPACES = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Character helpers                                                           #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8192)
def bidi_class(ch: str) -> str:
    """Return the Unicode bidirectional class of ``ch``.

    Args:
        ch: A single character.

    Returns:
        The class (``"L"``, ``"R"``, ``"AL"``, ``"EN"`` ...); unassigned code
        points count as ``"L"``.
    """
    return unicodedata.bidirectional(ch) or "L"


def has_rtl(text: str) -> bool:
    """Return ``True`` if ``text`` contains at least one right-to-left letter.

    Args:
        text: Any text.
    """
    return bool(text) and _RTL_CHAR_RE.search(text) is not None


def count_rtl(text: str) -> int:
    """Count the right-to-left letters of ``text`` (fast, regex based).

    Args:
        text: Any text.
    """
    return sum(1 for _ in _RTL_CHAR_RE.finditer(text)) if text else 0


def letter_counts(text: str) -> Tuple[int, int]:
    """Count right-to-left and left-to-right letters.

    Digits, punctuation and spaces are ignored because they have no
    direction of their own.

    Args:
        text: Any text.

    Returns:
        ``(rtl_letters, ltr_letters)``.
    """
    rtl = ltr = 0
    for ch in text:
        if ch.isalpha():
            kind = bidi_class(ch)
            if kind in ("R", "AL"):
                rtl += 1
            elif kind == "L":
                ltr += 1
    return rtl, ltr


@lru_cache(maxsize=8192)
def presentation_info(ch: str) -> Optional[Tuple[str, str]]:
    """Describe an Arabic presentation form.

    Args:
        ch: A single character.

    Returns:
        ``(form, letters)`` - ``form`` is ``"initial"``, ``"medial"``,
        ``"final"``, ``"isolated"`` or ``"compat"`` and ``letters`` the normal
        letter(s) it stands for (ligatures such as ``ﻻ`` give two letters) -
        or ``None`` if ``ch`` is not a presentation form.
    """
    code = ord(ch)
    if not (0xFB50 <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFC):
        return None
    decomposition = unicodedata.decomposition(ch)
    if not decomposition.startswith("<"):
        return None
    form = decomposition.split(" ", 1)[0].strip("<>")
    letters = unicodedata.normalize("NFKC", ch)
    if form == "compat":  # spacing diacritics such as U+FE70 decompose to "space + mark"
        letters = letters.strip()
    return form, letters


@lru_cache(maxsize=1)
def _joining_sets() -> Tuple[frozenset, frozenset]:
    """Derive the joining behaviour of Arabic letters from the presentation forms.

    A letter that has an initial or medial form can connect to the *next*
    letter (dual-joining); a letter with a final or medial form can connect to
    the *previous* letter.

    Returns:
        ``(joins_next, joins_previous)`` as sets of letters.
    """
    joins_next, joins_previous = set(), set()
    for code in list(range(0xFB50, 0xFE00)) + list(range(0xFE70, 0xFEFD)):
        info = presentation_info(chr(code))
        if not info or len(info[1]) != 1:
            continue
        if info[0] in ("initial", "medial"):
            joins_next.add(info[1])
        if info[0] in ("final", "medial"):
            joins_previous.add(info[1])
    return frozenset(joins_next), frozenset(joins_previous)


# --------------------------------------------------------------------------- #
# Unicode Bidirectional Algorithm (the parts needed for one line of PDF text)  #
# --------------------------------------------------------------------------- #
def resolve_levels(text: str, base: int) -> List[int]:
    """Compute the embedding level of every character (UAX #9, rules W1-W7, N1-N2, I1-I2, L1).

    Explicit embeddings and isolates never occur in text extracted from PDFs;
    they are treated as boundary neutrals. Bracket pairs (rule N0) are
    resolved by the general neutral rules, which gives the same result for
    normal text.

    Args:
        text: One line of text.
        base: Paragraph level - ``1`` for right-to-left, ``0`` for left-to-right.

    Returns:
        A list of levels (odd = right-to-left), one per character.
    """
    count = len(text)
    original = [bidi_class(ch) for ch in text]
    types = ["BN" if t in _EXPLICIT else t for t in original]
    sos = "R" if base % 2 else "L"

    # W1: a non-spacing mark takes the type of the character before it.
    previous = sos
    for i, kind in enumerate(types):
        if kind == "NSM":
            types[i] = previous
        elif kind != "BN":
            previous = kind
    # W2: European digits after Arabic letters are Arabic numbers.
    last = sos
    for i, kind in enumerate(types):
        if kind in _STRONG:
            last = kind
        elif kind == "EN" and last == "AL":
            types[i] = "AN"
    # W3: Arabic letters are right-to-left.
    types = ["R" if kind == "AL" else kind for kind in types]
    # W4: a single separator between two numbers of the same kind joins them.
    for i in range(1, count - 1):
        kind, before, after = types[i], types[i - 1], types[i + 1]
        if kind == "ES" and before == "EN" and after == "EN":
            types[i] = "EN"
        elif kind == "CS" and before == after and before in ("EN", "AN"):
            types[i] = before
    # W5: currency/percent signs next to European numbers belong to them.
    i = 0
    while i < count:
        if types[i] != "ET":
            i += 1
            continue
        j = i
        while j < count and types[j] == "ET":
            j += 1
        if (i > 0 and types[i - 1] == "EN") or (j < count and types[j] == "EN"):
            types[i:j] = ["EN"] * (j - i)
        i = j
    # W6: remaining separators and terminators are neutral.
    types = ["ON" if kind in ("ES", "ET", "CS") else kind for kind in types]
    # W7: European numbers in a left-to-right context are left-to-right.
    last = sos
    for i, kind in enumerate(types):
        if kind in ("L", "R"):
            last = kind
        elif kind == "EN" and last == "L":
            types[i] = "L"
    # N1/N2: neutrals between two strong types of the same direction take it,
    # otherwise the paragraph direction (numbers count as right-to-left).
    i = 0
    while i < count:
        if types[i] not in _NEUTRALS:
            i += 1
            continue
        j = i
        while j < count and types[j] in _NEUTRALS:
            j += 1
        before = sos if i == 0 else ("R" if types[i - 1] in ("R", "EN", "AN") else "L")
        after = sos if j == count else ("R" if types[j] in ("R", "EN", "AN") else "L")
        types[i:j] = [before if before == after else sos] * (j - i)
        i = j
    # I1/I2: implicit levels.
    levels = []
    for kind in types:
        if base % 2 == 0:
            levels.append(base + (1 if kind == "R" else 2 if kind in ("AN", "EN") else 0))
        else:
            levels.append(base + (1 if kind in ("L", "EN", "AN") else 0))
    # L1: separators and trailing white space get the paragraph level.
    trailing = True
    for i in range(count - 1, -1, -1):
        kind = original[i]
        if kind in ("S", "B"):
            levels[i] = base
            trailing = True
        elif trailing and kind in ("WS", "BN") + _EXPLICIT:
            levels[i] = base
        else:
            trailing = False
    return levels


def reorder_indices(levels: Sequence[int]) -> List[int]:
    """Apply rule L2: reverse runs from the highest level down to the lowest odd level.

    Args:
        levels: Embedding levels of the characters.

    Returns:
        The character indices in display order.
    """
    order = list(range(len(levels)))
    odd = [level for level in levels if level % 2]
    if not odd:
        return order
    for level in range(max(levels), min(odd) - 1, -1):
        i = 0
        while i < len(order):
            if levels[order[i]] < level:
                i += 1
                continue
            j = i
            while j < len(order) and levels[order[j]] >= level:
                j += 1
            order[i:j] = order[i:j][::-1]
            i = j
    return order


def reorder(text: str, rtl: bool, mirror: bool = True) -> Tuple[str, List[int]]:
    """Reorder one line between visual and logical order.

    Reordering is (for real text) its own inverse: applying the bidi
    algorithm to a line in visual order restores its logical order.

    Args:
        text: One line without line breaks.
        rtl: ``True`` if the paragraph is right-to-left.
        mirror: Swap brackets at right-to-left levels (``)`` becomes ``(``).
            Right for PDFs whose text maps mirrored glyphs to what they show
            (Chrome, Skia); wrong for producers that map them to the original
            character (InDesign) - see :func:`bracket_errors`.

    Returns:
        ``(reordered_text, permutation)`` - ``permutation[k]`` is the index in
        ``text`` of the ``k``-th output character.
    """
    levels = resolve_levels(text, 1 if rtl else 0)
    order = reorder_indices(levels)
    chars = [MIRROR.get(text[i], text[i]) if mirror and levels[i] % 2 else text[i] for i in order]
    return "".join(chars), order


def bracket_errors(text: str) -> int:
    """Count unmatched brackets and quotes (``( ) [ ] { } « »``) in logical text.

    Used to choose between mirrored and unmirrored brackets: in correct text
    an opening bracket comes before its closing partner.

    Args:
        text: Text in logical order.

    Returns:
        Number of closing brackets without partner plus opening brackets left open.
    """
    depth = [0, 0, 0, 0]
    errors = 0
    for match in _BRACKET_RE.finditer(text):
        kind, step = _BRACKETS[match.group(0)]
        if step > 0:
            depth[kind] += 1
        elif depth[kind]:
            depth[kind] -= 1
        else:
            errors += 1
    return errors + sum(depth)


def visual_to_logical(text: str, rtl: bool, mirror: bool = True) -> str:
    """Convert one line from visual (left-to-right glyph) order to logical order.

    Args:
        text: One line as extracted by opendataloader-pdf.
        rtl: Paragraph direction (see :func:`paragraph_is_rtl`).
        mirror: Un-mirror brackets (see :func:`reorder`).

    Returns:
        The line in reading order (unchanged if it has no RTL letters).
    """
    if not has_rtl(text):
        return text
    return reorder(text, rtl, mirror)[0]


# --------------------------------------------------------------------------- #
# Letter normalisation                                                        #
# --------------------------------------------------------------------------- #
def _needs_zwnj(first: Tuple[str, Optional[str]], second: Tuple[str, Optional[str]]) -> bool:
    """Decide whether a zero-width non-joiner was lost between two characters.

    Two letters that can connect but were drawn unconnected (a final or
    isolated form before, or an initial or isolated form after) prove that
    the original text had a ZWNJ there (``می‌خواهم``, ``خانه‌ها``). Letters
    without a presentation form carry no evidence.

    Args:
        first: ``(letters, form)`` of the first character (``form`` is ``None``
            for normal letters).
        second: ``(letters, form)`` of the following character.

    Returns:
        ``True`` if a ZWNJ must be inserted.
    """
    (text_a, form_a), (text_b, form_b) = first, second
    if (form_a is None and form_b is None) or not text_a or not text_b:
        return False
    joins_next, joins_previous = _joining_sets()
    if text_a[-1] not in joins_next or text_b[0] not in joins_previous:
        return False
    a_connects = None if form_a is None else form_a in ("initial", "medial")
    b_connects = None if form_b is None else form_b in ("medial", "final")
    if a_connects is False and b_connects is not True:
        return True
    return b_connects is False and a_connects is not True


def _fix_leading_marks(text: str) -> str:
    """Move combining marks that ended up at the start of a word.

    Some PDFs position marks such as the hamza of ``خانهٔ`` at the right edge of
    the word, so after reordering the mark precedes the word. A hamza above
    belongs to a final ``ه``; other marks are attached to the first letter.
    A hamza that ended up just before a final ``ه`` (``ٔه``, from a glyph that
    was mapped to two letters) is moved behind it, and a mark standing alone
    between two words is attached to the previous word.

    Args:
        text: Text in logical order.

    Returns:
        The text with marks after their letters.
    """
    def repair(match: "re.Match[str]") -> str:
        """Fix one word."""
        word = match.group(0)
        count = 0
        while count < len(word) and unicodedata.category(word[count]) == "Mn":
            count += 1
        if count == 0 or count == len(word) or not has_rtl(word[count]):
            return word
        marks, rest = word[:count], word[count:]
        end = len(rest)
        while end > 0 and not rest[end - 1].isalpha():
            end -= 1
        if "\u0654" in marks and end and rest[end - 1] in "\u0647\u06c0\u0629\u06d5":
            return rest[:end] + marks + rest[end:]
        return rest[0] + marks + rest[1:]

    if _LEADING_MARK_RE.search(text):
        text = _WORD.sub(repair, text)
        # A mark that stands alone between two words (an ezafe kasra the PDF
        # placed apart: "نام ِ رسمی") belongs to the end of the previous word.
        text = _LONE_MARK_RE.sub(r"\1", text)
    # A glyph "هٔ" mapped to two letters is reversed to "ٔه": put the hamza back on the heh.
    return re.sub("\u0654([\u0647\u0629\u06d5])(?![\u0621-\u064a\u0671-\u06d3])", "\\1\u0654", text)


def normalize_text(text: str, persian: bool = False) -> str:
    """Normalise right-to-left text that is already in logical order.

    * presentation forms become normal letters (``ﺳﻼﻡ`` → ``سلام``, ``ﻻ`` → ``لا``);
    * lost zero-width non-joiners are restored where the shapes prove them;
    * tatweels (kashida used for justification) are removed;
    * marks at the start of a word are moved behind their letter;
    * for Persian, Arabic ``ي ى ك`` become Persian ``ی ی ک`` and ``ۀ`` becomes ``هٔ``.

    Args:
        text: Text in logical order.
        persian: Apply the Persian letter normalisation.

    Returns:
        The normalised text.
    """
    if not has_rtl(text):
        return text
    if _PRESENTATION_RE.search(text):
        tokens: List[Tuple[str, Optional[str]]] = []
        for ch in text:
            info = presentation_info(ch)
            tokens.append((info[1], info[0]) if info else (ch, None))
        parts: List[str] = []
        for index, token in enumerate(tokens):
            parts.append(token[0])
            if index + 1 < len(tokens) and _needs_zwnj(token, tokens[index + 1]):
                parts.append(ZWNJ)
        result = "".join(parts)
    else:
        result = text  # plain letters carry no shape information
    if TATWEEL in result:
        result = re.sub("(?<=[\u0620-\u06ff\u200c])\u0640+|\u0640+(?=[\u0620-\u06ff])", "", result)
    if persian:
        result = result.translate(_PERSIAN_LETTERS)
    return _fix_leading_marks(result)


@lru_cache(maxsize=1)
def _word_ranks() -> Dict[str, Dict[str, int]]:
    """Load the ranked word lists (``{language: {word: rank}}``, rank 1 = most frequent).

    Returns an empty table if the data file is missing; the repair then simply
    skips the ligature step.
    """
    ranks: Dict[str, Dict[str, int]] = {}
    try:
        with gzip.open(WORDS_PATH, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                language, _, word = line.rstrip("\n").partition("\t")
                table = ranks.setdefault(language, {})
                if word and word not in table:
                    table[word] = len(table) + 1
    except OSError:
        pass
    return ranks


def _swap_positions(key: str, lam_alef: bool) -> List[int]:
    """Find letter pairs that may be the two letters of one ligature glyph, swapped.

    Args:
        key: Letters of a word in logical order.
        lam_alef: Also consider ``ال`` pairs (a lam-alef glyph read backwards).

    Returns:
        Positions ``i`` where ``key[i]`` and ``key[i + 1]`` may be swapped.
    """
    joins_next = _joining_sets()[0]
    positions = []
    for i in range(len(key) - 1):
        first, second = key[i], key[i + 1]  # a reversed ligature has its letters in this order
        if lam_alef and first in _ALEFS and second == _LAM:
            positions.append(i)
        elif first in _LIGATURE_SECONDS and second in joins_next:
            positions.append(i)
        elif i == len(key) - 2 and first in _FINAL_SECONDS and second in _FINAL_FIRSTS:
            positions.append(i)
    return positions[:6]


def _variants(key: str, lam_alef: bool) -> Iterator[str]:
    """Yield every spelling of ``key`` with one or more possible ligature pairs swapped back.

    Args:
        key: Letters of a word in logical order.
        lam_alef: Consider lam-alef pairs.
    """
    positions = _swap_positions(key, lam_alef)
    for mask in range(1, 1 << len(positions)):
        chosen = [positions[k] for k in range(len(positions)) if mask >> k & 1]
        if any(b - a < 2 for a, b in zip(chosen, chosen[1:])):
            continue  # overlapping pairs
        chars = list(key)
        for i in chosen:
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
        yield "".join(chars)


def _core_score(word: str, language: str, ranks: Dict[str, int]) -> Optional[Tuple[int, int]]:
    """Score how well ``word`` splits into known affixes around a known core.

    One-letter affixes are only removed from cores of five or more letters
    and never on both sides, otherwise real words such as ``وکالت`` would
    count as ``وکلا`` + ``ت``.

    Args:
        word: A spelling.
        language: ``"fa"`` or ``"ar"``.
        ranks: Word ranks of the language.

    Returns:
        ``(core_length, -core_rank)`` of the best split, or ``None``.
    """
    prefixes, suffixes = _AFFIXES[language]
    best: Optional[Tuple[int, int]] = None
    for prefix in prefixes:
        if not word.startswith(prefix):
            continue
        for suffix in suffixes:
            if (not prefix and not suffix) or (suffix and not word.endswith(suffix)):
                continue
            if len(prefix) == 1 and len(suffix) == 1:
                continue  # two one-letter affixes split almost anything
            core = word[len(prefix):len(word) - len(suffix)] if suffix else word[len(prefix):]
            shortest = 5 if (len(prefix) == 1 or len(suffix) == 1) else 3
            if len(core) >= shortest and core in ranks:
                score = (len(core), -ranks[core])
                if best is None or score > best:
                    best = score
    return best


@lru_cache(maxsize=65536)
def _lookup(key: str, language: str, lam_alef: bool) -> Optional[str]:
    """Find the real spelling of a word whose ligature letters may be swapped.

    * A known word is never changed (``سالم`` and ``سلام``, ``محله`` and
      ``حمله`` are all words - the text cannot tell them apart).
    * Otherwise the most frequent known swap variant wins.
    * Otherwise words are split into prefix + core + suffix (``نمی‌دانید``,
      ``اطلاعاتی``); a variant wins only if its core is a known word and the
      unchanged word cannot be split into known parts at all.

    Args:
        key: Letters of the word (no marks, tatweel or ZWNJ).
        language: ``"fa"`` or ``"ar"``.
        lam_alef: Consider lam-alef pairs.

    Returns:
        The spelling, or ``None`` to keep the word.
    """
    ranks = _word_ranks().get(language)
    if not ranks or key in ranks:
        return None
    variants = list(_variants(key, lam_alef))
    if not variants:
        return None
    known = [(ranks[v], v) for v in variants if v in ranks]
    if known:
        return min(known)[1]
    if _core_score(key, language, ranks) is not None:
        return None  # the word as it is splits into known parts
    best: Optional[Tuple[Tuple[int, int], str]] = None
    for variant in variants:
        score = _core_score(variant, language, ranks)
        if score is not None and (best is None or score > best[0]):
            best = (score, variant)
    return best[1] if best else None


def fix_ligatures(text: str, language: str, lam_alef: bool = True) -> str:
    """Restore ligatures whose two letters were swapped by reversing a visual line.

    Producers such as Word or InDesign map a ligature glyph (``لا``, ``نم``,
    ``تم``, ``نج`` ...) to its letters in reading order; reversing the visual
    line then swaps them (``اطالعات``, ``منایید``, ``متام``). Real letter pairs
    look the same, so a word is only changed when it is unknown and a swap
    variant is a known word (:func:`_lookup`). A final ``الً`` is always
    ``لاً`` (``قبلاً``, ``مثلاً``).

    Args:
        text: Text in logical order (Persian letters already normalised).
        language: ``"fa"`` or ``"ar"``.
        lam_alef: ``False`` for documents that write lam-alef as one
            presentation-form character (then ``ال`` is never a swapped ligature).

    Returns:
        The repaired text.
    """
    if not _word_ranks().get(language):
        return text

    def repair(match: "re.Match[str]") -> str:
        """Fix one word, keeping its marks and ZWNJs in place."""
        word = match.group(0)
        key = _NOT_LETTER.sub("", word)
        if len(key) < 2:
            return word
        spelling = _lookup(key, language, lam_alef)
        if spelling is None:
            if lam_alef:  # tanween sits on the alef of a final lam-alef: "قبالً" -> "قبلاً"
                return re.sub("([" + _ALEFS + "])" + _LAM + "(\u064b)", _LAM + "\\1\\2", word)
            return word
        if len(spelling) != len(key):
            return word
        letters = iter(spelling)
        return "".join(ch if _NOT_LETTER.match(ch) else next(letters) for ch in word)

    return _ARABIC_WORD.sub(repair, text)


# --------------------------------------------------------------------------- #
# Detection                                                                   #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _frequent_words() -> frozenset:
    """Return :data:`_FREQUENT_WORDS` folded for comparisons."""
    return frozenset(word.translate(_FOLD) for word in _FREQUENT_WORDS)


def _rtl_words(text: str) -> Iterator[str]:
    """Yield the runs of right-to-left letters (and their marks) in ``text``.

    Args:
        text: Any text.
    """
    current: List[str] = []
    for ch in text:
        if (ch.isalpha() and bidi_class(ch) in ("R", "AL")) or (current and unicodedata.category(ch) == "Mn") \
                or (current and ch == ZWNJ):
            current.append(ch)
        elif current:
            yield "".join(current)
            current = []
    if current:
        yield "".join(current)


def order_evidence(text: str) -> int:
    """Score whether the right-to-left text of ``text`` is in visual or logical order.

    Evidence:

    * presentation forms at word edges - in logical order a word starts with
      an initial form and ends with a final form, in visual order it is the
      other way round;
    * Hebrew final letters (``ם ן ץ ף ך``) and Arabic ``ة ى`` belong at the end
      of a word;
    * frequent words (``از``, ``در``, ``في``, ``של`` ...) read forwards or backwards.

    Args:
        text: Text of one element, page or document.

    Returns:
        A positive number for visual order, a negative one for logical order
        and ``0`` when there is no evidence.
    """
    score = 0
    frequent = _frequent_words()
    for word in _rtl_words(text):
        letters = [ch for ch in word if ch.isalpha()]
        if len(letters) < 2:
            continue
        first, last = presentation_info(letters[0]), presentation_info(letters[-1])
        if first:
            score += 1 if first[0] == "final" else -1 if first[0] == "initial" else 0
        if last:
            score += 1 if last[0] == "initial" else -1 if last[0] == "final" else 0
        if letters[0] in _HEBREW_FINALS or letters[0] in _ARABIC_FINALS:
            score += 1
        if letters[-1] in _HEBREW_FINALS:
            score -= 1
        folded = "".join(info[1] if info else ch for ch, info in ((c, presentation_info(c)) for c in letters))
        folded = folded.translate(_FOLD)
        if folded in frequent:
            score -= 2
        elif folded[::-1] in frequent:
            score += 2
    return score


def paragraph_is_rtl(lines: Sequence[str], visual: bool, doc_rtl: bool) -> bool:
    """Guess the direction of one paragraph (table cell, heading ...).

    The bidi algorithm takes the direction from the first strong letter of
    the paragraph. In visual order that letter is at the right end of the
    first line for RTL paragraphs and at the left end for LTR paragraphs, so
    both ends are inspected together with the share of RTL letters and the
    direction of the whole document.

    Args:
        lines: The lines of the paragraph (visual or logical order).
        visual: ``True`` if ``lines`` are in visual order.
        doc_rtl: ``True`` if most letters of the document are right-to-left.

    Returns:
        ``True`` for a right-to-left paragraph.
    """
    rtl = ltr = 0
    for line in lines:
        counts = letter_counts(line)
        rtl += counts[0]
        ltr += counts[1]
    if rtl == 0:
        return False
    if ltr == 0:
        return True
    share = rtl / (rtl + ltr)
    if share >= 0.6:
        return True
    if share <= 0.15:
        return False
    first_line = next((line for line in lines if any(ch.isalpha() for ch in line)), "")
    strong = [bidi_class(ch) != "L" for ch in first_line if ch.isalpha() and bidi_class(ch) in _STRONG]
    if not strong:
        return doc_rtl
    left_rtl, right_rtl = strong[0], strong[-1]
    if not visual:
        return left_rtl  # logical order: the first strong letter decides
    if left_rtl and right_rtl:
        return True
    if not left_rtl and not right_rtl:
        return False
    if right_rtl:
        return doc_rtl or share >= 0.35
    return doc_rtl or share >= 0.3


class DocProfile:
    """Statistics of one document: direction, language and order evidence."""

    def __init__(self, texts: Sequence[Tuple[Any, str]], mode: str = "auto") -> None:
        """Analyse the text of a document.

        Args:
            texts: ``(page_number, text)`` for every element.
            mode: ``"auto"`` (detect the order) or ``"always"`` (assume visual order).
        """
        self.mode = mode
        self.page_scores: Dict[Any, int] = {}
        self.doc_score = 0
        self._evidence: Dict[str, int] = {}  # order evidence per element text (reused by is_visual)
        rtl = ltr = persian = arabic = hebrew = presentation = lam_alef_glyphs = 0
        for page, text in texts:
            if not has_rtl(text):
                ltr += letter_counts(text)[1]
                continue
            counts = letter_counts(text)
            rtl += counts[0]
            ltr += counts[1]
            for ch in text:
                if ch in _PERSIAN_ONLY:
                    persian += 1
                elif ch in _ARABIC_ONLY:
                    arabic += 1
                elif "\u05d0" <= ch <= "\u05ea":
                    hebrew += 1
                if ch >= "\ufb50" and presentation_info(ch):
                    presentation += 1
                    if "\ufef5" <= ch <= "\ufefc":
                        lam_alef_glyphs += 1
            score = self._evidence.get(text)
            if score is None:
                score = self._evidence[text] = order_evidence(text)
            self.page_scores[page] = self.page_scores.get(page, 0) + score
            self.doc_score += score
        self.rtl_letters = rtl
        self.ltr_letters = ltr
        self.rtl_share = rtl / (rtl + ltr) if rtl + ltr else 0.0
        #: ``True`` if most letters of the document are right-to-left.
        self.doc_rtl = self.rtl_share >= 0.5
        #: ``True`` if the PDF producer maps letter shapes to presentation forms
        #: (then lam-alef is one character and never ambiguous).
        self.presentation_forms = presentation * 5 >= rtl > 0
        #: ``True`` if lam-alef appears as one presentation-form character
        #: (then ``ال`` in the text is never a swapped ligature).
        self.lam_alef_glyphs = lam_alef_glyphs > 0
        #: ``True`` if the document is Persian (enables ي→ی and ك→ک).
        self.persian = persian >= 3 and persian > arabic and hebrew * 2 < rtl
        if rtl == 0:
            self.language = "und"
        elif hebrew * 2 >= rtl:
            self.language = "he"
        else:
            self.language = "fa" if self.persian else "ar"

    def is_persian(self, text: str) -> bool:
        """Decide whether Persian letter normalisation applies to ``text``.

        Letters that only Persian (``پ چ ژ گ ی ک``) or only Arabic (``ة أ إ``)
        uses decide; without such letters the language of the document does.

        Args:
            text: Text of one element.
        """
        persian = arabic = 0
        for ch in text:
            if ch in _PERSIAN_ONLY:
                persian += 1
            elif ch in _ARABIC_ONLY:
                arabic += 1
        if persian != arabic:
            return persian > arabic
        return self.persian

    def is_visual(self, text: str, page: Any = None) -> bool:
        """Decide whether ``text`` is in visual order and must be reordered.

        The evidence of the element itself wins when it is clear, then the
        evidence of its page (hybrid mode may mix sources per page), then the
        whole document. Without any evidence visual order is assumed, because
        that is what opendataloader-pdf produces.

        Args:
            text: Text of the element.
            page: Page number of the element (``None`` if unknown).

        Returns:
            ``True`` if the text must be reordered.
        """
        if self.mode == "always":
            return True
        score = self._evidence.get(text)
        if score is None:
            score = order_evidence(text)
        if abs(score) >= 2:
            return score > 0
        page_score = self.page_scores.get(page, 0) if page is not None else 0
        if abs(page_score) >= 2:
            return page_score > 0
        if self.doc_score:
            return self.doc_score > 0
        return True


# --------------------------------------------------------------------------- #
# JSON helpers                                                                #
# --------------------------------------------------------------------------- #
def iter_text_nodes(node: Any) -> Iterator[Dict[str, Any]]:
    """Yield every JSON object that has a string ``content``, in document order.

    Args:
        node: Parsed opendataloader-pdf JSON (or a part of it).
    """
    if isinstance(node, dict):
        if isinstance(node.get("content"), str):
            yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from iter_text_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_text_nodes(item)


def _iter_tables(node: Any) -> Iterator[Dict[str, Any]]:
    """Yield every ``table`` object of a JSON document (outer tables first).

    Args:
        node: Parsed JSON.
    """
    if isinstance(node, dict):
        if node.get("type") == "table":
            yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _iter_tables(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_tables(item)


def dump_json(value: Any, level: int = 0) -> str:
    """Serialise JSON in the layout opendataloader-pdf uses (Jackson's pretty printer).

    Objects put every member on its own line (``"key" : value``), arrays stay
    on one line (``[ a, b ]``), so repaired files differ from the originals
    only where text changed.

    Args:
        value: JSON value.
        level: Current object nesting level.

    Returns:
        The JSON text.
    """
    if isinstance(value, dict):
        if not value:
            return "{ }"
        pad = "  " * (level + 1)
        members = [f"{pad}{json.dumps(str(k), ensure_ascii=False)} : {dump_json(v, level + 1)}"
                   for k, v in value.items()]
        return "{\n" + ",\n".join(members) + "\n" + "  " * level + "}"
    if isinstance(value, list):
        if not value:
            return "[ ]"
        return "[ " + ", ".join(dump_json(v, level) for v in value) + " ]"
    return json.dumps(value, ensure_ascii=False)


def join_lines(lines: Sequence[str]) -> str:
    """Join lines exactly like opendataloader-pdf does without ``--keep-line-breaks``.

    A line that ends with a hyphenation sign (``-``, ``—``, soft hyphen) loses
    it and is glued to the next line; otherwise one space is inserted
    (``TextChunkUtils.formatLineEnd``).

    Args:
        lines: The lines of one element.

    Returns:
        The joined text.
    """
    if not lines:
        return ""
    text = lines[0]
    for line in lines[1:]:
        if text and text[-1] in HYPHENATION_SIGNS:
            text = text[:-1] + line
        else:
            text = text + " " + line
    return text


def file_has_rtl(path: str, limit: int = 64 * 1024 * 1024) -> bool:
    """Return ``True`` if a text output contains enough right-to-left letters.

    Args:
        path: Output file (``.json``, ``.md``, ``.html``, ``.txt``).
        limit: Maximum number of bytes to inspect.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit)
    except OSError:
        return False
    return count_rtl(data.decode("utf-8", errors="ignore")) >= MIN_RTL_LETTERS


def _escape_markup(text: str) -> str:
    """Escape text like the Markdown and HTML writers of opendataloader-pdf (``& < >``)."""
    return text.replace("\0", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_plain(text: str) -> str:
    """Escape text like the text writer of opendataloader-pdf (only NUL is removed)."""
    return text.replace("\0", "")


def _compact(text: str) -> str:
    """Collapse white space like the table cells of the text writer."""
    return _SPACES.sub(" ", text).strip()


def _protect(text: str, pattern: "re.Pattern[str]") -> Tuple[str, List[str]]:
    """Replace markup matched by ``pattern`` with :data:`OBJECT_MARK` placeholders.

    Args:
        text: Text with markup.
        pattern: Regular expression of the protected tokens.

    Returns:
        ``(text_with_placeholders, tokens_in_original_order)``.
    """
    tokens: List[str] = []

    def keep(match: "re.Match[str]") -> str:
        """Remember one token."""
        tokens.append(match.group(0))
        return OBJECT_MARK

    return pattern.sub(keep, text), tokens


# --------------------------------------------------------------------------- #
# Output repair                                                               #
# --------------------------------------------------------------------------- #
class OutputRepair:
    """Repairs the outputs that opendataloader-pdf produced for one PDF file."""

    def __init__(self, line_doc: Any, keep_breaks: bool = False, mode: str = "auto",
                 separators: Sequence[str] = ()) -> None:
        """Prepare the repaired text of every element.

        Args:
            line_doc: Parsed JSON produced with ``--keep-line-breaks`` (one line
                of the PDF per ``\\n``).
            keep_breaks: ``True`` if the user asked for ``--keep-line-breaks``
                (lines stay separate in the outputs).
            mode: ``"auto"`` or ``"always"`` (see :data:`RTL_MODES`).
            separators: Page separators of the job (``%page-number%`` allowed);
                lines that match them are user text and are never changed.
        """
        self.keep_breaks = keep_breaks
        self.mode = "always" if mode == "always" else "auto"
        nodes = list(iter_text_nodes(line_doc))
        self.lines: List[List[str]] = [n["content"].split("\n") for n in nodes]
        self.pages: List[Any] = [n.get("page number") for n in nodes]
        self.profile = DocProfile([(p, " ".join(l)) for p, l in zip(self.pages, self.lines)], self.mode)
        # Every element is repaired with un-mirrored brackets first (Chrome and
        # most producers). The unmirrored reading is computed where brackets
        # do not match, and decides the convention of the producer (InDesign
        # maps mirrored glyphs to the original characters).
        setups = [self._element_setup(l, p) for l, p in zip(self.lines, self.pages)]
        mirrored = [self._apply(l, s) if s else None for l, s in zip(self.lines, setups)]
        errors_m = [bracket_errors(" ".join(m)) if m and s and s["brackets"] else 0 for m, s in zip(mirrored, setups)]
        plain: List[Optional[List[str]]] = [
            self._apply(l, s, mirror=False) if s and s["brackets"] and e else None
            for l, s, e in zip(self.lines, setups, errors_m)]
        errors_n = [bracket_errors(" ".join(p)) if p else 0 for p in plain]
        better = [0, 0]  # [mirrored better, unmirrored better]
        for p, e_m, e_n in zip(plain, errors_m, errors_n):
            if p is not None and e_m != e_n:
                better[e_n < e_m] += 1
        #: ``True`` if brackets are un-mirrored after reordering (Chrome-like PDFs).
        self.mirror = better[0] >= better[1]
        self.fixed: List[Optional[List[str]]] = []
        for index, lines in enumerate(self.lines):
            setup = setups[index]
            if setup is None:
                self.fixed.append(None)
                continue
            chosen = mirrored[index]
            if setup["brackets"]:
                if plain[index] is None and not self.mirror:
                    plain[index] = self._apply(lines, setup, mirror=False)
                    errors_n[index] = bracket_errors(" ".join(plain[index] or []))
                if plain[index] is not None and (errors_n[index] < errors_m[index] or
                                                 (errors_n[index] == errors_m[index] and not self.mirror)):
                    chosen = plain[index]
            self.fixed.append(chosen if chosen != lines else None)
        self._separators = [re.compile("^" + re.escape(s.strip()).replace("%page\\-number%", r"\d+") + "$")
                            for s in separators if s and s.strip()]
        #: Counters for the job log and the UI.
        self.stats: Dict[str, Any] = {"elements": sum(f is not None for f in self.fixed), "tables": 0,
                                      "approximate": 0, "language": self.profile.language}
        self._tables = 0  # tables reversed in the file being repaired

    @classmethod
    def from_file(cls, path: str, keep_breaks: bool = False, mode: str = "auto",
                  separators: Sequence[str] = ()) -> "OutputRepair":
        """Create the repair from a JSON file written with ``--keep-line-breaks``.

        Args:
            path: The JSON file.
            keep_breaks: See :meth:`__init__`.
            mode: See :meth:`__init__`.
            separators: See :meth:`__init__`.
        """
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh), keep_breaks, mode, separators)

    # ------------------------------------------------------------ elements
    def _element_setup(self, lines: List[str], page: Any) -> Optional[Dict[str, Any]]:
        """Decide how one element is repaired.

        Args:
            lines: Lines in extraction order.
            page: Page number.

        Returns:
            ``{"rtl", "visual", "persian", "ligatures", "lam_alef", "brackets"}``,
            or ``None`` if the element has no RTL text.
        """
        if not any(has_rtl(line) for line in lines):
            return None
        text = " ".join(lines)
        visual = self.profile.is_visual(text, page)
        persian = self.profile.is_persian(text)
        return {"rtl": paragraph_is_rtl(lines, visual, self.profile.doc_rtl), "visual": visual, "persian": persian,
                "ligatures": self._ligature_language(text, visual, persian),
                "lam_alef": not self.profile.lam_alef_glyphs,
                "brackets": visual and _BRACKET_RE.search(text) is not None}

    def _apply(self, lines: List[str], setup: Dict[str, Any], mirror: bool = True) -> List[str]:
        """Repair the lines of one element as decided by :meth:`_element_setup`.

        Args:
            lines: Lines in extraction order.
            setup: Decisions for the element.
            mirror: Un-mirror brackets.

        Returns:
            The repaired lines.
        """
        return [self.fix_line(line, setup["rtl"], setup["visual"], setup["persian"], setup["ligatures"],
                              setup["lam_alef"], mirror) for line in lines]

    def _ligature_language(self, text: str, visual: bool, persian: bool) -> Optional[str]:
        """Decide whether :func:`fix_ligatures` applies to an element.

        Only reversed text can contain swapped ligature letters; Hebrew has no
        ligatures of this kind.

        Args:
            text: Original text of the element.
            visual: ``True`` if the element is reordered.
            persian: ``True`` for Persian text.

        Returns:
            ``"fa"`` or ``"ar"``, or ``None`` if the step is not needed.
        """
        if not visual or self.profile.language == "he" or not has_rtl(text):
            return None
        return "fa" if persian else "ar"

    @staticmethod
    def fix_line(line: str, rtl: bool, visual: bool, persian: bool, ligatures: Optional[str] = None,
                 lam_alef: bool = True, mirror: bool = True) -> str:
        """Repair one line of text.

        In a right-to-left paragraph even a line without RTL letters is
        reordered: a last line such as ``.(۱۶`` must become ``۱۶).``.

        Args:
            line: The line.
            rtl: Paragraph direction.
            visual: ``True`` if the line is in visual order.
            persian: Apply the Persian letter normalisation.
            ligatures: Language of the ligature word list (``None`` = step not needed).
            lam_alef: Consider swapped lam-alef ligatures (see :func:`fix_ligatures`).
            mirror: Un-mirror brackets (see :func:`reorder`).

        Returns:
            The line in logical order with normalised letters.
        """
        if visual and (rtl or has_rtl(line)):
            line = reorder(line, rtl, mirror)[0]
        line = normalize_text(line, persian)
        return fix_ligatures(line, ligatures, lam_alef) if ligatures else line

    def _expected(self, index: int) -> str:
        """Text of element ``index`` as opendataloader-pdf wrote it into the JSON output."""
        lines = self.lines[index]
        return "\n".join(lines) if self.keep_breaks else join_lines(lines)

    def _final(self, index: int) -> str:
        """Repaired text of element ``index`` joined like the original."""
        fixed = self.fixed[index] or self.lines[index]
        return "\n".join(fixed) if self.keep_breaks else join_lines(fixed)

    def _pairs(self, index: int, fmt: str) -> List[Tuple[str, str]]:
        """Possible ``(original, repaired)`` spellings of an element in an output format.

        Args:
            index: Element index.
            fmt: ``"md"``, ``"html"`` or ``"txt"``.
        """
        lines, fixed = self.lines[index], self.fixed[index] or self.lines[index]
        if not self.keep_breaks or len(lines) == 1:
            original, repaired = join_lines(lines), join_lines(fixed)
            pairs = [(original, repaired)]
            if fmt == "txt" and _compact(original) != original:
                pairs.append((_compact(original), _compact(repaired)))
            return pairs
        separators = {"md": ("\n", " ", "<br>"), "html": ("\n", "<br>"), "txt": ("\n", "\r\n")}[fmt]
        pairs = [(sep.join(lines), sep.join(fixed)) for sep in separators]
        if fmt == "txt":
            pairs.append((_compact(" ".join(lines)), _compact(" ".join(fixed))))
        return pairs

    # ---------------------------------------------------------- free text
    def _is_separator(self, text: str) -> bool:
        """``True`` if ``text`` is one of the page separators of the job."""
        stripped = text.strip()
        return any(p.match(stripped) for p in self._separators)

    def _fix_segment(self, segment: str, pattern: Optional["re.Pattern[str]"] = None) -> str:
        """Repair a piece of text that could not be matched with an element.

        The piece is treated as one line; markup matched by ``pattern`` is
        protected so that links, images and entities survive the reordering.

        Args:
            segment: Text of one line, cell or HTML text node.
            pattern: Markup to protect (``None`` = none).

        Returns:
            The repaired text.
        """
        core = segment.strip()
        if not has_rtl(core) or self._is_separator(core):
            return segment
        lead = segment[:len(segment) - len(segment.lstrip())]
        trail = segment[len(segment.rstrip()):]
        source, tokens = _protect(core, pattern) if pattern is not None else (core, [])
        if not has_rtl(source):
            return segment
        visual = self.profile.is_visual(source)
        rtl = paragraph_is_rtl([source], visual, self.profile.doc_rtl)
        if visual:
            text, order = reorder(source, rtl, self.mirror)
            if any(ch in MIRROR for ch in source):
                other = reorder(source, rtl, not self.mirror)
                if bracket_errors(other[0]) < bracket_errors(text):
                    text, order = other
        else:
            text, order = source, list(range(len(source)))
        persian = self.profile.is_persian(source)
        text = normalize_text(text, persian)
        ligatures = self._ligature_language(source, visual, persian)
        if ligatures:
            text = fix_ligatures(text, ligatures, not self.profile.lam_alef_glyphs)
        if tokens:
            # The n-th placeholder of the source is tokens[n]; follow the
            # permutation to put every token where its placeholder moved.
            rank: Dict[int, int] = {}
            for position, ch in enumerate(source):
                if ch == OBJECT_MARK:
                    rank[position] = len(rank)
            moved = iter([tokens[rank[i]] for i in order if source[i] == OBJECT_MARK])
            text = "".join(next(moved) if ch == OBJECT_MARK else ch for ch in text)
        self.stats["approximate"] += 1
        return lead + text + trail

    def _fix_free(self, text: str, fmt: str) -> str:
        """Repair the parts of an output that lie between matched elements.

        Args:
            text: Part of the file.
            fmt: ``"md"``, ``"html"`` or ``"txt"``.

        Returns:
            The repaired part.
        """
        if not has_rtl(text):
            return text
        if fmt == "html":
            return self._fix_free_html(text)
        parts = []
        for line in text.splitlines(keepends=True):
            body = line.rstrip("\r\n")
            end = line[len(body):]
            if has_rtl(body):
                body = self._fix_md_line(body) if fmt == "md" else \
                    "\t".join(self._fix_segment(cell) for cell in body.split("\t"))
            parts.append(body + end)
        return "".join(parts)

    def _fix_md_line(self, line: str) -> str:
        """Repair one Markdown line outside the matched elements (keeps markers, pipes and ``<br>``)."""
        match = _MD_PREFIX.match(line)
        prefix = match.group(1) if match else ""
        rest = line[len(prefix):]
        if rest.startswith("|") and rest.rstrip().endswith("|"):
            cells = rest.split("|")
        else:
            cells = [rest]
        fixed = ["<br>".join(self._fix_segment(piece, _MD_TOKENS) for piece in cell.split("<br>")) for cell in cells]
        return prefix + "|".join(fixed)

    def _fix_free_html(self, text: str) -> str:
        """Repair the text nodes of an HTML fragment (tags, title, scripts and styles are kept)."""
        parts = _HTML_SPLIT.split(text)
        skip = False
        for index, part in enumerate(parts):
            if index % 2:
                tag = part.lower()
                if re.match(r"<(title|script|style)\b", tag):
                    skip = True
                elif re.match(r"</(title|script|style)\b", tag):
                    skip = False
                continue
            if skip or not has_rtl(part):
                continue
            parts[index] = "".join(self._fix_segment(piece, _HTML_TOKENS) if piece.strip() else piece
                                   for piece in re.split(r"(\n)", part))
        return "".join(parts)

    # ------------------------------------------------------------- tables
    def _table_is_rtl(self, text: str) -> bool:
        """``True`` if a table with this text should list its columns from right to left."""
        rtl, ltr = letter_counts(text)
        return rtl > ltr or (self.profile.doc_rtl and rtl > 0 and rtl * 4 >= rtl + ltr)

    def _reverse_json_tables(self, doc: Any) -> None:
        """Put the columns of right-to-left tables of a JSON document in RTL order."""
        for table in _iter_tables(doc):
            columns, rows = table.get("number of columns"), table.get("rows")
            if not isinstance(columns, int) or not isinstance(rows, list):
                continue
            if not self._table_is_rtl(" ".join(n["content"] for n in iter_text_nodes(rows))):
                continue
            for row in rows:
                cells = row.get("cells") if isinstance(row, dict) else None
                if not isinstance(cells, list):
                    continue
                for cell in cells:
                    number, span = cell.get("column number"), cell.get("column span") or 1
                    if isinstance(number, int) and isinstance(span, int):
                        cell["column number"] = columns - number - span + 2
                cells.reverse()
            self._tables += 1

    def _reverse_md_tables(self, text: str) -> str:
        """Put the columns of right-to-left Markdown tables in RTL order."""
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            if not (_MD_ROW.match(lines[i]) and i + 1 < len(lines) and _MD_SEP.match(lines[i + 1])):
                i += 1
                continue
            j = i
            while j < len(lines) and _MD_ROW.match(lines[j]):
                j += 1
            columns = lines[i + 1].strip().count("|") - 1
            rows = [line.strip()[1:-1].split("|") for line in lines[i:j]]
            body = " ".join(" ".join(r) for k, r in enumerate(rows) if k != 1)
            if all(len(r) == columns for r in rows) and self._table_is_rtl(body):
                for k in range(i, j):
                    lines[k] = "|" + "|".join(reversed(rows[k - i])) + "|"
                self._tables += 1
            i = j
        return "\n".join(lines)

    def _reverse_html_tables(self, text: str) -> str:
        """Put the cells of right-to-left HTML tables in RTL order and mark the table ``dir="rtl"``."""
        def table(match: "re.Match[str]") -> str:
            """Repair one table."""
            open_tag, inner = match.group(1), match.group(2)
            if "<table" in inner.lower() or not self._table_is_rtl(re.sub(r"<[^>]+>", " ", inner)):
                return match.group(0)
            inner = _HTML_ROW.sub(lambda r: r.group(1) + _reverse_cells(r.group(2)) + r.group(3), inner)
            if "dir=" not in open_tag.lower():
                open_tag = open_tag[:-1] + ' dir="rtl">'
            self._tables += 1
            return open_tag + inner + "</table>"

        return _HTML_TABLE.sub(table, text)

    def _reverse_txt_tables(self, text: str) -> str:
        """Put the cells of right-to-left rows of the text output (tab separated) in RTL order."""
        lines = text.splitlines(keepends=True)
        i = 0
        while i < len(lines):
            if "\t" not in lines[i]:
                i += 1
                continue
            j = i
            while j < len(lines) and "\t" in lines[j]:
                j += 1
            if self._table_is_rtl(" ".join(lines[i:j])):
                for k in range(i, j):
                    body = lines[k].rstrip("\r\n")
                    end = lines[k][len(body):]
                    indent = body[:len(body) - len(body.lstrip(" "))]
                    lines[k] = indent + "\t".join(reversed(body[len(indent):].split("\t"))) + end
                self._tables += 1
            i = j
        return "".join(lines)

    def _html_direction(self, text: str) -> str:
        """Add ``lang``/``dir`` to the document and ``dir="auto"`` to text blocks."""
        if not self.profile.rtl_letters:
            return text
        if self.profile.doc_rtl:
            def root(match: "re.Match[str]") -> str:
                """Set the language and direction of ``<html>``."""
                attrs = match.group(1)
                if "dir=" in attrs.lower():
                    return match.group(0)
                if re.search(r'lang="und"', attrs):
                    attrs = attrs.replace('lang="und"', f'lang="{self.profile.language}"')
                elif "lang=" not in attrs.lower():
                    attrs += f' lang="{self.profile.language}"'
                return f'<html{attrs} dir="rtl">'

            text = _HTML_ROOT.sub(root, text, count=1)

        def block(match: "re.Match[str]") -> str:
            """Let the browser pick the direction of every text block."""
            if "dir=" in match.group(2).lower():
                return match.group(0)
            return f'<{match.group(1)} dir="auto"{match.group(2)}>'

        return _HTML_BLOCK.sub(block, text)

    # -------------------------------------------------------------- files
    def repair_json(self, text: str) -> str:
        """Repair a JSON output.

        Elements are paired with the line data in document order; an element
        whose text does not match is looked up by text, and repaired as one
        line if that fails too.

        Args:
            text: Content of the JSON file.

        Returns:
            The repaired JSON text.
        """
        self._tables = 0
        doc = json.loads(text)
        nodes = list(iter_text_nodes(doc))
        same_shape = len(nodes) == len(self.lines)
        lookup: Optional[Dict[str, int]] = None
        for index, node in enumerate(nodes):
            content = node["content"]
            if same_shape and content == self._expected(index):
                if self.fixed[index] is not None:
                    node["content"] = self._final(index)
                continue
            if not has_rtl(content):
                continue
            if lookup is None:
                lookup = {self._expected(k): k for k in range(len(self.lines)) if self.fixed[k] is not None}
            match = lookup.get(content)
            if match is not None:
                node["content"] = self._final(match)
            else:
                node["content"] = "\n".join(self._fix_segment(part) for part in content.split("\n"))
        self._reverse_json_tables(doc)
        self.stats["tables"] = max(self.stats["tables"], self._tables)
        return dump_json(doc) + ("\n" if text.endswith("\n") else "")

    def repair_markup(self, text: str, fmt: str) -> str:
        """Repair a Markdown, HTML or text output.

        Every element is searched (in document order) in the spelling the
        writer used and replaced by its repaired text; the parts in between
        are repaired line by line. Finally tables and HTML attributes are fixed.

        Args:
            text: Content of the file.
            fmt: ``"md"``, ``"html"`` or ``"txt"``.

        Returns:
            The repaired content.
        """
        self._tables = 0
        if not has_rtl(text):
            return text
        escape = _escape_plain if fmt == "txt" else _escape_markup
        parts: List[str] = []
        cursor = 0
        for index in range(len(self.lines)):
            if self.fixed[index] is None:
                continue
            for original, repaired in self._pairs(index, fmt):
                needle = escape(original)
                if not needle.strip():
                    break
                position = self._find(text, needle, cursor)
                if position < 0:
                    continue
                parts.append(self._fix_free(text[cursor:position], fmt))
                parts.append(escape(repaired))
                cursor = position + len(needle)
                break
        parts.append(self._fix_free(text[cursor:], fmt))
        result = "".join(parts)
        if fmt == "md":
            result = self._reverse_html_tables(self._reverse_md_tables(result))
        elif fmt == "html":
            result = self._html_direction(self._reverse_html_tables(result))
        else:
            result = self._reverse_txt_tables(result)
        self.stats["tables"] = max(self.stats["tables"], self._tables)
        return result

    @staticmethod
    def _find(text: str, needle: str, start: int) -> int:
        """Find ``needle`` at a word boundary at or after ``start``.

        Short needles that would skip a lot of unrepaired RTL text are
        rejected - they are probably a different occurrence.

        Args:
            text: Haystack.
            needle: Escaped original text of an element.
            start: Search start.

        Returns:
            The position, or ``-1``.
        """
        position = text.find(needle, start)
        attempts = 0
        while position >= 0 and attempts < 40:
            before = text[position - 1] if position > 0 else " "
            end = position + len(needle)
            after = text[end] if end < len(text) else " "
            if not before.isalnum() and not after.isalnum():
                if len(needle) >= 24 or count_rtl(text[start:position]) <= 40:
                    return position
                return -1
            position = text.find(needle, position + 1)
            attempts += 1
        return -1

    def repair_file(self, path: str) -> bool:
        """Repair one output file in place.

        Args:
            path: ``.json``, ``.md``, ``.html`` or ``.txt`` file.

        Returns:
            ``True`` if the file was changed.

        Raises:
            OSError: The file cannot be read or replaced (for example because
                another program keeps it open); no temporary file is left behind.
        """
        ext = os.path.splitext(path)[1].lower()
        if ext not in TEXT_EXTENSIONS:
            return False
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        if ext == ".json":
            new = self.repair_json(text)
        else:
            new = self.repair_markup(text, {".md": "md", ".html": "html", ".txt": "txt"}[ext])
        if new == text:
            return False
        partial = path + ".rtl-tmp"
        try:
            with open(partial, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            os.replace(partial, path)  # fails if another program locks the file (Windows)
        except OSError:
            try:
                os.remove(partial)
            except OSError:
                pass
            raise
        return True


def _reverse_cells(row: str) -> str:
    """Reverse the ``<td>``/``<th>`` cells of one HTML table row, keeping the white space between them.

    Args:
        row: Inner HTML of a ``<tr>``.
    """
    cells = list(_HTML_CELL.finditer(row))
    if len(cells) < 2:
        return row
    gaps = [row[cells[k].end():cells[k + 1].start()] for k in range(len(cells) - 1)]
    parts = [row[:cells[0].start()]]
    for k, cell in enumerate(reversed(cells)):
        parts.append(cell.group(0))
        if k < len(gaps):
            parts.append(gaps[k])
    parts.append(row[cells[-1].end():])
    return "".join(parts)

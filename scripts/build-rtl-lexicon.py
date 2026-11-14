#!/usr/bin/env python3
"""
Build ``src/cyrusasha_odl/data/rtl-words.txt.gz`` - the word list of the RTL repair.

Background
----------
PDF producers such as Microsoft Word or Adobe InDesign draw ligatures - the
mandatory ``لا`` and, depending on the font, ``نم``, ``تم``, ``نج``, ``تح``,
``تن`` ... - as one glyph and map that glyph to its letters *in reading order*.
opendataloader-pdf returns RTL lines in visual order, so after the repair
reverses a line the two letters of every such glyph are swapped:
``اطالعات`` instead of ``اطلاعات``, ``منایید`` instead of ``نمایید``,
``متام`` instead of ``تمام`` (the well-known defects of Persian PDF text).

The text alone cannot show which letter pairs were one glyph; word knowledge
can. The repair (``rtl.fix_ligatures``) tries to swap back possible ligature
pairs of a word that is not a known word and keeps a variant that is a
frequent word. This script writes the ranked word lists it uses.

Usage
-----
::

    python scripts/build-rtl-lexicon.py fa:wiki_fa.txt:1 fa:fa_50k.txt:2 ar:wiki_ar.txt:1 ar:ar_50k.txt:2

Each argument is ``language:path:tier``. A file whose lines look like
``word count`` is read as a frequency list, any other file as plain text.
Tier 1 (formal text such as Wikipedia articles) ranks the words; tier 2
(for example the OpenSubtitles lists of
https://github.com/hermitdave/FrequencyWords, CC BY-SA 4.0) only adds a
small weight, because subtitles are dominated by spoken language
(``سلام`` "hello" would otherwise beat ``سالم`` "healthy").

The generated list is data derived from these sources and keeps their
CC BY-SA 4.0 licence (see the header of the generated file).
"""

from __future__ import annotations

import gzip
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "src", "cyrusasha_odl", "data", "rtl-words.txt.gz")

#: Number of words kept per language (the most frequent ones).
LIMITS = {"fa": 30000, "ar": 20000}
#: Weight of tier-2 sources relative to tier 1 (per million words).
TIER2_WEIGHT = 0.05

#: Letters of a word (Arabic script without digits, punctuation and marks).
WORD_RE = re.compile("[\u0621-\u063a\u0641-\u064a\u0671-\u06d3\u06d5\u200c]+")
MARKS_RE = re.compile("[\u064b-\u065f\u0670\u0640\u200c]")
PERSIAN_FOLD = str.maketrans({"\u064a": "\u06cc", "\u0649": "\u06cc", "\u0643": "\u06a9"})


def read_counts(path: str, language: str) -> Counter:
    """Count the words of a corpus file.

    Args:
        path: Plain text or a ``word count`` frequency list.
        language: ``"fa"`` (folds Arabic yeh/kaf to Persian) or ``"ar"``.

    Returns:
        Word counts (marks, tatweel and ZWNJ removed).
    """
    counts: Counter = Counter()
    with open(path, encoding="utf-8", errors="ignore") as fh:
        first = fh.readline()
        fh.seek(0)
        is_list = bool(re.match(r"^\S+ \d+\s*$", first))
        for line in fh:
            if is_list:
                parts = line.split()
                if len(parts) != 2 or not parts[1].isdigit():
                    continue
                pairs = [(parts[0], int(parts[1]))]
            else:
                pairs = [(w, 1) for w in WORD_RE.findall(line)]
            for word, count in pairs:
                word = MARKS_RE.sub("", word)
                if language == "fa":
                    word = word.translate(PERSIAN_FOLD)
                if len(word) >= 2 and WORD_RE.fullmatch(word):
                    counts[word] += count
    return counts


def build(sources: Dict[str, list]) -> Dict[str, List[str]]:
    """Rank the words of every language.

    Counts are normalised to occurrences per million words; tier-2 sources
    are multiplied by :data:`TIER2_WEIGHT`. Rare words (fewer than 2
    occurrences in a small corpus, 30 in a large one) are ignored.

    Args:
        sources: ``{language: [(path, tier), ...]}``.

    Returns:
        ``{language: [words, most frequent first]}``.
    """
    result: Dict[str, List[str]] = {}
    for language, files in sources.items():
        score: Dict[str, float] = defaultdict(float)
        for path, tier in files:
            counts = read_counts(path, language)
            total = sum(counts.values()) or 1
            minimum = 2 if total < 5_000_000 else 30
            weight = 1.0 if int(tier) == 1 else TIER2_WEIGHT
            for word, count in counts.items():
                if count >= minimum:
                    score[word] += weight * count * 1_000_000 / total
        ranked = sorted(score, key=lambda w: (-score[w], w))
        result[language] = ranked[:LIMITS.get(language, 20000)]
    return result


def main(argv: list) -> int:
    """Parse ``language:path:tier`` arguments and write the word list.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        Process exit code.
    """
    sources: Dict[str, list] = defaultdict(list)
    for arg in argv:
        language, path, tier = arg.split(":", 2)
        sources[language].append((path, int(tier)))
    if not sources:
        print(__doc__)
        return 2
    words = build(sources)
    lines = [
        "# Word list of the RTL repair (generated by scripts/build-rtl-lexicon.py).",
        "# Format: language<TAB>word, most frequent first. Derived from word counts of Wikipedia",
        "# articles and the OpenSubtitles frequency lists of github.com/hermitdave/FrequencyWords.",
        "# License of this data file: CC BY-SA 4.0.",
    ]
    for language in sorted(words):
        lines.extend(f"{language}\t{word}" for word in words[language])
    os.makedirs(os.path.dirname(TARGET), exist_ok=True)
    with gzip.GzipFile(TARGET, "wb", compresslevel=9, mtime=0) as fh:  # mtime=0: reproducible file
        fh.write(("\n".join(lines) + "\n").encode("utf-8"))
    for language, items in sorted(words.items()):
        print(f"{language}: {len(items)} words")
    print(f"written: {TARGET} ({os.path.getsize(TARGET) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

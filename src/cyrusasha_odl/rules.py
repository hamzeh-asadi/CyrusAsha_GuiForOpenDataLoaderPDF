"""
Sanitization rule files (``--sanitize-rules``).

opendataloader-pdf can replace sensitive data (e-mail addresses, phone and card
numbers, IP addresses, URLs, ...) using regular-expression rules stored in a
JSON file::

    {"rules": [{"id": "email", "pattern": "<Java regex>", "replacement": "email@example.com"}]}

The CLI refuses an invalid file *before* processing anything, so this module
validates rule sets with the same limits before they are saved:

* ``id`` - 1 to 64 characters ``[a-z0-9-]``, unique within the file
* ``pattern`` - at most 5000 characters (Java regular expression)
* ``replacement`` - at most 200 characters, no control characters
* at most 200 rules and 1 MiB per file

Files are stored in ``data/rules/<name>.json``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from .config import write_json_atomic
from .errors import ApiError

_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

MAX_RULES = 200
MAX_PATTERN = 5000
MAX_REPLACEMENT = 200
MAX_FILE_BYTES = 1024 * 1024

#: File with the default rule set published in the opendataloader-pdf repository
#: (examples/sanitize-rules/default-rules.json, Apache-2.0). It seeds data/rules.
DEFAULT_RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "default-rules.json")


def load_default_rules() -> Dict[str, Any]:
    """Return the bundled upstream default rules (an empty set if the file is missing)."""
    try:
        with open(DEFAULT_RULES_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"rules": []}


def validate_rules(data: Any) -> List[Dict[str, str]]:
    """Validate a rule set and return the cleaned list of rules.

    Python's ``re`` module is used to catch obvious pattern errors early; Java
    regex syntax is very similar, but a few constructs differ, therefore an
    error from Python is reported as a warning-level failure with details.

    Args:
        data: Parsed JSON (``{"rules": [...]}``).

    Returns:
        The rules, each with exactly ``id``, ``pattern`` and ``replacement``.

    Raises:
        ApiError: ``bad_rules`` with a description of the first problem.
    """
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ApiError("bad_rules", 'expected {"rules": [...]}')
    rules = data["rules"]
    if len(rules) > MAX_RULES:
        raise ApiError("bad_rules", f"more than {MAX_RULES} rules")
    seen = set()
    cleaned: List[Dict[str, str]] = []
    for index, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            raise ApiError("bad_rules", f"rule #{index} is not an object")
        rule_id = str(rule.get("id", ""))
        pattern = str(rule.get("pattern", ""))
        replacement = str(rule.get("replacement", ""))
        if not _ID_RE.match(rule_id):
            raise ApiError("bad_rules", f"rule #{index}: id must be 1-64 chars of a-z, 0-9, '-'")
        if rule_id in seen:
            raise ApiError("bad_rules", f"rule #{index}: duplicate id '{rule_id}'")
        seen.add(rule_id)
        if not pattern or len(pattern) > MAX_PATTERN:
            raise ApiError("bad_rules", f"rule '{rule_id}': pattern must be 1-{MAX_PATTERN} chars")
        if len(replacement) > MAX_REPLACEMENT or _CTRL_RE.search(replacement):
            raise ApiError("bad_rules", f"rule '{rule_id}': replacement too long or has control characters")
        try:
            re.compile(pattern)
        except re.error as exc:
            # Possessive quantifiers / atomic groups are valid in Java but only
            # supported by Python >= 3.11; other errors are real problems.
            raise ApiError("bad_rules", f"rule '{rule_id}': invalid pattern ({exc})") from exc
        cleaned.append({"id": rule_id, "pattern": pattern, "replacement": replacement})
    if len(json.dumps({"rules": cleaned}).encode("utf-8")) > MAX_FILE_BYTES:
        raise ApiError("bad_rules", "file larger than 1 MiB")
    return cleaned


def test_rules(rules: List[Dict[str, str]], text: str) -> Dict[str, Any]:
    """Apply rules to a sample text (approximation with Python's ``re``).

    Args:
        rules: Validated rules.
        text: Sample text from the UI (truncated to 20 000 characters).

    Returns:
        ``{"result": str, "hits": {rule_id: count}}``.
    """
    text = str(text or "")[:20000]
    hits: Dict[str, int] = {}
    for rule in rules:
        try:
            text, count = re.subn(rule["pattern"], lambda _m, r=rule["replacement"]: r, text)
        except re.error:
            count = 0
        if count:
            hits[rule["id"]] = count
    return {"result": text, "hits": hits}


class RuleStore:
    """Named rule files in ``data/rules``."""

    def __init__(self, folder: str) -> None:
        """Create the store and seed it with the default rule set.

        Args:
            folder: Storage folder.
        """
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        default = os.path.join(folder, "default-rules.json")
        if not os.path.exists(default):
            write_json_atomic(default, load_default_rules())

    def _path(self, name: str) -> str:
        """Return the file path for ``name`` (validated to prevent path traversal)."""
        name = name[:-5] if name.endswith(".json") else name
        if not _NAME_RE.match(name):
            raise ApiError("bad_rules_name", name)
        return os.path.join(self.folder, name + ".json")

    def list(self) -> List[Dict[str, Any]]:
        """Return ``[{"name", "path", "count", "valid"}]`` for every stored file."""
        result = []
        for entry in sorted(os.listdir(self.folder)):
            if not entry.endswith(".json"):
                continue
            path = os.path.join(self.folder, entry)
            count, valid = 0, True
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    count = len(validate_rules(json.load(fh)))
            except (OSError, ValueError, ApiError):
                valid = False
            result.append({"name": entry[:-5], "path": path, "count": count, "valid": valid})
        return result

    def get(self, name: str) -> Dict[str, Any]:
        """Return the content of a rule file.

        Raises:
            ApiError: ``not_found`` if it does not exist.
        """
        path = self._path(name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return {"name": name, "path": path, "data": json.load(fh)}
        except (OSError, ValueError) as exc:
            raise ApiError("not_found", name, 404) from exc

    def save(self, name: str, data: Any) -> Dict[str, Any]:
        """Validate and save a rule file.

        Returns:
            ``{"name", "path", "count"}``.
        """
        rules = validate_rules(data)
        path = self._path(name)
        write_json_atomic(path, {"rules": rules})
        return {"name": name, "path": path, "count": len(rules)}

    def delete(self, name: str) -> None:
        """Delete a rule file (missing files are ignored)."""
        try:
            os.remove(self._path(name))
        except FileNotFoundError:
            pass

    def resolve(self, name: str) -> str:
        """Return the path of a stored rule file for use with ``--sanitize-rules``.

        Raises:
            ApiError: ``not_found`` if the file does not exist.
        """
        path = self._path(name)
        if not os.path.isfile(path):
            raise ApiError("not_found", name, 404)
        return path

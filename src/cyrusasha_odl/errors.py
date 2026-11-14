"""
Error types shared by all modules.

API handlers raise :class:`ApiError` with a short machine readable ``code``.
The web UI translates the code into Persian or English (see ``web/js/i18n.js``,
keys ``err.<code>``) and shows ``detail`` as additional technical information.
"""

from __future__ import annotations


class ApiError(Exception):
    """An error that is reported to the web UI as JSON."""

    def __init__(self, code: str, detail: str = "", status: int = 400) -> None:
        """Create the error.

        Args:
            code: Stable identifier such as ``"input_missing"``.
            detail: Optional technical detail (file name, tool output, ...).
            status: HTTP status code sent to the browser.
        """
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.status = status

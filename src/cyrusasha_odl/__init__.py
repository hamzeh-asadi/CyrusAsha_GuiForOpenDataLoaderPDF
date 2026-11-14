"""
CyrusAsha_GuiForOpenDataLoaderPDF - a lightweight graphical manager for OpenDataLoader PDF.

Part of the CyrusAsha family (Code. Intelligence. Order). The package only uses
the Python standard library. It provides:

* ``config``          - paths, persistent settings and conversion presets
* ``platform_utils``  - OS helpers (Python/Java discovery, process trees, dialogs)
* ``procs``           - child-process runner with live log capture
* ``env``             - environment inspector (Java, venv, installed packages)
* ``setup_tasks``     - installer / updater tasks (venv, pip, portable Java, ...)
* ``jobs``            - conversion job queue built on the opendataloader-pdf CLI
* ``hybrid``          - manager for the local hybrid (AI) backend server
* ``rules``           - storage and validation of sanitization rule files
* ``server``          - the HTTP server that serves the web UI and the JSON API
"""

__version__ = "1.0.0"

#: Product name used for folders, shortcuts, logs and the UI.
#: Meaning: "CyrusAsha GUI for OpenDataLoader PDF".
PRODUCT = "CyrusAsha_GuiForOpenDataLoaderPDF"

#: Human readable name including the version, e.g. ``CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0``.
APP_NAME = f"{PRODUCT}-{__version__}"

#: Identifier returned by ``/api/ping`` so a second launch can detect a running instance.
APP_ID = "cyrusasha-guiforopendataloaderpdf"

#: User-Agent sent to PyPI and the Adoptium API.
USER_AGENT = f"{PRODUCT}/{__version__}"

#: Name of the custom header that every state-changing request must carry (CSRF protection).
CSRF_HEADER = "X-CyrusAsha-ODL"

#: Prefix of the environment variables (``CYRUSASHA_ODL_DATA``, ``..._HOST``, ``..._PORT``).
ENV_PREFIX = "CYRUSASHA_ODL_"

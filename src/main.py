#!/usr/bin/env python3
"""
CyrusAsha_GuiForOpenDataLoaderPDF - entry point.

Starts the local web server and opens the control center::

    python src/main.py                    # start and open the UI
    python src/main.py --port 8765        # use another port
    python src/main.py --browser          # open in a normal browser tab
    python src/main.py --no-browser       # only start the server
    python src/main.py --diagnose         # print an environment report
    python src/main.py --setup full       # headless install (console output)
    python src/main.py --setup full --hybrid --python "C:\\Python313\\python.exe"

On Windows the launchers in ``windows/`` (and ``Start.bat``) call this file.

Only the Python standard library is required (Python 3.9+ for the GUI itself;
opendataloader-pdf is installed into its own virtual environment, which needs
Python 3.11+).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request

if sys.version_info < (3, 9):  # pragma: no cover - checked before anything else is imported
    sys.stderr.write("CyrusAsha_GuiForOpenDataLoaderPDF needs Python 3.9 or newer.\n")
    sys.exit(1)

# Make the package importable no matter where the script is started from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cyrusasha_odl import APP_ID, APP_NAME, ENV_PREFIX  # noqa: E402

DEFAULT_PORT = 8765


def fix_console_streams() -> None:
    """Avoid ``UnicodeEncodeError`` when printing Persian text to old consoles.

    Under ``pythonw.exe`` the streams are ``None``; they are left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def parse_args(argv=None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Argument list (default: ``sys.argv[1:]``).

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(prog="CyrusAsha_GuiForOpenDataLoaderPDF",
                                     description="CyrusAsha control center for OpenDataLoader PDF")
    parser.add_argument("--host", default=os.environ.get(ENV_PREFIX + "HOST", "127.0.0.1"),
                        help="interface to bind (default 127.0.0.1 = this computer only)")
    parser.add_argument("--port", type=int, default=int(os.environ.get(ENV_PREFIX + "PORT", DEFAULT_PORT)),
                        help=f"TCP port (default {DEFAULT_PORT}; the next free port is used if busy)")
    parser.add_argument("--data", default="", help="data folder (default: data/ in the project root)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--no-browser", action="store_true", help="do not open the user interface")
    group.add_argument("--browser", action="store_true", help="open in the default browser instead of an app window")
    group.add_argument("--app", action="store_true", help="open in an Edge/Chrome app window")
    parser.add_argument("--exit-when-idle", nargs="?", type=int, const=180, default=0, metavar="SECONDS",
                        help="exit when no UI window has been open for SECONDS (default 180) and nothing runs; "
                             "used by start-silent.bat so no invisible server is left behind")
    parser.add_argument("--no-host-check", action="store_true",
                        help="accept any Host header (needed behind reverse proxies)")
    parser.add_argument("--setup", metavar="ACTION",
                        help="run an installer action without UI: full, install, update, hybrid, "
                             "java-portable, java-winget, verify, uninstall, reset ...")
    parser.add_argument("--hybrid", action="store_true", help="with --setup full: also install the hybrid extra")
    parser.add_argument("--python", default="", help="with --setup: interpreter used to create the venv")
    parser.add_argument("--pkg-version", default="", help="with --setup install/update: exact package version")
    parser.add_argument("--install-dir", default=None, metavar="FOLDER",
                        help="install folder for venv, Java and pip cache (\"\" = data folder); saved, files are not moved")
    parser.add_argument("--models-dir", default=None, metavar="FOLDER",
                        help="folder for downloaded AI models (\"\" = <install folder>/models); saved, files are not moved")
    parser.add_argument("--show-locations", action="store_true",
                        help="print the install and models folders and exit (used by install.bat)")
    parser.add_argument("--diagnose", action="store_true", help="print an environment report and exit")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=APP_NAME)
    return parser.parse_args(argv)


def ping(port: int, host: str = "127.0.0.1") -> bool:
    """Return ``True`` if a running instance of this application answers on ``host:port``.

    Args:
        port: TCP port.
        host: Address to contact.
    """
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{host}:{port}/api/ping", timeout=1.5) as response:
            return json.load(response).get("app") == APP_ID
    except Exception:
        return False


def port_free(host: str, port: int) -> bool:
    """Return ``True`` if ``port`` can be bound on ``host``."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def run_setup(args: argparse.Namespace) -> int:
    """Run one installer action in the console and return the process exit code.

    Args:
        args: Parsed command line (``--setup``, ``--hybrid``, ``--python``, ...).
    """
    from cyrusasha_odl.app import App
    from cyrusasha_odl.errors import ApiError

    try:
        app = App(args.data, verbose=args.verbose, install_dir=args.install_dir, models_dir=args.models_dir)
    except ApiError as exc:
        print(f"Error: {exc.code} {exc.detail}".rstrip())
        return 2
    params = {"hybrid": args.hybrid, "python": args.python, "version": args.pkg_version}
    try:
        app.setup.start(args.setup, params)
    except ApiError as exc:
        print(f"Error: {exc.code} {exc.detail}".rstrip())
        return 2
    since = 0
    last_progress = 0.0
    while True:
        chunk = app.setup.read_log(since)
        for line in chunk["lines"]:
            print(line, flush=True)
        since = chunk["next"]
        status = app.setup.status() or {}
        download = status.get("download")
        if download and download.get("total") and time.time() - last_progress > 2:
            last_progress = time.time()
            print(f"   ... {download['name']}: {download['done'] / download['total']:.0%}", flush=True)
        if status.get("state") != "running" and not chunk["lines"]:
            break
        time.sleep(0.3)
    print(f"Result: {status.get('state')}")
    return 0 if status.get("state") == "success" else 1


def start_idle_watchdog(app, server, limit: int) -> None:
    """Shut the application down once no UI has polled for ``limit`` seconds.

    Browsers keep polling (at least about once a minute even for minimized
    windows), so a long silence means every window was closed. Running jobs
    and installer tasks keep the application alive.

    Args:
        app: The application object.
        server: The HTTP server to stop.
        limit: Idle time in seconds.
    """
    def watch() -> None:
        """Thread body: check the idle time every few seconds."""
        while not app.shutdown_event.wait(10):
            if app.idle_for() > limit:
                app.log.info("no UI window for %ss and nothing running - exiting", limit)
                app.shutdown()
                server.shutdown()
                return

    threading.Thread(target=watch, name="idle-watchdog", daemon=True).start()


def run_show_locations(args: argparse.Namespace) -> int:
    """Print the current install and models folders (quick: nothing is started or created).

    Args:
        args: Parsed command line (``--data``).

    Returns:
        Always 0.
    """
    from cyrusasha_odl.config import Paths, Settings

    paths = Paths(args.data or None)
    settings = Settings(paths.settings_file)
    paths.apply_locations(settings.get("install_dir"), settings.get("models_dir"))
    print(f"Install folder : {paths.install_dir}{'  (default)' if paths.install_default else ''}")
    print(f"Models folder  : {paths.models_dir}{'  (default)' if paths.models_default else ''}")
    return 0


def run_diagnose(args: argparse.Namespace) -> int:
    """Print a readable environment report (Python, Java, venv, packages, folders).

    Used by ``windows/diagnose.bat``; useful when asking for help.

    Args:
        args: Parsed command line (``--data``).

    Returns:
        0 when Java and opendataloader-pdf are ready, otherwise 1.
    """
    import platform

    from cyrusasha_odl.app import App

    from cyrusasha_odl.errors import ApiError

    try:
        app = App(args.data, install_dir=args.install_dir, models_dir=args.models_dir)
    except ApiError as exc:
        print(f"Error: {exc.code} {exc.detail}".rstrip())
        return 2
    report = app.env.summary(refresh=True)
    java = report["java"]
    pkg = report["package"]
    lines = [
        APP_NAME,
        "=" * len(APP_NAME),
        f"OS                 : {report['os']}",
        f"GUI Python         : {report['gui_python']['version']} ({report['gui_python']['path']})",
        f"Machine            : {platform.machine()}",
        f"Data folder        : {report['paths']['data']}",
        f"Install folder     : {report['paths']['install']}",
        f"Models folder      : {report['paths']['models']}",
        f"Virtual env        : {'yes' if report['venv']['exists'] else 'no'} ({report['venv']['path']})",
        f"Env Python         : {report['venv']['python'] or '-'}",
        f"opendataloader-pdf : {pkg.get('version') or 'not installed'} (latest on PyPI: {pkg.get('latest') or '?'})",
        f"Hybrid extra       : {'installed' if pkg.get('hybrid') else 'not installed'}",
        f"Java (selected)    : {java['selected']['version'] + ' - ' + java['selected']['path'] if java.get('selected') else 'NOT FOUND'}",
        f"Java candidates    : {len(java.get('candidates', []))}",
        f"winget             : {'yes' if report['winget'] else 'no'}",
        f"Tesseract          : {report['tesseract'] or '-'}",
        f"Node.js            : {report['node'] or '-'}",
    ]
    if report.get("pypi_error"):
        lines.append(f"PyPI               : not reachable ({report['pypi_error']})")
    print("\n".join(lines))
    return 0 if (java.get("ok") and pkg.get("installed")) else 1


def main(argv=None) -> int:
    """Start the control center (or run a headless installer action / diagnosis).

    Args:
        argv: Argument list (default: ``sys.argv[1:]``).

    Returns:
        The process exit code.
    """
    fix_console_streams()
    args = parse_args(argv)
    if args.show_locations:
        return run_show_locations(args)
    if args.diagnose:
        return run_diagnose(args)
    if args.setup:
        return run_setup(args)

    from cyrusasha_odl.app import App
    from cyrusasha_odl.platform_utils import open_ui
    from cyrusasha_odl.server import make_server

    local_host = "127.0.0.1" if args.host in ("0.0.0.0", "", "::") else args.host

    # Single instance: if the GUI already runs, just show it.
    if ping(args.port, local_host):
        url = f"http://{local_host}:{args.port}/"
        print(f"{APP_NAME} is already running at {url}")
        if not args.no_browser:
            open_ui(url, "browser" if args.browser else "app")
        return 0

    port = args.port
    for candidate in range(args.port, args.port + 20):
        if port_free(args.host, candidate):
            port = candidate
            break
    else:
        print(f"No free port found between {args.port} and {args.port + 19}.")
        return 1

    try:
        app = App(args.data, verbose=args.verbose, install_dir=args.install_dir, models_dir=args.models_dir)
    except Exception as exc:  # invalid --install-dir / --models-dir
        print(f"Error: {getattr(exc, 'code', '') or exc} {getattr(exc, 'detail', '')}".rstrip())
        return 2
    server = make_server(app, args.host, port, host_check=False if args.no_host_check else None)
    url = f"http://{local_host}:{port}/"
    app.url = url
    app.log.info("listening on http://%s:%s/", args.host, port)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        app.log.warning("The GUI is reachable from other computers (bound to %s). It has no login!", args.host)
    print()
    print(f"  {APP_NAME} is running:  {url}")
    print("  Close this window or press Ctrl+C to stop.")
    print()

    if not args.no_browser:
        mode = "browser" if args.browser else ("app" if args.app else app.settings.get("ui_open_mode"))
        threading.Timer(0.6, open_ui, args=(url, mode)).start()

    if args.exit_when_idle:
        start_idle_watchdog(app, server, max(30, args.exit_when_idle))

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping ...")
    finally:
        if not app.shutdown_event.is_set():
            app.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

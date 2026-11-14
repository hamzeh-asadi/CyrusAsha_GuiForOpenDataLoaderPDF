"""
CyrusAsha_GuiForOpenDataLoaderPDF - tests of the install/models folders (``cyrusasha_odl.storage``).

Run from the project root::

    python -m unittest test.test_storage -v

Only the standard library is needed. The tests use temporary folders with
fake ``venv``/``java``/``cache``/``models`` contents; nothing is installed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cyrusasha_odl import CSRF_HEADER  # noqa: E402
from cyrusasha_odl import setup_tasks, storage  # noqa: E402
from cyrusasha_odl.app import App  # noqa: E402
from cyrusasha_odl.config import Paths, Settings  # noqa: E402
from cyrusasha_odl.errors import ApiError  # noqa: E402


def write(path: str, size: int = 1000) -> None:
    """Create a file of ``size`` bytes (and its folders)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def fill_install(paths: Paths) -> None:
    """Create fake contents in the install and models folders of ``paths``."""
    write(os.path.join(paths.venv_dir, "lib", "site.py"), 2000)
    write(os.path.join(paths.java_dir, "jre-21", "bin", "java"), 3000)
    write(os.path.join(paths.pip_cache_dir, "wheels", "a.whl"), 4000)
    write(os.path.join(paths.models_dir, "huggingface", "hub", "model.bin"), 5000)
    write(os.path.join(paths.models_dir, "easyocr", "model", "craft.pth"), 6000)


class TempTestCase(unittest.TestCase):
    """Provides a temporary folder per test."""

    def setUp(self) -> None:
        """Create the temporary folder."""
        self.tmp = tempfile.mkdtemp(prefix="cyrusasha-storage-test-")

    def tearDown(self) -> None:
        """Delete the temporary folder."""
        shutil.rmtree(self.tmp, ignore_errors=True)


class LocationTest(TempTestCase):
    """Paths, settings and child environments."""

    def test_default_locations(self) -> None:
        """By default everything lives in the data folder."""
        paths = Paths(self.tmp)
        self.assertEqual(paths.install_dir, self.tmp)
        self.assertEqual(paths.venv_dir, os.path.join(self.tmp, "venv"))
        self.assertEqual(paths.models_dir, os.path.join(self.tmp, "models"))
        self.assertTrue(paths.install_default and paths.models_default)

    def test_custom_locations(self) -> None:
        """A custom install folder moves venv/java/cache; models follow unless set separately."""
        paths = Paths(self.tmp)
        install = os.path.join(self.tmp, "odl")
        paths.apply_locations(install, "")
        self.assertEqual(paths.java_dir, os.path.join(install, "java"))
        self.assertEqual(paths.models_dir, os.path.join(install, "models"))
        self.assertEqual(paths.pip_cache_dir, os.path.join(install, "cache", "pip"))
        models = os.path.join(self.tmp, "models-big")
        paths.apply_locations(install, models)
        self.assertEqual(paths.models_dir, models)
        self.assertFalse(paths.models_default)

    def test_settings_validation(self) -> None:
        """Only absolute paths (or "" for the default) are accepted."""
        settings = Settings(os.path.join(self.tmp, "settings.json"))
        folder = os.path.join(self.tmp, "x", "..", "odl")
        self.assertEqual(settings.update({"install_dir": folder})["install_dir"], os.path.normpath(folder))
        self.assertEqual(settings.update({"install_dir": ""})["install_dir"], "")
        for bad in ("relative/path", "C:", "\x00"):
            with self.assertRaises(ApiError):
                settings.update({"models_dir": bad})

    def test_child_environment(self) -> None:
        """Model downloads always go to the models folder; pip's cache only for installs."""
        app = App(self.tmp)
        try:
            env = app.env.child_env(java=False)
            self.assertEqual(env["HF_HOME"], os.path.join(app.paths.models_dir, "huggingface"))
            self.assertEqual(env["EASYOCR_MODULE_PATH"], os.path.join(app.paths.models_dir, "easyocr"))
            self.assertNotIn("PIP_CACHE_DIR", env)
            env = app.env.child_env(java=False, install=True)
            self.assertEqual(env["PIP_CACHE_DIR"], app.paths.pip_cache_dir)
            self.assertEqual(env["TEMP"], app.paths.install_tmp_dir)
            self.assertTrue(os.path.isdir(app.paths.install_tmp_dir))
        finally:
            app.shutdown()

    def test_command_line_override(self) -> None:
        """``--install-dir`` / ``--models-dir`` are saved and applied (files are not moved)."""
        install = os.path.join(self.tmp, "elsewhere")
        app = App(self.tmp, install_dir=install, models_dir="")
        try:
            self.assertEqual(app.paths.install_dir, install)
            self.assertEqual(app.settings.get("install_dir"), install)
        finally:
            app.shutdown()


class InstallLocationTest(TempTestCase):
    """Folders chosen for a new installation (install dialog, install.bat, command line)."""

    def test_steps_of_the_install_dialog(self) -> None:
        """Folders sent with "full"/"install" add the "location" step in front."""
        steps_for = setup_tasks.SetupManager.steps_for
        self.assertEqual(steps_for("full", {"install_dir": "D:/ODL"})[0], "location")
        self.assertNotIn("location", steps_for("full", {}))
        self.assertNotIn("hybrid", steps_for("full", {"install_dir": ""}))
        self.assertEqual(steps_for("install", {"models_dir": ""})[:2], ["location", "venv"])
        with self.assertRaises(ApiError):
            steps_for("nonsense", {})

    def test_location_step(self) -> None:
        """The step applies the install folder first, then a default models folder below it."""
        app = App(os.path.join(self.tmp, "data"))
        try:
            target = os.path.join(self.tmp, "odl")
            task = setup_tasks.SetupTask("full", {"install_dir": target, "models_dir": ""}, ["location"],
                                         os.path.join(self.tmp, "task.log"))
            detail = app.setup._step_location(task)
            self.assertEqual(detail, target)
            self.assertEqual(app.paths.install_dir, target)
            self.assertEqual(app.paths.models_dir, os.path.join(target, "models"))
            task = setup_tasks.SetupTask("full", {"install_dir": "relative"}, ["location"],
                                         os.path.join(self.tmp, "task2.log"))
            with self.assertRaises(setup_tasks.StepFailed) as ctx:
                app.setup._step_location(task)
            self.assertEqual(ctx.exception.code, "location_not_absolute")
        finally:
            app.shutdown()

    def test_command_line_errors(self) -> None:
        """Invalid --install-dir values are refused with the location error code."""
        with self.assertRaises(ApiError) as ctx:
            App(os.path.join(self.tmp, "data"), install_dir="relative/path")
        self.assertEqual(ctx.exception.code, "location_not_absolute")

    def test_show_locations(self) -> None:
        """``--show-locations`` prints both folders quickly and creates nothing."""
        import subprocess

        data = os.path.join(self.tmp, "data")
        result = subprocess.run([sys.executable, os.path.join(ROOT, "src", "main.py"), "--show-locations",
                                 "--data", data], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Install folder", result.stdout)
        self.assertIn(os.path.join(data, "models"), result.stdout)
        self.assertFalse(os.path.exists(data))


class CheckTargetTest(TempTestCase):
    """Validation of folders chosen by the user."""

    def setUp(self) -> None:
        """Create paths with fake contents."""
        super().setUp()
        self.data = os.path.join(self.tmp, "data")
        self.paths = Paths(self.data)
        fill_install(self.paths)

    def test_valid_new_folder(self) -> None:
        """A new folder is accepted, measured and not left behind by the write test."""
        target = os.path.join(self.tmp, "new", "odl")
        result = storage.check_target(self.paths, "install", target)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["setting"], target)
        self.assertFalse(os.path.exists(target))
        self.assertIsNotNone(result["free"])

    def test_check_creates_nothing(self) -> None:
        """Checking a deep new path leaves no folders behind (the user may still be typing)."""
        target = os.path.join(self.tmp, "a", "b", "c", "odl")
        self.assertTrue(storage.check_target(self.paths, "install", target)["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "a")))
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith(".cyrusasha")], [])

    def test_default_and_same(self) -> None:
        """An empty value means the default location; the current folder is reported as same."""
        result = storage.check_target(self.paths, "install", "")
        self.assertTrue(result["ok"] and result["default"] and result["same"])
        self.assertEqual(result["setting"], "")

    def test_errors(self) -> None:
        """Relative paths, files, program folders and folders inside moved folders are refused."""
        cases = {
            "relative": "not_absolute",
            os.path.join(ROOT, "src", "x"): "inside_app",
            os.path.join(self.paths.venv_dir, "sub"): "nested",
            os.path.join(self.paths.java_dir, "jre-21", "bin", "java"): "nested",
        }
        for path, error in cases.items():
            self.assertEqual(storage.check_target(self.paths, "install", path)["error"], error, path)
        some_file = os.path.join(self.tmp, "file.txt")
        write(some_file)
        self.assertEqual(storage.check_target(self.paths, "install", some_file)["error"], "is_file")
        self.assertEqual(storage.check_target(self.paths, "models", os.path.join(self.paths.models_dir, "x"))["error"],
                         "nested")
        self.assertEqual(storage.check_target(self.paths, "models", self.paths.install_dir)["error"], "nested")

    def test_conflicts_and_non_empty(self) -> None:
        """Existing installation folders in the target are reported."""
        target = os.path.join(self.tmp, "other")
        os.makedirs(os.path.join(target, "venv"))
        write(os.path.join(target, "readme.txt"))
        result = storage.check_target(self.paths, "install", target)
        self.assertTrue(result["ok"])
        self.assertEqual(result["conflicts"], ["venv"])
        self.assertFalse(result["empty"])

    def test_warnings(self) -> None:
        """Non-ASCII paths and network shares produce warnings."""
        self.assertIn("non_ascii", storage.path_warnings(os.path.join(self.tmp, "پوشه"), "install"))
        self.assertIn("network", storage.path_warnings("\\\\server\\share\\odl", "models"))
        self.assertEqual(storage.path_warnings(os.path.join(self.tmp, "odl"), "install"), [])


class MoveTest(TempTestCase):
    """Moving folders: rename, copy with progress, cancel, links."""

    def make_source(self) -> str:
        """Create a source folder with nested files."""
        source = os.path.join(self.tmp, "src")
        write(os.path.join(source, "a.bin"), 10_000)
        write(os.path.join(source, "sub", "b.bin"), 20_000)
        return source

    def test_rename_on_same_drive(self) -> None:
        """On the same drive the folder is renamed."""
        source = self.make_source()
        target = os.path.join(self.tmp, "dst", "folder")
        storage.move_folder(source, target, lambda _n, _v: None)
        self.assertFalse(os.path.exists(source))
        self.assertEqual(storage.dir_size(target), (30_000, 2))

    def test_copy_with_progress(self) -> None:
        """A copy reports the bytes and deletes the source afterwards."""
        source = self.make_source()
        target = os.path.join(self.tmp, "copy")
        seen = []
        done = storage.move_folder(source, target, lambda name, value: seen.append(value), force_copy=True)
        self.assertEqual(done, 30_000)
        self.assertEqual(seen[-1], 30_000)
        self.assertFalse(os.path.exists(source))
        self.assertEqual(storage.dir_size(target), (30_000, 2))

    def test_cancel_keeps_the_source(self) -> None:
        """A canceled copy is removed and the source stays complete."""
        source = self.make_source()
        target = os.path.join(self.tmp, "copy")
        cancel = threading.Event()

        def progress(_name: str, value: int) -> None:
            """Cancel after the first file."""
            if value >= 10_000:
                cancel.set()

        with self.assertRaises(storage.MoveCanceled):
            storage.move_folder(source, target, progress, cancel, force_copy=True)
        self.assertFalse(os.path.exists(target))
        self.assertEqual(storage.dir_size(source), (30_000, 2))

    def test_existing_target_is_refused(self) -> None:
        """Nothing is ever overwritten; an empty target folder is fine."""
        source = self.make_source()
        target = os.path.join(self.tmp, "busy")
        write(os.path.join(target, "keep.txt"))
        with self.assertRaises(storage.MoveError) as ctx:
            storage.move_folder(source, target, lambda _n, _v: None)
        self.assertEqual(ctx.exception.code, "target_exists")
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        storage.move_folder(source, empty, lambda _n, _v: None)
        self.assertTrue(os.path.isfile(os.path.join(empty, "a.bin")))

    @unittest.skipIf(os.name == "nt", "symbolic links need extra rights on Windows")
    def test_links_are_kept(self) -> None:
        """Relative links (Hugging Face cache, venv interpreter) survive a copy."""
        source = self.make_source()
        os.symlink(os.path.join("..", "a.bin"), os.path.join(source, "sub", "link.bin"))
        target = os.path.join(self.tmp, "copy")
        storage.move_folder(source, target, lambda _n, _v: None, force_copy=True)
        link = os.path.join(target, "sub", "link.bin")
        self.assertTrue(os.path.islink(link))
        with open(link, "rb") as fh:
            self.assertEqual(len(fh.read()), 10_000)


class RelocateTaskTest(TempTestCase):
    """The installer actions ``relocate``, ``clear-cache`` and ``clear-models``."""

    def setUp(self) -> None:
        """Create an application with fake installed contents."""
        super().setUp()
        self.app = App(os.path.join(self.tmp, "data"))
        fill_install(self.app.paths)

    def tearDown(self) -> None:
        """Stop the application."""
        self.app.shutdown()
        super().tearDown()

    def run_action(self, action: str, params: dict) -> dict:
        """Start an installer action and wait for its end."""
        self.app.setup.start(action, params)
        deadline = time.time() + 30
        while time.time() < deadline:
            status = self.app.setup.status()
            if status and status["state"] != "running":
                return status
            time.sleep(0.05)
        self.fail("installer action did not finish")

    def test_move_install_folder(self) -> None:
        """venv, java, cache and the default models folder move; the setting changes."""
        target = os.path.join(self.tmp, "odl")
        status = self.run_action("relocate", {"kind": "install", "path": target, "mode": "move", "force_copy": True})
        self.assertEqual(status["state"], "success", status)
        paths = self.app.paths
        self.assertEqual(paths.install_dir, target)
        self.assertEqual(self.app.settings.get("install_dir"), target)
        for relative in ("venv/lib/site.py", "java/jre-21/bin/java", "cache/pip/wheels/a.whl",
                         "models/huggingface/hub/model.bin"):
            self.assertTrue(os.path.isfile(os.path.join(target, *relative.split("/"))), relative)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "data", "venv")))
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "data", "jobs")))  # data stays

    def test_move_models_folder_only(self) -> None:
        """A separate models folder takes the models; the install folder stays."""
        target = os.path.join(self.tmp, "big", "models")
        status = self.run_action("relocate", {"kind": "models", "path": target, "mode": "move"})
        self.assertEqual(status["state"], "success", status)
        self.assertEqual(self.app.paths.models_dir, target)
        self.assertTrue(os.path.isfile(os.path.join(target, "easyocr", "model", "craft.pth")))
        self.assertTrue(os.path.isfile(os.path.join(self.app.paths.venv_dir, "lib", "site.py")))
        self.assertEqual(self.app.env.child_env(java=False)["HF_HOME"], os.path.join(target, "huggingface"))

    def test_failed_move_is_rolled_back(self) -> None:
        """If a later folder fails, the folders moved before go back and the setting stays."""
        original = setup_tasks.move_folder
        calls = {"n": 0}

        def flaky(source, target, progress, cancel=None, done=0, force_copy=False):
            """Fail on the second folder."""
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            return original(source, target, progress, cancel, done, force_copy)

        setup_tasks.move_folder = flaky
        try:
            status = self.run_action("relocate", {"kind": "install", "path": os.path.join(self.tmp, "odl"), "mode": "move"})
        finally:
            setup_tasks.move_folder = original
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"], "move_failed")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "data", "venv", "lib", "site.py")))
        self.assertEqual(self.app.settings.get("install_dir"), "")

    def test_start_fresh_and_delete_old(self) -> None:
        """"fresh" switches the folder; ``delete_old`` removes the old files."""
        target = os.path.join(self.tmp, "fresh")
        status = self.run_action("relocate", {"kind": "install", "path": target, "mode": "fresh", "delete_old": True})
        self.assertEqual(status["state"], "success", status)
        self.assertEqual(self.app.paths.install_dir, target)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "data", "venv")))
        self.assertFalse(os.path.exists(os.path.join(target, "venv")))

    def test_conflict_is_refused(self) -> None:
        """Moving into a folder that already has a venv fails before anything moves."""
        target = os.path.join(self.tmp, "odl")
        write(os.path.join(target, "venv", "other.py"))
        status = self.run_action("relocate", {"kind": "install", "path": target, "mode": "move"})
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"], "location_target_exists")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "data", "venv", "lib", "site.py")))

    def test_clear_cache_and_models(self) -> None:
        """The pip cache and the downloaded models can be deleted."""
        self.assertEqual(self.run_action("clear-cache", {})["state"], "success")
        self.assertFalse(os.path.exists(self.app.paths.cache_dir))
        self.assertEqual(self.run_action("clear-models", {})["state"], "success")
        self.assertFalse(os.path.exists(self.app.paths.models_dir))
        self.assertEqual(self.run_action("clear-models", {})["steps"][0]["state"], "skipped")


class StorageApiTest(TempTestCase):
    """HTTP API of the storage feature."""

    def setUp(self) -> None:
        """Start a server on a free port with a temporary data folder."""
        super().setUp()
        from cyrusasha_odl.server import make_server

        self.app = App(os.path.join(self.tmp, "data"))
        fill_install(self.app.paths)
        self.server = make_server(self.app, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        """Stop the server."""
        self.server.shutdown()
        self.server.server_close()
        self.app.shutdown()
        super().tearDown()

    def call(self, method: str, path: str, body=None):
        """Send a request; return ``(status, json)``."""
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                         headers={"Content-Type": "application/json", CSRF_HEADER.split(":")[0]: "1"},
                                         data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_report_check_and_change(self) -> None:
        """Report sizes, check a folder, refuse the generic settings route, then move."""
        deadline = time.time() + 10
        status, report = self.call("GET", "/api/storage")
        while report["pending"] and time.time() < deadline:
            time.sleep(0.1)
            status, report = self.call("GET", "/api/storage")
        self.assertEqual(status, 200)
        self.assertEqual(report["install"]["size"], 2000 + 3000 + 4000)
        self.assertEqual(report["models"]["parts"]["easyocr"], 6000)
        target = os.path.join(self.tmp, "odl")
        status, check = self.call("POST", "/api/storage/check", {"kind": "install", "path": target})
        self.assertEqual((status, check["ok"]), (200, True))
        status, error = self.call("POST", "/api/settings", {"install_dir": target})
        self.assertEqual(status, 400)
        status, error = self.call("POST", "/api/storage/change", {"kind": "install", "path": "", "mode": "move"})
        self.assertEqual(error.get("code") or error.get("error"), "location_same")
        status, task = self.call("POST", "/api/storage/change", {"kind": "install", "path": target, "mode": "move"})
        self.assertEqual(status, 200)
        self.assertEqual(task["action"], "relocate")
        while self.app.setup.busy() and time.time() < deadline + 20:
            time.sleep(0.05)
        self.assertEqual(self.app.paths.install_dir, target)


if __name__ == "__main__":
    unittest.main()

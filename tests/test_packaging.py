import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class ConfigPathTests(unittest.TestCase):
    def test_override_creates_appdata_structure_without_reseeding_database(self):
        import app.config as config
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"CASHBOOK_APP_DATA_DIR": directory}, clear=False
        ):
            config = importlib.reload(config)
            config.ensure_application_directories()
            database = config.DATABASE_PATH
            database.parent.mkdir(parents=True, exist_ok=True)
            database.write_bytes(b"existing accounting data")
            config.ensure_application_directories()
            self.assertEqual(database.read_bytes(), b"existing accounting data")
            for path in (config.DATA_DIR, config.CASHBOOK_DIR, config.CASHBOOK_BACKUP_DIR,
                         config.DATABASE_BACKUP_DIR, config.LOG_DIR, config.CONFIG_DIR):
                self.assertTrue(path.is_dir())
            self.assertEqual(config.APP_DATA_DIR, Path(directory).resolve())
            self.assertNotIn("_MEI", str(config.DATABASE_PATH))
        importlib.reload(config)

    def test_resource_lookup_uses_bundle_root_when_present(self):
        import app.config as config
        with tempfile.TemporaryDirectory() as directory, patch.object(sys, "_MEIPASS", directory, create=True):
            self.assertEqual(config.resource_path("app", "static", "review.html"),
                             Path(directory) / "app" / "static" / "review.html")


class LauncherTests(unittest.TestCase):
    def test_health_polling_retries_then_succeeds(self):
        from app import launcher

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False

        calls = []
        def open_url(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("not ready")
            return Response()

        with patch("app.launcher.urllib.request.urlopen", side_effect=open_url), patch("app.launcher.time.sleep"):
            self.assertTrue(launcher.wait_for_health("http://127.0.0.1/health", timeout=1))
        self.assertEqual(len(calls), 2)

    def test_shutdown_requests_clean_server_exit(self):
        from app import launcher

        class Server: should_exit = False
        class Thread:
            def __init__(self): self.joined = False
            def join(self, timeout): self.joined = True
            def is_alive(self): return False

        server, thread = Server(), Thread()
        launcher.shutdown_server(server, thread)
        self.assertTrue(server.should_exit)
        self.assertTrue(thread.joined)

    def test_production_launcher_uses_desktop_webview_not_browser(self):
        from app import launcher

        calls = []
        class Server:
            should_exit = False
            def __init__(self, config): pass
            def run(self): pass
        desktop = types.SimpleNamespace(
            create_window=lambda *args, **kwargs: calls.append((args, kwargs)),
            start=lambda: calls.append(("start", {})),
        )
        with patch.dict(sys.modules, {"webview": desktop}), patch.object(
            launcher, "select_port", return_value=8123
        ), patch.object(launcher, "wait_for_health", return_value=True), patch.object(
            launcher.uvicorn, "Server", Server
        ), patch.object(launcher.webbrowser, "open") as browser:
            self.assertEqual(launcher.run(), 0)
        browser.assert_not_called()
        self.assertEqual(calls[0][0][0], "Bursar Cashbook")

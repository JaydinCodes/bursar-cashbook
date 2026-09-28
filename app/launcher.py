"""Windowed production entry point for Ledgerly.

This module owns the local-only Uvicorn server.  It is intentionally separate
from FastAPI so normal developer ASGI usage remains possible.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
import os
import webbrowser
from pathlib import Path
from contextlib import closing

import uvicorn

from app.config import APP_DATA_DIR, LOG_DIR, ensure_application_directories, resource_path
from app.errors import new_error_id
from app.logging_config import logger

HOST = "127.0.0.1"
PREFERRED_PORT = 8000
HEALTH_TIMEOUT_SECONDS = 20.0


class LauncherError(RuntimeError):
    pass


def _port_is_available(port: int) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as candidate:
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            candidate.bind((HOST, port))
        except OSError:
            return False
    return True


def select_port() -> int:
    """Use 8000 when safe, otherwise request an OS-selected loopback port."""
    if _port_is_available(PREFERRED_PORT):
        return PREFERRED_PORT
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as candidate:
        candidate.bind((HOST, 0))
        return int(candidate.getsockname()[1])


def wait_for_health(url: str, *, timeout: float = HEALTH_TIMEOUT_SECONDS,
                    interval: float = 0.2) -> bool:
    """Poll health with a bounded retry; no fixed startup sleep is used."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=min(2.0, interval + 1.0)) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(interval)
    return False


def shutdown_server(server: uvicorn.Server, thread: threading.Thread,
                    *, timeout: float = 10.0) -> None:
    """Ask Uvicorn to finish requests and wait for its serving thread."""
    server.should_exit = True
    thread.join(timeout=timeout)
    if thread.is_alive():
        logger.warning("launcher_server_shutdown_timed_out")


def _show_startup_error(error_id: str) -> None:
    message = (
        "Ledgerly could not start. Please close any previous copy and try again.\n\n"
        f"Error ID: {error_id}\n"
        f"Support files: {LOG_DIR}\n"
        f"Application data: {APP_DATA_DIR}"
    )
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Ledgerly", 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def _acquire_instance_lock():
    """Return a held Windows lock file, or None when another instance owns it."""
    path = Path(APP_DATA_DIR) / "BursarCashbook.instance.lock"
    handle = path.open("a+")
    if sys.platform != "win32":
        return handle
    try:
        import msvcrt
        handle.seek(0)
        if not handle.read(1):
            handle.write("0"); handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        handle.close()
        return None


def _show_already_running() -> None:
    message = "Ledgerly is already running. Please use the open application window."
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Ledgerly", 0x40)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def run() -> int:
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    lock_handle = None
    try:
        ensure_application_directories()
        lock_handle = _acquire_instance_lock()
        if lock_handle is None:
            _show_already_running()
            return 0
        port = select_port()
        config = uvicorn.Config("app.main:app", host=HOST, port=port, log_config=None)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="BursarCashbookServer")
        thread.start()
        url = f"http://{HOST}:{port}/"
        if not wait_for_health(url + "health"):
            raise LauncherError("The local service did not become ready in time.")
        logger.info("launcher_server_ready", extra={"host": HOST, "port": port})
        if os.getenv("CASHBOOK_OPEN_BROWSER") == "1":
            # Deliberately development-only: packaged use gets a native shell.
            webbrowser.open(url)
            while thread.is_alive():
                time.sleep(0.5)
            raise LauncherError("The local service stopped unexpectedly.")

        import webview
        webview.create_window(
            "Ledgerly", url, width=1400, height=900,
            min_size=(1100, 700), resizable=True,
            icon=str(resource_path("assets", "ledgerly.ico")),
        )
        # webview.start blocks until the native application window closes.
        # The finally block then asks Uvicorn to shut down cleanly.
        webview.start()
        logger.info("launcher_window_closed")
        return 0
    except KeyboardInterrupt:
        logger.info("launcher_shutdown_requested")
        return 0
    except Exception:
        error_id = new_error_id()
        logger.exception("launcher_startup_failed", extra={"error_id": error_id})
        _show_startup_error(error_id)
        return 1
    finally:
        if server is not None and thread is not None and thread.is_alive():
            shutdown_server(server, thread)
        if lock_handle is not None:
            lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(run())

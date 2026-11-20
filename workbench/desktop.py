"""Data Workbench as a desktop program: its own window, no browser tab and no console.

The same local app that `python -m workbench.app` serves runs in a background thread on a free
loopback port, and a WebView2 window (Microsoft Edge's engine, part of Windows 10 and 11) shows it.
Closing the window stops the app. A second launch brings the open window forward instead of
starting another copy. When the window cannot be created, the app opens in the browser with a
console window, as before 3.0.

Started with pythonw.exe there is no console, so everything is logged to
%LOCALAPPDATA%/DataWorkbench/logs/desktop.log.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from .common import ROOT, VERSION

APP_ID = "ZakJRademacher.DataWorkbench"
TITLE = "Data Workbench"
ICON = ROOT / "packaging" / "workbench.ico"
INSTANCE = ".desktop-instance.json"
log = logging.getLogger("workbench.desktop")


def data_root():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DataWorkbench"


def setup_logging():
    folder = data_root() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "desktop.log"
    if path.exists() and path.stat().st_size > 2_000_000:
        path.replace(folder / "desktop.previous.log")
    stream = open(path, "a", encoding="utf-8", buffering=1)
    # pythonw.exe has no stdout or stderr; anything printed would otherwise raise or vanish.
    if sys.stdout is None or sys.stderr is None or "pythonw" in Path(sys.executable).name.lower():
        sys.stdout = sys.stderr = stream
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(stream)],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    log.info("Data Workbench %s starting (%s)", VERSION, sys.executable)
    return path


def message(text, title=TITLE, question=False):
    """A Windows message box; True when the person chose Yes (or OK)."""
    if os.name != "nt":
        log.warning(text)
        return False
    flags = 0x04 | 0x30 if question else 0x40  # Yes/No + warning icon, or OK + information icon
    flags |= 0x10000  # keep it in front
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags) in (1, 6)


def free_socket():
    """A listening loopback socket on a port the system chose, so no fixed port can collide."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    return sock


def bring_forward(root):
    """Ask an already-running desktop session on these projects to show its window."""
    try:
        record = json.loads((Path(root) / INSTANCE).read_text(encoding="utf-8"))
        request = urllib.request.Request(f"http://127.0.0.1:{int(record['port'])}/api/desktop/focus", method="POST",
                                         headers={"X-Workbench-Desktop": record["key"]})
        with urllib.request.urlopen(request, timeout=3) as reply:
            return reply.status == 204
    except (OSError, ValueError, KeyError, TypeError):
        return False


class Server:
    """Uvicorn on its own thread, so the window can own the main thread."""

    def __init__(self, app, sock):
        import uvicorn
        self.sock = sock
        self.port = sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, log_config=None, log_level="warning", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, name="workbench-server", daemon=True)

    def start(self, timeout=20):
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("The local app did not start.")
            time.sleep(0.05)

    def stop(self, timeout=10):
        self.server.should_exit = True
        self.thread.join(timeout)


def browser_fallback(root, reason):
    """Open the app the way 2.x did: in the browser, with a console window to stop it."""
    log.error("Desktop window unavailable, opening in the browser instead: %s", reason)
    console = Path(sys.executable).with_name("python.exe")
    if not console.exists():
        console = Path(sys.executable)
    subprocess.Popen([str(console), "-B", "-m", "workbench.app", "--open-browser", "--projects", str(root)],
                     cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def projects_root(arg=None):
    return Path(arg or os.environ.get("DATA_WORKBENCH_PROJECTS") or ROOT / "projects").resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Data Workbench desktop window")
    parser.add_argument("--projects", type=Path)
    parser.add_argument("--browser", action="store_true", help="open in the browser instead of a window")
    parser.add_argument("--check", action="store_true", help="start, confirm the app answers, then exit")
    args = parser.parse_args(argv)
    setup_logging()
    root = projects_root(args.projects)
    if args.browser:
        browser_fallback(root, "--browser was requested")
        return 0
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    from .app import create_app
    app = create_app(root)
    store = app.state.store
    if not store.primary:
        store.close()
        if bring_forward(root):
            log.info("Brought the open window forward; exiting")
            return 0
        message("Data Workbench is already open with these projects in another window.")
        return 1

    key = secrets.token_urlsafe(24)
    app.state.desktop_key = key
    sock = free_socket()
    server = Server(app, sock)
    server.start()
    url = f"http://127.0.0.1:{server.port}/"
    instance = root / INSTANCE
    instance.write_text(json.dumps({"port": server.port, "key": key, "pid": os.getpid()}), encoding="utf-8")
    log.info("Serving %s from %s", url, root)
    fallback = None
    try:
        if args.check:
            with urllib.request.urlopen(url + "api/session", timeout=5) as reply:
                version = json.load(reply)["version"]
            print("Data Workbench", version, "answered at", url)
            return 0
        try:
            run_window(app, url)
        except Exception as error:  # WebView2 or .NET could not be loaded
            log.exception("Window failed")
            fallback = error
    finally:
        server.stop()
        instance.unlink(missing_ok=True)
        abandoned = store.busy
        store.close()
        log.info("Stopped%s", " with a run still processing" if abandoned else "")
    if fallback is not None:
        browser_fallback(root, fallback)
    elif abandoned:
        # The person chose to close during a run. Its worker thread cannot be stopped from here, so
        # end the process; the next start marks the run interrupted.
        logging.shutdown()
        os._exit(0)
    return 0


def run_window(app, url):
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    store = app.state.store
    window = webview.create_window(TITLE, url, width=1360, height=900, min_size=(900, 620),
                                   text_select=True, zoomable=True, background_color="#0E1A2B")

    def focus():
        window.restore()
        window.show()
        window.on_top = True
        window.on_top = False

    app.state.desktop_focus = focus

    def closing():
        if store.busy and not message("A run is still processing. Close Data Workbench anyway?\n\n"
                                      "The run will be marked interrupted and you can run it again.", question=True):
            return False
        return True

    window.events.closing += closing
    webview.start(gui="edgechromium", icon=str(ICON) if ICON.exists() else None, private_mode=True)


if __name__ == "__main__":
    sys.exit(main())

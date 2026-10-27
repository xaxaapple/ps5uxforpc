"""Точка входа: окно WebView2 (pywebview) + локальный сервер + бэкенд + геймпад."""
from __future__ import annotations

import ctypes
import logging
import logging.handlers
import sys
import threading

from . import PRODUCT, VERSION, paths


def _message(text: str, error: bool = True) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, PRODUCT, 0x10 if error else 0x40)      # type: ignore[attr-defined]
    except Exception:                                                                    # noqa: BLE001
        print(text, file=sys.stderr)


def _single_instance() -> bool:
    try:
        k = ctypes.windll.kernel32                                                       # type: ignore[attr-defined]
        k.CreateMutexW(None, False, "Local\\PC5LauncherWindowsSingleton")
        return k.GetLastError() != 183                                                   # ERROR_ALREADY_EXISTS
    except Exception:                                                                    # noqa: BLE001
        return True


def selftest() -> int:
    """Проверка собранного приложения без окна (для CI): пакеты на месте, сервер и RPC отвечают."""
    import json
    import urllib.request

    from .backend import Backend
    from .server import EventBus, Server
    try:
        import webview  # noqa: F401
        import webview.platforms.edgechromium  # noqa: F401  (тянет pythonnet и WebView2-библиотеки)
    except Exception as e:                                                               # noqa: BLE001
        logging.getLogger("pc5").exception("selftest: webview")
        _write_result("FAIL webview: %r" % e)
        return 3
    bus = EventBus()
    backend = Backend(bus.emit, lambda: None, lambda: None)
    server = Server(backend.call, bus, lambda: server.query_string("en"))
    server.start()
    try:
        with urllib.request.urlopen(server.url("en"), timeout=20) as r:
            html = r.read().decode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:%d/rpc" % server.port,
                                     json.dumps({"method": "sys.state", "params": {}}).encode(),
                                     {"X-PC5-Token": server.token})
        with urllib.request.urlopen(req, timeout=20) as r:
            state = json.loads(r.read().decode("utf-8"))
        ok = "webkit" in html and state.get("ok") and state["result"].get("version") == VERSION
    except Exception as e:                                                               # noqa: BLE001
        _write_result("FAIL server: %r" % e)
        return 4
    finally:
        backend.stop()
        server.stop()
    _write_result("OK" if ok else "FAIL content")
    return 0 if ok else 5


def _write_result(text: str) -> None:
    try:
        with open(paths.data_dir() + "\\selftest.txt", "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def main() -> int:
    handler = logging.handlers.RotatingFileHandler(paths.log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("pc5")
    log.info("%s %s startet", PRODUCT, VERSION)
    if "--selftest" in sys.argv:
        return selftest()
    if sys.platform == "win32" and not _single_instance():
        _message("PC5 Launcher is already running.", error=False)
        return 0
    try:
        import webview
    except ImportError:
        _message("The 'pywebview' component is missing. Reinstall PC5 Launcher.")
        return 2

    from .backend import Backend
    from .pad import PadReader
    from .server import EventBus, Server

    bus = EventBus()
    holder: dict = {}

    def lang() -> str:
        return backend.settings.data.get("lang", "en")

    def quit_app() -> None:
        w = holder.get("window")
        if w is not None:
            w.destroy()

    def reload_page() -> None:
        w = holder.get("window")
        if w is not None:
            w.load_url(server.url(lang()))

    backend = Backend(bus.emit, quit_app, reload_page)
    server = Server(backend.call, bus, lambda: server.query_string(lang()), backend.art_file)
    server.start()
    pad = PadReader(bus.emit)
    pad.start()

    fullscreen = "--fullscreen" in sys.argv
    window = webview.create_window(PRODUCT, server.url(lang()), width=1280, height=720, min_size=(960, 540),
                                   background_color="#05070d", fullscreen=fullscreen, text_select=False)
    holder["window"] = window
    window.events.closed += lambda: (pad.stop(), backend.stop())
    try:
        webview.start(storage_path=paths.data_dir() + "\\webview", private_mode=False)
    except Exception as e:                                                               # noqa: BLE001
        log.exception("Fenster")
        _message("The window could not be opened.\n\nMicrosoft Edge WebView2 Runtime is required "
                 "(it ships with Windows 11).\n\n%s" % e)
        return 1
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Локальный HTTP-сервер: отдаёт интерфейс (app/web), принимает RPC и раздаёт события длинным опросом.
Слушает только 127.0.0.1; RPC и события требуют случайный токен, Host проверяется (защита от DNS rebinding)."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from . import paths

MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
        ".json": "application/json", ".svg": "image/svg+xml", ".ttf": "font/ttf", ".woff2": "font/woff2",
        ".woff": "font/woff", ".txt": "text/plain", ".ogg": "audio/ogg", ".opus": "audio/ogg",
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_BODY = 4 * 1024 * 1024
ART_MAX = 32 * 1024 * 1024
ART_KEY = re.compile(r"[0-9a-f]{16}-(tile|hero)")
POLL_WAIT_S = 20.0


class EventBus:
    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._events: list[tuple[int, str, dict]] = []
        self._seq = 0

    def emit(self, event: str, payload: dict | None = None) -> None:
        with self._cv:
            self._seq += 1
            self._events.append((self._seq, event, payload or {}))
            del self._events[:-300]
            self._cv.notify_all()

    def wait(self, since: int) -> tuple[int, list[dict]]:
        with self._cv:
            if since > self._seq:                  # сервер перезапущен: начинаем сначала
                since = 0
            if not [e for e in self._events if e[0] > since]:
                self._cv.wait(POLL_WAIT_S)
            fresh = [{"event": e, "payload": p} for s, e, p in self._events if s > since]
            return self._seq, fresh


class Server:
    def __init__(self, rpc: Callable[[str, dict], dict], bus: EventBus, query: Callable[[], str],
                 art: Callable[[str], str | None] | None = None):
        self.token = secrets.token_urlsafe(24)
        self.rpc, self.bus, self.query = rpc, bus, query
        self.art = art
        self._web = paths.web_dir()
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [os.path.join(here, "shim.js"), os.path.join(getattr(sys, "_MEIPASS", here), "pc5win", "shim.js")]
        shim_path = next((c for c in candidates if os.path.isfile(c)), candidates[0])
        with open(shim_path, encoding="utf-8") as f:
            self._shim = f.read().replace("__PC5_TOKEN__", self.token)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a: Any) -> None:      # тишина в консоли
                pass

            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").split(":")[0]
                return host in ("127.0.0.1", "localhost")

            def _send(self, code: int, body: bytes, ctype: str = "application/json", cache: str = "no-store") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _authed(self) -> bool:
                return secrets.compare_digest(self.headers.get("X-PC5-Token") or "", outer.token)

            def do_GET(self) -> None:           # noqa: N802
                if not self._host_ok():
                    return self._send(403, b"{}")
                url = urllib.parse.urlsplit(self.path)
                if url.path == "/poll":
                    if not self._authed():
                        return self._send(403, b"{}")
                    since = int((urllib.parse.parse_qs(url.query).get("since") or ["0"])[0] or 0)
                    seq, events = outer.bus.wait(since)
                    return self._send(200, json.dumps({"seq": seq, "events": events, "query": outer.query()},
                                                      ensure_ascii=False).encode("utf-8"))
                if url.path.startswith("/ps5art/"):
                    return self._art(url.path[len("/ps5art/"):])
                rel = urllib.parse.unquote(url.path).lstrip("/") or "index.html"
                if rel == "index.html":
                    if (urllib.parse.parse_qs(url.query).get("k") or [""])[0] != outer.token:
                        return self._send(403, b"Forbidden", "text/plain")
                    return self._index()
                full = os.path.realpath(os.path.join(outer._web, rel))
                if not full.startswith(os.path.realpath(outer._web) + os.sep) or not os.path.isfile(full):
                    return self._send(404, b"Not found", "text/plain")
                ext = os.path.splitext(full)[1].lower()
                ctype = MIME.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
                with open(full, "rb") as f:
                    self._send(200, f.read(), ctype)

            def _art(self, key: str) -> None:
                """Иконка и фон PS5-игры. Файл берётся только по ключу, который выдал сам лаунчер (никаких путей из URL)."""
                path = outer.art(key) if outer.art and ART_KEY.fullmatch(key) else None
                if not path or not os.path.isfile(path):
                    return self._send(404, b"Not found", "text/plain")
                ext = os.path.splitext(path)[1].lower()
                if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                    return self._send(404, b"Not found", "text/plain")
                try:
                    with open(path, "rb") as f:
                        data = f.read(ART_MAX + 1)
                except OSError:
                    return self._send(404, b"Not found", "text/plain")
                if len(data) > ART_MAX:
                    return self._send(404, b"Not found", "text/plain")
                self._send(200, data, MIME.get(ext, "image/png"), "private, max-age=300")

            def _index(self) -> None:
                with open(os.path.join(outer._web, "index.html"), encoding="utf-8") as f:
                    html = f.read()
                tag = "<script>" + outer._shim + "</script>"
                html = html.replace("<head>", "<head>" + tag, 1)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

            def do_POST(self) -> None:          # noqa: N802
                if not self._host_ok() or not self._authed() or self.path != "/rpc":
                    return self._send(403, b"{}")
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > MAX_BODY:
                    return self._send(400, b"{}")
                try:
                    msg = json.loads(self.rfile.read(n).decode("utf-8"))
                    method = msg["method"]
                    if not isinstance(method, str):
                        raise ValueError
                    reply = outer.rpc(method, msg.get("params") or {})
                except (ValueError, KeyError, TypeError):
                    reply = {"ok": False, "error": "BadRequest", "message": "Invalid call."}
                self._send(200, json.dumps(reply, ensure_ascii=False, default=str).encode("utf-8"))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    def start(self) -> None:
        threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True).start()

    def stop(self) -> None:
        self.httpd.shutdown()

    def url(self, lang: str) -> str:
        return "http://127.0.0.1:%d/?%s" % (self.port, self.query_string(lang))

    def query_string(self, lang: str) -> str:
        q = {"k": self.token}
        if lang == "en":
            q["lang"] = "en"
        return urllib.parse.urlencode(q)

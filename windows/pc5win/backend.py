"""RPC-методы интерфейса для Windows. Имена методов и формы ответов совпадают с Linux-ядром (docs/RPC.md),
поэтому интерфейс из app/web работает без изменений."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from typing import Any, Callable

from . import PRODUCT, VERSION, paths, ps5, sources, themes
from .store import JsonStore

try:
    import psutil
except ImportError:      # сборка без psutil: запуск работает, но «идёт игра» не определяется
    psutil = None        # type: ignore

_log = logging.getLogger("pc5.backend")

OPTION_DEFAULTS = {
    "proton": "", "fsr4": "auto", "hdr": "auto", "optiscaler": "off", "freeze": True, "prefer_sdl": True,
    "gameid": "umu-default", "env": {}, "args": "", "exe": "", "resolution": "native", "fps_limit": 0,
    "perf_overlay": "", "launcher": "auto", "saves": "", "pad": "auto", "pad_look": 1.0, "pad_spots": {},
    "pad_keys": {},
}
UI_DEFAULTS = {"sounds": True, "soundVolume": 60, "reduceMotion": False, "scale": 1, "clock24h": True,
               "liveBackground": True, "theme": "", "themeCss": ""}
THEME_NAMES = {"ocean": "Ocean", "emerald": "Emerald", "violet": "Purple", "ember": "Ember", "graphite": "Graphite"}
POLL_S = 3.0
PS5_DEFAULTS = {"emulator": "", "folders": [], "games": [], "ignored": [], "fullscreen": False, "amd_cpu": False,
                "hide_cursor": True, "present_mode": "", "vblank": ""}


class RpcError(Exception):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code


class Backend:
    def __init__(self, emit: Callable[[str, dict], None], quit_app: Callable[[], None],
                 reload_page: Callable[[], None]):
        self.emit = emit
        self.quit_app = quit_app
        self.reload_page = reload_page
        self.settings = JsonStore("settings.json", {})
        self.meta = JsonStore("library.json", {})        # id -> {favorite, hidden, title, last_played, playtime, added, options}
        self.local = JsonStore("local-games.json", {"games": []})
        self.order = JsonStore("home-order.json", {"games": []})
        self._found: dict[str, sources.Found] = {}
        self._running: dict[str, dict] = {}
        self._ps5_art: dict[str, str] = {}               # ключ картинки -> файл (отдаётся сервером только по ключу)
        self._scan_lock = threading.Lock()
        self._scanned = threading.Event()
        self._stop = threading.Event()
        self._foreground: str | None = None
        self.started = time.time()
        self.methods: dict[str, Callable[[dict], Any]] = {}
        self._register()
        threading.Thread(target=self._scan_loop, name="scan", daemon=True).start()
        threading.Thread(target=self._watch_loop, name="watch", daemon=True).start()

    # ------------------------------------------------------------ вызов
    def call(self, method: str, params: dict) -> dict:
        fn = self.methods.get(method)
        if fn is None:
            return {"ok": False, "error": "UnknownMethod", "message": "Unknown method: %s" % method}
        try:
            result = fn(params if isinstance(params, dict) else {})
            return {"ok": True, "result": result if result is not None else {}}
        except RpcError as e:
            return {"ok": False, "error": e.code or "RpcError", "message": e.message}
        except Exception as e:       # noqa: BLE001
            _log.exception("RPC %s", method)
            return {"ok": False, "error": type(e).__name__, "message": "That didn't work.", "detail": str(e)}

    def stop(self) -> None:
        self._stop.set()
        self.meta.save()
        self.settings.save()

    # ------------------------------------------------------------ библиотека
    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            self.rescan()
            self._scanned.set()
            self._stop.wait(120)

    def rescan(self) -> None:
        with self._scan_lock:
            found = {f.id: f for f in sources.scan_all()}
            for g in self.local.data.get("games", []):
                exe = g.get("exe", "")
                found[g["id"]] = sources.Found(
                    id=g["id"], title=g.get("title") or os.path.splitext(os.path.basename(exe))[0], source="local",
                    badge="", launch={"exe": exe, "cwd": os.path.dirname(exe), "args": g.get("args", "")},
                    install_dir=os.path.dirname(exe), installed=os.path.isfile(exe), detail={})
            for g in self._scan_ps5():
                found[g.id] = g
            changed = set(found) != set(self._found)
            now = time.time()
            for fid in found:
                if fid not in self.meta.data:
                    self.meta.data[fid] = {"added": now if self._found else None}
            self._found = found
        if changed:
            self.emit("lib.changed", {})

    def _m(self, fid: str) -> dict:
        return self.meta.data.setdefault(fid, {})

    def _view(self, f: sources.Found) -> dict:
        m = self._m(f.id)
        origin = f.source if f.source not in ("local",) else ("epic" if f.detail.get("epic") else
                  "gog" if f.detail.get("archive") == "GOG" else "own-windows")
        last = m.get("last_played") or f.last_played
        return {
            "id": f.id, "title": m.get("title") or f.title, "kind": "game", "source": f.source, "badge": f.badge,
            "sort_title": (m.get("title") or f.title).casefold(), "art": dict(f.art), "available": f.installed,
            "installed": f.installed, "tags": [], "last_played": last, "added": m.get("added"),
            "playtime": int(m.get("playtime", 0)), "hidden": bool(m.get("hidden")), "favorite": bool(m.get("favorite")),
            "options": {**OPTION_DEFAULTS, **m.get("options", {})}, "running": f.id in self._running, "origin": origin,
        }

    def _apps(self) -> list[dict]:
        base = {"kind": "app", "source": "app", "badge": "", "art": {}, "available": True, "installed": True,
                "tags": [], "last_played": None, "added": None, "playtime": 0, "hidden": False, "favorite": False,
                "options": dict(OPTION_DEFAULTS), "origin": "app", "running": False}
        return [
            {**base, "id": "app:browser", "title": "Browser", "sort_title": "browser", "icon": "globe"},
            {**base, "id": "app:explorer", "title": "File Explorer", "sort_title": "file explorer", "icon": "folder"},
            {**base, "id": "app:desktop", "title": "Windows Desktop", "sort_title": "windows desktop", "icon": "desktop"},
        ]

    def _entry(self, fid: str) -> dict:
        for a in self._apps():
            if a["id"] == fid:
                return a
        f = self._found.get(fid)
        if f is None:
            raise RpcError("This entry no longer exists.", "NotFound")
        return self._view(f)

    def lib_list(self, p: dict) -> dict:
        self._scanned.wait(8.0)
        kind, flt = p.get("kind"), p.get("filter") or "all"
        query = str(p.get("query") or "").casefold().strip()
        if kind == "app":
            return {"entries": self._apps()}
        items = [self._view(f) for f in list(self._found.values())]
        items = [e for e in items if e["hidden"]] if flt == "hidden" else [e for e in items if not e["hidden"]]
        if flt == "installed":
            items = [e for e in items if e["installed"]]
        elif flt == "favorites":
            items = [e for e in items if e["favorite"]]
        if query:
            items = [e for e in items if query in e["title"].casefold()]
        sort = p.get("sort") or ("recent" if flt == "recent" else "title")
        if sort == "recent":
            items.sort(key=lambda e: (not e["running"], -max(e["last_played"] or 0, e["added"] or 0),
                                      -(e["last_played"] or 0)))
        elif sort == "playtime":
            items.sort(key=lambda e: -e["playtime"])
        elif sort == "added":
            items.sort(key=lambda e: -(e["added"] or 0))
        else:
            items.sort(key=lambda e: e["sort_title"])
        limit = p.get("limit")
        if limit is None and flt == "recent":
            limit = 12
        if isinstance(limit, (int, float)) and limit > 0:
            items = items[:int(limit)]
        return {"entries": items}

    def lib_get(self, p: dict) -> dict:
        e = self._entry(str(p.get("id")))
        run = e["running"]
        actions = [{"id": "focus" if run else "launch", "label": "Resume" if run else "Play"}]
        if run:
            actions.append({"id": "close", "label": "Close"})
        if e["kind"] == "game":
            actions.append({"id": "unfavorite" if e["favorite"] else "favorite",
                            "label": "Remove from favorites" if e["favorite"] else "Add to favorites"})
            actions.append({"id": "rename", "label": "Rename"})
            actions.append({"id": "unhide" if e["hidden"] else "hide", "label": "Show" if e["hidden"] else "Hide"})
        info = [{"label": "Source", "value": e["badge"] or e["source"]}]
        f = self._found.get(e["id"])
        if f and f.detail.get("ps5"):
            actions.append({"id": "forget", "label": "Remove from list"})
            if f.detail.get("title_id"):
                info.append({"label": "Title ID", "value": f.detail["title_id"]})
            if f.detail.get("version"):
                info.append({"label": "Version", "value": f.detail["version"]})
        if f and f.install_dir:
            info.append({"label": "Folder", "value": f.install_dir})
        return {"entry": e, "info": info, "actions": actions, "notes": [], "note": "", "week": 0, "pointer": False}

    def _need(self, p: dict) -> str:
        fid = str(p.get("id"))
        if fid not in self._found:
            raise RpcError("This entry no longer exists.", "NotFound")
        return fid

    def lib_hide(self, p: dict) -> dict:
        self._m(self._need(p))["hidden"] = bool(p.get("hidden", True))
        self.meta.save()
        self.emit("lib.changed", {})
        return {}

    def lib_rename(self, p: dict) -> dict:
        fid = self._need(p)
        t = p.get("title")
        self._m(fid)["title"] = t.strip() if isinstance(t, str) and t.strip() else None
        self.meta.save()
        return {"entry": self._entry(fid)}

    def lib_favorite(self, p: dict) -> dict:
        fid = self._need(p)
        self._m(fid)["favorite"] = bool(p.get("favorite", True))
        self.meta.save()
        return {"entry": self._entry(fid)}

    def lib_set_option(self, p: dict) -> dict:
        fid = self._need(p)
        key = str(p.get("key"))
        if key not in OPTION_DEFAULTS:
            raise RpcError("This setting does not exist.", "BadOption")
        self._m(fid).setdefault("options", {})[key] = p.get("value")
        self.meta.save()
        return {"entry": self._entry(fid)}

    def lib_forget(self, p: dict) -> dict:
        fid = str(p.get("id"))
        f = self._found.get(fid)
        if f is not None and f.detail.get("ps5"):
            cfg = self._ps5_cfg()
            game = f.detail.get("game", "")
            cfg["games"] = [g for g in cfg["games"] if os.path.normcase(g) != os.path.normcase(game)]
            key = f.detail.get("key", "")
            if key and key not in cfg["ignored"]:
                cfg["ignored"].append(key)           # найденная в папке-библиотеке игра не вернётся при пересканировании
            self.settings.save()
            self.rescan()
            return {"ok": True, "message": "Removed from the list."}
        games = self.local.data.get("games", [])
        self.local.data["games"] = [g for g in games if g["id"] != fid]
        self.local.save()
        self.rescan()
        return {"ok": True, "message": "Removed from the list."}

    # ------------------------------------------------------------ запуск и процессы
    def _ps5_procs(self, f: sources.Found) -> list:
        """Процессы kyty_emulator.exe, запущенные именно с этой игрой (--game <путь>)."""
        game = os.path.normcase(os.path.normpath(f.detail.get("game", "")))
        out = []
        for pr in psutil.process_iter(["exe"]):
            try:
                exe = pr.info.get("exe") or ""
                if os.path.basename(exe).lower() != ps5.EMULATOR_EXE:
                    continue
                args = [os.path.normcase(os.path.normpath(a)) for a in pr.cmdline()[1:]]
                if game in args:
                    out.append(pr)
            except (psutil.Error, OSError):
                continue
        return out

    def _procs_of(self, f: sources.Found) -> list:
        if psutil is None or not f.install_dir:
            return []
        if f.detail.get("ps5"):
            return self._ps5_procs(f)
        base = os.path.normcase(os.path.normpath(f.install_dir)) + os.sep
        out = []
        for pr in psutil.process_iter(["pid", "exe"]):
            try:
                exe = pr.info.get("exe") or ""
                if exe and os.path.normcase(exe).startswith(base):
                    out.append(pr)
            except (psutil.Error, OSError):
                continue
        return out

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(POLL_S)
            if psutil is None:
                continue
            try:
                now: dict[str, dict] = {}
                snapshot = [(p.info.get("exe") or "") for p in psutil.process_iter(["exe"])]
                exes = [os.path.normcase(x) for x in snapshot if x]
                emu_running = any(os.path.basename(x) == ps5.EMULATOR_EXE for x in exes)
                for fid, f in list(self._found.items()):
                    if not f.install_dir:
                        continue
                    if f.detail.get("ps5"):
                        if emu_running and self._ps5_procs(f):
                            now[fid] = {"entryId": fid, "title": self._m(fid).get("title") or f.title, "closing": False}
                        continue
                    base = os.path.normcase(os.path.normpath(f.install_dir)) + os.sep
                    if any(x.startswith(base) for x in exes):
                        now[fid] = {"entryId": fid, "title": self._m(fid).get("title") or f.title, "closing": False}
                for fid in now:
                    m = self._m(fid)
                    m["playtime"] = int(m.get("playtime", 0)) + int(POLL_S)
                    m["last_played"] = time.time()
                if set(now) != set(self._running):
                    self._running = now
                    self._push_running()
                    self.meta.save()
                elif now:
                    self._running = now
            except Exception:       # noqa: BLE001
                _log.exception("watch")

    def _push_running(self) -> None:
        running = list(self._running.values())
        self.emit("app.changed", {"running": running})
        self.emit("state", {"running": running})
        self.emit("lib.changed", {})

    def _mark_started(self, fid: str, title: str) -> None:
        m = self._m(fid)
        m["last_played"] = time.time()
        self._running[fid] = {"entryId": fid, "title": title, "closing": False}
        self._foreground = fid
        self.emit("state", {"running": list(self._running.values()), "foreground": fid})
        self.emit("app.changed", {"running": list(self._running.values())})

        def release() -> None:
            time.sleep(1.5)
            if self._foreground == fid:
                self._foreground = None
                self.emit("state", {"foreground": None})
        threading.Thread(target=release, daemon=True).start()
        self.meta.save()

    def app_launch(self, p: dict) -> dict:
        fid = str(p.get("id"))
        if fid.startswith("app:"):
            return self._launch_app(fid)
        f = self._found.get(fid)
        if f is None:
            raise RpcError("This entry no longer exists.", "LaunchError")
        title = self._m(fid).get("title") or f.title
        self.emit("app.launching", {"id": fid, "phase": "start"})
        try:
            spec = f.launch
            if spec.get("ps5"):
                self._launch_ps5(f, fid)
            elif "uri" in spec:
                os.startfile(spec["uri"])                            # type: ignore[attr-defined]
            else:
                exe = spec["exe"]
                if not os.path.isfile(exe):
                    raise RpcError("The program file is missing: %s" % exe, "LaunchError")
                args = spec.get("args") or ""
                cmd = '"%s" %s' % (exe, args) if args else [exe]
                subprocess.Popen(cmd, cwd=spec.get("cwd") or os.path.dirname(exe), close_fds=True)
        except RpcError:
            raise
        except OSError as e:
            self.emit("app.error", {"id": fid, "title": title, "message": "Could not start: %s" % e, "phase": "start"})
            raise RpcError("Could not start: %s" % e, "LaunchError")
        self._mark_started(fid, title)
        return {"ok": True}

    def _launch_ps5(self, f: sources.Found, fid: str) -> None:
        cfg = self._ps5_cfg()
        emu = ps5.find_emulator(cfg["emulator"])
        if not emu:
            raise RpcError("The KytyPS5 emulator was not found. Open Settings > PS5 games > Choose emulator folder "
                           "and pick the folder with kyty_emulator.exe.", "LaunchError")
        game = f.detail.get("game", "")
        if not ps5.is_game_path(game):
            raise RpcError("The game files are missing: %s" % game, "LaunchError")
        extra = str(self._m(fid).get("options", {}).get("args") or "")
        cmd = ps5.build_command(emu, game, cfg, extra)
        log_path = os.path.join(paths.data_dir(), "kyty-last-run.log")
        _log.info("PS5 start: %s", cmd)
        with open(log_path, "wb") as logf:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(cmd, cwd=os.path.dirname(emu), env=ps5.build_env(emu), close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT, creationflags=flags)

    def _launch_app(self, fid: str) -> dict:
        try:
            if fid == "app:browser":
                os.startfile("https://www.google.com")               # type: ignore[attr-defined]
            elif fid == "app:explorer":
                os.startfile(os.path.expanduser("~"))                # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer.exe", "shell:Desktop"])
        except OSError as e:
            raise RpcError("Could not start: %s" % e, "LaunchError")
        return {"ok": True}

    def app_focus(self, p: dict) -> dict:
        f = self._found.get(str(p.get("id")))
        if f is None:
            return {"ok": False}
        pids = {pr.pid for pr in self._procs_of(f)}
        if not pids:
            return {"ok": False}
        user32 = ctypes.windll.user32                                # type: ignore[attr-defined]
        hit: list[int] = []
        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def each(hwnd, _lp):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                hit.append(hwnd)
            return True
        user32.EnumWindows(proto(each), 0)
        if hit:
            user32.ShowWindow(hit[0], 9)                              # SW_RESTORE
            user32.SetForegroundWindow(hit[0])
        return {"ok": bool(hit)}

    def app_close(self, p: dict) -> dict:
        f = self._found.get(str(p.get("id")))
        if f is None:
            return {"ok": False}
        for pr in self._procs_of(f):
            subprocess.run(["taskkill", "/PID", str(pr.pid)], capture_output=True)      # вежливо (WM_CLOSE)

        def hard() -> None:
            time.sleep(6)
            for pr in self._procs_of(f):
                try:
                    pr.kill()
                except Exception:      # noqa: BLE001
                    pass
        threading.Thread(target=hard, daemon=True).start()
        return {"ok": True}

    def app_kill(self, p: dict) -> dict:
        f = self._found.get(str(p.get("id")))
        for pr in (self._procs_of(f) if f else []):
            try:
                pr.kill()
            except Exception:          # noqa: BLE001
                pass
        return {"ok": True}

    # ------------------------------------------------------------ файлы: «Запустить .exe», «Добавить в библиотеку»
    def files_list(self, p: dict) -> dict:
        path = str(p.get("path") or os.path.expanduser("~"))
        if not os.path.isdir(path):
            raise RpcError("This folder does not exist.", "NotFound")
        items = []
        try:
            with os.scandir(path) as it:
                for de in it:
                    try:
                        isdir = de.is_dir()
                        items.append({"name": de.name, "path": de.path, "dir": isdir, "type": "dir" if isdir else "file",
                                      "size": 0 if isdir else de.stat().st_size, "hidden": de.name.startswith(".")})
                    except OSError:
                        continue
        except OSError as e:
            raise RpcError("Cannot open the folder: %s" % e, "IOError")
        items.sort(key=lambda i: (not i["dir"], i["name"].casefold()))
        parent = os.path.dirname(path.rstrip("\\/"))
        return {"path": path, "parent": parent if parent and parent != path else "", "entries": items, "items": items}

    def files_add(self, p: dict) -> dict:
        path = str(p.get("path") or "")
        if os.path.basename(path).lower() == "eboot.bin":
            path = os.path.dirname(path)
        if ps5.is_game_path(path):
            return self.ps5_add({"path": path})
        if not path.lower().endswith((".exe", ".bat", ".lnk")) or not os.path.isfile(path):
            raise RpcError("Pick an .exe file.", "BadValue")
        fid = "local:" + uuid.uuid5(uuid.NAMESPACE_URL, os.path.normcase(path)).hex[:16]
        if not any(g["id"] == fid for g in self.local.data["games"]):
            self.local.data["games"].append({"id": fid, "exe": path,
                                              "title": os.path.splitext(os.path.basename(path))[0]})
            self.local.save()
        self.rescan()
        return {"ok": True, "id": fid, "message": "Added to the library."}

    def files_run(self, p: dict) -> dict:
        path = str(p.get("path") or "")
        if not os.path.isfile(path):
            raise RpcError("The file does not exist.", "NotFound")
        os.startfile(path)                                           # type: ignore[attr-defined]
        return {"ok": True}

    # ------------------------------------------------------------ PS5 (KytyPS5)
    def _ps5_cfg(self) -> dict:
        cfg = self.settings.data.setdefault("ps5", {})
        for k, v in PS5_DEFAULTS.items():
            if k not in cfg or not isinstance(cfg[k], type(v)):
                cfg[k] = json.loads(json.dumps(v))
        return cfg

    def _art_url(self, key: str, path: str) -> str:
        if not path:
            return ""
        self._ps5_art[key] = path
        try:
            ver = int(os.path.getmtime(path))
        except OSError:
            ver = 0
        return "/ps5art/%s?v=%d" % (key, ver)

    def art_file(self, key: str) -> str | None:
        """Файл картинки по ключу (для сервера). Произвольные пути отдавать нельзя — только зарегистрированные."""
        return self._ps5_art.get(key)

    def _scan_ps5(self) -> list[sources.Found]:
        cfg = self._ps5_cfg()
        paths_: list[str] = []
        seen: set[str] = set()
        for g in cfg["games"]:
            if isinstance(g, str) and ps5.is_game_path(g):
                paths_.append(g)
        for folder in cfg["folders"]:
            if isinstance(folder, str) and os.path.isdir(folder):
                paths_.extend(ps5.find_games(folder))
        out: list[sources.Found] = []
        emu_ok = bool(ps5.find_emulator(cfg["emulator"]))
        for g in paths_:
            info = ps5.read_game(g)
            if info is None or info["key"] in seen or info["key"] in cfg["ignored"]:
                continue
            seen.add(info["key"])
            key = info["key"]
            art = {}
            tile = self._art_url(key + "-tile", info["icon"])
            hero = self._art_url(key + "-hero", info["hero"])
            if tile:
                art["tile"] = tile
            if hero:
                art["hero"] = hero
            out.append(sources.Found(
                id="ps5:" + key, title=info["title"], source="rom", badge="PS5",
                launch={"ps5": True, "game": info["path"]}, install_dir=info["path"], art=art, installed=emu_ok,
                detail={"ps5": True, "game": info["path"], "key": key, "title_id": info["title_id"],
                        "version": info["version"], "system": "ps5"}))
        return out

    def ps5_add(self, p: dict) -> dict:
        """Добавить игру (папка с eboot.bin или .zar) либо папку-библиотеку с играми."""
        path = os.path.normpath(str(p.get("path") or ""))
        if os.path.basename(path).lower() == "eboot.bin":
            path = os.path.dirname(path)
        if not os.path.exists(path):
            raise RpcError("This folder does not exist.", "NotFound")
        cfg = self._ps5_cfg()
        key_of = ps5.game_key
        if ps5.is_game_path(path):
            if not any(os.path.normcase(g) == os.path.normcase(path) for g in cfg["games"]):
                cfg["games"].append(path)
            if key_of(path) in cfg["ignored"]:
                cfg["ignored"].remove(key_of(path))
            info = ps5.read_game(path) or {}
            self.settings.save()
            self.rescan()
            return {"ok": True, "id": "ps5:" + key_of(path), "message": "Added: %s" % (info.get("title") or path)}
        found = ps5.find_games(path) if os.path.isdir(path) else []
        if not found:
            raise RpcError("No PS5 game found here. Open the game's own folder (the one with eboot.bin) "
                           "or a folder that contains such game folders.", "BadValue")
        if not any(os.path.normcase(f) == os.path.normcase(path) for f in cfg["folders"]):
            cfg["folders"].append(path)
        cfg["ignored"] = [k for k in cfg["ignored"] if k not in {key_of(g) for g in found}]
        self.settings.save()
        self.rescan()
        return {"ok": True, "message": "Added games: %d" % len(found)}

    def ps5_set_emulator(self, p: dict) -> dict:
        path = str(p.get("path") or "")
        exe = ps5.find_emulator(path)
        if not exe:
            raise RpcError("kyty_emulator.exe was not found in this folder.", "BadValue")
        cfg = self._ps5_cfg()
        cfg["emulator"] = os.path.dirname(exe)
        self.settings.save()
        self.rescan()
        return {"ok": True, "message": "Emulator: %s" % exe}

    def ps5_open_kyty(self) -> dict:
        exe = ps5.find_emulator(self._ps5_cfg()["emulator"])
        launcher = ps5.kyty_launcher(exe)
        if not launcher:
            return {"error": "NotFound", "message": "launcher.exe was not found next to the emulator."}
        subprocess.Popen([launcher], cwd=os.path.dirname(launcher), env=ps5.build_env(exe), close_fds=True)
        return {"message": "KytyPS5 launcher opened."}

    # ------------------------------------------------------------ настройки
    def ui_state(self) -> dict:
        ui = {**UI_DEFAULTS, **self.settings.data.get("ui", {})}
        ui["themeCss"] = themes.css_for(ui.get("theme", ""))
        return ui

    def sys_state(self, p: dict) -> dict:
        return {
            "role": p.get("_role", "home"), "version": VERSION, "time": time.time(), "foreground": self._foreground,
            "overlay": "hidden", "running": list(self._running.values()), "pads": self.pads, "volume":
            {"level": 50, "muted": False, "sink": ""}, "network": {"state": "online", "type": "", "name": ""},
            "hdr": {"supported": False, "enabled": False, "active": False}, "notifications": 0, "jobs": [],
            "art_job": None, "update_available": False, "mouseMode": False, "visible": True, "saver": False,
            "ui": self.ui_state(), "setup": {"due": False},
        }

    pads: list = []

    def _push_ui(self) -> None:
        self.emit("state", {"ui": self.ui_state()})

    def settings_categories(self, p: dict) -> dict:
        return {"categories": [{"id": "home", "label": "Home screen", "icon": "home"},
                               {"id": "ps5", "label": "PS5 games", "icon": "emulator"},
                               {"id": "system", "label": "System", "icon": "system"}]}

    def settings_page(self, p: dict) -> dict:
        pid = str(p.get("id") or "")
        ui = {**UI_DEFAULTS, **self.settings.data.get("ui", {})}

        def row(rid, typ, label, **kw):
            base = {"id": rid, "type": typ, "label": label, "value": None, "options": None, "min": None, "max": None,
                    "step": None, "unit": "", "enabled": True, "hint": "", "note": "", "page": None, "confirm": None,
                    "trial": None, "secret": False, "learn": True, "default": None}
            base.update(kw)
            return base
        if pid == "home":
            opts = [{"value": "", "label": "Default"}] + [{"value": k, "label": THEME_NAMES.get(k, v[0])}
                                                            for k, v in themes.BUILTIN.items()]
            opts += [{"value": themes.FILE_PREFIX + n, "label": n} for n in themes.user_themes()]
            return {"id": "home", "title": "Home screen", "rows": [
                row("home.theme", "select", "Theme", value=ui["theme"], options=opts, default=""),
                row("home.sounds", "toggle", "Interface sounds", value=bool(ui["sounds"]), default=True),
                row("home.volume", "slider", "Sound volume", value=ui["soundVolume"], min=0, max=100, step=5, unit="%",
                    default=60),
                row("home.reduceMotion", "toggle", "Reduce motion", value=bool(ui["reduceMotion"]), default=False),
                row("home.clock24h", "toggle", "24-hour clock", value=bool(ui["clock24h"]), default=True),
                row("home.liveBackground", "toggle", "Animated background", value=bool(ui["liveBackground"]),
                    default=True),
                row("home.scale", "select", "Interface size", value=ui["scale"], default=1,
                    options=[{"value": 1, "label": "Normal"}, {"value": 1.1, "label": "Large"},
                             {"value": 1.2, "label": "Larger"}]),
            ]}
        if pid == "ps5":
            cfg = self._ps5_cfg()
            exe = ps5.find_emulator(cfg["emulator"])
            n = sum(1 for f in self._found.values() if f.detail.get("ps5"))
            on_off = lambda k: bool(cfg.get(k))     # noqa: E731
            return {"id": "ps5", "title": "PS5 games", "rows": [
                row("ps5.s1", "section", "Emulator (KytyPS5)"),
                row("ps5.emu", "info", "kyty_emulator.exe", value=exe or "Not found"),
                row("ps5.pickEmu", "action", "Choose emulator folder",
                    hint="The folder that contains kyty_emulator.exe and u59-preset.json."),
                row("ps5.openKyty", "action", "Open the KytyPS5 launcher",
                    hint="Controller, keyboard and graphics settings of the emulator.", enabled=bool(ps5.kyty_launcher(exe))),
                row("ps5.s2", "section", "Games"),
                row("ps5.count", "info", "Games in the library", value=str(n)),
                row("ps5.add", "action", "Add a game or a folder with games",
                    hint="Open the game folder (the one with eboot.bin), e.g. Astro's Playroom, and choose it with the triangle button."),
                row("ps5.rescan", "action", "Rescan PS5 games"),
                row("ps5.s3", "section", "Launch options"),
                row("ps5.fullscreen", "toggle", "Fullscreen", value=on_off("fullscreen"), default=False),
                row("ps5.amd_cpu", "toggle", "AMD CPU patch", value=on_off("amd_cpu"), default=False,
                    hint="Turn on for AMD processors (--amd-cpu)."),
                row("ps5.hide_cursor", "toggle", "Hide the cursor while playing", value=on_off("hide_cursor"), default=True),
                row("ps5.present_mode", "select", "Present mode", value=cfg["present_mode"], default="",
                    options=[{"value": "", "label": "Default (Mailbox)"}, {"value": "Mailbox", "label": "Mailbox"},
                             {"value": "Fifo", "label": "Fifo (V-Sync)"}, {"value": "Immediate", "label": "Immediate"}],
                    hint="Fifo can help if the game freezes on an RTX 50 card."),
                row("ps5.vblank", "select", "Frame rate (vblank)", value=cfg["vblank"], default="",
                    options=[{"value": "", "label": "Default (60)"}, {"value": "30", "label": "30"}]),
            ]}
        if pid == "system":
            lang = self.settings.data.get("lang", "en")
            return {"id": "system", "title": "System", "rows": [
                row("system.lang", "select", "Language", value=lang, default="en", hint="The page reloads after you change it.",
                    options=[{"value": "en", "label": "English"}, {"value": "de", "label": "Deutsch"}]),
                row("system.version", "info", "Version", value="%s (Windows)" % VERSION),
                row("system.data", "action", "Open data folder", hint=paths.data_dir()),
                row("system.rescan", "action", "Rescan games"),
                row("system.themes", "action", "Open themes folder", hint="Put your own .css themes here."),
            ]}
        return {"id": pid, "title": "Settings", "rows": [], "error": "This page does not exist."}

    def settings_set(self, p: dict) -> dict:
        rid, value = str(p.get("id")), p.get("value")
        ui = self.settings.data.setdefault("ui", {})
        mapping = {"home.theme": "theme", "home.sounds": "sounds", "home.volume": "soundVolume",
                   "home.reduceMotion": "reduceMotion", "home.clock24h": "clock24h",
                   "home.liveBackground": "liveBackground", "home.scale": "scale"}
        if rid in mapping:
            if rid == "home.theme" and not themes.valid(str(value or "")):
                raise RpcError("This theme does not exist.", "BadValue")
            ui[mapping[rid]] = value
            self.settings.save()
            self._push_ui()
            return {}
        if rid.startswith("ps5."):
            cfg = self._ps5_cfg()
            key = rid[4:]
            if key in ("fullscreen", "amd_cpu", "hide_cursor"):
                cfg[key] = bool(value)
            elif key == "present_mode" and value in ("", "Mailbox", "Fifo", "Immediate"):
                cfg[key] = value
            elif key == "vblank" and value in ("", "30"):
                cfg[key] = value
            else:
                raise RpcError("This setting does not exist.", "BadOption")
            self.settings.save()
            return {}
        if rid == "system.lang":
            if value not in ("en", "de"):
                raise RpcError("Unsupported language.", "BadValue")
            self.settings.data["lang"] = value
            self.settings.save()
            threading.Timer(0.4, self.reload_page).start()
            return {}
        raise RpcError("This setting does not exist.", "BadOption")

    def settings_action(self, p: dict) -> dict:
        rid = str(p.get("id"))
        if rid == "system.data":
            os.startfile(paths.data_dir())                            # type: ignore[attr-defined]
        elif rid == "system.themes":
            d = themes.user_dir()
            os.makedirs(d, exist_ok=True)
            os.startfile(d)                                          # type: ignore[attr-defined]
        elif rid == "ps5.pickEmu":
            return {"ui": "explorer", "params": {"start": None, "pick": {
                "label": "Choose this folder", "method": "ps5.setEmulator", "args": {}}}}
        elif rid == "ps5.add":
            return {"ui": "explorer", "params": {"start": None, "pick": {
                "label": "Add this folder", "method": "ps5.add", "args": {}}}}
        elif rid == "ps5.openKyty":
            return self.ps5_open_kyty()
        elif rid == "ps5.rescan":
            threading.Thread(target=self.rescan, daemon=True).start()
            return {"message": "Scanning…", "refresh": True}
        elif rid == "system.rescan":
            threading.Thread(target=self.rescan, daemon=True).start()
            return {"message": "Scanning…"}
        return {}

    def settings_reset(self, p: dict) -> dict:
        rid = str(p.get("id"))
        defaults = {"home.theme": "", "home.sounds": True, "home.volume": 60, "home.reduceMotion": False,
                    "home.clock24h": True, "home.liveBackground": True, "home.scale": 1}
        if rid in defaults:
            return self.settings_set({"id": rid, "value": defaults[rid]})
        return {}

    # ------------------------------------------------------------ питание
    def power_menu(self, p: dict) -> dict:
        items = [
            {"id": "suspend", "label": "Sleep", "enabled": True, "note": "", "endsApps": False, "confirm": None},
            {"id": "off", "label": "Shut down", "enabled": True, "note": "", "endsApps": True, "confirm": None},
            {"id": "reboot", "label": "Restart", "enabled": True, "note": "", "endsApps": True, "confirm": None},
            {"id": "desktop", "label": "Quit PC5 Launcher", "enabled": True, "note": "Back to the Windows desktop",
             "endsApps": False, "confirm": None},
        ]
        return {"items": items, "running": len(self._running), "jobs": 0, "copying": 0, "busy": []}

    def power_do(self, p: dict) -> dict:
        act = str(p.get("id") or p.get("action") or "")
        if act == "desktop":
            threading.Timer(0.3, self.quit_app).start()
        elif act == "off":
            subprocess.Popen(["shutdown", "/s", "/t", "3"])
        elif act == "reboot":
            subprocess.Popen(["shutdown", "/r", "/t", "3"])
        elif act == "suspend":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        else:
            raise RpcError("Unknown action.", "BadValue")
        return {"ok": True}

    # ------------------------------------------------------------ регистрация
    def _register(self) -> None:
        r = self.methods
        noop = lambda p: {}      # noqa: E731
        r.update({
            "sys.state": self.sys_state, "sys.log": lambda p: _log.info("[web] %s", p.get("text", "")) or {},
            "sys.firstFrame": noop, "sys.beat": noop,
            "lib.list": self.lib_list, "lib.get": self.lib_get, "lib.hide": self.lib_hide, "lib.rename": self.lib_rename,
            "lib.setFavorite": self.lib_favorite, "lib.setOption": self.lib_set_option, "lib.forget": self.lib_forget,
            "lib.rescan": lambda p: (self.rescan() or {}), "lib.exeCandidates": lambda p: {"candidates": []},
            "lib.fetchArt": lambda p: {"ok": False, "message": "Covers load automatically for Steam games."},
            "home.order": lambda p: {"games": list(self.order.data.get("games", []))},
            "home.setOrder": self._set_order,
            "ps5.add": self.ps5_add, "ps5.setEmulator": self.ps5_set_emulator,
            "app.launch": self.app_launch, "app.focus": self.app_focus, "app.close": self.app_close,
            "app.kill": self.app_kill, "app.home": noop, "overlay.close": noop,
            "files.list": self.files_list, "files.add": self.files_add, "files.run": self.files_run,
            "files.play": self.files_run,
            "settings.categories": self.settings_categories, "settings.page": self.settings_page,
            "settings.set": self.settings_set, "settings.action": self.settings_action,
            "settings.reset": self.settings_reset,
            "power.menu": self.power_menu, "power.do": self.power_do, "power.timer": noop,
            "notify.list": lambda p: {"items": []}, "notify.clear": noop,
            "audio.get": lambda p: {"level": 50, "muted": False, "sink": "", "sinks": []},
            "perf.get": lambda p: {}, "install.jobs": lambda p: {"jobs": []},
            "install.trashList": lambda p: {"items": []}, "files.trashList": lambda p: {"items": []},
            "setup.later": noop, "setup.finish": noop,
        })

    def _set_order(self, p: dict) -> dict:
        ids = [i for i in (p.get("games") or []) if isinstance(i, str)][:5000]
        self.order.data["games"] = ids
        self.order.save()
        return {"ok": True}

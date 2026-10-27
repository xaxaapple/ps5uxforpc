"""Проверка без окна: сервер, защита токеном, RPC, парсинг Steam. Запуск: python windows/tests/smoke_test.py"""
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("APPDATA", tempfile.mkdtemp())

from pc5win import ps5, sources                 # noqa: E402
from pc5win.backend import Backend              # noqa: E402
from pc5win.server import EventBus, Server     # noqa: E402


def main() -> int:
    root = tempfile.mkdtemp()
    os.makedirs(root + "/steamapps/common/Game A")
    with open(root + "/steamapps/libraryfolders.vdf", "w") as f:
        f.write('"libraryfolders"{"0"{"path" "%s"}}' % root.replace("\\", "\\\\"))
    with open(root + "/steamapps/appmanifest_123.acf", "w") as f:
        f.write('"AppState"{"appid" "123" "name" "Game A" "StateFlags" "4" "installdir" "Game A" "LastPlayed" "1700000000"}')
    sources.steam_root = lambda: root
    found = sources.scan_steam()
    assert [g.id for g in found] == ["steam:123"], found

    bus = EventBus()
    backend = Backend(bus.emit, lambda: None, lambda: None)
    server = Server(backend.call, bus, lambda: server.query_string("en"), backend.art_file)
    server.start()
    base = "http://127.0.0.1:%d" % server.port

    def rpc(method, params=None, token=True):
        headers = {"X-PC5-Token": server.token} if token else {}
        req = urllib.request.Request(base + "/rpc", json.dumps({"method": method, "params": params or {}}).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return {"http": e.code}

    assert rpc("sys.state", token=False) == {"http": 403}
    assert rpc("sys.state")["ok"]
    got = rpc("lib.list", {"kind": "game", "filter": "recent"})["result"]["entries"]
    assert [e["id"] for e in got] == ["steam:123"] and got[0]["art"]["cover"].startswith("https://"), got
    assert rpc("lib.get", {"id": "steam:123"})["result"]["actions"][0]["id"] == "launch"
    assert rpc("settings.set", {"id": "home.theme", "value": "ocean"})["ok"]
    assert rpc("settings.set", {"id": "home.theme", "value": "bogus"})["error"] == "BadValue"
    assert rpc("nope")["error"] == "UnknownMethod"

    # ---- PS5 (KytyPS5): добавление игры, название/иконка/фон из sce_sys, запуск без оболочки
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6300010000050001"
                        "0d0a2db40000000049454e44ae426082")
    lib = tempfile.mkdtemp()
    game = os.path.join(lib, "Games", "Astro's Playroom PPSA01325")
    os.makedirs(os.path.join(game, "sce_sys"))
    open(os.path.join(game, "eboot.bin"), "wb").write(b"\x7fELF")
    with open(os.path.join(game, "sce_sys", "param.json"), "w", encoding="utf-8-sig") as f:
        json.dump({"titleId": "PPSA01325", "contentVersion": "01.904.000",
                   "localizedParameters": {"defaultLanguage": "en-US", "en-US": {"titleName": "ASTRO's PLAYROOM"}}}, f)
    for n in ("icon0.png", "pic0.png"):
        open(os.path.join(game, "sce_sys", n), "wb").write(png)
    emu = os.path.join(lib, "KytyPS5")
    os.makedirs(emu)
    open(os.path.join(emu, "kyty_emulator.exe"), "wb").write(b"MZ")
    with open(os.path.join(emu, "u59-preset.json"), "w") as f:
        json.dump({"KYTY_DRAW_RUN": "1", "KYTY_VERTEX_FAILURE_DUMP": ""}, f)
    assert rpc("ps5.setEmulator", {"path": emu})["ok"]
    assert rpc("ps5.add", {"path": os.path.join(lib, "nothing")})["error"] == "NotFound"
    assert rpc("ps5.add", {"path": emu})["error"] == "BadValue"            # папка без игр
    added = rpc("ps5.add", {"path": os.path.join(lib, "Games")})["result"]      # папка-библиотека
    assert "1" in added["message"], added
    games = [e for e in rpc("lib.list", {"kind": "game"})["result"]["entries"] if e["badge"] == "PS5"]
    assert len(games) == 1 and games[0]["title"] == "ASTRO's PLAYROOM" and games[0]["available"], games
    assert games[0]["art"]["tile"].startswith("/ps5art/") and games[0]["art"]["hero"].startswith("/ps5art/"), games
    for kind in ("tile", "hero"):
        r = urllib.request.urlopen(base + games[0]["art"][kind], timeout=10)
        assert r.status == 200 and r.headers["Content-Type"] == "image/png" and r.read() == png
    for bad in ("/ps5art/../../etc/passwd", "/ps5art/0000000000000000-tile", "/ps5art/x"):
        try:
            code = urllib.request.urlopen(base + bad, timeout=10).status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 404, (bad, code)
    info = rpc("lib.get", {"id": games[0]["id"]})["result"]
    assert any(i["value"] == "PPSA01325" for i in info["info"]) and any(a["id"] == "forget" for a in info["actions"])
    page = rpc("settings.page", {"id": "ps5"})["result"]
    assert any(r["id"] == "ps5.fullscreen" for r in page["rows"]) and any(r["id"] == "ps5.pickEmu" for r in page["rows"])
    assert rpc("settings.set", {"id": "ps5.fullscreen", "value": True})["ok"]
    assert rpc("settings.set", {"id": "ps5.present_mode", "value": "evil"})["error"] == "BadOption"
    act = rpc("settings.action", {"id": "ps5.add"})["result"]
    assert act["ui"] == "explorer" and act["params"]["pick"]["method"] == "ps5.add", act
    exe = ps5.find_emulator(emu)
    assert exe.endswith("kyty_emulator.exe")
    cmd = ps5.build_command(exe, game, {"fullscreen": True, "amd_cpu": True, "present_mode": "Fifo", "vblank": "30"},
                            '--user-name "Ivan K"')
    assert cmd[1:3] == ["--game", game] and "--fullscreen" in cmd and cmd[-2:] == ["--user-name", "Ivan K"], cmd
    assert cmd[cmd.index("--present-mode") + 1] == "Fifo" and cmd[cmd.index("--vblank-frequency") + 1] == "30"
    env = ps5.build_env(exe, {"PATH": "x", "KYTY_OLD": "1", "tracy_x": "1"})
    assert env == {"PATH": "x", "KYTY_DRAW_RUN": "1", "KYTY_VERTEX_FAILURE_DUMP": ""}, env
    assert rpc("lib.forget", {"id": games[0]["id"]})["ok"]
    assert not [e for e in rpc("lib.list", {"kind": "game"})["result"]["entries"] if e["badge"] == "PS5"]
    for path, want in (("/", 403), ("/js/main.js", 200), ("/../../etc/passwd", 404)):
        try:
            code = urllib.request.urlopen(base + path, timeout=10).status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == want, (path, code)
    backend.stop()
    server.stop()
    print("smoke test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

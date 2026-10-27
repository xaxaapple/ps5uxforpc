"""Источники игр под Windows: Steam, Epic Games, GOG Galaxy, свои .exe. Только чтение, без сети."""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import winreg  # type: ignore
except ImportError:  # запуск проверок не на Windows
    winreg = None  # type: ignore

STEAM_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/%s/%s"
STEAM_SKIP_IDS = {"228980", "1070560", "1391110", "1493710", "1628350", "2180100"}   # Redist, Runtimes, Proton
STEAM_SKIP_PREFIX = ("Steamworks", "Steam Linux Runtime", "Proton ", "Steam Controller Configs")


@dataclass
class Found:
    id: str
    title: str
    source: str                 # steam | epic | gog | local
    badge: str
    launch: dict                # {"uri": ...} | {"exe": ..., "cwd": ..., "args": ...}
    install_dir: str = ""
    art: dict = field(default_factory=dict)
    last_played: float | None = None
    installed: bool = True
    detail: dict = field(default_factory=dict)


def _reg(hive: Any, path: str, name: str, view: int = 0) -> str:
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return ""


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# ------------------------------------------------------------------ Steam
def steam_root() -> str:
    if winreg is None:
        return ""
    p = _reg(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")
    if not p:
        p = _reg(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")
    return os.path.normpath(p) if p and os.path.isdir(p) else ""


def steam_libraries(root: str) -> list[str]:
    libs = [root]
    text = _read(os.path.join(root, "steamapps", "libraryfolders.vdf"))
    for m in re.finditer(r'"path"\s+"([^"]+)"', text):
        p = os.path.normpath(m.group(1).replace("\\\\", "\\"))
        if os.path.isdir(p) and p not in libs:
            libs.append(p)
    return libs


def _vdf(text: str, key: str) -> str:
    m = re.search(r'"%s"\s+"([^"]*)"' % re.escape(key), text, re.I)
    return m.group(1) if m else ""


def scan_steam() -> list[Found]:
    root = steam_root()
    out: list[Found] = []
    if not root:
        return out
    for lib in steam_libraries(root):
        for mf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            text = _read(mf)
            appid, name = _vdf(text, "appid"), _vdf(text, "name")
            if not appid or not name or appid in STEAM_SKIP_IDS or name.startswith(STEAM_SKIP_PREFIX):
                continue
            flags = _vdf(text, "StateFlags")
            installed = bool(flags) and (int(flags) & 4) != 0 if flags.isdigit() else True
            folder = os.path.join(lib, "steamapps", "common", _vdf(text, "installdir"))
            last = _vdf(text, "LastPlayed")
            out.append(Found(
                id="steam:" + appid, title=name, source="steam", badge="STEAM",
                launch={"uri": "steam://rungameid/" + appid}, install_dir=folder,
                art={"cover": STEAM_CDN % (appid, "library_600x900.jpg"),
                     "cover_full": STEAM_CDN % (appid, "library_600x900_2x.jpg"),
                     "hero": STEAM_CDN % (appid, "library_hero.jpg"),
                     "logo": STEAM_CDN % (appid, "logo.png")},
                last_played=float(last) if last.isdigit() and int(last) > 0 else None,
                installed=installed, detail={"appid": appid}))
    return out


# ------------------------------------------------------------------ Epic
def scan_epic() -> list[Found]:
    base = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                        "Epic", "EpicGamesLauncher", "Data", "Manifests")
    out: list[Found] = []
    for mf in glob.glob(os.path.join(base, "*.item")):
        try:
            with open(mf, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        name, app = d.get("DisplayName"), d.get("AppName")
        cats = [str(c).lower() for c in (d.get("AppCategories") or [])]
        if not name or not app or (cats and "games" not in cats) or d.get("bIsIncompleteInstall"):
            continue
        folder = d.get("InstallLocation") or ""
        ns, item = d.get("CatalogNamespace") or "", d.get("CatalogItemId") or ""
        uri = "com.epicgames.launcher://apps/%s%%3A%s%%3A%s?action=launch&silent=true" % (ns, item, app)
        out.append(Found(id="epic:" + app, title=name, source="local", badge="EPIC", launch={"uri": uri},
                         install_dir=folder, detail={"epic": True, "exe": d.get("LaunchExecutable", "")}))
    return out


# ------------------------------------------------------------------ GOG
def scan_gog() -> list[Found]:
    out: list[Found] = []
    if winreg is None:
        return out
    key = r"SOFTWARE\WOW6432Node\GOG.com\Games"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ)
    except OSError:
        return out
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            name = _reg(winreg.HKEY_LOCAL_MACHINE, key + "\\" + sub, "gameName")
            path = _reg(winreg.HKEY_LOCAL_MACHINE, key + "\\" + sub, "path")
            exe = _reg(winreg.HKEY_LOCAL_MACHINE, key + "\\" + sub, "exe")
            if not (name and exe and os.path.isfile(exe)):
                continue
            out.append(Found(id="gog:" + sub, title=name, source="local", badge="GOG",
                             launch={"exe": exe, "cwd": path or os.path.dirname(exe)},
                             install_dir=path or os.path.dirname(exe), detail={"archive": "GOG"}))
    return out


def scan_all() -> list[Found]:
    found: list[Found] = []
    for fn in (scan_steam, scan_epic, scan_gog):
        try:
            found.extend(fn())
        except Exception:          # один сломанный источник не должен ронять остальные
            continue
    return found

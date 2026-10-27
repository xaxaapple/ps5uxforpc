"""PS5-игры через эмулятор KytyPS5: поиск эмулятора, чтение метаданных игры (название, иконка, фон) и команда запуска.

Что известно о формате (проверено по exe эмулятора U59 и документации KytyPS5):
  * игра = папка с `eboot.bin` (дамп) или архив `.zar`; запуск: `kyty_emulator.exe --game <папка|.zar>`;
  * метаданные лежат в `sce_sys/`: `param.json` (titleId, localizedParameters -> titleName), `icon0.png`
    (иконка), `pic0.png` / `pic1.png` (фон);
  * переменные окружения (KYTY_*) из `u59-preset.json` рядом с эмулятором применяет только родной `launcher.exe`;
    при прямом запуске `kyty_emulator.exe` мы делаем то же самое сами.
Модуль только читает файлы и ничего не пишет в папки игр.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import sys
from typing import Any

from . import paths

_log = logging.getLogger("pc5.ps5")

EMULATOR_EXE = "kyty_emulator.exe"
KYTY_LAUNCHER_EXE = "launcher.exe"
PRESET_NAME = "u59-preset.json"
ICON_NAMES = ("icon0.png",)
HERO_NAMES = ("pic0.png", "pic1.png")
MAX_JSON = 1024 * 1024
SKIP_DIRS = {"sce_sys", "_savedata", "_trophies", "_patches", "$recycle.bin", "system volume information",
             "windows", "program files", "program files (x86)", "node_modules", ".git"}
TITLE_ID = re.compile(r"\b(PPSA\d{5})\b", re.I)


def app_dir() -> str:
    """Папка с PC5Launcher.exe (в собранном приложении) или с исходниками."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------ эмулятор
def _emu_candidates(configured: str) -> list[str]:
    out: list[str] = []
    if configured:
        out.append(configured)
    base = app_dir()
    for d in (base, os.path.dirname(base), paths.data_dir()):
        for name in ("KytyPS5", "kytyps5", "emulator", "Emulator", "Kyty"):
            out.append(os.path.join(d, name))
    return out


def find_emulator(configured: str = "") -> str:
    """Полный путь к kyty_emulator.exe или пустая строка. `configured` — папка или сам exe."""
    for cand in _emu_candidates(configured):
        if not cand:
            continue
        exe = cand if cand.lower().endswith(".exe") else os.path.join(cand, EMULATOR_EXE)
        if os.path.basename(exe).lower() == EMULATOR_EXE and os.path.isfile(exe):
            return os.path.normpath(exe)
        if cand.lower().endswith(".exe") and os.path.isfile(cand):
            # выбрали launcher.exe из той же папки — берём соседний эмулятор
            sib = os.path.join(os.path.dirname(cand), EMULATOR_EXE)
            if os.path.isfile(sib):
                return os.path.normpath(sib)
    return ""


def kyty_launcher(emu_exe: str) -> str:
    p = os.path.join(os.path.dirname(emu_exe), KYTY_LAUNCHER_EXE) if emu_exe else ""
    return p if p and os.path.isfile(p) else ""


# ------------------------------------------------------------------ метаданные игры
def _read_json(path: str) -> dict:
    try:
        if os.path.getsize(path) > MAX_JSON:
            return {}
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _title_from_param(param: dict) -> str:
    loc = param.get("localizedParameters")
    if isinstance(loc, dict):
        order = [str(loc.get("defaultLanguage") or ""), "en-US", "en-GB"]
        for key in order + [k for k in loc if isinstance(loc.get(k), dict)]:
            entry = loc.get(key)
            if isinstance(entry, dict):
                name = entry.get("titleName")
                if isinstance(name, str) and name.strip():
                    return name.strip()
    for key in ("titleName", "title", "name"):
        v = param.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _first_file(folder: str, names: tuple[str, ...]) -> str:
    for n in names:
        p = os.path.join(folder, "sce_sys", n)
        if os.path.isfile(p):
            return p
    return ""


def game_key(path: str) -> str:
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8", "replace")).hexdigest()[:16]


def is_game_dir(path: str) -> bool:
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "eboot.bin"))


def is_game_path(path: str) -> bool:
    return is_game_dir(path) or (os.path.isfile(path) and path.lower().endswith(".zar"))


def read_game(path: str) -> dict[str, Any] | None:
    """Описание игры по папке (eboot.bin) или .zar; None, если это не игра."""
    path = os.path.normpath(path)
    if not is_game_path(path):
        return None
    if os.path.isdir(path):
        param = _read_json(os.path.join(path, "sce_sys", "param.json"))
        icon, hero = _first_file(path, ICON_NAMES), _first_file(path, HERO_NAMES)
        stem = os.path.basename(path.rstrip("\\/"))
    else:
        param, icon, hero = {}, "", ""
        stem = os.path.splitext(os.path.basename(path))[0]
    tid = str(param.get("titleId") or "").strip()
    if not tid:
        m = TITLE_ID.search(stem) or TITLE_ID.search(path)
        tid = m.group(1).upper() if m else ""
    title = _title_from_param(param) or stem
    version = str(param.get("contentVersion") or param.get("masterVersion") or "").strip()
    return {"path": path, "key": game_key(path), "title": title, "title_id": tid, "version": version,
            "icon": icon, "hero": hero, "archive": os.path.isfile(path)}


def find_games(root: str, depth: int = 4, limit: int = 500) -> list[str]:
    """Папки с eboot.bin и .zar внутри `root` (до `depth` уровней). Внутрь найденной игры не заходит."""
    out: list[str] = []
    if is_game_path(root):
        return [os.path.normpath(root)]

    def walk(d: str, level: int) -> None:
        if len(out) >= limit or level > depth:
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            try:
                if e.is_file() and e.name.lower().endswith(".zar"):
                    out.append(os.path.normpath(e.path))
            except OSError:
                continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False) or e.name.lower() in SKIP_DIRS:
                    continue
            except OSError:
                continue
            if os.path.isfile(os.path.join(e.path, "eboot.bin")):
                out.append(os.path.normpath(e.path))
            else:
                walk(e.path, level + 1)
    walk(root, 0)
    return out


# ------------------------------------------------------------------ запуск
def load_preset(emu_exe: str) -> dict[str, str]:
    data = _read_json(os.path.join(os.path.dirname(emu_exe), PRESET_NAME))
    return {str(k): ("" if v is None else str(v)) for k, v in data.items() if str(k).strip()}


def build_env(emu_exe: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """Как родной launcher.exe: убрать унаследованные KYTY_/TRACY_ и применить u59-preset.json."""
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not k.upper().startswith(("KYTY_", "TRACY_"))}
    env.update(load_preset(emu_exe))
    return env


def build_command(emu_exe: str, game: str, opts: dict, extra: str = "") -> list[str]:
    """Список аргументов (без оболочки, поэтому пути с пробелами и кавычками безопасны)."""
    cmd = [emu_exe, "--game", game]
    if opts.get("fullscreen"):
        cmd.append("--fullscreen")
    if opts.get("amd_cpu"):
        cmd.append("--amd-cpu")
    if opts.get("hide_cursor"):
        cmd.append("--hide-cursor")
    pm = str(opts.get("present_mode") or "")
    if pm in ("Fifo", "Mailbox", "Immediate"):
        cmd += ["--present-mode", pm]
    vb = str(opts.get("vblank") or "")
    if vb.isdigit() and 1 <= int(vb) <= 240:
        cmd += ["--vblank-frequency", vb]
    if extra.strip():
        try:
            parts = shlex.split(extra, posix=False)
        except ValueError:
            parts = extra.split()
        cmd += [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]   # кавычки снимаем сами
    return cmd

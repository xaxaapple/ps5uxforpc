"""Все пути в одном месте (Windows: %APPDATA%, рядом с exe лежит папка web)."""
from __future__ import annotations

import os
import sys


def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "PC5Launcher")
    os.makedirs(d, exist_ok=True)
    return d


def log_file() -> str:
    return os.path.join(data_dir(), "pc5launcher.log")


def web_dir() -> str:
    """Папка с интерфейсом: в собранном приложении <exe>/web (или _MEIPASS), при разработке app/web."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        for cand in (os.path.join(base, "web"), os.path.join(os.path.dirname(sys.executable), "web")):
            if os.path.isdir(cand):
                return cand
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("../../app/web", "../web"):
        cand = os.path.normpath(os.path.join(here, rel))
        if os.path.isdir(cand):
            return cand
    raise FileNotFoundError("Папка интерфейса web не найдена")

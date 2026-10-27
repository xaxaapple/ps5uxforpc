"""PC5 Launcher для Windows 11: родной интерфейс (app/web) + новый бэкенд на Python."""
import os


def _version() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("VERSION", "../../VERSION", "../VERSION"):
        try:
            with open(os.path.join(here, rel), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return "0"


VERSION = _version()
PRODUCT = "PC5 Launcher"

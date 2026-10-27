"""Маленькое JSON-хранилище (настройки и мета-данные библиотеки), запись атомарно и с блокировкой."""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from . import paths


class JsonStore:
    def __init__(self, name: str, default: Any):
        self._path = os.path.join(paths.data_dir(), name)
        self._lock = threading.RLock()
        self._default = default
        self.data: Any = self._load()

    def _load(self) -> Any:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, type(self._default)):
                return data
        except (OSError, ValueError):
            pass
        return json.loads(json.dumps(self._default))

    def save(self) -> None:
        with self._lock:
            tmp = self._path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self._path)
            except OSError:
                pass

    @property
    def lock(self) -> threading.RLock:
        return self._lock

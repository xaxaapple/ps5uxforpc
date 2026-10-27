"""Точка входа для PyInstaller (абсолютный импорт пакета) и для запуска из исходников."""
from pc5win.app import main

if __name__ == "__main__":
    raise SystemExit(main())

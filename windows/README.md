# PC5 Launcher для Windows 11

Порт запускается как обычное приложение: окно с родным интерфейсом PC5 Launcher (из `app/web`), библиотека игр
Steam / Epic / GOG и свои `.exe`, управление геймпадом (XInput) и клавиатурой.

## Установка

Скачайте из **Actions → Windows build → Artifacts** (или из **Releases**, если сборка шла по тегу):

* `PC5Launcher-Setup-<версия>.exe` — установщик (в профиль пользователя, права администратора не нужны);
* `PC5Launcher-<версия>-windows-x64-portable.zip` — распаковать и запустить `PC5Launcher.exe`.

Нужен Microsoft Edge WebView2 Runtime — в Windows 11 он уже есть.

## Как собрать через GitHub Actions

1. Залейте репозиторий на GitHub (папка `.github/workflows/windows.yml` уже в нём).
2. Любой push в `main`/`master`, pull request или ручной запуск (**Actions → Windows build → Run workflow**) собирает приложение.
3. Релиз: `git tag v0.3.4 && git push origin v0.3.4` — установщик и zip появятся в **Releases**.

Версия берётся из файла `VERSION`.

## Локальная сборка (Windows, Python 3.12)

```powershell
python -m pip install -r windows\requirements-build.txt
powershell -ExecutionPolicy Bypass -File windows\build.ps1
```

Запуск из исходников: `python windows\run_pc5.py` (добавьте `--fullscreen` для полного экрана).

## PS5-игры (эмулятор KytyPS5)

Лаунчер умеет добавлять игры для эмулятора [KytyPS5](https://github.com/KytyPS5/KytyPS5) (например, Astro's Playroom,
`PPSA01325`) и запускать их через `kyty_emulator.exe --game <папка>`. Название, иконка и фон берутся из самой игры:
`sce_sys/param.json`, `sce_sys/icon0.png`, `sce_sys/pic0.png` (если нет — `pic1.png`).

1. **Эмулятор.** Распакуйте сборку KytyPS5 (там `kyty_emulator.exe`) одним из способов:
   * в папку `KytyPS5` рядом с `PC5Launcher.exe` или в `%APPDATA%\PC5Launcher\KytyPS5` — найдётся сам;
   * в любое место и укажите его: **Настройки → PS5 games → Choose emulator folder**;
   * перед сборкой положите её в `windows\emulator\` — `build.ps1` включит её в установщик и portable-zip.
2. **Игра.** **Настройки → PS5 games → Add a game or a folder with games**: откройте папку игры (где лежит `eboot.bin`) и
   выберите её кнопкой «треугольник» (или папку, внутри которой лежат несколько игр — найдутся все). Архивы `.zar`
   тоже подходят (без иконки: внутри архива лаунчер её не читает).
3. Игра появится в библиотеке с бейджем **PS5**, иконкой и фоном; запуск — как у остальных игр.

Что делает запуск: берёт настройки `u59-preset.json` из папки эмулятора (родной `launcher.exe` Kyty делает то же самое, а
при прямом запуске `kyty_emulator.exe` они иначе потерялись бы), убирает унаследованные переменные `KYTY_*`/`TRACY_*`,
запускает эмулятор без консольного окна и пишет его вывод в `%APPDATA%\PC5Launcher\kyty-last-run.log`.
В настройках: полный экран, «AMD CPU patch» (для процессоров AMD), скрытие курсора, режим present (Fifo помогает при
зависании на RTX 50), частота vblank (30). Управление геймпадом, клавиатурой и графика — в родном лаунчере Kyty
(**Open the KytyPS5 launcher**). Первый запуск каждой игры долго собирает кэш шейдеров — это нормально.
Файлы игр лаунчер только читает. Игры нужны ваши собственные (дамп своей копии).

## Управление

| Геймпад | Клавиатура |
|---|---|
| Крестовина / левый стик | Стрелки |
| A / B / X / Y | Enter или Пробел / Esc / X / Y |
| LB / RB, LT / RT | Q / E, 1 / 3 |
| Start | O |
| Правый стик вверх/вниз | PageUp / PageDown |
| Back + Start (1,2 с) — на главный экран | Home |
| — | F11 — полный экран |

Геймпад реагирует только пока окно лаунчера на переднем плане.

## Что работает, а что нет (честно)

Работает: главный экран, библиотека (Steam, Epic, GOG, свои `.exe`), запуск и определение «игра идёт», время в игре,
избранное, скрытие, переименование, обложки Steam, темы, настройки интерфейса, меню питания (сон, выключение,
перезагрузка), файловый обозреватель и «Добавить в библиотеку», язык English / Deutsch.

Не портировано, потому что это Linux-механика и в Windows она не нужна или невозможна: Gamescope, Proton / FSR 4 /
OptiScaler, Flatpak-эмуляторы, установка из архивов через Proton, Bluetooth/Wi-Fi/звук через PipeWire/NetworkManager,
Control Center (оверлей поверх игры), экранная клавиатура, магазины внутри лаунчера. Эти разделы показывают пустые
списки; в Windows игры ставятся родными лаунчерами.

Данные хранятся в `%APPDATA%\PC5Launcher` (настройки, библиотека, лог `pc5launcher.log`, свои темы в `themes\`).

## Устройство

```
windows/pc5win/server.py    локальный сервер 127.0.0.1 (токен, проверка Host), отдаёт app/web и RPC
windows/pc5win/shim.js      имитирует мост WebKitGTK (window.webkit.messageHandlers.couch) через HTTP
windows/pc5win/backend.py   RPC-методы с теми же именами и формами ответов, что у Linux-ядра
windows/pc5win/sources.py   Steam / Epic / GOG
windows/pc5win/ps5.py       KytyPS5: поиск эмулятора, param.json / иконка / фон, команда запуска
windows/pc5win/pad.py       XInput -> события «pad»
windows/pc5win/app.py       окно WebView2 (pywebview), одиночный экземпляр, --selftest для CI
```

Интерфейс в `app/web` не менялся. Лицензия — GPL-3.0, как у оригинала.

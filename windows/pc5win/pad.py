"""Геймпад (XInput) -> события «pad» интерфейса. Работает только пока окно лаунчера на переднем плане,
чтобы нажатия не уходили в лаунчер во время игры."""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from typing import Callable

ERROR_SUCCESS = 0
BTN = {0x0001: "up", 0x0002: "down", 0x0004: "left", 0x0008: "right", 0x0010: "options", 0x0020: "create",
       0x0040: "l3", 0x0080: "r3", 0x0100: "l1", 0x0200: "r1", 0x1000: "cross", 0x2000: "circle",
       0x4000: "square", 0x8000: "triangle"}
REPEAT_FIRST_S, REPEAT_EVERY_S = 0.40, 0.09
STICK_ON, STICK_OFF = 20000, 12000
TRIGGER_ON = 130
REPEATING = frozenset({"up", "down", "left", "right", "rs-up", "rs-down", "l1", "r1"})
PS_HOLD_S = 1.2           # Back + Start подряд 1,2 с = «PS долго» (вернуться на главный экран)


class _Gamepad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class _State(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", _Gamepad)]


def _load():
    if sys.platform != "win32":
        return None
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue
    return None


def _ours_in_front() -> bool:
    try:
        u = ctypes.windll.user32                                       # type: ignore[attr-defined]
        hwnd = u.GetForegroundWindow()
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:                                                  # noqa: BLE001
        return True


class PadReader(threading.Thread):
    def __init__(self, dispatch: Callable[[str, dict], None]):
        super().__init__(name="xinput", daemon=True)
        self.dispatch = dispatch
        self._stop = threading.Event()
        self._held: dict[str, float] = {}       # кнопка -> когда повторить в следующий раз
        self._ps_since: float | None = None

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _active(g: _Gamepad) -> set[str]:
        on = {name for bit, name in BTN.items() if g.wButtons & bit}
        if g.bLeftTrigger > TRIGGER_ON:
            on.add("l2")
        if g.bRightTrigger > TRIGGER_ON:
            on.add("r2")
        for v, neg, pos in ((g.sThumbLX, "left", "right"), (-g.sThumbLY, "up", "down")):
            if v < -STICK_ON:
                on.add(neg)
            elif v > STICK_ON:
                on.add(pos)
        if -g.sThumbRY < -STICK_ON:
            on.add("rs-up")
        elif -g.sThumbRY > STICK_ON:
            on.add("rs-down")
        return on

    def run(self) -> None:
        lib = _load()
        if lib is None:
            return
        state = _State()
        slot: int | None = None
        next_scan = 0.0
        while not self._stop.is_set():
            time.sleep(0.016)
            now = time.monotonic()
            if slot is not None and lib.XInputGetState(slot, ctypes.byref(state)) != ERROR_SUCCESS:
                slot = None
                self._held.clear()
            if slot is None:
                if now < next_scan:
                    continue
                next_scan = now + 1.5            # XInput дорого опрашивать по пустым слотам: ищем раз в 1,5 с
                slot = next((i for i in range(4) if lib.XInputGetState(i, ctypes.byref(state)) == ERROR_SUCCESS), None)
                if slot is None:
                    continue
            if not _ours_in_front():
                self._held.clear()
                self._ps_since = None
                continue
            active = self._active(state.Gamepad)
            if {"create", "options"} <= active:                          # Back+Start: «PS долго»
                if self._ps_since is None:
                    self._ps_since = now
                elif self._ps_since >= 0 and now - self._ps_since >= PS_HOLD_S:
                    self.dispatch("ps", {"kind": "long"})
                    self._ps_since = -1.0                                # один раз за удержание
                self._held.clear()
                continue
            self._ps_since = None
            for b in list(self._held):
                if b not in active:
                    del self._held[b]
            for b in active:
                due = self._held.get(b)
                if due is None:
                    self._held[b] = now + REPEAT_FIRST_S
                    self.dispatch("pad", {"button": b, "repeat": False})
                elif now >= due and b in REPEATING:
                    self._held[b] = now + REPEAT_EVERY_S
                    self.dispatch("pad", {"button": b, "repeat": True})

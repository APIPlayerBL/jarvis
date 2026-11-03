                   
"""Определение режима game/desktop по активному окну (WinAPI + psutil)."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

if __package__ in (None, ""):                                         
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from common.log import get_logger

log = get_logger("ears.mode")

_TTL = 1.0
_CFG_TTL = 10.0
_DEFAULT_CFG = {"procs": [], "subs": [], "fs_unknown": False}
_CACHE = {"mode": "desktop", "ts": 0.0, "title": "", "proc": ""}
_CFG_CACHE: dict = {"ts": 0.0, "data": _DEFAULT_CFG}
_user32 = None
_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000


def _get_user32():
    global _user32
    if _user32 is None:
        _user32 = ctypes.windll.user32
    return _user32


def _load_cfg() -> dict:
    now = time.monotonic()
    if now - _CFG_CACHE["ts"] < _CFG_TTL:
        return _CFG_CACHE["data"]
    data = dict(_DEFAULT_CFG)
    try:
        with open(config.GAMES_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        data = {
            "procs": [str(p).lower() for p in raw.get("processes", [])],
            "subs": [str(s).lower() for s in raw.get("window_substrings", [])],
            "fs_unknown": bool(raw.get("fullscreen_unknown_is_game", False)),
        }
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    _CFG_CACHE.update({"ts": now, "data": data})
    return data


def _foreground_info():
    user32 = _get_user32()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", "", False

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    title = ""
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value or ""
    except Exception:                
        pass

    proc = ""
    try:
        import psutil

        proc = psutil.Process(pid.value).name()
    except Exception:                
        pass

    fullscreen = False
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        style = get_long(hwnd, _GWL_STYLE) & 0xFFFFFFFF
        fullscreen = (
            rect.left == 0 and rect.top == 0
            and rect.right - rect.left == width and rect.bottom - rect.top == height
            and not (style & _WS_CAPTION)
        )
    except Exception:                
        pass
    return title, proc, fullscreen


def current_mode(force: bool = False) -> str:
    now = time.time()
    if not force and now - _CACHE["ts"] < _TTL:
        return _CACHE["mode"]

    try:
        title, proc, fullscreen = _foreground_info()
    except Exception as exc:                                                    
        log.debug("game_mode: %s", exc)
        return _CACHE["mode"]

    cfg = _load_cfg()
    proc_lower = proc.lower()
    title_lower = title.lower()
    mode = "desktop"
    if any(proc_lower == p or proc_lower.startswith(p) for p in cfg["procs"]):
        mode = "game"
    elif any(s in title_lower for s in cfg["subs"]):
        mode = "game"
    elif fullscreen and cfg["fs_unknown"]:
        mode = "game"

    _CACHE.update({"mode": mode, "ts": now, "title": title, "proc": proc})
    return mode


def debug_snapshot() -> dict:
    current_mode(force=True)
    return dict(_CACHE)


if __name__ == "__main__":
    print("Детектор режима. Ctrl+C — выход.\n")
    try:
        while True:
            snapshot = debug_snapshot()
            print(f"mode={snapshot['mode']:<8} proc={snapshot['proc']!r:<30} title={snapshot['title']!r}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nВыход.")

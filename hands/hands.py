# hands/hands.py
"""Ð˜ÑÐ¿Ð¾Ð»Ð½Ð¸Ñ‚ÐµÐ»ÑŒ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ð¹ Ð½Ð° ÐŸÐš.

ÐšÐ»ÑŽÑ‡ÐµÐ²Ñ‹Ðµ Ð¸ÑÐ¿Ñ€Ð°Ð²Ð»ÐµÐ½Ð¸Ñ:
- Ð½Ð¸ÐºÐ°ÐºÐ¾Ð³Ð¾ shell=True Ð¸ Â«Ð»Ð¾Ð¶Ð½Ð¾Ð³Ð¾ ÑƒÑÐ¿ÐµÑ…Ð°Â»: ÐµÑÐ»Ð¸ exe Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½ â€” Ñ‡ÐµÑÑ‚Ð½Ð°Ñ Ð¾ÑˆÐ¸Ð±ÐºÐ°;
- Ð³Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚ÑŒ Ñ‡ÐµÑ€ÐµÐ· Ð°ÐºÑ‚ÑƒÐ°Ð»ÑŒÐ½Ñ‹Ð¹ pycaw API (volume_percent/EndpointVolume);
- Ð¿Ð¾Ð´Ñ‚Ð²ÐµÑ€Ð¶Ð´ÐµÐ½Ð¸Ðµ Ð¾Ð¿Ð°ÑÐ½Ñ‹Ñ… Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ð¹ Ñ‡ÐµÑ€ÐµÐ· Ð²Ð½ÐµÐ´Ñ€ÑÐµÐ¼Ñ‹Ð¹ Ð¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº (Ð³Ð¾Ð»Ð¾Ñ/ÐºÐ¾Ð½ÑÐ¾Ð»ÑŒ),
  Ð° Ð½Ðµ Ð±ÐµÐ·ÑƒÑÐ»Ð¾Ð²Ð½Ñ‹Ð¹ input();
- Ð°Ñ€Ð³ÑƒÐ¼ÐµÐ½Ñ‚Ñ‹ tool-Ð²Ñ‹Ð·Ð¾Ð²Ð° Ð²Ð°Ð»Ð¸Ð´Ð¸Ñ€ÑƒÑŽÑ‚ÑÑ Ð¿Ð¾ ÑÐ¸Ð³Ð½Ð°Ñ‚ÑƒÑ€Ðµ Ñ„ÑƒÐ½ÐºÑ†Ð¸Ð¸;
- dry-run Ñ€ÐµÐ¶Ð¸Ð¼ Ð´Ð»Ñ Ð±ÐµÐ·Ð¾Ð¿Ð°ÑÐ½Ð¾Ð³Ð¾ Ñ‚ÐµÑÑ‚Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¸Ñ Ð²ÑÐµÐ³Ð¾ Ð¿Ð°Ð¹Ð¿Ð»Ð°Ð¹Ð½Ð°.
"""
from __future__ import annotations

import contextlib
import ctypes
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

import config
from common.log import get_logger
from common.timefmt import both_text, date_text, time_text
from memory import apps, discovery

log = get_logger("hands")

# --------------------------------------------------------------------------
# Ð¶ÑƒÑ€Ð½Ð°Ð» Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ð¹ (Ð°ÑƒÐ´Ð¸Ñ‚, Ð½Ðµ Ð´Ð¸Ð°Ð³Ð½Ð¾ÑÑ‚Ð¸ÐºÐ°)
# --------------------------------------------------------------------------
_MAX_AUDIT_BYTES = 1_000_000


def _audit(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    log.debug(message)
    try:
        path = config.ACTIONS_LOG_PATH
        if path.exists() and path.stat().st_size > _MAX_AUDIT_BYTES:
            path.replace(path.with_suffix(".log.1"))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
# Ð»ÐµÐ½Ð¸Ð²Ñ‹Ðµ Ð¸Ð¼Ð¿Ð¾Ñ€Ñ‚Ñ‹
# --------------------------------------------------------------------------
_pycaw_error_logged = False


def _endpoint_volume():
    """Ð’ÐµÑ€Ð½ÑƒÑ‚ÑŒ IAudioEndpointVolume Ð´Ð»Ñ ÑƒÑÑ‚Ñ€Ð¾Ð¹ÑÑ‚Ð²Ð° Ð¿Ð¾ ÑƒÐ¼Ð¾Ð»Ñ‡Ð°Ð½Ð¸ÑŽ."""
    global _pycaw_error_logged
    try:
        from pycaw.pycaw import AudioUtilities

        device = AudioUtilities.GetSpeakers()
        endpoint = getattr(device, "EndpointVolume", None)
        if endpoint is not None:
            return endpoint

        # Ð¡Ñ‚Ð°Ñ€Ñ‹Ð¹ pycaw: AudioDevice ÑÐ°Ð¼ ÑƒÐ¼ÐµÐµÑ‚ Activate.
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume

        interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception as exc:  # noqa: BLE001
        if not _pycaw_error_logged:
            _pycaw_error_logged = True
            log.warning("pycaw Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½: %s", exc)
        return None


def _get_volume() -> int | None:
    endpoint = _endpoint_volume()
    if endpoint is None:
        return None
    try:
        return int(round(endpoint.GetMasterVolumeLevelScalar() * 100))
    except Exception:  # noqa: BLE001
        return None


def _set_volume(percent: int) -> bool:
    endpoint = _endpoint_volume()
    if endpoint is None:
        return False
    try:
        endpoint.SetMasterVolumeLevelScalar(max(0, min(100, int(percent))) / 100.0, None)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ð´Ð°Ñ‚ÑŒ Ð³Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚ÑŒ: %s", exc)
        return False


def _set_mute(muted: bool) -> bool:
    endpoint = _endpoint_volume()
    if endpoint is None:
        return False
    try:
        endpoint.SetMute(1 if muted else 0, None)
        return True
    except Exception:  # noqa: BLE001
        return False


def _media_key(vk: int) -> bool:
    """ÐÐ°Ð¶Ð°Ñ‚Ð¸Ðµ Ð¼ÑƒÐ»ÑŒÑ‚Ð¸Ð¼ÐµÐ´Ð¸Ð°-ÐºÐ»Ð°Ð²Ð¸ÑˆÐ¸ Ñ‡ÐµÑ€ÐµÐ· keybd_event (Ð±ÐµÐ· pyautogui)."""
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 0x0002, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def _press_win_d() -> bool:
    try:
        user32 = ctypes.windll.user32
        vk_lwin, vk_d = 0x5B, 0x44
        user32.keybd_event(vk_lwin, 0, 0, 0)
        user32.keybd_event(vk_d, 0, 0, 0)
        user32.keybd_event(vk_d, 0, 0x0002, 0)
        user32.keybd_event(vk_lwin, 0, 0x0002, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Ð¿Ð¾Ð´Ñ‚Ð²ÐµÑ€Ð¶Ð´ÐµÐ½Ð¸Ðµ Ð¾Ð¿Ð°ÑÐ½Ñ‹Ñ… Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ð¹
# --------------------------------------------------------------------------
_confirm_handler = None


def set_confirm_handler(handler) -> None:
    """main Ð¿Ð¾Ð´ÐºÐ»ÑŽÑ‡Ð°ÐµÑ‚ Ð³Ð¾Ð»Ð¾ÑÐ¾Ð²Ð¾Ðµ Ð¿Ð¾Ð´Ñ‚Ð²ÐµÑ€Ð¶Ð´ÐµÐ½Ð¸Ðµ; Ñ‚ÐµÑÑ‚Ñ‹ â€” ÑÐ²Ð¾Ð¹ Ð¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº."""
    global _confirm_handler
    _confirm_handler = handler


def _confirm(prompt: str) -> bool:
    if getattr(config, "AUTO_CONFIRM", False) or not getattr(config, "CONFIRM_MODE", True):
        return True
    if config.env_bool("JARVIS_AUTO_CONFIRM", False):
        return True
    if _confirm_handler is not None:
        try:
            return bool(_confirm_handler(prompt))
        except Exception as exc:  # noqa: BLE001
            log.warning("ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº Ð¿Ð¾Ð´Ñ‚Ð²ÐµÑ€Ð¶Ð´ÐµÐ½Ð¸Ñ ÑƒÐ¿Ð°Ð»: %s", exc)
            return False
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            answer = input(f"{prompt} (Ð´Ð°/Ð½ÐµÑ‚): ").strip().lower()
            return answer in {"Ð´Ð°", "Ð´", "y", "yes", "Ð¿Ð¾Ð´Ñ‚Ð²ÐµÑ€Ð¶Ð´Ð°ÑŽ"}
        except (EOFError, KeyboardInterrupt):
            return False
    log.warning("ÐŸÐ¾Ð´Ñ‚Ð²ÐµÑ€Ð¶Ð´ÐµÐ½Ð¸Ðµ Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿Ð½Ð¾ (Ð½ÐµÑ‚ ÐºÐ¾Ð½ÑÐ¾Ð»Ð¸/Ð³Ð¾Ð»Ð¾ÑÐ¾Ð²Ð¾Ð³Ð¾ Ð¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸ÐºÐ°) â€” Ð¾Ñ‚ÐºÐ°Ð·")
    return False


# --------------------------------------------------------------------------
# Ð·Ð°Ð¿ÑƒÑÐº / Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ð¸Ðµ Ð¿Ñ€Ð¸Ð»Ð¾Ð¶ÐµÐ½Ð¸Ð¹
# --------------------------------------------------------------------------
def _candidate_paths(canonical: str) -> list[str]:
    candidates: list[str] = []
    explicit = apps.target(canonical)
    if explicit:
        candidates.append(os.path.expandvars(explicit))

    home = Path.home()
    local = Path(os.getenv("LOCALAPPDATA", home / "AppData" / "Local"))
    roaming = Path(os.getenv("APPDATA", home / "AppData" / "Roaming"))
    pf = Path(os.getenv("PROGRAMFILES", r"C:\Program Files"))
    pf86 = Path(os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))

    known = {
        "chrome": [pf / "Google/Chrome/Application/chrome.exe", pf86 / "Google/Chrome/Application/chrome.exe",
                   local / "Google/Chrome/Application/chrome.exe"],
        "edge": [pf86 / "Microsoft/Edge/Application/msedge.exe", pf / "Microsoft/Edge/Application/msedge.exe"],
        "telegram": [roaming / "Telegram Desktop/Telegram.exe", local / "Telegram Desktop/Telegram.exe"],
        "spotify": [roaming / "Spotify/Spotify.exe", local / "Microsoft/WindowsApps/Spotify.exe"],
        "steam": [pf86 / "Steam/steam.exe", pf / "Steam/steam.exe"],
        "word": [pf / "Microsoft Office/root/Office16/WINWORD.EXE", pf86 / "Microsoft Office/root/Office16/WINWORD.EXE"],
        "excel": [pf / "Microsoft Office/root/Office16/EXCEL.EXE", pf86 / "Microsoft Office/root/Office16/EXCEL.EXE"],
        "vlc": [pf / "VideoLAN/VLC/vlc.exe", pf86 / "VideoLAN/VLC/vlc.exe"],
    }
    candidates.extend(str(p) for p in known.get(canonical, []))
    if canonical == "discord":
        for pattern in ("Discord/app-*/Discord.exe", "DiscordPTB/app-*/DiscordPTB.exe"):
            candidates.extend(str(p) for p in sorted(local.glob(pattern), reverse=True))
    return candidates


def _resolve_target(canonical: str, original: str) -> str | None:
    for candidate in _candidate_paths(canonical):
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if candidate_path.is_file() or candidate_path.is_dir():
            return candidate
        if candidate.startswith(("http://", "https://")):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found

    for name in (canonical, original):
        path = discovery.find(name)
        if path and Path(path).exists():
            return path
    found = shutil.which(canonical)
    if found:
        return found
    # ÐšÑ€Ð¸Ñ‚Ð¸Ñ‡ÐµÑÐºÐ¸ Ð²Ð°Ð¶Ð½Ñ‹Ðµ Ð¿Ñ€Ð¸Ð»Ð¾Ð¶ÐµÐ½Ð¸Ñ, ÐºÐ¾Ñ‚Ð¾Ñ€Ñ‹Ñ… Ð½ÐµÑ‚ Ð»Ð¾ÐºÐ°Ð»ÑŒÐ½Ð¾, Ð½Ðµ ÑÑ‚Ð¾Ð¸Ñ‚ Ð¿Ñ€Ð¾ÑÑ‚Ð¾ Ð¾Ð±ÑŠÑÐ²Ð»ÑÑ‚ÑŒ
    # Â«Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾Â»: Ð´Ð°Ñ‘Ð¼ Ð½Ð°Ð´Ñ‘Ð¶Ð½Ñ‹Ð¹ web-Ñ„Ð¾Ð»Ð»Ð±ÑÐº, Ñ‡Ñ‚Ð¾Ð±Ñ‹ ÐºÐ¾Ð¼Ð°Ð½Ð´Ð° Ð½Ðµ Ð¿Ñ€Ð¾Ð²Ð°Ð»Ð¸Ð²Ð°Ð»Ð°ÑÑŒ.
    web_fallbacks = {
        "telegram": "https://web.telegram.org",
        "chrome": "https://www.google.com",
        "edge": "https://www.bing.com",
    }
    return web_fallbacks.get(canonical)


def launch_app(app_name: str) -> str:
    if not apps.is_safe_name(app_name):
        return "ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ð¾Ðµ Ð¸Ð¼Ñ Ð¿Ñ€Ð¸Ð»Ð¾Ð¶ÐµÐ½Ð¸Ñ."
    original = apps.norm(app_name)
    canonical = apps.resolve(original) or original
    _audit(f"launch_app: {app_name!r} -> canonical={canonical!r}")

    if original.startswith(("http://", "https://")):
        return open_website(original)

    appid = apps.steam_appid(canonical)
    if appid:
        try:
            os.startfile(f"steam://rungameid/{appid}")
            return f"Ð—Ð°Ð¿ÑƒÑÐºÐ°ÑŽ {canonical} Ñ‡ÐµÑ€ÐµÐ· Steam."
        except OSError as exc:
            return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ð¿ÑƒÑÑ‚Ð¸Ñ‚ÑŒ {canonical} Ñ‡ÐµÑ€ÐµÐ· Steam: {exc}"

    target = _resolve_target(canonical, original)
    if not target:
        return f"ÐÐµ Ð½Ð°ÑˆÑ‘Ð» Ð¿Ñ€Ð¸Ð»Ð¾Ð¶ÐµÐ½Ð¸Ðµ Â«{app_name}Â». Ð”Ð¾Ð±Ð°Ð²ÑŒ Ð¿ÑƒÑ‚ÑŒ Ð² config/apps.json."

    try:
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
            return f"ÐžÑ‚ÐºÑ€Ñ‹Ð» {canonical} Ð² Ð²ÐµÐ±-Ð²ÐµÑ€ÑÐ¸Ð¸."
        elif target.startswith(("ms-settings:", "steam://")) or target.lower().endswith(".lnk") or Path(target).is_dir():
            os.startfile(target)
        else:
            subprocess.Popen([target], cwd=str(Path(target).parent) if Path(target).parent != Path('.') else None, close_fds=True)
        return f"Ð—Ð°Ð¿ÑƒÑÑ‚Ð¸Ð» {canonical}."
    except OSError as exc:
        log.warning("launch_app %s: %s", target, exc)
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ð¿ÑƒÑÑ‚Ð¸Ñ‚ÑŒ {canonical}: {exc}"


_EXE_NAMES = {
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "discord": ["discord.exe", "discordptb.exe"],
    "telegram": ["telegram.exe"],
    "steam": ["steam.exe"],
    "spotify": ["spotify.exe"],
    "dota 2": ["dota2.exe"],
    "cs2": ["cs2.exe"],
    "notepad": ["notepad.exe"],
    "calc": ["calculatorapp.exe", "calc.exe"],
    "explorer": ["explorer.exe"],
    "word": ["winword.exe"],
    "excel": ["excel.exe"],
    "vlc": ["vlc.exe"],
}
_PROTECTED = {"python.exe", "pythonw.exe", "system", "registry", "memcompression"}


def close_app(app_name: str) -> str:
    canonical = apps.resolve(app_name) or apps.norm(app_name)
    _audit(f"close_app: {app_name!r} -> canonical={canonical!r}")
    exe_names = _EXE_NAMES.get(canonical, [f"{canonical}.exe"])
    try:
        import psutil
    except ImportError:
        return "ÐœÐ¾Ð´ÑƒÐ»ÑŒ psutil Ð½Ðµ ÑƒÑÑ‚Ð°Ð½Ð¾Ð²Ð»ÐµÐ½. Ð’Ñ‹Ð¿Ð¾Ð»Ð½Ð¸ pip install psutil."

    own_pid = os.getpid()
    closed = 0
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name not in exe_names or name in _PROTECTED or proc.info.get("pid") == own_pid:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except psutil.TimeoutExpired:
                proc.kill()
            closed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if closed:
        return f"Ð—Ð°ÐºÑ€Ñ‹Ð» {canonical}."
    return f"ÐŸÑ€Ð¾Ñ†ÐµÑÑ {canonical} Ð½Ðµ Ð·Ð°Ð¿ÑƒÑ‰ÐµÐ½."


# --------------------------------------------------------------------------
# Ð³Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚ÑŒ / Ð¼ÐµÐ´Ð¸Ð°
# --------------------------------------------------------------------------
def control_volume(action: str, level: int | None = None) -> str:
    action = str(action or "").lower()
    _audit(f"control_volume: action={action} level={level}")

    if action == "set":
        if level is None:
            return "ÐÐµ ÑƒÐºÐ°Ð·Ð°Ð½ ÑƒÑ€Ð¾Ð²ÐµÐ½ÑŒ Ð³Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚Ð¸."
        try:
            level = max(0, min(100, int(level)))
        except (TypeError, ValueError):
            return "ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ð¹ ÑƒÑ€Ð¾Ð²ÐµÐ½ÑŒ Ð³Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚Ð¸."
        if _set_volume(level):
            return f"Ð“Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚ÑŒ {level}%."
        return "ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¸Ð·Ð¼ÐµÐ½Ð¸Ñ‚ÑŒ Ð³Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚ÑŒ (pycaw Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½)."

    if action == "mute":
        if _set_mute(True):
            return "Ð—Ð²ÑƒÐº Ð²Ñ‹ÐºÐ»ÑŽÑ‡ÐµÐ½."
        return "ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð²Ñ‹ÐºÐ»ÑŽÑ‡Ð¸Ñ‚ÑŒ Ð·Ð²ÑƒÐº."
    if action == "unmute":
        if _set_mute(False):
            current = _get_volume()
            return f"Ð—Ð²ÑƒÐº Ð²ÐºÐ»ÑŽÑ‡Ñ‘Ð½. Ð“Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚ÑŒ {current}%." if current is not None else "Ð—Ð²ÑƒÐº Ð²ÐºÐ»ÑŽÑ‡Ñ‘Ð½."
        return "ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð²ÐºÐ»ÑŽÑ‡Ð¸Ñ‚ÑŒ Ð·Ð²ÑƒÐº."

    if action in {"up", "down"}:
        current = _get_volume()
        if current is not None:
            new_level = max(0, min(100, current + (5 if action == "up" else -5)))
            if _set_volume(new_level):
                return f"Ð“Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚ÑŒ {new_level}%."
        key = 0xAF if action == "up" else 0xAE  # VK_VOLUME_UP / DOWN
        if _media_key(key):
            return "Ð“Ñ€Ð¾Ð¼ÐºÐ¾ÑÑ‚ÑŒ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð°."
        return f"ÐÐµÐ¸Ð·Ð²ÐµÑÑ‚Ð½Ð¾Ðµ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ðµ: {action}"
    return f"ÐÐµÐ¸Ð·Ð²ÐµÑÑ‚Ð½Ð¾Ðµ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ðµ: {action}"


def media_control(action: str) -> str:
    action = str(action or "").lower()
    # Ð•ÑÐ»Ð¸ Ð¸Ð³Ñ€Ð°ÐµÐ¼ Ñ‚Ñ€ÐµÐº ÑÐ°Ð¼Ð¸ (Ð»Ð¾ÐºÐ°Ð»ÑŒÐ½Ñ‹Ð¹ Ñ„Ð°Ð¹Ð») â€” ÑƒÐ¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ ÑÐ²Ð¾Ð¸Ð¼ Ð¿Ñ€Ð¾Ð¸Ð³Ñ€Ñ‹Ð²Ð°Ñ‚ÐµÐ»ÐµÐ¼,
    # Ð¸Ð½Ð°Ñ‡Ðµ Ð¾Ñ‚Ð¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ ÑÐ¸ÑÑ‚ÐµÐ¼Ð½Ñ‹Ðµ Ð¼ÐµÐ´Ð¸Ð°-ÐºÐ»Ð°Ð²Ð¸ÑˆÐ¸.
    try:
        from hands import music

        if music.player.active:
            _audit(f"media_control: {action} (Ð»Ð¾ÐºÐ°Ð»ÑŒÐ½Ñ‹Ð¹ Ð¿Ð»ÐµÐµÑ€)")
            return music.media_action(action)
    except Exception as exc:  # noqa: BLE001
        log.debug("Ð›Ð¾ÐºÐ°Ð»ÑŒÐ½Ñ‹Ð¹ Ð¿Ð»ÐµÐµÑ€ Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½: %s", exc)
    vk = {
        "play": 0xB3,
        "pause": 0xB3,
        "next": 0xB0,
        "prev": 0xB1,
        "stop": 0xB2,
    }.get(action)
    if vk is None:
        return f"ÐÐµÐ¸Ð·Ð²ÐµÑÑ‚Ð½Ð¾Ðµ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ðµ: {action}"
    _audit(f"media_control: {action}")
    if _media_key(vk):
        return f"ÐœÐµÐ´Ð¸Ð°: {action}."
    return "ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¾Ñ‚Ð¿Ñ€Ð°Ð²Ð¸Ñ‚ÑŒ Ð¼ÐµÐ´Ð¸Ð°-ÐºÐ»Ð°Ð²Ð¸ÑˆÑƒ."


def minimize_all() -> str:
    _audit("minimize_all")
    if _press_win_d():
        return "Ð¡Ð²ÐµÑ€Ð½ÑƒÐ» Ð²ÑÐµ Ð¾ÐºÐ½Ð°."
    return "ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ ÑÐ²ÐµÑ€Ð½ÑƒÑ‚ÑŒ Ð¾ÐºÐ½Ð°."


# --------------------------------------------------------------------------
# Ñ„Ð°Ð¹Ð»Ñ‹
# --------------------------------------------------------------------------
_STOP = {
    "Ð½Ð°Ð¹Ð´Ð¸", "Ð½Ð°Ð¹Ñ‚Ð¸", "Ð¾Ñ‚ÐºÑ€Ð¾Ð¹", "Ð¾Ñ‚ÐºÑ€Ñ‹Ñ‚ÑŒ", "Ð·Ð°Ð¿ÑƒÑÑ‚Ð¸", "Ð·Ð°Ð¿ÑƒÑÑ‚Ð¸Ñ‚ÑŒ", "Ð¿Ð¾ÐºÐ°Ð¶Ð¸", "Ð¿Ð¾ÐºÐ°Ð·Ð°Ñ‚ÑŒ",
    "Ð»ÑŽÐ±Ð¾Ð¹", "Ð»ÑŽÐ±Ð¾Ðµ", "Ð»ÑŽÐ±ÑƒÑŽ", "Ð»ÑŽÐ±Ñ‹Ðµ", "Ñ„Ð°Ð¹Ð»", "Ñ„Ð°Ð¹Ð»Ñ‹", "Ð¿Ð°Ð¿ÐºÑƒ", "Ð¿Ð°Ð¿ÐºÐ°", "Ð¿Ð°Ð¿ÐºÐ¸",
    "Ð½Ð°", "Ð²", "Ð¸", "Ñ", "Ð¿Ð¾", "Ð¿Ðº", "ÐºÐ¾Ð¼Ð¿ÑŒÑŽÑ‚ÐµÑ€Ðµ", "ÐºÐ¾Ð¼Ð¿ÑŒÑŽÑ‚ÐµÑ€", "Ð¾Ñ‚Ñ‚ÑƒÐ´Ð°", "Ð¸Ð·", "Ð¼Ð½Ðµ",
    "Ð¿Ð¾Ð¶Ð°Ð»ÑƒÐ¹ÑÑ‚Ð°", "ÑÑ‚Ð¾Ñ‚", "ÑÑ‚Ñƒ", "ÑÐ°Ð¼Ñ‹Ð¹", "ÑÐ°Ð¼ÑƒÑŽ", "Ð½Ð¾Ð²Ñ‹Ð¹", "Ð½Ð¾Ð²ÑƒÑŽ", "Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ð¹",
    "Ð¿Ð¾ÑÐ»ÐµÐ´Ð½ÑŽÑŽ", "Ñ‡Ñ‚Ð¾", "Ñ‚Ð¾", "Ñ‚Ð°Ð¼", "Ð³Ð´Ðµ", "Ð¼Ð¾Ð¹", "Ð¼Ð¾ÑŽ", "Ð´Ð¸ÑÐºÐµ", "Ð´Ð¸ÑÐº",
}
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic")
_DOC_EXT = (".docx", ".doc", ".pdf", ".txt", ".rtf", ".odt", ".xlsx", ".pptx", ".csv")
_MEDIA_EXT = (".mp4", ".mkv", ".avi", ".mov", ".mp3", ".flac", ".wav", ".m4a")

_SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".svn", "venv", ".venv", "appdata", "windows", "$recycle.bin"}
_MAX_DEPTH = 4
_MAX_SCANNED = 30000
_TIME_BUDGET_SEC = 6.0


def _file_type_filter(query: str) -> tuple[str, ...]:
    if any(w in query for w in ("Ñ„Ð¾Ñ‚Ð¾", "Ñ„Ð¾Ñ‚Ð¾Ð³Ñ€Ð°Ñ„", "ÐºÐ°Ñ€Ñ‚Ð¸Ð½Ðº", "ÑÐºÑ€Ð¸Ð½ÑˆÐ¾Ñ‚", "Ð¸Ð·Ð¾Ð±Ñ€Ð°Ð¶ÐµÐ½", "ÑÐ½Ð¸Ð¼Ð¾Ðº")):
        return _IMAGE_EXT
    if any(w in query for w in ("Ð´Ð¾ÐºÑƒÐ¼ÐµÐ½Ñ‚", "Ð´Ð¸Ð¿Ð»Ð¾Ð¼", "Ð²Ð¾Ñ€Ð´", "word", "Ñ‚ÐµÐºÑÑ‚", "Ð¿Ñ€ÐµÐ·ÐµÐ½Ñ‚Ð°Ñ†", "Ñ‚Ð°Ð±Ð»Ð¸Ñ†", "ÑÐºÑÐµÐ»ÑŒ", "excel")):
        return _DOC_EXT
    if any(w in query for w in ("Ð²Ð¸Ð´ÐµÐ¾", "Ñ„Ð¸Ð»ÑŒÐ¼", "ÐºÐ»Ð¸Ð¿")):
        return _MEDIA_EXT
    if any(w in query for w in ("Ð¼ÑƒÐ·Ñ‹Ðº", "Ð¿ÐµÑÐ½", "Ð°ÑƒÐ´Ð¸Ð¾", "Ñ‚Ñ€ÐµÐº")):
        return (".mp3", ".flac", ".wav", ".m4a", ".ogg")
    return ()


def _keywords(query: str) -> list[str]:
    words = re.split(r"[^\w]+", query.lower().replace("Ñ‘", "Ðµ"), flags=re.UNICODE)
    type_words = {
        "Ñ„Ð¾Ñ‚Ð¾", "Ñ„Ð¾Ñ‚Ð¾Ð³Ñ€Ð°Ñ„", "Ñ„Ð¾Ñ‚Ð¾Ð³Ñ€Ð°Ñ„Ð¸ÑŽ", "ÐºÐ°Ñ€Ñ‚Ð¸Ð½ÐºÑƒ", "ÐºÐ°Ñ€Ñ‚Ð¸Ð½ÐºÐ°", "ÑÐºÑ€Ð¸Ð½ÑˆÐ¾Ñ‚", "Ð¸Ð·Ð¾Ð±Ñ€Ð°Ð¶ÐµÐ½Ð¸Ðµ",
        "Ð´Ð¾ÐºÑƒÐ¼ÐµÐ½Ñ‚", "Ð´Ð¾ÐºÑƒÐ¼ÐµÐ½Ñ‚Ñ‹", "Ð´Ð¸Ð¿Ð»Ð¾Ð¼", "Ð²Ð¾Ñ€Ð´", "word", "Ñ‚ÐµÐºÑÑ‚", "Ð¿Ñ€ÐµÐ·ÐµÐ½Ñ‚Ð°Ñ†Ð¸ÑŽ", "Ñ‚Ð°Ð±Ð»Ð¸Ñ†Ñƒ",
        "Ð²Ð¸Ð´ÐµÐ¾", "Ñ„Ð¸Ð»ÑŒÐ¼", "ÐºÐ»Ð¸Ð¿", "Ð¼ÑƒÐ·Ñ‹ÐºÑƒ", "Ð¿ÐµÑÐ½ÑŽ", "Ð°ÑƒÐ´Ð¸Ð¾", "Ñ‚Ñ€ÐµÐº",
    }
    return [w for w in words if w and w not in _STOP and w not in type_words and len(w) >= 2]


def _search_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home / "Desktop", home / "Documents", home / "Downloads",
        home / "Pictures", home / "Videos", home / "Music",
        home / "OneDrive" / "Desktop", home / "OneDrive" / "Documents",
        home / "OneDrive" / "Pictures",
    ]
    return [root for root in roots if root.is_dir()]


_FOLDER_HINTS = {
    "Ð·Ð°Ð³Ñ€ÑƒÐ·Ðº": "Downloads",
    "Ð´Ð¾ÐºÑƒÐ¼ÐµÐ½Ñ‚": "Documents",
    "Ñ€Ð°Ð±Ð¾Ñ‡Ð¸Ð¹ ÑÑ‚Ð¾Ð»": "Desktop",
    "ÐºÐ°Ñ€Ñ‚Ð¸Ð½Ðº": "Pictures",
    "Ð¸Ð·Ð¾Ð±Ñ€Ð°Ð¶ÐµÐ½": "Pictures",
    "Ð²Ð¸Ð´ÐµÐ¾": "Videos",
    "Ð¼ÑƒÐ·Ñ‹Ðº": "Music",
}


def _folder_shortcut(query: str) -> Path | None:
    if "Ð¿Ð°Ð¿Ðº" not in query and query.strip() not in {"Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ¸", "Ð´Ð¾ÐºÑƒÐ¼ÐµÐ½Ñ‚Ñ‹", "Ñ€Ð°Ð±Ð¾Ñ‡Ð¸Ð¹ ÑÑ‚Ð¾Ð»", "ÐºÐ°Ñ€Ñ‚Ð¸Ð½ÐºÐ¸", "Ð²Ð¸Ð´ÐµÐ¾", "Ð¼ÑƒÐ·Ñ‹ÐºÐ°"}:
        return None
    home = Path.home()
    for hint, folder in _FOLDER_HINTS.items():
        if hint in query:
            candidate = home / folder
            if candidate.is_dir():
                return candidate
    return None


_DANGEROUS_OPEN_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".ps1",
    ".vbs", ".js", ".jse", ".wsf", ".msi", ".dll",
    ".py", ".pyw", ".sh",
}


def _open_path(path: Path) -> str:
    if path.suffix.lower() in _DANGEROUS_OPEN_EXTENSIONS and not path.is_dir():
        return f"ÐžÑ‚ÐºÐ°Ð·Ð°Ð½Ð¾: {path.name} â€” potentially dangerous file type."
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
        return f"ÐžÑ‚ÐºÑ€Ñ‹Ð» {'Ð¿Ð°Ð¿ÐºÑƒ' if path.is_dir() else 'Ñ„Ð°Ð¹Ð»'}: {path.name}"
    except OSError as exc:
        return f"ÐÐ°ÑˆÑ‘Ð» {path}, Ð½Ð¾ Ð¾Ñ‚ÐºÑ€Ñ‹Ñ‚ÑŒ Ð½Ðµ ÑÐ¼Ð¾Ð³: {exc}"


def search_and_open_file(query: str) -> str:
    _audit(f"search_and_open_file: {query!r}")
    query_lower = (query or "").lower().replace("Ñ‘", "Ðµ")

    shortcut = _folder_shortcut(query_lower)
    if shortcut is not None:
        return _open_path(shortcut)

    ext_filter = _file_type_filter(query_lower)
    keywords = _keywords(query_lower)
    want_folder = "Ð¿Ð°Ð¿Ðº" in query_lower
    deadline = time.monotonic() + _TIME_BUDGET_SEC
    scanned = 0
    best: tuple[int, float, Path] | None = None

    for root in _search_roots():
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            if time.monotonic() > deadline or scanned > _MAX_SCANNED:
                break
            directory, depth = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                scanned += 1
                if scanned > _MAX_SCANNED or time.monotonic() > deadline:
                    break
                name = entry.name
                if name.startswith(".") or name.startswith("~$"):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if name.lower() in _SKIP_DIRS:
                        continue
                    if want_folder and keywords and all(k in str(entry.path).lower() for k in keywords):
                        try:
                            mtime = entry.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            mtime = 0.0
                        candidate = (0, -mtime, str(entry.path))
                        if best is None or candidate < best:
                            best = candidate
                    if depth < _MAX_DEPTH:
                        stack.append((Path(entry.path), depth + 1))
                    continue

                name_lower = name.lower()
                if ext_filter and not name_lower.endswith(ext_filter):
                    continue
                if keywords:
                    if all(k in name_lower for k in keywords):
                        score = 0
                    elif any(k in name_lower for k in keywords):
                        score = 1
                    elif any(k in str(entry.path).lower() for k in keywords):
                        score = 3
                    else:
                        continue
                else:
                    score = 0
                try:
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                candidate = (score, -mtime, str(entry.path))
                if best is None or candidate < best:
                    best = candidate

    if best is None:
        return f"Ð¯ Ð½Ðµ ÑÐ¼Ð¾Ð³ Ð½Ð°Ð¹Ñ‚Ð¸ Ñ„Ð°Ð¹Ð» Ð¿Ð¾ Ð·Ð°Ð¿Ñ€Ð¾ÑÑƒ '{query}'."
    return _open_path(Path(best[2]))


# --------------------------------------------------------------------------
# ÑÐ°Ð¹Ñ‚Ñ‹ / ÑÐ¸ÑÑ‚ÐµÐ¼Ð°
# --------------------------------------------------------------------------
def open_website(url: str) -> str:
    url = (url or "").strip()
    if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE) and not re.match(r"^https?://", url, re.IGNORECASE):
        return "ÐžÑ‚ÐºÑ€Ñ‹Ð²Ð°ÑŽ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ http(s)-ÑÑÑ‹Ð»ÐºÐ¸."
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url.lstrip("/")
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return "ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ð°Ñ ÑÑÑ‹Ð»ÐºÐ°."
    _audit(f"open_website: {url}")
    hint = ""
    try:
        from hands import browser as browser_mod

        if not browser_mod.dns_ok(parsed.netloc.split(":")[0]):
            hint = " (Ð´Ð¾Ð¼ÐµÐ½ Ð½Ðµ Ñ€ÐµÐ·Ð¾Ð»Ð²Ð¸Ñ‚ÑÑ â€” ÑÐ°Ð¹Ñ‚ Ð¼Ð¾Ð¶ÐµÑ‚ Ð±Ñ‹Ñ‚ÑŒ Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½ Ð² ÑÑ‚Ð¾Ð¹ ÑÐµÑ‚Ð¸)"
    except Exception:  # noqa: BLE001
        pass
    try:
        # Ð’ÐºÐ»Ð°Ð´ÐºÐ° Ð² ÑƒÐ¶Ðµ Ð¾Ñ‚ÐºÑ€Ñ‹Ñ‚Ð¾Ð¼ Ð±Ñ€Ð°ÑƒÐ·ÐµÑ€Ðµ: Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ð¾Ð³Ð¾ Ð¾ÐºÐ½Ð° Jarvis Ð½Ðµ ÑÐ¾Ð·Ð´Ð°Ñ‘Ñ‚.
        webbrowser.open_new_tab(url)
        return f"ÐžÑ‚ÐºÑ€Ñ‹Ð» {url} Ð² Ð¾ÑÐ½Ð¾Ð²Ð½Ð¾Ð¼ Ð±Ñ€Ð°ÑƒÐ·ÐµÑ€Ðµ{hint}"
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¾Ñ‚ÐºÑ€Ñ‹Ñ‚ÑŒ {url}: {exc}"


def search_web(query: str) -> str:
    _audit(f"search_web: {query!r}")
    from hands import browser as browser_mod

    if not browser_mod.dns_ok("www.google.com"):
        # Google Ð¸Ð½Ð¾Ð³Ð´Ð° Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½ â€” Ñ‚Ð¾Ð³Ð´Ð° Ð¸Ñ‰ÐµÐ¼ Ð² Ð¯Ð½Ð´ÐµÐºÑÐµ, Ð¾Ð½ ÐµÑÑ‚ÑŒ Ð²ÑÐµÐ³Ð´Ð°.
        return open_website("https://yandex.ru/search/?text=" + urllib.parse.quote(query or ""))
    return open_website("https://www.google.com/search?q=" + urllib.parse.quote(query or ""))


def get_datetime(what: str = "both") -> str:
    what = str(what or "both").lower()
    if what == "time":
        return f"Ð¡ÐµÐ¹Ñ‡Ð°Ñ {time_text()}."
    if what == "date":
        return f"Ð¡ÐµÐ³Ð¾Ð´Ð½Ñ {date_text()}."
    return f"Ð¡ÐµÐ¹Ñ‡Ð°Ñ {both_text()}."


def system_action(action: str) -> str:
    action = str(action or "").lower()
    _audit(f"system_action: {action}")
    if action == "lock":
        try:
            ctypes.windll.user32.LockWorkStation()
            return "Ð‘Ð»Ð¾ÐºÐ¸Ñ€ÑƒÑŽ ÑÐºÑ€Ð°Ð½."
        except Exception as exc:  # noqa: BLE001
            return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ð±Ð»Ð¾ÐºÐ¸Ñ€Ð¾Ð²Ð°Ñ‚ÑŒ ÑÐºÑ€Ð°Ð½: {exc}"

    if action not in {"sleep", "shutdown", "restart"}:
        return f"ÐÐµÐ¸Ð·Ð²ÐµÑÑ‚Ð½Ð¾Ðµ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ðµ: {action}"

    titles = {"sleep": "ÑƒÑÑ‹Ð¿Ð¸Ñ‚ÑŒ ÐºÐ¾Ð¼Ð¿ÑŒÑŽÑ‚ÐµÑ€", "shutdown": "Ð²Ñ‹ÐºÐ»ÑŽÑ‡Ð¸Ñ‚ÑŒ ÐºÐ¾Ð¼Ð¿ÑŒÑŽÑ‚ÐµÑ€", "restart": "Ð¿ÐµÑ€ÐµÐ·Ð°Ð³Ñ€ÑƒÐ·Ð¸Ñ‚ÑŒ ÐºÐ¾Ð¼Ð¿ÑŒÑŽÑ‚ÐµÑ€"}
    if not _confirm(f"ÐŸÐ¾Ð´Ñ‚Ð²ÐµÑ€Ð´Ð¸: {titles[action]}?"):
        return "ÐžÑ‚Ð¼ÐµÐ½Ð¸Ð»."

    try:
        if action == "sleep":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], close_fds=True)
            return "Ð£ÑÑ‹Ð¿Ð»ÑÑŽ ÐºÐ¾Ð¼Ð¿ÑŒÑŽÑ‚ÐµÑ€."
        if action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "5"], close_fds=True)
            return "Ð’Ñ‹ÐºÐ»ÑŽÑ‡Ñƒ Ñ‡ÐµÑ€ÐµÐ· 5 ÑÐµÐºÑƒÐ½Ð´."
        subprocess.Popen(["shutdown", "/r", "/t", "5"], close_fds=True)
        return "ÐŸÐµÑ€ÐµÐ·Ð°Ð³Ñ€ÑƒÐ¶Ñƒ Ñ‡ÐµÑ€ÐµÐ· 5 ÑÐµÐºÑƒÐ½Ð´."
    except OSError as exc:
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð²Ñ‹Ð¿Ð¾Ð»Ð½Ð¸Ñ‚ÑŒ {action}: {exc}"


# --------------------------------------------------------------------------
# Ð·Ñ€ÐµÐ½Ð¸Ðµ, Ð±Ñ€Ð°ÑƒÐ·ÐµÑ€, Discord/Steam, ÑƒÐ¿Ñ€Ð°Ð²Ð»ÐµÐ½Ð¸Ðµ ÑÐºÑ€Ð°Ð½Ð¾Ð¼
# --------------------------------------------------------------------------
_browser = None


def _get_browser():
    global _browser
    if _browser is None:
        from hands.browser import BrowserSession

        _browser = BrowserSession()
    return _browser


def _browser_call(method: str, *args) -> str:
    try:
        return getattr(_get_browser(), method)(*args)
    except Exception as exc:  # noqa: BLE001
        log.warning("Ð‘Ñ€Ð°ÑƒÐ·ÐµÑ€: %s", exc)
        return f"Ð‘Ñ€Ð°ÑƒÐ·ÐµÑ€ Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½: {exc}"


def see_screen(question: str = "Ð§Ñ‚Ð¾ Ð½Ð° ÑÐºÑ€Ð°Ð½Ðµ?") -> str:
    _audit("see_screen")
    from eyes.screen import analyze_screen

    return analyze_screen(question or "Ð§Ñ‚Ð¾ Ð½Ð° ÑÐºÑ€Ð°Ð½Ðµ?")


def browser_open(url: str) -> str:
    _audit(f"browser_open: {url!r}")
    return _browser_call("open", url)


def browser_read() -> str:
    _audit("browser_read")
    return _browser_call("read")


def browser_click(text: str) -> str:
    _audit(f"browser_click: {text!r}")
    lowered = (text or "").lower()
    dangerous = (
        "Ñ€ÐµÐ³Ð¸ÑÑ‚Ñ€Ð°Ñ†", "Ð·Ð°Ñ€ÐµÐ³Ð¸ÑÑ‚Ñ€", "sign up", "register", "submit", "Ð¾Ñ‚Ð¿Ñ€Ð°Ð²", "Ð¾Ð¿Ð»Ð°Ñ‚",
        "ÐºÑƒÐ¿Ð¸Ñ‚ÑŒ", "buy", "pay", "delete", "ÑƒÐ´Ð°Ð»", "Ð¿Ð¾Ð´Ñ‚Ð²ÐµÑ€Ð´", "ÑÐ¾Ð·Ð´Ð°Ñ‚ÑŒ", "Ð¿Ñ€Ð¾Ð´Ð¾Ð»Ð¶Ð¸Ñ‚ÑŒ",
    )
    if any(word in lowered for word in dangerous) and not _confirm(f"ÐÐ°Ð¶Ð°Ñ‚ÑŒ Â«{text}Â»?"):
        return "ÐžÑ‚Ð¼ÐµÐ½Ð¸Ð»."
    return _browser_call("click", text)


def browser_fill(field: str, value: str) -> str:
    _audit(f"browser_fill: {field!r} (Ð·Ð½Ð°Ñ‡ÐµÐ½Ð¸Ðµ ÑÐºÑ€Ñ‹Ñ‚Ð¾)")
    return _browser_call("fill", field, value)


def browser_play_youtube(query: str) -> str:
    _audit(f"browser_play_youtube: {query!r}")
    return _browser_call("play_youtube", query)


def browser_back() -> str:
    _audit("browser_back")
    return _browser_call("back")


def _focus_window_object(title: str):
    import pygetwindow as gw

    needle = (title or "").lower()
    for window in gw.getAllWindows():
        if window.visible and needle in (window.title or "").lower():
            try:
                if window.isMinimized:
                    window.restore()
                window.activate()
            except Exception:  # noqa: BLE001
                pass
            return window
    return None


def focus_window(title: str) -> str:
    _audit(f"focus_window: {title!r}")
    try:
        window = _focus_window_object(title)
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¿ÐµÑ€ÐµÐºÐ»ÑŽÑ‡Ð¸Ñ‚ÑŒ Ð¾ÐºÐ½Ð¾: {exc}"
    if window is None:
        return f"ÐžÐºÐ½Ð¾ Â«{title}Â» Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾."
    time.sleep(0.3)
    return f"Ð’ Ñ„Ð¾ÐºÑƒÑÐµ: {window.title}"


def click_screen(x: int, y: int) -> str:
    _audit(f"click_screen: {x},{y}")
    if config.DESKTOP_CONFIRM and not _confirm(f"ÐÐ°Ð¶Ð°Ñ‚ÑŒ Ð² Ñ‚Ð¾Ñ‡ÐºÐµ {x}, {y}?"):
        return "ÐžÑ‚Ð¼ÐµÐ½Ð¸Ð»."
    try:
        import pyautogui

        width, height = pyautogui.size()
        x = max(0, min(width - 1, int(x)))
        y = max(0, min(height - 1, int(y)))
        pyautogui.click(x, y)
        return f"ÐÐ°Ð¶Ð°Ð» Ð² Ñ‚Ð¾Ñ‡ÐºÐµ {x}, {y}."
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð½Ð°Ð¶Ð°Ñ‚ÑŒ: {exc}"


def click_text_on_screen(text: str) -> str:
    """ÐÐ°Ð¹Ñ‚Ð¸ Ñ‚ÐµÐºÑÑ‚/ÐºÐ½Ð¾Ð¿ÐºÑƒ Ð½Ð° ÑÐºÑ€Ð°Ð½Ðµ Ñ‡ÐµÑ€ÐµÐ· OCR Ð¸ ÐºÐ»Ð¸ÐºÐ½ÑƒÑ‚ÑŒ Ð¿Ð¾ Ñ†ÐµÐ½Ñ‚Ñ€Ñƒ."""
    _audit(f"click_text_on_screen: {text!r}")
    try:
        from eyes.screen import find_on_screen

        found = find_on_screen(text)
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð½Ð°Ð¹Ñ‚Ð¸ Ð½Ð° ÑÐºÑ€Ð°Ð½Ðµ: {exc}"
    if not found:
        return f"ÐÐµ Ð½Ð°ÑˆÑ‘Ð» Â«{text}Â» Ð½Ð° ÑÐºÑ€Ð°Ð½Ðµ."
    x, y, matched, image_width, image_height = found
    if config.DESKTOP_CONFIRM and not _confirm(f"ÐÐ°Ð¶Ð°Ñ‚ÑŒ Â«{matched}Â» Ð½Ð° ÑÐºÑ€Ð°Ð½Ðµ?"):
        return "ÐžÑ‚Ð¼ÐµÐ½Ð¸Ð»."
    try:
        from eyes import screen as screen_mod

        with contextlib.suppress(Exception):  # noqa: BLE001
            screen_mod._ensure_dpi_aware()
        import pyautogui

        # ÐšÐ¾Ð¾Ñ€Ð´Ð¸Ð½Ð°Ñ‚Ñ‹ ÑƒÐ¶Ðµ Ð² ÐºÐ¾Ð¾Ñ€Ð´Ð¸Ð½Ð°Ñ‚Ð°Ñ… ÑÐºÑ€Ð°Ð½Ð° (ImageGrab + offset). ÐÐ° Ð½ÐµÑÐºÐ¾Ð»ÑŒÐºÐ¸Ñ…
        # Ð¼Ð¾Ð½Ð¸Ñ‚Ð¾Ñ€Ð°Ñ… Ð¼Ð°ÑÑˆÑ‚Ð°Ð±Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¸Ðµ Ðº Ð¾ÑÐ½Ð¾Ð²Ð½Ð¾Ð¼Ñƒ Ñ€Ð°Ð·Ñ€ÐµÑˆÐµÐ½Ð¸ÑŽ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÑÐ±Ð¸Ð²Ð°ÐµÑ‚ ÐºÐ»Ð¸Ðº;
        # Ð¿Ð¾ÑÑ‚Ð¾Ð¼Ñƒ ÐºÐ»Ð¸ÐºÐ°ÐµÐ¼ Ñ€Ð¾Ð²Ð½Ð¾ Ð¿Ð¾ Ð¿Ð¾Ð»ÑƒÑ‡ÐµÐ½Ð½Ñ‹Ð¼ point.
        pyautogui.click(int(x), int(y))
        return f"ÐÐ°Ð¶Ð°Ð» Â«{matched}Â» ({x}, {y})."
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð½Ð°Ð¶Ð°Ñ‚ÑŒ Â«{matched}Â»: {exc}"


def type_text(text: str) -> str:
    _audit("type_text")
    preview = (text or "")[:60]
    if config.DESKTOP_CONFIRM and not _confirm(f"Ð’Ð²ÐµÑÑ‚Ð¸ Ñ‚ÐµÐºÑÑ‚: Â«{preview}Â»?"):
        return "ÐžÑ‚Ð¼ÐµÐ½Ð¸Ð»."
    try:
        import pyautogui
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        return "Ð’Ð²Ñ‘Ð» Ñ‚ÐµÐºÑÑ‚ Ð² Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾Ðµ Ð¾ÐºÐ½Ð¾."
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð²Ð²ÐµÑÑ‚Ð¸ Ñ‚ÐµÐºÑÑ‚: {exc}"


def press_keys(keys: str) -> str:
    _audit(f"press_keys: {keys!r}")
    if config.DESKTOP_CONFIRM and not _confirm(f"ÐÐ°Ð¶Ð°Ñ‚ÑŒ {keys}?"):
        return "ÐžÑ‚Ð¼ÐµÐ½Ð¸Ð»."
    try:
        import pyautogui

        parts = [part.strip() for part in str(keys).split("+") if part.strip()]
        if not parts:
            return "ÐÐµ ÑƒÐºÐ°Ð·Ð°Ð½Ñ‹ ÐºÐ»Ð°Ð²Ð¸ÑˆÐ¸."
        pyautogui.hotkey(*parts)
        return f"ÐÐ°Ð¶Ð°Ð» {keys}."
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð½Ð°Ð¶Ð°Ñ‚ÑŒ {keys}: {exc}"


def discord_action(action: str) -> str:
    action = str(action or "").lower()
    _audit(f"discord_action: {action}")
    if action in {"open", "launch"}:
        return launch_app("discord")
    if action == "focus":
        return focus_window("Discord")
    hotkeys = {
        "mute": ("ctrl", "shift", "m"),
        "unmute": ("ctrl", "shift", "m"),
        "deafen": ("ctrl", "shift", "d"),
        "undeafen": ("ctrl", "shift", "d"),
    }
    if action not in hotkeys:
        return f"ÐÐµÐ¸Ð·Ð²ÐµÑÑ‚Ð½Ð¾Ðµ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ðµ Discord: {action}"
    if _focus_window_object("Discord") is None:
        return "Discord Ð½Ðµ Ð·Ð°Ð¿ÑƒÑ‰ÐµÐ½."
    try:
        import pyautogui

        time.sleep(0.3)
        pyautogui.hotkey(*hotkeys[action])
        return f"Discord: {action} (Ð¿ÐµÑ€ÐµÐºÐ»ÑŽÑ‡ÐµÐ½Ð¾)."
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ ÑƒÐ¿Ñ€Ð°Ð²Ð»ÑÑ‚ÑŒ Discord: {exc}"


def discord_send(channel: str, message: str) -> str:
    _audit(f"discord_send: ÐºÐ°Ð½Ð°Ð»={channel!r}")
    if not channel or not message:
        return "ÐÑƒÐ¶Ð½Ñ‹ ÐºÐ°Ð½Ð°Ð» Ð¸ Ñ‚ÐµÐºÑÑ‚ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ñ."
    if not _confirm(f"ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð¸Ñ‚ÑŒ Ð² Discord Â«{channel}Â»: Â«{message[:80]}Â»?"):
        return "ÐžÑ‚Ð¼ÐµÐ½Ð¸Ð»."
    if _focus_window_object("Discord") is None:
        return "Discord Ð½Ðµ Ð·Ð°Ð¿ÑƒÑ‰ÐµÐ½."
    try:
        import pyautogui
        import pyperclip

        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "k")
        time.sleep(0.5)
        pyperclip.copy(channel)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.6)
        pyautogui.press("enter")
        time.sleep(0.7)
        pyperclip.copy(message)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.2)
        pyautogui.press("enter")
        return f"ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð¸Ð» Ð² Â«{channel}Â» (Ð¿Ñ€Ð¾Ð²ÐµÑ€ÑŒ, Ñ‡Ñ‚Ð¾ ÐºÐ°Ð½Ð°Ð» Ð²Ñ‹Ð±Ñ€Ð°Ð½ Ð²ÐµÑ€Ð½Ð¾)."
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¾Ñ‚Ð¿Ñ€Ð°Ð²Ð¸Ñ‚ÑŒ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ: {exc}"


def steam_action(action: str) -> str:
    action = str(action or "").lower()
    _audit(f"steam_action: {action}")
    if action in {"open", "launch", "client"}:
        return launch_app("steam")
    uris = {
        "store": "steam://store",
        "library": "steam://nav/games",
        "friends": "steam://open/friends",
        "downloads": "steam://open/downloads",
        "settings": "steam://open/settings",
        "profile": "steam://open/profile",
    }
    uri = uris.get(action)
    if not uri:
        return f"ÐÐµÐ¸Ð·Ð²ÐµÑÑ‚Ð½Ð¾Ðµ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ðµ Steam: {action}"
    try:
        os.startfile(uri)
        return f"Steam: {action}."
    except OSError as exc:
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¾Ñ‚ÐºÑ€Ñ‹Ñ‚ÑŒ Steam: {exc}"


def shutdown() -> None:
    """Ð—Ð°ÐºÑ€Ñ‹Ñ‚ÑŒ Ð±Ñ€Ð°ÑƒÐ·ÐµÑ€ Ð¸ Ð¾ÑÐ²Ð¾Ð±Ð¾Ð´Ð¸Ñ‚ÑŒ Ñ€ÐµÑÑƒÑ€ÑÑ‹."""
    global _browser
    if _browser is not None:
        with contextlib.suppress(Exception):  # noqa: BLE001
            _browser.close()
        _browser = None


# --------------------------------------------------------------------------
# Ð·Ð°Ð¼ÐµÑ‚ÐºÐ¸, Ð½Ð°Ð¿Ð¾Ð¼Ð¸Ð½Ð°Ð½Ð¸Ñ, Ð±ÑƒÑ„ÐµÑ€ Ð¾Ð±Ð¼ÐµÐ½Ð°, ÑÐºÑ€Ð¸Ð½ÑˆÐ¾Ñ‚
# --------------------------------------------------------------------------
def save_note(text: str) -> str:
    """Ð—Ð°Ð¿Ð¸ÑÐ°Ñ‚ÑŒ Ð·Ð°Ð¼ÐµÑ‚ÐºÑƒ Ð² memory/notes.md Ñ Ð´Ð°Ñ‚Ð¾Ð¹."""
    text = (text or "").strip()
    if not text:
        return "ÐÐµ ÑƒÐºÐ°Ð·Ð°Ð½ Ñ‚ÐµÐºÑÑ‚ Ð·Ð°Ð¼ÐµÑ‚ÐºÐ¸."
    _audit(f"save_note: {text[:60]!r}")
    try:
        config.NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        with open(config.NOTES_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"- [{stamp}] {text}\n")
    except OSError as exc:
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð·Ð°Ð¿Ð¸ÑÐ°Ñ‚ÑŒ Ð·Ð°Ð¼ÐµÑ‚ÐºÑƒ: {exc}"
    return f"Ð—Ð°Ð¿Ð¸ÑÐ°Ð» Ð·Ð°Ð¼ÐµÑ‚ÐºÑƒ: {text[:120]}"


def read_notes(limit: int = 5) -> str:
    """ÐŸÑ€Ð¾Ñ‡Ð¸Ñ‚Ð°Ñ‚ÑŒ Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ Ð·Ð°Ð¼ÐµÑ‚ÐºÐ¸."""
    try:
        lines = [line.strip() for line in
                 config.NOTES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return "Ð—Ð°Ð¼ÐµÑ‚Ð¾Ðº Ð¿Ð¾ÐºÐ° Ð½ÐµÑ‚."
    if not lines:
        return "Ð—Ð°Ð¼ÐµÑ‚Ð¾Ðº Ð¿Ð¾ÐºÐ° Ð½ÐµÑ‚."
    last = lines[-max(1, min(20, int(limit or 5))):]
    return "ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ Ð·Ð°Ð¼ÐµÑ‚ÐºÐ¸:\n" + "\n".join(last)


def set_reminder(when: str, text: str) -> str:
    """ÐÐ°Ð¿Ð¾Ð¼Ð¸Ð½Ð°Ð½Ð¸Ðµ: when â€” Â«Ñ‡ÐµÑ€ÐµÐ· 15 Ð¼Ð¸Ð½ÑƒÑ‚Â», Â«18:30Â», Â«Ð² 7 ÑƒÑ‚Ñ€Ð°Â»."""
    _audit(f"set_reminder: {when!r} â†’ {text[:60]!r}")
    from hands import reminders

    text = (text or "").strip()
    if not text:
        return "ÐÐµ ÑƒÐºÐ°Ð·Ð°Ð½Ð¾, Ð¾ Ñ‡Ñ‘Ð¼ Ð½Ð°Ð¿Ð¾Ð¼Ð½Ð¸Ñ‚ÑŒ."
    delay = reminders.parse_delay(when)
    if delay is None:
        return f"ÐÐµ Ð¿Ð¾Ð½ÑÐ» Ð²Ñ€ÐµÐ¼Ñ Â«{when}Â». Ð¡ÐºÐ°Ð¶Ð¸, Ð½Ð°Ð¿Ñ€Ð¸Ð¼ÐµÑ€: Â«Ñ‡ÐµÑ€ÐµÐ· 15 Ð¼Ð¸Ð½ÑƒÑ‚Â» Ð¸Ð»Ð¸ Â«Ð² 18:30Â»."
    if delay > 7 * 86400:
        return "Ð¡Ð»Ð¸ÑˆÐºÐ¾Ð¼ Ð´Ð°Ð»Ñ‘ÐºÐ¾Ðµ Ð²Ñ€ÐµÐ¼Ñ â€” Ð¿Ð¾ÑÑ‚Ð°Ð²Ð»ÑŽ Ð½Ðµ Ð±Ð¾Ð»ÑŒÑˆÐµ, Ñ‡ÐµÐ¼ Ð½Ð° Ð½ÐµÐ´ÐµÐ»ÑŽ Ð²Ð¿ÐµÑ€Ñ‘Ð´."
    reminders.schedule(delay, text)
    return f"ÐÐ°Ð¿Ð¾Ð¼Ð½ÑŽ Ñ‡ÐµÑ€ÐµÐ· {reminders.format_delay(delay)}: {text}"


def read_clipboard() -> str:
    """ÐŸÑ€Ð¾Ñ‡Ð¸Ñ‚Ð°Ñ‚ÑŒ Ñ‚ÐµÐºÑÑ‚ Ð¸Ð· Ð±ÑƒÑ„ÐµÑ€Ð° Ð¾Ð±Ð¼ÐµÐ½Ð° (Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð¿Ð¾ ÑÐ²Ð½Ð¾Ð¹ Ð¿Ñ€Ð¾ÑÑŒÐ±Ðµ)."""
    _audit("read_clipboard")
    try:
        import pyperclip

        value = (pyperclip.paste() or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"Ð‘ÑƒÑ„ÐµÑ€ Ð¾Ð±Ð¼ÐµÐ½Ð° Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½: {exc}"
    if not value:
        return "Ð‘ÑƒÑ„ÐµÑ€ Ð¾Ð±Ð¼ÐµÐ½Ð° Ð¿ÑƒÑÑ‚."
    return f"Ð’ Ð±ÑƒÑ„ÐµÑ€Ðµ Ð¾Ð±Ð¼ÐµÐ½Ð°: {value[:600]}"


def play_music(query: str = "") -> str:
    """Ð’ÐºÐ»ÑŽÑ‡Ð¸Ñ‚ÑŒ Ñ‚Ñ€ÐµÐº/Ð¸ÑÐ¿Ð¾Ð»Ð½Ð¸Ñ‚ÐµÐ»Ñ: Ð»Ð¾ÐºÐ°Ð»ÑŒÐ½Ñ‹Ð¹ Ñ„Ð°Ð¹Ð» Ð¸Ð»Ð¸ Ð¿Ð¾Ð¸ÑÐº Ð² Ð¾ÑÐ½Ð¾Ð²Ð½Ð¾Ð¼ Ð±Ñ€Ð°ÑƒÐ·ÐµÑ€Ðµ."""
    _audit(f"play_music: {query!r}")
    try:
        from hands import music

        return music.play_query(query or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("play_music: %s", exc)
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð²ÐºÐ»ÑŽÑ‡Ð¸Ñ‚ÑŒ Ð¼ÑƒÐ·Ñ‹ÐºÑƒ: {exc}"


def take_screenshot() -> str:
    """Ð¡Ð¾Ñ…Ñ€Ð°Ð½Ð¸Ñ‚ÑŒ ÑÐºÑ€Ð¸Ð½ÑˆÐ¾Ñ‚ ÑÐºÑ€Ð°Ð½Ð° Ð² assets/ Ð¸ ÑÐºÐ°Ð·Ð°Ñ‚ÑŒ Ð¿ÑƒÑ‚ÑŒ."""
    _audit("take_screenshot")
    try:
        from eyes.screen import capture_screen

        path, size, _offset = capture_screen()
        return f"Ð¡ÐºÑ€Ð¸Ð½ÑˆÐ¾Ñ‚ ÑÐ¾Ñ…Ñ€Ð°Ð½Ñ‘Ð½: {path} ({size[0]}x{size[1]})"
    except Exception as exc:  # noqa: BLE001
        return f"ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ ÑÐ´ÐµÐ»Ð°Ñ‚ÑŒ ÑÐºÑ€Ð¸Ð½ÑˆÐ¾Ñ‚: {exc}"


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
DISPATCH = {
    "launch_app": launch_app,
    "close_app": close_app,
    "open_website": open_website,
    "control_volume": control_volume,
    "media_control": media_control,
    "search_web": search_web,
    "system_action": system_action,
    "search_and_open_file": search_and_open_file,
    "get_datetime": get_datetime,
    "minimize_all": minimize_all,
    "see_screen": see_screen,
    "browser_open": browser_open,
    "browser_read": browser_read,
    "browser_click": browser_click,
    "browser_fill": browser_fill,
    "browser_play_youtube": browser_play_youtube,
    "browser_back": browser_back,
    "focus_window": focus_window,
    "click_screen": click_screen,
    "click_text_on_screen": click_text_on_screen,
    "type_text": type_text,
    "press_keys": press_keys,
    "discord_action": discord_action,
    "discord_send": discord_send,
    "steam_action": steam_action,
    "play_music": play_music,
    "save_note": save_note,
    "read_notes": read_notes,
    "set_reminder": set_reminder,
    "read_clipboard": read_clipboard,
    "take_screenshot": take_screenshot,
}


_FAILURE_PREFIXES = (
    "Ð½Ðµ Ð½Ð°ÑˆÑ‘Ð»", "Ð½Ðµ Ð½Ð°ÑˆÐ»Ð°", "Ð½Ðµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ", "Ð½ÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½", "Ð½ÐµÐ¸Ð·Ð²ÐµÑÑ‚Ð½", "Ð½Ðµ ÑƒÐºÐ°Ð·Ð°Ð½",
    "ÑÐ±Ð¾Ð¹", "Ð¾ÑˆÐ¸Ð±ÐºÐ°", "Ð¿Ñ€Ð¾Ñ†ÐµÑÑ ", "Ð¾Ñ‚Ð¼ÐµÐ½Ð¸Ð»", "Ð½Ðµ Ð¼Ð¾Ð³Ñƒ", "Ð½Ðµ Ñ…Ð²Ð°Ñ‚Ð°ÐµÑ‚", "Ð½ÐµÑ‚ ",
)


def _looks_failed(text: str) -> bool:
    lowered = text.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _FAILURE_PREFIXES)


def _run_tool(name: str, args: dict) -> tuple[str, bool]:
    function = DISPATCH.get(name)
    if function is None:
        return f"Ð¤ÑƒÐ½ÐºÑ†Ð¸Ñ '{name}' Ð½Ðµ Ð² Ð±ÐµÐ»Ð¾Ð¼ ÑÐ¿Ð¸ÑÐºÐµ â€” Ð¾Ñ‚ÐºÐ°Ð·.", False
    if not isinstance(args, dict):
        return f"ÐÑ€Ð³ÑƒÐ¼ÐµÐ½Ñ‚Ñ‹ Ð´Ð»Ñ {name} Ð´Ð¾Ð»Ð¶Ð½Ñ‹ Ð±Ñ‹Ñ‚ÑŒ Ð¾Ð±ÑŠÐµÐºÑ‚Ð¾Ð¼.", False

    signature = inspect.signature(function)
    accepted = {key: value for key, value in args.items() if key in signature.parameters}
    missing = [
        param_name
        for param_name, param in signature.parameters.items()
        if param.default is inspect.Parameter.empty and param_name not in accepted
    ]
    if missing:
        return f"ÐÐµ Ñ…Ð²Ð°Ñ‚Ð°ÐµÑ‚ Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€Ð¾Ð² Ð´Ð»Ñ {name}: {', '.join(missing)}.", False

    if config.env_bool("JARVIS_DRY_RUN", False):
        return f"[dry-run] {name}({json.dumps(accepted, ensure_ascii=False)})", True

    try:
        result = str(function(**accepted))
        return result, not _looks_failed(result)
    except Exception as exc:  # noqa: BLE001
        log.exception("Ð¡Ð±Ð¾Ð¹ Ð¸ÑÐ¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ñ %s", name)
        return f"Ð¡Ð±Ð¾Ð¹ Ð¸ÑÐ¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ñ {name}: {exc}", False


def execute_verbose(intent: dict | None, confirm=None, dry_run: bool | None = None) -> tuple[str, bool]:
    if confirm is not None:
        set_confirm_handler(confirm)
    if dry_run is not None:
        os.environ["JARVIS_DRY_RUN"] = "1" if dry_run else "0"
    if not isinstance(intent, dict):
        return "ÐÐµÐ¿Ð¾Ð½ÑÑ‚Ð½Ñ‹Ð¹ Ð¸Ð½Ñ‚ÐµÐ½Ñ‚.", False

    kind = intent.get("type")
    if kind == "chat":
        return intent.get("reply", ""), True
    if kind == "error":
        log.warning("ÐžÑˆÐ¸Ð±ÐºÐ° Ð¼Ð¾Ð·Ð³Ð°: %s", intent.get("detail"))
        return "ÐœÐ¾Ð·Ð³ Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½. ÐŸÑ€Ð¾Ð²ÐµÑ€ÑŒ VPN Ð¸ ÐºÐ»ÑŽÑ‡Ð¸ API.", False
    if kind == "tool":
        return _run_tool(intent.get("name", ""), intent.get("args") or {})
    if kind == "tools":
        results: list[str] = []
        all_ok = True
        for call in intent.get("calls") or []:
            text, ok = _run_tool(call.get("name", ""), call.get("args") or {})
            results.append(text)
            all_ok = all_ok and ok
        return " ".join(r for r in results if r), all_ok
    return "ÐÐµÐ¿Ð¾Ð½ÑÑ‚Ð½Ñ‹Ð¹ Ð¸Ð½Ñ‚ÐµÐ½Ñ‚.", False


def execute(intent: dict) -> str:
    return execute_verbose(intent)[0]



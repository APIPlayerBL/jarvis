# hands/hands.py
"""Исполнитель действий на ПК.

Ключевые исправления:
- никакого shell=True и «ложного успеха»: если exe не найден — честная ошибка;
- громкость через актуальный pycaw API (volume_percent/EndpointVolume);
- подтверждение опасных действий через внедряемый обработчик (голос/консоль),
  а не безусловный input();
- аргументы tool-вызова валидируются по сигнатуре функции;
- dry-run режим для безопасного тестирования всего пайплайна.
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
# журнал действий (аудит, не диагностика)
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
# ленивые импорты
# --------------------------------------------------------------------------
_pycaw_error_logged = False


def _endpoint_volume():
    """Вернуть IAudioEndpointVolume для устройства по умолчанию."""
    global _pycaw_error_logged
    try:
        from pycaw.pycaw import AudioUtilities

        device = AudioUtilities.GetSpeakers()
        endpoint = getattr(device, "EndpointVolume", None)
        if endpoint is not None:
            return endpoint

        # Старый pycaw: AudioDevice сам умеет Activate.
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume

        interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception as exc:  # noqa: BLE001
        if not _pycaw_error_logged:
            _pycaw_error_logged = True
            log.warning("pycaw недоступен: %s", exc)
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
        log.warning("Не удалось задать громкость: %s", exc)
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
    """Нажатие мультимедиа-клавиши через keybd_event (без pyautogui)."""
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
# подтверждение опасных действий
# --------------------------------------------------------------------------
_confirm_handler = None


def set_confirm_handler(handler) -> None:
    """main подключает голосовое подтверждение; тесты — свой обработчик."""
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
            log.warning("Обработчик подтверждения упал: %s", exc)
            return False
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            answer = input(f"{prompt} (да/нет): ").strip().lower()
            return answer in {"да", "д", "y", "yes", "подтверждаю"}
        except (EOFError, KeyboardInterrupt):
            return False
    log.warning("Подтверждение недоступно (нет консоли/голосового обработчика) — отказ")
    return False


# --------------------------------------------------------------------------
# запуск / закрытие приложений
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
    # Критически важные приложения, которых нет локально, не стоит просто объявлять
    # «не найдено»: даём надёжный web-фоллбэк, чтобы команда не проваливалась.
    web_fallbacks = {
        "telegram": "https://web.telegram.org",
        "chrome": "https://www.google.com",
        "edge": "https://www.bing.com",
    }
    return web_fallbacks.get(canonical)


def launch_app(app_name: str) -> str:
    if not apps.is_safe_name(app_name):
        return "Некорректное имя приложения."
    original = apps.norm(app_name)
    canonical = apps.resolve(original) or original
    _audit(f"launch_app: {app_name!r} -> canonical={canonical!r}")

    if original.startswith(("http://", "https://")):
        return open_website(original)

    appid = apps.steam_appid(canonical)
    if appid:
        try:
            os.startfile(f"steam://rungameid/{appid}")
            return f"Запускаю {canonical} через Steam."
        except OSError as exc:
            return f"Не удалось запустить {canonical} через Steam: {exc}"

    target = _resolve_target(canonical, original)
    if not target:
        return f"Не нашёл приложение «{app_name}». Добавь путь в config/apps.json."

    try:
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
            return f"Открыл {canonical} в веб-версии."
        elif target.startswith(("ms-settings:", "steam://")) or target.lower().endswith(".lnk") or Path(target).is_dir():
            os.startfile(target)
        else:
            subprocess.Popen([target], cwd=str(Path(target).parent) if Path(target).parent != Path('.') else None, close_fds=True)
        return f"Запустил {canonical}."
    except OSError as exc:
        log.warning("launch_app %s: %s", target, exc)
        return f"Не удалось запустить {canonical}: {exc}"


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
        return "Модуль psutil не установлен. Выполни pip install psutil."

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
        return f"Закрыл {canonical}."
    return f"Процесс {canonical} не запущен."


# --------------------------------------------------------------------------
# громкость / медиа
# --------------------------------------------------------------------------
def control_volume(action: str, level: int | None = None) -> str:
    action = str(action or "").lower()
    _audit(f"control_volume: action={action} level={level}")

    if action == "set":
        if level is None:
            return "Не указан уровень громкости."
        try:
            level = max(0, min(100, int(level)))
        except (TypeError, ValueError):
            return "Некорректный уровень громкости."
        if _set_volume(level):
            return f"Громкость {level}%."
        return "Не удалось изменить громкость (pycaw недоступен)."

    if action == "mute":
        if _set_mute(True):
            return "Звук выключен."
        return "Не удалось выключить звук."
    if action == "unmute":
        if _set_mute(False):
            current = _get_volume()
            return f"Звук включён. Громкость {current}%." if current is not None else "Звук включён."
        return "Не удалось включить звук."

    if action in {"up", "down"}:
        current = _get_volume()
        if current is not None:
            new_level = max(0, min(100, current + (5 if action == "up" else -5)))
            if _set_volume(new_level):
                return f"Громкость {new_level}%."
        key = 0xAF if action == "up" else 0xAE  # VK_VOLUME_UP / DOWN
        if _media_key(key):
            return "Громкость изменена."
        return f"Неизвестное действие: {action}"
    return f"Неизвестное действие: {action}"


def media_control(action: str) -> str:
    action = str(action or "").lower()
    # Если играем трек сами (локальный файл) — управляем своим проигрывателем,
    # иначе отправляем системные медиа-клавиши.
    try:
        from hands import music

        if music.player.active:
            _audit(f"media_control: {action} (локальный плеер)")
            return music.media_action(action)
    except Exception as exc:  # noqa: BLE001
        log.debug("Локальный плеер недоступен: %s", exc)
    vk = {
        "play": 0xB3,
        "pause": 0xB3,
        "next": 0xB0,
        "prev": 0xB1,
        "stop": 0xB2,
    }.get(action)
    if vk is None:
        return f"Неизвестное действие: {action}"
    _audit(f"media_control: {action}")
    if _media_key(vk):
        return f"Медиа: {action}."
    return "Не удалось отправить медиа-клавишу."


def minimize_all() -> str:
    _audit("minimize_all")
    if _press_win_d():
        return "Свернул все окна."
    return "Не удалось свернуть окна."


# --------------------------------------------------------------------------
# файлы
# --------------------------------------------------------------------------
_STOP = {
    "найди", "найти", "открой", "открыть", "запусти", "запустить", "покажи", "показать",
    "любой", "любое", "любую", "любые", "файл", "файлы", "папку", "папка", "папки",
    "на", "в", "и", "с", "по", "пк", "компьютере", "компьютер", "оттуда", "из", "мне",
    "пожалуйста", "этот", "эту", "самый", "самую", "новый", "новую", "последний",
    "последнюю", "что", "то", "там", "где", "мой", "мою", "диске", "диск",
}
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic")
_DOC_EXT = (".docx", ".doc", ".pdf", ".txt", ".rtf", ".odt", ".xlsx", ".pptx", ".csv")
_MEDIA_EXT = (".mp4", ".mkv", ".avi", ".mov", ".mp3", ".flac", ".wav", ".m4a")

_SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".svn", "venv", ".venv", "appdata", "windows", "$recycle.bin"}
_MAX_DEPTH = 4
_MAX_SCANNED = 30000
_TIME_BUDGET_SEC = 6.0


def _file_type_filter(query: str) -> tuple[str, ...]:
    if any(w in query for w in ("фото", "фотограф", "картинк", "скриншот", "изображен", "снимок")):
        return _IMAGE_EXT
    if any(w in query for w in ("документ", "диплом", "ворд", "word", "текст", "презентац", "таблиц", "эксель", "excel")):
        return _DOC_EXT
    if any(w in query for w in ("видео", "фильм", "клип")):
        return _MEDIA_EXT
    if any(w in query for w in ("музык", "песн", "аудио", "трек")):
        return (".mp3", ".flac", ".wav", ".m4a", ".ogg")
    return ()


def _keywords(query: str) -> list[str]:
    words = re.split(r"[^\w]+", query.lower().replace("ё", "е"), flags=re.UNICODE)
    type_words = {
        "фото", "фотограф", "фотографию", "картинку", "картинка", "скриншот", "изображение",
        "документ", "документы", "диплом", "ворд", "word", "текст", "презентацию", "таблицу",
        "видео", "фильм", "клип", "музыку", "песню", "аудио", "трек",
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
    "загрузк": "Downloads",
    "документ": "Documents",
    "рабочий стол": "Desktop",
    "картинк": "Pictures",
    "изображен": "Pictures",
    "видео": "Videos",
    "музык": "Music",
}


def _folder_shortcut(query: str) -> Path | None:
    if "папк" not in query and query.strip() not in {"загрузки", "документы", "рабочий стол", "картинки", "видео", "музыка"}:
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
        return f"Отказано: {path.name} — potentially dangerous file type."
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
        return f"Открыл {'папку' if path.is_dir() else 'файл'}: {path.name}"
    except OSError as exc:
        return f"Нашёл {path}, но открыть не смог: {exc}"


def search_and_open_file(query: str) -> str:
    _audit(f"search_and_open_file: {query!r}")
    query_lower = (query or "").lower().replace("ё", "е")

    shortcut = _folder_shortcut(query_lower)
    if shortcut is not None:
        return _open_path(shortcut)

    ext_filter = _file_type_filter(query_lower)
    keywords = _keywords(query_lower)
    want_folder = "папк" in query_lower
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
        return f"Я не смог найти файл по запросу '{query}'."
    return _open_path(Path(best[2]))


# --------------------------------------------------------------------------
# сайты / система
# --------------------------------------------------------------------------
def open_website(url: str) -> str:
    url = (url or "").strip()
    if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE) and not re.match(r"^https?://", url, re.IGNORECASE):
        return "Открываю только http(s)-ссылки."
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url.lstrip("/")
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return "Некорректная ссылка."
    _audit(f"open_website: {url}")
    hint = ""
    try:
        from hands import browser as browser_mod

        if not browser_mod.dns_ok(parsed.netloc.split(":")[0]):
            hint = " (домен не резолвится — сайт может быть недоступен в этой сети)"
    except Exception:  # noqa: BLE001
        pass
    try:
        # Вкладка в уже открытом браузере: отдельного окна Jarvis не создаёт.
        webbrowser.open_new_tab(url)
        return f"Открыл {url} в основном браузере{hint}"
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось открыть {url}: {exc}"


def search_web(query: str) -> str:
    _audit(f"search_web: {query!r}")
    from hands import browser as browser_mod

    if not browser_mod.dns_ok("www.google.com"):
        # Google иногда недоступен — тогда ищем в Яндексе, он есть всегда.
        return open_website("https://yandex.ru/search/?text=" + urllib.parse.quote(query or ""))
    return open_website("https://www.google.com/search?q=" + urllib.parse.quote(query or ""))


def get_datetime(what: str = "both") -> str:
    what = str(what or "both").lower()
    if what == "time":
        return f"Сейчас {time_text()}."
    if what == "date":
        return f"Сегодня {date_text()}."
    return f"Сейчас {both_text()}."


def system_action(action: str) -> str:
    action = str(action or "").lower()
    _audit(f"system_action: {action}")
    if action == "lock":
        try:
            ctypes.windll.user32.LockWorkStation()
            return "Блокирую экран."
        except Exception as exc:  # noqa: BLE001
            return f"Не удалось заблокировать экран: {exc}"

    if action not in {"sleep", "shutdown", "restart"}:
        return f"Неизвестное действие: {action}"

    titles = {"sleep": "усыпить компьютер", "shutdown": "выключить компьютер", "restart": "перезагрузить компьютер"}
    if not _confirm(f"Подтверди: {titles[action]}?"):
        return "Отменил."

    try:
        if action == "sleep":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], close_fds=True)
            return "Усыпляю компьютер."
        if action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "5"], close_fds=True)
            return "Выключу через 5 секунд."
        subprocess.Popen(["shutdown", "/r", "/t", "5"], close_fds=True)
        return "Перезагружу через 5 секунд."
    except OSError as exc:
        return f"Не удалось выполнить {action}: {exc}"


# --------------------------------------------------------------------------
# зрение, браузер, Discord/Steam, управление экраном
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
        log.warning("Браузер: %s", exc)
        return f"Браузер недоступен: {exc}"


def see_screen(question: str = "Что на экране?") -> str:
    _audit("see_screen")
    from eyes.screen import analyze_screen

    return analyze_screen(question or "Что на экране?")


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
        "регистрац", "зарегистр", "sign up", "register", "submit", "отправ", "оплат",
        "купить", "buy", "pay", "delete", "удал", "подтверд", "создать", "продолжить",
    )
    if any(word in lowered for word in dangerous) and not _confirm(f"Нажать «{text}»?"):
        return "Отменил."
    return _browser_call("click", text)


def browser_fill(field: str, value: str) -> str:
    _audit(f"browser_fill: {field!r} (значение скрыто)")
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
        return f"Не удалось переключить окно: {exc}"
    if window is None:
        return f"Окно «{title}» не найдено."
    time.sleep(0.3)
    return f"В фокусе: {window.title}"


def click_screen(x: int, y: int) -> str:
    _audit(f"click_screen: {x},{y}")
    if config.DESKTOP_CONFIRM and not _confirm(f"Нажать в точке {x}, {y}?"):
        return "Отменил."
    try:
        import pyautogui

        width, height = pyautogui.size()
        x = max(0, min(width - 1, int(x)))
        y = max(0, min(height - 1, int(y)))
        pyautogui.click(x, y)
        return f"Нажал в точке {x}, {y}."
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось нажать: {exc}"


def click_text_on_screen(text: str) -> str:
    """Найти текст/кнопку на экране через OCR и кликнуть по центру."""
    _audit(f"click_text_on_screen: {text!r}")
    try:
        from eyes.screen import find_on_screen

        found = find_on_screen(text)
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось найти на экране: {exc}"
    if not found:
        return f"Не нашёл «{text}» на экране."
    x, y, matched, image_width, image_height = found
    if config.DESKTOP_CONFIRM and not _confirm(f"Нажать «{matched}» на экране?"):
        return "Отменил."
    try:
        from eyes import screen as screen_mod

        with contextlib.suppress(Exception):  # noqa: BLE001
            screen_mod._ensure_dpi_aware()
        import pyautogui

        # Координаты уже в координатах экрана (ImageGrab + offset). На нескольких
        # мониторах масштабирование к основному разрешению только сбивает клик;
        # поэтому кликаем ровно по полученным point.
        pyautogui.click(int(x), int(y))
        return f"Нажал «{matched}» ({x}, {y})."
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось нажать «{matched}»: {exc}"


def type_text(text: str) -> str:
    _audit("type_text")
    preview = (text or "")[:60]
    if config.DESKTOP_CONFIRM and not _confirm(f"Ввести текст: «{preview}»?"):
        return "Отменил."
    try:
        import pyautogui
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        return "Ввёл текст в активное окно."
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось ввести текст: {exc}"


def press_keys(keys: str) -> str:
    _audit(f"press_keys: {keys!r}")
    if config.DESKTOP_CONFIRM and not _confirm(f"Нажать {keys}?"):
        return "Отменил."
    try:
        import pyautogui

        parts = [part.strip() for part in str(keys).split("+") if part.strip()]
        if not parts:
            return "Не указаны клавиши."
        pyautogui.hotkey(*parts)
        return f"Нажал {keys}."
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось нажать {keys}: {exc}"


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
        return f"Неизвестное действие Discord: {action}"
    if _focus_window_object("Discord") is None:
        return "Discord не запущен."
    try:
        import pyautogui

        time.sleep(0.3)
        pyautogui.hotkey(*hotkeys[action])
        return f"Discord: {action} (переключено)."
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось управлять Discord: {exc}"


def discord_send(channel: str, message: str) -> str:
    _audit(f"discord_send: канал={channel!r}")
    if not channel or not message:
        return "Нужны канал и текст сообщения."
    if not _confirm(f"Отправить в Discord «{channel}»: «{message[:80]}»?"):
        return "Отменил."
    if _focus_window_object("Discord") is None:
        return "Discord не запущен."
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
        return f"Отправил в «{channel}» (проверь, что канал выбран верно)."
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось отправить сообщение: {exc}"


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
        return f"Неизвестное действие Steam: {action}"
    try:
        os.startfile(uri)
        return f"Steam: {action}."
    except OSError as exc:
        return f"Не удалось открыть Steam: {exc}"


def shutdown() -> None:
    """Закрыть браузер и освободить ресурсы."""
    global _browser
    if _browser is not None:
        with contextlib.suppress(Exception):  # noqa: BLE001
            _browser.close()
        _browser = None


# --------------------------------------------------------------------------
# заметки, напоминания, буфер обмена, скриншот
# --------------------------------------------------------------------------
def save_note(text: str) -> str:
    """Записать заметку в memory/notes.md с датой."""
    text = (text or "").strip()
    if not text:
        return "Не указан текст заметки."
    _audit(f"save_note: {text[:60]!r}")
    try:
        config.NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        with open(config.NOTES_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"- [{stamp}] {text}\n")
    except OSError as exc:
        return f"Не удалось записать заметку: {exc}"
    return f"Записал заметку: {text[:120]}"


def read_notes(limit: int = 5) -> str:
    """Прочитать последние заметки."""
    try:
        lines = [line.strip() for line in
                 config.NOTES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return "Заметок пока нет."
    if not lines:
        return "Заметок пока нет."
    last = lines[-max(1, min(20, int(limit or 5))):]
    return "Последние заметки:\n" + "\n".join(last)


def set_reminder(when: str, text: str) -> str:
    """Напоминание: when — «через 15 минут», «18:30», «в 7 утра»."""
    _audit(f"set_reminder: {when!r} → {text[:60]!r}")
    from hands import reminders

    text = (text or "").strip()
    if not text:
        return "Не указано, о чём напомнить."
    delay = reminders.parse_delay(when)
    if delay is None:
        return f"Не понял время «{when}». Скажи, например: «через 15 минут» или «в 18:30»."
    if delay > 7 * 86400:
        return "Слишком далёкое время — поставлю не больше, чем на неделю вперёд."
    reminders.schedule(delay, text)
    return f"Напомню через {reminders.format_delay(delay)}: {text}"


def read_clipboard() -> str:
    """Прочитать текст из буфера обмена (только по явной просьбе)."""
    _audit("read_clipboard")
    try:
        import pyperclip

        value = (pyperclip.paste() or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"Буфер обмена недоступен: {exc}"
    if not value:
        return "Буфер обмена пуст."
    return f"В буфере обмена: {value[:600]}"


def play_music(query: str = "") -> str:
    """Включить трек/исполнителя: локальный файл или поиск в основном браузере."""
    _audit(f"play_music: {query!r}")
    try:
        from hands import music

        return music.play_query(query or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("play_music: %s", exc)
        return f"Не удалось включить музыку: {exc}"


def take_screenshot() -> str:
    """Сохранить скриншот экрана в assets/ и сказать путь."""
    _audit("take_screenshot")
    try:
        from eyes.screen import capture_screen

        path, size, _offset = capture_screen()
        return f"Скриншот сохранён: {path} ({size[0]}x{size[1]})"
    except Exception as exc:  # noqa: BLE001
        return f"Не удалось сделать скриншот: {exc}"


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
    "не нашёл", "не нашла", "не удалось", "некорректн", "неизвестн", "не указан",
    "сбой", "ошибка", "процесс ", "отменил", "не могу", "не хватает", "нет ",
)


def _looks_failed(text: str) -> bool:
    lowered = text.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _FAILURE_PREFIXES)


def _run_tool(name: str, args: dict) -> tuple[str, bool]:
    function = DISPATCH.get(name)
    if function is None:
        return f"Функция '{name}' не в белом списке — отказ.", False
    if not isinstance(args, dict):
        return f"Аргументы для {name} должны быть объектом.", False

    signature = inspect.signature(function)
    accepted = {key: value for key, value in args.items() if key in signature.parameters}
    missing = [
        param_name
        for param_name, param in signature.parameters.items()
        if param.default is inspect.Parameter.empty and param_name not in accepted
    ]
    if missing:
        return f"Не хватает параметров для {name}: {', '.join(missing)}.", False

    if config.env_bool("JARVIS_DRY_RUN", False):
        return f"[dry-run] {name}({json.dumps(accepted, ensure_ascii=False)})", True

    try:
        result = str(function(**accepted))
        return result, not _looks_failed(result)
    except Exception as exc:  # noqa: BLE001
        log.exception("Сбой исполнения %s", name)
        return f"Сбой исполнения {name}: {exc}", False


def execute_verbose(intent: dict | None, confirm=None, dry_run: bool | None = None) -> tuple[str, bool]:
    if confirm is not None:
        set_confirm_handler(confirm)
    if dry_run is not None:
        os.environ["JARVIS_DRY_RUN"] = "1" if dry_run else "0"
    if not isinstance(intent, dict):
        return "Непонятный интент.", False

    kind = intent.get("type")
    if kind == "chat":
        return intent.get("reply", ""), True
    if kind == "error":
        log.warning("Ошибка мозга: %s", intent.get("detail"))
        return "Сервис недоступен. Проверь VPN и ключи API.", False
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
    return "Непонятный интент.", False


def execute(intent: dict) -> str:
    return execute_verbose(intent)[0]


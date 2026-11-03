# eyes/screen.py
"""Скриншот активного окна, OCR, кэш и ответ по содержимому экрана.

Локальное OCR (Windows.Media.Ocr) + текстовая LLM: быстро, приватно и
надёжнее бесплатных vision-моделей.

Две оптимизации:
- **только активное окно**: OCR считается по пикселям текущего окна, а не всего
  экрана — меньше работы и в LLM не попадает содержимое остальных окон;
- **кэш на `JARVIS_OCR_CACHE_SEC`**: повторный вопрос по тому же окну (пока
  заголовок и размер не изменились) не тратит ни времени, ни приватности.

Координаты OCR всегда переводятся в координаты экрана (offset окна), чтобы
`click_text_on_screen` попадал в нужную кнопку.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from pathlib import Path

import config
from common.log import get_logger

log = get_logger("eyes")

# OrderedDict gives O(1) LRU move_to_end; eviction removes the least-recently-used key.
_CACHE: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 4
# Окно меньше 4% или почти во весь экран — проще OCR-ить весь экран.
_MIN_WINDOW_AREA = 0.04
_MAX_WINDOW_AREA = 0.85


# --------------------------------------------------------------------------
# активное окно
# --------------------------------------------------------------------------
def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Честные границы окна (без тени DWM), иначе обычный GetWindowRect."""
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        try:
            dwm = ctypes.windll.dwmapi
            # DWMWA_EXTENDED_FRAME_BOUNDS = 9
            if dwm.DwmGetWindowAttribute(int(hwnd), 9, ctypes.byref(rect),
                                         ctypes.sizeof(rect)) == 0:
                return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        except Exception:  # noqa: BLE001 - DWM недоступен (старая ОС/удалённый сеанс)
            pass
        if user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception as exc:  # noqa: BLE001
        log.debug("Не удалось получить границы окна: %s", exc)
    return None


def foreground_window() -> dict:
    """Заголовок, процесс, hwnd и прямоугольник активного окна."""
    info = {"hwnd": 0, "title": "", "process": "", "rect": None}
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return info
        info["hwnd"] = hwnd
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        info["title"] = buffer.value or ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            import psutil

            info["process"] = psutil.Process(pid.value).name()
        except Exception:  # noqa: BLE001
            pass
        info["rect"] = _window_rect(hwnd)
    except Exception:  # noqa: BLE001
        pass
    return info


def active_window() -> tuple[str, str]:
    """Заголовок и процесс активного окна (совместимо с прежним API)."""
    info = foreground_window()
    return info["title"], info["process"]


_dpi_ready = False


def _ensure_dpi_aware() -> None:
    """Пиксели снимка должны совпадать с координатами окон (иначе клики мимо)."""
    global _dpi_ready
    if _dpi_ready:
        return
    _dpi_ready = True
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:  # noqa: BLE001 - уже выставлено (gui.py) или старая ОС
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _screen_origin() -> tuple[int, int]:
    _ensure_dpi_aware()
    try:
        import ctypes

        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))
    except Exception:  # noqa: BLE001
        return 0, 0


def _screen_size() -> tuple[int, int]:
    _ensure_dpi_aware()
    try:
        import ctypes

        user32 = ctypes.windll.user32
        width = int(user32.GetSystemMetrics(78))
        height = int(user32.GetSystemMetrics(79))
        if width <= 0 or height <= 0:
            raise RuntimeError("virtual screen unavailable")
        return width, height
    except Exception:  # noqa: BLE001
        pass
    try:
        from PIL import ImageGrab

        return ImageGrab.grab().size
    except Exception:  # noqa: BLE001
        return 1920, 1080


def usable_rect(rect, screen: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Подходит ли окно для «локального» OCR (иначе снимаем весь экран)."""
    if not rect:
        return None
    left, top, right, bottom = (int(value) for value in rect)
    width, height = right - left, bottom - top
    screen_w, screen_h = screen
    origin_x, origin_y = _screen_origin()
    virtual_left = origin_x
    virtual_top = origin_y
    virtual_right = origin_x + screen_w
    virtual_bottom = origin_y + screen_h
    if width < 64 or height < 64:
        return None
    area = (width * height) / float(max(1, screen_w * screen_h))
    if area < _MIN_WINDOW_AREA or area > _MAX_WINDOW_AREA:
        return None
    clipped = (max(virtual_left, left), max(virtual_top, top),
               min(virtual_right, right), min(virtual_bottom, bottom))
    if clipped[2] - clipped[0] < 64 or clipped[3] - clipped[1] < 64:
        return None
    return clipped


# --------------------------------------------------------------------------
# снимок
# --------------------------------------------------------------------------
def _prune() -> None:
    try:
        old = sorted(config.ASSETS_DIR.glob("screen_*.png"))[:-20]
        for item in old:
            item.unlink(missing_ok=True)
    except OSError:
        pass


def capture_screen(path: Path | None = None,
                   rect: tuple[int, int, int, int] | None = None
                   ) -> tuple[Path, tuple[int, int], tuple[int, int]]:
    """Сделать скриншот (всего экрана или области). Возвращает (файл, размер, offset)."""
    from PIL import ImageGrab

    _ensure_dpi_aware()
    origin_x, origin_y = _screen_origin()
    full_w, full_h = _screen_size()
    if rect:
        image = ImageGrab.grab(bbox=rect)
        offset = (int(rect[0]), int(rect[1]))
    else:
        # ImageGrab.grab() по умолчанию даёт только главный монитор; на втором
        # экране Telegram бы не попал в кадр. Явно берём виртуальный рабочий стол.
        bbox = (origin_x, origin_y, origin_x + full_w, origin_y + full_h)
        image = ImageGrab.grab(bbox=bbox)
        offset = (origin_x, origin_y)
    image = image.convert("RGB")
    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        path = config.ASSETS_DIR / f"screen_{int(time.time() * 1000)}.png"
    image.save(path, "PNG")
    _prune()
    log.info("Скриншот: %s (%sx%s, offset %s)", path.name, *image.size, offset)
    return path, image.size, offset


# --------------------------------------------------------------------------
# OCR с кэшем
# --------------------------------------------------------------------------
def _cache_key(info: dict, rect) -> tuple:
    return (info.get("hwnd"), info.get("title"), tuple(rect) if rect else None)


def _cache_get(key: tuple, ttl: float):
    if ttl <= 0:
        return None
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        stamp, payload = item
        if time.monotonic() - stamp > ttl:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return payload


def _cache_put(key: tuple, payload: dict) -> None:
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
        _CACHE[key] = (time.monotonic(), payload)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def screen_ocr(window_only: bool | None = None, use_cache: bool | None = None) -> dict:
    """Снять активное окно (или экран) и распознать текст.

    Возвращает словарь: text, path, size, offset, title, process, cached, hits.
    """
    from eyes.ocr import OcrResult, ocr_image

    window_only = config.OCR_WINDOW_ONLY if window_only is None else window_only
    use_cache = (config.OCR_CACHE_SEC > 0) if use_cache is None else use_cache

    info = foreground_window()
    rect = None
    if window_only:
        rect = usable_rect(info.get("rect"), _screen_size())

    key = _cache_key(info, rect)
    if use_cache:
        cached = _cache_get(key, config.OCR_CACHE_SEC)
        if cached is not None:
            log.info("OCR из кэша: %s", info.get("title") or "—")
            return {**cached, "cached": True}

    path, size, offset = capture_screen(rect=rect)
    try:
        result = ocr_image(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR недоступен: %s", exc)
        return {"text": "", "path": path, "size": size, "offset": offset,
                "title": info.get("title", ""), "process": info.get("process", ""),
                "cached": False, "error": str(exc), "result": None, "hits": 0}

    payload = {
        "text": result.text,
        "path": path,
        "size": size,
        "offset": offset,
        "title": info.get("title", ""),
        "process": info.get("process", ""),
        "cached": False,
        "result": result,
        "hits": 0,
    }
    if use_cache:
        _cache_put(key, {**payload, "cached": False})
    return payload


def _answer_with_llm(question: str, context: str) -> str:
    from brain.brain import call_with_fallback
    from common import guard

    body = guard.guard_external(context, source="ocr", limit=config.OCR_CONTEXT_MAX_CHARS)
    prompt = (
        "Ниже OCR-текст экрана пользователя (может содержать мусор распознавания). "
        "Ответь на вопрос только по этому тексту, кратко и по-русски. "
        "Если ответа в тексте нет, честно скажи об этом.\n\n"
        f"Вопрос: {question}\n\nOCR:\n{body}"
    )
    payload, _elapsed, _who = call_with_fallback(prompt, use_tools=False)
    if payload.get("type") == "chat":
        return (payload.get("reply") or "").strip()
    return ""


def analyze_screen(question: str = "Что на экране?") -> str:
    """Ответить на вопрос по экрану через OCR + текстовую модель."""
    shot = screen_ocr()
    title = shot.get("title") or "—"
    process = shot.get("process") or "—"
    width, height = shot.get("size") or (0, 0)
    if shot.get("error"):
        return (f"Скриншот сохранён: {shot['path'].name}. Активное окно: {title} ({process}). "
                f"OCR недоступен: {shot['error']}")

    context = (f"Активное окно: {title} ({process})\n"
               f"Снимок: {width}x{height} (только активное окно)\n\n{shot['text']}")
    answer = _answer_with_llm(question, context)
    if answer:
        return answer
    if (shot.get("text") or "").strip():
        snippet = " ".join(shot["text"].split())[:1200]
        return f"На экране (OCR): {snippet}"
    return f"На экране не распознан текст. Активное окно: {title}."


def find_on_screen(text: str) -> tuple[int, int, str, int, int] | None:
    """Найти текст на экране.

    Координаты центра возвращаются уже в координатах экрана (учтён offset окна),
    поэтому размеры — размеры экрана: вызывающий код пересчитывать не должен.
    """
    from eyes.ocr import find_text

    shot = screen_ocr(use_cache=False)  # клик требует свежий снимок
    result = shot.get("result")
    if result is None:
        return None
    found = find_text(result, text)
    if not found:
        return None
    matched, (x, y) = found
    offset_x, offset_y = shot.get("offset") or (0, 0)
    screen_w, screen_h = _screen_size()
    return int(x + offset_x), int(y + offset_y), matched, screen_w, screen_h


if __name__ == "__main__":
    print(analyze_screen("Что открыто на экране? Ответь в двух предложениях."))

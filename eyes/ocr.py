             
"""Локальное OCR скриншота через Windows.Media.Ocr (winsdk).

Даёт текст и координаты слов — этого достаточно, чтобы читать ошибки,
находить кнопки и кликать по ним без облачных vision-моделей.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OcrWord:
    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


@dataclass
class OcrResult:
    text: str
    words: list[OcrWord] = field(default_factory=list)
    width: int = 0
    height: int = 0


async def _recognize(path: Path) -> OcrResult:
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage import FileAccessMode, StorageFile

    file = await StorageFile.get_file_from_path_async(str(path))
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows OCR недоступен для текущего языка")
    result = await engine.recognize_async(bitmap)
    words: list[OcrWord] = []
    for line in result.lines:
        for word in line.words:
            rect = word.bounding_rect
            words.append(OcrWord(str(word.text), float(rect.x), float(rect.y),
                                 float(rect.width), float(rect.height)))
    return OcrResult(str(result.text or ""), words, int(decoder.pixel_width), int(decoder.pixel_height))


def ocr_image(path: Path) -> OcrResult:
    """Распознать текст на изображении (синхронная обёртка).

    OCR выполняем в отдельном потоке со своим event loop: в потоке движка
    может уже работать loop (Playwright/asyncio), и тогда asyncio.run() падает
    с «cannot be called from a running event loop» — именно это ломало
    «что на экране» после работы браузера.
    """
    box: dict = {}

    def work() -> None:
        try:
            box["value"] = asyncio.run(_recognize(path))
        except BaseException as exc:                                                 
            box["error"] = exc

    thread = threading.Thread(target=work, name="ocr", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def find_text(result: OcrResult, query: str) -> tuple[str, tuple[float, float]] | None:
    """Найти строку/слово на изображении. Возвращает (текст, центр)."""
    from difflib import SequenceMatcher
    import re

    needle = re.sub(r"\s+", " ", (query or "").lower()).strip()
    if not needle or not result.words:
        return None

    def line_match() -> tuple[str, tuple[float, float]] | None:
        lines: dict[int, list[OcrWord]] = {}
        for word in result.words:
            lines.setdefault(round(word.y), []).append(word)
        for words in lines.values():
            line_text = " ".join(w.text for w in words).lower()
            if needle in line_text:
                left = min(w.x for w in words)
                top = min(w.y for w in words)
                right = max(w.x + w.width for w in words)
                bottom = max(w.y + w.height for w in words)
                return " ".join(w.text for w in words), ((left + right) / 2, (top + bottom) / 2)
        return None

                                                                          
    if " " not in needle:
        for word in result.words:
            if word.text.lower().strip(".,!?;:") == needle:
                return word.text, word.center
        best: tuple[float, OcrWord] | None = None
        for word in result.words:
            ratio = SequenceMatcher(None, needle, word.text.lower()).ratio()
            if ratio >= 0.75 and (best is None or ratio > best[0]):
                best = (ratio, word)
        if best:
            return best[1].text, best[1].center

    line = line_match()
    if line:
        return line

    if " " in needle:                                                       
        for token in reversed(needle.split()):
            found = find_text(result, token)
            if found:
                return found
    return None

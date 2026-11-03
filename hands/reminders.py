                    
"""Напоминания и таймеры: «напомни через 15 минут …», «напомни в 18:30 …».

Один фоновый поток (daemon) спит до ближайшего срока, поэтому в простое
нагрузки нет вообще — ни опроса, ни таймеров в UI. Когда время выходит,
Jarvis произносит напоминание (или печатает в консоли).
"""
from __future__ import annotations

import heapq
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):                                          
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.log import get_logger

log = get_logger("reminders")

_queue: list[tuple[float, str]] = []
_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None
_speaker = None                                                    


                                                                            
                
                                                                            
_NUM = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
        "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
        "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40,
        "сорок пять": 45, "час": 1}
_UNIT_SECONDS = {"секунд": 1, "минут": 60, "час": 3600}
_AT = re.compile(r"(?:в|к)\s+(\d{1,2})(?:[:.\s](\d{2}))?\s*(?:часов|часа|ч|утра|вечера|дня|ночи)?")
_BARE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*$")


def _words_to_number(raw: str) -> int | None:
    raw = (raw or "").strip()
    if raw.isdigit():
        return int(raw)
    total = 0
    for word in re.split(r"[\s-]+", raw):
        if word in _NUM:
            total += _NUM[word]
    return total or None


def parse_delay(when: str) -> float | None:
    """Строка вида «через 15 минут» / «18:30» / «в 7 утра» → секунды от сейчас."""
    text = (when or "").lower().replace("ё", "е").strip()
    if not text:
        return None

    tail = text.split("через", 1)[1].strip() if "через" in text else text
    if "полтора часа" in tail:
        return 5400.0
    if "полчаса" in tail or "пол часа" in tail:
        return 1800.0
    amount_unit = re.search(r"(\d+|[а-я]+)[\s-]*(секунд|минут|час)", tail)
    if amount_unit:
        amount = _words_to_number(amount_unit.group(1))
        if amount:
            return max(1.0, float(amount * _UNIT_SECONDS[amount_unit.group(2)]))
    if re.search(r"\bчас\b", tail):
        return 3600.0
    if re.search(r"\bминут", tail):
        return 60.0

    bare = _BARE.match(text)
    if bare:
        text = f"в {bare.group(1)}:{bare.group(2)}"

    match = _AT.search(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        lowered = text
        if "вечера" in lowered or "вечером" in lowered:
            hour = hour + 12 if hour < 12 else hour
        elif any(word in lowered for word in ("ночи", "утра", "утром")) and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        from datetime import timedelta

        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)                                     
        return max(1.0, (target - now).total_seconds())

    return None


def format_delay(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} с"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} мин" + (f" {rest} с" if rest and minutes < 5 else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч" + (f" {minutes} мин" if minutes else "")


                                                                            
         
                                                                            
def set_speaker(callback) -> None:
    """Кто озвучивает напоминание (GUI подставляет говорящего с mute микрофона)."""
    global _speaker
    _speaker = callback


def _say(text: str) -> None:
    callback = _speaker
    if callback is None:
        try:
            from tts.speaker import speak

            callback = speak
        except Exception:                
            callback = None
    if callback is None:
        log.info("🔔 %s", text)
        return
    try:
        callback(text)
    except Exception as exc:                
        log.warning("Озвучка напоминания не удалась: %s", exc)
        log.info("🔔 %s", text)


def _loop() -> None:
    while True:
        with _lock:
            delay = 30.0 if not _queue else max(0.2, min(30.0, _queue[0][0] - time.time()))
        _wake.wait(delay)
        _wake.clear()
        now = time.time()
        due: list[tuple[float, str]] = []
        with _lock:
            while _queue and _queue[0][0] <= now:
                due.append(heapq.heappop(_queue))
        for _due_at, text in due:
            log.info("Напоминание: %s", text)
            _say(f"Напоминаю: {text}" if not text.startswith("Напоминаю") else text)


def _ensure_thread() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="reminders", daemon=True)
    _thread.start()


def schedule(seconds: float, text: str) -> float:
    """Поставить напоминание через N секунд. Возвращает момент (unix time)."""
    due = time.time() + max(1.0, float(seconds))
    with _lock:
        heapq.heappush(_queue, (due, str(text)))
    _ensure_thread()
    _wake.set()
    log.info("Напоминание через %s: %s", format_delay(seconds), text)
    return due


def add(when: str, text: str) -> float | None:
    """Поставить напоминание по строке времени («через 15 минут», «18:30»)."""
    delay = parse_delay(when)
    if delay is None or not str(text or "").strip():
        return None
    return schedule(delay, str(text).strip())


def pending() -> list[tuple[float, str]]:
    with _lock:
        return sorted(_queue)


def clear() -> int:
    with _lock:
        count = len(_queue)
        _queue.clear()
    return count


if __name__ == "__main__":
    for sample in ("через 15 минут", "через полчаса", "через 2 часа", "18:30", "в 7 утра",
                   "через час", "мусор"):
        delay = parse_delay(sample)
        print(f"{sample:<18} -> {delay if delay is None else format_delay(delay)}")

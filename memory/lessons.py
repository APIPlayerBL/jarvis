# memory/lessons.py
"""Обучаемые команды: «включить музыку» → мой сайт, за 0 токенов и мгновенно.

Пользователь учит Jarvis в GUI (вкладка «Обучение») или из консоли:

    python -m memory.lessons --add "включи музыку" open_url https://music.yandex.ru
    python -m memory.lessons --list
    python -m memory.lessons --remove 3

Свои команды проверяются раньше встроенных правил роутера, поэтому «включить
музыку» перестаёт быть «медиа-паузой» и открывает именно ваш сайт. Совпадение
считается по нормализованной фразе, алиасам и мягкому сходству — «открой мою
музыку», «врубай музон» можно добавить как алиасы.
"""
from __future__ import annotations

import json
import re
import sys
import time
import uuid
from difflib import SequenceMatcher
from pathlib import Path

if __package__ in (None, ""):  # запуск как `python memory\lessons.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from common.fs import atomic_write_json
from common.log import get_logger

log = get_logger("memory.lessons")

# название → (человеческое описание, имя поля значения, подсказка)
ACTIONS: dict[str, tuple[str, str, str]] = {
    "open_url": ("Открыть сайт", "url", "https://music.yandex.ru"),
    "launch_app": ("Запустить приложение", "app", "spotify"),
    "search_web": ("Найти в браузере", "query", "погода в Барнауле"),
    "youtube": ("Включить видео на YouTube", "query", "lofi hip hop"),
    "media": ("Музыка: play/pause/next/prev", "action", "play"),
    "volume": ("Громкость", "level", "40"),
    "say": ("Ответить фразой", "text", "Музыка включена."),
    "note": ("Записать заметку", "text", "позвонить в сервис"),
    "close_app": ("Закрыть приложение", "app", "discord"),
    "screenshot": ("Скриншот экрана", "", ""),
    "minimize_all": ("Свернуть все окна", "", ""),
}

_MEDIA = ("play", "pause", "next", "prev", "stop")
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WEB = re.compile(r"^(https?://)[^\s]+$", re.IGNORECASE)
_MAX_LESSONS = 500
# Слова-обёртки, которые не должны мешать узнать фразу: «Джарвис, включи
# музыку, пожалуйста» — та же команда, что и «включи музыку».
_FILLERS = re.compile(
    r"\b(пожалуйста|плиз|пж|давай|ну|мне|сейчас|быстро|побыстрее|будь добр|"
    r"джарвис|жарвис|джейвис|jarvis)\b"
)
_cache: dict = {"mtime": None, "items": []}


# --------------------------------------------------------------------------
# хранилище
# --------------------------------------------------------------------------
def _path() -> Path:
    return config.LESSONS_PATH


def _read() -> list[dict]:
    path = _path()
    try:
        stat = path.stat()
    except OSError:
        return []
    if _cache["mtime"] == stat.st_mtime and _cache["items"]:
        return _cache["items"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("lessons.json не читается: %s", exc)
        return []
    items = raw.get("lessons") if isinstance(raw, dict) else raw
    result = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        phrase = str(item.get("phrase") or "").strip()
        action = str(item.get("action") or "").strip()
        if not phrase or action not in ACTIONS:
            continue
        result.append({
            "id": str(item.get("id") or uuid.uuid4().hex[:8]),
            "phrase": phrase,
            "aliases": [str(a).strip() for a in item.get("aliases") or [] if str(a).strip()],
            "action": action,
            "value": str(item.get("value", "")),
            "enabled": bool(item.get("enabled", True)),
            "created": float(item.get("created") or time.time()),
        })
    _cache.update(mtime=stat.st_mtime, items=result)
    return result


def list_lessons() -> list[dict]:
    return [dict(item) for item in _read()]


def save(items: list[dict]) -> None:
    path = _path()
    payload = {"version": 1, "lessons": items[:_MAX_LESSONS]}
    atomic_write_json(path, payload, ensure_ascii=False, indent=2)
    try:
        _cache.update(mtime=path.stat().st_mtime, items=payload["lessons"])
    except OSError:
        _cache.update(mtime=None, items=payload["lessons"])
    log.info("Обучение сохранено: %d команд", len(payload["lessons"]))


def _valid_url(value: str) -> bool:
    from urllib.parse import urlsplit

    if not value or " " in value or not _WEB.match(value):
        return False
    parts = urlsplit(value)
    host = parts.netloc
    return (parts.scheme in {"http", "https"} and "." in host
            and not any(ch in host for ch in ":/\\@"))


def _clean_value(action: str, value: str) -> str:
    value = str(value or "").strip()
    if action == "open_url":
        if not _WEB.match(value):
            value = "https://" + value.lstrip("/")
        if not _valid_url(value):
            raise ValueError("нужен адрес вида https://site.ru")
    elif action == "media":
        value = value.lower()
        if value not in _MEDIA:
            raise ValueError(f"для музыки допустимо: {', '.join(_MEDIA)}")
    elif action == "volume":
        try:
            value = str(max(0, min(100, int(float(value)))))
        except ValueError as exc:
            raise ValueError("громкость — число 0–100") from exc
    elif action in {"launch_app", "search_web", "youtube", "say", "note", "close_app"}:
        if not value:
            raise ValueError("значение не может быть пустым")
    elif action in {"screenshot", "minimize_all"}:
        value = ""
    return value


def add(phrase: str, action: str, value: str, aliases: list[str] | None = None) -> dict:
    """Добавить или обновить обучаемую команду (по фразе)."""
    phrase = str(phrase or "").strip()
    if not phrase:
        raise ValueError("нужна фраза, которой вы будете управлять")
    if action not in ACTIONS:
        raise ValueError(f"неизвестное действие: {action}")
    value = _clean_value(action, value)
    items = _read()
    key = normalize(phrase)
    record = None
    for item in items:
        if normalize(item["phrase"]) == key:
            record = item
            break
    if record is None:
        record = {"id": uuid.uuid4().hex[:8], "phrase": phrase, "aliases": [],
                  "action": action, "value": value, "enabled": True, "created": time.time()}
        items.append(record)
    else:
        record.update(action=action, value=value, enabled=True)
    if aliases:
        record["aliases"] = list(dict.fromkeys([a for a in aliases if a.strip()]))
    save(items)
    return dict(record)


def remove(lesson_id: str) -> bool:
    items = _read()
    kept = [item for item in items if item["id"] != lesson_id]
    if len(kept) == len(items):
        return False
    save(kept)
    return True


def toggle(lesson_id: str, enabled: bool | None = None) -> dict | None:
    items = _read()
    for item in items:
        if item["id"] == lesson_id:
            item["enabled"] = (not item["enabled"]) if enabled is None else bool(enabled)
            save(items)
            return dict(item)
    return None


def clear() -> None:
    save([])


# --------------------------------------------------------------------------
# распознавание
# --------------------------------------------------------------------------
def normalize(text: str, *, drop_fillers: bool = True) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    if drop_fillers:
        text = _FILLERS.sub(" ", text)
    return _WS.sub(" ", text).strip()


# Противоположные команды: «выключи музыку» не должно совпадать с обученной
# «включи музыку» (мягкое сходство у них 0.92).
_OPPOSITES = (("включ", "выключ"), ("включ", "отключ"), ("откр", "закр"),
              ("запуст", "останов"), ("громче", "тише"))


def _is_opposite(command: str, candidate: str) -> bool:
    for positive, negative in _OPPOSITES:
        if (positive in candidate and negative in command) or (
                negative in candidate and positive in command):
            return True
    return False


def _score(command: str, candidate: str) -> float:
    if not candidate:
        return 0.0
    if command == candidate:
        return 1.0
    if _is_opposite(command, candidate):
        return 0.0
    # Команда может быть длиннее («джарвис, включи музыку пожалуйста»), но если
    # после неё остались значимые слова — это уже другая команда: «включи
    # музыку кишлак» должен уйти в музыкальный скил, а не в обученную
    # «включи музыку».
    if len(candidate) >= 8 and candidate in command:
        leftover = _FILLERS.sub(" ", command.replace(candidate, " ", 1))
        leftover = _WS.sub(" ", leftover).strip(" ,.!?-")
        if not leftover:
            return 0.95
        return SequenceMatcher(None, command, candidate).ratio() * 0.9
    return SequenceMatcher(None, command, candidate).ratio()


def _intent(lesson: dict) -> dict | None:
    action, value = lesson["action"], lesson["value"]
    via = f"lesson:{lesson['id']}"
    if action == "open_url":
        return {"type": "tool", "name": "open_website", "args": {"url": value}, "_via": via}
    if action == "launch_app":
        return {"type": "tool", "name": "launch_app", "args": {"app_name": value}, "_via": via}
    if action == "search_web":
        return {"type": "tool", "name": "search_web", "args": {"query": value}, "_via": via}
    if action == "youtube":
        return {"type": "tool", "name": "browser_play_youtube", "args": {"query": value},
                "_via": via}
    if action == "media":
        return {"type": "tool", "name": "media_control", "args": {"action": value}, "_via": via}
    if action == "volume":
        return {"type": "tool", "name": "control_volume",
                "args": {"action": "set", "level": int(value)}, "_via": via}
    if action == "note":
        return {"type": "tool", "name": "save_note", "args": {"text": value}, "_via": via}
    if action == "close_app":
        return {"type": "tool", "name": "close_app", "args": {"app_name": value}, "_via": via}
    if action == "screenshot":
        return {"type": "tool", "name": "take_screenshot", "args": {}, "_via": via}
    if action == "minimize_all":
        return {"type": "tool", "name": "minimize_all", "args": {}, "_via": via}
    if action == "say":
        return {"type": "chat", "reply": value, "_via": via}
    return None


def match(text: str, threshold: float = 0.88) -> dict | None:
    """Вернуть интент обучаемой команды или None."""
    command = normalize(text)
    if not command:
        return None
    best: tuple[float, dict] | None = None
    for lesson in _read():
        if not lesson["enabled"]:
            continue
        candidates = [normalize(lesson["phrase"])] + [normalize(a) for a in lesson["aliases"]]
        score = max((_score(command, candidate) for candidate in candidates), default=0.0)
        if score >= threshold and (best is None or score > best[0]):
            best = (score, lesson)
    if best is None:
        return None
    intent = _intent(best[1])
    if intent is None:
        return None
    log.info("Обученная команда «%s» → %s (сходство %.2f)", best[1]["phrase"],
             best[1]["action"], best[0])
    return intent


def describe(lesson: dict) -> str:
    action = lesson.get("action", "")
    label = ACTIONS.get(action, (action, "", ""))[0]
    value = lesson.get("value", "")
    return f"{label}: {value}" if value else label


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Обучение Jarvis командам")
    parser.add_argument("--list", action="store_true", help="показать команды")
    parser.add_argument("--add", nargs=3, metavar=("ФРАЗА", "ДЕЙСТВИЕ", "ЗНАЧЕНИЕ"),
                        help="добавить: --add 'включи музыку' open_url https://music.yandex.ru")
    parser.add_argument("--alias", action="append", default=[], help="алиас фразы (можно несколько)")
    parser.add_argument("--remove", metavar="ID", help="удалить по id")
    parser.add_argument("--toggle", metavar="ID", help="включить/выключить по id")
    parser.add_argument("--test", metavar="ФРАЗА", help="проверить, какая команда сработает")
    args = parser.parse_args(argv)

    if args.add:
        phrase, action, value = args.add
        record = add(phrase, action, value, args.alias)
        print(f"Добавлено [{record['id']}]: {record['phrase']} → {describe(record)}")
        return 0
    if args.remove:
        print("Удалено" if remove(args.remove) else "Такой команды нет")
        return 0
    if args.toggle:
        record = toggle(args.toggle)
        print("Теперь:", record and describe(record))
        return 0
    if args.test:
        from memory import router

        print(router.route(args.test))
        return 0

    print("Действия:", ", ".join(ACTIONS))
    for lesson in list_lessons():
        state = "вкл " if lesson["enabled"] else "выкл"
        aliases = f"  (алиасы: {', '.join(lesson['aliases'])})" if lesson["aliases"] else ""
        print(f"  [{lesson['id']}] {state} {lesson['phrase']:<28} → {describe(lesson)}{aliases}")
    if not list_lessons():
        print("  Пока пусто. Пример: --add 'включи музыку' open_url https://music.yandex.ru")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

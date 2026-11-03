                  
"""Локальный роутер: максимум команд без обращения к LLM (0 токенов, ~0.1 мс).

Порядок правил важен:
1. время/дата; 2. громкость; 3. медиа; 4. питание/блокировка;
5. точный кэш; 6. файлы/папки; 7. закрытие; 8. открытие.
Громкость и медиа проверяются ДО закрытия, иначе «выключи музыку»
превращалось в close_app("музыку").
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in (None, ""):                                        
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.log import get_logger
from memory import apps, discovery, intent_cache, lessons

log = get_logger("memory.router")

                                          
_OPEN_VERBS = re.compile(
    r"\b(открой|открыть|открывай|запусти|запустить|запускай|старт|включи|включить|вруби|врубить|поставь|поставить|поставляй)\b"
)
_CLOSE_VERBS = re.compile(
    r"\b(закрой|закрыть|закрывай|заверши|завершить|выключи|выключить|отключи|отключить|выруби|вырубить|убей)\b"
)
_FIND_VERBS = re.compile(r"\b(найди|найти|поищи|покажи|открой|открыть|запусти)\b")
_FILLERS = re.compile(
    r"\b(пожалуйста|мне|пж|давай|джарвис|жарвис|джейвис|сейчас|быстро|побыстрее|плиз)\b"
)
_GENERIC = re.compile(
    r"\b(приложение|приложения|программу|программа|игру|игра|игрушку|сайт|страницу|вкладку|все|окно|окна)\b"
)
_VOLUME = re.compile(
    r"\b(громкость|громкости|громкостью|звук|звука|звуке|громче|тише|погромче|потише|мут|без звука)\b"
)
_MEDIA = re.compile(r"\b(музык\w*|трек\w*|песн\w*|плеер\w*|плейлист\w*|видео|аудио)\b")
_FILE_WORDS = re.compile(
    r"\b(файл\w*|фото\w*|фотограф\w*|картинк\w*|скриншот\w*|документ\w*|диплом\w*|папк\w*)\b"
)
_NUM = re.compile(r"(\d{1,3})")
_DOMAIN = re.compile(r"^(?:https?://)?(?:[\w-]+\.)+[a-zа-я]{2,}(?:/\S*)?$")
_WS = re.compile(r"\s+")

SITES = {
    "ютуб": "https://youtube.com",
    "ютьюб": "https://youtube.com",
    "youtube": "https://youtube.com",
    "вк": "https://vk.com",
    "vk": "https://vk.com",
    "вконтакте": "https://vk.com",
    "вк музыка": "https://vk.ru/audios457131425",
    "вк музыку": "https://vk.ru/audios457131425",
    "вк музыки": "https://vk.ru/audios457131425",
    "vk music": "https://vk.ru/audios457131425",
    "музыка вк": "https://vk.ru/audios457131425",
    "музыку вк": "https://vk.ru/audios457131425",
    "гугл": "https://google.com",
    "google": "https://google.com",
    "яндекс": "https://ya.ru",
    "yandex": "https://ya.ru",
    "почт": "https://mail.google.com",
    "gmail": "https://mail.google.com",
    "гитхаб": "https://github.com",
    "github": "https://github.com",
    "telegram": "https://web.telegram.org",
    "телеграм": "https://web.telegram.org",
    "тг": "https://web.telegram.org",
    "дискорд": "https://discord.com",
    "discord": "https://discord.com",
    "вконтакт": "https://vk.com",
    "стим": "https://store.steampowered.com",
    "погод": "https://yandex.ru/pogoda",
    "новост": "https://news.google.com",
    "кинопоиск": "https://www.kinopoisk.ru",
}


def _norm(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s.]", " ", text, flags=re.UNICODE)
    return _WS.sub(" ", text).strip()


def _clean_target(text: str, verbs: re.Pattern[str]) -> str:
    text = verbs.sub(" ", text)
    text = _FILLERS.sub(" ", text)
    text = _GENERIC.sub(" ", text)
    return _WS.sub(" ", text).strip(" .")


def _tool(name: str, args: dict, via: str = "local_router") -> dict:
    return {"type": "tool", "name": name, "args": args, "_via": via}


_VIDEO_NOISE = re.compile(
    r"\b(включи|включить|найди|найти|открой|открыть|запусти|запустить|поставь|поставить|"
    r"видео|ролик|клип|на|ютубе|youtube|пожалуйста|мне)\b"
)


def _special_intent(text: str) -> dict | None:
    if re.search(r"\b(посмотри|глянь|посмотри-ка)\b.*\b(экран|монитор)\b", text) or re.search(
        r"\bчто\b[^.]{0,25}\b(на|во) ?(экране|мониторе)\b"                                  
        r"|\b(что|ошибк\w*|проблем\w*)\b[^.]{0,25}\bв окне\b"
        r"|\bчто ты видишь\b|\bпроанализируй экран\b"
        r"|\b(что|кто) открыт\w*\b.*\bна экране\b"
        r"|\bесть ли\b[^.]{0,20}\bошибк\w*\b",
        text,
    ):
        return _tool("see_screen", {"question": text}, "local_vision")

    if ("видео" in text or "ролик" in text or "клип" in text or "на ютубе" in text) and re.search(
        r"\b(включи|найди|открой|запусти|поставь)\b", text
    ):
        query = _VIDEO_NOISE.sub(" ", text)
        query = _WS.sub(" ", query).strip(" .")
        if query:
            return _tool("browser_play_youtube", {"query": query}, "local_youtube")
        if "ютуб" in text or "youtube" in text:
            return _tool("open_website", {"url": "https://youtube.com"}, "local_youtube")

    if re.search(r"\b(стим|steam)\b", text) and re.search(r"\b(открой|покажи|запусти|перейди)\b", text):
        for words, action in (
            (("магазин", "store"), "store"),
            (("библиотек", "library"), "library"),
            (("друз", "friends"), "friends"),
            (("загрузк", "downloads"), "downloads"),
            (("настройк", "settings"), "settings"),
            (("профил", "profile"), "profile"),
        ):
            if any(word in text for word in words):
                return _tool("steam_action", {"action": action}, "local_steam")

    if re.search(r"\b(дискорд|discord)\b", text):
        if re.search(r"\b(замьють|замьютить|выключи микрофон|мут)\b", text):
            return _tool("discord_action", {"action": "mute"}, "local_discord")
        if re.search(r"\b(размьють|включи микрофон|unmute)\b", text):
            return _tool("discord_action", {"action": "unmute"}, "local_discord")
        if re.search(r"\b(заглуши|deafen)\b", text) and "звук" not in text:
            return _tool("discord_action", {"action": "deafen"}, "local_discord")
        if re.search(r"\b(верни звук|undeafen)\b", text):
            return _tool("discord_action", {"action": "undeafen"}, "local_discord")
    return None


_REMINDER_VERB = re.compile(r"\b(напомни|напоминание|напомнить)\b")
_NOTE_VERB = re.compile(r"\b(запиши|записать|запомни|заметк\w*)\b")
_REMINDER_NOISE = re.compile(
    r"^(поставь|создай|сделай|установи)?\s*(напоминание|напомни|напомнить)\s*"
)


def _split_reminder(rest: str) -> tuple[str | None, str]:
    """Отделить время от текста: «через 15 минут купить хлеб» → (время, текст)."""
    from hands import reminders

                                                                           
    rest = re.sub(r"\b(\d{1,2})\s+(\d{2})\b", r"\1:\2", rest)
    rest = re.sub(r"\b(мне|пожалуйста|плиз|пж)\b", " ", rest)
    rest = _WS.sub(" ", rest).strip()
    words = rest.split()
                                                                            
    dayparts = {"утра", "утром", "вечера", "вечером", "дня", "ночи"}
    for count in range(1, min(5, len(words)) + 1):
        head = " ".join(words[:count])
        tail = " ".join(words[count:]).strip(" ,:—")
        if count < len(words) and words[count] in dayparts:
            continue                                                    
        if tail and ":" not in head and reminders.parse_delay(head):
            return head, tail
        if head.count(":") == 1 and reminders.parse_delay(head) and tail:
            return head, tail
    return None, rest


def _notes_intent(text: str) -> dict | None:
    """Заметки, напоминания, буфер обмена, скриншот — локально и бесплатно."""
    if re.search(r"\b(что|покажи|прочитай)\b.*\bзаметк\w*", text) or "мои заметки" in text:
        return _tool("read_notes", {"limit": 5}, "local_notes")

    if _REMINDER_VERB.search(text):
        rest = _REMINDER_NOISE.sub("", text).strip(" ,:—")
        when, what = _split_reminder(rest)
        if when and what:
            return _tool("set_reminder", {"when": when, "text": what}, "local_reminder")
        return None                                                

    if re.search(r"\b(что|покажи|прочитай)\b.*(буфер\w*|скопировал|скопировано)", text):
        return _tool("read_clipboard", {}, "local_clipboard")

    if ("скрин" in text or "скриншот" in text or "скрин экрана" in text or "сними экран" in text):
        return _tool("take_screenshot", {}, "local_screenshot")

    if _NOTE_VERB.search(text):
        note = re.sub(r"^(запиши|записать|запомни|сделай заметку|заметка)\s*", "", text)
        note = re.sub(r"^(заметку|заметка|что|о том, что)\s*", "", note).strip(" ,:—.")
        if note:
            return _tool("save_note", {"text": note}, "local_notes")
    return None


def _datetime_intent(text: str) -> dict | None:
    if re.search(
        r"\b(котор\w* час|сколько (сейчас )?врем\w*|время сейчас|текущее время|"
        r"какое (сегодня |сейчас )?число|какая (сегодня |сейчас )?дата|какой (сегодня )?день|"
        r"сегодняшн\w* (дата|число)|день недели)\b",
        text,
    ):
        if re.search(r"\b(число|дата|день|недели)\b", text):
            return _tool("get_datetime", {"what": "date"}, "local_datetime")
        return _tool("get_datetime", {"what": "time"}, "local_datetime")
    return None


def _volume_intent(text: str) -> dict | None:
    if not _VOLUME.search(text):
        return None
    if re.search(r"\b(выключи|отключи|убери|мут|без звука)\b", text) and "звук" in text:
        return _tool("control_volume", {"action": "mute"})
    if re.search(r"\b(включи|верни|размут)\b", text) and "звук" in text:
        return _tool("control_volume", {"action": "unmute"})
    number = _NUM.search(text)
    if number:
        level = int(number.group(1))
        if 0 <= level <= 100:
            return _tool("control_volume", {"action": "set", "level": level})
    if re.search(r"\bмаксимум\b", text):
        return _tool("control_volume", {"action": "set", "level": 100})
    if re.search(r"\bминимум\b", text):
        return _tool("control_volume", {"action": "set", "level": 0})
    if re.search(r"\b(громче|прибавь|увеличь|выше|погромче)\b", text):
        return _tool("control_volume", {"action": "up"})
    if re.search(r"\b(тише|убавь|уменьши|ниже|потише)\b", text):
        return _tool("control_volume", {"action": "down"})
    if "мут" in text:
        return _tool("control_volume", {"action": "mute"})
    return None


_MUSIC_VERB = re.compile(
    r"\b(включи|включить|включай|поставь|поставить|вруби|врубить|"
    r"сыграй|играй|воспроизведи|воспроизвести)\b"
)
_MUSIC_NOUN = re.compile(
    r"\b(музык\w*|трек\w*|песн\w*|песенк\w*|композиц\w*|альбом\w*|плейлист\w*|"
    r"исполнител\w*|артист\w*|групп\w*)\b"
)
_MUSIC_STOP = re.compile(
    r"\b(выключи|отключи|пауза|паузу|стоп|останови|замолчи|громче|тише|погромче|"
    r"потише|следующ\w*|предыдущ\w*|назад|дальше|переключи)\b"
)
_MUSIC_NOISE = re.compile(
    r"\b(мне|пожалуйста|плиз|пж|давай|ну|сейчас|какую-нибудь|какую нибудь|"
    r"что-нибудь|что нибудь|чего-нибудь|чего нибудь|из|от|по|на|и|в|the|a)\b"
)

_MUSIC_NONMUSIC = re.compile(
    r"\b(впн|vpn|пеинт|пейнт|paint|программ\w*|приложен\w*|экран\w*|точк\w*|центр\w*|окн\w*|кнопк\w*|клик\w*|показ\w*|нарис\w*|созд\w*|заверш\w*|перемот\w*|ITCH\w*)\b", re.IGNORECASE)

def _music_intent(text: str) -> dict | None:
    """«Включи музыку кишлак» → play_music(query='кишлак') вместо простого play.

    Раньше любая фраза со словом «музык» превращалась в play/pause, и понять,
    ЧТО именно включать, было невозможно.
    """
    if not _MUSIC_VERB.search(text) or _MUSIC_STOP.search(text):
        return None
    has_noun = bool(_MUSIC_NOUN.search(text))
    query = _MUSIC_VERB.sub(" ", text)
    query = _MUSIC_NOUN.sub(" ", query)
    query = _MUSIC_NOISE.sub(" ", query)
    query = _FILLERS.sub(" ", query)                                               
    query = _WS.sub(" ", query).strip(" -,")
    if not query and not has_noun:
        return None
                                                                           
    if query and (apps.resolve(query) or _site_url(query)):
        return None
                                                                         
    if _MUSIC_NONMUSIC.search(text) or _MUSIC_NONMUSIC.search(query):
        return None
    return _tool("play_music", {"query": query}, "local_music")


def _media_intent(text: str) -> dict | None:
    if not _MEDIA.search(text):
        if text in {"пауза", "паузу", "стоп", "плей", "играй", "дальше", "следующий",
                    "следующая", "назад", "предыдущий", "предыдущая"}:
            action = {
                "пауза": "pause", "паузу": "pause", "стоп": "stop", "плей": "play",
                "играй": "play", "дальше": "next", "следующий": "next", "следующая": "next",
                "назад": "prev", "предыдущий": "prev", "предыдущая": "prev",
            }[text]
            return _tool("media_control", {"action": action})
        return None
    if re.search(r"\b(пауза|паузу|приостанови|приостановить|выключи|отключи|выруби)\b", text):
        return _tool("media_control", {"action": "pause"})
    if re.search(r"\b(стоп|останови|остановить|замолчи)\b", text):
        return _tool("media_control", {"action": "stop"})
    if re.search(r"\b(дальше|следующ\w*|переключи|переключить|next)\b", text):
        return _tool("media_control", {"action": "next"})
    if re.search(r"\b(назад|предыдущ\w*|prev)\b", text):
        return _tool("media_control", {"action": "prev"})
    if re.search(r"\b(играй|включи|включить|плей|продолжи|продолжить|запусти|играть)\b", text):
        return _tool("media_control", {"action": "play"})
    return None


def _system_intent(text: str) -> dict | None:
    if re.search(r"\b(заблокируй|заблокировать|блокировка)\b", text):
        return _tool("system_action", {"action": "lock"})
    if re.search(r"\b(перезагруз\w*|reload pc)\b", text):
        return _tool("system_action", {"action": "restart"})
    if re.search(r"\bвыключи\b.*\b(компьютер|пк|ноутбук)\b", text) or "заверши работу" in text:
        return _tool("system_action", {"action": "shutdown"})
    if re.search(r"\b(сон|усни|спать)\b", text) and re.search(r"\b(компьютер|пк|ноутбук)\b", text):
        return _tool("system_action", {"action": "sleep"})
    return None


def _window_intent(text: str) -> dict | None:
    if re.search(r"\b(сверни|свернуть|сверни-ка)\b.*\b(окна|окно|всё|все)\b", text):
        return _tool("minimize_all", {})
    if re.search(r"\b(покажи|открой)\b.*\bрабочий стол\b", text):
        return _tool("minimize_all", {})
    return None


def _file_intent(text: str) -> dict | None:
    if not _FILE_WORDS.search(text):
        return None
    if not (_FIND_VERBS.search(text) or "открой" in text or "покажи" in text):
        return None
    return _tool("search_and_open_file", {"query": text}, "local_file_router")


def _close_intent(text: str) -> dict | None:
    match = _CLOSE_VERBS.search(text)
    if not match:
        return None
    target = _clean_target(text, _CLOSE_VERBS)
    canonical = apps.resolve(target) or apps.resolve(text)
    if canonical:
        return _tool("close_app", {"app_name": canonical})
                                                                    
    return None


def _site_url(target: str) -> str | None:
    target = (target or "").strip().lower()
    if not target:
        return None
    if target in SITES:
        return SITES[target]
    words = target.split()
    for name, url in SITES.items():
        if len(name) >= 4:
            if any(word.startswith(name) for word in words):
                return url
        elif name in words:
            return url
    if " " not in target and _DOMAIN.match(target):
        return target if target.startswith(("http://", "https://")) else "https://" + target
    return None


def _open_intent(text: str) -> dict | None:
    if not _OPEN_VERBS.search(text):
        return None
    target = _clean_target(text, _OPEN_VERBS)
    canonical = apps.resolve(target) or apps.resolve(text)
    if canonical:
        return _tool("launch_app", {"app_name": canonical})

    url = _site_url(target)
    if url:
        return _tool("open_website", {"url": url})

    found = discovery.find(target) or discovery.find(text)
    if found:
        return _tool("launch_app", {"app_name": target or text})

                                                                          
                                                                          
    if target and _site_url(target):
        return _tool("open_website", {"url": _site_url(target)})
                                                                           
                                                         
    if target and len(target) >= 3 and not apps.resolve(target) and any(
            w in text for w in ("найди", "поиск", "поищи", "ищи", "search")):
        return _tool("search_web", {"query": target}, "local_router")
    return None


def route(text: str) -> dict | None:
    """Вернуть интент локально или None (пусть решает brain)."""
    normalized = _norm(text)
    if not normalized:
        return None

                                                                             
                                                                           
    try:
        learned = lessons.match(text)
    except Exception as exc:                                                             
        log.warning("Обученные команды недоступны: %s", exc)
        learned = None
    if learned:
        return learned

    for handler in (_special_intent, _notes_intent, _datetime_intent, _volume_intent,
                    _music_intent, _media_intent, _system_intent):
        intent = handler(normalized)
        if intent:
            return intent

    cached = intent_cache.get_intent(text)
    if cached:
        return cached

    for handler in (_window_intent, _file_intent, _close_intent, _open_intent):
        intent = handler(normalized)
        if intent:
            return intent
    return None


if __name__ == "__main__":
    samples = [
        "который час", "какое сегодня число", "выключи музыку", "включи музыку",
        "поставь громкость 30", "увеличь громкость", "выключи звук",
        "закрой доту", "открой стим", "открой ютуб", "открой youtube.com",
        "запусти доту", "найди диплом на компьютере", "заблокируй компьютер",
        "выключи компьютер", "сверни все окна", "как дела",
    ]
    for sample in samples:
        print(f"{sample!r:34} -> {route(sample)}")

                 
"""Скилы Jarvis: инструменты + короткая инструкция под задачу.

Раньше в каждый запрос уходили все схемы инструментов и большой общий промпт
(~2800 токенов). Скилы решают две вещи:

1. **набор инструментов** — под задачу берётся только нужное (3–8 схем);
2. **инструкция** — 1–3 строки «как это делать правильно» для активных скилов
   (вместо абзацев общих пояснений, которые большинство команд не используют).

Это одновременно дешевле (~в 4–5 раз меньше промпта) и точнее: модель не
видит нерелевантных функций и не путает их между собой.
"""
from __future__ import annotations

import re

                                                                            
       
                                                                            
                                                 
SKILLS: dict[str, dict] = {
    "core": {
        "title": "Базовые действия",
        "keywords": (),                     
        "tools": ("launch_app", "open_website", "get_datetime"),
        "prompt": "",
    },
    "browser": {
        "title": "Браузер",
        "keywords": ("сайт", "страниц", "браузер", "ссылк", "форм", "заполн", "вкладк",
                     "зайди", "перейди", "регистрац", "прочитай", "прочти", "телеграм",
                     "telegram", "вконтакт", "почт"),
        "tools": ("browser_open", "browser_read", "browser_click", "browser_fill",
                  "browser_back"),
        "prompt": ("Браузер: browser_open уже возвращает заголовок и начало текста — "
                   "browser_read только если этого мало. Нажимай по тексту кнопки, поля "
                   "заполняй по подписи. Пароль, капчу и финальное подтверждение оставь "
                   "пользователю. Сайт: open -> fill('search', query) -> "
                   "click('Найти') -> read."),
    },
    "youtube": {
        "title": "YouTube",
        "keywords": ("ютуб", "youtube", "видео", "ролик", "клип", "песн"),
        "tools": ("browser_play_youtube",),
        "prompt": ("YouTube: «включи видео X» = browser_play_youtube(query=X), без "
                   "открытия главной страницы."),
    },
    "screen": {
        "title": "Экран",
        "keywords": ("экран", "монитор", "скриншот", "видишь", "видно", "ошибк",
                     "что открыто", "принять", "прими", "кнопк", "приглас", "матч",
                     "очередь", "игра", "accept"),
        "tools": ("see_screen", "click_text_on_screen"),
        "prompt": ("Экран: see_screen(question) — ответ по OCR активного окна; кнопку на "
                   "экране ищи click_text_on_screen(текст). Если команда ‘прими игру’ — сначала "
                   "see_screen, найди кнопку Принять/Accept и нажми click_text_on_screen('Принять'); "
                   "повторяй, пока не появится и не будет нажата нужная кнопка. "
                   "Ты можешь управлять мышью и клавиатурой: click_screen(x,y), "
                   "click_text_on_screen(text), type_text(text), press_keys(keys)."),
    },
    "discord": {
        "title": "Discord",
        "keywords": ("дискорд", "discord", "канал", "сообщени", "мут", "заглуши",
                     "наушник"),
        "tools": ("discord_action", "discord_send"),
        "prompt": ("Discord: mute/deafen — discord_action, сообщение — discord_send("
                   "channel, message). Подтверждение спросит исполнитель."),
    },
    "steam": {
        "title": "Steam",
        "keywords": ("стим", "steam", "магазин", "библиотек", "загрузк", "друз"),
        "tools": ("steam_action",),
        "prompt": "Steam: раздел — steam_action(action).",
    },
    "desktop": {
        "title": "Клавиатура и мышь",
        "keywords": ("нажми", "введи", "клавиш", "кликн", "координат", "мышь", "мышк",
                     "напечатай", "сочетани", "хоткей", "сообщен", "тг", "telegram",
                     "телеграм", "дискорд", "discord", "чат"),
        "tools": ("type_text", "press_keys", "click_screen"),
        "prompt": ("Клавиатура/мышь: press_keys('ctrl+s'), type_text(text), click_screen(x,y) — "
                   "только когда без них не обойтись; спросят подтверждение. Сообщение в чате: "
                   "launch_app -> type_text(text) -> press_keys('enter')."),
    },
    "files": {
        "title": "Файлы и поиск",
        "keywords": ("файл", "папк", "документ", "диплом", "фото", "фотк", "картинк",
                     "найди", "поиск", "погод", "новост", "курс"),
        "tools": ("search_and_open_file", "search_web"),
        "prompt": ("Поиск: файл на диске — search_and_open_file(query), интернет — "
                   "search_web(query). Не выдумывай результаты."),
    },
    "media": {
        "title": "Музыка и звук",
        "keywords": ("музык", "трек", "песн", "исполнител", "артист", "плейлист",
                     "пауза", "паузу", "плей", "стоп", "громкост", "громче", "тише",
                     "звук", "переключи трек"),
        "tools": ("play_music", "media_control", "control_volume"),
        "prompt": ("Музыка: конкретный трек или исполнитель — play_music(query), "
                   "например «кишлак пыль»; просто «включи музыку» — play_music() без "
                   "query. Пауза/продолжить/стоп — media_control, громкость — "
                   "control_volume."),
    },
    "power": {
        "title": "Питание и окна",
        "keywords": ("выключи компьютер", "выключи пк", "перезагруз", "блокир", "усни",
                     "сверни", "рабочий стол", "закрой приложен"),
        "tools": ("system_action", "minimize_all", "close_app"),
        "prompt": ("Питание: system_action(lock|sleep|shutdown|restart) — подтверждение "
                   "спросит исполнитель. Окна — minimize_all."),
    },
    "notes": {
        "title": "Заметки и напоминания",
        "keywords": ("заметк", "запомни", "запиши", "напомни", "буфер", "скопировал",
                     "скриншот"),
        "tools": ("save_note", "read_notes", "set_reminder", "read_clipboard",
                  "take_screenshot"),
        "prompt": ("Заметки: save_note(text) / read_notes(); напоминание — "
                   "set_reminder(when='через 15 минут'|'18:30', text); буфер — "
                   "read_clipboard(); снимок экрана — take_screenshot()."),
    },
}

                                                                          
                                                              
_FAMILY = {name: skill for skill, data in SKILLS.items() for name in data["tools"]}

_MAX_SKILL_PROMPTS = 2
_MAX_PROMPT_CHARS = 450


def _norm(text: str) -> str:
    return str(text or "").lower().replace("ё", "е")


def active_skills(text: str, prev_tools=()) -> list[str]:
    """Скилы, релевантные фразе (core — всегда первым)."""
    haystack = _norm(text)
    chosen = ["core"]
    for name, data in SKILLS.items():
        if name == "core":
            continue
        if any(keyword in haystack for keyword in data["keywords"]):
            chosen.append(name)
    for tool in prev_tools or ():
        skill = _FAMILY.get(tool)
        if skill and skill not in chosen:
            chosen.append(skill)
    return chosen


def select_tools(text: str, universe: list[dict], prev_tools=(), limit: int = 8,
                 history=()) -> list[dict]:
    """Инструменты под фразу: только из активных скилов (core — всегда)."""
    by_name = {tool["function"]["name"]: tool for tool in universe}
    skills = active_skills(" ".join([str(text or "")] + [str(item) for item in history]),
                           prev_tools)
    wanted: list[str] = []
    for name in skills:
        for tool in SKILLS[name]["tools"]:
            if tool in by_name and tool not in wanted:
                wanted.append(tool)
    core = [tool for tool in SKILLS["core"]["tools"] if tool in by_name]
    if limit is not None and limit > 0:
                                                                 
        keep = list(core)
        for name in wanted:
            if name in keep:
                continue
            if len(keep) >= max(limit, len(core)):
                break
            keep.append(name)
        wanted = keep
    return [tool for name, tool in by_name.items() if name in wanted]


def prompt_for(text: str, tool_names=(), prev_tools=()) -> str:
    """Склеить инструкции активных скилов (максимум 3, ~до 700 символов)."""
    skills = active_skills(text, prev_tools)
    lines = []
    for name in skills:
        prompt = SKILLS[name]["prompt"]
        if prompt:
            lines.append("• " + prompt)
        if len(lines) >= _MAX_SKILL_PROMPTS:
            break
    text_out = "\n".join(lines)
    return text_out[:_MAX_PROMPT_CHARS]


def names_of(tools: list[dict]) -> list[str]:
    return [tool["function"]["name"] for tool in tools]


def titles() -> list[str]:
    return [data["title"] for data in SKILLS.values()]

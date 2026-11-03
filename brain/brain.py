# brain/brain.py
"""LLM-мозг Джарвиса: цепочка fallback, агентный цикл, стриминг.

Что важно для голосового ассистента и учтено здесь:
- жёсткий таймаут на запрос (иначе один зависший провайдер вешает всё);
- «карантин» мёртвых кандидатов (404/401/403/429) с TTL, чтобы не платить
  за них на каждой команде;
- тело HTTP-ошибки читается (раньше гео-блок 403 не распознавался);
- поддержка нескольких tool_calls в одном ответе;
- актуальные дата/время в системном промпте (модель больше не выдумывает час);
- динамический набор инструментов: в запрос уходят только релевантные схемы;
- streaming: текст ответа отдаётся по мере генерации, TTS начинает раньше;
- контекст 2–3 последних реплик для уточнений;
- внешний текст (страницы, OCR) идёт в модель только как данные (common.guard);
- YandexGPT как резервный провайдер без VPN.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if __package__ in (None, ""):  # запуск как `python brain\brain.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from brain import skills
from common import guard, timefmt
from common.log import get_logger

log = get_logger("brain")

# --------------------------------------------------------------------------
# провайдеры
# --------------------------------------------------------------------------
_BASE_URLS = {
    "openrouter": config.OPENROUTER_BASE_URL,
    "groq": config.GROQ_BASE_URL,
    "ollama": config.OLLAMA_BASE_URL,
    "ollama_cloud": config.OLLAMA_CLOUD_BASE_URL,
    "deepseek": config.DEEPSEEK_BASE_URL,
    "mistral": config.MISTRAL_BASE_URL,
    "yandex": config.YANDEX_BASE_URL,
}
_EXTRA_HEADERS = {
    "openrouter": {"HTTP-Referer": "https://localhost", "X-Title": "Jarvis PC Assistant"},
}
_chain = list(config.BRAIN_CHAIN)
# Бесплатная Ollama Cloud beam — быстрый первый приоритет, как раньше.
# _chain.insert(0, ("ollama_cloud", "beam", "OLLAMA_API_KEY"))
_chain.insert(0, ("ollama_cloud", "beam", "OLLAMA_API_KEY"))  # восстановлено по запросу
if config.YANDEX_API_KEY and config.YANDEX_FOLDER_ID:
    # YandexGPT доступен из РФ без VPN: ставим сразу после Mistral, до
    # Groq/OpenRouter, которым обычно нужен прокси.
    _chain.insert(min(2, len(_chain)),
                  ("yandex", "gpt://{folder}/" + config.YANDEX_MODEL_KIND, "YANDEX_API_KEY"))
if config.LOCAL_ONLY:
    # Полный запрет облачных вызовов: только локальная модель.
    _chain = [("ollama", config.LOCAL_LLM_MODEL, "")]
elif config.LOCAL_LLM_ENABLED:
    local_candidate = ("ollama", config.LOCAL_LLM_MODEL, "")
    if config.LOCAL_LLM_FIRST:
        _chain.insert(0, local_candidate)
    else:
        _chain.append(local_candidate)
CHAIN = tuple(_chain)

# UI: preferred chain reordering. Mutable so GUI/commands can change
# priority without restarting Python.
ACTIVE_PROVIDER: str = ""
ACTIVE_MODEL: str = ""


def model_labels() -> list[str]:
    return [f"{provider}:{model}" for provider, model, _ in CHAIN]


def active_model_label() -> str:
    if not CHAIN:
        return ""
    if ACTIVE_PROVIDER and ACTIVE_MODEL:
        return f"{ACTIVE_PROVIDER}:{ACTIVE_MODEL}"
    provider, model, _ = CHAIN[0]
    return f"{provider}:{model}"


def set_active_model(label: str) -> str:
    global ACTIVE_PROVIDER, ACTIVE_MODEL, CHAIN
    labels = model_labels()
    try:
        idx = labels.index(label)
    except ValueError:
        idx = 0
    ACTIVE_PROVIDER, ACTIVE_MODEL, _key = CHAIN[idx]
    rest = list(CHAIN[:idx]) + list(CHAIN[idx + 1:])
    # rebuild the module-global, reordered so the chosen model becomes first
    CHAIN = (CHAIN[idx],) + tuple(rest)
    return active_model_label()


def ping_model(label: str, timeout_sec: float = 8.0) -> tuple[bool, str]:
    """Быстрая проверка доступности одной модели из цепочки."""
    entry = next((item for item in CHAIN if f"{item[0]}:{item[1]}" == label), None)
    if entry is None:
        entry = next((item for item in CHAIN if item[1] in label), None)
    if entry is None:
        return False, "не найдена"
    provider, model, key_name = entry
    if not _BASE_URLS.get(provider):
        return False, "нет base_url"
    key = ""
    if key_name:
        try:
            key = getattr(config, key_name, "") or ""
        except Exception:  # noqa: BLE001
            key = ""
    try:
        client = _openai().OpenAI(
            base_url=_BASE_URLS[provider], api_key=key,
            timeout=timeout_sec, max_retries=0,
        )
        extra = _extra_body(provider) or {}
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8, temperature=0.0, timeout=timeout_sec, **extra,
        )
        return True, (getattr(resp.choices[0].message, "content", "") or "ok")[:60]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:80]


# Инструменты, чей результат приходит из внешнего мира (интернет/экран) и
# поэтому не может считаться доверенным.
UNTRUSTED_TOOLS = frozenset({"browser_open", "browser_read", "browser_click",
                             "browser_play_youtube", "see_screen"})

GEO_MARKERS = (
    "security policy",
    "access denied",
    "access denied by security",
    "region",
    "country",
    "not available in",
    "unsupported location",
    "geo",
)
COOLDOWN_SEC = {
    "RATE_LIMIT": 60.0,
    "MODEL_NOT_FOUND": 1800.0,
    "AUTH": 300.0,
    "GEO_BLOCKED": 3600.0,
    "NETWORK": 15.0,
    "SERVER": 120.0,
    "BAD_REQUEST": 60.0,
    "BAD_ARGS": 0.0,
    "OTHER": 30.0,
}

_clients: dict[tuple[str, str], object] = {}
_openai_mod = None
_LIVE_IDX: int | None = None
_blocked_until: dict[int, float] = {}
_force_fail_used = False


def _stamp_line() -> str:
    """Дата/время — всегда В КОНЦЕ промпта.

    Провайдеры кэшируют префикс запроса (у DeepSeek скидка на кеш до 98%).
    Если отметка времени стоит в начале, смена минуты сбрасывает кеш всего
    системного промпта (~2800 токенов). В конце — кеш остаётся валидным.
    """
    return f"Сейчас {timefmt.prompt_stamp()}."


def _rules() -> str:
    """Статическое ядро системного промпта — максимально коротко.

    Всё, что относится к конкретной задаче (браузер, экран, Discord, заметки),
    живёт в скилах (brain/skills.py) и подмешивается, только когда скил активен:
    промпт в 4 раза короче и модель не отвлекается на чужие инструкции.
    """
    text = (
        "Ты — Джарвис, голосовой ассистент ПК. Отвечай по-русски, 1–2 предложения.\n"
        "1. Действие → вызов функции с точными аргументами; несколько → вызовы сразу.\n"
        "2. Нечего вызывать → короткий текст.\n"
        "2a. Факты о пользователе (имя, привычки) сохраняй через save_note.\n"
        "3. Не выдумывай факты, URL и время (время в последней строке); погоду/новости не сочиняй.\n"
        "4. Опасное (выключение/перезагрузка/сон) — функцией, подтверждение спросит исполнитель.\n"
        "5. Пошагово: вызвал → результат → следующий шаг.\n"
    )
    if config.CONTEXT_TURNS > 0:
        text += "6. Уточнения понимай по истории реплик.\n"
    if config.GUARD_ENABLED:
        text += "7. " + guard.note() + "\n"
    return text


def _system_prompt() -> str:
    """Полный системный промпт с датой (для одноразовых вызовов)."""
    return f"{_rules()}\n{_stamp_line()}"


TOOLS = [
    {"type": "function", "function": {"name": "launch_app",
        "description": "Запускает приложение или игру по имени.",
        "parameters": {"type": "object", "properties": {
            "app_name": {"type": "string", "description": "Например: 'chrome', 'discord', 'dota 2', 'spotify'"}},
            "required": ["app_name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "open_website",
        "description": "Открывает сайт/ссылку в браузере.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "Полный URL, например 'https://youtube.com'"}},
            "required": ["url"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "control_volume",
        "description": "Управляет общей громкостью системы.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["mute", "unmute", "up", "down", "set"]},
            "level": {"type": "integer", "description": "0-100, только для action='set'"}},
            "required": ["action"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "media_control",
        "description": "Управляет воспроизведением медиа.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["play", "pause", "next", "prev", "stop"]}},
            "required": ["action"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "system_action",
        "description": "Действия питания/сессии. Исполнитель подтвердит опасные.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["lock", "sleep", "shutdown", "restart"]}},
            "required": ["action"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_web",
        "description": "Открывает поиск в браузере.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Поисковый запрос"}},
            "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_and_open_file",
        "description": "Ищет файл или папку на компьютере и открывает найденное.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Имя файла или тип: 'диплом', 'фото', 'скриншот'"}},
            "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "close_app",
        "description": "Закрывает приложение или игру по имени.",
        "parameters": {"type": "object", "properties": {
            "app_name": {"type": "string", "description": "Например: 'discord', 'chrome', 'dota 2'"}},
            "required": ["app_name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_datetime",
        "description": "Текущие дата и/или время на компьютере.",
        "parameters": {"type": "object", "properties": {
            "what": {"type": "string", "enum": ["time", "date", "both"]}},
            "required": ["what"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "minimize_all",
        "description": "Сворачивает все окна и показывает рабочий стол.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
]


def _agent_prompt(skill_text: str = "") -> str:
    """Промпт агента: короткое ядро + инструкции только активных скилов."""
    parts = [_rules()]
    if skill_text:
        parts.append("Как это делать:\n" + skill_text + "\n")
    parts.append(_stamp_line())
    return "\n".join(parts)


AGENT_TOOLS = TOOLS + [    {"type": "function", "function": {"name": "see_screen",
        "description": "Посмотреть на экран пользователя и ответить на вопрос по изображению.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string", "description": "Что именно найти/описать на экране"}},
            "required": ["question"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "browser_open",
        "description": "Открыть сайт в управляемом браузере Jarvis.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "URL или домен"}},
            "required": ["url"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "browser_read",
        "description": "Прочитать текущую страницу браузера: текст, поля формы, кнопки.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "browser_click",
        "description": "Нажать кнопку или ссылку по её тексту на текущей странице.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Текст кнопки/ссылки"}},
            "required": ["text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "browser_fill",
        "description": "Заполнить поле формы на текущей странице.",
        "parameters": {"type": "object", "properties": {
            "field": {"type": "string", "description": "Имя, placeholder или подпись поля"},
            "value": {"type": "string", "description": "Значение"}},
            "required": ["field", "value"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "browser_play_youtube",
        "description": "Найти на YouTube видео по запросу и включить первый результат.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Название видео или поисковый запрос"}},
            "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "browser_back",
        "description": "Вернуться на предыдущую страницу браузера.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "focus_window",
        "description": "Переключить фокус на окно по части заголовка.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Часть заголовка окна"}},
            "required": ["title"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "click_screen",
        "description": "Кликнуть мышью в координатах экрана.",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "click_text_on_screen",
        "description": "Найти текст/кнопку на экране (OCR) и кликнуть по нему.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Видимый текст кнопки/ссылки"}},
            "required": ["text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "type_text",
        "description": "Ввести текст в активное окно (через буфер обмена).",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}},
            "required": ["text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "press_keys",
        "description": "Нажать сочетание клавиш, например 'ctrl+shift+m'.",
        "parameters": {"type": "object", "properties": {
            "keys": {"type": "string"}},
            "required": ["keys"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "discord_action",
        "description": "Управление Discord: open/focus/mute/unmute/deafen/undeafen.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["open", "focus", "mute", "unmute", "deafen", "undeafen"]}},
            "required": ["action"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "discord_send",
        "description": "Отправить сообщение в канал Discord (с подтверждением).",
        "parameters": {"type": "object", "properties": {
            "channel": {"type": "string"}, "message": {"type": "string"}},
            "required": ["channel", "message"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "steam_action",
        "description": "Открыть раздел Steam: store/library/friends/downloads/settings/profile/open.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["store", "library", "friends", "downloads", "settings", "profile", "open"]}},
            "required": ["action"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "play_music",
        "description": "Включить музыку: конкретный трек или исполнителя. Ищет в локальной "
                       "папке и в основном браузере (VK/Яндекс.Музыка).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string",
                      "description": "Исполнитель и/или название: «кишлак пыль», «мирный воин». "
                                     "Пусто — просто открыть музыку"}},
            "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "save_note",
        "description": "Записать заметку в файл на диске.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Текст заметки"}},
            "required": ["text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_notes",
        "description": "Прочитать последние заметки.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "Сколько последних, 1-20"}},
            "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "set_reminder",
        "description": "Напомнить о чём-то позже: через время или в конкретный час.",
        "parameters": {"type": "object", "properties": {
            "when": {"type": "string", "description": "«через 15 минут», «18:30», «в 7 утра»"},
            "text": {"type": "string", "description": "О чём напомнить"}},
            "required": ["when", "text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_clipboard",
        "description": "Прочитать текст из буфера обмена.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "take_screenshot",
        "description": "Сделать скриншот экрана и сохранить в файл.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
]


def _local_agent_prompt(skill_text: str = "") -> str:
    """То же, но для локальной модели на CPU: ещё короче и без лишних шагов."""
    parts = [_rules(), "Работаешь локально на CPU: минимум шагов, без рассуждений вслух.\n"]
    if skill_text:
        parts.append(skill_text + "\n")
    parts.append(_stamp_line())
    return "".join(parts)


LOCAL_TOOLS = [
    tool for tool in AGENT_TOOLS
    if tool["function"]["name"] in {
        "launch_app", "close_app", "control_volume", "get_datetime", "see_screen",
        "browser_open", "browser_click", "browser_fill",
        "browser_play_youtube", "steam_action", "discord_action", "discord_send",
    }
]

# --------------------------------------------------------------------------
# динамический набор инструментов (скилы: brain/skills.py)
# --------------------------------------------------------------------------
def select_tools(text: str, *, local: bool = False, history=(), prev_tools=(),
                 limit: int | None = None) -> list[dict]:
    """Инструменты под фразу — только из активных скилов (brain/skills.py).

    Все 25 схем — ~2800 токенов в каждом запросе и лишние варианты для модели.
    Скилы оставляют 3–8 схем и подмешивают 1–3 строки инструкции.
    """
    universe = LOCAL_TOOLS if local else AGENT_TOOLS
    cap = config.TOOLS_MAX if limit is None else int(limit)
    if cap <= 0:
        return list(universe)
    return skills.select_tools(text, universe, prev_tools=prev_tools, limit=cap,
                               history=history)


def tool_names(text: str, **kwargs) -> list[str]:
    """Имена инструментов, которые уйдут модели для этой фразы (для логов/тестов)."""
    return [tool["function"]["name"] for tool in select_tools(text, **kwargs)]


_ACTION_HINTS = (
    "открой", "закрой", "запусти", "включи", "выключи", "найди", "покажи", "нажми",
    "заполни", "отправь", "посмотри", "переключи", "поставь", "скачай", "ответ", "реплик", "последнее", "зайди",
    "сверни", "заблокируй", "перезагруз", "громче", "тише", "видео", "ютуб",
    "youtube", "стим", "steam", "дискорд", "discord", "прочитай", "введи",
    "кликни", "зарегистр", "перейди", "выключи компьютер",
    # «что на экране», «что в браузере» — это тоже действие (см. see_screen).
    "экран", "монитор", "скриншот", "страниц", "вкладк",
)


def _looks_like_action(text: str) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in _ACTION_HINTS)


# --------------------------------------------------------------------------
# ленивый openai (старт без сети/без пакета не должен падать)
# --------------------------------------------------------------------------
def _openai():
    global _openai_mod
    if _openai_mod is None:
        import openai as module

        _openai_mod = module
    return _openai_mod


_EXTRA_CACHE: dict = {"raw": None, "value": {}}
# Учёт расхода токенов и денег (для логов и «сколько стоит команда»).
_USAGE: dict = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}


def _record_usage(prompt_tokens, completion_tokens) -> None:
    try:
        tokens_in = int(prompt_tokens or 0)
        tokens_out = int(completion_tokens or 0)
    except (TypeError, ValueError):
        return
    if not tokens_in and not tokens_out:
        return
    cost = (tokens_in * config.PRICE_IN + tokens_out * config.PRICE_OUT) / 1_000_000
    _USAGE["calls"] += 1
    _USAGE["tokens_in"] += tokens_in
    _USAGE["tokens_out"] += tokens_out
    _USAGE["cost"] += cost
    log.debug("Расход: %d+%d ток. = %.4f ₽ (всего %.2f ₽)", tokens_in, tokens_out, cost,
              _USAGE["cost"])


def usage_snapshot() -> dict:
    """Текущие счётчики: вызовы, токены и стоимость в ₽."""
    return {**_USAGE, "cost": round(_USAGE["cost"], 4)}


def reset_usage() -> None:
    _USAGE.update(calls=0, tokens_in=0, tokens_out=0, cost=0.0)


def _extra_body(provider: str) -> dict | None:
    """Доп. параметры запроса (JARVIS_BRAIN_EXTRA) для основного провайдера.

    Нужно для «думающих» моделей у агрегаторов: `{"reasoning": {"effort": "none"}}`
    выключает рассуждение, и первый токен приходит примерно вдвое быстрее.
    """
    if getattr(config, "THINKING_MODE", False):
        return {"reasoning": {"effort": "high"}}
    raw = config.BRAIN_EXTRA_JSON
    if not raw or provider != "deepseek":
        return None
    if _EXTRA_CACHE["raw"] != raw:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("JARVIS_BRAIN_EXTRA — не JSON: %r", raw)
            value = {}
        _EXTRA_CACHE.update(raw=raw, value=value if isinstance(value, dict) else {})
    return _EXTRA_CACHE["value"] or None


def _retryable_bad_request(exc: Exception, kwargs: dict) -> bool:
    """400 на неприменимый параметр (reasoning/max_tokens) — можно повторить без него."""
    if _classify(exc) != "BAD_REQUEST":
        return False
    body = _body(exc).lower()
    if "extra_body" in kwargs and any(word in body for word in
                                      ("reasoning", "extra", "unsupported", "unknown", "additional")):
        kwargs.pop("extra_body")
        return True
    if "max_tokens" in kwargs and "max_tokens" in body:
        kwargs.pop("max_tokens")
        return True
    if "stream_options" in kwargs and any(word in body for word in
                                          ("stream_options", "include_usage", "unsupported", "unknown")):
        kwargs.pop("stream_options")
        return True
    if "tool_choice" in kwargs and any(word in body for word in
                                       ("tool_choice", "required", "unsupported", "unknown")):
        kwargs.pop("tool_choice")
        return True
    return False


def _model_uri(provider: str, model: str) -> str:
    if provider == "yandex" and "{folder}" in model:
        return model.replace("{folder}", config.YANDEX_FOLDER_ID)
    return model


def _make_client(provider: str, api_key: str):
    cache_key = (provider, api_key[-6:] if api_key else "", config.PROXY_URL)
    client = _clients.get(cache_key)
    if client is not None:
        return client
    module = _openai()
    headers = dict(_EXTRA_HEADERS.get(provider) or {})
    if config.RELAY_TOKEN and provider != "ollama":
        headers["X-Relay-Token"] = config.RELAY_TOKEN
    kwargs = dict(
        api_key=api_key or "ollama",
        base_url=_BASE_URLS[provider],
        default_headers=headers or None,
        timeout=(config.OLLAMA_TIMEOUT_SEC if provider == "ollama"
                else config.BRAIN_TIMEOUT_SEC),
        max_retries=0,  # свои повторы/цепочка, без скрытых 600-секундных ретраев
    )
    if config.PROXY_URL and provider != "ollama":
        import httpx

        kwargs["http_client"] = httpx.Client(
            proxy=config.PROXY_URL,
            timeout=(config.OLLAMA_TIMEOUT_SEC if provider == "ollama"
                     else config.BRAIN_TIMEOUT_SEC),
            trust_env=False,
        )
    client = module.OpenAI(**kwargs)
    _clients[cache_key] = client
    return client


def _body(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    text = ""
    if response is not None:
        for attr in ("text", "content"):
            try:
                value = getattr(response, attr)
                if isinstance(value, bytes):
                    value = value.decode("utf-8", "replace")
                if value:
                    text = str(value)
                    break
            except Exception:
                continue
    if not text:
        body = getattr(exc, "body", None)
        if isinstance(body, (dict, list)):
            try:
                text = json.dumps(body, ensure_ascii=False)
            except Exception:
                text = str(body)
        elif body:
            text = str(body)
    if not text:
        text = str(exc)
    return text


def _classify(exc: Exception) -> str:
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    body = _body(exc).lower()

    if status == 429 or name == "RateLimitError":
        return "RATE_LIMIT"
    if name in {"APITimeoutError", "APIConnectionError", "ConnectError", "ReadTimeout", "ConnectTimeout"}:
        return "NETWORK"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "NETWORK"
    if status == 404 or name == "NotFoundError":
        return "MODEL_NOT_FOUND"
    if status in (401, 403) or name in {"AuthenticationError", "PermissionDeniedError"}:
        if any(marker in body for marker in GEO_MARKERS):
            return "GEO_BLOCKED"
        return "AUTH"
    if status is not None and status >= 500:
        return "SERVER"
    if status == 400:
        return "BAD_REQUEST"
    return "OTHER"


def _mark_failure(index: int, kind: str) -> None:
    cooldown = COOLDOWN_SEC.get(kind, 30.0)
    if cooldown > 0:
        _blocked_until[index] = time.monotonic() + cooldown


def _build_payload(content: str, calls: list[dict]) -> dict:
    """Единый формат ответа модели для одиночных и потоковых вызовов."""
    raw = {"content": content, "tool_calls": calls}
    if calls:
        if len(calls) == 1:
            return {"type": "tool", **calls[0], "raw": raw}
        return {"type": "tools", "calls": calls, "raw": raw}
    reply = (content or "").strip()
    if len(reply) > config.BRAIN_CHAT_MAX_CHARS:
        reply = reply[: config.BRAIN_CHAT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return {"type": "chat", "reply": reply, "raw": {"content": reply, "tool_calls": []}}


def _prepare_ollama_messages(messages: list) -> list:
    """Ollama native ждёт arguments объектом и tool_name в ответе инструмента."""
    tool_names: dict[str, str] = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if call.get("id"):
                tool_names[call["id"]] = function.get("name", "")
    prepared = []
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = dict(call.get("function") or {})
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                function["arguments"] = arguments if isinstance(arguments, dict) else {}
                calls.append({"function": function})
            prepared.append({"role": "assistant", "content": message.get("content") or "",
                             "tool_calls": calls})
        elif role == "tool":
            prepared.append({
                "role": "tool",
                "content": message.get("content") or "",
                "tool_name": tool_names.get(message.get("tool_call_id", ""), ""),
            })
        else:
            prepared.append({"role": role, "content": message.get("content") or ""})
    return prepared


def _ollama_chat(model: str, messages: list, use_tools: bool, tools: list | None):
    """Нативный Ollama API: только так надёжно передаются num_gpu/num_ctx."""
    root = config.OLLAMA_BASE_URL.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    payload = {
        "model": model,
        "messages": _prepare_ollama_messages(messages),
        "stream": False,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
        "options": {
            "num_gpu": config.OLLAMA_NUM_GPU,
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_thread": config.OLLAMA_THREADS,
            "temperature": config.BRAIN_TEMPERATURE,
            "num_predict": config.BRAIN_MAX_TOKENS,
        },
    }
    if use_tools:
        payload["tools"] = tools or TOOLS
    request = urllib.request.Request(
        root + "/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.OLLAMA_TIMEOUT_SEC) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 404 or "not found" in body.lower():
            return "MODEL_NOT_FOUND", f"модель {model}: {body}"
        return "OTHER", f"HTTP {exc.code}: {body}"
    except Exception as exc:  # noqa: BLE001
        return "NETWORK", f"{type(exc).__name__}: {exc}"

    message = data.get("message") or {}
    content = _sanitize_reply((message.get("content") or "").strip())
    calls = message.get("tool_calls") or []
    parsed = []
    for index, call in enumerate(calls):
        function = call.get("function") or {}
        name = function.get("name") or ""
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return "BAD_ARGS", f"невалидный JSON аргументов: {arguments!r}"
        if not isinstance(arguments, dict):
            return "BAD_ARGS", f"аргументы не объект: {arguments!r}"
        parsed.append({"id": call.get("id") or f"call_{index}", "name": name, "args": arguments})
    return "OK", _build_payload(content, parsed)


def _yandex_chat(model: str, api_key: str, messages: list, use_tools: bool, tools: list | None):
    """YandexGPT через OpenAI-совместимый эндпоинт (заголовок Api-Key)."""
    if not config.YANDEX_FOLDER_ID:
        return "AUTH", "не задан YANDEX_FOLDER_ID"
    url = config.YANDEX_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": _model_uri("yandex", model),
        "messages": messages,
        "temperature": config.BRAIN_TEMPERATURE,
        "max_tokens": config.BRAIN_MAX_TOKENS,
    }
    if use_tools:
        payload.update(tools=tools or TOOLS, tool_choice="auto")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {api_key}",
        },
        method="POST",
    )
    try:
        from common import net

        with urllib.request.urlopen(request, timeout=config.BRAIN_TIMEOUT_SEC,
                                    context=net.ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        kind = {401: "AUTH", 403: "AUTH", 404: "MODEL_NOT_FOUND",
                429: "RATE_LIMIT"}.get(exc.code, "BAD_REQUEST" if exc.code < 500 else "SERVER")
        return kind, f"HTTP {exc.code}: {body}"
    except Exception as exc:  # noqa: BLE001
        return "NETWORK", f"{type(exc).__name__}: {exc}"

    choices = data.get("choices") or []
    if not choices:
        return "OTHER", f"пустой ответ: {json.dumps(data, ensure_ascii=False)[:200]}"
    message = choices[0].get("message") or {}
    content = _sanitize_reply((message.get("content") or "").strip())
    parsed = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return "BAD_ARGS", f"невалидный JSON аргументов: {arguments!r}"
        parsed.append({"id": call.get("id") or f"call_{index}",
                       "name": function.get("name") or "", "args": arguments or {}})
    return "OK", _build_payload(content, parsed)


def _stream_round(client, kwargs: dict, on_delta):
    """Один потоковый проход. -> (kind, payload_или_исключение, частичный_текст)."""
    parts: list[str] = []
    calls: dict[int, dict] = {}
    try:
        stream = client.chat.completions.create(**kwargs)
        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:  # последний кусок с include_usage
                _record_usage(getattr(usage, "prompt_tokens", 0),
                              getattr(usage, "completion_tokens", 0))
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            text = getattr(delta, "content", None)
            if text:
                parts.append(text)
                try:
                    on_delta(text)
                except Exception:  # noqa: BLE001 - сбой TTS не должен рвать ответ
                    log.exception("on_delta")
            for call in (getattr(delta, "tool_calls", None) or []):
                index = getattr(call, "index", 0) or 0
                slot = calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
                if getattr(call, "id", None):
                    slot["id"] = call.id
                function = getattr(call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        slot["name"] = function.name
                    if getattr(function, "arguments", None):
                        slot["arguments"] += function.arguments
    except Exception as exc:  # noqa: BLE001
        return "EXC", exc, "".join(parts)

    parsed = []
    for index, slot in calls.items():
        if not slot.get("name"):
            continue
        try:
            args = json.loads(slot.get("arguments") or "{}")
        except json.JSONDecodeError:
            return "BAD_ARGS", f"невалидный JSON аргументов: {slot.get('arguments')!r}", ""
        if not isinstance(args, dict):
            return "BAD_ARGS", f"аргументы не объект: {args!r}", ""
        parsed.append({"id": slot.get("id") or f"call_{index}", "name": slot["name"], "args": args})
    return "OK", _build_payload("".join(parts), parsed), "".join(parts)


def _stream_chat(provider: str, model: str, api_key: str, messages: list,
                 use_tools: bool, tools: list | None, on_delta, tool_choice: str | None = None):
    """Потоковый вызов OpenAI-совместимого API.

    Каждый кусок текста сразу уходит в on_delta: TTS начинает говорить,
    не дожидаясь конца ответа (первый звук ~1 с вместо 3–5 с).
    """
    try:
        client = _make_client(provider, api_key)
    except Exception as exc:  # noqa: BLE001
        return "NETWORK", f"клиент {provider}: {type(exc).__name__}: {exc}"

    kwargs = dict(
        model=_model_uri(provider, model),
        messages=messages,
        temperature=config.BRAIN_TEMPERATURE,
        max_tokens=config.BRAIN_MAX_TOKENS,
        timeout=config.BRAIN_TIMEOUT_SEC,
        stream=True,
        # Нужно, чтобы провайдер прислал usage и мы считали расход токенов.
        stream_options={"include_usage": True},
    )
    extra = _extra_body(provider)
    if extra:
        kwargs["extra_body"] = extra
    if use_tools:
        kwargs.update(tools=tools or TOOLS, tool_choice=tool_choice or "auto")

    for attempt in range(2):
        kind, result, partial = _stream_round(client, kwargs, on_delta)
        if kind == "OK":
            return "OK", result
        if kind == "BAD_ARGS":
            return kind, result
        exc = result
        if partial.strip():
            # Часть ответа уже прозвучала: отдаём её, чтобы не повторять.
            return "PARTIAL", _build_payload(partial.strip(), [])
        if attempt == 0 and isinstance(exc, Exception) and _retryable_bad_request(exc, kwargs):
            log.info("Повтор без неподдерживаемого параметра (%s)", type(exc).__name__)
            continue
        return _classify(exc), f"{type(exc).__name__}: {str(exc)[:160]}"
    return "OTHER", "не удалось получить ответ"


def _call_once(provider: str, model: str, api_key: str, messages: list, use_tools: bool,
               tools: list | None = None, on_delta=None, tool_choice: str | None = None):
    global _force_fail_used
    if os.getenv("FORCE_FAIL_FIRST") == "1" and not _force_fail_used:
        _force_fail_used = True
        return "RATE_LIMIT", "forced 429 (тест цепочки)"
    if provider == "ollama":
        return _ollama_chat(model, messages, use_tools, tools)
    if provider == "yandex":
        return _yandex_chat(model, api_key, messages, use_tools, tools)
    if on_delta is not None:
        return _stream_chat(provider, model, api_key, messages, use_tools, tools, on_delta,
                            tool_choice=tool_choice)

    client = None
    try:
        client = _make_client(provider, api_key)
    except Exception as exc:  # noqa: BLE001 - прокси/httpx может не подняться
        return "NETWORK", f"клиент {provider}: {type(exc).__name__}: {exc}"
    kwargs = dict(
        model=_model_uri(provider, model),
        messages=messages,
        temperature=config.BRAIN_TEMPERATURE,
        max_tokens=config.BRAIN_MAX_TOKENS,
        timeout=config.BRAIN_TIMEOUT_SEC,
    )
    extra = _extra_body(provider)
    if extra:
        kwargs["extra_body"] = extra
    if use_tools:
        kwargs.update(tools=tools or TOOLS, tool_choice=tool_choice or "auto")

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - классифицируем сами
        if not _retryable_bad_request(exc, kwargs):
            return _classify(exc), f"{type(exc).__name__}: {str(exc)[:160]}"
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc2:  # noqa: BLE001
            return _classify(exc2), f"{type(exc2).__name__}: {_body(exc2)[:160]}"

    usage = getattr(response, "usage", None)
    if usage is not None:
        _record_usage(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))

    message = response.choices[0].message
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        parsed = []
        for index, call in enumerate(calls):
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                return "BAD_ARGS", f"невалидный JSON аргументов: {call.function.arguments!r}"
            if not isinstance(args, dict):
                return "BAD_ARGS", f"аргументы не объект: {args!r}"
            call_id = getattr(call, "id", None) or f"call_{index}"
            parsed.append({"id": call_id, "name": call.function.name, "args": args})
        return "OK", _build_payload(message.content or "", parsed)
    return "OK", _build_payload(message.content or "", [])


def _run_chain(messages: list, use_tools: bool = True, tools: list | None = None,
               on_delta=None, tool_choice: str | None = None):
    """Прогнать готовый список сообщений по цепочке провайдеров.

    on_delta (если задан) включает стриминг у первого живого кандидата;
    запасные провайдеры отвечают обычным запросом, чтобы не дублировать звук.
    """
    global _LIVE_IDX
    started = time.perf_counter()
    order = list(range(len(CHAIN)))
    if _LIVE_IDX is not None and _LIVE_IDX in order:
        order.remove(_LIVE_IDX)
        order.insert(0, _LIVE_IDX)

    now = time.monotonic()
    last_error = "все кандидаты исчерпаны"
    attempts = 0
    stream_delta = on_delta

    for index in order:
        provider, model, key_var = CHAIN[index]
        api_key = os.getenv(key_var) if key_var else ""
        if provider == "ollama":
            api_key = api_key or "ollama"
        if not api_key:
            last_error = f"{provider}:{model} -> нет {key_var}"
            continue
        if _blocked_until.get(index, 0.0) > now:
            continue

        attempts += 1
        kind, payload = _call_once(provider, model, api_key, messages, use_tools, tools,
                                   on_delta=stream_delta, tool_choice=tool_choice)
        stream_delta = None  # дальше — без стриминга
        if kind in {"OK", "PARTIAL"}:
            _LIVE_IDX = index
            if kind == "OK":
                _blocked_until.pop(index, None)
            return payload, time.perf_counter() - started, f"{provider}:{_model_uri(provider, model)}"

        last_error = f"{provider}:{model} -> {kind}: {str(payload)[:160]}"
        _mark_failure(index, kind)

    # Все кандидаты в карантине: сбрасываем и пробуем ещё раз (сеть могла вернуться).
    if attempts == 0 and _blocked_until:
        _blocked_until.clear()
        return _run_chain(messages, use_tools=use_tools, tools=tools, on_delta=on_delta,
                          tool_choice=tool_choice)

    return {"type": "error", "detail": last_error}, time.perf_counter() - started, "none"


def call_with_fallback(text: str, use_tools: bool = True):
    """Вернуть (payload, elapsed_sec, who). Совместимо с прежним API."""
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": text},
    ]
    return _run_chain(messages, use_tools=use_tools, tools=TOOLS)


def _local_chat_prompt() -> str:
    return (
        "Ты — Джарвис, голосовой ассистент на ПК. Отвечай ТОЛЬКО по-русски, кратко, "
        "1–2 предложениями, без иероглифов и других языков. Не выдумывай факты, время, "
        "погоду и новости. Если нужен свежий факт или действие — предложи открыть браузер."
    )


def _chat_prompt() -> str:
    """Короткий промпт для болталки: ~40 токенов вместо ~460.

    Схем инструментов здесь нет, длинные правила не нужны, а каждый лишний
    токен промпта — это +0.4 с до первого звука (замерено на AITUNNEL).
    """
    parts = [
        "Ты — Джарвис, голосовой ассистент пользователя на его ПК. "
        "Время и дату бери только из последней строки этого сообщения.",
        "Отвечай по-русски коротко: 1–2 предложения, это озвучивается голосом, "
        "без списков и форматирования. Не выдумывай факты, время, погоду и новости: "
        "если нужны свежие данные или действие на компьютере — скажи, что открыть "
        "или сделать, и пользователь попросит командой.",
    ]
    if config.GUARD_ENABLED:
        parts.append(guard.note())
    parts.append(_stamp_line())
    return "\n".join(parts)


_BAD_SCRIPTS = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")


def _sanitize_reply(text: str) -> str:
    """Qwen иногда дописывает китайский/корейский мусор — вырезаем его."""
    text = _BAD_SCRIPTS.split(text or "", maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text).strip(" ,;:-—")
    return text


# --------------------------------------------------------------------------
# стриминг -> TTS
# --------------------------------------------------------------------------
_SENTENCE_END = re.compile(r"[.!?…](\s|$)")


class StreamChunker:
    """Режет поток токенов LLM на фразы для озвучки.

    Копим до конца предложения (но не меньше min_chars и не больше max_chars),
    чтобы TTS не проговаривал обрывки по два слова.
    """

    def __init__(self, emit, min_chars: int | None = None, max_chars: int | None = None):
        self._emit = emit
        self.min_chars = max(1, int(min_chars if min_chars is not None
                                    else config.STREAM_MIN_CHARS))
        self.max_chars = max(self.min_chars + 20, int(max_chars if max_chars is not None
                                                      else config.STREAM_MAX_CHARS))
        self.buffer = ""
        self.emitted: list[str] = []

    @property
    def spoke(self) -> bool:
        return bool(self.emitted)

    def feed(self, delta: str) -> None:
        self.buffer += str(delta or "")
        while True:
            cut = self._cut_at()
            if cut is None:
                return
            chunk, self.buffer = self.buffer[:cut], self.buffer[cut:]
            self._push(chunk)

    def flush(self) -> None:
        chunk, self.buffer = self.buffer, ""
        self._push(chunk)

    def _push(self, chunk: str) -> None:
        chunk = chunk.strip()
        if len(chunk) < 2:
            if chunk:
                self.buffer = (chunk + " " + self.buffer).strip()
            return
        self.emitted.append(chunk)
        try:
            self._emit(chunk)
        except Exception:  # noqa: BLE001
            log.exception("Сбой озвучки фразы")

    def _cut_at(self) -> int | None:
        # Первую фразу отдаём короче: звук должен появиться как можно раньше.
        minimum = self.min_chars if self.emitted else max(4, self.min_chars // 3)
        if len(self.buffer) < minimum:
            return None
        for match in _SENTENCE_END.finditer(self.buffer):
            if match.end() >= minimum:
                return match.end()
        if len(self.buffer) >= self.max_chars:
            window = self.buffer[: self.max_chars]
            cut = max(window.rfind(" "), window.rfind(","), window.rfind(";"), window.rfind(":"))
            return cut + 1 if cut > self.min_chars // 2 else self.max_chars
        return None


# --------------------------------------------------------------------------
# агентный цикл
# --------------------------------------------------------------------------
def _history_messages(history) -> list[dict]:
    """Короткий контекст: последние 2–3 пары реплик (без tool_calls)."""
    if not history or config.CONTEXT_TURNS <= 0:
        return []
    messages: list[dict] = []
    for item in list(history)[-config.CONTEXT_TURNS:]:
        if isinstance(item, dict):
            user_text = item.get("user") or item.get("text") or ""
            answer = item.get("assistant") or item.get("reply") or ""
        else:
            user_text, answer = (list(item) + ["", ""])[:2]
        if not user_text:
            continue
        messages.append({"role": "user", "content": str(user_text)[:400]})
        if answer:
            messages.append({"role": "assistant", "content": str(answer)[:400]})
    return messages


def _history_tools(history) -> tuple[str, ...]:
    names: list[str] = []
    for item in list(history or ())[-config.CONTEXT_TURNS:]:
        if isinstance(item, dict):
            names.extend(item.get("tools") or ())
    return tuple(dict.fromkeys(names))


def _tool_message(name: str, result: str) -> str:
    """Результат инструмента для модели. Внешний контент — строго как данные."""
    text = str(result)
    if name in UNTRUSTED_TOOLS and config.GUARD_ENABLED:
        return guard.guard_external(text, source=name, limit=2500)
    return text[:2500]


def run_agent(text: str, executor, max_steps: int | None = None, on_step=None,
              history=None, on_sentence=None, on_thought=None) -> dict:
    """Агентный цикл: LLM вызывает инструменты, получает результаты, идёт дальше.

    executor(name, args) -> (текстовый_результат, ok: bool)
    on_sentence(chunk) — озвучка фраз по мере генерации (стриминг).
    history — прошлые реплики [(user, assistant), ...] или dict'ы с "tools".
    """
    started = time.perf_counter()
    collected_thoughts: list[str] = []
    local_mode = config.LOCAL_ONLY or (config.LOCAL_LLM_ENABLED and config.LOCAL_LLM_FIRST)
    chunker = StreamChunker(on_sentence) if on_sentence is not None else None

    def _thought_callback(text: str) -> None:
        text = (text or "").strip()
        if text:
            collected_thoughts.append(text)
        if on_thought is not None:
            try:
                on_thought(text)
            except Exception:  # noqa: BLE001
                log.exception("on_thought")
    if not config.STREAM_TTS and chunker is not None:
        chunker = None

    def emit_delta(delta: str) -> None:
        if chunker is not None:
            chunker.feed(delta)

    def finish(payload: dict, who: str, reply: str | None = None) -> dict:
        if chunker is not None:
            chunker.flush()
        result = dict(payload)
        if collected_thoughts:
            result["thoughts"] = list(collected_thoughts)
        result["_via"] = who
        result["_elapsed_sec"] = round(time.perf_counter() - started, 2)
        if chunker is not None:
            result["_spoken"] = "\n".join(chunker.emitted)
        if reply is not None:
            result["reply"] = reply
        return result

    history_messages = _history_messages(history)

    # 1. Болталка/вопрос без действий: без инструментов, потоково — быстрый
    #    первый звук и никаких схем функций в промпте.
    if (not local_mode and config.CHAT_FASTPATH and not _looks_like_action(text)):
        messages = ([{"role": "system", "content": _chat_prompt()}]
                    + history_messages + [{"role": "user", "content": text}])
        payload, _elapsed, who = _run_chain(messages, use_tools=False, on_delta=emit_delta)
        if payload.get("type") == "chat":
            return finish(payload, who, reply=(payload.get("reply") or "").strip())
        # Модель всё же захотела инструмент (или не ответила) — идём обычным
        # агентным путём: там схемы функций и исполнитель.
        log.info("Чат-путь не дал ответа (%s) — переключаюсь на агентный",
                 payload.get("type") or payload.get("detail"))

    if local_mode and not _looks_like_action(text):
        payload, _elapsed, who = _run_chain(
            [{"role": "system", "content": _local_chat_prompt()}]
            + history_messages + [{"role": "user", "content": text}],
            use_tools=False,
            on_delta=emit_delta,
        )
        if payload.get("type") == "error":
            return finish({"type": "error", "detail": payload.get("detail", "мозг недоступен")},
                          who)
        return finish({"type": "chat", "reply": (payload.get("reply") or "").strip()}, who)

    # 2. Агентный цикл с динамическим набором инструментов.
    universe = LOCAL_TOOLS if local_mode else AGENT_TOOLS
    selected = select_tools(
        text,
        local=local_mode,
        history=[str(item) for item in (history or ())],
        prev_tools=_history_tools(history),
    )
    tools = selected
    skill_text = "" if getattr(config, "NO_SCRIPTS_MODE", False) else skills.prompt_for(
        text, prev_tools=_history_tools(history))
    system_prompt = _local_agent_prompt(skill_text) if local_mode else _agent_prompt(skill_text)
    if getattr(config, "THINKING_MODE", False):
        system_prompt += ("\n\nРежим размышления включён: сделай явный, многошаговый анализ:\n"
                          "1) понять намерение пользователя и возможные неочевидные цели;\n"
                          "2) перечислить релевантные инструменты и выбрать лучший;\n"
                          "3) продумать последовательность действий и риски ошибок;\n"
                          "4) сделать допущения и, при неоднозначности, уточнить вопрос;\n"
                          "5) только после этого выполнить действия.\n"
                          "Перед финальным ответом ответь себе: ‘почему этот способ лучше’, "
                          "‘что может пойти не так’, ‘как проверить результат’.\n"
                          "Запиши ход мыслей в тегах <thought>...</thought>, затем дай сам ответ.")
    if getattr(config, "NO_SCRIPTS_MODE", False):
        system_prompt += ("\n\nРежим без скриптов включен: не опирайся на "
                          "шаблоны/уроки, думай самостоятельно.")
    messages = [{"role": "system", "content": system_prompt}] + history_messages + [
        {"role": "user", "content": text}]
    steps = max(1, max_steps or config.AGENT_MAX_STEPS)
    if getattr(config, "THINKING_MODE", False):
        steps = max(steps, min(config.AGENT_MAX_STEPS + 4, 12))
    if local_mode:
        steps = min(steps, 5)
    last_result = ""
    who = "none"
    tried_full_tools = False
    seen_calls: set[str] = set()

    log.info("Инструменты (%d/%d): %s", len(tools), len(universe),
             ", ".join(tool["function"]["name"] for tool in tools))

    forced = None
    try:
        for step in range(steps):
            payload, _elapsed, who = _run_chain(messages, use_tools=True, tools=tools,
                                                on_delta=emit_delta, tool_choice=forced)
            forced = None
            if payload.get("type") == "error":
                return finish({"type": "error",
                               "detail": payload.get("detail", "мозг недоступен")}, who)

            raw = payload.get("raw") or {}
            calls = raw.get("tool_calls") or []
            # Мысли ассистента перед вызовом инструментов (актуально при
            # включённом режиме размышления) отдаём наружу для отображения.
            if calls:
                thought_text = (payload.get("reply") or raw.get("content") or "").strip()
                if not thought_text:
                    names = ", ".join(str(call.get("name", "?")) for call in calls)
                    thought_text = f"Думаю: нужно выполнить {names}."
                _thought_callback(thought_text)
            if not calls:
                reply = (payload.get("reply") or raw.get("content") or "").strip()
                # Извлекаем мысли из <thought>...</thought>, чтобы показать их в UI.
                thought_match = re.search(r"<thought>(.*?)</thought>", reply, re.DOTALL | re.IGNORECASE)
                if thought_match:
                    _thought_callback(thought_match.group(1).strip())
                    reply = re.sub(r"<thought>.*?</thought>", "", reply, flags=re.DOTALL | re.IGNORECASE).strip()
                    reply = reply or "Готово."
                # Страховка: модель «поговорила» вместо действия, хотя в фразе
                # явный глагол. Один раз повторяем с полным набором схем.
                if (step == 0 and not tried_full_tools and config.TOOLS_RETRY_FULL
                        and len(tools) < len(universe) and _looks_like_action(text)
                        and not (chunker is not None and chunker.emitted)):
                    tried_full_tools = True
                    log.info("Повтор с полным набором инструментов (tool_choice=required)")
                    tools = list(universe)
                    forced = "required"  # гарантируем вызов, а не «поговорили»
                    continue
                return finish({"type": "chat", "reply": reply}, who)

            messages.append({
                "role": "assistant",
                "content": raw.get("content") or "",
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                        },
                    }
                    for call in calls
                ],
            })

            # Защита от зацикливания: одинаковый вызов дважды не выполняем
            # (в логах был случай: browser_read дважды и see_screen дважды подряд,
            # 24 тыс. токенов на одну команду).
            repeats = 0
            for call in calls:
                name = call.get("name", "")
                args = call.get("args") or {}
                signature = name + "|" + json.dumps(args, ensure_ascii=False, sort_keys=True)
                if signature in seen_calls:
                    repeats += 1
                    log.info("Повторный вызов %s — пропускаю", name)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": ("Этот вызов уже был и не помог. Не повторяй его: "
                                    "смени подход или ответь пользователю коротко."),
                    })
                    continue
                seen_calls.add(signature)
                log.info("Агент: %s %s", name, json.dumps(args, ensure_ascii=False)[:200])
                if on_step is not None:
                    try:
                        on_step(name, args)
                    except Exception:  # noqa: BLE001
                        log.exception("on_step")
                try:
                    result, _ok = executor(name, args)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Инструмент %s: %s", name, exc)
                    result = f"Ошибка инструмента {name}: {exc}"
                last_result = str(result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": _tool_message(name, last_result),
                })

            if repeats and repeats == len(calls) and last_result:
                log.info("Все вызовы повторные — завершаю цикл, чтобы не жечь токены")
                return finish({"type": "chat", "reply": last_result}, who)

        return finish({"type": "chat",
                       "reply": last_result or "Не удалось завершить задачу за отведённое число шагов."},
                      who)
    except Exception as exc:  # noqa: BLE001
        log.exception("Агент упал")
        return finish({"type": "error", "detail": f"{type(exc).__name__}: {exc}"}, who)


def process_command(text: str) -> dict:
    result, elapsed, who = call_with_fallback(text, use_tools=True)
    result.pop("raw", None)
    result["_elapsed_sec"] = round(elapsed, 2)
    result["_via"] = who
    return result


def reset_state() -> None:
    """Сброс карантина/клиентов — для тестов и ручного «полечить сеть»."""
    global _LIVE_IDX
    _blocked_until.clear()
    _clients.clear()
    _LIVE_IDX = None


def warmup() -> dict:
    """Прогреть провайдера заранее: первый запрос не платит за холодный старт.

    Холодный первый ответ у агрегаторов бывает вдвое медленнее (замерено:
    2.6 с против 0.7 с до первого токена). Вызов стоит несколько токенов,
    поэтому делается один раз при старте и в фоне.
    """
    payload, elapsed, who = _run_chain(
        [{"role": "user", "content": "Ответь одним словом: ОК"}], use_tools=False)
    ok = payload.get("type") != "error"
    log.info("Прогрев мозга: %s за %.2f с (%s)", who, elapsed, "ok" if ok else "ошибка")
    return {"ok": ok, "via": who, "elapsed": round(elapsed, 2)}


def probe_chain(prompt: str = "Ответь ровно одним словом: ОК") -> list[dict]:
    """Проверить каждый кандидат цепочки отдельно (диагностика --health).

    Помогает отличить «не тот ключ» (AUTH), «кончились деньги» (RATE_LIMIT/
    BAD_REQUEST) и «нет сети» (NETWORK) — по всей цепочке сразу.
    """
    messages = [{"role": "user", "content": prompt}]
    results: list[dict] = []
    for index, (provider, model, key_var) in enumerate(CHAIN):
        api_key = (os.getenv(key_var) if key_var else "") or ("ollama" if provider == "ollama" else "")
        item = {"index": index, "provider": provider, "model": _model_uri(provider, model)}
        if not api_key:
            results.append({**item, "status": "NO_KEY"})
            continue
        started = time.perf_counter()
        kind, payload = _call_once(provider, model, api_key, messages, use_tools=False)
        results.append({
            **item,
            "url": _BASE_URLS.get(provider, ""),
            "status": kind,
            "elapsed": round(time.perf_counter() - started, 2),
            "detail": None if kind == "OK" else str(payload)[:160],
        })
    return results


def health_check() -> dict:
    result, elapsed, who = call_with_fallback("Ответь ровно одним словом: ОК", use_tools=False)
    ok = result.get("type") != "error"
    hint = None
    if not os.getenv("DEEPSEEK_API_KEY") and not os.getenv("MISTRAL_API_KEY"):
        hint = ("Добавь DEEPSEEK_API_KEY или MISTRAL_API_KEY в .env — "
                "эти API доступны без VPN и не используют ресурсы ПК.")
    report = {
        "ok": ok,
        "via": who,
        "elapsed": round(elapsed, 2),
        "proxy": config.PROXY_URL or "direct",
        "deepseek_url": config.DEEPSEEK_BASE_URL,
        "no_gpu": config.NO_GPU,
        "local_llm": config.LOCAL_LLM_MODEL
        if (config.LOCAL_LLM_ENABLED or config.LOCAL_ONLY) else False,
        "hint": hint,
        "result": result,
    }
    if not ok:
        # Диагностика по всей цепочке: видно, какой ключ/адрес виноват.
        report["candidates"] = probe_chain()
        detail = str(result.get("detail") or "")
        if "AUTH" in detail and "aitunnel" in config.DEEPSEEK_BASE_URL.lower():
            report["hint"] = ("Ключ не принят агрегатором: проверь, что в "
                              "DEEPSEEK_API_KEY целиком новый ключ из панели aitunnel "
                              "(без остатка старого ключа).")
    return report


if __name__ == "__main__":
    print("Python:", sys.executable)
    print("Цепочка кандидатов:")
    for i, (provider, model, key_var) in enumerate(CHAIN):
        has_key = "ключ есть" if os.getenv(key_var) else "КЛЮЧА НЕТ (пропуск)"
        print(f"   [{i}] {provider:<11} {model:<42} {key_var:<20} {has_key}")

    print("\n[1/2] Проверка связи...")
    check = health_check()
    print("   ->", json.dumps(check, ensure_ascii=False))
    if not check["ok"]:
        sys.exit(1)

    print("\n[2/2] Ввод команд ('quit' — выход).")
    while True:
        try:
            line = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in {"quit", "exit"}:
            break
        if line:
            print("   ", json.dumps(process_command(line), ensure_ascii=False))

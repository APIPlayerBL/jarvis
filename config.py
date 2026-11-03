"""Единая конфигурация Jarvis.

Все пути строятся от расположения этого файла, поэтому проект можно переносить
в любую папку. Секреты и параметры, которые могут меняться между машинами,
читаются из .env (см. .env.example).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
LOG_DIR = BASE_DIR / "logs"
MEMORY_DIR = BASE_DIR / "memory"
ASSETS_DIR = BASE_DIR / "assets"
ENV_PATH = BASE_DIR / ".env"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:  # python-dotenv не обязателен для работы
        return
    load_dotenv(ENV_PATH, override=False)


_load_env()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env_str(name, str(default)).replace(",", "."))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = env_str(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "on", "да", "y"}


def env_device(name: str) -> int | str | None:
    value = env_str(name, "")
    if not value:
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def save_env_value(key: str, value: str) -> bool:
    """Аккуратно обновить одну переменную в .env, сохранив остальные строки."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key or ""):
        return False
    if value is None:
        value = ""
    if "\n" in value or "\r" in value:
        return False
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        new_line = f"{key}={value}"
        for index, line in enumerate(lines):
            if pattern.match(line):
                lines[index] = new_line
                break
        else:
            lines.append(new_line)
        ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


# Запрет GPU: Jarvis должен работать на CPU и не отбирать видеопамять у игр.
NO_GPU = env_bool("JARVIS_NO_GPU", True)
if NO_GPU:
    # Эти переменные читают CUDA/HIP-библиотеки при инициализации. Ставим до
    # импорта ctranslate2 / faster-whisper, поэтому config импортируется первым.
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HIP_VISIBLE_DEVICES"] = ""


# --------------------------------------------------------------------------
# аудио / STT
# --------------------------------------------------------------------------
SAMPLE_RATE = env_int("JARVIS_SAMPLE_RATE", 16000)
CHANNELS = 1
BLOCK_SEC = env_float("JARVIS_BLOCK_SEC", 0.1)
SILENCE_RMS = env_float("JARVIS_SILENCE_RMS", 0.0025)
SILENCE_SEC = env_float("JARVIS_SILENCE_SEC", 0.7)
MAX_SECONDS = env_float("JARVIS_MAX_SECONDS", 8.0)
MIN_AUDIO_SEC = env_float("JARVIS_MIN_AUDIO_SEC", 0.25)
PREROLL_SEC = env_float("JARVIS_PREROLL_SEC", 0.3)
NOISE_CALIBRATION_SEC = env_float("JARVIS_CALIBRATION_SEC", 0.6)
MIC_DEVICE = env_device("JARVIS_MIC")
# 1.0 — штатная чувствительность; больше = ловит тихую речь.
MIC_SENSITIVITY = env_float("JARVIS_SENSITIVITY", 1.0)

LANGUAGE = env_str("JARVIS_LANGUAGE", "ru")
USE_VAD = env_bool("JARVIS_USE_VAD", True)
STT_DEVICE = "cpu" if NO_GPU else env_str("JARVIS_STT_DEVICE", "cpu")
STT_COMPUTE = "int8" if NO_GPU else env_str("JARVIS_STT_COMPUTE", "int8")
CPU_THREADS = env_int("JARVIS_CPU_THREADS", max(1, min(2, (os.cpu_count() or 4) // 2)))
WHISPER_SIZES = {
    "game": env_str("JARVIS_WHISPER_GAME", "tiny"),
    "desktop": env_str("JARVIS_WHISPER_DESKTOP", "small"),
}
# Лёгкая модель только для проверки wake word: tiny (~75 МБ) вместо small
# (~500 МБ). Команду распознаёт основная модель, но только когда wake word
# действительно прозвучал. Пустая строка — выключить (использовать основную).
STT_WAKE_SIZE = env_str("JARVIS_STT_WAKE", "tiny")
# Сколько секунд простоя ждать, прежде чем выгрузить тяжёлую модель из RAM.
# 0 — никогда.  Лёгкая wake-модель остаётся, поэтому реакция на wake word
# не страдает, а память занимается только на время диалога.
STT_IDLE_UNLOAD_SEC = env_float("JARVIS_STT_IDLE_UNLOAD", 300.0)
STT_INITIAL_PROMPT = env_str(
    "JARVIS_STT_PROMPT",
    "Джарвис, открой Steam, запусти Dota 2, Discord, браузер, включи музыку, выключи музыку, "
    "следующий трек, увеличь громкость, уменьши громкость, поставь громкость 30, "
    "запусти Telegram, открой Chrome, сверни все окна, заблокируй компьютер, который час, "
    "сделай скриншот, запиши заметку, что на экране.",
)

# --------------------------------------------------------------------------
# wake word
# --------------------------------------------------------------------------
WAKE_WORDS = tuple(
    w.strip()
    for w in env_str(
        "JARVIS_WAKE_WORDS", "джарвис,жарвис,джейвис,jarvis"
    ).split(",")
    if w.strip()
)
WAKE_MATCH_RATIO = env_float("JARVIS_WAKE_RATIO", 0.88)
COOLDOWN_SEC = env_float("JARVIS_COOLDOWN_SEC", 0.25)
WAIT_TIMEOUT_SEC = env_float("JARVIS_WAIT_TIMEOUT", 10.0)
MAX_REASKS = env_int("JARVIS_MAX_REASKS", 2)
SPEAK_SETTLE_SEC = env_float("JARVIS_SPEAK_SETTLE", 0.35)

# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------
TTS_RATE = env_int("JARVIS_TTS_RATE", 180)
TTS_MAX_CHARS = env_int("JARVIS_TTS_MAX_CHARS", 260)
TTS_ENABLED = env_bool("JARVIS_TTS", True)
# auto: Piper (локально) -> edge-tts (облако, кэш) -> SAPI (pyttsx3).
TTS_ENGINE = env_str("JARVIS_TTS_ENGINE", "auto").lower()
EDGE_VOICE = env_str("JARVIS_EDGE_VOICE", "ru-RU-DmitryNeural")
EDGE_TIMEOUT_SEC = env_float("JARVIS_EDGE_TIMEOUT", 8.0)
PIPER_EXE = env_str("JARVIS_PIPER_EXE", "")
PIPER_MODEL = env_str("JARVIS_PIPER_MODEL", "")
TTS_CACHE_DIR = ASSETS_DIR / "tts_cache"
TTS_CACHE_MAX_FILES = env_int("JARVIS_TTS_CACHE_FILES", 300)
# Стриминг LLM -> TTS: озвучиваем по фразам, не дожидаясь конца ответа.
STREAM_TTS = env_bool("JARVIS_STREAM_TTS", True)
STREAM_MIN_CHARS = env_int("JARVIS_STREAM_MIN_CHARS", 24)
STREAM_MAX_CHARS = env_int("JARVIS_STREAM_MAX_CHARS", 140)

# --------------------------------------------------------------------------
# мозг (LLM)
# --------------------------------------------------------------------------
# Порядок: от быстрого к запасным. 404-модели и вечно-429 бесплатные модели
# из цепочки убраны (проверено вживую 29.10.2026).
# Имя модели задаётся отдельно: у агрегаторов (AITUNNEL) свои идентификаторы.
# Основная модель — mimo-v2.6-flash (AITUNNEL): 0.56/56 ₽ за 1M, 9/9 на
# типичных командах, самый быстрый первый токен.
DEEPSEEK_MODEL = env_str("DEEPSEEK_MODEL", "mimo-v2.6-flash")
# Та же провайдерская API, другая модель: если основная недоступна (404/лимит),
# Jarvis продолжит на ней, а не останется без мозга.
DEEPSEEK_FALLBACK_MODEL = env_str("DEEPSEEK_FALLBACK_MODEL", "deepseek-v4.1-flash-1017")
MISTRAL_MODEL = env_str("MISTRAL_MODEL", "mistral-small-latest")
BRAIN_CHAIN = (
    # Доступны из РФ напрямую, без VPN (проверено: 401 вместо 403).
    ("deepseek", DEEPSEEK_MODEL, "DEEPSEEK_API_KEY"),
    *([("deepseek", DEEPSEEK_FALLBACK_MODEL, "DEEPSEEK_API_KEY")]
      if DEEPSEEK_FALLBACK_MODEL else []),
    ("mistral", MISTRAL_MODEL, "MISTRAL_API_KEY"),
    # Резерв, если включён VPN/прокси/релей.
    ("groq", "openai/gpt-oss-120b", "GROQ_API_KEY"),
    ("groq", "openai/gpt-oss-20b", "GROQ_API_KEY"),
    ("openrouter", "nvidia/nemotron-3.5-lightning:free", "OPENROUTER_API_KEY"),
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free", "OPENROUTER_API_KEY"),
)
# Дополнительные параметры тела запроса к основному провайдеру (JSON).
# Для «думающих» моделей у агрегаторов: {"reasoning": {"effort": "none"}}
# отключает рассуждение — ответ и первый звук примерно вдвое быстрее.
# Применяется только к провайдеру deepseek; при ошибке 400 параметр
# автоматически снимается и запрос повторяется.
BRAIN_EXTRA_JSON = env_str("JARVIS_BRAIN_EXTRA", "")

# Цены основного провайдера, ₽ за 1M токенов — только для учёта расхода.
# Значения берутся с сайта провайдера и подставляются под текущую модель:
#   deepseek-v4.1-flash-1017 → 0.6 / 120;  mimo-v2.6-flash → 0.56 / 56;
#   mistral-nemo → 3.8 / 6;  qwen3.7-flash → 1.2 / 26.
PRICE_IN = env_float("JARVIS_PRICE_IN", 0.6)
PRICE_OUT = env_float("JARVIS_PRICE_OUT", 120.0)

BRAIN_TIMEOUT_SEC = env_float("JARVIS_BRAIN_TIMEOUT", 15.0)
BRAIN_MAX_TOKENS = env_int("JARVIS_BRAIN_MAX_TOKENS", 400)
BRAIN_TEMPERATURE = env_float("JARVIS_BRAIN_TEMPERATURE", 0.1)
BRAIN_CHAT_MAX_CHARS = env_int("JARVIS_BRAIN_CHAT_MAX_CHARS", 400)
AGENT_MAX_STEPS = env_int("JARVIS_AGENT_MAX_STEPS", 8)

# Динамический набор инструментов: в запрос уходят только релевантные схемы
# (24 -> 3…8). Меньше токенов и меньше шансов, что модель вызовет «не то».
TOOLS_MAX = env_int("JARVIS_TOOLS_MAX", 8)
TOOLS_RETRY_FULL = env_bool("JARVIS_TOOLS_RETRY", True)
# Болталка без инструментов идёт отдельным потоковым запросом — первый звук
# примерно в 2 раза быстрее. Выключить: JARVIS_CHAT_FASTPATH=0.
CHAT_FASTPATH = env_bool("JARVIS_CHAT_FASTPATH", True)
# Короткий контекст диалога (2–3 реплики) — для уточнений вроде «а на ютубе?».
CONTEXT_TURNS = env_int("JARVIS_CONTEXT_TURNS", 3)
# Защита от prompt injection: внешний текст (страницы, OCR) идёт в LLM только
# как данные в блоке <external_data>.
GUARD_ENABLED = env_bool("JARVIS_GUARD", True)

# --------------------------------------------------------------------------
# сеть: прокси/реле без системного VPN
# --------------------------------------------------------------------------
# Примеры: http://127.0.0.1:2080, socks5://127.0.0.1:1080 (нужен socksio)
PROXY_URL = env_str("JARVIS_PROXY", "")
# Можно указать свой релей (VPS/Cloudflare Worker), который проксирует API.
OPENROUTER_BASE_URL = env_str("JARVIS_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
GROQ_BASE_URL = env_str("JARVIS_GROQ_BASE_URL", "https://api.groq.com/openai/v1")
RELAY_TOKEN = env_str("JARVIS_RELAY_TOKEN", "")
# Адреса DeepSeek/Mistral можно перенаправить на агрегатор с тем же
# OpenAI-протоколом (например, AITUNNEL — доступен из РФ, оплата в рублях):
#   DEEPSEEK_BASE_URL=https://api.aitunnel.ru/v1
DEEPSEEK_BASE_URL = env_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
MISTRAL_BASE_URL = env_str("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")

# Локальная LLM через Ollama — полностью без интернета и без GPU.
LOCAL_LLM_ENABLED = env_bool("JARVIS_LOCAL_LLM", True)
LOCAL_LLM_FIRST = env_bool("JARVIS_LOCAL_LLM_FIRST", False)
LOCAL_ONLY = env_bool("JARVIS_LOCAL_ONLY", False)
LOCAL_LLM_MODEL = env_str("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = env_str("JARVIS_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
# Облачная Ollama (https://ollama.com): бесплатные/быстрые модели для первого приоритета.
OLLAMA_CLOUD_BASE_URL = env_str("JARVIS_OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1")
OLLAMA_CLOUD_API_KEY = env_str("OLLAMA_API_KEY", "")
OLLAMA_NUM_GPU = env_int("JARVIS_OLLAMA_NUM_GPU", 0)
OLLAMA_NUM_CTX = env_int("JARVIS_OLLAMA_NUM_CTX", 16384)
OLLAMA_THREADS = env_int("JARVIS_OLLAMA_THREADS", max(2, (os.cpu_count() or 4) - 2))
OLLAMA_TIMEOUT_SEC = env_float("JARVIS_OLLAMA_TIMEOUT", 180.0)
OLLAMA_KEEP_ALIVE = env_str("JARVIS_OLLAMA_KEEP_ALIVE", "30m")

# YandexGPT (Yandex Foundation Models) — резервный провайдер, доступен из РФ
# без VPN. Нужны YANDEX_API_KEY (Api-Key сервисного аккаунта) и YANDEX_FOLDER_ID.
YANDEX_API_KEY = env_str("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = env_str("YANDEX_FOLDER_ID", "")
YANDEX_BASE_URL = env_str("YANDEX_BASE_URL", "https://llm.api.cloud.yandex.net/v1")
# gpt://<folder>/yandexgpt-lite — дешевле и быстрее; yandexgpt — умнее.
YANDEX_MODEL_KIND = env_str("YANDEX_MODEL", "yandexgpt-lite")

# --------------------------------------------------------------------------
# зрение / браузер / десктоп
# --------------------------------------------------------------------------
BROWSER_PROFILE_DIR = Path(env_str("JARVIS_BROWSER_PROFILE_DIR", str(ASSETS_DIR / "browser_profile"))).expanduser()
BROWSER_HEADLESS = env_bool("JARVIS_BROWSER_HEADLESS", False)
BROWSER_CHANNEL = env_str("JARVIS_BROWSER_CHANNEL", "msedge")
BROWSER_TIMEOUT_SEC = env_float("JARVIS_BROWSER_TIMEOUT", 30.0)
BROWSER_PROXY = env_str("JARVIS_BROWSER_PROXY", "")
# main  — всё в твоём браузере: ссылки открываются вкладкой в уже открытом окне,
#         отдельное окно Playwright не создаётся (по умолчанию);
# managed — прежнее поведение: отдельный Edge с профилем assets/browser_profile.
BROWSER_MODE = env_str("JARVIS_BROWSER_MODE", "main").lower()
# Точка отладки основного браузера (CDP). Если браузер запущен с
# --remote-debugging-port=9222, Jarvis сможет читать страницу и нажимать
# кнопки прямо в твоих вкладках, не открывая своё окно.
BROWSER_CDP = env_str("JARVIS_BROWSER_CDP", "http://127.0.0.1:9222")
# Чем заменить YouTube, если он недоступен в сети (DNS-блокировка):
# yandex | vk | google | none
VIDEO_FALLBACK = env_str("JARVIS_VIDEO_FALLBACK", "yandex").lower()

# OCR по активному окну вместо всего экрана: меньше пикселей (быстрее) и в LLM
# не попадает содержимое остальных окон (приватнее). Результат кэшируется на
# JARVIS_OCR_CACHE_SEC, чтобы повторный вопрос по тому же окну был мгновенным.
OCR_WINDOW_ONLY = env_bool("JARVIS_OCR_WINDOW", True)
OCR_CACHE_SEC = env_float("JARVIS_OCR_CACHE_SEC", 20.0)
OCR_CONTEXT_MAX_CHARS = env_int("JARVIS_OCR_MAX_CHARS", 3000)

# --- музыка ---
# Папка с локальными треками: файл → мгновенное воспроизведение без браузера.
MUSIC_DIR = env_str("JARVIS_MUSIC_DIR", str(Path(os.path.expanduser("~")) / "Music"))
# Если файла нет — открываем поиск в основном браузере:
# yandex | vk (по умолчанию) | zvuk | youtube | auto | none
MUSIC_WEB = env_str("JARVIS_MUSIC_WEB", "vk").lower()
# Страница пользователя в VK, с которой Jarvis ищет и включает аудио.
VK_AUDIOS_URL = env_str("JARVIS_VK_AUDIOS_URL", "https://vk.ru/audios457131425").rstrip("/")
# Авто-воспроизведение в веб-сервисе без видимого окна: Jarvis открывает поиск
# в фоновом окне (за экраном), жмёт «играть» и проверяет звук. Включается
# `python setup_music.py` (там же — один раз войти в VK/Яндекс.Музыку).
MUSIC_AUTOPLAY = env_bool("JARVIS_MUSIC_AUTOPLAY", False)
# Онлайн-превью (iTunes/Deezer, ~30 с): Jarvis скачивает и играет сам, без
# логина и без браузера. Выключается: JARVIS_MUSIC_ONLINE=0.
MUSIC_ONLINE = env_bool("JARVIS_MUSIC_ONLINE", True)
MUSIC_CACHE_DIR = ASSETS_DIR / "music_cache"
MUSIC_CACHE_FILES = env_int("JARVIS_MUSIC_CACHE_FILES", 60)

USER_NAME = env_str("JARVIS_USER_NAME", "")
USER_EMAIL = env_str("JARVIS_USER_EMAIL", "")
DESKTOP_CONFIRM = env_bool("JARVIS_DESKTOP_CONFIRM", True)
CONFIRM_MODE = env_bool("JARVIS_CONFIRM_MODE", True)

# --------------------------------------------------------------------------
# кэш интентов
# --------------------------------------------------------------------------
INTENT_CACHE_TTL_DAYS = env_int("JARVIS_CACHE_TTL_DAYS", 30)

# --------------------------------------------------------------------------
# файлы
# --------------------------------------------------------------------------
# Обучаемые команды пользователя (вкладка «Обучение» в GUI).
LESSONS_PATH = CONFIG_DIR / "lessons.json"
# Заметки, которые ассистент записывает по команде.
NOTES_PATH = MEMORY_DIR / "notes.md"
APPS_PATH = CONFIG_DIR / "apps.json"
STEAM_GAMES_PATH = CONFIG_DIR / "steam_games.json"
GAMES_PATH = CONFIG_DIR / "games.json"
DISCOVERY_INDEX_PATH = MEMORY_DIR / "discovery_index.json"
INTENTS_DB_PATH = MEMORY_DIR / "intents.db"
ACTIONS_LOG_PATH = LOG_DIR / "actions.log"
JARVIS_LOG_PATH = LOG_DIR / "jarvis.log"
DISCOVERY_MAX_AGE_DAYS = env_int("JARVIS_DISCOVERY_MAX_AGE_DAYS", 7)

# --------------------------------------------------------------------------
# поведение
# --------------------------------------------------------------------------
AUTO_CONFIRM = env_bool("JARVIS_AUTO_CONFIRM", False)
DRY_RUN = env_bool("JARVIS_DRY_RUN", False)

# --- «не мешать компьютеру» ---
# Пониженный приоритет процесса: игры и тяжёлые программы получают CPU первыми.
LOW_PRIORITY = env_bool("JARVIS_LOW_PRIORITY", True)
THINKING_MODE = env_bool("JARVIS_THINKING_MODE", False)
NO_SCRIPTS_MODE = env_bool("JARVIS_NO_SCRIPTS_MODE", False)
# EcoQoS (Windows 11): приоритет на энергоэффективные ядра. Может добавить
# задержку на слабых CPU — по умолчанию выключено.
ECO_MODE = env_bool("JARVIS_ECO_MODE", False)
# В игре: меньше анимации в UI и реже обновляем индикатор уровня микрофона.
UI_FPS_BACKGROUND = env_int("JARVIS_UI_FPS_BACKGROUND", 4)
# Мини-режим: маленькое окно поверх остальных (удобно для голосовых команд).
COMPACT_TOPMOST = env_bool("JARVIS_COMPACT_TOPMOST", True)

# --------------------------------------------------------------------------
# звук: одно устройство вывода, безопасный уровень
# --------------------------------------------------------------------------
# Пусто — системное устройство. Иначе индекс или часть имени:
#   JARVIS_AUDIO_OUTPUT=27
#   JARVIS_AUDIO_OUTPUT=Headphones
AUDIO_OUTPUT = env_str("JARVIS_AUDIO_OUTPUT", "")
# Максимальный пик перед выводом (лимитер): больше 1.0 — клиппинг и треск.
AUDIO_MAX_PEAK = env_float("JARVIS_AUDIO_PEAK", 0.9)
# Приглушать музыку во время речи ассистента (0 — не приглушать).
AUDIO_DUCK_GAIN = env_float("JARVIS_AUDIO_DUCK", 0.25)

# Отладочный вывод: не падать на cp1252-консоли и не ломать stdout.
try:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

for _directory in (LOG_DIR, MEMORY_DIR, ASSETS_DIR, TTS_CACHE_DIR, MUSIC_CACHE_DIR):
    try:
        _directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

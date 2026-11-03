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

    except Exception:                                          

        return

    load_dotenv(ENV_PATH, override=False)





_load_env()





                                                                            

         

                                                                            

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





                                                                            

NO_GPU = env_bool("JARVIS_NO_GPU", True)

if NO_GPU:

                                                                            

                                                                                

    os.environ["CUDA_VISIBLE_DEVICES"] = ""

    os.environ["HIP_VISIBLE_DEVICES"] = ""





                                                                            

             

                                                                            

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

                                                                         

                                                                          

                                                                             

STT_WAKE_SIZE = env_str("JARVIS_STT_WAKE", "tiny")

                                                                           

                                                                         

                                                           

STT_IDLE_UNLOAD_SEC = env_float("JARVIS_STT_IDLE_UNLOAD", 300.0)

STT_INITIAL_PROMPT = env_str(

    "JARVIS_STT_PROMPT",

    "Джарвис, открой Steam, запусти Dota 2, Discord, браузер, включи музыку, выключи музыку, "

    "следующий трек, увеличь громкость, уменьши громкость, поставь громкость 30, "

    "запусти Telegram, открой Chrome, сверни все окна, заблокируй компьютер, который час, "

    "сделай скриншот, запиши заметку, что на экране.",

)



                                                                            

           

                                                                            

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



                                                                            

     

                                                                            

TTS_RATE = env_int("JARVIS_TTS_RATE", 180)

TTS_MAX_CHARS = env_int("JARVIS_TTS_MAX_CHARS", 260)

TTS_ENABLED = env_bool("JARVIS_TTS", True)

                                                                     

TTS_ENGINE = env_str("JARVIS_TTS_ENGINE", "auto").lower()

EDGE_VOICE = env_str("JARVIS_EDGE_VOICE", "ru-RU-DmitryNeural")

EDGE_TIMEOUT_SEC = env_float("JARVIS_EDGE_TIMEOUT", 8.0)

PIPER_EXE = env_str("JARVIS_PIPER_EXE", "")

PIPER_MODEL = env_str("JARVIS_PIPER_MODEL", "")

TTS_CACHE_DIR = ASSETS_DIR / "tts_cache"

TTS_CACHE_MAX_FILES = env_int("JARVIS_TTS_CACHE_FILES", 300)

                                                                       

STREAM_TTS = env_bool("JARVIS_STREAM_TTS", True)

STREAM_MIN_CHARS = env_int("JARVIS_STREAM_MIN_CHARS", 24)

STREAM_MAX_CHARS = env_int("JARVIS_STREAM_MAX_CHARS", 140)



                                                                            

            

                                                                            

                                                                           

                                                  

                                                                             

                                                                       

                                                

DEEPSEEK_MODEL = env_str("DEEPSEEK_MODEL", "mimo-v2.6-flash")

                                                                               

                                                    

DEEPSEEK_FALLBACK_MODEL = env_str("DEEPSEEK_FALLBACK_MODEL", "deepseek-v4.1-flash-1017")

MISTRAL_MODEL = env_str("MISTRAL_MODEL", "mistral-small-latest")

BRAIN_CHAIN = (

                                                                   

    ("deepseek", DEEPSEEK_MODEL, "DEEPSEEK_API_KEY"),

    *([("deepseek", DEEPSEEK_FALLBACK_MODEL, "DEEPSEEK_API_KEY")]

      if DEEPSEEK_FALLBACK_MODEL else []),

    ("mistral", MISTRAL_MODEL, "MISTRAL_API_KEY"),

                                            

    ("groq", "openai/gpt-oss-120b", "GROQ_API_KEY"),

    ("groq", "openai/gpt-oss-20b", "GROQ_API_KEY"),

    ("openrouter", "nvidia/nemotron-3.5-lightning:free", "OPENROUTER_API_KEY"),

    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free", "OPENROUTER_API_KEY"),

)

                                                                      

                                                                         

                                                                     

                                                                   

                                               

BRAIN_EXTRA_JSON = env_str("JARVIS_BRAIN_EXTRA", "")



                                                                        

                                                                         

                                                                       

                                                      

PRICE_IN = env_float("JARVIS_PRICE_IN", 0.6)

PRICE_OUT = env_float("JARVIS_PRICE_OUT", 120.0)



BRAIN_TIMEOUT_SEC = env_float("JARVIS_BRAIN_TIMEOUT", 15.0)

BRAIN_MAX_TOKENS = env_int("JARVIS_BRAIN_MAX_TOKENS", 400)

BRAIN_TEMPERATURE = env_float("JARVIS_BRAIN_TEMPERATURE", 0.1)

BRAIN_CHAT_MAX_CHARS = env_int("JARVIS_BRAIN_CHAT_MAX_CHARS", 400)

AGENT_MAX_STEPS = env_int("JARVIS_AGENT_MAX_STEPS", 8)



                                                                           

                                                                          

TOOLS_MAX = env_int("JARVIS_TOOLS_MAX", 8)

TOOLS_RETRY_FULL = env_bool("JARVIS_TOOLS_RETRY", True)

                                                                           

                                                               

CHAT_FASTPATH = env_bool("JARVIS_CHAT_FASTPATH", True)

                                                                              

CONTEXT_TURNS = env_int("JARVIS_CONTEXT_TURNS", 3)

                                                                             

                                     

GUARD_ENABLED = env_bool("JARVIS_GUARD", True)



                                                                            

                                      

                                                                            

                                                                         

PROXY_URL = env_str("JARVIS_PROXY", "")

                                                                           

OPENROUTER_BASE_URL = env_str("JARVIS_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

GROQ_BASE_URL = env_str("JARVIS_GROQ_BASE_URL", "https://api.groq.com/openai/v1")

RELAY_TOKEN = env_str("JARVIS_RELAY_TOKEN", "")

                                                                   

                                                                           

                                                

DEEPSEEK_BASE_URL = env_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

MISTRAL_BASE_URL = env_str("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")



                                                                 

LOCAL_LLM_ENABLED = env_bool("JARVIS_LOCAL_LLM", True)

LOCAL_LLM_FIRST = env_bool("JARVIS_LOCAL_LLM_FIRST", False)

LOCAL_ONLY = env_bool("JARVIS_LOCAL_ONLY", False)

LOCAL_LLM_MODEL = env_str("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")

OLLAMA_BASE_URL = env_str("JARVIS_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")

                                                                                         

OLLAMA_CLOUD_BASE_URL = env_str("JARVIS_OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1")

OLLAMA_CLOUD_API_KEY = env_str("OLLAMA_API_KEY", "")

OLLAMA_NUM_GPU = env_int("JARVIS_OLLAMA_NUM_GPU", 0)

OLLAMA_NUM_CTX = env_int("JARVIS_OLLAMA_NUM_CTX", 16384)

OLLAMA_THREADS = env_int("JARVIS_OLLAMA_THREADS", max(2, (os.cpu_count() or 4) - 2))

OLLAMA_TIMEOUT_SEC = env_float("JARVIS_OLLAMA_TIMEOUT", 180.0)

OLLAMA_KEEP_ALIVE = env_str("JARVIS_OLLAMA_KEEP_ALIVE", "30m")



                                                                            

# без VPN. Нужны YANDEX_API_KEY (Api-Key сервисного аккаунта) и YANDEX_FOLDER_ID.

YANDEX_API_KEY = env_str("YANDEX_API_KEY", "")

YANDEX_FOLDER_ID = env_str("YANDEX_FOLDER_ID", "")

YANDEX_BASE_URL = env_str("YANDEX_BASE_URL", "https://llm.api.cloud.yandex.net/v1")

                                                                       

YANDEX_MODEL_KIND = env_str("YANDEX_MODEL", "yandexgpt-lite")



                                                                            

                            

                                                                            

BROWSER_PROFILE_DIR = Path(env_str("JARVIS_BROWSER_PROFILE_DIR", str(ASSETS_DIR / "browser_profile"))).expanduser()

BROWSER_HEADLESS = env_bool("JARVIS_BROWSER_HEADLESS", False)

BROWSER_CHANNEL = env_str("JARVIS_BROWSER_CHANNEL", "msedge")

BROWSER_TIMEOUT_SEC = env_float("JARVIS_BROWSER_TIMEOUT", 30.0)

BROWSER_PROXY = env_str("JARVIS_BROWSER_PROXY", "")

                                                                                

                                                                

                                                                                

BROWSER_MODE = env_str("JARVIS_BROWSER_MODE", "main").lower()

                                                                

                                                                        

                                                       

BROWSER_CDP = env_str("JARVIS_BROWSER_CDP", "http://127.0.0.1:9222")

                                                                   

                             

VIDEO_FALLBACK = env_str("JARVIS_VIDEO_FALLBACK", "yandex").lower()



                                                                              

                                                                            

                                                                              

OCR_WINDOW_ONLY = env_bool("JARVIS_OCR_WINDOW", True)

OCR_CACHE_SEC = env_float("JARVIS_OCR_CACHE_SEC", 20.0)

OCR_CONTEXT_MAX_CHARS = env_int("JARVIS_OCR_MAX_CHARS", 3000)



                

                                                                             

MUSIC_DIR = env_str("JARVIS_MUSIC_DIR", str(Path(os.path.expanduser("~")) / "Music"))

                                                       

                                                           

MUSIC_WEB = env_str("JARVIS_MUSIC_WEB", "vk").lower()

                                                                     

VK_AUDIOS_URL = env_str("JARVIS_VK_AUDIOS_URL", "https://vk.ru/audios457131425").rstrip("/")

                                                                              

                                                                         

                                                                       

MUSIC_AUTOPLAY = env_bool("JARVIS_MUSIC_AUTOPLAY", False)

                                                                          

                                                            

MUSIC_ONLINE = env_bool("JARVIS_MUSIC_ONLINE", True)

MUSIC_CACHE_DIR = ASSETS_DIR / "music_cache"

MUSIC_CACHE_FILES = env_int("JARVIS_MUSIC_CACHE_FILES", 60)



USER_NAME = env_str("JARVIS_USER_NAME", "")

USER_EMAIL = env_str("JARVIS_USER_EMAIL", "")

DESKTOP_CONFIRM = env_bool("JARVIS_DESKTOP_CONFIRM", True)

CONFIRM_MODE = env_bool("JARVIS_CONFIRM_MODE", True)



                                                                            

              

                                                                            

INTENT_CACHE_TTL_DAYS = env_int("JARVIS_CACHE_TTL_DAYS", 30)



                                                                            

       

                                                                            

                                                            

LESSONS_PATH = CONFIG_DIR / "lessons.json"

                                                   

NOTES_PATH = MEMORY_DIR / "notes.md"

APPS_PATH = CONFIG_DIR / "apps.json"

STEAM_GAMES_PATH = CONFIG_DIR / "steam_games.json"

GAMES_PATH = CONFIG_DIR / "games.json"

DISCOVERY_INDEX_PATH = MEMORY_DIR / "discovery_index.json"

INTENTS_DB_PATH = MEMORY_DIR / "intents.db"

ACTIONS_LOG_PATH = LOG_DIR / "actions.log"

JARVIS_LOG_PATH = LOG_DIR / "jarvis.log"

DISCOVERY_MAX_AGE_DAYS = env_int("JARVIS_DISCOVERY_MAX_AGE_DAYS", 7)



                                                                            

           

                                                                            

AUTO_CONFIRM = env_bool("JARVIS_AUTO_CONFIRM", False)

DRY_RUN = env_bool("JARVIS_DRY_RUN", False)



                                

                                                                               

LOW_PRIORITY = env_bool("JARVIS_LOW_PRIORITY", True)

THINKING_MODE = env_bool("JARVIS_THINKING_MODE", False)

NO_SCRIPTS_MODE = env_bool("JARVIS_NO_SCRIPTS_MODE", False)

                                                                          

                                                  

ECO_MODE = env_bool("JARVIS_ECO_MODE", False)

                                                                           

UI_FPS_BACKGROUND = env_int("JARVIS_UI_FPS_BACKGROUND", 4)

                                                                            

COMPACT_TOPMOST = env_bool("JARVIS_COMPACT_TOPMOST", True)



                                                                            

                                                  

                                                                            

                                                             

                          

                                  

AUDIO_OUTPUT = env_str("JARVIS_AUDIO_OUTPUT", "")

                                                                          

AUDIO_MAX_PEAK = env_float("JARVIS_AUDIO_PEAK", 0.9)

                                                                 

AUDIO_DUCK_GAIN = env_float("JARVIS_AUDIO_DUCK", 0.25)



                                                                   

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


                          
"""Проверка сетевой доступности LLM-провайдеров (без расхода токенов).

GET на публичный каталог моделей показывает, жив ли периметр и не режет ли
провайдер гео. Ключ передаётся только в заголовке (не в URL).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config              
from dotenv import load_dotenv              

load_dotenv(config.ENV_PATH)

CANDIDATES = [
    ("deepseek", "https://api.deepseek.com/v1/models", "DEEPSEEK_API_KEY"),
    ("mistral", "https://api.mistral.ai/v1/models", "MISTRAL_API_KEY"),
    ("groq", "https://api.groq.com/openai/v1/models", "GROQ_API_KEY"),
    ("openrouter", "https://openrouter.ai/api/v1/models", "OPENROUTER_API_KEY"),
]
GEO_MARKERS = ("security policy", "access denied", "region", "country", "not available in",
               "unsupported location", "geo", "forbidden")
AUTH_MARKERS = ("api key", "authentication", "unauthorized", "key not valid", "invalid key",
                "missing api key")


def classify(status: int | None, body: str) -> tuple[str, str]:
    text = (body or "").lower()
    if status == 200:
        return "ЖИВ", "каталог доступен"
    if status in (401, 402, 429):
        return "ПЕРИМЕТР ЖИВ", "нужен ключ/квота/план"
    if status == 403:
        if any(marker in text for marker in GEO_MARKERS):
            return "ГЕО-БЛОК", "без VPN недоступно"
        if any(marker in text for marker in AUTH_MARKERS):
            return "ПЕРИМЕТР ЖИВ", "проблема ключа/аккаунта"
        return "403 НЕЯСНО", (body or "")[:100]
    if status is None:
        return "СЕТЬ НЕДОСТУПНА", "timeout/SSL/reset"
    return f"status={status}", (body or "")[:100]


def probe(name: str, url: str, key_var: str):
    key = os.getenv(key_var) or ""
    headers = {"User-Agent": "jarvis-probe/2.0"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(url, headers=headers, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", "replace")[:300]
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        status = exc.code
    except Exception as exc:                
        body = f"{type(exc).__name__}: {str(exc)[:120]}"
        status = None
    elapsed = time.perf_counter() - started
    return name, status, elapsed, *classify(status, body)


def main() -> int:
    print("Периметр LLM-провайдеров\n")
    print(f"{'PROVIDER':<12} {'STAT':>5} {'TIME':>6} | CLASS | note")
    print("-" * 90)
    alive = 0
    for name, url, key_var in CANDIDATES:
        name, status, elapsed, klass, note = probe(name, url, key_var)
        has_key = "ключ есть" if os.getenv(key_var) else "ключа нет"
        print(f"{name:<12} {str(status):>5} {elapsed:>5.2f}s | {klass:<18} | {note} ({has_key})")
        if klass in {"ЖИВ", "ПЕРИМЕТР ЖИВ"}:
            alive += 1
    print("-" * 90)
    print("Итог:", f"{alive} из {len(CANDIDATES)} доступны" if alive else "нет доступа без VPN")
    return 0 if alive else 1


if __name__ == "__main__":
    raise SystemExit(main())

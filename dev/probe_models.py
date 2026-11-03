                       
"""Автоподбор бесплатных моделей OpenRouter с поддержкой tools.

Диагностика: какие free-модели реально отвечают 200 и умеют function calling.
Ничего не записывает в .env — найденные id добавляются в config.BRAIN_CHAIN
вручную (или через переменные окружения JARVIS_*).
"""
from __future__ import annotations

import argparse
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

BASE = "https://openrouter.ai/api/v1"
API_KEY = os.getenv("OPENROUTER_API_KEY")


def http_json(url: str, payload=None, timeout: int = 20):
    headers = {"Authorization": f"Bearer {API_KEY}", "User-Agent": "jarvis-probe/2.0"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:300]
    except Exception as exc:                
        return None, f"{type(exc).__name__}: {str(exc)[:160]}"


def is_free_text(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    architecture = model.get("architecture") or {}
    outputs = architecture.get("output_modalities") or []
    free = str(pricing.get("prompt")) == "0" and str(pricing.get("completion")) == "0"
    text_out = ("text" in outputs) if outputs else True
    return free and text_out


def supports_tools(model: dict):
    architecture = model.get("architecture") or {}
    params = architecture.get("supported_parameters") or model.get("supported_parameters") or []
    return ("tools" in params) if params else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=12, help="сколько моделей проверить")
    args = parser.parse_args()

    if not API_KEY:
        print("OPENROUTER_API_KEY не найден в .env")
        return 1

    print("Запрашиваю каталог OpenRouter...")
    status, catalog = http_json(f"{BASE}/models")
    if status != 200 or not isinstance(catalog, dict):
        print(f"Каталог недоступен: status={status}, {catalog}")
        return 1

    candidates = []
    for model in catalog.get("data", []):
        if is_free_text(model):
            candidates.append((model.get("id"), supports_tools(model) is True, model.get("name", "")))
    candidates.sort(key=lambda item: (not item[1], item[0] or ""))
    candidates = [item for item in candidates if item[0]][: args.limit]
    print(f"Бесплатных текстовых моделей: проверяю {len(candidates)}\n")

    print(f"{'STATUS':>7} | {'MODEL ID':<48} | note")
    print("-" * 95)
    working = []
    for model_id, tagged_tools, _name in candidates:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "Ответь ровно одним словом: ОК"}],
            "temperature": 0,
            "max_tokens": 5,
        }
        started = time.perf_counter()
        status, body = http_json(f"{BASE}/chat/completions", payload)
        elapsed = time.perf_counter() - started
        if status == 200 and isinstance(body, dict):
            text = (body.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            note = f"OK {text[:12]!r} {elapsed:.2f}s" + (" [tools]" if tagged_tools else "")
            working.append((model_id, tagged_tools, elapsed))
        elif status == 403:
            note = "403 forbidden (гео/аккаунт)"
        elif status == 404:
            note = "404 model not found"
        elif status == 429:
            note = "429 rate limit"
        elif status == 402:
            note = "402 payment/limit"
        else:
            note = f"status={status} {str(body)[:60]}"
        print(f"{str(status):>7} | {model_id:<48} | {note}")

    print("\n" + "=" * 95)
    if working:
        working.sort(key=lambda item: (not item[1], item[2]))
        print("Рабочие кандидаты (с tools — первыми):")
        for model_id, tagged, elapsed in working[:5]:
            print(f"   {model_id:<52} tools={tagged} {elapsed:.2f}s")
        print("\nДобавь лучшую в config.BRAIN_CHAIN как резерв:")
        print(f'   ("openrouter", "{working[0][0]}", "OPENROUTER_API_KEY"),')
    else:
        print("Ни одна модель не ответила 200 — смотри статусы выше.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

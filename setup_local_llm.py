                     
"""Настройка локальной LLM (Ollama) для работы Jarvis без VPN и без GPU.

    python setup_local_llm.py --pull

Скрипт проверит Ollama, при --pull скачает модель и пропишет в .env:
    JARVIS_LOCAL_LLM=1
    JARVIS_LOCAL_LLM_FIRST=1
    JARVIS_LOCAL_LLM_MODEL=qwen2.5:7b
    JARVIS_NO_GPU=1

Модель работает на CPU (num_gpu=0), поэтому не занимает RTX.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config              


def find_ollama() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    candidate = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
    return str(candidate) if candidate.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Локальная LLM для Jarvis (CPU)")
    parser.add_argument("--model", default=config.LOCAL_LLM_MODEL, help="тег модели Ollama")
    parser.add_argument("--pull", action="store_true", help="скачать модель, если её нет")
    parser.add_argument("--allow-gpu", action="store_true", help="разрешить GPU (по умолчанию запрещён)")
    parser.add_argument("--local-only", action="store_true",
                        help="запретить облачные LLM (только локальная модель)")
    args = parser.parse_args()

    ollama = find_ollama()
    if not ollama:
        print("Ollama не найден. Установи: https://ollama.com/download")
        return 1

    print(f"Ollama: {ollama}")
    print(f"Модель: {args.model}  (~4–5 ГБ для 7B, скачивание один раз)")
    installed = ""
    try:
        installed = subprocess.run([ollama, "list"], capture_output=True, text=True,
                                   timeout=30).stdout
    except Exception as exc:                
        print(f"Не удалось получить список моделей: {exc}")
        return 1

    if args.model not in installed:
        if not args.pull:
            print(f"\nМодель не установлена. Запусти:\n  {ollama} pull {args.model}")
            print("или повтори этот скрипт с --pull")
            return 1
        print("\nСкачиваю модель (это может занять несколько минут)…")
        result = subprocess.run([ollama, "pull", args.model])
        if result.returncode != 0:
            print("Не удалось скачать модель.")
            return 1

    values = {
        "JARVIS_LOCAL_LLM": "1",
        "JARVIS_LOCAL_LLM_FIRST": "1",
        "JARVIS_LOCAL_LLM_MODEL": args.model,
        "JARVIS_NO_GPU": "0" if args.allow_gpu else "1",
        "JARVIS_OLLAMA_NUM_GPU": "0" if not args.allow_gpu else "99",
        "JARVIS_OLLAMA_NUM_CTX": "16384",
        "JARVIS_OLLAMA_THREADS": str(max(2, (os.cpu_count() or 4) - 2)),
        "JARVIS_OLLAMA_TIMEOUT": "180",
    }
    if args.local_only:
        values["JARVIS_LOCAL_ONLY"] = "1"
    for key, value in values.items():
        config.save_env_value(key, value)
    print("\nЗаписано в .env:")
    for key, value in values.items():
        print(f"  {key}={value}")
    print("\nПроверка:  python main.py --health")
    print("Если всё хорошо, Jarvis будет думать локально на CPU, без VPN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations



import os

import subprocess

import sys

import tkinter as tk

from pathlib import Path

from tkinter import ttk, messagebox



import config



ROOT = Path(__file__).resolve().parents[1]

SMALL_MODEL = "qwen2.5:1.5b"



_KEYS = (

    ("OPENROUTER_API_KEY", "OpenRouter API key"),

    ("DEEPSEEK_API_KEY", "DeepSeek API key"),

    ("GROQ_API_KEY", "Groq API key"),

    ("OLLAMA_API_KEY", "Ollama API key"),

    ("MISTRAL_API_KEY", "Mistral API key"),

)





def local_enabled() -> bool:

    return os.getenv("JARVIS_LOCAL_LLM", "").strip() == "1"





def has_api_key() -> bool:

    return any((os.getenv(name) or "").strip() for name, _ in _KEYS)





def find_ollama() -> str | None:

    import shutil

    found = shutil.which("ollama")

    if found:

        return found

    candidate = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"

    return str(candidate) if candidate.is_file() else None





def _prompt_api_key() -> bool:

    root = tk.Tk()

    root.title("Джарвис — первый запуск: API-ключ")

    root.resizable(False, False)

    frame = ttk.Frame(root, padding=16)

    frame.grid(row=0, column=0)

    ttk.Label(frame, text="Введите API-ключ провайдера").grid(row=0, column=0, columnspan=2, pady=(0, 8))

    entries = {}

    row = 1

    for name, title in _KEYS:

        ttk.Label(frame, text=title).grid(row=row, column=0, sticky="w", pady=2)

        entry = ttk.Entry(frame, width=48, show="*")

        entry.grid(row=row, column=1, pady=2)

        entries[name] = entry

        row += 1

    saved = {"ok": False}

    def on_save():

        filled = False

        for name, entry in entries.items():

            value = entry.get().strip()

            if value:

                config.save_env_value(name, value)

                os.environ[name] = value

                filled = True

        if not filled:

            messagebox.showwarning("Джарвис", "Вставьте хотя бы один API-ключ.")

            return

        saved["ok"] = True

        root.destroy()

    ttk.Button(frame, text="Сохранить", command=on_save).grid(row=row, column=0, columnspan=2, pady=(10, 0))

    root.protocol("WM_DELETE_WINDOW", lambda: None)

    root.mainloop()

    return saved["ok"] or has_api_key()





def _setup_local_model() -> bool:

    ollama = find_ollama()

    if not ollama:

        try:

            subprocess.run(["winget", "install", "-e", "--id", "Ollama.Ollama", "--silent"], check=True, timeout=300)

        except Exception:

            ollama = find_ollama()

        if not ollama:

            messagebox.showerror(

                "Джарвис",

                "Ollama не найден. Установи его с https://ollama.com/download и повтори выбор.",

            )

            return False

    script = ROOT / "setup_local_llm.py"

    if not script.exists():

        messagebox.showerror("Джарвис", "setup_local_llm.py не найден.")

        return False

    result = subprocess.run(

        [sys.executable, str(script), "--model", SMALL_MODEL, "--pull", "--local-only"],

        cwd=str(ROOT),

    )

    if result.returncode != 0:

        messagebox.showerror("Джарвис", "Не удалось скачать локальную модель.")

        return False

    os.environ["JARVIS_LOCAL_LLM"] = "1"

    os.environ["JARVIS_LOCAL_LLM_FIRST"] = "1"

    os.environ["JARVIS_LOCAL_LLM_MODEL"] = SMALL_MODEL

    os.environ["JARVIS_LOCAL_ONLY"] = "1"

    return True





def _choose_mode() -> str:

    root = tk.Tk()

    root.title("Джарвис — первый запуск")

    root.resizable(False, False)

    choice = {"value": ""}

    frame = ttk.Frame(root, padding=16)

    frame.grid(row=0, column=0)

    ttk.Label(frame, text="Выбери, как запустить Джарвиса").grid(row=0, column=0, pady=(0, 12))

    def choose_api():

        choice["value"] = "api"

        root.destroy()

    def choose_local():

        choice["value"] = "local"

        root.destroy()

    ttk.Button(frame, text="Использовать API-ключ", command=choose_api).grid(row=1, column=0, sticky="ew", pady=4)

    ttk.Button(frame, text=f"Скачать локальную модель ({SMALL_MODEL}, ~1 ГБ)", command=choose_local).grid(row=2, column=0, sticky="ew", pady=4)

    root.protocol("WM_DELETE_WINDOW", lambda: None)

    root.mainloop()

    return choice["value"] or "api"





def ensure_api_keys() -> None:
    if os.getenv("JARVIS_DEMO_MODE", "").strip() == "1":
        return
    if local_enabled() or has_api_key():
        return

    while True:

        mode = _choose_mode()

        if mode == "local":

            if _setup_local_model():

                return

        else:

            if _prompt_api_key():

                return




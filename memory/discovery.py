# memory/discovery.py
"""Индекс ярлыков Windows (меню «Пуск» + рабочий стол).

Оптимизации:
- индекс кэшируется в памяти и перечитывается только при изменении mtime;
- автоматически пересобирается, если старше DISCOVERY_MAX_AGE_DAYS;
- ранжирование вместо «первого совпадения»: точное имя > префикс > подстрока,
  с штрафом за мусорные ярлыки (changer/updater/uninstall/...).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # запуск как `python memory\discovery.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from common.fs import atomic_write_json

INDEX_PATH = Path(config.DISCOVERY_INDEX_PATH)

JUNK_SUBSTRINGS = (
    "uninstall", "updater", "update", "changer", "readme", "website",
    "manual", "repair", "activate", "license", "документац",
)
JUNK_PENALTY = 100

_CACHE: dict = {"mtime": 0.0, "index": {}, "loaded": 0.0}
_MEM_TTL = 300.0  # как часто проверять mtime файла индекса


def norm(name: str) -> str:
    name = Path(name).stem.lower().replace("ё", "е")
    return " ".join(name.split()).strip()


def _locations() -> list[Path]:
    env = os.environ
    home = Path.home()
    locations: list[Path] = []
    for base in (env.get("PROGRAMDATA"), env.get("APPDATA")):
        if base:
            locations.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    locations.append(home / "Desktop")
    locations.append(home / "OneDrive" / "Desktop")
    return locations


def build_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for root in _locations():
        if not root.is_dir():
            continue
        for dirpath, _dirs, filenames in os.walk(root, onerror=lambda _e: None):
            for filename in filenames:
                if not filename.lower().endswith(".lnk"):
                    continue
                lower = filename.lower()
                if any(junk in lower for junk in JUNK_SUBSTRINGS):
                    continue
                key = norm(filename)
                if key and key not in index:
                    index[key] = str(Path(dirpath) / filename)
    return index


def _write_index(index: dict[str, str]) -> None:
    atomic_write_json(INDEX_PATH, index, ensure_ascii=False, indent=1)


def load_or_build(force: bool = False) -> dict[str, str]:
    now = time.time()
    if not force and _CACHE["index"] and now - _CACHE["loaded"] < _MEM_TTL:
        return _CACHE["index"]

    index: dict[str, str] = {}
    try:
        mtime = INDEX_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    stale = bool(mtime) and (now - mtime) > config.DISCOVERY_MAX_AGE_DAYS * 86400

    if not force and mtime and not stale:
        try:
            raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                index = {str(k): str(v) for k, v in raw.items()}
        except (OSError, json.JSONDecodeError):
            index = {}

    if not index:
        index = build_index()
        _write_index(index)

    _CACHE.update({"mtime": mtime, "index": index, "loaded": now})
    return index


def refresh(force: bool = True) -> dict[str, str]:
    return load_or_build(force=force)


def _score(query: str, name: str) -> int | None:
    """Меньше = лучше. None = не совпало."""
    if name == query:
        base = 0
    elif name.startswith(query):
        base = 10 + len(name) - len(query)
    elif query.startswith(name):
        base = 20 + len(query) - len(name)
    elif query in name:
        base = 30 + name.index(query)
    elif name in query:
        base = 40 + len(query) - len(name)
    else:
        return None
    if any(junk in name for junk in JUNK_SUBSTRINGS):
        base += JUNK_PENALTY
    return base


def find(query: str) -> str | None:
    index = load_or_build()
    q = norm(query)
    if not q:
        return None
    if q in index:
        return index[q]

    best: tuple[int, str] | None = None
    for name, path in index.items():
        score = _score(q, name)
        if score is None:
            continue
        candidate = (score, path)
        if best is None or candidate < best:
            best = candidate
    return best[1] if best else None


def search(query: str, limit: int = 10) -> list[tuple[str, str]]:
    index = load_or_build()
    q = norm(query)
    if not q:
        return []
    ranked = []
    for name, path in index.items():
        score = _score(q, name)
        if score is not None:
            ranked.append((score, name, path))
    ranked.sort()
    return [(name, path) for _score_value, name, path in ranked[:limit]]


if __name__ == "__main__":
    started = time.perf_counter()
    idx = load_or_build(force=True)
    print(f"Программ найдено: {len(idx)} за {time.perf_counter() - started:.2f} сек")
    for sample in ("steam", "discord", "dota 2", "chrome", "telegram"):
        print(f"  {sample:12} -> {find(sample)}")

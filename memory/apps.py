                
"""Реестр приложений: канонические имена, русские алиасы, Steam-игры.

Один источник правды для router/hands, чтобы не было расхождений вроде
«закрой доту» -> close_app("доту") -> поиск доту.exe.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import config

_SHELL_BAD = re.compile(r"[\\/|&;<>()`$%\r\n\t]")
_META: dict = {"apps_mtime": None, "steam_mtime": None, "apps": {}, "alias": {}, "steam": {}, "steam_alias": {}}


def norm(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[^\w\s.+-]", " ", value, flags=re.UNICODE)
    return " ".join(value.split()).strip()


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _reload() -> None:
    apps_mtime = _mtime(config.APPS_PATH)
    steam_mtime = _mtime(config.STEAM_GAMES_PATH)
    if apps_mtime == _META["apps_mtime"] and steam_mtime == _META["steam_mtime"]:
        return

    apps: dict[str, dict] = {}
    alias: dict[str, str] = {}
    for key, value in _read_json(config.APPS_PATH).items():
        canonical = norm(key)
        if not canonical:
            continue
        if isinstance(value, str):
            target, names = value, []
        elif isinstance(value, dict):
            target = str(value.get("target") or "").strip()
            names = value.get("aliases") or []
            if not isinstance(names, list):
                names = [names]
        else:
            continue
        apps[canonical] = {"target": target, "aliases": [norm(n) for n in names if str(n).strip()]}
        alias[canonical] = canonical
        for name in apps[canonical]["aliases"]:
            if name:
                alias[name] = canonical

    steam: dict[str, int] = {}
    steam_alias: dict[str, str] = {}
    for key, value in _read_json(config.STEAM_GAMES_PATH).items():
        canonical = norm(key)
        try:
            appid = int(value)
        except (TypeError, ValueError):
            continue
        if not canonical or appid <= 0:
            continue
        steam[canonical] = appid
        steam_alias[canonical] = canonical
        if canonical not in apps:
            apps[canonical] = {"target": "", "aliases": []}

    _META.update(
        {
            "apps_mtime": apps_mtime,
            "steam_mtime": steam_mtime,
            "apps": apps,
            "alias": alias,
            "steam": steam,
            "steam_alias": steam_alias,
        }
    )


def all_apps() -> dict[str, dict]:
    _reload()
    return _META["apps"]


def aliases() -> dict[str, str]:
    _reload()
    return _META["alias"]


def resolve(name: str) -> str | None:
    """Вернуть каноническое имя приложения по любой форме названия."""
    _reload()
    key = norm(name)
    if not key:
        return None
    if key in _META["alias"]:
        return _META["alias"][key]
    if key in _META["steam_alias"]:
        return _META["steam_alias"][key]
                                                   
    words = key.split()
    while words:
        candidate = " ".join(words)
        if candidate in _META["alias"]:
            return _META["alias"][candidate]
        if candidate in _META["steam_alias"]:
            return _META["steam_alias"][candidate]
        words.pop()
    return None


def target(canonical: str) -> str:
    _reload()
    entry = _META["apps"].get(norm(canonical))
    return (entry or {}).get("target", "")


def steam_appid(canonical: str) -> int | None:
    _reload()
    return _META["steam"].get(norm(canonical))


def is_safe_name(name: str) -> bool:
    """Отсечь попытки подсунуть команде шелл-метасимволы через STT/LLM."""
    return bool(name) and len(name) <= 120 and not _SHELL_BAD.search(name)


def exists_on_disk(path: str) -> bool:
    return bool(path) and os.path.isfile(path)

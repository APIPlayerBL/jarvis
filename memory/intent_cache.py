                        
"""Кэш успешных интентов: только ТОЧНОЕ совпадение нормализованной фразы.

Fuzzy-поиск удалён осознанно: «включи музыку» и «выключи музыку» похожи на
0.96, из-за чего кэш выполнял противоположное действие. Точный ключ + TTL
дают безопасный выигрыш по скорости.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import config

DB_PATH = Path(config.INTENTS_DB_PATH)
_SCHEMA_READY = False


def _norm(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    return re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE).strip()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=2.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    global _SCHEMA_READY
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " phrase TEXT PRIMARY KEY,"
            " intent TEXT NOT NULL,"
            " created_at REAL,"
            " uses INTEGER DEFAULT 0)"
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(cache)")}
        if "created_at" not in columns:
            conn.execute("ALTER TABLE cache ADD COLUMN created_at REAL")
        if "uses" not in columns:
            conn.execute("ALTER TABLE cache ADD COLUMN uses INTEGER DEFAULT 0")
    _SCHEMA_READY = True


def _ensure_schema() -> None:
    if not _SCHEMA_READY:
        init_db()


def _ttl_seconds() -> float:
    return max(0, config.INTENT_CACHE_TTL_DAYS) * 86400.0


def get_intent(text: str) -> dict | None:
    """Точное совпадение фразы (без «похожих»)."""
    phrase = _norm(text)
    if not phrase:
        return None
    _ensure_schema()
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT intent, created_at FROM cache WHERE phrase = ?", (phrase,)
            ).fetchone()
            if not row:
                return None
            intent_json, created_at = row
            ttl = _ttl_seconds()
            if ttl and created_at and time.time() - created_at > ttl:
                conn.execute("DELETE FROM cache WHERE phrase = ?", (phrase,))
                return None
            conn.execute("UPDATE cache SET uses = uses + 1 WHERE phrase = ?", (phrase,))
        intent = json.loads(intent_json)
    except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(intent, dict):
        return None
    intent.pop("_elapsed_sec", None)
    intent["_via"] = "cache"
    return intent


def save_intent(text: str, intent: dict) -> None:
    """Сохранять только реально выполненные tool-интенты."""
    if not isinstance(intent, dict) or intent.get("type") not in {"tool", "tools"}:
        return
    if intent.get("name") == "get_datetime":                           
        return
    phrase = _norm(text)
    if not phrase or len(phrase) > 300:
        return
    stored = {k: v for k, v in intent.items() if not k.startswith("_")}
    _ensure_schema()
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO cache (phrase, intent, created_at, uses) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(phrase) DO UPDATE SET intent = excluded.intent,"
                " created_at = excluded.created_at, uses = cache.uses + 1",
                (phrase, json.dumps(stored, ensure_ascii=False), time.time()),
            )
    except (OSError, sqlite3.Error):
        return


def clear_cache() -> None:
    _ensure_schema()
    try:
        with _db() as conn:
            conn.execute("DELETE FROM cache")
    except (OSError, sqlite3.Error):
        pass


def count() -> int:
    _ensure_schema()
    try:
        with _db() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0])
    except (OSError, sqlite3.Error):
        return 0


init_db()


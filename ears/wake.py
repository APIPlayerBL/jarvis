              
"""Распознавание wake word с защитой от ложных срабатываний.

Порог 0.75 от SequenceMatcher ловил «барвис», «арвис», «карвис». Теперь
дополнительно требуется совпадение первой буквы, а для длинных слов —
высокий коэффициент схожести.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

import config

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WAKE = tuple(w.lower().replace("ё", "е") for w in config.WAKE_WORDS)


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split()).strip()


def is_wake_token(token: str) -> bool:
    token = normalize(token)
    if not token:
        return False
    for wake in _WAKE:
        if token == wake:
            return True
        if token.startswith(wake) and len(token) <= len(wake) + 4:
            return True                                   
        if abs(len(token) - len(wake)) <= 2 and token[0] == wake[0]:
            if SequenceMatcher(None, token, wake).ratio() >= config.WAKE_MATCH_RATIO:
                return True
    return False


def _is_wake_pair(first: str, second: str) -> bool:
    joined = first + second
    if joined in _WAKE:
        return True
    return any(
        SequenceMatcher(None, joined, wake).ratio() >= 0.9 and joined[:1] == wake[:1]
        for wake in _WAKE
    )


def split_wake(text: str) -> tuple[bool, str]:
    """Вернуть (найдено ли wake word, остаток фразы после него)."""
    tokens = normalize(text).split()
    for index, token in enumerate(tokens):
        if is_wake_token(token):
            return True, " ".join(tokens[index + 1:]).strip()
        if index + 1 < len(tokens) and _is_wake_pair(token, tokens[index + 1]):
            return True, " ".join(tokens[index + 2:]).strip()
    return False, ""


def contains_wake(text: str) -> bool:
    return split_wake(text)[0]

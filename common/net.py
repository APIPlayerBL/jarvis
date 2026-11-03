# common/net.py
"""HTTP-запросы Jarvis с нормальным SSL.

На этой машине стандартный контекст Python падает с
`CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`
(в системе нет нужных промежуточных сертификатов/стоит перехват), а браузер
и пакеты на certifi работают. Поэтому по умолчанию берём CA из certifi,
затем truststore (хранилище Windows), затем системный контекст.
"""
from __future__ import annotations

import ssl
import urllib.parse
import urllib.request

from common.log import get_logger

log = get_logger("net")

_CONTEXT: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    global _CONTEXT
    if _CONTEXT is not None:
        return _CONTEXT
    for name, factory in (
        ("certifi", lambda: ssl.create_default_context(
            cafile=__import__("certifi").where())),
        ("truststore", lambda: __import__("truststore").SSLContext(ssl.PROTOCOL_TLS_CLIENT)),
        ("system", ssl.create_default_context),
    ):
        try:
            _CONTEXT = factory()
            log.debug("SSL-контекст: %s", name)
            return _CONTEXT
        except Exception as exc:  # noqa: BLE001
            log.debug("SSL-контекст %s недоступен: %s", name, exc)
    _CONTEXT = ssl.create_default_context()
    return _CONTEXT


_DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


_ALLOWED_SCHEMES = {"http", "https"}


def _validated_url(url: str) -> str:
    """Отбрасываем небезопасные/не-HTTP схемы (file:, javascript:, data:, и т.п.)."""
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme and parsed.netloc and parsed.scheme.lower() in _ALLOWED_SCHEMES:
        return url
    # допускаем schemeless -> https-адрес
    if not parsed.scheme and parsed.netloc:
        return "https://" + str(url).lstrip("/")
    if not parsed.scheme and not parsed.netloc and not parsed.path.startswith("/"):
        # «голый» домен
        return "https://" + str(url).strip()
    raise ValueError(f"URL scheme/path not allowed: {url!r}")


def fetch(url: str, *, timeout: float = 12.0, limit: int = 400_000,
          headers: dict | None = None) -> tuple[str | None, str]:
    """GET-запрос: (текст, error). Без исключений наружу."""
    try:
        url = _validated_url(url)
    except ValueError:
        return None, "URL scheme not allowed"
    request = urllib.request.Request(url, headers=headers or _DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(limit).decode(charset, "replace"), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:160]}"


def fetch_bytes(url: str, *, timeout: float = 25.0, limit: int = 8_000_000,
                headers: dict | None = None) -> tuple[bytes | None, str]:
    """GET-запрос бинарных данных (аудио, картинки). Без исключений наружу."""
    try:
        url = _validated_url(url)
    except ValueError:
        return None, "URL scheme not allowed"
    request = urllib.request.Request(url, headers=headers or _DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            return response.read(limit), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:160]}"

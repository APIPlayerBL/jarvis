# hands/browser.py
"""Браузер Jarvis: по умолчанию — твой основной браузер, без отдельных окон.

Режим `main` (по умолчанию):
- ссылки и видео открываются вкладкой в **уже открытом** браузере
  (`webbrowser.open_new_tab`), своего окна Jarvis не создаёт;
- заголовок и текст страницы читаются по HTTP (без браузера), поэтому
  «открой сайт и скажи, что там» работает быстро;
- если браузер запущен с `--remote-debugging-port=9222` (JARVIS_BROWSER_CDP),
  Jarvis подключается к нему по CDP и умеет читать/нажимать/заполнять прямо
  в твоих вкладках — окно всё то же.

Режим `managed` (JARVIS_BROWSER_MODE=managed) — прежний Playwright-профиль в
отдельном окне: нужен только для сложной автоматизации без отладки.

YouTube в РФ часто недоступен на уровне DNS (Edge даёт ERR_NAME_NOT_RESOLVED).
Тогда вместо ошибки открывается поиск видео в доступном сервисе
(JARVIS_VIDEO_FALLBACK=yandex|vk|google).
"""
from __future__ import annotations

import html as html_mod
import json
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import config
from common.log import get_logger

log = get_logger("hands.browser")

_DNS_CACHE: dict[str, tuple[float, bool]] = {}
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_FIELDS_JS = """
() => Array.from(document.querySelectorAll('input,textarea,select')).slice(0, 30).map(el => ({
  tag: el.tagName.toLowerCase(),
  type: el.type || '',
  name: el.name || '',
  id: el.id || '',
  placeholder: el.placeholder || '',
  aria: el.getAttribute('aria-label') || '',
  label: (el.labels && el.labels[0] ? el.labels[0].innerText : '') || ''
}))
"""

_BUTTONS_JS = """
() => Array.from(document.querySelectorAll('button,a,[role="button"],input[type="submit"]'))
  .filter(el => el.offsetParent !== null)
  .map(el => (el.innerText || el.value || '').trim())
  .filter(t => t).slice(0, 25)
"""

_FIND_JS = """
(text) => {
  const needle = text.toLowerCase().trim();
  const nodes = Array.from(document.querySelectorAll(
    'a,button,[role="button"],[role="row"],[role="listitem"],li,div'
  ));
  const visible = nodes.filter(n => (n.offsetParent !== null || n.getClientRects().length > 0));
  const matches = visible.filter(n => ((n.innerText || n.value || '').toLowerCase().includes(needle)));
  if (!matches.length) return null;
  // Выбираем самый компактный элемент с текстом, чтобы не кликнуть внешний контейнер/панель чатов.
  const el = matches.sort((a, b) => ((a.innerText || a.value || '').length - (b.innerText || b.value || '').length))[0];
  if (!el) return null;
  el.scrollIntoView({block: 'center'});
  el.click();
  return (el.innerText || el.value || '').trim().slice(0, 120);
}
"""

_FILL_JS = """
([field, value]) => {
  const needle = field.toLowerCase().trim();
  const nodes = Array.from(document.querySelectorAll('input,textarea,select'));
  const visible = nodes.filter(n => n.offsetParent !== null && !n.disabled && n.type !== 'hidden');
  const labelOf = n => ((n.labels && n.labels[0] ? n.labels[0].innerText : '') || '').toLowerCase();
  const el = visible.find(n =>
    (n.name || '').toLowerCase().includes(needle) ||
    (n.id || '').toLowerCase().includes(needle) ||
    (n.placeholder || '').toLowerCase().includes(needle) ||
    (n.getAttribute('aria-label') || '').toLowerCase().includes(needle) ||
    labelOf(n).includes(needle)
  );
  if (!el) return null;
  el.focus();
  if (el.tagName === 'SELECT') {
    const wanted = value.toLowerCase().trim();
    const option = Array.from(el.options).find(o =>
      (o.text || '').toLowerCase().includes(wanted) || (o.value || '').toLowerCase() === wanted);
    if (!option) return null;
    el.value = option.value;
  } else if (el.type === 'checkbox' || el.type === 'radio') {
    el.checked = ['1', 'true', 'yes', 'on', 'да', 'y'].includes(value.toLowerCase().trim());
  } else {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
  }
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return (el.name || el.id || el.placeholder || 'field').slice(0, 80);
}
"""

_RESULTS_JS = """
() => {
  const collect = selector => Array.from(document.querySelectorAll(selector))
    .filter(a => a.offsetParent !== null)
    .map(a => ({title: (a.innerText || a.getAttribute('title') || '').trim(), href: a.href}))
    .filter(item => item.title && item.href && item.title.length > 3 && !/^\\d+:\\d+$/.test(item.title));
  const primary = collect('a#video-title');
  const list = primary.length ? primary : collect('a[href*="/watch"]');
  const seen = new Set();
  return list.filter(item => {
    if (seen.has(item.href)) return false;
    seen.add(item.href);
    return true;
  }).slice(0, 8);
}
"""


class BrowserError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# сеть без браузера: DNS-проверка, HTTP-чтение страницы
# --------------------------------------------------------------------------
def dns_ok(host: str, ttl: float = 600.0, timeout: float = 1.5) -> bool:
    """Резолвится ли домен (с кэшем): быстрый способ понять, что сеть его блокирует.

    Разрешение идёт в отдельном потоке с жёстким таймаутом: системный резолвер
    в Windows может висеть 10–30 с, если DNS недоступен, и тогда браузерные
    команды полностью блокируют ассистента.
    """
    now = time.monotonic()
    cached = _DNS_CACHE.get(host)
    if cached and now - cached[0] < ttl:
        return cached[1]

    result: dict[str, bool] = {}

    def work() -> None:
        try:
            socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            result["ok"] = True
        except OSError:
            result["ok"] = False

    worker = threading.Thread(target=work, name=f"dns-{host}", daemon=True)
    worker.start()
    worker.join(timeout=timeout)
    if worker.is_alive():   # резолвер завис — считаем домен недоступным (крат cache)
        _DNS_CACHE[host] = (now, False)
        return False
    ok = bool(result.get("ok", False))
    _DNS_CACHE[host] = (now, ok)
    return ok


def _fetch(url: str, timeout: float = 12.0, limit: int = 400_000) -> tuple[str | None, str]:
    from common import net

    return net.fetch(url, timeout=timeout, limit=limit,
                     headers={"User-Agent": _UA,
                              "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"})


def _page_info(url: str, limit: int = 1200) -> tuple[str, str]:
    """Заголовок и текст страницы по HTTP (без браузера)."""
    html, error = _fetch(url)
    if html is None:
        return "", error
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = html_mod.unescape(title_match.group(1)).strip() if title_match else ""
    body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    text = " ".join(html_mod.unescape(body).split())
    return title[:200], text[:limit]


def _open_in_main_browser(url: str) -> bool:
    """Открыть вкладку в основном браузере (то же окно, без нового процесса)."""
    try:
        if webbrowser.open_new_tab(url):
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("webbrowser: %s", exc)
    try:
        import os

        os.startfile(url)  # noqa: S606 - системный обработчик URL
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось открыть браузер: %s", exc)
        return False


def _youtube_first(query: str) -> tuple[str | None, str]:
    """Первый результат поиска YouTube по HTTP. Возвращает (url, заголовок)."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    html, error = _fetch(url)
    if html is None:
        return None, error
    ids = re.findall(r'"videoId":"([\w-]{11})"', html)
    if not ids:
        return None, "нет результатов"
    title = ""
    titles = re.findall(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.){3,160})"', html)
    if titles:
        title = titles[0].encode().decode("unicode_escape", "ignore")
    return f"https://www.youtube.com/watch?v={ids[0]}", title


def fallback_video_url(query: str) -> str:
    """Поиск видео там, где сеть реально работает."""
    quoted = urllib.parse.quote(query)
    mode = config.VIDEO_FALLBACK
    if mode == "vk":
        return f"https://vk.com/video?q={quoted}"
    if mode == "google":
        return f"https://www.google.com/search?q={quoted}+%D0%B2%D0%B8%D0%B4%D0%B5%D0%BE"
    if mode == "none":
        return ""
    return f"https://yandex.ru/video/search?text={quoted}"


# --------------------------------------------------------------------------
# сессия браузера
# --------------------------------------------------------------------------
class BackgroundBrowser:
    """Невидимое окно (за экраном) для звука: играет музыку без вида пользователю.

    Используется только для авто-воспроизведения в музыкальном сервисе, когда
    основной браузер нельзя автоматизировать (Edge 136+ запрещает отладку на
    профиле по умолчанию). Профиль `assets/browser_profile` — один раз в нём
    логинятся в VK/Яндекс.Музыку (setup_music.py), дальше Jarvis жмёт «играть»
    сам, а окно стоит за пределами экрана.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._playwright = None
        self._context = None
        self._page = None

    def page(self):
        with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                return None
            config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                if self._playwright is None:
                    self._playwright = sync_playwright().start()
                args = ["--no-first-run", "--no-default-browser-check",
                        "--window-position=-32000,-32000", "--window-size=900,700",
                        "--disable-features=Translate"]
                if config.NO_GPU:
                    args.append("--disable-gpu")
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(config.BROWSER_PROFILE_DIR),
                    channel=config.BROWSER_CHANNEL or None,
                    headless=False,
                    viewport={"width": 900, "height": 700},
                    args=args,
                )
                pages = self._context.pages
                self._page = pages[0] if pages else self._context.new_page()
                self._page.set_default_timeout(int(config.BROWSER_TIMEOUT_SEC * 1000))
                log.info("Фоновый браузер для музыки запущен (окно за экраном)")
                return self._page
            except Exception as exc:  # noqa: BLE001
                log.warning("Фоновый браузер не запустился: %s", exc)
                self.close()
                return None

    def close(self) -> None:
        with self._lock:
            try:
                if self._context is not None:
                    self._context.close()
            except Exception:  # noqa: BLE001
                pass
            self._context = None
            self._page = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._playwright = None


_background: BackgroundBrowser | None = None


def close_background() -> None:
    """Закрыть фоновый браузер, чтобы освободить CPU/GPU (например, в game)."""
    global _background
    if _background is not None:
        try:
            _background.close()
        except Exception:
            pass
        _background = None

def background_player() -> BackgroundBrowser:
    global _background
    if _background is None:
        _background = BackgroundBrowser()
    return _background


_SHARED: "BrowserSession | None" = None


def shared() -> "BrowserSession":
    """Единая сессия браузера на процесс (чтобы CDP не подключался заново)."""
    global _SHARED
    if _SHARED is None:
        _SHARED = BrowserSession()
    return _SHARED


def open_in_main_browser(url: str) -> bool:
    """Публичная обёртка: вкладка в основном браузере."""
    return _open_in_main_browser(url)


class BrowserSession:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._playwright = None
        self._context = None
        self._page = None
        self._browser = None
        self._last_url = ""
        self._cdp_failed = False

    @property
    def mode(self) -> str:
        return config.BROWSER_MODE

    # ------------------------------------------------------------------
    # Playwright: подключение к основному браузеру (CDP) или своё окно
    # ------------------------------------------------------------------
    def _cdp_page(self):
        """Страница в основном браузере через CDP (None — если недоступно)."""
        if self._cdp_failed or not config.BROWSER_CDP:
            return None
        if self._page is not None and not self._page.is_closed():
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._cdp_failed = True
            return None
        try:
            if self._playwright is None:
                self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(config.BROWSER_CDP)
            contexts = self._browser.contexts
            context = contexts[0] if contexts else self._browser.new_context()
            pages = context.pages
            self._context = context
            self._page = pages[-1] if pages else context.new_page()
            self._page.set_default_timeout(int(config.BROWSER_TIMEOUT_SEC * 1000))
            log.info("Подключился к основному браузеру по CDP: %s", config.BROWSER_CDP)
            return self._page
        except Exception as exc:  # noqa: BLE001
            self._cdp_failed = True
            log.info("CDP недоступен (%s) — работаю через основной браузер", str(exc)[:120])
            self._shutdown_playwright()
            return None

    def _ensure(self):
        """Своё окно Playwright (только для режима managed)."""
        if self._page is not None and not self._page.is_closed():
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError("Playwright не установлен: pip install playwright") from exc

        config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        args = ["--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
        if config.NO_GPU:
            args.append("--disable-gpu")
        launch_options = {
            "user_data_dir": str(config.BROWSER_PROFILE_DIR),
            "channel": config.BROWSER_CHANNEL or None,
            "headless": config.BROWSER_HEADLESS,
            "viewport": {"width": 1366, "height": 900},
            "args": args,
        }
        if config.BROWSER_PROXY:
            launch_options["proxy"] = {"server": config.BROWSER_PROXY}
        try:
            self._context = self._playwright.chromium.launch_persistent_context(**launch_options)
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._page.set_default_timeout(int(config.BROWSER_TIMEOUT_SEC * 1000))
        except Exception as exc:  # noqa: BLE001
            self._shutdown_playwright()
            raise BrowserError(f"Не удалось запустить браузер: {exc}") from exc
        return self._page

    def _shutdown_playwright(self) -> None:
        try:
            if self._browser is not None and self.mode == "main":
                self._browser.close()  # отключаемся от чужого браузера, но не закрываем его
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._context is not None and self.mode != "main":
                self._context.close()
        except Exception:  # noqa: BLE001
            pass
        self._browser = None
        self._context = None
        self._page = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None

    def close(self) -> None:
        with self._lock:
            self._shutdown_playwright()

    def _page_for_automation(self):
        """Страница для чтения/кликов: CDP основного браузера или своё окно."""
        page = self._cdp_page()
        if page is not None:
            return page
        if self.mode == "main":
            return None
        return self._ensure()

    def page_or_none(self):
        """Страница основного браузера (CDP) или None — без запуска своего окна."""
        return self._page_for_automation()

    def remember(self, url: str) -> None:
        """Запомнить адрес как «текущую страницу» для чтения."""
        self._last_url = url or self._last_url

    def _need_automation(self) -> str:
        return ("Для чтения и кликов без отдельного окна запусти браузер с отладкой: "
                "msedge.exe --remote-debugging-port=9222 (или JARVIS_BROWSER_MODE=managed).")

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(url: str) -> str:
        url = (url or "").strip()
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        return url

    def open(self, url: str) -> str:
        url = self._normalize(url)
        if not url:
            return "Не указан адрес."
        with self._lock:
            if self.mode == "main":
                if not _open_in_main_browser(url):
                    return f"Не удалось открыть {url} в браузере."
                self._last_url = url
                title, info = _page_info(url)
                if title:
                    return f"Открыл в основном браузере: {title} — {url}\nTEXT: {info[:700]}"
                hint = f" ({info})" if info and "DNS" not in info else ""
                return f"Открыл в основном браузере: {url}{hint}"
            try:
                page = self._ensure()
                page.goto(url, wait_until="domcontentloaded",
                          timeout=int(config.BROWSER_TIMEOUT_SEC * 1000))
                page.wait_for_timeout(600)
                try:
                    page.bring_to_front()
                except Exception:  # noqa: BLE001
                    pass
                snippet = ""
                try:
                    snippet = " ".join((page.inner_text("body") or "").split())[:700]
                except Exception:  # noqa: BLE001
                    pass
                self._last_url = page.url
                text = f"Открыл: {page.title()} — {page.url}"
                if snippet:
                    text += f"\nTEXT: {snippet}"
                return text
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось открыть {url}: {exc}"

    def read(self, limit: int = 2000) -> str:
        with self._lock:
            page = self._page_for_automation()
            if page is not None:
                try:
                    page.wait_for_timeout(200)
                    text = (page.inner_text("body") or "").strip()
                    fields = page.evaluate(_FIELDS_JS)
                    buttons = page.evaluate(_BUTTONS_JS)
                    parts = [f"URL: {page.url}", f"TITLE: {page.title()}", "TEXT:", text[:limit]]
                    if fields:
                        parts.append("FIELDS:\n" + json.dumps(fields, ensure_ascii=False))
                    if buttons:
                        parts.append("BUTTONS: " + ", ".join(dict.fromkeys(buttons)))
                    self._last_url = page.url
                    return "\n".join(parts)
                except Exception as exc:  # noqa: BLE001
                    return f"Не удалось прочитать страницу: {exc}"
            if not self._last_url:
                return "Нет открытой страницы — сначала открой сайт."
            title, text = _page_info(self._last_url, limit=limit)
            if not text:
                return (f"Страницу {self._last_url} прочитать не удалось. {self._need_automation()}")
            return f"URL: {self._last_url}\nTITLE: {title}\nTEXT:\n{text}"

    def click(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return "Не указано, что нажать."
        with self._lock:
            page = self._page_for_automation()
            if page is None:
                return f"Клики в основном браузере недоступны. {self._need_automation()}"
            try:
                if text.startswith("css="):
                    page.locator(text[4:]).first.click(timeout=5000)
                    page.wait_for_timeout(500)
                    return f"Нажал {text[4:]}."
                try:
                    try:
                        page.get_by_text(text, exact=True).first.click(timeout=4000)
                        clicked = text
                    except Exception:  # noqa: BLE001
                        try:
                            page.get_by_text(text, exact=False).first.click(timeout=4000)
                            clicked = text
                        except Exception:  # noqa: BLE001
                            clicked = None
                except Exception:  # noqa: BLE001
                    clicked = page.evaluate(_FIND_JS, text)
                if not clicked:
                    return f"Не нашёл на странице «{text}»."
                page.wait_for_timeout(1400)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:  # noqa: BLE001
                    pass
                self._last_url = page.url
                return f"Нажал: {clicked}"
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось нажать «{text}»: {exc}"

    def fill(self, field: str, value: str) -> str:
        with self._lock:
            page = self._page_for_automation()
            if page is None:
                return f"Заполнение полей в основном браузере недоступно. {self._need_automation()}"
            try:
                name = page.evaluate(_FILL_JS, [field, value])
                if not name:
                    name = page.evaluate("""(val) => {
                      const n = Array.from(document.querySelectorAll('[contenteditable="true"]')).filter(e => e.getClientRects().length > 0).pop();
                      if (!n) return null;
                      n.focus();
                      document.execCommand('selectAll', false, null);
                      document.execCommand('insertText', false, val || '');
                      return 'message input';
                    }""", value)
                if not name:
                    return f"Не нашёл поле «{field}»."
                return f"Заполнил поле «{name}»."
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось заполнить «{field}»: {exc}"

    def press(self, keys: str) -> str:
        with self._lock:
            page = self._page_for_automation()
            if page is None:
                return f"Клавиши в основном браузере недоступны. {self._need_automation()}"
            try:
                page.keyboard.press(keys)
                page.wait_for_timeout(300)
                return f"Нажал {keys}."
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось нажать {keys}: {exc}"

    def back(self) -> str:
        with self._lock:
            page = self._page_for_automation()
            if page is None:
                return f"История в основном браузере недоступна. {self._need_automation()}"
            try:
                page.go_back()
                page.wait_for_timeout(400)
                self._last_url = page.url
                return f"Назад: {page.title()} — {page.url}"
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось вернуться назад: {exc}"

    # ------------------------------------------------------------------
    def play_youtube(self, query: str) -> str:
        query = (query or "").strip()
        if not query:
            return "Не указано, какое видео искать."
        with self._lock:
            if self.mode == "main":
                if dns_ok("www.youtube.com"):
                    video_url, title = _youtube_first(query)
                    if video_url:
                        if _open_in_main_browser(video_url):
                            self._last_url = video_url
                            return (f"Включил в основном браузере: {title or query} — {video_url}")
                    search = ("https://www.youtube.com/results?search_query="
                              + urllib.parse.quote(query))
                    if _open_in_main_browser(search):
                        self._last_url = search
                        return f"Открыл поиск YouTube в основном браузере: «{query}»."
                    return "Не удалось открыть YouTube в браузере."
                # YouTube не резолвится: сразу открываем доступный поиск видео.
                fallback = fallback_video_url(query)
                if fallback and _open_in_main_browser(fallback):
                    self._last_url = fallback
                    return (f"YouTube недоступен в этой сети (DNS). Открыл другой поиск видео "
                            f"по «{query}» в основном браузере.")
                return "YouTube и запасной поиск недоступны — похоже, нет сети."
            # managed: прежняя автоматизация в своём окне
            try:
                page = self._ensure()
                url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
                page.goto(url, wait_until="domcontentloaded",
                          timeout=int(config.BROWSER_TIMEOUT_SEC * 1000))
                page.wait_for_timeout(2200)
                for label in ("Принять все", "Accept all", "Пропустить", "Reject all"):
                    try:
                        page.get_by_role("button", name=label, exact=False).first.click(timeout=1200)
                        page.wait_for_timeout(600)
                        break
                    except Exception:  # noqa: BLE001
                        continue
                results = page.evaluate(_RESULTS_JS)
                if not results:
                    return "Поиск на YouTube не дал результатов (возможно, мешает окно согласия)."
                first = results[0]
                page.goto(first["href"], wait_until="domcontentloaded",
                          timeout=int(config.BROWSER_TIMEOUT_SEC * 1000))
                page.wait_for_timeout(1200)
                try:
                    page.bring_to_front()
                except Exception:  # noqa: BLE001
                    pass
                self._last_url = page.url
                return f"Включил: {first['title']} — {page.url}"
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось открыть видео: {exc}"

    def screenshot(self, path: Path) -> str:
        with self._lock:
            page = self._page_for_automation()
            if page is None:
                return f"Скриншот страницы недоступен. {self._need_automation()}"
            try:
                page.screenshot(path=str(path), full_page=False)
                return str(path)
            except Exception as exc:  # noqa: BLE001
                return f"Не удалось сделать скриншот страницы: {exc}"

    def status(self) -> str:
        with self._lock:
            page = self._cdp_page()
            if page is not None:
                try:
                    return f"{page.title()} — {page.url}"
                except Exception:  # noqa: BLE001
                    pass
            if self._last_url:
                return f"основной браузер: {self._last_url}"
            return "основной браузер (страниц пока не открывал)"

                
"""Музыка: понимаем, ЧТО включить, и включаем.

Три уровня:
1. **Локальные файлы** (`JARVIS_MUSIC_DIR`, по умолчанию `%USERPROFILE%\\Music`):
   находим трек по исполнителю/названию и играем сами (sounddevice + av):
   мгновенно, без интернета и без браузера. Пауза/продолжить/стоп/следующий работают.
2. **Основной браузер**: если файла нет — открываем точный поиск трека
   (VK / Яндекс.Музыка / Звук) в уже открытом окне. Если браузер запущен с
   `--remote-debugging-port=9222`, Jarvis ещё и нажимает первый результат и
   проверяет, что звук пошёл (CDP).
3. Честный ответ: если ни файла, ни клика нет — говорим, что открыли поиск,
   а не «включил».

Разбор фразы: «включи музыку кишлак», «поставь песню Пыль», «вруби мирный воин»,
«включи что-нибудь из Кишлака» → запрос «кишлак» / «пыль» / «мирный воин».
"""
from __future__ import annotations

import re
import sys
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path

import config
from common.log import get_logger

log = get_logger("music")

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".wma"}
_CHUNK = 4096

                                                                            
                
                                                                            
_VERB = re.compile(
    r"\b(включи|включить|включай|поставь|поставить|вруби|врубить|запусти|запустить|"
    r"сыграй|играй|воспроизведи|воспроизвести|хочу)\b"
)
_NOUN = re.compile(
    r"\b(музык\w*|трек\w*|песн\w*|песенк\w*|композиц\w*|альбом\w*|плейлист\w*|"
    r"исполнител\w*|артист\w*|групп\w*)\b"
)
_STOP = re.compile(
    r"\b(выключи|отключи|пауза|паузу|стоп|останови|замолчи|громче|тише|погромче|"
    r"потише|следующ\w*|предыдущ\w*|назад|дальше|переключи)\b"
)
_NOISE = re.compile(
    r"\b(мне|пожалуйста|плиз|пж|давай|ну|сейчас|какую-нибудь|какую нибудь|"
    r"что-нибудь|что нибудь|чего-нибудь|чего нибудь|из|от|по|на|и|в|the|a)\b"
)
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s-]", re.UNICODE)
_FULL = re.compile(r"\b(полностью|целиком|полный трек|весь трек|весь)\b")


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def parse_query(text: str) -> tuple[str, bool]:
    """Вернуть (что_включить, это_вообще_про_музыку).

    («включи музыку кишлак» → («кишлак», True); «пауза» → («», False)).
    """
    raw = normalize(text)
    if not raw or _STOP.search(raw):
        return "", False
    if not _VERB.search(raw):
        return "", False
    has_noun = bool(_NOUN.search(raw))
    query = _VERB.sub(" ", raw)
    query = _NOUN.sub(" ", query)
    query = _NOISE.sub(" ", query)
    query = _WS.sub(" ", query).strip(" -,")
    if not query and not has_noun:
        return "", False
    return query, True


                                                                            
                      
                                                                            
_library: dict = {"ts": 0.0, "items": []}


def library(reload: bool = False) -> list[dict]:
    """Список локальных треков (кэш 5 минут)."""
    now = time.monotonic()
    if not reload and _library["items"] and now - _library["ts"] < 300:
        return _library["items"]
    items: list[dict] = []
    root = Path(config.MUSIC_DIR) if config.MUSIC_DIR else None
    if root and root.exists():
        try:
            for path in root.rglob("*"):
                if len(items) >= 4000:
                    break
                if not path.is_file() or path.suffix.lower() not in AUDIO_EXT:
                    continue
                items.append(_track_info(path))
        except OSError as exc:
            log.warning("Не удалось прочитать %s: %s", root, exc)
    _library.update(ts=now, items=items)
    if items:
        log.info("Локальная музыка: %d треков в %s", len(items), root)
    return items


def _track_info(path: Path) -> dict:
    stem = path.stem.replace("_", " ").strip()
    artist, title = "", stem
    for separator in (" - ", " — ", " – "):
        if separator in stem:
            artist, title = [part.strip() for part in stem.split(separator, 1)]
            break
    return {
        "path": str(path),
        "name": path.name,
        "artist": artist,
        "title": title,
        "key": normalize(f"{artist} {title}"),
    }


def search(query: str, limit: int = 5) -> list[dict]:
    """Найти треки по «исполнитель название» (нечёткое совпадение)."""
    needle = normalize(query)
    if not needle:
        return []
    tokens = [token for token in needle.split() if len(token) > 1]
    scored: list[tuple[float, dict]] = []
    for item in library():
        haystack = item["key"]
        score = 0.0
        for token in tokens:
            if token in haystack:
                score += 1.0
            elif len(token) > 3:
                score += 0.6 if SequenceMatcher(None, token, haystack).ratio() > 0.6 else 0.0
        score += SequenceMatcher(None, needle, haystack).ratio()
        if score >= max(1.0, len(tokens) * 0.6):
            scored.append((score, item))
    scored.sort(key=lambda pair: -pair[0])
    return [item for _score, item in scored[:limit]]


                                                                            
               
                                                                            
class MusicPlayer:
    """Воспроизведение файла: одно устройство, лимитер, пауза, duck на время речи."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._thread: threading.Thread | None = None
        self.current: dict | None = None
        self.playing = False
        self._pcm = None
        self._rate = 0
        self._channels = 1
        self._pos = 0
        self._ducked = False

                                                                         
    @staticmethod
    def _decode(path: str):
        """Файл -> (float32 [кадры, каналы], частота файла, каналы)."""
        import av
        import numpy as np

        with av.open(path) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise RuntimeError("в файле нет аудиодорожки")
            rate = int(stream.rate or 44100)
            channels = max(1, min(2, int(stream.channels or 2)))
            resampler = av.AudioResampler(
                format="flt", layout="stereo" if channels == 2 else "mono", rate=rate)
            frames = []
            for frame in container.decode(stream):
                frames.extend(resampler.resample(frame))
            frames.extend(resampler.resample(None))
            arrays = [f.to_ndarray().reshape(-1, channels) for f in frames if f is not None]
        if not arrays:
            raise RuntimeError("пустая аудиодорожка")
        return np.concatenate(arrays, axis=0).astype(np.float32), rate, channels

                                                                         
    def play(self, track: dict) -> str:
        import numpy as np

        from common import audio

        path = track.get("path")
        if not path or not Path(path).is_file():
            return f"Файл не найден: {path}"
        with self._lock:
            self.stop(silent=True)
            try:
                raw, rate, channels = self._decode(path)
                info = audio.configure()
                target = int(info["rate"])
                if channels > 1:
                    pcm = np.stack([audio.resample(raw[:, ch], rate, target)
                                    for ch in range(channels)], axis=1)
                else:
                    pcm = audio.resample(raw.reshape(-1), rate, target).reshape(-1, 1)
                pcm = np.ascontiguousarray(audio.limit(pcm))
            except Exception as exc:                
                log.warning("Не удалось декодировать %s: %s", path, exc)
                return f"Не смог прочитать файл: {exc}"
            self._pcm = pcm
            self._rate = target
            self._channels = int(channels)
            self.current = track
            self._pos = 0
            self._stop.clear()
            self._pause.clear()
            self._ducked = False
            self._thread = threading.Thread(target=self._run, name="music", daemon=True)
            self._thread.start()
            self.playing = True
            log.info("Музыка: %s (%d Гц, %d кан., пик %.2f) на [%s] %s", Path(path).name,
                     target, channels, float(np.max(np.abs(pcm))) if pcm.size else 0.0,
                     info["device_index"], info["device_name"])
        title = f"{track.get('artist')} — {track.get('title')}".strip(" —")
        return f"Включил: {title}"

    def _run(self) -> None:
        import numpy as np
        import sounddevice as sd

        from common import audio

        info = audio.configure()
        try:
            kwargs = dict(samplerate=self._rate, channels=self._channels, dtype="float32")
            if info["device_index"] is not None:
                kwargs["device"] = info["device_index"]
            with sd.OutputStream(**kwargs) as stream:
                pcm = self._pcm
                while not self._stop.is_set() and pcm is not None and self._pos < len(pcm):
                                                                               
                                                           
                    if self._pause.is_set() or audio.is_speaking():
                        time.sleep(0.05)
                        continue
                    block = pcm[self._pos:self._pos + _CHUNK]
                    stream.write(np.ascontiguousarray(block))
                    self._pos += len(block)
        except Exception as exc:                
            log.warning("Воспроизведение остановлено: %s", exc)
        finally:
            self.playing = False
            pcm = self._pcm
            if pcm is not None and self._pos >= len(pcm) and self.current:
                log.info("Трек закончился: %s", self.current.get("name"))
                                                                        
                                                   
                self.current = None
                self._pcm = None
                self._pos = 0

    def pause(self) -> str:
        if not self.current:
            return "Сейчас ничего не играет."
        self._pause.set()
        return "Пауза."

    def resume(self) -> str:
        if not self.current:
            return "Сейчас ничего не играет."
        self._ducked = False
        self._pause.clear()
        return "Продолжаю."

    def duck(self, enabled: bool) -> None:
        """Приостановить музыку, пока Jarvis говорит (иначе речь и музыка сливаются)."""
        if not self.current:
            return
        if enabled:
            if not self._pause.is_set():
                self._ducked = True
                self._pause.set()
        elif self._ducked:
            self._ducked = False
            self._pause.clear()

    def toggle(self) -> str:
        if not self.current:
            return "Сейчас ничего не играет."
        return self.resume() if self._pause.is_set() else self.pause()

    def stop(self, silent: bool = False) -> str:
        self._stop.set()
        self._pause.clear()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None
        was = self.current
        self.current = None
        self._pcm = None
        self._pos = 0
        self.playing = False
        self._ducked = False
        if silent:
            return ""
        return f"Остановил: {was.get('name')}" if was else "Сейчас ничего не играет."

    @property
    def active(self) -> bool:
        return self.current is not None

    def status(self) -> str:
        if not self.current:
            return "выключен"
        state = "пауза" if self._pause.is_set() else "играет"
        pcm = self._pcm
        total = int(len(pcm) / max(1, self._rate)) if pcm is not None else 0
        done = int(self._pos / max(1, self._rate))
        return (f"{self.current.get('artist')} — {self.current.get('title')} ({state}, "
                f"{done // 60}:{done % 60:02d} из {total // 60}:{total % 60:02d})")


player = MusicPlayer()


                                                                            
                                    
                                                                            
def web_url(query: str) -> str:
    """Адрес поиска (или главной) музыкального сервиса в основном браузере."""
    import urllib.parse

    query = (query or "").strip()
    quoted = urllib.parse.quote(query)
    engine = config.MUSIC_WEB
    if engine == "auto":
        engine = "youtube" if _youtube_ok() else "yandex"
    if engine == "youtube":
        return ("https://www.youtube.com/results?search_query=" + quoted if query
                else "https://www.youtube.com/")
    if engine == "zvuk":
        return ("https://zvuk.com/search?query=" + quoted if query else "https://zvuk.com/")
    if engine == "vk":
        base = getattr(config, "VK_AUDIOS_URL", "https://vk.ru/audios457131425")
        if not query:
            return base
                                                                         
                                                                         
        return base + ("&" if "?" in base else "?") + "act=search&q=" + quoted
    if engine == "none":
        return ""
    return ("https://music.yandex.ru/search?text=" + quoted if query
            else "https://music.yandex.ru/")


                                                                            
                                                        
                                                                            
_online_cache: dict[str, tuple[float, list[dict]]] = {}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def translit(text: str) -> str:
    """Русское имя латиницей: «миража» → «mirazha» (ищем Mirazh)."""
    return "".join(_TRANSLIT.get(char, char) for char in normalize(text))


def _cover(tokens: list[str], haystack: str) -> float:
    """Какая доля слов запроса есть в строке (с учётом транслитерации)."""
    if not tokens:
        return 0.0
    latin = translit(haystack)
    hits = 0
    for token in tokens:
        if token in haystack or (len(token) > 2 and translit(token) in latin):
            hits += 1
    return hits / len(tokens)


def _artist_similarity(needle: str, artist: str) -> float:
    """Насколько исполнитель похож на запрос («миража» ↔ Mirazh)."""
    artist = normalize(artist)
    if not artist:
        return 0.0
    direct = SequenceMatcher(None, needle, artist).ratio()
    latin = SequenceMatcher(None, translit(needle), translit(artist)).ratio()
    return max(direct, latin)


def _rank(results: list[dict], query: str, tokens: list[str]) -> list[dict]:
    """Сначала определяем исполнителя, потом выбираем трек у него.

    Иначе «кишлак» находил запись «Mountain Kishlak. Coturnix coturnix»
    (пение перепела), а «мираж» — кавер от случайного исполнителя.
    """
    if not results:
        return []
    needle = normalize(query)
    unique: dict[tuple[str, str], dict] = {}
    for item in results:
        item["artist_sim"] = round(_artist_similarity(needle, item["artist"]), 4)
        item["title_cover"] = round(_cover(tokens, normalize(item["title"])), 4)
        unique[(item["artist"], item["title"])] = item
    candidates = list(unique.values())
                                                                            
                                                                        
    if len(tokens) > 1:
        full_title = [item for item in candidates if item["title_cover"] >= 0.999]
        if full_title:
            full_title.sort(key=lambda item: (-item["artist_sim"], -item["title_cover"]))
            for index, item in enumerate(full_title):
                item["score"] = round(3 * item["artist_sim"] + 2 - index * 0.01, 4)
            return full_title
    best_artist = max(item["artist_sim"] for item in candidates)
                                                                 
    keep = [item for item in candidates if item["artist_sim"] >= best_artist - 0.2] or candidates
    keep.sort(key=lambda item: (-item["title_cover"], -item["artist_sim"]))
    for index, item in enumerate(keep):
        item["score"] = round(3 * item["artist_sim"] + 2 * item["title_cover"] - index * 0.01, 4)
    return keep


def online_search(query: str, limit: int = 5) -> list[dict]:
    """Найти трек в открытых каталогах (iTunes, затем Deezer).

    Возвращает [{artist, title, preview, source, ext}] — preview это ссылка на
    30-секундный аудиофрагмент, который можно скачать и проиграть самим.
    """
    query = (query or "").strip()
    if not query:
        return []
    cached = _online_cache.get(query)
    if cached and time.monotonic() - cached[0] < 3600:
        return cached[1]

    import json
    import urllib.parse

    from common import net

    results: list[dict] = []
    tokens = [word for word in normalize(query).split() if len(word) > 1]

    def add(term: str, attribute: str | None = None, take: int = 10) -> None:
        url = (f"https://itunes.apple.com/search?term={urllib.parse.quote(term)}"
               f"&media=music&limit={take}")
        if attribute:
            url += f"&attribute={attribute}"
        body, _error = net.fetch(url, timeout=12, limit=400_000)
        if not body:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return
        for item in data.get("results", []):
            preview = item.get("previewUrl")
            if not preview:
                continue
            results.append({
                "artist": str(item.get("artistName") or ""),
                "title": str(item.get("trackName") or ""),
                "preview": preview,
                "source": "itunes",
                "ext": ".m4a",
            })

                            
    add(query)
                                                             
    if tokens:
        add(tokens[0], "artistTerm", 50)
                                                                                        
    if len(tokens) > 1:
        add(" ".join(tokens[1:]), "songTerm", 50)

    if not results:
        body, _error = net.fetch(
            f"https://api.deezer.com/search?q={urllib.parse.quote(query)}&limit=8",
            timeout=12, limit=300_000)
        if body:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {}
            for item in data.get("data", []):
                preview = item.get("preview")
                if not preview:
                    continue
                results.append({
                    "artist": str((item.get("artist") or {}).get("name") or ""),
                    "title": str(item.get("title") or ""),
                    "preview": preview,
                    "source": "deezer",
                    "ext": ".mp3",
                })

    best = _rank(results, query, tokens)[:limit]
    _online_cache[query] = (time.monotonic(), best)
    if best:
        log.info("Онлайн-поиск «%s»: %d вариантов, лучший — %s — %s", query, len(best),
                 best[0]["artist"], best[0]["title"])
    return best


def _download_preview(track: dict) -> str | None:
    """Скачать превью в кэш (с ограничением по размеру и количеству файлов)."""
    import hashlib

    from common import net

    config.MUSIC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{track['artist']}|{track['title']}".encode("utf-8")).hexdigest()[:16]
    path = config.MUSIC_CACHE_DIR / f"{key}{track.get('ext') or '.m4a'}"
    if path.is_file() and path.stat().st_size > 1000:
        return str(path)
    data, error = net.fetch_bytes(track["preview"], timeout=25, limit=8_000_000)
    if not data:
        log.warning("Превью не скачалось: %s", error)
        return None
    try:
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        _trim_cache()
    except OSError as exc:
        log.warning("Не удалось сохранить превью: %s", exc)
        return None
    return str(path)


def _trim_cache() -> None:
    try:
        files = sorted(config.MUSIC_CACHE_DIR.glob("*.*"), key=lambda p: p.stat().st_mtime)
        for item in files[: max(0, len(files) - config.MUSIC_CACHE_FILES)]:
            item.unlink(missing_ok=True)
    except OSError:
        pass


def play_online(query: str) -> str | None:
    """Найти трек в интернете и включить его (превью ~30 с) нашим плеером."""
    if not config.MUSIC_ONLINE:
        return None
    found = online_search(query)
    if not found:
        return None
    track = found[0]
    path = _download_preview(track)
    if not path:
        return None
    title = f"{track['artist']} — {track['title']}".strip(" —")
    message = player.play({"path": path, "artist": track["artist"],
                           "title": track["title"], "name": Path(path).name})
    if not message.startswith("Включил"):
        return None
    return f"{message} (превью ~30 с)"


def _youtube_ok() -> bool:
    try:
        from hands import browser

        return browser.dns_ok("www.youtube.com")
    except Exception:                
        return False


def _browser_play(query: str) -> str:
    """Включить в браузере: CDP основного → фоновый профиль → вкладка поиска."""
    from hands import browser as browser_mod

    url = web_url(query)
    if not url:
        return ""
    session = browser_mod.shared()
    page = session.page_or_none()                                          
    if page is not None:
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=int(config.BROWSER_TIMEOUT_SEC * 1000))
            page.wait_for_timeout(7000)
            clicked = page.evaluate(_CLICK_JS)
            page.wait_for_timeout(1500)
            session.remember(url)
            if clicked:
                return f"Включил в браузере: {clicked}"
        except Exception as exc:                
            log.info("CDP-воспроизведение не удалось: %s", exc)

                                                                            
                                                                       
                                      
    try:
        from ears import game_mode
        if game_mode.current_mode() == "game":
            from hands import browser as browser_mod
            browser_mod.close_background()
            if browser_mod.open_in_main_browser(url):
                return f"Открыл поиск «{query}» в браузере."
            return ""
    except Exception:
        pass

                                                                 
    result = _autoplay(url, query)
    if result:
        return result

    if browser_mod.open_in_main_browser(url):
        log.info("Музыка: открыл поиск «%s» в основном браузере. Авто-плей доступен через setup_music.py", query)
                                                                                
        return f"Открыл поиск «{query}» в браузере — включится автоматически, если настроен автоплей."
    return ""


def _autoplay(url: str, query: str) -> str | None:
    """Нажать «играть» в фоновом окне за экраном и проверить звук."""
    try:
        from hands import browser as browser_mod

        page = browser_mod.background_player().page()
        if page is None:
            return None
        page.goto(url, wait_until="domcontentloaded",
                  timeout=int(config.BROWSER_TIMEOUT_SEC * 1000))
        page.wait_for_timeout(7000)
        clicked = page.evaluate(_CLICK_JS)
        page.wait_for_timeout(1500)
        if clicked:
            log.info("Музыка: нажал «%s» в фоновом окне.", clicked)
                                                                            
                                                                       
            return f"Включил в фоне: {query} ({str(clicked)[:60]})"
        return None
    except Exception as exc:                
        log.info("Фоновое воспроизведение не удалось: %s", exc)
        return None


_CLICK_JS = r"""
() => {
  const visible = el => el && el.offsetParent !== null;
  // 1) кнопки «играть» в списках (Яндекс.Музыка, VK, Звук)
  const playSelectors = [
    '[data-testid="MusicPlaylistItem_TogglePlaying"]',
    '[data-testid*="TogglePlaying"]',
    '[data-testid*="AudioPlaylistItemPlayButton"]',
    '[data-testid*="audio_play"]',
    '[data-testid="audio_row"] [class*="play"]',
    '.d-track__play', '.d-track__start-button', '.d-track__start',
    '[class*="TrackRow"] [class*="Play"]', '[class*="trackRow"] [class*="play"]',
    '.audio_row__play_btn', '.audio_row__play', '.PlayButton',
    '[data-testid*="play"]', 'button[aria-label*="play" i]',
    'button[aria-label*="воспроизв" i]', 'button[aria-label*="слушать" i]'
  ];
  for (const selector of playSelectors) {
    const el = Array.from(document.querySelectorAll(selector)).find(visible);
    if (el) {
      el.scrollIntoView({block: 'center'});
      el.click();
      return (el.getAttribute('aria-label') || el.className || 'play').slice(0, 80);
    }
  }
  // 2) первая кнопка «Слушать» (VK) — её текст надёжнее, чем классы.
  const listenBtn = Array.from(document.querySelectorAll('button'))
    .find(el => visible(el) && /^\s*слушать\b/i.test(el.innerText || ''));
  if (listenBtn) {
    listenBtn.scrollIntoView({block: 'center'});
    listenBtn.click();
    return (listenBtn.innerText || 'play').trim().slice(0, 80);
  }
  // 3) первая ссылка на трек — страница трека обычно запускает воспроизведение
  const link = Array.from(document.querySelectorAll(
    'a[href*="/track/"], a[href*="/audio"], a[href*="/track?"]'
  )).find(visible);
  if (link) {
    link.scrollIntoView({block: 'center'});
    link.click();
    return (link.textContent || link.href || 'трек').trim().slice(0, 80);
  }
  return null;
}
"""

_AUDIO_JS = """
() => {
  const media = Array.from(document.querySelectorAll('audio,video'));
  if (media.some(el => !el.paused && el.currentTime > 0)) return true;
  return false;
}
"""


def play_query(query: str) -> str:
    """Включить трек по запросу: поиск в основном браузере (VK).

    Принимает и чистый запрос («кишлак пыль»), и целую фразу
    («включи музыку кишлак») — фразу разбираем сами.
    """
    query = (query or "").strip()
    parsed, is_music = parse_query(query)
    if is_music:
        query = parsed
    if not query:
        return browser_open_music()
                                                                          
                                                                            
    full = bool(_FULL.search(query))
    if full:
        query = _FULL.sub(" ", query).strip() or query
    result = _browser_play(query)
    if result:
        return result
    return f"Не нашёл «{query}» в браузере."

def browser_open_music() -> str:
    """Просто открыть музыку (без конкретного трека)."""
    from hands import browser as browser_mod

    url = web_url("")
    if not url:
        return "Не задан музыкальный сервис (JARVIS_MUSIC_WEB)."
    return ("Открыл музыку в браузере." if browser_mod.open_in_main_browser(url)
            else "Не удалось открыть музыкальный сервис.")


def media_action(action: str) -> str:
    """Пауза/продолжить/стоп для нашего проигрывателя."""
    action = (action or "").lower()
    if action == "play":
        return player.resume() if player.active else "Сейчас ничего не играет."
    if action == "pause":
        return player.pause()
    if action in {"stop", "prev"}:
        return player.stop()
    if action == "next":
        return "Плейлиста нет — скажи, какой трек включить."
    return player.status()


def status() -> str:
    return player.status()


if __name__ == "__main__":
    samples = ["включи музыку кишлак", "включи кишлак пыль", "поставь песню мирный воин",
               "включи что-нибудь из Кишлака", "включи музыку", "пауза", "включи ютуб",
               "вруби трек кислород", "выключи музыку"]
    for sample in samples:
        print(f"{sample!r:44} -> {parse_query(sample)}")
    print("библиотека:", len(library()), "треков")
    print("веб:", web_url("кишлак пыль"))
    print("статус:", status())

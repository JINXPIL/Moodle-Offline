#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Moodle Offline — выгрузка учебных материалов из Moodle для оффлайн-доступа.

Этот файл — ядро и консольная версия. Графический интерфейс — gui.py (использует этот модуль).
Сделано под разметку Moodle 3.8 с темой "sevsu" (do.sevsu.ru), поддерживает и Moodle 3.5–4.x.
Все CSS-селекторы собраны в словаре SEL ниже — правьте их там, если вёрстка отличается.

Запуск из консоли:   python moodle_offline.py            (интерактивное меню)
                     python moodle_offline.py --sync     (быстрая синхронизация с сохранёнными настройками)
                     python moodle_offline.py --help     (все параметры)
"""
from __future__ import annotations

import argparse
import copy
import email.utils
import getpass
import hashlib
import html as htmllib
import json
import os
import posixpath
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse

import android    # Android (Termux): открыть страницу, уведомление, «не засыпать»
import datadir    # где лежат данные программы и перенос с прошлой версии
import deadlines  # календарь и дедлайны: сроки из Moodle, повестка, общий .ics
import dl_rules   # правила скачивания (что брать, что пропускать, шаблоны имён)
import news       # что нового: события, лента, уведомления
import search     # поиск по скачанным материалам (офлайн-индекс)
import watch      # таймер проверки «жив ли Moodle» и автообновление по таймеру

# В собранном .exe без консоли stdout/stderr отсутствуют — подменяем, чтобы print() не падал.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    print("Не установлены нужные библиотеки. Выполните:  pip install -r requirements.txt")
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:  # прогресс-бар необязателен
    tqdm = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:  # системное хранилище сертификатов (как у браузера) — помогает с «российскими» сертификатами
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"


# ════════════════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ════════════════════════════════════════════════════════════════════════════
APP_NAME = "Moodle Offline"
VERSION = "1.13.0"
DEFAULT_URL = "https://do.sevsu.ru"

FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
PLAN = datadir.make_plan(APP_DIR, FROZEN)       # у .exe данные лежат в папке пользователя, а не рядом с программой
DATA_DIR = PLAN.data_dir                        # настройки, сохранённый вход, кэш обложек
DEFAULT_OUT = PLAN.default_out                  # куда сохранять материалы, если путь не выбран


def prepare_data_dir(rep=None) -> dict | None:
    """Вызывается при запуске (окно и консоль): если это новая версия .exe, переносит данные прошлой.
    Копирует, ничего не удаляя. → сведения о переносе или None."""
    if DATA_DIR != PLAN.data_dir:           # тесты и свои настройки подменяют папку — не трогаем
        return None
    try:
        info = datadir.migrate_if_needed(PLAN)
    except Exception as e:                  # перенос не должен мешать запуску
        if rep:
            rep.log(f"Не удалось перенести данные прошлой версии: {e}", "warn")
        return None
    if info and rep:
        rep.log(f"Данные прошлой версии перенесены из {info['from']} (файлов: {info['files']}). Старая папка осталась как была.")
    return info

STATE_FILE = ".moodle_state.json"   # что уже скачано (для докачки и синхронизации)
LOG_FILE = "_журнал.log"
FAILED_FILE = "_не_скачано.txt"
NEW_FILE = "_новое.txt"
RULES_FILE = "_пропущено_правилами.txt"
NEWS_FILE = news.NEWS_FILE           # лента «Что нового» (в папке с материалами)
ANNOUNCE_DIR = "Объявления"          # сюда сохраняются тексты объявлений преподавателей
BACKUP_DIR = "_бэкапы"

# Паузы «как у человека» (секунды, случайно в диапазоне): страницы и файлы отдельно.
PAGE_DELAY = (2.0, 5.0)
FILE_DELAY = (0.5, 1.5)
RETRIES = 6
TIMEOUT = 60
WAIT_SERVER_MINUTES = 240       # сколько ждать «оживания» сервера/сети (0 — без ограничения)
SERVER_PING_EVERY = (30, 60)    # в режиме ожидания пингуем сайт раз в 30–60 с
BACKOFF_BASE = 5                # обрывы: 5, 10, 20, 40… с
SLEEP_BASE = 60                 # 429/503: 1, 2, 4, 8, 10 мин
CHUNK = 256 * 1024
RESUME_BACKOFF = 256 * 1024     # при докачке перекачиваем последние 256 КБ (защита от «хвоста» после отключения света)
PACKAGE_FILE_LIMIT = 3000       # максимум файлов из одного пакета IMS/SCORM
LABEL_MIN_TEXT = 300            # текстовые блоки длиннее этого сохраняются отдельной HTML-страницей

# Заголовки как у обычного Chrome под Windows (версию можно сменить в .env: MOODLE_CHROME_VERSION).
CHROME_VERSION = os.environ.get("MOODLE_CHROME_VERSION", "142")
USER_AGENT = os.environ.get(
    "MOODLE_USER_AGENT",
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36",
)

# ── Типы занятий ────────────────────────────────────────────────────────────
KINDS = ("Лекции", "ПЗ", "ЛЗ", "Прочее")
KIND_CODE = {"Лекции": "ЛК", "ПЗ": "ПЗ", "ЛЗ": "ЛЗ", "Прочее": "ДР"}
_L, _R = r"(?<![а-яёa-z])", r"(?![а-яёa-z])"   # «границы слова» только по буквам: ЛЗ1, ПР-2 тоже ловятся
KIND_RULES = [  # побеждает совпадение, стоящее раньше в названии
    ("ЛЗ", re.compile(rf"{_L}(?:л[зр]|лб){_R}|{_L}лаб|{_L}lab(?:s|oratory)?{_R}", re.I)),
    ("ПЗ", re.compile(rf"{_L}(?:п[зр]|сз){_R}|{_L}практи|{_L}семинар|{_L}practic|{_L}seminar", re.I)),
    ("Лекции", re.compile(rf"{_L}(?:лк|лек){_R}|{_L}лекци|{_L}конспект|{_L}lectur", re.I)),
]

# ── Типы элементов Moodle ───────────────────────────────────────────────────
HANDLERS = {
    "resource": "resource",  # Файл
    "folder": "folder",      # Папка
    "url": "url",            # Гиперссылка
    "page": "html",          # Страница
    "assign": "html",        # Задание (описание + прикреплённые файлы)
    "book": "book",          # Книга
    "quiz": "quiz",          # Тест (описание + просмотр завершённых попыток)
    "lesson": "lesson",      # Лекция (интерактивная)
    "imscp": "package",      # Пакет IMS-содержимого
    "scorm": "package",      # Пакет SCORM
    "file": "file",          # файл по прямой ссылке из текста раздела/блока
    "text": "text",          # длинный текстовый блок (label)
}
IGNORED_MODS = {"subsection", "qbank"}
RECHECK_IN_SYNC = {"folder"}  # в быстрой синхронизации папки перепроверяем всегда (в них часто докладывают файлы)

MOD_TITLES = {
    "resource": "файл", "folder": "папка", "url": "ссылка", "page": "страница", "book": "книга",
    "assign": "задание", "quiz": "тест", "forum": "форум", "attendance": "посещаемость",
    "imscp": "пакет IMS", "scorm": "пакет SCORM", "lesson": "лекция", "label": "пояснение",
    "glossary": "глоссарий", "lti": "внешний инструмент", "bigbluebuttonbn": "вебинар",
    "chat": "чат", "choice": "опрос", "feedback": "анкета", "workshop": "семинар",
    "wiki": "вики", "data": "база данных", "h5pactivity": "H5P", "hvp": "H5P",
    "file": "файл", "text": "текст", "zoom": "Zoom", "questionnaire": "анкета", "survey": "обзор",
}

# ── CSS-селекторы (Moodle 3.8 Boost + запасные варианты для 4.x) ─────────────
SEL = {
    # Вход
    "login_form": "form#login, form.login-form, form[action*='login/index.php']",
    "login_error": ".loginerrors .alert, #loginerrormessage, .loginerrors .error, .loginerrors, .alert-danger",
    "error_box": ".errorbox",
    # Страница курса /course/view.php?id=N
    "course_title": ".page-header-headings h1, #page-header h1, header h1, h1",
    "section": "li.section, li.course-section",
    "section_name": ".sectionname, h3.section-title, .course-section-header h3",
    "activity": "li.activity",
    "activity_link": "a.aalink, .activityinstance a[href], .activityname a[href]",
    "activity_name": ".instancename, .activityname",
    "activity_desc": ".contentafterlink, .activity-description",
    "hidden_text": ".accesshide, .sr-only, .visually-hidden",
    "label_content": ".contentwithoutlink, .activity-altcontent, .no-overflow",
    # Страницы элементов
    "main_region": "#region-main [role='main'], [role='main'], #region-main, #page-content",
    "folder_files": ".foldertree a[href*='pluginfile.php'], .fp-filename-icon a[href*='pluginfile.php']",
    "resource_file": (".resourceworkaround a[href], .resourcecontent [src], .resourcecontent [data], "
                      "object[data], iframe[src], embed[src], a[href*='pluginfile.php']"),
    "url_link": ".urlworkaround a[href], .urlembed a[href]",
    "book_toc": ".book_toc a[href*='chapterid']",
    "quiz_review": "a[href*='/mod/quiz/review.php']",
    "lesson_pages": "a[href*='/mod/lesson/view.php'][href*='pageid=']",
    "imscp_toc": "#imscp_toc a[href], .imscp_toc a[href], a[href*='mod_imscp/content']",
    # Что вырезать из сохраняемых страниц (кнопки, навигация, служебное)
    "strip_from_pages": ("script, noscript, nav, button, input[type=hidden], input[type=submit], "
                         ".activity-navigation, .activity-information, [data-region='activity-information'], "
                         ".completion-info, .automatic-completion-conditions, .modified, .skip-block, "
                         ".accesshide, .sr-only, #page-footer, .footer"),
}


# ════════════════════════════════════════════════════════════════════════════
#  ИСКЛЮЧЕНИЯ
# ════════════════════════════════════════════════════════════════════════════
class MoodleError(Exception):
    pass


class LoginError(MoodleError):
    pass


class SessionExpired(MoodleError):
    pass


class ExternalLink(MoodleError):
    """Адрес внутри Moodle перенаправил на другой сайт (так делает «Гиперссылка»): туда программа сама не ходит."""

    def __init__(self, url):
        super().__init__(f"переход на другой сайт: {url}")
        self.url = url


class NetworkDown(MoodleError):
    pass


class RetriesExhausted(MoodleError):
    pass


class Stopped(Exception):
    pass


class SkipFile(Exception):
    pass


class RuleSkip(Exception):
    """Файл пропущен по правилам скачивания (не ошибка)."""


class IncompleteRead(Exception):
    pass


GEO_SERVICES = [  # проверка страны внешнего IP (первый ответивший)
    ("https://ipinfo.io/json", lambda j: (j.get("ip"), j.get("country"))),
    ("https://api.country.is/", lambda j: (j.get("ip"), j.get("country"))),
    ("https://ipapi.co/json/", lambda j: (j.get("ip"), j.get("country_code") or j.get("country"))),
    ("http://ip-api.com/json/?fields=status,countryCode,query", lambda j: (j.get("query"), j.get("countryCode"))),
]

NET_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)
SERVER_DOWN_STATUS = {502, 504, 520, 521, 522, 523, 524}   # сервер «лежит» → режим ожидания с пингом
SLEEP_STATUS = {429, 503}                                  # перегрузка → «спящий режим» на минуты
RETRY_STATUS = {408, 425, 500}                             # разовый сбой → пара коротких повторов


# ════════════════════════════════════════════════════════════════════════════
#  УТИЛИТЫ
# ════════════════════════════════════════════════════════════════════════════
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"}
STORED_EXT = {".zip", ".rar", ".7z", ".gz", ".pdf", ".docx", ".xlsx", ".pptx", ".jpg", ".jpeg", ".png",
              ".gif", ".webp", ".mp4", ".mp3", ".avi", ".mkv", ".mov", ".odt", ".ods", ".odp", ".djvu"}


def clean_text(s) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", htmllib.unescape(str(s))).strip()


def strip_tags(s) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", s or ""))


_REPLACE = str.maketrans({'"': "'", ":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", "<": "(", ">": ")"})


def safe_name(s: str, maxlen: int = 80) -> str:
    """Имя папки/файла, допустимое в Windows и Linux (кавычки → ', двоеточие → « -», и т.д.)."""
    s = re.sub(r"\s+", " ", _ILLEGAL.sub("_", clean_text(s).translate(_REPLACE))).strip(" .")
    if not s:
        s = "_"
    if s.split(".")[0].upper() in _RESERVED:
        s = "_" + s
    if len(s) > maxlen:
        s = s[:maxlen].rstrip(" .") or "_"
    return s


def split_ext(name: str) -> tuple[str, str]:
    stem, ext = os.path.splitext(name)
    if not ext or len(ext) > 10 or not re.fullmatch(r"\.[0-9A-Za-z_-]+", ext):
        return name, ""
    return stem, ext


def safe_filename(name: str, maxlen: int = 110) -> str:
    stem, ext = split_ext(clean_text(name))
    return safe_name(stem, max(10, maxlen - len(ext))) + ext


def fsp(p) -> str:
    """Путь для файловых операций; в Windows включает поддержку длинных путей (>260 символов)."""
    s = str(p)
    if os.name == "nt":
        s = os.path.abspath(s)
        if len(s) >= 240 and not s.startswith("\\\\?\\"):
            s = "\\\\?\\" + s
    return s


def fexists(p) -> bool:
    return os.path.exists(fsp(p))


def fsize(p) -> int:
    return os.path.getsize(fsp(p))


def fmkdir(p) -> None:
    os.makedirs(fsp(p), exist_ok=True)


def fopen(p, mode="rb", **kw):
    return open(fsp(p), mode, **kw)


def freplace(a, b) -> None:
    """Переименование с повторами: в Windows файл может на секунды держать OneDrive, антивирус или Word."""
    for attempt in range(10):
        try:
            os.replace(fsp(a), fsp(b))
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(min(3.0, 0.3 * (attempt + 1)))


def funlink(p) -> None:
    try:
        os.remove(fsp(p))
    except FileNotFoundError:
        pass


def write_text_atomic(path: Path, text: str) -> bool:
    """Записывает текст через временный файл. Возвращает True, если содержимое изменилось."""
    data = text.encode("utf-8")
    if fexists(path):
        try:
            with fopen(path, "rb") as f:
                if f.read() == data:
                    return False
        except OSError:
            pass
    fmkdir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    with fopen(tmp, "wb") as f:
        f.write(data)
    freplace(tmp, path)
    return True


def sha1_file(path) -> str:
    h = hashlib.sha1()
    with fopen(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hsize(n) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ГБ"


def fmt_dur(sec: float) -> str:
    sec = int(round(sec))
    if sec < 60:
        return f"{sec} с"
    m, s = divmod(sec, 60)
    return f"{m} мин {s} с" if s else f"{m} мин"


def short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def describe_error(e: BaseException) -> str:
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    if isinstance(e, requests.Timeout):
        return "сервер не ответил вовремя (таймаут)"
    if isinstance(e, requests.exceptions.ChunkedEncodingError):
        return "обрыв передачи данных"
    if isinstance(e, requests.ConnectionError):
        return "нет соединения с сервером"
    return str(e) or e.__class__.__name__


def add_query(url: str, **params) -> str:
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    for k, v in params.items():
        q[k] = [str(v)]
    return urlunparse(p._replace(query=urlencode(q, doseq=True)))


def strip_sesskey(url: str) -> str:
    p = urlparse(url)
    if "sesskey" not in p.query:
        return url
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != "sesskey"]
    return urlunparse(p._replace(query=urlencode(q)))


def query_param(url: str, name: str):
    v = parse_qs(urlparse(url).query).get(name)
    return v[0] if v else None


def file_key(url: str) -> str:
    """Ключ файла в состоянии: адрес без #фрагмента и служебных параметров."""
    p = urlparse(url.split("#", 1)[0])
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in ("forcedownload", "time", "redirect")]
    return urlunparse(p._replace(netloc=p.netloc.lower(), query=urlencode(q)))


def pluginfile_parts(url: str):
    """Разбирает .../pluginfile.php/CTX/COMPONENT/AREA/ITEMID/путь/имя → словарь или None."""
    p = urlparse(url)
    path = unquote(p.path)
    if "pluginfile.php" not in path:
        return None
    after = path.split("pluginfile.php", 1)[1]
    if not after.strip("/"):  # slasharguments выключены: ?file=/...
        after = unquote(parse_qs(p.query).get("file", [""])[0])
    segs = [s for s in after.split("/") if s]
    if len(segs) < 4:
        return None
    ctx, component, area = segs[0], segs[1], segs[2]
    rest = segs[3:]
    itemid = None
    if len(rest) > 1 and rest[0].isdigit():
        itemid, rest = rest[0], rest[1:]
    return {"ctx": ctx, "component": component, "area": area, "itemid": itemid,
            "subdirs": rest[:-1], "filename": rest[-1]}


def name_from_url(url: str) -> str:
    parts = pluginfile_parts(url)
    if parts:
        return parts["filename"]
    last = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    return last or "file"


def filename_from_cd(cd: str | None):
    if not cd:
        return None
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, re.I)
    if m:
        try:
            return unquote(m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8")
        except LookupError:
            return unquote(m.group(2).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]*)"', cd, re.I) or re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        name = m.group(1).strip()
        try:
            name = name.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return unquote(name)
    return None


def classify(text: str):
    if not text:
        return None
    best = None
    for kind, rx in KIND_RULES:
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), kind)
    return best[1] if best else None


def parse_delay(s, default):
    if s is None or s == "":
        return default
    if isinstance(s, (tuple, list)):
        return float(s[0]), float(s[1])
    s = str(s).replace(",", ".").strip()
    if "-" in s:
        a, b = s.split("-", 1)
        return float(a), float(b)
    return float(s), float(s)


def parse_hours(s):
    """'01:00-07:00' или '1-7' → (минуты_начала, минуты_конца) или None."""
    if not s:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*", str(s))
    if not m:
        raise ValueError("Формат тихих часов: 01:00-07:00")
    a = int(m.group(1)) * 60 + int(m.group(2) or 0)
    b = int(m.group(3)) * 60 + int(m.group(4) or 0)
    return a % 1440, b % 1440


def in_window(window, now: datetime | None = None) -> bool:
    if not window:
        return True
    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    a, b = window
    return a <= cur < b if a < b else (cur >= a or cur < b)


def fmt_window(window) -> str:
    a, b = window
    return f"{a // 60:02d}:{a % 60:02d}–{b // 60:02d}:{b % 60:02d}"


def ensure_console_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
#  МОДЕЛЬ ДАННЫХ
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class Item:
    key: str
    modtype: str
    name: str
    url: str | None = None
    cmid: str | None = None
    context: str = ""          # заголовок блока-пояснения, из которого взята ссылка
    description: str = ""      # описание под элементом на странице курса
    hint: str | None = None    # тип занятия из ближайшего заголовка-пояснения
    kind: str = "Прочее"
    seq: int = 0
    inline_links: list = field(default_factory=list)
    label_html: str | None = None
    files: list = field(default_factory=list)
    external: str | None = None
    online_only: bool = False
    error: str | None = None
    cached: bool = False
    rule_skip: str | None = None   # причина, если элемент пропущен по правилам
    rule_notes: list = field(default_factory=list)   # какие файлы элемента пропущены по правилам


@dataclass
class Section:
    number: int
    name: str
    items: list = field(default_factory=list)
    page_url: str | None = None


@dataclass
class Course:
    id: int
    fullname: str = ""
    shortname: str = ""
    url: str = ""
    category: str = ""
    image: str = ""
    progress: float | None = None
    hidden: bool = False
    past: bool = False
    future: bool = False
    sections: list = field(default_factory=list)

    def to_dict(self):
        return {k: getattr(self, k) for k in ("id", "fullname", "shortname", "url", "category", "image",
                                               "progress", "hidden", "past", "future")}


@dataclass
class JobOptions:
    out_dir: Path = DEFAULT_OUT
    mode: int = 1                  # 1 — каталог с оглавлением, 2 — папки Курс/Раздел/Тип, 3 — свой шаблон
    sync: bool = False             # быстрая синхронизация
    dry_run: bool = False
    pdf: bool = False
    zip: bool = False
    original_names: bool = False
    index_in_mode2: bool = False
    rules: dict | None = None      # правила скачивания (см. dl_rules)
    dir_template: str = ""         # режим 3: шаблон папок, например {section_no}. {section}/{kind}
    file_template: str = ""        # режим 3: шаблон имени файла, например {section_no}_{item}
    news: dict | None = None       # настройки «Что нового» (см. news.DEFAULTS)
    calendar: dict | None = None   # настройки календаря (см. deadlines.DEFAULTS)
    calendar_on: bool = False      # после курсов обновить календарь сроков (вызывающий включает сам)


@dataclass
class JobResult:
    aborted: str | None = None     # None | "network" | "stopped" | "session"
    courses_done: int = 0
    failures: list = field(default_factory=list)
    new_files: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    events: list = field(default_factory=list)    # события «Что нового», найденные за запуск
    message: str = ""


# ════════════════════════════════════════════════════════════════════════════
#  УПРАВЛЕНИЕ (пауза / стоп / пропуск / тихие часы) И ОТЧЁТЫ О ХОДЕ РАБОТЫ
# ════════════════════════════════════════════════════════════════════════════
class Control:
    def __init__(self, window=None):
        self._run = threading.Event()
        self._run.set()
        self._stop = threading.Event()
        self._skip = threading.Event()
        self.window = window          # «тихие часы»: работать только в этом интервале
        self.reporter = None

    def pause(self):
        self._run.clear()

    def resume(self):
        self._run.set()

    @property
    def paused(self) -> bool:
        return not self._run.is_set()

    def stop(self):
        self._stop.set()
        self._run.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def skip_current(self):
        self._skip.set()

    def consume_skip(self) -> bool:
        if self._skip.is_set():
            self._skip.clear()
            return True
        return False

    def checkpoint(self):
        """Ждёт на паузе и вне «тихих часов»; бросает Stopped при остановке."""
        while not self._run.wait(0.5):
            pass
        if self._stop.is_set():
            raise Stopped()
        if self.window and not in_window(self.window):
            if self.reporter:
                self.reporter.log(f"Вне разрешённого времени работы ({fmt_window(self.window)}). Жду…", "warn")
                self.reporter.state_hint("waiting_window")
            while not in_window(self.window):
                if self._stop.wait(20):
                    raise Stopped()
            if self.reporter:
                self.reporter.log("Начались разрешённые часы — продолжаю.")
                self.reporter.state_hint("running")

    def sleep(self, sec: float):
        end = time.monotonic() + sec
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            if self._stop.wait(min(left, 0.5)):
                raise Stopped()
        self.checkpoint()


class Reporter:
    """Базовый «отчётчик»: пишет журнал в файл. Консоль и GUI переопределяют emit/file_*."""

    def __init__(self):
        self._logf = None
        self._lock = threading.Lock()

    def open_log(self, path: Path):
        self.close_log()
        try:
            fmkdir(path.parent)
            self._logf = fopen(path, "a", encoding="utf-8")
            self._logf.write(f"\n===== {datetime.now():%d.%m.%Y %H:%M:%S} · {APP_NAME} {VERSION} =====\n")
            self._logf.flush()
        except OSError:
            self._logf = None

    def close_log(self):
        if self._logf:
            try:
                self._logf.close()
            except OSError:
                pass
            self._logf = None

    def log(self, msg: str, level: str = "info"):
        with self._lock:
            if self._logf:
                try:
                    self._logf.write(f"{datetime.now():%H:%M:%S} [{level}] {msg}\n")
                    self._logf.flush()
                except OSError:
                    pass
        self.emit(msg, level)

    # переопределяются
    def emit(self, msg, level):
        pass

    def course(self, i, n, course):
        self.log(f"\n━━━ [{i}/{n}] {course.fullname} ━━━")

    def item(self, i, n, sec, it):
        self.log(f"[{i}/{n}] {it.kind} · {it.name}")

    def file_start(self, title, total, initial=0, quiet=False):
        pass

    def file_progress(self, done):
        pass

    def file_end(self, status):
        pass

    def net_status(self, status, detail=""):
        pass

    def state_hint(self, state):
        pass

    def recovered(self, waited_seconds):
        pass

    def saved(self, rel_out, title, size, status):
        """Файл сохранён/уже был (для менеджера загрузок в GUI). rel_out — путь от папки сохранения."""
        pass

    def failed(self, entry):
        pass


class ConsoleReporter(Reporter):
    def __init__(self, verbose=False, notify_on_recover=True):
        super().__init__()
        self.verbose = verbose
        self.notify_on_recover = notify_on_recover
        self.bar = None
        self._plain = None

    def recovered(self, waited_seconds):
        if self.notify_on_recover and waited_seconds > 120:
            notify(APP_NAME, "Moodle снова доступен! Продолжаю загрузку.")

    def emit(self, msg, level):
        if level == "debug" and not self.verbose:
            return
        if self.bar is not None and tqdm is not None:
            tqdm.write(msg)
        else:
            if self._plain:
                print()
                self._plain = None
            print(msg, flush=True)

    def file_start(self, title, total, initial=0, quiet=False):
        self.file_end(None)
        if quiet and (total or 0) < 2 * 1024 * 1024:
            return
        desc = "    ↓ " + short(title, 36)
        if tqdm is not None:
            self.bar = tqdm(total=total, initial=initial, unit="B", unit_scale=True, unit_divisor=1024,
                            desc=desc, leave=False, dynamic_ncols=True, mininterval=0.3)
        else:
            self._plain = {"title": desc, "total": total, "t": 0.0}
        self._last_done = initial

    def file_progress(self, done):
        if self.bar is not None:
            self.bar.update(done - self._last_done)
            self._last_done = done
        elif self._plain is not None:
            now = time.monotonic()
            if now - self._plain["t"] > 0.5:
                self._plain["t"] = now
                tot = self._plain["total"]
                pct = f"{done * 100 / tot:5.1f}%" if tot else hsize(done)
                print(f"\r{self._plain['title']}  {pct}", end="", flush=True)

    def file_end(self, status):
        if self.bar is not None:
            self.bar.close()
            self.bar = None
        if self._plain is not None:
            print("\r" + " " * 70 + "\r", end="", flush=True)
            self._plain = None


# ════════════════════════════════════════════════════════════════════════════
#  СОХРАНЁННЫЕ НАСТРОЙКИ И ВХОД («запомнить меня»)
# ════════════════════════════════════════════════════════════════════════════
if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _dpapi(data: bytes, protect: bool) -> bytes:
        """Шифрование Windows DPAPI: расшифровать может только ваша учётная запись Windows на этом ПК."""
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        ent_bytes = b"MoodleOffline"
        ebuf = ctypes.create_string_buffer(ent_bytes, len(ent_bytes))
        entropy = _BLOB(len(ent_bytes), ctypes.cast(ebuf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _BLOB()
        if protect:
            ok = crypt32.CryptProtectData(ctypes.byref(blob_in), ctypes.c_wchar_p("MoodleOffline"),
                                          ctypes.byref(entropy), None, None, 0x01, ctypes.byref(blob_out))
        else:
            ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(entropy),
                                            None, None, 0x01, ctypes.byref(blob_out))
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
else:
    _dpapi = None


class Vault:
    """settings.json — обычные настройки; session.bin — cookies (+ пароль в Windows), зашифровано DPAPI."""

    def __init__(self, data_dir: Path | None = None):
        self.dir = data_dir = Path(data_dir or DATA_DIR)
        self.settings_path = data_dir / "settings.json"
        self.secret_path = data_dir / "session.bin"

    @property
    def can_encrypt(self) -> bool:
        return _dpapi is not None

    def load_settings(self) -> dict:
        try:
            with fopen(self.settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_settings(self, d: dict):
        write_text_atomic(self.settings_path, json.dumps(d, ensure_ascii=False, indent=2))

    @staticmethod
    def _read_secret(path: Path) -> dict:
        try:
            with fopen(path, "rb") as f:
                raw = f.read()
        except OSError:
            return {}
        try:
            if raw.startswith(b"DPAPI1") and _dpapi:
                return json.loads(_dpapi(raw[6:], protect=False).decode("utf-8"))
            if raw.startswith(b"PLAIN1"):
                return json.loads(raw[6:].decode("utf-8"))
        except Exception:
            pass
        return {}

    def _write_secret(self, path: Path, d: dict, plain_drop=()):
        fmkdir(self.dir)
        d = dict(d)
        if _dpapi:
            raw = b"DPAPI1" + _dpapi(json.dumps(d, ensure_ascii=False).encode("utf-8"), protect=True)
        else:
            for k in plain_drop:   # без DPAPI пароль на диск не пишем
                d.pop(k, None)
            raw = b"PLAIN1" + json.dumps(d, ensure_ascii=False).encode("utf-8")
        tmp = path.with_name(path.stem + ".tmp")
        with fopen(tmp, "wb") as f:
            f.write(raw)
        try:
            os.chmod(fsp(tmp), 0o600)
        except OSError:
            pass
        freplace(tmp, path)

    def load_secret(self) -> dict:
        return self._read_secret(self.secret_path)

    def save_secret(self, d: dict):
        self._write_secret(self.secret_path, d, plain_drop=("password",))

    # Токен Telegram-бота и адрес webhook — в отдельном файле: «Выйти и забыть вход» их не стирает.
    def load_notify(self) -> dict:
        return self._read_secret(self.dir / "notify.bin")

    def save_notify(self, d: dict):
        self._write_secret(self.dir / "notify.bin", d)

    def forget(self):
        funlink(self.secret_path)


# ════════════════════════════════════════════════════════════════════════════
#  HTTP-КЛИЕНТ MOODLE
# ════════════════════════════════════════════════════════════════════════════
def normalize_base(url: str) -> str:
    url = (url or "").strip()
    if not url:
        url = DEFAULT_URL
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    path = re.sub(r"/(my|login|course|mod|user|admin|blocks|lib|theme)(/.*)?$", "", p.path)
    path = re.sub(r"/index\.php$", "", path).rstrip("/")
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", "", ""))


def check_ip_country(use_system_proxy=False, timeout=6):
    """Внешний IP и страна (ISO-код) тем же маршрутом, каким пойдут запросы к Moodle.
    → (ip, страна, сервис) или (None, None, None), если ни один сервис не ответил."""
    sess = requests.Session()
    sess.trust_env = use_system_proxy
    for url, parse in GEO_SERVICES:
        try:
            r = sess.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            if r.status_code != 200:
                continue
            ip, cc = parse(r.json())
            if cc:
                return ip, str(cc).upper(), urlparse(url).netloc
        except Exception:
            continue
    return None, None, None


GEO_WARNING = ("Сервисы проверки видят ваш IP {ip} как «{cc}», а не Россию. Moodle СевГУ блокирует зарубежные адреса. "
               "Если в VPN-клиенте (FlClash и т.п.) для {host} стоит правило DIRECT/«напрямую» — это ложная тревога: "
               "проверка IP идёт через VPN, а Moodle — напрямую; просто скройте предупреждение. Иначе выключите VPN.")


class MoodleClient:
    def __init__(self, base_url, *, timeout=TIMEOUT, retries=RETRIES, page_delay=PAGE_DELAY,
                 file_delay=FILE_DELAY, wait_minutes=WAIT_SERVER_MINUTES, control=None, reporter=None,
                 debug_dir=None, use_system_proxy=False):
        self.base = normalize_base(base_url)
        self.host = urlparse(self.base).netloc.lower()                 # с портом, если он есть
        self.hostname = (urlparse(self.base).hostname or self.host).lower()  # без порта — для cookies
        self.timeout = (min(20, timeout), timeout)
        self.retries = max(1, retries)
        self.page_delay = page_delay
        self.file_delay = file_delay
        self.wait_minutes = wait_minutes
        self.no_wait = False          # True — при падении сервера не ждать, а сразу сообщить (вход при запуске)
        self.control = control or Control()
        self.rep = reporter or Reporter()
        self.debug_dir = debug_dir
        self._dbg_n = 0
        self.use_system_proxy = use_system_proxy
        self.s = requests.Session()
        # По умолчанию идём в Moodle напрямую, мимо системного прокси/VPN-клиента (FlClash и т.п. в режиме
        # «системный прокси»): сайт вуза блокирует зарубежные адреса. В режиме TUN это не поможет — VPN выключайте.
        self.s.trust_env = use_system_proxy
        self.s.headers.clear()
        self.s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "sec-ch-ua": f'"Chromium";v="{CHROME_VERSION}", "Google Chrome";v="{CHROME_VERSION}", "Not_A Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        })
        self._probe_s = requests.Session()
        self._probe_s.trust_env = use_system_proxy
        self.sesskey = None
        self.creds = None              # (логин, пароль) в памяти — для повторного входа, если сессия истекла
        self.relogin_cb = None         # функция, которая спросит пароль (консоль), если его нет в памяти
        self.on_cookies_changed = None
        self.last_page = None          # для заголовка Referer
        self._last_req = 0.0
        self._req_lock = threading.Lock()
        self._relogins = []
        self.requests_made = 0

    # ── заголовки «как у браузера» ──────────────────────────────────────────
    def _headers(self, kind: str, referer=None) -> dict:
        ref = referer or self.last_page
        h = {}
        if kind in ("page", "file"):
            h["Accept"] = ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
                           "image/apng,*/*;q=0.8")
            h["Upgrade-Insecure-Requests"] = "1"
            h["Sec-Fetch-Dest"] = "document"
            h["Sec-Fetch-Mode"] = "navigate"
            h["Sec-Fetch-Site"] = "same-origin" if ref else "none"
            h["Sec-Fetch-User"] = "?1"
        elif kind == "image":
            h["Accept"] = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
            h["Sec-Fetch-Dest"] = "image"
            h["Sec-Fetch-Mode"] = "no-cors"
            h["Sec-Fetch-Site"] = "same-origin"
        elif kind == "ajax":
            h["Accept"] = "application/json, text/javascript, */*; q=0.01"
            h["X-Requested-With"] = "XMLHttpRequest"
            h["Origin"] = urlparse(self.base)._replace(path="").geturl()
            h["Sec-Fetch-Dest"] = "empty"
            h["Sec-Fetch-Mode"] = "cors"
            h["Sec-Fetch-Site"] = "same-origin"
        if ref:
            h["Referer"] = ref
        return h

    # ── паузы ───────────────────────────────────────────────────────────────
    def _pause(self, kind):
        lo, hi = self.file_delay if kind in ("file", "image", "ajax") else self.page_delay
        if hi <= 0:
            return
        wait = random.uniform(lo, hi) - (time.monotonic() - self._last_req)
        if wait > 0:
            self.control.sleep(wait)

    def _backoff(self, attempt, reason, status=None, retry_after=None):
        if status in (429, 503):
            delay = min(10 * SLEEP_BASE, SLEEP_BASE * 2 ** (attempt - 1))    # «спящий режим»: 1, 2, 4, 8, 10 мин
        else:
            delay = min(36 * BACKOFF_BASE, BACKOFF_BASE * 2 ** (attempt - 1))  # 5, 10, 20, 40, 80, 160 с
        delay *= random.uniform(1.0, 1.25)
        if retry_after:
            ra = None
            if retry_after.strip().isdigit():
                ra = int(retry_after.strip())
            else:
                try:
                    ra = (email.utils.parsedate_to_datetime(retry_after) - datetime.now().astimezone()).total_seconds()
                except Exception:
                    ra = None
            if ra and ra > 0:
                delay = max(delay, min(1800, ra * SLEEP_BASE / 60))
        self.rep.net_status("slow", reason)
        self.rep.log(f"  ! {reason}. Пауза {fmt_dur(delay)} (попытка {attempt}/{self.retries})", "warn")
        self.control.sleep(delay)

    # ── проверка связи ──────────────────────────────────────────────────────
    def probe(self, timeout=15):
        """Лёгкий запрос к странице входа. → (ok | slow | down, время ответа).
        down — сайт не отвечает или отдаёт 502/504 (сервер «лежит»); slow — медленно (>3 с) или ошибки 5xx."""
        t0 = time.monotonic()
        try:
            r = self._probe_s.get(self.base + "/login/index.php", timeout=(10, timeout), stream=True,
                                  allow_redirects=False, headers={"User-Agent": USER_AGENT,
                                                                  "Accept-Language": "ru-RU,ru;q=0.9"})
            r.close()
        except Exception:
            return "down", None
        dt = time.monotonic() - t0
        if r.status_code in SERVER_DOWN_STATUS:
            return "down", dt
        if r.status_code >= 500:
            return "slow", dt
        return ("ok" if dt < 3 else "slow"), dt

    def wait_for_server(self, reason: str) -> bool:
        """Режим ожидания: пингуем сайт каждые 30–60 с, пока не оживёт. False — истёк лимит ожидания."""
        self.rep.net_status("down", reason)
        self.rep.state_hint("waiting_network")
        limit = f" (не дольше {self.wait_minutes} мин)" if self.wait_minutes else ""
        self.rep.log(f"⏸ {reason}. Режим ожидания: проверяю сайт каждые 30–60 с{limit}. "
                     f"Прогресс сохранён — можно и закрыть программу, докачает при следующем запуске.", "warn")
        t0 = time.monotonic()
        while True:
            if self.wait_minutes and time.monotonic() - t0 > self.wait_minutes * 60:
                return False
            self.control.sleep(random.uniform(*SERVER_PING_EVERY))
            status, dt = self.probe()
            self.rep.net_status(status, "" if dt is None else f"{dt:.1f} с")
            if status != "down":
                waited = time.monotonic() - t0
                self.rep.log(f"▶ Сервер снова доступен (ждали {fmt_dur(waited)}) — продолжаю с того же места.")
                self.rep.state_hint("running")
                self.rep.recovered(waited)
                return True

    # ── базовый запрос с повторами ──────────────────────────────────────────
    def request(self, method, url, *, kind="page", referer=None, headers=None, stream=False,
                allow_redirects=True, **kw):
        h = self._headers(kind, referer)
        if headers:
            h.update(headers)
        attempt = 0        # повторы при обрывах / разовых ошибках
        down_cycles = 0    # сколько раз ждали «оживания» сервера для этого запроса
        while True:
            self.control.checkpoint()
            self._pause(kind)
            try:
                with self._req_lock:
                    r = self.s.request(method, url, headers=h, timeout=self.timeout, stream=stream,
                                       allow_redirects=allow_redirects, **kw)
                self._last_req = time.monotonic()
                self.requests_made += 1
            except requests.exceptions.SSLError as e:
                raise MoodleError(f"ошибка сертификата сайта ({e}). Если браузер открывает сайт нормально, "
                                  f"установите пакет truststore: pip install truststore") from e
            except NET_ERRORS as e:
                self._last_req = time.monotonic()
                attempt += 1
                if attempt <= 2:
                    self._backoff(attempt, describe_error(e))
                    continue
                status, _ = self.probe()
                if status == "down":       # пропала сеть / свет / сервер — ждём, а не долбим
                    if self.no_wait or down_cycles >= 5 or not self.wait_for_server(f"Нет связи с {self.host}"):
                        raise NetworkDown(f"нет связи с {self.host}") from e
                    down_cycles += 1
                    attempt = 0
                    continue
                if attempt <= self.retries:  # сайт жив, сбоит именно этот запрос
                    self._backoff(attempt, describe_error(e))
                    continue
                raise RetriesExhausted(describe_error(e)) from e
            code = r.status_code
            if code in SERVER_DOWN_STATUS:
                r.close()
                if down_cycles >= 5:
                    raise RetriesExhausted(f"сервер раз за разом отвечает {code} на этот адрес")
                down_cycles += 1
                name = "Bad Gateway" if code == 502 else "Gateway Timeout" if code == 504 else "сервер недоступен"
                if self.no_wait or not self.wait_for_server(f"Сервер Moodle упал ({code} {name})"):
                    raise NetworkDown(f"сервер отвечает {code}")
                continue
            if (code in SLEEP_STATUS and attempt < self.retries) or (code in RETRY_STATUS and attempt < 2):
                ra = r.headers.get("Retry-After")
                r.close()
                attempt += 1
                why = {429: "сервер просит снизить частоту запросов (429)",
                       503: "сервер перегружен (503) — «спящий режим»"}.get(code, f"сервер ответил ошибкой {code}")
                self._backoff(attempt, why, status=code, retry_after=ra)
                continue
            if attempt or down_cycles:
                self.rep.net_status("ok")
            return r

    @staticmethod
    def _is_login_url(url: str) -> bool:
        return "/login/index.php" in urlparse(url).path

    @staticmethod
    def _has_logout(text: str) -> bool:
        return bool(re.search(r"/login/logout\.php\?sesskey=", text))

    def _update_sesskey(self, text: str):
        m = re.search(r'"sesskey":"([^"]+)"', text) or re.search(r"sesskey=([A-Za-z0-9]{6,})", text)
        if m:
            self.sesskey = m.group(1)

    def _debug_save(self, url, text):
        if not self.debug_dir:
            return
        try:
            self._dbg_n += 1
            slug = re.sub(r"[^A-Za-z0-9]+", "_", urlparse(url).path + "_" + urlparse(url).query)[:80]
            red = re.sub(r'("sesskey":")[^"]+', r"\1REDACTED", text)
            red = re.sub(r"(sesskey=)[A-Za-z0-9]+", r"\1REDACTED", red)
            write_text_atomic(Path(self.debug_dir) / f"{self._dbg_n:04d}_{slug}.html", red)
        except OSError:
            pass

    @staticmethod
    def _text(r) -> str:
        if "charset" not in r.headers.get("Content-Type", "").lower():
            r.encoding = "utf-8"
        return r.text

    def page(self, url, *, params=None, referer=None):
        """GET страницы Moodle → (soup, response). Сам перезаходит, если сессия истекла."""
        relogged = False
        while True:
            r = self.request("GET", url, params=params, kind="page", referer=referer)
            text = self._text(r)
            if (self._is_login_url(r.url) or 'id="page-login-index"' in text) and not self._is_login_url(url):
                if not relogged and self.relogin():
                    relogged = True
                    continue
                raise SessionExpired("сессия истекла, требуется вход")
            r.raise_for_status()
            self.last_page = r.url
            self._update_sesskey(text)
            self._debug_save(r.url, text)
            soup = BeautifulSoup(text, PARSER)
            err = soup.select_one(SEL["error_box"])
            if err and not soup.select_one(SEL["section"]) and not soup.select_one(SEL["activity"]):
                msg = clean_text(err.get_text(" "))
                if msg:
                    raise MoodleError(f"Moodle: {short(msg, 200)}")
            return soup, r

    def fetch_internal(self, url, *, kind="page", stream=True, referer=None, max_hops=6):
        """GET страницы или файла этого же Moodle для просмотра внутри программы → (ответ, итоговый адрес).
        Переходы разбираем сами и идём только по адресам этого сайта; ушло на чужой сайт — ExternalLink.
        Сам перезаходит, если сессия истекла. Ответ при stream=True не прочитан — вызывающий закрывает его."""
        relogged = False
        while True:
            cur = url
            for _ in range(max_hops + 1):
                r = self.request("GET", cur, kind=kind, referer=referer, stream=stream, allow_redirects=False)
                loc = r.headers.get("Location") if 300 <= r.status_code < 400 else None
                if not loc:
                    break
                r.close()
                nxt = urljoin(cur, loc)
                p = urlparse(nxt)
                if p.scheme not in ("http", "https") or p.netloc.lower() != self.host:
                    raise ExternalLink(nxt)
                cur = nxt
            else:
                raise MoodleError("слишком много перенаправлений")
            if self._is_login_url(cur) and not self._is_login_url(url):
                r.close()
                if not relogged and self.relogin():
                    relogged = True
                    continue
                raise SessionExpired("сессия истекла, требуется вход")
            if r.status_code >= 400:
                code = r.status_code
                r.close()
                raise MoodleError(f"сайт ответил ошибкой {code}")
            self.last_page = cur
            return r, cur

    # ── вход ────────────────────────────────────────────────────────────────
    def _has_session_cookie(self) -> bool:
        return any(c.name.lower().startswith("moodlesession") for c in self.s.cookies)

    @staticmethod
    def _pick_login_form(soup):
        """Форма входа: ищем ту, где есть поле пароля (в шапке темы бывает вторая форма — без logintoken)."""
        cands = [f for f in soup.find_all("form") if f.select_one("input[type=password], input[name=password]")]
        if not cands:
            return None

        def score(f):
            sc = 0
            if f.get("id") == "login":
                sc += 100
            if f.select_one("input[name=logintoken]"):
                sc += 40
            if "login/index.php" in (f.get("action") or ""):
                sc += 10
            if f.find_parent(["nav", "header"]) is not None:
                sc -= 30
            return sc
        return max(cands, key=score)

    def _save_login_debug(self, diag: list, *pages):
        """Диагностика неудачного входа — без пароля; токены в HTML заменены на REDACTED."""
        try:
            fmkdir(DATA_DIR)
            write_text_atomic(DATA_DIR / "login_debug.txt", "\n".join(diag) + "\n")
            for n, html_text in enumerate(pages, 1):
                red = re.sub(r'(name="(?:logintoken|sesskey)"[^>]*value=")[^"]*', r"\1REDACTED", html_text or "")
                red = re.sub(r'(value=")[^"]*("[^>]*name="(?:logintoken|sesskey)")', r"\1REDACTED\2", red)
                red = re.sub(r'("sesskey":")[^"]+', r"\1REDACTED", red)
                red = re.sub(r"(sesskey=)[A-Za-z0-9]+", r"\1REDACTED", red)
                write_text_atomic(DATA_DIR / f"login_page_{n}.html", red)
        except OSError:
            pass

    IDP_SELECTORS = (".potentialidplist a[href], .potentialidp a[href], .login-identityproviders a[href], "
                     "a[href*='/auth/oauth2/login.php'], a[href*='/auth/saml2/'], a[href*='authCAS='], a[href*='/auth/cas/'], "
                     "a[href*='/auth/shibboleth/'], a[href*='/auth/mo_saml/'], a[href*='/auth/oidc/'], a[href*='/auth/esia/']")
    IDP_TEXT = re.compile(r"войти через|вход через|войти с помощью|единой учетн|единой учётн|единый вход|ЕЛК|личн\w* кабинет|"
                          r"СевГУ ID|СевГУ\.?\s*Аккаунт|госуслуг|ЕСИА|\bSSO\b|keycloak|microsoft|office ?365", re.I)

    @staticmethod
    def _is_guest_form(form) -> bool:
        if form is None:
            return False
        if form.get("id") == "guestlogin":
            return True
        u = form.select_one("input[name=username]")
        return u is not None and (u.get("type") or "").lower() == "hidden" and (u.get("value") or "").lower() == "guest"

    def _forms_in_scripts(self, soup):
        """Форма входа, которую тема вставляет через JavaScript (лежит строкой внутри <script>/<template>)."""
        for tag in soup.find_all(["script", "template"]):
            raw = tag.string or tag.decode_contents() or ""
            if "<form" in raw and "password" in raw:
                raw = raw.replace("\\/", "/").replace('\\"', '"')
                f = self._pick_login_form(BeautifulSoup(raw, PARSER))
                if f is not None and not self._is_guest_form(f):
                    return f
        return None

    def _find_idps(self, soup, page_url):
        found = []
        for a in soup.select(self.IDP_SELECTORS):
            found.append(a)
        for a in soup.find_all("a", href=True):
            if self.IDP_TEXT.search(clean_text(a.get_text(" ")) + " " + (a.get("title") or "")):
                found.append(a)
        out, seen = [], set()
        for a in found:
            u = urljoin(page_url, a["href"])
            path = urlparse(u).path
            if u in seen or not u.startswith("http") or any(x in path for x in ("/forgot_password", "/signup", "/logout")):
                continue
            seen.add(u)
            out.append((clean_text(a.get_text(" ")) or clean_text(a.get("title")) or urlparse(u).netloc, u))
        # у СевГУ на странице входа две кнопки: «СевГУ.Аккаунт» (единый вход вуза) и «Online EDU» — сначала вузовская
        out.sort(key=lambda x: 0 if re.search(r"севгу|sevsu|аккаунт|единый|единой", x[0], re.I) else 1)
        return out

    @staticmethod
    def _safe_url(u: str) -> str:
        """Адрес для журнала: без значений параметров (в них бывают одноразовые коды)."""
        p = urlparse(u)
        q = "&".join(k + "=…" for k, _ in parse_qsl(p.query, keep_blank_values=True))
        return urlunparse(p._replace(query=q, fragment=""))

    def _describe_forms(self, soup) -> str:
        forms = soup.find_all("form")
        return f"форм: {len(forms)}" + ("; " if forms else "") + "; ".join(
            f"id={f.get('id')!r} action={self._safe_url(urljoin(self.base, f.get('action') or ''))} "
            f"поля={[i.get('name') for i in f.select('input[name]')]}" for f in forms)

    def _login_sso(self, name, start_url, username, password, diag, pages) -> bool:
        """Вход через внешний сервис (OAuth2/OpenID, SAML, CAS…): как браузер — переходим по кнопке,
        вводим логин/пароль на странице единого входа, возвращаемся в Moodle."""
        diag.append(f"  SSO «{name}»: {self._safe_url(start_url)}")
        r = self.request("GET", start_url, kind="page", referer=self.base + "/login/index.php")
        sent = False
        for step in range(1, 9):
            text = self._text(r)
            host = (urlparse(r.url).hostname or "").lower()
            soup = BeautifulSoup(text, PARSER)
            diag.append(f"   {step}) {r.status_code} {self._safe_url(r.url)} · {self._describe_forms(soup)}")
            pages.append(text)
            if host == self.hostname and self._has_logout(text):
                self._update_sesskey(text)
                self.last_page = r.url
                return True
            form = self._pick_login_form(soup)
            if form is not None and not self._is_guest_form(form) and not sent:
                action = urljoin(r.url, form.get("action") or r.url)
                if urlparse(action).scheme != "https" and urlparse(self.base).scheme == "https":
                    raise LoginError(f"страница единого входа {host} не защищена (http) — пароль туда не отправляю")
                data = {i["name"]: i.get("value", "") for i in form.select("input[name]")
                        if (i.get("type") or "text").lower() in ("hidden", "submit")}
                ui = (form.select_one("input[name=username], input[name=UserName], input[name=login], input[name=email]")
                      or form.select_one("input[type=text], input[type=email], input:not([type])"))
                pw = form.select_one("input[type=password]")
                data[(ui.get("name") if ui is not None and ui.get("name") else "username")] = username
                data[(pw.get("name") if pw is not None and pw.get("name") else "password")] = password
                btn = form.select_one("button[name][type=submit], button[name]:not([type])")
                if btn is not None:
                    data.setdefault(btn["name"], btn.get("value", ""))
                diag.append(f"      ввожу логин/пароль в форму на {host} (поля {list(data)})")
                r = self.request("POST", action, data=data, kind="page", referer=r.url)
                sent = True
                continue
            # автоматически отправляемые формы (SAMLResponse, code/state, wresult…) — браузер шлёт их скриптом
            auto = next((f for f in soup.find_all("form") if f.select_one(
                "input[name=SAMLResponse], input[name=SAMLRequest], input[name=code], input[name=id_token], "
                "input[name=wresult], input[name=RelayState], input[name=state]")), None)
            if auto is not None:
                action = urljoin(r.url, auto.get("action") or r.url)
                data = {i["name"]: i.get("value", "") for i in auto.select("input[name]")}
                diag.append(f"      автоотправка формы → {self._safe_url(action)}")
                r = self.request((auto.get("method") or "post").upper(), action, data=data, kind="page", referer=r.url)
                continue
            m = re.search(r"""http-equiv=["']?refresh["']?[^>]*url=([^"'>]+)""", text, re.I) or \
                re.search(r"""(?:window\.)?location(?:\.href)?\s*=\s*["']([^"']+)["']""", text)
            if m:
                r = self.request("GET", urljoin(r.url, htmllib.unescape(m.group(1).strip())), kind="page", referer=r.url)
                continue
            if sent:
                err = soup.select_one("#input-error, .kc-feedback-text, .alert-error, .alert-danger, .error, "
                                      "[class*=error], [id*=error]")
                msg = clean_text(err.get_text(" ")) if err is not None else ""
                if msg:
                    raise LoginError(f"сервис единого входа ({host}): {short(msg, 200)}")
            break
        return self.verify()

    def login(self, username: str, password: str):
        url = self.base + "/login/index.php"
        last = "не удалось войти"
        diag = [f"Диагностика входа · {datetime.now():%d.%m.%Y %H:%M:%S} · {self.base}",
                f"Логин: {username!r} (длина пароля: {len(password)})"]
        home_soup = None
        if not self._has_session_cookie():
            # Как браузер: сначала главная страница — получаем cookie сессии, потом форма входа.
            try:
                r0 = self.request("GET", self.base + "/", kind="page")
                home_soup = BeautifulSoup(self._text(r0), PARSER)
                diag.append(f"0) GET / → {r0.status_code}, {r0.url}, cookies: {[c.name for c in self.s.cookies]}; "
                            f"на главной {self._describe_forms(home_soup)}")
            except (RetriesExhausted, requests.RequestException) as e:
                diag.append(f"0) GET / → ошибка {describe_error(e)}")
        for attempt in range(1, 4):
            r = self.request("GET", url, kind="page", referer=self.base + "/",
                             headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            text = self._text(r)
            diag.append(f"{attempt}.1) GET {url} → {r.status_code}, итоговый адрес {r.url}, "
                        f"cookies: {[c.name for c in self.s.cookies]}")
            if self._has_logout(text):          # уже вошли
                self._update_sesskey(text)
                self.creds = (username, password)
                return
            soup = BeautifulSoup(text, PARSER)
            forms = soup.find_all("form")
            form = self._pick_login_form(soup)
            diag.append(f"     форм на странице: {len(forms)}: " + "; ".join(
                f"id={f.get('id')!r} action={f.get('action')!r} поля={[i.get('name') for i in f.select('input[name]')]}"
                for f in forms))
            guest_only = form is None or self._is_guest_form(form)
            if guest_only:
                jsform = self._forms_in_scripts(soup)
                if jsform is not None:
                    diag.append("     форма входа найдена внутри скрипта темы")
                    form, guest_only = jsform, False
            if guest_only and home_soup is not None:
                hform = self._pick_login_form(home_soup) or self._forms_in_scripts(home_soup)
                if hform is not None and not self._is_guest_form(hform):
                    diag.append("     форма входа найдена на главной странице")
                    form, guest_only = hform, False
            auth_links = sorted({self._safe_url(urljoin(r.url, a["href"])) for a in soup.select("a[href]")
                                 if any(k in a["href"] for k in ("/auth/", "login", "sso", "oauth", "saml", "cas"))})
            diag.append(f"     ссылки, связанные со входом: {auth_links or 'нет'}")
            idps = self._find_idps(soup, r.url)
            if not idps and home_soup is not None:
                idps = self._find_idps(home_soup, self.base + "/")
            diag.append(f"     кнопки внешнего входа: {[(n, self._safe_url(u)) for n, u in idps] or 'нет'}")
            if guest_only and idps:
                pages = [text]
                last_err = None
                for name, u in idps[:3]:
                    try:
                        if self._login_sso(name, u, username, password, diag, pages):
                            self.creds = (username, password)
                            self._cookies_changed()
                            diag.append("     ✓ вход через внешний сервис выполнен")
                            return
                    except LoginError as e:
                        last_err = e
                        diag.append(f"     ✗ {e}")
                        break    # пароль отклонён — другие кнопки не пробуем (защита от блокировки)
                self._save_login_debug(diag, *pages[:4])
                raise LoginError(str(last_err) if last_err else
                                 f"вход через «{idps[0][0]}» не удался — войдите по cookie и пришлите login_debug.txt")
            if form is None:
                self._save_login_debug(diag, text)
                raise LoginError("на странице нет формы входа с паролем (возможно, вход через единый аккаунт/SSO — "
                                 "используйте вход по cookie)")
            if guest_only:
                diag.append("     обычной формы нет — только «Зайти гостем»; пробую отправить логин/пароль напрямую")
            data = {}
            for inp in form.select("input[name]"):
                if (inp.get("type") or "text").lower() == "hidden":
                    data[inp["name"]] = inp.get("value", "")
            if not data.get("logintoken"):
                tok = soup.select_one("input[name=logintoken]")
                if tok is not None and tok.get("value"):
                    data["logintoken"] = tok["value"]
            data.setdefault("anchor", "")
            user_field = "username"
            ui = form.select_one("input[name=username]") or form.select_one("input[type=text], input[type=email]")
            if ui is not None and ui.get("name"):
                user_field = ui["name"]
            pw = form.select_one("input[type=password]")
            pass_field = pw.get("name") if pw is not None and pw.get("name") else "password"
            data[user_field] = username
            data[pass_field] = password
            action = urljoin(r.url, form.get("action") or url)
            diag.append(f"     выбрана форма id={form.get('id')!r}, action={action}, поля логина/пароля: "
                        f"{user_field}/{pass_field}, logintoken: {'есть' if data.get('logintoken') else 'НЕТ'}")
            r2 = self.request("POST", action, data=data, kind="page", referer=r.url,
                              headers={"Origin": urlparse(self.base)._replace(path="").geturl(),
                                       "Sec-Fetch-Site": "same-origin", "Cache-Control": "max-age=0"})
            text2 = self._text(r2)
            chain = " → ".join(f"{h.status_code} {h.headers.get('Location', '')}" for h in r2.history) or "без перенаправлений"
            diag.append(f"{attempt}.2) POST → {r2.status_code}, итоговый адрес {r2.url}; цепочка: {chain}; "
                        f"cookies: {[c.name for c in self.s.cookies]}")
            if self._has_logout(text2):
                self._update_sesskey(text2)
                self.creds = (username, password)
                self.last_page = r2.url
                self._cookies_changed()
                return
            soup2 = BeautifulSoup(text2, PARSER)
            err = select_first(soup2, SEL["login_error"])
            msg = clean_text(err.get_text(" ")) if err is not None else ""
            if msg:
                diag.append(f"     ответ Moodle: «{msg}»")
                self._save_login_debug(diag, text, text2)
                if guest_only:
                    msg += (" На странице входа этого сайта нет обычной формы «логин + пароль» (только «Зайти гостем») — "
                            "видимо, вход идёт через внешний сервис, которого программа не нашла. Войдите по cookie "
                            "и пришлите файлы login_debug.txt и login_page_1.html.")
                # Неверный пароль — НЕ повторяем (чтобы не заблокировали аккаунт за перебор)
                raise LoginError(msg)
            if self.verify():
                self.creds = (username, password)
                self._cookies_changed()
                return
            last = "сервер не подтвердил вход"
            diag.append("     вход не подтверждён (нет ссылки «Выход»)")
        self._save_login_debug(diag)
        raise LoginError(last)

    def verify(self) -> bool:
        """Проверяет, что сессия жива (без повторного входа)."""
        try:
            r = self.request("GET", self.base + "/my/", kind="page")
        except (RetriesExhausted, MoodleError):
            return False
        text = self._text(r)
        if self._is_login_url(r.url) or not self._has_logout(text):
            return False
        self._update_sesskey(text)
        self.last_page = r.url
        return True

    def relogin(self) -> bool:
        now = time.monotonic()
        self._relogins = [t for t in self._relogins if now - t < 3600]
        if self._relogins and now - self._relogins[-1] < 300 or len(self._relogins) >= 3:
            return False   # не входим слишком часто — это похоже на перебор паролей
        creds = self.creds
        if not creds and self.relogin_cb:
            creds = self.relogin_cb()
        if not creds:
            return False
        self._relogins.append(now)
        self.rep.log("Сессия истекла — вхожу заново…", "warn")
        try:
            self.s.cookies.clear()
            self.login(*creds)
            return True
        except MoodleError as e:
            self.rep.log(f"Повторный вход не удался: {e}", "error")
            return False

    def export_cookies(self) -> list:
        return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self.s.cookies if self.hostname.endswith(c.domain.lstrip(".") or self.hostname)]

    def import_cookies(self, cookies: list):
        for c in cookies or []:
            self.s.cookies.set(c["name"], c["value"], domain=c.get("domain") or self.hostname, path=c.get("path") or "/")

    def use_cookie_string(self, cookie: str):
        """'MoodleSession=abc' или просто 'abc' (значение MoodleSession из браузера)."""
        cookie = cookie.strip()
        for part in cookie.split(";"):
            part = part.strip()
            if not part:
                continue
            name, _, value = part.partition("=") if "=" in part else ("MoodleSession", "", part)
            self.s.cookies.set(name.strip(), value.strip(), domain=self.hostname, path="/")

    def _cookies_changed(self):
        if self.on_cookies_changed:
            try:
                self.on_cookies_changed()
            except Exception:
                pass

    # ── AJAX-сервисы Moodle (как у блока «Обзор курсов») ─────────────────────
    def ajax(self, method: str, args: dict):
        if not self.sesskey:
            self.verify()
        r = self.request("POST", f"{self.base}/lib/ajax/service.php",
                         params={"sesskey": self.sesskey, "info": method}, kind="ajax",
                         referer=self.base + "/my/", json=[{"index": 0, "methodname": method, "args": args}])
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("error"):
            raise MoodleError(str(data.get("error")))
        res = data[0]
        if res.get("error"):
            exc = res.get("exception") or {}
            code = exc.get("errorcode", "")
            if code in ("servicerequireslogin", "requireloginerror", "invalidsesskey"):
                if self.relogin():
                    return self.ajax(method, args)
                raise SessionExpired("сессия истекла")
            raise MoodleError(exc.get("message") or code or "ошибка AJAX")
        return res.get("data")

    # ── список курсов ───────────────────────────────────────────────────────
    def get_courses(self) -> list[Course]:
        courses, seen = [], set()
        now = time.time()
        for classification in ("all", "hidden"):
            try:
                data = self.ajax("core_course_get_enrolled_courses_by_timeline_classification",
                                 {"offset": 0, "limit": 0, "classification": classification, "sort": "fullname"})
            except (NetworkDown, Stopped, SessionExpired):
                raise
            except Exception as e:
                self.rep.log(f"AJAX-список курсов недоступен ({e}), беру из меню/профиля", "debug")
                break
            for c in (data or {}).get("courses", []):
                cid = int(c["id"])
                if cid in seen:
                    continue
                seen.add(cid)
                end = c.get("enddate") or 0
                start = c.get("startdate") or 0
                img = c.get("courseimage") or ""
                courses.append(Course(
                    id=cid, fullname=strip_tags(c.get("fullnamedisplay") or c.get("fullname")),
                    shortname=strip_tags(c.get("shortname")), url=c.get("viewurl") or "",
                    category=strip_tags(c.get("coursecategory") or ""), image=img,
                    progress=c.get("progress") if c.get("hasprogress") else None,
                    hidden=bool(c.get("hidden")) or classification == "hidden",
                    past=bool(end and end < now), future=bool(start and start > now)))
        if not courses:
            courses = self._courses_from_html()
        return courses

    def _courses_from_html(self) -> list[Course]:
        found: dict[int, str] = {}
        for path in ("/my/", "/user/profile.php", "/my/courses.php"):
            try:
                soup, r = self.page(self.base + path)
            except (NetworkDown, Stopped, SessionExpired):
                raise
            except Exception:
                continue
            for a in soup.select("a[href*='/course/view.php?id='], a[href*='/user/view.php'][href*='course=']"):
                u = urljoin(r.url, a["href"])
                cid = query_param(u, "course") if "/user/view.php" in u else query_param(u, "id")
                if not cid or not cid.isdigit() or int(cid) == 1:
                    continue
                name = clean_text(a.get_text(" "))
                if len(name) > len(found.get(int(cid), "")):
                    found[int(cid)] = name
        return [Course(id=k, fullname=v or f"Курс {k}", url=f"{self.base}/course/view.php?id={k}")
                for k, v in sorted(found.items(), key=lambda kv: kv[1].lower())]

    # ── календарь Moodle: сроки сдачи и события ─────────────────────────────
    def get_action_events(self, start: float, end: float) -> tuple[list, bool]:
        """События блока «График» (сроки сдачи заданий, закрытие тестов…): core_calendar_get_action_events_by_timesort.
        Moodle отдаёт по 50 штук, дальше просим «после такого-то id». Сданные работы Moodle сам убирает из этого списка.
        → (события как есть, получили ли всё)."""
        events, after = [], 0
        for _ in range(20):
            data = self.ajax("core_calendar_get_action_events_by_timesort",
                             {"timesortfrom": int(start), "timesortto": int(end), "aftereventid": after,
                              "limitnum": 50, "limittononsuspendedevents": True})
            part = (data or {}).get("events") or []
            events += part
            after = (data or {}).get("lastid") or 0
            if len(part) < 50 or not after:
                return events, True
        return events, False       # 20 полных страниц подряд — читаем не всё, лучше не делать выводов о «пропавших»

    def get_month_events(self, start: float, end: float) -> list:
        """Обычные события календаря (ваши записи, события курсов и университета): «месячный вид» — по месяцу за запрос."""
        out, seen = [], set()
        a, b = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
        y, m = a.year, a.month
        for _ in range(15):
            if (y, m) > (b.year, b.month):
                break
            data = self.ajax("core_calendar_get_calendar_monthly_view",
                             {"year": y, "month": m, "courseid": 1, "categoryid": 0, "includenavigation": False,
                              "mini": False, "day": 1})
            for week in (data or {}).get("weeks") or []:
                for day in week.get("days") or []:
                    for ev in day.get("events") or []:
                        ts = ev.get("timestart") or 0
                        if ev.get("id") in seen or not (start <= ts <= end):
                            continue
                        seen.add(ev.get("id"))
                        out.append(ev)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out

    # ── разбор страницы курса ───────────────────────────────────────────────
    def parse_course(self, course: Course):
        url = f"{self.base}/course/view.php?id={course.id}"
        soup, r = self.page(url)
        course.url = url
        title = select_first(soup, SEL["course_title"])
        if title and clean_text(title.get_text(" ")):
            course.fullname = course.fullname or clean_text(title.get_text(" "))
        if not course.fullname:
            course.fullname = clean_text(soup.title.get_text()) if soup.title else f"Курс {course.id}"

        sections = parse_sections(soup, r.url, course.id)
        by_num = {s.number: s for s in sections}
        to_fetch = [s.page_url for s in sections if not s.items and s.page_url]
        # Форматы «одна секция на странице», вкладки, плитки: ссылки вида course/view.php?id=N&section=K
        for a in soup.select("a[href]"):
            u = urljoin(r.url, a["href"])
            p = urlparse(u)
            if p.path.endswith("/course/view.php") and query_param(u, "id") == str(course.id):
                sn = query_param(u, "section")
                if sn and sn.isdigit() and int(sn) not in by_num:
                    to_fetch.append(u.split("#", 1)[0])
        if not sections:
            to_fetch += [urljoin(r.url, a["href"]) for a in soup.select("a[href*='/course/section.php?id=']")]
        done = set()
        for link in to_fetch:
            if link in done:
                continue
            done.add(link)
            try:
                s2, r2 = self.page(link, referer=url)
            except (NetworkDown, Stopped, SessionExpired):
                raise
            except Exception as e:
                self.rep.log(f"  не открылась страница раздела {link}: {describe_error(e)}", "warn")
                continue
            for sec in parse_sections(s2, r2.url, course.id):
                ex = by_num.get(sec.number)
                if ex is None:
                    sections.append(sec)
                    by_num[sec.number] = sec
                elif not ex.items and sec.items:
                    ex.items = sec.items
        sections.sort(key=lambda s: s.number)
        if not any(s.items for s in sections):
            g = generic_scan(soup, r.url)
            if g.items:
                sections = [g]
        # одинаковый элемент мог попасть дважды (основная страница + страница раздела)
        seen = set()
        for s in sections:
            uniq = []
            for it in s.items:
                if it.key in seen:
                    continue
                seen.add(it.key)
                uniq.append(it)
            s.items = uniq
        course.sections = [s for s in sections if s.items]
        return course

    # ── скачивание файла: .tmp → переименование, докачка Range, проверка SHA-1 ─
    def download(self, url, dest: Path, *, title="", referer=None, quiet=False, max_bytes=0) -> dict:
        tmp = dest.with_name(dest.name + ".tmp")
        fmkdir(dest.parent)
        attempts = 0
        fresh_retry = False
        relogged = False
        while True:
            self.control.checkpoint()
            pos = fsize(tmp) if fexists(tmp) else 0
            if pos and pos <= RESUME_BACKOFF:
                pos = 0
            elif pos:
                pos -= RESUME_BACKOFF
                with fopen(tmp, "r+b") as f:
                    f.truncate(pos)
            headers = {"Accept-Encoding": "identity"}
            if pos:
                headers["Range"] = f"bytes={pos}-"
            started = False
            try:
                r = self.request("GET", url, kind="file", referer=referer, headers=headers, stream=True)
                with r:
                    if self._is_login_url(r.url):
                        if not relogged and self.relogin():
                            relogged = True
                            continue
                        raise SessionExpired("сессия истекла")
                    if r.status_code == 416:
                        funlink(tmp)
                        attempts += 1
                        if attempts > self.retries:
                            raise MoodleError("сервер отклонил докачку (416)")
                        continue
                    r.raise_for_status()
                    ctype = r.headers.get("Content-Type", "")
                    if "text/html" in ctype.lower() and "pluginfile.php" not in urlparse(r.url).path:
                        raise MoodleError("сервер вернул веб-страницу вместо файла")
                    if r.status_code == 206:
                        m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
                        total = int(m.group(1)) if m else None
                        mode = "ab"
                    else:
                        pos, mode = 0, "wb"
                        cl = r.headers.get("Content-Length", "")
                        total = int(cl) if cl.isdigit() else None
                    if max_bytes and total is not None and total > max_bytes:
                        funlink(tmp)
                        raise RuleSkip(f"файл {hsize(total)} больше лимита {hsize(max_bytes)}")
                    etag = r.headers.get("ETag", "").strip()
                    etag = etag[2:] if etag.startswith("W/") else etag
                    etag = etag.strip('"').lower()
                    self.rep.file_start(title or dest.name, total, pos, quiet)
                    started = True
                    done = pos
                    with fopen(tmp, mode) as f:
                        for chunk in r.iter_content(CHUNK):
                            if chunk:
                                f.write(chunk)
                                done += len(chunk)
                                self.rep.file_progress(done)
                            if self.control.consume_skip():
                                raise SkipFile()
                            self.control.checkpoint()
                        f.flush()
                        os.fsync(f.fileno())
                    final_url = r.url
                size = fsize(tmp)
                if total is not None and size != total:
                    raise IncompleteRead(f"получено {hsize(size)} из {hsize(total)}")
                if re.fullmatch(r"[0-9a-f]{40}", etag) and sha1_file(tmp) != etag:
                    if pos > 0 or not fresh_retry:
                        funlink(tmp)
                        fresh_retry = True
                        self.rep.log("  контрольная сумма не совпала — скачиваю файл заново", "warn")
                        continue
                    self.rep.log("  ETag не совпал с SHA-1 (сервер, видимо, считает его иначе)", "debug")
                freplace(tmp, dest)
                self.rep.file_end("new")
                return {"size": size, "final_url": final_url, "content_type": ctype}
            except SkipFile:
                self.rep.file_end("skip")
                raise
            except (Stopped, KeyboardInterrupt):
                if started:
                    self.rep.file_end("stop")
                raise
            except NET_ERRORS + (IncompleteRead,) as e:
                if started:
                    self.rep.file_end("retry")
                attempts += 1
                if attempts > self.retries:
                    raise RetriesExhausted(f"обрывы при загрузке ({describe_error(e)})") from e
                self._backoff(attempts, f"обрыв загрузки ({describe_error(e)}), докачаю с места остановки")
            except Exception:
                if started:
                    self.rep.file_end("fail")
                raise

    def resolve_redirect(self, url, referer=None):
        """Возвращает адрес, на который Moodle перенаправляет (…/view.php?id=N&redirect=1), либо None."""
        relogged = False
        while True:
            r = self.request("GET", url, kind="page", referer=referer, stream=True, allow_redirects=False)
            with r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                    loc = urljoin(url, r.headers["Location"])
                    if self._is_login_url(loc):
                        if not relogged and self.relogin():
                            relogged = True
                            continue
                        raise SessionExpired("сессия истекла")
                    return loc, None
                r.raise_for_status()
                ctype = r.headers.get("Content-Type", "")
                if "text/html" not in ctype.lower():   # файл отдан сразу, без перенаправления
                    name = filename_from_cd(r.headers.get("Content-Disposition"))
                    if not name:
                        import mimetypes
                        name = "file" + (mimetypes.guess_extension(ctype.split(";")[0].strip()) or "")
                    return url, name
                return None, None

    def fetch_bytes(self, url, kind="image", limit=5 * 1024 * 1024) -> tuple[bytes, str]:
        r = self.request("GET", url, kind=kind, stream=True)
        with r:
            r.raise_for_status()
            data = r.raw.read(limit + 1, decode_content=True)
            if len(data) > limit:
                raise MoodleError("слишком большой файл")
            return data, r.headers.get("Content-Type", "application/octet-stream")


# ════════════════════════════════════════════════════════════════════════════
#  РАЗБОР HTML
# ════════════════════════════════════════════════════════════════════════════
def select_first(node, selectors: str):
    """Как select_one, но по ПРИОРИТЕТУ: сначала первый селектор из списка, потом второй и т.д."""
    for sel in selectors.split(","):
        sel = sel.strip()
        if sel:
            el = node.select_one(sel)
            if el is not None:
                return el
    return None


def select_main(soup):
    return select_first(soup, SEL["main_region"]) or soup.body or soup


def text_without_hidden(node) -> str:
    node = copy.copy(node)
    for h in node.select(SEL["hidden_text"]):
        h.decompose()
    return clean_text(node.get_text(" "))


def _has_ancestor_in(el, ids: set) -> bool:
    return any(id(p) in ids for p in el.parents)


def context_id(soup):
    body = soup.body
    if body:
        for c in body.get("class") or []:
            m = re.fullmatch(r"context-(\d+)", c)
            if m:
                return m.group(1)
    m = re.search(r'"contextid":(\d+)', str(soup)[:20000])
    return m.group(1) if m else None


def is_section_link(u: str, cid: int) -> bool:
    p = urlparse(u)
    if p.path.endswith("/course/view.php") and query_param(u, "id") == str(cid) and (query_param(u, "section") or "").isdigit():
        return True
    return p.path.endswith("/course/section.php") and bool(query_param(u, "id"))


def label_heading(content) -> str:
    for h in content.select("h1, h2, h3, h4, h5, h6, strong, b"):
        t = clean_text(h.get_text(" "))
        if len(t) >= 3:
            return short(t, 120)
    for line in content.get_text("\n").split("\n"):
        t = clean_text(line)
        if t:
            return short(t, 120)
    return ""


def parse_sections(soup, page_url: str, cid: int) -> list[Section]:
    main = select_main(soup)
    els = main.select(SEL["section"]) or soup.select(SEL["section"])
    ids = {id(e) for e in els}
    top = [e for e in els if not _has_ancestor_in(e, ids)]
    sections = []
    for idx, el in enumerate(top):
        num = el.get("data-number") or el.get("data-sectionnum")
        if not (num and str(num).isdigit()):
            m = re.search(r"section-(\d+)", el.get("id", ""))
            num = m.group(1) if m else idx
        num = int(num)
        nm = select_first(el, SEL["section_name"])
        name = clean_text(nm.get_text(" ")) if nm else clean_text(el.get("aria-label"))
        if not name:
            name = "Общее" if num == 0 else f"Раздел {num}"
        sec = Section(num, name)
        for a in el.select("a[href]"):
            u = urljoin(page_url, a["href"])
            if is_section_link(u, cid):
                sec.page_url = u.split("#", 1)[0]
                break
        acts = el.select(SEL["activity"])
        act_ids = {id(a) for a in acts}
        # Ссылки на файлы в описании раздела (вне элементов)
        summary_items = []
        for a in el.select("a[href*='pluginfile.php']"):
            if _has_ancestor_in(a, act_ids):
                continue
            u = urljoin(page_url, a["href"])
            summary_items.append(_file_item(u, clean_text(a.get_text(" ")) or name_from_url(u), "Описание раздела"))
        sec.items.extend(summary_items)
        hint = None
        for a_el in acts:
            parsed = parse_activity(a_el, page_url, hint)
            if parsed is None:
                continue
            items, new_hint, set_hint = parsed
            if set_hint:
                hint = new_hint
            sec.items.extend(items)
        sections.append(sec)
    return sections


def _file_item(url: str, text: str, context: str) -> Item:
    return Item(key="lnk-" + hashlib.sha1(file_key(url).encode()).hexdigest()[:12], modtype="file",
                name=text, url=url, context=context)


def parse_activity(a_el, page_url, hint):
    """→ (список элементов, новая_подсказка_типа, менять_ли_подсказку) или None."""
    classes = a_el.get("class") or []
    modtype = next((c[len("modtype_"):] for c in classes if c.startswith("modtype_")), "")
    cmid = a_el.get("data-id")
    if not cmid:
        m = re.search(r"module-(\d+)", a_el.get("id", ""))
        cmid = m.group(1) if m else None

    if modtype == "label":
        content = select_first(a_el, SEL["label_content"]) or a_el
        heading = label_heading(content)
        items = []
        for a in content.select("a[href*='pluginfile.php']"):
            u = urljoin(page_url, a["href"])
            it = _file_item(u, clean_text(a.get_text(" ")) or name_from_url(u), heading)
            it.hint = classify(heading) or hint
            items.append(it)
        text = clean_text(content.get_text(" "))
        if len(text) >= LABEL_MIN_TEXT:
            items.insert(0, Item(key=f"cm-{cmid}" if cmid else "txt-" + hashlib.sha1(text.encode()).hexdigest()[:12],
                                 modtype="text", name=heading or short(text, 60), cmid=cmid,
                                 label_html=str(content), hint=classify(heading) or hint))
        # Заголовок-пояснение задаёт тип для следующих элементов («Лабораторные работы» → ЛЗ)
        return items, classify(heading), bool(heading)

    link = select_first(a_el, SEL["activity_link"])
    href = urljoin(page_url, link["href"]) if link is not None and link.get("href") else None
    if not modtype and href:
        m = re.search(r"/mod/([a-z0-9_]+)/", href)
        modtype = m.group(1) if m else ""
    if not cmid and href:
        cmid = query_param(href, "id")
    if not modtype or modtype in IGNORED_MODS:
        return None
    holder = a_el.select_one("[data-activityname]")
    if holder is not None and holder.get("data-activityname"):
        name = clean_text(holder["data-activityname"])
    else:
        nm = select_first(a_el, SEL["activity_name"])
        name = text_without_hidden(nm) if nm is not None else (text_without_hidden(link) if link is not None else "")
    if not name:
        name = f"{MOD_TITLES.get(modtype, modtype)} {cmid or ''}".strip()
    desc_el = select_first(a_el, SEL["activity_desc"])
    it = Item(key=f"cm-{cmid}" if cmid else "x-" + hashlib.sha1((name + (href or "")).encode()).hexdigest()[:12],
              modtype=modtype, name=name, url=href, cmid=cmid, hint=hint,
              description=short(clean_text(desc_el.get_text(" ")), 400) if desc_el is not None else "")
    if modtype == "folder":  # папка, показанная прямо на странице курса
        for a in a_el.select(SEL["folder_files"]):
            it.inline_links.append((urljoin(page_url, a["href"]), clean_text(a.get_text(" "))))
    if modtype not in HANDLERS or (not href and not it.inline_links):
        it.online_only = True
    return [it], hint, False


def generic_scan(soup, page_url) -> Section:
    """Запасной вариант для нестандартных тем: собираем все ссылки на элементы /mod/…/view.php."""
    main = select_main(soup)
    sec = Section(0, "Материалы курса")
    seen = set()
    for a in main.select("a[href*='/mod/']"):
        u = urljoin(page_url, a["href"])
        m = re.search(r"/mod/([a-z0-9_]+)/view\.php", u)
        cmid = query_param(u, "id")
        if not m or not cmid or cmid in seen:
            continue
        seen.add(cmid)
        mod = m.group(1)
        if mod in IGNORED_MODS:
            continue
        it = Item(key=f"cm-{cmid}", modtype=mod, name=text_without_hidden(a) or f"{mod} {cmid}", url=u, cmid=cmid)
        it.online_only = mod not in HANDLERS
        sec.items.append(it)
    return sec


HINT_MODS = {"resource", "folder", "url", "page", "file", "text", "assign", "book", "imscp", "scorm", "lesson"}


def classify_course(course: Course):
    """Тип занятия: по названию → по заголовку блока → по ближайшему заголовку-пояснению (только для
    материалов, не для тестов/форумов) → по названию раздела → «Прочее»."""
    for sec in course.sections:
        counters = {k: 0 for k in KINDS}
        sec_kind = classify(sec.name)
        for it in sec.items:
            hint = it.hint if it.modtype in HINT_MODS else None
            it.kind = classify(it.name) or classify(it.context) or hint or sec_kind or "Прочее"
            counters[it.kind] += 1
            it.seq = counters[it.kind]


def parse_manifest(data: bytes):
    """imsmanifest.xml → (список относительных путей файлов, оглавление [(уровень, название, href)])."""
    root = ET.fromstring(data)
    xml_base = "{http://www.w3.org/XML/1998/namespace}base"

    def local(tag):
        return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

    resources, files = {}, []
    for res_list in root.iter():
        if local(res_list.tag) != "resources":
            continue
        lbase = res_list.get(xml_base) or ""
        for res in res_list:
            if local(res.tag) != "resource":
                continue
            rbase = posixpath.join(lbase, res.get(xml_base) or "")
            href = res.get("href")
            if href:
                full = posixpath.join(rbase, href)
                files.append(full)
                resources[res.get("identifier")] = full
            for f in res:
                if local(f.tag) == "file" and f.get("href"):
                    files.append(posixpath.join(rbase, f.get("href")))
    toc = []

    def walk(node, depth):
        for ch in node:
            if local(ch.tag) == "item":
                title = next((clean_text(t.text) for t in ch if local(t.tag) == "title"), "")
                href = resources.get(ch.get("identifierref"))
                if href and ch.get("parameters"):
                    href += ch.get("parameters")
                toc.append((depth, title or "Без названия", href))
                walk(ch, depth + 1)

    for org in root.iter():
        if local(org.tag) == "organization":
            walk(org, 0)
    clean = []
    for f in files:
        f = posixpath.normpath(f.split("?", 1)[0].split("#", 1)[0]).lstrip("/")
        if f and not f.startswith("..") and f not in clean:
            clean.append(f)
    return clean, toc


_REF_RX = re.compile(r"""(?:src|href|data|poster|background)\s*=\s*["']([^"'<>]+)["']""", re.I)
_CSS_RX = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)|@import\s+["']([^"']+)["']""", re.I)


def extract_refs(text: str, ext: str) -> list[str]:
    refs = []
    if ext in (".html", ".htm", ".xhtml", ".xml"):
        refs += _REF_RX.findall(text)
    for a, b in _CSS_RX.findall(text):
        refs.append(a or b)
    out = []
    for r in refs:
        r = htmllib.unescape(r.strip())
        if not r or r.startswith(("#", "javascript:", "mailto:", "data:", "about:", "tel:")):
            continue
        out.append(r)
    return out


# ════════════════════════════════════════════════════════════════════════════
#  РАСКЛАДКА ФАЙЛОВ (режимы 1 и 2)
# ════════════════════════════════════════════════════════════════════════════
class Layout:
    """Режим 1: «Курс/03_ЛЗ_02 Название.pdf» + оглавление. Режим 2: «Курс/03. Раздел/ЛЗ/Название.pdf».
    Режим 3: то же, но папки и имена файлов собираются по шаблонам пользователя (см. dl_rules.PLACEHOLDERS)."""

    def __init__(self, out: Path, course: Course, mode: int, original_names=False, dir_tpl="", file_tpl=""):
        self.out = out
        self.mode = mode
        self.original_names = original_names
        dir_tpl, file_tpl = (dir_tpl or "").strip(), (file_tpl or "").strip()
        # папка может быть пустой (всё в одну папку), но только если задано имя файла
        self.dir_tpl = dir_tpl if (dir_tpl or file_tpl) else dl_rules.DEFAULT_DIR_TPL
        self.file_tpl = file_tpl or dl_rules.DEFAULT_FILE_TPL
        self.course_rel = PurePosixPath(safe_name(course.fullname or f"Курс {course.id}", 70))
        self.claimed: set[str] = set()
        self._item_dirs: dict[str, PurePosixPath] = {}

    @property
    def course_dir(self) -> Path:
        return self.out / Path(*self.course_rel.parts)

    def abs(self, rel: PurePosixPath) -> Path:
        return self.course_dir / Path(*PurePosixPath(rel).parts)

    def claim(self, rel):
        self.claimed.add(str(rel).lower())

    def _unique(self, rel) -> PurePosixPath:
        rel = PurePosixPath(rel)
        stem, ext = split_ext(rel.name)
        cand, n = rel, 2
        while str(cand).lower() in self.claimed:
            cand = rel.with_name(f"{stem} ({n}){ext}")
            n += 1
        self.claim(cand)
        return cand

    @property
    def tree(self) -> bool:
        """Файлы лежат по папкам (режимы 2 и 3), а не в одной куче с префиксами."""
        return self.mode in (2, 3)

    def section_dir(self, sec) -> PurePosixPath:
        return PurePosixPath(safe_name(f"{sec.number:02d}. {sec.name}", 60)) if self.mode == 2 else PurePosixPath()

    def _values(self, sec, it, item="") -> dict:
        return {"section_no": f"{sec.number:02d}", "section": sec.name, "kind": it.kind,
                "kind_code": KIND_CODE[it.kind], "seq": f"{it.seq:02d}", "item": item or it.name,
                "type": MOD_TITLES.get(it.modtype, it.modtype)}

    def base_dir(self, sec, it) -> PurePosixPath:
        if self.mode == 2:
            return self.section_dir(sec) / it.kind
        if self.mode == 3:
            return PurePosixPath(*dl_rules.dir_parts(self.dir_tpl, self._values(sec, it), lambda x: safe_name(x, 60)))
        return PurePosixPath()

    def prefix(self, sec, it) -> str:
        return f"{sec.number:02d}_{KIND_CODE[it.kind]}_{it.seq:02d}"

    def _decorate(self, sec, it, name, with_ext=False) -> str:
        if self.mode == 3:
            stem, ext = split_ext(name) if with_ext else (name, "")
            vals = {k: safe_name(v, 80) for k, v in self._values(sec, it, stem).items()}
            vals["item"] = stem   # название чистится позже, вместе со всем именем
            return (dl_rules.render_template(self.file_tpl, vals).strip() or stem) + ext
        return f"{self.prefix(sec, it)} {name}" if self.mode == 1 else name

    def single_file(self, sec, it, server_name, use_item_name=True) -> PurePosixPath:
        _, ext = split_ext(server_name)
        if use_item_name and not self.original_names and it.name:
            base = it.name if (ext and it.name.lower().endswith(ext.lower())) else it.name + ext
        else:
            base = server_name
        return self._unique(self.base_dir(sec, it) / safe_filename(self._decorate(sec, it, base, with_ext=True)))

    def item_dir(self, sec, it) -> PurePosixPath:
        if it.key not in self._item_dirs:
            self._item_dirs[it.key] = self._unique(self.base_dir(sec, it) / safe_name(self._decorate(sec, it, it.name), 80))
        return self._item_dirs[it.key]

    def item_dirs(self, it) -> list:
        d = self._item_dirs.get(it.key)
        return [d.as_posix()] if d else []

    def folder_file(self, sec, it, subdirs, filename) -> PurePosixPath:
        if self.tree:
            rel = self.item_dir(sec, it).joinpath(*[safe_name(x, 60) for x in subdirs]) / safe_filename(filename)
        else:
            rel = self.base_dir(sec, it) / safe_filename(self._decorate(sec, it, " - ".join([*subdirs, filename])))
        return self._unique(rel)

    def html_file(self, sec, it, suffix="") -> PurePosixPath:
        return self._unique(self.base_dir(sec, it) / (safe_name(self._decorate(sec, it, it.name + suffix), 100) + ".html"))

    def url_file(self, sec, it) -> PurePosixPath:
        return self._unique(self.base_dir(sec, it) / (safe_name(self._decorate(sec, it, it.name), 100) + ".url"))

    def asset_file(self, html_rel: PurePosixPath, filename) -> PurePosixPath:
        return self._unique(html_rel.parent / (html_rel.stem + "_files") / safe_filename(filename))


# ════════════════════════════════════════════════════════════════════════════
#  СОСТОЯНИЕ (что скачано) — для докачки и быстрой синхронизации
# ════════════════════════════════════════════════════════════════════════════
class State:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"version": 1, "files": {}, "items": {}, "courses": {}, "news": {}}
        if fexists(path):
            try:
                with fopen(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (OSError, ValueError):
                try:
                    freplace(path, path.with_name(path.name + ".bad"))
                except OSError:
                    pass
        for k in ("files", "items", "courses", "news"):
            self.data.setdefault(k, {})
        self._owner = {}
        for k, e in self.data["files"].items():
            for p in e.get("paths", []):
                self._owner[p.lower()] = k
        self._last_save = 0.0
        self._dirty = False

    def find(self, key, out: Path, prefer: str):
        e = self.data["files"].get(key)
        if not e:
            return None
        for p in sorted(e.get("paths", []), key=lambda x: x != prefer):
            ap = out / Path(*PurePosixPath(p).parts)
            try:
                if fexists(ap) and fsize(ap) == e.get("size"):
                    return p
            except OSError:
                continue
        return None

    def owner_of(self, rel_out: str):
        return self._owner.get(rel_out.lower())

    def add_file(self, key, rel_out: str, size: int):
        with self.lock:
            old = self._owner.get(rel_out.lower())
            if old and old != key and old in self.data["files"]:
                self.data["files"][old]["paths"] = [p for p in self.data["files"][old]["paths"] if p.lower() != rel_out.lower()]
            e = self.data["files"].setdefault(key, {"size": size, "paths": []})
            if e.get("size") != size:
                e["size"] = size
                e["paths"] = []
            if rel_out not in e["paths"]:
                e["paths"].append(rel_out)
            self._owner[rel_out.lower()] = key
            self.save()

    def get_item(self, k):
        return self.data["items"].get(k)

    def set_item(self, k, v):
        with self.lock:
            self.data["items"][k] = v
            self.save()

    def set_course(self, cid, v):
        with self.lock:
            self.data["courses"][str(cid)] = v
            self.save(force=True)

    def get_news(self, cid):
        """Что программа запомнила о курсе для «Что нового»; None — курс ещё не запоминался."""
        return self.data["news"].get(str(cid))

    def set_news(self, cid, v):
        with self.lock:
            self.data["news"][str(cid)] = v
            self.save()

    def save(self, force=False):
        with self.lock:
            now = time.monotonic()
            if not force and now - self._last_save < 1.5:
                self._dirty = True
                return
            try:
                write_text_atomic(self.path, json.dumps(self.data, ensure_ascii=False))
                self._last_save = now
                self._dirty = False
            except OSError:
                pass


# ════════════════════════════════════════════════════════════════════════════
#  ОБРАБОТКА КУРСА
# ════════════════════════════════════════════════════════════════════════════
PAGE_CSS = """
:root{color-scheme:light dark;--fg:#1b1f24;--muted:#5f6b7a;--line:#d8dee6;--bg:#fff;--accent:#2f6fde}
@media (prefers-color-scheme:dark){:root{--fg:#e6e9ee;--muted:#9aa5b4;--line:#334;--bg:#15181d;--accent:#6ea0ff}}
body{font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:var(--fg);background:var(--bg);
max-width:960px;margin:2rem auto;padding:0 1.2rem}
a{color:var(--accent)} img,video,iframe,object{max-width:100%;height:auto}
table{border-collapse:collapse;max-width:100%;overflow:auto;display:block} td,th{border:1px solid var(--line);padding:.35em .6em;vertical-align:top}
pre,code{white-space:pre-wrap} h1,h2,h3{line-height:1.25}
.mo-meta{font-size:.85em;color:var(--muted);border-bottom:1px solid var(--line);padding-bottom:.7em;margin-bottom:1.4em}
.que{border:1px solid var(--line);border-radius:10px;padding:1em 1.2em;margin:1.2em 0}
.que .info{font-size:.9em;color:var(--muted)} .que .correct{background:#2e7d3222} .que .incorrect{background:#c6282822}
.que .rightanswer,.que .outcome{margin-top:.6em;padding:.6em .8em;border-radius:8px;background:#8881}
.mo-chapter{border-top:2px solid var(--line);margin-top:2.5em;padding-top:1em}
@media print{body{margin:0;max-width:none}.mo-meta a{color:inherit}}
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{title}}</title><style>{{css}}</style></head><body>
<div class="mo-meta">{{course}} · {{section}}<br>Сохранено {{date}} · <a href="{{src}}">открыть на сайте</a></div>
<h1>{{title}}</h1>
{{body}}
</body></html>"""


def render(tpl: str, **kw) -> str:
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


class Processor:
    def __init__(self, client: MoodleClient, state: State, out: Path, opts: JobOptions, rep: Reporter, control: Control):
        self.client = client
        self.state = state
        self.out = out
        self.opts = opts
        self.rep = rep
        self.control = control
        self.failures = []
        self.new_files = []
        self.stats = {"new": 0, "skipped": 0, "copied": 0, "failed": 0, "bytes": 0, "cached_items": 0, "items": 0,
                      "rule_skipped": 0}
        self.rule_skips = []
        self.events = []        # события «Что нового» за запуск
        self.cr = dl_rules.CourseRules()
        self.sig = ""
        self.pdf_on = bool(opts.pdf)
        self._cur = None
        rules = opts.rules if isinstance(opts.rules, dict) else {}
        want_pdf = bool(opts.pdf) or any((r or {}).get("pdf") for r in (rules.get("courses") or {}).values())
        self.browser = find_browser() if want_pdf else None
        self.pdf_fail = 0
        if want_pdf and not self.browser:
            rep.log("PDF: не найден Microsoft Edge / Google Chrome — страницы сохраню только в HTML.", "warn")
        self.course = None
        self.layout = None

    # ── общая логика ────────────────────────────────────────────────────────
    def _ikey(self, it) -> str:
        return f"{self.opts.mode}:{self.course.id}:{it.key}"

    def _setup_rules(self, course: Course):
        self.cr = dl_rules.CourseRules(dl_rules.effective(self.opts.rules, course.id))
        self.pdf_on = bool(self.cr.pdf if self.cr.pdf is not None else self.opts.pdf)
        sig = self.cr.sig
        if self.opts.mode == 3:   # сменили шаблон имён — быстрая синхронизация должна пересобрать раскладку
            sig += "|" + hashlib.sha1(f"{self.opts.dir_template}\n{self.opts.file_template}".encode()).hexdigest()[:8]
        self.sig = sig
        if self.cr.active:
            e = self.cr.eff
            bits = []
            if self.cr.ext_mode != "all":
                bits.append(("только " if self.cr.ext_mode == "only" else "без ") + ", ".join(e["ext_list"]))
            if self.cr.max_bytes:
                bits.append(f"файлы до {e['max_mb']} МБ")
            if e["skip_mods"]:
                bits.append("без типов: " + ", ".join(e["skip_mods"]))
            if e["skip_sections"]:
                bits.append("без разделов: " + ", ".join(e["skip_sections"]))
            if e["skip_names"]:
                bits.append("без названий: " + ", ".join(e["skip_names"]))
            self.rep.log("Правила скачивания: " + "; ".join(bits))
        if self.pdf_on and not self.browser:
            self.rep.log("PDF для этого курса включён, но не найден Edge / Chrome — сохраню только HTML.", "warn")

    def _rule_note(self, it, what, why):
        """Запоминает, что файл или элемент пропущен по правилам (не ошибка)."""
        s = self._cur[0] if self._cur else None
        self.stats["rule_skipped"] += 1
        self.rule_skips.append({"course": self.course.fullname, "section": f"{s.number:02d}. {s.name}" if s else "—",
                                "item": it.name, "what": what, "reason": why})
        self.rep.log(f"  ⊘ пропущено правилами: {short(what, 80)} — {why}", "info")

    def _item_rule_reason(self, s, it) -> str | None:
        if not self.cr.active:
            return None
        return self.cr.section_skip(s.name) or self.cr.item_skip(it.modtype, it.name)

    def run_course(self, course: Course, layout: Layout):
        self.course, self.layout = course, layout
        self._setup_rules(course)
        items = [(s, it) for s in course.sections for it in s.items]
        if self.opts.sync:
            for s, it in items:
                rec = self.state.get_item(self._ikey(it))
                if (rec and rec.get("complete") and it.modtype not in RECHECK_IN_SYNC
                        and rec.get("rules", "") == self.sig):
                    it.cached = True
                    it.files = rec.get("files", [])
                    it.external = rec.get("external")
                    it.online_only = rec.get("online_only", it.online_only)
                    for f in it.files:
                        layout.claim(f["path"])
                    for d in rec.get("dirs", []):
                        layout.claim(d)
            cached = sum(1 for _, it in items if it.cached)
            if cached:
                self.rep.log(f"Быстрая синхронизация: {cached} из {len(items)} элементов уже обработаны ранее — пропускаю.")
        total = len(items)
        for n, (s, it) in enumerate(items, 1):
            if it.cached:
                self.stats["cached_items"] += 1
                continue
            self.control.checkpoint()
            self.stats["items"] += 1
            self._cur = (s, it)
            why = self._item_rule_reason(s, it)
            if why:
                it.files, it.error, it.online_only, it.rule_skip = [], None, True, why
                self._rule_note(it, it.name, why)
                self.state.set_item(self._ikey(it), {
                    "name": it.name, "files": [], "external": it.external, "online_only": True, "complete": True,
                    "dirs": [], "rules": self.sig, "ts": int(time.time())})
                continue
            self.rep.item(n, total, s, it)
            it.files, it.error = [], None
            try:
                self.handle(s, it)
            except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
                raise
            except SkipFile:
                self.fail(s, it, it.url, "пропущено вручную")
            except Exception as e:
                self.fail(s, it, it.url, describe_error(e))
            self.state.set_item(self._ikey(it), {
                "name": it.name, "files": it.files, "external": it.external, "online_only": it.online_only,
                "complete": it.error is None, "dirs": layout.item_dirs(it), "rules": self.sig, "ts": int(time.time())})

    # ── «Что нового» ────────────────────────────────────────────────────────
    def news_pass(self, course: Course, layout: Layout, cfg: dict) -> list:
        """Сравнивает курс с тем, что запомнили в прошлый раз, и возвращает события.
        Первый раз для курса (и для каждого вида данных отдельно) — только запоминает, без событий.
        Запомненное обновляем всегда, даже если вид события выключен: включите позже — лавины «нового» не будет."""
        self._cur = None
        rec = dict(self.state.get_news(course.id) or {})
        track = cfg["track"]
        events = []
        now = []
        for s in course.sections:
            for it in s.items:
                now.append({"key": it.key, "name": it.name, "modtype": it.modtype, "url": it.url,
                            "section": short(s.name, 60), "note": ("не скачано: " + it.rule_skip) if it.rule_skip else "",
                            "files": [{"title": f.get("title", ""), "path": (layout.course_rel / f["path"]).as_posix()}
                                      for f in it.files if f.get("path")]})
        if now:   # пустой разбор страницы курса — это сбой, а не «преподаватель всё удалил»
            ev, seen, suspicious = news.diff_items(course.id, course.fullname, rec.get("seen"), now,
                                                   item_new_tracked=bool(track.get("item_new")))
            if suspicious:
                self.rep.log("  Что нового: с курса «исчезла» почти половина элементов — похоже на сбой разбора страницы, "
                             "сообщения об удалённом пропускаю.", "warn")
            events += ev
            rec["seen"] = seen
        if track.get("announcement"):
            try:
                events += self._news_announcements(course, layout, cfg, rec)
            except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
                raise
            except Exception as e:
                self.rep.log(f"  Объявления не проверены: {describe_error(e)}", "warn")
        if track.get("grade"):
            try:
                url = f"{self.client.base}/grade/report/user/index.php?id={course.id}"
                soup, r = self.client.page(url, referer=course.url)
                snap = news.parse_grades(soup)
                if snap is None:
                    self.rep.log("  Оценки: таблица не найдена (журнал закрыт или другая тема сайта) — пропускаю.", "debug")
                else:
                    events += news.diff_grades(course.id, course.fullname, rec.get("grades"), snap, r.url)
                    rec["grades"] = snap
            except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
                raise
            except Exception as e:
                self.rep.log(f"  Оценки не проверены: {describe_error(e)}", "debug")
        rec["ts"] = int(time.time())
        self.state.set_news(course.id, rec)
        events = [e for e in events if track.get(e["kind"])]
        if events:
            self.rep.log(f"  Что нового: {len(events)} " + news.plural(len(events), "событие", "события", "событий"))
        return events

    def _news_announcements(self, course: Course, layout: Layout, cfg: dict, rec: dict) -> list:
        forums = [it for s in course.sections for it in s.items
                  if it.modtype == "forum" and it.url and news.is_news_forum(it.name, cfg["forum_words"])]
        if not forums:
            return []
        disc = rec.setdefault("disc", {})
        events = []
        for f in forums:
            self.control.checkpoint()
            soup, r = self.client.page(f.url, referer=course.url)
            found = news.parse_discussions(soup, r.url)
            if not found:
                continue
            known = disc.get(f.key)
            if known is None:   # первый раз: запоминаем все, а несколько последних сохраняем молча — чтобы были под рукой офлайн
                todo, silent = found[: cfg["backfill"]], True
            else:
                todo, silent = [d for d in found if d["id"] not in set(known)], False
            for n, d in enumerate(todo):
                post, rel = None, ""
                if n < 10:   # не больше десяти страниц за раз; остальные — просто строкой в «Что нового»
                    try:
                        soup2, r2 = self.client.page(d["url"], referer=r.url)
                        post = news.parse_post(soup2)
                        rel = self._write_announcement(course, layout, d, post, r2.url)
                        self.rep.log(f"  ✓ объявление: {short(d['title'], 70)}")
                    except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
                        raise
                    except Exception as e:
                        self.rep.log(f"  объявление «{short(d['title'], 50)}» не сохранено: {describe_error(e)}", "warn")
                if not silent:
                    events.append(news.make_event("announcement", course.id, course.fullname,
                                                  (post or {}).get("title") or d["title"] or f"Объявление {d['id']}",
                                                  detail=short((post or {}).get("text", ""), 220), url=d["url"], path=rel,
                                                  section=f.name, key=f"{f.key}|{d['id']}"))
            disc[f.key] = sorted({*(known or []), *(d["id"] for d in found)})[-300:]
        return events

    def _write_announcement(self, course: Course, layout: Layout, d: dict, post: dict, page_url: str) -> str:
        """Сохраняет текст объявления в «Курс/Объявления/ГГГГ-ММ-ДД Тема.html». Вложения и картинки с сайта Moodle
        скачиваются рядом (по тем же правилам скачивания), остальные ссылки остаются ссылками."""
        frag = copy.copy(post["html"])
        for el in frag.select(SEL["strip_from_pages"]):
            el.decompose()
        for form in frag.find_all("form"):
            form.unwrap()
        title = post.get("title") or d.get("title") or f"Объявление {d['id']}"
        when = post.get("when") or ""
        stamp = when[:10] if re.match(r"\d{4}-\d\d-\d\d", when) else datetime.now().strftime("%Y-%m-%d")
        base = layout.course_dir / ANNOUNCE_DIR
        name = safe_name(f"{stamp} {title}", 100)
        if fexists(base / (name + ".html")):   # одинаковые темы в один день не затирают друг друга
            name = safe_name(f"{stamp} {title}", 90) + f" ({d['id']})"
        rel_html = PurePosixPath(ANNOUNCE_DIR) / (name + ".html")
        pseudo = Item(key=f"ann-{d['id']}", modtype="forum", name=title, url=d.get("url"))
        for tag, attr in (("img", "src"), ("a", "href")):
            for el in frag.find_all(tag):
                v = el.get(attr)
                if not v or v.startswith(("#", "javascript:", "mailto:", "data:")):
                    continue
                u = urljoin(page_url, v)
                pu = urlparse(u)
                if pu.netloc.lower() == self.client.host and "pluginfile.php" in pu.path:
                    fname = name_from_url(u)
                    is_img = tag == "img" or split_ext(fname)[1].lower() in IMG_EXT
                    rel = PurePosixPath(ANNOUNCE_DIR) / f"{name}_files" / safe_filename(fname)
                    try:
                        rec = self.fetch(u, rel, pseudo, fname, listed=False, quiet=is_img, referer=page_url,
                                         apply_rules=not is_img)
                    except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
                        raise
                    except Exception as e:
                        self.rep.log(f"  вложение объявления «{short(fname, 50)}» не скачано: {describe_error(e)}", "warn")
                        rec = None
                    if rec:
                        el[attr] = quote(posixpath.relpath(rec["path"], ANNOUNCE_DIR))
                        continue
                el[attr] = strip_sesskey(u)
        html_text = render(PAGE_TEMPLATE, title=htmllib.escape(title), css=PAGE_CSS, course=htmllib.escape(course.fullname),
                           section=ANNOUNCE_DIR, date=datetime.now().strftime("%d.%m.%Y %H:%M"),
                           src=htmllib.escape(page_url), body=str(frag))
        path = layout.course_dir / Path(*rel_html.parts)
        write_text_atomic(path, html_text)
        rel_out = (layout.course_rel / rel_html).as_posix()
        self.rep.saved(rel_out, title, fsize(path), "page")
        return rel_out

    def handle(self, s, it):
        if it.online_only:
            self.rep.log(f"  · только онлайн ({MOD_TITLES.get(it.modtype, it.modtype)}) — ссылка будет в оглавлении", "debug")
            return
        getattr(self, "h_" + HANDLERS[it.modtype])(s, it)

    def fail(self, s, it, url, err):
        self.stats["failed"] += 1
        if it.error is None:
            it.error = err
        self.failures.append({"course": self.course.fullname, "section": f"{s.number:02d}. {s.name}",
                              "item": it.name, "kind": it.kind, "url": url or "", "error": err})
        self.rep.log(f"  ✗ {err}" + (f" — {url}" if url else ""), "error")
        self.rep.failed(self.failures[-1])

    def fetch(self, url, rel: PurePosixPath, it, title, *, listed=True, quiet=False, referer=None, apply_rules=None) -> dict | None:
        """Скачивает файл или берёт уже скачанный. Возвращает запись {path,title,size}.
        None — файл пропущен по правилам (apply_rules: проверять ли правила; по умолчанию — для видимых в списке файлов)."""
        check = listed if apply_rules is None else apply_rules
        key = file_key(url)
        dest = self.layout.abs(rel)
        rel_out = (self.layout.course_rel / rel).as_posix()
        status = None
        have = self.state.find(key, self.out, rel_out)
        if have == rel_out:
            status = "skipped"
        elif have:
            fmkdir(dest.parent)
            tmp = dest.with_name(dest.name + ".tmp")
            shutil.copyfile(fsp(self.out / Path(*PurePosixPath(have).parts)), fsp(tmp))
            freplace(tmp, dest)
            status = "copied"
        elif fexists(dest) and self.state.owner_of(rel_out) in (None, key):
            status = "skipped"   # файл уже на месте (например, после сбоя до записи состояния)
        if status:
            size = fsize(dest)
            self.state.add_file(key, rel_out, size)
            self.stats[status] += 1
            if not quiet:
                self.rep.log(f"  = уже есть: {short(rel.name, 80)}", "debug")
        else:
            if check and self.cr.active:
                why = self.cr.file_skip(rel.name)
                if why:
                    it.rule_notes.append(f"{title or rel.name}: {why}")
                    self._rule_note(it, title or rel.name, why)
                    return None
            try:
                info = self.client.download(url, dest, title=title or rel.name, quiet=quiet,
                                            referer=referer or (it.url if it.url else self.course.url),
                                            max_bytes=self.cr.max_bytes if check else 0)
            except RuleSkip as e:
                it.rule_notes.append(f"{title or rel.name}: {e}")
                self._rule_note(it, title or rel.name, str(e))
                return None
            size = info["size"]
            self.state.add_file(key, rel_out, size)
            self.stats["new"] += 1
            self.stats["bytes"] += size
            self.new_files.append({"course": self.course.fullname, "path": rel_out, "size": size, "item": it.name})
            if not quiet:
                self.rep.log(f"  ✓ {short(rel.name, 80)} ({hsize(size)})")
        rec = {"path": rel.as_posix(), "title": title or rel.name, "size": size}
        if not quiet:
            self.rep.saved(rel_out, title or rel.name, size, "new" if not status else status)
        if listed:
            it.files.append(rec)
        return rec

    def _fetch_soft(self, s, it, url, rel, title, **kw):
        """Как fetch, но ошибка одного файла не прерывает весь элемент."""
        try:
            return self.fetch(url, rel, it, title, **kw)
        except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
            raise
        except SkipFile:
            self.fail(s, it, url, f"пропущено вручную: {title}")
        except Exception as e:
            self.fail(s, it, url, f"{title}: {describe_error(e)}")
        return None

    # ── обработчики типов элементов ─────────────────────────────────────────
    def h_resource(self, s, it):
        file_url, cd_name = self.client.resolve_redirect(add_query(it.url, redirect=1), referer=self.course.url)
        if file_url is None:   # Moodle показал страницу (режим «встроить»/«в новом окне») — ищем файл на ней
            soup, r = self.client.page(it.url, referer=self.course.url)
            file_url = None
            for el in select_main(soup).select(SEL["resource_file"]):
                cand = el.get("href") or el.get("src") or el.get("data")
                if cand and "pluginfile.php" in cand:
                    file_url = urljoin(r.url, cand)
                    break
        if not file_url:
            raise MoodleError("не найден файл ресурса на странице")
        if urlparse(file_url).netloc.lower() != self.client.host:
            it.external = file_url
            return self._write_url_file(s, it, file_url)
        server_name = cd_name or name_from_url(file_url)
        rel = self.layout.single_file(s, it, server_name)
        self.fetch(file_url, rel, it, title=server_name)

    def h_file(self, s, it):
        fname = name_from_url(it.url)
        rel = self.layout.single_file(s, it, fname, use_item_name=False)
        self.fetch(it.url, rel, it, title=fname)

    def h_folder(self, s, it):
        links = list(it.inline_links)
        if not links:
            soup, r = self.client.page(it.url, referer=self.course.url)
            main = select_main(soup)
            els = main.select(SEL["folder_files"]) or main.select("a[href*='pluginfile.php']")
            links = [(urljoin(r.url, a["href"]), clean_text(a.get_text(" "))) for a in els]
        seen = set()
        for url, _text in links:
            k = file_key(url)
            if k in seen:
                continue
            seen.add(k)
            parts = pluginfile_parts(url) or {"subdirs": [], "filename": name_from_url(url)}
            rel = self.layout.folder_file(s, it, parts["subdirs"], parts["filename"])
            self._fetch_soft(s, it, url, rel, "/".join([*parts["subdirs"], parts["filename"]]))
        if not seen:
            self.rep.log("  (папка пуста)", "debug")

    def h_url(self, s, it):
        target, _ = self.client.resolve_redirect(add_query(it.url, redirect=1), referer=self.course.url)
        if not target or "/mod/url/view.php" in target:
            soup, r = self.client.page(it.url, referer=self.course.url)
            a = select_first(soup, SEL["url_link"])
            target = urljoin(r.url, a["href"]) if a is not None else None
        if not target:
            raise MoodleError("не удалось определить адрес ссылки")
        it.external = target
        if urlparse(target).netloc.lower() == self.client.host and "pluginfile.php" in target:
            fname = name_from_url(target)
            return self.fetch(target, self.layout.single_file(s, it, fname), it, title=fname)
        self._write_url_file(s, it, target)

    def _write_url_file(self, s, it, target):
        rel = self.layout.url_file(s, it)
        write_text_atomic(self.layout.abs(rel), f"[InternetShortcut]\r\nURL={target}\r\n")
        it.files.append({"path": rel.as_posix(), "title": "ярлык ссылки", "size": fsize(self.layout.abs(rel))})

    def h_html(self, s, it):
        soup, r = self.client.page(it.url, referer=self.course.url)
        self.save_html(s, it, select_main(soup), r.url)

    def h_text(self, s, it):
        frag = BeautifulSoup(it.label_html or "", PARSER)
        self.save_html(s, it, frag.body or frag, self.course.url)

    def h_book(self, s, it):
        frag, src = None, it.url
        try:
            soup, r = self.client.page(f"{self.client.base}/mod/book/tool/print/index.php?id={it.cmid}", referer=it.url)
            frag, src = (select_first(soup, SEL["main_region"]) or soup.body), r.url
        except (NetworkDown, Stopped, SessionExpired):
            raise
        except Exception as e:
            self.rep.log(f"  печатная версия книги недоступна ({describe_error(e)}), собираю по главам", "debug")
        if frag is None:
            soup, r = self.client.page(it.url, referer=self.course.url)
            box = BeautifulSoup("<div></div>", PARSER).div
            box.append(copy.copy(select_main(soup)))
            chapters = []
            for a in soup.select(SEL["book_toc"]):
                u = urljoin(r.url, a["href"])
                if u not in chapters and query_param(u, "chapterid") != query_param(r.url, "chapterid"):
                    chapters.append(u)
            for u in chapters:
                s2, _ = self.client.page(u, referer=r.url)
                wrap = BeautifulSoup('<div class="mo-chapter"></div>', PARSER).div
                wrap.append(copy.copy(select_main(s2)))
                box.append(wrap)
            frag = box
        self.save_html(s, it, frag, src)

    def h_quiz(self, s, it):
        soup, r = self.client.page(it.url, referer=self.course.url)
        self.save_html(s, it, select_main(soup), r.url)
        reviews = []
        for a in soup.select(SEL["quiz_review"]):
            u = urljoin(r.url, a["href"])
            if u not in reviews:
                reviews.append(u)
        for n, u in enumerate(reviews, 1):
            try:
                s2, r2 = self.client.page(add_query(u, showall=1), referer=r.url)
                self.save_html(s, it, select_main(s2), r2.url, suffix=f" — попытка {n}",
                               title=f"{it.name} — просмотр попытки {n}")
            except (NetworkDown, Stopped, SessionExpired):
                raise
            except Exception as e:
                self.fail(s, it, u, f"просмотр попытки {n}: {describe_error(e)}")

    def h_lesson(self, s, it):
        soup, r = self.client.page(it.url, referer=self.course.url)
        restart = soup.select_one("a[href*='startlastseen=no']")
        if restart is not None:
            soup, r = self.client.page(urljoin(r.url, restart["href"]), referer=r.url)
        box = BeautifulSoup("<div></div>", PARSER).div
        box.append(copy.copy(select_main(soup)))
        pages = []
        for a in soup.select(SEL["lesson_pages"]):
            u = urljoin(r.url, a["href"])
            if query_param(u, "id") == it.cmid and u not in pages and query_param(u, "pageid") != query_param(r.url, "pageid"):
                pages.append(u)
        for u in pages[:200]:
            s2, _ = self.client.page(u, referer=r.url)
            wrap = BeautifulSoup('<div class="mo-chapter"></div>', PARSER).div
            wrap.append(copy.copy(select_main(s2)))
            box.append(wrap)
        self.save_html(s, it, box, r.url)

    def h_package(self, s, it):
        """Пакеты IMS / SCORM: скачиваем все файлы с сохранением структуры + локальное оглавление."""
        soup, r = self.client.page(it.url, referer=self.course.url)
        comp = "mod_scorm" if it.modtype == "scorm" else "mod_imscp"
        base_prefix, toc_page = None, []
        for a in soup.select(SEL["imscp_toc"]):
            u = urljoin(r.url, a["href"])
            m = re.search(rf"^(.*?/pluginfile\.php/\d+/{comp}/content/\d+/)", u)
            if m:
                base_prefix = base_prefix or m.group(1)
                toc_page.append((0, clean_text(a.get_text(" ")) or clean_text(a.get("title")), u[len(m.group(1)):]))
        if not base_prefix:
            m = re.search(rf"(https?://[^\"'\s]+?/pluginfile\.php/\d+/{comp}/content/\d+/)", str(soup))
            if m:
                base_prefix = m.group(1)
        if not base_prefix:
            ctx = context_id(soup)
            if not ctx:
                raise MoodleError("не удалось определить адрес содержимого пакета")
            base_prefix = f"{self.client.base}/pluginfile.php/{ctx}/{comp}/content/1/"
        pkg = self.layout.item_dir(s, it)
        files, toc = [], []
        try:
            rec = self.fetch(base_prefix + "imsmanifest.xml", pkg / "imsmanifest.xml", it, "imsmanifest.xml",
                             listed=False, quiet=True, referer=r.url)
            with fopen(self.layout.abs(PurePosixPath(rec["path"])), "rb") as f:
                files, toc = parse_manifest(f.read())
        except (NetworkDown, Stopped, SessionExpired):
            raise
        except Exception as e:
            self.rep.log(f"  манифест пакета недоступен ({describe_error(e)}), собираю по ссылкам", "debug")
        toc = toc or toc_page
        start = [base_prefix + quote(f) for f in files]
        start += [base_prefix + h for _, _, h in toc if h]
        if not start:
            raise MoodleError("пакет пуст или закрыт для студентов")
        count = self._crawl_package(s, it, base_prefix, pkg, start, referer=r.url)
        # локальное оглавление пакета
        rows = []
        for depth, title, href in toc:
            pad = depth * 1.4
            if href:
                rows.append(f'<li style="margin-left:{pad}em"><a href="{htmllib.escape(quote(href.split("#")[0].split("?")[0]))}">'
                            f"{htmllib.escape(title)}</a></li>")
            else:
                rows.append(f'<li style="margin-left:{pad}em"><b>{htmllib.escape(title)}</b></li>')
        body = (f"<p>Пакет содержит {count} файлов. Откройте нужный раздел:</p><ul>{''.join(rows)}</ul>"
                if rows else f"<p>Пакет содержит {count} файлов.</p>")
        toc_rel = pkg / "_оглавление.html"
        html_text = render(PAGE_TEMPLATE, title=htmllib.escape(it.name), css=PAGE_CSS, course=htmllib.escape(self.course.fullname),
                           section=htmllib.escape(s.name), date=datetime.now().strftime("%d.%m.%Y %H:%M"),
                           src=htmllib.escape(it.url or ""), body=body)
        write_text_atomic(self.layout.abs(toc_rel), html_text)
        it.files.insert(0, {"path": toc_rel.as_posix(), "title": f"Оглавление пакета ({count} файлов)",
                            "size": fsize(self.layout.abs(toc_rel))})
        self.rep.log(f"  ✓ пакет: {count} файлов")

    def _crawl_package(self, s, it, base_prefix, pkg, start, referer):
        queue, seen, count = deque(start), set(), 0
        while queue and count < PACKAGE_FILE_LIMIT:
            u = queue.popleft().split("#", 1)[0].split("?", 1)[0]
            if u in seen or not u.startswith(base_prefix):
                continue
            seen.add(u)
            relpath = unquote(u[len(base_prefix):])
            parts = [_ILLEGAL.sub("_", p) for p in PurePosixPath(relpath).parts if p not in ("", ".", "..")]
            if not parts:
                continue
            rel = pkg.joinpath(*parts)
            rec = self._fetch_soft(s, it, u, rel, relpath, listed=False, quiet=True, referer=referer)
            if not rec:
                continue
            count += 1
            ext = PurePosixPath(parts[-1]).suffix.lower()
            if ext in (".html", ".htm", ".xhtml", ".css", ".xml") and rec["size"] < 8 * 1024 * 1024:
                try:
                    with fopen(self.layout.abs(PurePosixPath(rec["path"])), "rb") as f:
                        text = f.read().decode("utf-8", errors="ignore")
                except OSError:
                    continue
                for ref in extract_refs(text, ext):
                    absu = urljoin(u, ref)
                    if absu.startswith(base_prefix):
                        queue.append(absu)
        return count

    # ── сохранение страницы в HTML (+PDF) с картинками и вложениями ─────────
    def save_html(self, s, it, fragment, page_url, suffix="", title=None):
        rel_html = self.layout.html_file(s, it, suffix)
        frag = copy.copy(fragment)
        for el in frag.select(SEL["strip_from_pages"]):
            el.decompose()
        for form in frag.find_all("form"):
            form.unwrap()
        attachments = []
        for tag, attr in (("img", "src"), ("a", "href"), ("source", "src"), ("video", "src"), ("audio", "src"),
                          ("video", "poster"), ("object", "data"), ("iframe", "src"), ("embed", "src")):
            for el in frag.find_all(tag):
                v = el.get(attr)
                if not v or v.startswith(("#", "javascript:", "mailto:", "data:")):
                    continue
                u = urljoin(page_url, v)
                pu = urlparse(u)
                if el.has_attr("srcset"):
                    del el["srcset"]
                if pu.netloc.lower() == self.client.host and "pluginfile.php" in pu.path:
                    fname = name_from_url(u)
                    is_img = tag == "img" or split_ext(fname)[1].lower() in IMG_EXT
                    rel = self.layout.asset_file(rel_html, fname)
                    rec = self._fetch_soft(s, it, u, rel, fname, listed=False, quiet=is_img, referer=page_url,
                                           apply_rules=not is_img)
                    if rec:
                        el[attr] = quote(posixpath.relpath(rec["path"], rel_html.parent.as_posix()))
                        if not is_img:
                            attachments.append(rec)
                    else:
                        el[attr] = u
                else:
                    el[attr] = strip_sesskey(u)
        html_text = render(PAGE_TEMPLATE, title=htmllib.escape(title or it.name), css=PAGE_CSS,
                           course=htmllib.escape(self.course.fullname), section=htmllib.escape(s.name),
                           date=datetime.now().strftime("%d.%m.%Y %H:%M"), src=htmllib.escape(page_url), body=str(frag))
        abs_html = self.layout.abs(rel_html)
        changed = write_text_atomic(abs_html, html_text)
        self.rep.saved((self.layout.course_rel / rel_html).as_posix(), title or it.name, fsize(abs_html),
                       "page" if changed else "skipped")
        it.files.append({"path": rel_html.as_posix(), "title": "страница (HTML)" + (suffix and f" {suffix.strip(' —')}"),
                         "size": fsize(abs_html)})
        if self.browser and self.pdf_on:
            rel_pdf = rel_html.with_suffix(".pdf")
            abs_pdf = self.layout.abs(rel_pdf)
            if changed or not fexists(abs_pdf):
                self.control.checkpoint()
                self.rep.file_start(f"создаю PDF: {rel_pdf.name}", None, 0, True)
                ok_pdf = html_to_pdf(self.browser, abs_html, abs_pdf)
                self.rep.file_end("pdf")
                if ok_pdf:
                    self.pdf_fail = 0
                    self.rep.log(f"  ✓ PDF: {short(rel_pdf.name, 80)}", "debug")
                else:
                    self.pdf_fail += 1
                    self.rep.log("  PDF не получился (страница сохранена в HTML)", "warn")
                    if self.pdf_fail == 1 and PDF_LAST_ERROR:
                        self.rep.log(f"  причина: {PDF_LAST_ERROR}", "warn")
                    if self.pdf_fail >= 2:
                        self.browser = None
                        self.rep.log("  PDF отключён до конца запуска: браузер не печатает страницы.", "warn")
            if fexists(abs_pdf):
                self.layout.claim(rel_pdf)
                it.files.append({"path": rel_pdf.as_posix(), "title": "страница (PDF)", "size": fsize(abs_pdf)})
        it.files.extend(attachments)


# ════════════════════════════════════════════════════════════════════════════
#  PDF, ZIP, УВЕДОМЛЕНИЯ
# ════════════════════════════════════════════════════════════════════════════
def find_browser():
    cands = []
    if os.environ.get("MOODLE_BROWSER"):   # свой путь к Edge/Chrome, если стоит в нестандартном месте
        cands.append(Path(os.environ["MOODLE_BROWSER"]))
    if os.name == "nt":
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            b = os.environ.get(env)
            if b:
                cands += [Path(b) / "Microsoft/Edge/Application/msedge.exe",
                          Path(b) / "Google/Chrome/Application/chrome.exe"]
    for name in ("msedge", "microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            cands.append(Path(p))
    if sys.platform == "darwin":
        cands += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    for c in cands:
        if c.exists():
            return c
    return None


def _kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=0x08000000)
        else:
            os.killpg(proc.pid, 9)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def proc_exit_info(proc, errfile) -> str:
    """«код 21: сообщение браузера» — чтобы в журнале была причина, а не просто «не запустился»."""
    parts = []
    rc = proc.returncode if proc is not None else None
    if rc is not None:
        parts.append(f"код выхода {rc & 0xFFFFFFFF if rc < 0 else rc}")
    try:
        errfile.seek(0)
        raw = errfile.read()[-1500:].decode("utf-8", "replace")
        msg = " ".join(x.strip() for x in raw.splitlines() if x.strip())[-260:]
        if msg:
            parts.append(f"браузер пишет: {msg}")
    except (OSError, ValueError):
        pass
    return ", ".join(parts) or "без сообщения"


PDF_LAST_ERROR = ""     # почему фоновый браузер не напечатал PDF (код выхода и его сообщение) — для журнала


def html_to_pdf(browser: Path, html_path: Path, pdf_path: Path, timeout=45) -> bool:
    """HTML → PDF через установленный Edge/Chrome в фоновом режиме (без окон)."""
    global PDF_LAST_ERROR
    tmp_pdf = pdf_path.with_name(pdf_path.name + ".tmp")
    out_arg = os.path.abspath(str(tmp_pdf))
    problems = []
    # сначала полный набор ключей, потом «только самое нужное» — вдруг какой-то ключ запрещён политикой/антивирусом
    for headless, minimal in (("--headless=new", False), ("--headless", False), ("--headless=new", True)):
        with tempfile.TemporaryDirectory(prefix="mo-pdf-") as prof:
            if minimal:
                cmd = [str(browser), headless, "--no-first-run", f"--user-data-dir={prof}", "--no-pdf-header-footer",
                       f"--print-to-pdf={out_arg}", Path(os.path.abspath(html_path)).as_uri()]
            else:
                cmd = [str(browser), headless, "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                       "--disable-extensions", "--disable-background-networking", "--disable-component-update",
                       "--disable-sync", "--disable-default-apps", "--mute-audio", "--no-sandbox",
                       f"--user-data-dir={prof}", "--no-pdf-header-footer", "--print-to-pdf-no-header",
                       "--allow-file-access-from-files", "--virtual-time-budget=8000",
                       f"--print-to-pdf={out_arg}", Path(os.path.abspath(html_path)).as_uri()]
            try:
                kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
                with tempfile.TemporaryFile() as err:
                    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err, **kw)
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        _kill_tree(proc)
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            pass
                        funlink(tmp_pdf)
                        PDF_LAST_ERROR = f"браузер завис дольше {timeout} с"
                        return False   # браузер завис — другие варианты не пробуем, чтобы не терять время
                    if not (fexists(tmp_pdf) and fsize(tmp_pdf) > 0):
                        problems.append(f"{headless}{' (минимально)' if minimal else ''}: {proc_exit_info(proc, err)}")
            except OSError as e:
                problems.append(f"{headless}: не запустился ({describe_error(e)})")
        if fexists(tmp_pdf) and fsize(tmp_pdf) > 0:
            freplace(tmp_pdf, pdf_path)
            PDF_LAST_ERROR = ""
            return True
    funlink(tmp_pdf)
    PDF_LAST_ERROR = "; ".join(problems)
    return False


def _zip_add(z, full, arcname, compress) -> bool:
    """Добавляет файл в архив; если файл занят другой программой — ждёт до ~15 с. False — так и не освободился."""
    for attempt in range(8):
        try:
            z.write(full, arcname=arcname, compress_type=compress)
            return True
        except PermissionError:
            time.sleep(min(3.0, 0.5 * (attempt + 1)))
    return False


def zip_course(out: Path, layout: Layout, rep: Reporter) -> Path | None:
    src = layout.course_dir
    if not fexists(src):
        return None
    zdir = out / BACKUP_DIR
    fmkdir(zdir)
    zpath = zdir / safe_filename(f"{layout.course_rel.name}_бэкап_{datetime.now():%d.%m.%Y}.zip")
    tmp = zpath.with_name(zpath.name + ".tmp")
    rep.log(f"Архивирую курс в {zpath.name}…")
    count, skipped = 0, []
    try:
        with zipfile.ZipFile(fsp(tmp), "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for root, _dirs, files in os.walk(fsp(src)):
                for fn in sorted(files):
                    if fn.endswith(".tmp") or fn.startswith("~$"):   # недокачанное и временные файлы Word
                        continue
                    full = os.path.join(root, fn)
                    rel = os.path.relpath(full, fsp(src)).replace(os.sep, "/")
                    ext = os.path.splitext(fn)[1].lower()
                    if _zip_add(z, full, f"{layout.course_rel.name}/{rel}",
                                zipfile.ZIP_STORED if ext in STORED_EXT else zipfile.ZIP_DEFLATED):
                        count += 1
                    else:
                        skipped.append(rel)
        freplace(tmp, zpath)
    except BaseException:
        funlink(tmp)
        raise
    rep.log(f"  ✓ архив готов: {count} файлов, {hsize(fsize(zpath))}")
    if skipped:
        rep.log(f"  ! в архив не попали {len(skipped)} файл(ов) — они заняты другой программой "
                f"(открыты в Word или их синхронизирует OneDrive): " + "; ".join(skipped[:5])
                + (" …" if len(skipped) > 5 else "") + ". Закройте их — при следующем запуске архив пересоберётся.", "warn")
    return zpath


_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
$title = $env:MO_TITLE; $msg = $env:MO_MSG
try {
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
  $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $x = $t.GetElementsByTagName('text')
  $x.Item(0).AppendChild($t.CreateTextNode($title)) > $null
  $x.Item(1).AppendChild($t.CreateTextNode($msg)) > $null
  $n = [Windows.UI.Notifications.ToastNotification]::new($t)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
} catch {
  Add-Type -AssemblyName System.Windows.Forms, System.Drawing
  $ni = New-Object System.Windows.Forms.NotifyIcon
  $ni.Icon = [System.Drawing.SystemIcons]::Information; $ni.Visible = $true
  $ni.ShowBalloonTip(10000, $title, $msg, 'Info'); Start-Sleep -Seconds 10; $ni.Dispose()
}
"""


def notify(title: str, message: str, ok: bool = True) -> bool:
    """Звук + системное уведомление (Windows: всплывающее окно; Linux: notify-send; Android: termux-notification)."""
    try:
        if os.name == "nt":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK if ok else winsound.MB_ICONHAND)
            import base64
            env = dict(os.environ, MO_TITLE=title, MO_MSG=message)   # текст передаём через переменные окружения
            encoded = base64.b64encode(_TOAST_PS.encode("utf-16-le")).decode("ascii")
            subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                              "-EncodedCommand", encoded], env=env, creationflags=0x08000000,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif android.is_android():
            return android.notify(title, message)     # нужны приложение Termux:API и пакет termux-api; нет их — False
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
            if shutil.which("notify-send"):
                subprocess.Popen(["notify-send", "-a", APP_NAME, title, message],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return True


def search_summary(r: dict) -> str:
    """Одна строка итога индексации для журнала и консоли."""
    changed = r.get("new", 0) + r.get("changed", 0)
    parts = []
    if changed:
        parts.append(f"прочитано новых и изменённых файлов: {changed}")
    if r.get("removed"):
        parts.append(f"убрано пропавших: {r['removed']}")
    if r.get("empty"):
        parts.append(f"без текста (сканы и пустые): {r['empty']}")
    if r.get("err"):
        parts.append(f"не прочитано: {r['err']}")
    if r.get("legacy"):
        parts.append(f"старых .doc/.ppt/.xls: {r['legacy']}")
    if r.get("nolib"):
        parts.append(f"PDF без библиотеки pypdfium2: {r['nolib']}")
    head = "Поиск: индекс " + ("прерван" if r.get("stopped") else "обновлён") + f" за {r.get('secs', 0)} с, файлов в папке: {r.get('files', 0)}"
    return head + ("; " + "; ".join(parts) if parts else "; изменений нет")


def search_pass(out: Path, settings: dict, rep: Reporter, *, full=False, stop=None, progress=None) -> dict:
    """Обновляет офлайн-индекс поиска по материалам. Любая ошибка индекса — только строка в журнале: загрузке она не мешает."""
    cfg = search.normalize(settings.get("search"))
    try:
        r = search.Index(out).update(cfg, full=full, stop=stop, progress=progress)
    except KeyboardInterrupt:
        raise
    except Exception as e:  # noqa: BLE001
        rep.log(f"Поиск: индекс не обновлён — {describe_error(e)}", "warn")
        return {}
    rep.log(search_summary(r))
    return r


def send_news(events: list, cfg: dict, vault: "Vault", rep: Reporter, *, toast=True, channels=None,
              use_proxy=False) -> dict:
    """Отправляет события по каналам. channels — явный список («toast», «telegram», «webhook»), иначе — включённые в настройках.
    Значения оценок во внешние каналы уходят только если включено «показывать оценки» (share_grades)."""
    sec = vault.load_notify()
    token = sec.get("telegram_token") or os.environ.get("MOODLE_TELEGRAM_TOKEN", "")
    chat = cfg["telegram_chat"] or os.environ.get("MOODLE_TELEGRAM_CHAT", "")
    hook = sec.get("webhook_url") or os.environ.get("MOODLE_WEBHOOK_URL", "")
    want = set(channels) if channels is not None else {c for c in ("toast", "telegram", "webhook") if cfg.get(c)}
    hide = not cfg["share_grades"]
    out = {"toast": None, "telegram": None, "webhook": None}
    title, text = news.build_digest(events, hide_secrets=hide, max_lines=cfg["max_lines"], app=APP_NAME)
    if "toast" in want and toast:
        shown = notify(APP_NAME, "Что нового: " + news.toast_text(events))
        out["toast"] = {"ok": shown, "msg": "показано" if shown else "не получилось: на телефоне нужны приложение Termux:API и пакет termux-api"}
    if "telegram" in want:
        ok, msg = news.send_telegram(token, chat, f"{title}\n\n{text}", use_system_proxy=use_proxy)
        out["telegram"] = {"ok": ok, "msg": msg}
        if not ok:
            rep.log(f"Telegram: не отправлено — {msg}", "warn")
    if "webhook" in want:
        ok, msg = news.send_webhook(hook, cfg["webhook_format"], title, text, events, hide_secrets=hide,
                                    use_system_proxy=use_proxy)
        out["webhook"] = {"ok": ok, "msg": msg}
        if not ok:
            rep.log(f"Webhook: не отправлено — {msg}", "warn")
    return out


def deliver_news(events: list, settings: dict, vault: "Vault", rep: Reporter, *, toast_on=True) -> dict:
    """После запуска: выбирает события, о которых надо уведомить (вид события включён, курс не отключён), и рассылает.
    → {"count": сколько событий в уведомлении, "toast": bool, ...}."""
    cfg = news.normalize(settings.get("news"))
    res = {"count": 0, "toast": False, "telegram": None, "webhook": None}
    if not cfg["enabled"] or not events:
        return res
    push = [e for e in events if cfg["push"].get(e["kind"]) and e.get("course_id") not in cfg["mute"]]
    res["count"] = len(push)
    if not push:
        return res
    title, text = news.build_digest(push, hide_secrets=True, max_lines=cfg["max_lines"], app=APP_NAME)
    rep.log(f"{title}\n{text}")   # в журнал — без значений оценок; полный список — во вкладке «Что нового»
    r = send_news(push, cfg, vault, rep, toast=toast_on, use_proxy=bool(settings.get("use_system_proxy")))
    res.update({"toast": bool(r["toast"]), "telegram": r["telegram"], "webhook": r["webhook"]})
    return res


# ════════════════════════════════════════════════════════════════════════════
#  КАЛЕНДАРЬ И ДЕДЛАЙНЫ
# ════════════════════════════════════════════════════════════════════════════
def calendar_sync(client: "MoodleClient", out: Path, settings: dict, rep: Reporter, *, now: float | None = None) -> dict:
    """Читает календарь Moodle, обновляет сроки, Календарь/moodle_calendar.ics и Календарь/index.html.
    → {"ok", "text", "events": [события для «Что нового»: новый срок, перенос, срок горит], "files", "total", ...}.
    Сети нужна только для чтения; сломалась сеть или сессия — исключение уходит наверх, сроки остаются прежними."""
    cfg = deadlines.normalize(settings.get("calendar"))
    now = now or time.time()
    lo, hi = now - cfg["days_back"] * 86400, now + cfg["days_ahead"] * 86400
    store = deadlines.Store(Path(out) / deadlines.DEADLINES_FILE)
    first = store.first_sync()
    fetched, kinds, notes, source, complete = [], set(), [], [], True
    timeline_ok = False
    rep.log("Календарь Moodle: читаю сроки…")
    try:
        raw, full = client.get_action_events(lo, hi)
        fetched += deadlines.parse_events(raw)
        kinds |= {"deadline", "open", "completion"}
        timeline_ok, complete = True, full
        source.append("timeline")
        if not full:
            notes.append("сроков очень много — прочитана только часть")
    except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
        raise
    except Exception as e:
        notes.append(f"блок «График» недоступен ({describe_error(e)})")
    if not timeline_ok or "events" in cfg["kinds"]:
        try:
            more = deadlines.parse_events(client.get_month_events(lo, hi))
            if timeline_ok:    # сроки сдачи у «Графика» точнее (сданное он убирает) — из месяца берём только обычные события
                more = [e for e in more if e["kind"] == "events"]
                kinds.add("events")
            else:
                kinds |= set(deadlines.GROUPS)
            have = {e["id"] for e in fetched}
            fetched += [e for e in more if e["id"] not in have]
            source.append("monthly")
        except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
            raise
        except Exception as e:
            notes.append(f"месячный вид календаря недоступен ({describe_error(e)})")
            complete = False
    if not source:
        msg = "Календарь Moodle не прочитан: " + "; ".join(notes) + ". Сроки остались прежними."
        store.touch_sync(now, False, msg)
        rep.log(msg, "warn")
        return {"ok": False, "text": msg, "events": [], "files": {}, "total": 0}
    if source == ["monthly"]:
        notes.append("читаю «месячный вид»: сданные работы из него не пропадают сами — отмечайте их «сделано»")
    changes = store.merge(fetched, now, lo, hi, complete=complete, source="+".join(source), note="; ".join(notes), kinds=kinds)
    for n in notes:
        rep.log(f"  ! {n}", "warn")
    if changes["suspicious"]:
        rep.log("  ! Moodle вернул пустой календарь при множестве известных сроков — не стал считать их убранными.", "warn")
    events = deadlines.change_events(changes, now, cfg, announce_new=not first)
    events += deadlines.soon_events(store, cfg, now)
    files = {}
    try:
        files = deadlines.write_outputs(out, store, cfg, now)
    except OSError as e:
        rep.log(f"Календарь: файлы не записаны ({e}) — сроки сохранены, пересоберу в следующий раз.", "error")
    v = deadlines.view(store, [], cfg, now)
    shown = lambda e: e.get("kind") in cfg["kinds"]          # в итоге считаем только то, что вы смотрите
    n_new = sum(1 for e in changes["new"] if shown(e))
    n_moved = sum(1 for _, e in changes["moved"] if shown(e))
    n_gone = sum(1 for e in changes["gone"] if shown(e))
    text = (f"Календарь Moodle: сроков впереди — {v['counts']['total']}, просрочено — {v['counts']['overdue']}"
            + (f"; новых {n_new}" if n_new and not first else "")
            + (f", перенесено {n_moved}" if n_moved else "")
            + (f", убрано с сайта {n_gone} (вероятно, сданы)" if n_gone else "")
            + (" · первый запуск: сроки запомнил" if first else ""))
    rep.log(text)
    return {"ok": True, "text": text, "events": events, "files": files, "total": v["counts"]["total"],
            "new": n_new, "moved": n_moved, "gone": n_gone}


def calendar_watch(out: Path, settings: dict, vault: "Vault", rep: Reporter, *, now: float | None = None,
                   toast_on=True) -> int:
    """Без сети: смотрит сохранённые сроки и напоминает о тех, до которых осталось меньше порога (сутки, 3 часа…).
    Вызывается, пока открыто окно программы, и после каждой синхронизации. → сколько напоминаний отправлено."""
    cfg = deadlines.normalize(settings.get("calendar"))
    ncfg = news.normalize(settings.get("news"))
    if not (cfg["notify"] and ncfg["enabled"] and ncfg["track"].get("deadline_soon")):
        return 0
    store = deadlines.Store(Path(out) / deadlines.DEADLINES_FILE)
    if not store.events:
        return 0
    evs = deadlines.soon_events(store, cfg, now or time.time())
    if not evs:
        return 0
    try:
        news.NewsLog(Path(out) / NEWS_FILE).add(evs)
    except OSError as e:
        rep.log(f"Не удалось записать ленту «Что нового»: {e}", "error")
    deliver_news(evs, settings, vault, rep, toast_on=toast_on)
    return len(evs)


def cli_deadlines(args, settings: dict, rep: Reporter) -> int:
    """--deadlines [ДНЕЙ]: сроки и пары по сохранённым данным; без интернета и без входа."""
    out = Path(args.out or settings.get("out_dir") or DEFAULT_OUT)
    cfg = deadlines.normalize(settings.get("calendar"))
    store = deadlines.Store(out / deadlines.DEADLINES_FILE)
    lessons = deadlines.load_lessons(out) if cfg["lessons"] else []
    v = deadlines.view(store, lessons, cfg, time.time(), days=max(1, int(args.deadlines)), with_done=False)
    print(deadlines.format_view(v))
    ics = out / deadlines.CALENDAR_DIR / deadlines.ICS_FILE
    if ics.exists():
        print(f"\nКалендарь для телефона: {ics}")
    return 0


def cli_calendar_sync(client: "MoodleClient", vault: "Vault", settings: dict, args, rep: Reporter) -> int:
    """--calendar-sync: только календарь Moodle (после входа), без скачивания материалов."""
    out = Path(args.out or settings.get("out_dir") or DEFAULT_OUT)
    ncfg = news.normalize(settings.get("news"))
    r = calendar_sync(client, out, settings, rep)
    evs = [e for e in r["events"] if ncfg["enabled"] and ncfg["track"].get(e["kind"])]
    if evs:
        try:
            news.NewsLog(out / NEWS_FILE).add(evs)
        except OSError as e:
            rep.log(f"Не удалось записать ленту «Что нового»: {e}", "error")
        deliver_news(evs, settings, vault, rep, toast_on=not args.no_notify)
    if r.get("files"):
        print(f"Календарь для телефона: {r['files']['ics']}\nСтраница для офлайна: {r['files']['page']}")
    return 0 if r["ok"] else 2



# ════════════════════════════════════════════════════════════════════════════
#  ТАЙМЕР: ПРОВЕРКА «ЖИВ ЛИ MOODLE» И АВТООБНОВЛЕНИЕ
# ════════════════════════════════════════════════════════════════════════════
WATCH_BEAT = None                  # пока идёт слежение: обновить замок (зовётся во время долгой паузы)


def _watch_nap(sec):
    """Пауза между проверками кусками по 30 с: замок живёт 3 минуты, а пауза может быть до часа — без этого окно сочло бы его брошенным."""
    end = time.monotonic() + sec
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 30))
        if WATCH_BEAT:
            WATCH_BEAT()


WATCH_SLEEP = _watch_nap           # тесты подменяют, чтобы не ждать


def audit_downloads(out: Path, course_ids=None, mode: int | None = None) -> dict:
    """Сверяет память программы (.moodle_state.json) с тем, что реально лежит на диске.
    → {"has_state", "items", "files", "missing_files", "missing_items": [ключи], "courses": {id: {...}}, "empty"}.
    empty — на диске нет ни одного скачанного файла (по выбранным курсам)."""
    out = Path(out)
    res = {"has_state": fexists(out / STATE_FILE), "items": 0, "files": 0, "missing_files": 0, "missing_items": [],
           "courses": {}, "empty": True}
    if not res["has_state"]:
        return res
    state = State(out / STATE_FILE)
    ids = None if course_ids is None else {str(i) for i in course_ids}
    names = {k: v.get("name", "") for k, v in state.data["courses"].items()}
    dirs = {k: v.get("dir") for k, v in state.data["courses"].items()}     # пути файлов в элементах — внутри папки курса
    for key, rec in state.data["items"].items():
        parts = key.split(":", 2)
        if len(parts) < 3 or (mode is not None and parts[0] != str(mode)):
            continue
        cid = parts[1]
        if (ids is not None and cid not in ids) or not dirs.get(cid):     # курс не дошёл до конца — проверить нечем
            continue
        c = res["courses"].setdefault(cid, {"name": names.get(cid, ""), "items": 0, "files": 0, "missing_files": 0, "missing_items": 0})
        c["items"] += 1
        res["items"] += 1
        miss = 0
        for f in rec.get("files") or []:
            path = f.get("path")
            if not path:
                continue
            c["files"] += 1
            res["files"] += 1
            try:
                ok = fexists(out / Path(*PurePosixPath(dirs[cid]).parts) / Path(*PurePosixPath(path).parts))
            except OSError:
                ok = False
            if not ok:
                miss += 1
        if miss:
            c["missing_files"] += miss
            c["missing_items"] += 1
            res["missing_files"] += miss
            res["missing_items"].append(key)
    res["empty"] = res["files"] == 0
    return res


def invalidate_missing(out: Path, audit: dict) -> int:
    """Элементы, у которых на диске не хватает файлов, помечаются «не завершены» — тогда быстрая синхронизация
    обработает их заново и докачает недостающее. Остальные по-прежнему пропускаются. → сколько элементов помечено."""
    keys = audit.get("missing_items") or []
    if not keys:
        return 0
    state = State(Path(out) / STATE_FILE)
    n = 0
    for k in keys:
        rec = state.data["items"].get(k)
        if rec and rec.get("complete"):
            rec["complete"] = False
            n += 1
    state.save(force=True)
    return n


def pick_course_ids(acfg: dict, courses: list, state_courses: dict) -> list[int]:
    """Какие курсы обновлять автоматически: все (кроме прошлых и скрытых), выбранные или уже скачанные."""
    if acfg["scope"] == "selected":
        return list(acfg["courses"])
    if acfg["scope"] == "existing":
        return [int(k) for k in state_courses if str(k).isdigit()]
    return [c.id for c in courses if not c.hidden and (acfg["include_past"] or not c.past)]


def prepare_auto_update(out: Path, ids: list[int], mode: int, rep: Reporter) -> dict:
    """Перед автообновлением: что есть на диске? Недостающее помечаем к докачке, готовое пропускаем,
    а если скачанного нет вообще — качаем всё выбранное. → итоги проверки."""
    a = audit_downloads(out, ids, mode)
    n = invalidate_missing(out, a)
    if a["empty"]:
        rep.log(f"Проверка файлов: на диске пока нет скачанного — скачаю всё выбранное (курсов: {len(ids)}).")
    elif a["missing_files"]:
        rep.log(f"Проверка файлов: на диске не хватает {a['missing_files']} файл(ов) в {len(a['courses']) and sum(1 for c in a['courses'].values() if c['missing_files'])} курсе(ах) — "
                f"докачаю их ({n} элемент(ов)), остальное пропущу.")
    else:
        rep.log(f"Проверка файлов: всё скачанное на месте ({a['files']} файл(ов)) — загляну только за новым.")
    return a


def auto_job_options(settings: dict, out: Path) -> "JobOptions":
    """Параметры автообновления: как у быстрой синхронизации, с сохранёнными настройками."""
    return JobOptions(out_dir=out, mode=int(settings.get("mode", 1)), sync=True, pdf=bool(settings.get("pdf")),
                      zip=bool(settings.get("zip")), original_names=bool(settings.get("original_names")),
                      index_in_mode2=bool(settings.get("index_in_mode2")), rules=settings.get("rules"),
                      dir_template=settings.get("layout_dir") or "", file_template=settings.get("layout_file") or "",
                      news=settings.get("news"), calendar=settings.get("calendar"),
                      calendar_on=deadlines.normalize(settings.get("calendar"))["auto"])


def auto_update(client: "MoodleClient", vault: "Vault", settings: dict, rep: Reporter, control: Control, *,
                toast_on=True) -> dict:
    """Автообновление без окна (консоль, --watch): сверить диск, докачать недостающее и новое, разослать уведомления.
    Вход уже выполнен. → {"ok", "text", "aborted", ...}."""
    acfg = watch.normalize(settings.get("watch"))["auto"]
    out = Path(settings.get("out_dir") or DEFAULT_OUT)
    courses = client.get_courses()
    state_courses = State(out / STATE_FILE).data["courses"] if fexists(out / STATE_FILE) else {}
    ids = pick_course_ids(acfg, courses, state_courses)
    known = {c.id: c for c in courses}
    selected = [known.get(i) or Course(id=i, fullname=str((state_courses.get(str(i)) or {}).get("name", ""))) for i in ids]
    if not selected:
        text = "Автообновление: нет курсов для обновления (проверьте, какие курсы выбраны)."
        rep.log(text, "warn")
        return {"ok": False, "text": text, "aborted": "empty"}
    opts = auto_job_options(settings, out)
    fmkdir(out)
    rep.log(f"Автообновление: курсов {len(selected)}, режим {opts.mode}, папка {out}")
    prepare_auto_update(out, [c.id for c in selected], opts.mode, rep)
    t0 = time.monotonic()
    res = run_job(client, selected, opts, rep, control)
    if res.aborted is None:
        extra_sync(settings, vault, client, out, rep, control, notify_on=toast_on)
    delivered = deliver_news(res.events, settings, vault, rep, toast_on=toast_on) if res.events else {}
    if res.aborted != "stopped" and search.normalize(settings.get("search"))["auto"]:
        try:
            search_pass(out, settings, rep)
        except KeyboardInterrupt:
            rep.log("Индексация для поиска прервана — доделается в следующий раз.", "warn")
    head = {"network": "Прервано: нет связи", "session": "Прервано: сессия истекла", "stopped": "Остановлено"}.get(res.aborted, "Готово")
    text = f"Автообновление. {head} за {fmt_dur(time.monotonic() - t0)}. {summary_text(res, opts)}"
    rep.log(text)
    if toast_on and res.aborted != "stopped" and not (delivered.get("toast") and res.aborted is None):
        notify(APP_NAME, text, ok=res.aborted is None)
    return {"ok": res.aborted is None, "text": text, "aborted": res.aborted, "new": len(res.new_files),
            "failures": len(res.failures)}


def watch_notify(evs: list, settings: dict, vault: "Vault", rep: Reporter, out: Path, *, toast_on=True):
    """События таймера («Moodle недоступен» / «снова работает») → лента «Что нового» и уведомления."""
    cfg = watch.normalize(settings.get("watch"))["check"]
    ncfg = news.normalize(settings.get("news"))
    host = urlparse(normalize_base(settings.get("base_url") or DEFAULT_URL)).netloc
    events = watch.make_events(evs, host, cfg["notify_down"], cfg["notify_up"], time.time())
    events = [e for e in events if ncfg["enabled"] and ncfg["track"].get(e["kind"])]
    if not events:
        return
    try:
        news.NewsLog(Path(out) / NEWS_FILE).add(events)
    except OSError as e:
        rep.log(f"Не удалось записать ленту «Что нового»: {e}", "error")
    deliver_news(events, settings, vault, rep, toast_on=toast_on)


def update_tick(settings: dict, vault: "Vault", rep: Reporter, toast_on: bool = True) -> None:
    """Слежение без окна: раз в сутки (по настройке) спросить GitHub, не вышла ли новая версия, и сказать об этом один раз."""
    import updater
    cfg = updater.normalize(settings.get("update"))
    if not updater.due(cfg):
        return
    k = updater.kind(frozen=bool(getattr(sys, "frozen", False)), apk=android.is_apk(), android=android.is_android())
    updater.run_check(cfg, VERSION, k, fetch=lambda cur: updater.fetch_latest(cur, bool(settings.get("use_system_proxy"))))
    st = updater.status(cfg, VERSION, k)
    if updater.should_notify(cfg, st):
        cfg["notified"] = st["latest"]["tag"]
        rep.log(f"Вышла новая версия {st['latest']['version']} (у вас {VERSION}). {st['how']}")
        if toast_on:
            notify(APP_NAME, updater.notice_text(st))
    elif cfg.get("error"):
        rep.log(f"Новую версию не проверил: {cfg['error']}", "debug")
    vault.save_settings({**vault.load_settings(), "update": cfg})


def cli_watch(args, settings: dict, vault: "Vault", rep: Reporter) -> int:
    """--watch: слежение без окна. По настройкам раз в N секунд проверяет Moodle (упал / ожил → уведомление),
    а рубильником «автообновление» по таймеру докачивает недостающее. Остановка — Ctrl+C."""
    lock = watch.Lock(DATA_DIR, "консоль")
    if not lock.acquire():
        h = lock.holder() or {}
        print(f"Слежение уже ведёт другая программа ({h.get('who') or 'окно или другая консоль'}). Закройте её или подождите 3 минуты.")
        return 3
    base = normalize_base(args.url or settings.get("base_url") or DEFAULT_URL)
    settings["base_url"] = base
    control = Control()
    control.reporter = rep
    client = MoodleClient(base, page_delay=parse_delay(args.page_delay or settings.get("page_delay"), PAGE_DELAY),
                          file_delay=parse_delay(args.file_delay or settings.get("file_delay"), FILE_DELAY),
                          wait_minutes=int(settings.get("wait_minutes", WAIT_SERVER_MINUTES)),
                          use_system_proxy=bool(settings.get("use_system_proxy") or args.use_proxy),
                          control=control, reporter=rep)
    client.no_wait = True
    tracker = watch.Tracker()
    ticks = 0
    global WATCH_BEAT
    WATCH_BEAT = lock.acquire
    held = False
    if android.is_android() and not args.no_wakelock:
        held = android.wake_lock(True)                 # иначе телефон усыпит Termux, и проверки встанут
        rep.log("Телефон не будет засыпать, пока идёт слежение (termux-wake-lock)." if held
                else "Не удалось включить termux-wake-lock: ночью Android может приостановить слежение (см. README, раздел про Android).", "info" if held else "warn")
    rep.log(f"Слежение за {urlparse(base).netloc} запущено (Ctrl+C — остановить).")
    try:
        while True:
            settings = {**vault.load_settings(), "base_url": base}           # настройки из окна подхватываются на лету
            cfg = watch.normalize(settings.get("watch"))
            now = datetime.now()
            tracker.down_after = cfg["check"]["down_after"]
            out = Path(args.out or settings.get("out_dir") or DEFAULT_OUT)
            if not lock.acquire():
                rep.log("Слежение перехватила другая программа — выхожу.", "warn")
                return 3
            probed = None
            if cfg["check"]["enabled"] and watch.in_window(cfg["check"]["window"], now):
                st, dt = client.probe()
                probed = st
                watch.record_check(DATA_DIR, st, time.time())
                evs = tracker.feed(st, time.time())
                rep.log(f"Moodle: {st}" + (f" ({dt:.1f} с)" if dt else ""), "debug")
                if evs:
                    for e in evs:
                        rep.log("Moodle не отвечает." if e["type"] == "down" else f"Moodle снова работает (не работал {watch.fmt_outage(e['outage'])}).")
                    watch_notify(evs, settings, vault, rep, out, toast_on=not args.no_notify)
            acfg = cfg["auto"]
            wstate = watch.load_state(DATA_DIR)
            ok_now = None if probed is None else probed != "down"
            if ok_now is None and acfg["enabled"] and tracker.status != "unknown":
                ok_now = tracker.status != "down"
            can_login = bool(vault.load_secret().get("cookies") or vault.load_secret().get("password")
                             or os.environ.get("MOODLE_PASSWORD") or os.environ.get("MOODLE_COOKIE") or args.cookie)
            due, why = watch.auto_due(acfg, wstate["auto"], now, moodle_ok=ok_now, busy=False, can_login=can_login)
            if due:
                rep.log(f"Автообновление: запускаю ({why}).")
                watch.record_attempt(DATA_DIR, time.time())
                try:
                    if not (args.no_geo_check or settings.get("no_geo_check") or args.ignore_geo):
                        ip, cc, _svc = check_ip_country(bool(settings.get("use_system_proxy") or args.use_proxy))
                        if cc and cc != "RU":
                            raise MoodleError(f"IP {ip} ({cc}) — похоже, включён VPN; Moodle может не открыться. Отложу до следующего раза")
                    client.s.cookies.clear()
                    cli_login(client, vault, settings, args, False, rep)
                    client.no_wait = False
                    r = auto_update(client, vault, settings, rep, control, toast_on=not args.no_notify)
                    watch.record_result(DATA_DIR, r["ok"], r["text"], time.time())
                except (LoginError, MoodleError, RetriesExhausted, NetworkDown) as e:
                    msg = f"Автообновление не вышло: {describe_error(e)}"
                    rep.log(msg, "error")
                    watch.record_result(DATA_DIR, False, msg, time.time())
                finally:
                    client.no_wait = True
            try:
                update_tick(settings, vault, rep, toast_on=not args.no_notify)
            except Exception as e:                       # проверка обновлений не должна ронять слежение
                rep.log(f"Проверка обновлений: сбой ({describe_error(e)})", "debug")
            ticks += 1
            if args.watch_max and ticks >= args.watch_max:
                return 0
            interval = cfg["check"]["interval"] if cfg["check"]["enabled"] else 60
            WATCH_SLEEP(min(interval, 3600))
    except KeyboardInterrupt:
        print("\nСлежение остановлено.")
        return 130
    finally:
        WATCH_BEAT = None
        if held:
            android.wake_lock(False)
        lock.release()


def cli_watch_status(settings: dict) -> int:
    cfg = watch.normalize(settings.get("watch"))
    st = watch.load_state(DATA_DIR)
    s = watch.summary(cfg, st, datetime.now())
    print(f"Проверка Moodle: {s['check']}\nАвтообновление: {s['auto']}")
    if s["last_text"]:
        print(f"Последний запуск ({s['last_end']}): {s['last_text']}")
    h = watch.Lock(DATA_DIR).holder()
    if h and time.time() - float(h.get("ts") or 0) < watch.LOCK_STALE:
        print(f"Сейчас слежение ведёт: {h.get('who') or '?'}")
    return 0


# ════════════════════════════════════════════════════════════════════════════
#  ОГЛАВЛЕНИЯ И ОТЧЁТЫ
# ════════════════════════════════════════════════════════════════════════════
INDEX_CSS = """
:root{color-scheme:light dark;--fg:#1b1f24;--muted:#5f6b7a;--line:#e2e6ec;--bg:#f6f7f9;--card:#fff;--accent:#2f6fde;
--lk:#2f6fde;--pz:#0d9488;--lz:#c2410c;--dr:#6b7280}
@media (prefers-color-scheme:dark){:root{--fg:#e6e9ee;--muted:#9aa5b4;--line:#2a313b;--bg:#0f1216;--card:#171b21;--accent:#6ea0ff}}
*{box-sizing:border-box} body{margin:0;font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:var(--fg);background:var(--bg)}
header{padding:28px 24px 18px;max-width:1100px;margin:auto} h1{margin:0 0 6px;font-size:26px;line-height:1.25}
.meta{color:var(--muted);font-size:13px} .meta a{color:var(--accent)}
.bar{position:sticky;top:0;background:var(--bg);padding:10px 24px;z-index:2;border-bottom:1px solid var(--line)}
.bar>div{max-width:1100px;margin:auto;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input[type=search]{flex:1;min-width:220px;padding:9px 12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--fg);font:inherit}
.chip{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:6px 12px;cursor:pointer;font:inherit;font-size:13px}
.chip.on{background:var(--accent);color:#fff;border-color:var(--accent)}
main{max-width:1100px;margin:auto;padding:10px 24px 60px}
details{background:var(--card);border:1px solid var(--line);border-radius:14px;margin:14px 0;overflow:hidden}
summary{cursor:pointer;padding:14px 18px;font-weight:600;font-size:16px;list-style:none} summary::-webkit-details-marker{display:none}
summary small{color:var(--muted);font-weight:400;margin-left:6px}
.row{display:grid;grid-template-columns:64px 1fr;gap:12px;padding:12px 18px;border-top:1px solid var(--line)}
.kind{font-size:11px;font-weight:700;letter-spacing:.04em;border-radius:6px;padding:3px 0;text-align:center;color:#fff;height:fit-content}
.k-ЛК{background:var(--lk)}.k-ПЗ{background:var(--pz)}.k-ЛЗ{background:var(--lz)}.k-ДР{background:var(--dr)}
.name{font-weight:600} .ctx,.desc{color:var(--muted);font-size:13px} .files{margin:6px 0 0;padding:0;list-style:none}
.files li{font-size:14px;margin:2px 0} .files a{color:var(--accent);text-decoration:none} .files a:hover{text-decoration:underline}
.size{color:var(--muted);font-size:12px;margin-left:6px} .warn{color:#c62828;font-size:13px} .online{color:var(--muted);font-size:13px}
.hide{display:none!important}
"""

INDEX_JS = """
const q=document.getElementById('q');const chips=[...document.querySelectorAll('.chip')];let kind='';
function apply(){const t=q.value.trim().toLowerCase();document.querySelectorAll('details').forEach(d=>{let any=false;
d.querySelectorAll('.row').forEach(r=>{const ok=(!t||r.textContent.toLowerCase().includes(t))&&(!kind||r.dataset.kind===kind);
r.classList.toggle('hide',!ok);if(ok)any=true});d.classList.toggle('hide',!any);if(t||kind)d.open=true;});}
q.addEventListener('input',apply);chips.forEach(c=>c.onclick=()=>{chips.forEach(x=>x.classList.remove('on'));c.classList.add('on');kind=c.dataset.k;apply();});
"""


def _href(path: str) -> str:
    return quote(path, safe="/()-_.~!$&'*+,;=:@")


def _mdlink(text: str, path: str) -> str:
    """Читаемая ссылка Markdown: [текст](<путь с пробелами.pdf>) — понимают GitHub, Obsidian, VS Code."""
    text = re.sub(r"([\[\]])", r"\\\1", text)
    return f"[{text}](<{path}>)"


def write_course_index(course: Course, layout: Layout):
    cdir = layout.course_dir
    date = datetime.now().strftime("%d.%m.%Y %H:%M")
    esc = htmllib.escape
    # ── README.md ──
    md = [f"# {course.fullname}", "", f"Источник: {course.url}  ", f"Сохранено: {date}  ",
          "Обозначения: ЛК — лекции, ПЗ — практические, ЛЗ — лабораторные, ДР — прочее.", ""]
    rows_html = []
    for sec in course.sections:
        md.append(f"## {sec.number:02d}. {sec.name}")
        sec_rows = []
        for kind in KINDS:
            items = [it for it in sec.items if it.kind == kind]
            if not items:
                continue
            md.append(f"### {kind}")
            for it in items:
                name_md = re.sub(r"([\[\]*_`<>])", r"\\\1", it.name)
                ctx = f" _(из блока «{it.context}»)_" if it.context and it.context != it.name else ""
                if it.online_only:
                    md.append(f"- {name_md}{ctx} — " + ("пропущено правилами скачивания" if it.rule_skip
                                                         else f"только онлайн ({MOD_TITLES.get(it.modtype, it.modtype)})")
                              + (f": <{it.url}>" if it.url else " (недоступно)"))
                elif len(it.files) == 1 and not it.error:
                    f = it.files[0]
                    md.append(f"- **{name_md}**{ctx} — {_mdlink(PurePosixPath(f['path']).name, f['path'])} · {hsize(f['size'])}")
                else:
                    md.append(f"- **{name_md}**{ctx}")
                    for f in it.files:
                        md.append(f"  - {_mdlink(f['title'], f['path'])} · {hsize(f['size'])}")
                if it.external:
                    md.append(f"  - внешняя ссылка: <{it.external}>")
                for note in it.rule_notes:
                    md.append(f"  - ⊘ пропущено правилами: {note}")
                if it.error:
                    md.append(f"  - ⚠ скачано не полностью: {it.error}")
                # HTML-строка
                files_html = "".join(
                    f'<li><a href="{esc(_href(f["path"]))}">{esc(f["title"] if len(it.files) > 1 else PurePosixPath(f["path"]).name)}</a>'
                    f'<span class="size">{hsize(f["size"])}</span></li>' for f in it.files)
                extra = ""
                if it.online_only:
                    extra += (f'<div class="online">' + ("Пропущено правилами скачивания" if it.rule_skip
                                                          else f"Только онлайн ({esc(MOD_TITLES.get(it.modtype, it.modtype))})") + ' — '
                              + (f'<a href="{esc(it.url)}">открыть на сайте</a>' if it.url else "недоступно") + "</div>")
                for note in it.rule_notes:
                    extra += f'<div class="online">Пропущено правилами: {esc(note)}</div>'
                if it.external:
                    extra += f'<div class="online">Внешняя ссылка: <a href="{esc(it.external)}">{esc(short(it.external, 90))}</a></div>'
                if it.error:
                    extra += f'<div class="warn">Скачано не полностью: {esc(it.error)}</div>'
                desc = f'<div class="desc">{esc(it.description)}</div>' if it.description else ""
                ctxh = f'<div class="ctx">из блока «{esc(it.context)}»</div>' if ctx else ""
                code = KIND_CODE[it.kind]
                sec_rows.append(f'<div class="row" data-kind="{code}"><div class="kind k-{code}">{code}</div><div>'
                                f'<div class="name">{esc(it.name)}</div>{ctxh}{desc}'
                                f'<ul class="files">{files_html}</ul>{extra}</div></div>')
        md.append("")
        rows_html.append(f"<details open><summary>{sec.number:02d}. {esc(sec.name)}<small>{len(sec.items)} эл.</small></summary>"
                         f"{''.join(sec_rows)}</details>")
    write_text_atomic(cdir / "README.md", "\n".join(md) + "\n")
    chips = '<button class="chip on" data-k="">Все</button>' + "".join(
        f'<button class="chip" data-k="{KIND_CODE[k]}">{k}</button>' for k in KINDS)
    page = (f'<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(course.fullname)}</title><style>{INDEX_CSS}</style></head><body>"
            f'<header><h1>{esc(course.fullname)}</h1><div class="meta">Сохранено {date} · '
            f'<a href="{esc(course.url)}">открыть курс на сайте</a> · <a href="../index.html">все курсы</a></div></header>'
            f'<div class="bar"><div><input id="q" type="search" placeholder="Поиск по названию…">{chips}</div></div>'
            f"<main>{''.join(rows_html)}</main><script>{INDEX_JS}</script></body></html>")
    write_text_atomic(cdir / "index.html", page)


def write_course_json(course: Course, layout: Layout):
    """Структура курса для просмотра в окне программы «как на сайте» (разделы, элементы, скачанные файлы)."""
    data = {"id": course.id, "fullname": course.fullname, "url": course.url, "category": course.category,
            "dir": layout.course_rel.as_posix(), "mode": layout.mode, "updated": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "sections": [{"number": s.number, "name": s.name, "items": [
                {"key": it.key, "name": it.name, "modtype": it.modtype, "kind": it.kind, "url": it.url,
                 "description": it.description, "context": it.context, "external": it.external,
                 "online_only": it.online_only, "error": it.error, "files": it.files} for it in s.items]}
                for s in course.sections]}
    write_text_atomic(layout.course_dir / ".course.json", json.dumps(data, ensure_ascii=False, indent=1))


def write_root_index(out: Path, state: State):
    esc = htmllib.escape
    cards = []
    for cid, c in sorted(state.data.get("courses", {}).items(), key=lambda kv: kv[1].get("name", "").lower()):
        d = c.get("dir")
        if not d or not fexists(out / d / "index.html"):
            continue
        cards.append(f'<a class="card" href="{esc(_href(d))}/index.html"><b>{esc(c.get("name", ""))}</b>'
                     f'<span>{c.get("files", 0)} файлов · обновлено {esc(c.get("updated", ""))}</span></a>')
    css = INDEX_CSS + (".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}"
                       ".card{display:flex;flex-direction:column;gap:6px;background:var(--card);border:1px solid var(--line);"
                       "border-radius:14px;padding:18px;color:var(--fg);text-decoration:none}.card:hover{border-color:var(--accent)}"
                       ".card span{color:var(--muted);font-size:13px}")
    page = (f'<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>Материалы Moodle</title><style>{css}</style></head>'
            f'<body><header><h1>Материалы Moodle</h1><div class="meta">Оффлайн-копия · {datetime.now():%d.%m.%Y %H:%M}</div></header>'
            f'<main><div class="grid">{"".join(cards) or "<p>Пока ничего не скачано.</p>"}</div></main></body></html>')
    write_text_atomic(out / "index.html", page)


def write_reports(out: Path, proc: Processor | None, res: JobResult):
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    failures = proc.failures if proc else res.failures
    if failures:
        lines = [f"Не удалось скачать (запуск {stamp}).",
                 "При следующем запуске программа попробует скачать это снова автоматически.", ""]
        for f in failures:
            lines += [f"[{f['course']}]", f"  Раздел:  {f['section']}", f"  Элемент: {f['item']} ({f['kind']})",
                      f"  Причина: {f['error']}", f"  Ссылка:  {f['url'] or '—'}", ""]
        urls = [f["url"] for f in failures if f.get("url")]
        if urls:
            lines += ["── Только ссылки (можно открыть в браузере) ──", *dict.fromkeys(urls)]
        write_text_atomic(out / FAILED_FILE, "\n".join(lines) + "\n")
    else:
        funlink(out / FAILED_FILE)
    new = proc.new_files if proc else []
    lines = [f"Новое за запуск {stamp}", ""]
    if new:
        by_course = {}
        for f in new:
            by_course.setdefault(f["course"], []).append(f)
        for c, fl in by_course.items():
            lines.append(f"[{c}] — {len(fl)} файл(ов)")
            lines += [f"  {f['path']}  ({hsize(f['size'])})" for f in fl]
            lines.append("")
    else:
        lines.append("Новых файлов нет.")
    write_text_atomic(out / NEW_FILE, "\n".join(lines) + "\n")
    skips = proc.rule_skips if proc else []
    if skips:
        lines = [f"Пропущено по правилам скачивания (запуск {stamp}).",
                 "Это не ошибки. Чтобы скачать, поменяйте правила и запустите загрузку ещё раз.", ""]
        by_course = {}
        for f in skips:
            by_course.setdefault(f["course"], []).append(f)
        for c, fl in by_course.items():
            lines.append(f"[{c}] — {len(fl)}")
            lines += [f"  {f['section']} · {f['what']} — {f['reason']}" for f in fl]
            lines.append("")
        write_text_atomic(out / RULES_FILE, "\n".join(lines) + "\n")
    else:
        funlink(out / RULES_FILE)


def print_tree(course: Course, rep: Reporter):
    rep.log(f"Курс: {course.fullname}  (id {course.id})")
    for s in course.sections:
        rep.log(f"  {s.number:02d}. {s.name}")
        for it in s.items:
            flag = "  [только онлайн]" if it.online_only else ""
            rep.log(f"      [{KIND_CODE[it.kind]}] {it.name}  ({MOD_TITLES.get(it.modtype, it.modtype)}){flag}")


# ════════════════════════════════════════════════════════════════════════════
#  ЗАДАНИЕ ЦЕЛИКОМ (используется и консолью, и GUI)
# ════════════════════════════════════════════════════════════════════════════
def run_job(client: MoodleClient, courses: list[Course], opts: JobOptions, rep: Reporter, control: Control) -> JobResult:
    out = Path(opts.out_dir)
    res = JobResult()
    proc = None
    state = None
    ncfg = news.normalize(opts.news)
    watch_news = ncfg["enabled"] and any(ncfg["track"].values())
    if not opts.dry_run:
        fmkdir(out)
        rep.open_log(out / LOG_FILE)
        state = State(out / STATE_FILE)
        proc = Processor(client, state, out, opts, rep, control)
    control.reporter = rep
    try:
        for ci, c in enumerate(courses, 1):
            control.checkpoint()
            rep.course(ci, len(courses), c)
            try:
                client.parse_course(c)
            except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
                raise
            except Exception as e:
                rep.log(f"Не удалось открыть курс: {describe_error(e)}", "error")
                res.failures.append({"course": c.fullname or str(c.id), "section": "—", "item": "страница курса",
                                     "kind": "—", "url": c.url, "error": describe_error(e)})
                if proc:
                    proc.failures.append(res.failures[-1])
                continue
            classify_course(c)
            n_items = sum(len(s.items) for s in c.sections)
            rep.log(f"Разделов: {len(c.sections)}, элементов: {n_items}")
            if opts.dry_run:
                print_tree(c, rep)
                res.courses_done += 1
                continue
            layout = Layout(out, c, opts.mode, opts.original_names, opts.dir_template or "", opts.file_template or "")
            proc.run_course(c, layout)
            try:
                write_course_json(c, layout)
                if opts.mode == 1 or opts.index_in_mode2:
                    write_course_index(c, layout)
            except OSError as e:
                rep.log(f"Не удалось обновить оглавление курса (файл занят?): {e}", "error")
            n_files = sum(len(it.files) for s in c.sections for it in s.items)
            state.set_course(c.id, {"name": c.fullname, "dir": layout.course_rel.as_posix(), "files": n_files,
                                    "updated": datetime.now().strftime("%d.%m.%Y %H:%M"), "mode": opts.mode})
            if watch_news:
                try:
                    proc.events += proc.news_pass(c, layout, ncfg)
                except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
                    raise
                except Exception as e:
                    rep.log(f"Что нового: курс не проверен ({describe_error(e)}). Скачанное в порядке.", "warn")
            if proc.cr.zip if proc.cr.zip is not None else opts.zip:
                try:
                    zip_course(out, layout, rep)
                except OSError as e:
                    rep.log(f"Не удалось создать архив: {e}. Скачанные файлы в порядке — архив можно пересобрать "
                            f"следующим запуском.", "error")
            res.courses_done += 1
        if proc and opts.calendar_on and not opts.dry_run:
            control.checkpoint()
            try:
                cal = calendar_sync(client, out, {"calendar": opts.calendar}, rep)
                if ncfg["enabled"]:
                    proc.events += [e for e in cal["events"] if ncfg["track"].get(e["kind"])]
            except (NetworkDown, Stopped, SessionExpired, KeyboardInterrupt):
                raise
            except Exception as e:
                rep.log(f"Календарь: сроки не обновлены ({describe_error(e)}). Скачанное в порядке.", "warn")
    except NetworkDown as e:
        res.aborted, res.message = "network", str(e)
        rep.log(f"Остановлено: {e}. Прогресс сохранён — запустите программу ещё раз, когда связь появится.", "error")
    except SessionExpired as e:
        res.aborted, res.message = "session", str(e)
        rep.log("Остановлено: сессия Moodle истекла. Войдите заново и запустите ещё раз — скачанное не потеряется.", "error")
    except (Stopped, KeyboardInterrupt):
        res.aborted, res.message = "stopped", "остановлено пользователем"
        rep.log("Остановлено. Прогресс сохранён, при следующем запуске продолжу с этого места.", "warn")
    finally:
        if state:
            state.save(force=True)
        if proc and proc.events:
            try:
                news.NewsLog(out / NEWS_FILE).add(proc.events)
            except OSError as e:
                rep.log(f"Не удалось записать ленту «Что нового»: {e}", "error")
        if proc:
            try:
                write_reports(out, proc, res)
                if opts.mode == 1 or opts.index_in_mode2:
                    write_root_index(out, state)
            except OSError as e:
                rep.log(f"Не удалось записать отчёты: {e}", "error")
    if proc:
        res.failures, res.new_files, res.stats, res.events = proc.failures, proc.new_files, proc.stats, proc.events
    return res


def summary_text(res: JobResult, opts: JobOptions) -> str:
    st = res.stats or {}
    parts = [f"Новых файлов: {st.get('new', 0)} ({hsize(st.get('bytes', 0))})",
             f"уже было: {st.get('skipped', 0) + st.get('copied', 0)}"]
    if st.get("rule_skipped"):
        parts.append(f"пропущено правилами: {st['rule_skipped']} (список в {RULES_FILE})")
    if res.failures:
        parts.append(f"не скачано: {len(res.failures)} (список в {FAILED_FILE})")
    if res.events:
        parts.append(f"что нового: {len(res.events)}")
    return ", ".join(parts)


# ════════════════════════════════════════════════════════════════════════════
#  КОНСОЛЬНАЯ ВЕРСИЯ
# ════════════════════════════════════════════════════════════════════════════
def load_env():
    if load_dotenv:
        for p in (APP_DIR / ".env", Path.cwd() / ".env"):
            if p.exists():
                load_dotenv(p, override=False)


def ask(prompt: str, default: str = "") -> str:
    tail = f" [{default}]" if default else ""
    try:
        v = input(f"{prompt}{tail}: ").strip()
    except EOFError:
        v = ""
    return v or default


def ask_yes(prompt: str, default: bool = False) -> bool:
    d = "Д/н" if default else "д/Н"
    v = ask(f"{prompt} ({d})").lower()
    if not v:
        return default
    return v[0] in ("д", "y", "1", "l")  # 'l' — «д» в английской раскладке


def parse_selection(s: str, n: int) -> list[int]:
    s = s.strip().lower()
    if s in ("", "все", "all", "*", "0"):
        return list(range(n))
    out = []
    for part in re.split(r"[,\s;]+", s):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out += list(range(min(a, b) - 1, max(a, b)))
        elif part.isdigit():
            out.append(int(part) - 1)
        else:
            raise ValueError(part)
    return [i for i in dict.fromkeys(out) if 0 <= i < n]


def cli_login(client: MoodleClient, vault: Vault, settings: dict, args, interactive: bool, rep: Reporter):
    secret = vault.load_secret()
    if secret.get("base_url") and normalize_base(secret["base_url"]) != client.base:
        secret = {}
    cookie = args.cookie or os.environ.get("MOODLE_COOKIE")
    if cookie:
        client.use_cookie_string(cookie)
        if client.verify():
            rep.log("Вход по cookie выполнен.")
            return
        raise LoginError("cookie недействителен или устарел")
    if secret.get("cookies"):
        client.import_cookies(secret["cookies"])
        if client.verify():
            rep.log("Вход по сохранённой сессии — пароль не нужен.")
            if secret.get("username") and secret.get("password"):
                client.creds = (secret["username"], secret["password"])
            return
        client.s.cookies.clear()
    username = args.user or os.environ.get("MOODLE_USERNAME") or secret.get("username") or settings.get("username") or ""
    password = os.environ.get("MOODLE_PASSWORD") or secret.get("password") or ""
    for attempt in range(3):
        if not username:
            if not interactive:
                raise LoginError("не задан логин (сохраните вход в GUI или укажите --user / MOODLE_USERNAME)")
            username = ask("Логин")
        if not password:
            if not interactive:
                raise LoginError("нет сохранённого пароля для автоматического входа")
            try:
                password = getpass.getpass("Пароль (символы не отображаются — это нормально): ")
            except EOFError:
                raise LoginError("пароль не введён")
        try:
            rep.log("Вхожу в Moodle…")
            client.login(username, password)
            rep.log("Вход выполнен.")
            break
        except LoginError as e:
            rep.log(f"Не удалось войти: {e}", "error")
            if not interactive or attempt == 2:
                raise
            password = ""
            if ask_yes("Ввести логин заново?", False):
                username = ""
    settings["username"] = username
    remember = settings.get("remember")
    if remember is None and interactive:
        note = ("логин и пароль будут зашифрованы средствами Windows и доступны только вашей учётной записи"
                if vault.can_encrypt else "сохраню только cookie сессии; когда она истечёт, пароль спросят снова")
        remember = ask_yes(f"Запомнить вход на этом компьютере? ({note})", True)
        settings["remember"] = remember
    if remember:
        vault.save_secret({"base_url": client.base, "username": username, "password": password,
                           "cookies": client.export_cookies()})


def extra_sync(settings, vault, client, out, rep, control, notify_on=True):
    """При быстрой синхронизации: обновить расписание на 2 недели и проверить новые элективы (если включено)."""
    if settings.get("schedule_auto", True) and settings.get("schedule_group"):
        try:
            import schedule as sched
            sec = vault.load_secret()
            creds = (sec["username"], sec["password"]) if sec.get("username") and sec.get("password") else None
            job = sched.ScheduleJob(out, settings["schedule_group"], settings.get("schedule_sources") or sched.DEFAULT_SOURCES,
                                    rep, control, urls=settings.get("schedule_urls"), pdf=bool(settings.get("schedule_pdf", True)),
                                    use_system_proxy=bool(settings.get("use_system_proxy")),
                                    page_delay=parse_delay(settings.get("page_delay"), PAGE_DELAY),
                                    creds=creds, cookies=sec.get("sso_cookies"))
            rng = settings.get("schedule_auto_range")
            job.run(rng if rng in sched.AUTO_RANGES else "2weeks")
            if job.cookies_out and settings.get("remember"):
                vault.save_secret({**vault.load_secret(), "sso_cookies": job.cookies_out[-60:]})
        except (Stopped, KeyboardInterrupt):
            raise
        except Exception as e:
            rep.log(f"Расписание не обновилось: {describe_error(e)}", "warn")
    if settings.get("electives_watch"):
        try:
            import iotapi
            sec = vault.load_secret()
            creds = client.creds or ((sec.get("username"), sec.get("password")) if sec.get("password") else None)
            e = iotapi.Iot(out, DATA_DIR, rep, control, creds=creds,
                           cookies=sec.get("sso_cookies") or (sec.get("portals") or {}).get("iot.sevsu.ru"),
                           use_system_proxy=bool(settings.get("use_system_proxy")),
                           page_delay=parse_delay(settings.get("page_delay"), PAGE_DELAY),
                           file_delay=parse_delay(settings.get("file_delay"), FILE_DELAY))
            st = e.refresh()
            if e.cookies_out and settings.get("remember"):
                vault.save_secret({**vault.load_secret(), "sso_cookies": e.cookies_out[-60:]})
            if st.get("changes") and notify_on:
                notify(APP_NAME, "Элективы: " + "; ".join(short(c, 70) for c in st["changes"][:3]))
        except (Stopped, KeyboardInterrupt):
            raise
        except Exception as e:
            rep.log(f"Элективы не проверены: {describe_error(e)}. Откройте вкладку «Элективы» в окне программы — "
                    f"там можно ввести пароль и повторить.", "warn")


def cli_search(args, settings: dict, rep: Reporter) -> int:
    out = Path(args.out or settings.get("out_dir") or DEFAULT_OUT)
    if not out.is_dir():
        print(f"Папка с материалами не найдена: {out}")
        return 1
    ix = search.Index(out)
    cfg = search.normalize(settings.get("search"))
    first = not ix.exists()
    if args.search_index or args.search_reindex or first or (cfg["auto"] and not args.no_search_index):
        if first:
            print("Поискового индекса ещё нет — строю (это один раз, потом обновляется только изменённое)…")
        try:
            r = ix.update(cfg, full=bool(args.search_reindex))
        except KeyboardInterrupt:
            print("\nПрервано. Уже прочитанное сохранено, остальное доделается в следующий раз.")
            return 130
        except Exception as e:  # noqa: BLE001
            print(f"Индекс не обновлён: {describe_error(e)}")
            return 1
        if args.search_index or args.search_reindex or first or r["new"] or r["changed"] or r["removed"]:
            print(search_summary(r))
    if args.search_index or args.search_reindex:
        st = ix.status()
        print(search.format_status(st))
        if not st.get("pdf") and st.get("by_status", {}).get("nolib"):
            print("Чтобы искать и внутри PDF: pip install pypdfium2")
        return 0
    course = None
    if args.search_course:
        names = [c["name"] for c in ix.status(problems=0).get("courses", [])]
        want = args.search_course.casefold()
        hit = [n for n in names if n.casefold() == want] or [n for n in names if want in n.casefold()]
        if len(hit) != 1:
            print(("Курс «%s» не найден." % args.search_course) if not hit else
                  "Под «%s» подходит несколько курсов, уточните:\n  " % args.search_course + "\n  ".join(hit))
            return 1
        course = hit[0]
    res = ix.search(args.search, course=course, limit=max(1, args.search_limit))
    print(search.format_results(res))
    return 0 if res.get("ok") else 1


def cli_main(argv=None) -> int:
    ensure_console_utf8()
    load_env()
    p = argparse.ArgumentParser(prog="moodle_offline", description="Выгрузка учебных материалов из Moodle для оффлайн-доступа.")
    p.add_argument("--url", help=f"адрес Moodle (по умолчанию {DEFAULT_URL})")
    p.add_argument("--user", help="логин")
    p.add_argument("--cookie", help="значение cookie MoodleSession из браузера (вход без пароля)")
    p.add_argument("--out", help="папка для сохранения")
    p.add_argument("--mode", type=int, choices=(1, 2, 3),
                   help="1 — каталог с оглавлением, 2 — папки Курс/Раздел/Тип, 3 — свой шаблон (--dir-template, --file-template)")
    p.add_argument("--dir-template", help="шаблон папок для режима 3, например \"{section_no}. {section}/{kind}\" "
                                          "(поля: " + ", ".join("{" + k + "}" for k in dl_rules.PLACEHOLDERS) + ")")
    p.add_argument("--file-template", help="шаблон имени файла для режима 3, например \"{seq} {item}\"")
    p.add_argument("--courses", help="ID курсов через запятую (число из адреса course/view.php?id=…)")
    p.add_argument("--all", action="store_true", help="все курсы")
    p.add_argument("--sync", action="store_true", help="быстрая синхронизация: только новое, с сохранёнными настройками")
    p.add_argument("--dry-run", action="store_true", help="только показать структуру курсов, ничего не скачивать")
    p.add_argument("--list", action="store_true", help="показать список курсов и выйти")
    p.add_argument("--pdf", action=argparse.BooleanOptionalAction, default=None, help="дублировать страницы в PDF (нужен Edge/Chrome)")
    p.add_argument("--zip", action=argparse.BooleanOptionalAction, default=None, help="упаковать каждый курс в ZIP")
    p.add_argument("--index", action="store_true", help="в режиме 2 тоже создавать оглавление")
    p.add_argument("--original-names", action="store_true", help="называть файлы как на сервере, а не как элементы курса")
    p.add_argument("--page-delay", help="пауза между страницами, сек (по умолчанию 2-5)")
    p.add_argument("--file-delay", help="пауза между файлами, сек (по умолчанию 0.5-1.5)")
    p.add_argument("--hours", help="тихие часы: работать только в это время, например 01:00-07:00")
    p.add_argument("--retries", type=int, default=RETRIES)
    p.add_argument("--timeout", type=int, default=TIMEOUT)
    p.add_argument("--wait-server", action="store_true", help="ждать, пока Moodle станет доступен, и сразу начать")
    p.add_argument("--wait-minutes", type=int, default=None,
                   help=f"сколько ждать «оживания» сервера при 502/обрыве сети, мин (по умолчанию {WAIT_SERVER_MINUTES}, 0 — без ограничения)")
    p.add_argument("--use-proxy", action="store_true", help="ходить через системный прокси (по умолчанию — напрямую)")
    p.add_argument("--no-geo-check", action="store_true", help="не проверять страну IP-адреса")
    p.add_argument("--ignore-geo", action="store_true", help="продолжать даже при зарубежном IP")
    p.add_argument("--no-notify", action="store_true", help="без звука и всплывающих уведомлений")
    p.add_argument("--no-news", action="store_true", help="не искать «что нового» в этом запуске (ленту и уведомления не трогать)")
    p.add_argument("--news-list", action="store_true", help="показать непрочитанное из «Что нового» и выйти")
    p.add_argument("--news-read", action="store_true", help="пометить всё в «Что нового» прочитанным (можно вместе с --news-list)")
    p.add_argument("--notify-test", action="store_true", help="отправить тестовое уведомление по включённым каналам и выйти")
    p.add_argument("--deadlines", nargs="?", const=14, type=int, metavar="ДНЕЙ",
                   help="показать сроки сдачи и пары на N дней вперёд (по умолчанию 14) и выйти; без интернета и входа")
    p.add_argument("--calendar-sync", action="store_true", help="обновить только календарь Moodle (сроки и .ics), без скачивания материалов")
    p.add_argument("--no-calendar", action="store_true", help="не обновлять календарь сроков в этом запуске")
    p.add_argument("--search", metavar="ЗАПРОС", help="найти в скачанных материалах и выйти (слова — все сразу, \"точная фраза\", -исключить)")
    p.add_argument("--search-course", metavar="КУРС", help="искать только в этом курсе (часть названия папки курса)")
    p.add_argument("--search-limit", type=int, default=10, help="сколько файлов показать (по умолчанию 10)")
    p.add_argument("--search-index", action="store_true", help="обновить поисковый индекс материалов и выйти")
    p.add_argument("--search-reindex", action="store_true", help="построить поисковый индекс заново и выйти")
    p.add_argument("--no-search-index", action="store_true", help="не обновлять поисковый индекс после загрузки")
    p.add_argument("--watch", action="store_true",
                   help="слежение без окна: проверять Moodle по таймеру из настроек и (если включено) докачивать недостающее; Ctrl+C — стоп")
    p.add_argument("--no-wakelock", action="store_true", help="Android: не включать termux-wake-lock при слежении (телефон сможет засыпать)")
    p.add_argument("--watch-status", action="store_true", help="показать настройки таймера и итог последнего автообновления и выйти")
    p.add_argument("--auto-update", action="store_true",
                   help="один раз: сверить файлы с диском, докачать недостающее и новое по правилам «Автообновления» (курсы из настроек)")
    p.add_argument("--watch-max", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--save-html", action="store_true", help="сохранять исходный HTML страниц в _debug (для настройки селекторов)")
    p.add_argument("--forget", action="store_true", help="забыть сохранённый вход")
    p.add_argument("--import-data", metavar="ПАПКА",
                   help="перенести данные прошлой версии (папка со старым .exe или её `.moodle_offline`) и выйти")
    p.add_argument("--data-info", action="store_true", help="показать, где хранятся данные программы, и выйти")
    p.add_argument("--schedule", nargs="?", const="2weeks", choices=("week", "next", "2weeks", "month", "semester", "custom"),
                   help="сохранить расписание для офлайна: week, next, 2weeks (по умолчанию), month, semester, custom")
    p.add_argument("--from", dest="date_from", help="начало периода для --schedule custom, ГГГГ-ММ-ДД")
    p.add_argument("--to", dest="date_to", help="конец периода для --schedule custom, ГГГГ-ММ-ДД")
    p.add_argument("--group", help="группа для расписания, например ib-b-25-2-o")
    p.add_argument("-y", "--yes", action="store_true", help="не задавать вопросов")
    p.add_argument("-v", "--verbose", action="store_true", help="подробный вывод")
    args = p.parse_args(argv)

    if args.data_info:
        print(f"Режим: {PLAN.mode}\nДанные (вход, настройки, кэш): {DATA_DIR}\nМатериалы по умолчанию: {DEFAULT_OUT}\nПрограмма: {APP_DIR}")
        return 0
    if args.import_data:
        try:
            info = datadir.import_from(Path(args.import_data), datadir.Plan(PLAN.mode, APP_DIR, DATA_DIR, DEFAULT_OUT), overwrite=True)
        except ValueError as e:
            print(f"Ошибка: {e}")
            return 2
        print(f"Перенесено из {info['from']}: файлов {info['files']}.")
        if info["out_dir"]:
            print(f"Материалы остались на месте: {info['out_dir']}")
        return 0
    prepare_data_dir(ConsoleReporter(verbose=args.verbose, notify_on_recover=False))
    vault = Vault()
    settings = vault.load_settings()
    if args.watch_status:
        return cli_watch_status(settings)
    if args.forget:
        vault.forget()
        settings.pop("remember", None)
        vault.save_settings(settings)
        print("Сохранённый вход удалён.")
        return 0

    interactive = bool(sys.stdin and sys.stdin.isatty()) and not args.yes
    rep = ConsoleReporter(verbose=args.verbose, notify_on_recover=not args.no_notify)
    if args.schedule:   # только расписание — вход в Moodle не нужен
        import schedule as sched
        out = Path(args.out or settings.get("out_dir") or DEFAULT_OUT)
        rep.open_log(out / LOG_FILE)
        sec = Vault().load_secret()
        try:
            sched.run_cli(out, args.group or settings.get("schedule_group") or sched.DEFAULT_GROUP,
                          settings.get("schedule_sources") or sched.DEFAULT_SOURCES, args.schedule, args.date_from, args.date_to,
                          creds=(sec["username"], sec["password"]) if sec.get("username") and sec.get("password") else None,
                          cookies=sec.get("sso_cookies"),
                          pdf=bool(settings.get("schedule_pdf", True)), rep=rep, use_proxy=bool(settings.get("use_system_proxy")),
                          urls=settings.get("schedule_urls"),
                          page_delay=parse_delay(args.page_delay or settings.get("page_delay"), PAGE_DELAY))
        except (NetworkDown, Stopped, KeyboardInterrupt) as e:
            rep.log(f"Остановлено: {e}", "error")
            return 3
        return 0
    if args.news_list or args.news_read or args.notify_test:   # лента и уведомления — вход в Moodle не нужен
        out = Path(args.out or settings.get("out_dir") or DEFAULT_OUT)
        ncfg = news.normalize(settings.get("news"))
        if args.notify_test:
            ev = news.make_event("announcement", 0, "Проверка связи", "Тестовое уведомление",
                                 detail="Если вы это видите — уведомления настроены верно.", key=str(time.time()))
            r = send_news([ev], ncfg, vault, rep, toast=not args.no_notify, use_proxy=bool(settings.get("use_system_proxy")))
            sent = {k: v for k, v in r.items() if v}
            if not sent:
                print("Ни один канал не включён: включите Windows-окно, Telegram или webhook во вкладке «Что нового» → «Уведомления».")
                return 1
            for k, v in sent.items():
                print(f"{k}: {'✓' if v['ok'] else '✗'} {v['msg']}")
            return 0 if all(v["ok"] for v in sent.values()) else 1
        log = news.NewsLog(out / NEWS_FILE)
        if args.news_list:
            lst = log.listing(unread_only=True, limit=500)
            if not lst["events"]:
                print("Непрочитанного нет.")
            by = {}
            for e in lst["events"]:
                by.setdefault(e["course"], []).append(e)
            for c, evs in by.items():
                print(f"\n{c}")
                for e in evs:
                    print("  • " + news.event_line(e) + (f"\n      {e['url']}" if e.get("url") else ""))
        if args.news_read:
            print(f"\nОтмечено прочитанным: {log.mark_read()}")
        return 0
    if args.deadlines is not None:     # сроки по сохранённым данным — без интернета и без входа
        return cli_deadlines(args, settings, rep)
    if args.search or args.search_index or args.search_reindex:   # поиск работает без интернета и без входа
        return cli_search(args, settings, rep)
    if args.watch:                    # слежение без окна: свой цикл, вход только когда нужно обновить
        return cli_watch(args, settings, vault, rep)
    window = parse_hours(args.hours or settings.get("quiet_hours") or os.environ.get("MOODLE_HOURS"))
    control = Control(window)
    control.reporter = rep

    action = "sync" if args.sync else "dry" if args.dry_run else "list" if args.list else None
    if action is None and interactive and not (args.courses or args.all or args.auto_update):
        print("═" * 56)
        print(f"  {APP_NAME} {VERSION} — выгрузка материалов Moodle")
        print("═" * 56)
        print("  1 — Скачать / докачать материалы")
        print("  2 — Быстрая синхронизация (только новое)")
        print("  3 — Проверка: показать структуру курсов без скачивания")
        print("  4 — Забыть сохранённый вход")
        choice = ask("Выбор", "1")
        if choice == "4":
            vault.forget()
            settings.pop("remember", None)
            vault.save_settings(settings)
            print("Сохранённый вход удалён.")
            return 0
        action = {"2": "sync", "3": "dry"}.get(choice, "full")
    action = action or "full"

    base = args.url or os.environ.get("MOODLE_URL") or settings.get("base_url")
    if not base:
        base = ask("Адрес Moodle", DEFAULT_URL) if interactive else DEFAULT_URL
    use_proxy = args.use_proxy or bool(settings.get("use_system_proxy"))
    client = MoodleClient(base, timeout=args.timeout, retries=args.retries, use_system_proxy=use_proxy,
                          wait_minutes=args.wait_minutes if args.wait_minutes is not None
                          else int(settings.get("wait_minutes", WAIT_SERVER_MINUTES)),
                          page_delay=parse_delay(args.page_delay or settings.get("page_delay"), PAGE_DELAY),
                          file_delay=parse_delay(args.file_delay or settings.get("file_delay"), FILE_DELAY),
                          control=control, reporter=rep,
                          debug_dir=(Path(args.out or settings.get("out_dir") or DEFAULT_OUT) / "_debug") if args.save_html else None)
    settings["base_url"] = client.base
    if interactive:
        client.relogin_cb = lambda: (settings.get("username") or ask("Логин"),
                                     getpass.getpass("Сессия истекла. Пароль: "))

    if not args.no_geo_check and not settings.get("no_geo_check"):
        ip, cc, svc = check_ip_country(use_proxy)
        if cc and cc != "RU":
            rep.log("⚠ " + GEO_WARNING.format(ip=ip, cc=cc, host=client.host), "warn")
            if not args.ignore_geo:
                if not interactive:
                    if not args.no_notify:
                        notify(APP_NAME, f"Не начинаю: IP {ip} ({cc}) — похоже, включён VPN.", ok=False)
                    return 4
                if not ask_yes("Всё равно продолжить?", False):
                    return 4
        elif cc:
            rep.log(f"IP {ip} · {cc} — ок.", "debug")
        else:
            rep.log("Не удалось проверить страну IP (сервисы геолокации не ответили) — продолжаю.", "debug")

    try:
        if args.wait_server:
            st, _ = client.probe()
            if st == "down":
                rep.log(f"Moodle ({client.host}) недоступен. Жду, пока сервер поднимется (проверка раз в 30 с)…")
                while st == "down":
                    control.sleep(30)
                    st, _ = client.probe()
                rep.log("Moodle снова доступен! Начинаю загрузку.")
                if not args.no_notify:
                    notify(APP_NAME, "Moodle снова доступен! Начинаю загрузку.")
        cli_login(client, vault, settings, args, interactive, rep)
        if args.calendar_sync:
            return cli_calendar_sync(client, vault, settings, args, rep)
        if args.auto_update:
            r = auto_update(client, vault, settings, rep, control, toast_on=not args.no_notify)
            watch.record_result(DATA_DIR, r["ok"], r["text"], time.time())
            return 0 if r["ok"] else 3
        rep.log("Получаю список курсов…")
        courses = client.get_courses()
    except (LoginError, MoodleError, RetriesExhausted, NetworkDown) as e:
        rep.log(f"Ошибка: {e}", "error")
        if not args.no_notify and not interactive:
            notify(APP_NAME, f"Не удалось начать: {e}", ok=False)
        return 2
    except (Stopped, KeyboardInterrupt):
        print("\nПрервано.")
        return 130
    if not courses:
        rep.log("Курсы не найдены. Проверьте, что вход выполнен под нужным аккаунтом.", "error")
        return 2

    if args.list or action == "list":
        for i, c in enumerate(courses, 1):
            print(f"{i:3d}. {c.fullname}  (id {c.id}){'  [прошлый]' if c.past else ''}{'  [скрыт]' if c.hidden else ''}")
        return 0

    # ── выбор курсов ──
    selected = None
    if args.courses:
        ids = {int(x) for x in re.findall(r"\d+", args.courses)}
        selected = [c for c in courses if c.id in ids] + [Course(id=i) for i in ids - {c.id for c in courses}]
    elif args.all:
        selected = courses
    elif action == "sync" and settings.get("course_ids"):
        ids = set(settings["course_ids"])
        selected = [c for c in courses if c.id in ids]
    elif interactive:
        print(f"\nНайдено курсов: {len(courses)}")
        for i, c in enumerate(courses, 1):
            tag = "  · прошлый" if c.past else ("  · скрыт" if c.hidden else "")
            print(f"  {i:3d}. {c.fullname}{tag}")
        while selected is None:
            try:
                idx = parse_selection(ask("\nНомера курсов через запятую (например 1, 3, 5-7). Enter — все"), len(courses))
                selected = [courses[i] for i in idx]
            except ValueError:
                print("Не понял ввод. Пример: 1, 3, 5-7")
    else:
        selected = courses
    if not selected:
        rep.log("Не выбрано ни одного курса.", "error")
        return 1

    # ── режим, папка, PDF, ZIP ──
    mode = args.mode or (settings.get("mode") if action == "sync" else None)
    if not mode and (args.dir_template is not None or args.file_template is not None):
        mode = 3
    if not mode:
        if interactive:
            print("\nКак раскладывать файлы:")
            print("  1 — Каталог: файлы с префиксами (03_ЛЗ_02 …) + оглавление index.html и README.md")
            print("  2 — Папки: Курс / 03. Раздел / Лекции | ПЗ | ЛЗ / файлы")
            mode = 2 if ask("Выбор", str(settings.get("mode", 1))) == "2" else 1
        else:
            mode = settings.get("mode", 1)
    out = Path(args.out or (settings.get("out_dir") if action == "sync" or not interactive else "") or "")
    if not str(out) or str(out) == ".":
        out = Path(ask("Папка для сохранения", settings.get("out_dir") or str(DEFAULT_OUT))) if interactive \
            else Path(settings.get("out_dir") or DEFAULT_OUT)
    pdf = args.pdf if args.pdf is not None else settings.get("pdf")
    if pdf is None:
        pdf = ask_yes("Дублировать текстовые страницы и задания в PDF (через Edge/Chrome)?", False) if interactive and action != "sync" else False
    zp = args.zip if args.zip is not None else settings.get("zip")
    if zp is None:
        zp = ask_yes("Упаковывать каждый курс в ZIP-бэкап после загрузки?", False) if interactive and action != "sync" else False

    if args.dir_template is not None:
        settings["layout_dir"] = args.dir_template
    if args.file_template is not None:
        settings["layout_file"] = args.file_template
    if mode == 3:
        for tpl in (settings.get("layout_dir") or "", settings.get("layout_file") or ""):
            for msg in dl_rules.template_errors(tpl):
                rep.log(f"Шаблон: {msg}", "warn")
    for msg in dl_rules.validate(settings.get("rules")):
        rep.log(f"Правила: {msg}", "warn")
    settings.update({"mode": mode, "out_dir": str(out), "pdf": bool(pdf), "zip": bool(zp),
                     "course_ids": [c.id for c in selected]})
    vault.save_settings(settings)
    if settings.get("remember"):
        client.on_cookies_changed = lambda: vault.save_secret({**vault.load_secret(), "base_url": client.base,
                                                                "cookies": client.export_cookies()})

    opts = JobOptions(out_dir=out, mode=mode, sync=(action == "sync"), dry_run=(action == "dry"), pdf=bool(pdf),
                      zip=bool(zp), original_names=args.original_names, index_in_mode2=args.index,
                      rules=settings.get("rules"), dir_template=settings.get("layout_dir") or "",
                      file_template=settings.get("layout_file") or "",
                      news={**news.normalize(settings.get("news")), "enabled": False} if args.no_news else settings.get("news"),
                      calendar=settings.get("calendar"),
                      calendar_on=(not args.no_calendar and deadlines.normalize(settings.get("calendar"))["auto"]))
    rep.log(f"\nКурсов: {len(selected)} · режим {mode} · папка: {out}"
            + (f" · тихие часы {fmt_window(window)}" if window else ""))
    t0 = time.monotonic()
    res = run_job(client, selected, opts, rep, control)
    if settings.get("remember"):
        try:
            vault.save_secret({**vault.load_secret(), "base_url": client.base, "cookies": client.export_cookies()})
        except Exception:
            pass
    if opts.dry_run:
        return 0
    if action == "sync" and res.aborted is None:
        extra_sync(settings, vault, client, out, rep, control, notify_on=not args.no_notify)
    delivered = deliver_news(res.events, settings, vault, rep, toast_on=not args.no_notify) if res.events else {}
    if res.aborted != "stopped" and not args.no_search_index and search.normalize(settings.get("search"))["auto"]:
        try:
            search_pass(out, settings, rep)
        except KeyboardInterrupt:
            rep.log("Индексация для поиска прервана — доделается в следующий раз.", "warn")
    took = fmt_dur(time.monotonic() - t0)
    head = {"network": "Прервано: нет связи", "session": "Прервано: сессия истекла",
            "stopped": "Остановлено"}.get(res.aborted, "Готово")
    text = f"{head} за {took}. {summary_text(res, opts)}"
    rep.log("\n" + "═" * 56 + f"\n{text}\nПапка: {out}\n" + "═" * 56)
    if not args.no_notify and not (delivered.get("toast") and res.aborted is None):   # окно «Что нового» уже показано
        notify(APP_NAME, text, ok=res.aborted is None)
    return 0 if res.aborted is None else 3


if __name__ == "__main__":
    try:
        sys.exit(cli_main())
    except KeyboardInterrupt:
        print("\nПрервано.")
        sys.exit(130)

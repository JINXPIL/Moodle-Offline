# -*- coding: utf-8 -*-
"""
Расписание СевГУ для офлайна (часть Moodle Offline).

Источники:
  • ysturasp.ru — зеркало расписания СевГУ, открывается по неделям (?week=N) → можно сохранить любой период;
  • schedule.sevsu.ru и timetable.sevsu.ru — официальные сайты; их страницы открываются через Edge/Chrome
    в фоне (как в обычном браузере), сохраняются снимками «как на сайте» (HTML + PDF) и разбираются на пары.

Результат — папка «Расписание» в папке сохранения:
  index.html       — офлайн-расписание: неделя / месяц / список, фильтр подгрупп и типов, «сейчас идёт»;
  raspisanie.ics   — календарь для телефона / Google / Outlook;
  снимки/…         — исходные страницы сайтов;  data/…json — данные по источникам (копятся между запусками).
"""
from __future__ import annotations

import hashlib
import html as htmllib
import json
import os
import random
import re
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

import moodle_offline as mo
import electives as el

SCHEDULE_DIR = "Расписание"
DEFAULT_GROUP = "ib-b-25-2-o"
MSK = timezone(timedelta(hours=3))   # Севастополь/Москва: UTC+3 без перехода на летнее время

SOURCES = {
    "schedule": {
        "title": "schedule.sevsu.ru — официальное расписание группы (все потоки и подгруппы)",
        "short": "Группа (официальное)",
        "url": "https://schedule.sevsu.ru/groups/{group}",
        "site": "https://schedule.sevsu.ru/groups/{group}",
    },
    "timetable": {
        "title": "timetable.sevsu.ru — личное расписание студента (нужен вход СевГУ)",
        "short": "Личное (после входа)",
        "url": "https://timetable.sevsu.ru/timetablestudent",
        "site": "https://timetable.sevsu.ru/timetablestudent",
        "auth": True,
    },
    "ysturasp": {
        "title": "ysturasp.ru — зеркало расписания СевГУ (запасной источник)",
        "short": "Зеркало ysturasp",
        "url": "https://ysturasp.ru/schedule/sevsu?group={group}&week={week}",
        "current": "https://ysturasp.ru/schedule/sevsu?group={group}",
        "site": "https://ysturasp.ru/schedule/sevsu?group={group}",
    },
}
AUTO_RANGES = ("2weeks", "month", "semester")        # на сколько вперёд обновлять при быстрой синхронизации
SOURCE_ORDER = ["schedule", "timetable", "ysturasp"]      # порядок важен: при совпадении пар главнее первый
DEFAULT_SOURCES = ["schedule", "timetable"]

# Официальный сайт schedule.sevsu.ru сам берёт данные JSON-ом по неделям (/api/schedule) — читаем так же:
# быстро, точно и любую неделю семестра. Значения — из кода сайта (типы занятий, звонки).
SEVSU_BASE = "https://schedule.sevsu.ru"
SEVSU_API_V = "6.2"
SEVSU_TYPES = ["Лекция", "Лабораторная (ЛЗ)", "Практика (ПЗ)", "Зачёт", "Дифференцированный зачёт",
               "Консультация перед экзаменом", "Экзамен", "ВУД"]
BELLS = {1: ("08:30", "10:00"), 2: ("10:10", "11:40"), 3: ("11:50", "13:20"), 4: ("14:00", "15:30"),
         5: ("15:40", "17:10"), 6: ("17:20", "18:50"), 7: ("19:00", "20:30"), 8: ("20:40", "22:10")}
PDF_WEEKS_MAX = 6          # снимков «как на сайте» (PDF) — не больше стольких недель за раз: дальше хватает офлайн-страницы

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "j", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                      "u", "f", "h", "c", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def group_slug(group: str) -> str:
    """«ИБ/б-25-2-о» → «ib-b-25-2-o» (как в адресе сайта); код из адреса возвращается как есть."""
    g = (group or "").strip().lower()
    if re.fullmatch(r"[a-z0-9\-]+", g):
        return g
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", "".join(_TRANSLIT.get(ch, ch) for ch in g))).strip("-")


GROUPS_CACHE = mo.DATA_DIR / "sevsu_groups.json"   # список отделов и групп schedule.sevsu.ru (для выбора и офлайна)


def load_groups_cache() -> dict:
    try:
        with mo.fopen(GROUPS_CACHE, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("groups", {})
        return d
    except (OSError, ValueError):
        return {"departments": [], "groups": {}, "current": ""}


def save_groups_cache(d: dict):
    try:
        mo.fmkdir(GROUPS_CACHE.parent)
        mo.write_text_atomic(GROUPS_CACHE, json.dumps(d, ensure_ascii=False))
    except OSError:
        pass


def _norm_group(g: str) -> str:
    return re.sub(r"[\s/_\-]+", "", (g or "").lower()).replace("ё", "е")


def resolve_group(text: str) -> str:
    """Группа как угодно: «ИБ/б-25-2-о», «иб/б-25-2о», «ib-b-25-2-o» → код из адреса сайта (по списку СевГУ, если он есть)."""
    t = (text or "").strip()
    n = _norm_group(t)
    for gs in load_groups_cache().get("groups", {}).values():
        for g in gs:
            if n in (_norm_group(g.get("name")), _norm_group(g.get("slug"))):
                return g.get("slug") or group_slug(t)
    return group_slug(t)


def group_name(slug: str) -> str:
    for gs in load_groups_cache().get("groups", {}).values():
        for g in gs:
            if g.get("slug") == slug:
                return g.get("name", "")
    return ""


def fetch_departments(c, slug=DEFAULT_GROUP) -> tuple[list, str]:
    """Отделы (институты/факультеты) со страницы группы: они есть прямо в HTML сайта."""
    r = c.request("GET", f"{SEVSU_BASE}/groups/{slug}", kind="page")
    soup = BeautifulSoup(c._text(r), mo.PARSER)
    deps = [{"id": o.get("value", ""), "name": mo.clean_text(o.get_text())}
            for o in soup.select("#department-select option") if o.get("value")]
    m = re.search(r"\bdepartment\s*:\s*['\"](\d+)['\"]", c._text(r))
    return deps, (m.group(1) if m else "")


def fetch_groups(c, dept: str, section=0) -> list:
    r = c.request("GET", f"{SEVSU_BASE}/api/groups?" + urlencode({"v": SEVSU_API_V, "section": section, "department": dept}),
                  kind="ajax")
    d = json.loads(c._text(r))
    return [{"name": g.get("group_name", ""), "slug": g.get("group_slug", ""), "weeks": g.get("weeks") or []}
            for g in d.get("groups", [])] if d.get("ok") else []


def academic_week1(today: date | None = None, start_year: int | None = None) -> date:
    """Понедельник недели, в которую попало 1 сентября (так нумерует недели сайт СевГУ: «Неделя 1 (31.08-06.09)»)."""
    t = today or date.today()
    y = start_year or (t.year if t.month >= 8 else t.year - 1)
    d = date(y, 9, 1)
    return d - timedelta(days=d.weekday())


def sevsu_lesson(x: dict) -> dict:
    """Пара из ответа /api/schedule → формат программы."""
    n = int(x.get("n") or 0)
    st, en = BELLS.get(n, ("", ""))
    sg = str(x.get("subgroup") if x.get("subgroup") is not None else "0").strip()
    t = x.get("type")
    typ = SEVSU_TYPES[t] if isinstance(t, int) and 0 <= t < len(SEVSU_TYPES) else str(t or "")
    room = mo.clean_text(x.get("location") or "")
    if room.lower() in ("д", "дист", "до", "дистант"):
        room = "Дистанционно"
    le = {"date": str(x.get("date", ""))[:10], "start": st, "end": en, "pair": n,
          "subject": mo.clean_text(x.get("lesson") or ""), "type": typ, "teacher": mo.clean_text(x.get("teacher") or ""),
          "room": room, "address": "", "subgroup": sg if sg.isdigit() and sg != "0" else "",
          "stream": "" if sg.isdigit() else sg, "note": ""}
    le["kind"] = kind_of(typ)
    return le


def _fmt_iso_msk(s: str) -> str:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(MSK).strftime("%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        return s or ""


# ── личное расписание timetable.sevsu.ru: «2. Предмет. Лекц., Иванов И. И. (I-В-411) (группа / Уч.гр. поток)» ──
_DAY_HDR = re.compile(r"^(?:(понедельник|вторник|среда|четверг|пятница|суббота|воскресенье|сегодня|завтра|вчера)"
                      r"\s*[•·,\-–—|]?\s*)?(\d{1,2})\s+(январ|феврал|март|апрел|ма|июн|июл|август|сентябр|октябр|ноябр|декабр)"
                      r"[а-яё]*\.?(?:\s+(\d{4}))?(?:\s*г\.?)?(?:\s*[•·,\-–—|]?\s*(понедельник|вторник|среда|четверг|пятница|суббота|воскресенье))?$", re.I)
_PAIR_START = re.compile(r"^([1-8])\s*(?:[.)]|пара\b[.:,]?)\s*(.*)$", re.I)
_SUBG_MARK = re.compile(r"(?:^|\s)(I{1,3}|[1-3])\s*(?:-?я\s*)?(?:подгрупп\w*|п/г)\s*:?", re.I)
_TYPE_ABBR = re.compile(r"(?:^|[.,;]\s*)(лекц\w*\.?|лек\.|л\.|пз|пр\.?|практ\w*\.?|лз|лр|лаб\w*\.?|сем\w*\.?|"
                        r"зач[её]т\w*|экз\w*\.?|конс\w*\.?)\s*(?=[,;(]|$)", re.I)
_NO_LESSONS = re.compile(r"нет занятий|занятий нет|выходной", re.I)


def _norm_type(t: str) -> str:
    t = (t or "").lower().strip(" .")
    if re.match(r"лаб|лз|лр", t):
        return "Лабораторная (ЛЗ)"
    if re.match(r"пр|пз|практ", t):
        return "Практика (ПЗ)"
    if re.match(r"лек|л$", t):
        return "Лекция"
    if t.startswith("сем"):
        return "Семинар"
    if t.startswith("зач"):
        return "Зачёт"
    if t.startswith("экз"):
        return "Экзамен"
    if t.startswith("конс"):
        return "Консультация"
    return t


def _year_for(month: int, day: int, ref: date) -> date | None:
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((d - ref).days) < abs((best - ref).days):
            best = d
    return best


def _pair_part(text: str, own_group: str = "") -> dict:
    """«Физические основы защиты информации. ЛЗ, Серяк Е. С. (I-А-503) (ИБ/б-25-2-о / Уч.гр. ОТРИБ/б-25-1-о)»"""
    groups = re.findall(r"\(([^()]*)\)", text)
    main = mo.clean_text(re.sub(r"\([^()]*\)", " ", text))
    room = next((g.strip() for g in groups if "/" not in g and re.search(r"\d|дист|онлайн|^\s*д\s*$|спорт|стадион|бассейн", g, re.I)), "")
    if room.lower() in ("д", "дист"):
        room = "Дистанционно"
    stream = ""
    for g in groups:
        m = re.search(r"уч\.?\s*гр\.?\s*:?\s*([^/;,]+/[^/;,\s]+)", g, re.I)
        if m:
            stream = m.group(1).strip()
        elif "/" in g and not stream:
            cand = [c.strip() for c in g.split(" / ") if c.strip()]
            other = [c for c in cand if c.lower() != own_group.lower() and group_slug(c) != group_slug(own_group)]
            if other and len(cand) > 1:
                stream = other[-1]
    subject, typ, teacher = main, "", ""
    m = _TYPE_ABBR.search(main)
    if m:
        subject, typ, teacher = main[:m.start()], _norm_type(m.group(1)), main[m.end():]
    else:
        mt = re.search(r",\s*([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.?|"
                       r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)\s*$", main)
        if mt:
            subject, teacher = main[:mt.start()], mt.group(1)
    return {"subject": subject.strip(" .,;:"), "type": typ, "teacher": teacher.strip(" ,;:"), "room": room, "stream": stream}


def parse_pairs(html_text: str, ref: date | None = None, own_group: str = "") -> tuple[list[dict], dict]:
    """Личное расписание: дни-заголовки («Понедельник • 2 ноября») и пары «N. Предмет. Тип, Преподаватель (ауд.)»."""
    ref = ref or date.today()
    lines = _page_lines(html_text)
    info = {"week": None}
    m = re.search(r"(\d{1,2})\s*(?:-?я)?\s*недел|недел[яи]\s*№?\s*(\d{1,2})", " ".join(lines[:300]), re.I)
    if m:
        info["week"] = int(m.group(1) or m.group(2))
    lessons, cur_date, buf = [], None, None

    def flush():
        nonlocal buf
        if not buf or not cur_date:
            buf = None
            return
        n, text = buf
        tm = re.search(rf"{_TIME}\s*[-–—]\s*{_TIME}", text)
        st, en = BELLS.get(n, ("", ""))
        if tm:
            st, en = f"{int(tm.group(1)):02d}:{tm.group(2)}", f"{int(tm.group(3)):02d}:{tm.group(4)}"
            text = text[:tm.start()] + " " + text[tm.end():]
        marks = list(_SUBG_MARK.finditer(text))
        parts = []
        if marks:
            head = text[:marks[0].start()].strip(" .,;:")
            if len(head) > 3:
                parts.append(("", head))
            for i, mk in enumerate(marks):
                seg = text[mk.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
                sg = mk.group(1).upper()
                parts.append(({"I": "1", "II": "2", "III": "3"}.get(sg, sg), seg))
        else:
            parts.append(("", text))
        for sg, seg in parts:
            p = _pair_part(seg, own_group)
            if not p["subject"] or len(p["subject"]) < 2:
                continue
            le = {"date": cur_date, "start": st, "end": en, "pair": n, "subject": p["subject"], "type": p["type"],
                  "teacher": p["teacher"], "room": p["room"], "address": "", "subgroup": sg, "stream": p["stream"], "note": ""}
            le["kind"] = kind_of(le["type"])
            lessons.append(le)
        buf = None

    for ln in lines:
        s = ln.strip()
        low = s.lower()
        hd = _DAY_HDR.match(low)
        dm = _DATE_FULL.match(s)
        if hd or dm:
            flush()
            if hd:
                mon = next(v for k, v in MONTHS.items() if hd.group(3).lower().startswith(k))
                d = date(int(hd.group(4)), mon, int(hd.group(2))) if hd.group(4) else _year_for(mon, int(hd.group(2)), ref)
            else:
                d = date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1)))
            cur_date = d.isoformat() if d else None
            continue
        if _NO_LESSONS.search(low) and len(s) < 60:
            flush()
            continue
        pm = _PAIR_START.match(s)
        if pm and cur_date and (re.search(r"[а-яё]{3}", pm.group(2), re.I) or not pm.group(2)):
            flush()
            buf = (int(pm.group(1)), pm.group(2))
            continue
        if buf is not None and not _NOISE.search(s):
            buf = (buf[0], (buf[1] + " " + s).strip())
    flush()
    return lessons, info


WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
WD_SHORT = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}

_DATE_FULL = re.compile(r"^(?:[а-яё]+[,\s]+)?(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s*г\.?)?(?:[,\s]+[а-яё]+)?(?:\s*\((?:сегодня|завтра|вчера)\))?$", re.I)
_DATE_SHORT = re.compile(r"^(?:(понедельник|вторник|среда|четверг|пятница|суббота|воскресенье|пн|вт|ср|чт|пт|сб|вс)[,\s]+)"
                         r"(\d{1,2})\.(\d{1,2})\.?$", re.I)
_DATE_WORDS = re.compile(r"^(?:[а-яё]+[,\s]+)?(\d{1,2})\s+(январ|феврал|март|апрел|ма|июн|июл|август|сентябр|октябр|ноябр|декабр)"
                         r"[а-яё]*(?:\s+(\d{4}))?(?:[,\s]+[а-яё]+)?$", re.I)
_TIME = r"([01]?\d|2[0-3])[:.]([0-5]\d)"
_TIME_LINE = re.compile(rf"^(?:\d\s*пара[,:\s]*)?{_TIME}(?:\s*(?:[-–—]|до)?\s*{_TIME})?$", re.I)
_TYPE_PURE = re.compile(r"^(лекци[яи]|лекционное занятие|практика|практическое занятие|практическ\w+ работа|семинар\w*|"
                        r"лабораторн\w+ (?:работа|занятие)|лабораторная|зач[её]т\w*(?: с оценкой)?|дифференцированный зач[её]т|"
                        r"экзамен|консультаци[яи]|курсов\w+ (?:работа|проект)|контрольн\w+ (?:работа|точка)|"
                        r"самостоятельная работа|лк|пз|лз|лр|пр)\b\.?(\s*[(\[].*[)\]])?$", re.I)
_SUBGROUP = re.compile(r"подгрупп\w*\s*№?\s*(\d+)|(\d+)\s*(?:-?я\s*)?подгрупп|п/г\s*(\d+)", re.I)
_TEACHER = re.compile(r"^(?:(?:доц|проф|ст\.?\s*преп|преп|асс)\w*\.?\s+)?"
                      r"(?:[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+|"
                      r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.)$")
_ROOM = re.compile(r"·|\bауд\.?|\bкорп|\bкаб\.?|\b[IVX]{1,4}-[А-ЯA-Z]-?\d|\b[А-ЯA-Z]?-?\d{3,4}[а-я]?\b|ул\.|"
                   r"\bонлайн\b|дистанц|спортзал|стадион|бассейн", re.I)
_NOISE = re.compile(r"скинуть друзьям|telegram|телеграм|₽|попробовать|уведомлени|копировать|в избранное|загрузка|"
                    r"показать расписание|расписание актуально|поделиться|подписаться", re.I)


# ════════════════════════════════════════════════════════════════════════════
#  РАЗБОР СТРАНИЦЫ В СПИСОК ПАР (по тексту — не зависит от вёрстки)
# ════════════════════════════════════════════════════════════════════════════
def kind_of(lesson_type: str) -> str:
    t = (lesson_type or "").lower()
    if re.search(r"лаборатор|\bлз\b|\bлр\b", t):
        return "lab"
    if re.search(r"практи|семинар|\bпз\b|\bпр\b", t):
        return "practice"
    if re.search(r"лекци|\bлк\b", t):
        return "lecture"
    if re.search(r"экзамен|зач[её]т|контрольн|консультац", t):
        return "exam"
    return "other"


def _page_lines(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text or "", mo.PARSER)
    for t in soup(["script", "style", "noscript", "svg", "template", "head"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    lines = [mo.clean_text(x) for x in soup.get_text("\n").split("\n")]
    out = []
    for ln in lines:
        if not ln:
            continue
        # «I-В-411» + «· ул. Университетская, 33» (ссылка и текст рядом) — склеиваем обратно
        if out and (ln.startswith(("·", ",", "—")) or out[-1].endswith(("·", ","))):
            out[-1] = f"{out[-1]} {ln}".strip()
        else:
            out.append(ln)
    return out


def parse_lessons(html_text: str, source: str, year_hint: int | None = None) -> tuple[list[dict], dict]:
    """Ищет в тексте страницы даты, время пар и поля пар. → (пары, сведения о странице)."""
    lines = _page_lines(html_text)
    info = {"week": None}
    m = re.search(r"неделя\s*№?\s*(\d{1,2})\b", " ".join(lines[:400]), re.I)
    if m:
        info["week"] = int(m.group(1))
    year_hint = year_hint or date.today().year
    lessons, cur_date, cur = [], None, None

    def flush():
        nonlocal cur
        if cur and cur.get("subject") and cur.get("date"):
            lessons.append(cur)
        cur = None

    i = 0
    while i < len(lines):
        ln = lines[i]
        low = ln.lower().strip(" ,.")
        d = None
        m = _DATE_FULL.match(ln)
        if m:
            d = _mkdate(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        else:
            m = _DATE_SHORT.match(ln)
            if m:
                d = _mkdate(year_hint, int(m.group(3)), int(m.group(2)))
            else:
                m = _DATE_WORDS.match(ln)
                if m:
                    mon = next((v for k, v in MONTHS.items() if m.group(2).lower().startswith(k)), None)
                    if mon:
                        d = _mkdate(int(m.group(3) or year_hint), mon, int(m.group(1)))
        if d:
            flush()
            cur_date = d
            i += 1
            continue
        if low in WEEKDAYS:
            flush()
            i += 1
            continue
        tm = _TIME_LINE.match(ln)
        if tm:
            start = f"{int(tm.group(1)):02d}:{tm.group(2)}"
            end = f"{int(tm.group(3)):02d}:{tm.group(4)}" if tm.group(3) else ""
            used = 1
            if not end and i + 1 < len(lines):           # «10:10» и «11:40» в разных строках
                tm2 = re.fullmatch(_TIME, lines[i + 1])
                if tm2:
                    end, used = f"{int(tm2.group(1)):02d}:{tm2.group(2)}", 2
            flush()
            cur = {"date": cur_date, "start": start, "end": end, "subject": "", "type": "", "subgroup": "",
                   "room": "", "address": "", "teacher": "", "note": "", "src": [source]}
            i += used
            continue
        if cur is None or _NOISE.search(ln) or len(ln) > 200:
            i += 1
            continue
        sg = _SUBGROUP.search(ln)
        if _TYPE_PURE.match(ln) and not cur["type"]:
            cur["type"] = re.sub(r"\s*[(\[].*$", "", ln).strip()
            if sg:
                cur["subgroup"] = next(x for x in sg.groups() if x)
        elif not cur["subject"]:
            cur["subject"] = ln
        elif not cur["room"] and _ROOM.search(ln) and not _TEACHER.match(ln):
            room, _, addr = ln.partition("·")
            cur["room"], cur["address"] = room.strip(" ,"), addr.strip(" ,")
        elif not cur["teacher"] and _TEACHER.match(ln):
            cur["teacher"] = ln
        elif not cur["type"] and re.search(r"лекци|практи|лаборатор|семинар|зач[её]т|экзамен", ln, re.I) and len(ln) < 60:
            cur["type"] = re.sub(r"\s*[(\[].*$", "", ln).strip()
            if sg:
                cur["subgroup"] = next(x for x in sg.groups() if x)
        elif sg and not cur["subgroup"]:
            cur["subgroup"] = next(x for x in sg.groups() if x)
        else:
            cur["note"] = (cur["note"] + "; " + ln).strip("; ") if len(cur["note"]) < 200 else cur["note"]
        i += 1
    flush()
    for le in lessons:
        le["kind"] = kind_of(le["type"])
    return lessons, info


def _mkdate(y, m, d):
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def lesson_key(le: dict) -> str:
    subj = re.sub(r"[^а-яёa-z0-9]", "", le.get("subject", "").lower())[:40]
    return f"{le['date']}|{le['start']}|{subj}|{le.get('subgroup', '') or le.get('stream', '')}"


# ════════════════════════════════════════════════════════════════════════════
#  ЗАГРУЗКА СТРАНИЦ: обычный запрос, при необходимости — рендер в Edge/Chrome
# ════════════════════════════════════════════════════════════════════════════
def _browser_cmd(browser, prof, extra):
    return [str(browser), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--mute-audio", "--no-sandbox", f"--user-data-dir={prof}",
            f"--user-agent={mo.USER_AGENT}", "--lang=ru-RU", "--virtual-time-budget=15000",
            "--run-all-compositor-stages-before-draw", *extra]


def render_dom(browser, url: str, timeout=45) -> str | None:
    """Открывает страницу в фоновом Edge/Chrome (выполняется JavaScript, как в обычном браузере) и отдаёт итоговый HTML."""
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
    with tempfile.TemporaryDirectory(prefix="mo-dom-") as prof:
        try:
            proc = subprocess.Popen(_browser_cmd(browser, prof, ["--dump-dom", url]), stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, **kw)
            try:
                out, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                mo._kill_tree(proc)
                out, _ = proc.communicate(timeout=10)
        except OSError:
            return None
    text = (out or b"").decode("utf-8", "replace").strip()
    return text if "<" in text else None


def print_url_pdf(browser, url: str, pdf_path: Path, timeout=45) -> bool:
    """Снимок страницы «как на сайте» в PDF (рендер в фоновом браузере)."""
    tmp = pdf_path.with_name(pdf_path.name + ".tmp")
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
    with tempfile.TemporaryDirectory(prefix="mo-pdf-") as prof:
        try:
            proc = subprocess.Popen(_browser_cmd(browser, prof, ["--no-pdf-header-footer", "--print-to-pdf-no-header",
                                                                 f"--print-to-pdf={os.path.abspath(str(tmp))}", url]),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                mo._kill_tree(proc)
        except OSError:
            return False
    if mo.fexists(tmp) and mo.fsize(tmp) > 0:
        mo.freplace(tmp, pdf_path)
        return True
    mo.funlink(tmp)
    return False


def _with_base(html_text: str, url: str) -> str:
    """Чтобы картинки и стили снимка подтягивались с сайта, когда есть интернет."""
    p = urlparse(url)
    base = f'<base href="{urlunparse((p.scheme, p.netloc, "/", "", "", ""))}">'
    if re.search(r"<head[^>]*>", html_text, re.I):
        return re.sub(r"(<head[^>]*>)", r"\1" + base.replace("\\", "\\\\"), html_text, count=1, flags=re.I)
    return base + html_text


# ════════════════════════════════════════════════════════════════════════════
#  ПЕРИОДЫ
# ════════════════════════════════════════════════════════════════════════════
def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def semester_end(d: date) -> date:
    if d.month >= 8:
        return date(d.year + 1, 1, 31)      # осенний семестр + зимняя сессия
    if d.month == 1:
        return date(d.year, 1, 31)
    return date(d.year, 7, 15)              # весенний семестр + летняя сессия


def resolve_range(kind: str, start: str | None = None, end: str | None = None, today: date | None = None):
    today = today or date.today()
    mon = monday(today)
    if kind == "week":
        return mon, mon + timedelta(days=6)
    if kind == "next":
        return mon + timedelta(days=7), mon + timedelta(days=13)
    if kind == "2weeks":
        return mon, mon + timedelta(days=13)
    if kind == "month":
        return mon, mon + timedelta(days=34)
    if kind == "semester":
        return mon, semester_end(today)
    a = date.fromisoformat(start) if start else mon
    b = date.fromisoformat(end) if end else a + timedelta(days=6)
    if b < a:
        a, b = b, a
    return a, b


# ════════════════════════════════════════════════════════════════════════════
#  СБОР
# ════════════════════════════════════════════════════════════════════════════
class ScheduleJob:
    def __init__(self, out_dir: Path, group: str, sources: list[str], rep: mo.Reporter, control: mo.Control,
                 urls: dict | None = None, pdf=True, use_system_proxy=False, page_delay=mo.PAGE_DELAY,
                 creds=None, cookies=None):
        self.root = Path(out_dir) / SCHEDULE_DIR
        self.group = resolve_group((group or DEFAULT_GROUP).strip()) or DEFAULT_GROUP
        self.sources = [s for s in SOURCE_ORDER if s in sources]
        self.urls = {k: (urls or {}).get(k) or SOURCES[k]["url"] for k in SOURCES}
        self.rep, self.control = rep, control
        self.pdf = pdf
        self.browser = mo.find_browser()
        self.use_system_proxy = use_system_proxy
        self.page_delay = page_delay
        self.clients = {}
        self.stats = {"pages": 0, "lessons": 0, "snapshots": 0}
        self.browser_fail = 0
        self.creds = creds                  # (логин, пароль) учётной записи СевГУ — для личного расписания
        self.cookies = cookies or []        # сохранённые cookie единого входа (чтобы не вводить пароль каждый раз)
        self.cookies_out = None             # свежие cookie после входа — программа сохранит их в зашифрованное хранилище
        self.site_info = {}
        self.limit_note = ""      # «сайт выложил не весь период» — показываем в окне, а не только в журнале

    def _browser_result(self, ok):
        """Если фоновый браузер дважды подряд не справился — отключаем его до конца, чтобы не ждать зря."""
        self.browser_fail = 0 if ok else self.browser_fail + 1
        if self.browser_fail >= 2 and self.browser:
            self.browser = None
            self.rep.log("  фоновый браузер не отвечает — дальше без снимков PDF и рендера страниц", "warn")

    def client(self, url):
        host = urlparse(url).netloc
        if host not in self.clients:
            p = urlparse(url)
            self.clients[host] = mo.MoodleClient(f"{p.scheme}://{p.netloc}", page_delay=self.page_delay,
                                                 wait_minutes=15, control=self.control, reporter=self.rep,
                                                 use_system_proxy=self.use_system_proxy)
        return self.clients[host]

    def fetch(self, url) -> tuple[str | None, str]:
        """→ (html, способ). Сначала обычный запрос; если пар не видно — рендер в браузере."""
        html_text, how = None, "http"
        try:
            c = self.client(url)
            r = c.request("GET", url, kind="page")
            if r.status_code < 400:
                html_text = c._text(r)
            else:
                self.rep.log(f"  {urlparse(url).netloc} ответил {r.status_code}", "warn")
        except (mo.NetworkDown, mo.Stopped):
            raise
        except Exception as e:
            self.rep.log(f"  не открылось обычным запросом ({mo.describe_error(e)})", "debug")
        if (not html_text or not parse_lessons(html_text, "x")[0]) and self.browser:
            self.control.checkpoint()
            self.rep.log("  открываю страницу через браузер (сайт строит её скриптом)…", "debug")
            dom = render_dom(self.browser, url)
            self._browser_result(bool(dom))
            if dom:
                html_text, how = dom, "browser"
        self.stats["pages"] += 1
        return html_text, how

    def snapshot(self, source, label, url, html_text, pdf=True):
        d = self.root / "снимки" / source
        mo.fmkdir(d)
        name = mo.safe_filename(label)
        if html_text:
            mo.write_text_atomic(d / f"{name}.html", _with_base(html_text, url))
            self.stats["snapshots"] += 1
        if self.pdf and self.browser and pdf:
            self.control.checkpoint()
            ok = print_url_pdf(self.browser, url, d / f"{name}.pdf")
            self._browser_result(ok)
            if ok:
                self.stats["snapshots"] += 1

    def run(self, kind="2weeks", start=None, end=None) -> dict:
        a, b = resolve_range(kind, start, end)
        self.rep.log(f"Расписание группы {self.group}: {a:%d.%m.%Y} — {b:%d.%m.%Y}, источники: {', '.join(self.sources)}")
        mo.fmkdir(self.root)
        results = {}
        for src in self.sources:
            self.control.checkpoint()
            self.rep.log(f"━━━ {SOURCES[src]['title']} ━━━")
            try:
                if src == "schedule" and self.urls[src] == SOURCES[src]["url"]:
                    lessons = self._run_sevsu(a, b, kind)
                elif src == "timetable":
                    lessons = self._run_personal(a, b, kind)
                    if lessons is None:
                        results[src] = None
                        continue
                elif "{week}" in self.urls[src] or "{date}" in self.urls[src] or "{monday}" in self.urls[src]:
                    lessons = self._run_weekly(src, a, b, kind)
                else:
                    lessons = self._run_single(src, a, b)
                self._store(src, a, b, lessons)
                results[src] = len(lessons)
                self.rep.log(f"  ✓ пар найдено: {len(lessons)}")
            except (mo.NetworkDown, mo.Stopped, KeyboardInterrupt):
                raise
            except Exception as e:
                results[src] = None
                self.rep.log(f"  ✗ {SOURCES[src]['title']}: {mo.describe_error(e)}", "error")
        merged = self.write_outputs()
        self.stats["lessons"] = len(merged)
        if self.limit_note:
            self.rep.log("  " + self.limit_note, "warn")
        return {"range": [a.isoformat(), b.isoformat()], "per_source": results, "total": len(merged),
                "dir": SCHEDULE_DIR, "note": self.limit_note}

    def _url(self, src, week=None, mon: date | None = None):
        u = self.urls[src]
        return (u.replace("{group}", group_slug(self.group)).replace("{week}", str(week if week is not None else ""))
                 .replace("{date}", (mon or date.today()).isoformat()).replace("{monday}", (mon or date.today()).isoformat()))

    def _run_single(self, src, a, b):
        url = self._url(src)
        self.rep.log(f"  {url}")
        html_text, how = self.fetch(url)
        self.snapshot(src, f"{date.today():%Y-%m-%d}", url, html_text)
        lessons, _ = parse_lessons(html_text or "", src, a.year)
        if not lessons:
            self.rep.log("  пары на странице не распознаны — сохранён снимок страницы «как на сайте» (папка «снимки»)", "warn")
        return [le for le in lessons if a.isoformat() <= le["date"] <= b.isoformat()]

    def _run_weekly(self, src, a, b, kind):
        tmpl = self.urls[src]
        week_mode = "{week}" in tmpl
        n0, mon0 = None, monday(date.today())
        if week_mode:
            cur_tmpl = re.sub(r"([?&])week=\{week\}&?", r"\1", tmpl).rstrip("?&")   # тот же адрес без номера недели
            cur_url = cur_tmpl.replace("{group}", self.group)
            html0, _ = self.fetch(cur_url)
            ls0, info0 = parse_lessons(html0 or "", src)
            n0 = info0.get("week")
            if ls0:
                mon0 = monday(date.fromisoformat(min(le["date"] for le in ls0)))
            if n0 is None:
                n0 = (monday(date.today()) - monday(date(date.today().year if date.today().month >= 9 else date.today().year - 1, 9, 1))).days // 7 + 1
                self.rep.log(f"  номер текущей недели не найден на странице — считаю от 1 сентября: {n0}", "warn")
            self.rep.log(f"  текущая неделя: №{n0} (с {mon0:%d.%m})")
        out, empty_run = [], 0
        m = monday(a)
        while m <= b:
            self.control.checkpoint()
            week = n0 + (m - mon0).days // 7 if week_mode else None
            if week is not None and week < 1:
                m += timedelta(days=7)
                continue
            url = self._url(src, week, m)
            html_text, how = self.fetch(url)
            lessons, info = parse_lessons(html_text or "", src, m.year)
            if lessons and week_mode:   # самопроверка: та ли неделя пришла
                got = monday(date.fromisoformat(min(le["date"] for le in lessons)))
                if got != m:
                    shift = (m - got).days // 7
                    self.rep.log(f"  нумерация недель сместилась на {shift} — поправляю", "warn")
                    n0 += shift
                    url = self._url(src, week + shift, m)
                    html_text, how = self.fetch(url)
                    lessons, info = parse_lessons(html_text or "", src, m.year)
            label = f"{m:%Y-%m-%d}" + (f"_неделя_{week}" if week is not None else "")
            self.snapshot(src, label, url, html_text)
            wk = [le for le in lessons if a.isoformat() <= le["date"] <= b.isoformat()]
            self.rep.log(f"  неделя {m:%d.%m}–{m + timedelta(days=6):%d.%m}: пар {len(wk)}")
            out += wk
            empty_run = 0 if wk else empty_run + 1
            if kind == "semester" and empty_run >= 3 and out:
                self.rep.log("  три недели подряд без пар — похоже, семестр закончился")
                break
            m += timedelta(days=7)
        return out


    # ── schedule.sevsu.ru: JSON по неделям, как берёт его сам сайт ──
    def _run_sevsu(self, a, b, kind):
        page_url = self._url("schedule")
        self.rep.log(f"  {page_url}")
        c = self.client(page_url)
        r = c.request("GET", page_url, kind="page")
        t = c._text(r) if r.status_code < 400 else ""
        name = re.search(r"\bgroup\s*:\s*['\"]([^'\"]+)['\"]", t)
        if not name:
            self.rep.log("  страница группы без данных для запроса — читаю саму страницу (через браузер)", "warn")
            return self._run_single("schedule", a, b)
        name = name.group(1)
        ym = re.search(r"PERIOD_START_YEAR\s*=\s*(\d{4})", t)
        sm = re.search(r"SCHEDULE_SECTION\s*=\s*(\d+)", t)
        section = int(sm.group(1)) if sm else 0
        w1 = academic_week1(start_year=int(ym.group(1)) if ym else None)
        self.site_info.update(group_name=name, week1=w1.isoformat())
        weeks_on_site = None
        dm = re.search(r"\bdepartment\s*:\s*['\"](\d+)['\"]", t)
        if dm:   # какие недели уже есть на сайте — остальные не запрашиваем зря
            try:
                gs = fetch_groups(c, dm.group(1), section)
                cache = load_groups_cache()
                cache["groups"][dm.group(1)] = gs
                cache["current"] = cache.get("current") or dm.group(1)
                save_groups_cache(cache)
                me = next((g for g in gs if g["slug"] == self.group or g["name"] == name), None)
                if me and me.get("weeks"):
                    weeks_on_site = set(me["weeks"])
                    last_wk = max(weeks_on_site)
                    until = w1 + timedelta(days=7 * last_wk - 1)
                    self.site_info.update(published_week=last_wk, published_until=until.isoformat())
                    if until < b:
                        self.limit_note = (f"Сайт пока выложил расписание только до {until:%d.%m.%Y} ({last_wk}-я неделя), "
                                           f"дальше на нём пусто — запрашивать там нечего. Остальное появится, когда СевГУ его "
                                           f"выложит: нажмите «Сохранить» ещё раз позже (или включите автообновление «до конца семестра»).")
            except (mo.NetworkDown, mo.Stopped):
                raise
            except Exception:
                pass
        try:   # список потоков (учебных групп по выбору) — для фильтра «мои потоки» в офлайн-расписании
            rs = c.request("GET", f"{SEVSU_BASE}/api/subgroups?" + urlencode({"v": SEVSU_API_V, "group": name}),
                           kind="ajax", referer=page_url)
            sub = json.loads(c._text(rs))
            if sub.get("ok"):
                streams = [{"code": g, "lesson": x.get("lesson", "")} for x in sub.get("subgroups", [])
                           for g in x.get("subgroups", [])]
                mo.write_text_atomic(self.root / "data" / "streams.json", json.dumps(streams, ensure_ascii=False, indent=1))
        except (mo.NetworkDown, mo.Stopped):
            raise
        except Exception:
            pass
        out, empty_run, pdf_weeks = [], 0, []
        m = monday(a)
        while m <= b:
            self.control.checkpoint()
            week = (m - w1).days // 7 + 1
            if not 1 <= week <= 52:
                m += timedelta(days=7)
                continue
            if weeks_on_site is not None and week not in weeks_on_site:
                if week > max(weeks_on_site or {0}):
                    self.rep.log(f"  дальше {max(weeks_on_site)}-й недели расписание на сайте ещё не выложено")
                    break
                m += timedelta(days=7)
                continue
            url = f"{SEVSU_BASE}/api/schedule?" + urlencode({"v": SEVSU_API_V, "section": section, "group": name, "week": week})
            r = c.request("GET", url, kind="ajax", referer=page_url)
            self.stats["pages"] += 1
            try:
                d = json.loads(c._text(r))
            except ValueError:
                raise mo.MoodleError(f"сайт ответил не данными расписания (код {r.status_code})")
            if not d.get("ok"):
                self.rep.log(f"  неделя {week}: сайт сообщил «{d.get('error') or 'ошибка'}»", "warn")
                d["schedule"] = []
            if d.get("last_update"):
                self.site_info["changed"] = _fmt_iso_msk(d["last_update"])
            snap = self.root / "снимки" / "schedule"
            mo.fmkdir(snap)
            mo.write_text_atomic(snap / f"{m:%Y-%m-%d}_неделя_{week}.json", json.dumps(d, ensure_ascii=False, indent=1))
            ls = [sevsu_lesson(x) for x in d.get("schedule", []) if x.get("date")]
            wk = [le for le in ls if a.isoformat() <= le["date"] <= b.isoformat()]
            self.rep.log(f"  неделя {week} ({m:%d.%m}–{m + timedelta(days=6):%d.%m}): пар {len(wk)}")
            out += wk
            if len(pdf_weeks) < PDF_WEEKS_MAX:
                pdf_weeks.append((week, m))
            empty_run = 0 if wk else empty_run + 1
            if kind == "semester" and empty_run >= 3 and out:
                self.rep.log("  три недели подряд без пар — похоже, семестр закончился")
                break
            m += timedelta(days=7)
        if self.pdf and self.browser and pdf_weeks:
            self._sevsu_pdfs(page_url, pdf_weeks)
        return out

    def _sevsu_pdfs(self, page_url, weeks):
        """Снимки «как на сайте» (PDF) — сайт сам переключает недели в выпадающем списке."""
        import cdp
        d = self.root / "снимки" / "schedule"
        try:
            with cdp.Browser(self.browser, timeout=45, direct=not self.use_system_proxy) as br:
                br.goto(page_url, settle=6)
                for week, m in weeks:
                    self.control.checkpoint()
                    if br.try_eval(f"({cdp.JS_PICK})({int(week)})", ""):
                        br.wait_loaded(15)
                        br.settle(8)
                    if br.pdf(d / f"{m:%Y-%m-%d}_неделя_{week}.pdf"):
                        self.stats["snapshots"] += 1
                    self.control.sleep(random.uniform(1.0, 2.0))
            self._browser_result(True)
        except (mo.Stopped, KeyboardInterrupt):
            raise
        except Exception as e:
            self.rep.log(f"  снимки PDF не получились ({mo.describe_error(e)}) — данные расписания сохранены", "warn")
            self._browser_result(False)

    # ── timetable.sevsu.ru: личное расписание под учётной записью СевГУ ──
    def _run_personal(self, a, b, kind):
        url = self._url("timetable")
        self.rep.log(f"  {url}")
        if not self.creds and not self.cookies:
            self.rep.log("  личному расписанию нужен вход в учётную запись СевГУ (логин и пароль как в Moodle). "
                         "Войдите в программе с галочкой «Запомнить» — и источник заработает. Пока пропускаю.", "warn")
            return None
        portal = el.Portal(url, self.rep, self.control, creds=self.creds, cookies=self.cookies,
                           use_system_proxy=self.use_system_proxy, page_delay=self.page_delay)
        text, final = "", url
        try:
            r, text, soup = portal.get()
            final = r.url
            self.cookies_out = portal.cookies()
        except el.PortalError as e:
            if not self.browser or "вход не удался" in str(e):   # неверный пароль второй раз не вводим
                raise mo.MoodleError(f"личное расписание: {e}")
            self.rep.log(f"  обычный вход не прошёл ({e}) — пробую через фоновый браузер", "debug")
        w1 = academic_week1()
        out, need = [], []
        m = monday(a)
        while m <= b:
            need.append(((m - w1).days // 7 + 1, m))
            m += timedelta(days=7)
        ls, info = parse_pairs(text, monday(date.today()), self.group)
        got_weeks = set()
        if ls:
            self.snapshot("timetable", f"{date.today():%Y-%m-%d}_текущая", final, text, pdf=False)
            got = monday(date.fromisoformat(min(le["date"] for le in ls)))
            got_weeks.add(got)
            out += [le for le in ls if a.isoformat() <= le["date"] <= b.isoformat()]
            links = self._week_links(BeautifulSoup(text, mo.PARSER), final)
            for week, m in need:
                if m in got_weeks or week not in links:
                    continue
                self.control.checkpoint()
                r2 = portal.c.request("GET", links[week], kind="page", referer=final)
                t2 = portal.c._text(r2)
                l2, _ = parse_pairs(t2, m, self.group)
                self.snapshot("timetable", f"{m:%Y-%m-%d}_неделя_{week}", links[week], t2, pdf=False)
                if l2:
                    got_weeks.add(monday(date.fromisoformat(min(le["date"] for le in l2))))
                    out += [le for le in l2 if a.isoformat() <= le["date"] <= b.isoformat()]
                self.rep.log(f"  неделя {week} ({m:%d.%m}): пар {len(l2)}")
        missing = [(w, m) for w, m in need if m not in got_weeks]
        if missing and self.browser:
            out += self._personal_browser(url, missing, a, b, portal, need_all=not ls)
        elif not ls:
            self.snapshot("timetable", f"{date.today():%Y-%m-%d}", final, text, pdf=False)
            self.rep.log("  пары на странице не распознаны — сохранён снимок страницы (папка «снимки»). "
                         "Пришлите его автору — допишу разбор.", "warn")
        return out

    def _week_links(self, soup, page_url) -> dict:
        """Кнопки недель обычными ссылками: «7 8 9 … 21» с адресом вида ?week=N."""
        res = {}
        for a in soup.select("a[href]"):
            t = mo.clean_text(a.get_text())
            if re.fullmatch(r"\d{1,2}", t) and re.search(r"week|nedel|недел|\d", a["href"], re.I):
                res.setdefault(int(t), urljoin(page_url, a["href"]))
        return res

    def _personal_browser(self, url, weeks, a, b, portal, need_all=False):
        import cdp
        out, diag = [], []
        cookies = (portal.cookies() if portal else []) or self.cookies
        try:
            with cdp.open_authed(self.browser, url, self.creds, cookies, rep=self.rep, diag=diag,
                                 direct=not self.use_system_proxy) as br:
                self._browser_result(True)
                self.cookies_out = br.cookies()
                html0 = br.html()
                ls0, _ = parse_pairs(html0, monday(date.today()), self.group)
                cur = monday(date.fromisoformat(min(le["date"] for le in ls0))) if ls0 else None
                if ls0:
                    out += [le for le in ls0 if a.isoformat() <= le["date"] <= b.isoformat()]
                    self.snapshot("timetable", f"{cur:%Y-%m-%d}_текущая", br.url(), html0, pdf=False)
                    if self.pdf:
                        br.pdf(self.root / "снимки" / "timetable" / f"{cur:%Y-%m-%d}_текущая.pdf")
                shift = 0
                for week, m in weeks:
                    if m == cur:
                        continue
                    self.control.checkpoint()
                    if not br.try_eval(f"({cdp.JS_PICK})({int(week + shift)})", ""):
                        self.rep.log(f"  неделя {week}: на странице нет такой кнопки — пропускаю", "debug")
                        continue
                    br.wait_loaded(15)
                    br.settle(8)
                    h = br.html()
                    ls, _ = parse_pairs(h, m, self.group)
                    if ls:
                        g = monday(date.fromisoformat(min(le["date"] for le in ls)))
                        if g != m and shift == 0:
                            shift = (m - g).days // 7
                            self.rep.log(f"  нумерация недель на сайте сдвинута на {shift} — поправляю", "warn")
                            if br.try_eval(f"({cdp.JS_PICK})({int(week + shift)})", ""):
                                br.wait_loaded(15)
                                br.settle(8)
                                h = br.html()
                                ls, _ = parse_pairs(h, m, self.group)
                    label = f"{m:%Y-%m-%d}_неделя_{week + shift}"
                    self.snapshot("timetable", label, br.url(), h, pdf=False)
                    if self.pdf:
                        br.pdf(self.root / "снимки" / "timetable" / f"{label}.pdf")
                    wk = [le for le in ls if a.isoformat() <= le["date"] <= b.isoformat()]
                    self.rep.log(f"  неделя {week + shift} ({m:%d.%m}): пар {len(wk)}")
                    out += wk
                    self.control.sleep(random.uniform(*self.page_delay))
                if not out:
                    mo.fmkdir(mo.DATA_DIR)
                    mo.write_text_atomic(mo.DATA_DIR / "timetable_page.html", el._redact(html0))
                    mo.write_text_atomic(mo.DATA_DIR / "timetable_debug.txt", "\n".join(portal.diag + diag) + "\n")
                    self.rep.log("  пары не распознаны — сохранил страницу для разбора: "
                                 f"{mo.DATA_DIR / 'timetable_page.html'} (без паролей)", "warn")
        except cdp.CDPLoginError as e:
            raise mo.MoodleError(f"личное расписание: {e}")
        except cdp.CDPError as e:
            self._browser_result(False)
            self.rep.log(f"  фоновый браузер не справился: {e}", "warn")
        return out

    # ── хранение: данные копятся, свежий период заменяет старый ──
    def _store(self, src, a, b, lessons):
        p = self.root / "data" / f"{src}.json"
        old = {}
        try:
            with mo.fopen(p, "r", encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            pass
        keep = [le for le in old.get("lessons", []) if not (a.isoformat() <= le["date"] <= b.isoformat())]
        data = {"source": src, "group": self.group, "updated": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "lessons": sorted(keep + lessons, key=lambda x: (x["date"], x["start"]))}
        mo.write_text_atomic(p, json.dumps(data, ensure_ascii=False, indent=1))

    def write_outputs(self) -> list:
        merged = load_merged(self.root)
        meta_p = self.root / "data" / "meta.json"
        try:
            with mo.fopen(meta_p, "r", encoding="utf-8") as f:
                site = json.load(f)
        except (OSError, ValueError):
            site = {}
        site.update(self.site_info)
        mo.write_text_atomic(meta_p, json.dumps(site, ensure_ascii=False))
        try:
            with mo.fopen(self.root / "data" / "streams.json", "r", encoding="utf-8") as f:
                streams = json.load(f)
        except (OSError, ValueError):
            streams = []
        meta = {"group": site.get("group_name") or self.group, "generated": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "changed": site.get("changed", ""), "streams": streams,
                "published_until": site.get("published_until", ""),
                "sources": {k: SOURCES[k]["title"] for k in SOURCE_ORDER},
                "short": {k: SOURCES[k]["short"] for k in SOURCE_ORDER},
                "sites": {k: SOURCES[k]["site"].replace("{group}", group_slug(self.group)) for k in SOURCE_ORDER}}
        mo.write_text_atomic(self.root / "index.html", render_viewer(merged, meta))
        mo.write_text_atomic(self.root / "raspisanie.ics", render_ics(merged, self.group))
        mo.write_text_atomic(self.root / "schedule.json", json.dumps({"meta": meta, "lessons": merged}, ensure_ascii=False))
        return merged


def load_merged(root: Path) -> list:
    by_key = {}
    for src in SOURCE_ORDER:
        try:
            with mo.fopen(root / "data" / f"{src}.json", "r", encoding="utf-8") as f:
                ls = json.load(f).get("lessons", [])
        except (OSError, ValueError):
            continue
        for le in ls:
            k = lesson_key(le)
            if k in by_key:
                ex = by_key[k]
                for fld in ("end", "type", "room", "address", "teacher", "subgroup", "stream", "pair"):
                    if not ex.get(fld) and le.get(fld):
                        ex[fld] = le[fld]
                ex["src"] = sorted(set(ex.get("src", [])) | set(le.get("src", [src])))
            else:
                by_key[k] = dict(le, src=le.get("src", [src]))
    for le in by_key.values():
        le["kind"] = kind_of(le.get("type", ""))
    return sorted(by_key.values(), key=lambda x: (x["date"], x["start"], x.get("subgroup", "")))


# ════════════════════════════════════════════════════════════════════════════
#  ЭКСПОРТ: календарь .ics и офлайн-страница
# ════════════════════════════════════════════════════════════════════════════
def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    raw = line.encode("utf-8")
    if len(raw) <= 74:
        return line
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 73:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def render_ics(lessons: list, group: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Moodle Offline//Расписание СевГУ//RU", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:Расписание {group}", "X-WR-TIMEZONE:Europe/Moscow"]
    for le in lessons:
        try:
            d = date.fromisoformat(le["date"])
            h1, m1 = map(int, le["start"].split(":"))
            st = datetime(d.year, d.month, d.day, h1, m1, tzinfo=MSK)
            if le.get("end"):
                h2, m2 = map(int, le["end"].split(":"))
                en = datetime(d.year, d.month, d.day, h2, m2, tzinfo=MSK)
            else:
                en = st + timedelta(minutes=90)
        except (ValueError, KeyError):
            continue
        title = le["subject"] + (f" ({le['type']})" if le.get("type") else "")
        if le.get("subgroup"):
            title += f" · подгр. {le['subgroup']}"
        desc = "\n".join(x for x in (le.get("teacher"), le.get("note")) if x)
        loc = " · ".join(x for x in (le.get("room"), le.get("address")) if x)
        uid = hashlib.sha1(lesson_key(le).encode()).hexdigest()[:20] + "@moodle-offline"
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{now}",
                  f"DTSTART:{st.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}", f"DTEND:{en.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
                  _ics_fold("SUMMARY:" + _ics_escape(title))]
        if loc:
            lines.append(_ics_fold("LOCATION:" + _ics_escape(loc)))
        if desc:
            lines.append(_ics_fold("DESCRIPTION:" + _ics_escape(desc)))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def render_viewer(lessons: list, meta: dict) -> str:
    data = json.dumps({"meta": meta, "lessons": lessons}, ensure_ascii=False).replace("</", "<\\/")
    return VIEWER_HTML.replace("__DATA__", data).replace("__GROUP__", htmllib.escape(meta.get("group", "")))


VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Расписание __GROUP__</title>
<style>
:root{--accent:#2f6fde;--bg:#f3f5f8;--card:#fff;--line:rgba(16,24,40,.09);--text:#16181d;--muted:#5d6675;--faint:#8a93a3;
--lecture:#2f6fde;--practice:#0f9d8a;--lab:#e8710a;--exam:#d93a3a;--other:#7c4dff;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#17191d;--card:#22252b;--line:rgba(255,255,255,.08);--text:#e9ebef;--muted:#a3abb8;--faint:#7d8594;color-scheme:dark}}
:root[data-theme=dark]{--bg:#17191d;--card:#22252b;--line:rgba(255,255,255,.08);--text:#e9ebef;--muted:#a3abb8;--faint:#7d8594;color-scheme:dark}
*{box-sizing:border-box}html,body{max-width:100%;overflow-x:hidden}body{margin:0;font:14px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,Roboto,Arial,sans-serif;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
h1{font-size:18px;margin:0 8px 0 0}.muted{color:var(--muted);font-size:12.5px}
button,input,select{font:inherit;color:inherit}
.seg{display:inline-flex;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:3px;gap:2px}
.seg button{border:0;background:transparent;padding:6px 10px;border-radius:7px;cursor:pointer;color:var(--muted)}
.seg button.on{background:var(--accent);color:#fff}
.btn{height:34px;padding:0 12px;border-radius:9px;border:1px solid var(--line);background:var(--card);cursor:pointer}
.btn:hover{border-color:var(--accent)}
input[type=search]{height:34px;border-radius:9px;border:1px solid var(--line);background:var(--card);padding:0 10px;min-width:0;width:min(260px,100%)}
.chips{display:flex;gap:6px;flex-wrap:wrap}.chip{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:4px 10px;cursor:pointer;font-size:12.5px;display:flex;align-items:center;gap:6px}
.chip i{width:9px;height:9px;border-radius:50%;display:inline-block}.chip.off{opacity:.45}
main{padding:14px 16px 40px;max-width:1200px;margin:auto}
.now{background:color-mix(in srgb,var(--accent) 12%,var(--card));border:1px solid color-mix(in srgb,var(--accent) 35%,transparent);border-radius:12px;padding:10px 14px;margin-bottom:12px}
.weeknav{display:flex;align-items:center;gap:10px;margin:4px 0 12px}.weeknav b{font-size:16px}
.days{display:grid;gap:12px}
.day{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.day h3{margin:0;padding:10px 14px;font-size:14px;display:flex;justify-content:space-between;border-bottom:1px solid var(--line)}
.day.today{outline:2px solid var(--accent)}.day.empty{opacity:.6}
.les{display:grid;grid-template-columns:92px 1fr;gap:10px;padding:10px 14px;border-top:1px solid var(--line)}
.les:first-of-type{border-top:0}.les .t{font-variant-numeric:tabular-nums;font-weight:600}.les .t small{display:block;color:var(--muted);font-weight:400}
.les .s{font-weight:600}.les .m{color:var(--muted);font-size:12.5px}.les.past{opacity:.55}
.tag{display:inline-block;font-size:11px;font-weight:700;color:#fff;border-radius:6px;padding:1px 7px;margin-right:6px;vertical-align:1px}
.k-lecture{background:var(--lecture)}.k-practice{background:var(--practice)}.k-lab{background:var(--lab)}.k-exam{background:var(--exam)}.k-other{background:var(--other)}
.sg{font-size:11px;border:1px solid var(--line);border-radius:6px;padding:0 6px;margin-left:6px;color:var(--muted)}
.st{font-size:11px;background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent);border-radius:6px;padding:0 6px;margin-right:6px;font-weight:600}
.les.mine{background:color-mix(in srgb,var(--accent) 8%,transparent);box-shadow:inset 3px 0 0 var(--accent)}
.les.dim{opacity:.4}
details.streams{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px 10px;font-size:12.5px}
details.streams summary{cursor:pointer;color:var(--muted)}details.streams .box{padding:8px 0;display:grid;gap:6px;max-width:520px}
details.streams label{display:flex;gap:6px;align-items:flex-start}details.streams label small{color:var(--muted)}
.month{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px}.month .wd{font-size:12px;color:var(--muted);text-align:center}
.cell{min-width:0;overflow:hidden;min-height:96px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px;cursor:pointer;font-size:11.5px}
.cell.other{opacity:.4}.cell.today{outline:2px solid var(--accent)}.cell b{font-size:12px}
.cell .it{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-left:3px solid var(--line);padding-left:4px;margin-top:3px}
.empty-all{padding:60px 20px;text-align:center;color:var(--muted)}
footer{color:var(--faint);font-size:12px;text-align:center;padding:20px}
@media print{header{position:static}.btn,.seg,.chips,input{display:none}.day{break-inside:avoid}}
</style></head><body>
<header>
  <h1>Расписание __GROUP__</h1><span class="muted" id="gen"></span>
  <div class="seg" id="view"><button data-v="week" class="on">Неделя</button><button data-v="month">Месяц</button><button data-v="list">Список</button></div>
  <div class="seg hidden-x" id="who"><button data-w="me" class="on">Моё</button><button data-w="group">Вся группа</button></div>
  <div class="seg" id="sub"><button data-s="" class="on">Все подгруппы</button><button data-s="1">1</button><button data-s="2">2</button></div>
  <details class="streams" id="streams"><summary>Потоки (по выбору)</summary><div class="box">
    <div class="seg" id="smode"><button data-m="off">Все</button><button data-m="mark">Выделять мои</button><button data-m="hide">Только мои</button></div>
    <div id="slist"></div><small class="muted">Отметьте потоки, в которые вы записаны (как «Фильтр учебных групп» на сайте).</small></div></details>
  <input type="search" id="q" placeholder="Поиск: предмет, преподаватель, аудитория">
  <div class="chips" id="kinds"></div>
  <button class="btn" onclick="print()">Печать / PDF</button>
</header>
<main id="main"></main>
<footer id="foot"></footer>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const LS = {get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
            set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }};
const D = JSON.parse(document.getElementById("data").textContent), L = D.lessons, M = D.meta;
const h = new URLSearchParams(location.hash.slice(1));
if (h.get("theme")) document.documentElement.dataset.theme = h.get("theme");
if (h.get("accent")) document.documentElement.style.setProperty("--accent", h.get("accent"));
const KIND = {lecture:"Лекция", practice:"Практика", lab:"Лаб.", exam:"Зачёт/экзамен", other:"Другое"};
const WD = ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота","Воскресенье"];
const MN = ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"];
const MN1 = ["Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"];
const plural = n => (n % 10 === 1 && n % 100 !== 11) ? "пара" : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? "пары" : "пар";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const iso = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
const parse = s => { const [y,m,d] = s.split("-").map(Number); return new Date(y, m-1, d); };
const mondayOf = d => { const x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); x.setHours(0,0,0,0); return x; };
const add = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
let view = h.get("view") || LS.get("rs_view", "week"), sub = LS.get("rs_sub", ""), q = "";
let kinds = new Set(JSON.parse(LS.get("rs_kinds", "[]")));
const hasMe = L.some(l => (l.src || []).includes("timetable")), hasGroup = L.some(l => (l.src || []).some(s => s !== "timetable"));
let who = hasMe ? LS.get("rs_who", "me") : "group";
const streamList = [...new Map([...(M.streams || []).map(x => [x.code, x.lesson]), ...L.filter(l => l.stream).map(l => [l.stream, l.subject])]).entries()]
  .filter(([c]) => c).sort((a, b) => a[0].localeCompare(b[0]));
let mine = new Set(JSON.parse(LS.get("rs_streams", "[]"))), smode = LS.get("rs_smode", "off");
if (hasMe && !LS.get("rs_streams", "")) L.filter(l => l.stream && (l.src || []).includes("timetable")).forEach(l => mine.add(l.stream));
const today = new Date(); today.setHours(0,0,0,0);
let cursor = mondayOf(today);
if (L.length && !L.some(l => parse(l.date) >= cursor && parse(l.date) <= add(cursor, 6))) {
  const fut = L.find(l => parse(l.date) >= cursor); cursor = mondayOf(parse((fut || L[0]).date)); }
document.getElementById("gen").textContent = "сохранено " + (M.generated || "") + (M.changed ? " · на сайте изменено " + M.changed : "") + " · пар: " + L.length;
if (!(hasMe && hasGroup)) document.getElementById("who").style.display = "none";
if (!streamList.length) document.getElementById("streams").style.display = "none";
document.getElementById("slist").innerHTML = streamList.map(([c, t]) => `<label><input type="checkbox" data-c="${esc(c)}" ${mine.has(c) ? "checked" : ""}><span><b>${esc(c)}</b> <small>${esc(t)}</small></span></label>`).join("");
document.getElementById("slist").onchange = e => { const c = e.target.dataset.c; if (!c) return; e.target.checked ? mine.add(c) : mine.delete(c);
  LS.set("rs_streams", JSON.stringify([...mine])); render(); };
document.getElementById("foot").innerHTML = "Сохранено для офлайна — открывается и без интернета. Источники: " + Object.entries(M.sites || {}).map(([k, u]) => `<a href="${esc(u)}">${esc((M.sources||{})[k] || k)}</a>`).join(" · ");
const kc = document.getElementById("kinds");
kc.innerHTML = Object.entries(KIND).map(([k, t]) => `<span class="chip ${kinds.has(k) ? "off" : ""}" data-k="${k}"><i class="k-${k}"></i>${t}</span>`).join("");
kc.onclick = e => { const c = e.target.closest(".chip"); if (!c) return; const k = c.dataset.k; kinds.has(k) ? kinds.delete(k) : kinds.add(k);
  c.classList.toggle("off"); LS.set("rs_kinds", JSON.stringify([...kinds])); render(); };
const segs = (id, attr, val, cb) => { const el = document.getElementById(id); el.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset[attr] === val));
  el.onclick = e => { const b = e.target.closest("button"); if (!b) return; el.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); cb(b.dataset[attr]); }; };
segs("view", "v", view, v => { view = v; LS.set("rs_view", v); render(); });
segs("sub", "s", sub, s => { sub = s; LS.set("rs_sub", s); render(); });
segs("who", "w", who, w => { who = w; LS.set("rs_who", w); render(); });
segs("smode", "m", smode, m => { smode = m; LS.set("rs_smode", m); render(); });
document.getElementById("q").oninput = e => { q = e.target.value.trim().toLowerCase(); render(); };
const srcOk = l => { const s = l.src || []; if (!hasMe || !hasGroup) return true;
  return who === "me" ? s.includes("timetable") : s.some(x => x !== "timetable"); };
const streamOk = l => !l.stream || smode !== "hide" || mine.has(l.stream) || (who === "me" && hasMe);
const ok = l => srcOk(l) && streamOk(l) && (!sub || !l.subgroup || l.subgroup === sub) && !kinds.has(l.kind || "other") &&
  (!q || [l.subject, l.teacher, l.room, l.type, l.address, l.stream].join(" ").toLowerCase().includes(q));
function lessonHTML(l){
  const end = l.end ? `<small>${esc(l.end)}</small>` : "", past = parse(l.date) < today ? " past" : "";
  const mark = l.stream && smode === "mark" ? (mine.has(l.stream) ? " mine" : " dim") : "";
  return `<div class="les${past}${mark}"><div class="t">${esc(l.start)}${end}</div><div>
    <div class="s">${l.stream ? `<span class="st">${esc(l.stream)}</span>` : ""}<span class="tag k-${l.kind || "other"}">${esc(l.type || KIND[l.kind] || "")}</span>${esc(l.subject)}${l.subgroup ? `<span class="sg">подгр. ${esc(l.subgroup)}</span>` : ""}</div>
    <div class="m">${[l.room, l.address].filter(Boolean).map(esc).join(" · ")}${l.teacher ? " · " + esc(l.teacher) : ""}${l.note ? " · " + esc(l.note) : ""}</div></div></div>`;
}
function nowBanner(){
  const now = new Date(), t = now.toTimeString().slice(0,5), d = iso(now);
  const todays = L.filter(l => l.date === d && ok(l));
  const cur = todays.find(l => l.start <= t && (l.end || "23:59") > t), next = L.find(l => (l.date > d || (l.date === d && l.start > t)) && ok(l));
  if (!cur && !next) return "";
  return `<div class="now">${cur ? `<b>Сейчас:</b> ${esc(cur.subject)} (${esc(cur.type)}), ${esc(cur.room)} — до ${esc(cur.end)}<br>` : ""}${next ? `<b>Следующая:</b> ${esc(next.subject)} — ${next.date === d ? "сегодня" : parse(next.date).getDate() + " " + MN[parse(next.date).getMonth()]} в ${esc(next.start)}, ${esc(next.room)}` : ""}</div>`;
}
function renderWeek(){
  const end = add(cursor, 6);
  let html = nowBanner() + `<div class="weeknav"><button class="btn" id="prev">← Неделя</button><b>${cursor.getDate()} ${MN[cursor.getMonth()]} — ${end.getDate()} ${MN[end.getMonth()]} ${end.getFullYear()}</b>
    <button class="btn" id="next">Неделя →</button><button class="btn" id="tw">Сегодня</button></div><div class="days">`;
  for (let i = 0; i < 7; i++) {
    const d = add(cursor, i), ls = L.filter(l => l.date === iso(d) && ok(l));
    if (!ls.length && i > 4) continue;
    html += `<div class="day ${iso(d) === iso(today) ? "today" : ""} ${ls.length ? "" : "empty"}"><h3><span>${WD[i]}, ${d.getDate()} ${MN[d.getMonth()]}</span><span class="muted">${ls.length ? ls.length + " " + plural(ls.length) : "нет пар"}</span></h3>${ls.map(lessonHTML).join("")}</div>`;
  }
  return html + "</div>";
}
function renderMonth(){
  const first = new Date(cursor.getFullYear(), cursor.getMonth() + (cursor.getDate() > 22 ? 1 : 0), 1), start = mondayOf(first);
  let html = `<div class="weeknav"><button class="btn" id="mprev">← Месяц</button><b>${MN1[first.getMonth()]} ${first.getFullYear()}</b><button class="btn" id="mnext">Месяц →</button><button class="btn" id="tw">Сегодня</button></div>
    <div class="month">` + ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"].map(w => `<div class="wd">${w}</div>`).join("");
  for (let i = 0; i < 42; i++) {
    const d = add(start, i), ls = L.filter(l => l.date === iso(d) && ok(l));
    html += `<div class="cell ${d.getMonth() !== first.getMonth() ? "other" : ""} ${iso(d) === iso(today) ? "today" : ""}" data-d="${iso(d)}"><b>${d.getDate()}</b>` +
      ls.slice(0, 4).map(l => `<div class="it" title="${esc(l.start + " " + l.subject + (l.room ? ", " + l.room : ""))}" style="border-color:var(--${l.kind || "other"})">${esc(l.start)} ${esc(l.subject)}</div>`).join("") + (ls.length > 4 ? `<div class="muted">+${ls.length - 4}</div>` : "") + "</div>";
  }
  return html + "</div>";
}
function renderList(){
  const ls = L.filter(ok); if (!ls.length) return `<div class="empty-all">Ничего не найдено.</div>`;
  let html = "", lastD = "";
  html += '<div class="days">';
  for (const l of ls) {
    if (l.date !== lastD) { if (lastD) html += "</div>"; const d = parse(l.date); html += `<div class="day ${l.date === iso(today) ? "today" : ""}"><h3><span>${WD[(d.getDay()+6)%7]}, ${d.getDate()} ${MN[d.getMonth()]} ${d.getFullYear()}</span></h3>`; lastD = l.date; }
    html += lessonHTML(l);
  }
  return html + "</div></div>";
}
function render(){
  const m = document.getElementById("main");
  if (!L.length) { m.innerHTML = `<div class="empty-all">Расписание пока не сохранено.</div>`; return; }
  m.innerHTML = view === "month" ? renderMonth() : view === "list" ? renderList() : renderWeek();
  const on = (id, f) => { const b = document.getElementById(id); if (b) b.onclick = f; };
  on("prev", () => { cursor = add(cursor, -7); render(); }); on("next", () => { cursor = add(cursor, 7); render(); });
  on("tw", () => { cursor = mondayOf(today); render(); });
  on("mprev", () => { cursor = mondayOf(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 15)); render(); });
  on("mnext", () => { cursor = mondayOf(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 15)); render(); });
  m.querySelectorAll(".cell").forEach(c => c.onclick = () => { cursor = mondayOf(parse(c.dataset.d)); view = "week";
    document.querySelectorAll("#view button").forEach(b => b.classList.toggle("on", b.dataset.v === "week")); render(); });
}
render();
</script></body></html>"""


def run_cli(out_dir, group, sources, kind, start=None, end=None, pdf=True, rep=None, control=None, use_proxy=False,
            urls=None, page_delay=mo.PAGE_DELAY, creds=None, cookies=None):
    rep = rep or mo.ConsoleReporter()
    control = control or mo.Control()
    job = ScheduleJob(out_dir, group, sources, rep, control, urls=urls, pdf=pdf, use_system_proxy=use_proxy,
                      page_delay=page_delay, creds=creds, cookies=cookies)
    res = job.run(kind, start, end)
    rep.log(f"Расписание сохранено: {res['total']} пар → {Path(out_dir) / SCHEDULE_DIR / 'index.html'}")
    return res

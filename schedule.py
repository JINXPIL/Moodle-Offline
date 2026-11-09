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
import re
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup

import moodle_offline as mo

SCHEDULE_DIR = "Расписание"
DEFAULT_GROUP = "ib-b-25-2-o"
MSK = timezone(timedelta(hours=3))   # Севастополь/Москва: UTC+3 без перехода на летнее время

SOURCES = {
    "ysturasp": {
        "title": "ysturasp.ru — зеркало расписания СевГУ (любые недели)",
        "url": "https://ysturasp.ru/schedule/sevsu?group={group}&week={week}",
        "current": "https://ysturasp.ru/schedule/sevsu?group={group}",
        "site": "https://ysturasp.ru/schedule/sevsu?group={group}",
    },
    "schedule": {
        "title": "schedule.sevsu.ru — официальное расписание",
        "url": "https://schedule.sevsu.ru/groups/{group}",
        "site": "https://schedule.sevsu.ru/groups/{group}",
    },
    "timetable": {
        "title": "timetable.sevsu.ru — официальное расписание (старый сайт)",
        "url": "https://timetable.sevsu.ru/timetablestudent",
        "site": "https://timetable.sevsu.ru/timetablestudent",
    },
}
SOURCE_ORDER = ["ysturasp", "schedule", "timetable"]

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
WD_SHORT = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}

_DATE_FULL = re.compile(r"^(?:[а-яё]+[,\s]+)?(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s*г\.?)?(?:[,\s]+[а-яё]+)?$", re.I)
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
    return f"{le['date']}|{le['start']}|{subj}|{le.get('subgroup', '')}"


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
                 urls: dict | None = None, pdf=True, use_system_proxy=False, page_delay=mo.PAGE_DELAY):
        self.root = Path(out_dir) / SCHEDULE_DIR
        self.group = (group or DEFAULT_GROUP).strip()
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

    def snapshot(self, source, label, url, html_text):
        d = self.root / "снимки" / source
        mo.fmkdir(d)
        name = mo.safe_filename(label)
        if html_text:
            mo.write_text_atomic(d / f"{name}.html", _with_base(html_text, url))
            self.stats["snapshots"] += 1
        if self.pdf and self.browser:
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
                lessons = self._run_weekly(src, a, b, kind) if "{week}" in self.urls[src] or "{date}" in self.urls[src] \
                    or "{monday}" in self.urls[src] else self._run_single(src, a, b)
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
        return {"range": [a.isoformat(), b.isoformat()], "per_source": results, "total": len(merged),
                "dir": SCHEDULE_DIR}

    def _url(self, src, week=None, mon: date | None = None):
        u = self.urls[src]
        return (u.replace("{group}", self.group).replace("{week}", str(week if week is not None else ""))
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
        meta = {"group": self.group, "generated": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "sources": {k: SOURCES[k]["title"] for k in SOURCE_ORDER},
                "sites": {k: SOURCES[k]["site"].replace("{group}", self.group) for k in SOURCE_ORDER}}
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
                for fld in ("end", "type", "room", "address", "teacher", "subgroup"):
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
*{box-sizing:border-box}body{margin:0;font:14px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,Roboto,Arial,sans-serif;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
h1{font-size:18px;margin:0 8px 0 0}.muted{color:var(--muted);font-size:12.5px}
button,input,select{font:inherit;color:inherit}
.seg{display:inline-flex;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:3px;gap:2px}
.seg button{border:0;background:transparent;padding:6px 10px;border-radius:7px;cursor:pointer;color:var(--muted)}
.seg button.on{background:var(--accent);color:#fff}
.btn{height:34px;padding:0 12px;border-radius:9px;border:1px solid var(--line);background:var(--card);cursor:pointer}
.btn:hover{border-color:var(--accent)}
input[type=search]{height:34px;border-radius:9px;border:1px solid var(--line);background:var(--card);padding:0 10px;min-width:180px}
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
.month{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}.month .wd{font-size:12px;color:var(--muted);text-align:center}
.cell{min-height:96px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px;cursor:pointer;font-size:11.5px}
.cell.other{opacity:.4}.cell.today{outline:2px solid var(--accent)}.cell b{font-size:12px}
.cell .it{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-left:3px solid var(--line);padding-left:4px;margin-top:3px}
.empty-all{padding:60px 20px;text-align:center;color:var(--muted)}
footer{color:var(--faint);font-size:12px;text-align:center;padding:20px}
@media print{header{position:static}.btn,.seg,.chips,input{display:none}.day{break-inside:avoid}}
</style></head><body>
<header>
  <h1>Расписание __GROUP__</h1><span class="muted" id="gen"></span>
  <div class="seg" id="view"><button data-v="week" class="on">Неделя</button><button data-v="month">Месяц</button><button data-v="list">Список</button></div>
  <div class="seg" id="sub"><button data-s="" class="on">Все подгруппы</button><button data-s="1">1</button><button data-s="2">2</button></div>
  <input type="search" id="q" placeholder="Поиск: предмет, преподаватель, аудитория">
  <div class="chips" id="kinds"></div>
  <button class="btn" onclick="print()">Печать / PDF</button>
</header>
<main id="main"></main>
<footer id="foot"></footer>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
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
let view = localStorage.getItem("rs_view") || "week", sub = localStorage.getItem("rs_sub") || "", q = "";
let kinds = new Set(JSON.parse(localStorage.getItem("rs_kinds") || "[]"));
const today = new Date(); today.setHours(0,0,0,0);
let cursor = mondayOf(today);
if (L.length && !L.some(l => parse(l.date) >= cursor && parse(l.date) <= add(cursor, 6))) {
  const fut = L.find(l => parse(l.date) >= cursor); cursor = mondayOf(parse((fut || L[0]).date)); }
document.getElementById("gen").textContent = "сохранено " + (M.generated || "") + " · пар: " + L.length;
document.getElementById("foot").innerHTML = "Источники: " + Object.entries(M.sites || {}).map(([k, u]) => `<a href="${esc(u)}">${esc((M.sources||{})[k] || k)}</a>`).join(" · ");
const kc = document.getElementById("kinds");
kc.innerHTML = Object.entries(KIND).map(([k, t]) => `<span class="chip ${kinds.has(k) ? "off" : ""}" data-k="${k}"><i class="k-${k}"></i>${t}</span>`).join("");
kc.onclick = e => { const c = e.target.closest(".chip"); if (!c) return; const k = c.dataset.k; kinds.has(k) ? kinds.delete(k) : kinds.add(k);
  c.classList.toggle("off"); localStorage.setItem("rs_kinds", JSON.stringify([...kinds])); render(); };
const segs = (id, attr, val, cb) => { const el = document.getElementById(id); el.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset[attr] === val));
  el.onclick = e => { const b = e.target.closest("button"); if (!b) return; el.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); cb(b.dataset[attr]); }; };
segs("view", "v", view, v => { view = v; localStorage.setItem("rs_view", v); render(); });
segs("sub", "s", sub, s => { sub = s; localStorage.setItem("rs_sub", s); render(); });
document.getElementById("q").oninput = e => { q = e.target.value.trim().toLowerCase(); render(); };
const ok = l => (!sub || !l.subgroup || l.subgroup === sub) && !kinds.has(l.kind || "other") &&
  (!q || [l.subject, l.teacher, l.room, l.type, l.address].join(" ").toLowerCase().includes(q));
function lessonHTML(l){
  const end = l.end ? `<small>${esc(l.end)}</small>` : "", past = parse(l.date) < today ? " past" : "";
  return `<div class="les${past}"><div class="t">${esc(l.start)}${end}</div><div>
    <div class="s"><span class="tag k-${l.kind || "other"}">${esc(l.type || KIND[l.kind] || "")}</span>${esc(l.subject)}${l.subgroup ? `<span class="sg">подгр. ${esc(l.subgroup)}</span>` : ""}</div>
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
    <div class="month">` + WD.map(w => `<div class="wd">${w.slice(0,2)}</div>`).join("");
  for (let i = 0; i < 42; i++) {
    const d = add(start, i), ls = L.filter(l => l.date === iso(d) && ok(l));
    html += `<div class="cell ${d.getMonth() !== first.getMonth() ? "other" : ""} ${iso(d) === iso(today) ? "today" : ""}" data-d="${iso(d)}"><b>${d.getDate()}</b>` +
      ls.slice(0, 4).map(l => `<div class="it" style="border-color:var(--${l.kind || "other"})">${esc(l.start)} ${esc(l.subject)}</div>`).join("") + (ls.length > 4 ? `<div class="muted">+${ls.length - 4}</div>` : "") + "</div>";
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
            urls=None, page_delay=mo.PAGE_DELAY):
    rep = rep or mo.ConsoleReporter()
    control = control or mo.Control()
    job = ScheduleJob(out_dir, group, sources, rep, control, urls=urls, pdf=pdf, use_system_proxy=use_proxy,
                      page_delay=page_delay)
    res = job.run(kind, start, end)
    rep.log(f"Расписание сохранено: {res['total']} пар → {Path(out_dir) / SCHEDULE_DIR / 'index.html'}")
    return res

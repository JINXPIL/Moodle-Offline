# -*- coding: utf-8 -*-
"""Календарь и дедлайны: сроки сдачи из календаря Moodle + пары расписания → повестка, общий .ics, напоминания.

Модуль без сторонних библиотек (requests/bs4 не нужны). Здесь нет обращений к Moodle:
события скачивает moodle_offline.py (MoodleClient.get_calendar_events) и отдаёт сюда как есть (JSON от Moodle).

Что хранится (в папке с материалами):
  .moodle_deadlines.json            — все известные сроки, отметки «сделано», что уже напоминали;
  Календарь/moodle_calendar.ics     — сроки + пары расписания, с напоминаниями (для телефона, Google, Outlook);
  Календарь/index.html              — офлайн-страница «что и когда», открывается без программы и без интернета.

Как понимаем, что задание сдано: когда вы сдаёте работу, Moodle сам убирает срок из календаря.
Если срок пропал, а время ещё не вышло, программа помечает его «убрано с сайта» (скорее всего, сдано) и перестаёт напоминать.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import news    # здесь только plural() и make_event(); Moodle-кода в нём нет

DEADLINES_FILE = ".moodle_deadlines.json"
CALENDAR_DIR = "Календарь"
ICS_FILE = "moodle_calendar.ics"
PAGE_FILE = "index.html"
SCHEDULE_JSON = ("Расписание", "schedule.json")      # пары, которые сохраняет schedule.py
MSK = timezone(timedelta(hours=3))                    # Севастополь/Москва: UTC+3 без перехода на летнее время
MOVED_MIN = 60                                        # сдвиг срока меньше минуты не считаем переносом
KEEP_DAYS = 180                                       # сроки старше этого забываем
PRODID = "-//Moodle Offline//Календарь СевГУ//RU"

# вид событий → (название, подсказка, брать по умолчанию)
GROUPS = {
    "deadline": ("Сроки сдачи", "задания, тесты и другие элементы со сроком: «сдать до», «закрывается»", True),
    "events": ("События календаря", "ваши записи и события, которые добавили преподаватели или университет", True),
    "open": ("Открытие", "когда задание или тест открывается для выполнения", False),
    "completion": ("Ожидаемое завершение", "срок завершения элемента по настройкам курса (отметка прогресса)", False),
    "sessions": ("Занятия из «Посещаемости»", "время занятий, которые преподаватель завёл в журнале посещаемости (их на сайте много, срока сдачи у них нет)", False),
}
DEFAULTS = {
    "auto": True,                          # обновлять календарь после каждого скачивания
    "kinds": [k for k, v in GROUPS.items() if v[2]],
    "days_ahead": 120,                     # на сколько дней вперёд спрашивать у Moodle
    "days_back": 14,                       # просроченное показываем, пока ему не больше стольких дней
    "exclude_courses": [],                 # id курсов, сроки которых не нужны
    "lessons": True,                       # пары расписания — в общий .ics и в повестку
    "remind": [1440, 180],                 # за сколько минут до срока напоминать (.ics и уведомления программы)
    "lesson_remind": 15,                   # за сколько минут до пары напоминать в .ics; 0 — не надо
    "notify": True,                        # сообщать в «Что нового» о новых, перенесённых и горящих сроках
    "hide_done": True,                     # не показывать то, что отмечено «сделано»
}
SHOW = ("all", "deadlines", "lessons")

ETYPE_GROUP = {"due": "deadline", "close": "deadline", "open": "open", "expectcompletionon": "completion",
               "course": "events", "category": "events", "site": "events", "group": "events", "user": "events",
               "gradingdue": None}                  # «пора проверять» — это для преподавателя
ETYPE_LABEL = {"due": "срок сдачи", "close": "закрывается", "open": "открывается", "expectcompletionon": "ожидаемое завершение",
               "course": "событие курса", "category": "событие", "site": "событие университета", "group": "событие группы",
               "user": "личное событие"}
ICON = {"deadline": "⏰", "events": "📌", "open": "🔓", "completion": "☑️", "sessions": "👥"}
SESSION_MODULES = ("attendance",)                   # элементы курса, чьи события — время занятия, а не срок
SUFFIXES = re.compile(r"\s*[-–—:]?\s*\b(должен быть выполнен|должна быть выполнена|должно быть выполнено|срок сдачи|срок выполнения|"
                      r"закрывается|открывается|is due|closes|opens|due)\s*$", re.I)
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


# ════════════════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ════════════════════════════════════════════════════════════════════════════
def normalize(cfg) -> dict:
    """Полный набор настроек: чего нет — по умолчанию, неверные типы и значения вне границ поправляем."""
    cfg = cfg if isinstance(cfg, dict) else {}
    out = json.loads(json.dumps(DEFAULTS))
    for k in ("auto", "lessons", "notify", "hide_done"):
        if k in cfg:
            out[k] = bool(cfg[k])
    if isinstance(cfg.get("kinds"), (list, tuple)):
        out["kinds"] = [k for k in GROUPS if k in cfg["kinds"]]
    for k, lo, hi in (("days_ahead", 7, 365), ("days_back", 0, 90), ("lesson_remind", 0, 1440)):
        try:
            out[k] = min(hi, max(lo, int(cfg.get(k, DEFAULTS[k]))))
        except (TypeError, ValueError):
            pass
    if "remind" in cfg:
        raw = cfg["remind"]
        if isinstance(raw, str):
            raw = re.split(r"[\s,;]+", raw)
        vals = set()
        for x in raw if isinstance(raw, (list, tuple)) else []:
            try:
                m = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= m <= 43200:
                vals.add(m)
        out["remind"] = sorted(vals, reverse=True)[:4]
    ex = set()
    for x in cfg.get("exclude_courses") or []:
        try:
            ex.add(int(x))
        except (TypeError, ValueError):
            pass
    out["exclude_courses"] = sorted(ex)
    return out


def groups_meta() -> list[dict]:
    return [{"key": k, "title": v[0], "hint": v[1], "default": v[2]} for k, v in GROUPS.items()]


# ════════════════════════════════════════════════════════════════════════════
#  ВРЕМЯ И ТЕКСТЫ
# ════════════════════════════════════════════════════════════════════════════
def msk(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, MSK)


def fmt_dt(ts: float, now: float | None = None) -> str:
    """«пт 14 ноя, 23:59» (с годом, если он не текущий)."""
    d = msk(ts)
    year = "" if now is None or msk(now).year == d.year else f" {d.year}"
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day} {MONTHS_SHORT[d.month - 1]}{year}, {d:%H:%M}"


def fmt_day(d: date, today: date) -> str:
    rel = {0: "Сегодня", 1: "Завтра", -1: "Вчера"}.get((d - today).days)
    base = f"{WEEKDAYS[d.weekday()].capitalize()}, {d.day} {MONTHS[d.month - 1]}"
    return f"{rel} · {base}" if rel else base


def left_text(due: float, now: float) -> str:
    """«через 5 ч» / «просрочено на 2 дня»."""
    delta = due - now
    minutes = int(abs(delta) // 60)
    if minutes < 1:
        return "сейчас" if delta >= 0 else "только что истёк"
    if minutes < 60:
        span = f"{minutes} мин"
    else:
        hours = minutes // 60
        if hours < 48:
            span = f"{hours} ч" + (f" {minutes % 60} мин" if hours < 6 and minutes % 60 else "")
        else:
            days = hours // 24
            span = f"{days} {news.plural(days, 'день', 'дня', 'дней')}"
    return f"через {span}" if delta >= 0 else f"просрочено на {span}"


def _clean(s) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _plain(s, n=400) -> str:
    """HTML из описания события → чистый текст."""
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", " ", t)
    t = htmllib.unescape(re.sub(r"<[^>]+>", "", t))
    t = _clean(t)
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _safe_url(u) -> str:
    """Только http(s), без sesskey (он не должен попадать ни в файлы, ни в уведомления)."""
    u = str(u or "").strip()
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() != "sesskey"]
    return urlunparse(p._replace(query=urlencode(q)))


# ════════════════════════════════════════════════════════════════════════════
#  СОБЫТИЕ MOODLE → НАША ЗАПИСЬ
# ════════════════════════════════════════════════════════════════════════════
def group_of(etype: str, module: str = "") -> str | None:
    """Вид события для настроек; None — событие не нужно студенту (проверка работ — для преподавателя)."""
    et = (etype or "").lower()
    if et == "attendance" or (module or "").lower() in SESSION_MODULES:
        return "sessions"           # «Посещаемость (Группа …)» — занятие по журналу, а не срок сдачи
    if et in ETYPE_GROUP:
        return ETYPE_GROUP[et]
    return "deadline" if module else "events"


def from_ajax(ev) -> dict | None:
    """Событие из ответа AJAX Moodle (core_calendar_*) → запись. Нет id или времени — None."""
    if not isinstance(ev, dict):
        return None
    eid = ev.get("id")
    try:
        due = int(ev.get("timestart") or ev.get("timesort") or 0)
    except (TypeError, ValueError):
        return None
    if eid in (None, "") or due <= 0:
        return None
    etype = str(ev.get("eventtype") or ev.get("normalisedeventtype") or "").lower()
    module = str(ev.get("modulename") or "")
    group = group_of(etype, module)
    if group is None:
        return None
    course = ev.get("course") if isinstance(ev.get("course"), dict) else {}
    try:
        cid = int(course.get("id") or ev.get("courseid") or 0)
    except (TypeError, ValueError):
        cid = 0
    action = ev.get("action") if isinstance(ev.get("action"), dict) else {}
    name = _clean(ev.get("name"))
    title = _clean(ev.get("activityname")) or SUFFIXES.sub("", name) or name
    try:
        dur = max(0, int(ev.get("timeduration") or 0))
    except (TypeError, ValueError):
        dur = 0
    return {
        "id": f"m{eid}", "title": title[:200], "name": name[:200], "course_id": cid,
        "course": _clean(course.get("fullname"))[:200], "cshort": _clean(course.get("shortname"))[:60],
        "module": module, "etype": etype, "kind": group,
        "label": "занятие" if group == "sessions" else (_clean(ev.get("normalisedeventtypetext")).lower() or ETYPE_LABEL.get(etype, "событие")),
        "due": due, "end": due + dur if dur else 0,
        "url": _safe_url(action.get("url") or ev.get("viewurl") or ev.get("url")),
        "action": _clean(action.get("name"))[:80], "desc": _plain(ev.get("description")),
        "where": _plain(ev.get("location"), 200),
    }


def parse_events(raw) -> list[dict]:
    """Список «сырых» событий Moodle → записи (без повторов по id)."""
    out, seen = [], set()
    for ev in raw or []:
        e = from_ajax(ev)
        if e and e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    return out


# ════════════════════════════════════════════════════════════════════════════
#  ХРАНИЛИЩЕ
# ════════════════════════════════════════════════════════════════════════════
def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:       # Windows: файл открыт антивирусом или другой программой
            time.sleep(0.2 * (i + 1))
    os.replace(tmp, path)


class Store:
    """Файл .moodle_deadlines.json. Перечитывает его, если файл изменила другая программа (консольный запуск)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._mtime = None
        self.data = self._empty()
        self._load()

    @staticmethod
    def _empty() -> dict:
        return {"version": 1, "events": {}, "done": {}, "notified": {},
                "sync": {"last": 0, "ok": False, "source": "", "note": "", "count": 0}}

    def _stat(self):
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def _load(self):
        self._mtime = self._stat()
        self.data = self._empty()
        if self._mtime is None:
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                for k in ("events", "done", "notified", "sync"):
                    if isinstance(d.get(k), dict):
                        self.data[k].update(d[k]) if k == "sync" else self.data[k].update(d[k])
        except (OSError, ValueError):
            try:
                self.path.replace(self.path.with_name(self.path.name + ".bad"))
            except OSError:
                pass

    def refresh(self):
        with self.lock:
            if self._stat() != self._mtime:
                self._load()

    def save(self):
        with self.lock:
            _atomic_write(self.path, json.dumps(self.data, ensure_ascii=False))
            self._mtime = self._stat()

    # ── чтение ──
    @property
    def events(self) -> dict:
        self.refresh()
        return self.data["events"]

    @property
    def done(self) -> dict:
        return self.data["done"]

    @property
    def sync(self) -> dict:
        self.refresh()
        return self.data["sync"]

    def first_sync(self) -> bool:
        return not self.sync.get("last")

    # ── запись ──
    def set_done(self, eid: str, done: bool, now: float | None = None) -> bool:
        with self.lock:
            self.refresh()
            if eid not in self.data["events"]:
                return False
            if done:
                self.data["done"][eid] = int(now or time.time())
            else:
                self.data["done"].pop(eid, None)
            self.save()
            return True

    def merge(self, fetched: list[dict], now: float, lo: float, hi: float, *, complete=True, source="", note="",
              kinds=None) -> dict:
        """Вливает свежие события. lo..hi — период, за который спрашивали Moodle; complete — получили всё без обрывов;
        kinds — какие виды событий спрашивали (остальные «пропавшими» не считаем: их просто не запрашивали).
        → {"new": [...], "moved": [(старая дата, запись)], "gone": [...], "back": [...], "suspicious": bool}"""
        with self.lock:
            self.refresh()
            evs = self.data["events"]
            res = {"new": [], "moved": [], "gone": [], "back": [], "suspicious": False}
            seen = set()
            for e in fetched:
                seen.add(e["id"])
                old = evs.get(e["id"])
                if not old:
                    evs[e["id"]] = dict(e, first=int(now), seen=int(now), gone=0)
                    res["new"].append(evs[e["id"]])
                    continue
                if old.get("gone"):
                    res["back"].append(old)
                moved_from = old["due"] if abs(old["due"] - e["due"]) >= MOVED_MIN else 0
                evs[e["id"]] = dict(e, first=old.get("first", int(now)), seen=int(now), gone=0)
                if moved_from:
                    res["moved"].append((moved_from, evs[e["id"]]))
            if complete:
                vanished = [e for k, e in evs.items() if k not in seen and not e.get("gone") and lo <= e["due"] <= hi
                            and e["due"] > now and (kinds is None or e.get("kind") in kinds)]
                # пустой ответ при множестве известных сроков — скорее сбой Moodle, чем «всё сдано разом»
                if not fetched and len(vanished) >= 3:
                    res["suspicious"] = True
                else:
                    for e in vanished:
                        e["gone"] = int(now)
                        res["gone"].append(e)
            self.data["sync"] = {"last": int(now), "ok": bool(complete), "source": source, "note": note,
                                 "count": len(fetched)}
            self._prune(now)
            self.save()
            return res

    def _prune(self, now: float):
        cut = now - KEEP_DAYS * 86400
        for k in [k for k, e in self.data["events"].items() if e.get("due", 0) < cut]:
            del self.data["events"][k]
            self.data["done"].pop(k, None)
        for k in [k for k in self.data["done"] if k not in self.data["events"]]:
            del self.data["done"][k]
        for k in [k for k in self.data["notified"] if k not in self.data["events"]]:
            del self.data["notified"][k]

    def touch_sync(self, now: float, ok: bool, note: str):
        """Запись о неудачной попытке (сроки не трогаем)."""
        with self.lock:
            self.refresh()
            s = self.data["sync"]
            s.update({"attempt": int(now), "ok": bool(ok), "note": note})
            self.save()


# ════════════════════════════════════════════════════════════════════════════
#  ПАРЫ РАСПИСАНИЯ
# ════════════════════════════════════════════════════════════════════════════
def load_lessons(out_dir: Path) -> list[dict]:
    """Пары из Расписание/schedule.json (их сохраняет schedule.py). Нет файла — пусто."""
    p = Path(out_dir).joinpath(*SCHEDULE_JSON)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("lessons") if isinstance(data, dict) else None
    out = []
    for le in rows or []:
        if isinstance(le, dict) and le.get("date") and le.get("start") and le.get("subject"):
            out.append(le)
    return out


def lesson_uid(le: dict) -> str:
    """Тот же UID, что у raspisanie.ics (schedule.py): если импортировали оба файла, пары не задвоятся."""
    subj = re.sub(r"[^а-яёa-z0-9]", "", le.get("subject", "").lower())[:40]
    key = f"{le['date']}|{le['start']}|{subj}|{le.get('subgroup', '') or le.get('stream', '')}"
    return hashlib.sha1(key.encode()).hexdigest()[:20] + "@moodle-offline"


def lesson_times(le: dict) -> tuple[datetime, datetime] | None:
    try:
        d = date.fromisoformat(le["date"])
        h1, m1 = map(int, le["start"].split(":"))
        st = datetime(d.year, d.month, d.day, h1, m1, tzinfo=MSK)
        if le.get("end"):
            h2, m2 = map(int, le["end"].split(":"))
            en = datetime(d.year, d.month, d.day, h2, m2, tzinfo=MSK)
        else:
            en = st + timedelta(minutes=90)
        return st, en
    except (ValueError, KeyError, TypeError):
        return None


def lesson_title(le: dict) -> str:
    t = le["subject"] + (f" ({le['type']})" if le.get("type") else "")
    return t + (f" · подгр. {le['subgroup']}" if le.get("subgroup") else "")


# ════════════════════════════════════════════════════════════════════════════
#  ПОВЕСТКА (для окна программы, офлайн-страницы и консоли)
# ════════════════════════════════════════════════════════════════════════════
def _short_course(e: dict) -> str:
    return e.get("cshort") or e.get("course") or ""


def _item(e: dict, store: Store, now: float, cfg: dict) -> dict:
    soon_min = max(cfg["remind"]) if cfg["remind"] else 1440
    left = e["due"] - now
    return {"type": "deadline", "id": e["id"], "title": e["title"], "course": e.get("course", ""),
            "cshort": _short_course(e), "course_id": e.get("course_id", 0), "label": e.get("label", ""),
            "kind": e.get("kind", ""), "ts": e["due"], "time": f"{msk(e['due']):%H:%M}", "date": msk(e["due"]).date().isoformat(),
            "when": fmt_dt(e["due"], now), "left": left_text(e["due"], now),
            "state": "late" if left < 0 else ("soon" if left <= soon_min * 60 else "ok"),
            "url": e.get("url", ""), "action": e.get("action", ""), "desc": e.get("desc", ""), "where": e.get("where", ""),
            "done": e["id"] in store.done, "gone": bool(e.get("gone"))}


def view(store: Store, lessons: list, cfg: dict, now: float, *, days=14, show="all", course=0, with_done=None,
         with_gone=False) -> dict:
    """Всё, что нужно показать: просроченное, затем дни по порядку (сроки и пары вперемешку), счётчики, список курсов."""
    show = show if show in SHOW else "all"
    with_done = (not cfg["hide_done"]) if with_done is None else with_done
    today = msk(now).date()
    last = today + timedelta(days=max(1, int(days)))
    back = now - cfg["days_back"] * 86400
    deadlines, courses = [], {}
    for e in store.events.values():
        if e.get("kind") not in cfg["kinds"] or e.get("course_id") in cfg["exclude_courses"]:
            continue
        it = _item(e, store, now, cfg)
        if e["id"] in store.done and not with_done:
            continue
        if it["gone"] and not with_gone:
            continue
        c = courses.setdefault(it["course_id"], {"id": it["course_id"], "name": it["course"] or "Без курса", "count": 0})
        if not it["done"] and not it["gone"] and e["due"] >= now:
            c["count"] += 1
        if course and it["course_id"] != course:
            continue
        deadlines.append(it)
    deadlines.sort(key=lambda x: (x["ts"], x["title"]))
    overdue = [i for i in deadlines if i["ts"] < now and i["ts"] >= back and not i["gone"]]
    # «горит»: ещё не истекло, не сделано, не убрано с сайта
    live = [i for i in deadlines if i["ts"] >= now and not i["done"] and not i["gone"]]
    counts = {"overdue": sum(1 for i in overdue if not i["done"]),
              "today": sum(1 for i in live if i["date"] == today.isoformat()),
              "tomorrow": sum(1 for i in live if i["date"] == (today + timedelta(days=1)).isoformat()),
              "week": sum(1 for i in live if i["ts"] < now + 7 * 86400), "total": len(live)}
    by_day: dict[str, list] = {}
    if show != "lessons":
        for i in deadlines:
            if i["ts"] >= now or i["done"] or i["gone"]:
                if today.isoformat() <= i["date"] <= last.isoformat() and (i["ts"] >= now or i["date"] == today.isoformat()):
                    by_day.setdefault(i["date"], []).append(i)
    if show != "deadlines" and cfg["lessons"] and not course:
        for le in lessons:
            tm = lesson_times(le)
            if not tm or not today <= tm[0].date() <= last:
                continue
            ds = tm[0].date().isoformat()
            by_day.setdefault(ds, []).append({
                "type": "lesson", "id": "", "title": lesson_title(le), "time": f"{tm[0]:%H:%M}", "end": f"{tm[1]:%H:%M}",
                "date": ds, "ts": int(tm[0].timestamp()), "past": tm[1].timestamp() < now, "room": le.get("room", ""),
                "address": le.get("address", ""), "teacher": le.get("teacher", ""), "label": le.get("type", ""),
                "kind": le.get("kind", "")})
    day_list = []
    for ds in sorted(by_day):
        d = date.fromisoformat(ds)
        items = sorted(by_day[ds], key=lambda x: (x["time"], 0 if x["type"] == "lesson" else 1, x["title"]))
        day_list.append({"date": ds, "label": fmt_day(d, today), "items": items})
    shown_overdue = overdue if show != "lessons" else []
    return {"now": int(now), "overdue": shown_overdue, "days": day_list, "counts": counts,
            "courses": sorted(courses.values(), key=lambda c: c["name"].lower()),
            "sync": dict(store.sync), "empty": not (shown_overdue or day_list),
            "gone": sum(1 for e in store.events.values() if e.get("gone") and e["kind"] in cfg["kinds"])}


# ════════════════════════════════════════════════════════════════════════════
#  СОБЫТИЯ ДЛЯ «ЧТО НОВОГО» (новый срок, перенос, срок горит)
# ════════════════════════════════════════════════════════════════════════════
def _ev(kind: str, e: dict, detail: str, key: str) -> dict:
    ev = news.make_event(kind, e.get("course_id", 0), e.get("course", ""), e["title"], detail=detail,
                         url=e.get("url", ""), key=key)
    # id только от вида и ключа: текст («через 5 ч») со временем меняется, а событие то же — лента не должна его дублировать
    ev["id"] = hashlib.sha1(f"{kind}|{key}".encode("utf-8")).hexdigest()[:16]
    return ev


def change_events(changes: dict, now: float, cfg: dict, *, announce_new=True) -> list[dict]:
    """Новые и перенесённые сроки → события ленты. announce_new=False при первой синхронизации: тогда всё «новое»
    просто запоминаем, чтобы не получить сотню уведомлений о сроках, которые были и раньше."""
    if not cfg["notify"]:
        return []
    out = []
    ok = lambda e: e.get("kind") == "deadline" and e["due"] > now - 86400 and e.get("course_id") not in cfg["exclude_courses"]
    if announce_new:
        for e in changes.get("new", []):
            if ok(e):
                out.append(_ev("deadline_new", e, f"{e.get('label', 'срок')}: {fmt_dt(e['due'], now)} ({left_text(e['due'], now)})",
                               e["id"]))
    for old, e in changes.get("moved", []):
        if ok(e):
            out.append(_ev("deadline_moved", e, f"перенесено с {fmt_dt(old, now)} на {fmt_dt(e['due'], now)}",
                           f"{e['id']}|{old}|{e['due']}"))
    return out


def soon_events(store: Store, cfg: dict, now: float, *, mark=True) -> list[dict]:
    """Сроки, до которых осталось меньше одного из порогов напоминания (по умолчанию сутки и 3 часа). Каждый порог — один раз."""
    if not cfg["notify"] or not cfg["remind"]:
        return []
    thresholds = sorted(cfg["remind"])
    out = []
    with store.lock:
        store.refresh()
        changed = False
        for e in sorted(store.data["events"].values(), key=lambda x: x["due"]):
            if (e.get("kind") != "deadline" or e.get("gone") or e["id"] in store.data["done"]
                    or e.get("course_id") in cfg["exclude_courses"] or e["due"] <= now):
                continue
            minutes = (e["due"] - now) / 60
            hit = next((t for t in thresholds if minutes <= t), None)     # самый тесный из пройденных порогов
            if hit is None:
                continue
            rec = store.data["notified"].get(e["id"])
            same = isinstance(rec, list) and len(rec) == 2 and abs(rec[0] - e["due"]) < MOVED_MIN   # срок не переносили
            if same and hit >= rec[1]:
                continue
            anchor = rec[0] if same else e["due"]
            out.append(_ev("deadline_soon", e, f"{e.get('label', 'срок')}: {fmt_dt(e['due'], now)} — {left_text(e['due'], now)}",
                           f"{e['id']}|{anchor}|{hit}"))
            if mark:
                store.data["notified"][e["id"]] = [anchor, hit]
                changed = True
        if changed:
            store.save()
    return out


# ════════════════════════════════════════════════════════════════════════════
#  КАЛЕНДАРЬ .ics
# ════════════════════════════════════════════════════════════════════════════
def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    if len(line.encode("utf-8")) <= 74:
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


def _alarm(minutes: int, text: str) -> list[str]:
    return ["BEGIN:VALARM", "ACTION:DISPLAY", _fold("DESCRIPTION:" + _esc(text)), f"TRIGGER:-PT{int(minutes)}M", "END:VALARM"]


def _utc(ts) -> str:
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def deadline_vevent(e: dict, cfg: dict, stamp: str, now: float) -> list[str]:
    """Срок — получасовое событие, которое заканчивается ровно в момент срока; напоминания идут от его начала."""
    start = e["due"] - 1800
    label = e.get("label") or "срок"
    lines = ["BEGIN:VEVENT", f"UID:dl-{e['id']}@moodle-offline", f"DTSTAMP:{stamp}", f"DTSTART:{_utc(start)}",
             f"DTEND:{_utc(e['end'] if e.get('end') and e['end'] > e['due'] else e['due'])}",
             _fold("SUMMARY:" + _esc(f"{ICON.get(e.get('kind'), '⏰')} {e['title']} — {label}")), "TRANSP:TRANSPARENT"]
    if e.get("end") and e["end"] > e["due"]:      # у события есть длительность (например, открытое окно)
        lines[3] = f"DTSTART:{_utc(e['due'])}"
    desc = [x for x in (e.get("course"), e.get("action"), e.get("desc"), f"До: {fmt_dt(e['due'], now)} (МСК)") if x]
    lines.append(_fold("DESCRIPTION:" + _esc("\n".join(desc))))
    if e.get("url"):
        lines.append(_fold("URL:" + e["url"]))
    if e.get("where"):
        lines.append(_fold("LOCATION:" + _esc(e["where"])))
    lines.append(_fold("CATEGORIES:" + _esc(e.get("cshort") or e.get("course") or "Moodle")))
    for m in cfg["remind"]:
        lines += _alarm(m, f"{e['title']} — {label} через {_span_text(m)}")
    lines.append("END:VEVENT")
    return lines


def _span_text(minutes: int) -> str:
    if minutes % 1440 == 0:
        d = minutes // 1440
        return f"{d} {news.plural(d, 'день', 'дня', 'дней')}"
    if minutes % 60 == 0:
        h = minutes // 60
        return f"{h} {news.plural(h, 'час', 'часа', 'часов')}"
    return f"{minutes} мин"


def lesson_vevent(le: dict, cfg: dict, stamp: str) -> list[str]:
    tm = lesson_times(le)
    if not tm:
        return []
    lines = ["BEGIN:VEVENT", f"UID:{lesson_uid(le)}", f"DTSTAMP:{stamp}", f"DTSTART:{_utc(tm[0])}", f"DTEND:{_utc(tm[1])}",
             _fold("SUMMARY:" + _esc(lesson_title(le)))]
    loc = " · ".join(x for x in (le.get("room"), le.get("address")) if x)
    if loc:
        lines.append(_fold("LOCATION:" + _esc(loc)))
    desc = "\n".join(x for x in (le.get("teacher"), le.get("note")) if x)
    if desc:
        lines.append(_fold("DESCRIPTION:" + _esc(desc)))
    if cfg["lesson_remind"] > 0:
        lines += _alarm(cfg["lesson_remind"], f"{le['subject']} — через {cfg['lesson_remind']} мин")
    lines.append("END:VEVENT")
    return lines


def render_ics(store: Store, lessons: list, cfg: dict, now: float) -> tuple[str, dict]:
    """Общий календарь. → (текст .ics, сколько чего внутри)."""
    stamp = _utc(now)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:Moodle Offline: сроки и пары", "X-WR-TIMEZONE:Europe/Moscow"]
    n_dl = n_ls = 0
    back = now - cfg["days_back"] * 86400
    for e in sorted(store.events.values(), key=lambda x: x["due"]):
        if (e.get("kind") not in cfg["kinds"] or e.get("course_id") in cfg["exclude_courses"] or e.get("gone")
                or e["id"] in store.done or e["due"] < back):
            continue
        lines += deadline_vevent(e, cfg, stamp, now)
        n_dl += 1
    if cfg["lessons"]:
        horizon = (msk(now).date() - timedelta(days=7)).isoformat()
        for le in lessons:
            if le["date"] >= horizon:
                ev = lesson_vevent(le, cfg, stamp)
                if ev:
                    lines += ev
                    n_ls += 1
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n", {"deadlines": n_dl, "lessons": n_ls}


# ════════════════════════════════════════════════════════════════════════════
#  ОФЛАЙН-СТРАНИЦА
# ════════════════════════════════════════════════════════════════════════════
PAGE_CSS = """
:root{color-scheme:light dark;--fg:#1b1f24;--muted:#5f6b7a;--line:#e2e6ec;--bg:#f6f7f9;--card:#fff;--accent:#2f6fde;--late:#c2362b;--soon:#b45309;--ok:#0d9488}
@media (prefers-color-scheme:dark){:root{--fg:#e6e9ee;--muted:#9aa5b4;--line:#2a313b;--bg:#0f1216;--card:#171b21;--accent:#6ea0ff;--late:#ff8a80;--soon:#fbbf24;--ok:#5eead4}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:var(--fg);background:var(--bg)}
main{max-width:860px;margin:auto;padding:20px 16px 60px}h1{margin:0 0 4px;font-size:24px}.meta{color:var(--muted);font-size:13px}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:14px 0}.bar select,.bar label{font-size:13px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:3px 12px;font-size:13px}
.chip b{font-size:15px}.chip.late b{color:var(--late)}.chip.soon b{color:var(--soon)}
h2{font-size:15px;margin:22px 0 8px;color:var(--muted);text-transform:none}h2.late{color:var(--late)}h2 .badge{font-weight:400;font-size:12px;border:1px solid var(--line);border-radius:6px;padding:0 6px;margin-left:6px}
.row{display:flex;gap:12px;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 12px;margin:6px 0}
.row.late{border-left:4px solid var(--late)}.row.soon{border-left:4px solid var(--soon)}.row.lesson{border-left:4px solid var(--accent);opacity:.95}.row.past{opacity:.55}
.row.done .t{text-decoration:line-through;color:var(--muted)}.row.gone{opacity:.6}
.tm{min-width:92px;font-variant-numeric:tabular-nums;color:var(--muted);font-size:13px}.t{font-weight:600}.sub{color:var(--muted);font-size:13px}
.left{font-size:13px;margin-left:8px}.late .left{color:var(--late)}.soon .left{color:var(--soon)}.ok .left{color:var(--ok)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.empty{color:var(--muted);padding:30px 0;text-align:center}
@media print{.bar{display:none}body{background:#fff}}
"""
PAGE_JS = """
(function(){
  var rows=[].slice.call(document.querySelectorAll('.row[data-id]')),KEY='mo-done:';
  function ls(k,v){try{if(v===undefined)return localStorage.getItem(k);if(v===null)localStorage.removeItem(k);else localStorage.setItem(k,v)}catch(e){return null}}
  function msk(ts){return new Date(ts*1000+3*3600*1000)}
  function left(due,now){var d=due-now,m=Math.floor(Math.abs(d)/60),s;
    if(m<1)return d>=0?'сейчас':'только что истёк';
    if(m<60)s=m+' мин';else{var h=Math.floor(m/60);if(h<48)s=h+' ч'+(h<6&&m%60?' '+(m%60)+' мин':'');else{var n=Math.floor(h/24),a=n%10,b=n%100;s=n+' '+(a==1&&b!=11?'день':(a>=2&&a<=4&&(b<12||b>14)?'дня':'дней'))}}
    return d>=0?'через '+s:'просрочено на '+s}
  function tick(){var now=Math.floor(Date.now()/1000),soon=(+document.body.dataset.soon||1440)*60;
    document.querySelectorAll('.row.dl').forEach(function(r){var ts=+r.dataset.ts,d=ts-now,el=r.querySelector('.left');
      r.classList.remove('late','soon','ok');r.classList.add(d<0?'late':(d<=soon?'soon':'ok'));if(el)el.textContent=left(ts,now)});
    document.querySelectorAll('.row.lesson').forEach(function(r){r.classList.toggle('past',+r.dataset.end<now)});
    var t=msk(now).toISOString().slice(0,10),tm=msk(now+86400).toISOString().slice(0,10);
    document.querySelectorAll('h2[data-date]').forEach(function(h){var b=h.querySelector('.badge');if(b)b.remove();
      var w=h.dataset.date==t?'сегодня':(h.dataset.date==tm?'завтра':'');if(w){b=document.createElement('span');b.className='badge';b.textContent=w;h.appendChild(b)}})}
  function apply(){var c=document.getElementById('course').value,hide=document.getElementById('hide').checked;
    rows.forEach(function(r){var done=r.classList.contains('dl')&&(r.dataset.done=='1'||ls(KEY+r.dataset.id)=='1');
      r.classList.toggle('done',done);var cb=r.querySelector('input');if(cb)cb.checked=done;
      var off=(c&&r.dataset.course!=c&&r.classList.contains('dl'))||(hide&&done);r.style.display=off?'none':''});
    document.querySelectorAll('main h2').forEach(function(h){var n=h.nextElementSibling,any=false;
      while(n&&n.tagName!='H2'){if(n.classList.contains('row')&&n.style.display!='none')any=true;n=n.nextElementSibling}
      h.style.display=any?'':'none'})}
  document.addEventListener('change',function(e){var r=e.target.closest&&e.target.closest('.row.dl');
    if(r&&e.target.type=='checkbox'){r.dataset.done=e.target.checked?'1':'0';ls(KEY+r.dataset.id,e.target.checked?'1':null);apply()}});
  document.getElementById('course').onchange=apply;document.getElementById('hide').onchange=apply;
  tick();apply();setInterval(tick,30000);
})();
"""


def _e(s) -> str:
    return htmllib.escape(str(s if s is not None else ""), quote=True)


def _row(i: dict) -> str:
    if i["type"] == "lesson":
        place = " · ".join(x for x in (i.get("room"), i.get("address")) if x)
        sub = " · ".join(x for x in (place, i.get("teacher")) if x)
        end_ts = int(datetime.fromisoformat(f"{i['date']}T{i['end']}").replace(tzinfo=MSK).timestamp())
        return (f'<div class="row lesson" data-end="{end_ts}"><span style="width:13px;flex:none"></span><div class="tm">{_e(i["time"])}–{_e(i["end"])}</div>'
                f'<div><div class="t">{_e(i["title"])}</div>' + (f'<div class="sub">{_e(sub)}</div>' if sub else "") + "</div></div>")
    link = f'<a href="{_e(i["url"])}" target="_blank" rel="noopener">{_e(i["title"])}</a>' if i.get("url") else _e(i["title"])
    sub = " · ".join(x for x in (i.get("cshort") or i.get("course"), i.get("label"), i.get("action")) if x)
    gone = ' · <span title="Moodle убрал срок из календаря — обычно это значит, что работа сдана">убрано с сайта</span>' if i.get("gone") else ""
    return (f'<div class="row dl {i["state"]}{" gone" if i.get("gone") else ""}" data-id="{_e(i["id"])}" data-ts="{i["ts"]}" '
            f'data-course="{i["course_id"]}" data-done="{1 if i["done"] else 0}">'
            f'<input type="checkbox" title="сделано"><div class="tm">{_e(i["time"] if i["date"] else i["when"])}</div>'
            f'<div><div class="t">{link}<span class="left">{_e(i["left"])}</span></div>'
            f'<div class="sub">{_e(sub)}{gone}</div></div></div>')


def render_page(v: dict, cfg: dict, generated: str) -> str:
    """Самодостаточная страница: открывается двойным щелчком, без интернета и без программы."""
    sync = v["sync"]
    syncs = (f"Сроки обновлены {fmt_dt(sync['last'], v['now'])}" if sync.get("last") else "Сроки ещё не загружались")
    chips = "".join(f'<span class="chip {cls}"><b>{n}</b> {_e(txt)}</span>' for cls, n, txt in (
        ("late", v["counts"]["overdue"], "просрочено"), ("soon", v["counts"]["today"], "сегодня"),
        ("", v["counts"]["tomorrow"], "завтра"), ("", v["counts"]["week"], "за 7 дней"), ("", v["counts"]["total"], "всего впереди")))
    body = []
    if v["overdue"]:
        body.append('<h2 class="late">Просрочено</h2>' + "".join(_row(i) for i in v["overdue"]))
    for d in v["days"]:
        body.append(f'<h2 data-date="{_e(d["date"])}">{_e(d["label"].split(" · ", 1)[-1])}</h2>' + "".join(_row(i) for i in d["items"]))
    if v["empty"]:
        body.append('<div class="empty">Ближайших сроков и пар нет — или календарь ещё не загружен.</div>')
    courses = '<option value="">Все курсы</option>' + "".join(
        f'<option value="{c["id"]}">{_e(c["name"][:70])} ({c["count"]})</option>' for c in v["courses"])
    soon = max(cfg["remind"]) if cfg["remind"] else 1440
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Сроки и пары — Moodle Offline</title><style>{PAGE_CSS}</style></head><body data-soon="{soon}"><main>'
            f'<h1>Сроки и пары</h1><div class="meta">{_e(syncs)} · страница собрана {_e(generated)} · время московское</div>'
            f'<div class="chips">{chips}</div><div class="bar"><select id="course">{courses}</select>'
            f'<label><input type="checkbox" id="hide" checked> скрывать сделанное</label></div>'
            + "".join(body) + f'</main><script>{PAGE_JS}</script></body></html>')


# ════════════════════════════════════════════════════════════════════════════
#  ЗАПИСЬ ФАЙЛОВ И КОНСОЛЬ
# ════════════════════════════════════════════════════════════════════════════
def write_outputs(out_dir: Path, store: Store, cfg: dict, now: float | None = None) -> dict:
    """Пересобирает Календарь/moodle_calendar.ics и Календарь/index.html. → {"ics": путь, "page": путь, "deadlines": n, "lessons": n}"""
    now = now or time.time()
    root = Path(out_dir) / CALENDAR_DIR
    lessons = load_lessons(out_dir) if cfg["lessons"] else []
    text, cnt = render_ics(store, lessons, cfg, now)
    _atomic_write(root / ICS_FILE, text)
    v = view(store, lessons, cfg, now, days=30, show="all", with_done=True, with_gone=False)
    _atomic_write(root / PAGE_FILE, render_page(v, cfg, f"{msk(now):%d.%m.%Y %H:%M}"))
    return {"ics": str(root / ICS_FILE), "page": str(root / PAGE_FILE), **cnt}


def plain_line(i: dict) -> str:
    if i["type"] == "lesson":
        return f"{i['time']}–{i['end']}  пара: {i['title']}" + (f" ({i['room']})" if i.get("room") else "")
    mark = "[x]" if i.get("done") else "[ ]"
    return f"{i['time']}  {mark} {i['title']} — {i.get('label', '')} · {i.get('cshort') or i.get('course', '')} · {i['left']}"


def format_view(v: dict) -> str:
    """Повестка текстом для консоли."""
    out = []
    c = v["counts"]
    out.append(f"Просрочено: {c['overdue']} · сегодня: {c['today']} · завтра: {c['tomorrow']} · за 7 дней: {c['week']} · всего впереди: {c['total']}")
    if v["overdue"]:
        out.append("\nПРОСРОЧЕНО")
        out += ["  " + f"{i['when']}  " + plain_line(i)[7:] for i in v["overdue"]]
    for d in v["days"]:
        out.append("\n" + d["label"].upper())
        out += ["  " + plain_line(i) for i in d["items"]]
    if v["empty"]:
        out.append("\nБлижайших сроков и пар нет.")
    s = v["sync"]
    out.append("\n" + (f"Сроки обновлены {fmt_dt(s['last'], v['now'])}" if s.get("last") else "Сроки ещё не загружались (запустите скачивание или --calendar-sync)"))
    return "\n".join(out)

"""Таймер проверки «жив ли Moodle» и автообновление по таймеру. Только стандартная библиотека и `news`.

Здесь вся логика без сети (её можно проверять с подменой часов): настройки, окна «когда работать»,
различение «сайт упал / ожил», решение «пора ли запускать автообновление», замок «кто ведёт таймер».
Запросы к сайту делают `gui.py` (пока открыто окно) и `moodle_offline.py --watch` (без окна).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

import news

WATCH_FILE = "watch.json"          # состояние таймера (в папке данных): когда проверяли, когда обновляли
LOCK_FILE = "watch.lock"           # кто сейчас ведёт таймер (окно или консольное слежение)
LOCK_STALE = 180                   # замок без обновления дольше этого (сек) считается брошенным

INTERVALS = (30, 60, 120, 300, 600, 900, 1800, 3600, 10800)           # как часто проверять, сек (быстрее 30 с — нельзя)
EVERY = (30, 60, 120, 180, 360, 720, 1440, 2880)                      # как часто автообновлять, мин
SCOPES = {"all": "Все курсы", "selected": "Выбранные курсы", "existing": "Только уже скачанные"}
DAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

MIN_INTERVAL, MAX_INTERVAL = 30, 86400
MIN_EVERY, MAX_EVERY = 15, 10080
MIN_RETRY, MAX_RETRY = 5, 1440


def _win():
    return {"days": [0, 1, 2, 3, 4, 5, 6], "from": "00:00", "to": "24:00"}


DEFAULTS = {
    "check": {
        "enabled": True,            # проверять, жив ли Moodle («светофор»)
        "interval": 30,             # раз в сколько секунд
        "window": _win(),           # в какие дни и часы проверять (вне окна — не стучимся)
        "notify_down": False,       # сообщить, что Moodle не отвечает
        "notify_up": True,          # сообщить, что Moodle снова работает
        "down_after": 2,            # сколько проверок подряд без ответа считать падением
    },
    "auto": {
        "enabled": False,           # «рубильник»: докачивать недостающее само
        "every": 360,               # как часто, мин
        "retry": 30,                # через сколько повторить, если не вышло, мин
        "scope": "all",             # all — все курсы, selected — выбранные, existing — только уже скачанные
        "courses": [],              # id выбранных курсов (для scope=selected)
        "include_past": False,      # для scope=all: брать и прошедшие курсы
        "window": _win(),           # в какие дни и часы запускать
    },
}


# ── разбор настроек ──────────────────────────────────────────────────────────
def hhmm(v, default: str, allow_24=False) -> str:
    """'8:5' → '08:05'; мусор → default; '24:00' разрешено только для конца окна."""
    try:
        h, m = str(v).strip().split(":")[:2]
        h, m = int(h), int(m)
    except (ValueError, TypeError):
        return default
    if allow_24 and (h, m) == (24, 0):
        return "24:00"
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return default
    return f"{h:02d}:{m:02d}"


def minutes(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _clamp(v, lo, hi, default):
    try:
        v = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _bool(v, default):
    return default if v is None else bool(v)


def norm_window(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    days = sorted({int(d) for d in (raw.get("days") or []) if str(d).lstrip("-").isdigit() and 0 <= int(d) <= 6})
    a = hhmm(raw.get("from"), "00:00")
    b = hhmm(raw.get("to"), "24:00", allow_24=True)
    if a == b:                       # одинаковые границы — «весь день», иначе окно было бы пустым
        a, b = "00:00", "24:00"
    return {"days": days or list(range(7)), "from": a, "to": b}


def normalize(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    c, a = raw.get("check"), raw.get("auto")
    c, a = (c if isinstance(c, dict) else {}), (a if isinstance(a, dict) else {})
    d = DEFAULTS
    check = {
        "enabled": _bool(c.get("enabled"), d["check"]["enabled"]),
        "interval": _clamp(c.get("interval"), MIN_INTERVAL, MAX_INTERVAL, d["check"]["interval"]),
        "window": norm_window(c.get("window")),
        "notify_down": _bool(c.get("notify_down"), d["check"]["notify_down"]),
        "notify_up": _bool(c.get("notify_up"), d["check"]["notify_up"]),
        "down_after": _clamp(c.get("down_after"), 1, 10, d["check"]["down_after"]),
    }
    ids, seen = [], set()
    for x in a.get("courses") or []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i not in seen:
            seen.add(i)
            ids.append(i)
    scope = a.get("scope") if a.get("scope") in SCOPES else d["auto"]["scope"]
    auto = {
        "enabled": _bool(a.get("enabled"), d["auto"]["enabled"]),
        "every": _clamp(a.get("every"), MIN_EVERY, MAX_EVERY, d["auto"]["every"]),
        "retry": _clamp(a.get("retry"), MIN_RETRY, MAX_RETRY, d["auto"]["retry"]),
        "scope": scope,
        "courses": ids,
        "include_past": _bool(a.get("include_past"), d["auto"]["include_past"]),
        "window": norm_window(a.get("window")),
    }
    return {"check": check, "auto": auto}


# ── «когда» ──────────────────────────────────────────────────────────────────
def in_window(win: dict, dt: datetime) -> bool:
    """Открыто ли окно в этот момент. Окно через полночь (22:00–06:00) относится ко дню, в который началось."""
    a, b = minutes(win["from"]), minutes(win["to"])
    t = dt.hour * 60 + dt.minute
    wd = dt.weekday()
    if a < b:
        return a <= t < b and wd in win["days"]
    if t >= a:
        return wd in win["days"]
    return t < b and (wd - 1) % 7 in win["days"]


def next_open(win: dict, dt: datetime) -> datetime | None:
    """Ближайший момент (не раньше dt), когда окно открыто."""
    if in_window(win, dt):
        return dt
    a = minutes(win["from"])
    best = None
    for off in range(0, 9):
        day = (dt + timedelta(days=off)).replace(hour=a // 60, minute=a % 60, second=0, microsecond=0)
        if day.weekday() in win["days"] and day >= dt and (best is None or day < best):
            best = day
    return best


def describe_window(win: dict) -> str:
    days = win["days"]
    if len(days) == 7:
        d = "каждый день"
    elif days == [0, 1, 2, 3, 4]:
        d = "по будням"
    elif days == [5, 6]:
        d = "по выходным"
    else:
        d = ", ".join(DAYS[i] for i in days)
    if (win["from"], win["to"]) == ("00:00", "24:00"):
        return f"{d}, круглосуточно"
    return f"{d}, с {win['from']} до {win['to']}"


def describe_interval(sec: int) -> str:
    if sec < 60:
        return f"{sec} с"
    if sec < 3600:
        return f"{sec // 60} мин"
    return f"{sec / 3600:g} ч"


def describe_every(minutes_: int) -> str:
    if minutes_ < 60:
        return f"{minutes_} мин"
    if minutes_ % 1440 == 0:
        return f"{minutes_ // 1440} дн."
    return f"{minutes_ / 60:g} ч"


# ── «жив / упал» ─────────────────────────────────────────────────────────────
class Tracker:
    """Превращает ряд проверок (ok / slow / down) в события «упал» и «ожил».
    Одиночный сбой не считается падением: нужно `down_after` неудач подряд. «Ожил» бывает только после объявленного «упал»."""

    def __init__(self, down_after: int = 2):
        self.down_after = down_after
        self.status = "unknown"
        self.fails = 0
        self.down_since: float | None = None
        self.announced = False

    def feed(self, st: str, now: float) -> list[dict]:
        out = []
        if st == "down":
            self.fails += 1
            if self.down_since is None:
                self.down_since = now
            if self.fails >= self.down_after and not self.announced:
                self.announced = True
                out.append({"type": "down", "since": self.down_since})
            if self.announced:
                self.status = "down"
        else:
            if self.announced and self.down_since is not None:
                out.append({"type": "up", "outage": max(0.0, now - self.down_since), "since": self.down_since})
            self.fails, self.down_since, self.announced = 0, None, False
            self.status = st
        return out


def fmt_outage(sec: float) -> str:
    sec = int(sec)
    if sec < 90:
        return f"{sec} с"
    if sec < 5400:
        return f"{round(sec / 60)} мин"
    h, m = divmod(sec // 60, 60)
    return f"{h} ч {m:02d} мин" if m else f"{h} ч"


def make_events(evs: list[dict], host: str, notify_down: bool, notify_up: bool, now: float) -> list[dict]:
    """События таймера → события ленты «Что нового» (виды moodle_down / moodle_up)."""
    out = []
    for e in evs:
        if e["type"] == "down" and notify_down:
            kind, detail, key = "moodle_down", "не отвечает несколько проверок подряд, продолжаю проверять", f"{int(e['since'])}"
        elif e["type"] == "up" and notify_up:
            kind, detail, key = "moodle_up", f"не работал {fmt_outage(e['outage'])}", f"{int(e['since'])}"
        else:
            continue
        ev = news.make_event(kind, 0, "Статус сайта", host, detail=detail, url="", key=key)
        ev["id"] = hashlib.sha1(f"{kind}|{key}".encode("utf-8")).hexdigest()[:16]
        out.append(ev)
    return out


# ── автообновление: пора ли ─────────────────────────────────────────────────
def auto_due(cfg: dict, st: dict, now: datetime, *, moodle_ok: bool | None, busy: bool, can_login: bool = True) -> tuple[bool, str]:
    """→ (пора ли запускать, почему нет). `cfg` — normalize(...)['auto'], `st` — watch.json['auto']."""
    if not cfg["enabled"]:
        return False, "выключено"
    if busy:
        return False, "идёт другая работа"
    if not can_login:
        return False, "нужен вход (отметьте «Запомнить меня»)"
    if moodle_ok is False:
        return False, "Moodle не отвечает"
    if not in_window(cfg["window"], now):
        nxt = next_open(cfg["window"], now)
        return False, "вне разрешённого времени" + (f" (откроется {nxt:%d.%m %H:%M})" if nxt else "")
    t = now.timestamp()
    last = float(st.get("last_attempt") or 0)
    if not last:
        return True, "первый запуск"
    period = (cfg["retry"] if st.get("last_failed") else cfg["every"]) * 60
    if t - last >= period:
        return True, "по таймеру"
    return False, f"ещё рано (следующий запуск около {datetime.fromtimestamp(last + period):%H:%M})"


def next_auto(cfg: dict, st: dict, now: datetime) -> datetime | None:
    """Когда ориентировочно будет следующий запуск (для подписи в окне). None — выключено."""
    if not cfg["enabled"]:
        return None
    last = float(st.get("last_attempt") or 0)
    when = now if not last else datetime.fromtimestamp(last + (cfg["retry"] if st.get("last_failed") else cfg["every"]) * 60)
    when = max(when, now)
    return next_open(cfg["window"], when)


# ── состояние на диске ───────────────────────────────────────────────────────
def _read_json(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_state(data_dir: Path) -> dict:
    d = _read_json(Path(data_dir) / WATCH_FILE)
    d.setdefault("auto", {})
    d.setdefault("check", {})
    return d


def save_state(data_dir: Path, st: dict):
    try:
        _write_json(Path(data_dir) / WATCH_FILE, st)
    except OSError:
        pass


def record_check(data_dir: Path, status: str, now: float):
    st = load_state(data_dir)
    c = st["check"]
    if c.get("status") != status:
        c["since"] = now
    c.update(status=status, last=now)
    save_state(data_dir, st)


def record_attempt(data_dir: Path, now: float):
    st = load_state(data_dir)
    st["auto"].update(last_attempt=now, last_failed=False, running=True)
    save_state(data_dir, st)


def record_result(data_dir: Path, ok: bool, text: str, now: float):
    st = load_state(data_dir)
    a = st["auto"]
    a.update(last_failed=not ok, running=False, last_text=text, last_end=now)
    if ok:
        a["last_ok"] = now
    a["runs"] = int(a.get("runs") or 0) + 1
    save_state(data_dir, st)


# ── замок: кто ведёт таймер ─────────────────────────────────────────────────
class Lock:
    """Чтобы окно и консольное слежение не запускали автообновление одновременно. Кто взял замок и обновляет его —
    тот ведёт таймер; бросил — через LOCK_STALE секунд замок можно забрать."""

    def __init__(self, data_dir: Path, who: str = ""):
        self.path = Path(data_dir) / LOCK_FILE
        self.token = secrets.token_hex(6)
        self.who = who

    def holder(self) -> dict | None:
        d = _read_json(self.path)
        return d or None

    def acquire(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        h = self.holder()
        if h and h.get("token") != self.token and now - float(h.get("ts") or 0) < LOCK_STALE:
            return False
        try:
            _write_json(self.path, {"token": self.token, "ts": now, "who": self.who, "pid": os.getpid()})
        except OSError:
            return True       # не смогли записать — лучше работать, чем молчать
        return True

    def release(self):
        h = self.holder()
        if h and h.get("token") == self.token:
            try:
                self.path.unlink()
            except OSError:
                pass


# ── для окна: краткая сводка ────────────────────────────────────────────────
def summary(cfg: dict, st: dict, now: datetime, net_status: str = "") -> dict:
    """Строки для подписей в окне: что и как часто проверяется, когда следующее автообновление."""
    c, a = cfg["check"], cfg["auto"]
    chk = ("проверка выключена" if not c["enabled"]
           else f"проверяю каждые {describe_interval(c['interval'])} ({describe_window(c['window'])})")
    nxt = next_auto(a, st["auto"], now)
    if not a["enabled"]:
        auto = "автообновление выключено"
    else:
        auto = f"автообновление: раз в {describe_every(a['every'])} ({describe_window(a['window'])})"
        if nxt:
            auto += f"; следующее ~ {nxt:%d.%m %H:%M}"
    last = st["auto"].get("last_end")
    return {"check": chk, "auto": auto, "last_text": st["auto"].get("last_text", ""),
            "last_end": datetime.fromtimestamp(last).strftime("%d.%m %H:%M") if last else "",
            "running": bool(st["auto"].get("running")), "net": net_status}


# ── автозапуск слежения без окна (Windows) ──────────────────────────────────
def startup_path(env) -> Path | None:
    """Папка автозагрузки текущего пользователя в Windows (права администратора не нужны)."""
    appdata = (env.get("APPDATA") or "").strip()
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "MoodleOffline слежение.vbs"


def startup_script(cmd: list[str]) -> str:
    """VBScript, запускающий команду без окна консоли. cmd — программа и её параметры."""
    line = " ".join('"' + c + '"' for c in cmd)
    return ("' Moodle Offline: слежение за Moodle и автообновление без окна. Удалите этот файл, чтобы отключить.\r\n"
            'CreateObject("WScript.Shell").Run "' + line.replace('"', '""') + '", 0, False\r\n')

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Moodle Offline — графический интерфейс.

Локальный веб-интерфейс (только 127.0.0.1) в отдельном окне Microsoft Edge/Chrome в режиме приложения:
без адресной строки, светлая/тёмная тема по системе, карточки курсов, менеджер загрузок, светофор сервера.

    python gui.py              — открыть интерфейс
    python gui.py --sync       — быстрая синхронизация без окна (для планировщика), см. moodle_offline.py --help
"""
from __future__ import annotations

import contextlib
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import ai
import android
import dl_rules
import moodle_offline as mo
import datadir
import deadlines
import news
import notes
import search
import watch
import electives as el
import iotapi
import mcp_server
import qr
import reader
import reserve
import schedule as sched
import sch_book as book
import xlsx_schedule as xs
import transfer
import updater

UI_DIR = Path(os.environ.get("MOODLE_OFFLINE_UI") or (Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "ui"))
TASK_NAME = "MoodleOffline - ночная синхронизация"
SSO_DOMAIN = "sevsu.ru"      # единый вход вуза: auth.sevsu.ru, iot.sevsu.ru, timetable.sevsu.ru
HEARTBEAT_IDLE = 30
CLOSE_GRACE = 3.0           # сколько сервер ещё живёт после просьбы новой версии закрыться: окну нужно успеть закрыть себя
HEARTBEAT_BUSY = 60
# Когда считать, что окна больше нет. Раньше хватало 25 секунд без запроса состояния, но свёрнутое или «уснувшее» окно браузера
# запросы почти не шлёт (таймеры в нём замедляют до раза в минуту или замораживают совсем), и программа закрывалась у живого окна.
WINDOW_FIRST_WAIT = 60.0            # окно ещё ни разу не отозвалось (запуск без окна, браузер не открылся)
WINDOW_BYE_GRACE = 15.0             # окно сообщило «закрываюсь» (/api/bye) и не вернулось: F5 и перезагрузка страницы успевают вернуться
WINDOW_SILENCE_LIMIT = 12 * 3600.0  # окно давно молчит и не прощалось (браузер убили, ПК завис): страховка, не обычный путь
SLEEP_GAP = 45.0                    # между двумя проходами сторожа прошло больше — компьютер спал: окну нужно время очнуться
ERROR_LOG = "errors.log"            # сбои внутри программы (без паролей и ключей): для разбора, когда «что-то перестало отвечать»
ERROR_LOG_MAX = 200_000
_ERR_SEEN: dict = {}                # (где, что) → когда писали в последний раз: одинаковый сбой каждые 5 секунд журнал не засоряет
_SECRET_RE = re.compile(r"(sesskey|token|moodlesession|password|passwd|secret|cookie|key)=([^&\s'\"]+)", re.I)
_AIKEY_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|Bearer\s+\S+)")     # ключи нейросетей


def scrub(text, limit: int = 400) -> str:
    """Текст ошибки без значений вида «sesskey=…», «token=…», «password=…» (в сообщениях requests бывают адреса с параметрами)."""
    return _AIKEY_RE.sub("…", _SECRET_RE.sub(lambda m: m.group(1) + "=…", str(text)))[:limit]


def log_error(where: str, exc: BaseException, rep=None) -> str:
    """Записать сбой внутри программы: errors.log в папке данных и «Журнал» окна. Только тип, сжатое сообщение и имена
    строк кода — без паролей, ключей и cookies. → короткое описание («TypeError: …»)."""
    import traceback
    short = f"{type(exc).__name__}: {scrub(exc)}"
    now = time.monotonic()
    key = (where, short)
    if now - _ERR_SEEN.get(key, -1e9) < 600:
        return short
    _ERR_SEEN[key] = now
    try:
        frames = [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in traceback.extract_tb(exc.__traceback__)[-6:]]
    except Exception:           # noqa: BLE001
        frames = []
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{where}] {short}" + (f"  ← {' > '.join(frames)}" if frames else "")
    try:
        path = mo.DATA_DIR / ERROR_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > ERROR_LOG_MAX:      # файл не растёт бесконечно: оставляем вторую половину
            tail = path.read_text(encoding="utf-8", errors="replace")[-ERROR_LOG_MAX // 2:]
            path.write_text(tail[tail.find("\n") + 1:], encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if rep is not None:
        try:
            rep.log(f"Сбой внутри программы ({where}): {short}", "warn")
        except Exception:       # noqa: BLE001
            pass
    return short
ANDROID = android.is_android()      # на телефоне окно — это браузер, а программа живёт в Termux
CAL_WATCH_EVERY = 300         # как часто (сек) проверять «горящие» сроки, пока открыто окно


# ════════════════════════════════════════════════════════════════════════════
#  ОТЧЁТЧИК ДЛЯ GUI: журнал, текущий файл, список загрузок
# ════════════════════════════════════════════════════════════════════════════
class GuiReporter(mo.Reporter):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.glock = threading.Lock()
        self.lines = deque(maxlen=1500)
        self.seq = 0
        self.downloads = deque(maxlen=400)
        self.dseq = 0
        self.reset()

    def reset(self):
        self.file = None
        self.course_info = None
        self.item_info = None
        self.counts = {"new": 0, "skipped": 0, "failed": 0, "bytes": 0}
        self.tot_bytes = 0                       # сколько байт скачано в этом запуске (для общей скорости)
        self.samples = deque(maxlen=200)         # (время, tot_bytes) — раз в полсекунды
        self._smp_t = 0.0
        self.plan_courses = []                   # очередь курсов запуска: [{"id", "name", "state"}]
        self._plan_ix = {}

    def emit(self, msg, level):
        with self.glock:
            self.seq += 1
            self.lines.append({"seq": self.seq, "t": datetime.now().strftime("%H:%M:%S"), "level": level,
                               "msg": msg.strip("\n")})

    def plan(self, courses):
        self.plan_courses = [{"id": c.id, "name": c.fullname or str(c.id), "state": "wait", "new": 0, "skip": 0, "fail": 0, "bytes": 0}
                             for c in courses]
        self._plan_ix = {p["id"]: p for p in self.plan_courses}

    def course(self, i, n, course):
        super().course(i, n, course)
        for p in self.plan_courses:              # прежний курс закончен, этот — «сейчас»
            if p["state"] == "now":
                p["state"] = "done"
        p = self._plan_ix.get(course.id)
        if p:
            p["state"] = "now"
            if course.fullname:
                p["name"] = course.fullname
        self.course_info = {"i": i, "n": n, "name": course.fullname, "id": course.id}
        self.item_info = None

    def item(self, i, n, sec, it):
        super().item(i, n, sec, it)
        self.item_info = {"i": i, "n": n, "name": it.name, "kind": it.kind, "section": sec.name}

    def file_start(self, title, total, initial=0, quiet=False):
        now = time.monotonic()
        self.file = {"title": title, "total": total, "done": initial, "speed": 0.0, "_t": now, "_d": initial,
                     "_last": initial, "quiet": quiet}

    def file_progress(self, done):
        f = self.file
        if not f:
            return
        if done > f["_last"]:
            self.tot_bytes += done - f["_last"]
        f["_last"] = f["done"] = done
        now = time.monotonic()
        if now - self._smp_t >= 0.5:
            self.samples.append((now, self.tot_bytes))
            self._smp_t = now
        dt = now - f["_t"]
        if dt >= 0.4:
            inst = (done - f["_d"]) / dt
            f["speed"] = inst if f["speed"] == 0 else 0.6 * f["speed"] + 0.4 * inst
            f["_t"], f["_d"] = now, done

    def file_end(self, status):
        self.file = None

    def net_status(self, status, detail=""):
        self.app.set_net(status, detail)

    def state_hint(self, state):
        self.app.job_hint = state

    def recovered(self, waited_seconds):
        if waited_seconds > 60:
            mo.notify(mo.APP_NAME, "Moodle снова доступен! Продолжаю загрузку.")

    def _plan_now(self):
        return self._plan_ix.get((self.course_info or {}).get("id"))

    def saved(self, rel_out, title, size, status):
        p = self._plan_now()
        if status in ("new", "page"):
            self.counts["new"] += 1
            self.counts["bytes"] += size or 0
            if p:
                p["new"] += 1
                p["bytes"] += size or 0
        elif status in ("skipped", "copied"):
            self.counts["skipped"] += 1
            if p:
                p["skip"] += 1
        if status == "skipped":
            return
        with self.glock:
            self.dseq += 1
            self.downloads.appendleft({"id": self.dseq, "path": rel_out, "title": title, "size": size,
                                       "status": status, "t": datetime.now().strftime("%H:%M"),
                                       "course": (self.course_info or {}).get("name", ""),
                                       "cid": (self.course_info or {}).get("id")})

    def failed(self, entry):
        self.counts["failed"] += 1
        p = self._plan_now()
        if p:
            p["fail"] += 1
        with self.glock:
            self.dseq += 1
            self.downloads.appendleft({"id": self.dseq, "path": "", "title": entry.get("item", ""),
                                       "size": 0, "status": "fail", "error": entry.get("error", ""),
                                       "url": entry.get("url", ""), "t": datetime.now().strftime("%H:%M"),
                                       "course": entry.get("course", ""), "cid": (self.course_info or {}).get("id")})

    def overall(self):
        """Общий ход запуска: процент (по курсам и элементам), скорость за последние 10 с, скачано байт."""
        ci, ii, f = self.course_info, self.item_info, self.file
        if not ci:
            return None
        frac = 0.0
        if ii and ii.get("n"):
            part = (f["done"] / f["total"]) if f and f.get("total") else 0.0
            frac = min(1.0, (ii["i"] - 1 + min(1.0, part)) / ii["n"])
        pct = min(100.0, 100.0 * (ci["i"] - 1 + frac) / max(ci["n"], 1))
        now = time.monotonic()
        pts = [(t, b) for t, b in list(self.samples) if now - t <= 10.0]
        speed = (self.tot_bytes - pts[0][1]) / max(now - pts[0][0], 1.0) if pts else 0.0
        return {"pct": round(pct, 1), "ci": ci["i"], "cn": ci["n"], "bytes": self.tot_bytes, "speed": max(speed, 0.0)}

    def snapshot(self, since, dsince=None):
        with self.glock:
            lines = [ln for ln in self.lines if ln["seq"] > since]
            # окно запоминает уже полученное и просит только новое (dsince); без него — последние 150, как раньше
            downloads = list(self.downloads)[:150] if dsince is None else [d for d in self.downloads if d["id"] > dsince]
        f = self.file
        fs = None
        if f:
            eta = (f["total"] - f["done"]) / f["speed"] if f["total"] and f["speed"] > 1 else None
            fs = {"title": f["title"], "total": f["total"], "done": f["done"], "speed": f["speed"], "eta": eta}
        return {"lines": lines, "seq": self.seq, "downloads": downloads, "file": fs,
                "course": self.course_info, "item": self.item_info, "counts": dict(self.counts),
                "overall": self.overall(), "dseq": self.dseq,
                "plan": [dict(p, state="done" if p["state"] == "now" and self.app.job_state == "idle" else p["state"])
                         for p in self.plan_courses]}


# ════════════════════════════════════════════════════════════════════════════
#  СОСТОЯНИЕ ПРИЛОЖЕНИЯ
# ════════════════════════════════════════════════════════════════════════════
class App:
    def __init__(self):
        self.lock = threading.RLock()
        self.vault = mo.Vault()
        self.settings = self.vault.load_settings()
        self.settings.setdefault("base_url", mo.DEFAULT_URL)
        self.settings.setdefault("mode", 1)
        self.settings.setdefault("out_dir", str(mo.DEFAULT_OUT))
        self.rep = GuiReporter(self)
        self.idle_control = mo.Control()
        self.control = None
        self.client: mo.MoodleClient | None = None
        self.user_name = ""
        self.courses: list[mo.Course] = []
        self.login_state = "idle"     # idle | logging | ok | error
        self.login_error = ""
        self.job_thread = None
        self.job_state = "idle"       # idle | running | paused | stopping | waiting_server | waiting_window
        self.job_hint = "running"
        self.job_kind = ""
        self.job_started = None
        self.last_result = None
        self.armed = None             # параметры задания, которое стартует, когда сервер поднимется
        self.net = {"status": "unknown", "detail": "", "latency": None, "checked": None}
        self.geo = {"ip": None, "cc": None, "checked": False, "warning": ""}
        self.started = time.monotonic()
        self.last_poll = self.started
        self.polled = False           # окно хотя бы раз спрашивало состояние
        self.bye_at = 0.0             # когда окно сказало «закрываюсь» (0 — не говорило или уже вернулось)
        self._tick_at = self.started  # прошлый проход сторожа: по разрыву видно, что компьютер спал
        self.token = secrets.token_urlsafe(18)
        self.mcp: mcp_server.McpServer | None = None      # доступ для внешних нейросетей (раздел 3ж README)
        self.mcp_error = ""
        self._mcp_busy = threading.Lock()                 # start/stop сервера — по одному (стартовый поток и окно)
        self.mcp_log = mcp_server.AccessLog()
        self.mcp_tools = mcp_server.Tools(lambda: Path(self.settings.get("out_dir") or mo.DEFAULT_OUT),
                                          lambda: {k: v for k, v in self.mcp_cfg().items() if k in ("materials", "notes")})
        self.shutdown = threading.Event()
        self.closing_why = ""         # зачем: "restart" (кнопка) / "update" (новая версия) — чтобы окно написало верно, даже если пропустило промежуточное состояние
        self.closing = ""             # почему эта копия закрывается: "update" (ставит новую версию) / "replaced" (новая просит место); окна по этому закрываются сами
        self.el_lock = threading.Lock()
        self.reader_lock = threading.Lock()     # читалка: по одному запросу к Moodle за раз
        self.electives_checked = 0.0
        self.upd_lock = threading.Lock()      # одна проверка обновлений за раз
        self.upd_install = {"state": "idle", "done": 0, "total": 0, "error": "", "version": ""}   # установка новой версии (только .exe)
        self.offline = {"reason": "", "since": "", "cached": ""}   # офлайн-режим: работаем с сохранённым
        self.relogin_tried = 0.0
        self.tracker = watch.Tracker()          # упал / ожил: по ряду проверок
        self.wlock = watch.Lock(mo.DATA_DIR, "окно")    # кто ведёт автообновление: окно или консольное слежение
        self.wlock_at = -99.0
        self.auto_why = ""                      # почему автообновление пока не запущено (для подписи в окне)
        self.keep_running = ANDROID             # не выходить, когда страницу закрыли (на телефоне браузер часто в фоне)
        self.window_seen = False                # приложение .apk: окно уже запрашивало состояние (для проверки в эмуляторе)
        self.probe_kick = False                 # настройки таймера сменили — проверить сайт сразу, не ждать старого интервала
        self._news = None             # (путь, NewsLog)
        self._cal = None              # (путь, deadlines.Store)
        self.cal_checked = 0.0        # когда в последний раз проверяли «горящие» сроки (monotonic)
        self._cal_badge, self._cal_badge_at = 0, -99.0     # значок на вкладке «Календарь» (кэш на 20 секунд)
        self._search = None           # (путь, search.Index)
        self.search_ev = threading.Event()      # остановка индексации
        self.search_thread = None
        self.search_prog = {"running": False, "phase": "", "done": 0, "total": 0, "current": ""}
        self.search_last = None       # итог последней индексации: {"ok", "text", ...}
        self.ai_jobs: dict = {}       # запросы к нейросети: номер → состояние (последние несколько)
        self.tr_sender = None         # раздача данных по Wi-Fi (transfer.Sender)
        self.tr_ip = None
        self.tr_last = None           # итог последнего переноса: {"ok", "kind", "text", ...}

    # ── где хранятся данные и перенос с прошлой версии ──
    def data_info(self) -> dict:
        mode = mo.PLAN.mode if mo.DATA_DIR == mo.PLAN.data_dir else "custom"
        return {"dir": str(mo.DATA_DIR), "mode": mode, "out": str(self.settings.get("out_dir") or mo.DEFAULT_OUT),
                "notice": datadir.pending_notice(mo.DATA_DIR)}

    def data_import(self, path: str) -> dict:
        """Копирует данные прошлой версии из указанной папки и сразу подхватывает их (вход, настройки, курсы)."""
        try:
            info = datadir.import_from(Path(path), datadir.Plan(mo.PLAN.mode, mo.APP_DIR, mo.DATA_DIR, mo.DEFAULT_OUT), overwrite=True)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": mo.describe_error(e)}
        with self.lock:
            self.settings = self.vault.load_settings()
        self.rep.log(f"Данные перенесены из {info['from']} (файлов: {info['files']}).")
        if self.login_state != "ok" and self.vault.load_secret():
            threading.Thread(target=self.try_saved_login, daemon=True).start()
        return {"ok": True, **info}

    def save_settings(self):
        with self.lock:
            self.vault.save_settings(self.settings)

    def new_client(self, base=None) -> mo.MoodleClient:
        st = self.settings
        return mo.MoodleClient(
            base or st.get("base_url") or mo.DEFAULT_URL,
            page_delay=mo.parse_delay(st.get("page_delay"), mo.PAGE_DELAY),
            file_delay=mo.parse_delay(st.get("file_delay"), mo.FILE_DELAY),
            wait_minutes=int(st.get("wait_minutes", mo.WAIT_SERVER_MINUTES)),
            use_system_proxy=bool(st.get("use_system_proxy")),
            control=self.idle_control, reporter=self.rep)

    # ── светофор ──
    def set_net(self, status, detail="", latency=None):
        with self.lock:
            prev = self.net["status"]
            self.net = {"status": status, "detail": detail, "latency": latency,
                        "checked": datetime.now().strftime("%H:%M:%S"), "paused": ""}
        if prev == "down" and status in ("ok", "slow"):
            self.on_server_up()

    def note_poll(self):
        """Окно спросило состояние: оно живо и ничего не закрывало."""
        self.last_poll = time.monotonic()
        self.polled = True
        self.bye_at = 0.0

    def window_gone(self, now: float | None = None) -> bool:
        """Окна больше нет? Решает только «прощание» окна (/api/bye) — а не тишина: у свёрнутого окна тишина в порядке вещей."""
        now = time.monotonic() if now is None else now
        if self.bye_at:
            return now - self.bye_at > WINDOW_BYE_GRACE
        if not self.polled:
            return now - self.started > WINDOW_FIRST_WAIT
        return now - self.last_poll > WINDOW_SILENCE_LIMIT

    def heartbeat_loop(self):
        """Светофор: лёгкий запрос к сайту по таймеру из настроек (по умолчанию раз в 30 с, во время загрузки — не чаще раза в минуту).
        Заодно раз в 5 с проверяем, открыто ли окно (если закрыли и ничего не качается — выходим) и не пора ли автообновление."""
        next_probe = 0.0
        while not self.shutdown.is_set():
            try:
                now = time.monotonic()
                if now >= next_probe or self.probe_kick:
                    self.probe_kick = False
                    chk = watch.normalize(self.settings.get("watch"))["check"]
                    waiting = bool(self.armed) or self.login_state == "offline"     # ждём Moodle, чтобы войти или начать загрузку
                    active = chk["enabled"] and watch.in_window(chk["window"], datetime.now())
                    if active or waiting:
                        try:
                            prober = self.client or self.new_client()
                            st, dt = prober.probe()
                            self.set_net(st, "" if dt is None else f"{dt:.1f} с", dt)
                            self.watch_feed(st)
                        except Exception:
                            pass
                    else:
                        with self.lock:
                            self.net["paused"] = "проверка выключена" if not chk["enabled"] else "вне заданного времени"
                    interval = chk["interval"] if chk["enabled"] else 60
                    if waiting:
                        interval = min(interval, 300)
                    if self.job_state != "idle":
                        interval = max(interval, HEARTBEAT_BUSY)
                    next_probe = time.monotonic() + interval
                now2 = time.monotonic()
                if now2 - self._tick_at > SLEEP_GAP and not self.bye_at:       # ПК спал или процесс «замораживали»: окно очнётся не сразу
                    self.last_poll = now2
                self._tick_at = now2
                if not self.keep_running and self.window_gone(now2) and self.job_state == "idle" and not self.armed:
                    self.shutdown.set()
                    break
                if (self.login_state == "offline" and self.net["status"] in ("ok", "slow") and self.vault.load_secret()
                        and not self.offline.get("need_login")
                        and time.monotonic() - self.relogin_tried > 120):
                    threading.Thread(target=self._recover_from_offline, daemon=True).start()
                try:
                    self.auto_tick()
                except Exception as e:      # таймер не должен ронять светофор
                    self.rep.log(f"Автообновление: сбой таймера ({mo.describe_error(e)})", "warn")
                if time.monotonic() - self.cal_checked > CAL_WATCH_EVERY:     # напоминания о сроках: без сети, из сохранённого
                    self.cal_checked = time.monotonic()
                    threading.Thread(target=self.cal_watch_tick, daemon=True).start()
                if updater.due(self._upd_cfg()) and not self.upd_lock.locked():       # раз в сутки спросить GitHub о новой версии
                    threading.Thread(target=self.update_check, args=(False,), daemon=True).start()
                hours = int(self.settings.get("electives_watch_hours", 6) or 6)
                if (self.settings.get("electives_watch") and self.job_state == "idle" and self._el_creds()
                        and time.monotonic() - self.electives_checked > hours * 3600):
                    self.electives_checked = time.monotonic()
                    threading.Thread(target=self.electives_refresh, daemon=True).start()
            except Exception as e:      # один сбой не должен навсегда остановить светофор, автообновление и напоминания
                log_error("сторож", e, self.rep)
            self.shutdown.wait(5)

    # ── таймер: упал / ожил и автообновление ──
    def watch_other(self) -> str:
        """Кто ещё (не это окно) ведёт таймер, например консольное слежение; пусто — никто."""
        h = watch.Lock(mo.DATA_DIR).holder()
        if h and h.get("token") != self.wlock.token and time.time() - float(h.get("ts") or 0) < watch.LOCK_STALE:
            return h.get("who") or "другая программа"
        return ""

    def watch_feed(self, status):
        """Результат очередной проверки → события «Moodle недоступен» / «снова работает» (если включены)."""
        cfg = watch.normalize(self.settings.get("watch"))["check"]
        self.tracker.down_after = cfg["down_after"]
        watch.record_check(mo.DATA_DIR, status, time.time())
        if self.watch_other():                 # таймер ведёт консоль — она сама сообщит, чтобы не было двух одинаковых уведомлений
            return
        evs = self.tracker.feed(status, time.time())
        for e in evs:
            self.rep.log("Moodle не отвечает." if e["type"] == "down"
                         else f"Moodle снова работает (не работал {watch.fmt_outage(e['outage'])}).")
        if evs:
            threading.Thread(target=self._watch_notify, args=(evs,), daemon=True).start()

    def _watch_notify(self, evs):
        try:
            mo.watch_notify(evs, self.settings, self.vault, self.rep, Path(self.settings.get("out_dir") or mo.DEFAULT_OUT))
        except Exception as e:
            self.rep.log(f"Уведомление о состоянии Moodle не отправлено: {mo.describe_error(e)}", "warn")

    def auto_ids(self, acfg) -> list:
        out = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
        state_courses = {}
        if acfg["scope"] == "existing" and mo.fexists(out / mo.STATE_FILE):
            state_courses = mo.State(out / mo.STATE_FILE).data["courses"]
        return mo.pick_course_ids(acfg, self.courses, state_courses)

    def auto_tick(self):
        """Раз в 5 с: не пора ли запустить автообновление. Запускает только тот, кто держит замок (не консольное слежение)."""
        acfg = watch.normalize(self.settings.get("watch"))["auto"]
        if not acfg["enabled"]:
            if self.wlock_at > 0:
                self.wlock.release()
                self.wlock_at = -99.0
            self.auto_why = "выключено"
            return
        if time.monotonic() - self.wlock_at > 30:        # замок обновляем раз в полминуты
            if not self.wlock.acquire():
                self.wlock_at = -99.0
                self.auto_why = "слежение ведёт консольная программа"
                return
            self.wlock_at = time.monotonic()
        elif self.wlock_at < 0:
            return
        st = watch.load_state(mo.DATA_DIR)
        ns = self.net["status"]
        due, why = watch.auto_due(acfg, st["auto"], datetime.now(), moodle_ok=None if ns == "unknown" else ns != "down",
                                  busy=self.job_state != "idle" or bool(self.armed),
                                  can_login=self.login_state == "ok" or bool(self.vault.load_secret()))
        self.auto_why = why
        if due:
            self.rep.log(f"Автообновление: запускаю ({why}).")
            self.auto_run()

    def auto_run(self) -> dict:
        """Сверить диск и докачать недостающее/новое по выбранным курсам. Тот же путь, что у кнопки «Быстрая синхронизация»."""
        acfg = watch.normalize(self.settings.get("watch"))["auto"]
        ids = self.auto_ids(acfg)
        now = time.time()
        watch.record_attempt(mo.DATA_DIR, now)
        if not ids:
            text = "Автообновление: нет курсов для обновления — выберите курсы в настройках автообновления."
            self.rep.log(text, "warn")
            watch.record_result(mo.DATA_DIR, False, text, now)
            return {"ok": False, "error": text}
        r = self.start_job({"kind": "auto", "sync": True, "auto": True, "course_ids": ids})
        if not r.get("ok"):
            watch.record_result(mo.DATA_DIR, False, r.get("error", "не удалось начать"), now)
        return r

    def watch_info(self) -> dict:
        cfg = watch.normalize(self.settings.get("watch"))
        st = watch.load_state(mo.DATA_DIR)
        s = watch.summary(cfg, st, datetime.now(), self.net["status"])
        return {"cfg": cfg, "summary": s, "why": self.auto_why, "net": dict(self.net), "other": self.watch_other(),
                "scopes": watch.SCOPES, "intervals": list(watch.INTERVALS), "every": list(watch.EVERY), "days": list(watch.DAYS),
                "autostart": self.autostart_info()}

    def watch_save(self, cfg) -> dict:
        with self.lock:
            self.settings["watch"] = watch.normalize(cfg)
        self.save_settings()
        self.auto_why = ""
        self.probe_kick = True
        if self.settings["watch"]["auto"]["enabled"]:
            self.wlock_at = -99.0         # включили рубильник — сразу проверим замок и срок
        return {"ok": True, **self.watch_info()}

    def watch_audit(self) -> dict:
        """Ничего не качает: считает, сколько файлов из выбранных курсов уже на диске и скольких не хватает."""
        acfg = watch.normalize(self.settings.get("watch"))["auto"]
        ids = self.auto_ids(acfg)
        out = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
        a = mo.audit_downloads(out, ids, int(self.settings.get("mode", 1)))
        names = {c.id: c.fullname for c in self.courses}
        rows = [{"id": int(k), "name": names.get(int(k)) or v["name"] or k, "files": v["files"], "missing": v["missing_files"]}
                for k, v in a["courses"].items()]
        return {"ok": True, "scope": len(ids), "files": a["files"], "missing": a["missing_files"], "empty": a["empty"],
                "has_state": a["has_state"], "courses": rows, "dir": str(out)}

    def android_info(self) -> dict:
        """Для окна на телефоне: где лежат материалы и видны ли они приложению «Файлы»."""
        out, home = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT), Path.home()
        sh = android.shared_storage(home)
        info = {"out": str(out), "shared": bool(sh), "private_out": android.in_private_storage(out, home),
                "suggest": str(android.default_materials(home, datadir.LEGACY_OUT)) if sh else "",
                "kind": "apk" if android.is_apk() else "termux"}
        if android.is_apk():
            info["battery_ok"] = android.battery_exempt()
        return info

    def android_action(self, name: str) -> dict:
        """Системные экраны Android, которые может открыть только приложение (.apk): доступ к файлам, батарея."""
        if not android.is_apk():
            return {"ok": False, "error": "это действие есть только в приложении для Android"}
        if name == "allfiles":
            return {"ok": android.request_all_files()}
        if name == "battery":
            return {"ok": android.request_battery()}
        return {"ok": False, "error": "неизвестное действие"}

    # ── слежение в фоне без окна (автозапуск Windows) ──
    def autostart_info(self) -> dict:
        if android.is_apk():      # приложение само запускает службу при включении телефона
            return {"supported": True, "kind": "apk-boot", "on": android.apk_autostart_get(), "path": "", "app_ready": True}
        if ANDROID:               # Termux:Boot запускает скрипты из ~/.termux/boot при включении телефона
            home = Path.home()
            p = android.boot_path(home)
            return {"supported": True, "kind": "termux-boot", "on": p.exists(), "path": str(p),
                    "app_ready": android.boot_dir(home).is_dir()}
        p = watch.startup_path(os.environ)
        return {"supported": os.name == "nt", "kind": "windows", "on": bool(p and p.exists()), "path": str(p or "")}

    def autostart_set(self, enabled: bool) -> dict:
        if android.is_apk():
            if not android.apk_autostart_set(enabled):
                return {"ok": False, "error": "не получилось сохранить настройку автозапуска"}
            return {"ok": True, **self.autostart_info()}
        if ANDROID:
            p = android.boot_path(Path.home())
            try:
                if enabled:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(android.boot_script([sys.executable, str(mo.APP_DIR / "moodle_offline.py"), "--watch", "-y"]), encoding="utf-8")
                    p.chmod(0o755)
                elif p.exists():
                    p.unlink()
            except OSError as e:
                return {"ok": False, "error": mo.describe_error(e)}
            return {"ok": True, **self.autostart_info()}
        if os.name != "nt":
            return {"ok": False, "error": "автозапуск из окна есть только в Windows; в Linux и Android используйте systemd/cron/Termux:Boot (см. README)"}
        p = watch.startup_path(os.environ)
        if p is None:
            return {"ok": False, "error": "не нашёл папку автозагрузки Windows"}
        try:
            if enabled:
                if mo.FROZEN:
                    cmd = [sys.executable, "--watch", "-y"]
                else:
                    exe = Path(sys.executable)
                    quiet = exe.with_name("pythonw.exe")
                    cmd = [str(quiet if quiet.exists() else exe), str(mo.APP_DIR / "moodle_offline.py"), "--watch", "-y"]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(watch.startup_script(cmd), encoding="utf-16")    # WSH читает Юникод только с BOM (иначе кириллица в пути ломается)
            elif p.exists():
                p.unlink()
        except OSError as e:
            return {"ok": False, "error": mo.describe_error(e)}
        return {"ok": True, **self.autostart_info()}

    def on_server_up(self):
        if self.login_state == "offline" and self.vault.load_secret() and not self.offline.get("need_login"):
            threading.Thread(target=self._recover_from_offline, daemon=True).start()
            return
        if self.armed and self.job_state == "idle":
            params, self.armed = self.armed, None
            mo.notify(mo.APP_NAME, "Moodle снова доступен! Начинаю загрузку.")
            self.rep.log("Moodle снова доступен! Начинаю загрузку.")
            self.start_job(params)

    def _recover_from_offline(self):
        """Moodle ожил: входим по сохранённому входу, обновляем курсы и запускаем отложенную загрузку."""
        if time.monotonic() - self.relogin_tried < 20:
            return
        self.relogin_tried = time.monotonic()
        self.try_saved_login(True)
        if self.login_state == "ok" and self.armed and self.job_state == "idle":
            params, self.armed = self.armed, None
            mo.notify(mo.APP_NAME, "Moodle снова доступен! Начинаю загрузку.")
            self.rep.log("Moodle снова доступен! Начинаю загрузку.")
            self.start_job(params)

    def check_geo(self):
        ip, cc, svc = mo.check_ip_country(bool(self.settings.get("use_system_proxy")))
        warn = ""
        if cc and cc != "RU":
            host = mo.urlparse(mo.normalize_base(self.settings.get("base_url"))).netloc
            warn = mo.GEO_WARNING.format(ip=ip, cc=cc, host=host)
        with self.lock:
            self.geo = {"ip": ip, "cc": cc, "checked": True, "warning": warn, "service": svc}
        return self.geo

    # ── вход ──
    def try_saved_login(self, from_offline=False):
        secret = self.vault.load_secret()
        if not secret:
            return
        base = secret.get("base_url") or self.settings.get("base_url")
        if not from_offline:
            self.login_state = "logging"
        try:
            client = self.new_client(base)
            client.no_wait = True          # при запуске не ждём сервер часами: нет сайта — открываемся офлайн
            if client.probe()[0] == "down":
                raise mo.NetworkDown("Moodle не отвечает")
            ok = False
            if secret.get("cookies"):
                client.import_cookies(secret["cookies"])
                ok = client.verify()
            if not ok and secret.get("username") and secret.get("password"):
                client.s.cookies.clear()
                client.login(secret["username"], secret["password"])
                ok = True
            if not ok:
                if from_offline:   # сайт ожил, но сохранённый вход устарел — остаёмся офлайн, просим войти
                    self.offline.update(reason="сохранённый вход устарел — нажмите «Войти»", need_login=True)
                else:
                    self.login_state = "idle"
                return
            if secret.get("username") and secret.get("password"):
                client.creds = (secret["username"], secret["password"])
            self._after_login(client, secret.get("username", ""), remember=True)
            client.no_wait = False
            if from_offline:
                mo.notify(mo.APP_NAME, "Moodle снова доступен — вошёл, список курсов обновлён.")
        except Exception as e:
            net = isinstance(e, (mo.NetworkDown, mo.RetriesExhausted)) or "нет связи" in str(e) or "timed out" in str(e)
            if net or from_offline:
                if self.login_state != "offline":
                    self.go_offline("Moodle недоступен" if net else f"вход не удался: {mo.describe_error(e)}")
            else:
                self.login_state = "idle"
            self.rep.log(f"Автовход не удался: {mo.describe_error(e)}", "warn")

    def login(self, base, username, password, remember, cookie=""):
        self.login_state, self.login_error = "logging", ""
        try:
            client = self.new_client(base)
            if cookie:
                client.use_cookie_string(cookie)
                if not client.verify():
                    raise mo.LoginError("cookie недействителен или устарел")
            else:
                client.login(username, password)
            self.settings["base_url"] = client.base
            self.settings["username"] = username
            self.settings["remember"] = bool(remember)
            self.save_settings()
            if remember:
                self.vault.save_secret({"base_url": client.base, "username": username,
                                        "password": password, "cookies": client.export_cookies()})
            else:
                self.vault.forget()
            self._after_login(client, username, remember)
            return {"ok": True}
        except Exception as e:
            self.login_state, self.login_error = "error", mo.describe_error(e)
            hint = ""
            if "неверн" in self.login_error.lower() or "invalid" in self.login_error.lower():
                hint = ("Moodle пишет так и при неверном пароле, и при сбое формы входа. Проверьте вход на сайте в браузере. "
                        "Не пробуйте много раз подряд: после нескольких ошибок Moodle может временно заблокировать вход. "
                        "Пока можно войти по cookie (ссылка «Вход по cookie»). "
                        f"Диагностика без пароля сохранена в {mo.DATA_DIR / 'login_debug.txt'}")
            return {"ok": False, "error": self.login_error, "hint": hint}

    def _after_login(self, client, username, remember):
        if remember:
            client.on_cookies_changed = lambda: self.vault.save_secret(
                {**self.vault.load_secret(), "base_url": client.base, "cookies": client.export_cookies()})
        self.client = client
        self.user_name = username
        try:
            soup, _ = client.page(client.base + "/my/")
            um = soup.select_one(".usermenu .usertext, .usermenu .login, .usertext")
            if um is not None and mo.clean_text(um.get_text(" ")):
                self.user_name = mo.clean_text(um.get_text(" "))
        except Exception:
            pass
        try:
            self._save_sso([{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "secure": bool(c.secure)}
                            for c in client.s.cookies if client.hostname not in (c.domain or "")])
        except Exception:
            pass
        self.courses = client.get_courses()
        self.login_state = "ok"
        self.offline = {"reason": "", "since": "", "cached": ""}
        if remember:
            self.save_offline_cache()
        self.rep.log(f"Вход выполнен. Курсов: {len(self.courses)}.")

    # ── офлайн-режим: Moodle не работает, а программа — да ──
    OFFLINE_FILE = mo.DATA_DIR / "offline.json"

    def save_offline_cache(self):
        try:
            mo.fmkdir(mo.DATA_DIR)
            mo.write_text_atomic(self.OFFLINE_FILE, json.dumps({
                "user": self.user_name, "username": self.settings.get("username", ""),
                "base": self.client.base if self.client else self.settings.get("base_url"),
                "saved": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "courses": [c.to_dict() for c in self.courses]}, ensure_ascii=False))
        except Exception:
            pass

    def load_offline_cache(self) -> dict:
        try:
            with mo.fopen(self.OFFLINE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def go_offline(self, reason="без входа"):
        """Показываем всё сохранённое: курсы (из кэша или из папки загрузок), файлы, расписание, элективы."""
        cache = self.load_offline_cache()
        courses = []
        for d in cache.get("courses", []):
            try:
                courses.append(mo.Course(**{k: v for k, v in d.items() if k in mo.Course.__dataclass_fields__}))
            except TypeError:
                pass
        if not courses:   # кэша нет — собираем карточки из уже скачанного
            self.adopt_courses(force=True)
            out = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
            try:
                with mo.fopen(out / mo.STATE_FILE, "r", encoding="utf-8") as f:
                    for cid, sc in (json.load(f).get("courses") or {}).items():
                        if str(cid).isdigit():
                            courses.append(mo.Course(id=int(cid), fullname=sc.get("name") or Path(sc.get("dir") or str(cid)).name))
            except (OSError, ValueError):
                pass
        with self.lock:
            if self.login_state == "ok":
                return
            self.courses = courses
            self.user_name = cache.get("user") or self.settings.get("username", "")
            self.offline = {"reason": reason, "since": datetime.now().strftime("%H:%M"), "cached": cache.get("saved", "")}
            self.login_state = "offline"
        self.rep.log(f"Офлайн-режим ({reason}): курсов в сохранённом списке — {len(courses)}.")

    def logout(self):
        self.vault.forget()
        self.settings.pop("remember", None)
        self.save_settings()
        self.client, self.courses, self.login_state, self.user_name = None, [], "idle", ""
        self._iot_tok = None
        try:
            self.OFFLINE_FILE.unlink()
        except OSError:
            pass

    # ── курсы ──
    def adopt_courses(self, force=False) -> list:
        """Курсы, которые лежат в папке сохранения, но не записаны в состояние (после «Переноса данных» или копирования папки),
        записываем в него. Недорого (читается только список папок), поэтому проверяем не чаще раза в полминуты и не во время
        загрузки: у неё своя копия состояния, и она его перезапишет."""
        now = time.monotonic()
        if not force and (self.job_state != "idle" or now - getattr(self, "_adopt_at", -1e9) < 30):
            return []
        self._adopt_at = now
        try:
            added = mo.adopt_courses(Path(self.settings.get("out_dir") or mo.DEFAULT_OUT))
        except Exception as e:       # необязательная починка: не должна ломать список курсов
            self.rep.log(f"Курсы из папки сохранения не добавлены в список: {mo.describe_error(e)}", "warn")
            return []
        if added:
            self.rep.log(f"В память программы добавлены курсы из папки сохранения: {len(added)}.")
        return added

    def course_list(self):
        self.adopt_courses()
        out = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
        state_courses = {}
        try:
            with mo.fopen(out / mo.STATE_FILE, "r", encoding="utf-8") as f:
                state_courses = json.load(f).get("courses", {})
        except (OSError, ValueError):
            pass
        res = []
        grades = (mo.load_grades(out).get("courses") or {})
        for c in self.courses:
            d = c.to_dict()
            d["grade"] = (grades.get(str(c.id)) or {}).get("grade", "")
            sc = state_courses.get(str(c.id)) or {}
            d["downloaded"] = sc.get("files", 0)
            d["updated"] = sc.get("updated", "")
            d["dir"] = sc.get("dir", "")
            d["image"] = c.image if c.image.startswith("data:") else (f"/api/cover?id={c.id}" if c.image else "")
            res.append(d)
        return res

    def cover(self, cid):
        c = next((c for c in self.courses if c.id == cid), None)
        if not c or not c.image or c.image.startswith("data:"):
            return None
        cache = mo.DATA_DIR / "covers"
        p = cache / f"{cid}.bin"
        meta = cache / f"{cid}.type"
        if p.exists() and meta.exists():
            return p.read_bytes(), meta.read_text()
        if self.job_state != "idle" or not self.client:
            return None   # во время загрузки обложки не качаем — меньше нагрузки на сервер
        try:
            data, ctype = self.client.fetch_bytes(c.image, kind="image", limit=3 * 1024 * 1024)
        except Exception:
            return None
        cache.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        meta.write_text(ctype)
        return data, ctype

    # ── просмотр курса «как на сайте» ──
    def course_structure(self, cid: int, live=False):
        if not live:
            self.adopt_courses()
        out = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
        sc = {}
        try:
            with mo.fopen(out / mo.STATE_FILE, "r", encoding="utf-8") as f:
                sc = json.load(f).get("courses", {}).get(str(cid)) or {}
        except (OSError, ValueError):
            pass
        if sc.get("dir") and not live:
            try:
                with mo.fopen(out / sc["dir"] / ".course.json", "r", encoding="utf-8") as f:
                    d = json.load(f)
                d["downloaded"] = True
                try:
                    res = reserve.versions(out / sc["dir"])
                    if res:
                        d["reserve"] = res
                except OSError:
                    pass
                return d
            except (OSError, ValueError):
                pass
        cache = mo.DATA_DIR / "structure" / f"{cid}.json"
        if cache.exists() and not live:
            d = json.loads(cache.read_text(encoding="utf-8"))
            d["downloaded"] = False
            return d
        if not self.client:
            return {"error": "Moodle сейчас недоступен — этот курс ещё не открывался и не скачивался. "
                             "Как только сайт оживёт, программа войдёт сама." if self.login_state == "offline"
                    else "нужно войти в Moodle"}
        if self.job_state != "idle":
            return {"error": "идёт загрузка — структуру курса покажу, когда она закончится"}
        known = next((c for c in self.courses if c.id == cid), None)
        course = mo.Course(**known.to_dict()) if known else mo.Course(id=cid)
        try:
            self.client.parse_course(course)
        except Exception as e:
            return {"error": mo.describe_error(e)}
        mo.classify_course(course)
        d = {"id": course.id, "fullname": course.fullname, "url": course.url, "category": course.category, "dir": "",
             "updated": datetime.now().strftime("%d.%m.%Y %H:%M"), "sections": [
                 {"number": s.number, "name": s.name, "restriction": s.restriction, "items": [
                     {"key": it.key, "name": it.name, "modtype": it.modtype, "kind": it.kind, "url": it.url,
                      "description": it.description, "context": it.context, "online_only": it.online_only, "files": [],
                      "done": it.done, "restriction": it.restriction}
                     for it in s.items]} for s in course.sections]}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        d["downloaded"] = False
        return d

    # ── читалка: открыть страницу/файл элемента курса внутри программы ──
    READER_TTL = 600           # свежая копия страницы (сек): внутри этого срока повторно не ходим на сайт
    READER_INLINE = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "application/pdf", "text/plain")

    def _reader_host(self) -> str:
        base = self.client.base if self.client else (self.settings.get("base_url") or "https://do.sevsu.ru")
        return mo.urlparse(mo.normalize_base(base)).netloc.lower()

    def _reader_online(self) -> bool:
        return bool(self.client) and self.job_state == "idle" and self.login_state != "offline"

    def reader_page(self, url, live=False):
        host = self._reader_host()
        url = reader.norm_url(url or "")
        if not reader.same_site(url, host):
            return {"ok": False, "error": "это адрес не с сайта Moodle — откройте его в браузере"}
        cached = reader.load(mo.DATA_DIR, url)

        def from_cache(note):
            d = dict(cached, ok=True, cached=True, note=note)
            d["saved"] = datetime.fromtimestamp(cached["ts"]).strftime("%d.%m.%Y %H:%M")
            return d
        online = self._reader_online()
        if cached and not live and (not online or time.time() - cached["ts"] < self.READER_TTL):
            return from_cache("" if online else "нет связи с Moodle — показана сохранённая копия")
        if not online:
            why = "идёт загрузка" if self.job_state != "idle" else "Moodle сейчас недоступен"
            return {"ok": False, "error": f"{why}, а эта страница здесь ещё не открывалась. Откройте её, когда будет связь, — "
                                           "копия сохранится и потом будет читаться без интернета."}
        with self.reader_lock, self._no_wait():
            try:
                for attempt in (1, 2):
                    r, final = self.client.fetch_internal(url, kind="page", stream=True)
                    ct = (r.headers.get("Content-Type") or "").lower()
                    if ct and "html" not in ct:                  # по адресу лежит файл, а не страница
                        name = self._reader_name(r, final)
                        r.close()
                        return {"ok": True, "file": final, "name": name, "ct": ct.split(";")[0]}
                    text = mo.MoodleClient._text(r)
                    r.close()
                    if 'id="page-login-index"' in text and attempt == 1 and self.client.relogin():
                        continue
                    break
                if 'id="page-login-index"' in text:
                    raise mo.SessionExpired("сессия истекла, требуется вход")
            except mo.ExternalLink as e:
                return {"ok": True, "external": e.url}
            except Exception as e:
                msg = mo.describe_error(e)
                if cached:
                    return from_cache(f"не удалось обновить ({msg}) — показана сохранённая копия")
                return {"ok": False, "error": msg}
        rec = reader.render(text, final, host)
        reader.save(mo.DATA_DIR, url, rec)
        rec = dict(rec, ok=True, cached=False, url=url, note="")
        rec["saved"] = datetime.now().strftime("%d.%m.%Y %H:%M")
        return rec

    @contextlib.contextmanager
    def _no_wait(self):
        """Читалка не ждёт «оживания» сайта часами, как загрузка: сайт не отвечает — сразу говорим и показываем копию."""
        c = self.client
        old = c.no_wait if c else False
        if c:
            c.no_wait = True
        try:
            yield
        finally:
            if c:
                c.no_wait = old

    @staticmethod
    def _reader_name(r, url) -> str:
        cd = r.headers.get("Content-Disposition") or ""
        m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
        name = unquote(m.group(1)) if m else unquote(mo.urlparse(url).path.rsplit("/", 1)[-1])
        return mo.safe_name(name) or "файл"

    def reader_file_open(self, url):
        """Файл/картинка этого Moodle для показа. → {"error"} | {"path", "ct"} (из кэша) | {"resp", "ct", "name", "cache"}."""
        host = self._reader_host()
        url = reader.norm_url(url or "")
        if not reader.same_site(url, host):
            return {"error": "адрес не с сайта Moodle", "code": 400}
        ip = reader.image_path(mo.DATA_DIR, url)
        if ip.is_file():
            try:
                return {"path": ip, "ct": ip.with_name(ip.name + ".ct").read_text(encoding="utf-8").strip() or "image/png", "name": ip.name}
            except OSError:
                pass
        if not self._reader_online():
            return {"error": "файл не сохранён, а Moodle сейчас недоступен", "code": 503}
        return {"url": url}

    def reader_file_fetch(self, url):
        """Запрашивает файл у Moodle (под замком читалки). → (ответ, тип, имя) или бросает исключение."""
        r, final = self.client.fetch_internal(url, kind="image" if url.lower().split("?")[0].endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")) else "file", stream=True)
        ct = (r.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
        return r, ct, self._reader_name(r, final)

    # ── обновление самой программы ──
    def _upd_cfg(self) -> dict:
        return updater.normalize(self.settings.get("update"))

    def _upd_kind(self) -> str:
        return updater.kind(frozen=bool(getattr(sys, "frozen", False)), apk=android.is_apk(), android=ANDROID)

    def _exe_path(self) -> str:
        return os.path.abspath(sys.executable)

    def update_status(self) -> dict:
        st = updater.status(self._upd_cfg(), mo.VERSION, self._upd_kind())
        lt = st.get("latest") or {}
        # «Обновить сейчас» есть только у Windows-программы (.exe), когда в релизе есть и файл, и контрольная сумма, и рядом можно писать
        can = bool(st["kind"] == "exe" and st["newer"] and lt.get("asset_url") and lt.get("sha_url") and updater.can_write_next_to(self._exe_path()))
        with self.lock:
            inst = dict(self.upd_install)
        return {"ok": True, **st, "can_install": can, "install": inst}

    def update_install_start(self) -> dict:
        """Скачать новую версию в фоне (рядом с программой), проверить контрольную сумму. Подмена и перезапуск — отдельной кнопкой."""
        st = self.update_status()
        if not st["can_install"]:
            return {"ok": False, "error": "Из программы обновиться нельзя (не Windows-версия, нет файла в релизе или папку программы нельзя менять) — скачайте файл вручную."}
        with self.lock:
            if self.upd_install["state"] in ("downloading", "ready"):
                return {"ok": True, **self.update_status_unlocked()}
            self.upd_install = {"state": "downloading", "done": 0, "total": 0, "error": "", "version": st["latest"]["version"]}
        threading.Thread(target=self._update_install_job, args=(st["latest"],), daemon=True).start()
        return {"ok": True, **self.update_status()}

    def update_status_unlocked(self) -> dict:
        return {"install": dict(self.upd_install)}

    def _update_install_job(self, lt: dict) -> None:
        exe = self._exe_path()
        proxy = bool(self.settings.get("use_system_proxy", True))

        def prog(done, total):
            with self.lock:
                self.upd_install["done"], self.upd_install["total"] = done, total

        try:
            sha = updater.fetch_expected_sha(lt.get("sha_url", ""), mo.VERSION, proxy)
            updater.download_verified(lt["asset_url"], updater.new_path(exe), sha, int(lt.get("size") or 0), progress=prog,
                                      stop=lambda: self.shutdown.is_set(), current=mo.VERSION, use_system_proxy=proxy)
            with self.lock:
                self.upd_install["state"] = "ready"
            self.rep.log(f"Новая версия {lt['version']} скачана и проверена. Нажмите «Перезапустить и обновить».")
        except updater.UpdateError as e:
            with self.lock:
                self.upd_install = {"state": "error", "done": 0, "total": 0, "error": str(e), "version": lt.get("version", "")}
            self.rep.log(f"Обновление не скачалось: {e}", "warn")
        except Exception as e:                                        # noqa: BLE001 — любая неожиданность не должна оставить «скачиваю…» навсегда
            with self.lock:
                self.upd_install = {"state": "error", "done": 0, "total": 0, "error": f"{type(e).__name__}: {e}", "version": lt.get("version", "")}
            self.rep.log(f"Обновление не скачалось: {type(e).__name__}: {e}", "warn")

    def update_restart(self) -> dict:
        """Подменить программу скачанным файлом и запустить новую; эта копия закрывается (идущая загрузка останавливается и продолжится позже)."""
        with self.lock:
            ready = self.upd_install["state"] == "ready"
        exe = self._exe_path()
        if not ready or not os.path.exists(updater.new_path(exe)):
            return {"ok": False, "error": "Новая версия ещё не скачана"}
        try:
            updater.swap_exe(exe, updater.new_path(exe))
        except updater.UpdateError as e:
            with self.lock:
                self.upd_install = {"state": "error", "done": 0, "total": 0, "error": str(e), "version": ""}
            return {"ok": False, "error": str(e)}
        err = ""
        try:
            updater.launch(exe)
        except updater.UpdateError as e:
            err = str(e)
        if err:                                   # файл уже новый, но сам не запустился: эту копию не закрываем, пусть человек решит
            return {"ok": False, "error": err}
        self.rep.log("Перезапускаюсь в новую версию…")
        self.closing = "update"                   # окно увидит это и дождётся, пока новая копия займёт место
        self.closing_why = "update"
        if self.control:
            self.control.stop()                   # идущая загрузка останавливается сразу

        def _bye():
            # Новая копия стартует несколько секунд, а потом сама просит эту закрыться (/api/quit): окно в этот момент закрывается
            # само и открывается её окно. Если она так и не пришла (не запустилась) — не висим вечно и закрываемся сами.
            self.shutdown.wait(25)
            self.shutdown.set()
        threading.Thread(target=_bye, daemon=True).start()
        return {"ok": True}

    def restart_cmd(self) -> list:
        """Чем запустить программу заново: сам .exe или тот же Python с этим файлом (так же, как делает Запустить.bat)."""
        if getattr(sys, "frozen", False):
            return [os.path.abspath(sys.executable)]
        return [sys.executable, str(Path(__file__).resolve())]

    def restart(self) -> dict:
        """Перезапуск программы по кнопке: запускаем новую копию с пометкой «перезапуск», она просит эту закрыться (/api/quit),
        окно этой копии закрывается само, открывается окно новой. Работает, пока эта копия жива (даже если окно ждёт ответа);
        «мёртвую» программу окно запустить не может — для этого она должна быть запущена как-то снаружи."""
        if ANDROID:
            return {"ok": False, "error": "На телефоне перезапуска нет: закройте приложение и откройте снова."}
        cmd = self.restart_cmd()
        try:
            updater.launch_cmd(cmd, cwd=os.path.dirname(cmd[-1]) or None, extra_env={"MOODLE_OFFLINE_RESTART": "1"})
        except updater.UpdateError as e:
            return {"ok": False, "error": "Не получилось перезапустить: " + str(e)}
        self.rep.log("Перезапускаюсь…")
        self.closing = "restart"                  # окно увидит это и дождётся, пока новая копия займёт место
        self.closing_why = "restart"

        def _wait():
            # Новая копия стартует несколько секунд, потом сама просит эту закрыться (/api/quit). Не пришла — не закрываемся: лучше остаться, чем потерять программу.
            if not self.shutdown.wait(40):
                self.closing = self.closing_why = ""
                self.rep.log("Перезапуск не получился: новая копия не ответила. Работаю как раньше.", "warn")
        threading.Thread(target=_wait, daemon=True).start()
        return {"ok": True}

    def update_brief(self) -> dict:
        """Для общего состояния окна: без сети, только «есть ли новая версия»."""
        st = updater.status(self._upd_cfg(), mo.VERSION, self._upd_kind())
        lt = st.get("latest") or {}
        return {"newer": bool(st["newer"] and not st["skipped"]), "version": lt.get("version", ""), "tag": lt.get("tag", "")}

    def update_check(self, manual=True) -> dict:
        """Спросить GitHub о последней версии. manual=False — по таймеру: тогда о новой версии скажет уведомление."""
        if not self.upd_lock.acquire(blocking=False):
            return self.update_status()
        try:
            k = self._upd_kind()
            cfg = updater.run_check(self._upd_cfg(), mo.VERSION, k,
                                    fetch=updater.fetch_latest)
            st = updater.status(cfg, mo.VERSION, k)
            if updater.should_notify(cfg, st):
                cfg["notified"] = st["latest"]["tag"]
                if not manual:
                    mo.notify(mo.APP_NAME, updater.notice_text(st))
                    self.rep.log(f"Вышла новая версия {st['latest']['version']} (у вас {mo.VERSION}).")
            with self.lock:
                self.settings["update"] = cfg
            self.save_settings()
            return {"ok": True, **st}
        finally:
            self.upd_lock.release()

    def update_settings(self, b: dict) -> dict:
        with self.lock:
            cfg = self._upd_cfg()
            if "auto" in b:
                cfg["auto"] = bool(b["auto"])
            if "every" in b:
                try:
                    h = int(b["every"])
                except (TypeError, ValueError):
                    h = 0
                if h in updater.EVERY_HOURS:
                    cfg["every"] = h
            if "skip" in b:                     # пропустить версию (tag) или снять пропуск (пустая строка)
                cfg["skip"] = str(b["skip"] or "")
            self.settings["update"] = cfg
        self.save_settings()
        return self.update_status()

    # ── расписание ──
    def schedule_status(self):
        out = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
        st = self.settings
        slug = st.get("schedule_group") or sched.DEFAULT_GROUP
        info = {"group": slug, "group_name": sched.group_name(slug),
                "sources": st.get("schedule_sources") or list(sched.DEFAULT_SOURCES),
                "order": list(sched.SOURCE_ORDER), "short": {k: v["short"] for k, v in sched.SOURCES.items()},
                "auth": {k: bool(v.get("auth")) for k, v in sched.SOURCES.items()}, "has_creds": self._el_creds() is not None,
                "urls": {k: (st.get("schedule_urls") or {}).get(k) or v["url"] for k, v in sched.SOURCES.items()},
                "titles": {k: v["title"] for k, v in sched.SOURCES.items()},
                "pdf": bool(st.get("schedule_pdf", True)), "auto": bool(st.get("schedule_auto", True)),
                "auto_range": st.get("schedule_auto_range") if st.get("schedule_auto_range") in sched.AUTO_RANGES else "2weeks",
                "published_until": "", "saved": False, "count": 0, "updated": "", "first": "", "last": "", "dir": sched.SCHEDULE_DIR,
                "excel": self.excel_status()}
        try:
            sched.refresh_viewer(out / sched.SCHEDULE_DIR)      # после обновления программы страница сама получает новый вид
            for pr in book.list_profiles(out, slug)[1:]:        # …и у чужих расписаний тоже
                sched.refresh_viewer(out / Path(*pr["dir"].split("/")))
        except Exception:
            pass
        try:
            with mo.fopen(out / sched.SCHEDULE_DIR / "schedule.json", "r", encoding="utf-8") as f:
                d = json.load(f)
            ls = d.get("lessons", [])
            info.update(saved=True, count=len(ls), updated=d.get("meta", {}).get("generated", ""),
                        first=ls[0]["date"] if ls else "", last=ls[-1]["date"] if ls else "")
            pub = d.get("meta", {}).get("published_until", "")
            if pub and pub < sched.semester_end(date.today()).isoformat():   # показываем, только если сайт выложил не весь семестр
                info["published_until"] = pub
        except (OSError, ValueError, KeyError, IndexError):
            pass
        try:
            info["profiles"] = book.list_profiles(out, slug, info["group_name"])    # своё + чужие (история), для переключателя
        except Exception:
            info["profiles"] = []
        info["android_share"] = bool(ANDROID)
        return info

    # ── чужие расписания, история, файл для друзей ──
    def _out(self) -> Path:
        return Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)

    def other_save(self, b):
        """Добавить чужую группу (или обновить уже добавленную) и сразу скачать её расписание."""
        text = (b.get("group") or "").strip()
        if not text:
            return {"ok": False, "error": "укажите группу"}
        slug = sched.resolve_group(text)
        own = self.settings.get("schedule_group") or sched.DEFAULT_GROUP
        if not book.valid_slug(slug):
            return {"ok": False, "error": "не понял код группы — выберите её из списка СевГУ или напишите как на сайте (ИБ/б-25-1-о)"}
        if slug == own:
            return {"ok": False, "error": "это ваша группа — её расписание настраивается выше, в блоке «Группа и источники»"}
        sources = [x for x in (b.get("sources") or ["schedule"]) if x in ("schedule", "excel")] or ["schedule"]
        out = self._out()
        book.ensure_profile(out, slug, sched.group_name(slug) or text)
        book.write_profile(book.profile_dir(out, slug), sources=sources)
        book.touch(out, slug)
        r = self.start_job({"kind": "schedule", "profile": slug, "sources": sources, "range": b.get("range", "semester"),
                            "from": b.get("from"), "to": b.get("to")})
        return {**r, "slug": slug} if isinstance(r, dict) else r

    def other_refresh(self, b):
        slug = b.get("slug") or ""
        if not book.valid_slug(slug) or not (book.profile_dir(self._out(), slug)).is_dir():
            return {"ok": False, "error": "такого расписания нет"}
        book.touch(self._out(), slug)
        return self.start_job({"kind": "schedule", "profile": slug, "range": b.get("range", "semester")})

    def other_flags(self, b):
        slug = b.get("slug") or ""
        if not book.valid_slug(slug) or not (book.profile_dir(self._out(), slug)).is_dir():
            return {"ok": False, "error": "такого расписания нет"}
        kw = {}
        if "auto" in b:
            kw["auto"] = bool(b["auto"])
        if "sources" in b:
            kw["sources"] = [x for x in (b["sources"] or []) if x in ("schedule", "excel")] or ["schedule"]
        book.write_profile(book.profile_dir(self._out(), slug), **kw)
        return {"ok": True}

    def other_delete(self, b):
        try:
            ok = book.delete_profile(self._out(), b.get("slug") or "")
        except book.BookError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": ok, "error": "" if ok else "не удалось удалить — закройте файлы расписания и повторите"}

    def profile_use(self, b):
        slug = b.get("slug") or ""
        if book.valid_slug(slug):
            book.touch(self._out(), slug)
        return {"ok": True}

    def schedule_share(self, b):
        """Собрать .zip из выбранных расписаний (для друга: откроется и без программы, а в программу добавляется кнопкой «Загрузить из файла»)."""
        out = self._out()
        slug = self.settings.get("schedule_group") or sched.DEFAULT_GROUP
        try:
            z, notes = book.export_zip(out, [str(i) for i in (b.get("ids") or [])], slug, sched.group_name(slug), mo.VERSION)
        except book.BookError as e:
            return {"ok": False, "error": str(e)}
        shared = bool(ANDROID and b.get("send") and android.share_file(z))
        return {"ok": True, "path": z.relative_to(out).as_posix(), "name": z.name, "notes": notes, "shared": shared}

    def schedule_import(self, rfile, length, name):
        if length <= 0 or length > book.MAX_UNPACKED:
            return {"ok": False, "error": "файл пустой или слишком большой для расписания"}
        data = rfile.read(length)
        if len(data) != length:
            return {"ok": False, "error": "файл передался не до конца — попробуйте ещё раз"}
        try:
            added = book.import_zip(self._out(), data)
        except book.BookError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "added": added}

    def schedule_groups(self, dept=None, refresh=False):
        """Отделы и группы schedule.sevsu.ru для выпадающего списка; кэш на неделю — работает и офлайн."""
        cache = sched.load_groups_cache()
        fresh = time.time() - cache.get("ts", 0) < 7 * 86400 and not refresh
        err = ""
        try:
            c = mo.MoodleClient(sched.SEVSU_BASE, page_delay=(0.2, 0.5), wait_minutes=1, control=mo.Control(),
                                reporter=self.rep, use_system_proxy=bool(self.settings.get("use_system_proxy")))
            c.no_wait = True
            if not cache.get("departments") or not fresh:
                deps, cur = sched.fetch_departments(c, self.settings.get("schedule_group") or sched.DEFAULT_GROUP)
                if deps:
                    cache.update(departments=deps, current=cur or cache.get("current", ""), ts=time.time())
            dept = dept or cache.get("current") or ""
            if dept and (dept not in cache["groups"] or not fresh or dept != cache.get("current")):
                cache["groups"][dept] = sched.fetch_groups(c, dept)
            sched.save_groups_cache(cache)
        except Exception as e:
            err = mo.describe_error(e)
            dept = dept or cache.get("current") or ""
        return {"ok": bool(cache.get("departments")), "error": err, "offline": bool(err),
                "departments": cache.get("departments", []), "current": dept,
                "groups": cache["groups"].get(dept, []), "selected": self.settings.get("schedule_group") or sched.DEFAULT_GROUP}

    # ── Excel-расписание университета ──
    def excel_status(self):
        st = self.settings
        cfg = st.get("schedule_excel") or {}
        out = Path(st.get("out_dir") or mo.DEFAULT_OUT)
        info = {"mode": cfg.get("mode") if cfg.get("mode") in ("site", "local") else "auto",
                "name": cfg.get("name") or "", "text": (cfg.get("sig") or {}).get("text", ""),
                "ctx": " › ".join((cfg.get("sig") or {}).get("ctx", [])), "on": "excel" in (st.get("schedule_sources") or []),
                "last": None}
        try:
            with mo.fopen(out / sched.SCHEDULE_DIR / "data" / "excel.json", "r", encoding="utf-8") as f:
                d = json.load(f)
            ls = d.get("lessons", [])
            info["last"] = {"updated": d.get("updated", ""), "file": d.get("file", ""), "count": len(ls),
                            "first": ls[0]["date"] if ls else "", "last": ls[-1]["date"] if ls else ""}
        except (OSError, ValueError, KeyError, IndexError):
            pass
        return info

    def _excel_enable(self):
        src = list(self.settings.get("schedule_sources") or sched.DEFAULT_SOURCES)
        if "excel" not in src:
            src.append("excel")
        self.settings["schedule_sources"] = src

    def excel_links(self):
        """Ссылки на Excel со страницы расписания университета — для выбора файла в окне."""
        page = (self.settings.get("schedule_urls") or {}).get("excel") or sched.SOURCES["excel"]["url"]
        if not sched.is_sevsu_url(page):
            return {"ok": False, "error": "адрес страницы должен быть https на sevsu.ru", "links": [], "page": page}
        try:
            pu = urlparse(page)
            c = mo.MoodleClient(f"{pu.scheme}://{pu.netloc}", page_delay=(0.2, 0.5), wait_minutes=1, control=mo.Control(),
                                reporter=self.rep, use_system_proxy=bool(self.settings.get("use_system_proxy")))
            c.no_wait = True
            r = c.request("GET", page, kind="page")
            if r.status_code >= 400:
                return {"ok": False, "error": f"сайт ответил {r.status_code}", "links": [], "page": page}
            links = sched.find_excel_links(c._text(r), r.url or page)
        except Exception as e:
            return {"ok": False, "error": mo.describe_error(e), "links": [], "page": page}
        group = self.settings.get("schedule_group") or sched.DEFAULT_GROUP
        ranked = sched.rank_excel(links, group)
        top = ranked[0]["score"] if ranked else 0
        for i, l in enumerate(ranked):
            l["best"] = bool(top > 0 and l["score"] == top and i < 3)
        cfg = (self.settings.get("schedule_excel") or {}).get("sig")
        cur = sched.match_sig(links, cfg)
        course, level = sched.excel_course(group)
        return {"ok": True, "error": "", "page": page, "links": ranked, "current": cur["url"] if cur else "",
                "hint": f"{course} курс, {level}" if course and level else ""}

    def excel_choose(self, b):
        url = str(b.get("url") or "")
        if not sched.is_sevsu_url(url):
            return {"ok": False, "error": "ссылка должна быть https на sevsu.ru"}
        link = {"url": url, "file": b.get("file") or Path(unquote(urlparse(url).path)).name, "text": str(b.get("text") or ""),
                "col": str(b.get("col") or ""), "ctx": [str(x) for x in (b.get("ctx") or [])][:4]}
        with self.lock:
            self.settings["schedule_excel"] = {"mode": "site", "sig": sched.link_sig(link), "name": link["file"], "url": url}
            self._excel_enable()
            self.save_settings()
        return {"ok": True}

    def excel_auto(self):
        with self.lock:
            self.settings.pop("schedule_excel", None)
            self._excel_enable()
            self.save_settings()
        return {"ok": True}

    def excel_upload(self, rfile, length, name):
        """Файл Excel, выбранный на устройстве (если страницу университета не открыть или она изменилась)."""
        if length <= 0 or length > sched.MAX_XLSX:
            return {"ok": False, "error": "файл пустой или слишком большой для расписания"}
        data = rfile.read(length) if length else b""
        if len(data) != length:
            return {"ok": False, "error": "файл передался не до конца — попробуйте ещё раз"}
        group = self.settings.get("schedule_group") or sched.DEFAULT_GROUP
        try:
            names = xs.list_groups(xs.read_workbook(data))
        except xs.XlsxError as e:
            return {"ok": False, "error": str(e)}
        if not names:
            return {"ok": False, "error": "в таблице не нашёл блоков «Группа : …» — это не расписание занятий"}
        if not any(xs.group_key(n) == xs.group_key(group) for n in names):
            return {"ok": False, "error": f"в файле нет группы «{sched.group_name(group) or group}». В нём: " + ", ".join(names[:12])}
        out = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
        self._write_bin(out / sched.SCHEDULE_DIR / "excel" / sched.LOCAL_XLSX, data)
        with self.lock:
            self.settings["schedule_excel"] = {"mode": "local", "name": mo.safe_filename(name or "") or sched.LOCAL_XLSX}
            self._excel_enable()
            self.save_settings()
        return {"ok": True, "groups": len(names)}

    @staticmethod
    def _write_bin(path: Path, data: bytes):
        mo.fmkdir(path.parent)
        tmp = path.with_name(path.name + ".tmp")
        with mo.fopen(tmp, "wb") as f:
            f.write(data)
        mo.freplace(tmp, path)

    # ── элективы ──
    def _el_creds(self, password=None, username=None):
        if password:
            return ((username or "").strip() or self.settings.get("username") or "", password)
        if self.client and self.client.creds:
            return self.client.creds
        sec = self.vault.load_secret()
        if sec.get("username") and sec.get("password"):
            return (sec["username"], sec["password"])
        return None

    def _sso_cookies(self) -> list:
        """Cookie единого входа СевГУ (auth.sevsu.ru и сайты вуза) — общие для элективов и личного расписания."""
        sec = self.vault.load_secret()
        seen, out = set(), []
        for cs in [sec.get("sso_cookies") or []] + list((sec.get("portals") or {}).values()):
            for c in cs or []:
                k = (c.get("domain"), c.get("name"), c.get("path"))
                if k not in seen:
                    seen.add(k)
                    out.append(c)
        return out

    def _save_sso(self, cookies):
        if not cookies or not self.settings.get("remember"):
            return
        try:
            sec = self.vault.load_secret()
            merged = {(c.get("domain"), c.get("name"), c.get("path")): c for c in (sec.get("sso_cookies") or [])}
            for c in cookies:
                if not (c.get("domain") or "").lstrip(".").endswith(SSO_DOMAIN) or "do.sevsu.ru" in c.get("domain", ""):
                    continue
                merged[(c.get("domain"), c.get("name"), c.get("path"))] = c
            sec["sso_cookies"] = list(merged.values())[-60:]
            sec.pop("portals", None)
            self.vault.save_secret(sec)
        except Exception:
            pass

    # ── элективы: основной путь — программный интерфейс сайта (iotapi); снимок страницы через браузер — запасной ──
    def _iot(self, password=None, username=None):
        e = iotapi.Iot(Path(self.settings.get("out_dir") or mo.DEFAULT_OUT), mo.DATA_DIR, self.rep, mo.Control(),
                       creds=self._el_creds(password, username), cookies=self._sso_cookies(),
                       use_system_proxy=bool(self.settings.get("use_system_proxy")),
                       page_delay=mo.parse_delay(self.settings.get("page_delay"), mo.PAGE_DELAY),
                       file_delay=mo.parse_delay(self.settings.get("file_delay"), mo.FILE_DELAY))
        tok = getattr(self, "_iot_tok", None)            # ключ доступа живёт только в памяти, пока он действует
        if tok and tok[1] > time.monotonic() and not password:
            e.token, e.token_until = tok
        return e

    def _iot_done(self, e):
        self._keep_portal_cookies(e)
        if e.token:
            self._iot_tok = (e.token, e.token_until)

    def _electives(self, password=None, username=None):
        url = self.settings.get("electives_url") or el.ELECTIVES_URL
        host = mo.urlparse(url).hostname
        return el.Electives(Path(self.settings.get("out_dir") or mo.DEFAULT_OUT), mo.DATA_DIR, self.rep, mo.Control(),
                            url=url, creds=self._el_creds(password, username), cookies=self._sso_cookies(),
                            use_system_proxy=bool(self.settings.get("use_system_proxy")),
                            page_delay=mo.parse_delay(self.settings.get("page_delay"), mo.PAGE_DELAY)), host

    def _keep_portal_cookies(self, e, host=None):
        self._save_sso(e.cookies_out or (e.portal.cookies() if hasattr(e, "portal") else None))

    def electives_state(self):
        d = {"api": self._iot().load() or None}
        snap, _ = self._electives()
        s = snap.load()
        d["snapshot"] = s or None
        d["url"] = self.settings.get("electives_url") or el.ELECTIVES_URL
        d["watch"] = bool(self.settings.get("electives_watch"))
        d["watch_hours"] = int(self.settings.get("electives_watch_hours", 6))
        d["has_creds"] = self._el_creds() is not None
        d["username"] = self.settings.get("username") or ""
        d["dir"] = el.ELECTIVES_DIR
        d["android"] = ANDROID
        d["browser"] = bool(mo.find_browser()) if not d["android"] else False
        return d

    def _iot_fail(self, ex, what="Элективы"):
        need = isinstance(ex, iotapi.IotNeedLogin)
        msg = str(ex) if isinstance(ex, iotapi.IotError) else mo.describe_error(ex)
        self.rep.log(f"{what}: {msg}", "warn")
        return {"ok": False, "error": msg, "need_password": need or "вход не удался" in msg,
                "debug": str(mo.DATA_DIR / "iot_debug.txt")}

    def electives_refresh(self, password=None, username=None):
        with self.el_lock:
            e = self._iot(password, username)
            try:
                st = e.refresh()
            except Exception as ex:
                return self._iot_fail(ex)
            self._iot_done(e)
            self.electives_checked = time.monotonic()
            if st.get("changes"):
                mo.notify(mo.APP_NAME, "Элективы: " + "; ".join(mo.short(c, 70) for c in st["changes"][:3]))
            return {"ok": True, "state": self.electives_state(), "changes": st.get("changes", [])}

    def _iot_action(self, fn_name, args, password=None, what="Элективы", username=None):
        with self.el_lock:
            e = self._iot(password, username)
            try:
                res = getattr(e, fn_name)(*args)
            except Exception as ex:
                return self._iot_fail(ex, what)
            self._iot_done(e)
            self.electives_checked = time.monotonic()
            return {"ok": True, "message": res.get("message", ""), "state": self.electives_state()}

    def electives_choose(self, block_id, primary, backup, password=None, username=None):
        return self._iot_action("save_choice", (block_id, list(primary or []), list(backup or [])), password, "Выбор электива", username)

    def electives_register(self, event_id, password=None, username=None):
        return self._iot_action("register", (event_id,), password, "Запись на занятие", username)

    def electives_retract(self, reg_id, password=None, username=None):
        return self._iot_action("retract", (reg_id,), password, "Отмена записи на занятие", username)

    # ── запасной путь: снимок страницы через фоновый браузер (Windows) ──
    def electives_snapshot(self, password=None, username=None):
        with self.el_lock:
            e, host = self._electives(password, username)
            try:
                st = e.refresh()
            except Exception as ex:
                return {"ok": False, "error": mo.describe_error(ex),
                        "need_password": "логин и пароль" in str(ex) or "вход не удался" in str(ex),
                        "debug": str(mo.DATA_DIR / "electives_debug.txt")}
            self._keep_portal_cookies(e, host)
            return {"ok": True, "state": self.electives_state(), "new": len(st.get("new", []))}

    def electives_act(self, action_id, password=None, username=None):
        with self.el_lock:
            e, host = self._electives(password, username)
            try:
                res = e.act(action_id)
            except Exception as ex:
                return {"ok": False, "error": mo.describe_error(ex)}
            self._keep_portal_cookies(e, host)
            return {"ok": res["ok"], "messages": res["messages"], "state": self.electives_state()}

    # ── задание ──
    def start_job(self, params):
        with self.lock:
            if self.job_state != "idle":
                return {"ok": False, "error": "загрузка уже идёт"}
            moodle_job = params.get("kind") not in ("schedule", "transfer")
            if moodle_job and self.login_state == "offline" and not self.client:
                if self.net["status"] == "down" or self.vault.load_secret():
                    self.armed = params
                    self.rep.log("Moodle недоступен — загрузка начнётся сама, как только сайт оживёт и я войду.")
                    return {"ok": True, "armed": True}
                return {"ok": False, "error": "войдите в Moodle, чтобы скачивать материалы", "need_login": True}
            if params.get("wait_for_server") and self.net["status"] == "down":
                self.armed = params
                self.rep.log("Сервер недоступен. Загрузка начнётся сама, как только Moodle оживёт.")
                return {"ok": True, "armed": True}
            self.job_state = "running"
            self.job_hint = "running"
            self.job_kind = params.get("kind") or ("sync" if params.get("sync") else "full")
            self.job_started = time.time()
            self.control = mo.Control(mo.parse_hours(self.settings.get("quiet_hours"))
                                      if self.settings.get("quiet_hours_on") and params.get("kind") != "transfer" else None)
            self.control.reporter = self.rep
            self.rep.reset()
        self.job_thread = threading.Thread(target=self._job, args=(params,), daemon=True)
        self.job_thread.start()
        return {"ok": True}

    def _job(self, params):
        if params.get("kind") == "schedule":
            return self._schedule_job(params)
        if params.get("kind") == "transfer":
            return self._transfer_job(params)
        if params.get("kind") == "calendar":
            return self._calendar_job(params)
        t0 = time.monotonic()
        res = None
        try:
            if not self.client:
                self.try_saved_login()
            if not self.client:
                raise mo.LoginError("нужно войти в Moodle")
            client = self.client
            client.control = self.control
            client.rep = self.rep
            ids = [int(x) for x in params.get("course_ids") or self.settings.get("course_ids") or []]
            known = {c.id: c for c in self.courses}
            selected = [mo.Course(**{k: v for k, v in known[i].to_dict().items()}) for i in ids if i in known]
            if not selected:
                raise mo.MoodleError("не выбрано ни одного курса")
            st = self.settings
            opts = mo.JobOptions(out_dir=Path(st.get("out_dir") or mo.DEFAULT_OUT), mode=int(st.get("mode", 1)),
                                 sync=bool(params.get("sync")), pdf=bool(st.get("pdf")), zip=bool(st.get("zip")),
                                 original_names=bool(st.get("original_names")), index_in_mode2=bool(st.get("index_in_mode2")),
                                 keep_versions=bool(st.get("keep_versions", True)), rules=st.get("rules"), dir_template=st.get("layout_dir") or "",
                                 file_template=st.get("layout_file") or "", news=st.get("news"),
                                 calendar=st.get("calendar"), calendar_on=deadlines.normalize(st.get("calendar"))["auto"],
                                 grades_on=bool(st.get("save_grades", True)))
            auto = bool(params.get("auto"))
            self.rep.log(f"{'Автообновление' if auto else 'Быстрая синхронизация' if opts.sync else 'Загрузка'}: курсов {len(selected)}, "
                         f"режим {opts.mode}, папка {opts.out_dir}")
            if auto:     # сначала сверяем диск: недостающее помечается к докачке, готовое пропускается
                mo.prepare_auto_update(opts.out_dir, [c.id for c in selected], opts.mode, self.rep)
            res = mo.run_job(client, selected, opts, self.rep, self.control)
            took = mo.fmt_dur(time.monotonic() - t0)
            head = {"network": "Прервано: нет связи", "session": "Прервано: сессия истекла",
                    "stopped": "Остановлено"}.get(res.aborted, "Готово")
            text = f"{'Автообновление. ' if auto else ''}{head} за {took}. {mo.summary_text(res, opts)}"
            if auto:
                watch.record_result(mo.DATA_DIR, res.aborted is None, text, time.time())
            self.last_result = {"ok": res.aborted is None, "text": text, "aborted": res.aborted,
                                "failures": len(res.failures), "new": len(res.new_files), "news": len(res.events)}
            self.rep.log(text)
            try:
                sent = mo.deliver_news(res.events, st, self.vault, self.rep) if res.events else {}
            except Exception as e:   # уведомления не должны ломать итог загрузки
                sent = {}
                self.rep.log(f"Уведомления о новом не отправлены: {mo.describe_error(e)}", "warn")
            if res.aborted != "stopped" and not (sent.get("toast") and res.aborted is None):   # окно «Что нового» уже показано
                mo.notify(mo.APP_NAME, text, ok=res.aborted is None)
            if res.aborted != "stopped" and search.normalize(st.get("search"))["auto"]:
                self.search_start()   # индекс поиска обновляется в фоне; загрузку это не задерживает
        except Exception as e:
            msg = mo.describe_error(e)
            self.last_result = {"ok": False, "text": f"Ошибка: {msg}", "aborted": "error"}
            self.rep.log(f"Ошибка: {msg}", "error")
            mo.notify(mo.APP_NAME, f"Ошибка: {msg}", ok=False)
            if params.get("auto"):
                watch.record_result(mo.DATA_DIR, False, f"Автообновление не вышло: {msg}", time.time())
        finally:
            self._cal_badge_at = -99.0     # сроки могли измениться — значок пересчитать сразу
            if self.client:
                self.client.control = self.idle_control
            with self.lock:
                self.job_state = "idle"
                self.control = None

    def _schedule_job(self, params):
        t0 = time.monotonic()
        st = self.settings
        if params.get("profile"):
            return self._schedule_job_other(params, t0)
        try:
            job = sched.ScheduleJob(Path(st.get("out_dir") or mo.DEFAULT_OUT), st.get("schedule_group") or sched.DEFAULT_GROUP,
                                    st.get("schedule_sources") or sched.DEFAULT_SOURCES, self.rep, self.control,
                                    urls=st.get("schedule_urls"), pdf=bool(st.get("schedule_pdf", True)),
                                    use_system_proxy=bool(st.get("use_system_proxy")),
                                    page_delay=mo.parse_delay(st.get("page_delay"), mo.PAGE_DELAY),
                                    creds=self._el_creds(), cookies=self._sso_cookies(), excel=st.get("schedule_excel"))
            try:
                res = job.run(params.get("range", "2weeks"), params.get("from"), params.get("to"))
            finally:
                self._save_sso(job.cookies_out)
                if job.excel_out:                   # файл нашли сами — запоминаем, чтобы в следующий раз не перебирать
                    with self.lock:
                        self.settings["schedule_excel"] = job.excel_out
                        self.save_settings()
            text = (f"Расписание сохранено за {mo.fmt_dur(time.monotonic() - t0)}: {res['total']} пар "
                    f"({res['range'][0]} — {res['range'][1]})")
            if res.get("per_source", {}).get("excel"):
                text += f". Из Excel-таблицы взят весь семестр ({res['per_source']['excel']} пар)"
            if res.get("note"):
                text += ". " + res["note"]
            self.last_result = {"ok": True, "text": text, "aborted": None, "kind": "schedule"}
            self.rep.log(text)
            mo.notify(mo.APP_NAME, text)
        except (mo.Stopped, KeyboardInterrupt):
            self.last_result = {"ok": False, "text": "Сохранение расписания остановлено", "aborted": "stopped", "kind": "schedule"}
        except Exception as e:
            self.last_result = {"ok": False, "text": f"Расписание: {mo.describe_error(e)}", "aborted": "error", "kind": "schedule"}
            self.rep.log(self.last_result["text"], "error")
        finally:
            with self.lock:
                self.job_state = "idle"
                self.control = None


    def _schedule_job_other(self, params, t0):
        """Расписание чужой группы: официальный сайт и (по желанию) Excel университета. Личные источники не используются,
        свои данные не трогаются — всё в Расписание/Другие группы/<код>/."""
        st, out, slug = self.settings, self._out(), params["profile"]
        try:
            root = book.profile_dir(out, slug)
            pr = book.read_profile(root)
            sources = [x for x in (params.get("sources") or pr.get("sources") or ["schedule"]) if x in ("schedule", "excel")] or ["schedule"]
            job = sched.ScheduleJob(out, slug, sources, self.rep, self.control, urls=st.get("schedule_urls"), pdf=False,
                                    use_system_proxy=bool(st.get("use_system_proxy")),
                                    page_delay=mo.parse_delay(st.get("page_delay"), mo.PAGE_DELAY), excel=pr.get("excel"), profile=slug)
            try:
                res = job.run(params.get("range", "semester"), params.get("from"), params.get("to"))
            finally:
                if job.excel_out:
                    book.write_profile(root, excel=job.excel_out)
            title = book.pick_title(job.site_info.get("group_name") or "", slug, pr.get("title") or "")
            book.write_profile(root, title=title, sources=sources, last_used=int(time.time()), refreshed=int(time.time()))
            if res["total"] == 0:
                text = f"Расписание группы «{title}»: пар не нашлось. Проверьте код группы и период; сайт мог ещё не выложить расписание."
                self.last_result = {"ok": False, "text": text, "aborted": "error", "kind": "schedule"}
            else:
                text = f"Расписание группы «{title}» сохранено за {mo.fmt_dur(time.monotonic() - t0)}: {res['total']} пар"
                if res.get("note"):
                    text += ". " + res["note"]
                self.last_result = {"ok": True, "text": text, "aborted": None, "kind": "schedule"}
            self.rep.log(text)
        except (mo.Stopped, KeyboardInterrupt):
            self.last_result = {"ok": False, "text": "Сохранение расписания остановлено", "aborted": "stopped", "kind": "schedule"}
        except Exception as e:
            self.last_result = {"ok": False, "text": f"Расписание группы: {mo.describe_error(e)}", "aborted": "error", "kind": "schedule"}
            self.rep.log(self.last_result["text"], "error")
        finally:
            with self.lock:
                self.job_state = "idle"
                self.control = None

    # ── перенос данных на другое устройство (архив / Wi-Fi) ──
    def _tr_roots(self):
        return Path(self.settings.get("out_dir") or mo.DEFAULT_OUT), mo.DATA_DIR

    def _tr_sender_info(self):
        sd = self.tr_sender
        if sd and not sd.active:
            sd.stop()
            self.tr_sender = sd = None
        if not sd:
            return None
        info = sd.info(self.tr_ip)
        info["qr"] = qr.to_svg(qr.make(info["url"]))
        return info

    def transfer_state(self):
        out, data = self._tr_roots()
        inv = transfer.inventory(out, data)
        busy = self.job_state != "idle" and self.job_kind == "transfer"
        prog = None
        if busy:
            f = self.rep.snapshot(self.rep.seq)["file"]
            prog = {"title": f["title"], "done": f["done"], "total": f["total"], "speed": f["speed"]} if f else {}
        return {"busy": busy, "prog": prog, "cats": [{"id": i, "title": t, "hint": h, "default": i in transfer.DEFAULT_CATS, **inv.get(i, {"files": 0, "bytes": 0, "stamp": 0})}
                         for i, t, h in transfer.CATS],
                "sender": self._tr_sender_info(), "last": self.tr_last, "android": ANDROID, "apk": android.is_apk(),
                "dir": "Перенос"}

    def transfer_serve(self, cats, ip=None):
        out, data = self._tr_roots()
        cats = [c for c in cats if c in transfer.CAT_IDS]
        if not cats:
            return {"ok": False, "error": "отметьте, что передавать"}
        if self.tr_sender:
            self.tr_sender.stop()
        self.tr_ip = ip or None
        self.tr_sender = transfer.Sender(out, data, cats, self.settings, self.rep)
        try:
            self.tr_sender.start()
        except OSError as e:
            self.tr_sender = None
            return {"ok": False, "error": f"не удалось открыть порт: {mo.describe_error(e)}"}
        self.rep.log(f"Передача по Wi-Fi включена на 15 минут: {self.tr_sender.info(self.tr_ip)['url']}")
        return {"ok": True, "state": self.transfer_state()}

    def transfer_stop(self):
        if self.tr_sender:
            self.tr_sender.stop()
            self.tr_sender = None
            self.rep.log("Передача по Wi-Fi остановлена.")
        return {"ok": True}

    def _transfer_job(self, params):
        t0 = time.monotonic()
        out, data = self._tr_roots()
        action = params.get("action")
        cats = [c for c in params.get("cats") or [] if c in transfer.CAT_IDS]
        file = params.get("file")
        try:
            if action == "export":
                dest = out / "Перенос" / f"MoodleOffline-перенос-{datetime.now():%Y-%m-%d_%H%M}.zip"
                res = transfer.export_archive(dest, out, data, cats, self.settings, self.rep, self.control)
                text = (f"Архив готов за {mo.fmt_dur(time.monotonic() - t0)}: {res['files']} файлов, "
                        f"{transfer._fmt_size(res['size'])}. Лежит в папке «Перенос» в папке сохранения.")
                if res["skipped"]:
                    text += f" Не вошло: {len(res['skipped'])} (файлы заняты или исчезли)."
                self.tr_last = {"ok": True, "kind": "export", "text": text, "path": f"Перенос/{dest.name}"}
            else:
                if action == "pull":
                    base, _code = transfer.parse_address(params.get("address") or "")
                    res = transfer.pull(base, cats, out, data, self.rep, self.control, bool(params.get("force")))
                else:
                    res = transfer.import_archive(Path(file), cats, out, data, self.rep, self.control, bool(params.get("force")))
                text = self._tr_finish(res, cats, time.monotonic() - t0)
                self.tr_last = {"ok": res["failed"] == 0, "kind": action, "text": text, "notes": res["notes"], "errors": res["errors"]}
            self.last_result = {"ok": self.tr_last["ok"], "text": self.tr_last["text"], "aborted": None, "kind": "transfer"}
            self.rep.log(self.tr_last["text"])
            mo.notify(mo.APP_NAME, self.tr_last["text"], ok=self.tr_last["ok"])
        except (mo.Stopped, KeyboardInterrupt):
            self.tr_last = {"ok": False, "kind": action, "text": "Перенос остановлен. Скопированное сохранено, остальное — при повторе."}
            self.last_result = {"ok": False, "text": self.tr_last["text"], "aborted": "stopped", "kind": "transfer"}
        except Exception as e:
            self.tr_last = {"ok": False, "kind": action, "text": f"Перенос: {mo.describe_error(e)}"}
            self.last_result = {"ok": False, "text": self.tr_last["text"], "aborted": "error", "kind": "transfer"}
            self.rep.log(self.tr_last["text"], "error")
        finally:
            if file:
                mo.funlink(file)
            with self.lock:
                self.job_state = "idle"
                self.control = None

    def _tr_finish(self, res, cats, took):
        """Применяет настройки и обновляет то, что программа держит в памяти; возвращает текст итога."""
        changed = []
        if res.get("settings"):
            changed = transfer.apply_settings(res["settings"], self.settings)
            if changed:
                self.save_settings()
        if "courses" in cats and self.login_state in ("idle", "offline", "error") and not self.client:
            self.login_state = "idle"
            self.go_offline("данные с другого устройства")
        self._news = self._cal = None      # лента и сроки могли обновиться — перечитать
        adopted = self.adopt_courses(force=True) if "materials" in cats else []   # файлы курсов пришли — запишем курсы в память
        parts = [f"Перенос готов за {mo.fmt_dur(took)}: получено файлов {res['copied']} из {res['total']} ({transfer._fmt_size(res['bytes'])})"]
        if changed:
            parts.append(f"настроек применено: {len(changed)}")
        if res["failed"]:
            parts.append(f"не получилось: {res['failed']}")
        if adopted:
            parts.append(f"курсов появилось в разделе «Курсы» как скачанные: {len(adopted)}")
        if not res["total"] and not changed:
            parts = ["Всё уже на месте — ничего нового нет"]
            if adopted:
                parts.append(f"курсов появилось в разделе «Курсы» как скачанные: {len(adopted)}")
        if res["from"]:
            parts.append(f"данные с устройства «{res['from']}»")
        return ", ".join(parts) + "."

    def transfer_upload(self, rfile, length, cats, force):
        """Архив, выбранный в окне, приходит сюда потоком — кладём во временный файл и запускаем импорт."""
        if self.job_state != "idle":
            return {"ok": False, "error": "сейчас идёт другая задача"}
        tmp = mo.DATA_DIR / "incoming_transfer.zip"
        mo.fmkdir(tmp.parent)
        got = 0
        with mo.fopen(tmp, "wb") as f:
            while got < length:
                chunk = rfile.read(min(1 << 20, length - got))
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        if got != length:
            mo.funlink(tmp)
            return {"ok": False, "error": "файл передался не до конца — попробуйте ещё раз"}
        try:
            man = transfer.read_archive_manifest(tmp)
        except transfer.TransferError as e:
            mo.funlink(tmp)
            return {"ok": False, "error": str(e)}
        r = self.start_job({"kind": "transfer", "action": "import", "cats": cats, "file": str(tmp), "force": force})
        if not r.get("ok"):
            mo.funlink(tmp)
        r["summary"] = man.get("cats", {})
        r["device"], r["created"] = man.get("device", ""), man.get("created", "")
        return r

    # ── поиск по материалам ──
    def search_index(self) -> search.Index:
        path = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
        with self.lock:
            if not self._search or self._search[0] != path:
                self._search = (path, search.Index(path))
            return self._search[1]

    def search_query(self, q):
        try:
            limit = int(q.get("limit") or 30)
        except ValueError:
            limit = 30
        groups = [g for g in (q.get("groups") or "").split(",") if g]
        return self.search_index().search(q.get("q", ""), course=q.get("course") or None, groups=groups,
                                          limit=min(100, max(1, limit)), order="new" if q.get("order") == "new" else "rank")

    def search_info(self):
        return {"status": self.search_index().status(), "cfg": search.normalize(self.settings.get("search")),
                "groups": search.groups_meta(), "progress": dict(self.search_prog), "last": self.search_last}

    def search_progress(self):
        return {**self.search_prog, "last": self.search_last}

    def search_start(self, full=False):
        with self.lock:
            if self.search_thread and self.search_thread.is_alive():
                return {"ok": False, "error": "индексация уже идёт"}
            out = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
            if not out.is_dir():
                return {"ok": False, "error": "папка с материалами не найдена — сначала скачайте что-нибудь"}
            self.search_ev.clear()
            self.search_prog = {"running": True, "phase": "scan", "done": 0, "total": 0, "current": ""}
            self.search_thread = threading.Thread(target=self._search_job, args=(out, bool(full)), daemon=True)
            self.search_thread.start()
        return {"ok": True}

    def _search_job(self, out, full):
        def prog(d):
            self.search_prog = {**d, "running": True}
        try:
            r = mo.search_pass(out, self.settings, self.rep, full=full, stop=self.search_ev, progress=prog)
            self.search_last = ({"ok": not r.get("stopped"), "text": mo.search_summary(r), **r} if r
                                else {"ok": False, "text": "Индекс не обновлён — подробности в журнале"})
        except Exception as e:  # noqa: BLE001
            self.search_last = {"ok": False, "text": f"Индекс не обновлён: {mo.describe_error(e)}"}
        finally:
            self.search_prog = {"running": False, "phase": "done", "done": 0, "total": 0, "current": ""}

    def search_settings_save(self, b):
        cfg = search.normalize(b.get("cfg"))
        with self.lock:
            self.settings["search"] = cfg
            self.save_settings()
        return {"ok": True, "cfg": cfg}

    # ── заметки ──
    def notes_out(self) -> Path:
        return Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)

    def notes_trash(self) -> Path:
        return mo.DATA_DIR / "notes_trash"

    def notes_list(self, q) -> dict:
        try:
            course = int(q["course"]) if q.get("course") not in (None, "") else None
        except ValueError:
            course = None
        item = q.get("item") or None
        lst = notes.list_notes(self.notes_out(), course=course, item=item if course is not None else None,
                               general=q.get("general") == "1", q=q.get("q", ""))
        return {"notes": lst[:500], "total": len(lst), "dir": notes.NOTES_DIR}

    def notes_get(self, q) -> dict:
        try:
            return {"ok": True, "note": notes.get_note(self.notes_out(), q.get("id", ""))}
        except notes.NoteError as e:
            return {"ok": False, "error": str(e)}

    def notes_save(self, b) -> dict:
        try:
            try:
                course = int(b["course"]) if b.get("course") not in (None, "") else None
            except (TypeError, ValueError):
                course = None
            n = notes.save_note(self.notes_out(), nid=b.get("id") or None, title=str(b.get("title") or ""),
                                body=str(b.get("body") or ""), course=course, course_name=str(b.get("course_name") or ""),
                                item=str(b.get("item") or "") or None, item_name=str(b.get("item_name") or ""),
                                pinned=b.get("pinned") if isinstance(b.get("pinned"), bool) else None)
        except notes.NoteError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": "не удалось записать заметку: " + mo.describe_error(e)}
        self.notes_changed()
        return {"ok": True, "note": n}

    def notes_delete(self, b) -> dict:
        try:
            r = notes.delete_note(self.notes_out(), str(b.get("id") or ""), self.notes_trash())
        except notes.NoteError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": "не удалось удалить заметку: " + mo.describe_error(e)}
        self.notes_changed()
        return {"ok": True, **r}

    def notes_restore(self, b) -> dict:
        try:
            n = notes.restore_note(self.notes_out(), str(b.get("trash") or ""), self.notes_trash())
        except notes.NoteError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": "не удалось вернуть заметку: " + mo.describe_error(e)}
        self.notes_changed()
        return {"ok": True, "note": n}

    def notes_pin(self, b) -> dict:
        try:
            n = notes.set_pinned(self.notes_out(), str(b.get("id") or ""), bool(b.get("pinned")))
        except notes.NoteError as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": "не удалось записать заметку: " + mo.describe_error(e)}
        self.notes_changed()
        return {"ok": True, "note": n}

    def notes_delete_many(self, b) -> dict:
        ids = b.get("ids")
        if not isinstance(ids, list) or not ids:
            return {"ok": False, "error": "Не выбрано ни одной заметки."}
        try:
            r = notes.delete_many(self.notes_out(), [str(i) for i in ids[:2000]], self.notes_trash())
        except OSError as e:
            return {"ok": False, "error": "не удалось удалить заметки: " + mo.describe_error(e)}
        self.notes_changed()
        return {"ok": True, **r}

    def notes_restore_many(self, b) -> dict:
        names = b.get("trash")
        if not isinstance(names, list) or not names:
            return {"ok": False, "error": "Нечего возвращать."}
        try:
            r = notes.restore_many(self.notes_out(), [str(i) for i in names[:2000]], self.notes_trash())
        except OSError as e:
            return {"ok": False, "error": "не удалось вернуть заметки: " + mo.describe_error(e)}
        self.notes_changed()
        return {"ok": True, **r}

    def notes_changed(self):
        """После правки заметки обновляем индекс поиска — но только если им уже пользовались, и не чаще раза в несколько
        секунд (пока человек печатает, заметка сохраняется часто)."""
        try:
            if not self.search_index().status().get("exists"):
                return
        except Exception:  # noqa: BLE001
            return
        with self.lock:
            if getattr(self, "_notes_timer", None):
                self._notes_timer.cancel()
            t = threading.Timer(4.0, self.search_start)
            t.daemon = True
            self._notes_timer = t
            t.start()

    # ── нейросеть ──
    # ── доступ для внешних нейросетей (mcp_server.py) ──
    def mcp_cfg(self) -> dict:
        return mcp_server.normalize(self.settings.get("mcp"))

    def mcp_token(self) -> str:
        t = self.vault.load_mcp().get("token")
        return t if isinstance(t, str) else ""

    def mcp_apply(self, wait: float = 0.0):
        """Приводит сервер в соответствие с настройками: включён — слушает 127.0.0.1:порт, выключен — закрыт.
        wait — сколько секунд ждать, пока освободится порт (при перезапуске программы прежняя копия ещё уходит)."""
        with self._mcp_busy:
            self._mcp_apply(wait)

    def _mcp_apply(self, wait: float):
        with self.lock:
            cfg = self.mcp_cfg()
            if self.mcp and (not cfg["enabled"] or self.mcp.port != cfg["port"]):
                self.mcp.stop()
                self.mcp = None
            if not cfg["enabled"]:
                self.mcp_error = ""
                return
            if self.mcp and self.mcp.running:
                return
            if not self.mcp_token():                          # без токена сервер не открываем
                self.mcp_error = "Нет токена — создайте его в окне."
                return
        end = time.monotonic() + max(0.0, wait)
        while True:
            srv = mcp_server.McpServer(cfg["port"], self.mcp_tools, self.mcp_token, self.mcp_log, mo.VERSION)
            try:
                srv.start()
            except OSError as e:
                if time.monotonic() < end:
                    time.sleep(1.0)
                    continue
                with self.lock:
                    self.mcp_error = f"Порт {cfg['port']} занят или недоступен ({getattr(e, 'strerror', '') or e}). Выберите другой порт."
                return
            with self.lock:
                self.mcp, self.mcp_error = srv, ""
            return

    def mcp_stdio(self) -> dict | None:
        """Как клиенту запустить stdio-вариант: только из исходников и Termux (у .exe нет консоли, у .apk нет python)."""
        if getattr(sys, "frozen", False) or android.is_apk():
            return None
        return {"command": sys.executable or "python", "args": [str(Path(mo.__file__).resolve()), "--mcp"]}

    def mcp_info(self) -> dict:
        cfg, tok = self.mcp_cfg(), self.mcp_token()
        return {"cfg": cfg, "running": bool(self.mcp and self.mcp.running), "error": self.mcp_error,
                "url": f"http://127.0.0.1:{cfg['port']}/mcp", "has_token": bool(tok), "token_mask": mcp_server.mask(tok),
                "log": self.mcp_log.tail(30), "total": self.mcp_log.total, "stdio": self.mcp_stdio(),
                "tools": {"materials": [n for n, t in mcp_server.TOOLS.items() if t[0] == "materials"],
                          "notes": [n for n, t in mcp_server.TOOLS.items() if t[0] == "notes"]}}

    def mcp_settings_save(self, b) -> dict:
        cur = self.mcp_cfg()
        port = b.get("port", cur["port"])
        try:
            port_n = int(port)
        except (TypeError, ValueError):
            port_n = 0
        if not (mcp_server.MIN_PORT <= port_n <= mcp_server.MAX_PORT):
            return {"ok": False, "error": f"Порт — число от {mcp_server.MIN_PORT} до {mcp_server.MAX_PORT}."}
        cfg = mcp_server.normalize({"enabled": b.get("enabled", cur["enabled"]), "port": port_n,
                                    "materials": b.get("materials", cur["materials"]), "notes": b.get("notes", cur["notes"])})
        with self.lock:
            if cfg["enabled"] and not self.mcp_token():
                try:
                    self.vault.save_mcp({"token": mcp_server.new_token()})
                except OSError as e:
                    return {"ok": False, "error": "Не удалось сохранить токен: " + mo.describe_error(e)}
            self.settings["mcp"] = cfg
            self.save_settings()
        self.mcp_apply()
        return {"ok": True, **self.mcp_info()}

    def mcp_token_action(self, b) -> dict:
        """show — показать токен (в окне он не хранится); new — выпустить новый (старый перестаёт работать сразу)."""
        with self.lock:
            if b.get("action") == "new":
                try:
                    self.vault.save_mcp({"token": mcp_server.new_token()})
                except OSError as e:
                    return {"ok": False, "error": "Не удалось сохранить токен: " + mo.describe_error(e)}
                self.mcp_error = "" if self.mcp_error.startswith("Нет токена") else self.mcp_error
            tok = self.mcp_token()
        if not tok:
            return {"ok": False, "error": "Токена ещё нет — включите доступ."}
        if b.get("action") == "new":
            self.mcp_apply()
        return {"ok": True, "token": tok, **self.mcp_info()}

    def ai_cfg(self) -> dict:
        return ai.normalize(self.settings.get("ai"))

    def ai_keys(self) -> dict:
        k = self.vault.load_ai().get("keys")
        return {p: v for p, v in k.items() if p in ai.PROVIDERS and isinstance(v, str) and v} if isinstance(k, dict) else {}

    def ai_info(self) -> dict:
        cfg, keys = self.ai_cfg(), self.ai_keys()
        pid = cfg["provider"]
        why = ai.ready(cfg, keys.get(pid, ""))
        return {"cfg": {"provider": pid, "model": ai.model_of(cfg), "base_url": cfg["base_url"], "proxy": cfg["proxy"], "models": cfg["models"]},
                "providers": [{"id": i, **{k: v[k] for k in ("title", "model", "models", "key_url", "need_key", "hint")}, "host": urlparse(v["base"]).hostname or ""}
                              for i, v in ai.PROVIDERS.items()],
                "has_key": {i: i in keys for i in ai.PROVIDERS}, "ready": not why, "why": why, "consent": bool(pid) and ai.has_consent(cfg),
                "provider_title": ai.PROVIDERS[pid]["title"] if pid else "", "host": self._ai_host(cfg),
                "encrypted": self.vault.can_encrypt, "caps": {"ask": ai.ASK_CHARS, "doc": ai.DOC_CHARS},
                "exts": sorted(search.READABLE)}      # какие файлы можно отправить на пересказ (то же, что читает поиск)

    @staticmethod
    def _ai_host(cfg) -> str:
        try:
            base = ai.check_base(cfg["base_url"]) if cfg["provider"] == "custom" else ai.PROVIDERS[cfg["provider"]]["base"]
        except (ai.AiError, KeyError):
            return ""
        return urlparse(base).hostname or ""

    def ai_form(self, b) -> tuple[dict, str]:
        """Настройки из формы окна (ещё не сохранённые) + ключ: из формы или, если там пусто, сохранённый. Для «Проверить» и «Список моделей»."""
        cur = self.ai_cfg()

        def pick(k):
            v = b.get(k)
            return v if isinstance(v, str) else cur[k]
        pid = b.get("provider") if b.get("provider") in ai.PROVIDERS else cur["provider"]
        models = dict(cur["models"])
        if isinstance(b.get("model"), str) and b["model"].strip():
            models[pid] = b["model"]
        cfg = ai.normalize({**cur, "provider": pid, "models": models, "base_url": pick("base_url"), "proxy": pick("proxy")})
        key = b.get("key") if isinstance(b.get("key"), str) and b["key"].strip() else self.ai_keys().get(pid, "")
        return cfg, key.strip()

    def ai_settings_save(self, b) -> dict:
        key = b.get("key").strip() if isinstance(b.get("key"), str) else ""
        if key and re.search(r"\s", key):
            return {"ok": False, "error": "В ключе есть пробелы или переносы строк — скопируйте его заново, целиком."}
        cfg, _ = self.ai_form(b)
        pid = cfg["provider"]
        try:
            cfg["proxy"] = ai.check_proxy(cfg["proxy"])
            if pid == "custom" and cfg["base_url"]:
                cfg["base_url"] = ai.check_base(cfg["base_url"])
        except ai.AiError as e:
            return {"ok": False, "error": str(e)}
        if pid and b.get("consent") is True and ai.consent_token(cfg):
            cfg["consent"][pid] = ai.consent_token(cfg)
        elif pid and (b.get("consent") is False or b.get("clear_key")):
            cfg["consent"].pop(pid, None)
        with self.lock:
            if pid and (key or b.get("clear_key")):
                keys = self.ai_keys()
                if b.get("clear_key"):
                    keys.pop(pid, None)
                else:
                    keys[pid] = key
                try:
                    self.vault.save_ai({"keys": keys})
                except OSError as e:
                    return {"ok": False, "error": "Не удалось сохранить ключ: " + mo.describe_error(e)}
            self.settings["ai"] = cfg
            self.save_settings()
        return {"ok": True, **self.ai_info()}

    def _ai_client(self, b) -> tuple[ai.Client | None, str]:
        cfg, key = self.ai_form(b)
        try:
            return ai.Client(cfg, key), ""
        except ai.AiError as e:
            return None, str(e)

    def ai_models(self, b) -> dict:
        cl, why = self._ai_client(b)
        if not cl:
            return {"ok": False, "error": why}
        try:
            return {"ok": True, "models": cl.list_models()}
        except ai.AiError as e:
            return {"ok": False, "error": str(e)}

    def ai_test(self, b) -> dict:
        cl, why = self._ai_client(b)
        if not cl:
            return {"ok": False, "error": why}
        t0 = time.perf_counter()
        try:
            r = cl.complete("Отвечай одним словом.", "Скажи: ок", 256)
        except ai.AiError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "model": r["model"], "text": r["text"][:80]}

    def ai_run(self, b) -> dict:
        task = b.get("task")
        if task not in ("ask", "summary", "quiz"):
            return {"ok": False, "error": "Неизвестное действие."}
        cfg = self.ai_cfg()
        key = self.ai_keys().get(cfg["provider"], "")
        why = ai.ready(cfg, key)
        if why:
            return {"ok": False, "need_setup": True, "error": why}
        if not ai.has_consent(cfg):
            return {"ok": False, "need_consent": True, "error": "Сначала подтвердите отправку текста в настройках нейросети."}
        with self.lock:
            if any(j["state"] == "run" for j in self.ai_jobs.values()):
                return {"ok": False, "error": "Предыдущий запрос ещё выполняется — дождитесь ответа или отмените его."}
            jid = secrets.token_hex(4)
            job = {"id": jid, "task": task, "state": "run", "phase": "готовлю текст", "started": time.time(), "result": None, "error": ""}
            self.ai_jobs[jid] = job
            for old in list(self.ai_jobs)[:-6]:
                self.ai_jobs.pop(old, None)
        threading.Thread(target=self._ai_job, args=(job, cfg, key, dict(b)), daemon=True).start()
        return {"ok": True, "id": jid}

    def _ai_job(self, job, cfg, key, b):
        out = self._out()
        try:
            cl = ai.Client(cfg, key)
            if job["task"] == "ask":
                res = self._ai_ask(job, cl, out, b)
            else:
                res = self._ai_docs(job, cl, cfg, out, b)
            if job["state"] == "run":
                job["result"], job["state"] = res, "done"
                self.rep.log(f"Нейросеть ({ai.PROVIDERS[cfg['provider']]['title']}, {cl.model}): {job['task']}, отправлено ~{res.get('sent', 0)} символов.")
        except ai.AiError as e:
            if job["state"] == "run":
                job["error"], job["state"] = str(e), "error"
        except Exception as e:  # noqa: BLE001 — любой сбой — понятное сообщение в окне, а не обрыв
            log_error("нейросеть", e, self.rep)
            if job["state"] == "run":
                job["error"], job["state"] = "Внутренняя ошибка программы: " + scrub(e, 200), "error"

    def _ai_ask(self, job, cl, out, b) -> dict:
        q = str(b.get("question") or "").strip()
        if not q:
            raise ai.AiError("Введите вопрос.")
        got = self.search_index().passages(q, course=str(b.get("course") or "") or None, limit=8)
        if not got.get("ok"):
            raise ai.AiError(got.get("error") or "Поиск по материалам недоступен.")
        if not got["passages"]:
            return {"text": "", "sources": [], "nothing": True, "sent": 0}
        system, user, used = ai.build_ask(q, got["passages"])
        job["phase"] = f"жду ответ ({cl.host})"
        r = cl.complete(system, user, 1800)
        return {"text": r["text"], "truncated": r["truncated"], "model": r["model"], "sent": len(user),
                "sources": [{"n": i, "path": p["path"], "loc": p.get("loc", ""), "course": p.get("course", "")} for i, p in enumerate(used, 1)]}

    def _ai_docs(self, job, cl, cfg, out, b) -> dict:
        paths = [str(x) for x in (b.get("paths") or []) if isinstance(x, str)][:8]
        if not paths:
            raise ai.AiError("Не выбраны файлы.")
        scfg = search.normalize(self.settings.get("search"))
        files, skipped = [], []
        for rel in paths:
            t = out / rel
            if not inside(out, t) or not t.is_file():
                skipped.append({"path": rel, "why": "файл не найден"})
                continue
            try:
                blocks = search.read_blocks(t, max_pages=scfg["max_pages"], max_mb=scfg["max_mb"])
            except search.Unreadable as e:
                skipped.append({"path": rel, "why": str(e)})
                continue
            if blocks:
                files.append((rel, blocks))
            else:
                skipped.append({"path": rel, "why": "в файле нет текста (возможно, это скан — распознавать картинки программа не умеет)"})
        if not files:
            raise ai.AiError("Нечего отправлять: " + "; ".join(f"«{s['path'].rsplit('/', 1)[-1]}» — {s['why']}" for s in skipped[:3]))
        text, used, total = ai.pack_files(files)
        title = str(b.get("title") or files[0][0].rsplit("/", 1)[-1])[:200]
        job["phase"] = f"жду ответ ({cl.host})"
        if job["task"] == "summary":
            system, user = ai.build_summary(title, text)
            r = cl.complete(system, user, 2500)
            res = {"text": r["text"]}
        else:
            system, user = ai.build_quiz(title, text, b.get("count"))
            r = cl.complete(system, user, 4096)
            qs, ans = ai.split_quiz(r["text"])
            res = {"text": r["text"], "questions": qs, "answers": ans}
        res.update({"truncated": r["truncated"], "model": r["model"], "sent": len(user), "files": [f[0] for f in files], "skipped": skipped,
                    "used_chars": used, "total_chars": total})
        return res

    def ai_job_state(self, q) -> dict:
        j = self.ai_jobs.get(q.get("id", ""))
        if not j:
            return {"ok": False, "error": "Запрос не найден (программу перезапускали?)."}
        return {"ok": True, "id": j["id"], "state": j["state"], "phase": j["phase"], "secs": int(time.time() - j["started"]), "result": j["result"], "error": j["error"]}

    def ai_cancel(self, b) -> dict:
        j = self.ai_jobs.get(str(b.get("id") or ""))
        if j and j["state"] == "run":
            j["state"] = "cancelled"
        return {"ok": True}

    # ── календарь и дедлайны ──
    def cal_out(self) -> Path:
        return Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)

    def cal_store(self) -> deadlines.Store:
        path = self.cal_out() / deadlines.DEADLINES_FILE
        with self.lock:
            if not self._cal or self._cal[0] != path:
                self._cal = (path, deadlines.Store(path))
            return self._cal[1]

    def cal_cfg(self) -> dict:
        return deadlines.normalize(self.settings.get("calendar"))

    def cal_view(self, q) -> dict:
        cfg = self.cal_cfg()
        out = self.cal_out()
        try:
            days = min(120, max(1, int(q.get("days") or 14)))
            course = int(q.get("course") or 0)
        except ValueError:
            days, course = 14, 0
        lessons = deadlines.load_lessons(out) if cfg["lessons"] else []
        v = deadlines.view(self.cal_store(), lessons, cfg, time.time(), days=days, show=q.get("show") or "all", course=course,
                           with_done=(q.get("done") == "1") if "done" in q else None, with_gone=q.get("gone") == "1")
        ics = out / deadlines.CALENDAR_DIR / deadlines.ICS_FILE
        v.update({"cfg": cfg, "ics": ics.exists(), "has_lessons": bool(lessons),
                  "offline": self.login_state == "offline" or not self.client})
        return v

    def cal_write(self):
        """Пересобирает .ics и страницу (после «сделано» или смены настроек). Ошибки записи — не помеха."""
        try:
            return deadlines.write_outputs(self.cal_out(), self.cal_store(), self.cal_cfg())
        except OSError as e:
            self.rep.log(f"Календарь: файлы не записаны ({e})", "warn")
            return {}

    def cal_done(self, b):
        ok = self.cal_store().set_done(str(b.get("id") or ""), bool(b.get("done")))
        if ok:
            self.cal_write()
            self._cal_badge_at = -99
        return {"ok": ok}

    def cal_settings_info(self):
        known = {c.id: c.fullname for c in self.courses}
        for e in self.cal_store().events.values():
            if e.get("course_id") and e.get("course"):
                known.setdefault(e["course_id"], e["course"])
        known.pop(1, None)
        return {"cfg": self.cal_cfg(), "groups": deadlines.groups_meta(),
                "courses": sorted(({"id": k, "name": v} for k, v in known.items()), key=lambda c: c["name"].lower())}

    def cal_settings_save(self, b):
        cfg = deadlines.normalize(b.get("cfg"))
        with self.lock:
            self.settings["calendar"] = cfg
            self.save_settings()
        self._cal_badge_at = -99
        return {"ok": True, "cfg": cfg, "files": self.cal_write()}

    def cal_ics(self):
        """Тело .ics для скачивания (всегда свежее из сохранённых данных)."""
        w = self.cal_write()
        p = Path(w["ics"]) if w else self.cal_out() / deadlines.CALENDAR_DIR / deadlines.ICS_FILE
        return p.read_bytes() if p.exists() else b""

    def cal_badge(self) -> int:
        """Сколько неотмеченных сроков уже просрочено или истекает сегодня — для значка на вкладке (кэш на 20 секунд)."""
        now = time.monotonic()
        if now - self._cal_badge_at < 20:
            return self._cal_badge
        try:
            v = deadlines.view(self.cal_store(), [], self.cal_cfg(), time.time(), days=1, with_done=False)
            self._cal_badge = v["counts"]["overdue"] + v["counts"]["today"]
        except Exception:
            self._cal_badge = 0
        self._cal_badge_at = now
        return self._cal_badge

    def cal_watch_tick(self):
        """Раз в несколько минут, пока открыто окно: не пора ли напомнить о сроке (без сети, по сохранённому)."""
        try:
            n = mo.calendar_watch(self.cal_out(), self.settings, self.vault, self.rep)
            if n:
                self.rep.log(f"Календарь: напоминаний о сроках — {n}")
        except Exception as e:
            self.rep.log(f"Календарь: проверка сроков не удалась ({mo.describe_error(e)})", "warn")

    def _calendar_job(self, params):
        """Только обновить сроки из календаря Moodle (без скачивания материалов)."""
        t0 = time.monotonic()
        try:
            if not self.client:
                self.try_saved_login()
            if not self.client:
                raise mo.LoginError("нужно войти в Moodle")
            client = self.client
            client.control = self.control
            client.rep = self.rep
            r = mo.calendar_sync(client, self.cal_out(), self.settings, self.rep)
            ncfg = news.normalize(self.settings.get("news"))
            evs = [e for e in r["events"] if ncfg["enabled"] and ncfg["track"].get(e["kind"])]
            if evs:
                try:
                    news.NewsLog(self.cal_out() / mo.NEWS_FILE).add(evs)
                    mo.deliver_news(evs, self.settings, self.vault, self.rep)
                except Exception as e:   # уведомления не должны ломать обновление сроков
                    self.rep.log(f"Уведомления о сроках не отправлены: {mo.describe_error(e)}", "warn")
            self.last_result = {"ok": r["ok"], "kind": "calendar", "aborted": None if r["ok"] else "error",
                                "text": f"{r['text']} ({mo.fmt_dur(time.monotonic() - t0)})"}
        except (mo.Stopped, KeyboardInterrupt):
            self.last_result = {"ok": False, "text": "Обновление сроков остановлено", "aborted": "stopped", "kind": "calendar"}
        except mo.SessionExpired:
            self.last_result = {"ok": False, "text": "Сессия Moodle истекла — войдите заново", "aborted": "session", "kind": "calendar"}
        except Exception as e:
            self.last_result = {"ok": False, "text": f"Календарь: {mo.describe_error(e)}", "aborted": "error", "kind": "calendar"}
            self.rep.log(self.last_result["text"], "error")
        finally:
            self._cal_badge_at = -99.0
            if self.client:
                self.client.control = self.idle_control
            with self.lock:
                self.job_state = "idle"
                self.control = None

    # ── правила скачивания ──
    # ── «Что нового» ──
    def news_log(self) -> news.NewsLog:
        path = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT) / mo.NEWS_FILE
        with self.lock:
            if not self._news or self._news[0] != path:
                self._news = (path, news.NewsLog(path))
            return self._news[1]

    def news_unread(self) -> int:
        try:
            return self.news_log().unread()
        except OSError:
            return 0

    def news_list(self, q):
        return self.news_log().listing(kind=q.get("kind") or "", course=int(q.get("course") or 0) or 0,
                                       unread_only=q.get("unread") == "1", limit=min(int(q.get("limit") or 300), 1000))

    def news_read(self, b):
        ids = b.get("ids")
        log = self.news_log()
        n = log.mark_read(None if ids in (None, "all") else [str(x) for x in ids])
        if b.get("clear"):
            log.clear_read()
        return {"ok": True, "marked": n, "unread": log.unread()}

    def news_settings_info(self):
        cfg = news.normalize(self.settings.get("news"))
        sec = self.vault.load_notify()
        tok, hook = sec.get("telegram_token", ""), sec.get("webhook_url", "")
        courses = {c.id: c.fullname for c in self.courses}
        for c in self.news_log().listing(limit=1)["courses"]:
            if c["id"] and c["id"] not in courses:
                courses[c["id"]] = c["name"]
        return {"cfg": cfg, "kinds": news.kinds_meta(),
                "formats": [{"key": k, "title": v} for k, v in news.WEBHOOK_FORMATS.items()],
                "secrets": {"telegram_token": {"set": bool(tok), "hint": news.mask_secret(tok)},
                            "webhook_url": {"set": bool(hook), "hint": news.mask_url(hook)}},
                "env": {"telegram": bool(os.environ.get("MOODLE_TELEGRAM_TOKEN")), "webhook": bool(os.environ.get("MOODLE_WEBHOOK_URL"))},
                "courses": [{"id": i, "name": n} for i, n in sorted(courses.items(), key=lambda kv: kv[1].lower())]}

    def news_settings_save(self, b):
        cfg = b.get("cfg") if isinstance(b.get("cfg"), dict) else None
        errs = news.validate(cfg) if cfg is not None else []
        hook = (b.get("webhook_url") or "").strip()
        if hook and news.check_webhook_url(hook):
            errs.append(news.check_webhook_url(hook))
        if errs:
            return {"ok": False, "error": "; ".join(errs)}
        with self.lock:
            if cfg is not None:
                self.settings["news"] = news.normalize(cfg)
                self.save_settings()
            sec = self.vault.load_notify()
            tok = (b.get("telegram_token") or "").strip()
            changed = False
            if b.get("clear_telegram_token"):
                changed = sec.pop("telegram_token", None) is not None or changed
            elif tok:
                sec["telegram_token"], changed = tok, True
            if b.get("clear_webhook_url"):
                changed = sec.pop("webhook_url", None) is not None or changed
            elif hook:
                sec["webhook_url"], changed = hook, True
            if changed:
                self.vault.save_notify(sec)
        return {"ok": True}

    def news_test(self, b):
        cfg = news.normalize(self.settings.get("news"))
        chans = [c for c in (b.get("channels") or []) if c in ("toast", "telegram", "webhook")]
        if not chans:
            return {"ok": False, "error": "не выбран ни один способ уведомления"}
        ev = news.make_event("announcement", 0, "Проверка связи", "Тестовое уведомление",
                             detail="Если вы это видите — уведомления настроены верно.", key=str(time.time()))
        r = mo.send_news([ev], cfg, self.vault, self.rep, toast=True, channels=chans,
                         use_proxy=bool(self.settings.get("use_system_proxy")))
        res = {k: v for k, v in r.items() if v}
        return {"ok": all(v["ok"] for v in res.values()), "results": res}

    def rules_info(self):
        st = self.settings
        return {
            "rules": dl_rules.normalize(st.get("rules")),
            "layout_dir": st.get("layout_dir") if st.get("layout_dir") is not None else dl_rules.DEFAULT_DIR_TPL,
            "layout_file": st.get("layout_file") or dl_rules.DEFAULT_FILE_TPL,
            "meta": {
                "presets": [{"key": k, **v} for k, v in dl_rules.PRESETS.items()],
                "ext_groups": dl_rules.EXT_GROUPS,
                "mods": [{"key": k, "title": t} for k, t in dl_rules.MOD_CHOICES],
                "placeholders": dl_rules.PLACEHOLDERS,
                "layout_presets": dl_rules.LAYOUT_PRESETS,
                "defaults": dl_rules.DEFAULT_RULES,
            }}

    def rules_save(self, b):
        errs = []
        if "rules" in b:
            errs += dl_rules.validate(b["rules"])
        for k in ("layout_dir", "layout_file"):
            if k in b:
                errs += dl_rules.template_errors(str(b[k] or ""))
        if errs:
            return {"ok": False, "error": "; ".join(errs)}
        with self.lock:
            if "rules" in b:
                self.settings["rules"] = dl_rules.normalize(b["rules"])
            for k in ("layout_dir", "layout_file"):
                if k in b:
                    self.settings[k] = str(b[k] or "").strip()
            self.save_settings()
        return {"ok": True}

    def job_status(self):
        st = self.job_state
        if st != "idle" and self.control:
            if self.control.stopped:
                st = "stopping"
            elif self.control.paused:
                st = "paused"
            elif self.job_hint in ("waiting_network", "waiting_window"):
                st = self.job_hint
        return st

    # ── планировщик Windows ──
    def schedule(self, enabled: bool, hhmm: str):
        if os.name != "nt":
            return {"ok": False, "error": "расписание через планировщик есть только в Windows (в Linux используйте cron)"}
        if not enabled:
            r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True,
                               creationflags=0x08000000)
            self.settings["schedule_on"] = False
            self.save_settings()
            return {"ok": True}
        if mo.FROZEN:
            cmd = f'"{sys.executable}" --sync -y'
        else:
            # pythonw.exe — без чёрного окна консоли посреди ночи; итог придёт уведомлением и в _журнал.log
            exe = Path(sys.executable)
            quiet = exe.with_name("pythonw.exe")
            cmd = f'"{quiet if quiet.exists() else exe}" "{mo.APP_DIR / "moodle_offline.py"}" --sync -y'
        r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", hhmm, "/TN", TASK_NAME, "/TR", cmd],
                           capture_output=True, text=True, creationflags=0x08000000)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout or "schtasks вернул ошибку").strip()}
        self.settings["schedule_on"] = True
        self.settings["schedule_time"] = hhmm
        self.settings["schedule_exe"] = sys.executable if mo.FROZEN else ""
        self.save_settings()
        return {"ok": True}

    def repair_schedule(self):
        """Ночная синхронизация в планировщике Windows хранит путь к .exe. Положили новую версию в другое место —
        старая задача указывает в пустоту. Если путь сменился, пересоздаём задачу с тем же временем."""
        st = self.settings
        if os.name != "nt" or not mo.FROZEN or not st.get("schedule_on") or st.get("schedule_exe") == sys.executable:
            return
        r = self.schedule(True, st.get("schedule_time") or "03:10")
        self.rep.log("Ночная синхронизация: путь к программе изменился, задачу в планировщике обновил." if r.get("ok")
                     else f"Ночная синхронизация: не удалось обновить задачу в планировщике ({r.get('error')}).", "info" if r.get("ok") else "warn")


# ════════════════════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНОЕ: открыть файл/папку, выбор папки
# ════════════════════════════════════════════════════════════════════════════
def open_path(p: Path, reveal=False):
    p = Path(p)
    if ANDROID:
        if p.is_dir() or reveal:            # «Открыть папку» / «Показать в папке» — как проводник на компьютере: открываем приложение «Файлы»
            folder = p if p.is_dir() else p.parent
            if not android.is_apk():
                raise OSError("папки на телефоне открывает приложение «Файлы». Путь: " + str(folder))
            if android.in_private_storage(folder, Path.home()):
                how = ("нажмите «Положить в Документы»" if android.shared_storage(Path.home())
                       else "нажмите «Разрешить доступ», а затем «Положить в Документы»")
                raise OSError("Материалы лежат в памяти приложения — «Файлы» её не показывают. Под полем «Папка для сохранения» " + how + ".")
            if not android.open_folder(folder):
                raise OSError("не удалось открыть приложение «Файлы». Папка: " + str(folder))
            return
        if not android.open_file(p):
            raise OSError("не удалось открыть файл приложением телефона (нужен пакет termux-tools); откройте его в приложении «Файлы»")
        return
    if os.name == "nt":
        if reveal and p.exists() and p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(p if p.exists() else p.parent))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)] if reveal else ["open", str(p)])
    else:
        target = p.parent if reveal and p.is_file() else p
        subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pick_folder(initial: str):
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    path = filedialog.askdirectory(initialdir=initial or str(Path.home()), title="Куда сохранять материалы")
    root.destroy()
    return path or None


EXT_MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
            ".html": "text/html", ".htm": "text/html", ".xml": "application/xml", ".svg": "image/svg+xml",
            ".wasm": "application/wasm", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
            ".otf": "font/otf", ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
            ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav", ".pdf": "application/pdf"}


def guess_ctype(name: str) -> str:
    ext = Path(name).suffix.lower()
    ct = EXT_MIME.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    return ct + "; charset=utf-8" if ct.startswith("text/") or ct in ("application/json", "application/xml") else ct


def inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


# ════════════════════════════════════════════════════════════════════════════
#  HTTP-СЕРВЕР ИНТЕРФЕЙСА
# ════════════════════════════════════════════════════════════════════════════
APP: App | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "MoodleOffline"

    def log_message(self, *a):
        pass

    _started = False

    def end_headers(self):
        self._started = True
        super().end_headers()

    def _guard(self, fn):
        """Падение внутри обработчика раньше обрывало соединение без ответа, и окно писало «Программа закрыта», хотя программа
        работала. Теперь окно получает ответ 500 с причиной, а сбой попадает в errors.log и «Журнал»."""
        self._started = False
        try:
            fn()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            return                                  # окно закрыло соединение само (остановили скачивание, ушли со страницы) — не сбой
        except Exception as e:                      # noqa: BLE001
            short = log_error(self.path.split("?")[0], e, APP.rep if APP else None)
            if not self._started:
                try:
                    self._send(500, {"error": "Сбой внутри программы: " + short, "internal": True})
                except Exception:                   # noqa: BLE001
                    pass

    def do_GET(self):
        self._guard(self._do_get)

    def do_POST(self):
        self._guard(self._do_post)

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_reader_file(self, url):
        """Отдаёт файл или картинку Moodle окну программы. Картинки запоминаем (читаются и без интернета). Опасные типы
        (html, svg — в них бывают скрипты) отдаём только как скачивание и под «песочницей»."""
        res = APP.reader_file_open(url)
        if res.get("error"):
            return self._send(res.get("code", 404), {"error": res["error"]})
        safe_hdr = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        if res.get("path"):
            data = res["path"].read_bytes()
            return self._send(200, data, res["ct"], {"Cache-Control": "max-age=3600"})
        with APP.reader_lock, APP._no_wait():
            try:
                r, ct, name = APP.reader_file_fetch(res["url"])
            except mo.ExternalLink as e:
                return self._send(502, {"error": "файл лежит на другом сайте: " + e.url})
            except Exception as e:
                return self._send(502, {"error": mo.describe_error(e)})
            try:
                if ct == "text/html" or ct == "application/xhtml+xml":
                    return self._send(502, {"error": "по этому адресу не файл, а страница"})
                inline = ct in APP.READER_INLINE or ct.startswith(("audio/", "video/"))
                size = int(r.headers.get("Content-Length") or 0)
                self.send_response(200)
                self.send_header("Content-Type", ct if not ct.startswith("text/") else ct + "; charset=utf-8")
                if size:
                    self.send_header("Content-Length", str(size))
                disp = "inline" if inline else "attachment"
                self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{quote(name)}")
                for k, v in safe_hdr.items():
                    self.send_header(k, v)
                if not inline:
                    self.send_header("Content-Security-Policy", "sandbox")
                self.end_headers()
                keep = ct.startswith("image/") and ct != "image/svg+xml" and (not size or size <= 5 * 1024 * 1024)
                buf, total = [], 0
                for chunk in r.iter_content(65536):
                    if not chunk:
                        continue
                    self.wfile.write(chunk)
                    if keep:
                        total += len(chunk)
                        if total > 5 * 1024 * 1024:
                            keep, buf = False, []
                        else:
                            buf.append(chunk)
                if keep and buf:
                    ip = reader.image_path(mo.DATA_DIR, res["url"])
                    try:
                        ip.parent.mkdir(parents=True, exist_ok=True)
                        ip.with_name(ip.name + ".ct").write_text(ct, encoding="utf-8")
                        ip.write_bytes(b"".join(buf))
                        reader.prune_files(mo.DATA_DIR)
                    except OSError:
                        pass
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                r.close()

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")      # защита от DNS-rebinding

    def _token_ok(self, tok: str) -> bool:
        return self._host_ok() and secrets.compare_digest(tok or "", APP.token)

    def _authorized(self, q) -> bool:
        return self._token_ok(self.headers.get("X-Token") or q.get("t", ""))

    def _serve_files(self, rel: str):
        """Файл из папки с материалами. Докачка частями (Range) нужна видео и аудио: без неё их нельзя перематывать."""
        out = Path(APP.settings.get("out_dir") or mo.DEFAULT_OUT)
        target = out / rel
        if not rel or not inside(out, target) or not target.is_file():
            return self._send(404, {"error": "not found"})
        try:
            size = target.stat().st_size
        except OSError:
            return self._send(404, {"error": "not found"})
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:                                   # «последние N байт»
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", guess_ctype(target.name))
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(target, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def _do_get(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ("/", "/index.html"):
            return self._send(200, (UI_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/ping":
            ok = self._authorized(q)
            return self._send(200, {"ok": ok, **({"version": mo.VERSION, "pid": os.getpid()} if ok else {})})
        if u.path.startswith("/f/"):
            # Ключ — в самом адресе: относительные ссылки страницы (скрипты, стили, картинки, соседние разделы пакета)
            # получают тот же префикс сами. С «?t=» они приходили без ключа, и страницы из курсов открывались пустыми.
            tok, _, rel = u.path[3:].partition("/")
            if not self._token_ok(unquote(tok)):
                return self._send(403, {"error": "forbidden"})
            return self._serve_files(unquote(rel))
        if not self._authorized(q):
            return self._send(403, {"error": "forbidden"})
        if u.path == "/api/state":
            if not APP.window_seen and android.is_apk():
                APP.window_seen = True
                print("Окно приложения подключилось к программе", flush=True)
            return self._send(200, self.state(int(q.get("since", 0) or 0), int(q["dsince"]) if q.get("dsince", "").isdigit() else None))
        if u.path == "/api/courses":
            return self._send(200, APP.course_list())
        if u.path == "/api/course":
            return self._send(200, APP.course_structure(int(q.get("id", 0) or 0), live=q.get("live") == "1"))
        if u.path == "/api/schedule/groups":
            return self._send(200, APP.schedule_groups(q.get("dept") or None, bool(q.get("refresh"))))
        if u.path == "/api/schedule/excel/links":
            return self._send(200, APP.excel_links())
        if u.path == "/api/update":
            return self._send(200, APP.update_status())
        if u.path == "/api/schedule":
            return self._send(200, APP.schedule_status())
        if u.path == "/api/electives":
            return self._send(200, APP.electives_state())
        if u.path == "/api/transfer":
            return self._send(200, APP.transfer_state())
        if u.path == "/api/rules":
            return self._send(200, APP.rules_info())
        if u.path == "/api/news":
            return self._send(200, APP.news_list(q))
        if u.path == "/api/mcp":
            return self._send(200, APP.mcp_info())
        if u.path == "/api/ai":
            return self._send(200, APP.ai_info())
        if u.path == "/api/ai/job":
            return self._send(200, APP.ai_job_state(q))
        if u.path == "/api/notes":
            return self._send(200, APP.notes_list(q))
        if u.path == "/api/notes/get":
            return self._send(200, APP.notes_get(q))
        if u.path == "/api/news/settings":
            return self._send(200, APP.news_settings_info())
        if u.path == "/api/calendar":
            return self._send(200, APP.cal_view(q))
        if u.path == "/api/watch":
            return self._send(200, APP.watch_info())
        if u.path == "/api/calendar/settings":
            return self._send(200, APP.cal_settings_info())
        if u.path == "/api/calendar/ics":
            data = APP.cal_ics()
            if not data:
                return self._send(404, {"error": "календарь ещё не создан — обновите сроки"})
            return self._send(200, data, "text/calendar; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="moodle_calendar.ics"'})
        if u.path == "/api/search":
            return self._send(200, APP.search_query(q))
        if u.path == "/api/search/status":
            return self._send(200, APP.search_info())
        if u.path == "/api/cover":
            r = APP.cover(int(q.get("id", 0) or 0))
            if not r:
                return self._send(404, {"error": "no cover"})
            return self._send(200, r[0], r[1], {"Cache-Control": "max-age=86400"})
        if u.path == "/api/reader/file":
            return self._send_reader_file(q.get("url", ""))
        if u.path.startswith("/files/"):
            return self._serve_files(unquote(u.path[len("/files/"):]))
        return self._send(404, {"error": "not found"})

    def _do_post(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._authorized(q):
            return self._send(403, {"error": "forbidden"})
        p = u.path
        if p == "/api/bye":                  # окно закрывается (sendBeacon из pagehide): если оно не вернётся — выходим
            APP.bye_at = time.monotonic()
            return self._send(200, {"ok": True})
        if p == "/api/transfer/upload":      # большой файл — читаем потоком, не в память
            cats = [c for c in (q.get("cats") or "").split(",") if c]
            return self._send(200, APP.transfer_upload(self.rfile, int(self.headers.get("Content-Length") or 0), cats,
                                                       q.get("force") == "1"))
        if p == "/api/schedule/excel/upload":
            return self._send(200, APP.excel_upload(self.rfile, int(self.headers.get("Content-Length") or 0), q.get("name", "")))
        if p == "/api/schedule/import":
            return self._send(200, APP.schedule_import(self.rfile, int(self.headers.get("Content-Length") or 0), q.get("name", "")))
        b = self._body()
        if p == "/api/login":
            return self._send(200, APP.login(b.get("url", ""), b.get("username", ""), b.get("password", ""),
                                             bool(b.get("remember")), b.get("cookie", "")))
        if p == "/api/logout":
            APP.logout()
            return self._send(200, {"ok": True})
        if p == "/api/offline":
            APP.go_offline(b.get("reason") or "без входа")
            return self._send(200, {"ok": True})
        if p == "/api/relogin":
            if APP.login_state == "offline":
                APP.login_state, APP.client = "idle", None
            return self._send(200, {"ok": True})
        if p == "/api/refresh_courses":
            if APP.client and APP.job_state == "idle":
                try:
                    APP.courses = APP.client.get_courses()
                except Exception as e:
                    return self._send(200, {"ok": False, "error": mo.describe_error(e)})
            return self._send(200, {"ok": True})
        if p == "/api/settings":
            allowed = {"mode", "out_dir", "pdf", "zip", "course_ids", "quiet_hours", "quiet_hours_on",
                       "page_delay", "file_delay", "wait_minutes", "use_system_proxy", "original_names",
                       "index_in_mode2", "wait_for_server", "no_geo_check", "base_url", "android_wakelock", "keep_versions", "save_grades"}
            with APP.lock:
                for k, v in b.items():
                    if k in allowed:
                        APP.settings[k] = v
                if APP.settings.get("no_geo_check"):
                    APP.geo = {"ip": None, "cc": None, "checked": False, "warning": "", "skipped": True}
                if APP.settings.get("quiet_hours"):
                    try:
                        mo.parse_hours(APP.settings["quiet_hours"])
                    except ValueError as e:
                        return self._send(200, {"ok": False, "error": str(e)})
                APP.save_settings()
            if ANDROID and "android_wakelock" in b:
                threading.Thread(target=android.wake_lock, args=(bool(b["android_wakelock"]),), daemon=True).start()
            return self._send(200, {"ok": True})
        if p == "/api/update/check":
            return self._send(200, APP.update_check(True))
        if p == "/api/update/install":
            return self._send(200, APP.update_install_start())
        if p == "/api/update/restart":
            return self._send(200, APP.update_restart())
        if p == "/api/update/settings":
            return self._send(200, APP.update_settings(b))
        if p == "/api/rules/save":
            return self._send(200, APP.rules_save(b))
        if p == "/api/mcp/settings":
            return self._send(200, APP.mcp_settings_save(b))
        if p == "/api/mcp/token":
            return self._send(200, APP.mcp_token_action(b))
        if p == "/api/mcp/log_clear":
            APP.mcp_log.clear()
            return self._send(200, {"ok": True})
        if p == "/api/ai/settings":
            return self._send(200, APP.ai_settings_save(b))
        if p == "/api/ai/models":
            return self._send(200, APP.ai_models(b))
        if p == "/api/ai/test":
            return self._send(200, APP.ai_test(b))
        if p == "/api/ai/run":
            return self._send(200, APP.ai_run(b))
        if p == "/api/ai/cancel":
            return self._send(200, APP.ai_cancel(b))
        if p == "/api/notes/save":
            return self._send(200, APP.notes_save(b))
        if p == "/api/notes/delete":
            return self._send(200, APP.notes_delete(b))
        if p == "/api/notes/restore":
            return self._send(200, APP.notes_restore(b))
        if p == "/api/notes/pin":
            return self._send(200, APP.notes_pin(b))
        if p == "/api/notes/delete_many":
            return self._send(200, APP.notes_delete_many(b))
        if p == "/api/notes/restore_many":
            return self._send(200, APP.notes_restore_many(b))
        if p == "/api/news/read":
            return self._send(200, APP.news_read(b))
        if p == "/api/news/settings":
            return self._send(200, APP.news_settings_save(b))
        if p == "/api/news/test":
            return self._send(200, APP.news_test(b))
        if p == "/api/calendar/done":
            return self._send(200, APP.cal_done(b))
        if p == "/api/calendar/settings":
            return self._send(200, APP.cal_settings_save(b))
        if p == "/api/calendar/sync":
            if APP.geo.get("warning") and not b.get("ignore_geo") and not APP.settings.get("no_geo_check"):
                return self._send(200, {"ok": False, "geo": APP.geo["warning"]})
            return self._send(200, APP.start_job({"kind": "calendar"}))
        if p == "/api/search/index":
            return self._send(200, APP.search_start(bool(b.get("full"))))
        if p == "/api/search/stop":
            APP.search_ev.set()
            return self._send(200, {"ok": True})
        if p == "/api/search/settings":
            return self._send(200, APP.search_settings_save(b))
        if p == "/api/start":
            if b.get("course_ids") is not None:
                APP.settings["course_ids"] = b["course_ids"]
                APP.save_settings()
            if APP.geo.get("warning") and not b.get("ignore_geo") and not APP.settings.get("no_geo_check"):
                return self._send(200, {"ok": False, "geo": APP.geo["warning"]})
            return self._send(200, APP.start_job({"sync": bool(b.get("sync")), "course_ids": b.get("course_ids"),
                                                  "wait_for_server": bool(b.get("wait_for_server"))}))
        if p == "/api/schedule/save":
            for k in ("schedule_group", "schedule_sources", "schedule_urls", "schedule_pdf", "schedule_auto", "schedule_auto_range"):
                if k in b:
                    APP.settings[k] = b[k]
            if b.get("schedule_group"):   # можно писать по-русски, как на сайте, — храним код из адреса
                APP.settings["schedule_group"] = sched.resolve_group(b["schedule_group"])
            APP.save_settings()
            if b.get("start"):
                return self._send(200, APP.start_job({"kind": "schedule", "range": b.get("range", "2weeks"),
                                                      "from": b.get("from"), "to": b.get("to")}))
            return self._send(200, {"ok": True})
        if p == "/api/schedule/other/save":
            return self._send(200, APP.other_save(b))
        if p == "/api/schedule/other/refresh":
            return self._send(200, APP.other_refresh(b))
        if p == "/api/schedule/other/flags":
            return self._send(200, APP.other_flags(b))
        if p == "/api/schedule/other/delete":
            return self._send(200, APP.other_delete(b))
        if p == "/api/schedule/profile/use":
            return self._send(200, APP.profile_use(b))
        if p == "/api/schedule/share":
            return self._send(200, APP.schedule_share(b))
        if p == "/api/schedule/excel/choose":
            return self._send(200, APP.excel_choose(b))
        if p == "/api/schedule/excel/auto":
            return self._send(200, APP.excel_auto())
        if p == "/api/transfer/serve":
            return self._send(200, APP.transfer_serve(b.get("cats") or [], b.get("ip")))
        if p == "/api/transfer/stop":
            return self._send(200, APP.transfer_stop())
        if p in ("/api/transfer/export", "/api/transfer/pull"):
            if p.endswith("export") and not b.get("cats"):
                return self._send(200, {"ok": False, "error": "отметьте, что переносить"})
            return self._send(200, APP.start_job({"kind": "transfer", "action": p.rsplit("/", 1)[1], "cats": b.get("cats") or [],
                                                  "address": b.get("address") or "", "force": bool(b.get("force"))}))
        if p == "/api/electives/refresh":
            for k in ("electives_url", "electives_watch", "electives_watch_hours"):
                if k in b:
                    APP.settings[k] = b[k]
            APP.save_settings()
            if b.get("settings_only"):
                return self._send(200, {"ok": True, "state": APP.electives_state()})
            return self._send(200, APP.electives_refresh(b.get("password") or None, b.get("username") or None))
        if p == "/api/reader":
            return self._send(200, APP.reader_page(b.get("url", ""), bool(b.get("live"))))
        if p == "/api/electives/choose":
            return self._send(200, APP.electives_choose(b.get("block"), b.get("primary") or [], b.get("backup") or [],
                                                        b.get("password") or None, b.get("username") or None))
        if p == "/api/electives/register":
            return self._send(200, APP.electives_register(b.get("event"), b.get("password") or None, b.get("username") or None))
        if p == "/api/electives/retract":
            return self._send(200, APP.electives_retract(b.get("reg"), b.get("password") or None, b.get("username") or None))
        if p == "/api/electives/snapshot":
            return self._send(200, APP.electives_snapshot(b.get("password") or None, b.get("username") or None))
        if p == "/api/electives/act":
            return self._send(200, APP.electives_act(b.get("id", ""), b.get("password") or None, b.get("username") or None))
        if p == "/api/android/action":
            return self._send(200, APP.android_action(str(b.get("name", ""))))
        if p == "/api/open_url":
            url = b.get("url", "")
            if not url.startswith(("http://", "https://")):
                return self._send(200, {"ok": False, "error": "неверный адрес"})
            if ANDROID:
                android.open_url(url)
            else:
                webbrowser.open(url)
            return self._send(200, {"ok": True})
        if p == "/api/disarm":
            APP.armed = None
            return self._send(200, {"ok": True})
        if p in ("/api/pause", "/api/resume", "/api/stop", "/api/skip"):
            c = APP.control
            if c:
                {"/api/pause": c.pause, "/api/resume": c.resume, "/api/stop": c.stop,
                 "/api/skip": c.skip_current}[p]()
            if p == "/api/stop":
                APP.armed = None
            return self._send(200, {"ok": True})
        if p == "/api/geo":
            return self._send(200, APP.check_geo())
        if p == "/api/open":
            out = Path(APP.settings.get("out_dir") or mo.DEFAULT_OUT)
            rel = b.get("path", "")
            target = out / rel if rel else out
            if not inside(out, target) and target != out:
                return self._send(403, {"error": "вне папки сохранения"})
            if not target.exists():
                if rel:
                    return self._send(200, {"ok": False, "error": "файл не найден"})
                target.mkdir(parents=True, exist_ok=True)
            try:
                open_path(target, reveal=bool(b.get("reveal")))
            except Exception as e:
                return self._send(200, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True})
        if p == "/api/watch/settings":
            return self._send(200, APP.watch_save(b.get("cfg") or {}))
        if p == "/api/watch/audit":
            return self._send(200, APP.watch_audit())
        if p == "/api/watch/run":
            return self._send(200, APP.auto_run())
        if p == "/api/watch/autostart":
            return self._send(200, APP.autostart_set(bool(b.get("enabled"))))
        if p == "/api/data/seen":
            datadir.mark_seen(mo.DATA_DIR)
            return self._send(200, {"ok": True})
        if p == "/api/data/import":
            path = (b.get("path") or "").strip() or pick_folder(str(mo.APP_DIR.parent))
            if not path:
                return self._send(200, {"ok": False, "cancelled": True})
            return self._send(200, APP.data_import(path))
        if p == "/api/pick_folder":
            path = pick_folder(APP.settings.get("out_dir", ""))
            if path:
                APP.settings["out_dir"] = path
                APP.save_settings()
            return self._send(200, {"ok": bool(path), "path": path or ""})
        if p == "/api/schedule":
            return self._send(200, APP.schedule(bool(b.get("enabled")), b.get("time", "03:00")))
        if p == "/api/restart":
            return self._send(200, APP.restart())
        if p == "/api/quit":
            if APP.control:
                APP.control.stop()
            if b.get("why") == "replaced":
                # просит новая версия: окну нужно успеть увидеть «closing» и закрыться само, потом уже выключаем сервер
                APP.closing = "replaced"
                threading.Timer(CLOSE_GRACE, APP.shutdown.set).start()
            else:
                APP.shutdown.set()
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def state(self, since, dsince=None):
        APP.note_poll()
        snap = APP.rep.snapshot(since, dsince)
        st = APP.settings
        return {
            "version": mo.VERSION, "closing": APP.closing, "closing_why": APP.closing_why, "can_restart": not ANDROID,
            "login": {"state": APP.login_state, "error": APP.login_error, "user": APP.user_name,
                      "base": APP.client.base if APP.client else st.get("base_url"), "username": st.get("username", ""),
                      "remember": bool(st.get("remember", True)), "can_encrypt": APP.vault.can_encrypt,
                      "offline": APP.offline if APP.login_state == "offline" else None,
                      "has_cache": APP.OFFLINE_FILE.exists()},
            "net": APP.net, "geo": APP.geo,
            "job": {"state": APP.job_status(), "kind": APP.job_kind, "armed": bool(APP.armed),
                    "started": APP.job_started, "result": APP.last_result, **snap},
            "settings": {**{k: st.get(k) for k in ("mode", "out_dir", "pdf", "zip", "course_ids", "quiet_hours",
                                                   "quiet_hours_on", "schedule_on", "schedule_time", "wait_for_server",
                                                   "use_system_proxy", "original_names", "index_in_mode2", "no_geo_check", "android_wakelock")},
                         "keep_versions": bool(st.get("keep_versions", True)), "save_grades": bool(st.get("save_grades", True))},
            "platform": os.name, "android": ANDROID, "android_info": APP.android_info() if ANDROID else None, "browser_pdf": bool(mo.find_browser()),
            "news_unread": APP.news_unread(),
            "update": APP.update_brief(),
            "cal_badge": APP.cal_badge(),
            "search": APP.search_progress(),
            "data": APP.data_info(),
        }


# ════════════════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ════════════════════════════════════════════════════════════════════════════
def find_app_browser():
    b = mo.find_browser()
    return str(b) if b else None


def open_window(url: str):
    if ANDROID:
        if not android.open_url(url):
            print("Откройте адрес в браузере телефона (нажмите и подержите — «Копировать»).")
        return
    b = find_app_browser()
    if b:
        prof = mo.DATA_DIR / "window-profile"
        prof.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen([b, f"--app={url}", f"--user-data-dir={prof}", "--no-first-run",
                              "--no-default-browser-check", "--window-size=1320,880", "--disable-extensions"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    webbrowser.open(url)


def running_instance():
    """Уже запущенная копия программы (по gui.json + проверка связи). → {url, port, token, version, pid} или None.
    version — None, если копия настолько старая, что не сообщает версию."""
    info = mo.DATA_DIR / "gui.json"
    try:
        d = json.loads(info.read_text(encoding="utf-8"))
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{d['port']}/api/ping?t={d['token']}", timeout=2) as r:
            j = json.loads(r.read())
        if j.get("ok"):
            return {"url": f"http://127.0.0.1:{d['port']}/?t={d['token']}", "port": d["port"], "token": d["token"],
                    "version": j.get("version"), "pid": j.get("pid") or d.get("pid")}
    except Exception:
        return None
    return None


def existing_instance():
    r = running_instance()
    return r["url"] if r else None


class _WinApi:
    """Окна Windows через ctypes (без сторонних библиотек): список видимых окон верхнего уровня и «закрыть как кнопкой ×»."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.u = ctypes.windll.user32

    def windows(self):
        ct, wt, u = self.ct, self.wt, self.u
        out = []
        proc = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def cb(hwnd, _lp):
            if u.IsWindowVisible(hwnd):
                n = u.GetWindowTextLengthW(hwnd)
                title = ct.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, title, n + 1)
                cls = ct.create_unicode_buffer(256)
                u.GetClassNameW(hwnd, cls, 256)
                out.append((hwnd, title.value, cls.value))
            return True
        u.EnumWindows(proc(cb), 0)
        return out

    def close(self, hwnd):
        self.u.PostMessageW(hwnd, 0x0010, 0, 0)         # WM_CLOSE — то же, что нажать «×»


def close_old_windows(title: str = "Moodle Offline", api=None) -> int:
    """Windows: закрыть окна прежней копии программы. Окно-приложение — отдельное окно Edge/Chrome, и копии до 1.17.1 сами его
    не закрывали: после обновления оставалось пустое окно «Программа закрыта — перезапустите её». Берём только окна Chromium
    (класс Chrome_WidgetWin_1) с заголовком ровно «Moodle Offline»: у обычной вкладки заголовок длиннее («… — Microsoft Edge»).
    Зовём, когда прежняя копия уже ушла, а своё окно ещё не открыто. Любой сбой — тихо (окно тогда закроют вручную).
    → сколько окон закрыто."""
    try:
        if api is None:
            if os.name != "nt":
                return 0
            api = _WinApi()
        n = 0
        for hwnd, t, cls in api.windows():
            if t == title and cls == "Chrome_WidgetWin_1":
                api.close(hwnd)
                n += 1
        return n
    except Exception:
        return 0


def hand_off(old: dict, timeout: float = 75.0) -> bool:
    """Новая версия просит прежнюю (ещё работающую в фоне) закрыться и ждёт, пока та освободит место. → True, если место свободно.
    Иначе (раньше так и было) запуск новой версии просто открывал окно старой, и казалось, что обновление «не встало»."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{old['port']}/api/quit?t={old['token']}", data=b'{"why": "replaced"}', method="POST",
                                     headers={"Content-Type": "application/json", "X-Token": old["token"]})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass                                        # возможно, она уже закрывается — это видно ниже
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        cur = running_instance()
        if not cur:
            if close_old_windows():                   # окно прежней копии (в нём ещё «Программа закрыта») — закрываем за неё
                time.sleep(1.5)                       # браузеру нужно успеть закрыться: свежий запуск с тем же профилем в этот миг мог бы потеряться
            return True
        if cur["port"] != old["port"]:
            return True                               # место уже занято другой копией: её окно не трогаем
        time.sleep(0.5)
    return False


def gui_main(no_window=False, port=0, keep_running=False, on_ready=None):
    global APP
    mo.ensure_console_utf8()
    mo.load_env()
    migrated = mo.prepare_data_dir()
    restart = os.environ.pop("MOODLE_OFFLINE_RESTART", "") == "1"      # нас запустила кнопка «Перезапустить»: прежнюю копию нужно заменить, а не открывать её окно
    old = running_instance() if not no_window else None
    if old:
        ov = old.get("version")
        if not restart and (ov == mo.VERSION or (ov and updater.is_newer(ov, mo.VERSION))):
            # та же (или более новая) версия уже работает — просто открываем её окно
            print("Программа уже запущена — открываю окно." if ov == mo.VERSION else
                  f"Уже работает более новая версия {ov} (этот файл — {mo.VERSION}) — открываю её окно.")
            open_window(old["url"])
            return 0
        # прежняя версия ещё работает (например, свёрнута или докачивает): просим её закрыться и запускаем эту
        print("Перезапуск: прошу прежнюю копию закрыться (текущая загрузка остановится и продолжится позже)." if restart else
              f"Работает прежняя версия {ov or 'старее 1.16.2'} — прошу её закрыться (текущая загрузка остановится и продолжится позже).")
        if not hand_off(old):
            print("Прежняя версия не закрылась за 75 секунд — открываю её окно. Закройте её кнопкой «Остановить программу» и запустите новую снова.")
            open_window(old["url"])
            return 1
    if getattr(sys, "frozen", False):
        updater.cleanup_later(os.path.abspath(sys.executable))  # остатки обновления рядом с программой (прежний файл, недокачанное); с повторами: прежний процесс ещё завершается
    APP = App()
    if migrated:
        APP.rep.log(f"Данные прошлой версии перенесены из {migrated['from']} (файлов: {migrated['files']}). Старая папка осталась как была.")
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={APP.token}"
    mo.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (mo.DATA_DIR / "gui.json").write_text(json.dumps({"port": port, "token": APP.token, "pid": os.getpid()}), encoding="utf-8")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=APP.heartbeat_loop, daemon=True).start()
    threading.Thread(target=lambda: APP.mcp_apply(wait=15.0 if restart else 5.0), daemon=True).start()
    if not APP.settings.get("no_geo_check"):
        threading.Thread(target=APP.check_geo, daemon=True).start()
    threading.Thread(target=APP.try_saved_login, daemon=True).start()
    threading.Thread(target=APP.repair_schedule, daemon=True).start()
    if keep_running:
        APP.keep_running = True
    if on_ready:
        on_ready(url)         # приложение .apk получает адрес окна (ключ доступа в журнал не пишем)
    if android.is_apk():
        print(f"{mo.APP_NAME} {mo.VERSION}: сервер приложения запущен.")
        if APP.settings.get("android_wakelock"):
            android.wake_lock(True)
    elif ANDROID:
        print(f"{mo.APP_NAME} {mo.VERSION}: программа запущена в Termux.")
        print(f"Откройте в браузере телефона: {url}")
        print("Закрыть вкладку можно — программа продолжит работать. Остановить: Ctrl+C здесь или кнопка «Остановить программу» в окне.")
        if APP.settings.get("android_wakelock"):
            android.wake_lock(True)
    else:
        print(f"{mo.APP_NAME} {mo.VERSION}: интерфейс открыт в отдельном окне.")
        print(f"Если окно не появилось, откройте в браузере: {url}")
        print("Это окно консоли можно свернуть. Закроете окно программы — консоль закроется сама (после загрузки)." if not APP.keep_running
              else "Закрыть вкладку можно — программа продолжит работать. Остановить: Ctrl+C.")
    if not no_window:
        open_window(url)
    try:
        while not APP.shutdown.wait(1):
            pass
    except KeyboardInterrupt:
        if APP.control:
            APP.control.stop()
    # ждём аккуратного завершения загрузки (состояние сохраняется)
    if APP.job_thread and APP.job_thread.is_alive():
        APP.job_thread.join(timeout=30)
    srv.shutdown()
    if ANDROID and APP.settings.get("android_wakelock"):
        android.wake_lock(False)
    try:
        (mo.DATA_DIR / "gui.json").unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] not in ("--no-window", "--port", "--keep-running"):
        # любые параметры консольной версии: python gui.py --sync  (так же работает собранный .exe)
        sys.exit(mo.cli_main(argv))
    nw = "--no-window" in argv
    prt = int(argv[argv.index("--port") + 1]) if "--port" in argv else 0
    sys.exit(gui_main(no_window=nw, port=prt, keep_running="--keep-running" in argv))

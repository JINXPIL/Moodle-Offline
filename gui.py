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

import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import dl_rules
import moodle_offline as mo
import deadlines
import news
import search
import electives as el
import schedule as sched

UI_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "ui"
TASK_NAME = "MoodleOffline - ночная синхронизация"
SSO_DOMAIN = "sevsu.ru"      # единый вход вуза: auth.sevsu.ru, iot.sevsu.ru, timetable.sevsu.ru
HEARTBEAT_IDLE = 30
HEARTBEAT_BUSY = 60
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

    def emit(self, msg, level):
        with self.glock:
            self.seq += 1
            self.lines.append({"seq": self.seq, "t": datetime.now().strftime("%H:%M:%S"), "level": level,
                               "msg": msg.strip("\n")})

    def course(self, i, n, course):
        super().course(i, n, course)
        self.course_info = {"i": i, "n": n, "name": course.fullname, "id": course.id}

    def item(self, i, n, sec, it):
        super().item(i, n, sec, it)
        self.item_info = {"i": i, "n": n, "name": it.name, "kind": it.kind, "section": sec.name}

    def file_start(self, title, total, initial=0, quiet=False):
        now = time.monotonic()
        self.file = {"title": title, "total": total, "done": initial, "speed": 0.0, "_t": now, "_d": initial,
                     "quiet": quiet}

    def file_progress(self, done):
        f = self.file
        if not f:
            return
        f["done"] = done
        now = time.monotonic()
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

    def saved(self, rel_out, title, size, status):
        if status in ("new", "page"):
            self.counts["new"] += 1
            self.counts["bytes"] += size or 0
        elif status in ("skipped", "copied"):
            self.counts["skipped"] += 1
        if status == "skipped":
            return
        with self.glock:
            self.dseq += 1
            self.downloads.appendleft({"id": self.dseq, "path": rel_out, "title": title, "size": size,
                                       "status": status, "t": datetime.now().strftime("%H:%M"),
                                       "course": (self.course_info or {}).get("name", "")})

    def failed(self, entry):
        self.counts["failed"] += 1
        with self.glock:
            self.dseq += 1
            self.downloads.appendleft({"id": self.dseq, "path": "", "title": entry.get("item", ""),
                                       "size": 0, "status": "fail", "error": entry.get("error", ""),
                                       "url": entry.get("url", ""), "t": datetime.now().strftime("%H:%M"),
                                       "course": entry.get("course", "")})

    def snapshot(self, since):
        with self.glock:
            lines = [ln for ln in self.lines if ln["seq"] > since]
            downloads = list(self.downloads)[:150]
        f = self.file
        fs = None
        if f:
            eta = (f["total"] - f["done"]) / f["speed"] if f["total"] and f["speed"] > 1 else None
            fs = {"title": f["title"], "total": f["total"], "done": f["done"], "speed": f["speed"], "eta": eta}
        return {"lines": lines, "seq": self.seq, "downloads": downloads, "file": fs,
                "course": self.course_info, "item": self.item_info, "counts": dict(self.counts)}


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
        self.last_poll = time.monotonic()
        self.token = secrets.token_urlsafe(18)
        self.shutdown = threading.Event()
        self.el_lock = threading.Lock()
        self.electives_checked = 0.0
        self.offline = {"reason": "", "since": "", "cached": ""}   # офлайн-режим: работаем с сохранённым
        self.relogin_tried = 0.0
        self._news = None             # (путь, NewsLog)
        self._cal = None              # (путь, deadlines.Store)
        self.cal_checked = 0.0        # когда в последний раз проверяли «горящие» сроки (monotonic)
        self._cal_badge, self._cal_badge_at = 0, -99.0     # значок на вкладке «Календарь» (кэш на 20 секунд)
        self._search = None           # (путь, search.Index)
        self.search_ev = threading.Event()      # остановка индексации
        self.search_thread = None
        self.search_prog = {"running": False, "phase": "", "done": 0, "total": 0, "current": ""}
        self.search_last = None       # итог последней индексации: {"ok", "text", ...}

    # ── настройки ──
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
                        "checked": datetime.now().strftime("%H:%M:%S")}
        if prev == "down" and status in ("ok", "slow"):
            self.on_server_up()

    def heartbeat_loop(self):
        """Светофор: лёгкий запрос к сайту раз в 30 с (во время загрузки — раз в 60 с).
        Заодно раз в 5 с проверяем, открыто ли окно: если закрыли и ничего не качается — выходим."""
        next_probe = 0.0
        while not self.shutdown.is_set():
            now = time.monotonic()
            if now >= next_probe:
                try:
                    prober = self.client or self.new_client()
                    st, dt = prober.probe()
                    self.set_net(st, "" if dt is None else f"{dt:.1f} с", dt)
                except Exception:
                    pass
                busy = self.job_state != "idle"
                next_probe = time.monotonic() + (HEARTBEAT_BUSY if busy else HEARTBEAT_IDLE)
            if time.monotonic() - self.last_poll > 25 and self.job_state == "idle" and not self.armed:
                self.shutdown.set()
                break
            if (self.login_state == "offline" and self.net["status"] in ("ok", "slow") and self.vault.load_secret()
                    and not self.offline.get("need_login")
                    and time.monotonic() - self.relogin_tried > 120):
                threading.Thread(target=self._recover_from_offline, daemon=True).start()
            if time.monotonic() - self.cal_checked > CAL_WATCH_EVERY:     # напоминания о сроках: без сети, из сохранённого
                self.cal_checked = time.monotonic()
                threading.Thread(target=self.cal_watch_tick, daemon=True).start()
            hours = int(self.settings.get("electives_watch_hours", 6) or 6)
            if (self.settings.get("electives_watch") and self.job_state == "idle" and self._el_creds()
                    and time.monotonic() - self.electives_checked > hours * 3600):
                self.electives_checked = time.monotonic()
                threading.Thread(target=self.electives_refresh, daemon=True).start()
            self.shutdown.wait(5)

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
        try:
            self.OFFLINE_FILE.unlink()
        except OSError:
            pass

    # ── курсы ──
    def course_list(self):
        out = Path(self.settings.get("out_dir") or mo.DEFAULT_OUT)
        state_courses = {}
        try:
            with mo.fopen(out / mo.STATE_FILE, "r", encoding="utf-8") as f:
                state_courses = json.load(f).get("courses", {})
        except (OSError, ValueError):
            pass
        res = []
        for c in self.courses:
            d = c.to_dict()
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
                 {"number": s.number, "name": s.name, "items": [
                     {"key": it.key, "name": it.name, "modtype": it.modtype, "kind": it.kind, "url": it.url,
                      "description": it.description, "context": it.context, "online_only": it.online_only, "files": []}
                     for it in s.items]} for s in course.sections]}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        d["downloaded"] = False
        return d

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
                "saved": False, "count": 0, "updated": "", "first": "", "last": "", "dir": sched.SCHEDULE_DIR}
        try:
            with mo.fopen(out / sched.SCHEDULE_DIR / "schedule.json", "r", encoding="utf-8") as f:
                d = json.load(f)
            ls = d.get("lessons", [])
            info.update(saved=True, count=len(ls), updated=d.get("meta", {}).get("generated", ""),
                        first=ls[0]["date"] if ls else "", last=ls[-1]["date"] if ls else "")
        except (OSError, ValueError, KeyError, IndexError):
            pass
        return info

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

    # ── элективы ──
    def _el_creds(self, password=None):
        if password:
            return (self.settings.get("username") or "", password)
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

    def _electives(self, password=None):
        url = self.settings.get("electives_url") or el.ELECTIVES_URL
        host = mo.urlparse(url).hostname
        return el.Electives(Path(self.settings.get("out_dir") or mo.DEFAULT_OUT), mo.DATA_DIR, self.rep, mo.Control(),
                            url=url, creds=self._el_creds(password), cookies=self._sso_cookies(),
                            use_system_proxy=bool(self.settings.get("use_system_proxy")),
                            page_delay=mo.parse_delay(self.settings.get("page_delay"), mo.PAGE_DELAY)), host

    def _keep_portal_cookies(self, e, host):
        self._save_sso(e.cookies_out or e.portal.cookies())

    def electives_state(self):
        e, _ = self._electives()
        d = e.load()
        d["url"] = self.settings.get("electives_url") or el.ELECTIVES_URL
        d["watch"] = bool(self.settings.get("electives_watch"))
        d["watch_hours"] = int(self.settings.get("electives_watch_hours", 6))
        d["has_creds"] = self._el_creds() is not None
        d["dir"] = el.ELECTIVES_DIR
        return d

    def electives_refresh(self, password=None):
        with self.el_lock:
            e, host = self._electives(password)
            try:
                st = e.refresh()
            except Exception as ex:
                return {"ok": False, "error": mo.describe_error(ex),
                        "need_password": "логин и пароль" in str(ex) or "вход не удался" in str(ex),
                        "debug": str(mo.DATA_DIR / "electives_debug.txt")}
            self._keep_portal_cookies(e, host)
            self.electives_checked = time.monotonic()
            if st.get("new"):
                mo.notify(mo.APP_NAME, "Новые элективы: " + "; ".join(mo.short(a["context"], 60) for a in st["new"][:3]))
            return {"ok": True, "state": self.electives_state(), "new": len(st.get("new", []))}

    def electives_act(self, action_id, password=None):
        with self.el_lock:
            e, host = self._electives(password)
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
            moodle_job = params.get("kind") not in ("schedule",)
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
                                      if self.settings.get("quiet_hours_on") else None)
            self.control.reporter = self.rep
            self.rep.reset()
        self.job_thread = threading.Thread(target=self._job, args=(params,), daemon=True)
        self.job_thread.start()
        return {"ok": True}

    def _job(self, params):
        if params.get("kind") == "schedule":
            return self._schedule_job(params)
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
                                 rules=st.get("rules"), dir_template=st.get("layout_dir") or "",
                                 file_template=st.get("layout_file") or "", news=st.get("news"),
                                 calendar=st.get("calendar"), calendar_on=deadlines.normalize(st.get("calendar"))["auto"])
            self.rep.log(f"{'Быстрая синхронизация' if opts.sync else 'Загрузка'}: курсов {len(selected)}, "
                         f"режим {opts.mode}, папка {opts.out_dir}")
            res = mo.run_job(client, selected, opts, self.rep, self.control)
            took = mo.fmt_dur(time.monotonic() - t0)
            head = {"network": "Прервано: нет связи", "session": "Прервано: сессия истекла",
                    "stopped": "Остановлено"}.get(res.aborted, "Готово")
            text = f"{head} за {took}. {mo.summary_text(res, opts)}"
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
        try:
            job = sched.ScheduleJob(Path(st.get("out_dir") or mo.DEFAULT_OUT), st.get("schedule_group") or sched.DEFAULT_GROUP,
                                    st.get("schedule_sources") or sched.DEFAULT_SOURCES, self.rep, self.control,
                                    urls=st.get("schedule_urls"), pdf=bool(st.get("schedule_pdf", True)),
                                    use_system_proxy=bool(st.get("use_system_proxy")),
                                    page_delay=mo.parse_delay(st.get("page_delay"), mo.PAGE_DELAY),
                                    creds=self._el_creds(), cookies=self._sso_cookies())
            try:
                res = job.run(params.get("range", "2weeks"), params.get("from"), params.get("to"))
            finally:
                self._save_sso(job.cookies_out)
            text = (f"Расписание сохранено за {mo.fmt_dur(time.monotonic() - t0)}: {res['total']} пар "
                    f"({res['range'][0]} — {res['range'][1]})")
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
        self.save_settings()
        return {"ok": True}


# ════════════════════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНОЕ: открыть файл/папку, выбор папки
# ════════════════════════════════════════════════════════════════════════════
def open_path(p: Path, reveal=False):
    p = Path(p)
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

    def _authorized(self, q) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False   # защита от DNS-rebinding
        tok = self.headers.get("X-Token") or q.get("t", "")
        return secrets.compare_digest(tok, APP.token)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ("/", "/index.html"):
            return self._send(200, (UI_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/ping":
            return self._send(200, {"ok": self._authorized(q)})
        if not self._authorized(q):
            return self._send(403, {"error": "forbidden"})
        if u.path == "/api/state":
            return self._send(200, self.state(int(q.get("since", 0) or 0)))
        if u.path == "/api/courses":
            return self._send(200, APP.course_list())
        if u.path == "/api/course":
            return self._send(200, APP.course_structure(int(q.get("id", 0) or 0), live=q.get("live") == "1"))
        if u.path == "/api/schedule/groups":
            return self._send(200, APP.schedule_groups(q.get("dept") or None, bool(q.get("refresh"))))
        if u.path == "/api/schedule":
            return self._send(200, APP.schedule_status())
        if u.path == "/api/electives":
            return self._send(200, APP.electives_state())
        if u.path == "/api/rules":
            return self._send(200, APP.rules_info())
        if u.path == "/api/news":
            return self._send(200, APP.news_list(q))
        if u.path == "/api/news/settings":
            return self._send(200, APP.news_settings_info())
        if u.path == "/api/calendar":
            return self._send(200, APP.cal_view(q))
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
        if u.path.startswith("/files/"):
            out = Path(APP.settings.get("out_dir") or mo.DEFAULT_OUT)
            target = out / unquote(u.path[len("/files/"):])
            if not inside(out, target) or not target.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/"):
                ctype += "; charset=utf-8"
            return self._send(200, target.read_bytes(), ctype)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._authorized(q):
            return self._send(403, {"error": "forbidden"})
        b = self._body()
        p = u.path
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
                       "index_in_mode2", "wait_for_server", "no_geo_check", "base_url"}
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
            return self._send(200, {"ok": True})
        if p == "/api/rules/save":
            return self._send(200, APP.rules_save(b))
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
            for k in ("schedule_group", "schedule_sources", "schedule_urls", "schedule_pdf", "schedule_auto"):
                if k in b:
                    APP.settings[k] = b[k]
            if b.get("schedule_group"):   # можно писать по-русски, как на сайте, — храним код из адреса
                APP.settings["schedule_group"] = sched.resolve_group(b["schedule_group"])
            APP.save_settings()
            if b.get("start"):
                return self._send(200, APP.start_job({"kind": "schedule", "range": b.get("range", "2weeks"),
                                                      "from": b.get("from"), "to": b.get("to")}))
            return self._send(200, {"ok": True})
        if p == "/api/electives/refresh":
            for k in ("electives_url", "electives_watch", "electives_watch_hours"):
                if k in b:
                    APP.settings[k] = b[k]
            APP.save_settings()
            if b.get("settings_only"):
                return self._send(200, {"ok": True, "state": APP.electives_state()})
            return self._send(200, APP.electives_refresh(b.get("password") or None))
        if p == "/api/electives/act":
            return self._send(200, APP.electives_act(b.get("id", ""), b.get("password") or None))
        if p == "/api/open_url":
            url = b.get("url", "")
            if not url.startswith(("http://", "https://")):
                return self._send(200, {"ok": False, "error": "неверный адрес"})
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
        if p == "/api/pick_folder":
            path = pick_folder(APP.settings.get("out_dir", ""))
            if path:
                APP.settings["out_dir"] = path
                APP.save_settings()
            return self._send(200, {"ok": bool(path), "path": path or ""})
        if p == "/api/schedule":
            return self._send(200, APP.schedule(bool(b.get("enabled")), b.get("time", "03:00")))
        if p == "/api/quit":
            if APP.control:
                APP.control.stop()
            APP.shutdown.set()
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def state(self, since):
        APP.last_poll = time.monotonic()
        snap = APP.rep.snapshot(since)
        st = APP.settings
        return {
            "version": mo.VERSION,
            "login": {"state": APP.login_state, "error": APP.login_error, "user": APP.user_name,
                      "base": APP.client.base if APP.client else st.get("base_url"), "username": st.get("username", ""),
                      "remember": bool(st.get("remember", True)), "can_encrypt": APP.vault.can_encrypt,
                      "offline": APP.offline if APP.login_state == "offline" else None,
                      "has_cache": APP.OFFLINE_FILE.exists()},
            "net": APP.net, "geo": APP.geo,
            "job": {"state": APP.job_status(), "kind": APP.job_kind, "armed": bool(APP.armed),
                    "started": APP.job_started, "result": APP.last_result, **snap},
            "settings": {k: st.get(k) for k in ("mode", "out_dir", "pdf", "zip", "course_ids", "quiet_hours",
                                                "quiet_hours_on", "schedule_on", "schedule_time", "wait_for_server",
                                                "use_system_proxy", "original_names", "index_in_mode2", "no_geo_check")},
            "platform": os.name, "browser_pdf": bool(mo.find_browser()),
            "news_unread": APP.news_unread(),
            "cal_badge": APP.cal_badge(),
            "search": APP.search_progress(),
        }


# ════════════════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ════════════════════════════════════════════════════════════════════════════
def find_app_browser():
    b = mo.find_browser()
    return str(b) if b else None


def open_window(url: str):
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


def existing_instance():
    info = mo.DATA_DIR / "gui.json"
    try:
        d = json.loads(info.read_text(encoding="utf-8"))
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{d['port']}/api/ping?t={d['token']}", timeout=2) as r:
            if json.loads(r.read()).get("ok"):
                return f"http://127.0.0.1:{d['port']}/?t={d['token']}"
    except Exception:
        return None
    return None


def gui_main(no_window=False, port=0):
    global APP
    mo.ensure_console_utf8()
    mo.load_env()
    url = existing_instance()
    if url and not no_window:
        print("Программа уже запущена — открываю окно.")
        open_window(url)
        return 0
    APP = App()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={APP.token}"
    mo.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (mo.DATA_DIR / "gui.json").write_text(json.dumps({"port": port, "token": APP.token, "pid": os.getpid()}), encoding="utf-8")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=APP.heartbeat_loop, daemon=True).start()
    if not APP.settings.get("no_geo_check"):
        threading.Thread(target=APP.check_geo, daemon=True).start()
    threading.Thread(target=APP.try_saved_login, daemon=True).start()
    print(f"{mo.APP_NAME} {mo.VERSION}: интерфейс открыт в отдельном окне.")
    print(f"Если окно не появилось, откройте в браузере: {url}")
    print("Это окно консоли можно свернуть. Закроете окно программы — консоль закроется сама (после загрузки).")
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
    try:
        (mo.DATA_DIR / "gui.json").unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] not in ("--no-window", "--port"):
        # любые параметры консольной версии: python gui.py --sync  (так же работает собранный .exe)
        sys.exit(mo.cli_main(argv))
    nw = "--no-window" in argv
    prt = int(argv[argv.index("--port") + 1]) if "--port" in argv else 0
    sys.exit(gui_main(no_window=nw, port=prt))

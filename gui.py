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

import moodle_offline as mo
import electives as el
import schedule as sched

UI_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "ui"
TASK_NAME = "MoodleOffline - ночная синхронизация"
HEARTBEAT_IDLE = 30
HEARTBEAT_BUSY = 60


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
            hours = int(self.settings.get("electives_watch_hours", 6) or 6)
            if (self.settings.get("electives_watch") and self.job_state == "idle" and self._el_creds()
                    and time.monotonic() - self.electives_checked > hours * 3600):
                self.electives_checked = time.monotonic()
                threading.Thread(target=self.electives_refresh, daemon=True).start()
            self.shutdown.wait(5)

    def on_server_up(self):
        if self.armed and self.job_state == "idle":
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
    def try_saved_login(self):
        secret = self.vault.load_secret()
        if not secret:
            return
        base = secret.get("base_url") or self.settings.get("base_url")
        self.login_state = "logging"
        try:
            client = self.new_client(base)
            ok = False
            if secret.get("cookies"):
                client.import_cookies(secret["cookies"])
                ok = client.verify()
            if not ok and secret.get("username") and secret.get("password"):
                client.s.cookies.clear()
                client.login(secret["username"], secret["password"])
                ok = True
            if not ok:
                self.login_state = "idle"
                return
            if secret.get("username") and secret.get("password"):
                client.creds = (secret["username"], secret["password"])
            self._after_login(client, secret.get("username", ""), remember=True)
        except Exception as e:
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
        self.courses = client.get_courses()
        self.login_state = "ok"
        self.rep.log(f"Вход выполнен. Курсов: {len(self.courses)}.")

    def logout(self):
        self.vault.forget()
        self.settings.pop("remember", None)
        self.save_settings()
        self.client, self.courses, self.login_state, self.user_name = None, [], "idle", ""

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
            return {"error": "нужно войти в Moodle"}
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
        info = {"group": st.get("schedule_group") or sched.DEFAULT_GROUP,
                "sources": st.get("schedule_sources") or list(sched.SOURCE_ORDER),
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

    def _electives(self, password=None):
        url = self.settings.get("electives_url") or el.ELECTIVES_URL
        host = mo.urlparse(url).hostname
        sec = self.vault.load_secret()
        return el.Electives(Path(self.settings.get("out_dir") or mo.DEFAULT_OUT), mo.DATA_DIR, self.rep, mo.Control(),
                            url=url, creds=self._el_creds(password), cookies=(sec.get("portals") or {}).get(host),
                            use_system_proxy=bool(self.settings.get("use_system_proxy")),
                            page_delay=mo.parse_delay(self.settings.get("page_delay"), mo.PAGE_DELAY)), host

    def _keep_portal_cookies(self, e, host):
        if self.settings.get("remember"):
            try:
                sec = self.vault.load_secret()
                portals = sec.get("portals") or {}
                portals[host] = e.portal.c.export_cookies()
                self.vault.save_secret({**sec, "portals": portals})
            except Exception:
                pass

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
                                 original_names=bool(st.get("original_names")), index_in_mode2=bool(st.get("index_in_mode2")))
            self.rep.log(f"{'Быстрая синхронизация' if opts.sync else 'Загрузка'}: курсов {len(selected)}, "
                         f"режим {opts.mode}, папка {opts.out_dir}")
            res = mo.run_job(client, selected, opts, self.rep, self.control)
            took = mo.fmt_dur(time.monotonic() - t0)
            head = {"network": "Прервано: нет связи", "session": "Прервано: сессия истекла",
                    "stopped": "Остановлено"}.get(res.aborted, "Готово")
            text = f"{head} за {took}. {mo.summary_text(res, opts)}"
            self.last_result = {"ok": res.aborted is None, "text": text, "aborted": res.aborted,
                                "failures": len(res.failures), "new": len(res.new_files)}
            self.rep.log(text)
            if res.aborted != "stopped":
                mo.notify(mo.APP_NAME, text, ok=res.aborted is None)
        except Exception as e:
            msg = mo.describe_error(e)
            self.last_result = {"ok": False, "text": f"Ошибка: {msg}", "aborted": "error"}
            self.rep.log(f"Ошибка: {msg}", "error")
            mo.notify(mo.APP_NAME, f"Ошибка: {msg}", ok=False)
        finally:
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
                                    st.get("schedule_sources") or sched.SOURCE_ORDER, self.rep, self.control,
                                    urls=st.get("schedule_urls"), pdf=bool(st.get("schedule_pdf", True)),
                                    use_system_proxy=bool(st.get("use_system_proxy")),
                                    page_delay=mo.parse_delay(st.get("page_delay"), mo.PAGE_DELAY))
            res = job.run(params.get("range", "2weeks"), params.get("from"), params.get("to"))
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
        if u.path == "/api/schedule":
            return self._send(200, APP.schedule_status())
        if u.path == "/api/electives":
            return self._send(200, APP.electives_state())
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
                      "remember": bool(st.get("remember", True)), "can_encrypt": APP.vault.can_encrypt},
            "net": APP.net, "geo": APP.geo,
            "job": {"state": APP.job_status(), "kind": APP.job_kind, "armed": bool(APP.armed),
                    "started": APP.job_started, "result": APP.last_result, **snap},
            "settings": {k: st.get(k) for k in ("mode", "out_dir", "pdf", "zip", "course_ids", "quiet_hours",
                                                "quiet_hours_on", "schedule_on", "schedule_time", "wait_for_server",
                                                "use_system_proxy", "original_names", "index_in_mode2", "no_geo_check")},
            "platform": os.name, "browser_pdf": bool(mo.find_browser()),
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

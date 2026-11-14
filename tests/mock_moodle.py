"""Фейковый Moodle 3.8 (разметка как у do.sevsu.ru) для проверки moodle_offline.

Флаги окружения:
  FLAKY=1        — случайные обрывы при отдаче больших файлов и редкие 502 на страницах
  OUTAGE=N       — после N-го запроса сервер «падает» (502) на OUTAGE_LEN запросов
  EXPIRE_AFTER=N — после N запросов сессия истекает (проверка повторного входа)
"""
import _root  # noqa: F401  (быстрый запуск сервера без обратного DNS, см. _root.py)
import hashlib
import json
import os
import random
import re
import secrets
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

USER, PASSWORD = "student", "secret"
SESSIONS = {}   # sid -> {"logged": bool, "token": str}
LOCK = threading.Lock()
COUNTER = {"n": 0, "pluginfile": 0, "outage_left": 0, "bytes": 0}
FLAKY = os.environ.get("FLAKY") == "1"
OUTAGE_AT = int(os.environ.get("OUTAGE", "0") or 0)
OUTAGE_LEN = int(os.environ.get("OUTAGE_LEN", "4") or 4)
EXPIRE_AFTER = int(os.environ.get("EXPIRE_AFTER", "0") or 0)
random.seed(int(os.environ.get("SEED", "7")))
PATHLOG = []   # все пути запросов (для проверки, что именно запрашивали): /__paths, /__paths?reset=1
ANN = {"n": 0}   # сколько «лишних» объявлений в форуме курса: /__ann?n=25 (номера тем 1001…1000+n, новые сверху)
CAL = {"n": 0, "fail": "", "extra": 0, "now": 0}   # календарь: /__cal?n=1&fail=timeline&extra=120&now=<unix>


def cal_events(now=None):
    """События календаря относительно «сейчас». n=1: перенос, пропажа (сдали), новый срок."""
    now = int(CAL["now"] or now or time.time())
    A = {"id": 3594, "fullname": "Теория информации для защиты информационных процессов. Часть 2", "shortname": "ТИдляЗИП (часть 2)"}
    B = {"id": 3576, "fullname": "ТИвИБ (Семестр 1)", "shortname": "ТИвИБ(Семестр1)"}
    def ev(i, name, due, course, etype="due", mod="assign", act=None, activity=None, desc=""):
        d = {"id": i, "name": name, "description": desc, "modulename": mod, "eventtype": etype, "timestart": due, "timesort": due,
             "timeduration": 0, "course": course, "url": f"{H}/calendar/view.php?view=day&time={due}",
             "normalisedeventtype": etype, "isactionevent": bool(act)}
        if activity:
            d["activityname"] = activity
        if act:
            d["action"] = {"name": act, "url": f"{H}/mod/{mod}/view.php?id={i}&sesskey=LEAKME", "actionable": True, "itemcount": 1}
        return d
    out = [
        ev(5001, 'Практическая работа №1 "Перевод чисел из одной системы счисления в другую" должен быть выполнен', now + 30 * 3600, A,
           act="Добавить ответ на задание", activity='Практическая работа №1 "Перевод чисел из одной системы счисления в другую"',
           desc="<p>Сдать отчёт в <b>PDF</b></p>"),
        ev(5002, "Лабораторная работа №1 должен быть выполнен", now + 3 * 86400, A, act="Добавить ответ на задание", activity="Лабораторная работа №1"),
        ev(5003, "Тест 1 закрывается", now + 9 * 86400, B, etype="close", mod="quiz", act="Пройти тест"),
        ev(5004, "Реферат должен быть выполнен", now - 2 * 86400, B, act="Добавить ответ на задание", activity="Реферат"),
        ev(5005, "Проверка работ", now + 86400, A, etype="gradingdue", act="Оценить"),
        ev(5006, "Консультация по курсу", now + 4 * 86400, {"id": 1, "fullname": "Сайт", "shortname": "СевГУ"}, etype="user", mod="", act=None),
        ev(5007, "Тест 1 открывается", now + 86400, B, etype="open", mod="quiz", act=None),
    ]
    if CAL["n"] >= 1:
        out = [e for e in out if e["id"] != 5001]                         # 5001 сдали — Moodle убрал срок
        for e in out:
            if e["id"] == 5002:
                e["timestart"] = e["timesort"] = now + 5 * 86400          # перенесли
        out.append(ev(5008, "Домашнее задание 2 должен быть выполнен", now + 5 * 3600, A, act="Добавить ответ на задание", activity="Домашнее задание 2"))
    for k in range(CAL["extra"]):
        out.append(ev(6000 + k, f"Filler {k}", now + 3600 * (k + 1), A, act="Добавить ответ на задание", activity=f"Filler {k}"))
    return out


STEP = {"n": int(os.environ.get("STEP", "0") or 0)}   # «версия» курса для проверки «что нового»: /__step?n=1


def big(n, seed):
    rnd = random.Random(seed)
    return bytes(rnd.getrandbits(8) for _ in range(n))


FILES = {  # путь после /pluginfile.php → (bytes, content-type)
    "/101/mod_resource/content/3/УТП_ТИдляЗИП.pdf": (b"%PDF-1.4 utp " + big(300_000, 1), "application/pdf"),
    "/205/mod_folder/content/4/Методичка ЛР1.pdf": (b"%PDF-1.4 m1 " + big(1_500_000, 2), "application/pdf"),
    "/205/mod_folder/content/4/Шаблоны/Титульный лист.docx": (b"PK docx " + big(40_000, 3), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "/206/mod_folder/content/1/таблица.xlsx": (b"PK xlsx " + big(20_000, 4), "application/vnd.ms-excel"),
    "/301/mod_assign/introattachment/0/Задание ПР1.docx": (b"PK assign " + big(60_000, 5), "application/msword"),
    "/302/mod_assign/introattachment/0/Методические указания ЛР1.pdf": (b"%PDF lr1 " + big(2_500_000, 6), "application/pdf"),
    "/401/mod_page/content/2/схема.png": (b"\x89PNG fake " + big(5_000, 7), "image/png"),
    "/401/mod_page/content/2/Список литературы.pdf": (b"%PDF lit " + big(80_000, 8), "application/pdf"),
    "/501/course/section/77/Рабочая программа.pdf": (b"%PDF rp " + big(120_000, 9), "application/pdf"),
    "/601/mod_label/intro/Лабораторная работа 2 - бланк.doc": (b"DOC " + big(30_000, 10), "application/msword"),
    "/701/mod_imscp/content/2/imsmanifest.xml": (None, "text/xml"),
    "/701/mod_imscp/content/2/index.html": (b'<html><head><link rel="stylesheet" href="css/style.css"></head><body><h1>Lecture 1</h1><img src="img/pic1.png"><a href="part2.html">next</a></body></html>', "text/html"),
    "/701/mod_imscp/content/2/part2.html": (b'<html><body><h1>Part 2</h1><img src="img/pic2.png"></body></html>', "text/html"),
    "/701/mod_imscp/content/2/css/style.css": (b'body{background:url("../img/bg.png")}', "text/css"),
    "/701/mod_imscp/content/2/img/pic1.png": (b"\x89PNG1" + big(3000, 11), "image/png"),
    "/701/mod_imscp/content/2/img/pic2.png": (b"\x89PNG2" + big(3000, 12), "image/png"),
    "/701/mod_imscp/content/2/img/bg.png": (b"\x89PNG3" + big(3000, 13), "image/png"),
    "/801/mod_resource/content/1/Лекция 3.pptx": (b"PK pptx " + big(3_000_000, 14), "application/vnd.ms-powerpoint"),
    "/901/mod_resource/content/1/old_course.pdf": (b"%PDF old " + big(50_000, 15), "application/pdf"),
    "/902/mod_resource/content/1/Лекция 6.pdf": (b"%PDF l6 " + big(70_000, 16), "application/pdf"),
    "/205/mod_folder/content/4/Новый шаблон.docx": (b"PK newtpl " + big(15_000, 17), "application/msword"),
    "/950/mod_forum/attachment/12/Расписание консультаций.pdf": (b"%PDF cons " + big(20_000, 18), "application/pdf"),
}
MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="M1" xmlns="http://www.imsglobal.org/xsd/imscp_v1p1">
 <organizations default="ORG1"><organization identifier="ORG1"><title>Lecture 1</title>
  <item identifier="I1" identifierref="R1"><title>Определение информации</title>
   <item identifier="I2" identifierref="R2"><title>Часть 2</title></item></item>
 </organization></organizations>
 <resources>
  <resource identifier="R1" type="webcontent" href="index.html"><file href="index.html"/><file href="css/style.css"/><file href="img/pic1.png"/></resource>
  <resource identifier="R2" type="webcontent" href="part2.html"><file href="part2.html"/></resource>
 </resources></manifest>""".encode("utf-8")
FILES["/701/mod_imscp/content/2/imsmanifest.xml"] = (MANIFEST, "text/xml")

H = "http://127.0.0.1:{port}"


def head(title, body_id, ctx, sesskey, logged):
    logout = f'<a href="{H}/login/logout.php?sesskey={sesskey}" class="dropdown-item">Выход</a>' if logged else ""
    return f"""<!DOCTYPE html><html dir="ltr" lang="ru"><head><title>{title}</title>
<script>M.cfg = {{"wwwroot":"{H}","sesskey":"{sesskey}","sessiontimeout":"28800","theme":"sevsu","contextid":{ctx}}};</script></head>
<body id="{body_id}" class="format-topics path-course course-3594 context-{ctx} drawer-open-left">
<div class="usermenu">Иванов Иван {logout}</div>
<div id="nav-drawer"><a class="list-group-item" href="{H}/course/view.php?id=3594" data-parent-key="mycourses">ТИдляЗИП (часть 2)</a>
<a class="list-group-item" href="{H}/course/view.php?id=3576" data-parent-key="mycourses">ТИвИБ(Семестр1)</a></div>
<div id="page" class="container-fluid"><header id="page-header"><div class="page-header-headings"><h1>{title}</h1></div></header>
<div id="page-content"><section id="region-main"><div role="main"><span id="maincontent"></span>"""


FOOT = '</div></section></div></div><footer id="page-footer">SEVGU</footer></body></html>'


def completion(cmid):
    """Как на настоящем сайте (Moodle 3.8, тема sevsu): автоотметка, ручная отметка или без отметки."""
    img = lambda n, label: f'<img alt="{label}" class="icon" src="{H}/theme/image.php/sevsu/core/1/i/completion-{n}" title="{label}"/>'
    k = int(cmid) % 4 if str(cmid).isdigit() else 3
    if k == 0:
        return f'<div class="actions"><span class="autocompletion">{img("auto-y", "Выполнено: элемент")}</span></div>'
    if k == 1:
        return (f'<div class="actions"><form action="{H}/course/togglecompletion.php" class="togglecompletion" method="post"><div>'
                f'<input name="id" type="hidden" value="{cmid}"/><input name="sesskey" type="hidden" value="SECRETKEY99"/>'
                f'<button aria-live="assertive" class="btn btn-link">{img("manual-n", "Не выполнено: элемент. Выберите, чтобы отметить элемент как выполненный.")}</button></div></form></div>')
    if k == 2:
        return f'<div class="actions"><span class="autocompletion">{img("auto-pass", "Выполнено: элемент (оценка выше проходного балла)")}</span></div>'
    return ""


def act(cmid, mod, name, extra_cls="", desc="", indent=0, inner_after=""):
    icon_name = {"resource": "Файл", "assign": "Задание", "imscp": "Пакет IMS содержимого", "quiz": "Тест",
                 "forum": "Форум", "folder": "Папка", "url": "Гиперссылка", "page": "Страница", "book": "Книга",
                 "attendance": "Посещаемость"}.get(mod, mod)
    d = f'<div class="contentafterlink"><div class="no-overflow"><p><em>{desc}</em></p></div></div>' if desc else ""
    return f"""<li class="activity {mod} modtype_{mod} {extra_cls}" id="module-{cmid}"><div><div class="mod-indent-outer">
<div class="mod-indent mod-indent-{indent}"></div><div><div class="activityinstance">
<a class="aalink" onclick="" href="{H}/mod/{mod}/view.php?id={cmid}"><img src="{H}/theme/image.php/sevsu/{mod}/1/icon" class="iconlarge activityicon" alt=" " role="presentation" />
<span class="instancename">{name}<span class="accesshide " > {icon_name}</span></span></a></div>
{completion(cmid)}
{d}{inner_after}</div></div></div></li>"""


def label(cmid, html):
    return f"""<li class="activity label modtype_label " id="module-{cmid}"><div><div class="mod-indent-outer"><div class="mod-indent"></div><div>
<div class="contentwithoutlink "><div class="no-overflow"><div class="no-overflow">{html}</div></div></div></div></div></div></li>"""


def section(num, name, summary, acts, cid=3594):
    hide = " accesshide" if num == 0 else ""
    return f"""<li id="section-{num}" class="section main clearfix" role="region" aria-label="{name}">
<div class="left side"></div><div class="right side"></div><div class="content">
<h3 class="sectionname{hide}"><span><a href="{H}/course/view.php?id={cid}#section-{num}">{name}</a></span></h3>
<div class="section_availability"></div><div class="summary"><div class="no-overflow">{summary}</div></div>
<ul class="section img-text">{''.join(acts)}</ul></div></li>"""


def course_3594():
    banner = f'<img src="{H}/pluginfile.php/501/course/section/77/banner.png" alt="">'
    s0 = section(0, "Общее", f'<img src="{H}/pluginfile.php/501/course/section/76/header.jpg">'
                 f'<p><a href="{H}/pluginfile.php/501/course/section/77/%D0%A0%D0%B0%D0%B1%D0%BE%D1%87%D0%B0%D1%8F%20%D0%BF%D1%80%D0%BE%D0%B3%D1%80%D0%B0%D0%BC%D0%BC%D0%B0.pdf">Рабочая программа дисциплины</a></p>', [
        act(1001, "page", "АННОТАЦИЯ КУРСА", desc="Здесь размещено краткое описание курса, цели, задачи и результаты освоения дисциплины."),
        act(1002, "forum", "Объявления по курсу", desc="В этом форуме собраны главные новости и объявления по курсу."),
        act(1003, "forum", "Форум \"Вопросы-ответы\""),
        act(1004, "attendance", "Посещаемость"),
        *([act(1005, "resource", "Учебно-тематический план", desc="Здесь размещен учебно-тематический план дисциплины")] if STEP["n"] < 2 else []),
    ])
    s1 = section(1, "РАЗДЕЛ 1. Основы теории информации", banner, [
        act(1101, "imscp", "Лекция 1. Определение информации"),
        act(1102, "resource", "Лекция 2. Системы счисления"),
        act(1103, "assign", "Практическая работа №1 \"Перевод чисел из одной системы счисления в другую\"", indent=1),
        act(1104, "assign", "Лабораторная работа №1. \"Арифметические операции в позиционных системах счисления\"\"", indent=2),
    ])
    s2 = section(2, "РАЗДЕЛ 2. Виды оценки информации. Мера Хартли. Энтропия Шеннона.", banner, [
        act(1201, "page", "Лекция 4. Энтропия, негэнтропия и их связь с информацией"),
        label(1202, "<h4>Лабораторные работы</h4><p>Бланк отчёта: <a href=\"" + H + "/pluginfile.php/601/mod_label/intro/"
              + quote("Лабораторная работа 2 - бланк.doc") + "\">скачать бланк</a></p>"),
        act(1203, "folder", "Материалы к лабораторным"),
        act(1204, "folder", "Таблицы (на странице)", extra_cls="",
            inner_after=f'<div class="foldertree"><ul><li><span class="fp-filename-icon"><a href="{H}/pluginfile.php/206/mod_folder/content/1/{quote("таблица.xlsx")}?forcedownload=1"><span class="fp-filename">таблица.xlsx</span></a></span></li></ul></div>'),
        act(1205, "quiz", "Тестирование по темам 6-7 (Контрольный модуль_теоретическая часть)"),
    ])
    long_text = "Порядок выполнения: " + "Подробное описание порядка выполнения работы. " * 12
    s3 = section(3, "РАЗДЕЛ 3. Численная оценка канала связи с шумами.", banner, [
        act(1301, "url", "Онлайн-калькулятор энтропии"),
        act(1302, "book", "Лекция 5. Понятие условной энтропии"),
        label(1303, f"<p><strong>Самостоятельная работа</strong></p><p>{long_text}</p>"),
        act(1304, "resource", "Лекция 3. Определение количества информации" + (" (обновлено)" if STEP["n"] >= 2 else "")),
        *([act(1306, "resource", "Лекция 6. Новая тема")] if STEP["n"] >= 1 else []),
        '<li class="activity resource modtype_resource dimmed" id="module-1305"><div class="activityinstance"><span class="instancename">Закрытый материал<span class="accesshide"> Файл</span></span></div><div class="availabilityinfo isrestricted"><span class="badge badge-info">Ограничено</span> Недоступно, пока не выполнены условия: Вы принадлежите к группе лок. ИБ/б-25-1-о</div></li>',
    ])
    return "<div class='course-content'><ul class='topics'>" + s0 + s1 + s2 + s3 + "</ul></div>"


def course_3576_main():
    # формат «одна секция на странице»: на главной только сводки разделов со ссылками
    s0 = section(0, "Общее", "", [], cid=3576)
    lis = []
    for n, nm in ((1, "Тема 1. Введение"), (2, "Тема 2. Пустая")):
        lis.append(f"""<li id="section-{n}" class="section main section-summary clearfix" role="region" aria-label="{nm}">
<h3 class="section-title"><a href="{H}/course/view.php?id=3576&amp;section={n}">{nm}</a></h3><div class="summarytext"></div></li>""")
    return "<ul class='topics'>" + s0 + "</ul><ul class='section-summary'>" + "".join(lis) + "</ul>"


def course_3576_section(n):
    if n == 1:
        return "<ul class='topics'>" + section(1, "Тема 1. Введение", "", [
            act(2101, "resource", "ЛК1 Введение в ИБ"),
        ], cid=3576) + "</ul>"
    return "<ul class='topics'>" + section(2, "Тема 2. Пустая", "", [], cid=3576) + "</ul>"


RES_MAP = {1306: "/902/mod_resource/content/1/Лекция 6.pdf", 1005: "/101/mod_resource/content/3/УТП_ТИдляЗИП.pdf", 1102: "/801/mod_resource/content/1/Лекция 3.pptx",
           1304: "/901/mod_resource/content/1/old_course.pdf", 2101: "/901/mod_resource/content/1/old_course.pdf"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # helpers
    def sid(self):
        m = re.search(r"MoodleSession=([a-z0-9]+)", self.headers.get("Cookie", ""))
        return m.group(1) if m and m.group(1) in SESSIONS else None

    def session(self):
        if getattr(self, "_sess", None):
            return self._sess
        sid = self.sid()
        if not sid:
            sid = secrets.token_hex(8)
            with LOCK:
                SESSIONS[sid] = {"logged": False, "token": secrets.token_hex(6), "key": secrets.token_hex(5)}
            self._newcookie = sid
        self._sess = (sid, SESSIONS[sid])
        return self._sess

    def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        if getattr(self, "_newcookie", None):
            self.send_header("Set-Cookie", f"MoodleSession={self._newcookie}; path=/; HttpOnly")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, loc):
        self.send(303, f"<a href='{loc}'>redirect</a>", headers={"Location": loc})

    def page(self, title, body, body_id="page-x", ctx=980827):
        sid, s = self.session()
        return self.send(200, head(title, body_id, ctx, s["key"], s["logged"]).replace("{H}", H) + body + FOOT)

    # routing
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        global H
        self._newcookie = None
        self._sess = None
        with LOCK:
            COUNTER["n"] += 1
            n = COUNTER["n"]
            if OUTAGE_AT and n == OUTAGE_AT:
                COUNTER["outage_left"] = OUTAGE_LEN
            down = COUNTER["outage_left"] > 0
            if down:
                COUNTER["outage_left"] -= 1
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = unquote(u.path)
        if self.headers.get("User-Agent", "").startswith("python-requests"):
            return self.send(403, "bot UA")
        if path == "/__paths":
            out = json.dumps(PATHLOG)
            if q.get("reset"):
                PATHLOG.clear()
            return self.send(200, out, "application/json")
        if not path.startswith("/__"):
            PATHLOG.append(self.path)
        if path == "/__down":      # /__down?n=K — следующие K запросов отвечают 502; n=-1 — «лежит», пока не пришлёт n=0
            with LOCK:
                n_ = int(q.get("n", "0"))
                COUNTER["outage_left"] = 10 ** 9 if n_ < 0 else n_
            return self.send(200, "ok")
        if down:
            return self.send(502, "<h1>502 Bad Gateway</h1>")
        if FLAKY and path != "/login/index.php" and "pluginfile" not in path and random.random() < 0.06:
            return self.send(502, "<h1>502 Bad Gateway</h1>")
        host_hdr = (self.headers.get("Host") or "").split(":")[0]
        if host_hdr == "localhost":   # «сервер единого входа» (как Keycloak)
            idp = H.replace("127.0.0.1", "localhost")
            if path == "/realms/sevsu/protocol/openid-connect/auth":
                self._newcookie = None
                body = f"""<html><body><div id="kc-content"><form id="kc-form-login" action="{idp}/realms/sevsu/login-actions/authenticate?session_code=SC1&amp;execution=EX1&amp;client_id=moodle&amp;tab_id=T1&amp;st={q.get('state','')}" method="post">
<input tabindex="1" id="username" name="username" type="text" autofocus autocomplete="off"><input tabindex="2" id="password" name="password" type="password" autocomplete="off">
<input type="hidden" id="id-hidden-input" name="credentialId"><input tabindex="4" name="login" id="kc-login" type="submit" value="Вход"></form></div></body></html>"""
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); b = body.encode()
                self.send_header("Set-Cookie", "AUTH_SESSION_ID=kc1; Path=/realms/sevsu/"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
            if path == "/realms/sevsu/login-actions/authenticate":
                ln = int(self.headers.get("Content-Length") or 0)
                f = {k: v[0] for k, v in parse_qs(self.rfile.read(ln).decode()).items()}
                if "kc1" not in (self.headers.get("Cookie") or ""):
                    return self.send(400, "cookie not found")
                if f.get("username") == USER and f.get("password") == PASSWORD:
                    return self.send(302, "", headers={"Location": f"{H}/admin/oauth2callback.php?state={q.get('st','')}&code=GOODCODE"})
                return self.send(200, '<html><body><form id="kc-form-login" action="#" method="post"><input name="username"><input type="password" name="password"></form><span id="input-error" class="kc-feedback-text" aria-live="polite">Неверное имя пользователя или пароль.</span></body></html>')
            return self.send(404, "idp 404")
        if path == "/__geo":
            return self.send(200, json.dumps({"ip": "185.0.0.7", "country": os.environ.get("GEO", "RU")}), "application/json")
        if path == "/__stats":
            return self.send(200, json.dumps(COUNTER), "application/json")
        if path == "/__bump":      # /__bump?id=1005&size=2000 — преподаватель заменил файл ресурса: новая «ревизия» в адресе; size — размер (заглушка)
            cmid = int(q["id"]); oldp = RES_MAP[cmid]; parts = oldp.split("/"); parts[4] = str(int(parts[4]) + 1); newp = "/".join(parts)
            body, ctype = FILES[oldp]
            if q.get("size"):
                body = (b"%PDF-1.4 stub " + big(int(q["size"]), 99 + int(parts[4])))[: int(q["size"])]
            elif q.get("same"):
                pass                                   # тот же файл, только новый адрес (ревизия)
            else:
                body = body + b"\n%new"
            FILES[newp] = (body, ctype); RES_MAP[cmid] = newp
            return self.send(200, json.dumps({"path": newp, "size": len(body)}), "application/json")
        if path == "/__step":
            STEP["n"] = int(q.get("n", 0))
            return self.send(200, json.dumps(STEP), "application/json")
        if path == "/__ann":
            ANN["n"] = int(q.get("n", 0))
            return self.send(200, json.dumps(ANN), "application/json")
        if path == "/__cal":
            for k in ("n", "extra", "now"):
                if k in q:
                    CAL[k] = int(q[k])
            if "fail" in q:
                CAL["fail"] = q["fail"]
            return self.send(200, json.dumps(CAL), "application/json")
        sid, s = self.session()
        if EXPIRE_AFTER and n == EXPIRE_AFTER:
            s["logged"] = False
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else b""

        if path == "/login/index.php":
            if method == "POST":
                f = {k: v[0] for k, v in parse_qs(data.decode()).items()}
                if f.get("logintoken") != s["token"]:
                    return self.page("Вход", '<div class="loginerrors mt-3"><a href="#" id="loginerrormessage" class="accesshide">Неверный логин или пароль, попробуйте заново.</a><div class="alert alert-danger" role="alert">Неверный логин или пароль, попробуйте заново.</div></div>', "page-login-index")
                if f.get("username") == USER and f.get("password") == PASSWORD and os.environ.get("SSO") != "1":
                    s["logged"] = True
                    s["token"] = secrets.token_hex(6)
                    return self.redirect(f"{H}/login/index.php?testsession=38309")
                return self.page("Вход", f'<div class="loginerrors mt-3"><a href="#" id="loginerrormessage" class="accesshide">Неверный логин или пароль, попробуйте заново.</a><div class="alert alert-danger" role="alert">Неверный логин или пароль, попробуйте заново.</div></div>'
                                 f'<form class="mt-3" action="{H}/login/index.php" method="post" id="login"><input type="hidden" name="logintoken" value="{s["token"]}"></form>', "page-login-index")
            if "testsession" in q:
                return self.redirect(f"{H}/my/")
            if s["logged"]:
                return self.page("Вход", "<p>Вы уже вошли</p>", "page-login-index")
            if os.environ.get("SSO") == "1":
                return self.page("Вход", f"""<div class="loginform"><div class="potentialidplist mt-3">
<div class="potentialidp"><a href="{H}/auth/oauth2/login.php?id=1&amp;wantsurl=%2F&amp;sesskey={s['key']}" title="Единая учётная запись СевГУ" class="btn btn-secondary btn-block">Единая учётная запись СевГУ</a></div></div>
<div class="mt-2"><form action="{H}/login/index.php" method="post" id="guestlogin"><input type="hidden" name="logintoken" value="{s['token']}">
<input type="hidden" name="username" value="guest"><input type="hidden" name="password" value="guest"><button class="btn btn-secondary" type="submit">Зайти гостем</button></form></div></div>""", "page-login-index")
            hdr = (f'<nav class="navbar"><form class="navbar-form" action="{H}/login/index.php" method="post">'
                   f'<input type="text" name="username"><input type="password" name="password"><button>Вход</button></form></nav>'
                   if os.environ.get("HEADER_FORM") == "1" else "")
            return self.page("Вход", hdr + f"""<form class="mt-3" action="{H}/login/index.php" method="post" id="login">
<input id="anchor" type="hidden" name="anchor" value=""><input type="hidden" name="logintoken" value="{s['token']}">
<input type="text" name="username" id="username"><input type="password" name="password" id="password">
<input type="checkbox" name="rememberusername" value="1"><button type="submit" id="loginbtn">Вход</button></form>""", "page-login-index")

        if path == "/auth/oauth2/login.php" and not s["logged"]:
            s["oauth_state"] = secrets.token_hex(4)
            idp = H.replace("127.0.0.1", "localhost")
            return self.redirect(f"{idp}/realms/sevsu/protocol/openid-connect/auth?client_id=moodle&response_type=code&state={s['oauth_state']}&redirect_uri={quote(H + '/admin/oauth2callback.php')}")
        if path == "/admin/oauth2callback.php":
            if q.get("code") == "GOODCODE" and q.get("state") == s.get("oauth_state"):
                s["logged"] = True
                return self.redirect(f"{H}/my/")
            return self.page("Ошибка", '<div class="errorbox">bad oauth state</div>')
        if not s["logged"]:
            if path == "/lib/ajax/service.php":
                return self.send(200, json.dumps([{"error": True, "exception": {"errorcode": "servicerequireslogin", "message": "login"}}]), "application/json")
            return self.redirect(f"{H}/login/index.php")

        if path == "/" and not s["logged"]:
            return self.page("СевГУ", "<p>Главная</p>", "page-site-index")
        if path == "/my/":
            return self.page("Личный кабинет", "<div class='block_myoverview'>Обзор курсов</div>", "page-my-index")
        if path == "/lib/ajax/service.php":
            if q.get("sesskey") != s["key"]:
                return self.send(200, json.dumps([{"error": True, "exception": {"errorcode": "invalidsesskey", "message": "bad sesskey"}}]), "application/json")
            req = json.loads(data)
            meth, args = req[0].get("methodname"), req[0].get("args") or {}
            if meth in ("core_calendar_get_action_events_by_timesort", "core_calendar_get_calendar_monthly_view"):
                COUNTER["calendar"] = COUNTER.get("calendar", 0) + 1
                tl = meth.endswith("timesort")
                if CAL["fail"] == "all" or (CAL["fail"] == "timeline" and tl) or (CAL["fail"] == "monthly" and not tl):
                    return self.send(200, json.dumps([{"error": True, "exception": {"errorcode": "servicenotavailable", "message": "Сервис недоступен"}}]), "application/json")
                evs = [] if CAL["fail"] == "empty" else cal_events()
                if tl:
                    evs = [e for e in evs if e.get("action")]      # «График» отдаёт только события с действием
                    evs = [e for e in evs if args.get("timesortfrom", 0) <= e["timesort"] <= (args.get("timesortto") or 10 ** 12)]
                    evs.sort(key=lambda e: (e["timesort"], e["id"]))
                    after, lim = int(args.get("aftereventid") or 0), int(args.get("limitnum") or 20)
                    ids = [e["id"] for e in evs]
                    if after in ids:
                        evs = evs[ids.index(after) + 1:]
                    page = evs[:lim]
                    return self.send(200, json.dumps([{"error": False, "data": {"events": page, "firstid": page[0]["id"] if page else 0,
                                                                              "lastid": page[-1]["id"] if page else 0}}]), "application/json")
                y, mo = int(args["year"]), int(args["month"])
                days = {}
                for e in evs:
                    dt = datetime.fromtimestamp(e["timestart"])
                    if (dt.year, dt.month) == (y, mo):
                        days.setdefault(dt.day, []).append(e)
                return self.send(200, json.dumps([{"error": False, "data": {"weeks": [{"days": [{"mday": d, "events": days[d]} for d in sorted(days)]}]}}]), "application/json")
            cls = args["classification"]
            courses = [] if cls == "hidden" else [
                {"id": 3594, "fullname": "Теория информации для защиты информационных процессов. Часть 2", "shortname": "ТИдляЗИП (часть 2)",
                 "viewurl": f"{H}/course/view.php?id=3594", "courseimage": "data:image/svg+xml;base64,PHN2Zy8+", "progress": 22, "hasprogress": True,
                 "coursecategory": "Кафедра &quot;Информационная безопасность&quot;", "startdate": 1, "enddate": 0, "hidden": False},
                {"id": 3576, "fullname": "ТИвИБ (Семестр 1)", "shortname": "ТИвИБ(Семестр1)", "viewurl": f"{H}/course/view.php?id=3576",
                 "courseimage": "", "progress": 0, "hasprogress": False, "coursecategory": "ИБ", "startdate": 1, "enddate": 2, "hidden": False}]
            if os.environ.get("MANY") == "1" and cls != "hidden":
                extra = [("Алгоритмизация и программирование (ИБ 2 семестр)", "Секция \"Информационные системы\"", 22),
                         ("Защита программ и данных", "Кафедра \"Информационная безопасность\"", 4),
                         ("Защита от угроз информационной безопасности", "Кафедра \"Информационная безопасность\"", 19),
                         ("Высшая математика (3 часть) (10.03.01)", "Кафедра \"Прикладная математика и информатика\"", 2),
                         ("Основы информационной безопасности", "Кафедра \"Информационная безопасность\"", 61),
                         ("Английский язык для IT специалистов. Часть 2", "Кафедра \"Иностранные языки\"", 0)]
                for i, (nm, cat, pr) in enumerate(extra):
                    courses.append({"id": 9650 + i * 7, "fullname": nm, "shortname": nm[:12], "viewurl": "", "courseimage": "",
                                    "progress": pr, "hasprogress": True, "coursecategory": cat, "startdate": 1, "enddate": 0})
            return self.send(200, json.dumps([{"error": False, "data": {"courses": courses, "nextoffset": 0}}]), "application/json")
        if path == "/course/view.php":
            cid = int(q.get("id", 0))
            if cid == 3594:
                return self.page("Теория информации для защиты информационных процессов. Часть 2", course_3594(), "page-course-view-topics")
            if cid == 3576:
                if "section" in q:
                    return self.page("ТИвИБ (Семестр 1)", course_3576_section(int(q["section"])), "page-course-view-topics")
                return self.page("ТИвИБ (Семестр 1)", course_3576_main(), "page-course-view-topics")
            return self.send(404, "no course")
        m = re.match(r"^/mod/(\w+)/view\.php$", path)
        if m:
            mod, cmid = m.group(1), int(q.get("id", 0))
            if mod == "attendance":
                # разметка как на настоящем сайте (mod_attendance, вид студента); по умолчанию — только текущий месяц, view=5 — все занятия
                ALL = [("7.11.26 (Sat)", "10:10 - 11:40", "Практическая работа", "Присутствовал", "1 / 1", "Отмечено студентом"),
                       ("7.11.26 (Sat)", "11:50 - 13:20", "Практическая работа", "Не был", "0 / 1", "автоматически записан системой"),
                       ("13.11.26 (Fri)", "10:10 - 11:40", "Практическая работа", "Опоздал", "0,5 / 1", ""),
                       ("13.11.26 (Fri)", "11:50 - 13:20", "Лекция", "?", "? / 1", ""),
                       ("21.11.26 (Sat)", "10:10 - 11:40", "Практическая работа", "?", "? / 1", ""),
                       ("27.11.26 (Fri)", "10:10 - 11:40", "Лекция", "?", "? / 1", "")]
                rows = ALL if q.get("view") == "5" else ALL[:2]
                body_rows = "".join(f'''<tr class=""> <td class="datecol cell c0" style="width:1px;">{d} <nobr>{tm}</nobr></td> <td class="desccol cell c1" style="text-align:left;width:*;"><p>{ds}</p></td> <td class="statuscol cell c2" style="text-align:center;width:*;">{st}</td> <td class="pointscol cell c3" style="text-align:center;width:1px;">{pt}</td> <td class="remarkscol cell c4 lastcol" style="text-align:center;width:*;">{rm}</td> </tr>''' for d, tm, ds, st, pt, rm in rows)
                return self.page("Посещаемость", f'''<h2>Посещаемость</h2><div class="box generalbox"><p>Уважаемые студенты!</p></div>
    <table class="controls"><tr class="lastrow"><td class="cell c0"></td><td class="cell c1" style="text-align:center;"><script>//<![CDATA[
    M.attendance = {{"cal_cur_date":1794496950}}; //]]></script> <a href="{H}/mod/attendance/view.php?id={cmid}&amp;curdate=1790802000">◄</a>
    <a href="{H}/mod/attendance/view.php?id={cmid}&amp;view=5">Все</a> <a href="{H}/mod/attendance/view.php?id={cmid}&amp;view=2">Неделя</a></td></tr></table>
    <table class="generaltable attwidth boxaligncenter"> <thead> <tr> <th class="header c0 datecol" scope="col">Дата</th> <th class="header c1 desccol" scope="col">Описание</th> <th class="header c2 statuscol" scope="col">Статус</th> <th class="header c3 pointscol" scope="col">Баллы</th> <th class="header c4 lastcol remarkscol" scope="col">Заметки</th> </tr> </thead> <tbody>{body_rows}</tbody></table>
    <table class="attlist"> <tbody><tr class="normal"> <td class="cell c0">Посещенные занятия:</td> <td class="cell c1 lastcol">3</td> </tr> <tr class="normal"> <td class="cell c0">Баллы по всем посещенным занятиям:</td> <td class="cell c1 lastcol">1,5 / 3</td> </tr> <tr class="normal lastrow"> <td class="cell c0">Процент по всем посещенным занятиям:</td> <td class="cell c1 lastcol">50,0%</td> </tr> </tbody> </table>''')
            if mod == "resource":
                if cmid in RES_MAP and q.get("redirect") == "1":
                    return self.redirect(f"{H}/pluginfile.php" + quote(RES_MAP[cmid]))
                return self.page("Файл", f'<div class="resourceworkaround">Нажмите <a href="{H}/pluginfile.php{quote(RES_MAP.get(cmid, "/x/y/z/0/none.pdf"))}">ссылку</a></div>')
            if mod == "forum":
                disc = [(11, "Добро пожаловать на курс"), (10, "Правила курса и аттестации")]
                if STEP["n"] >= 1:
                    disc.insert(0, (12, "Перенос лекции на пятницу"))
                if cmid == 1002 and ANN["n"]:
                    disc = [(1000 + i, f"Объявление {i}") for i in range(ANN["n"], 0, -1)] + disc
                if cmid == 1003:
                    disc = [(30, "Как оформлять отчёты?")]
                rows = "".join(f'<tr class="discussion r0"><td class="topic starter"><a href="{H}/mod/forum/discuss.php?d={d}">{t}</a></td>'
                               f'<td class="author">Иванов И.И.</td><td class="replies"><a href="{H}/mod/forum/discuss.php?d={d}">0</a></td>'
                               f'<td class="lastpost"><a href="{H}/mod/forum/discuss.php?d={d}#p{d}1">Ответ</a> '
                               f'<a href="{H}/mod/forum/post.php?reply={d}1">Ответить</a></td></tr>' for d, t in disc)
                return self.page("Объявления", f'<table class="forumheaderlist table table-hover"><thead><tr><th>Тема</th></tr></thead><tbody>{rows}</tbody></table>')
            if mod == "folder":
                new_tpl = (f'<li><span class="fp-filename-icon"><a href="{H}/pluginfile.php{quote("/205/mod_folder/content/4/Новый шаблон.docx")}?forcedownload=1"><span class="fp-filename">Новый шаблон.docx</span></a></span></li>' if STEP["n"] >= 1 else "")
                return self.page("Материалы к лабораторным", f"""<div class="box generalbox foldertree"><div id="folder_tree0" class="filemanager"><ul><li>
<span class="fp-filename-icon"><a href="{H}/pluginfile.php{quote('/205/mod_folder/content/4/Методичка ЛР1.pdf')}?forcedownload=1"><span class="fp-filename">Методичка ЛР1.pdf</span></a></span></li>
{new_tpl}<li><span class="fp-filename">Шаблоны</span><ul><li><span class="fp-filename-icon"><a href="{H}/pluginfile.php{quote('/205/mod_folder/content/4/Шаблоны/Титульный лист.docx')}?forcedownload=1">Титульный лист.docx</a></span></li></ul></li></ul></div></div>
<a href="{H}/mod/folder/download_folder.php?id={cmid}">Скачать папку</a>""")
            if mod == "url":
                if q.get("redirect") == "1":
                    return self.redirect("https://planetcalc.ru/2476/")
                return self.page("URL", '<div class="urlworkaround"><a href="https://planetcalc.ru/2476/">ссылка</a></div>')
            if mod == "page":
                return self.page("Страница", f"""<h2>Аннотация</h2><div class="box generalbox center clearfix"><div class="no-overflow">
<p>Цели и задачи курса…</p><p><img src="{H}/pluginfile.php{quote('/401/mod_page/content/2/схема.png')}" srcset="x 2x"></p>
<p><a href="{H}/pluginfile.php{quote('/401/mod_page/content/2/Список литературы.pdf')}">Список литературы (PDF)</a></p>
<p><a href="{H}/mod/quiz/view.php?id=1205&amp;sesskey={s['key']}">связанный тест</a></p></div></div>
<div class="modified">Последние изменения: вторник</div>""")
            if mod == "assign":
                att = {1103: "/301/mod_assign/introattachment/0/Задание ПР1.docx", 1104: "/302/mod_assign/introattachment/0/Методические указания ЛР1.pdf"}.get(cmid)
                return self.page("Задание", f"""<h2>Задание</h2><div id="intro" class="box generalbox boxaligncenter"><div class="no-overflow"><p>Выполните перевод чисел…</p></div>
<div id="assign_files_tree"><a href="{H}/pluginfile.php{quote(att)}?forcedownload=1">{att.rsplit('/',1)[-1]}</a></div></div>
<div class="submissionstatustable"><h3>Состояние ответа</h3><table class="generaltable"><tr><td>Состояние</td><td>Отправлено для оценивания</td></tr></table></div>
<form method="post" action="{H}/mod/assign/view.php"><button>Добавить ответ на задание</button></form>""")
            if mod == "quiz":
                return self.page("Тест", f"""<div class="box quizinfo"><p>Разрешено попыток: 2</p></div>
<table class="generaltable quizattemptsummary"><tr><td>1</td><td>Завершено</td><td><a href="{H}/mod/quiz/review.php?attempt=555&amp;cmid={cmid}">Просмотр</a></td></tr></table>
<div class="box quizattempt"><form method="post" action="{H}/mod/quiz/startattempt.php"><button>Начать попытку</button></form></div>""")
            if mod == "book":
                return self.page("Книга", "<p>Глава 1</p>")
            if mod == "imscp":
                base = f"{H}/pluginfile.php/701/mod_imscp/content/2/"
                return self.page("Лекция 1", f"""<div id="imscp_layout"><div id="imscp_toc"><ul><li><a href="{base}index.html" title="Определение информации">Определение информации</a>
<ul><li><a href="{base}part2.html">Часть 2</a></li></ul></li></ul></div><div id="imscp_content"></div></div>""", ctx=701)
            return self.page("Элемент", "<p>онлайн-элемент</p>")
        if path == "/grade/report/overview/index.php":
            G = [(3594, "Теория информации для защиты информационных процессов. Часть 2", "69,75", "239/0"), (3576, "ТИвИБ (Семестр 1)", "-", "-"), (9999, "Курс без доступа", "17,50", "-")]
            rows = "".join(f'''<tr class="" id="grade-report-overview-1_r{i}"><td class="cell c0" id="grade-report-overview-1_r{i}_c0"><a href="{H}/course/user.php?mode=grade&amp;id={cid}&amp;user=1">{nm}</a></td><td class="cell c1" id="grade-report-overview-1_r{i}_c1">{g}</td><td class="cell c2" id="grade-report-overview-1_r{i}_c2">{rk}</td></tr>''' for i, (cid, nm, g, rk) in enumerate(G))
            return self.page("Оценки - Студент", f'''<table class="flexible table table-striped table-hover boxaligncenter generaltable" id="overview-grade"><thead><tr><th class="header c0" scope="col">Название курса</th><th class="header c1" scope="col">Оценка</th><th class="header c2" scope="col">Место</th></tr></thead><tbody>{rows}</tbody></table>''')
        if path == "/mod/forum/discuss.php":
            d = int(q.get("d", 0))
            texts = {12: ("Перенос лекции на пятницу", "Коллеги, лекция в четверг переносится на пятницу, 14:00, аудитория 201. "
                          f'Расписание консультаций: <a href="{H}/pluginfile.php{quote("/950/mod_forum/attachment/12/Расписание консультаций.pdf")}">файл</a>'),
                     11: ("Добро пожаловать на курс", "Здравствуйте! Материалы курса будут появляться по мере изучения тем."),
                     10: ("Правила курса и аттестации", "Аттестация — по результатам тестов и лабораторных работ. Пересдачи по графику."),
                     30: ("Как оформлять отчёты?", "Вопрос студента.")}
            if 1000 < d <= 1000 + ANN["n"]:
                texts[d] = (f"Объявление {d - 1000}", f"Текст объявления номер {d - 1000}.")
            if d not in texts:
                return self.send(404, "no discussion")
            sub, body = texts[d]
            return self.page(sub, f'''<div class="forumpost clearfix firstpost starter"><div class="row header clearfix"><div class="topic starter"><div class="subject">{sub}</div>
<div class="author">от <a href="{H}/user/view.php?id=5">Иванов И.И.</a> - Понедельник, 1 сентября 2025, 10:00</div></div></div>
<div class="row maincontent clearfix"><div class="no-overflow"><div class="posting fullpost"><p>{body}</p></div></div></div>
<div class="commands"><a href="{H}/mod/forum/post.php?reply={d}1">Ответить</a></div></div>''')
        if path == "/grade/report/user/index.php":
            cid = int(q.get("id", 0))
            if cid != 3594:
                return self.page("Ошибка", '<div class="errorbox alert alert-danger">Нет доступа к журналу оценок</div>')
            n = STEP["n"]
            rows = [("Практическая работа №1", "5,00" if n >= 1 else "-", "100,00 %" if n >= 1 else "-", ""),
                    ("Тест 1", "5,00" if n >= 2 else "4,00", "100,00 %" if n >= 2 else "80,00 %", ""),
                    ("Лабораторная работа №1", "-", "-", "Хорошо, но оформите отчёт по ГОСТ" if n >= 2 else ""),
                    ("Итого по курсу", "9,00" if n >= 2 else ("9,00" if n >= 1 else "4,00"), "", "")]
            body = "".join(f'<tr class="item"><th class="level2 leveleven item b1b column-itemname" scope="row"><a href="#">{nm}</a></th><td class="column-weight">-</td>'
                           f'<td class="level2 column-grade">{g}</td><td class="column-range">0–5</td><td class="column-percentage">{pc}</td><td class="column-feedback">{fb}</td></tr>'
                           for nm, g, pc, fb in rows)
            return self.page("Оценки пользователя", f'''<table id="user-grade" class="boxaligncenter generaltable user-grade"><thead><tr><th class="header column-itemname">Элемент оценивания</th>
<th class="header column-weight">Вес</th><th class="header column-grade">Оценка</th><th class="header column-range">Диапазон</th><th class="header column-percentage">Проценты</th><th class="header column-feedback">Отзыв</th></tr></thead><tbody>{body}</tbody></table>''')
        if path == "/mod/quiz/review.php":
            return self.page("Просмотр попытки", """<form action="reviewquestion.php" method="post"><div class="que multichoice deferredfeedback correct">
<div class="info"><h3 class="no">Вопрос <span class="qno">1</span></h3></div><div class="content"><div class="formulation">Что такое бит?
<input type="radio" checked disabled> Единица информации</div><div class="outcome"><div class="rightanswer">Правильный ответ: Единица информации</div></div></div></div></form>""")
        if path == "/mod/book/tool/print/index.php":
            return self.send(200, f"""<!DOCTYPE html><html><head><title>Книга</title></head><body class="context-1302">
<div class="book_info">Книга: Лекция 5</div><div class="book_chapter"><h2>1. Условная энтропия</h2><p>Текст главы…</p></div>
<div class="book_chapter"><h2>2. Взаимная информация</h2><p>Текст…</p></div></body></html>""")
        if path.startswith("/pluginfile.php"):
            rel = path[len("/pluginfile.php"):]
            if rel not in FILES:
                return self.send(404, "not found")
            body, ctype = FILES[rel]
            with LOCK:
                COUNTER["pluginfile"] += 1
            etag = hashlib.sha1(body).hexdigest()
            total = len(body)
            start = 0
            rng = self.headers.get("Range")
            code = 200
            hdrs = {"ETag": f'"{etag}"', "Accept-Ranges": "bytes",
                    "Content-Disposition": f"inline; filename*=UTF-8''{quote(rel.rsplit('/', 1)[-1])}"}
            if rng:
                m2 = re.match(r"bytes=(\d+)-", rng)
                start = int(m2.group(1))
                if start >= total:
                    return self.send(416, "", headers={"Content-Range": f"bytes */{total}"})
                code = 206
                hdrs["Content-Range"] = f"bytes {start}-{total - 1}/{total}"
            chunk = body[start:]
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(chunk)))
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.end_headers()
            if FLAKY and len(chunk) > 500_000 and random.random() < 0.5:
                cut = random.randint(100_000, len(chunk) - 1)
                self.wfile.write(chunk[:cut])
                self.wfile.flush()
                with LOCK:
                    COUNTER["bytes"] += cut
                self.close_connection = True
                try:
                    self.connection.shutdown(2)
                except OSError:
                    pass
                return
            if os.environ.get("SLOW") == "1":
                import time as _t
                for i in range(0, len(chunk), 65536):
                    try:
                        self.wfile.write(chunk[i:i + 65536]); self.wfile.flush()
                    except OSError:
                        return
                    with LOCK:
                        COUNTER["bytes"] += len(chunk[i:i + 65536])
                    _t.sleep(0.05)
                return
            self.wfile.write(chunk)
            with LOCK:
                COUNTER["bytes"] += len(chunk)
            return
        host_hdr = (self.headers.get("Host") or "").split(":")[0]
        if host_hdr == "localhost":   # «сервер единого входа» (как Keycloak)
            idp = H.replace("127.0.0.1", "localhost")
            if path == "/realms/sevsu/protocol/openid-connect/auth":
                self._newcookie = None
                body = f"""<html><body><div id="kc-content"><form id="kc-form-login" action="{idp}/realms/sevsu/login-actions/authenticate?session_code=SC1&amp;execution=EX1&amp;client_id=moodle&amp;tab_id=T1&amp;st={q.get('state','')}" method="post">
<input tabindex="1" id="username" name="username" type="text" autofocus autocomplete="off"><input tabindex="2" id="password" name="password" type="password" autocomplete="off">
<input type="hidden" id="id-hidden-input" name="credentialId"><input tabindex="4" name="login" id="kc-login" type="submit" value="Вход"></form></div></body></html>"""
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); b = body.encode()
                self.send_header("Set-Cookie", "AUTH_SESSION_ID=kc1; Path=/realms/sevsu/"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
            if path == "/realms/sevsu/login-actions/authenticate":
                ln = int(self.headers.get("Content-Length") or 0)
                f = {k: v[0] for k, v in parse_qs(self.rfile.read(ln).decode()).items()}
                if "kc1" not in (self.headers.get("Cookie") or ""):
                    return self.send(400, "cookie not found")
                if f.get("username") == USER and f.get("password") == PASSWORD:
                    return self.send(302, "", headers={"Location": f"{H}/admin/oauth2callback.php?state={q.get('st','')}&code=GOODCODE"})
                return self.send(200, '<html><body><form id="kc-form-login" action="#" method="post"><input name="username"><input type="password" name="password"></form><span id="input-error" class="kc-feedback-text" aria-live="polite">Неверное имя пользователя или пароль.</span></body></html>')
            return self.send(404, "idp 404")
        if path == "/__geo":
            return self.send(200, json.dumps({"ip": "185.0.0.7", "country": os.environ.get("GEO", "RU")}), "application/json")
        if path == "/__stats":
            return self.send(200, json.dumps(COUNTER), "application/json")
        return self.send(404, "not found")


def main():
    global H
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    H = f"http://127.0.0.1:{port}"
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock moodle on {H}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

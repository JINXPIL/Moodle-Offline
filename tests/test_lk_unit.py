# -*- coding: utf-8 -*-
"""«Успеваемость» (lk.py): разбор страницы lk.sevsu.ru/student/grade, цифры, сравнение, хранение, вход.
Страница ниже — вымышленная (предметы и оценки придуманы), но вёрстка та же, что у настоящей страницы."""
import http.server
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
import _root
import moodle_offline as mo
import lk
import news

FAIL = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        FAIL.append(name)

from lk_pages import NAME, CSRF, row, page, S1, S2, S3, PAGE      # вымышленные страницы (общие с test_lk_api_unit.py)

# ── разбор ──
d = lk.parse(PAGE)
check("parse: одна программа, три семестра", len(d["programs"]) == 1 and len(d["programs"][0]["semesters"]) == 3, d)
p = d["programs"][0]
check("parse: название программы без дубля кода", p["name"] == "10.03.01 Информационная безопасность", p["name"])
check("parse: номера семестров по названию («Четвертый» = 4)", [s["n"] for s in p["semesters"]] == [1, 2, 4], [s["n"] for s in p["semesters"]])
r = p["semesters"][0]["rows"]
check("parse: строки первого семестра", len(r) == 5 and r[0]["subject"] == "Алгоритмы и структуры", r[:1])
check("parse: оценка/балл/вид", (r[0]["gk"], r[0]["score"]) == ("3", 3) and (r[1]["gk"], r[1]["score"]) == ("5", 5) and r[3]["gk"] == "pass", r)
check("parse: вид контроля", [x["ck"] for x in r] == ["exam", "credit_diff", "exam", "credit", "credit_diff"], [x["ck"] for x in r])
check("parse: ЗЕТ, часы — числа", r[0]["zet"] == 8 and r[0]["hours"] == 288 and isinstance(r[0]["zet"], int), r[0])
check("parse: дата и ISO", r[0]["date"] == "26.01.2026" and r[0]["iso"] == "2026-01-26", r[0])
r2 = p["semesters"][1]["rows"]
check("parse: пустая оценка — none", r2[0]["gk"] == "none" and r2[0]["grade"] == "" and r2[0]["iso"] == "", r2[0])
check("parse: «Не зачтено» — fail, а не pass", r2[3]["gk"] == "fail", r2[3])
check("parse: дробные ЗЕТ, &amp; в названии", r2[3]["zet"] == 2.5 and r2[3]["subject"] == "История региона & культуры", r2[3])
check("parse: курсовая и ВКР", r2[2]["ck"] == "course" and p["semesters"][2]["rows"][0]["ck"] == "thesis")
dump = json.dumps(d, ensure_ascii=False)
check("parse: имени студента и csrf в данных нет", NAME not in dump and "Тестов" not in dump and CSRF not in dump, dump[:200])
check("parse: не страница успеваемости → пусто", lk.parse("<html><body>Вход</body></html>")["programs"] == [] and not lk.has_rows(lk.parse("")))
check("parse: has_rows", lk.has_rows(d))

# две программы и выбор программы
two = lk.parse(page([("Первый семестр", S1)], extra_progs=[[("Первый семестр", S3)]], select=True))
check("parse: две программы, названия из выпадающего списка", [x["name"] for x in two["programs"]] == ["Первая программа", "Вторая программа"], two["programs"])
# таблицы без вкладок и без шапки (вёрстка поменялась) — всё равно читаем
bare = '<div class="grade-widget"><table><tbody><tr><td>1</td><td>Предмет</td><td>Экзамен</td><td>Хорошо</td><td>3</td><td>108</td><td>01.02.2026</td></tr></tbody></table></div>'
bd = lk.parse(bare)
check("parse: без вкладок и thead — колонки по умолчанию", lk.has_rows(bd) and bd["programs"][0]["semesters"][0]["rows"][0]["gk"] == "4", bd)
# колонки в другом порядке читаются по заголовкам
swapped = ('<div class="grade-widget"><table><thead><tr><th>Дата</th><th>Оценка</th><th>Предмет</th></tr></thead><tbody>'
           '<tr><td>02.02.2026</td><td>Отлично</td><td>Что-то</td></tr></tbody></table></div>')
sd = lk.parse(swapped)
check("parse: колонки по заголовкам, не по порядку", sd["programs"][0]["semesters"][0]["rows"][0]["subject"] == "Что-то"
      and sd["programs"][0]["semesters"][0]["rows"][0]["gk"] == "5")

# ── виды оценок ──
check("grade_kind: пробелы/регистр/ё", lk.grade_kind("  ОТЛИЧНО ") == ("5", 5) and lk.grade_kind("зачтено")[0] == "pass")
check("grade_kind: неудовлетворительно не путается с удовлетворительно", lk.grade_kind("Неудовлетворительно") == ("2", 2))
check("grade_kind: не явился / неизвестное", lk.grade_kind("Не явился")[0] == "absent" and lk.grade_kind("что-то новое")[0] == "other")
check("grade_kind: пустое и прочерк", lk.grade_kind("")[0] == "none" and lk.grade_kind("—")[0] == "none")
check("control_kind: «Зачёт» с ё", lk.control_kind("Зачёт") == "credit" and lk.control_kind("Зачёт с оценкой") == "credit_diff")

# ── цифры ──
st = lk.stats(p["semesters"][0]["rows"])
check("stats: первый семестр — всё сдано", (st["total"], st["graded"], st["passed"], st["failed"], st["left"]) == (5, 5, 5, 0, 0), st)
check("stats: средний балл = (3+5+5+4)/4 = 4.25, зачёт без оценки не входит", st["avg"] == 4.25 and st["n5"] == 2 and st["n3"] == 1, st)
check("stats: ЗЕТ набрано 22 из 22", st["zet_done"] == 22 and st["zet_all"] == 22, st)
st2 = lk.stats(p["semesters"][1]["rows"])
check("stats: второй семестр — 1 сдан, 1 не сдан, 2 без оценки", (st2["passed"], st2["failed"], st2["left"]) == (1, 1, 2), st2)
check("stats: ЗЕТ набраны только за сданное", st2["zet_done"] == 8 and st2["zet_all"] == 14.5, st2)
check("stats: пусто — без деления на ноль", lk.stats([])["avg"] is None and lk.stats([])["total"] == 0)
sm = lk.summary(d)
check("summary: по программе и семестрам", sm[0]["stats"]["total"] == 10 and [s["n"] for s in sm[0]["semesters"]] == [1, 2, 4])
check("current_semester: последний, где есть оценка", sm[0]["cur"] == 2, sm[0]["cur"])
check("current_semester: оценок нет — первый", lk.current_semester({"semesters": [{"n": 3, "rows": [{"gk": "none"}]}]}) == 3)

# ── что изменилось ──
check("diff: прошлого снимка нет — молчим", lk.diff(None, d) == [] and lk.diff({"programs": []}, d) == [])
S2b = [row(1, "Право", "Зачет", "Зачтено", 2, 72, "20.06.2026")] + S2[1:2] + [S2[2]] + [S2[3]]
d2 = lk.parse(page([("Первый семестр", S1), ("Второй семестр", S2b), ("Четвертый семестр", S3)]))
ch = lk.diff(d, d2)
check("diff: появилась оценка по «Право»", len(ch) == 1 and ch[0]["kind"] == "new" and ch[0]["subject"] == "Право" and ch[0]["grade"] == "Зачтено", ch)
check("diff: без изменений — пусто", lk.diff(d, d) == [])
S1c = [S1[0].replace("Удовлетворительно", "Хорошо")] + S1[1:]
d3 = lk.parse(page([("Первый семестр", S1c), ("Второй семестр", S2), ("Четвертый семестр", S3)]))
ch = lk.diff(d, d3)
check("diff: оценку исправили", len(ch) == 1 and ch[0]["kind"] == "changed" and ch[0]["old"] == "Удовлетворительно" and ch[0]["grade"] == "Хорошо", ch)
S2n = S2 + [row(5, "Новый предмет", "Зачет", "", 2, 72, "")]
d4 = lk.parse(page([("Первый семестр", S1), ("Второй семестр", S2n), ("Четвертый семестр", S3)]))
check("diff: новый предмет без оценки — не событие", lk.diff(d, d4) == [])
dsub = lk.parse(page([("Первый семестр", S1), ("Второй семестр", S2), ("Четвертый семестр", S3)]))
S2dup = S2 + [row(5, "Алгоритмы и структуры", "Экзамен", "Отлично", 8, 288, "01.07.2026")]
d5 = lk.parse(page([("Первый семестр", S1), ("Второй семестр", S2dup), ("Четвертый семестр", S3)]))
ch = lk.diff(dsub, d5)
check("diff: тот же предмет в том же семестре дважды — новая строка считается новой", len(ch) == 1 and ch[0]["grade"] == "Отлично", ch)
check("diff: предмет в другом семестре с тем же названием — отдельная запись",
      lk.diff(d, lk.parse(page([("Первый семестр", S1), ("Второй семестр", S2), ("Четвертый семестр", S3)]))) == [])

evs = lk.news_events(lk.diff(d, d2) + lk.diff(d, d3))
check("news_events: вид lk_grade, оценка — личное", len(evs) == 2 and all(e["kind"] == "lk_grade" and e["secret"] for e in evs), evs)
check("news_events: название — предмет, в тексте оценка и семестр", evs[0]["title"] == "Право" and "Зачтено" in evs[0]["detail"] and "2 семестр" in evs[0]["detail"], evs[0])
check("news_events: исправление показывает «было → стало»", "Удовлетворительно → Хорошо" in evs[1]["detail"], evs[1]["detail"])
check("news_events: id стабилен", lk.news_events(lk.diff(d, d2))[0]["id"] == evs[0]["id"] and evs[0]["id"] != evs[1]["id"])
check("news.KINDS знает lk_grade", "lk_grade" in news.KINDS and news.KINDS["lk_grade"][2] is True)

# ── хранение ──
tmp = Path(tempfile.mkdtemp(prefix="lk_"))
check("load: пока ничего нет", lk.load(tmp) is None)
c1 = lk.save(tmp, d, now=1_800_000_000)
files = sorted(x.name for x in (tmp / "Успеваемость").iterdir())
check("save: json и страница в папке «Успеваемость»", files == ["index.html", "lk_grades.json"], files)
check("save: первый раз изменений нет", c1 == [])
back = lk.load(tmp)
check("load: то же и время обновления", back["programs"] == d["programs"] and back["fetched"] == 1_800_000_000)
c2 = lk.save(tmp, d2, now=1_800_000_100)
check("save: второй раз видит новую оценку", len(c2) == 1 and c2[0]["subject"] == "Право", c2)
c3 = lk.save(tmp, d2, now=1_800_000_200)
check("save: то же ещё раз — тихо", c3 == [])
(tmp / "Успеваемость" / "lk_grades.json").write_text("{не json", encoding="utf-8")
check("load: битый файл → None и отложен в сторону", lk.load(tmp) is None and any(".broken" in x.name for x in (tmp / "Успеваемость").iterdir()))
(tmp / "Успеваемость" / "lk_grades.json").write_text('{"programs": 5}', encoding="utf-8")
check("load: не тот формат → None", lk.load(tmp) is None)
# снимок, который прежняя версия записала с чужой страницы («Программа 1», реклама вместо предметов): его нет и «прошлым разом» он не считается
old_snap = {"v": 1, "url": lk.GRADE_URL, "programs": [{"name": "Программа 1", "semesters": [
    {"n": 1, "title": "Вкладка 1", "rows": [{"n": 1, "subject": "Реклама", "control": "", "ck": "other", "grade": "", "gk": "none",
                                             "score": None, "zet": None, "hours": None, "date": "", "iso": ""}]}]}]}
(tmp / "Успеваемость" / "lk_grades.json").write_text(json.dumps(old_snap, ensure_ascii=False), encoding="utf-8")
check("load: снимок старого формата (v1, мог быть с чужой страницы) → None", lk.load(tmp) is None)
tmp2 = Path(tempfile.mkdtemp(prefix="lk_old_"))
(tmp2 / "Успеваемость").mkdir()
(tmp2 / "Успеваемость" / "lk_grades.json").write_text(json.dumps(old_snap, ensure_ascii=False), encoding="utf-8")
check("save: после старого снимка не сыплются «новые оценки» (первый настоящий разбор — молча)", lk.save(tmp2, d, now=1_800_000_300) == [])
check("parse: формат снимка — 2", d.get("v") == 2, d.get("v"))

# ── страница ──
html = lk.render_html({**d, "fetched": 1_800_000_000})
check("render: без скриптов и внешних адресов", "<script" not in html.lower() and "http://" not in html and "https://" not in html)
check("render: сводка и семестры", "Первый семестр" in html and "Четвертый семестр" in html and "средний балл" in html)
check("render: символы экранируются", "История региона &amp; культуры" in html)
evil = lk.parse(page([("Первый семестр", [row(1, "&lt;script&gt;alert(1)&lt;/script&gt;", "Зачет", "Зачтено", 1, 36, "01.01.2026")])]))
check("render: чужой текст из страницы экранирован", "<script>alert(1)</script>" not in lk.render_html(evil) and "&lt;script&gt;" in lk.render_html(evil))
check("render: имени студента нет", NAME not in html and CSRF not in html)

# ── сохранённая вручную страница ──
tmp2 = Path(tempfile.mkdtemp(prefix="lk2_"))
data, chg = lk.import_html(tmp2, PAGE.encode("utf-8"), now=1)
check("import_html: utf-8", lk.has_rows(data) and (tmp2 / "Успеваемость" / "lk_grades.json").exists() and chg == [])
data, chg = lk.import_html(tmp2, PAGE.encode("cp1251", "replace").replace(b'charset=utf-8', b'charset=windows-1251'), now=2)
check("import_html: cp1251", lk.has_rows(data))
for label, raw in (("пустой файл", b""), ("не та страница", "<html>Вход в кабинет</html>".encode()), ("слишком большой", b"x" * (lk.MAX_IMPORT + 1))):
    try:
        lk.import_html(tmp2, raw)
        check(f"import_html: {label} → ошибка", False)
    except lk.LkError as e:
        check(f"import_html: {label} → понятная ошибка", bool(str(e)), str(e))

# ── страница, на которой есть вкладки и таблицы, но это не ведомость (случай с настоящего кабинета: реклама СНО в таблице) ──
HOME = ("<html><head><title>Личный кабинет</title></head><body><div class='container'><ul class='nav nav-tabs'>"
        + "".join(f"<li><a href='#t{i}' data-toggle='tab'>Вкладка {i}</a></li>" for i in range(1, 5)) + "</ul><div class='tab-content'>"
        + "".join(f"<div class='tab-pane' id='t{i}'><table class='table'><tr><td>1</td><td>Хочешь активно участвовать в научно-образовательной "
                  f"жизни университета — присоединяйся к студенческому научному обществу!</td></tr></table></div>" for i in range(1, 5))
        + "</div></div></body></html>")
hd = lk.parse(HOME)
check("parse: страница с вкладками и таблицами, но без ведомости → пусто (не «Программа 1»)", not lk.has_rows(hd) and hd["programs"] == [], hd)
check("parse: пустая страница и просто таблица → пусто", not lk.has_rows(lk.parse("<html><body><table><tr><td>1</td><td>a</td></tr></table></body></html>")))

# ── адреса ──
check("trusted_url: lk.sevsu.ru по https", lk.trusted_url("https://lk.sevsu.ru/student/grade") and lk.trusted_url("https://auth.sevsu.ru/x"))
check("trusted_url: http и чужие — нет", not lk.trusted_url("http://lk.sevsu.ru/") and not lk.trusted_url("https://sevsu.ru.evil.com/")
      and not lk.trusted_url("https://evilsevsu.ru/") and not lk.trusted_url("https://lk.sevsu.ru@evil.com/") and not lk.trusted_url(""))

# ── вход на макете сайта ──
STATE = {"logins": 0, "bad": 0, "grade_gets": 0, "evil_form": False, "page": PAGE, "mode": "ok", "home_gets": 0}

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def _send(self, code, body="", headers=None):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def logged(self):
        return "lksess=ok" in (self.headers.get("Cookie") or "")
    def do_GET(self):
        if self.path.startswith("/student/grade"):
            if not self.logged():
                return self._send(302, headers={"Location": "/user/sign-in/login"})
            if STATE["mode"] == "home" or (STATE["mode"] == "home_once" and STATE["home_gets"] == 0):
                return self._send(302, headers={"Location": "/"})       # кабинет уводит на главную вместо ведомости
            STATE["grade_gets"] += 1
            return self._send(200, STATE["page"])
        if self.path == "/":
            STATE["home_gets"] += 1
            return self._send(200, HOME)
        if self.path.startswith("/user/sign-in/login"):
            action = "http://203.0.113.9/steal" if STATE["evil_form"] else "/user/sign-in/login"
            return self._send(200, f'<html><body><form method="post" action="{action}"><input type="hidden" name="_csrf" value="abc">'
                                   '<input name="LoginForm[username]"><input type="password" name="LoginForm[password]"><button>Войти</button></form></body></html>')
        self._send(404, "нет")
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        if "LoginForm%5Busername%5D=student" in body and "LoginForm%5Bpassword%5D=pw123" in body and "_csrf=abc" in body:
            STATE["logins"] += 1
            return self._send(302, headers={"Location": "/student/grade", "Set-Cookie": "lksess=ok; Path=/"})
        STATE["bad"] += 1
        self._send(200, '<html><body><div class="alert-danger">Неверный логин или пароль</div><form method="post"><input name="LoginForm[username]">'
                        '<input type="password" name="LoginForm[password]"></form></body></html>')

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/student/grade"
lk.INSECURE_TEST = True

class Rep(mo.Reporter):
    def __init__(self): super().__init__(); self.lines = []
    def emit(self, msg, level): self.lines.append(msg)
rep = Rep()
kw = dict(page_delay=(0, 0), url=URL)

got, cookies, diag = lk.fetch(rep, mo.Control(), creds=("student", "pw123"), **kw)
check("fetch: вход по форме и разбор", lk.has_rows(got) and STATE["logins"] == 1 and got["programs"][0]["semesters"][0]["rows"][0]["subject"] == "Алгоритмы и структуры", got)
check("fetch: cookie для следующего раза", any(c["name"] == "lksess" for c in cookies), cookies)
check("fetch: в журнале входа нет пароля, оценок и имени", "pw123" not in "\n".join(diag) and NAME not in "\n".join(diag) and "Отлично" not in "\n".join(diag), diag)
before = STATE["logins"]
got2, _, _ = lk.fetch(rep, mo.Control(), cookies=cookies, **kw)
check("fetch: со старыми cookie — без пароля и без повторного входа", lk.has_rows(got2) and STATE["logins"] == before)
try:
    lk.fetch(rep, mo.Control(), **kw); check("fetch: без пароля и cookie → ошибка", False)
except lk.LkError as e:
    check("fetch: без пароля и cookie → «нужен логин и пароль»", "логин" in str(e), str(e))
try:
    lk.fetch(rep, mo.Control(), creds=("student", "wrong"), **kw); check("fetch: неверный пароль → ошибка", False)
except lk.LkError as e:
    check("fetch: неверный пароль → «вход не удался»", "вход не удался" in str(e), str(e))
STATE["evil_form"] = True
bad_before = STATE["bad"]
try:
    lk.fetch(rep, mo.Control(), creds=("student", "pw123"), **kw); check("fetch: форма уводит пароль на чужой адрес → отказ", False)
except lk.LkError as e:
    check("fetch: пароль на чужой адрес не отправляется", "не сайт СевГУ" in str(e) and STATE["bad"] == bad_before, str(e))
STATE["evil_form"] = False
STATE["mode"] = "home_once"; STATE["home_gets"] = 0
got3, _, diag3 = lk.fetch(rep, mo.Control(), cookies=cookies, **kw)
check("fetch: кабинет увёл на главную один раз → программа сама повторяет запрос ведомости", lk.has_rows(got3) and STATE["home_gets"] == 1, diag3)
STATE["mode"] = "home"; STATE["home_gets"] = 0
try:
    lk.fetch(rep, mo.Control(), cookies=cookies, **kw); check("fetch: кабинет всегда уводит на главную → ошибка, а не «Программа 1»", False)
except lk.LkError as e:
    check("fetch: ошибка говорит, что открылась не страница ведомости", "не страница успеваемости" in str(e), str(e))
    check("fetch: в журнале есть признаки страницы (числа, без текста)", any("grade-widget" in x for x in e.diag) and "научно" not in "\n".join(e.diag), e.diag)
STATE["mode"] = "ok"
STATE["page"] = "<html><body>Сайт на обслуживании</body></html>"
try:
    lk.fetch(rep, mo.Control(), cookies=cookies, **kw); check("fetch: страница без таблиц → ошибка", False)
except lk.LkError as e:
    check("fetch: страница без таблиц → подсказка про «Из файла»", "Из файла" in str(e), str(e))
lk.INSECURE_TEST = False
try:
    lk.fetch(rep, mo.Control(), creds=("student", "pw123"), url="http://127.0.0.1:1/student/grade"); check("fetch: не sevsu.ru → отказ", False)
except lk.LkError as e:
    check("fetch: чужой адрес отвергается сразу", "не похож" in str(e), str(e))
srv.shutdown()

print("\n" + ("ALL PASS" if not FAIL else f"FAILED: {len(FAIL)}\n  " + "\n  ".join(FAIL)))
sys.exit(1 if FAIL else 0)

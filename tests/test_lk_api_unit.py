"""«Успеваемость» через окно программы: /api/lk*, сохранённая страница, вход на макете, «Что нового», удаление.
Без настоящих сайтов: страница и сайт входа — вымышленные (как в test_lk_unit.py), данные — во временной папке."""
import os, sys, json, shutil, tempfile, threading, urllib.request, urllib.error, http.server, io
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_lk_api"
shutil.rmtree(BASE, ignore_errors=True); (BASE / "data").mkdir(parents=True); (BASE / "out").mkdir(parents=True)
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
(BASE / "data" / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}},
                                                         "out_dir": str(BASE / "out")}), encoding="utf-8")
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import gui, lk
import lk_pages as pg                  # те же вымышленные страницы, что в test_lk_unit.py
from http.server import ThreadingHTTPServer

APP = gui.APP = gui.App()
APP.settings.update({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(BASE / "out")})
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT, TOK = srv.server_address[1], APP.token

def call(path, body=None, token=TOK, raw=None):
    url = f"http://127.0.0.1:{PORT}{path}" + (("&" if "?" in path else "?") + "t=" + token)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

st, j = call("/api/lk")
check("пусто: ничего не сохранено", st == 200 and j["has"] is False and j["dir"] == "Успеваемость", j)
check("без ключа доступа — 403", call("/api/lk", token="wrong")[0] == 403 and call("/api/lk/import", raw=b"x", token="wrong")[0] == 403)
st, j = call("/api/lk/import", raw="<html>Вход</html>".encode("utf-8"))
check("не та страница → понятная ошибка", st == 200 and j["ok"] is False and "нет таблиц" in j["error"], j)
st, j = call("/api/lk/import", raw=b"")
check("пустой файл → ошибка", j["ok"] is False)
st, j = call("/api/lk/import", raw=pg.PAGE.encode("utf-8"))
check("сохранённая страница прочитана", j["ok"] and j["state"]["has"] and j["changes"] == 0, j)
state = j["state"]
check("в ответе данные, сводка и время", state["summary"][0]["stats"]["total"] == 10 and state["fetched"] and state["data"]["programs"][0]["semesters"], str(state)[:200])
check("в ответе нет имени студента и токена страницы", pg.NAME not in json.dumps(state, ensure_ascii=False) and pg.CSRF not in json.dumps(state))
check("файлы лежат в «Успеваемость» в папке материалов", sorted(p.name for p in (BASE / "out" / "Успеваемость").iterdir()) == ["index.html", "lk_grades.json"])
check("GET /api/lk отдаёт то же", call("/api/lk")[1]["has"] is True)

# новая оценка → событие «Что нового»
page2 = pg.page([("Первый семестр", pg.S1), ("Второй семестр", pg.S2b), ("Четвертый семестр", pg.S3)])
st, j = call("/api/lk/import", raw=page2.encode("utf-8"))
check("вторая страница: одна новая оценка", j["ok"] and j["changes"] == 1, j)
log = json.loads((BASE / "out" / ".moodle_news.json").read_text(encoding="utf-8")) if (BASE / "out" / ".moodle_news.json").exists() else {}
evs = log.get("events", log) if isinstance(log, dict) else log
check("«Что нового»: событие lk_grade, оценка скрыта от внешних каналов", any(e.get("kind") == "lk_grade" and e.get("secret") and e["title"] == "Право" for e in evs), str(evs)[:300])
st, j = call("/api/news")
check("лента «Что нового» отдаёт событие", st == 200 and any(e.get("kind") == "lk_grade" for e in j.get("events", [])), str(j)[:200])

# вход на макете сайта
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    page = pg.PAGE
    def log_message(self, *a): pass
    def _s(self, code, body="", h=None):
        b = body.encode(); self.send_response(code)
        for k, v in (h or {}).items(): self.send_header(k, v)
        self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path.startswith("/student/grade"):
            return self._s(200, H.page) if "lksess=ok" in (self.headers.get("Cookie") or "") else self._s(302, h={"Location": "/user/sign-in/login"})
        if self.path.startswith("/user/sign-in/login"):
            return self._s(200, '<form method="post"><input type="hidden" name="_csrf" value="a"><input name="LoginForm[username]"><input type="password" name="LoginForm[password]"></form>')
        self._s(404, "-")
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if "LoginForm%5Bpassword%5D=pw123" in body:
            return self._s(302, h={"Location": "/student/grade", "Set-Cookie": "lksess=ok; Path=/"})
        self._s(200, '<div class="alert-danger">Неверный логин или пароль</div><form method="post"><input name="LoginForm[username]"><input type="password" name="LoginForm[password]"></form>')
site = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=site.serve_forever, daemon=True).start()
lk.INSECURE_TEST = True
lk.GRADE_URL = f"http://127.0.0.1:{site.server_address[1]}/student/grade"

st, j = call("/api/lk/refresh", {"username": "student", "password": "wrong"})
check("неверный пароль → ошибка и просьба ввести пароль", j["ok"] is False and j["need_password"] is True and "вход не удался" in j["error"], j)
check("отладочный файл входа без пароля и оценок", (BASE / "data" / "lk_debug.txt").exists()
      and not any(w in (BASE / "data" / "lk_debug.txt").read_text(encoding="utf-8") for w in ("wrong", "pw123", "Отлично", pg.NAME)))
st, j = call("/api/lk/refresh", {"username": "student", "password": "pw123"})
check("вход по логину и паролю → страница обновлена", j["ok"] and j["state"]["has"], j)
# lk.fetch берёт адрес из lk.GRADE_URL только по умолчанию — проверим, что именно он использован
check("в сохранённых данных вымышленная страница", j["state"]["summary"][0]["stats"]["total"] == 10)
st, j = call("/api/lk/clear", {})
check("удаление: файлов нет, состояние пустое", j["ok"] and not j["state"]["has"] and not (BASE / "out" / "Успеваемость").exists(), j)
site.shutdown(); srv.shutdown()
print("\n" + ("ALL PASS" if not FAILS else f"FAILED: {len(FAILS)}\n  " + "\n  ".join(FAILS)))
sys.exit(1 if FAILS else 0)

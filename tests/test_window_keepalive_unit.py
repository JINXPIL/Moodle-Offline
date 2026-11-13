"""Окно и программа: когда программа считает, что окна больше нет, и что окно видит, если запрос упал.

Раньше сервер выходил, если окно 25 секунд не спрашивало состояние. Но свёрнутое или «уснувшее» окно браузера запросы
почти не шлёт — и программа закрывалась у живого окна («Программа закрыта — перезапустите её», проверка обновлений
не работала). Теперь выходим, когда окно само сказало «закрываюсь» (/api/bye) и не вернулось. Ещё: падение внутри
обработчика раньше обрывало соединение без ответа — окно считало программу закрытой; теперь оно получает ответ 500.
Без интернета: сайты не трогаются (проверка Moodle, обновлений и страны IP выключены)."""
import os, sys, json, shutil, tempfile, threading, time, urllib.request, urllib.error
from pathlib import Path
import _root

DATA = Path(tempfile.gettempdir()) / "mo_keepalive_data"
shutil.rmtree(DATA, ignore_errors=True); DATA.mkdir(parents=True)
os.environ["MOODLE_OFFLINE_DATA"] = str(DATA)
# ничего не ходит в сеть: страну IP, проверку Moodle и обновлений выключаем настройками до старта
(DATA / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}}), encoding="utf-8")

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import gui
from http.server import ThreadingHTTPServer


def mk_app():
    a = gui.App()
    a.settings["no_geo_check"] = True
    a.settings["update"] = {"auto": False}
    a.settings["watch"] = {"check": {"enabled": False}}
    return a


# ── 1. правило «окна больше нет» ──
a = mk_app()
t0 = a.started
check("окно ещё не отзывалось: 30 с — ждём", a.window_gone(t0 + 30) is False)
check("окно ещё не отзывалось: дольше минуты — окна нет (запуск без окна)", a.window_gone(t0 + gui.WINDOW_FIRST_WAIT + 1) is True)
a.note_poll(); lp = a.last_poll
check("окно было и молчит минуту (как свёрнутое) — оно живо", a.window_gone(lp + 60) is False)
check("…и 5 минут, и 3 часа (заморожено браузером) — тоже живо", a.window_gone(lp + 300) is False and a.window_gone(lp + 3 * 3600) is False)
check("…страховка: 12 часов тишины без «до свидания» — окна нет", a.window_gone(lp + gui.WINDOW_SILENCE_LIMIT + 1) is True)
a.bye_at = lp + 100
check("окно сказало «закрываюсь»: первые секунды ждём (F5 успевает вернуться)", a.window_gone(lp + 100 + gui.WINDOW_BYE_GRACE - 1) is False)
check("…не вернулось — окна нет", a.window_gone(lp + 100 + gui.WINDOW_BYE_GRACE + 1) is True)
a.note_poll()
check("окно вернулось и спросило состояние — «прощание» снято", a.bye_at == 0 and a.window_gone(a.last_poll + 60) is False)

# ── 2. сторож в работе (быстрые проходы): не выключает живое окно, выключает ушедшее ──
class FastEvent(threading.Event):
    def wait(self, timeout=None):
        return super().wait(0.05 if timeout == 5 else timeout)

def run_guard(app, secs):
    app.shutdown = FastEvent()
    th = threading.Thread(target=app.heartbeat_loop, daemon=True); th.start()
    th.join(secs)
    done = app.shutdown.is_set()
    app.shutdown.set(); th.join(2)
    return done

g = mk_app(); g.note_poll(); g.last_poll -= 600           # окно молчит 10 минут (свёрнуто)
check("сторож: окно молчит 10 минут — программа НЕ закрывается (раньше закрывалась через 25 с)", run_guard(g, 1.0) is False)
g = mk_app(); g.note_poll(); g.last_poll -= 600; g.bye_at = time.monotonic() - 60   # окно попрощалось минуту назад
check("сторож: окно попрощалось и не вернулось — программа закрывается", run_guard(g, 3.0) is True)
g = mk_app(); g.note_poll(); g.last_poll -= gui.WINDOW_SILENCE_LIMIT + 5; g._tick_at = time.monotonic() - 100   # «спали» 100 с между проходами
check("сторож: компьютер спал — окну даётся время очнуться, программа не закрывается", run_guard(g, 1.0) is False)
g = mk_app(); g.note_poll(); g.last_poll -= gui.WINDOW_SILENCE_LIMIT + 5
check("сторож: 12 часов тишины и не спали — закрывается (страховка)", run_guard(g, 3.0) is True)
g = mk_app(); g.note_poll(); g.bye_at = time.monotonic() - 60; g.job_state = "running"
check("сторож: окно попрощалось, но идёт загрузка — не закрываемся", run_guard(g, 1.0) is False)
g = mk_app(); g.note_poll(); g.bye_at = time.monotonic() - 60; g.keep_running = True
check("сторож: на телефоне (keep_running) окно не решает ничего", run_guard(g, 1.0) is False)

# ── 3. настоящий сервер: /api/bye, ответ 500 вместо оборванного соединения ──
APP = gui.APP = mk_app()
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]; TOK = APP.token

def call(path, body=None, token=TOK, header=False):
    url = f"http://127.0.0.1:{PORT}{path}" + ("" if header else (("&" if "?" in path else "?") + "t=" + token))
    req = urllib.request.Request(url, data=(json.dumps(body).encode() if body is not None else None), method="POST" if body is not None else "GET",
                                 headers={"X-Token": token} if header else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

st, j = call("/api/state?since=0")
check("состояние отвечает, окно отмечено живым", st == 200 and APP.polled is True and "can_restart" in j and j["can_restart"] is True)
st, j = call("/api/bye", {})
check("/api/bye с ключом в адресе (sendBeacon) принят, время «прощания» запомнено", st == 200 and APP.bye_at > 0)
call("/api/state?since=0")
check("следующий запрос состояния снимает «прощание»", APP.bye_at == 0)
st, _ = call("/api/bye", {}, token="wrong")
check("/api/bye без верного ключа — 403 и ничего не меняет", st == 403 and APP.bye_at == 0)
check("GET /api/bye не существует (404)", call("/api/bye")[0] == 404)

# падение внутри обработчика
def boom(*a, **k): raise RuntimeError("упало: https://moodle.example/x?sesskey=СЕКРЕТ123&id=5 password=hunter2")
real_snapshot = APP.rep.snapshot
APP.rep.snapshot = boom
st, j = call("/api/state?since=0")
check("падение в /api/state → ответ 500 с причиной, а не оборванное соединение", st == 500 and j.get("internal") is True and "RuntimeError" in j.get("error", ""), str((st, j)))
check("…в ответе нет ключей и паролей", "СЕКРЕТ123" not in json.dumps(j, ensure_ascii=False) and "hunter2" not in json.dumps(j, ensure_ascii=False))
log = (DATA / gui.ERROR_LOG).read_text(encoding="utf-8")
check("сбой записан в errors.log (где, тип)", "/api/state" in log and "RuntimeError" in log, log)
check("…без sesskey и пароля", "СЕКРЕТ123" not in log and "hunter2" not in log and "sesskey=…" in log, log)
n1 = log.count("\n"); call("/api/state?since=0")
check("одинаковый сбой подряд второй раз в файл не пишется", (DATA / gui.ERROR_LOG).read_text(encoding="utf-8").count("\n") == n1)
jl = [ln["msg"] for ln in APP.rep.lines if "Сбой внутри программы" in ln["msg"]]
check("сбой виден и в «Журнале» программы, без секретов", len(jl) == 1 and "RuntimeError" in jl[0] and "hunter2" not in jl[0], str(jl))
st, j = call("/api/ping")
check("после падения программа жива (ping отвечает)", st == 200 and j.get("ok") is True)
APP.rep.snapshot = real_snapshot
st, j = call("/api/state?since=0")
check("…и когда причина ушла, состояние снова отдаётся", st == 200 and "job" in j)

# обрыв со стороны окна — не сбой
import socket
s = socket.create_connection(("127.0.0.1", PORT)); s.sendall(f"GET /api/state?t={TOK} HTTP/1.1\r\nHost: x\r\n\r\n".encode()); s.close()
time.sleep(0.3)
check("окно оборвало соединение посреди ответа — это не сбой и в журнал не пишется", (DATA / gui.ERROR_LOG).read_text(encoding="utf-8").count("\n") == n1)

# ── 4. scrub / log_error ──
check("scrub прячет значения ключей", "abc" not in gui.scrub("a?token=abc&b=1 MoodleSession=abc cookie=abc") and "b=1" in gui.scrub("a?token=abc&b=1"))
big = DATA / gui.ERROR_LOG; big.write_text("x" * (gui.ERROR_LOG_MAX + 1000) + "\nстарая строка\n", encoding="utf-8")
gui._ERR_SEEN.clear(); gui.log_error("тест", ValueError("новая"), None)
check("errors.log не растёт бесконечно (усекается)", big.stat().st_size < gui.ERROR_LOG_MAX, str(big.stat().st_size))
check("…а свежая запись на месте", "ValueError: новая" in big.read_text(encoding="utf-8"))

# ── 5. кнопка «Перезапустить»: запуск новой копии с пометкой ──
launched = []
real_launch = gui.updater.launch_cmd
gui.updater.launch_cmd = lambda cmd, cwd=None, extra_env=None: launched.append((list(cmd), cwd, dict(extra_env or {})))
APP.shutdown = threading.Event()
r = APP.restart()
check("restart: новая копия запущена с пометкой MOODLE_OFFLINE_RESTART=1", r.get("ok") is True and launched and launched[0][2] == {"MOODLE_OFFLINE_RESTART": "1"}, str((r, launched)))
check("restart: запускается тот же файл (python + gui.py, либо сам exe)", launched[0][0][-1].endswith("gui.py") and launched[0][1])
check("restart: окно предупреждено (closing = restart)", APP.closing == "restart")
APP.shutdown.set()                                      # «новая копия пришла» — фоновый ожидатель уходит
st, j = call("/api/state?since=0")
check("/api/state отдаёт closing и зачем (closing_why)", j.get("closing") == "restart" and j.get("closing_why") == "restart")
APP.closing = APP.closing_why = ""
def bad_launch(cmd, cwd=None, extra_env=None): raise gui.updater.UpdateError("файл не найден")
gui.updater.launch_cmd = bad_launch
r = APP.restart()
check("restart: запустить не вышло → ошибка простыми словами, программа остаётся как была", r.get("ok") is False and "файл не найден" in r.get("error", "") and APP.closing == "")
gui.updater.launch_cmd = real_launch
gui.ANDROID = True
check("restart на телефоне — отказ с подсказкой", APP.restart().get("ok") is False and call("/api/restart", {})[1].get("ok") is False)
gui.ANDROID = False
st, j = call("/api/restart", {}, token="wrong")
check("/api/restart без ключа — 403", st == 403)

# новая копия не пришла за отведённое время: closing снимается, программа остаётся
gui.updater.launch_cmd = lambda *a, **k: None
APP.shutdown = threading.Event()
real_wait = APP.shutdown.wait
class QuickEv(threading.Event):
    def wait(self, timeout=None): return super().wait(0.3 if timeout == 40 else timeout)
APP.shutdown = QuickEv()
APP.restart(); time.sleep(0.8)
check("restart: новая копия не пришла → closing снимается, программа не закрывается", APP.closing == "" and APP.closing_why == "" and not APP.shutdown.is_set())
gui.updater.launch_cmd = real_launch

print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

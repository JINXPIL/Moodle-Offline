"""Элективы: «Сохранить на сайте» / «Записаться» не должны виснуть, когда iot.sevsu.ru не отвечает (1.32.1).
Раньше клиент сайта при обрыве ждал «оживания» до 15 минут по пять раз подряд, а окно просто крутилось: кнопки «Отмена»
не было, а в шапке при этом загоралось «Moodle недоступен» (отчётчик общий). Теперь: действие из окна — быстрый отказ
(`no_wait`, короткие таймауты), свой `Control`, который останавливает `POST /api/electives/cancel`, отдельный отчётчик
без индикатора Moodle, понятные тексты («запрос мог дойти до сайта» — только если запись уже уходила).
Настоящий сайт не нужен: клиент `iotapi.Iot` подменён фальшивым."""
import os, sys, json, shutil, tempfile, threading, time, types, urllib.request, urllib.error
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_el_cancel"
shutil.rmtree(BASE, ignore_errors=True); (BASE / "data").mkdir(parents=True); (BASE / "out").mkdir(parents=True)
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
(BASE / "data" / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}},
                                                         "out_dir": str(BASE / "out")}), encoding="utf-8")
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import gui
import moodle_offline as mo
import iotapi
from http.server import ThreadingHTTPServer

APP = gui.APP = gui.App()
APP.settings.update({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(BASE / "out")})
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT, TOK = srv.server_address[1], APP.token

def call(path, body=None, token=TOK, timeout=30):
    url = f"http://127.0.0.1:{PORT}{path}" + (("&" if "?" in path else "?") + "t=" + token)
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

NET = []
APP.set_net = lambda status, detail="": NET.append((status, detail))        # что бы ни случилось с iot, индикатор Moodle не трогаем

class FakeIot:
    mode = "ok"
    calls = 0
    def __init__(self, out, data, rep, control, creds=None, cookies=None, **kw):
        self.control, self.rep, self.token, self.token_until, self.cookies_out, self.sending = control, rep, None, 0.0, None, False
        self.c = types.SimpleNamespace(no_wait=False, timeout=None, control=control, rep=rep)
        FakeIot.last = self
    def load(self): return None
    def _act(self):
        FakeIot.calls += 1
        m = FakeIot.mode
        if m == "down_before": raise mo.NetworkDown("нет связи с iot.sevsu.ru")
        if m == "down_after": self.sending = True; raise mo.NetworkDown("нет связи с iot.sevsu.ru")
        if m == "hang": self.control.sleep(60)
        return {"ok": True, "message": "готово"}
    def save_choice(self, b, p, bk): return self._act()
    def register(self, e): return self._act()
    def retract(self, r): return self._act()
    def refresh(self):
        self._act(); return {"changes": []}

REAL = iotapi.Iot
BODY = {"block": 7, "primary": ["abc"], "backup": []}

# --- 1. настоящий клиент: что выставляет действие из окна
e = APP._iot(run=True)
check("1. действие из окна: клиент сайта не ждёт «оживания» (`no_wait`), один повтор, таймауты короткие", e.c.no_wait is True and e.c.net_retries == 1 and e.c.timeout == (gui.IOT_CONNECT_TIMEOUT, gui.IOT_READ_TIMEOUT) and gui.IOT_READ_TIMEOUT <= 60, (e.c.no_wait, e.c.timeout))
check("2. у действия свой Control — тот, что останавливает «Отмена»", e.c.control is APP.el_ctl and APP.el_ctl is not None)
e.c.rep.net_status("down", "iot лежит"); e.c.rep.state_hint("waiting_network"); e.c.rep.recovered(999)
check("3. сбой iot.sevsu.ru не переключает индикатор «Moodle недоступен» и не меняет состояние задания", NET == [] and APP.job_hint != "waiting_network", (NET, APP.job_hint))
e.c.rep.log("проверка отчётчика")
check("4. журнал при этом пишется (отчётчик отдаёт `log` настоящему)", hasattr(e.c.rep, "log") and isinstance(e.c.rep, gui.IotReporter))
APP.el_ctl = None
e2 = APP._iot()
check("5. чтение сохранённого (`load`) — без ожидания и без остановки: быстрый отказ только у действий", e2.c.no_wait is False)

# --- 1б. настоящий клиент сайта против закрытого порта: отказ быстрый, «Отмена» прерывает паузу между повторами
import socket
_s = socket.socket(); _s.bind(("127.0.0.1", 0)); DEAD = _s.getsockname()[1]; _s.close()          # порт, на котором никто не слушает
def dead_client(ctl):
    c = mo.MoodleClient(f"http://127.0.0.1:{DEAD}", control=ctl, reporter=gui.IotReporter(APP.rep), page_delay=(0, 0), file_delay=(0, 0), wait_minutes=15)
    c.no_wait, c.net_retries, c.timeout = True, 1, (2, 3)
    return c
t0 = time.monotonic()
try:
    dead_client(mo.Control()).request("GET", f"http://127.0.0.1:{DEAD}/api/electives")
    got = "ответ?!"
except Exception as ex:
    got = type(ex).__name__
dt = time.monotonic() - t0
check("1б. сайт не отвечает: настоящий клиент сдаётся за секунды (а не ждёт до 15 минут), `NetworkDown`", got == "NetworkDown" and dt < 20, (got, round(dt, 1)))
ctl = mo.Control(); out = {}
def _go():
    try:
        dead_client(ctl).request("GET", f"http://127.0.0.1:{DEAD}/api/electives"); out["r"] = "ответ?!"
    except BaseException as ex:
        out["r"] = type(ex).__name__
th = threading.Thread(target=_go, daemon=True); th.start(); time.sleep(1.0)
t0 = time.monotonic(); ctl.stop(); th.join(5)
check("1в. «Отмена» прерывает паузу между повторами сразу (`Stopped`), не дожидаясь её конца", not th.is_alive() and out.get("r") == "Stopped" and time.monotonic() - t0 < 2, (out, time.monotonic() - t0))

iotapi.Iot = FakeIot
gui.iotapi.Iot = FakeIot
try:
    # --- 2. сайт не отвечает
    FakeIot.mode = "down_before"
    st, j = call("/api/electives/choose", BODY)
    check("6. сайт не отвечает до отправки: ошибка без «зависания», `offline`, «ничего не изменилось»", st == 200 and j["ok"] is False and j.get("offline") and j.get("sent") is False and "Ничего не изменилось" in j["error"] and "iot.sevsu.ru" in j["error"], j)
    check("7. нет связи ≠ неверный пароль: окно не просит пароль", j.get("need_password") is False)
    FakeIot.mode = "down_after"
    st, j = call("/api/electives/register", {"event": "ev-1"})
    check("8. оборвалось уже ПОСЛЕ отправки записи: честно «запрос мог дойти до сайта» + что делать", j["ok"] is False and j.get("sent") is True and "мог дойти" in j["error"] and "Обновить" in j["error"], j)
    check("9. индикатор Moodle не дёргался и после этих сбоев", NET == [], NET)
    check("10. замок и состояние после сбоя свободны (следующее действие не зависнет)", APP.el_busy is False and APP.el_ctl is None and not APP.el_lock.locked())

    # --- 3. «Отмена» во время ожидания
    FakeIot.mode = "hang"; FakeIot.calls = 0
    res = {}
    t = threading.Thread(target=lambda: res.update(r=call("/api/electives/choose", BODY, timeout=40)), daemon=True); t.start()
    for _ in range(100):
        if APP.el_busy: break
        time.sleep(0.05)
    check("11. действие идёт (окно видит, что занято)", APP.el_busy is True and APP.el_ctl is not None)
    t0 = time.monotonic()
    st, c = call("/api/electives/cancel", {})
    t.join(10)
    dt = time.monotonic() - t0
    check("12. «Отмена» отвечает сразу и говорит, что что-то было запущено", st == 200 and c.get("ok") and c.get("was_busy") is True, c)
    st2, j = res.get("r", (0, {}))
    check("13. зависшее действие останавливается быстро (а не через 15 минут) и сообщает «отменено»", not t.is_alive() and dt < 5 and j.get("cancelled") is True and j["ok"] is False, (dt, j))
    check("14. после отмены всё освобождено", APP.el_busy is False and APP.el_ctl is None and not APP.el_lock.locked())

    # --- 4. отмена очереди: второе действие ждёт замок и не должно уйти на сайт
    FakeIot.calls = 0
    r1, r2 = {}, {}
    t1 = threading.Thread(target=lambda: r1.update(r=call("/api/electives/choose", BODY, timeout=40)), daemon=True); t1.start()
    for _ in range(100):
        if APP.el_busy: break
        time.sleep(0.05)
    t2 = threading.Thread(target=lambda: r2.update(r=call("/api/electives/register", {"event": 5}, timeout=40)), daemon=True); t2.start()
    time.sleep(0.4)
    call("/api/electives/cancel", {})
    t1.join(10); t2.join(10)
    check("15. отмена снимает и то действие, что стояло в очереди: на сайт ушло одно (первое) и оба вернули «отменено»",
          not t1.is_alive() and not t2.is_alive() and FakeIot.calls == 1 and r1["r"][1].get("cancelled") and r2["r"][1].get("cancelled"), (FakeIot.calls, r1, r2))

    # --- 5. после отмены всё работает
    FakeIot.mode = "ok"
    st, j = call("/api/electives/choose", BODY)
    check("16. после отмены следующее действие работает как обычно", j.get("ok") is True and j.get("message") == "готово", j)
    st, j = call("/api/electives/refresh", {})
    check("17. «Обновить» тоже идёт тем же путём и успешно", j.get("ok") is True, j)
    FakeIot.mode = "down_before"
    st, j = call("/api/electives/refresh", {})
    check("18. «Обновить», когда сайт лежит: понятный отказ без «зависания»", j["ok"] is False and j.get("offline") is True, j)
    FakeIot.mode = "hang"
    r3 = {}
    t3 = threading.Thread(target=lambda: r3.update(r=call("/api/electives/refresh", {}, timeout=40)), daemon=True); t3.start()
    for _ in range(100):
        if APP.el_busy: break
        time.sleep(0.05)
    call("/api/electives/cancel", {}); t3.join(10)
    check("19. «Обновить» тоже можно отменить", not t3.is_alive() and r3["r"][1].get("cancelled") is True, r3)
    st, c = call("/api/electives/cancel", {})
    check("20. «Отмена», когда ничего не идёт, безвредна (`was_busy` ложь)", st == 200 and c.get("ok") and c.get("was_busy") is False, c)
    check("21. без ключа доступа — 403", call("/api/electives/cancel", {}, token="wrong")[0] == 403)
finally:
    iotapi.Iot = REAL
    gui.iotapi.Iot = REAL

if FAILS:
    print("\nПровалено:", len(FAILS)); sys.exit(1)
print("\nВсё в порядке")

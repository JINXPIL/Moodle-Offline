"""Перезапуск программы по кнопке — двумя настоящими процессами (без окна браузера и без интернета).

Старая копия получает POST /api/restart, запускает новую с пометкой MOODLE_OFFLINE_RESTART=1, та просит старую закрыться,
занимает её место и «открывает окно» (метка в файле). Также: обычный повторный запуск той же версии окно открывает, а
работающую копию не трогает (как и раньше)."""
import os, sys, json, shutil, subprocess, tempfile, time, urllib.request
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_restart_e2e"
shutil.rmtree(BASE, ignore_errors=True)
DATA, MARK = BASE / "data", BASE / "marks"
DATA.mkdir(parents=True); MARK.mkdir()
(DATA / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}}), encoding="utf-8")
ENV = dict(os.environ, MOODLE_OFFLINE_DATA=str(DATA), RESTART_MARK_DIR=str(MARK), PYTHONIOENCODING="utf-8")
ENV.pop("MOODLE_OFFLINE_RESTART", None)
HELPER = str(_root.TESTS / "restart_helper.py")
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

def info():
    try:
        return json.loads((DATA / "gui.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

def call(d, path, body=None, timeout=5):
    req = urllib.request.Request(f"http://127.0.0.1:{d['port']}{path}{'&' if '?' in path else '?'}t={d['token']}", data=(json.dumps(body).encode() if body is not None else None),
                                 method="POST" if body is not None else "GET", headers={"X-Token": d["token"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def wait(fn, secs):
    end = time.time() + secs
    while time.time() < end:
        try:
            v = fn()
            if v: return v
        except Exception:
            pass
        time.sleep(0.2)
    return None

def alive(pid):
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False

old_p = subprocess.Popen([sys.executable, HELPER], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
new_pid = None
try:
    d0 = wait(lambda: (info() and call(info(), "/api/ping").get("ok") and info()), 25)
    check("старая копия запустилась, окно «открыто»", bool(d0) and (MARK / f"opened-{d0['pid']}").exists() if d0 else False)
    if not d0:
        raise SystemExit("старая копия не запустилась")
    check("её pid — наш процесс", d0["pid"] == old_p.pid)

    # обычный повторный запуск той же версии: только окно, старая работает
    again = subprocess.run([sys.executable, HELPER], env=ENV, capture_output=True, timeout=40)
    d1 = info()
    check("повторный запуск без перезапуска: код 0, работает прежняя копия, окно открыто", again.returncode == 0 and d1 and d1["pid"] == d0["pid"] and old_p.poll() is None)

    r = call(d0, "/api/restart", {})
    check("/api/restart принят", r.get("ok") is True, str(r))
    seen = set()
    def watch_old():
        try:
            seen.add(call(d0, "/api/state?since=0", timeout=2).get("closing") or "")
        except Exception:
            return True
    # пока старая жива, окно видит closing (restart, затем replaced, когда новая попросит закрыться)
    wait(lambda: (watch_old() or ("restart" in seen or "replaced" in seen)), 8)
    check("пока старая копия закрывается, окно видит closing (restart/replaced)", bool(seen & {"restart", "replaced"}), str(seen))
    d2 = wait(lambda: (lambda i: i and i["pid"] != d0["pid"] and call(i, "/api/ping").get("ok") and i)(info()), 60)
    check("новая копия заняла место: другой pid, отвечает", bool(d2))
    if d2:
        new_pid = d2["pid"]
        check("старая копия закрылась", wait(lambda: old_p.poll() is not None, 40) is not None)
        check("окно новой копии открыто (метка)", (MARK / f"opened-{new_pid}").exists())
        st = call(d2, "/api/state?since=0")
        check("у новой копии closing пуст и версия та же", st.get("closing") == "" and st.get("version"), str(st.get("closing")))
finally:
    for pid in (new_pid, old_p.pid):
        try:
            d = info()
            if d and d["pid"] == pid:
                call(d, "/api/quit", {}, timeout=3)
        except Exception:
            pass
    time.sleep(1.0)
    for pid in (new_pid, old_p.pid):
        if pid and alive(pid):
            try: os.kill(pid, 9)
            except OSError: pass
    try: old_p.wait(3)
    except Exception: pass

print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

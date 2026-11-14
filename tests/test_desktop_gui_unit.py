"""Linux и macOS в окне программы: автозапуск и ночная синхронизация через desktop.py, окно выбора папки, подписи по системе.
Настоящих systemd, launchd и osascript нет: команды подменены, файлы пишутся во временную «домашнюю» папку."""
import json, os, shutil, subprocess, sys, tempfile, threading, urllib.request, urllib.error
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_desktop_gui"
shutil.rmtree(BASE, ignore_errors=True); (BASE / "data").mkdir(parents=True); (BASE / "home").mkdir()
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
os.environ["HOME"] = str(BASE / "home")
os.environ.pop("XDG_CONFIG_HOME", None)
(BASE / "data" / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(BASE / "out")}), encoding="utf-8")

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import desktop, gui
import moodle_offline as mo
from http.server import ThreadingHTTPServer
APP = gui.APP = gui.App()
APP.settings.update({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(BASE / "out")})
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT, TOK = srv.server_address[1], APP.token

def call(path, body=None):
    url = f"http://127.0.0.1:{PORT}{path}" + ("&" if "?" in path else "?") + "t=" + TOK
    req = urllib.request.Request(url, data=(json.dumps(body).encode() if body is not None else None), method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"null")

# ── состояние окна ──
st = call("/api/state")[1]
check("окну отдана система (а не только os.name)", st["system"] in ("linux", "macos", "windows") and st["system"] == desktop.system() and st["platform"] == os.name)

# ── что запускается из фона ──
cmd = APP._bg_command("--watch", "-y")
check("из исходников: Python + moodle_offline.py + ключи", cmd[0] == sys.executable and cmd[1].endswith("moodle_offline.py") and cmd[2:] == ["--watch", "-y"], str(cmd))
mo.FROZEN = True
cmd = APP._bg_command("--sync", "-y")
check("из собранного файла: он сам и ключи (gui.py пропускает их консольной версии)", cmd == [sys.executable, "--sync", "-y"], str(cmd))
mo.FROZEN = False

# ── автозапуск (системный вызов не нужен: пишется файл в «домашней» папке) ──
if os.name != "nt" and sys.platform != "darwin":
    info = APP.autostart_info()
    p = Path(info["path"])
    check("Linux: автозапуск поддержан, выключен, файл в ~/.config/autostart", info["supported"] and info["kind"] == "xdg" and not info["on"] and p == BASE / "home/.config/autostart/moodle-offline-watch.desktop", str(info))
    r = call("/api/watch/autostart", {"enabled": True})[1]
    check("включили: файл создан, Exec с ключами", r["ok"] and r["on"] and p.is_file() and "moodle_offline.py" in p.read_text(encoding="utf-8") and "--watch -y" in p.read_text(encoding="utf-8"), str(r))
    r = call("/api/watch/autostart", {"enabled": False})[1]
    check("выключили: файла нет, окно видит «выключено»", r["ok"] and not r["on"] and not p.exists())

# ── ночная синхронизация: подменяем systemctl/launchctl ──
runs = []
real_run = subprocess.run
def fake_run(cmd, **k):
    runs.append(cmd); return subprocess.CompletedProcess(cmd, 0, "", "")
subprocess.run = fake_run
real_which = desktop.shutil.which
desktop.shutil.which = lambda n: "/usr/bin/" + n if n == "systemctl" else real_which(n)
try:
    if os.name != "nt":
        r = call("/api/schedule", {"enabled": True, "time": "04:15"})[1]
        sv, tm = desktop.sync_paths(None, BASE / "home", {})
        if sys.platform == "darwin":
            ok_files = sv.is_file()
        else:
            ok_files = sv.is_file() and tm.is_file() and "OnCalendar=*-*-* 04:15:00" in tm.read_text(encoding="utf-8")
        check("включили расписание: файлы системы записаны", r.get("ok") and ok_files, str(r))
        check("настройки запомнили время и «включено»", APP.settings.get("schedule_on") is True and APP.settings.get("schedule_time") == "04:15")
        check("системе ушли команды списком (без shell)", runs and all(isinstance(c, list) for c in runs), str(runs[:3]))
        r = call("/api/schedule", {"enabled": True, "time": "99:99"})[1]
        check("неверное время — ошибка, настройки не испорчены", r["ok"] is False and APP.settings.get("schedule_time") == "04:15", str(r))
        r = call("/api/schedule", {"enabled": False, "time": "04:15"})[1]
        check("выключили: файлы убраны, «включено» снято", r["ok"] and not sv.exists() and APP.settings.get("schedule_on") is False)
        # путь к собранному файлу сменился → задача пересоздаётся с тем же временем
        mo.FROZEN = True
        APP.settings.update(schedule_on=True, schedule_time="05:30", schedule_exe="/старое/место/MoodleOffline")
        APP.repair_schedule()
        tt = tm.read_text(encoding="utf-8") if tm.exists() else ""
        check("путь программы сменился — задача пересоздана на новый путь и то же время",
              (APP.settings["schedule_exe"] == sys.executable) and ("05:30" in tt or sys.platform == "darwin") and sys.executable in sv.read_text(encoding="utf-8", errors="ignore") if sv.exists() and sys.platform != "darwin" else APP.settings["schedule_exe"] == sys.executable)
        mo.FROZEN = False
finally:
    subprocess.run = real_run
    desktop.shutil.which = real_which

# ── окно выбора папки ──
real_pick = desktop.pick_folder
real_tk = gui._tk_pick_folder
gui._tk_pick_folder = lambda initial, title: None
desktop.pick_folder = lambda title, initial, *a, **k: None
if os.name != "nt":
    r = call("/api/pick_folder", {})[1]
    check("нет окна выбора → понятная подсказка, а не молчание", r["ok"] is False and r.get("error") and "вручную" in r["error"], str(r))
    check("подсказка про zenity — только в Linux", ("zenity" in r["error"]) == (desktop.system() == "linux"))
    r = call("/api/data/import", {})[1]
    check("«Перенос данных» без окна выбора: тоже подсказка, не «отмена»", r["ok"] is False and not r.get("cancelled") and r.get("error"))
    desktop.pick_folder = lambda title, initial, *a, **k: ""
    r = call("/api/pick_folder", {})[1]
    check("«Отмена» — тихо, без ошибки", r["ok"] is False and "error" not in r)
    desktop.pick_folder = lambda title, initial, *a, **k: str(BASE / "out2")
    r = call("/api/pick_folder", {})[1]
    check("выбрали — путь сохранён в настройках", r["ok"] and r["path"] == str(BASE / "out2") and APP.settings["out_dir"] == str(BASE / "out2"))
desktop.pick_folder = real_pick
gui._tk_pick_folder = real_tk

# macOS не должен падать в tkinter, когда osascript нет
real_sys = desktop.system
desktop.system = lambda platform=None: "macos"
desktop.pick_folder = lambda title, initial, *a, **k: None
called = []
gui._tk_pick_folder = lambda initial, title: called.append(1) or "/x"
if os.name != "nt":
    check("macOS: запасного tkinter нет (Tk падает не в главном потоке)", gui.pick_folder("/") is None and not called)
desktop.system = real_sys; desktop.pick_folder = real_pick; gui._tk_pick_folder = real_tk

print("FAILS:", FAILS); srv.shutdown(); sys.exit(1 if FAILS else 0)

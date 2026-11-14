"""Перенос данных между версиями exe: выбор папки, поиск старых данных, копирование, защита."""
import json, os, sys, tempfile, time, types
from pathlib import Path
import _root
import datadir as dd

FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: FAILS.append(name)

def mk_legacy(app: Path, *, out_dir=None, with_out=True, secret=b"PLAIN1{}", stamp=None):
    d = app / ".moodle_offline"; d.mkdir(parents=True, exist_ok=True)
    s = {"username": "u", "remember": True}
    if out_dir: s["out_dir"] = out_dir
    (d / "settings.json").write_text(json.dumps(s), encoding="utf-8")
    (d / "session.bin").write_bytes(secret)
    (d / "offline.json").write_text('{"courses": []}', encoding="utf-8")
    (d / "gui.json").write_text('{"port": 1}', encoding="utf-8")
    (d / "login_page_1.html").write_text("<html/>", encoding="utf-8")
    (d / "x.tmp").write_text("t", encoding="utf-8")
    (d / "window-profile").mkdir(exist_ok=True); (d / "window-profile" / "big").write_text("x")
    (d / "covers").mkdir(exist_ok=True); (d / "covers" / "1.jpg").write_bytes(b"jpg")
    (d / "structure").mkdir(exist_ok=True); (d / "structure" / "3594.json").write_text("{}")
    if with_out:
        o = app / "Материалы Moodle"; o.mkdir(exist_ok=True); (o / ".moodle_state.json").write_text("{}")
    if stamp:
        for n in dd.HAS_DATA: os.utime(d / n, (stamp, stamp))
    return d

tmp = Path(tempfile.mkdtemp(prefix="dd_"))

# ── 1. план ──
home = tmp / "home"; (home / "Documents").mkdir(parents=True)
app = tmp / "app"; app.mkdir()
p = dd.make_plan(app, False, env={}, home=home)
check("исходники: рядом с кодом", p.mode == "dev" and p.data_dir == app / ".moodle_offline" and p.default_out == app / "Материалы Moodle")
p = dd.make_plan(app, True, env={"LOCALAPPDATA": str(tmp / "LA")}, home=home, os_name="nt")
check("exe на Windows: LOCALAPPDATA", p.mode == "user" and p.data_dir == tmp / "LA" / "MoodleOffline", str(p))
check("материалы по умолчанию в Documents", p.default_out == home / "Documents" / "Материалы Moodle")
p = dd.make_plan(app, True, env={}, home=home, os_name="nt")
check("нет LOCALAPPDATA → ~/AppData/Local", p.data_dir == home / "AppData" / "Local" / "MoodleOffline")
p = dd.make_plan(app, True, env={}, home=home, os_name="posix")
check("Linux: ~/.local/share", p.data_dir == home / ".local" / "share" / "moodle-offline")
p = dd.make_plan(app, True, env={"XDG_DATA_HOME": str(tmp / "xdg")}, home=home, os_name="posix")
check("Linux: XDG_DATA_HOME", p.data_dir == tmp / "xdg" / "moodle-offline")
p = dd.make_plan(app, True, env={"XDG_DATA_HOME": str(tmp / "xdg")}, home=home, os_name="darwin")
check("macOS: ~/Library/Application Support/MoodleOffline (XDG там не нужен)", p.mode == "user" and p.data_dir == home / "Library" / "Application Support" / "MoodleOffline", str(p))
check("macOS: материалы по умолчанию в Documents", p.default_out == home / "Documents" / "Материалы Moodle")
p = dd.make_plan(app, False, env={}, home=home, os_name="darwin")
check("macOS из исходников: рядом с кодом, как везде", p.mode == "dev" and p.data_dir == app / ".moodle_offline")
(app / "portable.txt").write_text("")
p = dd.make_plan(app, True, env={}, home=home)
check("portable.txt → рядом с exe", p.mode == "portable" and p.data_dir == app / ".moodle_offline" and p.default_out == app / "Материалы Moodle")
(app / "portable.txt").unlink()
p = dd.make_plan(app, True, env={dd.ENV_DATA: str(tmp / "mine")}, home=home)
check("своя папка из переменной", p.mode == "custom" and p.data_dir == tmp / "mine" and p.default_out == tmp / "mine" / "Материалы Moodle")
home2 = tmp / "home_nodocs"; home2.mkdir()
check("нет Documents → домашняя", dd.default_materials(home2) == home2 / "Материалы Moodle")

# ── 2. новый exe на месте старого (данные рядом) ──
def fresh(name):
    base = tmp / name; base.mkdir(); h = base / "home"; (h / "Downloads").mkdir(parents=True); (h / "Documents").mkdir()
    return base, h
base, h = fresh("t2")
app = base / "app"; app.mkdir()
old = mk_legacy(app)
plan = dd.Plan("user", app, base / "userdata", h / "Documents" / "Материалы Moodle")
before = {str(f.relative_to(old)): f.read_bytes() for f in old.rglob("*") if f.is_file()}
info = dd.migrate_if_needed(plan, home=h, env={})
check("перенос найден рядом с exe", info is not None and info["from"] == str(old), str(info))
dst = plan.data_dir
check("вход и настройки скопированы", (dst / "session.bin").read_bytes() == b"PLAIN1{}" and (dst / "settings.json").exists() and (dst / "offline.json").exists())
check("кэш скопирован", (dst / "covers" / "1.jpg").read_bytes() == b"jpg" and (dst / "structure" / "3594.json").exists())
check("временное и чужое не скопировано", not (dst / "gui.json").exists() and not (dst / "login_page_1.html").exists()
      and not (dst / "x.tmp").exists() and not (dst / "window-profile").exists())
st = json.loads((dst / "settings.json").read_text(encoding="utf-8"))
check("материалы остались на старом месте (out_dir записан)", st.get("out_dir") == str(app / "Материалы Moodle") and info["out_dir"] == st["out_dir"], str(st))
after = {str(f.relative_to(old)): f.read_bytes() for f in old.rglob("*") if f.is_file()}
check("старая папка не изменилась", before == after)
check("маркер записан, не показан", dd.pending_notice(dst) and dd.pending_notice(dst)["from"] == str(old))
dd.mark_seen(dst)
check("после показа уведомления нет", dd.pending_notice(dst) is None)
check("второй запуск ничего не делает", dd.migrate_if_needed(plan, home=h, env={}) is None)

# ── 3. рядом лежит папка старой версии ──
base, h = fresh("t3")
(base / "new").mkdir(); (base / "MoodleOffline_1.4").mkdir()
old = mk_legacy(base / "MoodleOffline_1.4", out_dir=str(base / "D" / "Moo"))
plan = dd.Plan("user", base / "new", base / "ud", h / "Documents" / "M")
info = dd.migrate_if_needed(plan, home=h, env={})
check("найдено в соседней папке", info and info["from"] == str(old))
st = json.loads((plan.data_dir / "settings.json").read_text(encoding="utf-8"))
check("свой путь материалов не трогаем", st["out_dir"] == str(base / "D" / "Moo") and info["out_dir"] == "")

# ── 4. «Загрузки», две версии → берём свежую ──
base, h = fresh("t4")
a1 = h / "Downloads" / "v1.3" / "MoodleOffline"; a2 = h / "Downloads" / "v1.5"
a1.mkdir(parents=True); a2.mkdir(parents=True)
now = time.time()
o1 = mk_legacy(a1, stamp=now - 86400 * 40); o2 = mk_legacy(a2, stamp=now - 86400)
(app4 := base / "app").mkdir()
plan = dd.Plan("user", app4, base / "ud", h / "Documents" / "M")
info = dd.migrate_if_needed(plan, home=h, env={})
check("в Загрузках берётся самая свежая", info and info["from"] == str(o2) and info["others"] == [str(o1)], str(info))

# ── 5. не лезем в папку с материалами и не глубже лимита ──
base, h = fresh("t5")
mats = h / "Documents" / "Материалы"; (mats / "Курс" / "Раздел").mkdir(parents=True)
(mats / ".moodle_state.json").write_text("{}")
mk_legacy(mats / "Курс" / "Раздел", with_out=False)
deep = h / "Downloads" / "a" / "b" / "c" / "d"; deep.mkdir(parents=True); mk_legacy(deep, with_out=False)
(app5 := base / "app").mkdir()
check("внутри папки с материалами и слишком глубоко не ищем", dd.find_legacy(app5, h, env={}) == [])
loop = h / "Downloads" / "loop"
try:
    os.symlink(h / "Downloads", loop)
    t0 = time.time(); r = dd.find_legacy(app5, h, env={})
    check("петля из ссылок не зацикливает поиск", time.time() - t0 < 5 and r == [])
except OSError:
    check("петля из ссылок (пропуск: нет symlink)", True)
old_lim = dd.SCAN_LIMIT; dd.SCAN_LIMIT = 3
for i in range(20): (h / "Downloads" / f"d{i}").mkdir()
check("лимит просмотренных папок соблюдается", dd.find_legacy(app5, h, env={}) == [])
dd.SCAN_LIMIT = old_lim

# ── 6. защиты ──
base, h = fresh("t6")
app6 = base / "app"; app6.mkdir(); mk_legacy(app6)
ud = base / "ud"; ud.mkdir(); (ud / "settings.json").write_text('{"username":"new"}')
plan = dd.Plan("user", app6, ud, h / "M")
check("уже есть данные → не переносим", dd.migrate_if_needed(plan, home=h, env={}) is None
      and json.loads((ud / "settings.json").read_text())["username"] == "new")
for mode in ("portable", "dev", "custom"):
    check(f"режим {mode}: перенос не нужен", dd.migrate_if_needed(dd.Plan(mode, app6, base / "zz", h / "M"), home=h, env={}) is None)
base, h = fresh("t6b")
plan = dd.Plan("user", base / "empty_app", base / "ud2", h / "M"); (base / "empty_app").mkdir()
check("старых данных нет → None", dd.migrate_if_needed(plan, home=h, env={}) is None)
check("нет данных: маркера и папки нет", not (base / "ud2").exists())

# ── 7. ручной импорт ──
base, h = fresh("t7")
app7 = base / "oldexe"; app7.mkdir(); mk_legacy(app7, out_dir="")
(app7 / ".moodle_offline" / "settings.json").write_text(json.dumps({"username": "OLD", "mode": 2}))
ud = base / "ud"; ud.mkdir(); (ud / "settings.json").write_text('{"username":"new"}'); (ud / "session.bin").write_bytes(b"PLAIN1{\"x\":1}")
plan = dd.Plan("user", base / "newexe", ud, h / "M")
info = dd.import_from(app7, plan, overwrite=True)           # указали папку со старым exe
check("импорт из папки с exe перезаписывает", json.loads((ud / "settings.json").read_text())["username"] == "OLD" and info["files"] > 0)
check("после импорта материалы на старом месте", json.loads((ud / "settings.json").read_text()).get("out_dir") == str(app7 / "Материалы Moodle"))
try: dd.import_from(base / "nope", plan); ok = False
except ValueError as e: ok = "нет данных" in str(e)
check("импорт из пустой папки — понятная ошибка", ok)
try: dd.import_from(ud, plan); ok = False
except ValueError as e: ok = "текущая" in str(e)
check("импорт из самой себя запрещён", ok)
info = dd.import_from(app7 / ".moodle_offline", plan, overwrite=False)
check("импорт без перезаписи не затирает существующее", info["files"] == 0 or json.loads((ud / "session.bin").read_bytes()[6:] or b"{}") is not None)

# ── 8. mo.prepare_data_dir ──
import moodle_offline as mo
base, h = fresh("t8")
app8 = base / "app"; app8.mkdir(); mk_legacy(app8)
mo.PLAN = dd.Plan("user", app8, base / "ud8", h / "Documents" / "M"); mo.DATA_DIR = mo.PLAN.data_dir
real_home = dd.Path.home
dd.Path.home = classmethod(lambda cls: h)
try:
    info = mo.prepare_data_dir()
finally:
    dd.Path.home = real_home
check("mo.prepare_data_dir переносит", info and (base / "ud8" / "session.bin").exists())
mo.DATA_DIR = base / "other"
check("подменённая папка данных (тесты) не трогается", mo.prepare_data_dir() is None)
mo.DATA_DIR = mo.PLAN.data_dir
v = mo.Vault(); s = v.load_settings()
check("Vault читает перенесённые настройки", s.get("username") == "u", str(s))

# ── 9. CLI ──
import contextlib, io
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = mo.cli_main(["--data-info"])
check("--data-info", rc == 0 and str(mo.DATA_DIR) in buf.getvalue() and "user" in buf.getvalue(), buf.getvalue())
base, h = fresh("t9"); app9 = base / "o"; app9.mkdir(); mk_legacy(app9)
mo.PLAN = dd.Plan("user", base / "n", base / "ud9", h / "M"); mo.DATA_DIR = mo.PLAN.data_dir
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = mo.cli_main(["--import-data", str(app9)])
check("--import-data", rc == 0 and (base / "ud9" / "session.bin").exists() and "Перенесено" in buf.getvalue(), buf.getvalue())
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = mo.cli_main(["--import-data", str(base / "none")])
check("--import-data: ошибка → код 2", rc == 2 and "Ошибка" in buf.getvalue())

# ── 10. GUI: данные и ремонт задачи планировщика ──
import gui
a = gui.App.__new__(gui.App)
a.settings = {"schedule_on": True, "schedule_time": "04:20", "schedule_exe": "C:\\old\\MoodleOffline.exe"}
logs = []; a.rep = types.SimpleNamespace(log=lambda m, l="info": logs.append((l, m)))
calls = []; a.schedule = lambda en, hhmm: (calls.append((en, hhmm)), {"ok": True})[1]
class FakeOS:
    name = "nt"
    def __getattr__(self, k): return getattr(os, k)
real_os, real_frozen, real_exe = gui.os, mo.FROZEN, sys.executable
gui.os = FakeOS(); mo.FROZEN = True; sys.executable = "C:\\new\\MoodleOffline.exe"
try:
    a.repair_schedule()
    check("путь exe сменился → задача пересоздана с тем же временем", calls == [(True, "04:20")] and logs and logs[0][0] == "info", str(calls))
    calls.clear(); a.settings["schedule_exe"] = sys.executable; a.repair_schedule()
    check("путь тот же → ничего не делаем", calls == [])
    a.settings = {"schedule_on": False, "schedule_exe": "x"}; a.repair_schedule()
    check("ночная синхронизация выключена → ничего не делаем", calls == [])
    a.settings = {"schedule_on": True, "schedule_time": "03:10"}; a.repair_schedule()
    check("после переноса (нет schedule_exe) задача пересоздаётся", calls == [(True, "03:10")])
finally:
    gui.os, mo.FROZEN, sys.executable = real_os, real_frozen, real_exe

a2 = gui.App.__new__(gui.App)
a2.settings = {}; mo.DATA_DIR = base / "ud9"
d = a2.data_info()
check("data_info: путь, режим, уведомление", d["dir"] == str(base / "ud9") and d["mode"] == "user" and d["notice"]["files"] > 0, str(d))

print("\nALL PASS" if not FAILS else f"\nFAILED: {FAILS}")
sys.exit(1 if FAILS else 0)

"""Курсы из папки сохранения → в память программы (после «Переноса данных» окно курса не видело скачанное)."""
import sys, os, json, shutil, time
import _root
from pathlib import Path
import moodle_offline as mo
FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond: FAILS.append(name)

BASE = Path("/tmp/adopt_t"); shutil.rmtree(BASE, ignore_errors=True)
out = BASE / "out"; out.mkdir(parents=True)
def cj(folder, cid, name, nfiles=2, upd="10.11.2026 09:00", raw=None):
    d = out / folder; d.mkdir(parents=True, exist_ok=True)
    (d / ".course.json").write_text(raw if raw is not None else json.dumps({"id": cid, "fullname": name, "dir": folder, "mode": 1, "updated": upd,
        "sections": [{"number": 1, "name": "Р1", "items": [{"key": "k", "name": "i", "files": [{"title": f"f{i}", "path": f"f{i}.pdf", "size": 1} for i in range(nfiles)]}]}]}, ensure_ascii=False), encoding="utf-8")

# 1. пустая папка / нет курсов
check("нет папки — пусто, без ошибок", mo.adopt_courses(BASE / "нет") == [])
check("пустая папка — пусто, состояние не создаётся", mo.adopt_courses(out) == [] and not (out / mo.STATE_FILE).exists())

# 2. обычный случай
cj("Матан", 101, "Высшая математика", 3)
cj("Физика", 102, "Физика", 1, upd="")
for service in ("Расписание", "Календарь", "Элективы", "_бэкапы", ".скрытая", "Без структуры"):
    (out / service).mkdir(exist_ok=True)
    (out / service / "a.txt").write_text("x")
(out / ".скрытая" / ".course.json").write_text(json.dumps({"id": 999, "sections": []}))
(out / "_бэкапы" / ".course.json").write_text(json.dumps({"id": 998, "sections": []}))
cj("Битый", 103, "x", raw="{не json")
cj("БезId", 0, "x", raw=json.dumps({"fullname": "без id", "sections": []}))
added = mo.adopt_courses(out)
check("добавлены два нормальных курса", sorted(added) == ["Высшая математика", "Физика"], added)
st = json.load(open(out / mo.STATE_FILE, encoding="utf-8"))
c = st["courses"]
check("в состоянии ровно два курса (служебные, скрытые и битые пропущены)", sorted(c) == ["101", "102"], list(c))
check("запись курса: имя, папка, число файлов, дата, режим", c["101"] == {"name": "Высшая математика", "dir": "Матан", "files": 3, "updated": "10.11.2026 09:00", "mode": 1}, c["101"])
check("нет даты в .course.json — берётся время файла", len(c["102"]["updated"]) == 16 and c["102"]["updated"][2] == ".", c["102"])
check("остальные разделы состояния на месте", all(k in st for k in ("files", "items", "news", "version")))
check("повторный вызов ничего не добавляет", mo.adopt_courses(out) == [])

# 3. чужие записи не трогаем
st["courses"]["101"]["files"] = 77; st["files"] = {"k": {"size": 5, "paths": ["Матан/f0.pdf"]}}
json.dump(st, open(out / mo.STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
cj("Химия", 104, "Химия", 4)
cj("Матан копия", 101, "Высшая математика (копия)", 9)       # тот же id в другой папке — первая запись главнее
added = mo.adopt_courses(out)
st2 = json.load(open(out / mo.STATE_FILE, encoding="utf-8"))
check("новый курс добавлен, курс с тем же id в другой папке — нет", added == ["Химия"], added)
check("существующая запись не перезаписана", st2["courses"]["101"]["files"] == 77 and st2["courses"]["101"]["dir"] == "Матан")
check("записи файлов (докачка) не тронуты", st2["files"] == {"k": {"size": 5, "paths": ["Матан/f0.pdf"]}})

# 4. запись про курс уже есть по папке (другой id в файле) — не дублируем
st2["courses"]["555"] = {"name": "Старое имя", "dir": "Химия", "files": 1, "updated": "", "mode": 1}
del st2["courses"]["104"]
json.dump(st2, open(out / mo.STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
check("папка уже записана под другим id — не дублируем", mo.adopt_courses(out) == [])

# 5. окно (App): курс после переноса виден как скачанный; во время загрузки не вмешиваемся
ph_data = BASE / "ph_data"; ph_data.mkdir()
shutil.rmtree(out); out.mkdir(); cj("Матан", 101, "Высшая математика", 3)
(ph_data / "structure").mkdir()
json.dump({"id": 101, "fullname": "Высшая математика", "dir": "", "sections": [{"number": 1, "name": "Р1", "items": [{"key": "k", "name": "i", "files": []}]}]}, open(ph_data / "structure" / "101.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"out_dir": str(out)}, open(ph_data / "settings.json", "w"))
mo.DATA_DIR = ph_data
import gui
app = gui.App()
app.job_state = "running"
check("во время загрузки App ничего не добавляет", app.adopt_courses() == [] and not (out / mo.STATE_FILE).exists())
d = app.course_structure(101)
check("загрузка идёт — курс пока «не скачан» (как и было)", d.get("downloaded") is False)
app.job_state = "idle"; app._adopt_at = -1e9
d = app.course_structure(101)
check("после переноса окно курса видит курс скачанным и файлы", d.get("downloaded") is True and len(d["sections"][0]["items"][0]["files"]) == 3, d.get("downloaded"))
check("не чаще раза в полминуты", app.adopt_courses() == [])
cj("Новый", 105, "Новый курс", 1)
check("force обходит ограничение по времени", app.adopt_courses(force=True) == ["Новый курс"])
res = app._tr_finish({"settings": None, "copied": 0, "total": 0, "bytes": 0, "failed": 0, "from": "ПК", "notes": [], "errors": []}, ["materials"], 1.0)
check("итог переноса: «всё на месте» без новых курсов", res.startswith("Всё уже на месте") and "скачанные" not in res, res)
cj("Ещё", 106, "Ещё курс", 1)
res = app._tr_finish({"settings": None, "copied": 0, "total": 0, "bytes": 0, "failed": 0, "from": "ПК", "notes": [], "errors": []}, ["materials"], 1.0)
check("итог переноса: файлы уже на месте, курс появился в «Курсы»", "Всё уже на месте" in res and "курсов появилось в разделе «Курсы» как скачанные: 1" in res, res)
res = app._tr_finish({"settings": None, "copied": 5, "total": 5, "bytes": 100, "failed": 0, "from": "ПК", "notes": [], "errors": []}, ["schedule"], 1.0)
check("без «Материалов» курсы не трогаем", "скачанные" not in res, res)
print("\nИТОГО:", "всё хорошо" if not FAILS else f"ОШИБОК: {len(FAILS)}")
sys.exit(1 if FAILS else 0)

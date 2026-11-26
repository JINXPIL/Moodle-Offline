"""Выбор папки внутри программы (1.35.1): folders.py и запросы окна /api/folders*. Всё во временной папке; общая память и
доступ ко всем файлам подменяются, настоящие папки телефона и компьютера не трогаем."""
import os, sys, json, shutil, tempfile, threading, urllib.request, urllib.error
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_folders_unit"
shutil.rmtree(BASE, ignore_errors=True)
HOME = BASE / "home"; SHARED = BASE / "shared"; SD = BASE / "storage" / "AAAA-1111"; OUTSIDE = BASE / "outside"
for d in (HOME / "data", SHARED / "Documents" / "Moodle", SHARED / "Download", SHARED / "Pictures", SHARED / ".hidden", SD / "Books",
          BASE / "storage" / "emulated", BASE / "storage" / "self", OUTSIDE / "secret", BASE / "data"):
    d.mkdir(parents=True)
(SHARED / "Documents" / "file.txt").write_text("не папка")
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
os.environ["HOME"] = str(HOME); os.environ["USERPROFILE"] = str(HOME)
(BASE / "data" / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}},
                                                         "out_dir": str(SHARED / "Documents" / "Moodle")}), encoding="utf-8")
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import folders

# --- имя новой папки
def bad(name):
    try: folders.clean_name(name); return False
    except ValueError: return True
check("1. имя: пустое, точки, пути и перевод строки не принимаются", all(bad(n) for n in ("", "  ", ".", "..", "a/b", "..\\x", "../x", "a\nb", None)))
check("2. имя: обычное и с пробелами/кириллицей принимается, края срезаются", folders.clean_name("  Учёба 2026 ") == "Учёба 2026" and folders.clean_name("a.b") == "a.b")
check("3. имя длиннее 100 знаков не принимается", bad("я" * 101) and not bad("я" * 100))

# --- корни
rs = folders.roots(SHARED, HOME, storage=str(BASE / "storage"))
names = [r["name"] for r in rs]
check("4. корни: память, документы, загрузки, карта, память приложения — по порядку",
      names == ["Память телефона", "Документы", "Загрузки", "Карта памяти", "Память приложения"], names)
check("5. корни: `emulated` и `self` не показываются как карты", not any("emulated" in r["path"] or r["path"].endswith("self") for r in rs))
check("6. корни без общей памяти: только то, что есть", [r["name"] for r in folders.roots(None, HOME, storage=str(BASE / "nope"))] == ["Память приложения"])
check("7. корни не повторяются", len({r["path"] for r in rs}) == len(rs))

# --- обзор папки
j = folders.browse(str(SHARED), rs)
check("8. корень: подпапки по алфавиту, скрытые и файлы не показываются, «наверх» нет",
      j["ok"] and [d["name"] for d in j["dirs"]] == ["Documents", "Download", "Pictures"] and j["parent"] is None, j)
check("9. корень: путь-цепочка из одного звена", [t["name"] for t in j["trail"]] == ["Память телефона"])
j = folders.browse(str(SHARED / "Documents"), rs)
check("10. «Документы» — отдельный корень: наверх нельзя, подпапка Moodle видна, файла нет", j["parent"] is None and [d["name"] for d in j["dirs"]] == ["Moodle"], j)
j = folders.browse(str(SHARED / "Documents" / "Moodle"), rs)
check("11. вложенная папка: наверх ведёт в «Документы», цепочка из двух звеньев",
      j["parent"] == str((SHARED / "Documents").resolve()) and [t["name"] for t in j["trail"]] == ["Документы", "Moodle"], j)
check("12. в ответе есть корни и признак записи", j["can_write"] is True and len(j["roots"]) == len(rs))

# --- границы: нельзя выйти за корни
for label, p in (("снаружи", OUTSIDE), ("через ..", SHARED / ".." / "outside"), ("корень диска", Path(os.path.abspath(os.sep))), ("файл", SHARED / "Documents" / "file.txt"), ("нет такой", SHARED / "nope")):
    j = folders.browse(str(p), rs)
    check(f"13. путь «{label}» не открывается: показан запасной вариант внутри корней", j["ok"] and folders.inside(j["path"], rs) is not None, j.get("path"))
j = folders.browse(str(OUTSIDE), rs, start=str(SHARED / "Documents" / "Moodle"))
check("14. запасной вариант — папка сохранения, если она годится", j["path"] == str((SHARED / "Documents" / "Moodle").resolve()))
j = folders.browse("", rs, start=str(OUTSIDE))
check("15. и первый корень, если не годится даже она", j["path"] == rs[0]["path"])
if hasattr(os, "symlink"):
    try:
        os.symlink(str(OUTSIDE), str(SHARED / "Pictures" / "link"))
        sym = True
    except OSError:
        sym = False
    if sym:
        j = folders.browse(str(SHARED / "Pictures" / "link"), rs)
        check("16. ссылка наружу не выпускает за корни", not j["path"].startswith(str(OUTSIDE.resolve())), j["path"])
        check("17. inside() ссылку наружу не принимает", folders.inside(SHARED / "Pictures" / "link", rs) is None)
check("18. пустые корни — аккуратная ошибка, а не падение", folders.browse("", [])["ok"] is False)

# --- много папок
big = SHARED / "Pictures"
for i in range(folders.MAX_ENTRIES + 20):
    (big / f"p{i:04d}").mkdir()
j = folders.browse(str(big), rs)
check("19. очень много папок: отдаётся не больше предела, есть пометка", len(j["dirs"]) == folders.MAX_ENTRIES and j["truncated"] is True)

# --- новая папка
new = folders.make_dir(str(SHARED / "Documents"), "Учёба 2026", rs)
check("20. новая папка создаётся там, где открыто", new == (SHARED / "Documents" / "Учёба 2026").resolve() and new.is_dir())
def raises(f):
    try: f(); return False
    except ValueError: return True
check("21. такая же — понятная ошибка", raises(lambda: folders.make_dir(str(SHARED / "Documents"), "Учёба 2026", rs)))
check("22. путь внутри имени и вне корней — ошибка, ничего не создано",
      raises(lambda: folders.make_dir(str(SHARED), "../escape", rs)) and raises(lambda: folders.make_dir(str(OUTSIDE), "x", rs)) and not (BASE / "escape").exists()
      and not (OUTSIDE / "x").exists())
check("23. проверка записи: в обычную папку можно, лишних файлов не остаётся",
      folders.probe_writable(new) and not [p for p in new.iterdir()])
check("24. проверка записи: в несуществующую нельзя", folders.probe_writable(BASE / "nope") is False)

# --- запросы окна
import gui
from http.server import ThreadingHTTPServer
gui.android.shared_storage = lambda home: SHARED
gui.folders.volumes = lambda storage="/storage": [SD]
APP = gui.APP = gui.App()
APP.settings.update({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(SHARED / "Documents" / "Moodle")})
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT, TOK = srv.server_address[1], APP.token
def call(path, body=None, token=TOK):
    url = f"http://127.0.0.1:{PORT}{path}" + (("&" if "?" in path else "?") + "t=" + token)
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

check("25. без ключа доступа — 403",
      all(call(p, {"path": str(SHARED)}, token="wrong")[0] == 403 for p in ("/api/folders", "/api/folders/new", "/api/folders/use")))
st, j = call("/api/folders", {"path": ""})
check("26. окно открывается на папке сохранения", st == 200 and j["ok"] and j["path"] == str((SHARED / "Documents" / "Moodle").resolve()) and j["current"] == j["path"], j.get("path"))
APP.settings["out_dir"] = str(SHARED / "Documents" / "Moodle" / ".." / "Moodle")
st, j = call("/api/folders", {"path": ""})
check("26а. текущая папка отмечена и для сохранённого пути с псевдонимом", st == 200 and j["ok"] and j["current"] == j["path"], j)
check("27. в ответе есть корни, вид телефона и признак «нужен доступ»", len(j["roots"]) >= 4 and j["kind"] in ("apk", "termux", "other") and j["need_access"] is False)
st, j = call("/api/folders", {"path": str(OUTSIDE)})
check("28. чужая папка не открывается", j["ok"] and not j["path"].startswith(str(OUTSIDE.resolve())))
st, j = call("/api/folders/new", {"parent": str(SHARED / "Documents"), "name": "Мудл"})
check("29. новая папка через окно", st == 200 and j["ok"] and (SHARED / "Documents" / "Мудл").is_dir(), j)
st, j = call("/api/folders/new", {"parent": str(SHARED / "Documents"), "name": "../x"})
check("30. плохое имя — ошибка с текстом, ничего не создано", j["ok"] is False and j["error"] and not (SHARED / "x").exists(), j)
st, j = call("/api/folders/use", {"path": str(SHARED / "Documents" / "Мудл")})
saved = json.loads((BASE / "data" / "settings.json").read_text(encoding="utf-8")).get("out_dir")
check("31. выбрана папка: путь в настройках и в файле", st == 200 and j["ok"] and APP.settings["out_dir"] == str((SHARED / "Documents" / "Мудл").resolve()) and saved == APP.settings["out_dir"], (j, saved))
before = APP.settings["out_dir"]
st, j = call("/api/folders/use", {"path": str(OUTSIDE)})
check("32. чужая папка не выбирается, настройка не меняется", j["ok"] is False and APP.settings["out_dir"] == before, j)
st, j = call("/api/folders/use", {"path": str(SHARED / "nope")})
check("33. несуществующая не выбирается", j["ok"] is False and APP.settings["out_dir"] == before)
ro = SHARED / "Pictures" / "p0001"
if os.name != "nt" and os.geteuid() != 0:
    os.chmod(ro, 0o500)
    st, j = call("/api/folders/use", {"path": str(ro)})
    check("34. папка без права записи не выбирается", j["ok"] is False and "записывать" in j["error"] and APP.settings["out_dir"] == before, j)
    os.chmod(ro, 0o700)
else:
    print("SKIP 34. (запущено от администратора или Windows: право записи не проверить)")
_probe = gui.folders.probe_writable
gui.folders.probe_writable = lambda path: False                       # право записи пропало (карта памяти только для чтения и т. п.)
st, j = call("/api/folders/use", {"path": str(SHARED / "Documents")})
gui.folders.probe_writable = _probe
check("34б. если запись не удалась, папка не выбирается и настройка не меняется", j["ok"] is False and "записывать" in j["error"] and APP.settings["out_dir"] == before, j)
st, j = call("/api/folders", {"path": str(SHARED)})
check("35. «Папка приложения» (дом) тоже в списке корней", any(r["name"] == "Память приложения" for r in j["roots"]))

src = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
check("36. в окне: кнопка «…» есть и на телефоне (без `no-android`)", 'id="pickDir"' in src and 'class="btn no-android" id="pickDir"' not in src)
check("37. в окне: на телефоне «…» открывает своё окно, на компьютере — прежний выбор", "if (ANDROID) return dirOpen();" in src and 'api("/api/pick_folder"' in src)

print("FAILS:", FAILS)
shutil.rmtree(BASE, ignore_errors=True)
raise SystemExit(1 if FAILS else 0)

"""Заметки через окно программы: запросы /api/notes*, возврат удалённой, обновление поиска после правки.
Без сети: сайты не трогаются (проверка Moodle, обновлений и страны IP выключены), данные — во временной папке."""
import os, sys, json, shutil, tempfile, threading, time, urllib.request, urllib.error, urllib.parse
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_notes_api"
shutil.rmtree(BASE, ignore_errors=True); (BASE / "data").mkdir(parents=True); (BASE / "out" / "Курс Сети" / "Лекции").mkdir(parents=True)
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
(BASE / "data" / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}},
                                                         "out_dir": str(BASE / "out")}), encoding="utf-8")
(BASE / "out" / "Курс Сети" / "Лекции" / "лекция.txt").write_text("Маршрутизация пакетов в сети", encoding="utf-8")

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import gui
from http.server import ThreadingHTTPServer
APP = gui.APP = gui.App()
APP.settings.update({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(BASE / "out")})
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT, TOK = srv.server_address[1], APP.token

def call(path, body=None, token=TOK):
    url = f"http://127.0.0.1:{PORT}{path}" + (("&" if "?" in path else "?") + "t=" + token)
    req = urllib.request.Request(url, data=(json.dumps(body).encode() if body is not None else None), method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

st, j = call("/api/notes")
check("пустой список заметок", st == 200 and j["notes"] == [] and j["total"] == 0, str(j))
check("без ключа доступа — 403", call("/api/notes", token="wrong")[0] == 403)
st, j = call("/api/notes/save", {"title": "Мысли", "body": "Про сети"})
nid = j["note"]["id"]
check("общая заметка сохранена", st == 200 and j["ok"] and j["note"]["course"] is None and (BASE / "out" / "Заметки" / "Блокнот" / "Мысли.md").exists(), str(j))
st, j = call("/api/notes/save", {"title": "К лекции 1", "body": "Спросить про OSPF", "course": 55, "course_name": "Курс Сети", "item": "k-lec1", "item_name": "Лекция 1"})
cid = j["note"]["id"]
check("заметка к элементу курса: папка курса, привязка", j["ok"] and j["note"]["item"] == "k-lec1" and j["note"]["course"] == 55 and (BASE / "out" / "Заметки" / "Курс Сети" / "К лекции 1.md").exists(), str(j))
check("список: все, только курса, только общие, поиск",
      call("/api/notes")[1]["total"] == 2 and [n["id"] for n in call("/api/notes?course=55")[1]["notes"]] == [cid]
      and [n["id"] for n in call("/api/notes?general=1")[1]["notes"]] == [nid] and [n["id"] for n in call("/api/notes?q=ospf")[1]["notes"]] == [cid])
j = call("/api/notes/get?id=" + cid)[1]
check("чтение заметки с текстом", j["ok"] and j["note"]["body"] == "Спросить про OSPF\n")
check("чужой номер — ответ с ошибкой, не падение", call("/api/notes/get?id=../../x")[1]["ok"] is False and call("/api/notes/get?id=n0123456789")[1]["ok"] is False)
j = call("/api/notes/save", {"id": cid, "title": "К лекции 1", "body": "Спросить про OSPF и BGP", "pinned": True, "course": 7})[1]
check("изменение: тот же номер, закреплена, курс не подменён", j["ok"] and j["note"]["id"] == cid and j["note"]["pinned"] and j["note"]["course"] == 55)
check("слишком длинная заметка — понятная ошибка", "Слишком длинная" in call("/api/notes/save", {"title": "x", "body": "я" * 250_000})[1].get("error", ""))
check("кривые данные не роняют сервер", call("/api/notes/save", {"title": 5, "body": None, "course": "abc", "pinned": "yes"})[1]["ok"] is True)

# удаление и возврат
j = call("/api/notes/delete", {"id": nid})[1]
check("удалена: нет в списке, файл в корзине программы (не в папке материалов)", j["ok"] and nid not in [n["id"] for n in call("/api/notes")[1]["notes"]]
      and (BASE / "data" / "notes_trash" / j["trash"]).exists() and not any("notes_trash" in str(p) for p in (BASE / "out").rglob("*")), str(j))
j2 = call("/api/notes/restore", {"trash": j["trash"]})[1]
check("«Вернуть»: заметка на месте с тем же номером", j2["ok"] and j2["note"]["id"] == nid and call("/api/notes/get?id=" + nid)[1]["ok"])
check("удалить несуществующую — ошибка, не падение", call("/api/notes/delete", {"id": "n0123456789ab"})[1]["ok"] is False)
check("вернуть с чужим именем файла — отказ", call("/api/notes/restore", {"trash": "../settings.json"})[1]["ok"] is False)

# поиск: индекса ещё нет — правка заметки его не создаёт; после первой индексации новая заметка находится сама
check("индекса нет — после правки он не появился", not (BASE / "out" / ".moodle_search.sqlite").exists())
r = call("/api/search/index", {})[1]
for _ in range(120):
    time.sleep(0.5)
    if not call("/api/search/status")[1].get("progress", {}).get("running"): break
res = call("/api/search?q=OSPF")[1]
check("первая индексация видит заметки (папка «Заметки» — как курс)", res.get("results") and "OSPF" in json.dumps(res, ensure_ascii=False), str(res)[:300])
call("/api/notes/save", {"title": "Новая", "body": "Уникальное слово жираф"})
found = False
for _ in range(60):
    time.sleep(0.5)
    res = call("/api/search?q=" + urllib.parse.quote("жираф"))[1]
    if res.get("results"): found = True; break
check("после правки заметки поиск обновился сам (через несколько секунд)", found, str(res)[:300])
hit = json.dumps(res, ensure_ascii=False)
check("в найденном нет служебной строки", "mo-note" not in hit)

print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

"""Старые версии программы через окно: GET /api/oldver, POST /api/oldver/delete. Домашняя папка и данные — временные,
настоящие «Загрузки» не трогаем. Телефон: доступ к общей памяти подменяется."""
import os, sys, json, shutil, tempfile, threading, urllib.request, urllib.error
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_oldver_api"
shutil.rmtree(BASE, ignore_errors=True); (BASE / "data").mkdir(parents=True); (BASE / "out").mkdir(parents=True)
HOME = BASE / "home"; (HOME / "Downloads").mkdir(parents=True)
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
os.environ["HOME"] = str(HOME); os.environ["USERPROFILE"] = str(HOME)
(BASE / "data" / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}},
                                                         "out_dir": str(BASE / "out")}), encoding="utf-8")
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import gui, android, desktop
import moodle_offline as mo
from http.server import ThreadingHTTPServer

APP = gui.APP = gui.App()
APP.settings.update({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(BASE / "out")})
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

D = HOME / "Downloads"
(D / "MoodleOffline-1.0.0.exe").write_bytes(b"a" * 1000)
(D / "MoodleOffline-1.0.0.exe.sha256").write_bytes(b"x")
(D / "MoodleOffline.exe.crdownload").write_bytes(b"p" * 50)
(D / f"MoodleOffline-{mo.VERSION}.exe").write_bytes(b"c" * 2000)        # такая же версия, как запущенная
(D / "MoodleOffline-99.0.0.apk").write_bytes(b"n" * 300)                # из имени — новее
(D / "diplom.docx").write_bytes(b"not ours")

check("без ключа доступа — 403", call("/api/oldver", token="wrong")[0] == 403 and call("/api/oldver/delete", {"ids": []}, token="wrong")[0] == 403)
st, j = call("/api/oldver")
by = {x["name"]: x for x in j.get("items", [])}
check("поиск: ответ целиком", st == 200 and j["ok"] and j["current"] == mo.VERSION and j["system"] == desktop.system() and j["need_access"] is False and j["dirs"] == [str(D)], str(j)[:300])
check("поиск: нашлись файлы программы, чужой документ — нет", set(by) == {"MoodleOffline-1.0.0.exe", "MoodleOffline-1.0.0.exe.sha256", "MoodleOffline.exe.crdownload",
                                                                         f"MoodleOffline-{mo.VERSION}.exe", "MoodleOffline-99.0.0.apk"}, sorted(by))
check("поиск: оценки и отметки", by["MoodleOffline-1.0.0.exe"]["status"] == "old" and by["MoodleOffline-1.0.0.exe"]["checked"]
      and by["MoodleOffline.exe.crdownload"]["status"] == "junk" and by[f"MoodleOffline-{mo.VERSION}.exe"]["status"] == "same"
      and by["MoodleOffline-99.0.0.apk"]["status"] == "newer" and not by["MoodleOffline-99.0.0.apk"]["checked"], {k: v["status"] for k, v in by.items()})
check("поиск: итоги по размеру", j["total"] == 1000 + 1 + 50 + 2000 + 300 and j["to_delete"] == 1000 + 1 + 50, (j["total"], j["to_delete"]))

st, j2 = call("/api/oldver/delete", {"ids": []})
check("удаление без выбора → понятная ошибка", st == 200 and j2["ok"] is False)
st, j2 = call("/api/oldver/delete", {"ids": "все"})
check("удаление: не список → ошибка, ничего не тронуто", j2["ok"] is False and (D / "MoodleOffline-1.0.0.exe").exists())
bad = ["../../etc/passwd", str(D / "diplom.docx"), "x" * 10, "", str(D / "MoodleOffline-1.0.0.exe")]
st, j2 = call("/api/oldver/delete", {"ids": bad})
check("удаление: чужие пути и выдуманные id не удаляют ничего", j2["ok"] and j2["deleted"] == [] and len(j2["failed"]) >= 4 and (D / "diplom.docx").exists()
      and (D / "MoodleOffline-1.0.0.exe").exists(), str(j2)[:300])

ids = [x["id"] for x in by.values() if x["checked"]]
st, j3 = call("/api/oldver/delete", {"ids": ids})
check("удаление: отмеченное удалено, место посчитано", j3["ok"] and len(j3["deleted"]) == 3 and j3["freed"] == 1051 and not j3["failed"], str(j3)[:300])
check("удалено с диска, остальное цело", not (D / "MoodleOffline-1.0.0.exe").exists() and not (D / "MoodleOffline.exe.crdownload").exists()
      and (D / f"MoodleOffline-{mo.VERSION}.exe").exists() and (D / "MoodleOffline-99.0.0.apk").exists() and (D / "diplom.docx").exists())
check("после удаления ответ уже содержит свежий список", {x["name"] for x in j3["state"]["items"]} == {f"MoodleOffline-{mo.VERSION}.exe", "MoodleOffline-99.0.0.apk"}, str(j3["state"])[:200])
st, j4 = call("/api/oldver/delete", {"ids": ids})
check("повторное удаление тех же id — ошибки по каждому, без падения", st == 200 and j4["ok"] and j4["deleted"] == [] and len(j4["failed"]) == 3)

# запущена собранная программа: её файл не показываем, остатки обновления прячем, пока идёт обновление
EXE = BASE / "app"; EXE.mkdir()
(EXE / "MoodleOffline.exe").write_bytes(b"R" * 3000); (EXE / "MoodleOffline.exe.new").write_bytes(b"half"); (EXE / "MoodleOffline.exe.old").write_bytes(b"prev")
sys.frozen = True; real_exe = sys.executable; sys.executable = str(EXE / "MoodleOffline.exe")
try:
    names = {x["name"] for x in call("/api/oldver")[1]["items"] if x["dir"] == str(EXE)}
    check("запущенная программа в списке не видна, остатки рядом — видны", names == {"MoodleOffline.exe.new", "MoodleOffline.exe.old"}, sorted(names))
    APP.upd_install["state"] = "downloading"
    names = {x["name"] for x in call("/api/oldver")[1]["items"] if x["dir"] == str(EXE)}
    check("пока идёт обновление, его временные файлы не показываются (и не удаляются)", names == set(), sorted(names))
    APP.upd_install["state"] = "idle"
finally:
    sys.frozen = False; delattr(sys, "frozen"); sys.executable = real_exe

# телефон: общая память видна только при доступе
SH = BASE / "shared"; (SH / "Download").mkdir(parents=True)
(SH / "Download" / "MoodleOffline (1).apk").write_bytes(b"z" * 10)
real = (gui.ANDROID, android.shared_storage, android.is_apk)
gui.ANDROID = True
try:
    android.shared_storage = lambda home: None; android.is_apk = lambda env=None: True
    j5 = call("/api/oldver")[1]
    check("телефон без доступа ко всем файлам: просим доступ, ничего не ищем", j5["need_access"] is True and j5["can_ask"] is True and j5["items"] == [] and j5["system"] == "android", str(j5)[:200])
    android.is_apk = lambda env=None: False
    check("Termux без доступа: подсказка вместо кнопки", call("/api/oldver")[1]["can_ask"] is False)
    android.shared_storage = lambda home: SH
    j6 = call("/api/oldver")[1]
    check("телефон с доступом: ищет в «Download» общей памяти", j6["need_access"] is False and [x["name"] for x in j6["items"]] == ["MoodleOffline (1).apk"]
          and str(SH / "Download") in j6["dirs"] and str(D) not in j6["dirs"], str(j6)[:300])
finally:
    gui.ANDROID, android.shared_storage, android.is_apk = real

shutil.rmtree(BASE, ignore_errors=True)
print("\nFAILS:", FAILS)
sys.exit(1 if FAILS else 0)

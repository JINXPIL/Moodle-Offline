"""Настройки окна переживают перезапуск и обновление (uistate.py, ui_state.json), сброс по умолчанию, резервная копия
settings.json, надёжная запись файлов, защита окна от страниц из материалов курсов (1.27.0). Настоящий локальный сервер gui.py."""
import os, sys, json, shutil, tempfile, threading, time, urllib.request, urllib.error
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_uistate"
shutil.rmtree(BASE, ignore_errors=True); (BASE / "data").mkdir(parents=True)
OUT = BASE / "out"; OUT.mkdir()
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
(BASE / "data" / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}},
                                                          "out_dir": str(OUT), "mode": 3, "page_delay": "5-9", "pdf": True, "schedule_group": "ib-b-25-2-o",
                                                          "course_ids": [1, 2], "ai": {"provider": "claude"}, "quiet_hours_on": True}), encoding="utf-8")
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import moodle_offline as mo, uistate, gui

# ── 1. хранилище настроек окна
st = uistate.Store(BASE / "data")
check("пусто: init=False (окно отдаст программе то, что было в браузере)", st.get() == {"init": False, "data": {}})
r = st.update({"set": {"mo_view": '{"courses":{"cols":"3"}}', "mo_theme": "dark", "rs_view": "week", "rs:ИБ/б-25-2-о:sub": "1",
                       "mo_t": "секрет", "evil": "x", "mo_bad key": "x", "mo_num": 5}, "init": True})
d = st.get()["data"]
check("сохраняются только ключи окна и расписания", r["ok"] and set(d) == {"mo_view", "mo_theme", "rs_view", "rs:ИБ/б-25-2-о:sub"}, str(sorted(d)))
check("ключ доступа окна (mo_t) не сохраняется никогда", "mo_t" not in d)
st2 = uistate.Store(BASE / "data")
check("после перезапуска программы всё на месте (init=True)", st2.get() == {"init": True, "data": d})
st2.update({"del": ["mo_theme"], "set": {"mo_accent": "#ff0000"}})
check("удаление и добавление", set(uistate.Store(BASE / "data").get()["data"]) == {"mo_view", "rs_view", "rs:ИБ/б-25-2-о:sub", "mo_accent"})
check("слишком длинное значение не принимается", st2.update({"set": {"mo_big": "x" * (uistate.MAX_VALUE + 1)}})["ok"] and "mo_big" not in st2.get()["data"])
big = {f"mo_k{i}": "y" * 2000 for i in range(300)}
check("общий объём ограничен (окно не забьёт диск)", st2.update({"set": big})["ok"] is False and "mo_k1" not in st2.get()["data"])
st2.reset()
check("сброс: пусто, но init=True (окно сотрёт у себя лишнее)", uistate.Store(BASE / "data").get() == {"init": True, "data": {}})
(BASE / "data" / uistate.FILE).write_text("{битый", encoding="utf-8")
st3 = uistate.Store(BASE / "data")
check("битый ui_state.json: не падаем, init=False", st3.get() == {"init": False, "data": {}})
check("…а сам файл отложен рядом (.broken-…), не затёрт", any(p.name.startswith(uistate.FILE + ".broken-") for p in (BASE / "data").iterdir()))
sc = uistate.page_script({"init": True, "data": {"mo_x": "</script><script>alert(1)</script>"}}).decode()
check("вписываемый в страницу снимок не может закрыть <script>", sc.count("</script>") == 1 and sc.startswith("<script>window.__MO_UI="), sc)

# ── 2. сброс настроек программы
new, dropped = uistate.reset_settings({"out_dir": "X", "base_url": "B", "mode": 3, "pdf": True, "course_ids": [1], "schedule_group": "g",
                                        "ai": {"provider": "claude"}, "mcp": {"enabled": True}, "watch": {"autostart": True}, "page_delay": "5"})
check("сброс: остаются папка, адрес, курсы, группа, нейросети, автозапуск", new == {"out_dir": "X", "base_url": "B", "course_ids": [1], "schedule_group": "g",
                                                                                     "ai": {"provider": "claude"}, "mcp": {"enabled": True}, "watch": {"autostart": True}}, str(new))
check("сброс: способ сохранения, PDF, паузы — по умолчанию", dropped == ["mode", "page_delay", "pdf"], str(dropped))

# ── 3. settings.json: резервная копия, битый файл, надёжная запись
v = mo.Vault(BASE / "vault"); (BASE / "vault").mkdir()
v.save_settings({"a": 1, "b": "два"})
check("сохранение настроек пишет и копию settings.json.bak", json.loads(v.settings_backup.read_text(encoding="utf-8")) == {"a": 1, "b": "два"})
v.settings_path.write_text('{"a": 1, "b": "дв', encoding="utf-8")      # оборвалась запись / испортился диск
got = mo.Vault(BASE / "vault")
check("битый settings.json → настройки из копии (раньше — молча «по умолчанию» и затирались)", got.load_settings() == {"a": 1, "b": "два"})
check("…об этом есть сообщение для журнала", "резервной копии" in got.restored)
check("…испорченный файл отложен рядом", any(p.name.startswith("settings.json.broken-") for p in (BASE / "vault").iterdir()))
v.settings_path.write_text("[1, 2]", encoding="utf-8")
check("settings.json не словарь — тоже из копии", mo.Vault(BASE / "vault").load_settings() == {"a": 1, "b": "два"})
p = BASE / "conc.json"
def writer(i):
    for _ in range(40): mo.write_text_atomic(p, json.dumps({"i": i, "pad": "x" * 5000}), durable=(i % 2 == 0))
ths = [threading.Thread(target=writer, args=(i,)) for i in range(6)]; [t.start() for t in ths]; [t.join() for t in ths]
ok_json = True
try: json.loads(p.read_text())
except ValueError: ok_json = False
check("шесть потоков пишут один файл одновременно — файл целый (раньше общий «.tmp»)", ok_json)
check("временных файлов не остаётся", not [x for x in BASE.iterdir() if x.name.endswith(".tmp")], str([x.name for x in BASE.iterdir()]))
for i in range(5):
    (BASE / "kb.json").write_text("x"); mo.keep_broken(BASE / "kb.json"); time.sleep(1.05 if i < 4 else 0)
check("копий испорченного файла хранится не больше трёх", len(list(BASE.glob("kb.json.broken-*"))) == 3, str(len(list(BASE.glob("kb.json.broken-*")))))

# ── 3б. файл на мгновение занят (антивирус, OneDrive) — это не порча: читаем ещё раз, ничего не откладываем и не затираем
import builtins
real_open = builtins.open
def flaky(times):
    left = [times]
    def op(file, mode="r", *a, **k):
        if "r" in mode and "settings.json" in str(file) and not str(file).endswith(".bak") and left[0] > 0:
            left[0] -= 1; raise PermissionError(13, "занят", str(file))
        return real_open(file, mode, *a, **k)
    return op
v.save_settings({"a": 7})
for f in (BASE / "vault").glob("settings.json.broken-*"): f.unlink()
builtins.open = flaky(2)
try: got = mo.Vault(BASE / "vault").load_settings()
finally: builtins.open = real_open
check("settings.json занят дважды подряд — прочитан с третьего раза, не отложен как испорченный",
      got == {"a": 7} and not list((BASE / "vault").glob("settings.json.broken-*")), str(got))
builtins.open = flaky(99)
try: got = mo.Vault(BASE / "vault").load_settings()
finally: builtins.open = real_open
check("settings.json занят всё время — берём копию, а сам файл не трогаем", got == {"a": 7} and v.settings_path.exists() and not list((BASE / "vault").glob("settings.json.broken-*")))
sp = BASE / "st" / mo.STATE_FILE; sp.parent.mkdir()
sp.write_text(json.dumps({"version": 1, "files": {"k": {"paths": ["a.pdf"], "size": 1}}, "items": {}, "courses": {"5": {}}, "news": {}}), encoding="utf-8")
def flaky_state(file, mode="r", *a, **k):
    if "r" in mode and str(file).endswith(mo.STATE_FILE): raise PermissionError(13, "занят", str(file))
    return real_open(file, mode, *a, **k)
builtins.open = flaky_state
try: stt = mo.State(sp)
finally: builtins.open = real_open
stt.save(force=True)
check("состояние загрузок не прочитать (занято) — оно не откладывается в .bad и не затирается пустым",
      not (sp.parent / (mo.STATE_FILE + ".bad")).exists() and json.loads(sp.read_text(encoding="utf-8"))["courses"] == {"5": {}})
import news as nw
npth = BASE / "news" / ".moodle_news.json"; npth.parent.mkdir()
npth.write_text(json.dumps({"version": 1, "events": [{"id": "e1", "kind": "x"}]}), encoding="utf-8")
def flaky_news(file, *a, **k):
    if str(file).endswith(".moodle_news.json"): raise PermissionError(13, "занят", str(file))
    return real_rt(file, *a, **k)
real_rt = Path.read_text
Path.read_text = lambda self, *a, **k: flaky_news(self, *a, **k) if str(self).endswith(".moodle_news.json") else real_rt(self, *a, **k)
try:
    feed = nw.NewsLog(npth)
finally:
    Path.read_text = real_rt
feed._save()
if True:
    check("лента «Что нового» не прочитана (занята) — не затирается пустой", json.loads(npth.read_text(encoding="utf-8"))["events"][0]["id"] == "e1" and not (npth.parent / ".moodle_news.json.bad").exists())

# ── 4. окно: API настроек окна, вписывание в страницу, сброс, защита
APP = gui.APP = gui.App()
opened = []
gui.open_path = lambda target, reveal=False: opened.append((Path(target).name, reveal))
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT, TOK = srv.server_address[1], APP.token
def req(path, body=None, headers=None, token=True, raw=False):
    url = f"http://127.0.0.1:{PORT}{path}" + ((("&" if "?" in path else "?") + "t=" + TOK) if token else "")
    rq = urllib.request.Request(url, data=(json.dumps(body).encode() if body is not None else None), method="POST" if body is not None else "GET", headers=headers or {})
    try:
        with urllib.request.urlopen(rq, timeout=20) as r: data = r.read(); return r.status, (data if raw else json.loads(data or b"null")), dict(r.headers)
    except urllib.error.HTTPError as e:
        data = e.read(); return e.code, (data if raw else (json.loads(data) if data[:1] in (b"{", b"[") else data)), dict(e.headers)

c, j, _ = req("/api/ui")
check("GET /api/ui отвечает (init, data)", c == 200 and set(j) == {"init", "data"}, str(j))
c, j, _ = req("/api/ui", {"set": {"mo_theme": "light", "rs_scale": "1.2"}, "init": True})
check("POST /api/ui сохраняет", c == 200 and j["ok"] and gui.APP.ui.get()["data"] == {"mo_theme": "light", "rs_scale": "1.2"})
c, page, _ = req("/", raw=True)
check("страница с ключом: настройки окна вписаны в начало <head>", b"<head><script>window.__MO_UI=" in page and b'"mo_theme": "light"' in page)
c, page, _ = req("/", raw=True, token=False)
check("страница без ключа: настройки не вписываются (их видит только окно программы)", b"__MO_UI=" not in page.split(b"<style>")[0])
c, j, _ = req("/api/settings/reset", {"what": "view"})
check("сброс вида: настройки окна пусты, настройки программы не тронуты", j["ok"] and APP.ui.get()["data"] == {} and APP.settings.get("mode") == 3)
c, j, _ = req("/api/settings/reset", {"what": "all"})
check("сброс всего: способ сохранения и паузы — по умолчанию", j["ok"] and APP.settings.get("mode") == 1 and "page_delay" not in APP.settings and "pdf" not in APP.settings, str(APP.settings))
check("…папка, группа, курсы, нейросеть остались", APP.settings.get("out_dir") == str(OUT) and APP.settings.get("schedule_group") == "ib-b-25-2-o"
      and APP.settings.get("course_ids") == [1, 2] and APP.settings.get("ai") == {"provider": "claude"})
bk = json.loads((BASE / "data" / "settings.json.before-reset").read_text(encoding="utf-8"))
check("…копия прежних настроек лежит в settings.json.before-reset", bk.get("mode") == 3 and bk.get("page_delay") == "5-9")
check("…и на диске сохранено", json.loads((BASE / "data" / "settings.json").read_text(encoding="utf-8")).get("mode") == 1)
check("неизвестный вид сброса — отказ", req("/api/settings/reset", {"what": "всё-всё"})[1]["ok"] is False)

# защита: запросы к API — только от самого окна
c, j, _ = req("/api/state", headers={"Sec-Fetch-Site": "same-origin"})
check("окно программы (same-origin) — пускаем", c == 200)
c, j, _ = req("/api/state")
check("не из браузера (консоль, приложение, вторая копия) — пускаем", c == 200)
for h, why in (({"Sec-Fetch-Site": "cross-site"}, "страница чужого сайта"), ({"Origin": "null"}, "страница в песочнице (Origin: null)"),
               ({"Origin": "http://evil.example"}, "чужой Origin"), ({"Sec-Fetch-Site": "same-site"}, "соседний адрес")):
    check(f"{why} с правильным ключом — отказ", req("/api/state", headers=h)[0] == 403)
    check(f"{why}: POST /api/open — отказ", req("/api/open", {"path": "x"}, headers=h)[0] == 403)
c, j, _ = req("/api/ui", {"set": {"mo_theme": "dark"}}, headers={"Origin": f"http://127.0.0.1:{PORT}", "Sec-Fetch-Site": "same-origin"})
check("POST из самого окна (Origin свой) — пускаем", c == 200 and j["ok"])

# файлы из материалов: страницы и SVG — в песочнице, свой просмотрщик расписания — нет
(OUT / "Курс" / "Форум").mkdir(parents=True)
(OUT / "Курс" / "Форум" / "вложение.html").write_text("<script>parent.T</script>", encoding="utf-8")
(OUT / "Курс" / "Форум" / "рисунок.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>", encoding="utf-8")
(OUT / "Курс" / "Форум" / "лекция.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
(OUT / "Курс" / "Форум" / "вирус.exe").write_bytes(b"MZ")
(OUT / "Курс" / "index.html").write_text("<!DOCTYPE html>\n<!--viewer:a0674fe127--><script>localStorage</script>", encoding="utf-8")     # подделка «просмотрщика» в курсе
(OUT / "Расписание").mkdir()
(OUT / "Расписание" / "index.html").write_text("<!DOCTYPE html>\n<!--viewer:a0674fe127-->\n<html></html>", encoding="utf-8")
(OUT / "Расписание" / "Другие группы" / "ib-b-25-1-o").mkdir(parents=True)
(OUT / "Расписание" / "Другие группы" / "ib-b-25-1-o" / "index.html").write_text("<!DOCTYPE html>\n<!--viewer:a0674fe127-->", encoding="utf-8")
(OUT / "Расписание" / "Другие группы" / "ib-b-25-1-o" / "fake.html").write_text("<!DOCTYPE html>\n<!--viewer:a0674fe127-->", encoding="utf-8")
(OUT / "Расписание" / "чужое.html").write_text("<script>1</script>", encoding="utf-8")
def hdr(rel):
    c, _, h = req("/f/" + urllib.parse.quote(TOK) + "/" + urllib.parse.quote(rel), raw=True, token=False)
    return c, (h.get("Content-Security-Policy") or "")
import urllib.parse
for rel in ("Курс/Форум/вложение.html", "Курс/Форум/рисунок.svg", "Курс/index.html", "Расписание/чужое.html"):
    c, csp = hdr(rel)
    check(f"{rel}: отдаётся в песочнице (без allow-same-origin)", c == 200 and csp.startswith("sandbox") and "allow-same-origin" not in csp, csp)
c, csp = hdr("Расписание/index.html")
check("свой просмотрщик расписания — без песочницы (ему нужно хранилище настроек)", c == 200 and csp == "", csp)
c, csp = hdr("Расписание/Другие группы/ib-b-25-1-o/index.html")
check("просмотрщик чужой группы — тоже без песочницы", c == 200 and csp == "", csp)
c, csp = hdr("Расписание/Другие группы/ib-b-25-1-o/fake.html")
check("другой файл рядом с ним, даже с отпечатком, — в песочнице", csp.startswith("sandbox"), csp)
c, csp = hdr("Курс/Форум/лекция.pdf")
check("PDF — без песочницы (иначе встроенный просмотр не откроется)", c == 200 and csp == "", csp)
c, j, _ = req("/api/open", {"path": "Курс/Форум/вирус.exe"})
check("«Открыть» .exe из материалов — отказ с объяснением, программа его не запускает", j["ok"] is False and j.get("risky") and not opened, str(j))
c, j, _ = req("/api/open", {"path": "Курс/Форум/вирус.exe", "reveal": True})
check("«Показать в папке» для .exe — можно", j["ok"] and opened == [("вирус.exe", True)], str(opened))
c, j, _ = req("/api/open", {"path": "Курс/Форум/лекция.pdf"})
check("обычный файл открывается как раньше", j["ok"] and opened[-1] == ("лекция.pdf", False))

print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

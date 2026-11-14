"""Доступ для нейросетей (mcp_server.py): инструменты чтения, защита путей, JSON-RPC, HTTP на настоящем сокете и stdio.
Без сети и сторонних библиотек; токены и тексты — вымышленные."""
import http.client, io, json, os, shutil, socket, sys, tempfile, threading
from pathlib import Path
import _root
import mcp_server as M
import notes, search, transfer

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

BASE = Path(tempfile.gettempdir()) / "mo_mcp_t"; shutil.rmtree(BASE, ignore_errors=True)
OUT = BASE / "out"; OUT.mkdir(parents=True)
CRS, CRS2 = "Сети и телекоммуникации", "Физика. Часть 2"
(OUT / CRS / "Лекции").mkdir(parents=True); (OUT / CRS2).mkdir()
(OUT / CRS / "Лекции" / "ospf.txt").write_text("OSPF — протокол маршрутизации по состоянию каналов.\n" + "Маршрутизатор строит карту сети. " * 40, encoding="utf-8")
(OUT / CRS / "Лекции" / "bgp.html").write_text("<html><body><h1>BGP</h1><p>Протокол между автономными системами.</p></body></html>", encoding="utf-8")
(OUT / CRS / "Лекции" / "прога.exe").write_bytes(b"MZ\x00\x00")
(OUT / CRS / "Лекции" / "скан.txt").write_text("   \n", encoding="utf-8")
(OUT / CRS2 / "кинематика.txt").write_text("Скорость — производная координаты. Маршрутизация тут ни при чём.", encoding="utf-8")
(OUT / ".moodle_offline").mkdir(); (OUT / ".moodle_offline" / "token.txt").write_text("секрет-в-служебной-папке", encoding="utf-8")
(OUT / CRS / ".скрытый.txt").write_text("скрытое", encoding="utf-8")
(BASE / "снаружи.txt").write_text("СНАРУЖИ ПАПКИ МАТЕРИАЛОВ", encoding="utf-8")
for tag, cid, name, files in (("c1", 101, CRS, ["Лекции/ospf.txt", "Лекции/bgp.html", "Лекции/прога.exe"]), ("c2", 102, CRS2, ["кинематика.txt"])):
    d = OUT / name
    (d / ".course.json").write_text(json.dumps({"id": cid, "fullname": name, "dir": name, "updated": "01.09.2026", "sections": [
        {"number": 1, "name": "Раздел 1", "items": [{"key": "k-" + tag, "name": "Материалы", "kind": "Лекция",
                                                       "files": [{"path": p, "title": p, "size": 10} for p in files]}]}]}, ensure_ascii=False), encoding="utf-8")
notes.save_note(OUT, title="Мой конспект по сетям", body="Конспект: OSPF выбирает кратчайший путь. СЕКРЕТНАЯ ФРАЗА ЗАМЕТОК", course=101, course_name=CRS, now=1_700_000_000)
nb = notes.save_note(OUT, title="Общая идея", body="Идея про маршрутизацию для курсовой", now=1_700_000_100)
# заметки попадают в тот же поисковый индекс — служебная папка не должна просочиться в «материалы»
ix = search.Index(OUT); ix.update(search.normalize(None))

CFG = {"materials": True, "notes": False}
T = M.Tools(lambda: OUT, lambda: dict(CFG))

# 1. настройки
d = M.normalize(None)
check("по умолчанию: выключено, порт 8787, материалы да, заметки нет", d == {"enabled": False, "port": 8787, "materials": True, "notes": False}, str(d))
check("порт вне диапазона и мусор → 8787", M.normalize({"port": 80})["port"] == 8787 and M.normalize({"port": "x"})["port"] == 8787 and M.normalize({"port": 70000})["port"] == 8787)
check("свой порт и галочки читаются", M.normalize({"enabled": 1, "port": "9000", "notes": 1, "materials": 0}) == {"enabled": True, "port": 9000, "materials": False, "notes": True})
check("токен длинный и разный", len(M.new_token()) > 40 and M.new_token() != M.new_token() and M.new_token().startswith("mo_"))
check("в маске токена его середины нет", "XYZ" not in M.mask("mo_abcXYZdefghijklmnopqrstuvw") and M.mask("") == "")
check("список служебных папок не отстаёт от transfer.SERVICE_TOP", M.SERVICE_TOP >= transfer.SERVICE_TOP, str(transfer.SERVICE_TOP - M.SERVICE_TOP))

# 2. защита путей
def bad(rel):
    try:
        M.resolve_material(OUT, rel); return None
    except M.McpError as e:
        return str(e)
ok = M.resolve_material(OUT, CRS + "/Лекции/ospf.txt")
check("обычный путь читается", ok.name == "ospf.txt" and ok.is_file())
check("обратные слэши и «./» допустимы", M.resolve_material(OUT, CRS + "\\Лекции\\.\\ospf.txt") == ok)
for label, rel in (("«..» наружу", CRS + "/../../снаружи.txt"), ("«..» внутри", CRS + "/Лекции/../../" + "снаружи.txt"), ("абсолютный путь", "/etc/passwd"),
                   ("диск Windows", "C:/Windows/win.ini"), ("поток NTFS", CRS + "/Лекции/ospf.txt:stream"), ("скрытый файл", CRS + "/.скрытый.txt"),
                   ("служебная .moodle_offline", ".moodle_offline/token.txt"), ("«Заметки»", "Заметки/Блокнот/Общая идея.md"),
                   ("«заметки» другим регистром", "заметки/Блокнот/Общая идея.md"), ("_бэкапы", "_бэкапы/x.txt"), ("Расписание", "Расписание/index.html"),
                   ("пустой", ""), ("не строка", 5), ("нулевой байт", CRS + "/Лекции/ospf.txt\x00.exe"), ("несуществующий", CRS + "/нет.txt"),
                   ("неподходящее расширение", CRS + "/Лекции/прога.exe"), ("папка", CRS + "/Лекции")):
    check("путь отклонён: " + label, bad(rel) is not None, str(bad(rel)))
check("исходный текст ошибки по-русски и без путей диска", "/tmp" not in (bad("../x.txt") or "") and bad(CRS + "/нет.txt") == "Файл не найден в материалах.")
try:
    os.symlink(BASE / "снаружи.txt", OUT / CRS / "Лекции" / "ссылка.txt")
    os.symlink(OUT / "Заметки", OUT / "Ссылка-на-заметки", target_is_directory=True)
    SYM = True
except (OSError, NotImplementedError):
    SYM = False
if SYM:
    check("ссылка на файл вне папки материалов отклонена", bad(CRS + "/Лекции/ссылка.txt") is not None)
    check("«папка-курс» со ссылкой на служебную папку отклонена", bad("Ссылка-на-заметки/Блокнот/Общая идея.md") is not None)
else:
    print("SKIP ссылки (нет прав на symlink)")

# 3. какие инструменты видны
names = lambda: sorted(t["name"] for t in T.spec())
check("материалы вкл., заметки выкл.: 4 инструмента, без заметок", names() == ["list_course_files", "list_courses", "read_material", "search_materials"], str(names()))
CFG["notes"] = True
check("заметки вкл.: ещё 2", "list_notes" in names() and "read_note" in names() and len(names()) == 6)
CFG["materials"] = False
check("материалы выкл.: только заметки", names() == ["list_notes", "read_note"], str(names()))
CFG["materials"], CFG["notes"] = True, False
check("у всех инструментов «только чтение» и схема", all(t["annotations"]["readOnlyHint"] and t["inputSchema"]["type"] == "object" and t["description"] for t in T.spec()))
try:
    T.call("list_notes", {}); check("заметки выключены: вызов отклонён", False)
except M.McpError as e:
    check("заметки выключены: вызов отклонён", "выключен" in str(e))
try:
    T.call("нет_такого", {}); check("неизвестный инструмент → KeyError", False)
except KeyError:
    check("неизвестный инструмент → KeyError", True)

# 4. курсы
cl = json.loads(T.call("list_courses", {}))
check("курсы: два, с номером, названием и числом файлов", {c["id"] for c in cl} == {101, 102} and next(c for c in cl if c["id"] == 101)["files"] == 3, str(cl))
check("служебные папки не показываются как курсы", all(c["dir"] in (CRS, CRS2) for c in cl))
check("курс по номеру / папке / части названия", M.find_course(OUT, "101")["dir"] == CRS and M.find_course(OUT, CRS2)["id"] == 102 and M.find_course(OUT, "физика")["id"] == 102)
for ref, word in (("", "Укажите"), ("нет такого", "не найден"), ("с", "несколько")):
    try:
        M.find_course(OUT, ref); check("find_course(%r)" % ref, False)
    except M.McpError as e:
        check("find_course(%r): %s" % (ref, word), word in str(e), str(e))
lf = json.loads(T.call("list_course_files", {"course": "сети"}))
fl = [f for s in lf["sections"] for it in s["items"] for f in it["files"]]
check("список файлов курса: пути с папкой курса, признак «читается»", {f["path"] for f in fl} == {CRS + "/Лекции/ospf.txt", CRS + "/Лекции/bgp.html", CRS + "/Лекции/прога.exe"}
      and {f["path"]: f["readable"] for f in fl}[CRS + "/Лекции/прога.exe"] is False, str(fl))
check("путь из списка проходит в read_material", all(bad(f["path"]) is None for f in fl if f["readable"]))
check("пустой список курсов — понятная фраза", M.Tools(lambda: BASE / "пусто", lambda: CFG).call("list_courses", {}).startswith("Курсов в материалах нет"))

# 5. поиск
r = T.call("search_materials", {"query": "маршрутизации"})
check("поиск находит файл и фрагмент, путь с папкой курса", CRS + "/Лекции/ospf.txt" in r and "OSPF" in r, r[:300])
check("в ответе напоминание, что это данные", r.startswith(M.NOTICE))
check("маркеры подсветки не просачиваются", "\x01" not in r and "\x02" not in r)
check("заметки не попадают в поиск материалов (слово есть только в заметке)", "ничего не найдено" in T.call("search_materials", {"query": "кратчайший"}) and "Заметки" not in T.call("search_materials", {"query": "маршрутизацию"}))
check("а в самом индексе заметка есть (проверка стенда)", any(e["path"].startswith("Заметки/") for e in ix.search("кратчайший")["results"]))
check("фильтр по курсу", "Физика" not in T.call("search_materials", {"query": "маршрутизации", "course": "101"}) and
      CRS2 in T.call("search_materials", {"query": "маршрутизация", "course": "физика"}))
check("ничего не найдено — вежливая фраза", "ничего не найдено" in T.call("search_materials", {"query": "квантовая хромодинамика"}))
check("limit работает", json.loads(T.call("search_materials", {"query": "маршрутизации", "limit": 1}).split("\n\n", 1)[1])["found"] == 1)
for args in ({}, {"query": ""}, {"query": 5}):
    try:
        T.call("search_materials", args); check("пустой запрос отклонён %r" % (args,), False)
    except M.McpError:
        check("пустой запрос отклонён %r" % (args,), True)
T2 = M.Tools(lambda: BASE / "без-индекса", lambda: CFG); (BASE / "без-индекса").mkdir()
try:
    T2.call("search_materials", {"query": "что-то"}); check("нет индекса: подсказка", False)
except M.McpError as e:
    check("нет индекса: подсказка, что делать", "индекс" in str(e) and "Поиск" in str(e), str(e))

# 6. чтение файла
r = T.call("read_material", {"path": CRS + "/Лекции/ospf.txt"})
head, body = r[len(M.NOTICE) + 2:].split("\n\n", 1)
h = json.loads(head)
check("чтение: заголовок и текст", h["offset"] == 0 and h["total_chars"] > 1000 and "OSPF" in body, head)
r1 = T.call("read_material", {"path": CRS + "/Лекции/ospf.txt", "max_chars": 1000})
h1 = json.loads(r1[len(M.NOTICE) + 2:].split("\n\n", 1)[0])
check("кусками: next_offset указывает дальше", h1["chars"] == 1000 and h1["next_offset"] == 1000, str(h1))
r2 = T.call("read_material", {"path": CRS + "/Лекции/ospf.txt", "offset": h1["next_offset"], "max_chars": 60000})
h2 = json.loads(r2[len(M.NOTICE) + 2:].split("\n\n", 1)[0])
check("последний кусок: next_offset пуст, куски склеиваются в целое", h2["next_offset"] is None and h2["offset"] + h2["chars"] == h2["total_chars"] == h["total_chars"])
check("html читается как текст без тегов", "Протокол между автономными системами" in T.call("read_material", {"path": CRS + "/Лекции/bgp.html"}) and "<h1>" not in T.call("read_material", {"path": CRS + "/Лекции/bgp.html"}))
check("max_chars ограничен сверху", json.loads(T.call("read_material", {"path": CRS + "/Лекции/ospf.txt", "max_chars": 10 ** 9}).split("\n\n")[1])["chars"] <= M.READ_MAX)
check("offset больше длины — пустой кусок, не сбой", "next_offset\": null" in T.call("read_material", {"path": CRS + "/Лекции/ospf.txt", "offset": 10 ** 9}))
for rel, word in ((CRS + "/Лекции/скан.txt", "нет текста"), ("Заметки/Блокнот/x.md", "служебная"), (CRS + "/../../снаружи.txt", "недопустим")):
    try:
        T.call("read_material", {"path": rel}); check("read_material %s" % rel, False)
    except M.McpError as e:
        check("read_material отказ: %s" % word, word in str(e), str(e))
calls = []
_real = search.read_blocks
search.read_blocks = lambda *a, **k: (calls.append(1), _real(*a, **k))[1]
try:
    for off in (0, 1000, 2000):
        T.call("read_material", {"path": CRS + "/Лекции/bgp.html", "offset": off})
finally:
    search.read_blocks = _real
check("файл читается один раз на все куски (кэш)", len(calls) <= 1, str(len(calls)))
cc = []
search.read_blocks = lambda *a, **k: (cc.append((k.get("max_pages"), k.get("max_mb"))), _real(*a, **k))[1]
try:
    T.call("read_material", {"path": CRS + "/Лекции/ospf.txt"})
finally:
    search.read_blocks = _real
check("пределы чтения файла заданы", cc and cc[0] == (M.READ_PAGES, M.READ_MB) or not cc, str(cc))

# 7. заметки
CFG["notes"] = True
ln = json.loads(T.call("list_notes", {}))
check("заметки: две, без текста в списке", ln["total"] == 2 and all("body" not in n for n in ln["notes"]))
check("по курсу и по тексту", [n["title"] for n in json.loads(T.call("list_notes", {"course": "сети"}))["notes"]] == ["Мой конспект по сетям"]
      and json.loads(T.call("list_notes", {"query": "курсовой"}))["total"] == 1)
rn = T.call("read_note", {"id": nb["id"]})
check("чтение заметки: напоминание и текст, без служебной строки", rn.startswith(M.NOTICE) and "Идея про маршрутизацию" in rn and "mo-note" not in rn, rn[:200])
for nid in ("", "нет", "nzzzz", None):
    try:
        T.call("read_note", {"id": nid}); check("read_note(%r)" % nid, False)
    except M.McpError:
        check("read_note(%r) → ошибка" % nid, True)
big = notes.save_note(OUT, title="Длинная", body="я" * (M.NOTE_MAX + 5000), now=1_700_000_200)
check("длинная заметка обрезается с пометкой", "truncated" in T.call("read_note", {"id": big["id"]}) and len(T.call("read_note", {"id": big["id"]})) < M.NOTE_MAX + 2000)
CFG["notes"] = False

# 8. JSON-RPC
def rpc(msg, tools=T):
    return M.handle(msg, tools, "9.9")
ini = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
check("initialize: версия клиента повторяется, возможности «tools», версия программы", ini["result"]["protocolVersion"] == "2025-06-18" and "tools" in ini["result"]["capabilities"] and ini["result"]["serverInfo"]["version"] == "9.9")
check("initialize: неизвестная версия → наша новейшая", rpc({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})["result"]["protocolVersion"] == M.PROTOCOLS[0])
check("initialize без params не падает", rpc({"jsonrpc": "2.0", "id": 3, "method": "initialize"})["result"]["protocolVersion"] == M.PROTOCOLS[0])
check("уведомление — без ответа", rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None)
check("ответ клиента (result без method) — без ответа", rpc({"jsonrpc": "2.0", "id": 5, "result": {}}) is None)
check("ping → пустой результат", rpc({"jsonrpc": "2.0", "id": 4, "method": "ping"})["result"] == {})
tl = rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})["result"]["tools"]
check("tools/list: 4 инструмента с name/description/inputSchema", len(tl) == 4 and all({"name", "description", "inputSchema"} <= set(t) for t in tl))
ca = rpc({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "list_courses", "arguments": {}}})
check("tools/call: content[text] и isError=false", ca["result"]["isError"] is False and ca["result"]["content"][0]["type"] == "text" and "Сети" in ca["result"]["content"][0]["text"])
ce = rpc({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "read_material", "arguments": {"path": "../x.txt"}}})
check("ошибка инструмента — isError=true, а не ошибка протокола", "error" not in ce and ce["result"]["isError"] is True and "недопустим" in ce["result"]["content"][0]["text"])
cn = rpc({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "rm_rf", "arguments": {}}})
check("неизвестный инструмент — ошибка протокола -32602", cn["error"]["code"] == -32602)
check("имя инструмента — не строка: не падает", rpc({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": ["x"]}})["error"]["code"] == -32602)
check("аргументы — не объект: не падает", rpc({"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "list_courses", "arguments": "x"}})["result"]["isError"] is False)
check("неизвестный метод → -32601", rpc({"jsonrpc": "2.0", "id": 12, "method": "resources/list"})["error"]["code"] == -32601)
check("не JSON-RPC 2.0 → -32600", rpc({"id": 1, "method": "ping"})["error"]["code"] == -32600 and rpc([1])["error"]["code"] == -32600 and rpc("x")["error"]["code"] == -32600)
check("метод — не строка → -32600", rpc({"jsonrpc": "2.0", "id": 1, "method": 5})["error"]["code"] == -32600)
check("тело не JSON → 400 и -32700", M.handle_body("{нет".encode("utf-8"), T)[0] == 400 and M.handle_body(b"\xff\xfe", T)[1]["error"]["code"] == -32700)
code, resp = M.handle_body(json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}]).encode(), T)
check("пакет: ответ только на запросы", code == 200 and isinstance(resp, list) and len(resp) == 1)
check("пустой пакет — 400; пакет из одних уведомлений — 202", M.handle_body(b"[]", T)[0] == 400 and M.handle_body(json.dumps([{"jsonrpc": "2.0", "method": "x"}]).encode(), T)[0] == 202)
class Boom(M.Tools):
    def t_list_courses(self, a): raise RuntimeError("секрет-пути /home/x/token")
bm = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_courses"}}, Boom(lambda: OUT, lambda: CFG))
check("сбой инструмента: isError и без текста исключения", bm["result"]["isError"] and "секрет" not in bm["result"]["content"][0]["text"] and "RuntimeError" in bm["result"]["content"][0]["text"])

# 9. HTTP на настоящем сокете
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT, TOKEN = free_port(), "mo_test-token-ONLY-for-checks-0123456789"
STATE = {"token": TOKEN}
LOG = M.AccessLog()
SRV = M.McpServer(PORT, T, lambda: STATE["token"], LOG, "9.9"); SRV.start()
def post(body=None, *, token=TOKEN, headers=None, path="/mcp", method="POST", raw=None, host=None, clen=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    h = {"Content-Type": "application/json"}
    if token is not None: h["Authorization"] = "Bearer " + token
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else b"")
    if clen is not None: h["Content-Length"] = str(clen)
    c.putrequest(method, path, skip_host=host is not None)
    if host is not None: c.putheader("Host", host)
    for k, v in h.items(): c.putheader(k, v)
    if "Content-Length" not in h: c.putheader("Content-Length", str(len(data)))
    c.endheaders(data if clen is None else b"")
    r = c.getresponse(); b = r.read(); hd = dict((k.lower(), v) for k, v in r.getheaders()); c.close()
    return r.status, (json.loads(b) if b else None), hd
PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
check("слушает только 127.0.0.1", SRV.httpd.server_address[0] == "127.0.0.1")
check("порт в Windows занимается эксклюзивно (без SO_REUSEADDR), в Linux — с ним (быстрый перезапуск)", M._Httpd.allow_reuse_address == (os.name != "nt"))
check("верный токен → 200 и JSON-RPC", post(PING)[:2] == (200, {"jsonrpc": "2.0", "id": 1, "result": {}}))
check("без токена → 401", post(PING, token=None)[0] == 401)
check("чужой токен → 401", post(PING, token="mo_wrong")[0] == 401)
check("токен без «Bearer» → 401", post(PING, token=None, headers={"Authorization": TOKEN})[0] == 401)
check("токен в адресе не принимается", post(PING, token=None, path="/mcp?token=" + TOKEN)[0] == 401)
check("уведомление → 202 без тела", post({"jsonrpc": "2.0", "method": "notifications/initialized"})[:2] == (202, None))
check("GET → 405 и Allow: POST", post(None, method="GET")[0] == 405 and post(None, method="GET")[2].get("allow") == "POST")
check("DELETE/PUT → 405", post(None, method="DELETE")[0] == 405 and post(PING, method="PUT")[0] == 405)
st, _b, hd = post(None, method="OPTIONS", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}, token=None)
check("предзапрос CORS (OPTIONS) отклонён, заголовков CORS нет", st == 405 and not any(k.startswith("access-control") for k in hd), str(hd))
check("ответы без CORS-заголовков", not any(k.startswith("access-control") for k in post(PING)[2]))
check("другой адрес → 404", post(PING, path="/api/state")[0] == 404 and post(PING, path="/")[0] == 404)
check("подмена Host (DNS rebinding) → 403 даже с токеном", post(PING, host="evil.example:%d" % PORT)[0] == 403 and post(PING, host="127.0.0.1:1")[0] == 403)
check("Host localhost:порт разрешён", post(PING, host="localhost:%d" % PORT)[0] == 200)
check("Origin чужого сайта → 403 даже с токеном", post(PING, headers={"Origin": "https://evil.example"})[0] == 403 and post(PING, headers={"Origin": "null"})[0] == 403)
check("Origin с хоста localhost проходит (нужен ещё токен)", post(PING, headers={"Origin": "http://localhost:5173"})[0] == 200 and post(PING, headers={"Origin": "http://localhost:5173"}, token=None)[0] == 401)
check("слишком большой запрос → 413 без чтения", post(PING, clen=M.MAX_BODY + 1)[0] == 413)
check("без Content-Length → 411", post(None, raw=b"", headers={"Content-Length": ""})[0] in (411, 400))
check("битый JSON → 400", post(raw="{не json".encode("utf-8"))[0] == 400 and post(raw="{не json".encode("utf-8"))[1]["error"]["code"] == -32700)
check("версия протокола в заголовке не мешает", post(PING, headers={"MCP-Protocol-Version": "2025-06-18"})[0] == 200 and post(PING, headers={"MCP-Protocol-Version": "9999-01-01"})[0] == 200)
st, ans, _h = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_materials", "arguments": {"query": "OSPF"}}})
check("инструмент по HTTP: поиск работает", st == 200 and "ospf.txt" in ans["result"]["content"][0]["text"])
STATE["token"] = "mo_new-token-after-reissue-0123456789"
check("после смены токена старый не работает, новый — да", post(PING)[0] == 401 and post(PING, token=STATE["token"])[0] == 200)
STATE["token"] = ""
check("токен не задан → пускать некого (даже пустой)", post(PING, token="")[0] == 401 and post(PING, token=None)[0] == 401)
STATE["token"] = TOKEN
SRV.fails.clear()
res = []
def worker():
    res.append(post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_courses"}})[0])
ths = [threading.Thread(target=worker) for _ in range(12)]
[t.start() for t in ths]; [t.join() for t in ths]
check("12 одновременных запросов — все 200", res.count(200) == 12, str(res))
# блокировка после серии неверных токенов
SRV.fails.clear()
for _ in range(M.FAIL_LIMIT): post(PING, token="mo_guess")
st, _b, hd = post(PING)
check("после серии неверных токенов — 429 даже с верным, Retry-After", st == 429 and hd.get("retry-after") == str(M.FAIL_WINDOW), str(st))
SRV.fails.clear()
check("после паузы снова пускает", post(PING)[0] == 200)
# журнал
tail = LOG.tail(100); dumped = json.dumps(tail, ensure_ascii=False)
check("в журнале есть вызовы инструментов", any(e["tool"] == "search_materials" and e["arg"] == "OSPF" and e["ok"] and e["via"] == "http" for e in tail), dumped[:300])
check("в журнале нет токенов и текста материалов", TOKEN not in dumped and "mo_" not in dumped and "Маршрутизатор строит карту" not in dumped and "снаружи" not in dumped.lower())
check("в журнале только короткие поля", all(set(e) == {"t", "tool", "arg", "ok", "ms", "chars", "via"} and len(e["arg"]) <= 80 for e in tail))
LOG.add("search_materials", {"query": "я" * 500}, True, 1, 1)
check("длинный запрос в журнале обрезается", len(LOG.tail(1)[0]["arg"]) == 80)
for i in range(M.LOG_KEEP + 20): LOG.add("x", {}, True, 0, 0)
check("журнал не растёт бесконечно", len(LOG.tail(10 ** 6)) == M.LOG_KEEP and LOG.total > M.LOG_KEEP)
LOG.clear(); check("журнал очищается", LOG.tail() == [])
# остановка и повторный запуск
SRV.stop()
try:
    socket.create_connection(("127.0.0.1", PORT), timeout=2).close(); check("после остановки порт закрыт", False)
except OSError:
    check("после остановки порт закрыт", True)
check("сервер помнит, что остановлен", SRV.running is False)
SRV2 = M.McpServer(PORT, T, lambda: TOKEN, LOG, "9.9"); SRV2.start()
check("запускается заново на том же порту", post(PING)[0] == 200)
SRV3 = M.McpServer(PORT, T, lambda: TOKEN, LOG, "9.9")
try:
    SRV3.start(); check("занятый порт → OSError", False)
except OSError:
    check("занятый порт → OSError (причина понятна окну)", True)
SRV2.stop()

# 10. stdio
def stdio(lines):
    rf, wf = io.BytesIO(b"".join(lines)), io.BytesIO()
    rc = M.serve_stdio(T, rf, wf, LOG, "9.9")
    return rc, [json.loads(x) for x in wf.getvalue().decode("utf-8").splitlines()]
J = lambda o: json.dumps(o, ensure_ascii=False).encode("utf-8") + b"\n"
rc, outl = stdio([J({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}),
                  J({"jsonrpc": "2.0", "method": "notifications/initialized"}), b"\n",
                  J({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_materials", "arguments": {"query": "маршрутизации"}}}),
                  "это не json\n".encode("utf-8"), J({"jsonrpc": "2.0", "id": 3, "method": "ping"})])
check("stdio: ответы только на запросы, по строке на ответ, выход на конце ввода", rc == 0 and [o.get("id") for o in outl] == [1, 2, None, 3], str([o.get("id") for o in outl]))
check("stdio: initialize повторяет версию, поиск работает", outl[0]["result"]["protocolVersion"] == "2025-03-26" and "ospf.txt" in outl[1]["result"]["content"][0]["text"])
check("stdio: битая строка → -32700, дальше работает", outl[2]["error"]["code"] == -32700 and outl[3]["result"] == {})
check("stdio: русский текст не экранируется в \\uXXXX", "маршрутизации" in json.dumps(outl, ensure_ascii=False))
check("stdio: вызовы попали в журнал с пометкой stdio", any(e["via"] == "stdio" for e in LOG.tail()))
rc, outl = stdio([b"x" * (M.MAX_BODY + 10) + b"\n"])
check("stdio: слишком длинная строка отклонена, а не съедает память", outl and outl[0]["error"]["code"] == -32600)
check("stdio: заметки выключены → инструментов заметок нет", all(t["name"] not in ("list_notes", "read_note") for t in stdio([J({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})])[1][0]["result"]["tools"]))

# 11. секреты и перенос
check("settings['mcp'] не переносится между устройствами", "mcp" not in transfer.SETTINGS_KEYS and "mcp" not in transfer.safe_settings({"mcp": {"enabled": True, "port": 8787}, "rules": {}}))
src = Path(_root.ROOT / "mcp_server.py").read_text(encoding="utf-8")
check("в mcp_server.py нет обращений к сети, Moodle и записи на диск", not any(w in src for w in ("import requests", "import urllib", "moodle_offline", "open(p, \"w\"", ".write_text(", ".write_bytes(", "os.remove", "unlink(")))

print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

"""Доступ для нейросетей в программе: настройки и токен через /api/mcp*, настоящий MCP-сервер рядом с окном,
токен только в mcp.bin, галочки действуют сразу, порт занят, перезапуск программы. Токены вымышленных нет — настоящие, но временные."""
import http.client, json, os, shutil, socket, stat, sys, tempfile, threading, time, urllib.request, urllib.error
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_mcp_api"
shutil.rmtree(BASE, ignore_errors=True); (BASE / "data").mkdir(parents=True)
OUT = BASE / "out"; (OUT / "Курс Сети" / "Лекции").mkdir(parents=True)
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
(BASE / "data" / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(OUT)}), encoding="utf-8")
(OUT / "Курс Сети" / "Лекции" / "ospf.txt").write_text("OSPF — протокол маршрутизации по состоянию каналов.", encoding="utf-8")
(OUT / "Курс Сети" / ".course.json").write_text(json.dumps({"id": 7, "fullname": "Курс Сети", "dir": "Курс Сети", "sections": [
    {"number": 1, "name": "Лекции", "items": [{"key": "k1", "name": "OSPF", "files": [{"path": "Лекции/ospf.txt", "size": 5}]}]}]}, ensure_ascii=False), encoding="utf-8")

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import gui, mcp_server, notes, transfer
import moodle_offline as mo
from http.server import ThreadingHTTPServer
APP = gui.APP = gui.App()
APP.settings.update({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(OUT)})
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT, TOK = srv.server_address[1], APP.token
notes.save_note(OUT, title="Личное", body="ЛИЧНАЯ ЗАМЕТКА про OSPF", now=1_700_000_000)

def call(path, body=None, token=TOK):
    url = f"http://127.0.0.1:{PORT}{path}" + (("&" if "?" in path else "?") + "t=" + token)
    req = urllib.request.Request(url, data=(json.dumps(body).encode() if body is not None else None), method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"null")

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
def rpc(port, token, method, params=None, rid=1, origin=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Content-Type": "application/json", "Authorization": "Bearer " + token}
    c.request("POST", "/mcp", json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}).encode(), h)
    r = c.getresponse(); b = r.read(); c.close()
    return r.status, (json.loads(b) if b else None)
def tool_names(port, token):
    return sorted(t["name"] for t in rpc(port, token, "tools/list")[1]["result"]["tools"])
def disk():
    return "\n".join(p.read_text(errors="ignore") for p in BASE.rglob("*") if p.is_file() and p.suffix in (".json", ".bin", ".log", ".txt", ".md"))

# 1. исходное состояние
j = call("/api/mcp")[1]
check("сначала выключено, токена нет, порт по умолчанию", not j["cfg"]["enabled"] and not j["running"] and not j["has_token"] and j["cfg"]["port"] == 8787 and j["token_mask"] == "", str(j))
check("материалы по умолчанию открыты, заметки закрыты", j["cfg"]["materials"] is True and j["cfg"]["notes"] is False)
check("окну отдан список инструментов по группам", "search_materials" in j["tools"]["materials"] and j["tools"]["notes"] == ["list_notes", "read_note"])
check("без ключа окна запросы отклоняются (403)", call("/api/mcp", token="x")[0] == 403 and call("/api/mcp/settings", {"enabled": True}, token="x")[0] == 403)
check("исходники: подсказка запуска stdio есть", (j["stdio"] or {}).get("args", [""])[-1] == "--mcp" and j["stdio"]["args"][0].endswith("moodle_offline.py"), str(j["stdio"]))
check("токен не показывается без запроса (только маска, когда он есть)", call("/api/mcp/token", {"action": "show"})[1]["ok"] is False)

# 2. включение
P = free_port()
check("порт 80 отклонён", "Порт" in call("/api/mcp/settings", {"enabled": True, "port": 80})[1]["error"] and call("/api/mcp/settings", {"enabled": True, "port": "abc"})[1]["ok"] is False)
check("после отказа ничего не включилось и токен не создан", not call("/api/mcp")[1]["running"] and not (BASE / "data" / "mcp.bin").exists())
j = call("/api/mcp/settings", {"enabled": True, "port": P})[1]
check("включено: слушает, токен создан", j["ok"] and j["running"] and j["has_token"] and j["error"] == "" and j["url"] == f"http://127.0.0.1:{P}/mcp", str(j)[:300])
tok = call("/api/mcp/token", {"action": "show"})[1]["token"]
check("токен длинный, с префиксом mo_, в маске середины нет", tok.startswith("mo_") and len(tok) > 40 and j["token_mask"] and tok not in json.dumps(j) and tok[8:-5] not in j["token_mask"])
check("токен лежит в mcp.bin, а не в settings.json и других файлах", tok in (BASE / "data" / "mcp.bin").read_text(errors="ignore") and tok not in (BASE / "data" / "settings.json").read_text())
others = "\n".join(p.read_text(errors="ignore") for p in BASE.rglob("*") if p.is_file() and p.name != "mcp.bin" and p.suffix in (".json", ".bin", ".log", ".txt", ".md"))
check("и больше нигде на диске его нет", tok not in others)
if os.name != "nt":
    check("mcp.bin доступен только владельцу (chmod 600)", stat.S_IMODE((BASE / "data" / "mcp.bin").stat().st_mode) == 0o600)
check("в «Перенос данных» настройки доступа не попадают", "mcp" not in transfer.safe_settings(APP.settings) and tok not in json.dumps(transfer.safe_settings(APP.settings)))
check("«Выйти и забыть вход» токен не стирает", (APP.vault.forget(), (BASE / "data" / "mcp.bin").exists())[1])

# 3. работа сервера и разделение секретов
check("MCP отвечает с токеном", rpc(P, tok, "ping")[0] == 200)
check("ключ окна на MCP-сервере не годится", rpc(P, TOK, "ping")[0] == 401)
check("токен MCP не годится для API окна", call("/api/state", token=tok)[0] == 403 and call("/api/mcp", token=tok)[0] == 403)
check("порт окна MCP-сервером не является", rpc(PORT, tok, "ping")[0] in (403, 404, 405, 401))
s, ans = rpc(P, tok, "tools/call", {"name": "list_courses", "arguments": {}})
check("список курсов через MCP", s == 200 and "Курс Сети" in ans["result"]["content"][0]["text"])
check("без Moodle: вход и сеть не нужны (состояние входа не менялось)", APP.login_state in ("idle", "offline") and APP.client is None)

# 4. галочки действуют сразу, без перезапуска
check("заметки закрыты: инструментов заметок нет", tool_names(P, tok) == ["list_course_files", "list_courses", "read_material", "search_materials"], str(tool_names(P, tok)))
s, ans = rpc(P, tok, "tools/call", {"name": "list_notes", "arguments": {}})
check("вызов закрытого инструмента — ошибка с пояснением", ans["result"]["isError"] and "выключен" in ans["result"]["content"][0]["text"])
j = call("/api/mcp/settings", {"notes": True})[1]
check("заметки включены: порт тот же, сервер не перезапускался", j["ok"] and j["running"] and j["cfg"]["notes"] and j["cfg"]["port"] == P)
check("инструменты заметок появились сразу", "list_notes" in tool_names(P, tok) and "read_note" in tool_names(P, tok))
s, ans = rpc(P, tok, "tools/call", {"name": "list_notes", "arguments": {}})
check("заметки читаются", "Личное" in ans["result"]["content"][0]["text"])
j = call("/api/mcp/settings", {"notes": False, "materials": False})[1]
check("всё закрыто: инструментов нет совсем, но сервер жив", tool_names(P, tok) == [] and j["running"])
call("/api/mcp/settings", {"materials": True})
check("материалы снова открыты", "search_materials" in tool_names(P, tok))

# 5. новый токен
j = call("/api/mcp/token", {"action": "new"})[1]
tok2 = j["token"]
check("новый токен выпущен, старый перестал работать сразу, новый работает", j["ok"] and tok2 != tok and rpc(P, tok, "ping")[0] == 401 and rpc(P, tok2, "ping")[0] == 200)
check("в файле — только новый", tok2 in (BASE / "data" / "mcp.bin").read_text(errors="ignore") and tok not in disk())

# 6. журнал
rpc(P, tok2, "tools/call", {"name": "search_materials", "arguments": {"query": "OSPF"}})
j = call("/api/mcp")[1]
check("журнал: последние обращения без токена и текста", any(e["tool"] == "search_materials" and e["arg"] == "OSPF" for e in j["log"]) and tok2 not in json.dumps(j) and "протокол маршрутизации" not in json.dumps(j) and j["total"] >= 3)
call("/api/mcp/log_clear", {})
check("журнал очищается", call("/api/mcp")[1]["log"] == [])

# 7. порт занят
busy = socket.socket(); busy.bind(("127.0.0.1", 0)); busy.listen(1); BP = busy.getsockname()[1]
j = call("/api/mcp/settings", {"port": BP})[1]
check("порт занят: сервер не работает, причина понятна, прежний закрыт", j["ok"] and not j["running"] and "занят" in j["error"] and str(BP) in j["error"], str(j.get("error")))
try:
    socket.create_connection(("127.0.0.1", P), timeout=1).close(); old_open = True
except OSError:
    old_open = False
check("прежний порт при смене закрыт", not old_open)
busy.close()
j = call("/api/mcp/settings", {"port": BP})[1]
check("порт освободился — включается тем же действием", j["running"] and j["error"] == "" and rpc(BP, tok2, "ping")[0] == 200)

# 8. выключение
j = call("/api/mcp/settings", {"enabled": False})[1]
try:
    socket.create_connection(("127.0.0.1", BP), timeout=1).close(); still = True
except OSError:
    still = False
check("выключено: порт закрыт, токен остался (включить снова — тот же)", not j["running"] and not still and j["has_token"] and APP.mcp_token() == tok2)

# 9. перезапуск программы: настройки и токен сохранились, порт ждёт освобождения
j = call("/api/mcp/settings", {"enabled": True, "port": BP})[1]
APP.mcp.stop(); APP.mcp = None
APP2 = gui.App()
check("новая копия читает те же настройки и токен", APP2.mcp_cfg()["enabled"] and APP2.mcp_cfg()["port"] == BP and APP2.mcp_token() == tok2)
hold = socket.socket(); hold.bind(("127.0.0.1", BP)); hold.listen(1)         # прежняя копия ещё не отпустила порт
th = threading.Thread(target=lambda: APP2.mcp_apply(wait=6.0)); th.start()
time.sleep(1.2); hold.close(); th.join(10)
check("порт освободился во время ожидания — сервер встал сам", APP2.mcp and APP2.mcp.running and rpc(BP, tok2, "ping")[0] == 200 and APP2.mcp_error == "")
APP2.mcp.stop()
APP3 = gui.App(); APP3.settings["mcp"] = {"enabled": True, "port": P}
(BASE / "data" / "mcp.bin").unlink()
APP3.mcp_apply()
check("включено, но токена нет (файл пропал): сервер не открывается без защиты", not (APP3.mcp and APP3.mcp.running) and "токен" in APP3.mcp_error.lower())

check("ошибок внутри программы не было", not (BASE / "data" / "errors.log").exists(), (BASE / "data" / "errors.log").read_text() if (BASE / "data" / "errors.log").exists() else "")
print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

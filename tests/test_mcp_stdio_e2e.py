"""`python moodle_offline.py --mcp` — настоящий процесс: в stdout только ответы протокола, галочки из settings.json
действуют сразу, выход на конце ввода. Данные и тексты вымышленные."""
import json, os, re, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_mcp_stdio"
shutil.rmtree(BASE, ignore_errors=True); (BASE / "data").mkdir(parents=True)
OUT = BASE / "out"; (OUT / "Курс Сети" / "Лекции").mkdir(parents=True)
(OUT / "Курс Сети" / "Лекции" / "ospf.txt").write_text("OSPF — протокол маршрутизации по состоянию каналов.", encoding="utf-8")
(OUT / "Курс Сети" / ".course.json").write_text(json.dumps({"id": 7, "fullname": "Курс Сети", "sections": []}, ensure_ascii=False), encoding="utf-8")
SET = BASE / "data" / "settings.json"
def setcfg(mcp):
    SET.write_text(json.dumps({"out_dir": str(OUT), "mcp": mcp}), encoding="utf-8")
setcfg({"enabled": False, "notes": False})

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

env = {**os.environ, "MOODLE_OFFLINE_DATA": str(BASE / "data"), "PYTHONIOENCODING": "utf-8"}
P = subprocess.Popen([sys.executable, str(_root.ROOT / "moodle_offline.py"), "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, env=env, cwd=str(BASE))
err = []
threading.Thread(target=lambda: err.append(P.stderr.read()), daemon=True).start()
def ask(method, params=None, rid=1):
    P.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, ensure_ascii=False).encode("utf-8") + b"\n"); P.stdin.flush()
    line = P.stdout.readline()
    return json.loads(line)
try:
    r = ask("initialize", {"protocolVersion": "2025-06-18"})
    check("initialize по настоящему процессу", r["result"]["serverInfo"]["name"] == "moodle-offline" and r["result"]["protocolVersion"] == "2025-06-18", str(r))
    P.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); P.stdin.flush()
    names = lambda: sorted(t["name"] for t in ask("tools/list", rid=2)["result"]["tools"])
    check("общий выключатель окна для stdio не нужен: материалы доступны", names() == ["list_course_files", "list_courses", "read_material", "search_materials"], str(names()))
    r = ask("tools/call", {"name": "read_material", "arguments": {"path": "Курс Сети/Лекции/ospf.txt"}}, 3)
    check("чтение файла по stdio (русский без \\u-экранирования потерь)", "протокол маршрутизации" in r["result"]["content"][0]["text"])
    r = ask("tools/call", {"name": "read_material", "arguments": {"path": "../../data/settings.json"}}, 4)
    check("выход из папки материалов закрыт", r["result"]["isError"] and "mcp" not in r["result"]["content"][0]["text"])
    setcfg({"enabled": False, "notes": True, "materials": False})
    check("галочки из settings.json действуют сразу, без перезапуска процесса", names() == ["list_notes", "read_note"], str(names()))
    setcfg({"notes": False, "materials": False})
    check("всё закрыто — инструментов нет", names() == [])
    P.stdin.close()
    try:
        rc = P.wait(10)
    except subprocess.TimeoutExpired:
        P.kill(); rc = None
    check("конец ввода — чистый выход с кодом 0", rc == 0, str(rc))
    rest = P.stdout.read()
    check("в stdout ничего, кроме ответов протокола", rest == b"", repr(rest[:200]))
    time.sleep(0.2)
    e = b"".join(err).decode("utf-8", "replace")
    check("служебные сообщения — в stderr, пароля и токенов там нет", "MCP по stdio" in e and not re.search(r"mo_[A-Za-z0-9_-]{20,}", e), e[:200])
finally:
    if P.poll() is None: P.kill()
print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

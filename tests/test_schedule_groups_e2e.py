"""Расписание: своя группа и чужие группы через окно программы (настоящий локальный сервер gui.py + макет schedule.sevsu.ru).
Ошибки, которые здесь ловятся (1.27.0):
- у нового пользователя «своей» по умолчанию была группа автора программы (ИБ/б-25-2-о);
- после смены своей группы пары прежней группы оставались в расписании (и её название — в заголовке);
- группа, которой нет на сайте, оставляла пустую запись в списке «Другие группы»."""
import os, sys, json, shutil, tempfile, threading, time, urllib.request, urllib.error
from pathlib import Path
import _root
from mock_schedule import MockSchedule, patch, GROUPS

BASE = Path(tempfile.gettempdir()) / "mo_sched_groups"
shutil.rmtree(BASE, ignore_errors=True); (BASE / "data").mkdir(parents=True)
OUT = BASE / "out"; OUT.mkdir()
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
BASE_SETTINGS = {"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(OUT),
                 "page_delay": "0", "schedule_pdf": False, "schedule_sources": ["schedule"]}
(BASE / "data" / "settings.json").write_text(json.dumps(BASE_SETTINGS), encoding="utf-8")

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import moodle_offline as mo, schedule as sched, sch_book as book, gui
mo.find_browser = lambda *a, **k: None          # без фонового браузера: снимки PDF и рендер страниц в тесте не нужны
M = MockSchedule().start(); patch(sched, M)
APP = gui.APP = gui.App()
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT, TOK = srv.server_address[1], APP.token

def call(path, body=None):
    url = f"http://127.0.0.1:{PORT}{path}" + ("&" if "?" in path else "?") + "t=" + TOK
    req = urllib.request.Request(url, data=(json.dumps(body).encode() if body is not None else None), method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e: return json.loads(e.read() or b"null")

def wait_idle(secs=60):
    for _ in range(secs * 10):
        if APP.job_state == "idle": return APP.last_result or {}
        time.sleep(0.1)
    return {"timeout": True}

def subjects(rel):
    d = json.loads((OUT / rel / "schedule.json").read_text(encoding="utf-8"))
    return {le["subject"].split(" · ")[-1] for le in d["lessons"]}, d["meta"].get("group", "")

try:
    # 1. новый пользователь: группа не выбрана — не подставляем чужую (автора программы)
    s = call("/api/schedule")
    check("новый пользователь: своя группа не выбрана (раньше подставлялась ИБ/б-25-2-о)", s.get("group") == "" and s.get("need_group") is True, f"group={s.get('group')!r} need={s.get('need_group')!r}")
    check("…и в списке расписаний «Моё» без чужого названия", (s.get("profiles") or [{}])[0].get("title") in ("", "Моя группа"), str((s.get("profiles") or [{}])[0].get("title")))
    r = call("/api/schedule/save", {"start": True, "range": "2weeks"})
    check("без группы «Сохранить для офлайна» просит выбрать группу, а не качает чужую", r.get("ok") is False and "групп" in (r.get("error") or ""), str(r))

    # 2. своя группа ИБ/б-25-2-о: выбрали и сразу скачали
    r = call("/api/schedule/save", {"schedule_group": "ИБ/б-25-2-о", "start": True, "range": "month"})
    check("выбор своей группы запускает скачивание", r.get("ok") is True, str(r))
    res = wait_idle()
    got, name = subjects("Расписание")
    check("своя группа скачана: только её пары", res.get("ok") and got == {"ИБ/б-25-2-о"}, f"{res} {got}")

    # 3. сменили свою группу на ПИ/б-24-1-о: пока не скачали — окно знает, что сохранено расписание прежней группы
    r = call("/api/schedule/save", {"schedule_group": "ПИ/б-24-1-о"})
    s = call("/api/schedule")
    check("после смены группы окно видит, что сохранено расписание прежней", s.get("stale_group") == "ИБ/б-25-2-о", str(s.get("stale_group")))
    check("…а «Моё» в списке подписано выбранной группой", s["profiles"][0]["title"] in ("ПИ/б-24-1-о", "pi-b-24-1-o"), s["profiles"][0]["title"])
    r = call("/api/schedule/save", {"start": True, "range": "2weeks"}); wait_idle()
    got, name = subjects("Расписание")
    check("после скачивания новой группы пар прежней (ИБ) не осталось", got == {"ПИ/б-24-1-о"}, str(got))
    check("и в заголовке страницы — новая группа", name == "ПИ/б-24-1-о", name)
    check("признак «сохранена прежняя группа» снят", not call("/api/schedule").get("stale_group"))
    # личное расписание (timetable) прежней группы тоже не подмешивается
    tt = OUT / "Расписание" / "data" / "timetable.json"
    tt.write_text(json.dumps({"source": "timetable", "group": "ib-b-25-2-o", "covered": [["2026-11-16", "2026-11-22"]],
                              "lessons": [{"date": "2026-11-18", "start": "17:20", "end": "18:50", "subject": "Чужая пара · ИБ/б-25-2-о", "type": "Лекция", "src": ["timetable"]}]},
                             ensure_ascii=False), encoding="utf-8")
    sched.build_outputs(OUT / "Расписание", "pi-b-24-1-o", None, own=True)
    got, _ = subjects("Расписание")
    check("файл источника, записанный для прежней группы, при сборке пропускается", got == {"ПИ/б-24-1-о"}, str(got))

    # 4. чужая группа: добавилась, скачалась, своё не тронуто
    r = call("/api/schedule/other/save", {"group": "ИБ/б-25-1-о", "range": "2weeks"})
    check("чужая группа добавляется", r.get("ok") and r.get("slug") == "ib-b-25-1-o", str(r))
    res = wait_idle()
    got, _ = subjects("Расписание/Другие группы/ib-b-25-1-o")
    check("у чужой группы — только её пары", res.get("ok") and got == {"ИБ/б-25-1-о"}, f"{res} {got}")
    check("своё расписание при этом не изменилось", subjects("Расписание")[0] == {"ПИ/б-24-1-о"})
    ids = [p["id"] for p in call("/api/schedule")["profiles"]]
    check("в списке: своё и добавленная группа", ids == ["own", "ib-b-25-1-o"], str(ids))
    check("свою группу как «чужую» добавить нельзя", call("/api/schedule/other/save", {"group": "ПИ/б-24-1-о"}).get("ok") is False)

    # 5. группы нет на сайте — понятная ошибка, и пустой записи в списке не остаётся
    r = call("/api/schedule/other/save", {"group": "XX/б-99-9-о", "range": "2weeks"})
    res = wait_idle()
    check("несуществующая группа: загрузка заканчивается ошибкой", res.get("ok") is False, str(res))
    check("…с понятным текстом (группы нет на сайте)", "не найден" in (res.get("text") or "") or "нет на сайте" in (res.get("text") or ""), res.get("text", ""))
    ids = [p["id"] for p in call("/api/schedule")["profiles"]]
    check("…и пустая запись не остаётся в списке", ids == ["own", "ib-b-25-1-o"], str(ids))
    check("…и папки для неё не осталось", not (OUT / "Расписание" / "Другие группы" / "xx-b-99-9-o").exists())

    # 5б. своя группа с опечаткой: понятная ошибка, а не «сохранено 0 пар»
    call("/api/schedule/save", {"schedule_group": "ЯЯ/б-00-0-о", "start": True, "range": "2weeks"}); res = wait_idle()
    check("своя группа, которой нет на сайте: загрузка — ошибка с понятным текстом", res.get("ok") is False and "нет на сайте" in res.get("text", ""), str(res))
    call("/api/schedule/save", {"schedule_group": "ПИ/б-24-1-о"})

    # 6. старый пользователь, который группу явно не выбирал (раньше работала подстановка ИБ): группа берётся из сохранённого расписания
    APP.settings.pop("schedule_group", None); APP.save_settings()
    d = OUT / "Расписание" / "data" / "schedule.json"; j = json.loads(d.read_text(encoding="utf-8")); j["group"] = "ib-b-25-2-o"; d.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    s = call("/api/schedule")
    check("группа без явного выбора берётся из сохранённого расписания (старые установки не теряют своё)", s.get("group") == "ib-b-25-2-o" and APP.settings.get("schedule_group") == "ib-b-25-2-o", str(s.get("group")))
finally:
    M.stop()
print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

# -*- coding: utf-8 -*-
import json, os, re, sys, tempfile
from datetime import datetime, timedelta
from pathlib import Path
import _root
import deadlines as dl
import schedule as sched

FAIL = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond: FAIL.append(name)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=dl.MSK).timestamp()
def at(days=0, hh=23, mm=59):
    d = datetime(2026, 11, 11, hh, mm, tzinfo=dl.MSK) + timedelta(days=days)
    return int(d.timestamp())

def raw(i, name, ts, etype="due", mod="assign", cid=3594, **kw):
    e = {"id": i, "name": name, "activityname": kw.pop("activityname", ""), "modulename": mod, "eventtype": etype,
         "timestart": ts, "timesort": ts, "timeduration": 0, "description": "", "course": {"id": cid, "fullname": "Курс " + str(cid), "shortname": "К" + str(cid)},
         "action": {"name": "Добавить ответ на задание", "url": f"https://do.sevsu.ru/mod/assign/view.php?id={i}&action=editsubmission", "actionable": True},
         "url": "https://do.sevsu.ru/calendar/view.php?view=day"}
    e.update(kw)
    return e

# ── настройки ──
n = dl.normalize({"remind": "60, 1440; 60 , 99999, x", "days_ahead": 9999, "days_back": -5, "lesson_remind": "abc", "kinds": ["open", "zzz"],
                  "exclude_courses": ["5", "x", 7], "auto": 0})
check("normalize: remind из строки, дедуп, порядок, отброс мусора", n["remind"] == [1440, 60], n["remind"])
check("normalize: границы", n["days_ahead"] == 365 and n["days_back"] == 0 and n["lesson_remind"] == 15, n)
check("normalize: kinds только известные", n["kinds"] == ["open"], n["kinds"])
check("normalize: exclude_courses числа", n["exclude_courses"] == [5, 7], n["exclude_courses"])
check("normalize: bool", n["auto"] is False)
check("normalize: пустой remind разрешён", dl.normalize({"remind": []})["remind"] == [])
check("normalize: не dict", dl.normalize("x") == dl.normalize({}))
check("normalize: максимум 4 напоминаний", len(dl.normalize({"remind": [1, 2, 3, 4, 5, 6]})["remind"]) == 4)

# ── разбор события ──
e = dl.from_ajax(raw(11, "Практическая работа №1 должен быть выполнен", at(1)))
check("from_ajax: суффикс «должен быть выполнен» убран", e and e["title"] == "Практическая работа №1", e and e["title"])
e2 = dl.from_ajax(raw(12, "Тест 3 закрывается", at(2), etype="close", mod="quiz", activityname="Тест 3"))
check("from_ajax: activityname главнее", e2["title"] == "Тест 3" and e2["kind"] == "deadline" and e2["label"] == "закрывается", e2)
check("from_ajax: gradingdue отброшен", dl.from_ajax(raw(13, "x", at(1), etype="gradingdue")) is None)
check("from_ajax: нет id / нет времени", dl.from_ajax({"name": "x", "timestart": 5}) is None and dl.from_ajax({"id": 3}) is None)
check("from_ajax: не dict", dl.from_ajax("x") is None and dl.from_ajax(None) is None)
e3 = dl.from_ajax(raw(14, "Колл", at(1), etype="user", mod=""))
check("from_ajax: личное → events", e3["kind"] == "events" and e3["label"] == "личное событие", e3)
e4 = dl.from_ajax(raw(15, "Откр", at(1), etype="open", mod="quiz"))
check("from_ajax: open / expectcompletionon", e4["kind"] == "open" and dl.from_ajax(raw(16, "z", at(1), etype="expectcompletionon"))["kind"] == "completion")
check("from_ajax: неизвестный тип с модулем → deadline", dl.from_ajax(raw(17, "z", at(1), etype="weird", mod="workshop"))["kind"] == "deadline")
e5 = dl.from_ajax(raw(18, "Y", at(1), action={"name": "A", "url": "https://do.sevsu.ru/m?id=1&sesskey=SECRETKEY"}))
check("from_ajax: sesskey вырезан из ссылки", "SECRETKEY" not in e5["url"] and "id=1" in e5["url"], e5["url"])
e6 = dl.from_ajax(raw(19, "Y", at(1), action={"name": "A", "url": "javascript:alert(1)"}, url="data:text/html,x"))
check("from_ajax: небезопасные ссылки выброшены", e6["url"] == "", e6["url"])
e7 = dl.from_ajax(raw(20, "Y", at(1), description="<p>Сдать <b>отчёт</b>&nbsp;в PDF</p><script>evil()</script><br>Срок жёсткий"))
check("from_ajax: описание без HTML", e7["desc"] == "Сдать отчёт в PDF Срок жёсткий", e7["desc"])
check("from_ajax: строка в timestart", dl.from_ajax(raw(21, "q", str(at(1))))["due"] == at(1))
check("from_ajax: мусор в timestart", dl.from_ajax(raw(22, "q", "abc", timesort=0)) is None)
check("from_ajax: длительность → end", dl.from_ajax(raw(23, "q", at(1), timeduration=3600))["end"] == at(1) + 3600)
check("parse_events: дубли id схлопнуты", len(dl.parse_events([raw(1, "a", at(1)), raw(1, "a", at(1)), raw(2, "b", at(2))])) == 2)

# ── время ──
check("left_text: часы", dl.left_text(NOW + 5 * 3600, NOW) == "через 5 ч")
check("left_text: часы и минуты", dl.left_text(NOW + 2 * 3600 + 20 * 60, NOW) == "через 2 ч 20 мин")
check("left_text: дни", dl.left_text(NOW + 3 * 86400, NOW) == "через 3 дня" and dl.left_text(NOW + 5 * 86400, NOW) == "через 5 дней" and dl.left_text(NOW + 21 * 86400, NOW) == "через 21 день")
check("left_text: просрочено", dl.left_text(NOW - 90 * 60, NOW) == "просрочено на 1 ч 30 мин" and dl.left_text(NOW - 30 * 60, NOW) == "просрочено на 30 мин")
check("left_text: секунды", dl.left_text(NOW + 20, NOW) == "сейчас" and dl.left_text(NOW - 20, NOW) == "только что истёк")
check("fmt_dt", dl.fmt_dt(at(0), NOW) == "ср 11 ноя, 23:59", dl.fmt_dt(at(0), NOW))
check("fmt_dt: чужой год", dl.fmt_dt(at(60), NOW).endswith("2027, 23:59") or "2027" in dl.fmt_dt(at(60), NOW), dl.fmt_dt(at(60), NOW))
check("fmt_day", dl.fmt_day(datetime(2026, 11, 11).date(), datetime(2026, 11, 11).date()).startswith("Сегодня · Среда, 11 ноября"))

# ── хранилище ──
tmp = Path(tempfile.mkdtemp())
P = tmp / ".moodle_deadlines.json"
st = dl.Store(P)
check("store: первая синхронизация", st.first_sync())
lo, hi = NOW - 14 * 86400, NOW + 120 * 86400
f1 = dl.parse_events([raw(1, "ПР1", at(1)), raw(2, "ЛР1", at(3)), raw(3, "Тест", at(10), etype="close", mod="quiz")])
r = st.merge(f1, NOW, lo, hi, source="timeline")
check("merge: всё новое", len(r["new"]) == 3 and not r["moved"] and not r["gone"], r)
check("merge: sync записан", not st.first_sync() and st.sync["count"] == 3 and st.sync["ok"])
# перенос + пропажа + новый
f2 = dl.parse_events([raw(1, "ПР1", at(2)), raw(3, "Тест", at(10), etype="close", mod="quiz"), raw(4, "ПР2", at(5))])
r = st.merge(f2, NOW + 600, lo, hi, source="timeline")
check("merge: перенос замечен", len(r["moved"]) == 1 and r["moved"][0][0] == at(1) and r["moved"][0][1]["due"] == at(2), r["moved"])
check("merge: пропавший помечен gone", [e["id"] for e in r["gone"]] == ["m2"] and st.events["m2"]["gone"], r["gone"])
check("merge: новый", [e["id"] for e in r["new"]] == ["m4"])
check("merge: first сохраняется", st.events["m1"]["first"] == int(NOW))
# возвращение
r = st.merge(dl.parse_events([raw(2, "ЛР1", at(3))]), NOW + 1200, lo, hi)
check("merge: вернувшийся не gone, в back", not st.events["m2"]["gone"] and [e["id"] for e in r["back"]] == ["m2"], r["back"])
# неполный ответ ничего не помечает
st2 = dl.Store(tmp / "inc.json")
st2.merge(f1, NOW, lo, hi)
r = st2.merge([], NOW + 60, lo, hi, complete=False)
check("merge: неполный ответ не помечает gone", not r["gone"] and all(not e["gone"] for e in st2.events.values()))
# пустой ответ при ≥3 известных — подозрительно
r = st2.merge([], NOW + 120, lo, hi, complete=True)
check("merge: пустой ответ при 3 известных — suspicious", r["suspicious"] and not r["gone"], r)
st3 = dl.Store(tmp / "one.json")
st3.merge(dl.parse_events([raw(1, "A", at(1))]), NOW, lo, hi)
r = st3.merge([], NOW + 60, lo, hi)
check("merge: единственный срок пропал → gone (сдано)", [e["id"] for e in r["gone"]] == ["m1"] and not r["suspicious"])
# срок вне окна не трогаем
st4 = dl.Store(tmp / "win.json")
st4.merge(dl.parse_events([raw(1, "Далеко", at(200))]), NOW, lo, hi)
r = st4.merge([], NOW + 60, lo, hi)
check("merge: за пределами окна не помечается", not r["gone"])
# прошедший срок не помечается gone
st5 = dl.Store(tmp / "past.json")
st5.merge(dl.parse_events([raw(1, "Был", int(NOW - 3600))]), NOW - 7200, lo, hi)
r = st5.merge([], NOW, lo, hi)
check("merge: истёкший срок не gone", not r["gone"])
# done и перезагрузка из файла
check("set_done", st.set_done("m1", True, NOW) and "m1" in st.done and not st.set_done("nope", True))
st_re = dl.Store(P)
check("store: перечитывание файла", st_re.done.get("m1") and len(st_re.events) == 4 and st_re.events["m1"]["due"] == at(2))
st_re.set_done("m1", False)
st.refresh()
check("store: другая копия видит изменение", "m1" not in st.done)
# битый файл
bad = tmp / "bad.json"; bad.write_text("{не json", encoding="utf-8")
sb = dl.Store(bad)
check("store: битый файл → .bad и пустое хранилище", not sb.events and (tmp / "bad.json.bad").exists())
# prune
st6 = dl.Store(tmp / "prune.json")
st6.merge(dl.parse_events([raw(1, "Старый", int(NOW - 400 * 86400)), raw(2, "Свежий", at(1))]), NOW, lo, hi)
check("prune: старше KEEP_DAYS забыт", list(st6.events) == ["m2"], list(st6.events))
# разные процессы: хранилище обновилось снаружи
a = dl.Store(tmp / "shared.json"); b = dl.Store(tmp / "shared.json")
a.merge(dl.parse_events([raw(1, "A", at(1))]), NOW, lo, hi)
b.merge(dl.parse_events([raw(1, "A", at(1)), raw(2, "B", at(2))]), NOW + 5, lo, hi)
check("store: два процесса не теряют данные", set(a.events) == {"m1", "m2"})

# ── события для ленты ──
cfg = dl.normalize({})
st7 = dl.Store(tmp / "news.json")
r = st7.merge(f1, NOW, lo, hi)
check("change_events: первая синхронизация молчит о новых", dl.change_events(r, NOW, cfg, announce_new=False) == [])
r = st7.merge(f2, NOW + 60, lo, hi)
evs = dl.change_events(r, NOW + 60, cfg)
kinds = sorted(e["kind"] for e in evs)
check("change_events: новый и перенос", kinds == ["deadline_moved", "deadline_new"], kinds)
mv = [e for e in evs if e["kind"] == "deadline_moved"][0]
check("change_events: текст переноса", "перенесено с" in mv["detail"] and "на" in mv["detail"], mv["detail"])
check("change_events: notify=False — тишина", dl.change_events(r, NOW, dict(cfg, notify=False)) == [])
r2 = {"new": [dl.parse_events([raw(9, "Не нужно", at(1), etype="user", mod="")])[0]], "moved": []}
check("change_events: личные события не в ленту", dl.change_events(r2, NOW, cfg) == [])
r3 = {"new": [dl.parse_events([raw(9, "Чужой", at(1), cid=77)])[0]], "moved": []}
check("change_events: исключённый курс молчит", dl.change_events(r3, NOW, dict(cfg, exclude_courses=[77])) == [])
check("change_events: id детерминирован", dl.change_events(r3, NOW, cfg)[0]["id"] == dl.change_events(r3, NOW, cfg)[0]["id"])

# ── «горит» ──
ss = dl.Store(tmp / "soon.json")
ss.merge(dl.parse_events([raw(1, "Через 10 ч", int(NOW + 10 * 3600)), raw(2, "Через 2 ч", int(NOW + 2 * 3600)),
                          raw(3, "Через 5 дней", int(NOW + 5 * 86400)), raw(4, "Просрочен", int(NOW - 3600)),
                          raw(5, "Личное", int(NOW + 3600), etype="user", mod="")]), NOW, lo, hi)
s1 = dl.soon_events(ss, cfg, NOW)
titles = sorted(e["title"] for e in s1)
check("soon: только пройденные пороги", titles == ["Через 10 ч", "Через 2 ч"], titles)
check("soon: повторно не напоминает", dl.soon_events(ss, cfg, NOW + 60) == [])
s2 = dl.soon_events(ss, cfg, NOW + 8 * 3600 + 60)      # «Через 10 ч» стало «через 1 ч 59 мин» → порог 3 ч
check("soon: следующий порог срабатывает", [e["title"] for e in s2] == ["Через 10 ч"], [e["title"] for e in s2])
check("soon: и снова тишина", dl.soon_events(ss, cfg, NOW + 8 * 3600 + 120) == [])
# приложение было закрыто: сразу тесный порог один раз
sx = dl.Store(tmp / "soon2.json")
sx.merge(dl.parse_events([raw(1, "Долго спал", int(NOW + 30 * 60))]), NOW, lo, hi)
sy = dl.soon_events(sx, cfg, NOW)
check("soon: один сигнал по самому тесному порогу", len(sy) == 1 and "30 мин" in sy[0]["detail"], sy)
# сделано / gone / перенос
sx.set_done("m1", True, NOW)
sz = dl.Store(tmp / "soon3.json")
sz.merge(dl.parse_events([raw(1, "A", int(NOW + 3600)), raw(2, "B", int(NOW + 3600))]), NOW, lo, hi)
sz.set_done("m1", True, NOW)
check("soon: сделанное молчит", [e["title"] for e in dl.soon_events(sz, cfg, NOW)] == ["B"])
sz.merge(dl.parse_events([raw(2, "B", int(NOW + 90 * 86400 // 90 * 0 + 7200 * 20))]), NOW + 10, lo, hi)   # B перенесли на 40 ч вперёд
sz.merge(dl.parse_events([raw(2, "B", int(NOW + 3000))]), NOW + 20, lo, hi)                              # и обратно на ~50 мин
check("soon: после переноса порог снова взведён", len(dl.soon_events(sz, cfg, NOW + 30)) == 1)
check("soon: remind пуст — тишина", dl.soon_events(dl.Store(tmp / "soon3.json"), dict(cfg, remind=[]), NOW) == [])
sm = dl.Store(tmp / "soon4.json"); sm.merge(dl.parse_events([raw(1, "M", int(NOW + 3600))]), NOW, lo, hi)
check("soon: mark=False не запоминает", len(dl.soon_events(sm, cfg, NOW, mark=False)) == 1 and len(dl.soon_events(sm, cfg, NOW, mark=False)) == 1 and len(dl.soon_events(sm, cfg, NOW)) == 1 and not dl.soon_events(sm, cfg, NOW))

# ── повестка ──
lessons = [{"date": "2026-11-11", "start": "14:00", "end": "15:30", "subject": "Высшая математика", "type": "Лекция", "room": "А-101", "teacher": "Иванов И.И."},
           {"date": "2026-11-11", "start": "08:30", "end": "10:00", "subject": "Физика", "type": "ЛЗ"},
           {"date": "2026-11-12", "start": "10:10", "end": "11:40", "subject": "Информатика", "type": "Практика (ПЗ)", "subgroup": "1"},
           {"date": "2026-11-01", "start": "10:10", "end": "11:40", "subject": "Давно", "type": "Лекция"},
           {"date": "2026-12-30", "start": "10:10", "end": "11:40", "subject": "Далеко", "type": "Лекция"}]
sv = dl.Store(tmp / "view.json")
sv.merge(dl.parse_events([raw(1, "Просрочено вчера", at(-1, 23, 59)), raw(2, "Сегодня вечером", at(0)), raw(3, "Завтра", at(1)),
                          raw(4, "Через неделю", at(6)), raw(5, "Другой курс", at(2), cid=55), raw(6, "Очень давно", at(-30)),
                          raw(7, "Через 10 дней", at(10))]), NOW, lo, hi)
sv.set_done("m4", True, NOW)
v = dl.view(sv, lessons, cfg, NOW, days=14)
check("view: просрочено — одно (давнее за days_back не берём)", [i["title"] for i in v["overdue"]] == ["Просрочено вчера"], [i["title"] for i in v["overdue"]])
check("view: счётчики", v["counts"]["overdue"] == 1 and v["counts"]["today"] == 1 and v["counts"]["tomorrow"] == 1 and v["counts"]["week"] == 3, v["counts"])
d0 = v["days"][0]
check("view: сегодня пары и срок по времени", d0["date"] == "2026-11-11" and [i["title"] for i in d0["items"]] == ["Физика (ЛЗ)", "Высшая математика (Лекция)", "Сегодня вечером"], [i["title"] for i in d0["items"]])
check("view: пара сегодня утром — «прошла»", d0["items"][0]["past"] is True and d0["items"][1]["past"] is False)
check("view: сделанное скрыто", "Через неделю" not in json.dumps(v, ensure_ascii=False))
check("view: прошлые пары и далёкие не попали", "Давно" not in json.dumps(v, ensure_ascii=False) and "Далеко" not in json.dumps(v, ensure_ascii=False))
check("view: подгруппа в названии", any("подгр. 1" in i["title"] for d in v["days"] for i in d["items"]))
check("view: состояние soon/late/ok", [i["state"] for i in v["overdue"]] == ["late"] and d0["items"][2]["state"] == "soon")
v2 = dl.view(sv, lessons, cfg, NOW, days=14, with_done=True)
check("view: with_done показывает сделанное", "Через неделю" in json.dumps(v2, ensure_ascii=False) and any(i.get("done") for d in v2["days"] for i in d["items"]))
v3 = dl.view(sv, lessons, cfg, NOW, show="deadlines")
check("view: только сроки — без пар", all(i["type"] == "deadline" for d in v3["days"] for i in d["items"]))
v4 = dl.view(sv, lessons, cfg, NOW, show="lessons")
check("view: только пары — без сроков и просрочки", all(i["type"] == "lesson" for d in v4["days"] for i in d["items"]) and not v4["overdue"])
v5 = dl.view(sv, lessons, cfg, NOW, course=55)
check("view: фильтр по курсу", [i["title"] for d in v5["days"] for i in d["items"]] == ["Другой курс"] and not v5["overdue"], v5["days"])
check("view: список курсов со счётчиками", {c["id"]: c["count"] for c in v["courses"]} == {3594: 3, 55: 1}, v["courses"])
v6 = dl.view(sv, lessons, dict(cfg, lessons=False), NOW)
check("view: lessons=False", all(i["type"] == "deadline" for d in v6["days"] for i in d["items"]))
v7 = dl.view(sv, lessons, dict(cfg, exclude_courses=[55]), NOW)
check("view: исключённый курс скрыт", "Другой курс" not in json.dumps(v7, ensure_ascii=False))
v8 = dl.view(sv, lessons, dict(cfg, kinds=["events"]), NOW)
check("view: kinds фильтрует", not v8["overdue"] and all(i["type"] == "lesson" for d in v8["days"] for i in d["items"]))
check("view: days=1 — не дальше завтра", all(d["date"] <= "2026-11-12" for d in dl.view(sv, lessons, cfg, NOW, days=1)["days"]))
check("view: пустое хранилище", dl.view(dl.Store(tmp / "e.json"), [], cfg, NOW)["empty"])
# gone
sg = dl.Store(tmp / "gone.json")
sg.merge(dl.parse_events([raw(1, "Сдано", at(2)), raw(2, "Остаётся", at(3))]), NOW, lo, hi)
sg.merge(dl.parse_events([raw(2, "Остаётся", at(3))]), NOW + 60, lo, hi)
vg = dl.view(sg, [], cfg, NOW + 60)
check("view: убранное с сайта скрыто, но посчитано", "Сдано" not in json.dumps(vg, ensure_ascii=False) and vg["gone"] == 1 and vg["counts"]["total"] == 1)
check("view: with_gone показывает пометку", any(i["gone"] for d in dl.view(sg, [], cfg, NOW + 60, with_gone=True)["days"] for i in d["items"]))

# ── .ics ──
txt, cnt = dl.render_ics(sv, lessons, cfg, NOW)
check("ics: CRLF и рамка", txt.startswith("BEGIN:VCALENDAR\r\n") and txt.endswith("END:VCALENDAR\r\n") and "\n" not in txt.replace("\r\n", ""))
check("ics: счётчики (сделанное исключено)", cnt["deadlines"] == 5 and cnt["lessons"] == 4, cnt)
check("ics: строки ≤ 75 октетов", all(len(l.encode()) <= 75 for l in txt.split("\r\n")), [l for l in txt.split("\r\n") if len(l.encode()) > 75][:2])
check("ics: напоминания о сроках (2 на срок)", txt.count("TRIGGER:-PT1440M") == 5 and txt.count("TRIGGER:-PT180M") == 5)
check("ics: напоминание о паре", txt.count("TRIGGER:-PT15M") == 4)
check("ics: UID срока стабильный", "UID:dl-m1@moodle-offline" in txt)
check("ics: BEGIN/END парные", txt.count("BEGIN:VEVENT") == txt.count("END:VEVENT") == 9 and txt.count("BEGIN:VALARM") == txt.count("END:VALARM"))
# UID пары тот же, что у schedule.render_ics
le = lessons[0]
old_ics = sched.render_ics([dict(le, subject=le["subject"])], "g")
uid_old = re.search(r"UID:(\S+)", old_ics).group(1)
check("ics: UID пары совпадает с raspisanie.ics", f"UID:{uid_old}" in txt, uid_old)
# DTSTART срока: за 30 минут до срока, UTC
m = re.search(r"UID:dl-m2@moodle-offline\r\nDTSTAMP:\S+\r\nDTSTART:(\S+)\r\nDTEND:(\S+)", txt)
d_end = datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ"); d_start = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ")
check("ics: срок — получасовое событие, заканчивается в срок (UTC)", d_end - d_start == timedelta(minutes=30) and d_end.strftime("%d %H:%M") == "11 20:59", (m.group(1), m.group(2)))
check("ics: SUMMARY с иконкой и типом", "SUMMARY:⏰ Сегодня вечером — срок сдачи" in txt.replace("\r\n ", ""))
# спецсимволы
sc = dl.Store(tmp / "esc.json")
sc.merge(dl.parse_events([raw(1, "A, B; C \\ D", at(2), description="Строка1<br>Строка2")]), NOW, lo, hi)
t2, _ = dl.render_ics(sc, [], cfg, NOW)
check("ics: экранирование , ; " + chr(92) + " и переносов", ("SUMMARY:⏰ A" + chr(92) + ", B" + chr(92) + "; C " + chr(92)*2 + " D — срок сдачи") in t2.replace("\r\n ", "") and "Строка1 Строка2" in t2.replace("\r\n ", ""), t2[:900])
t3, c3 = dl.render_ics(sv, lessons, dict(cfg, remind=[], lesson_remind=0, lessons=False), NOW)
check("ics: без напоминаний и без пар", "VALARM" not in t3 and c3["lessons"] == 0 and "Физика" not in t3)
t4, c4 = dl.render_ics(sv, lessons, dict(cfg, exclude_courses=[55]), NOW)
check("ics: исключённый курс", "Другой курс" not in t4 and c4["deadlines"] == 4)
t5, c5 = dl.render_ics(sg, [], cfg, NOW + 60)
check("ics: убранное с сайта не попадает", "Сдано" not in t5 and c5["deadlines"] == 1)
long_title = ("Очень длинное название работы " * 8)[:190].strip()
sl = dl.Store(tmp / "long.json"); sl.merge(dl.parse_events([raw(1, long_title, at(2))]), NOW, lo, hi)
t6, _ = dl.render_ics(sl, [], cfg, NOW)
check("ics: длинные строки сложены и собираются обратно", long_title in t6.replace("\r\n ", "") and all(len(l.encode()) <= 75 for l in t6.split("\r\n")))
check("ics: пустое хранилище валидно", dl.render_ics(dl.Store(tmp / "ee.json"), [], cfg, NOW)[0].count("BEGIN:VEVENT") == 0)

# ── страница ──
html = dl.render_page(dl.view(sv, lessons, cfg, NOW, days=30, with_done=True, with_gone=True), cfg, "11.11.2026 12:00")
check("page: самодостаточна (нет внешних скриптов, стилей, картинок)", html.startswith("<!doctype html>") and "<script>" in html and not re.search(r'<(script|link|img)[^>]+(src|href)="https?:', html))
xs = dl.Store(tmp / "xss.json")
xs.merge(dl.parse_events([raw(1, '<img src=x onerror=alert(1)>', at(2), action={"name": "A", "url": 'https://do.sevsu.ru/x?a="><script>1</script>'})]), NOW, lo, hi)
h2 = dl.render_page(dl.view(xs, [], cfg, NOW), cfg, "now")
check("page: экранирование HTML в названии и ссылке", "<img src=x" not in h2 and "onerror=alert(1)>" not in h2 and '"><script>1' not in h2, h2[h2.find("row dl") - 20:h2.find("row dl") + 500])
check("page: данные для скрипта", 'data-ts="' in html and 'data-course="3594"' in html and 'data-done="1"' in html)
check("page: пустая", "Ближайших сроков" in dl.render_page(dl.view(dl.Store(tmp / "e2.json"), [], cfg, NOW), cfg, "n"))
# write_outputs
out = tmp / "out"
(out / "Расписание").mkdir(parents=True)
(out / "Расписание" / "schedule.json").write_text(json.dumps({"meta": {}, "lessons": lessons}, ensure_ascii=False), encoding="utf-8")
w = dl.write_outputs(out, sv, cfg, NOW)
check("write_outputs: файлы созданы", Path(w["ics"]).exists() and Path(w["page"]).exists() and w["lessons"] == 4 and w["deadlines"] == 5, w)
check("write_outputs: без .tmp", not list((out / "Календарь").glob("*.tmp")))
check("load_lessons: нет файла", dl.load_lessons(tmp / "nowhere") == [])
(out / "Расписание" / "schedule.json").write_text("{плохо", encoding="utf-8")
check("load_lessons: битый файл", dl.load_lessons(out) == [])
cl = dl.format_view(dl.view(sv, lessons, cfg, NOW))
check("format_view: есть блоки", "ПРОСРОЧЕНО" in cl and "СЕГОДНЯ" in cl and "[ ] Сегодня вечером" in cl and "пара: Физика" in cl, cl)

# ── дрожание времени срока и стабильные id ──
sj = dl.Store(tmp / "jit.json")
sj.merge(dl.parse_events([raw(1, "J", int(NOW + 5 * 3600))]), NOW, lo, hi)
j1 = dl.soon_events(sj, cfg, NOW)
sj.merge(dl.parse_events([raw(1, "J", int(NOW + 5 * 3600 + 17))]), NOW + 30, lo, hi)     # сервер отдал срок на 17 секунд иначе
check("soon: дрожание срока < минуты не повторяет напоминание", len(j1) == 1 and dl.soon_events(sj, cfg, NOW + 30) == [])
j2 = dl.soon_events(dl.Store(tmp / "jit2.json"), cfg, NOW)
sk = dl.Store(tmp / "jit3.json"); sk.merge(dl.parse_events([raw(1, "K", int(NOW + 3600))]), NOW, lo, hi)
a1 = dl.soon_events(sk, cfg, NOW, mark=False)[0]; a2 = dl.soon_events(sk, cfg, NOW + 300, mark=False)[0]
check("soon: id события не зависит от текста «через …»", a1["id"] == a2["id"] and a1["detail"] != a2["detail"], (a1["detail"], a2["detail"]))
sk.merge(dl.parse_events([raw(1, "K", int(NOW + 3600 + 7200))]), NOW + 60, lo, hi)      # перенос на 2 часа вперёд
sk.merge(dl.parse_events([raw(1, "K", int(NOW + 3600 + 7200 - 4000))]), NOW + 90, lo, hi)  # и снова ближе: по-настоящему другой срок
check("soon: настоящий перенос взводит порог заново", len(dl.soon_events(sk, cfg, NOW + 100)) == 1)
# merge(kinds=...): виды, которые не спрашивали, «пропавшими» не считаем
skn = dl.Store(tmp / "kinds.json")
skn.merge(dl.parse_events([raw(1, "Срок", at(2)), raw(2, "Личное", at(3), etype="user", mod="")]), NOW, lo, hi)
r = skn.merge(dl.parse_events([raw(1, "Срок", at(2))]), NOW + 60, lo, hi, kinds={"deadline", "open", "completion"})
check("merge: kinds — личное не спрашивали, не пропало", not r["gone"] and not skn.events["m2"]["gone"], r["gone"])
r = skn.merge(dl.parse_events([raw(1, "Срок", at(2))]), NOW + 120, lo, hi, kinds={"deadline", "events"})
check("merge: kinds — личное спрашивали и не пришло → убрано", [e["id"] for e in r["gone"]] == ["m2"])
# нормализация: «напоминать» после сохранения в настройках остаётся стабильной
check("normalize идемпотентна", dl.normalize(dl.normalize({"remind": "30, 60, 1440", "kinds": ["open", "deadline"]})) == dl.normalize({"remind": "30, 60, 1440", "kinds": ["open", "deadline"]}))

print("\n" + ("ALL PASS" if not FAIL else f"FAILED: {len(FAIL)}\n  " + "\n  ".join(FAIL)))
sys.exit(1 if FAIL else 0)

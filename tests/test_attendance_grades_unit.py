"""Посещаемость (mod_attendance) и сводка «Оценки»: разбор кусков разметки и — если лежат — настоящих страниц Алексея
(/mnt/user-data/uploads/eef18dd2-attachment.txt — посещаемость, 2a64e1c2-attachment.txt — оценки)."""
import sys, os, re, json, tempfile
import _root
import moodle_offline as mo
from bs4 import BeautifulSoup
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

# ── статусы ──
for st, want in [("Присутствовал", "present"), ("Не был", "absent"), ("?", "pending"), (" ? ", "pending"),
                 ("Опоздал", "late"), ("Уважительная причина", "excused"), ("Отсутствовал", "absent"), ("Что-то новое", "other"), ("", "other")]:
    check(f"статус «{st}» → {want}", mo.attendance_kind(st) == want, mo.attendance_kind(st))

# ── кусок разметки ──
ROW = ('<tr><td class="datecol cell c0">{d} <nobr>{t}</nobr></td><td class="desccol cell c1"><p>{ds}</p></td>'
       '<td class="statuscol cell c2">{s}</td><td class="pointscol cell c3">{p}</td><td class="remarkscol cell c4">{r}</td></tr>')
def page(rows, extra=""):
    body = "".join(ROW.format(**r) for r in rows)
    return BeautifulSoup('<html><body><div role="main"><table class="generaltable attwidth"><thead><tr><th class="datecol">Дата</th></tr></thead><tbody>'
                         + body + '</tbody></table><table class="attlist"><tbody>'
                         '<tr><td>Посещенные занятия:</td><td>3</td></tr><tr><td>Баллы по всем посещенным занятиям:</td><td>1,5 / 3</td></tr>'
                         '<tr><td>Процент по всем посещенным занятиям:</td><td>50,0%</td></tr></tbody></table>' + extra + '</div></body></html>', mo.PARSER)
rows = [dict(d="7.11.26 (Sat)", t="10:10 - 11:40", ds="Практика", s="Присутствовал", p="1 / 1", r="Отмечено студентом"),
        dict(d="7.11.26 (Sat)", t="11:50 - 13:20", ds="Практика", s="Не был", p="0 / 1", r=""),
        dict(d="13.11.26 (Fri)", t="10:10 - 11:40", ds="Лекция", s="Опоздал", p="0,5 / 1", r=""),
        dict(d="13.11.26 (Fri)", t="11:50 - 13:20", ds="Лекция", s="?", p="? / 1", r="")]
a = mo.parse_attendance(page(rows))
check("таблица найдена", a is not None)
check("4 занятия", a and a["counts"]["total"] == 4, a and a["counts"])
check("счётчики", a and (a["counts"]["present"], a["counts"]["absent"], a["counts"]["late"], a["counts"]["pending"]) == (1, 1, 1, 1), a and a["counts"])
check("дата без времени", a and a["sessions"][0]["date"] == "7.11.26 (Sat)", a and a["sessions"][0])
check("время отдельно", a and a["sessions"][0]["time"] == "10:10 - 11:40", a and a["sessions"][0])
check("баллы и заметки", a and a["sessions"][0]["points"] == "1 / 1" and a["sessions"][0]["remarks"] == "Отмечено студентом", a and a["sessions"][0])
check("итог: занятия/баллы/процент", a and a["summary"] == {"taken": "3", "points": "1,5 / 3", "percent": "50,0%"}, a and a["summary"])
line = mo.attendance_line(a)
check("строка итога", "был 1" in line and "не был 1" in line and "опоздал 1" in line and "50,0%" in line and "всего занятий: 4" in line, line)
check("нет таблицы → None", mo.parse_attendance(BeautifulSoup("<html><body><div role='main'><p>Пусто</p></div></body></html>", mo.PARSER)) is None)
a0 = mo.parse_attendance(page([]))
check("пустая таблица → 0 занятий", a0 is not None and a0["counts"]["total"] == 0, a0)
check("строка итога при пустой таблице", "нет отмеченных занятий" in mo.attendance_line(a0), mo.attendance_line(a0))

# ── настоящая страница посещаемости ──
RP = "/mnt/user-data/uploads/eef18dd2-attachment.txt"
if os.path.exists(RP):
    soup = BeautifulSoup(open(RP, encoding="utf-8", errors="replace").read(), mo.PARSER)
    r = mo.parse_attendance(soup)
    check("настоящая: таблица найдена", r is not None)
    if r:
        c = r["counts"]
        check("настоящая: 8 занятий", c["total"] == 8, c)
        check("настоящая: 1 был, 1 не был, 6 не отмечено", (c["present"], c["absent"], c["pending"]) == (1, 1, 6), c)
        check("настоящая: итог 2 / 1 / 2 / 50,0%", r["summary"].get("percent") == "50,0%", r["summary"])
        check("настоящая: у каждого занятия дата и статус", all(s["date"] and s["status"] for s in r["sessions"]), r["sessions"][:2])
        print("   ", mo.attendance_line(r)); print("   ", r["summary"]); print("   ", r["sessions"][0]); print("   ", r["sessions"][1])
        blob = json.dumps(r, ensure_ascii=False)
        check("настоящая: sesskey в результат не попал", "sesskey" not in blob.lower())
        frag = mo.attendance_fragment(soup)
        fh = str(frag); ft = mo.clean_text(frag.get_text(" "))
        check("настоящая: страница для офлайна — вкладки и переключатели убраны", "nav-tabs" not in fh and "attfiltercontrols" not in fh and "Все курсы" not in ft and "Неделя" not in ft, ft[:300])
        check("настоящая: ссылок на attendance/view.php не осталось", "attendance/view.php" not in fh)
        check("настоящая: таблица занятий и итог остались", "attwidth" in fh and "attlist" in fh and "Присутствовал" in ft and "50,0%" in ft)
        check("настоящая: описание преподавателя осталось", "Уважаемые студенты" in ft)
        check("настоящая: нет форм, скриптов, sesskey", not any(x in fh.lower() for x in ("<form", "<script", "sesskey")))
else:
    print("SKIP настоящей страницы посещаемости нет")

# ── сводка оценок: кусок ──
OV = ('<table class="flexible table generaltable" id="overview-grade"><thead><tr><th>Название курса</th><th>Оценка</th><th>Место</th></tr></thead><tbody>'
      '<tr><td class="cell c0"><a href="https://do.sevsu.ru/course/user.php?mode=grade&amp;id=10384&amp;user=77">Курс «А»</a></td><td>69,75</td><td>3</td></tr>'
      '<tr><td class="cell c0"><a href="https://do.sevsu.ru/course/user.php?mode=grade&amp;id=9650&amp;user=77">Курс Б</a></td><td>-</td><td>-</td></tr>'
      '<tr><td class="cell c0">Без ссылки</td><td>5</td><td>1</td></tr>'
      '<tr><td class="cell c0"><a href="https://do.sevsu.ru/x?id=abc">Кривая ссылка</a></td><td>5</td><td>1</td></tr>'
      '</tbody></table>')
g = mo.parse_grades_overview(BeautifulSoup("<html><body>" + OV + "</body></html>", mo.PARSER))
check("оценки: 2 курса (без ссылки и с кривым id пропущены)", len(g) == 2, g)
check("оценки: 69,75", g and g[0] == {"id": 10384, "name": "Курс «А»", "grade": "69,75", "rank": "3"}, g and g[0])
check("оценки: прочерк → пусто", g and g[1]["grade"] == "" and g[1]["rank"] == "", g and g[1])
check("оценки: нет таблицы → []", mo.parse_grades_overview(BeautifulSoup("<html><body><p>x</p></body></html>", mo.PARSER)) == [])

# ── настоящая сводка оценок ──
RG = "/mnt/user-data/uploads/2a64e1c2-attachment.txt"
if os.path.exists(RG):
    soup = BeautifulSoup(open(RG, encoding="utf-8", errors="replace").read(), mo.PARSER)
    rows = mo.parse_grades_overview(soup)
    check("настоящие оценки: 40 курсов", len(rows) == 40, len(rows))
    by = {r["id"]: r for r in rows}
    check("настоящие оценки: 10384 → 69,75", by.get(10384, {}).get("grade") == "69,75", by.get(10384))
    check("настоящие оценки: 13017 → 92", by.get(13017, {}).get("grade") == "92", by.get(13017))
    check("настоящие оценки: 9650 → нет оценки", 9650 in by and by[9650]["grade"] == "", by.get(9650))
    check("настоящие оценки: названия не пустые", all(r["name"] for r in rows))
    print("   с оценкой:", sum(1 for r in rows if r["grade"]), "из", len(rows))
else:
    print("SKIP настоящей сводки оценок нет")

# ── хранение ──
with tempfile.TemporaryDirectory() as d:
    from pathlib import Path
    check("load_grades: нет файла → {}", mo.load_grades(Path(d)) == {})
    (Path(d) / mo.GRADES_FILE).write_text("{битый", encoding="utf-8")
    check("load_grades: битый файл → {}", mo.load_grades(Path(d)) == {})
    (Path(d) / mo.GRADES_FILE).write_text(json.dumps({"updated": "x", "courses": {"1": {"name": "n", "grade": "5", "rank": ""}}}), encoding="utf-8")
    check("load_grades: читает", mo.load_grades(Path(d))["courses"]["1"]["grade"] == "5")

print("FAILS:", FAILS)
sys.exit(1 if FAILS else 0)

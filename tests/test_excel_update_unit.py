# -*- coding: utf-8 -*-
"""Excel-расписание на странице sevsu.ru/univers/shedule: ссылки без «.xlsx» (download.php?file=…, значок file-xlsx),
заголовки вокруг, обновление файла и «остаёмся на старом, если сайт не отвечает».
Страница ниже собрана по вёрстке настоящей: названия институтов и файлы придуманы."""
import json, sys, tempfile, hashlib
from pathlib import Path
import _root
import moodle_offline as mo
import schedule as sch
import xlsx_schedule as xs
import mkxlsx

FAIL = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond: FAIL.append(name)

def lnk(tok, name, icon="file-xlsx"):
    return (f'<a href="/univers/shedule/download.php?file={tok}" download="" class="d-flex document-link">\n'
            f'<div class="icon {icon}"></div>\n<div class="document-link__name">\n{name}                                </div>\n</a>\n')

def acc(title, groups):
    body = "".join(f'<div class="document-link__group">\n<div class="document-link__group-name">\n{g}\n</div>\n{"".join(ls)}</div>\n'
                   for g, ls in groups)
    return (f'<div class="document-accordion document-accordion-2" id="bx_1_1">\n<div class="d-flex document-accordion__header">\n'
            f'<h4>{title}</h4>\n<div class="icon icon-arrow"></div>\n</div>\n<div class="document-accordion__body">\n{body}</div>\n</div>\n')

def column(name, accs):
    return (f'<div class="schedule-table__column js-table-column">\n<div class="schedule-table__column-header js-table-column-header">\n'
            f'<div class="schedule-table__column-name">\n{name}        </div>\n<div class="schedule-table__column-icon"></div>\n</div>\n'
            f'<div class="schedule-table__column-body">\n{"".join(accs)}</div>\n</div>\n')

def page(year="2026-2027 учебный год", sem2=False):
    day = [("1 курс Бакалавриат", "A1"), ("2 курс Бакалавриат", "A2"), ("3 курс Бакалавриат", "A3"), ("2 курс Магистратура ОЗФО", "A4")]
    other = [("1 курс Бакалавриат", "B1"), ("2 курс Бакалавриат", "B2"), ("2 курс Специалитет", "B3")]
    groups_a = [("I семестр", [lnk(t, n) for n, t in day])] + ([("II семестр", [lnk("S" + t, n) for n, t in day])] if sem2 else [])
    groups_b = [("I семестр", [lnk(t, n) for n, t in other])] + ([("II семестр", [lnk("S" + t, n) for n, t in other])] if sem2 else [])
    zaoch = [("I семестр", [lnk("Z2", "2 курс Бакалавриат ЗФО"), lnk("Z3", "3 курс Бакалавриат ЗФО")])]
    cols = [column("Расписание учебных занятий ОФО, ОЗФО", [acc("Институт Первый", groups_a), acc("Институт Второй", groups_b)]),
            column("Расписание экзаменационной и установочной сессии ЗФО", [acc("Институт Первый", zaoch)]),
            column("Расписание промежуточной аттестации ОФО, ОЗФО", [acc("Институт Первый", [("", [lnk("P1", "2 курс Бакалавриат")])])])]
    gia = ('<div class="document-accordion document-accordion-2"><div class="d-flex document-accordion__header"><h4>Расписание ГИА</h4></div>'
           '<div class="document-accordion__body">' + lnk("G1", "Распоряжение № 1", "file-pdf-orange") + '</div></div>')
    return (f'<html><body>{gia}<div class="schedule-table">\n<h2 class="schedule-table__title">\n{year}<br></h2>\n'
            f'<div class="schedule-table__content">\n{"".join(cols)}</div></div></body></html>')

BASE = "https://www.sevsu.ru/univers/shedule/"
PAGE = page()
links = sch.find_excel_links(PAGE, BASE)
check("настоящая вёрстка: 11 ссылок (4+3+2+1 сессия/аттестация), PDF не взят", len(links) == 10 and all("download.php" in l["url"] for l in links), len(links))
a2 = next(l for l in links if l["text"] == "2 курс Бакалавриат" and l["ctx"][2] == "Институт Первый" and "учебных" in l["ctx"][1])
check("ссылка: заголовки сверху вниз — год, вид расписания, институт, семестр", a2["ctx"] == ["2026-2027 учебный год",
      "Расписание учебных занятий ОФО, ОЗФО", "Институт Первый", "I семестр"], a2["ctx"])
check("ссылка: адрес абсолютный, имени файла в адресе нет", a2["url"] == "https://www.sevsu.ru/univers/shedule/download.php?file=A2" and a2["file"] == "", a2)
check("ссылка: у второй и третьей ссылки группы заголовки те же (раньше терялись)",
      all(l["ctx"][3] == "I семестр" and l["ctx"][2] == "Институт Первый" for l in links if l["url"].endswith(("A1", "A2", "A3", "A4"))))
rk = sch.rank_excel(links, "ib-b-25-2-o", sch.date(2026, 11, 11))
top = [l["url"][-2:] for l in rk[:2]]
check("подбор: «2 курс Бакалавриат» учебных занятий — первыми (оба института), сессия и аттестация ниже", set(top) == {"A2", "B2"}, top)
check("подбор: сессия ЗФО и аттестация — в конце", {l["url"][-2:] for l in rk[-2:]} <= {"Z2", "Z3", "P1", "A4", "B3"}, [l["url"][-2:] for l in rk])
zg = sch.rank_excel(links, "ib-b-25-2-z", sch.date(2026, 11, 11))
check("подбор: у заочной группы сессия ЗФО выше очных", zg[0]["url"].endswith("Z2"), [l["url"][-2:] for l in zg[:3]])
sig = sch.link_sig(a2)
nl = sch.find_excel_links(page("2027-2028 учебный год", sem2=True), BASE)
m = sch.match_sig(nl, sig, sch.date(2027, 2, 20))
check("выбор находится в новом учебном году и в весеннем семестре", m and m["url"].endswith("SA2") and "II" in m["ctx"][3], m)
m2 = sch.match_sig(nl, sig, sch.date(2027, 10, 5))
check("выбор: осенью берётся I семестр", m2 and m2["url"].endswith("A2"), m2)
check("Content-Disposition: имя файла, кириллица в latin-1", sch.ScheduleJob._disposition_name('attachment; filename="fit_25_b_o11.11.xlsx"') == "fit_25_b_o11.11.xlsx"
      and sch.ScheduleJob._disposition_name("attachment; filename*=UTF-8''%D0%98%D0%91.xlsx") == "ИБ.xlsx"
      and sch.ScheduleJob._disposition_name('attachment; filename="' + "ИБ.xlsx".encode("utf-8").decode("latin-1") + '"') == "ИБ.xlsx"
      and sch.ScheduleJob._disposition_name("") == "")

# ── задача: скачать, обновить, пережить недоступный сайт ──
ME, OTHER = "ИБ/б-25-2-о", "ИИ/б-25-1-о"
def book(extra_day=False, gone=False):
    d1 = {2: {ME: ("whole", "Теория информации, Кузнецова Мария Ивановна (I-В-411)\n", "Л\n", "I-В-411\n")}}
    if extra_day: d1[3] = {ME: ("whole", "Новая пара, Петров Пётр Петрович (I-Г-1)\n", "Л\n", "I-Г-1\n")}
    days = [("26.10.2026", d1), ("27.10.2026", {1: {ME: ("whole", "Физика, Петров П. П. (I-А-101)\n", "Л\n", "I-А-101\n")}})]
    if gone: days = [("26.10.2026", {1: {OTHER: ("whole", "Чужая, Иванов И. И. (I-1)\n", "Л\n", "I-1\n")}})]
    return mkxlsx.build([OTHER] if gone else [ME, OTHER], [("уч.н. 9", days)])

V1, V2 = book(), book(extra_day=True)
class Down(Exception): pass

class Site:
    """Подставной сайт: страница + download.php. mode: ok / down (ничего не отвечает) / http500 / redesign (на странице нет Excel)."""
    def __init__(self): self.mode, self.page, self.files, self.dl = "ok", PAGE, {}, []
    def client(self, url): return self
    def request(self, m, url, **kw):
        if self.mode == "down": raise mo.NetworkDown("сайт не отвечает")
        class R: pass
        r = R(); r.url = BASE; r.status_code = 500 if self.mode == "http500" else 200
        return r
    def _text(self, r): return "<html><body>Страница изменена</body></html>" if self.mode == "redesign" else self.page
    def download(self, url, dest, **kw):
        self.dl.append(url)
        if self.mode == "down": raise mo.NetworkDown("сайт не отвечает")
        if url not in self.files: raise mo.MoodleError("сервер вернул веб-страницу вместо файла")
        Path(dest).parent.mkdir(parents=True, exist_ok=True); Path(dest).write_bytes(self.files[url])
        return {"size": len(self.files[url]), "disposition": 'attachment; filename="fit_25_b_o.xlsx"'}

def job(tmp, site, excel=None):
    j = sch.ScheduleJob(tmp, "ib-b-25-2-o", ["excel"], mo.Reporter(), mo.Control(), pdf=False, excel=excel)
    j.browser = None; j.client = site.client
    return j

tmp = Path(tempfile.mkdtemp(prefix="exup_"))
site = Site()
site.files = {"https://www.sevsu.ru/univers/shedule/download.php?file=" + t: V1 if t in ("A2",) else book(gone=True)
              for t in ("A1", "A2", "A3", "A4", "B1", "B2", "B3", "Z2", "Z3", "P1")}
j1 = job(tmp, site); r1 = j1.run("2weeks")
meta = json.loads((tmp / "Расписание" / "excel" / sch.EXCEL_META).read_text(encoding="utf-8"))
check("первый запуск: файл найден по ссылке без .xlsx", r1["per_source"]["excel"] == 2 and j1.excel_report["state"] == "new", (r1, j1.excel_report))
check("первый запуск: в папке один xlsx под именем из Content-Disposition + метаданные", [p.name for p in (tmp / "Расписание" / "excel").glob("*.xls*")] == ["fit_25_b_o.xlsx"]
      and meta["sha256"] == hashlib.sha256(V1).hexdigest() and meta["name"] == "fit_25_b_o.xlsx", meta)
check("первый запуск: временных .cand-* не осталось", not list((tmp / "Расписание" / "excel").glob(".cand-*")))
check("первый запуск: выбор запомнен (с заголовками)", j1.excel_out and j1.excel_out["sig"]["ctx"][2] == "Институт Первый", j1.excel_out)
dl_first = len(site.dl)

site.dl.clear()
j2 = job(tmp, site, excel=j1.excel_out); j2.run("2weeks")
check("повторно: файл не менялся, скачан один", j2.excel_report["state"] == "same" and len(site.dl) == 1, (j2.excel_report, site.dl))
d = json.loads((tmp / "Расписание" / "data" / "excel.json").read_text(encoding="utf-8"))
check("повторно: в данных отмечено «не менялся» и время проверки", d["file_state"] == "same" and d["file_checked"], d)
saved_when = meta["saved_text"]

site.files["https://www.sevsu.ru/univers/shedule/download.php?file=A2"] = V2
j3 = job(tmp, site, excel=j1.excel_out); j3.run("2weeks")
check("файл заменили на сайте: подхвачен новый", j3.excel_report["state"] == "updated" and j3.excel_report["added"] == 1 and j3.excel_report["removed"] == 0, j3.excel_report)
meta3 = json.loads((tmp / "Расписание" / "excel" / sch.EXCEL_META).read_text(encoding="utf-8"))
d = json.loads((tmp / "Расписание" / "data" / "excel.json").read_text(encoding="utf-8"))
check("файл заменили: на диске новый, хеш обновился, пар стало больше", meta3["sha256"] == hashlib.sha256(V2).hexdigest() and len(d["lessons"]) == 3, (meta3, len(d["lessons"])))
check("файл заменили: в данных сказано, сколько пар добавилось", d["file_state"] == "updated" and d["file_added"] == 1, d)

# сайт не отвечает → остаёмся на сохранённом файле
for mode, label in (("down", "сайт не отвечает"), ("http500", "сайт отвечает 500"), ("redesign", "страницу перестроили (Excel-ссылок нет)")):
    site.mode = mode
    jd = job(tmp, site, excel=j1.excel_out); rd = jd.run("2weeks")
    d = json.loads((tmp / "Расписание" / "data" / "excel.json").read_text(encoding="utf-8"))
    check(f"{label}: остаёмся на старом файле, пары на месте, источник не в ошибке", rd["per_source"]["excel"] == 3 and not jd.errors
          and jd.excel_report["state"] == "stale" and len(d["lessons"]) == 3, (rd, jd.excel_report, jd.errors))
    check(f"{label}: сохранённый файл и метаданные не тронуты", json.loads((tmp / "Расписание" / "excel" / sch.EXCEL_META).read_text(encoding="utf-8"))["sha256"] == meta3["sha256"])
    check(f"{label}: в данных причина и дата сохранённого файла", d["file_state"] == "stale" and d["file_reason"] and d["updated"] == meta3["saved_text"], d)
site.mode = "ok"
# сайт ожил → снова свежий
jr = job(tmp, site, excel=j1.excel_out); jr.run("2weeks")
check("сайт ожил: снова обычная проверка (файл тот же)", jr.excel_report["state"] == "same", jr.excel_report)

# вместо файла пришёл мусор (200 OK) → не затираем сохранённый
site.files["https://www.sevsu.ru/univers/shedule/download.php?file=A2"] = "<html>ошибка сервера</html>".encode("utf-8")
jg = job(tmp, site, excel=j1.excel_out); jg.run("2weeks")
check("вместо файла пришёл мусор: остаёмся на сохранённом, он не затёрт", jg.excel_report["state"] == "stale"
      and json.loads((tmp / "Расписание" / "excel" / sch.EXCEL_META).read_text(encoding="utf-8"))["sha256"] == meta3["sha256"]
      and (tmp / "Расписание" / "excel" / "fit_25_b_o.xlsx").read_bytes() == V2, jg.excel_report)
# файл заменили, а нашей группы в нём нет → остаёмся на старом (тот же выбор)
site.files["https://www.sevsu.ru/univers/shedule/download.php?file=A2"] = book(gone=True)
jh = job(tmp, site, excel=j1.excel_out); jh.run("2weeks")
check("новый файл без нашей группы: остаёмся на сохранённом того же выбора", jh.excel_report["state"] == "stale" and "нет группы" in jh.excel_report["reason"], jh.excel_report)
# повреждён сохранённый файл → как резерв не берём; сайта нет → сеть недоступна (как и у других источников)
site.files["https://www.sevsu.ru/univers/shedule/download.php?file=A2"] = V2
(tmp / "Расписание" / "excel" / "fit_25_b_o.xlsx").write_bytes(b"broken")
site.mode = "down"
try:
    job(tmp, site, excel=j1.excel_out).run("2weeks"); check("сохранённый файл повреждён и сайта нет → сеть недоступна", False)
except mo.NetworkDown:
    check("сохранённый файл повреждён и сайта нет → сообщаем, что сеть недоступна; прежние пары остались",
          len(json.loads((tmp / "Расписание" / "data" / "excel.json").read_text(encoding="utf-8"))["lessons"]) == 3)
# сайт отвечает 500, сохранённого нет → ошибка источника
tmp2 = Path(tempfile.mkdtemp(prefix="exup2_"))
site.mode = "http500"
jf = job(tmp2, site); rf = jf.run("2weeks")
check("первый запуск при ошибке сайта и без сохранённого файла → ошибка источника", rf["per_source"]["excel"] is None and "excel" in jf.errors, jf.errors)

print("\n" + ("ALL PASS" if not FAIL else f"FAILED: {len(FAIL)}\n  " + "\n  ".join(FAIL)))
sys.exit(1 if FAIL else 0)
